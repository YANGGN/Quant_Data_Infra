from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage11_scope import (
    REVIEWED_STAGE11_MACRO_SCOPE_SHA256,
    STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT,
    STAGE11_REQUIRED_MARKET_PROFILE_ID,
    STAGE11_SCOPE_CONTRACT,
    STAGE11_TARGET_PROFILE_ID,
    STAGE11_TARGET_ROOT,
    load_stage11_macro_scope,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage11_macro_scope.json"


def _raw_scope() -> dict[str, object]:
    raw = loads_strict(SCOPE_PATH.read_bytes())
    if not isinstance(raw, dict):  # defensive guard for the test fixture itself
        raise AssertionError("Stage 11 scope fixture must be an object")
    return raw


class Stage11MacroScopeTests(unittest.TestCase):
    def _load(self, raw: object):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            path = Path(directory) / "scope.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            return load_stage11_macro_scope(path)

    def test_reviewed_scope_is_typed_immutable_and_exact(self) -> None:
        raw = _raw_scope()
        scope = load_stage11_macro_scope(SCOPE_PATH)

        self.assertEqual(scope.manifest_mapping(), raw)
        self.assertEqual(scope.manifest_sha256, REVIEWED_STAGE11_MACRO_SCOPE_SHA256)
        self.assertEqual(scope.contract, STAGE11_SCOPE_CONTRACT)
        self.assertEqual(scope.version, "1.0.0")
        self.assertEqual(scope.target_profile_id, STAGE11_TARGET_PROFILE_ID)
        self.assertEqual(scope.target_root, STAGE11_TARGET_ROOT)
        self.assertEqual(
            scope.dependency.required_market_candidate_contract,
            STAGE11_REQUIRED_MARKET_CANDIDATE_CONTRACT,
        )
        self.assertEqual(
            scope.dependency.required_market_profile_id,
            STAGE11_REQUIRED_MARKET_PROFILE_ID,
        )
        self.assertEqual(
            scope.bounds.manifest_mapping(),
            {
                "bea_max_bytes_per_response": 8_388_608,
                "bea_max_requests": 2,
                "bea_max_rows_per_series": 1_000,
                "eia_max_bytes_per_response": 16_777_216,
                "eia_max_pages": 8,
                "eia_max_rows_per_page": 5_000,
                "eia_weekly_max_rows": 5_000,
                "minimum_request_interval_milliseconds": 1_000,
            },
        )
        self.assertEqual(scope.bea.configuration_env, "BEA_API_KEY")
        self.assertEqual(scope.bea.host, "apps.bea.gov")
        self.assertEqual(
            tuple(
                (
                    request.path,
                    request.method,
                    request.dataset_name,
                    request.table_name,
                    request.series[0].provider_series_code,
                    request.series[0].canonical_series_id,
                    request.frequency,
                    request.year,
                    request.result_format,
                )
                for request in scope.bea.requests
            ),
            (
                (
                    "/api/data/",
                    "GetData",
                    "NIPA",
                    "T10101",
                    "A191RL",
                    "macro.gdp.real_qoq_saar_pct",
                    "Q",
                    "ALL",
                    "JSON",
                ),
                (
                    "/api/data/",
                    "GetData",
                    "NIPA",
                    "T10105",
                    "A191RC",
                    "macro.gdp.nominal_billions",
                    "Q",
                    "ALL",
                    "JSON",
                ),
            ),
        )
        self.assertEqual(scope.eia.configuration_env, "EIA_API_KEY")
        self.assertEqual(scope.eia.host, "api.eia.gov")
        self.assertEqual(scope.eia.retail.path, "/v2/electricity/retail-sales/data")
        self.assertEqual(scope.eia.retail.frequency, "monthly")
        self.assertEqual(scope.eia.retail.length, 5_000)
        self.assertEqual(dict(scope.eia.retail.facets), {"sectorid": ("ALL",), "stateid": ("US",)})
        self.assertEqual(
            tuple((item.canonical_series_id, item.field) for item in scope.eia.retail.data),
            (
                ("macro.eia.electricity.retail_sales", "sales"),
                ("macro.eia.electricity.retail_revenue", "revenue"),
                ("macro.eia.electricity.retail_price", "price"),
                ("macro.eia.electricity.retail_customers", "customers"),
            ),
        )
        self.assertEqual(
            tuple((item.column, item.direction) for item in scope.eia.retail.sort),
            (("period", "asc"),),
        )
        self.assertFalse(scope.eia.retail.tombstone_authoritative)
        self.assertEqual(scope.eia.weekly.path, "/v2/seriesid/PET.WCESTUS1.W")
        self.assertEqual(scope.eia.weekly.provider_series_id, "PET.WCESTUS1.W")
        self.assertEqual(scope.eia.weekly.canonical_series_id, "macro.eia.weekly.petroleum_stock")
        with self.assertRaises(TypeError):
            scope.eia.retail.facets["stateid"] = ("CA",)  # type: ignore[index]

    def test_rejects_broadened_or_secret_bearing_scope(self) -> None:
        cases: list[tuple[str, object]] = []

        extra_bea_request = _raw_scope()
        extra_bea_request["providers"]["bea"]["requests"].append(  # type: ignore[index]
            copy.deepcopy(extra_bea_request["providers"]["bea"]["requests"][0])  # type: ignore[index]
        )
        cases.append(("extra BEA request", extra_bea_request))

        broadened_facet = _raw_scope()
        broadened_facet["providers"]["eia"]["retail"]["facets"]["stateid"].append("CA")  # type: ignore[index]
        cases.append(("broadened EIA facet", broadened_facet))

        changed_bound = _raw_scope()
        changed_bound["bounds"]["eia_max_pages"] = 9  # type: ignore[index]
        cases.append(("changed page bound", changed_bound))

        changed_target = _raw_scope()
        changed_target["target_root"] = "/tmp/other-target"
        cases.append(("changed target", changed_target))

        changed_dependency = _raw_scope()
        changed_dependency["dependency"]["required_market_profile_id"] = "other-market-profile"  # type: ignore[index]
        cases.append(("changed dependency", changed_dependency))

        secret_field = _raw_scope()
        secret_field["providers"]["bea"]["requests"][0]["api_key"] = "not-allowed"  # type: ignore[index]
        cases.append(("embedded secret key", secret_field))

        unreviewed_path = _raw_scope()
        unreviewed_path["providers"]["eia"]["retail"]["cache_path"] = "/tmp/cache"  # type: ignore[index]
        cases.append(("unreviewed path", unreviewed_path))

        broadened_weekly = _raw_scope()
        broadened_weekly["providers"]["eia"]["weekly"]["url"] = "https://api.eia.gov/other"  # type: ignore[index]
        cases.append(("unreviewed URL", broadened_weekly))

        for name, raw in cases:
            with self.subTest(case=name):
                with self.assertRaises(ValidationError):
                    self._load(raw)

    def test_requires_an_explicit_available_scope_path(self) -> None:
        with self.assertRaises(ValidationError):
            load_stage11_macro_scope("")
        with self.assertRaises(ValidationError):
            load_stage11_macro_scope(object())  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            with self.assertRaises(ValidationError):
                load_stage11_macro_scope(Path(directory) / "missing.json")


if __name__ == "__main__":
    unittest.main()
