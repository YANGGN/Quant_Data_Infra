from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from quant_data.dashboard.current_tool_page import latest_tool_version
from quant_data.dashboard.presentation import (
    render_agent_tools_page,
    render_gdp_vintages_page,
    render_overview_page,
    render_shell,
    render_table_inspector_page,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _contrast_ratio(first: str, second: str) -> float:
    def luminance(value: str) -> float:
        channels = tuple(int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))
        linear = tuple(
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class PresentationTests(unittest.TestCase):
    def test_current_tool_page_selects_semantic_latest_version(self) -> None:
        self.assertEqual(
            latest_tool_version(
                {
                    "version": "1.0.0",
                    "versions": (
                        {"version": "2.7.0"},
                        {"version": "1.0.0"},
                        {"version": "2.10.0"},
                    ),
                }
            ),
            "2.10.0",
        )

    def test_shell_uses_only_local_assets_and_escapes_plain_body(self) -> None:
        page = render_shell(
            title='<script>alert("title")</script>',
            active_route="/",
            eyebrow='<img src=x onerror="bad">',
            body_html='<script>alert("body")</script>',
        )
        self.assertIn('href="/assets/dashboard.css"', page)
        self.assertIn('src="/assets/dashboard.js"', page)
        self.assertIn('href="/assets/inter-variable.woff2"', page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert", page)
        self.assertIn("Skip to main content", page)
        self.assertIn('aria-current="page">Overview</a>', page)
        self.assertNotIn("style=", page)

    def test_pages_escape_hostile_service_values_and_mark_active_nav(self) -> None:
        hostile = '<img src=x onerror="alert(1)">'
        pages = {
            "/": render_overview_page(
                {
                    "stores": [
                        {
                            "role": hostile,
                            "status": "ok",
                            "integrity": "ok",
                            "datasets": [{"dataset_id": hostile}],
                            "migration_ids": [hostile],
                        }
                    ],
                    "receipt": {"registry_revision": hostile},
                }
            ),
            "/gdp-vintages": render_gdp_vintages_page(
                {
                    "query": {"series": "real-growth", "mode": "as_of", "as_of": hostile, "limit": 25},
                    "rows": [{"period_start": hostile, "value": 1}],
                    "warnings": [{"code": hostile, "message": hostile}],
                }
            ),
            "/table-inspector": render_table_inspector_page(
                {
                    "query": {"view": "market-prices", "search": hostile},
                    "available_sorts": [{"value": "trade_date", "label": hostile}],
                    "rows": [{"trade_date": hostile, "close": 1}],
                }
            ),
            "/agent-tools": render_agent_tools_page(
                {
                    "api_version": "1.0",
                    "tools": [
                        {"name": hostile, "version": "1.0.0", "family": hostile, "read_only": True}
                    ],
                }
            ),
        }
        for route, page in pages.items():
            with self.subTest(route=route):
                self.assertNotIn('<img src=x onerror="alert(1)">', page)
                self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", page)
                self.assertEqual(page.count('aria-current="page"'), 1)
                expected = next(label for candidate, label in (("/", "Overview"), ("/gdp-vintages", "GDP Vintages"), ("/table-inspector", "Tables"), ("/agent-tools", "Agent Tools")) if candidate == route)
                self.assertIn(f'aria-current="page">{expected}</a>', page)

    def test_gdp_form_has_the_declared_bounded_query_fields(self) -> None:
        page = render_gdp_vintages_page(
            {
                "query": {
                    "series": "nominal-gdp",
                    "mode": "as_of",
                    "as_of": "2026-07-09",
                    "date_only_policy": "calendar_date_inclusive",
                    "limit": 100,
                },
                "rows": [{"period_start": "2026-01-01", "value": 2.1, "available_at": "2026-02-01"}],
            }
        )
        for field in ("series", "mode", "as_of", "date_only_policy", "limit"):
            self.assertIn(f'name="{field}"', page)
        self.assertIn('action="/api/gdp-vintages"', page)
        self.assertIn('max="100"', page)
        self.assertIn('id="gdp-vintages-table"', page)
        self.assertIn('data-state="ready"', page)

    def test_tables_uses_only_returned_sort_choices_and_bounded_fields(self) -> None:
        page = render_table_inspector_page(
            {
                "query": {"view": "news-items", "sort": "published_at", "direction": "desc", "page": 2, "limit": 25},
                "available_sorts": [
                    {"value": "published_at", "label": "Published at"},
                    {"value": "headline", "label": "Headline"},
                ],
                "columns": ["published_at", "headline"],
                "rows": [{"published_at": "2026-07-01", "headline": "Fixture item"}],
            }
        )
        self.assertIn('action="/api/table-inspector"', page)
        self.assertIn('name="view"', page)
        self.assertIn('name="search"', page)
        self.assertIn('maxlength="128"', page)
        self.assertIn('value="published_at" selected', page)
        self.assertIn('value="headline"', page)
        self.assertNotIn('value="trade_date"', page)
        self.assertIn('name="page"', page)
        self.assertIn('name="limit"', page)

    def test_agent_tools_renders_every_manifest_tool_and_honest_runner_status(self) -> None:
        tools = [
            {
                "name": f"fixture.tool_{index:02d}",
                "version": "1.0.0",
                "family": "fixture",
                "read_only": True,
                "availability_policy": {"point_in_time_default": "not_established"},
            }
            for index in range(57)
        ]
        page = render_agent_tools_page(
            {
                "api_version": "1.0",
                "tools": tools,
                "runner_result": {"result": {"status": "not_established", "warnings": ["fixture"]}},
            }
        )
        # Each declaration appears in the bounded runner select, the manifest
        # table, and the strict runner-result preview; no tool is omitted.
        self.assertEqual(page.count("fixture.tool_"), 171)
        self.assertIn('action="/api/agent-tools/call"', page)
        self.assertIn('name="arguments"', page)
        self.assertIn('data-state="not_established"', page)
        self.assertIn("Result status: not_established", page)
        self.assertIn("57 declared tools", page)

    def test_local_assets_define_accepted_tokens_and_no_remote_or_unsafe_dom_sink(self) -> None:
        css = (PROJECT_ROOT / "quant_data/dashboard/static/dashboard.css").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "quant_data/dashboard/static/dashboard.js").read_text(encoding="utf-8")
        inspector_css = (
            PROJECT_ROOT / "quant_data/dashboard/static/inspector_tools.css"
        ).read_text(encoding="utf-8")
        inspector_javascript = (
            PROJECT_ROOT / "quant_data/dashboard/static/inspector_tools.js"
        ).read_text(encoding="utf-8")
        for token in (
            "#f8f9fa",
            "#ffffff",
            "#0f172a",
            "#475569",
            "#64748b",
            "#e2e8f0",
            "#f1f5f9",
            "#94a3b8",
            "#047857",
            "#be123c",
        ):
            self.assertIn(token, css)
        self.assertIn('url("/assets/inter-variable.woff2")', css)
        self.assertIn("outline: 2px solid var(--text-primary)", css)
        self.assertIn("box-shadow: 0 0 0 4px var(--focus)", css)
        self.assertGreaterEqual(_contrast_ratio("#0f172a", "#ffffff"), 3.0)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("@media (max-width: 640px)", css)
        self.assertNotRegex(css + javascript, r"https?://")
        self.assertNotIn("innerHTML", javascript)
        self.assertIn("textContent", javascript)
        endpoints = set(re.findall(r'"(/api/[^"]+)"', javascript))
        self.assertEqual(
            endpoints,
            {
                "/api/gdp-vintages",
                "/api/table-inspector",
                "/api/agent-tools",
                "/api/agent-tools/call",
            },
        )
        self.assertNotRegex(inspector_css + inspector_javascript, r"https?://")
        self.assertNotIn("innerHTML", inspector_javascript)
        self.assertIn("textContent", inspector_javascript)
        inspector_endpoints = set(
            re.findall(r'"(/api/[^"]+)"', inspector_javascript)
        )
        self.assertEqual(
            inspector_endpoints,
            {"/api/agent-tools", "/api/agent-tools/call"},
        )
        self.assertIn("MAX_TREE_PREVIEW_ENTRIES = 500", inspector_javascript)
        self.assertIn(
            "MAX_RAW_PREVIEW_CHARACTERS = 131072",
            inspector_javascript,
        )
        self.assertIn("complete paged response", inspector_javascript)



    @unittest.skipUnless(shutil.which("node"), "requires the WSL-native Node runtime")
    def test_agent_tool_runner_rejects_non_strict_json_before_fetch(self) -> None:
        harness = textwrap.dedent(
            r'''
            const dashboard = require("./quant_data/dashboard/static/dashboard.js");
            const requests = [];
            global.fetch = async (path, options) => {
              requests.push({ path, options });
              return { ok: true, json: async () => ({ result: { status: "ok" } }) };
            };
            const endpoint = { pathname: "/api/agent-tools/call", search: "" };
            async function expectPreflightFailure(argumentsText) {
              const before = requests.length;
              let rejected = false;
              try {
                await dashboard.runToolRequest(endpoint, "1.0", "fixture.tool", argumentsText);
              } catch (error) {
                rejected = error instanceof Error && error.message.includes("strict JSON object");
              }
              if (!rejected) {
                throw new Error("invalid arguments did not fail strict preflight");
              }
              if (requests.length !== before) {
                throw new Error("fetch ran before strict preflight rejected arguments");
              }
            }
            (async () => {
              await expectPreflightFailure('{"duplicate": 1, "duplicate": 2}');
              await expectPreflightFailure('{"nested": {"duplicate": 1, "duplicate": 2}}');
              await expectPreflightFailure('{"value": 1e400}');
              await expectPreflightFailure('{"value": NaN}');
              const rawArguments = ' { "value": 1.2300, "nested": { "labels": ["ok"] } } ';
              const result = await dashboard.runToolRequest(
                endpoint, "1.0", "fixture.tool", rawArguments
              );
              if (result.result.status !== "ok" || requests.length !== 1) {
                throw new Error("valid strict envelope did not reach fetch exactly once");
              }
              const request = requests[0];
              if (request.path !== "/api/agent-tools/call"
                || request.options.body.indexOf('"arguments":' + rawArguments) === -1) {
                throw new Error("valid argument text was not preserved in the strict envelope");
              }
            })().catch((error) => { console.error(error); process.exitCode = 1; });
            '''
        )
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "requires the WSL-native Node runtime")
    def test_current_inspector_tool_helpers_are_strict_versioned_and_composable(
        self,
    ) -> None:
        harness = textwrap.dedent(
            r'''
            const inspector = require(
              "./quant_data/dashboard/static/inspector_tools.js"
            );
            function mustReject(source) {
              let rejected = false;
              try {
                inspector.parseStrictJson(source);
              } catch (_error) {
                rejected = true;
              }
              if (!rejected) {
                throw new Error("invalid strict JSON was accepted");
              }
            }
            mustReject('{"duplicate":1,"duplicate":2}');
            mustReject('{"value":NaN}');
            mustReject('{"value":1e4097}');
            mustReject('{"value":1e-4097}');
            mustReject('{"value":1} trailing');
            mustReject('{"value":"\\ud800"}');
            const paddedExponent = inspector.parseStrictJson(
              '{"value":1e0000000400}'
            );
            if (inspector.stringifyStrictJson(paddedExponent)
              !== '{"value":1e0000000400}') {
              throw new Error("valid padded exponent was not preserved");
            }
            const protectedObject = inspector.parseStrictJson(
              '{"__proto__":{"polluted":true}}'
            );
            if (!Object.prototype.hasOwnProperty.call(protectedObject, "__proto__")
              || ({}).polluted !== undefined) {
              throw new Error("strict JSON object parsing allowed prototype mutation");
            }
            const precise = inspector.parseStrictJson(
              '{"large":9007199254740993,'
              + '"decimal":0.123456789012345678901234567890,'
              + '"wide":1e400}'
            );
            const preciseEnvelope = inspector.buildEnvelope(
              "1.0",
              "market.technical_indicators",
              "2.7.0",
              precise
            );
            if (!preciseEnvelope.includes('"large":9007199254740993')
              || !preciseEnvelope.includes(
                '"decimal":0.123456789012345678901234567890'
              )
              || !preciseEnvelope.includes('"wide":1e400')) {
              throw new Error("strict JSON numbers lost their exact lexemes");
            }
            if (inspector.stringifyStrictJson(precise)
              !== '{"large":9007199254740993,'
                + '"decimal":0.123456789012345678901234567890,'
                + '"wide":1e400}') {
              throw new Error("strict JSON rendering changed numeric precision");
            }
            const args = inspector.parseStrictJson(
              '{"indicator":"kdj","window":9,"signal_window":3}'
            );
            const envelope = JSON.parse(inspector.buildEnvelope(
              "1.0",
              "market.technical_indicators",
              "2.3.0",
              args
            ));
            if (envelope.tool_version !== "2.3.0"
              || envelope.arguments.indicator !== "kdj") {
              throw new Error("explicit tool version was not preserved");
            }
            const latest = inspector.latestVersion({
              version: "1.0.0",
              versions: [
                { version: "2.7.0" },
                { version: "1.0.0" },
                { version: "2.10.0" }
              ]
            });
            if (latest !== "2.10.0") {
              throw new Error("latest semantic tool version was not selected");
            }
            const series = {
              contract: "quant_data.timeseries",
              series_id: "example:close",
              lineage_digest: "abc",
              metadata: { observation_field: "close" },
              observations: []
            };
            const found = inspector.collectSeries({
              result: { series: [series, series] }
            });
            if (found.length !== 1 || found[0].value.series_id !== "example:close") {
              throw new Error("returned typed series were not deduplicated");
            }
            '''
        )
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

if __name__ == "__main__":
    unittest.main()
