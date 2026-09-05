from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quant_data.boundary import Stage1Application
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.news.current_repository import CurrentNewsSelection
from quant_data.registry import load_registry
from quant_data.stores import StoreMap
from quant_data.tool_platform.local_agent_cli import PROJECT_ROOT, run
from quant_data.tool_platform.technical_indicators import TECHNICAL_INDICATORS_V2


REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class LocalAgentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.application = Stage1Application(
            temporary_store_map(self.root),
            self.registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(
        self,
        argv: list[str],
        body: bytes = b"",
    ) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = run(
            argv,
            stdin=io.BytesIO(body),
            stdout=stdout,
            stderr=stderr,
            application=self.application,
        )
        return code, loads_strict(stdout.getvalue()), stderr.getvalue()

    def test_list_is_compact_dynamic_active_inventory(self) -> None:
        code, payload, stderr = self.invoke(["list"])
        manifest = self.application.dispatcher.manifest()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["tool_count"], len(manifest["tools"]))
        self.assertEqual(payload["tool_count"], 74)
        self.assertIn(
            "market.get_available_ticker",
            [item["name"] for item in payload["tools"]],
        )
        self.assertEqual(
            [item["name"] for item in payload["tools"]],
            [item["name"] for item in manifest["tools"]],
        )
        for item in payload["tools"]:
            self.assertEqual(
                set(item),
                {
                    "name",
                    "version",
                    "available_versions",
                    "lifecycle",
                    "compatibility_status",
                    "description",
                    "read_only",
                },
            )
            self.assertTrue(item["read_only"])
        self.assertFalse(any(self.root.iterdir()))

    def test_manifest_and_describe_reuse_public_dispatcher_contract(self) -> None:
        code, payload, stderr = self.invoke(["manifest"])
        expected = loads_strict(
            self.application.handle("GET", "/api/agent-tools", headers={}).body
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(payload, expected)

        code, description, _ = self.invoke(["describe", "market.get_price_series"])
        declared = next(
            item
            for item in expected["tools"]
            if item["name"] == "market.get_price_series"
        )
        self.assertEqual(code, 0)
        self.assertEqual(description["tool"], declared)
        self.assertEqual(
            description["selection"]["tool_version"],
            declared["version"],
        )
        ticker_code, ticker_description, _ = self.invoke(
            ["describe", "market.get_available_ticker"]
        )
        ticker_declared = next(
            item
            for item in expected["tools"]
            if item["name"] == "market.get_available_ticker"
        )
        self.assertEqual(ticker_code, 0)
        self.assertEqual(ticker_description["tool"], ticker_declared)
        self.assertEqual(ticker_declared["input_schema"]["required"], [])
        self.assertEqual(ticker_declared["examples"], [{}])
        self.assertFalse(any(self.root.iterdir()))

    def test_describe_supports_advertised_version_selection(self) -> None:
        code, payload, _ = self.invoke(
            ["describe", "market.get_returns", "--tool-version", "2.0.0"]
        )
        expected = self.registry.tool("market.get_returns", "2.0.0")
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["selection"],
            {
                "name": "market.get_returns",
                "tool_version": "2.0.0",
            },
        )
        self.assertEqual(payload["tool"]["input_schema"], expected["input_schema"])
        self.assertEqual(payload["tool"]["output_schema"], expected["output_schema"])
        self.assertFalse(any(self.root.iterdir()))

    def test_local_agents_can_discover_and_call_technical_indicator_v2(self) -> None:
        default_code, default_payload, default_stderr = self.invoke(
            ["describe", "market.technical_indicators"]
        )
        self.assertEqual(default_code, 0)
        self.assertEqual(default_stderr, "")
        self.assertEqual(default_payload["selection"]["tool_version"], "1.0.0")

        code, payload, stderr = self.invoke(
            [
                "describe",
                "market.technical_indicators",
                "--tool-version",
                "2.0.0",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            payload["selection"],
            {
                "name": "market.technical_indicators",
                "tool_version": "2.0.0",
            },
        )
        declaration = payload["tool"]
        self.assertEqual(declaration["stores"], [])
        self.assertFalse(declaration["live_capability"]["possible"])
        self.assertEqual(
            declaration["input_schema"]["properties"]["indicator"]["enum"],
            list(TECHNICAL_INDICATORS_V2),
        )
        self.assertEqual(
            declaration["input_schema"]["required"],
            [
                "series",
                "indicator",
                "window",
                "fast_window",
                "slow_window",
                "signal_window",
                "standard_deviation_multiplier",
                "limit",
            ],
        )

        envelope = {
            "api_version": self.application.api_version,
            "tool": "market.technical_indicators",
            "tool_version": "2.0.0",
            "arguments": declaration["examples"][0],
        }
        call_code, call_payload, call_stderr = self.invoke(
            ["call"], dumps_strict(envelope).encode("utf-8")
        )
        self.assertEqual(call_code, 0)
        self.assertEqual(call_stderr, "")
        self.assertEqual(
            call_payload["tool"],
            {"name": "market.technical_indicators", "version": "2.0.0"},
        )
        self.assertEqual(call_payload["result"]["status"], "ok")
        self.assertEqual(call_payload["receipt"]["logical_stores"], [])
        self.assertFalse(any(self.root.iterdir()))

    def test_local_agents_can_call_latest_technical_indicator_v27(self) -> None:
        code, payload, stderr = self.invoke(
            [
                "describe",
                "market.technical_indicators",
                "--tool-version",
                "2.7.0",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            payload["selection"],
            {
                "name": "market.technical_indicators",
                "tool_version": "2.7.0",
            },
        )

        envelope = {
            "api_version": self.application.api_version,
            "tool": "market.technical_indicators",
            "tool_version": "2.7.0",
            "arguments": payload["tool"]["examples"][0],
        }
        call_code, call_payload, call_stderr = self.invoke(
            ["call"], dumps_strict(envelope).encode("utf-8")
        )
        self.assertEqual(call_code, 0)
        self.assertEqual(call_stderr, "")
        self.assertEqual(
            call_payload["tool"],
            {"name": "market.technical_indicators", "version": "2.7.0"},
        )
        self.assertEqual(call_payload["result"]["status"], "ok")
        self.assertEqual(call_payload["receipt"]["logical_stores"], [])
        self.assertFalse(any(self.root.iterdir()))

    def test_local_agents_can_discover_and_call_current_news_v2(self) -> None:
        code, payload, stderr = self.invoke(
            ["describe", "news.search", "--tool-version", "2.0.0"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(
            payload["selection"],
            {
                "name": "news.search",
                "tool_version": "2.0.0",
            },
        )

        envelope = {
            "api_version": self.application.api_version,
            "tool": "news.search",
            "tool_version": "2.0.0",
            "arguments": payload["tool"]["examples"][0],
        }
        selection = CurrentNewsSelection(
            records=(),
            total_selected_count=0,
            truncated=False,
            migration_ids=("news:0006_fmp_stock_latest_current",),
            receipt_sha256="a" * 64,
        )
        with mock.patch(
            "quant_data.tool_platform.news_access.CurrentNewsRepository"
        ) as repository_type:
            repository_type.return_value.search.return_value = selection
            call_code, call_payload, call_stderr = self.invoke(
                ["call"], dumps_strict(envelope).encode("utf-8")
            )

        self.assertEqual(call_code, 0)
        self.assertEqual(call_stderr, "")
        self.assertEqual(
            call_payload["tool"],
            {"name": "news.search", "version": "2.0.0"},
        )
        self.assertEqual(call_payload["result"]["status"], "ok")
        self.assertEqual(call_payload["result"]["records"], [])
        self.assertEqual(call_payload["receipt"]["logical_stores"], ["news"])
        repository_type.return_value.search.assert_called_once()
        self.assertFalse(any(self.root.iterdir()))

    def test_call_matches_in_process_application_and_does_not_write(self) -> None:
        declaration = self.registry.tool("timeseries.transform")
        envelope = {
            "api_version": self.application.api_version,
            "tool": declaration["id"],
            "arguments": declaration["examples"][0],
        }
        body = dumps_strict(envelope).encode("utf-8")
        expected = loads_strict(
            self.application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={},
                body=body,
            ).body
        )
        code, payload, stderr = self.invoke(["call"], body)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["result"], expected["result"])
        self.assertEqual(payload["tool"], expected["tool"])
        self.assertEqual(
            payload["receipt"]["registry_revision"],
            expected["receipt"]["registry_revision"],
        )
        self.assertFalse(any(self.root.iterdir()))

    def test_call_rejects_host_overrides_and_strict_json_hazards(self) -> None:
        declaration = self.registry.tool("timeseries.transform")
        valid = {
            "api_version": self.application.api_version,
            "tool": declaration["id"],
            "arguments": declaration["examples"][0],
        }
        hostile_bodies = (
            b'{"api_version":"1.0","api_version":"1.0","tool":"timeseries.transform","arguments":{}}',
            b'{"api_version":"1.0","tool":"timeseries.transform","arguments":{"limit":NaN}}',
            dumps_strict({**valid, "project_root": str(self.root)}).encode("utf-8"),
            dumps_strict(
                {
                    **valid,
                    "arguments": {
                        **valid["arguments"],
                        "database_path": str(self.root / "market.sqlite"),
                    },
                }
            ).encode("utf-8"),
            dumps_strict(
                {
                    **valid,
                    "arguments": {**valid["arguments"], "sql": "select 1"},
                }
            ).encode("utf-8"),
            dumps_strict(valid).encode("utf-8")
            + b"\n"
            + dumps_strict(valid).encode("utf-8"),
        )
        for body in hostile_bodies:
            with self.subTest(body=body[:40]):
                code, payload, stderr = self.invoke(["call"], body)
                self.assertNotEqual(code, 0)
                self.assertEqual(stderr, "")
                self.assertIn("error", payload)
                rendered = dumps_strict(payload)
                self.assertNotIn(str(self.root), rendered)
                self.assertNotIn("Traceback", rendered)
        self.assertFalse(any(self.root.iterdir()))

    def test_no_cli_path_or_extra_argument_switches_are_accepted(self) -> None:
        for argv in (
            ["list", "--project-root", str(self.root)],
            ["manifest", "--registry", str(REGISTRY_PATH)],
            ["describe", "market.get_price_series", "--store", str(self.root)],
            ["call", "request.json"],
        ):
            with self.subTest(argv=argv):
                code, payload, stderr = self.invoke(argv)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "")
                self.assertEqual(payload["error"]["code"], "invalid_request")
                self.assertNotIn(str(self.root), dumps_strict(payload))
        self.assertFalse(any(self.root.iterdir()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
