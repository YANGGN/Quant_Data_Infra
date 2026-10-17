from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import (
    REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256,
    load_stage12b_incremental_market_v1_scope,
    require_stage12a_binding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
STAGE12A_SCOPE_PATH = PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"


class Stage12BIncrementalScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = load_stage12b_incremental_market_v1_scope(SCOPE_PATH)
        self.stage12a = load_stage12_market_v1_scope(STAGE12A_SCOPE_PATH)

    def test_closed_registry_style_collector_manifest_and_binding_are_pinned(self) -> None:
        self.assertEqual(
            self.scope.manifest_sha256,
            REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256,
        )
        collector = self.scope.collector.manifest_mapping()
        self.assertEqual(
            set(collector),
            {
                "configuration_env",
                "handler",
                "id",
                "input_datasets",
                "mutation_policy",
                "network",
                "output_datasets",
                "physical_locks",
                "retry_policy",
                "schedule_eligibility",
                "semantic_identity",
                "version",
                "workload_bounds",
            },
        )
        self.assertEqual(
            collector["workload_bounds"],
            {"max_bytes": 65536, "max_requests": 1, "max_rows": 5, "max_seconds": 30},
        )
        self.assertEqual(
            collector["mutation_policy"],
            {"mode": "append_versions_and_move_current_projection", "unchanged": "zero_persistent_writes"},
        )
        self.assertEqual(collector["retry_policy"]["max_attempts"], 1)
        self.assertIs(
            require_stage12a_binding(
                self.scope, self.stage12a, scope_source=STAGE12A_SCOPE_PATH
            ),
            self.stage12a,
        )

    def test_stage12a_binding_rejects_semantically_equal_changed_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            changed = Path(temporary) / "stage12a-scope.json"
            changed.write_bytes(b"\n" + STAGE12A_SCOPE_PATH.read_bytes())
            semantically_equal = load_stage12_market_v1_scope(changed)
            self.assertEqual(
                semantically_equal.manifest_sha256,
                self.stage12a.manifest_sha256,
            )
            with self.assertRaises(ValidationError):
                require_stage12a_binding(
                    self.scope,
                    semantically_equal,
                    scope_source=changed,
                )

    def test_unknown_or_legacy_collector_shape_is_rejected(self) -> None:
        source = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        source["collector"]["bounds"] = source["collector"].pop("workload_bounds")
        self._assert_invalid(source)
        source = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        source["collector"]["unexpected"] = "drift"
        self._assert_invalid(source)

    def test_policy_and_stage12a_binding_drift_are_rejected(self) -> None:
        source = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        source["collector"]["retry_policy"]["max_attempts"] = 2
        self._assert_invalid(source)
        source = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
        source["stage12a_binding"]["roster_sha256"] = "0" * 64
        self._assert_invalid(source)

    def _assert_invalid(self, source: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            path = Path(temporary) / "scope.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_stage12b_incremental_market_v1_scope(path)


if __name__ == "__main__":
    unittest.main()
