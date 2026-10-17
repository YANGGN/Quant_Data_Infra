from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.boundary.dispatcher import UnsupportedToolVersionError
from quant_data.errors import (
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage10_series import (
    DATASET_ID,
    Stage10DailyPriceQuery,
    Stage10AvailableTickerQuery,
    Stage10DailyPriceRepository,
)
from quant_data.market.stage12_incremental import (
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
)
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.migrations import initialize_all
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole, writer_connection
from quant_data.tool_platform.market_returns import (
    Stage10MarketReturnEngine,
    Stage10MarketReturnRequest,
    audit_stage10_market_return,
)
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STAGE12A_SCOPE = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
_STAGE12B_SCOPE = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "stage12b" / "daily_price_complete.json"
_CAPTURED_AT = "2026-08-15T01:00:00Z"


class Stage10DailyPriceSeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_store_map(self.root / "stores")
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at="2026-08-15T00:00:00Z")
        self._seed_instrument()
        self.collector = Stage12BIncrementalCollector(
            fixture_root=self.root,
            market_store=self.stores.market,
            scope=load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE),
            stage12a_scope=load_stage12_market_v1_scope(_STAGE12A_SCOPE),
            stage12a_scope_source=_STAGE12A_SCOPE,
        )
        self.body = _FIXTURE.read_bytes()
        self._publish(self.body, captured_at=_CAPTURED_AT)
        self.repository = Stage10DailyPriceRepository(self.stores, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()
        assert_project_data_unchanged(
            self._project_data_before,
            project_root=PROJECT_ROOT,
        )

    def _seed_instrument(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "stage10_series_seed_run",
                    "market.stage10.instruments",
                    "a" * 64,
                    "fixture.seed",
                    "{}",
                    _CAPTURED_AT,
                    "fixture",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type,
                    display_name, exchange_code, currency_segment,
                    first_trade_date, identity_seed_sha256, captured_at,
                    captured_precision, run_id
                ) VALUES (?, 'fmp', 'AAPL', 'equity', 'AAPL fixture', 'XNAS',
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                (
                    "stage10_series_aapl",
                    "b" * 64,
                    _CAPTURED_AT,
                    "stage10_series_seed_run",
                ),
            )

    @staticmethod
    def _request(captured_at: str) -> Stage12BFixtureRequest:
        return Stage12BFixtureRequest(
            symbol="AAPL",
            from_date="2026-08-10",
            to_date="2026-08-12",
            session_dates=("2026-08-10", "2026-08-11", "2026-08-12"),
            captured_at=captured_at,
        )

    def _publish(self, body: bytes, *, captured_at: str) -> None:
        response = Stage12BFixtureResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=body,
            elapsed_seconds=1,
        )
        prepared = self.collector.prepare(self._request(captured_at), response)
        self.collector.publish(prepared)

    def _corrected_body(self, close_value: int) -> bytes:
        rows = json.loads(self.body)
        open_value = int(rows[1]["open"])
        rows[1].update(
            {
                "high": close_value + 1,
                "close": close_value,
                "change": close_value - open_value,
                "changePercent": close_value - open_value,
                "vwap": close_value - 2,
            }
        )
        return json.dumps(rows, separators=(",", ":")).encode("utf-8")

    def _volume_corrected_body(self, volume: int) -> bytes:
        rows = json.loads(self.body)
        rows[1]["volume"] = volume
        return json.dumps(rows, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _query(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
        identifier: str = "AAPL",
        identifier_kind: str = "provider_symbol",
        start_date: str | None = "2026-08-10",
        end_date: str | None = "2026-08-12",
    ) -> Stage10DailyPriceQuery:
        return Stage10DailyPriceQuery(
            identifier=identifier,
            identifier_kind=identifier_kind,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            as_of=as_of,
            date_only_policy="completed_date",
            limit=limit,
        )

    def _return_request(
        self,
        *,
        direction: str = "trailing",
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
        horizon: int = 1,
        method: str = "simple",
    ) -> Stage10MarketReturnRequest:
        return Stage10MarketReturnRequest(
            prices=self._query(mode=mode, as_of=as_of, limit=limit),
            direction=direction,
            method=method,
            horizon=horizon,
        )

    @staticmethod
    def _public_v2_arguments(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
        horizon: int = 1,
        method: str = "simple",
    ) -> dict[str, object]:
        return {
            "identifier": "AAPL",
            "identifier_kind": "provider_symbol",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": "completed_date",
            "method": method,
            "horizon": horizon,
            "limit": limit,
        }


    @staticmethod
    def _public_price_arguments(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        return {
            "ticker": "AAPL",
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": "completed_date",
            "limit": limit,
        }

    @staticmethod
    def _public_volume_arguments(
        *,
        mode: str = "latest",
        as_of: str | None = None,
        limit: int = 100,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "ticker": "AAPL",
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": "completed_date",
            "limit": limit,
        }
        if start_date is not None:
            result["start_date"] = start_date
        if end_date is not None:
            result["end_date"] = end_date
        return result

    def test_typed_ohlc_series_are_coherent_and_optional_bounds_are_read_only(
        self,
    ) -> None:
        before = mutation_fingerprint(self.stores)
        series = self.repository.get_ohlc_series(
            self._query(start_date=None, end_date=None)
        )
        start_only = self.repository.get_ohlc_series(
            self._query(start_date="2026-08-11", end_date=None)
        )
        end_only = self.repository.get_ohlc_series(
            self._query(start_date=None, end_date="2026-08-11")
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(
            tuple(item.metadata["observation_field"] for item in series),
            ("open", "high", "low", "close"),
        )
        self.assertEqual(
            tuple(tuple(point.value for point in item.observations) for item in series),
            (
                (Decimal("100"), Decimal("101"), Decimal("102")),
                (Decimal("105"), Decimal("106"), Decimal("107")),
                (Decimal("99"), Decimal("100"), Decimal("101")),
                (Decimal("102"), Decimal("103"), Decimal("104")),
            ),
        )
        self.assertEqual(len({item.series_id for item in series}), 4)
        self.assertTrue(
            all(
                item.audit["requested_start_date"] is None
                and item.audit["requested_end_date"] is None
                for item in series
            )
        )
        self.assertEqual(
            tuple(point.period_start for point in start_only[0].observations),
            ("2026-08-11", "2026-08-12"),
        )
        self.assertEqual(
            tuple(point.period_start for point in end_only[0].observations),
            ("2026-08-10", "2026-08-11"),
        )
        for row_index in range(3):
            self.assertEqual(
                len(
                    {
                        item.observations[row_index].version_id
                        for item in series
                    }
                ),
                1,
            )
            self.assertEqual(
                len(
                    {
                        item.observations[row_index].evidence_id
                        for item in series
                    }
                ),
                1,
            )

        before_correction = self.repository.get_ohlc_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        latest = self.repository.get_ohlc_series(self._query())
        repeated = self.repository.get_ohlc_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self.assertEqual(before_correction[1].observations[1].value, Decimal("106"))
        self.assertEqual(before_correction[3].observations[1].value, Decimal("103"))
        self.assertEqual(latest[1].observations[1].value, Decimal("109"))
        self.assertEqual(latest[3].observations[1].value, Decimal("108"))
        self.assertEqual(
            dumps_strict([item.to_primitive() for item in before_correction]),
            dumps_strict([item.to_primitive() for item in repeated]),
        )

    def test_typed_close_series_preserves_stage10_semantics_and_is_read_only(self) -> None:
        before = mutation_fingerprint(self.stores)
        series = self.repository.get_close_series(self._query())
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(
            tuple(item.value for item in series.observations),
            (Decimal("102"), Decimal("103"), Decimal("104")),
        )
        self.assertEqual(
            tuple(item.period_start for item in series.observations),
            ("2026-08-10", "2026-08-11", "2026-08-12"),
        )
        self.assertTrue(
            all(item.available_at == item.captured_at for item in series.observations)
        )
        self.assertTrue(
            all(item.available_precision == "datetime" for item in series.observations)
        )
        self.assertTrue(
            all(item.vintage_at == item.captured_at for item in series.observations)
        )
        self.assertEqual(series.metadata["unit"], "provider_native_currency")
        self.assertEqual(series.metadata["frequency"], "daily")
        self.assertEqual(series.audit["availability_basis"], "local_capture")
        self.assertEqual(series.audit["requested_mode"], "latest")
        self.assertEqual(series.audit["actual_mode"], "latest")
        self.assertEqual(series.audit["point_in_time_status"], "not_applicable")
        self.assertEqual(series.audit["session_calendar_status"], "not_established")
        self.assertEqual(series.provenance["dataset_id"], DATASET_ID)
        self.assertIn(
            "adjustment_and_total_return_semantics_not_established", series.warnings
        )
        self.assertIn("session_calendar_not_established", series.warnings)
        self.assertTrue(series.provenance["store_receipt"]["sha256"])
        self.assertEqual(
            series.provenance["store_receipt"]["selected_instrument_identity"],
            {
                "instrument_id": "stage10_series_aapl",
                "provider_symbol": "AAPL",
                "asset_type": "equity",
                "display_name": "AAPL fixture",
                "exchange_code": "XNAS",
                "currency_segment": "provider_native",
                "identity_seed_sha256": "b" * 64,
                "captured_at": _CAPTURED_AT,
                "captured_precision": "datetime",
                "run_id": "stage10_series_seed_run",
            },
        )
        self.assertTrue(
            all(item.version_id and item.evidence_id for item in series.observations)
        )
        series.validate_lineage()
        dumps_strict(series.to_primitive())

    def test_typed_volume_series_reuses_selection_without_price_metadata_leakage(
        self,
    ) -> None:
        before = mutation_fingerprint(self.stores)
        series = self.repository.get_volume_series(
            self._query(start_date=None, end_date=None)
        )
        start_only = self.repository.get_volume_series(
            self._query(start_date="2026-08-11", end_date=None)
        )
        end_only = self.repository.get_volume_series(
            self._query(start_date=None, end_date="2026-08-11")
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(
            tuple(item.value for item in series.observations),
            (Decimal("1000"), Decimal("1001"), Decimal("1002")),
        )
        self.assertEqual(
            tuple(item.period_start for item in start_only.observations),
            ("2026-08-11", "2026-08-12"),
        )
        self.assertEqual(
            tuple(item.period_start for item in end_only.observations),
            ("2026-08-10", "2026-08-11"),
        )
        self.assertEqual(series.metadata["unit"], "provider_native_volume")
        self.assertEqual(series.metadata["value_representation"], "volume")
        self.assertEqual(series.metadata["data_variant"], "fmp_full_eod_v1")
        self.assertEqual(series.metadata["observation_field"], "volume")
        self.assertNotIn("currency_segment", series.metadata)
        self.assertNotIn("price_variant", series.metadata)
        self.assertNotIn("currency_segment", series.audit)
        self.assertNotIn("price_variant", series.audit)
        self.assertTrue(
            all(
                item.unit == "provider_native_volume"
                and item.value_representation == "volume"
                and item.dimensions["data_variant"] == "fmp_full_eod_v1"
                and "currency_segment" not in item.dimensions
                and "price_variant" not in item.dimensions
                for item in series.observations
            )
        )
        self.assertEqual(series.audit["requested_start_date"], None)
        self.assertEqual(series.audit["requested_end_date"], None)
        self.assertEqual(series.provenance["dataset_id"], DATASET_ID)
        self.assertIn(
            "volume_adjustment_semantics_not_established", series.warnings
        )
        self.assertIn("volume_unit_semantics_provider_native", series.warnings)
        self.assertTrue(
            all(item.version_id and item.evidence_id for item in series.observations)
        )
        series.validate_lineage()
        dumps_strict(series.to_primitive())

        before_correction = self.repository.get_volume_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self._publish(
            self._volume_corrected_body(0),
            captured_at="2026-08-15T03:00:00Z",
        )
        read_before = mutation_fingerprint(self.stores)
        latest = self.repository.get_volume_series(self._query())
        repeated = self.repository.get_volume_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        after_correction = self.repository.get_volume_series(
            self._query(mode="as_of", as_of="2026-08-15T04:00:00Z")
        )
        read_after = mutation_fingerprint(self.stores)

        self.assertEqual(read_before["sha256"], read_after["sha256"])
        self.assertEqual(before_correction.observations[1].value, Decimal("1001"))
        self.assertEqual(latest.observations[1].value, Decimal("0"))
        self.assertEqual(after_correction.observations[1].value, Decimal("0"))
        self.assertEqual(
            dumps_strict(before_correction.to_primitive()),
            dumps_strict(repeated.to_primitive()),
        )
        self.assertNotEqual(
            before_correction.observations[1].version_id,
            after_correction.observations[1].version_id,
        )
        self.assertEqual(before_correction.audit["point_in_time_status"], "safe")
        self.assertEqual(
            before_correction.audit["point_in_time_scope"],
            "retained_local_captures",
        )

    def test_public_volume_series_omits_dates_matches_http_and_preserves_lineage(
        self,
    ) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        arguments = self._public_volume_arguments()
        declaration = self.registry.tool("market.get_volume_series")
        self.assertNotIn("start_date", declaration["input_schema"]["required"])
        self.assertNotIn("end_date", declaration["input_schema"]["required"])
        self.assertIn("start_date", declaration["input_schema"]["properties"])
        self.assertIn("end_date", declaration["input_schema"]["properties"])

        before = mutation_fingerprint(self.stores)
        direct = dispatcher.call("market.get_volume_series", arguments)
        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "market.get_volume_series",
                    "arguments": arguments,
                }
            ).encode("utf-8"),
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(dumps_strict(payload["result"]), dumps_strict(direct))
        self.assertEqual(payload["tool"]["version"], "1.0.0")
        self.assertEqual(
            payload["receipt"]["operation_graph_id"],
            "tool_platform.market.get_volume_series.v1",
        )
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(len(direct["series"]), 1)
        series = direct["series"][0]
        self.assertEqual(series["metadata"]["unit"], "provider_native_volume")
        self.assertEqual(series["metadata"]["value_representation"], "volume")
        self.assertNotIn("currency_segment", series["metadata"])
        self.assertNotIn("price_variant", series["metadata"])
        self.assertEqual(
            tuple(item["value"] for item in series["observations"]),
            (Decimal("1000"), Decimal("1001"), Decimal("1002")),
        )
        self.assertEqual(direct["truncation"]["returned_count"], 3)
        self.assertEqual(
            direct["diagnostics"][0]["code"], "stage10_market_volume_quality"
        )
        self.assertEqual(
            tuple(item["dataset_id"] for item in direct["lineage"]),
            (
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ),
        )
        manifest_tool = next(
            item
            for item in dispatcher.manifest()["tools"]
            if item["name"] == "market.get_volume_series"
        )
        self.assertEqual(manifest_tool["version"], "1.0.0")
        self.assertEqual(manifest_tool["stores"], ["market"])
        self.assertEqual(
            manifest_tool["datasets"],
            [
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
                "market.stage10.instruments",
            ],
        )

    def test_as_of_instrument_identity_fails_closed_before_capture(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unavailable at the cutoff"):
            self.repository.get_close_series(
                self._query(mode="as_of", as_of="2026-08-15T00:59:59Z")
            )

        at_capture = self.repository.get_close_series(
            self._query(mode="as_of", as_of=_CAPTURED_AT)
        )
        self.assertEqual(len(at_capture.observations), 3)
        identity = at_capture.provenance["store_receipt"][
            "selected_instrument_identity"
        ]
        self.assertEqual(identity["captured_at"], _CAPTURED_AT)
        self.assertEqual(identity["captured_precision"], "datetime")
        self.assertEqual(identity["run_id"], "stage10_series_seed_run")
        self.assertEqual(at_capture.audit["point_in_time_status"], "safe")

    def test_latest_and_as_of_select_corrections_without_future_leakage(self) -> None:
        before_correction = self.repository.get_close_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        latest = self.repository.get_close_series(self._query())
        before_again = self.repository.get_close_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        after_correction = self.repository.get_close_series(
            self._query(mode="as_of", as_of="2026-08-15T04:00:00Z")
        )

        self.assertEqual(latest.observations[1].value, Decimal("108"))
        self.assertEqual(before_correction.observations[1].value, Decimal("103"))
        self.assertEqual(after_correction.observations[1].value, Decimal("108"))
        self.assertEqual(before_correction.audit["requested_mode"], "as_of")
        self.assertEqual(before_correction.audit["actual_mode"], "as_of")
        self.assertEqual(before_correction.audit["point_in_time_status"], "safe")
        self.assertEqual(
            before_correction.audit["point_in_time_scope"],
            "retained_local_captures",
        )
        self.assertIn(
            "point_in_time_safe_only_for_retained_local_captures",
            before_correction.warnings,
        )
        self.assertEqual(
            dumps_strict(before_correction.to_primitive()),
            dumps_strict(before_again.to_primitive()),
        )
        self.assertNotEqual(
            before_correction.observations[1].version_id,
            after_correction.observations[1].version_id,
        )

    def test_earlier_as_of_bytes_survive_a_second_future_correction(self) -> None:
        earlier = self.repository.get_close_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        self._publish(
            self._corrected_body(109),
            captured_at="2026-08-15T04:00:00Z",
        )
        repeated = self.repository.get_close_series(
            self._query(mode="as_of", as_of="2026-08-15T02:00:00Z")
        )
        self.assertEqual(
            dumps_strict(earlier.to_primitive()),
            dumps_strict(repeated.to_primitive()),
        )

    def test_trailing_forward_returns_and_quality_compose_read_only(self) -> None:
        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        engine = Stage10MarketReturnEngine(self.stores, self.registry)
        before = mutation_fingerprint(self.stores)

        source = self.repository.get_close_series(self._query())
        trailing = engine.calculate(self._return_request())
        forward = engine.calculate(self._return_request(direction="forward"))
        quality = audit_stage10_market_return(trailing)
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        trailing_observations = trailing.observations
        forward_observations = forward.observations
        with localcontext() as context:
            context.prec = 34
            first_return = Decimal("108") / Decimal("102") - Decimal("1")
            second_return = Decimal("104") / Decimal("108") - Decimal("1")
        self.assertEqual(
            [item.missing_reason for item in trailing_observations],
            ["insufficient_history", None, None],
        )
        self.assertEqual(
            [item.value for item in trailing_observations],
            [None, first_return, second_return],
        )
        self.assertEqual(
            [item.missing_reason for item in forward_observations],
            [None, None, "insufficient_forward_horizon"],
        )
        self.assertEqual(
            [item.value for item in forward_observations],
            [first_return, second_return, None],
        )
        self.assertEqual(
            forward_observations[0].available_at,
            "2026-08-15T03:00:00.000000Z",
        )
        self.assertEqual(trailing.metadata["unit"], "fraction")
        self.assertEqual(
            trailing.metadata["horizon_basis"],
            "observed_rows",
        )
        self.assertEqual(trailing.audit["missing_count"], 1)
        self.assertEqual(forward.audit["missing_count"], 1)
        self.assertIn(
            "adjustment_and_total_return_semantics_not_established",
            trailing.warnings,
        )
        self.assertIn(
            "forward_return_is_outcome_label",
            forward.warnings,
        )
        self.assertEqual(quality.missing_count, 1)
        self.assertEqual(quality.nonmissing_count, 2)
        self.assertEqual(quality.availability_basis, "local_capture")
        self.assertEqual(quality.mode, "latest")
        self.assertEqual(quality.requested_mode, "latest")
        self.assertEqual(quality.actual_mode, "latest")
        self.assertEqual(quality.point_in_time_status, "not_applicable")
        self.assertEqual(quality.return_direction, "trailing")
        self.assertEqual(quality.return_target_status, "historical_transform")
        self.assertEqual(quality.input_lineage_digest, trailing.lineage_digest)
        coerced_quality = replace(quality, unsafe_reasons=[])
        self.assertEqual(coerced_quality.unsafe_reasons, ())
        self.assertIsInstance(coerced_quality.unsafe_reasons, tuple)
        with self.assertRaises(ValidationError):
            replace(quality, availability_basis="source_evidenced")
        selected = trailing.provenance["derivation"]["source_selection"]
        self.assertEqual(
            tuple(item["version_id"] for item in selected["observations"]),
            tuple(item.version_id for item in source.observations),
        )
        dumps_strict(quality.to_primitive())

    def test_available_tickers_filter_order_truncate_and_remain_read_only(
        self,
    ) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            for symbol, instrument_id, seed in (
                ("MSFT", "stage10_series_msft", "c" * 64),
                ("ZZZZ", "stage10_series_no_price", "d" * 64),
            ):
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id, provider, provider_symbol, asset_type,
                        display_name, exchange_code, currency_segment,
                        first_trade_date, identity_seed_sha256, captured_at,
                        captured_precision, run_id
                    ) VALUES (?, 'fmp', ?, 'equity', ?, 'XNAS',
                              'provider_native', NULL, ?, ?, 'datetime', ?)
                    """,
                    (
                        instrument_id,
                        symbol,
                        f"{symbol} fixture",
                        seed,
                        _CAPTURED_AT,
                        "stage10_series_seed_run",
                    ),
                )
        msft_body = json.dumps(
            [
                {**row, "symbol": "MSFT"}
                for row in json.loads(self.body)
            ],
            separators=(",", ":"),
        ).encode("utf-8")
        response = Stage12BFixtureResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=msft_body,
            elapsed_seconds=1,
        )
        prepared = self.collector.prepare(
            replace(self._request(_CAPTURED_AT), symbol="MSFT"),
            response,
        )
        self.collector.publish(prepared)

        before = mutation_fingerprint(self.stores)
        all_rows = self.repository.list_available_tickers(
            Stage10AvailableTickerQuery()
        )
        limited = self.repository.list_available_tickers(
            Stage10AvailableTickerQuery(limit=1)
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(
            tuple(item.ticker for item in all_rows.tickers),
            ("AAPL", "MSFT"),
        )
        self.assertNotIn("ZZZZ", tuple(item.ticker for item in all_rows.tickers))
        self.assertFalse(all_rows.truncated)
        self.assertEqual(tuple(item.ticker for item in limited.tickers), ("AAPL",))
        self.assertTrue(limited.truncated)
        self.assertTrue(all_rows.semantic_id)
        self.assertTrue(all_rows.migration_ids)
        with self.assertRaises(ResourceLimitError):
            Stage10AvailableTickerQuery(limit=10_001)

        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        after_correction = self.repository.list_available_tickers(
            Stage10AvailableTickerQuery()
        )
        self.assertEqual(
            tuple(item.ticker for item in after_correction.tickers),
            ("AAPL", "MSFT"),
        )

    def test_public_available_ticker_defaults_http_parity_and_fail_before_read(
        self,
    ) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        declaration = self.registry.tool("market.get_available_ticker")
        self.assertEqual(declaration["input_schema"]["required"], [])
        self.assertEqual(declaration["examples"], [{}])

        before = mutation_fingerprint(self.stores)
        direct = dispatcher.call("market.get_available_ticker", {})
        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "market.get_available_ticker",
                    "arguments": {},
                }
            ).encode("utf-8"),
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(dumps_strict(payload["result"]), dumps_strict(direct))
        self.assertEqual(payload["tool"]["version"], "1.0.0")
        self.assertEqual(
            payload["receipt"]["operation_graph_id"],
            "tool_platform.market.get_available_ticker.v1",
        )
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(len(direct["records"]), 1)
        fields = {
            field["name"]: field["value"]
            for field in direct["records"][0]["fields"]
        }
        self.assertEqual(fields["ticker"], "AAPL")
        self.assertEqual(fields["provider"], "fmp")
        self.assertEqual(
            direct["records"][0]["record_type"],
            "stage10_available_ticker",
        )
        self.assertEqual(
            direct["diagnostics"][0]["code"],
            "stage10_available_ticker_inventory",
        )
        self.assertEqual(
            direct["warnings"][0]["code"],
            "current_universe_not_point_in_time",
        )
        self.assertEqual(direct["truncation"]["returned_count"], 1)
        self.assertEqual(direct["truncation"]["total_known_count"], 1)
        self.assertEqual(
            tuple(item["dataset_id"] for item in direct["lineage"]),
            (
                "market.stage10.instruments",
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
            ),
        )
        manifest_tool = next(
            item
            for item in dispatcher.manifest()["tools"]
            if item["name"] == "market.get_available_ticker"
        )
        self.assertEqual(manifest_tool["version"], "1.0.0")
        self.assertEqual(manifest_tool["stores"], ["market"])
        self.assertEqual(
            manifest_tool["datasets"],
            [
                "market.stage10.instruments",
                "market.stage10.daily_prices",
                "market.stage10.source_evidence",
            ],
        )

        self.assertIn(
            "omitted_limit_defaults_to_10000",
            manifest_tool["assumptions"],
        )
        invalid_requests = (
            {"database_path": "/tmp/forbidden.sqlite"},
            {"limit": 0},
            {"limit": True},
            {"limit": 10_001},
        )
        with patch(
            "quant_data.market.stage10_series._quiet_market_connection",
            side_effect=AssertionError("store reader must not run"),
        ) as reader:
            for invalid in invalid_requests:
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValidationError):
                        dispatcher.call("market.get_available_ticker", invalid)
        reader.assert_not_called()

    def test_public_price_series_omits_dates_and_matches_http_read_only(
        self,
    ) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        arguments = self._public_price_arguments()
        declaration = self.registry.tool("market.get_price_series")
        self.assertNotIn("start_date", declaration["input_schema"]["required"])
        self.assertNotIn("end_date", declaration["input_schema"]["required"])
        self.assertIn("start_date", declaration["input_schema"]["properties"])
        self.assertIn("end_date", declaration["input_schema"]["properties"])

        before = mutation_fingerprint(self.stores)
        direct = dispatcher.call("market.get_price_series", arguments)
        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "market.get_price_series",
                    "arguments": arguments,
                }
            ).encode("utf-8"),
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(dumps_strict(payload["result"]), dumps_strict(direct))
        self.assertEqual(payload["tool"]["version"], "1.0.0")
        self.assertEqual(
            payload["receipt"]["operation_graph_id"],
            "tool_platform.market.get_price_series.v1",
        )
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(
            tuple(
                item["metadata"]["observation_field"]
                for item in direct["series"]
            ),
            ("open", "high", "low", "close"),
        )
        self.assertEqual(direct["truncation"]["returned_count"], 3)
        self.assertEqual(len(direct["lineage"]), 4)
        self.assertEqual(
            direct["diagnostics"][0]["code"],
            "stage10_market_price_quality",
        )
        manifest_tool = next(
            item
            for item in dispatcher.manifest()["tools"]
            if item["name"] == "market.get_price_series"
        )
        self.assertEqual(manifest_tool["version"], "1.0.0")
        self.assertEqual(manifest_tool["stores"], ["market"])

    def test_public_price_series_invalid_inputs_fail_before_store_read(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        valid = self._public_price_arguments()
        invalid_requests = (
            {key: value for key, value in valid.items() if key != "ticker"},
            {**valid, "database_path": "/tmp/forbidden.sqlite"},
            {
                **valid,
                "start_date": "2026-08-12",
                "end_date": "2026-08-10",
            },
        )
        with patch(
            "quant_data.market.stage10_series._quiet_market_connection",
            side_effect=AssertionError("store reader must not run"),
        ) as reader:
            for invalid in invalid_requests:
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValidationError):
                        dispatcher.call("market.get_price_series", invalid)
        reader.assert_not_called()

    def test_public_v2_is_explicit_typed_and_direct_http_equivalent(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        arguments = self._public_v2_arguments()
        before = mutation_fingerprint(self.stores)

        direct = dispatcher.call(
            "market.get_returns", arguments, tool_version="2.0.0"
        )
        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "market.get_returns",
                    "tool_version": "2.0.0",
                    "arguments": arguments,
                }
            ).encode("utf-8"),
        )
        after = mutation_fingerprint(self.stores)

        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(dumps_strict(payload["result"]), dumps_strict(direct))
        self.assertEqual(payload["tool"]["version"], "2.0.0")
        self.assertEqual(payload["receipt"]["tool_version"], "2.0.0")
        self.assertEqual(
            payload["receipt"]["operation_graph_id"],
            "tool_platform.market.get_returns.v2",
        )
        self.assertEqual(payload["receipt"]["deprecation_warnings"], [])
        self.assertEqual(direct["status"], "ok")
        self.assertEqual(len(direct["series"]), 1)
        self.assertEqual(
            [item["missing_reason"] for item in direct["series"][0]["observations"]],
            ["insufficient_history", None, None],
        )
        self.assertEqual(
            direct["series"][0]["audit"]["point_in_time_status"],
            "not_applicable",
        )
        self.assertEqual(
            direct["diagnostics"][0]["code"], "stage10_market_return_quality"
        )

    def test_public_v2_description_composes_real_returns_without_store_access(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        returns = dispatcher.call(
            "market.get_returns",
            self._public_v2_arguments(),
            tool_version="2.0.0",
        )
        before = mutation_fingerprint(self.stores)
        with patch(
            "quant_data.market.stage10_series._quiet_market_connection",
            side_effect=AssertionError("composable statistic must not open a store"),
        ) as reader:
            result = dispatcher.call(
                "timeseries.describe",
                {"series": returns["series"][0], "limit": 100},
                tool_version="2.0.0",
            )
        after = mutation_fingerprint(self.stores)

        reader.assert_not_called()
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertEqual(result["status"], "ok")
        fields = {
            item["name"]: item["value"]
            for item in result["records"][0]["fields"]
        }
        observations = returns["series"][0]["observations"]
        known = tuple(
            item["value"] for item in observations if item["value"] is not None
        )
        with localcontext() as context:
            context.prec = 34
            expected_mean = sum(known, Decimal("0")) / Decimal(len(known))
            expected_variance = sum(
                ((item - expected_mean) ** 2 for item in known), Decimal("0")
            ) / Decimal(len(known) - 1)
        self.assertEqual(fields["count"], 3)
        self.assertEqual(fields["nonmissing_count"], 2)
        self.assertEqual(fields["missing_count"], 1)
        self.assertEqual(fields["mean"], expected_mean)
        self.assertEqual(fields["sample_variance"], expected_variance)
        self.assertEqual(
            result["lineage"][0]["semantic_id"],
            returns["series"][0]["lineage_digest"],
        )

    def test_public_v2_returns_survive_ordinary_json_round_trip(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        returns = dispatcher.call(
            "market.get_returns",
            self._public_v2_arguments(),
            tool_version="2.0.0",
        )
        series = returns["series"][0]
        round_tripped = loads_strict(json.dumps(json.loads(dumps_strict(series))))

        direct = dispatcher.call(
            "timeseries.describe",
            {"series": series, "limit": 100},
            tool_version="2.0.0",
        )
        composed = dispatcher.call(
            "timeseries.describe",
            {"series": round_tripped, "limit": 100},
            tool_version="2.0.0",
        )

        self.assertEqual(dumps_strict(composed), dumps_strict(direct))
        observations = round_tripped["observations"]
        self.assertFalse(
            any(
                flag.startswith("exact_decimal_value:")
                for flag in observations[0]["quality_flags"]
            )
        )
        self.assertTrue(
            all(
                any(
                    flag.startswith("exact_decimal_value:")
                    for flag in item["quality_flags"]
                )
                for item in observations[1:]
            )
        )

    def test_public_v2_exact_decimal_metadata_rejects_tampering(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        returns = dispatcher.call(
            "market.get_returns",
            self._public_v2_arguments(),
            tool_version="2.0.0",
        )
        wire = json.dumps(json.loads(dumps_strict(returns["series"][0])))

        changed_value = loads_strict(wire)
        changed_value["observations"][1]["value"] = Decimal("0.5")
        with self.assertRaisesRegex(
            ValidationError, "does not match exact decimal metadata"
        ):
            dispatcher.call(
                "timeseries.describe",
                {"series": changed_value, "limit": 100},
                tool_version="2.0.0",
            )

        malformed_metadata = loads_strict(wire)
        flags = malformed_metadata["observations"][1]["quality_flags"]
        exact_index = next(
            index
            for index, flag in enumerate(flags)
            if flag.startswith("exact_decimal_value:")
        )
        flags[exact_index] = "exact_decimal_value:not-a-decimal"
        with self.assertRaisesRegex(ValidationError, "metadata is invalid"):
            dispatcher.call(
                "timeseries.describe",
                {"series": malformed_metadata, "limit": 100},
                tool_version="2.0.0",
            )

    def test_public_v1_default_is_unchanged_and_deprecation_is_out_of_band(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        declaration = self.registry.tool("market.get_returns")
        arguments = declaration["examples"][0]
        omitted = dispatcher.call("market.get_returns", arguments)
        explicit = dispatcher.call(
            "market.get_returns", arguments, tool_version="1.0.0"
        )
        self.assertEqual(dumps_strict(omitted), dumps_strict(explicit))
        self.assertEqual(omitted["status"], "not_established")
        receipt = dispatcher.receipt_for(
            "market.get_returns",
            arguments,
            explicit,
            tool_version="1.0.0",
        ).to_primitive()
        self.assertEqual(receipt["tool_version"], "1.0.0")
        self.assertEqual(
            receipt["deprecation_warnings"][0]["replacement"],
            {"tool": "market.get_returns", "version": "2.0.0"},
        )
        manifest_tool = next(
            item
            for item in dispatcher.manifest()["tools"]
            if item["name"] == "market.get_returns"
        )
        self.assertEqual(manifest_tool["version"], "1.0.0")
        self.assertEqual(
            [item["version"] for item in manifest_tool["versions"]],
            ["1.0.0", "2.0.0"],
        )
        self.assertEqual(
            manifest_tool["deprecation"], receipt["deprecation_warnings"][0]
        )

    def test_public_v2_forward_as_of_preserves_pit_and_truncation_contracts(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        result = dispatcher.call(
            "market.get_forward_returns",
            self._public_v2_arguments(
                mode="as_of",
                as_of=_CAPTURED_AT,
                limit=2,
            ),
            tool_version="2.0.0",
        )
        series = result["series"][0]
        self.assertEqual(series["metadata"]["return_direction"], "forward")
        self.assertEqual(series["metadata"]["return_target_status"], "outcome_label")
        self.assertEqual(series["audit"]["point_in_time_status"], "safe")
        self.assertEqual(
            series["audit"]["point_in_time_scope"], "retained_local_captures"
        )
        self.assertEqual(series["audit"]["source_selected_count"], 3)
        self.assertTrue(series["truncated"])
        self.assertEqual(result["truncation"]["returned_count"], 2)
        self.assertTrue(result["truncation"]["has_more"])
        self.assertIn(
            "forward_return_is_outcome_label",
            [item["code"] for item in result["warnings"]],
        )
        described = dispatcher.call(
            "timeseries.describe",
            {"series": series, "limit": 100},
            tool_version="2.0.0",
        )
        self.assertEqual(described["status"], "ok")
        self.assertIn(
            "forward_return_inputs_are_outcome_labels",
            [item["code"] for item in described["warnings"]],
        )

    def test_public_v2_selector_and_forward_bound_fail_before_store_read(self) -> None:
        dispatcher = ToolDispatcher(self.stores, self.registry)
        with patch(
            "quant_data.market.stage10_series._quiet_market_connection",
            side_effect=AssertionError("store reader must not run"),
        ) as reader:
            with self.assertRaises(UnsupportedToolVersionError):
                dispatcher.call(
                    "market.get_returns", {}, tool_version="3.0.0"
                )
            with self.assertRaises(ResourceLimitError):
                dispatcher.call(
                    "market.get_forward_returns",
                    self._public_v2_arguments(limit=10_000, horizon=1),
                    tool_version="2.0.0",
                )
        reader.assert_not_called()

    def test_log_and_multirow_horizons_match_decimal_references(self) -> None:
        engine = Stage10MarketReturnEngine(self.stores, self.registry)
        logarithmic = engine.calculate(self._return_request(method="log"))
        trailing_two = engine.calculate(self._return_request(horizon=2))
        forward_two = engine.calculate(
            self._return_request(direction="forward", horizon=2)
        )

        with localcontext() as context:
            context.prec = 34
            expected_log = (Decimal("103") / Decimal("102")).ln()
            expected_two = Decimal("104") / Decimal("102") - Decimal("1")
        self.assertEqual(logarithmic.observations[1].value, expected_log)
        self.assertEqual(
            tuple(item.missing_reason for item in trailing_two.observations),
            ("insufficient_history", "insufficient_history", None),
        )
        self.assertEqual(trailing_two.observations[2].value, expected_two)
        self.assertEqual(trailing_two.audit["missing_count"], 2)
        self.assertEqual(forward_two.observations[0].value, expected_two)
        self.assertEqual(
            tuple(item.missing_reason for item in forward_two.observations),
            (None, "insufficient_forward_horizon", "insufficient_forward_horizon"),
        )
        self.assertEqual(forward_two.audit["missing_count"], 2)
        self.assertEqual(
            audit_stage10_market_return(forward_two).missing_count,
            2,
        )
        self.assertEqual(
            audit_stage10_market_return(forward_two).return_target_status,
            "outcome_label",
        )

    def test_return_engine_as_of_is_future_stable_and_truncation_propagates(self) -> None:
        engine = Stage10MarketReturnEngine(self.stores, self.registry)
        request = self._return_request(
            mode="as_of",
            as_of="2026-08-15T02:00:00Z",
        )
        earlier = engine.calculate(request)
        earlier_quality = audit_stage10_market_return(earlier)
        self._publish(
            self._corrected_body(108),
            captured_at="2026-08-15T03:00:00Z",
        )
        repeated = engine.calculate(request)
        full_forward = engine.calculate(
            self._return_request(direction="forward")
        )
        truncated = engine.calculate(
            self._return_request(direction="forward", limit=2)
        )

        self.assertEqual(
            dumps_strict(earlier.to_primitive()),
            dumps_strict(repeated.to_primitive()),
        )
        self.assertEqual(earlier_quality.mode, "as_of")
        self.assertEqual(earlier_quality.requested_mode, "as_of")
        self.assertEqual(earlier_quality.actual_mode, "as_of")
        self.assertEqual(earlier_quality.cutoff, "2026-08-15T02:00:00Z")
        self.assertEqual(earlier_quality.availability_basis, "local_capture")
        self.assertEqual(earlier_quality.point_in_time_status, "safe")
        self.assertEqual(
            earlier_quality.point_in_time_scope,
            "retained_local_captures",
        )
        earlier_ids = tuple(
            item["version_id"]
            for item in earlier.provenance["derivation"]["source_selection"][
                "observations"
            ]
        )
        repeated_ids = tuple(
            item["version_id"]
            for item in repeated.provenance["derivation"]["source_selection"][
                "observations"
            ]
        )
        latest_ids = tuple(
            item["version_id"]
            for item in full_forward.provenance["derivation"]["source_selection"][
                "observations"
            ]
        )
        self.assertEqual(earlier_ids, repeated_ids)
        self.assertNotEqual(earlier_ids, latest_ids)
        self.assertTrue(truncated.truncated)
        self.assertTrue(truncated.audit["truncated"])
        self.assertEqual(len(truncated.observations), 2)
        self.assertEqual(
            tuple(
                (
                    item.period_start,
                    item.value,
                    item.missing_reason,
                    item.available_at,
                )
                for item in truncated.observations
            ),
            tuple(
                (
                    item.period_start,
                    item.value,
                    item.missing_reason,
                    item.available_at,
                )
                for item in full_forward.observations[:2]
            ),
        )
        self.assertEqual(
            tuple(item.version_id for item in truncated.observations),
            tuple(item.version_id for item in full_forward.observations[:2]),
        )
        self.assertEqual(
            tuple(
                dumps_strict(item.to_primitive())
                for item in truncated.observations
            ),
            tuple(
                dumps_strict(item.to_primitive())
                for item in full_forward.observations[:2]
            ),
        )
        self.assertIsNotNone(truncated.observations[1].value)
        self.assertEqual(truncated.audit["source_selection_limit"], 3)
        truncated_quality = audit_stage10_market_return(truncated)
        self.assertTrue(truncated_quality.truncated)
        self.assertEqual(truncated_quality.limit, 2)
        self.assertEqual(truncated_quality.selected_count, 2)
        self.assertEqual(
            len(
                truncated.provenance["derivation"]["source_selection"][
                    "observations"
                ]
            ),
            3,
        )

    def test_bounds_truncation_and_unsupported_modes_fail_closed(self) -> None:
        truncated = self.repository.get_close_series(self._query(limit=2))
        self.assertTrue(truncated.truncated)
        self.assertEqual(len(truncated.observations), 2)
        self.assertIn("result_truncated", truncated.warnings)

        stable = self.repository.get_close_series(
            self._query(
                identifier="stage10_series_aapl",
                identifier_kind="instrument_id",
            )
        )
        self.assertEqual(len(stable.observations), 3)
        with self.assertRaises(ValidationError):
            self.repository.get_close_series(self._query(identifier="MISSING"))
        with self.assertRaises(ValidationError):
            Stage10DailyPriceQuery(
                identifier="AAPL",
                identifier_kind="provider_symbol",
                start_date="2026-08-10",
                end_date="2026-08-12",
                mode="first_release",
                as_of=None,
                date_only_policy="completed_date",
                limit=100,
            )
        with self.assertRaises(ResourceLimitError):
            self._query(limit=10_001)
        with self.assertRaises(ResourceLimitError):
            Stage10MarketReturnRequest(
                prices=self._query(limit=10_000),
                direction="forward",
                method="simple",
                horizon=1,
            )

        for direction, method, horizon in (
            ("calendar", "simple", 1),
            ("trailing", "arithmetic", 1),
            ("trailing", "simple", 0),
            ("trailing", "simple", True),
            ("trailing", "simple", 253),
        ):
            with self.subTest(
                direction=direction,
                method=method,
                horizon=horizon,
            ):
                with self.assertRaises(ValidationError):
                    Stage10MarketReturnRequest(
                        prices=self._query(),
                        direction=direction,
                        method=method,
                        horizon=horizon,
                    )
        with self.assertRaises(ValidationError):
            Stage10MarketReturnEngine(self.stores, self.registry).calculate(  # type: ignore[arg-type]
                "not-a-request"
            )

    def test_immutable_reader_rejects_busy_or_hardlinked_store(self) -> None:
        journal = Path(str(self.stores.market) + "-journal")
        journal.write_bytes(b"busy")
        try:
            with self.assertRaises(StoreUnavailableError):
                self.repository.get_close_series(self._query())
        finally:
            journal.unlink()

        sidecar_target = self.root / "sidecar-target"
        sidecar_target.write_bytes(b"")
        journal.symlink_to(sidecar_target)
        try:
            with self.assertRaises(StoreUnavailableError):
                self.repository.get_close_series(self._query())
        finally:
            journal.unlink()
            sidecar_target.unlink()

        hardlink = self.root / "market-hardlink.sqlite"
        os.link(self.stores.market, hardlink)
        try:
            with self.assertRaises(StoreUnavailableError):
                self.repository.get_close_series(self._query())
        finally:
            hardlink.unlink()

    def test_immutable_reader_rejects_tampered_migration_digest(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute(
                "UPDATE schema_migrations SET sha256=? WHERE ordinal=10",
                ("0" * 64,),
            )

        with self.assertRaises(StoreUnavailableError):
            self.repository.get_close_series(self._query())

    def test_immutable_reader_rejects_wrong_migration_resource(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute("DROP TRIGGER schema_migrations_immutable_update")
            connection.execute(
                "UPDATE schema_migrations SET resource=? WHERE ordinal=10",
                ("quant_data/migrations/market/tampered.sql",),
            )

        with self.assertRaises(StoreUnavailableError):
            self.repository.get_close_series(self._query())

    def test_immutable_reader_rejects_dataset_registration_drift(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute("DROP TRIGGER dataset_registry_contract_immutable")
            connection.execute(
                "UPDATE dataset_registry SET relations_json='[]' WHERE dataset_id=?",
                (DATASET_ID,),
            )

        with self.assertRaises(StoreUnavailableError):
            self.repository.get_close_series(self._query())


class IsolatedPublicStage10VolumeSeriesTests(unittest.TestCase):
    """Public volume regression coverage without the project-data runtime guard."""

    def test_as_of_limit_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            stores = explicit_store_map(root / "stores")
            registry = load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
            initialize_all(stores, registry, applied_at="2026-08-15T00:00:00Z")
            with writer_connection(stores, StoreRole.MARKET) as connection:
                connection.execute(
                    """
                    INSERT INTO ingestion_runs(
                        run_id, dataset_id, semantic_identity, command, scope_json,
                        status, started_at, code_version
                    ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (
                        "isolated_volume_seed_run",
                        "market.stage10.instruments",
                        "c" * 64,
                        "fixture.seed",
                        "{}",
                        _CAPTURED_AT,
                        "fixture",
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id, provider, provider_symbol, asset_type,
                        display_name, exchange_code, currency_segment,
                        first_trade_date, identity_seed_sha256, captured_at,
                        captured_precision, run_id
                    ) VALUES (?, 'fmp', 'AAPL', 'equity', 'AAPL fixture', 'XNAS',
                              'provider_native', NULL, ?, ?, 'datetime', ?)
                    """,
                    (
                        "isolated_volume_aapl",
                        "d" * 64,
                        _CAPTURED_AT,
                        "isolated_volume_seed_run",
                    ),
                )

            collector = Stage12BIncrementalCollector(
                fixture_root=root,
                market_store=stores.market,
                scope=load_stage12b_incremental_market_v1_scope(_STAGE12B_SCOPE),
                stage12a_scope=load_stage12_market_v1_scope(_STAGE12A_SCOPE),
                stage12a_scope_source=_STAGE12A_SCOPE,
            )

            def publish(body: bytes, *, captured_at: str) -> None:
                collector.publish(
                    collector.prepare(
                        Stage12BFixtureRequest(
                            symbol="AAPL",
                            from_date="2026-08-10",
                            to_date="2026-08-12",
                            session_dates=(
                                "2026-08-10",
                                "2026-08-11",
                                "2026-08-12",
                            ),
                            captured_at=captured_at,
                        ),
                        Stage12BFixtureResponse(
                            status=200,
                            media_type="application/json; charset=utf-8",
                            body=body,
                            elapsed_seconds=1,
                        ),
                    )
                )

            initial_body = _FIXTURE.read_bytes()
            publish(initial_body, captured_at=_CAPTURED_AT)
            corrected_rows = json.loads(initial_body)
            corrected_rows[0]["volume"] = 0
            publish(
                json.dumps(corrected_rows, separators=(",", ":")).encode("utf-8"),
                captured_at="2026-08-15T03:00:00Z",
            )

            dispatcher = ToolDispatcher(stores, registry)
            as_of_arguments = {
                "ticker": "AAPL",
                "mode": "as_of",
                "as_of": "2026-08-15T02:00:00Z",
                "date_only_policy": "completed_date",
                "limit": 1,
            }
            before = mutation_fingerprint(stores)
            selected = dispatcher.call("market.get_volume_series", as_of_arguments)
            repeated = dispatcher.call("market.get_volume_series", as_of_arguments)
            latest = dispatcher.call(
                "market.get_volume_series",
                {
                    **as_of_arguments,
                    "mode": "latest",
                    "as_of": None,
                },
            )
            after = mutation_fingerprint(stores)

            self.assertEqual(before["sha256"], after["sha256"])
            self.assertEqual(dumps_strict(selected), dumps_strict(repeated))
            self.assertTrue(selected["truncation"]["applied"])
            self.assertEqual(selected["truncation"]["limit"], 1)
            self.assertEqual(selected["truncation"]["returned_count"], 1)
            self.assertTrue(selected["truncation"]["has_more"])
            series = selected["series"][0]
            self.assertEqual(series["audit"]["mode"], "as_of")
            self.assertEqual(series["audit"]["cutoff"], "2026-08-15T02:00:00Z")
            self.assertEqual(series["audit"]["point_in_time_status"], "safe")
            self.assertEqual(series["observations"][0]["period_start"], "2026-08-10")
            self.assertEqual(series["observations"][0]["value"], Decimal("1000"))
            self.assertEqual(latest["series"][0]["observations"][0]["value"], Decimal("0"))
            self.assertNotEqual(
                series["observations"][0]["version_id"],
                latest["series"][0]["observations"][0]["version_id"],
            )
            self.assertEqual(
                tuple(item["dataset_id"] for item in selected["lineage"]),
                (
                    "market.stage10.daily_prices",
                    "market.stage10.source_evidence",
                    "market.stage10.instruments",
                ),
            )


if __name__ == "__main__":
    unittest.main()
