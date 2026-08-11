from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import textwrap
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ATLAS_ROOT = PROJECT_ROOT / "sites" / "quant-data-atlas"
HTML_PATH = ATLAS_ROOT / "index.html"
CSS_PATH = ATLAS_ROOT / "assets" / "atlas.css"
JAVASCRIPT_PATH = ATLAS_ROOT / "assets" / "atlas.js"


class _AtlasHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.append((tag, dict(attrs)))


class StaticAtlasTests(unittest.TestCase):
    def test_semantic_shell_uses_only_relative_local_assets(self) -> None:
        page = HTML_PATH.read_text(encoding="utf-8")
        parser = _AtlasHtml()
        parser.feed(page)

        for landmark in ("header", "nav", "main", "section", "footer", "table", "form"):
            self.assertIn(landmark, parser.tags)
        self.assertIn('id="main-content"', page)
        self.assertIn('href="#main-content"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('maxlength="128"', page)
        self.assertIn('id="atlas-dataset-select"', page)
        self.assertIn('id="atlas-schema-details"', page)
        self.assertIn('id="atlas-provenance-details"', page)

        linked_assets = {
            attributes.get("href")
            for tag, attributes in parser.attributes
            if tag == "link" and attributes.get("href")
        }
        self.assertTrue({"assets/dashboard.css", "assets/atlas.css", "assets/inter-variable.woff2"}.issubset(linked_assets))
        script_sources = {
            attributes.get("src")
            for tag, attributes in parser.attributes
            if tag == "script" and attributes.get("src")
        }
        self.assertEqual(script_sources, {"assets/atlas.js"})
        self.assertNotRegex(page, r"https?://")
        self.assertNotRegex(page, r"(?<!:)//[A-Za-z0-9]")
        self.assertNotIn("/api/", page)
        self.assertNotIn("<img", page.lower())

    def test_atlas_assets_preserve_shared_tokens_and_safe_dom_boundary(self) -> None:
        css = CSS_PATH.read_text(encoding="utf-8")
        javascript = JAVASCRIPT_PATH.read_text(encoding="utf-8")

        self.assertNotIn(":root", css)
        self.assertIn('url("inter-variable.woff2")', css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("@media (max-width: 640px)", css)
        self.assertIn("var(--surface)", css)
        self.assertIn("var(--focus)", css)
        self.assertNotRegex(css + javascript, r"https?://")
        self.assertNotRegex(css + javascript, r"(?<!:)//[A-Za-z0-9]")
        self.assertNotIn("inner" + "HTML", javascript)
        self.assertNotIn("document.write", javascript)
        self.assertNotIn("eval(", javascript)
        self.assertNotIn("Function(", javascript)
        self.assertIn("createElement", javascript)
        self.assertIn("textContent", javascript)
        self.assertIn('MANIFEST_PATH = "data/manifest.json"', javascript)
        self.assertNotIn("/api/", javascript)
        self.assertIn("crypto.subtle.digest", javascript)
        self.assertIn("MAX_TOTAL_ROWS", javascript)
        self.assertIn("MAX_TOTAL_CHUNKS", javascript)
        self.assertIn("MAX_TOTAL_BYTES", javascript)

    @unittest.skipUnless(shutil.which("node"), "requires the WSL-native Node runtime")
    def test_static_loader_syntax_and_bounded_behavior(self) -> None:
        completed = subprocess.run(
            ["node", "--check", str(JAVASCRIPT_PATH)],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

        payload = json.dumps(
            [{"trade_date": "2026-08-01", "close": 123.45}],
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        dataset_ids = (
            "market-prices",
            "gdp-vintages",
            "company-issuers",
            "news-items",
        )
        source_stores = ("market", "macro", "company", "news")
        identity_keys = (
            ["fixture:SPY", "2026-08-01", "fixture", "close", "USD"],
            ["fixture", "2026-08-01", "advance"],
            ["0000001"],
            ["fixture", "news-item-1"],
        )

        def chunk(dataset_id: str, key: list[str]) -> dict[str, object]:
            return {
                "path": f"data/chunks/{dataset_id}-0001.json",
                "sha256": digest,
                "rows": 1,
                "bytes": len(payload),
                "first_key": key,
                "last_key": key,
            }

        def dataset(index: int) -> dict[str, object]:
            dataset_id = dataset_ids[index]
            key = identity_keys[index]
            return {
                "id": dataset_id,
                "label": dataset_id.replace("-", " ").title(),
                "state": "ready",
                "completeness": "complete",
                "freshness": {"state": "fixture"},
                "schema": {"id": f"{dataset_id}.v1", "fields": ["trade_date", "close"]},
                "rows": 1,
                "bytes": len(payload),
                "key_range": {"first_key": key, "last_key": key},
                "chunks": [chunk(dataset_id, key)],
            }

        manifest = {
            "export_id": "atlas.fixture_snapshot",
            "revision_id": "a" * 64,
            "generated_at": "2026-08-11T12:00:00Z",
            "cutoff": "2026-08-10T00:00:00Z",
            "semantic_dataset_id": "atlas.fixture_snapshot.core_v1",
            "lifecycle": {
                "fixture_only": True,
                "hosting": False,
                "network": False,
                "mode": "manual_only",
                "status": "fixture_validated",
            },
            "completeness": "complete",
            "point_in_time": {
                "availability": "at_or_before",
                "date_only_policy": "completed_date",
                "timezone": "aware_utc",
            },
            "registry": {"id": "quant_data", "revision": "2.6.0"},
            "contract": {
                "consumers": [{"id": "quant_data_atlas", "mode": "static_read_only"}],
                "chunking": {"max_rows": 250, "max_bytes": 262144},
                "serialization": {"format": "strict_json"},
            },
            "provenance": {
                "included_fields": ["availability", "captured_at"],
                "excluded_fields": ["body", "source_url"],
                "source_fingerprint_policy": "logical_sha256",
            },
            "source_stores": [
                {
                    "store": source_stores[index],
                    "snapshot_at": "2026-08-11T11:59:00Z",
                    "source_logical_sha256": "b" * 64,
                    "source_fingerprint": {"method": "online_backup"},
                    "eligible_rows": 1,
                    "eligible_rows_sha256": "c" * 64,
                }
                for index in range(4)
            ],
            "cross_store_atomic": False,
            "totals": {
                "rows": 4,
                "chunk_bytes": len(payload) * 4,
                "bytes": len(payload) * 4,
                "bounds": {"max_total_rows": 12000, "max_bytes": 8388608},
            },
            "files": {"count": 10, "inventory_path": "data/checksums.json"},
            "datasets": [dataset(index) for index in range(4)],
        }
        harness = textwrap.dedent(
            """
            const { webcrypto } = require("node:crypto");
            globalThis.crypto = webcrypto;
            globalThis.window = { location: { href: "https://atlas.example/public/index.html" } };
            const atlas = require("./sites/quant-data-atlas/assets/atlas.js");
            const manifest = __MANIFEST__;
            const payload = Buffer.from(__PAYLOAD__, "hex");
            const requests = [];
            globalThis.fetch = async (path, options) => {
              requests.push({ path, options });
              if (!options || options.redirect !== "error") {
                requests.push({ path: "https://outside.example/redirect-target", options: {} });
              }
              throw new TypeError("simulated redirect rejection");
            };
            const clone = (value) => JSON.parse(JSON.stringify(value));
            function rejected(fn, label) {
              let failed = false;
              try { fn(); } catch (_error) { failed = true; }
              if (!failed) throw new Error(label + " was accepted");
            }
            async function expectRedirectBlocked(path) {
              const before = requests.length;
              let failed = false;
              try {
                await atlas.fetchStaticBytes(path, 1024, "Redirect fixture");
              } catch (_error) {
                failed = true;
              }
              if (!failed) throw new Error("redirect fixture did not reject");
              const made = requests.slice(before);
              if (made.length !== 1 || made[0].path !== path) {
                throw new Error("redirect handling made a second or external request");
              }
              if (!made[0].options || made[0].options.redirect !== "error") {
                throw new Error("static fetch did not use redirect error mode");
              }
            }
            (async () => {
              await expectRedirectBlocked("data/manifest.json");
              await expectRedirectBlocked("data/chunks/market-prices-0001.json");
              atlas.validateManifest(manifest);
              const expectedLimits = {
                MAX_CHUNK_BYTES: 262144, MAX_TOTAL_BYTES: 8388608, MAX_DATASETS: 4,
                MAX_CHUNKS_PER_DATASET: 20, MAX_TOTAL_CHUNKS: 48, MAX_ROWS_PER_CHUNK: 250,
                MAX_ROWS_PER_DATASET: 5000, MAX_TOTAL_ROWS: 12000
              };
              for (const [key, value] of Object.entries(expectedLimits)) {
                if (atlas[key] !== value) throw new Error(key + " did not match the frozen viewer cap");
              }
              atlas.validateStaticResponseUrl(
                { redirected: false, url: "https://atlas.example/public/data/manifest.json" },
                "data/manifest.json"
              );
              rejected(() => atlas.validateStaticResponseUrl(
                { redirected: false, url: "https://outside.example/data/manifest.json" },
                "data/manifest.json"
              ), "cross-origin response");
              rejected(() => atlas.validateStaticResponseUrl(
                { redirected: true, url: "https://atlas.example/public/data/manifest.json" },
                "data/manifest.json"
              ), "redirected response");
              rejected(() => atlas.parseStrictJson('{"duplicate":1,"duplicate":2}', 1024, "fixture"), "duplicate key");
              rejected(() => atlas.parseStrictJson('{"number":1e400}', 1024, "fixture"), "nonfinite number");
              rejected(() => atlas.validateDataPath('../operational.sqlite'), "path traversal");
              rejected(() => atlas.validateDataPath('https://example.test/chunk.json'), "remote URL");
              rejected(() => atlas.validateDataPath('//example.test/chunk.json'), "protocol-relative URL");
              rejected(() => atlas.validateDataPath('/data/chunk.json'), "absolute path");

              const extraTop = clone(manifest);
              extraTop.unexpected = true;
              rejected(() => atlas.validateManifest(extraTop), "unexpected manifest field");

              const extraDataset = clone(manifest);
              extraDataset.datasets[0].untracked = true;
              rejected(() => atlas.validateManifest(extraDataset), "unexpected dataset field");

              const missingChunkField = clone(manifest);
              delete missingChunkField.datasets[0].chunks[0].first_key;
              rejected(() => atlas.validateManifest(missingChunkField), "missing chunk key range");

              const extraSourceField = clone(manifest);
              extraSourceField.source_stores[0].untracked = true;
              rejected(() => atlas.validateManifest(extraSourceField), "unexpected source-store field");

              const wrongTotals = clone(manifest);
              wrongTotals.totals.rows += 1;
              rejected(() => atlas.validateManifest(wrongTotals), "wrong aggregate row total");

              const wrongDatasetTotals = clone(manifest);
              wrongDatasetTotals.datasets[0].bytes += 1;
              rejected(() => atlas.validateManifest(wrongDatasetTotals), "wrong dataset byte total");

              const reversedKeyRange = clone(manifest);
              reversedKeyRange.datasets[0].chunks[0].first_key[0] = "zzzz";
              rejected(() => atlas.validateManifest(reversedKeyRange), "reversed chunk key range");

              const mismatchedDatasetRange = clone(manifest);
              mismatchedDatasetRange.datasets[0].key_range.last_key[0] = "zzzz";
              rejected(() => atlas.validateManifest(mismatchedDatasetRange), "mismatched dataset key range");

              const wrongSourceRows = clone(manifest);
              wrongSourceRows.source_stores[0].eligible_rows = 2;
              rejected(() => atlas.validateManifest(wrongSourceRows), "source eligible row mismatch");

              const unsafeInventory = clone(manifest);
              unsafeInventory.files.inventory_path = "//outside.example/checksums.json";
              rejected(() => atlas.validateManifest(unsafeInventory), "unsafe checksum inventory path");

              const rows = await atlas.verifyChunkBytes(payload, manifest.datasets[0].chunks[0]);
              if (rows.length !== 1 || rows[0].close !== 123.45) throw new Error("verified rows were not returned");
              const wrongDigest = Object.assign({}, manifest.datasets[0].chunks[0], { sha256: "0".repeat(64) });
              let digestRejected = false;
              try { await atlas.verifyChunkBytes(payload, wrongDigest); } catch (_error) { digestRejected = true; }
              if (!digestRejected) throw new Error("bad digest was accepted");
            })().catch((error) => { console.error(error); process.exitCode = 1; });
            """
        ).replace("__MANIFEST__", json.dumps(manifest)).replace("__PAYLOAD__", json.dumps(payload.hex()))
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "requires the WSL-native Node runtime")
    def test_static_loader_rejects_hostile_snapshot_contracts(self) -> None:
        harness = textwrap.dedent(
            """
            const { webcrypto, createHash } = require("node:crypto");
            globalThis.crypto = webcrypto;
            globalThis.window = { location: { href: "https://atlas.example/public/index.html" } };
            const atlas = require("./sites/quant-data-atlas/assets/atlas.js");
            const requests = [];
            let resources = new Map();
            globalThis.fetch = async (path, options) => {
              requests.push({ path, options });
              const payload = resources.get(path);
              return {
                ok: Boolean(payload),
                redirected: false,
                url: "https://atlas.example/public/" + path,
                headers: { get: () => null },
                arrayBuffer: async () => payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength)
              };
            };
            function digest(value) {
              return createHash("sha256").update(value).digest("hex");
            }
            function clone(value) {
              return JSON.parse(JSON.stringify(value));
            }
            function state() {
              return { identities: new Set(), previousRow: null, firstKey: null, lastKey: null, rows: 0 };
            }
            function rejected(fn, label) {
              let failed = false;
              try { fn(); } catch (_error) { failed = true; }
              if (!failed) throw new Error(label + " was accepted");
            }
            async function rejectedAsync(fn, label) {
              let failed = false;
              try { await fn(); } catch (_error) { failed = true; }
              if (!failed) throw new Error(label + " was accepted");
            }
            const datasetIds = ["market-prices", "gdp-vintages", "company-issuers", "news-items"];
            const sourceStores = ["market", "macro", "company", "news"];
            const identities = [
              ["fixture:SPY", "2026-08-01", "fixture", "close", "USD"],
              ["fixture", "2026-08-01", "advance"],
              ["0000001"],
              ["fixture", "news-item-1"]
            ];
            const payload = Buffer.from(JSON.stringify([{ trade_date: "2026-08-01", close: 123.45 }]));
            const chunkDigest = digest(payload);
            function chunk(id, key) {
              return {
                path: "data/chunks/" + id + "-0001.json",
                sha256: chunkDigest,
                rows: 1,
                bytes: payload.byteLength,
                first_key: key,
                last_key: key
              };
            }
            function dataset(index) {
              const id = datasetIds[index];
              const key = identities[index];
              return {
                id,
                label: ["Market prices", "GDP vintages", "Company issuers", "News items"][index],
                state: "ready",
                completeness: "complete",
                freshness: { state: "fixture" },
                schema: { id: id + ".placeholder", fields: ["trade_date", "close"] },
                rows: 1,
                bytes: payload.byteLength,
                key_range: { first_key: key, last_key: key },
                chunks: [chunk(id, key)]
              };
            }
            const manifest = {
              export_id: "atlas.fixture_snapshot",
              revision_id: "a".repeat(64),
              generated_at: "2026-08-11T12:00:00Z",
              cutoff: "2026-08-10T00:00:00Z",
              semantic_dataset_id: "atlas.fixture_snapshot.core_v1",
              lifecycle: { fixture_only: true, hosting: false, network: false, mode: "manual_only", status: "fixture_validated" },
              completeness: "complete",
              point_in_time: { availability: "at_or_before", date_only_policy: "completed_date", timezone: "aware_utc" },
              registry: { id: "quant_data", revision: "2.6.0" },
              contract: { chunking: { max_rows: 250, max_bytes: 262144 }, serialization: { format: "strict_json" } },
              provenance: { included_fields: ["availability"], excluded_fields: ["body"], source_fingerprint_policy: "logical_sha256" },
              source_stores: sourceStores.map((store, index) => ({
                store,
                snapshot_at: "2026-08-11T11:59:00Z",
                source_logical_sha256: "b".repeat(64),
                source_fingerprint: { method: "online_backup" },
                eligible_rows: 1,
                eligible_rows_sha256: "c".repeat(64)
              })),
              cross_store_atomic: false,
              totals: {
                rows: 4,
                chunk_bytes: payload.byteLength * 4,
                bytes: payload.byteLength * 4,
                bounds: { max_total_rows: 12000, max_bytes: 8388608 }
              },
              files: { count: 13, inventory_path: "data/checksums.json" },
              datasets: datasetIds.map((_id, index) => dataset(index))
            };
            function inventoryFor(manifestBytes, manifestDigest) {
              const records = [
                "assets/INTER-OFL-1.1.txt",
                "assets/atlas.css",
                "assets/atlas.js",
                "assets/dashboard.css",
                "assets/inter-variable.woff2",
                "index.html"
              ].map((path) => ({ path, sha256: "d".repeat(64), bytes: 1 }));
              records.push({ path: "data/schema.json", sha256: "e".repeat(64), bytes: 1 });
              for (const item of manifest.datasets) {
                records.push({
                  path: item.chunks[0].path,
                  sha256: item.chunks[0].sha256,
                  bytes: item.chunks[0].bytes
                });
              }
              records.push({ path: "data/manifest.json", sha256: manifestDigest, bytes: manifestBytes.byteLength });
              return { files: records };
            }
            function assertOnlySafePrelude() {
              const paths = requests.map((request) => request.path);
              if (JSON.stringify(paths) !== JSON.stringify(["data/manifest.json", "data/checksums.json"])) {
                throw new Error("invalid inventory caused an unsafe follow-up request: " + JSON.stringify(paths));
              }
              for (const request of requests) {
                if (!request.options || request.options.redirect !== "error" || /^https?:|^\/\//.test(request.path)) {
                  throw new Error("static request did not stay relative with redirect rejection");
                }
              }
            }
            (async () => {
              const manifestBytes = Buffer.from(JSON.stringify(manifest));
              resources = new Map([
                ["data/manifest.json", manifestBytes],
                ["data/checksums.json", Buffer.from(JSON.stringify({ files: [] }))]
              ]);
              requests.length = 0;
              await rejectedAsync(() => atlas.loadVerifiedSnapshot(), "missing checksum inventory");
              assertOnlySafePrelude();

              const tamperedInventory = inventoryFor(manifestBytes, "0".repeat(64));
              resources = new Map([
                ["data/manifest.json", manifestBytes],
                ["data/checksums.json", Buffer.from(JSON.stringify(tamperedInventory))]
              ]);
              requests.length = 0;
              await rejectedAsync(() => atlas.loadVerifiedSnapshot(), "tampered manifest checksum");
              assertOnlySafePrelude();

              const stringSchema = {
                fields: [{ name: "id", type: "string", nullable: false }],
                row_identity: ["id"],
                order_by: [{ field: "id", direction: "asc" }]
              };
              const finiteSchema = {
                fields: [
                  { name: "id", type: "string", nullable: false },
                  { name: "sequence", type: "integer", nullable: false }
                ],
                row_identity: ["id"],
                order_by: [{ field: "sequence", direction: "asc" }]
              };
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: "a", unexpected: true }],
                  { first_key: ["a"], last_key: ["a"] },
                  stringSchema,
                  state()
                ),
                "wrong row field"
              );
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: 7 }],
                  { first_key: [7], last_key: [7] },
                  stringSchema,
                  state()
                ),
                "wrong row type"
              );
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: "a", sequence: Infinity }],
                  { first_key: ["a"], last_key: ["a"] },
                  finiteSchema,
                  state()
                ),
                "nonfinite row value"
              );
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: "same" }, { id: "same" }],
                  { first_key: ["same"], last_key: ["same"] },
                  stringSchema,
                  state()
                ),
                "duplicate row identity"
              );
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: "b" }, { id: "a" }],
                  { first_key: ["b"], last_key: ["a"] },
                  stringSchema,
                  state()
                ),
                "out-of-order rows"
              );
              rejected(
                () => atlas.validateTypedChunkRows(
                  [{ id: "a" }],
                  { first_key: ["wrong"], last_key: ["a"] },
                  stringSchema,
                  state()
                ),
                "chunk key-range mismatch"
              );
            })().catch((error) => { console.error(error); process.exitCode = 1; });
            """
        )
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_static_source_has_no_generated_or_hosting_scaffold(self) -> None:
        required = {"index.html", "assets/atlas.css", "assets/atlas.js", "README.md"}
        for relative in required:
            self.assertTrue((ATLAS_ROOT / relative).is_file())
        forbidden = {
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            ".openai/hosting.json",
        }
        for relative in forbidden:
            self.assertFalse((ATLAS_ROOT / relative).exists())
        self.assertFalse((ATLAS_ROOT / "dist").exists())
        self.assertFalse((ATLAS_ROOT / "node_modules").exists())


if __name__ == "__main__":
    unittest.main()
