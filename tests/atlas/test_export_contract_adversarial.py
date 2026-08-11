"""Adversarial contract tests for the bounded offline Stage 8 Atlas export.

These tests deliberately use only explicit ``/tmp`` four-store fixtures.  They
exercise the receipt-chain, strict-schema, resource-bound, and publication
contracts without changing the approved Stage 8 golden evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import quant_data.atlas.projections as atlas_projections
from quant_data.atlas import AtlasSnapshotExporter
from quant_data.atlas.contracts import AtlasProjection
from quant_data.atlas.site_bundle import copy_site_bundle
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.operations.backup import (
    _required_source_receipt_checks,
    backup_all,
    capture_readonly_copies,
)
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock
from quant_data.temporal import TemporalValue


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
EXPORT_ID = "atlas.fixture_snapshot"
CUTOFF = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
REQUIRED_DATASETS_BY_ROLE = {
    StoreRole.MARKET: "fixture.market.daily_prices",
    StoreRole.MACRO: "fixture.macro.gdp_vintages",
    StoreRole.COMPANY: "fixture.company.issuers",
    StoreRole.NEWS: "fixture.news.items",
}
_PRIVATE_PATH_KEYS = {
    "database_path",
    "filesystem_path",
    "physical_path",
    "project_root",
    "export_root",
    "source_path",
    "target_path",
}


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _render_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class AtlasExportContractAdversarialTests(unittest.TestCase):
    """Independent hostile inputs for the reviewed one-profile export contract."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.assertTrue(str(self.root).startswith("/tmp/"))
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.stores = _stores(self.root / "source")
        initialize_all(self.stores, self.registry)
        self.fixtures = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self._seed_complete_receipt_chain()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_complete_receipt_chain(self) -> None:
        imports = (
            (DailyPriceImporter(self.stores, self.fixtures), "market.base"),
            (MacroStage3FixtureImporter(self.stores, self.fixtures), "gdp_advance"),
            (CompanyStage4FixtureImporter(self.stores, self.fixtures), "sec_initial"),
            (NewsStage4FixtureImporter(self.stores, self.fixtures), "news_initial"),
        )
        for importer, fixture_id in imports:
            receipt = importer.import_fixture(fixture_id)
            self.assertEqual(receipt.outcome, "succeeded", fixture_id)
            self.assertGreater(receipt.written_count, 0, fixture_id)

    def _clone(self, label: str) -> StoreMap:
        return backup_all(
            self.stores,
            self.registry,
            target_root=self.root / "clones" / label,
        ).store_map

    def _capture(self, stores: StoreMap, label: str):
        return capture_readonly_copies(
            stores,
            self.registry,
            self.root / "copies" / label,
            CUTOFF,
            required_dataset_ids_by_role=REQUIRED_DATASETS_BY_ROLE,
        )

    def _exporter(
        self,
        *,
        attempt: str,
        stores: StoreMap | None = None,
        output: Path | None = None,
        code_revision: str = "stage8-adversarial",
        failure_hook=None,
        monotonic=None,
    ) -> AtlasSnapshotExporter:
        return AtlasSnapshotExporter(
            self.registry,
            self.stores if stores is None else stores,
            PROJECT_ROOT,
            self.root / "exports" if output is None else output,
            CUTOFF,
            lambda: attempt,
            code_revision,
            failure_hook,
            monotonic=monotonic,
        )

    @staticmethod
    def _checkpoint(connection: sqlite3.Connection, dataset_id: str) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT last_successful_run_id, last_semantic_identity
            FROM dataset_registry
            WHERE dataset_id=?
            """,
            (dataset_id,),
        ).fetchone()
        if row is None:
            raise AssertionError("fixture did not create a dataset checkpoint")
        return row

    def _mutate(
        self,
        stores: StoreMap,
        role: StoreRole,
        callback,
    ) -> None:
        """Change only an isolated /tmp clone to model a corrupt receipt chain."""

        with StoreWriteLock(stores.path(role), timeout_seconds=0.0):
            connection = sqlite3.connect(stores.path(role))
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=OFF")
                callback(connection)
                connection.commit()
            finally:
                connection.close()

    @staticmethod
    def _drop_immutable_delete_trigger(connection: sqlite3.Connection, table: str) -> None:
        connection.execute(f"DROP TRIGGER {table}_immutable_delete")

    def _remove_chain_link(self, stores: StoreMap, link: str) -> None:
        role = StoreRole.MARKET
        dataset_id = REQUIRED_DATASETS_BY_ROLE[role]

        def remove(connection: sqlite3.Connection) -> None:
            checkpoint = self._checkpoint(connection, dataset_id)
            run_id = str(checkpoint["last_successful_run_id"])
            if link == "output":
                self._drop_immutable_delete_trigger(connection, "ingestion_run_outputs")
                connection.execute(
                    "DELETE FROM ingestion_run_outputs WHERE run_id=? AND dataset_id=?",
                    (run_id, dataset_id),
                )
                return
            run = connection.execute(
                "SELECT artifact_id, snapshot_id FROM ingestion_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise AssertionError("fixture did not create the checkpoint run")
            if link == "artifact":
                self._drop_immutable_delete_trigger(connection, "ingestion_artifacts")
                connection.execute(
                    "DELETE FROM ingestion_artifacts WHERE artifact_id=?",
                    (run["artifact_id"],),
                )
                return
            if link == "snapshot":
                self._drop_immutable_delete_trigger(connection, "ingestion_snapshots")
                connection.execute(
                    "DELETE FROM ingestion_snapshots WHERE snapshot_id=?",
                    (run["snapshot_id"],),
                )
                return
            if link == "membership":
                self._drop_immutable_delete_trigger(
                    connection,
                    "ingestion_snapshot_artifacts",
                )
                connection.execute(
                    "DELETE FROM ingestion_snapshot_artifacts WHERE snapshot_id=? AND artifact_id=?",
                    (run["snapshot_id"], run["artifact_id"]),
                )
                return
            raise AssertionError(f"unknown receipt-chain link: {link}")

        self._mutate(stores, role, remove)

    def _set_missing_checkpoint(self, stores: StoreMap) -> None:
        role = StoreRole.MARKET
        dataset_id = REQUIRED_DATASETS_BY_ROLE[role]

        def remove(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE dataset_registry
                SET last_successful_run_id=NULL, last_semantic_identity=NULL
                WHERE dataset_id=?
                """,
                (dataset_id,),
            )

        self._mutate(stores, role, remove)

    def _insert_running_selected_output(self, stores: StoreMap) -> None:
        role = StoreRole.MARKET
        dataset_id = REQUIRED_DATASETS_BY_ROLE[role]

        def insert(connection: sqlite3.Connection) -> None:
            semantic_identity = "f" * 64
            run_id = "stage8-running-output"
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, completed_at, artifact_id, snapshot_id,
                    fetched_count, written_count, warnings_json, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, NULL, NULL, 0, 0, '[]', ?)
                """,
                (
                    run_id,
                    dataset_id,
                    semantic_identity,
                    "stage8-adversarial",
                    "{}",
                    _render_datetime(CUTOFF),
                    "stage8-adversarial",
                ),
            )
            connection.execute(
                """
                INSERT INTO ingestion_run_outputs (run_id, dataset_id, semantic_identity)
                VALUES (?, ?, ?)
                """,
                (run_id, dataset_id, semantic_identity),
            )

        self._mutate(stores, role, insert)

    def _insert_terminal_failure(self, stores: StoreMap, *, newer: bool) -> None:
        role = StoreRole.MARKET
        dataset_id = REQUIRED_DATASETS_BY_ROLE[role]

        def insert(connection: sqlite3.Connection) -> None:
            checkpoint = self._checkpoint(connection, dataset_id)
            run = connection.execute(
                "SELECT completed_at FROM ingestion_runs WHERE run_id=?",
                (checkpoint["last_successful_run_id"],),
            ).fetchone()
            if run is None or not isinstance(run["completed_at"], str):
                raise AssertionError("fixture checkpoint has no completion time")
            completed = datetime.fromisoformat(run["completed_at"].replace("Z", "+00:00"))
            failure_at = completed + timedelta(seconds=1) if newer else completed - timedelta(seconds=1)
            suffix = "newer" if newer else "older"
            connection.execute(
                """
                INSERT INTO ingestion_run_failures (
                    failure_id, run_id, dataset_id, semantic_identity, command,
                    scope_json, status, started_at, completed_at, fetched_count,
                    error_code, code_version
                ) VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, 0, ?, ?)
                """,
                (
                    f"stage8-{suffix}-failure",
                    f"stage8-{suffix}-failed-run",
                    dataset_id,
                    ("e" if newer else "d") * 64,
                    "stage8-adversarial",
                    "{}",
                    _render_datetime(failure_at - timedelta(seconds=1)),
                    _render_datetime(failure_at),
                    "injected_failure",
                    "stage8-adversarial",
                ),
            )

        self._mutate(stores, role, insert)

    def _market_projection_and_declaration(self) -> tuple[AtlasProjection, dict[str, object]]:
        declaration = self.registry.export(EXPORT_ID)
        specs = atlas_projections._projection_specs(declaration)
        spec = next(item for item in specs if item["id"] == "market-prices")
        row = {
            "instrument_id": "fixture:SPY",
            "trade_date": "2026-08-10",
            "provider": "fixture",
            "price_variant": "close",
            "currency_segment": "USD",
            "open": "1.00",
            "high": "1.00",
            "low": "1.00",
            "close": "1.00",
            "volume": 1,
            "available_at": "2026-08-10T16:00:00Z",
            "available_precision": "datetime",
            "captured_at": "2026-08-10T16:00:00Z",
            "captured_precision": "datetime",
            "correction_sequence": 1,
        }
        projected = atlas_projections._finish_projection(
            declaration=spec,
            rows=(row,),
            warnings=set(),
        )
        return projected, dict(spec)

    def _private_receipts(self, output: Path) -> list[tuple[Path, dict[str, object]]]:
        root = output / "private-receipts"
        if not root.exists():
            return []
        result: list[tuple[Path, dict[str, object]]] = []
        for path in sorted(root.rglob("*.json")):
            value = loads_strict(path.read_bytes())
            if isinstance(value, dict) and value.get("export_id") == EXPORT_ID:
                result.append((path, value))
        return result

    def _assert_path_safe_public_value(self, value: object) -> None:
        if isinstance(value, dict):
            self.assertFalse(set(value).intersection(_PRIVATE_PATH_KEYS))
            for key, item in value.items():
                if key == "path":
                    self.assertIsInstance(item, str)
                    self.assertFalse(str(item).startswith(("/", "\\\\")))
                    self.assertNotIn("..", Path(str(item)).parts)
                    self.assertNotIn(chr(92), str(item))
                    self.assertNotIn(":", str(item))
                self._assert_path_safe_public_value(item)
        elif isinstance(value, list):
            for item in value:
                self._assert_path_safe_public_value(item)
        elif isinstance(value, str):
            self.assertNotIn(str(self.root), value)
            self.assertNotIn(str(PROJECT_ROOT), value)
            self.assertNotIn("/tmp/", value)

    def test_complete_required_chain_is_copied_and_required_by_exporter_without_source_mutation(self) -> None:
        before = mutation_fingerprint(self.stores)
        cohort = self._capture(self.stores, "complete-chain")
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])
        self.assertEqual(
            tuple(item.role for item in cohort.source_receipt_checks),
            tuple(role.value for role in StoreRole),
        )
        self.assertTrue(all(item.complete for item in cohort.source_receipt_checks))
        self.assertTrue(
            all(item.running_runs == 0 for item in cohort.source_receipt_checks)
        )
        self.assertTrue(
            all(item.unreconciled_failures == 0 for item in cohort.source_receipt_checks)
        )

        exporter = self._exporter(attempt="complete-export")
        with patch(
            "quant_data.atlas.exporter.capture_readonly_copies",
            wraps=capture_readonly_copies,
        ) as captured:
            publication = exporter.publish()
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])
        self.assertEqual(publication.export_id, EXPORT_ID)
        required = captured.call_args.kwargs.get("required_dataset_ids_by_role")
        self.assertIsNotNone(required)
        self.assertEqual(
            {StoreRole(role).value: dataset for role, dataset in required.items()},
            {role.value: dataset for role, dataset in REQUIRED_DATASETS_BY_ROLE.items()},
        )

    def test_required_chain_rejects_missing_checkpoint_output_artifact_snapshot_and_membership(self) -> None:
        cases = (("checkpoint", self._set_missing_checkpoint),) + tuple(
            (
                link,
                lambda stores, link=link: self._remove_chain_link(stores, link),
            )
            for link in ("output", "artifact", "snapshot", "membership")
        )
        for label, corrupt in cases:
            with self.subTest(link=label):
                stores = self._clone("missing-" + label)
                corrupt(stores)
                with self.assertRaises(ValidationError):
                    _required_source_receipt_checks(stores, REQUIRED_DATASETS_BY_ROLE)

    def test_selected_output_running_and_newer_terminal_failure_fail_but_older_failure_reconciles(self) -> None:
        running = self._clone("selected-output-running")
        self._insert_running_selected_output(running)
        with self.assertRaisesRegex(ValidationError, "running collector"):
            self._capture(running, "selected-output-running")

        older = self._clone("older-failure")
        self._insert_terminal_failure(older, newer=False)
        cohort = self._capture(older, "older-failure")
        self.assertTrue(all(item.unreconciled_failures == 0 for item in cohort.source_receipt_checks))

        newer = self._clone("newer-failure")
        self._insert_terminal_failure(newer, newer=True)
        with self.assertRaisesRegex(ValidationError, "failed or partial collector"):
            self._capture(newer, "newer-failure")

    def test_nonfinite_float_decimal_and_registered_type_drift_are_rejected_before_publication(self) -> None:
        cases = (
            (float("nan"), "integer"),
            (float("inf"), "integer"),
            (Decimal("NaN"), "decimal_string"),
            (Decimal("Infinity"), "decimal_string"),
            ("42", "integer"),
            (42, "decimal_string"),
        )
        for value, type_name in cases:
            with self.subTest(value=repr(value), type_name=type_name):
                with self.assertRaises(ValidationError):
                    atlas_projections._validate_public_value(
                        value,
                        field="adversarial",
                        type_name=type_name,
                        nullable=False,
                    )
        self.assertFalse((self.root / "exports" / "revisions").exists())

    def test_duplicate_identity_and_out_of_order_schema_mapping_fail_closed(self) -> None:
        projection, declaration = self._market_projection_and_declaration()
        self.assertGreater(len(projection.rows), 0)
        row = dict(projection.rows[0])
        with self.assertRaisesRegex(ValidationError, "identity is not unique"):
            atlas_projections._finish_projection(
                declaration=declaration,
                rows=(row, dict(row)),
                warnings=set(),
            )
        reversed_row = {key: row[key] for key in reversed(tuple(row))}
        with self.assertRaisesRegex(ValidationError, "fields do not match"):
            atlas_projections._finish_projection(
                declaration=declaration,
                rows=(reversed_row,),
                warnings=set(),
            )

    def test_per_projection_and_aggregate_row_and_byte_bounds_fail_closed(self) -> None:
        projection, declaration = self._market_projection_and_declaration()
        over_projection = tuple(dict(projection.rows[0]) for _ in range(5001))
        with self.assertRaises(ResourceLimitError):
            atlas_projections._finish_projection(
                declaration=declaration,
                rows=over_projection,
                warnings=set(),
            )

        export = self.registry.export(EXPORT_ID)
        cutoff = TemporalValue.parse("2026-08-11T12:00:00Z", pointer="/cutoff")

        def fake_projection(rows: tuple[dict[str, object], ...]):
            def select(_stores, declared, _cutoff):
                return AtlasProjection(
                    projection_id=str(declared["id"]),
                    operation_id=str(declared["operation_id"]),
                    dataset_id=str(declared["dataset"]),
                    store=str(declared["store"]),
                    schema_id=str(declared["schema_id"]),
                    fields=("payload",),
                    rows=rows,
                    row_sha256="0" * 64,
                )

            return select

        selector_patch = {
            "_market_projection": fake_projection(({"payload": "x"},) * 3001),
            "_gdp_projection": fake_projection(({"payload": "x"},) * 3001),
            "_company_projection": fake_projection(({"payload": "x"},) * 3001),
            "_news_projection": fake_projection(({"payload": "x"},) * 3001),
        }
        with patch.multiple(atlas_projections, **selector_patch):
            with self.assertRaisesRegex(ResourceLimitError, "total row bound"):
                atlas_projections.collect_atlas_projections(
                    self.registry,
                    self.stores,
                    export,
                    cutoff,
                )

        large_row = {"payload": "x" * (2 * 1024 * 1024)}
        selector_patch = {
            "_market_projection": fake_projection((large_row,)),
            "_gdp_projection": fake_projection((large_row,)),
            "_company_projection": fake_projection((large_row,)),
            "_news_projection": fake_projection((large_row,)),
        }
        with patch.multiple(atlas_projections, **selector_patch):
            with self.assertRaisesRegex(ResourceLimitError, "byte bound"):
                atlas_projections.collect_atlas_projections(
                    self.registry,
                    self.stores,
                    export,
                    cutoff,
                )

    def test_projection_and_exporter_monotonic_deadlines_fail_before_current_or_source_mutation(self) -> None:
        cohort = self._capture(self.stores, "deadline-source")
        with self.assertRaisesRegex(ResourceLimitError, "runtime bound"):
            atlas_projections.collect_atlas_projections(
                self.registry,
                cohort.copy_store_map,
                self.registry.export(EXPORT_ID),
                TemporalValue.parse("2026-08-11T12:00:00Z", pointer="/cutoff"),
                deadline=30.0,
                monotonic=lambda: 31.0,
            )

        before = mutation_fingerprint(self.stores)
        output = self.root / "deadline-export"
        monotonic_values = iter((0.0, 31.0))
        exporter = self._exporter(
            attempt="deadline-export",
            output=output,
            monotonic=lambda: next(monotonic_values),
        )
        with self.assertRaisesRegex(ResourceLimitError, "runtime bound"):
            exporter.publish()
        self.assertEqual(before["sha256"], mutation_fingerprint(self.stores)["sha256"])
        self.assertFalse((output / "current" / f"{EXPORT_ID}.json").exists())

    def test_private_receipts_precede_pointer_and_pointer_binds_final_receipt_digest(self) -> None:
        output = self.root / "receipt-order"
        exporter = self._exporter(attempt="two-phase", output=output)
        observed_pointer_states: list[bool] = []
        original = exporter._write_private_receipt

        def record_receipt(*args, **kwargs):
            observed_pointer_states.append(
                (output / "current" / f"{EXPORT_ID}.json").exists()
            )
            return original(*args, **kwargs)

        with patch.object(exporter, "_write_private_receipt", side_effect=record_receipt):
            publication = exporter.publish()
        self.assertGreaterEqual(len(observed_pointer_states), 2)
        self.assertFalse(observed_pointer_states[0])
        pointer = loads_strict((output / "current" / f"{EXPORT_ID}.json").read_bytes())
        self.assertEqual(
            pointer,
            {
                "export_id": EXPORT_ID,
                "revision_id": publication.revision_id,
                "manifest_sha256": publication.manifest_sha256,
                "receipt_sha256": publication.receipt_sha256,
            },
        )
        receipts = self._private_receipts(output)
        self.assertGreaterEqual(len(receipts), 2)
        final_receipts = [
            (path, value)
            for path, value in receipts
            if value.get("revision_id") == publication.revision_id
        ]
        self.assertTrue(final_receipts)
        self.assertIn(
            publication.receipt_sha256,
            {_sha256(path.read_bytes()) for path, _value in final_receipts},
        )

    def test_receipt_failure_preserves_prior_pointer_and_post_promotion_orphan_has_receipt(self) -> None:
        output = self.root / "receipt-failure"
        baseline = self._exporter(
            attempt="baseline",
            output=output,
            code_revision="receipt-baseline",
        ).publish()
        pointer_path = output / "current" / f"{EXPORT_ID}.json"
        pointer_before = pointer_path.read_bytes()

        failed_receipt = self._exporter(
            attempt="receipt-write-failure",
            output=output,
            code_revision="receipt-failure",
        )
        with patch.object(
            failed_receipt,
            "_write_private_receipt",
            side_effect=RuntimeError("injected receipt failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "receipt failure"):
                failed_receipt.publish()
        self.assertEqual(pointer_path.read_bytes(), pointer_before)

        self.assertEqual(
            {
                path.name
                for path in (output / "revisions" / EXPORT_ID).iterdir()
                if path.is_dir()
            },
            {baseline.revision_id},
        )
        def fail_before_pointer(phase: str) -> None:
            if phase == "before_pointer":
                raise RuntimeError("injected before pointer")

        with self.assertRaisesRegex(RuntimeError, "before pointer"):
            self._exporter(
                attempt="orphan",
                output=output,
                code_revision="receipt-orphan",
                failure_hook=fail_before_pointer,
            ).publish()
        self.assertEqual(pointer_path.read_bytes(), pointer_before)
        revisions = sorted(
            path.name
            for path in (output / "revisions" / EXPORT_ID).iterdir()
            if path.is_dir() and path.name != baseline.revision_id
        )
        self.assertEqual(len(revisions), 1)
        orphan_id = revisions[0]
        orphan_receipts = [
            value
            for _path, value in self._private_receipts(output)
            if value.get("revision_id") == orphan_id
        ]
        self.assertTrue(orphan_receipts)

    def test_public_manifest_has_lifecycle_pit_totals_provenance_fingerprints_consumers_and_safe_shape(self) -> None:
        output = self.root / "public-contract"
        publication = self._exporter(attempt="public-contract", output=output).publish()
        public = output / "revisions" / EXPORT_ID / publication.revision_id / "public"
        manifest = loads_strict((public / "data" / "manifest.json").read_bytes())
        self.assertIsInstance(manifest, dict)
        required = {
            "export_id",
            "revision_id",
            "generated_at",
            "cutoff",
            "semantic_dataset_id",
            "lifecycle",
            "completeness",
            "point_in_time",
            "registry",
            "contract",
            "provenance",
            "source_stores",
            "cross_store_atomic",
            "totals",
            "files",
            "datasets",
        }
        self.assertTrue(required.issubset(manifest))
        self.assertEqual(manifest["export_id"], EXPORT_ID)
        self.assertEqual(manifest["revision_id"], publication.revision_id)
        self.assertFalse(manifest["cross_store_atomic"])
        self.assertEqual(manifest["completeness"], "complete")
        self.assertTrue(manifest["lifecycle"]["fixture_only"])
        self.assertEqual(manifest["point_in_time"]["availability"], "at_or_before")
        self.assertEqual(manifest["point_in_time"]["date_only_policy"], "completed_date")
        self.assertIn("consumers", manifest["contract"])
        self.assertIn("included_fields", manifest["provenance"])
        self.assertIn("excluded_fields", manifest["provenance"])
        self.assertEqual(len(manifest["source_stores"]), 4)
        self.assertTrue(
            all("source_fingerprint" in item for item in manifest["source_stores"])
        )
        self.assertLessEqual(manifest["totals"]["rows"], 12_000)
        self.assertLessEqual(manifest["totals"]["bytes"], 8 * 1024 * 1024)
        self._assert_path_safe_public_value(manifest)

    def test_protocol_relative_site_bundle_source_is_rejected(self) -> None:
        source_root = self.root / "site-source"
        for relative in (
            "quant_data/dashboard/static/dashboard.css",
            "quant_data/dashboard/static/inter-variable.woff2",
            "quant_data/dashboard/licenses/INTER-OFL-1.1.txt",
            "sites/quant-data-atlas/index.html",
            "sites/quant-data-atlas/assets/atlas.css",
        ):
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((PROJECT_ROOT / relative).read_bytes())
        unsafe_script = source_root / "sites" / "quant-data-atlas" / "assets" / "atlas.js"
        unsafe_script.parent.mkdir(parents=True, exist_ok=True)
        unsafe_script.write_text(
            "const blockedProtocolRelativeAsset = '//example.invalid/atlas.js';\n",
            encoding="utf-8",
        )
        payload_root = self.root / "site-payload"
        payload_root.mkdir()
        with self.assertRaises(ValidationError):
            copy_site_bundle(source_root, payload_root)


if __name__ == "__main__":
    unittest.main()
