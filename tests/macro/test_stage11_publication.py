from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
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
    parse_eia_weekly_response,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.migrations import initialize_all
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, read_connection, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_TIME = "2026-08-12T12:00:00Z"


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
                            "CL_UNIT": "Percent" if series_code == "A191RL" else "Billions of Dollars",
                            "UNIT_MULT": "0",
                        }
                    ]
                }
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _bea_cohort(*, real_value: str = "1.5"):
    first = parse_bea_nipa_response(
        body=_bea_body(BEA_NIPA_T10101, "A191RL", real_value),
        prepared=prepare_bea_nipa_capture(BEA_NIPA_T10101),
    )
    second = parse_bea_nipa_response(
        body=_bea_body(BEA_NIPA_T10105, "A191RC", "21000"),
        prepared=prepare_bea_nipa_capture(BEA_NIPA_T10105),
    )
    return assemble_bea_nipa_history((first, second))


def _retail_body(*, sales: str = "100") -> bytes:
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
                        "sales": sales,
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


def _retail_cohort(*, sales: str = "100"):
    page = parse_eia_retail_page_response(
        body=_retail_body(sales=sales), prepared=prepare_eia_retail_page()
    )
    return assemble_eia_retail_capture((page,))


def _weekly_body(*, value: str = "2.5") -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 1,
                "dateFormat": "YYYY-MM-DD",
                "frequency": "weekly",
                "data": [
                    {
                        "period": "2020-01-03",
                        "value": value,
                        "series": "PET.WCESTUS1.W",
                        "units": "Dollars per Gallon",
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _weekly_capture(*, value: str = "2.5"):
    return parse_eia_weekly_response(
        body=_weekly_body(value=value), prepared=prepare_eia_weekly_capture()
    )


class Stage11PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.store_map = explicit_store_map(root / "stores")
        self.registry = stage11_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        initialize_all(self.store_map, self.registry, applied_at=_MIGRATION_TIME)
        self.captured_at = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
        self.importer = Stage11MacroImporter(
            self.store_map,
            self.registry,
            clock=lambda: self.captured_at,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _advance_clock(self) -> None:
        self.captured_at += timedelta(minutes=1)

    def test_sparse_retail_history_publishes_no_invented_metric_value(self) -> None:
        payload = json.loads(_retail_body())
        first = payload["response"]["data"][0]
        first["customers"] = None
        second = dict(first)
        second["period"] = "2020-02"
        second["customers"] = "4"
        payload["response"]["total"] = 2
        payload["response"]["data"] = [first, second]
        page = parse_eia_retail_page_response(
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            prepared=prepare_eia_retail_page(),
        )
        cohort = assemble_eia_retail_capture((page,))
        receipt = self.importer.publish_prepared(
            self.importer.prepare_eia_retail_history(cohort)
        )
        self.assertEqual(receipt.outcome, "succeeded")
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            metrics = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT metric FROM stage11_eia_retail_observation_versions "
                    "ORDER BY metric"
                )
            )
            self.assertEqual(
                metrics,
                (
                    "customers",
                    "price",
                    "price",
                    "revenue",
                    "revenue",
                    "sales",
                    "sales",
                ),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT row_count FROM stage11_eia_retail_captures"
                ).fetchone()[0],
                2,
            )

    def test_happy_publications_replay_zero_writes_and_leave_stage3_relations_untouched(self) -> None:
        before_prepare = mutation_fingerprint(self.store_map)
        bea = self.importer.prepare_bea_nipa_history(_bea_cohort())
        retail = self.importer.prepare_eia_retail_history(_retail_cohort())
        weekly = self.importer.prepare_eia_weekly_history(_weekly_capture())
        self.assertEqual(mutation_fingerprint(self.store_map), before_prepare)

        before_stage3 = self._stage3_counts()
        receipts = tuple(
            self.importer.publish_prepared(candidate) for candidate in (bea, retail, weekly)
        )
        self.assertEqual(tuple(item.outcome for item in receipts), ("succeeded",) * 3)
        self.assertEqual(self._stage3_counts(), before_stage3)

        after_first = mutation_fingerprint(self.store_map)
        replay = tuple(
            self.importer.publish_prepared(candidate)
            for candidate in (
                self.importer.prepare_bea_nipa_history(_bea_cohort()),
                self.importer.prepare_eia_retail_history(_retail_cohort()),
                self.importer.prepare_eia_weekly_history(_weekly_capture()),
            )
        )
        self.assertEqual(
            tuple((item.outcome, item.written_count) for item in replay),
            (("unchanged", 0),) * 3,
        )
        self.assertEqual(mutation_fingerprint(self.store_map), after_first)

        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            expected = {
                "stage11_bea_nipa_captures": 2,
                "stage11_bea_nipa_observation_versions": 2,
                "stage11_bea_nipa_observations": 2,
                "stage11_eia_retail_captures": 1,
                "stage11_eia_retail_observation_versions": 4,
                "stage11_eia_retail_observations": 4,
                "stage11_eia_weekly_captures": 1,
                "stage11_eia_weekly_observation_versions": 1,
                "stage11_eia_weekly_observations": 1,
            }
            for table, count in expected.items():
                with self.subTest(table=table):
                    self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], count)
            available = connection.execute(
                "SELECT DISTINCT available_at, available_precision FROM stage11_bea_nipa_observation_versions"
            ).fetchall()
            self.assertEqual(tuple(tuple(row) for row in available), (("2026-08-12T14:00:00.000000Z", "datetime"),))

    def test_corrections_append_versions_and_advance_current_pointers(self) -> None:
        self.importer.publish_prepared(self.importer.prepare_bea_nipa_history(_bea_cohort()))
        self.importer.publish_prepared(self.importer.prepare_eia_retail_history(_retail_cohort()))
        self.importer.publish_prepared(self.importer.prepare_eia_weekly_history(_weekly_capture()))
        self._advance_clock()
        receipts = (
            self.importer.publish_prepared(
                self.importer.prepare_bea_nipa_history(_bea_cohort(real_value="1.7"))
            ),
            self.importer.publish_prepared(
                self.importer.prepare_eia_retail_history(_retail_cohort(sales="101"))
            ),
            self.importer.publish_prepared(
                self.importer.prepare_eia_weekly_history(_weekly_capture(value="2.7"))
            ),
        )
        self.assertEqual(tuple(item.outcome for item in receipts), ("succeeded",) * 3)
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage11_bea_nipa_observation_versions").fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage11_eia_retail_observation_versions").fetchone()[0],
                5,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM stage11_eia_weekly_observation_versions").fetchone()[0],
                2,
            )
            bea = connection.execute(
                """
                SELECT version.value_text, version.correction_sequence, version.supersedes_version_id
                FROM stage11_bea_nipa_observations AS current
                JOIN stage11_bea_nipa_observation_versions AS version
                  ON version.version_id=current.current_version_id
                WHERE current.canonical_series_id='macro.gdp.real_qoq_saar_pct'
                """
            ).fetchone()
            self.assertEqual((bea["value_text"], bea["correction_sequence"]), ("1.7", 2))
            self.assertIsNotNone(bea["supersedes_version_id"])
            weekly = connection.execute(
                """
                SELECT version.value_text, version.correction_sequence
                FROM stage11_eia_weekly_observations AS current
                JOIN stage11_eia_weekly_observation_versions AS version
                  ON version.version_id=current.current_version_id
                """
            ).fetchone()
            self.assertEqual((weekly["value_text"], weekly["correction_sequence"]), ("2.7", 2))

    def test_migration_guards_immutable_evidence_versions_and_latest_current_pointer(self) -> None:
        self.importer.publish_prepared(self.importer.prepare_bea_nipa_history(_bea_cohort()))
        self._advance_clock()
        self.importer.publish_prepared(
            self.importer.prepare_bea_nipa_history(_bea_cohort(real_value="1.7"))
        )
        with writer_connection(self.store_map, StoreRole.MACRO) as connection:
            capture_id = connection.execute(
                "SELECT capture_id FROM stage11_bea_nipa_captures LIMIT 1"
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE stage11_bea_nipa_captures SET row_count=2 WHERE capture_id=?",
                    (capture_id,),
                )
            old_version_id = connection.execute(
                """
                SELECT version_id FROM stage11_bea_nipa_observation_versions
                WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct'
                ORDER BY correction_sequence LIMIT 1
                """
            ).fetchone()[0]
            wrong_parent_id = connection.execute(
                """
                SELECT version_id FROM stage11_bea_nipa_observation_versions
                WHERE canonical_series_id='macro.gdp.nominal_billions'
                """
            ).fetchone()[0]
            latest_real_id = connection.execute(
                """
                SELECT current_version_id FROM stage11_bea_nipa_observations
                WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct' AND period='2020Q1'
                """
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO stage11_bea_nipa_observation_versions (
                        version_id, canonical_series_id, table_name, series_code, period,
                        value_text, unit, unit_multiplier, available_at,
                        available_precision, captured_at, captured_precision,
                        correction_sequence, supersedes_version_id, capture_id,
                        artifact_id, snapshot_id, run_id, source_row
                    )
                    SELECT ?, canonical_series_id, table_name, series_code, period,
                           value_text, unit, unit_multiplier, available_at,
                           available_precision, captured_at, captured_precision,
                           3, ?, capture_id, artifact_id, snapshot_id, run_id, source_row
                    FROM stage11_bea_nipa_observation_versions
                    WHERE version_id=?
                    """,
                    ("hostile_stage11_bea_lineage", wrong_parent_id, latest_real_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE stage11_bea_nipa_observations SET current_version_id=?
                    WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct' AND period='2020Q1'
                    """,
                    (old_version_id,),
                )
            current_version_id = connection.execute(
                """
                SELECT current_version_id FROM stage11_bea_nipa_observations
                WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct' AND period='2020Q1'
                """
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE stage11_bea_nipa_observation_versions SET value_text='99' WHERE version_id=?",
                    (current_version_id,),
                )

    def test_malformed_or_incomplete_capture_never_reaches_a_write(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            self.importer.prepare_bea_nipa_history(object())  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            parse_eia_weekly_response(
                body=b'{"response":{"total":2,"dateFormat":"YYYY-MM-DD","frequency":"weekly","data":[]}}',
                prepared=prepare_eia_weekly_capture(),
            )
        with self.assertRaises(ValidationError):
            parse_eia_retail_page_response(
                body=b'{"response":{"total":1,"dateFormat":"YYYY-MM","frequency":"monthly","data":[]}}',
                prepared=prepare_eia_retail_page(),
            )
        self.assertEqual(mutation_fingerprint(self.store_map), before)

    def test_publication_rejects_unsanitized_raw_capture_before_any_write(self) -> None:
        before = mutation_fingerprint(self.store_map)
        cohort = _bea_cohort()
        unsafe_bytes = b'{"api_key":"must-not-persist"}'
        unsafe_capture = replace(
            cohort.captures[0],
            raw_bytes=unsafe_bytes,
            raw_bytes_sha256=sha256(unsafe_bytes).hexdigest(),
        )
        unsafe_cohort = replace(
            cohort,
            captures=(unsafe_capture, cohort.captures[1]),
        )
        with self.assertRaises(ValidationError):
            self.importer.prepare_bea_nipa_history(unsafe_cohort)
        self.assertEqual(mutation_fingerprint(self.store_map), before)

    def _stage3_counts(self) -> tuple[int, int, int]:
        with read_connection(self.store_map, StoreRole.MACRO) as connection:
            return tuple(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "macro_series",
                    "macro_observation_versions",
                    "macro_observations",
                )
            )


if __name__ == "__main__":
    unittest.main()
