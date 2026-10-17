from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from quant_data.errors import ValidationError
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.market.stage12c_scope import (
    REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256,
    REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256,
    load_stage12c_market_gap_v1_scope,
    require_stage12c_bindings,
    require_stage12c_scope,
    stage12c_ordered_symbols,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage12c_market_gap_v1_scope.json"
STAGE12A_SCOPE_PATH = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
STAGE12B_SCOPE_PATH = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"


class Stage12CMarketGapScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = load_stage12c_market_gap_v1_scope(SCOPE_PATH)
        self.stage12a = load_stage12_market_v1_scope(STAGE12A_SCOPE_PATH)
        self.stage12b = load_stage12b_incremental_market_v1_scope(STAGE12B_SCOPE_PATH)

    def test_exact_scope_binds_raw_and_semantic_prior_authority_and_request_order(self) -> None:
        self.assertEqual(self.scope.manifest_sha256, REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256)
        self.assertEqual(self.scope.source_file_sha256, REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256)
        self.assertEqual(self.scope.request_plan.session_dates, ("2026-08-13", "2026-08-14"))
        self.assertEqual(
            self.scope.collector.manifest_mapping()["workload_bounds"],
            {"max_bytes": 65536, "max_requests": 1, "max_rows": 2, "max_seconds": 45},
        )
        self.assertEqual(
            self.scope.collector.manifest_mapping()["semantic_identity"],
            {
                "excludes": [
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                ],
                "includes": [
                    "scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                ],
            },
        )
        validated = require_stage12c_bindings(self.scope, self.stage12a, self.stage12b)
        self.assertIs(validated[0], self.scope)
        order = stage12c_ordered_symbols(self.scope, self.stage12a, self.stage12b)
        self.assertEqual((len(order), order[:3]), (629, ("AAPL", "A", "ABBV")))
        self.assertEqual(len(set(order)), 629)
        self.assertEqual(order[1:], tuple(sorted(order[1:])))

    def test_scope_source_bytes_and_unknown_or_broadened_content_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            changed = Path(temporary) / "scope.json"
            changed.write_bytes(SCOPE_PATH.read_bytes() + b"\n")
            with self.assertRaises(ValidationError):
                load_stage12c_market_gap_v1_scope(changed)

            source = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
            source["request_plan"]["to"] = "2026-08-15"
            changed.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_stage12c_market_gap_v1_scope(changed)

            semantic_drift = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
            semantic_drift["collector"]["semantic_identity"]["includes"] = [
                "request_scope",
                "scope_manifest_sha256",
                "normalization_version",
                "normalized_complete_batch",
            ]
            payload = json.dumps(
                semantic_drift,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            changed.write_bytes(payload)
            with mock.patch(
                "quant_data.market.stage12c_scope.REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256",
                hashlib.sha256(payload).hexdigest(),
            ):
                with self.assertRaises(ValidationError):
                    load_stage12c_market_gap_v1_scope(changed)

    def test_forged_scope_or_prior_authority_object_is_rejected_before_use(self) -> None:
        forged_scope = replace(
            self.scope,
            collector=replace(self.scope.collector, max_seconds=999),
        )
        with self.assertRaises(ValidationError):
            require_stage12c_scope(forged_scope)

        with self.assertRaises(ValidationError):
            require_stage12c_scope(replace(self.scope, manifest_sha256="0" * 64))

        forged_stage12a = replace(
            self.stage12a,
            roster_sha256="0" * 64,
        )
        with self.assertRaises(ValidationError):
            require_stage12c_bindings(self.scope, forged_stage12a, self.stage12b)

        forged_stage12b = replace(
            self.stage12b,
            collector=replace(self.stage12b.collector, max_rows=9),
        )
        with self.assertRaises(ValidationError):
            require_stage12c_bindings(self.scope, self.stage12a, forged_stage12b)


if __name__ == "__main__":
    unittest.main()
