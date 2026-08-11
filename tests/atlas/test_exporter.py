from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.atlas import AtlasSnapshotExporter
from quant_data.atlas.contracts import AtlasProjection
from quant_data.atlas.projections import collect_atlas_projections
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ValidationError
from quant_data.fixtures import FixtureManifest
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.operations.backup import capture_readonly_copies
from quant_data.registry import load_registry
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _clock(cutoff: datetime, offset_seconds: int):
    calls = 0

    def now() -> datetime:
        nonlocal calls
        value = cutoff if calls == 0 else cutoff + timedelta(seconds=offset_seconds + calls)
        calls += 1
        return value

    return now


class AtlasSnapshotExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.store_map = _stores(self.root / "stores")
        initialize_all(self.store_map, self.registry)
        self.fixtures = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self.importer = DailyPriceImporter(self.store_map, self.fixtures)
        self.assertEqual(self.importer.import_fixture("market.base").outcome, "succeeded")
        self.assertEqual(
            MacroStage3FixtureImporter(self.store_map, self.fixtures)
            .import_fixture("gdp_advance")
            .outcome,
            "succeeded",
        )
        self.assertEqual(
            CompanyStage4FixtureImporter(self.store_map, self.fixtures)
            .import_fixture("sec_initial")
            .outcome,
            "succeeded",
        )
        self.assertEqual(
            NewsStage4FixtureImporter(self.store_map, self.fixtures)
            .import_fixture("news_initial")
            .outcome,
            "succeeded",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _exporter(
        self,
        *,
        cutoff: datetime,
        attempt: str,
        offset_seconds: int = 0,
        code_revision: str = "stage8-fixture",
    ) -> AtlasSnapshotExporter:
        return AtlasSnapshotExporter(
            self.registry,
            self.store_map,
            PROJECT_ROOT,
            self.root / "exports",
            _clock(cutoff, offset_seconds),
            lambda: attempt,
            code_revision,
        )

    def test_reuse_ignores_copy_timing_and_future_validated_delta(
        self,
    ) -> None:
        cutoff = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        cutoff_value, _ = self._exporter(
            cutoff=cutoff,
            attempt="projection-cutoff",
        )._cutoff()
        declaration = self.registry.export("atlas.fixture_snapshot")
        projections_before = collect_atlas_projections(
            self.registry,
            self.store_map,
            declaration,
            cutoff_value,
        )
        source_before = mutation_fingerprint(self.store_map)
        first = self._exporter(
            cutoff=cutoff,
            attempt="first",
            offset_seconds=0,
        ).publish()
        self.assertEqual(
            source_before["sha256"], mutation_fingerprint(self.store_map)["sha256"]
        )
        first_manifest = (
            self.root
            / "exports"
            / "revisions"
            / first.export_id
            / first.revision_id
            / "public"
            / "data"
            / "manifest.json"
        ).read_bytes()
        second = self._exporter(
            cutoff=cutoff,
            attempt="second",
            offset_seconds=30,
        ).publish()
        self.assertTrue(second.reused)
        self.assertEqual(first.revision_id, second.revision_id)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(
            first_manifest,
            (
                self.root
                / "exports"
                / "revisions"
                / first.export_id
                / first.revision_id
                / "public"
                / "data"
                / "manifest.json"
            ).read_bytes(),
        )

        self.assertEqual(
            self.importer.import_fixture("market.correction").outcome,
            "succeeded",
        )
        projections_after = collect_atlas_projections(
            self.registry,
            self.store_map,
            declaration,
            cutoff_value,
        )
        self.assertEqual(projections_before, projections_after)
        after_correction = self._exporter(
            cutoff=cutoff,
            attempt="future-validated-delta",
            offset_seconds=60,
        ).publish()
        self.assertTrue(after_correction.reused)
        self.assertEqual(first.revision_id, after_correction.revision_id)
        self.assertEqual(first.manifest_sha256, after_correction.manifest_sha256)
        self.assertEqual(
            first_manifest,
            (
                self.root
                / "exports"
                / "revisions"
                / after_correction.export_id
                / after_correction.revision_id
                / "public"
                / "data"
                / "manifest.json"
            ).read_bytes(),
        )

    def test_public_manifest_and_chunks_are_path_and_private_field_free(self) -> None:
        publication = self._exporter(
            cutoff=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
            attempt="public-check",
        ).publish()
        public_root = (
            self.root / "exports" / "revisions" / publication.export_id / publication.revision_id / "public"
        )
        manifest = json.loads((public_root / "data" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(manifest),
            {
                "export_id", "revision_id", "generated_at", "cutoff",
                "semantic_dataset_id", "lifecycle", "completeness",
                "point_in_time", "registry", "contract", "provenance",
                "source_stores", "cross_store_atomic", "totals", "files",
                "datasets",
            },
        )
        self.assertFalse(manifest["cross_store_atomic"])
        self.assertEqual(manifest["completeness"], "complete")
        self.assertEqual(manifest["registry"]["code_revision"], "stage8-fixture")
        self.assertIn("schema", manifest["contract"])
        self.assertLessEqual(manifest["totals"]["rows"], 12_000)
        self.assertLessEqual(manifest["totals"]["bytes"], 8 * 1024 * 1024)
        self.assertEqual(len(manifest["datasets"]), 4)
        excluded_fields = set(manifest["provenance"]["excluded_fields"])
        self.assertTrue(
            {
                "database_path", "filesystem_path", "private_receipt", "source_url",
                "artifact_id", "snapshot_id", "run_id",
            }.issubset(excluded_fields)
        )
        safe_manifest = dict(manifest)
        safe_provenance = dict(manifest["provenance"])
        safe_provenance["excluded_fields"] = []
        safe_manifest["provenance"] = safe_provenance
        rendered = json.dumps(safe_manifest, sort_keys=True) + "".join(
            path.read_text(encoding="utf-8")
            for path in public_root.rglob("*.json")
            if path.name != "manifest.json"
        )
        for forbidden in (
            str(self.root), "database_path", "filesystem_path", "private_receipt",
            "source_url", "lock_key", "artifact_id", "snapshot_id", "run_id",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue((public_root / "assets" / "dashboard.css").is_file())
        self.assertTrue((public_root / "assets" / "inter-variable.woff2").is_file())

    def test_public_source_provenance_uses_actual_copy_receipts(self) -> None:
        cutoff = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        publication = self._exporter(
            cutoff=cutoff,
            attempt="receipt-provenance",
            offset_seconds=3,
        ).publish()
        public_root = (
            self.root
            / "exports"
            / "revisions"
            / publication.export_id
            / publication.revision_id
            / "public"
        )
        manifest = json.loads(
            (public_root / "data" / "manifest.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (
                self.root
                / "exports"
                / "private-receipts"
                / publication.export_id
                / "receipt-provenance.json"
            ).read_text(encoding="utf-8")
        )
        cohort = receipt["copy_cohort"]
        copy_receipts = {item["role"]: item for item in cohort["receipts"]}
        source_checks = {
            item["role"]: item for item in cohort["source_receipt_checks"]
        }
        sources = {item["store"]: item for item in manifest["source_stores"]}
        self.assertEqual(
            manifest["provenance"]["coordination_window"],
            {
                "started_at": cohort["coordination_started_at"],
                "completed_at": cohort["coordination_completed_at"],
            },
        )
        for role, source in sources.items():
            copy = copy_receipts[role]
            check = source_checks[role]
            fingerprint = source["source_fingerprint"]
            self.assertEqual(source["snapshot_at"], copy["completed_at"])
            self.assertEqual(
                source["source_logical_sha256"], copy["source_logical_sha256"]
            )
            self.assertEqual(
                fingerprint["source_logical_sha256"],
                copy["source_logical_sha256"],
            )
            self.assertEqual(
                fingerprint["copy_logical_sha256"],
                copy["target_logical_sha256"],
            )
            self.assertEqual(fingerprint["completed_at"], copy["completed_at"])
            self.assertEqual(
                fingerprint["migration_evidence"],
                [
                    {
                        "ordinal": migration["ordinal"],
                        "sha256": migration["sha256"],
                        "reconstruction_state": migration["reconstruction_state"],
                    }
                    for migration in copy["source_inspection"]["migrations"]
                ],
            )
            self.assertEqual(
                fingerprint["checkpoint"],
                {
                    "dataset_id": check["dataset_id"],
                    "checkpoint_sha256": check["checkpoint_sha256"],
                    "completed_at": check["completed_at"],
                    "complete": True,
                },
            )
            self.assertNotEqual(
                source["source_logical_sha256"], source["eligible_rows_sha256"]
            )
        self.assertNotIn("lock_key", json.dumps(manifest, sort_keys=True))

    def test_revision_identity_is_stable_across_copy_receipt_evidence(self) -> None:
        """Physical copy evidence is public provenance, not PIT revision input."""

        cutoff = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        exporter = self._exporter(cutoff=cutoff, attempt="identity-evidence")
        cutoff_value, cutoff_text = exporter._cutoff()
        declaration = self.registry.export("atlas.fixture_snapshot")
        cohort = capture_readonly_copies(
            self.store_map,
            self.registry,
            self.root / "identity-copies",
            _clock(cutoff, 7),
            required_dataset_ids_by_role=exporter._required_dataset_ids(declaration),
        )
        projections = collect_atlas_projections(
            self.registry,
            cohort.copy_store_map,
            declaration,
            cutoff_value,
        )
        alternate_cohort = replace(
            cohort,
            receipts=(
                replace(
                    cohort.receipts[0],
                    source_logical_sha256="a" * 64,
                    target_logical_sha256="a" * 64,
                ),
                *cohort.receipts[1:],
            ),
            source_receipt_checks=(
                replace(
                    cohort.source_receipt_checks[0],
                    checkpoint_sha256="b" * 64,
                ),
                *cohort.source_receipt_checks[1:],
            ),
        )

        self.assertEqual(
            exporter._revision_id_v2(
                declaration,
                cutoff_text,
                projections,
                cohort,
            ),
            exporter._revision_id_v2(
                declaration,
                cutoff_text,
                projections,
                alternate_cohort,
            ),
        )
        original = {
            item["store"]: item
            for item in exporter._public_sources_v2(cohort, projections, cutoff_text)
        }
        alternate = {
            item["store"]: item
            for item in exporter._public_sources_v2(
                alternate_cohort,
                projections,
                cutoff_text,
            )
        }
        self.assertNotEqual(original["market"], alternate["market"])
        self.assertEqual(
            alternate["market"]["source_logical_sha256"],
            "a" * 64,
        )
        self.assertEqual(
            alternate["market"]["source_fingerprint"]["checkpoint"][
                "checkpoint_sha256"
            ],
            "b" * 64,
        )

    def test_byte_limited_chunking_neither_drops_nor_duplicates_rows(self) -> None:
        exporter = self._exporter(
            cutoff=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
            attempt="chunk-test",
        )
        rows = (
            {"value": "a" * 80},
            {"value": "b" * 80},
            {"value": "c" * 80},
        )
        projection = AtlasProjection(
            projection_id="market-prices",
            operation_id="market.daily_prices",
            dataset_id="fixture.market.daily_prices",
            store="market",
            schema_id="test.schema",
            fields=("value",),
            rows=rows,
            row_sha256="0" * 64,
        )
        one_row_bytes = len(dumps_strict([rows[0]]).encode("utf-8"))
        chunks = exporter._chunk_payloads(projection, max_rows=10, max_bytes=one_row_bytes + 1)
        decoded = tuple(row for payload in chunks for row in json.loads(payload.decode("utf-8")))
        self.assertEqual(decoded, rows)
        self.assertEqual(len(chunks), len(rows))


if __name__ == "__main__":
    unittest.main()
