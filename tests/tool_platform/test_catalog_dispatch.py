from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher
from quant_data.errors import (
    CancellationError,
    CapabilityUnavailableError,
    ConcurrencyLimitError,
    DeadlineExceededError,
    ResourceLimitError,
    ValidationError,
)
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.registry import (
    PUBLIC_TOOL_NAMES,
    load_registry,
    stage4_registry_profile,
    stage5_registry_profile,
)
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap
from quant_data.tool_platform.catalog import FAMILY_COUNTS
from quant_data.tool_platform.context import (
    CancellationToken,
    Deadline,
    ExecutionBudget,
)
from quant_data.tool_platform.generate import generate
from quant_data.tool_platform.results import QueryResult


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
CATALOG_PATH = PROJECT_ROOT / "quant_data" / "generated" / "tool_contract_schemas_v1.json"
CATALOG_SHA256 = "a2469c903cc6c9dae64ea29c4d3b543837a37d4989277290220061101d28de87"


def explicit_nonexistent_stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class Stage5CatalogAndDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.stores = explicit_nonexistent_stores(self.root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.dispatcher = ToolDispatcher(self.stores, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_generated_inventory_examples_and_legacy_projection(self) -> None:
        self.assertEqual(self.registry.schema_version, "1.8.0")
        self.assertEqual(self.registry.registry_version, "2.31.0")
        stage5 = stage5_registry_profile(self.registry)
        self.assertEqual(stage5.schema_version, "1.1.0")
        self.assertEqual(stage5.registry_version, "2.3.0")
        self.assertEqual([item["id"] for item in stage5.dashboard], ["stage1.overview"])
        self.assertEqual(tuple(item["id"] for item in self.registry.tools), PUBLIC_TOOL_NAMES)
        counts = {name: 0 for name in FAMILY_COUNTS}
        for declaration in self.registry.tools:
            counts[declaration["family"]] += 1
            self.assertEqual(declaration["compatibility"]["status"],
                "recovered_fixture_validated" if declaration["id"] in {
                    "macro.get_series", "timeseries.describe"
                } else "forward_reconstructed_v1")
            for example in declaration["examples"]:
                validate_schema(example, declaration["input_schema"])
        self.assertEqual(counts, FAMILY_COUNTS)
        stage4 = stage4_registry_profile(self.registry)
        self.assertEqual(stage4.schema_version, "1.0.0")
        self.assertEqual([item["id"] for item in stage4.tools],
            ["macro.get_series", "timeseries.describe"])
        self.assertNotIn("tool_schema_catalog", stage4.raw)

    def test_generated_artifacts_are_current_and_checksum_bound(self) -> None:
        before = (hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(),
            hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest())
        self.assertEqual(before[1], CATALOG_SHA256)
        generate(PROJECT_ROOT, check=True)
        after = (hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest(),
            hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest())
        self.assertEqual(before, after)
        self.assertEqual(after[1], CATALOG_SHA256)
        self.assertEqual(after[1], self.registry.raw["tool_schema_catalog"]["sha256"])

    def test_manifest_has_57_sanitized_generated_contracts(self) -> None:
        manifest = self.dispatcher.manifest()
        self.assertEqual(manifest["milestone"], {"id": "stage5", "status": "fixture_validated"})
        self.assertEqual([item["name"] for item in manifest["tools"]], list(PUBLIC_TOOL_NAMES))
        self.assertEqual(len(manifest["tools"]), 57)
        for item in manifest["tools"]:
            self.assertNotIn("handler", item)
            self.assertIn("operation_graph_id", item)
            self.assertTrue(item["input_schema_id"].startswith("urn:quant-data:tool:"))
            self.assertTrue(item["output_schema_id"].startswith("urn:quant-data:tool:"))
            self.assertTrue(item["read_only"])

    def test_analytics_http_parity_receipt_and_no_path_creation(self) -> None:
        declaration = self.registry.tool("timeseries.transform")
        arguments = declaration["examples"][0]
        direct = self.dispatcher.call("timeseries.transform", arguments)
        application = Stage1Application(self.stores, self.registry)
        envelope = {
            "api_version": "1.0",
            "tool": "timeseries.transform",
            "arguments": arguments,
        }
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(envelope).encode("utf-8"),
        )
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        self.assertEqual(dumps_strict(payload["result"]), dumps_strict(direct))

        receipt = payload["receipt"]
        self.assertEqual(receipt["operation_graph_id"], "tool_platform.timeseries.transform")
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertNotEqual(receipt["started_at"], "1970-01-01T00:00:00Z")
        self.assertNotEqual(receipt["finished_at"], "1970-01-01T00:00:00Z")
        self.assertGreaterEqual(receipt["elapsed_seconds"], Decimal("0"))
        self.assertEqual(receipt["input_schema_id"], declaration["input_schema_id"])
        self.assertEqual(receipt["output_schema_id"], declaration["output_schema_id"])
        self.assertEqual(
            receipt["input_schema_sha256"],
            hashlib.sha256(
                dumps_strict(declaration["input_schema"]).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            receipt["output_schema_sha256"],
            hashlib.sha256(
                dumps_strict(declaration["output_schema"]).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            receipt["logical_store_aliases"],
            [f"{role}:host_selected" for role in declaration["stores"]],
        )
        self.assertEqual(receipt["error_codes"], [])
        self.assertNotIn("database_path", dumps_strict(receipt))
        self.assertFalse(any(self.root.iterdir()))

    def test_schema_limits_run_before_typed_series_construction(self) -> None:
        declaration = self.registry.tool("timeseries.align")
        arguments = loads_strict(dumps_strict(declaration["examples"][0]))
        arguments["series"] = [arguments["series"][0]] * 21
        with patch.object(
            self.dispatcher,
            "_timeseries_from_public",
            side_effect=AssertionError("typed conversion ran before maxItems validation"),
        ) as decoder:
            with self.assertRaises(ValidationError):
                self.dispatcher.call(declaration["id"], arguments)
        decoder.assert_not_called()
        self.assertFalse(any(self.root.iterdir()))

    def test_intraday_is_registered_but_offline_capability_is_unavailable(self) -> None:
        declaration = self.registry.tool("macro.get_intraday_releases")
        with self.assertRaises(CapabilityUnavailableError) as caught:
            self.dispatcher.call(declaration["id"], declaration["examples"][0])
        self.assertEqual(caught.exception.http_status, 503)
        self.assertEqual(caught.exception.capability_id, "macro_intraday_live")
        self.assertFalse(any(self.root.iterdir()))

    def test_unknown_fields_paths_sql_and_output_violations_fail_closed(self) -> None:
        declaration = self.registry.tool("market.search_instruments")
        for name in ("database_path", "sql", "pragma", "credential"):
            hostile = {**declaration["examples"][0], name: "not allowed"}
            with self.assertRaises(ValidationError):
                self.dispatcher.call(declaration["id"], hostile)

        class InvalidResult:
            def to_primitive(self) -> dict[str, object]:
                return {"contract": "wrong"}

        with patch("quant_data.boundary.dispatcher.invoke_operation", return_value=InvalidResult()):
            with self.assertRaises(Exception) as caught:
                self.dispatcher.call(declaration["id"], declaration["examples"][0])
            application = Stage1Application(self.stores, self.registry)
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": declaration["id"],
                        "arguments": declaration["examples"][0],
                    }
                ).encode("utf-8"),
            )
        self.assertIn("invalid", str(caught.exception).lower())
        self.assertEqual(response.status, 500)
        error_payload = loads_strict(response.body)
        self.assertEqual(
            error_payload["error"]["code"],
            "internal_output_validation",
        )
        self.assertEqual(
            error_payload["receipt"]["outcome"],
            "internal_output_validation",
        )
        self.assertEqual(
            error_payload["receipt"]["error_codes"],
            ["internal_output_validation"],
        )
        self.assertEqual(len(error_payload["receipt"]["request_id"]), 64)
        self.assertFalse(any(self.root.iterdir()))

    def test_host_concurrency_and_deadline_are_actively_enforced(self) -> None:
        declaration = self.registry.tool("market.search_instruments")
        acquired = 0
        try:
            for _ in range(self.dispatcher._max_concurrent_requests):
                self.assertTrue(
                    self.dispatcher._execution_slots.acquire(blocking=False)
                )
                acquired += 1
            with self.assertRaises(ConcurrencyLimitError):
                self.dispatcher.call(
                    declaration["id"],
                    declaration["examples"][0],
                )
        finally:
            for _ in range(acquired):
                self.dispatcher._execution_slots.release()

        with patch(
            "quant_data.tool_platform.context.monotonic_ns",
            side_effect=(0, 6_000_000_000),
        ):
            with self.assertRaises(DeadlineExceededError):
                self.dispatcher.call(
                    declaration["id"],
                    declaration["examples"][0],
                )

        application = Stage1Application(self.stores, self.registry)
        with patch(
            "quant_data.tool_platform.context.monotonic_ns",
            side_effect=(0, 6_000_000_000),
        ):
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": declaration["id"],
                        "arguments": declaration["examples"][0],
                    }
                ).encode("utf-8"),
            )
        self.assertEqual(response.status, 504)
        payload = loads_strict(response.body)
        self.assertEqual(payload["error"]["code"], "deadline_exceeded")
        self.assertEqual(payload["receipt"]["outcome"], "deadline_exceeded")

        cancelled_dispatcher = ToolDispatcher(
            self.stores,
            self.registry,
            cancellation=CancellationToken(True),
        )
        with self.assertRaises(CancellationError):
            cancelled_dispatcher.call(
                declaration["id"],
                declaration["examples"][0],
            )
        cancelled_application = Stage1Application(
            self.stores,
            self.registry,
            cancellation=CancellationToken(True),
        )
        response = cancelled_application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": declaration["id"],
                    "arguments": declaration["examples"][0],
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status, 499)
        payload = loads_strict(response.body)
        self.assertEqual(payload["error"]["code"], "cancelled")
        self.assertEqual(payload["receipt"]["outcome"], "cancelled")
        self.assertEqual(payload["receipt"]["error_codes"], ["cancelled"])
        self.assertFalse(any(self.root.iterdir()))

    def test_unknown_tool_attempt_has_bounded_sanitized_receipt(self) -> None:
        application = Stage1Application(self.stores, self.registry)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": "unknown.tool",
                    "arguments": {"query": "sensitive-user-value"},
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status, 404)
        payload = loads_strict(response.body)
        receipt = payload["receipt"]
        self.assertEqual(payload["error"]["code"], "unknown_tool")
        self.assertEqual(receipt["tool_name"], "unknown.tool")
        self.assertEqual(receipt["tool_version"], "unknown")
        self.assertEqual(receipt["outcome"], "unknown_tool")
        self.assertEqual(receipt["error_codes"], ["unknown_tool"])
        self.assertEqual(receipt["logical_stores"], [])
        self.assertEqual(receipt["logical_store_aliases"], [])
        self.assertIsNone(receipt["input_schema_id"])
        self.assertIsNone(receipt["output_schema_id"])
        self.assertIsNone(receipt["input_schema_sha256"])
        self.assertIsNone(receipt["output_schema_sha256"])
        self.assertEqual(len(receipt["request_id"]), 64)
        self.assertNotIn("sensitive-user-value", dumps_strict(receipt))

        declaration = self.registry.tool("market.search_instruments")
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(
                {
                    "api_version": "1.0",
                    "tool": declaration["id"],
                    "arguments": [],
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status, 400)
        payload = loads_strict(response.body)
        receipt = payload["receipt"]
        self.assertEqual(receipt["tool_name"], declaration["id"])
        self.assertEqual(receipt["outcome"], "invalid_request")
        self.assertEqual(receipt["error_codes"], ["invalid_request"])
        self.assertEqual(receipt["input_schema_id"], declaration["input_schema_id"])
        self.assertEqual(receipt["output_schema_id"], declaration["output_schema_id"])
        self.assertEqual(len(receipt["request_id"]), 64)

        for hostile_envelope in (
            {
                "api_version": "1.0",
                "tool": declaration["id"],
                "arguments": declaration["examples"][0],
                "unexpected": True,
            },
            {
                "api_version": "9.9",
                "tool": declaration["id"],
                "arguments": declaration["examples"][0],
            },
        ):
            with self.subTest(envelope=hostile_envelope):
                response = application.handle(
                    "POST",
                    "/api/agent-tools/call",
                    headers={"Content-Type": "application/json"},
                    body=dumps_strict(hostile_envelope).encode("utf-8"),
                )
                self.assertEqual(response.status, 400)
                payload = loads_strict(response.body)
                receipt = payload["receipt"]
                self.assertEqual(receipt["tool_name"], declaration["id"])
                self.assertEqual(receipt["outcome"], "invalid_request")
                self.assertEqual(receipt["error_codes"], ["invalid_request"])
                self.assertEqual(len(receipt["input_schema_sha256"]), 64)
                self.assertEqual(len(receipt["output_schema_sha256"]), 64)
        self.assertFalse(any(self.root.iterdir()))

    def test_domain_completion_rechecks_host_deadline(self) -> None:
        declaration = self.registry.tool("market.search_instruments")
        with (
            patch(
                "quant_data.tool_platform.context.monotonic_ns",
                side_effect=(0, 0, 6_000_000_000),
            ),
            patch(
                "quant_data.tool_platform.domain_operations.invoke_domain_operation",
                return_value=QueryResult(tool=declaration["id"]),
            ),
        ):
            with self.assertRaises(DeadlineExceededError):
                self.dispatcher.call(
                    declaration["id"],
                    declaration["examples"][0],
                )
        self.assertFalse(any(self.root.iterdir()))

    def test_host_owned_budget_deadline_and_cancellation_are_distinct(self) -> None:
        budget = ExecutionBudget(max_rows=10, max_series=2, max_operations=100,
            max_output_bytes=1000)
        budget.require(rows=10, series=2, operations=100)
        with self.assertRaises(ResourceLimitError):
            budget.require(rows=11)
        with self.assertRaises(DeadlineExceededError):
            Deadline(Decimal("2")).check(Decimal("2"))
        with self.assertRaises(CancellationError):
            CancellationToken(True).check()


if __name__ == "__main__":
    unittest.main()
