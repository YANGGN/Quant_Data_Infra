from __future__ import annotations

import http.client
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from quant_data.boundary import create_server
from quant_data.dashboard import INTER_FONT_SHA256, Stage6Application
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.migrations import initialize_all
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage6_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from quant_data.stage4 import run_clean_stage4_rebuild


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
}


class Stage6ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(cls._temporary.name)
        run_clean_stage4_rebuild(
            project_root=PROJECT_ROOT,
            work_root=root / "stage4",
        )
        cls.store_map = explicit_store_map(
            root / "stage4" / "stage3" / "stage2" / "source"
        )
        cls.registry = stage6_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        initialize_all(cls.store_map, cls.registry)
        cls.before = mutation_fingerprint(cls.store_map)
        cls.application = Stage6Application(cls.store_map, cls.registry)

    @classmethod
    def tearDownClass(cls) -> None:
        after = mutation_fingerprint(cls.store_map)
        if dumps_strict(cls.before) != dumps_strict(after):
            raise AssertionError("Stage 6 application tests mutated a store")
        cls._temporary.cleanup()

    def test_four_pages_and_local_assets_render_the_reviewed_portal(self) -> None:
        pages = {
            "/": ("Overview", 'aria-current="page">Overview'),
            "/gdp-vintages": ("GDP Vintages", 'aria-current="page">GDP Vintages'),
            "/table-inspector": ("Tables", 'aria-current="page">Tables'),
            "/agent-tools": ("Agent Tools", 'aria-current="page">Agent Tools'),
        }
        for route, (title, active) in pages.items():
            with self.subTest(route=route):
                response = self.application.handle("GET", route)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "text/html; charset=utf-8")
                document = response.body.decode("utf-8")
                self.assertIn(f"<title>{title}", document)
                self.assertIn("Quant Data Infrastructure</title>", document)
                self.assertIn(active, document)
                self.assertIn("/assets/dashboard.css", document)
                self.assertIn("/assets/dashboard.js", document)
                self.assertNotIn("http://", document)
                self.assertNotIn("https://", document)

        tools = self.application.handle("GET", "/agent-tools").body.decode("utf-8")
        self.assertEqual(tools.count("<tr><th scope=\"row\"><code>"), 57)
        self.assertIn("capability_unavailable", tools)
        self.assertIn("not_established", tools)

        assets = {
            "/assets/dashboard.css": "text/css; charset=utf-8",
            "/assets/dashboard.js": "application/javascript; charset=utf-8",
            "/assets/inter-variable.woff2": "font/woff2",
        }
        for route, content_type in assets.items():
            with self.subTest(route=route):
                response = self.application.handle("GET", route)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, content_type)
                self.assertTrue(response.body)
        import hashlib

        self.assertEqual(
            hashlib.sha256(
                self.application.handle("GET", "/assets/inter-variable.woff2").body
            ).hexdigest(),
            INTER_FONT_SHA256,
        )

    def test_gdp_and_table_apis_are_bounded_strict_json(self) -> None:
        gdp = self.application.handle(
            "GET",
            "/api/gdp-vintages?series=real-growth&mode=latest"
            "&date_only_policy=completed_date&limit=25",
        )
        self.assertEqual(gdp.status, 200)
        gdp_payload = loads_strict(gdp.body)
        self.assertEqual(gdp_payload["execution"], "read_only")
        self.assertEqual(gdp_payload["result"]["query"]["mode"], "latest")
        self.assertTrue(gdp_payload["result"]["observations"])
        self.assertEqual(gdp_payload["receipt"]["registry_revision"], "2.4.0")

        table = self.application.handle(
            "GET",
            "/api/table-inspector?view=company-filings&sort=filing_date"
            "&direction=desc&page=1&limit=25",
        )
        self.assertEqual(table.status, 200)
        table_payload = loads_strict(table.body)
        result = table_payload["result"]
        self.assertEqual(result["view"], "company-filings")
        self.assertIn("first_observed_issuer_id", result["columns"])
        self.assertNotIn("relation", result)
        self.assertNotIn("sql", dumps_strict(result).lower())

        default_table = self.application.handle("GET", "/api/table-inspector")
        self.assertEqual(default_table.status, 200)
        self.assertEqual(
            loads_strict(default_table.body)["result"]["view"],
            "market-prices",
        )

    def test_hostile_queries_methods_and_unknown_routes_fail_closed(self) -> None:
        cases = (
            ("/api/table-inspector?relation=sqlite_master", 400),
            ("/api/table-inspector?view=market-prices&sort=drop_table", 400),
            ("/api/table-inspector?view=market-prices&page=101", 413),
            ("/api/gdp-vintages?sql=select", 400),
            ("/?path=/tmp/market.sqlite", 400),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                response = self.application.handle("GET", target)
                self.assertEqual(response.status, expected)
                payload = loads_strict(response.body)
                self.assertIn(payload["error"]["code"], {"invalid_request", "resource_limit"})
                serialized = dumps_strict(payload).lower()
                self.assertNotIn("sqlite_master", serialized)
                self.assertNotIn("/tmp/market.sqlite", serialized)

        self.assertEqual(
            self.application.handle("POST", "/gdp-vintages", body=b"{}").status,
            405,
        )
        self.assertEqual(self.application.handle("DELETE", "/").status, 405)
        self.assertEqual(self.application.handle("GET", "/missing").status, 404)

    def test_routes_do_not_reach_any_writer_entry_point(self) -> None:
        before = mutation_fingerprint(self.store_map)
        with (
            patch(
                "quant_data.ingestion.IngestionCoordinator.execute",
                side_effect=AssertionError("dashboard reached ingestion"),
            ) as ingestion,
            patch(
                "quant_data.migrations.initialize_all",
                side_effect=AssertionError("dashboard reached initialization"),
            ) as initializer,
            patch(
                "quant_data.migrations.migrate_store",
                side_effect=AssertionError("dashboard reached migration"),
            ) as migrate,
            patch(
                "quant_data.stores.writer_connection",
                side_effect=AssertionError("dashboard reached writer connection"),
            ) as writer,
        ):
            for route in (
                "/",
                "/gdp-vintages",
                "/table-inspector",
                "/agent-tools",
                "/api/health",
                "/api/gdp-vintages",
                "/api/table-inspector",
                "/api/agent-tools",
            ):
                self.assertEqual(self.application.handle("GET", route).status, 200)

        ingestion.assert_not_called()
        initializer.assert_not_called()
        migrate.assert_not_called()
        writer.assert_not_called()
        self.assertEqual(
            dumps_strict(before),
            dumps_strict(mutation_fingerprint(self.store_map)),
        )

    def test_loopback_server_adds_security_headers_to_pages_assets_and_errors(self) -> None:
        with self.server() as port:
            for route in (
                "/",
                "/gdp-vintages",
                "/table-inspector",
                "/agent-tools",
                "/assets/dashboard.css",
                "/assets/dashboard.js",
                "/assets/inter-variable.woff2",
                "/api/gdp-vintages",
                "/api/table-inspector",
                "/missing",
            ):
                with self.subTest(route=route):
                    status, headers, _payload = self.request(port, "GET", route)
                    self.assertEqual(status, 404 if route == "/missing" else 200)
                    for name, value in _SECURITY_HEADERS.items():
                        self.assertEqual(headers.get(name), value)

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
            headers = (
                {"Content-Type": "application/json; charset=utf-8"}
                if body is not None
                else {}
            )
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
