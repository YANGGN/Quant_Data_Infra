from __future__ import annotations

import copy
import http.client
import socket
import tempfile
import threading
import unittest
from contextlib import contextmanager
from decimal import Decimal
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from quant_data.boundary import Stage1Application, ToolDispatcher, create_server
from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import MAX_JSON_BYTES, dumps_strict, loads_strict
from quant_data.macro import MacroFixtureImporter, MacroSeriesQuery, MacroSeriesRepository
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
SERIES_ID = "fixture:philadelphia_fed_rtdsm:EMPLOY"


def temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class OverviewStructureParser(HTMLParser):
    """Small structural inspector for the dependency-free Overview HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))

    def element_with_id(self, element_id: str) -> tuple[str, dict[str, str | None]]:
        for tag, attrs in self.elements:
            if attrs.get("id") == element_id:
                return tag, attrs
        raise AssertionError(f"No element with id={element_id!r}")


class Stage1BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = temporary_store_map(self.root)
        self.registry = load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        initialize_all(self.store_map, self.registry)
        fixture_manifest = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT
        )

        # Domain writers own these importers.  The boundary test consumes their
        # frozen public fixture APIs and never changes their data contract.
        from quant_data.market import DailyPriceImporter

        market_importer = DailyPriceImporter(self.store_map, fixture_manifest)
        macro_importer = MacroFixtureImporter(self.store_map, fixture_manifest)
        market_importer.import_fixture("market.base")
        market_importer.import_fixture("market.correction")
        macro_importer.import_fixture("macro.first_vintage")
        macro_importer.import_fixture("macro.revised_vintage")
        self.dispatcher = ToolDispatcher(self.store_map, self.registry)
        self.application = Stage1Application(self.store_map, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def macro_arguments(**overrides: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "series_id": SERIES_ID,
            "start_date": "2026-01-01",
            "end_date": "2026-02-28",
            "vintage_mode": "latest",
            "date_only_policy": "completed_date",
            "limit": 100,
        }
        arguments.update(overrides)
        return arguments

    @staticmethod
    def price_target(**overrides: str) -> str:
        values = {
            "provider": "fixture_fmp",
            "provider_symbol": "SPY",
            "price_variant": "raw",
            "currency_segment": "USD",
            "start_date": "2026-07-16",
            "end_date": "2026-07-17",
            "mode": "latest",
            "date_only_policy": "completed_date",
            "limit": "100",
        }
        values.update(overrides)
        return "/api/price-series?" + "&".join(f"{key}={value}" for key, value in values.items())

    def assert_read_only(self, action: Any) -> Any:
        before = mutation_fingerprint(self.store_map)
        result = action()
        after = mutation_fingerprint(self.store_map)
        self.assertEqual(before["sha256"], after["sha256"])
        return result

    def test_manifest_is_exactly_the_two_validated_stage1_tools(self) -> None:
        manifest = self.assert_read_only(self.dispatcher.manifest)
        self.assertEqual(manifest["api_version"], "1.0")
        self.assertEqual(manifest["execution"], "read_only")
        self.assertEqual(
            [tool["name"] for tool in manifest["tools"]],
            ["macro.get_series", "timeseries.describe"],
        )
        self.assertEqual(len(manifest["tools"]), 2)

    def test_direct_macro_and_describe_composition_preserves_lineage(self) -> None:
        typed = self.assert_read_only(
            lambda: MacroSeriesRepository(self.store_map, self.registry).get_series(
                MacroSeriesQuery(
                    SERIES_ID,
                    "2026-01-01",
                    "2026-02-28",
                    "latest",
                    None,
                    "completed_date",
                    100,
                )
            )
        )
        description = self.assert_read_only(lambda: self.dispatcher.describe(typed))
        self.assertEqual(description["count"], 2)
        self.assertEqual(description["nonmissing_count"], 2)
        self.assertEqual(description["missing_count"], 0)
        self.assertEqual(description["audit"]["input_lineage_digest"], typed.lineage_digest)
        self.assertEqual(description["mean"], Decimal("99.75"))

    def test_public_timeseries_is_revalidated_before_describe(self) -> None:
        series = self.assert_read_only(
            lambda: self.dispatcher.call("macro.get_series", self.macro_arguments())
        )
        description = self.assert_read_only(
            lambda: self.dispatcher.call("timeseries.describe", {"series": series})
        )
        self.assertEqual(description["audit"]["input_lineage_digest"], series["lineage_digest"])
        tampered = copy.deepcopy(series)
        tampered["lineage_digest"] = "0" * 64
        with self.assertRaises(ValidationError):
            self.dispatcher.call("timeseries.describe", {"series": tampered})
        tampered = copy.deepcopy(series)
        tampered["metadata"]["unknown"] = "x"
        with self.assertRaises(ValidationError):
            self.dispatcher.call("timeseries.describe", {"series": tampered})

    def test_empty_and_all_missing_description_has_null_statistics_and_warnings(self) -> None:
        empty = TimeSeries(
            series_id="fixture:empty",
            metadata={},
            observations=(),
            warnings=(),
            audit={"cutoff": None},
            provenance={},
        )
        empty_result = self.dispatcher.describe(empty)
        self.assertEqual(empty_result["count"], 0)
        self.assertIsNone(empty_result["minimum"])
        self.assertIsNone(empty_result["maximum"])
        self.assertIsNone(empty_result["mean"])
        self.assertIn("empty_series", empty_result["warnings"])

        missing = TimeSeries(
            series_id="fixture:missing",
            metadata={},
            observations=(
                Observation(
                    period_start="2026-01-01",
                    period_end="2026-01-31",
                    value=None,
                    missing_reason="source_suppressed",
                    unit="index",
                    value_representation="level",
                    scale="ones",
                    vintage_at="2026-06-12",
                    available_at="2026-06-12",
                    available_precision="date",
                    captured_at="2026-06-12T17:00:00-04:00",
                    captured_precision="datetime",
                    version_id="missing-version",
                    evidence_id="missing-evidence",
                    snapshot_id="missing-snapshot",
                    run_id="missing-run",
                ),
            ),
            warnings=(),
            audit={"cutoff": None},
            provenance={},
        )
        missing_result = self.dispatcher.describe(missing)
        self.assertEqual(missing_result["missing_count"], 1)
        self.assertIsNone(missing_result["mean"])
        self.assertIn("all_observations_missing", missing_result["warnings"])

    def test_dispatcher_rejects_conditional_and_unknown_macro_inputs(self) -> None:
        with self.assertRaises(ValidationError):
            self.dispatcher.call(
                "macro.get_series", self.macro_arguments(vintage_mode="as_of")
            )
        with self.assertRaises(ValidationError):
            self.dispatcher.call(
                "macro.get_series",
                self.macro_arguments(as_of="2026-07-09"),
            )
        with self.assertRaises(ValidationError):
            self.dispatcher.call(
                "macro.get_series", self.macro_arguments(limit=10_001)
            )
        with self.assertRaises(ValidationError):
            self.dispatcher.call(
                "macro.get_series", self.macro_arguments(sql="SELECT 1")
            )

    def test_application_read_families_do_not_mutate_any_store(self) -> None:
        macro_call = {
            "api_version": "1.0",
            "tool": "macro.get_series",
            "arguments": self.macro_arguments(),
        }
        raw_series = self.dispatcher.call("macro.get_series", self.macro_arguments())
        describe_call = {
            "api_version": "1.0",
            "tool": "timeseries.describe",
            "arguments": {"series": raw_series},
        }
        actions = (
            lambda: self.application.handle("GET", "/"),
            lambda: self.application.handle("GET", "/api/health"),
            lambda: self.application.handle("GET", "/api/agent-tools"),
            lambda: self.application.handle("GET", self.price_target()),
            lambda: self.application.handle(
                "POST", "/api/agent-tools/call", body=dumps_strict(macro_call).encode("utf-8")
            ),
            lambda: self.application.handle(
                "POST", "/api/agent-tools/call", body=dumps_strict(describe_call).encode("utf-8")
            ),
            lambda: self.application.handle("GET", "/api/price-series?sql=SELECT%201"),
        )
        for action in actions:
            response = self.assert_read_only(action)
            self.assertIn(response.status, {200, 400})

    def test_overview_structurally_exposes_slice_state_and_is_read_only(self) -> None:
        response = self.assert_read_only(lambda: self.application.handle("GET", "/"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        document = response.body.decode("utf-8")
        parser = OverviewStructureParser()
        parser.feed(document)

        for element_id in (
            "primary-navigation",
            "store-health",
            "store-health-table",
            "dataset-state",
            "dataset-state-table",
            "market-fixture",
            "market-instrument-1",
            "market-instrument-2",
            "macro-fixture",
            "macro-preview-controls",
            "macro-vintage-mode",
            "macro-cutoff",
            "macro-date-only-policy",
            "macro-observations",
            "tool-manifest",
            "tool-manifest-table",
            "tool-result-preview",
            "tool-manifest-preview",
        ):
            parser.element_with_id(element_id)
        self.assertEqual(parser.element_with_id("primary-navigation")[0], "nav")
        self.assertIn('href="#market-fixture"', document)
        self.assertIn('href="#macro-fixture"', document)
        for control_id in ("macro-vintage-mode", "macro-cutoff", "macro-date-only-policy"):
            tag, attrs = parser.element_with_id(control_id)
            self.assertEqual(tag, "select")
            self.assertIn("disabled", attrs)
        self.assertIn("<option selected>as_of</option>", document)
        self.assertIn("<option selected>2026-07-09</option>", document)

        self.assertIn("Stage 1 local, read-only inspection surface", document)
        for role in ("market", "macro", "company", "news"):
            self.assertIn(role, document)
        for dataset in (
            "fixture.market.daily_price_evidence",
            "fixture.market.instruments",
            "fixture.market.daily_prices",
            "fixture.macro.rtdsm_employ_evidence",
            "fixture.macro.rtdsm_employ",
        ):
            self.assertIn(dataset, document)
        self.assertIn("SPY", document)
        self.assertIn("^GSPC", document)

        price_response = self.assert_read_only(
            lambda: self.application.handle("GET", self.price_target())
        )
        price_result = loads_strict(price_response.body)["result"]
        price_observation = price_result["observations"][-1]
        self.assertIn(escape(str(price_observation["trade_date"])), document)
        self.assertIn(escape(str(price_observation["close"])), document)
        self.assertIn(escape(str(price_observation["version_id"])), document)
        self.assertIn(escape(str(price_observation["evidence_id"])), document)
        self.assertIn(escape(str(price_observation["available_precision"])), document)
        self.assertIn(escape(str(price_observation["captured_precision"])), document)

        health_response = self.assert_read_only(
            lambda: self.application.handle("GET", "/api/health")
        )
        health = loads_strict(health_response.body)["result"]
        for store in health["stores"]:
            for dataset in store.get("datasets", []):
                run_id = dataset["last_successful_run_id"]
                if run_id is not None:
                    self.assertIn(escape(str(run_id)), document)

        macro_result = self.assert_read_only(
            lambda: self.dispatcher.call(
                "macro.get_series",
                self.macro_arguments(vintage_mode="as_of", as_of="2026-07-09"),
            )
        )
        macro_missing = macro_result["observations"][-1]
        self.assertIsNone(macro_missing["value"])
        self.assertEqual(macro_missing["missing_reason"], "source_suppressed")
        self.assertIn(escape(str(macro_missing["version_id"])), document)
        self.assertIn(escape(str(macro_missing["evidence_id"])), document)
        self.assertIn(escape(str(macro_missing["available_precision"])), document)
        self.assertIn(escape(str(macro_missing["captured_precision"])), document)
        self.assertIn("2026-07-09", document)
        self.assertIn("completed_date", document)
        self.assertIn('<span class="null">null</span>', document)
        self.assertIn("source_suppressed", document)
        self.assertIn("null observation(s) retained with explicit missingness", document)
        self.assertIn("Result is not truncated", document)
        self.assertIn("macro.get_series", document)
        self.assertIn("timeseries.describe", document)
        self.assertIn("Structured tool result preview", document)
        self.assertIn("Registry manifest preview", document)
        self.assertNotIn("<input", document)
        self.assertNotIn("<form", document)
        self.assertNotIn("<textarea", document)
        self.assertNotIn("<script", document)
        self.assertNotIn("SQL console", document)
        self.assertNotIn("file picker", document)
        self.assertNotIn("ingestion control", document)

    def test_overview_escapes_dynamic_values_without_store_mutation(self) -> None:
        malicious = '<img src=x onerror="alert(1)">'
        health = {
            "stores": [
                {
                    "role": malicious,
                    "status": malicious,
                    "integrity": malicious,
                    "migration_ids": [malicious],
                    "datasets": [
                        {
                            "dataset_id": malicious,
                            "last_successful_run_id": malicious,
                        }
                    ],
                }
            ]
        }
        market = [
            {
                "symbol": malicious,
                "error": None,
                "result": {
                    "instrument": {
                        "instrument_id": malicious,
                        "provider": malicious,
                        "provider_symbol": malicious,
                    },
                    "observations": [
                        {
                            "trade_date": malicious,
                            "open": malicious,
                            "high": malicious,
                            "low": malicious,
                            "close": malicious,
                            "volume": malicious,
                            "available_at": malicious,
                            "available_precision": malicious,
                            "captured_at": malicious,
                            "captured_precision": malicious,
                            "version_id": malicious,
                            "evidence_id": malicious,
                        }
                    ],
                    "warnings": [malicious],
                    "truncated": False,
                },
            }
        ]
        macro = {
            "error": None,
            "series": {
                "series_id": malicious,
                "metadata": {"provider": malicious, "frequency": malicious, "unit": malicious},
                "audit": {"mode": malicious, "cutoff": malicious, "cutoff_precision": malicious},
                "observations": [
                    {
                        "period_start": malicious,
                        "period_end": malicious,
                        "value": None,
                        "missing_reason": malicious,
                        "vintage_at": malicious,
                        "available_at": malicious,
                        "available_precision": malicious,
                        "captured_at": malicious,
                        "captured_precision": malicious,
                        "version_id": malicious,
                        "evidence_id": malicious,
                    }
                ],
                "warnings": [malicious],
                "truncated": False,
            },
            "description": {"missing_count": 1},
        }
        with (
            patch.object(self.application, "_health_payload", return_value=health),
            patch.object(self.application, "_overview_market_previews", return_value=market),
            patch.object(self.application, "_overview_macro_preview", return_value=macro),
        ):
            response = self.assert_read_only(lambda: self.application.handle("GET", "/"))
        document = response.body.decode("utf-8")
        self.assertNotIn(malicious, document)
        self.assertIn(escape(malicious, quote=True), document)
        self.assertNotIn("<img", document)
        self.assertNotIn('onerror="alert(1)"', document)

    def test_missing_and_wrong_store_routes_fail_closed_without_default_fallback(self) -> None:
        missing_map = StoreMap.four_explicit(
            market=self.store_map.market,
            macro=self.root / "missing-macro.sqlite",
            company=self.store_map.company,
            news=self.store_map.news,
        )
        missing = Stage1Application(missing_map, self.registry)
        body = dumps_strict(
            {
                "api_version": "1.0",
                "tool": "macro.get_series",
                "arguments": self.macro_arguments(),
            }
        ).encode("utf-8")
        response = self.assert_read_only(
            lambda: missing.handle("POST", "/api/agent-tools/call", body=body)
        )
        self.assertEqual(response.status, 503)
        self.assertFalse(missing_map.macro.exists())

        swapped_map = StoreMap.four_explicit(
            market=self.store_map.macro,
            macro=self.store_map.market,
            company=self.store_map.company,
            news=self.store_map.news,
        )
        swapped = Stage1Application(swapped_map, self.registry)
        response = self.assert_read_only(
            lambda: swapped.handle("POST", "/api/agent-tools/call", body=body)
        )
        self.assertEqual(response.status, 503)

    def test_actual_loopback_server_routes_headers_and_errors(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.server() as port:
            status, headers, overview = self.request(port, "GET", "/")
            self.assertEqual(status, 200)
            self.assertIn('id="primary-navigation"', overview)
            self.assertIn('id="tool-result-preview"', overview)
            self.assert_security_headers(headers)

            status, headers, payload = self.request(port, "GET", "/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(payload["execution"], "read_only")
            self.assertEqual(len(payload["result"]["stores"]), 4)
            self.assert_security_headers(headers)

            status, headers, manifest = self.request(port, "GET", "/api/agent-tools")
            self.assertEqual(status, 200)
            self.assertEqual([item["name"] for item in manifest["tools"]], ["macro.get_series", "timeseries.describe"])
            self.assert_security_headers(headers)

            payload = {
                "api_version": "1.0",
                "tool": "macro.get_series",
                "arguments": self.macro_arguments(),
            }
            status, headers, tool_result = self.request(
                port,
                "POST",
                "/api/agent-tools/call",
                dumps_strict(payload).encode("utf-8"),
            )
            self.assertEqual(status, 200)
            self.assertEqual(tool_result["tool"]["name"], "macro.get_series")
            self.assertEqual(tool_result["result"]["contract"], "quant_data.timeseries")
            self.assert_security_headers(headers)

            status, headers, bad_method = self.request(port, "PUT", "/api/health")
            self.assertEqual(status, 405)
            self.assertEqual(bad_method["error"]["code"], "method_not_allowed")
            self.assert_security_headers(headers)

            status, headers, bad_method = self.request(port, "PROPFIND", "/api/health")
            self.assertEqual(status, 405)
            self.assertEqual(
                {key.lower(): value for key, value in headers.items()}.get("content-type"),
                "application/json; charset=utf-8",
            )
            self.assertIsInstance(bad_method, dict)
            self.assertEqual(bad_method["error"]["code"], "method_not_allowed")
            self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_actual_server_rejects_malformed_duplicate_oversized_and_unknown_input(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with self.server() as port:
            cases = (
                (b"{", "invalid_json"),
                (b"\xff", "invalid_utf8"),
                (
                    b'{"api_version":"1.0","api_version":"1.0","tool":"macro.get_series","arguments":{}}',
                    "invalid_request",
                ),
                (
                    dumps_strict(
                        {
                            "api_version": "1.0",
                            "tool": "does.not.exist",
                            "arguments": {},
                        }
                    ).encode("utf-8"),
                    "unknown_tool",
                ),
                (
                    dumps_strict(
                        {
                            "api_version": "1.0",
                            "tool": "macro.get_series",
                            "arguments": self.macro_arguments(path="/tmp/not-allowed"),
                        }
                    ).encode("utf-8"),
                    "invalid_request",
                ),
                (
                    b'{"api_version":"1.0","tool":"macro.get_series","arguments":{"limit":NaN}}',
                    "invalid_request",
                ),
            )
            for body, expected_code in cases:
                status, headers, payload = self.request(port, "POST", "/api/agent-tools/call", body)
                self.assertIn(status, {400, 404})
                self.assertEqual(payload["error"]["code"], expected_code)
                self.assert_security_headers(headers)

            status, headers, payload = self.request_declared_length(
                port,
                "/api/agent-tools/call",
                MAX_JSON_BYTES + 1,
            )
            self.assertEqual(status, 413)
            self.assertEqual(payload["error"]["code"], "resource_limit")
            self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_raw_socket_malformed_request_lines_use_safe_json_writer(self) -> None:
        before = mutation_fingerprint(self.store_map)
        malformed_requests = (
            b"GET / HTTP/9.9\r\nHost: 127.0.0.1\r\n\r\n",
            b"GET / HTTP/x\r\nHost: 127.0.0.1\r\n\r\n",
        )
        with self.server() as port:
            for request in malformed_requests:
                with self.subTest(request_line=request.split(b"\r\n", 1)[0]):
                    raw, status, headers, body = self.raw_request(port, request)
                    self.assertLessEqual(len(raw), 4096)
                    self.assertTrue(raw.startswith(b"HTTP/1.0 "))
                    self.assertGreaterEqual(status, 400)
                    self.assertLess(status, 600)
                    self.assertEqual(headers.get("content-type"), "application/json; charset=utf-8")
                    self.assertEqual(headers.get("content-length"), str(len(body)))
                    payload = loads_strict(body)
                    self.assertIsInstance(payload, dict)
                    self.assertIsInstance(payload.get("error"), dict)
                    self.assertIsInstance(payload["error"].get("code"), str)
                    self.assertNotIn(b"<html", raw.lower())
                    self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_loopback_manifest_matches_registry_and_executes_describe_example(self) -> None:
        expected_manifest = self.assert_read_only(self.dispatcher.manifest)
        before = mutation_fingerprint(self.store_map)
        with self.server() as port:
            status, headers, manifest = self.request(port, "GET", "/api/agent-tools")
            self.assertEqual(status, 200)
            self.assertEqual(manifest["api_version"], expected_manifest["api_version"])
            self.assertEqual(manifest["execution"], "read_only")
            self.assertEqual(manifest["registry_revision"], self.registry.revision)
            self.assertEqual(
                manifest["milestone"],
                {"id": "stage1", "status": "validated_non_active"},
            )
            self.assertEqual(manifest["tools"], expected_manifest["tools"])
            self.assertEqual(
                manifest["receipt"],
                {"registry_revision": self.registry.revision},
            )
            self.assert_security_headers(headers)

            tools_by_name = {tool["name"]: tool for tool in manifest["tools"]}
            macro = tools_by_name["macro.get_series"]
            describe = tools_by_name["timeseries.describe"]
            self.assertEqual(
                describe["input_schema"],
                self.registry.tool("timeseries.describe")["input_schema"],
            )
            self.assertEqual(
                describe["input_schema"]["properties"]["series"],
                macro["output_schema"],
            )
            self.assertEqual(
                describe["examples"],
                self.registry.tool("timeseries.describe")["examples"],
            )
            self.assertTrue(describe["examples"])

            request_body = dumps_strict(
                {
                    "api_version": manifest["api_version"],
                    "tool": describe["name"],
                    "arguments": copy.deepcopy(describe["examples"][0]),
                }
            ).encode("utf-8")
            status, headers, result = self.request(
                port,
                "POST",
                "/api/agent-tools/call",
                request_body,
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["tool"]["name"], "timeseries.describe")
            self.assertEqual(result["result"]["count"], 0)
            self.assertIn("empty_series", result["result"]["warnings"])
            self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_loopback_describe_rejects_tampered_public_series_lineage(self) -> None:
        series = self.assert_read_only(
            lambda: self.dispatcher.call("macro.get_series", self.macro_arguments())
        )
        tampered = copy.deepcopy(series)
        old_digest = tampered["lineage_digest"]
        original_value = tampered["observations"][0]["value"]
        tampered["observations"][0]["value"] = (
            Decimal("999999") if original_value != Decimal("999999") else Decimal("999998")
        )
        self.assertNotEqual(tampered["observations"][0]["value"], original_value)
        self.assertEqual(tampered["lineage_digest"], old_digest)

        before = mutation_fingerprint(self.store_map)
        with self.server() as port:
            status, headers, payload = self.request(
                port,
                "POST",
                "/api/agent-tools/call",
                dumps_strict(
                    {
                        "api_version": "1.0",
                        "tool": "timeseries.describe",
                        "arguments": {"series": tampered},
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(status, 400)
            self.assertEqual(payload["error"]["code"], "invalid_request")
            self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_loopback_rejects_compact_extreme_exponent_before_expansion(self) -> None:
        body = (
            b'{"api_version":"1.0","tool":"macro.get_series",'
            b'"arguments":{"limit":1e8500000}}'
        )
        self.assertLess(len(body), 128)

        before = mutation_fingerprint(self.store_map)
        with self.server() as port:
            status, headers, payload = self.request(
                port,
                "POST",
                "/api/agent-tools/call",
                body,
            )
            self.assertEqual(status, 413)
            self.assertEqual(payload["error"]["code"], "resource_limit")
            self.assert_security_headers(headers)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_server_rejects_non_loopback_binding(self) -> None:
        with self.assertRaises(ValidationError):
            create_server(self.application, host="0.0.0.0")

    @contextmanager
    def server(self) -> Iterator[int]:
        server = create_server(self.application)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield int(server.server_address[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @staticmethod
    def request(
        port: int,
        method: str,
        target: str,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            headers = {"Content-Type": "application/json; charset=utf-8"} if body is not None else {}
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            content_type = response.getheader("Content-Type", "")
            parsed: Any
            if content_type.startswith("application/json"):
                parsed = loads_strict(payload)
            else:
                parsed = payload.decode("utf-8")
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    @staticmethod
    def request_declared_length(
        port: int,
        target: str,
        declared_length: int,
    ) -> tuple[int, dict[str, str], Any]:
        """Assert the handler rejects an oversized header before body reads."""

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.putrequest("POST", target)
            connection.putheader("Content-Type", "application/json; charset=utf-8")
            connection.putheader("Content-Length", str(declared_length))
            connection.endheaders()
            response = connection.getresponse()
            payload = loads_strict(response.read())
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    @staticmethod
    def raw_request(
        port: int,
        request: bytes,
        *,
        max_response_bytes: int = 4096,
    ) -> tuple[bytes, int, dict[str, str], bytes]:
        """Send one malformed request line and parse only a bounded response."""

        with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
            connection.settimeout(2)
            connection.sendall(request)
            response = bytearray()
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                if len(response) > max_response_bytes:
                    raise AssertionError("Malformed-request response exceeded its fixed test bound")

        raw = bytes(response)
        header_block, separator, body = raw.partition(b"\r\n\r\n")
        if not separator:
            raise AssertionError("Malformed-request response lacked an HTTP header separator")
        header_lines = header_block.split(b"\r\n")
        status_parts = header_lines[0].split(maxsplit=2)
        if len(status_parts) < 2 or not status_parts[0].startswith(b"HTTP/"):
            raise AssertionError("Malformed-request response lacked a parseable HTTP status line")
        try:
            status = int(status_parts[1])
        except ValueError as exc:
            raise AssertionError("Malformed-request response status was not numeric") from exc

        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            name, delimiter, value = line.partition(b":")
            if not delimiter:
                raise AssertionError("Malformed-request response contained an invalid header")
            headers[name.decode("ascii").lower()] = value.strip().decode("latin-1")
        return raw, status, headers, body

    def assert_security_headers(self, headers: Mapping[str, str]) -> None:
        normalized = {key.lower(): value for key, value in headers.items()}
        self.assertEqual(normalized.get("cache-control"), "no-store")
        self.assertEqual(normalized.get("x-content-type-options"), "nosniff")
        self.assertEqual(normalized.get("x-frame-options"), "DENY")
        self.assertEqual(normalized.get("referrer-policy"), "no-referrer")
        self.assertEqual(
            normalized.get("content-security-policy"),
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )


if __name__ == "__main__":
    unittest.main()
