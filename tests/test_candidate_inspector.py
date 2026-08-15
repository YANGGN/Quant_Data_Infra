from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import shutil
import sqlite3
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data import candidate_inspector
from quant_data.candidate_inspector import (
    FileStamp,
    InspectorConfigurationError,
    InspectorConflictError,
    InspectorMethodError,
    InspectorNotFoundError,
    InspectorResourceError,
    InspectorValidationError,
    Stage11CandidateInspector,
    create_server,
)
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage11_bea import (
    BEA_NIPA_T10101,
    BEA_NIPA_T10105,
    assemble_bea_nipa_history,
    parse_bea_nipa_response,
    prepare_bea_nipa_capture,
)
from quant_data.macro.stage11_eia import (
    assemble_eia_retail_capture,
    parse_eia_retail_page_response,
    prepare_eia_retail_page,
)
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.macro.stage11_scope import load_stage11_macro_scope
from quant_data.migrations import initialize_all
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bea_body(table_name: str, series_code: str, value: str) -> bytes:
    return json.dumps(
        {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "TableName": table_name,
                            "SeriesCode": series_code,
                            "TimePeriod": "2020Q1",
                            "DataValue": value,
                            "CL_UNIT": (
                                "Percent"
                                if series_code == "A191RL"
                                else "Billions of Dollars"
                            ),
                            "UNIT_MULT": "0",
                        }
                    ]
                }
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _bea_cohort():
    return assemble_bea_nipa_history(
        (
            parse_bea_nipa_response(
                body=_bea_body(BEA_NIPA_T10101, "A191RL", "1.5"),
                prepared=prepare_bea_nipa_capture(BEA_NIPA_T10101),
            ),
            parse_bea_nipa_response(
                body=_bea_body(BEA_NIPA_T10105, "A191RC", "21000"),
                prepared=prepare_bea_nipa_capture(BEA_NIPA_T10105),
            ),
        )
    )


