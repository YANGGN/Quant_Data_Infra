"""ETF public contract, retained cutoff selection and mutation neutrality."""
from __future__ import annotations
import hashlib
import copy
import json
from dataclasses import replace
import io
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.etf_calculations import ETF_SYMBOLS, FEATURE_NAMES
from quant_data.market import etf_snapshot
from quant_data.migrations import initialize_all
from quant_data.registry import RegistryError, load_registry, etf_snapshot_registry_profile, fmp_research_registry_profile
from quant_data.schema import validate_schema
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.local_agent_cli import run

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOL = "portfolio.get_etf_allocator_snapshot"
_CAPTURED_AT = "2026-09-01T00:00:00Z"
def _digest(value):
    return f"{value:064x}"
def _fields(row):
    return {field["name"]: field["value"] for field in row["fields"]}
def _summary(result):
    return {field["name"]: field["value"] for field in result["diagnostics"][0]["metrics"]}


class EtfSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry("config/system_registry.json",
            project_root=PROJECT_ROOT, environment={})

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        initialize_all(self.stores, self.registry, applied_at=_CAPTURED_AT)
        self._seed_prices()
        self.baseline = mutation_fingerprint(self.stores)
        self.app = Stage1Application(self.stores, self.registry)

    def tearDown(self):
        self.assertEqual(self.baseline["sha256"], mutation_fingerprint(self.stores)["sha256"])
        self.temporary.cleanup()

    def invoke(self, symbols=ETF_SYMBOLS, cutoff="2026-09-05T00:00:00Z", **extra):
        request = {"api_version": "1.0", "tool": TOOL, "tool_version": "1.0.0",
            "arguments": {"symbols": list(symbols), "decision_as_of": cutoff, **extra}}
        stdout, stderr = io.StringIO(), io.StringIO()
        code = run(["call"], stdin=io.BytesIO(dumps_strict(request).encode()),
            stdout=stdout, stderr=stderr, application=self.app)
        self.assertEqual(stderr.getvalue(), "")
        return code, loads_strict(stdout.getvalue())

    def test_all_symbols_missing_history_schema_receipt_and_single_transaction(self):
        gateway = etf_snapshot._quiet_market_connection
        calls = []
        @contextmanager
        def counted(stores):
            calls.append(stores)
            with gateway(stores) as connection:
                yield connection
        with patch.object(etf_snapshot, "_quiet_market_connection", counted):
            code, response = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        result = response["result"]
        self.assertEqual(result["status"], "not_established")
        rows = [_fields(row) for row in result["records"]]
        self.assertEqual([r["symbol"] for r in rows], list(ETF_SYMBOLS))
        self.assertEqual(len(rows), 25)
        for row in rows:
            for feature in FEATURE_NAMES:
                self.assertIsNone(row[feature])
                self.assertIsNotNone(row[feature + "_missing_reason"])
            self.assertNotIn("structure_approved", row)
            self.assertNotIn("qualification", row)
        by_symbol = {r["symbol"]: r for r in rows}
        self.assertEqual(by_symbol["SPY"]["instrument_id"], "cross-section-spy")
        self.assertEqual(by_symbol["SPY"]["classification_status"], "retained_identity_only")
        self.assertEqual(by_symbol["XLY"]["classification_status"], "not_retained")
        self.assertEqual(by_symbol["IEF"]["source_observation_count"], 1)
        self.assertIsNone(by_symbol["XLY"]["instrument_id"])
        self.assertIn("instrument_not_retained", by_symbol["XLY"]["missing_inputs"])
        self.assertIn("missing_month_end_observations", by_symbol["SPY"]["missing_inputs"])
        summary = _summary(result)
        self.assertEqual(summary["selected_month_end"], "2026-08-31")
        self.assertEqual(summary["decision_as_of"], "2026-09-05T00:00:00Z")
        self.assertEqual(summary["registry_revision"], self.registry.revision)
        self.assertFalse(summary["complete"])
        self.assertFalse(result["truncation"]["applied"])
        declaration = self.registry.tool(TOOL, "1.0.0")
        validate_schema(result, declaration["output_schema"])
        receipt = response["receipt"]
        for key in ("input_schema_sha256", "output_schema_sha256", "registry_schema_sha256"):
            self.assertRegex(receipt[key], "^[a-f0-9]{64}$")
        self.assertNotIn(str(self.root), dumps_strict(response))
        self.assertNotIn("policy_inputs", result)
        self.assertEqual(self.invoke()[1]["result"], result)

    def test_invalid_inputs_and_future_time_rejected_before_store(self):
        cases = [
            ([], "2026-09-05T00:00:00Z", {}),
            (["SPY", "SPY"], "2026-09-05T00:00:00Z", {}),
            (["SGOV"], "2026-09-05T00:00:00Z", {}),
            (["SPY"], "2026-09-05", {}),
            (["SPY"], "2026-09-05T00:00:00", {}),
            (["SPY"], "9999-09-05T00:00:00Z", {}),
            (["SPY"], "2026-09-05T00:00:00Z", {"path": "/tmp/hostile"}),
        ]
        with patch.object(etf_snapshot, "_quiet_market_connection",
                          side_effect=AssertionError("Store opened")):
            for symbols, cutoff, extra in cases:
                with self.subTest(symbols=symbols, cutoff=cutoff, extra=extra):
                    code, result = self.invoke(symbols, cutoff, **extra)
                    self.assertEqual(code, 2)
                    self.assertIn("error", result)

    def test_unsupported_history_does_not_leak_current_identity_or_prices(self):
        code, response = self.invoke(cutoff="2026-08-31T23:59:59Z")
        self.assertEqual(code, 0)
        rows = [_fields(row) for row in response["result"]["records"]]
        self.assertTrue(all(row["instrument_id"] is None for row in rows))
        self.assertTrue(all(row["source_observation_count"] == 0 for row in rows))
        self.assertEqual(_summary(response["result"])["point_in_time_status"], "not_established")
        with patch.object(etf_snapshot, "_quiet_market_connection",
                          side_effect=AssertionError("Store opened")):
            code, response = self.invoke(cutoff="2020-08-31T23:59:59Z")
        self.assertEqual(code, 0)
        self.assertIsNone(_summary(response["result"])["selected_month_end"])
        with patch.object(etf_snapshot, "_quiet_market_connection",
                          side_effect=AssertionError("Store opened")):
            code, response = self.invoke(cutoff="0001-01-01T00:00:00Z")
        self.assertEqual(code, 0)
        self.assertIsNone(_summary(response["result"])["selected_month_end"])


    def test_later_correction_does_not_change_past_snapshot(self):
        before = self.invoke(["SPY"])[1]["result"]
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            original = dict(connection.execute(
                "SELECT * FROM stage10_daily_price_versions WHERE version_id=?",
                ("cross-section-version-spy-3",)).fetchone())
            revised = {**original, "version_id": "future-correction",
                "available_at": "2026-09-06T00:00:00Z",
                "captured_at": "2026-09-06T00:00:00Z",
                "correction_sequence": 2, "supersedes_version_id": original["version_id"],
                "open_value": "999", "high_value": "999", "low_value": "999", "close_value": "999"}
            names = list(revised)
            connection.execute("INSERT INTO stage10_daily_price_versions (" +
                ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")",
                tuple(revised.values()))
            connection.execute("UPDATE stage10_daily_prices SET current_version_id=? WHERE instrument_id=? AND trade_date=?",
                ("future-correction", original["instrument_id"], original["trade_date"]))
        self.baseline = mutation_fingerprint(self.stores)
        after = self.invoke(["SPY"])[1]["result"]
        self.assertEqual(before, after)

    def test_non_etf_identity_fails_closed_before_price_selection(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            self.stores = explicit_store_map(Path(directory) / "stores")
            initialize_all(self.stores, self.registry, applied_at=_CAPTURED_AT)
            self._seed_prices(spy_asset_type="equity")
            self.app = Stage1Application(self.stores, self.registry)
            before = mutation_fingerprint(self.stores)
            try:
                with patch.object(etf_snapshot.Stage10DailyPriceRepository, "_as_of_rows",
                                  side_effect=AssertionError("Price selection reached")):
                    code, response = self.invoke(["SPY"])
                self.assertEqual(code, 0)
                row = _fields(response["result"]["records"][0])
                self.assertEqual(row["asset_type"], "equity")
                self.assertEqual(row["classification_status"], "retained_identity_only")
                self.assertEqual(row["source_observation_count"], 0)
                self.assertEqual(loads_strict(row["missing_inputs"]), ["instrument_type_mismatch"])
                self.assertTrue(all(row[name] is None for name in FEATURE_NAMES))
                self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])
            finally:
                self.stores = explicit_store_map(self.root / "stores")
                self.app = Stage1Application(self.stores, self.registry)

    def test_projection_rejects_tampered_current_tool_with_recomputed_hash(self):
        baseline = fmp_research_registry_profile(self.registry)
        raw = copy.deepcopy(dict(baseline.raw))
        declaration = next(tool for tool in raw["tools"] if tool["id"] == TOOL)
        declaration["description"] += " tampered"
        source = (json.dumps(raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
        altered = replace(baseline, raw=raw, tools=tuple(raw["tools"]),
                          source_sha256=hashlib.sha256(source).hexdigest())
        with self.assertRaisesRegex(RegistryError, "ETF snapshot registry source drifted"):
            etf_snapshot_registry_profile(altered)

    def test_exact_predecessor_and_discovery(self):
        previous = etf_snapshot_registry_profile(self.registry)
        self.assertEqual(previous.revision, "2.69.0")
        self.assertEqual(previous.source_sha256,
            "2e9c3e4d2bfc263735a1e9c875d2091210065e0a375a0a0e0c420839a03c774f")
        from quant_data.tool_platform.generate import generated_bytes
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root / "config").mkdir()
            import json
            (root / "config/system_registry.json").write_text(
                json.dumps(previous.raw, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
            generated, _, catalog = generated_bytes(root)
        # This historical successor advances 2.69 to the frozen 2.70 release.
        self.assertEqual(hashlib.sha256(generated).hexdigest(),
            "4c2de9ef1ac49a4c23ab326000878fa66629caa1f8a0bcb65d4c089d827e9ac3")
        self.assertEqual(hashlib.sha256(catalog).hexdigest(),
            "6f143f9f32fe0cc7d713b9afb1425901ee96e3fdea1539d09f8d602ca794894b")
        manifest = self.app.dispatcher.manifest()
        advertised = [tool for tool in manifest["tools"] if tool["name"] == TOOL]
        self.assertEqual(len(advertised), 1)
        self.assertEqual(advertised[0]["version"], "1.0.0")
        self.assertEqual(self.registry.tool(TOOL)["stores"], ["market"])

    def _insert_run(
        self,
        connection: object,
        *,
        run_id: str,
        dataset_id: str,
        semantic_identity: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO ingestion_runs(
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, code_version
            ) VALUES (?, ?, ?, 'fixture.seed', '{}', 'running', ?, 'fixture')
            """,
            (run_id, dataset_id, semantic_identity, _CAPTURED_AT),
        )

    def _seed_prices(self, *, spy_asset_type="etf") -> None:
        fixtures = tuple((symbol, spy_asset_type if symbol == "SPY" else "etf", (100,) if symbol == "IEF" else (100, 120, 125))
                         for symbol in ETF_SYMBOLS if symbol != "XLY")
        dates = ("2025-08-29", "2026-07-31", "2026-08-31")
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            identity_run_id = "cross-section-identity-run"
            self._insert_run(
                connection,
                run_id=identity_run_id,
                dataset_id="market.stage10.instruments",
                semantic_identity=_digest(1),
            )
            for index, (ticker, asset_type, closes) in enumerate(fixtures, start=1):
                instrument_id = f"cross-section-{ticker.lower()}"
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id, provider, provider_symbol, asset_type,
                        display_name, exchange_code, currency_segment,
                        first_trade_date, identity_seed_sha256, captured_at,
                        captured_precision, run_id
                    ) VALUES (?, 'fmp', ?, ?, ?, 'XNAS', 'provider_native', NULL,
                              ?, ?, 'datetime', ?)
                    """,
                    (
                        instrument_id,
                        ticker,
                        asset_type,
                        f"{ticker} fixture",
                        _digest(10 + index),
                        _CAPTURED_AT,
                        identity_run_id,
                    ),
                )
                run_id = f"cross-section-capture-run-{ticker.lower()}"
                capture_id = f"cross-section-capture-{ticker.lower()}"
                artifact_id = f"cross-section-artifact-{ticker.lower()}"
                snapshot_id = f"cross-section-snapshot-{ticker.lower()}"
                self._insert_run(
                    connection,
                    run_id=run_id,
                    dataset_id="market.stage10.source_evidence",
                    semantic_identity=_digest(100 + index),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_captures(
                        capture_id, dataset_id, provider, instrument_id,
                        provider_symbol, endpoint_path, scope_manifest_sha256,
                        request_scope_json, request_scope_sha256, response_sha256,
                        response_bytes, http_status, content_type,
                        semantic_identity, completeness, artifact_id, snapshot_id,
                        captured_at, captured_precision, earliest_trade_date,
                        latest_trade_date, row_count, normalization_version, run_id
                    ) VALUES (
                        ?, 'market.stage10.source_evidence', 'fmp', ?, ?,
                        '/stable/historical-price-eod/full', ?, '{}', ?, ?, ?,
                        200, 'application/json', ?, 'complete', ?, ?, ?,
                        'datetime', ?, ?, ?, 'stage10.fmp.daily_price.v1', ?
                    )
                    """,
                    (
                        capture_id,
                        instrument_id,
                        ticker,
                        _digest(200 + index),
                        _digest(300 + index),
                        _digest(400 + index),
                        f'{{"symbol":"{ticker}"}}'.encode("utf-8"),
                        _digest(500 + index),
                        artifact_id,
                        snapshot_id,
                        _CAPTURED_AT,
                        dates[0],
                        dates[len(closes) - 1],
                        len(closes),
                        run_id,
                    ),
                )
                for source_row, close in enumerate(closes, start=1):
                    version_id = f"cross-section-version-{ticker.lower()}-{source_row}"
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_price_versions(
                            version_id, instrument_id, trade_date, provider,
                            price_variant, currency_segment, open_value,
                            high_value, low_value, close_value, volume,
                            available_at, available_precision, captured_at,
                            captured_precision, correction_sequence,
                            supersedes_version_id, capture_id, artifact_id,
                            snapshot_id, run_id, source_row
                        ) VALUES (
                            ?, ?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native',
                            ?, ?, ?, ?, 1, ?, 'datetime', ?, 'datetime', 1, NULL,
                            ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            version_id,
                            instrument_id,
                            dates[source_row - 1],
                            str(close),
                            str(close),
                            str(close),
                            str(close),
                            _CAPTURED_AT,
                            _CAPTURED_AT,
                            capture_id,
                            artifact_id,
                            snapshot_id,
                            run_id,
                            source_row,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_prices(
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', 'fmp_full_eod_v1',
                                  'provider_native', ?)
                        """,
                        (instrument_id, dates[source_row - 1], version_id),
                    )
