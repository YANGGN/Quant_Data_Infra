from __future__ import annotations

from dataclasses import FrozenInstanceError
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.stage12_scope import (
    REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256,
    STAGE12_SCOPE_CONTRACT,
    STAGE12_TARGET_PROFILE_ID,
    load_stage12_market_v1_scope,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"


def _raw_scope() -> dict[str, object]:
    raw = loads_strict(SCOPE_PATH.read_bytes())
    if not isinstance(raw, dict):
        raise AssertionError("Stage 12A scope fixture must be an object")
    return raw


class Stage12MarketV1ScopeTests(unittest.TestCase):
    def _load(self, raw: object):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "scope.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            return load_stage12_market_v1_scope(path)

    def test_reviewed_scope_is_typed_immutable_and_freezes_explicit_roster(self) -> None:
        raw = _raw_scope()
        scope = load_stage12_market_v1_scope(SCOPE_PATH)

        self.assertEqual(scope.manifest_mapping(), raw)
        self.assertEqual(scope.manifest_sha256, REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256)
        self.assertEqual(scope.contract, STAGE12_SCOPE_CONTRACT)
        self.assertEqual(scope.target_profile_id, STAGE12_TARGET_PROFILE_ID)
        self.assertEqual(scope.baseline.frozen_symbol_count, 629)
        self.assertEqual(scope.baseline.coverage_end_date, "2026-08-12")
        self.assertEqual(scope.baseline.price_data, "fmp_daily_provider_native_ohlcv")
        self.assertEqual(scope.baseline.price_variant, "fmp_full_eod_v1")
        self.assertEqual(scope.baseline.history_policy, "earliest_provider_returned_per_symbol")
        self.assertEqual(len(scope.roster), 629)
        self.assertEqual(scope.roster_sha256, "a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb")
        self.assertEqual(
            {
                kind: sum(item.asset_type == kind for item in scope.roster)
                for kind in ("equity", "etf", "index")
            },
            {"equity": 519, "etf": 95, "index": 15},
        )
        symbols = tuple(item.symbol for item in scope.roster)
        self.assertEqual(symbols, tuple(sorted(symbols)))
        self.assertEqual(len(set(symbols)), 629)
        self.assertEqual(symbols[:3], ("A", "AAPL", "ABBV"))
        self.assertEqual(symbols[-3:], ("^STOXX50E", "^TWII", "^VIX"))
        self.assertNotIn("IWM", symbols)
        self.assertNotIn("^RUT", symbols)
        self.assertEqual(
            scope.source_bindings.resume_artifact_sha256,
            "1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795",
        )
        self.assertEqual(
            scope.source_bindings.stage10_scope_manifest_sha256,
            "0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd",
        )
        self.assertEqual(scope.store_defaults["market"], "data/market.sqlite")
        with self.assertRaises(TypeError):
            scope.store_defaults["market"] = "data/other.sqlite"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            scope.baseline.price_variant = "other"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            scope.roster[0].symbol = "OTHER"  # type: ignore[misc]

    def test_rejects_unknown_secret_broadened_and_drifted_scope(self) -> None:
        cases: list[tuple[str, object]] = []

        unknown = _raw_scope()
        unknown["unreviewed"] = True
        cases.append(("unknown field", unknown))

        secret = _raw_scope()
        secret["api_key"] = "not-allowed"
        cases.append(("secret field", secret))

        wrong_market_path = _raw_scope()
        wrong_market_path["store_defaults"]["market"] = "data/market_data.sqlite"  # type: ignore[index]
        cases.append(("wrong canonical market path", wrong_market_path))

        broadened_claims = _raw_scope()
        broadened_claims["excluded_claims"].pop()  # type: ignore[index]
        cases.append(("broadened claim", broadened_claims))

        wrong_price_variant = _raw_scope()
        wrong_price_variant["baseline"]["price_variant"] = "other"  # type: ignore[index]
        cases.append(("price variant drift", wrong_price_variant))

        reordered_roster = _raw_scope()
        reordered_roster["roster"][0], reordered_roster["roster"][1] = (  # type: ignore[index]
            reordered_roster["roster"][1],  # type: ignore[index]
            reordered_roster["roster"][0],  # type: ignore[index]
        )
        cases.append(("reordered roster", reordered_roster))

        forbidden_symbol = _raw_scope()
        forbidden_symbol["roster"][0]["symbol"] = "IWM"  # type: ignore[index]
        cases.append(("explicitly excluded symbol", forbidden_symbol))

        wrong_asset_type = _raw_scope()
        wrong_asset_type["roster"][0]["asset_type"] = "etf"  # type: ignore[index]
        cases.append(("asset-type count drift", wrong_asset_type))

        source_drift = _raw_scope()
        source_drift["source_bindings"]["resume_artifact_sha256"] = "0" * 64  # type: ignore[index]
        cases.append(("source binding drift", source_drift))

        duplicate_roster = _raw_scope()
        duplicate_roster["roster"][-1]["symbol"] = duplicate_roster["roster"][-2]["symbol"]  # type: ignore[index]
        cases.append(("duplicate roster symbol", duplicate_roster))

        wrong_roster_count = _raw_scope()
        wrong_roster_count["roster"].pop()  # type: ignore[index]
        cases.append(("wrong roster count", wrong_roster_count))

        for name, invalid_path in (
            ("absolute store path", "/tmp/market.sqlite"),
            ("traversing store path", "../market.sqlite"),
            ("backslash store path", "data\\market.sqlite"),
        ):
            wrong_path = _raw_scope()
            wrong_path["store_defaults"]["market"] = invalid_path  # type: ignore[index]
            cases.append((name, wrong_path))

        reordered_phases = _raw_scope()
        phases = reordered_phases["successor_phases"]
        phases[0], phases[1] = phases[1], phases[0]  # type: ignore[index]
        cases.append(("reordered successor phases", reordered_phases))

        opened_phase = _raw_scope()
        opened_phase["successor_phases"][0]["status"] = "authorized"  # type: ignore[index]
        cases.append(("opened successor phase", opened_phase))

        digest_drift = _raw_scope()
        digest_drift["roster"][1]["symbol"] = "AA"  # type: ignore[index]
        cases.append(("manifest digest drift", digest_drift))

        for name, raw in cases:
            with self.subTest(case=name):
                with self.assertRaises(ValidationError):
                    self._load(raw)

    def test_requires_explicit_available_and_bounded_scope_path(self) -> None:
        with self.assertRaises(ValidationError):
            load_stage12_market_v1_scope("")
        with self.assertRaises(ValidationError):
            load_stage12_market_v1_scope(object())  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            directory_path = Path(directory)
            with self.assertRaises(ValidationError):
                load_stage12_market_v1_scope(directory_path / "missing.json")
            oversized = directory_path / "oversized.json"
            oversized.write_bytes(SCOPE_PATH.read_bytes() + b" " * (64 * 1024))
            with self.assertRaises(ResourceLimitError):
                load_stage12_market_v1_scope(oversized)


if __name__ == "__main__":
    unittest.main()