def _retail_body() -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 1,
                "dateFormat": "YYYY-MM",
                "frequency": "monthly",
                "data": [
                    {
                        "period": "2020-01",
                        "stateid": "US",
                        "sectorid": "ALL",
                        "sales": "100",
                        "revenue": "200",
                        "price": "2",
                        "customers": "3",
                        "sales-units": "million kilowatthours",
                        "revenue-units": "million dollars",
                        "price-units": "cents per kilowatthour",
                        "customers-units": "thousand customers",
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _retail_cohort():
    return assemble_eia_retail_capture(
        (
            parse_eia_retail_page_response(
                body=_retail_body(),
                prepared=prepare_eia_retail_page(),
            ),
        )
    )


def _file_fingerprint(path: Path) -> tuple[bool, int | None, str | None]:
    if not path.exists():
        return (False, None, None)
    payload = path.read_bytes()
    status = path.stat()
    return (True, status.st_size, hashlib.sha256(payload).hexdigest())


def _candidate_files(root: Path) -> tuple[tuple[bool, int | None, str | None], ...]:
    database = root / "stores" / "macro.sqlite"
    return tuple(
        _file_fingerprint(path)
        for path in (
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
            root / "private" / "candidate" / "stage11" / "resume.json",
            root / "private" / "candidate" / "stage11" / "completion.json",
        )
    )


class CandidateInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls.target = Path(cls.temporary.name)
        cls.store_map = explicit_store_map(cls.target / "stores")
        cls.registry = stage11_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        cls.scope = load_stage11_macro_scope(
            PROJECT_ROOT / "config" / "stage11_macro_scope.json"
        )
        if cls.registry.source_sha256 is None:
            raise AssertionError("Synthetic registry source digest is unavailable")
        initialize_all(
            cls.store_map,
            cls.registry,
            applied_at="2026-08-12T12:00:00Z",
        )
        importer = Stage11MacroImporter(
            cls.store_map,
            cls.registry,
            clock=lambda: datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
        )
        results = (
            importer.publish_prepared(
                importer.prepare_bea_nipa_history(_bea_cohort())
            ),
            importer.publish_prepared(
                importer.prepare_eia_retail_history(_retail_cohort())
            ),
        )
        if tuple(item.outcome for item in results) != ("succeeded", "succeeded"):
            raise AssertionError("Synthetic Stage 11 publication failed")
        state = cls.target / "private" / "candidate" / "stage11"
        state.mkdir(parents=True, mode=0o700)
        (state / "resume.json").write_text(
            json.dumps(
                {
                    "bea_published": True,
                    "contract": "quant_data.stage11_backfill_resume",
                    "registry_source_sha256": cls.registry.source_sha256,
                    "retail_published": True,
                    "scope_manifest_sha256": cls.scope.manifest_sha256,
                    "target_profile_id": "stage11_bea_eia_macro_v1",
                    "version": "1.0.0",
                    "weekly_published": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        database = cls.store_map.path("macro")
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        with mock.patch.object(candidate_inspector, "APPROVED_TARGET_ROOT", cls.target):
            cls.application = Stage11CandidateInspector()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.before_logical = mutation_fingerprint(self.store_map)
        self.before_files = _candidate_files(self.target)

    def tearDown(self) -> None:
        self.assertEqual(self.before_logical, mutation_fingerprint(self.store_map))
        self.assertEqual(self.before_files, _candidate_files(self.target))

    def test_summary_and_observations_show_real_relations_and_partial_state(self) -> None:
        self.assertEqual(
            self.application._candidate_receipt_directory,
            self.target
            / "private"
            / "candidate"
            / "stage11"
            / "candidate"
            / "promotion-candidates",
        )
        summary = self.application.summary()
        self.assertEqual(summary["candidate_state"], "partial")
        self.assertTrue(summary["markers"]["bea_published"])
        self.assertTrue(summary["markers"]["retail_published"])
        self.assertFalse(summary["markers"]["weekly_published"])
        self.assertFalse(summary["markers"]["completion_receipt_present"])
        by_slug = {item["slug"]: item for item in summary["series"]}
        self.assertEqual(len(by_slug), 7)
        self.assertEqual(by_slug["gdp-real-qoq-saar-pct"]["version_count"], 1)
        self.assertEqual(by_slug["gdp-nominal-billions"]["version_count"], 1)
        self.assertEqual(by_slug["electricity-retail-sales"]["version_count"], 1)
        self.assertEqual(by_slug["petroleum-weekly-stock"]["state"], "not_yet_populated")

        real = self.application.observations(
            {
                "series": "gdp-real-qoq-saar-pct",
                "page": "1",
                "limit": "100",
                "direction": "desc",
            }
        )
        self.assertEqual(real["pagination"]["total_rows"], 1)
        self.assertEqual(real["rows"][0]["period"], "2020Q1")
        self.assertEqual(real["rows"][0]["value_text"], "1.5")
        self.assertEqual(real["rows"][0]["dimensions"]["series_code"], "A191RL")

        weekly = self.application.observations(
            {
                "series": "petroleum-weekly-stock",
                "page": "1",
                "limit": "25",
                "direction": "asc",
            }
        )
        self.assertEqual(weekly["series"]["state"], "not_yet_populated")
        self.assertEqual(weekly["pagination"]["total_rows"], 0)
        self.assertEqual(weekly["rows"], [])

        page = self.application.handle("GET", "/").body.decode("utf-8")
        detail = self.application.handle(
            "GET",
            "/observations?series=gdp-real-qoq-saar-pct&page=1&limit=100&direction=desc",
        ).body.decode("utf-8")
        self.assertIn("Stage 11 candidate inspector", page)
        self.assertIn("Real GDP growth", detail)
        self.assertNotIn(str(self.target), page + detail)
        self.assertNotIn("response_bytes", page + detail)

    def test_closed_query_contract_methods_and_routes_fail_safely(self) -> None:
        invalid_targets = (
            "/?path=/tmp/other.sqlite",
            "/api/summary?sql=select",
            "/observations?series=gdp-real-qoq-saar-pct&series=gdp-nominal-billions&page=1&limit=25&direction=desc",
            "/observations?series=unknown&page=1&limit=25&direction=desc",
            "/observations?series=gdp-real-qoq-saar-pct&page=0&limit=25&direction=desc",
            "/observations?series=gdp-real-qoq-saar-pct&page=101&limit=25&direction=desc",
            "/observations?series=gdp-real-qoq-saar-pct&page=1&limit=1.5&direction=desc",
            "/observations?series=gdp-real-qoq-saar-pct&page=1&limit=25&direction=random",
            "/observations?series=gdp-real-qoq-saar-pct&page=1&limit=25&direction=desc&relation=sqlite_master",
        )
        for target in invalid_targets:
            with self.subTest(target=target):
                with self.assertRaises((InspectorValidationError, InspectorResourceError)):
                    self.application.handle("GET", target)
        with self.assertRaises(InspectorMethodError):
            self.application.handle("POST", "/")
        with self.assertRaises(InspectorNotFoundError):
            self.application.handle("GET", "/missing")

    def test_loopback_http_headers_errors_and_no_writer_entrypoints(self) -> None:
        with self.assertRaises(InspectorConfigurationError):
            create_server(self.application, host="0.0.0.0")
        for bad_port in (-1, 65536, True, 1.5):
            with self.subTest(port=bad_port):
                with self.assertRaises(InspectorConfigurationError):
                    create_server(self.application, port=bad_port)  # type: ignore[arg-type]

        with (
            mock.patch(
                "quant_data.stores.writer_connection",
                side_effect=AssertionError("writer reached"),
            ),
            mock.patch(
                "quant_data.migrations.initialize_all",
                side_effect=AssertionError("migration reached"),
            ),
            mock.patch(
                "quant_data.macro.stage11_publication.Stage11MacroImporter.publish_prepared",
                side_effect=AssertionError("publication reached"),
            ),
            self.running_server() as port,
        ):
            cases = (
                ("GET", "/", 200),
                ("GET", "/api/summary", 200),
                (
                    "GET",
                    "/api/observations?series=electricity-retail-sales&page=1&limit=25&direction=asc",
                    200,
                ),
                ("GET", "/assets/dashboard.css", 200),
                ("GET", "/missing", 404),
                ("POST", "/", 405),
                ("GET", "/?sql=select", 400),
            )
            for method, target, expected in cases:
                with self.subTest(method=method, target=target):
                    status, headers, body = self.request(port, method, target)
                    self.assertEqual(status, expected)
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                    self.assertEqual(headers["X-Frame-Options"], "DENY")
                    self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                    self.assertNotIn(str(self.target).encode(), body)
                    if expected >= 400:
                        error = loads_strict(body)
                        self.assertIn("code", error["error"])
                        self.assertNotIn("select", body.decode("utf-8").lower())

    def test_renderer_escapes_database_values(self) -> None:
        hostile = {
            "series": {
                "slug": "gdp-real-qoq-saar-pct",
                "label": '<script>alert("x")</script>',
                "family": '<img src=x onerror="x">',
                "state": "populated",
            },
            "query": {"page": 1, "limit": 25, "direction": "desc"},
            "pagination": {
                "total_rows": 1,
                "returned_rows": 1,
                "has_previous": False,
                "has_next": False,
            },
            "rows": [
                {
                    "period": "2020Q1",
                    "value_text": "<b>1</b>",
                    "unit": '" onmouseover="x',
                    "available_at": "2026-01-01",
                    "captured_at": "2026-01-02",
                    "correction_sequence": 1,
                    "dimensions": {"series": "<unsafe>"},
                    "version_id": "<version>",
                    "capture_id": "<capture>",
                }
            ],
        }
        rendered = candidate_inspector._render_observations(hostile)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn('onerror="x"', rendered)
        self.assertIn("&lt;img", rendered)
        self.assertNotIn("<b>1</b>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;b&gt;1&lt;/b&gt;", rendered)

    def test_snapshot_change_nonempty_wal_symlink_and_schema_drift_fail_closed(self) -> None:
        changed = list(self.application._baseline)
        first = changed[0]
        changed[0] = FileStamp(
            first.present,
            first.device,
            first.inode,
            first.mode,
            first.size,
            first.mtime_ns,
            "0" * 64,
        )
        with mock.patch.object(
            self.application,
            "_snapshot",
            side_effect=(self.application._baseline, tuple(changed)),
        ):
            with self.assertRaises(InspectorConflictError):
                self.application.summary()

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            wal = bad_root / "stores" / "macro.sqlite-wal"
            wal.write_bytes(b"not-empty")
            with mock.patch.object(candidate_inspector, "APPROVED_TARGET_ROOT", bad_root):
                with self.assertRaises(InspectorConflictError):
                    Stage11CandidateInspector()

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            database = bad_root / "stores" / "macro.sqlite"
            database.unlink()
            os.symlink(self.store_map.path("macro"), database)
            with mock.patch.object(candidate_inspector, "APPROVED_TARGET_ROOT", bad_root):
                with self.assertRaises(InspectorConfigurationError):
                    Stage11CandidateInspector()

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            database = bad_root / "stores" / "macro.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute("DROP TABLE stage11_eia_weekly_observations")
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
            with mock.patch.object(candidate_inspector, "APPROVED_TARGET_ROOT", bad_root):
                with self.assertRaises(InspectorConfigurationError):
                    Stage11CandidateInspector()


    def test_missing_current_pointer_and_invalid_completion_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            database = bad_root / "stores" / "macro.sqlite"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    DELETE FROM stage11_bea_nipa_observations
                    WHERE canonical_series_id=?
                    """,
                    ("macro.gdp.real_qoq_saar_pct",),
                )
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
            with mock.patch.object(
                candidate_inspector, "APPROVED_TARGET_ROOT", bad_root
            ):
                with self.assertRaises(InspectorConfigurationError):
                    Stage11CandidateInspector()

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            completion = (
                bad_root
                / "private"
                / "candidate"
                / "stage11"
                / "completion.json"
            )
            completion.write_bytes(b"malformed-not-json")
            with mock.patch.object(
                candidate_inspector, "APPROVED_TARGET_ROOT", bad_root
            ):
                with self.assertRaises(InspectorConfigurationError):
                    Stage11CandidateInspector()

        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            bad_root = Path(directory) / "candidate"
            shutil.copytree(self.target, bad_root)
            state = bad_root / "private" / "candidate" / "stage11"
            resume_path = state / "resume.json"
            resume = loads_strict(resume_path.read_bytes())
            resume["weekly_published"] = True
            resume_path.write_bytes(
                dumps_strict(resume).encode("utf-8")
            )
            material = {
                "candidate_receipt_sha256": "1" * 64,
                "contract": "quant_data.stage11_backfill_completion",
                "evidence_sha256": "2" * 64,
                "registry_source_sha256": self.registry.source_sha256,
                "scope_manifest_sha256": self.scope.manifest_sha256,
                "target_profile_id": "stage11_bea_eia_macro_v1",
                "version": "1.0.0",
            }
            receipt = {
                **material,
                "sha256": hashlib.sha256(
                    dumps_strict(material).encode("utf-8")
                ).hexdigest(),
            }
            (state / "completion.json").write_bytes(
                dumps_strict(receipt).encode("utf-8")
            )
            with mock.patch.object(
                candidate_inspector, "APPROVED_TARGET_ROOT", bad_root
            ):
                with self.assertRaises(InspectorConfigurationError):
                    Stage11CandidateInspector()


    def test_occupied_port_returns_sanitized_startup_error(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = int(listener.getsockname()[1])
            stream = io.StringIO()
            with (
                mock.patch.object(
                    candidate_inspector, "APPROVED_TARGET_ROOT", self.target
                ),
                redirect_stderr(stream),
            ):
                self.assertEqual(
                    candidate_inspector.main(["--port", str(port)]),
                    1,
                )
        rendered = stream.getvalue()
        payload = loads_strict(rendered)
        self.assertEqual(
            payload["contract"],
            "quant_data.stage11_candidate_inspector_startup_error",
        )
        self.assertEqual(payload["error"]["code"], "candidate_unavailable")
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn(str(PROJECT_ROOT), rendered)

    @contextmanager
    def running_server(self):
        server = create_server(self.application)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield int(server.server_address[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    @staticmethod
    def request(
        port: int,
        method: str,
        target: str,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request(method, target)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
