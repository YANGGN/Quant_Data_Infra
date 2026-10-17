from __future__ import annotations

from dataclasses import FrozenInstanceError
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    Stage10MarketScope,
    load_stage10_market_scope,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"


class Stage10ScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _payload() -> dict[str, object]:
        raw = loads_strict(SCOPE_PATH.read_bytes())
        assert isinstance(raw, dict)
        return raw

    def _write_payload(self, payload: dict[str, object], *, name: str = "scope.json") -> Path:
        path = self.root / name
        path.write_text(dumps_strict(payload), encoding="utf-8")
        return path

    def _assert_rejected(self, payload: dict[str, object]) -> None:
        with self.assertRaises(ValidationError):
            load_stage10_market_scope(self._write_payload(payload))

    @staticmethod
    def _etf(payload: dict[str, object], symbol: str) -> dict[str, object]:
        etfs = payload["curated_etfs"]
        assert isinstance(etfs, list)
        for item in etfs:
            assert isinstance(item, dict)
            if item["symbol"] == symbol:
                return item
        raise AssertionError(f"ETF {symbol} was not found")

    @staticmethod
    def _index(payload: dict[str, object], canonical_id: str) -> dict[str, object]:
        indexes = payload["major_indexes"]
        assert isinstance(indexes, list)
        for item in indexes:
            assert isinstance(item, dict)
            if item["canonical_id"] == canonical_id:
                return item
        raise AssertionError(f"Index {canonical_id} was not found")

    def test_reviewed_scope_loads_as_frozen_typed_data_with_stable_manifest(self) -> None:
        first = load_stage10_market_scope(SCOPE_PATH)
        second = load_stage10_market_scope(self._write_payload(self._payload()))
        self.assertIsInstance(first, Stage10MarketScope)
        self.assertEqual(first, second)
        self.assertEqual(first.manifest_sha256, REVIEWED_STAGE10_MANIFEST_SHA256)
        self.assertEqual(len(first.universe_sources), 3)
        self.assertEqual(
            tuple(item.id for item in first.universe_sources),
            ("sp500_current", "nasdaq100_current", "dow30_current"),
        )
        self.assertEqual(len(first.curated_etfs), 95)
        self.assertEqual(len(first.major_indexes), 15)
        etf_symbols = {item.symbol for item in first.curated_etfs}
        self.assertIn("IJR", etf_symbols)
        self.assertNotIn("IWM", etf_symbols)
        self.assertNotIn("^RUT", {item.provider_symbol for item in first.major_indexes})
        self.assertEqual(first.price_history.endpoint_path, "/stable/historical-price-eod/full")

    def test_returned_scope_and_nested_records_are_immutable(self) -> None:
        scope = load_stage10_market_scope(SCOPE_PATH)
        with self.assertRaises(FrozenInstanceError):
            scope.provider = "other"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            scope.curated_etfs[0].symbol = "OTHER"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            scope.bounds.max_instruments = 0  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            scope.curated_etfs += ()  # type: ignore[misc]

    def test_russell_constituent_universe_is_explicitly_rejected(self) -> None:
        payload = self._payload()
        sources = payload["universe_sources"]
        assert isinstance(sources, list)
        assert isinstance(sources[1], dict)
        sources[1]["canonical_index_id"] = "russell1000"
        self._assert_rejected(payload)

    def test_russell_major_index_is_explicitly_rejected(self) -> None:
        payload = self._payload()
        index = self._index(payload, "vix")
        index["canonical_id"] = "russell2000_price"
        index["provider_symbol"] = "^RUT"
        self._assert_rejected(payload)

    def test_duplicate_curated_etf_symbol_is_rejected(self) -> None:
        payload = self._payload()
        self._etf(payload, "QQQ")["symbol"] = "SPY"
        self._assert_rejected(payload)

    def test_altered_bounds_are_rejected(self) -> None:
        payload = self._payload()
        bounds = payload["bounds"]
        assert isinstance(bounds, dict)
        bounds["max_instruments"] = 801
        self._assert_rejected(payload)

    def test_dram_and_euv_category_date_contracts_are_rejected_when_altered(self) -> None:
        with self.subTest(case="dram_date"):
            payload = self._payload()
            self._etf(payload, "DRAM")["first_trade_date"] = "2026-04-03"
            self._assert_rejected(payload)
        with self.subTest(case="euv_category"):
            payload = self._payload()
            self._etf(payload, "EUV")["category"] = "asset_class_equity_us"
            self._assert_rejected(payload)

    def test_bad_major_index_symbol_is_rejected(self) -> None:
        payload = self._payload()
        self._index(payload, "sp500_price")["provider_symbol"] = "^SPY"
        self._assert_rejected(payload)

    def test_closed_schema_rejects_unknown_and_sensitive_request_fields(self) -> None:
        with self.subTest(case="ordinary_unknown"):
            payload = self._payload()
            payload["unreviewed"] = True
            self._assert_rejected(payload)
        for key in ("api_key", "request_headers", "request_url", "database_path"):
            with self.subTest(case=key):
                payload = self._payload()
                price_history = payload["price_history"]
                assert isinstance(price_history, dict)
                price_history[key] = "not-allowed"
                self._assert_rejected(payload)

    def test_invalid_utf8_and_malformed_json_are_rejected(self) -> None:
        invalid_utf8 = self.root / "invalid-utf8.json"
        invalid_utf8.write_bytes(b"\xff")
        malformed = self.root / "malformed.json"
        malformed.write_bytes(b"{")
        for path in (invalid_utf8, malformed):
            with self.subTest(path=path.name):
                with self.assertRaises(ValidationError):
                    load_stage10_market_scope(path)


if __name__ == "__main__":
    unittest.main()
