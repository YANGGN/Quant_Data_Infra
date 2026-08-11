from __future__ import annotations

from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import quant_data.atlas.exporter as atlas_exporter
from quant_data.atlas import AtlasSnapshotExporter, validate_export_root
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ConflictError, ValidationError
from quant_data.fixtures import FixtureManifest
from quant_data.fingerprint import mutation_fingerprint
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.registry import load_registry
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
CUTOFF = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _stores(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )

def _publish_in_process(
    source_root: str,
    export_root: str,
    attempt: str,
    code_revision: str,
    entered_after_pointer_read,
    release_after_pointer_read,
    results,
) -> None:
    """Publish in an independent WSL process for the fcntl lock regression."""

    registry = load_registry(
        REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )

    def hook(phase: str) -> None:
        if phase != "after_pointer_read":
            return
        entered_after_pointer_read.set()
        if (
            release_after_pointer_read is not None
            and not release_after_pointer_read.wait(timeout=10)
        ):
            raise RuntimeError("test did not release Atlas publication lock")

    try:
        publication = AtlasSnapshotExporter(
            registry,
            _stores(Path(source_root)),
            PROJECT_ROOT,
            Path(export_root),
            CUTOFF,
            lambda: attempt,
            code_revision,
            hook,
        ).publish()
    except BaseException as exc:
        results.put((attempt, "error", type(exc).__name__, str(exc)))
    else:
        results.put((attempt, "ok", publication.revision_id))



class AtlasPublicationFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.stores = _stores(self.root / "stores")
        initialize_all(self.stores, self.registry)
        fixtures = FixtureManifest.load(FIXTURE_MANIFEST_PATH, project_root=PROJECT_ROOT)
        DailyPriceImporter(self.stores, fixtures).import_fixture("market.base")
        MacroStage3FixtureImporter(self.stores, fixtures).import_fixture("gdp_advance")
        CompanyStage4FixtureImporter(self.stores, fixtures).import_fixture("sec_initial")
        NewsStage4FixtureImporter(self.stores, fixtures).import_fixture("news_initial")
        self.export_root = self.root / "exports"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _exporter(
        self,
        attempt: str,
        *,
        code_revision: str = "stage8-failure",
        failure_hook=None,
    ) -> AtlasSnapshotExporter:
        return AtlasSnapshotExporter(
            self.registry,
            self.stores,
            PROJECT_ROOT,
            self.export_root,
            CUTOFF,
            lambda: attempt,
            code_revision,
            failure_hook,
        )

    def _pointer(self) -> Path:
        return self.export_root / "current" / "atlas.fixture_snapshot.json"

    def test_failure_after_revision_or_receipt_preserves_old_current_bytes(self) -> None:
        baseline = self._exporter("baseline", code_revision="stage8-baseline").publish()
        original_pointer = self._pointer().read_bytes()

        def before_pointer(phase: str) -> None:
            if phase == "before_pointer":
                raise RuntimeError("injected before pointer")

        with self.assertRaisesRegex(RuntimeError, "before pointer"):
            self._exporter(
                "after-revision",
                code_revision="stage8-after-revision",
                failure_hook=before_pointer,
            ).publish()
        self.assertEqual(self._pointer().read_bytes(), original_pointer)
        pending_receipt = json.loads(
            (
                self.export_root
                / "private-receipts"
                / baseline.export_id
                / "after-revision.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(pending_receipt["phase"], "publication")
        self.assertEqual(pending_receipt["outcome"], "revision_promoted")
        self.assertEqual(
            pending_receipt["promotion"],
            {"state": "promoted", "reused": False},
        )
        self.assertEqual(pending_receipt["pointer_state"], "not_published")
        self.assertEqual(pending_receipt["pointer_intent"], "current")
        self.assertNotEqual(pending_receipt["outcome"], "succeeded")
        revision_root = self.export_root / "revisions" / baseline.export_id
        self.assertGreaterEqual(len([path for path in revision_root.iterdir() if path.is_dir()]), 2)

        exporter = self._exporter("receipt-failure", code_revision="stage8-receipt-failure")
        with patch.object(exporter, "_write_private_receipt", side_effect=RuntimeError("receipt")):
            with self.assertRaisesRegex(RuntimeError, "receipt"):
                exporter.publish()
        self.assertEqual(self._pointer().read_bytes(), original_pointer)


    def test_pointer_replace_failure_cleans_temp_and_allows_retry(self) -> None:
        baseline = self._exporter(
            "replace-baseline",
            code_revision="stage8-replace-baseline",
        ).publish()
        pointer = self._pointer()
        pointer_before = pointer.read_bytes()
        stores_before = mutation_fingerprint(self.stores)
        real_replace = os.replace

        def fail_pointer_replace(source: object, destination: object) -> None:
            if Path(destination) == pointer:
                raise OSError("injected pointer replace failure")
            real_replace(source, destination)

        with patch.object(
            atlas_exporter.os,
            "replace",
            side_effect=fail_pointer_replace,
        ):
            with self.assertRaisesRegex(
                ValidationError,
                "Atlas atomic promotion failed",
            ):
                self._exporter(
                    "replace-failure",
                    code_revision="stage8-replace-retry",
                ).publish()

        self.assertEqual(pointer.read_bytes(), pointer_before)
        self.assertEqual(
            list(pointer.parent.glob("." + pointer.name + ".*.tmp")),
            [],
        )
        retried = self._exporter(
            "replace-retry",
            code_revision="stage8-replace-retry",
        ).publish()
        self.assertNotEqual(retried.revision_id, baseline.revision_id)
        self.assertEqual(
            json.loads(pointer.read_text(encoding="utf-8"))["revision_id"],
            retried.revision_id,
        )
        self.assertEqual(mutation_fingerprint(self.stores), stores_before)

    def test_post_commit_directory_fsync_failure_reports_success(self) -> None:
        self._exporter(
            "fsync-baseline",
            code_revision="stage8-fsync-baseline",
        ).publish()
        pointer = self._pointer()
        stores_before = mutation_fingerprint(self.stores)
        real_replace = os.replace
        real_fsync = os.fsync
        pointer_committed = False

        def track_pointer_replace(source: object, destination: object) -> None:
            nonlocal pointer_committed
            real_replace(source, destination)
            if Path(destination) == pointer:
                pointer_committed = True

        def fail_post_commit_fsync(descriptor: int) -> None:
            nonlocal pointer_committed
            if pointer_committed:
                pointer_committed = False
                raise OSError("injected post-commit directory fsync failure")
            real_fsync(descriptor)

        with patch.object(
            atlas_exporter.os,
            "replace",
            side_effect=track_pointer_replace,
        ), patch.object(
            atlas_exporter.os,
            "fsync",
            side_effect=fail_post_commit_fsync,
        ):
            publication = self._exporter(
                "fsync-commit",
                code_revision="stage8-fsync-commit",
            ).publish()

        pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
        self.assertEqual(pointer_payload["revision_id"], publication.revision_id)
        self.assertEqual(
            pointer_payload["receipt_sha256"],
            publication.receipt_sha256,
        )
        self.assertEqual(
            list(pointer.parent.glob("." + pointer.name + ".*.tmp")),
            [],
        )
        self.assertEqual(mutation_fingerprint(self.stores), stores_before)

    def test_checksum_tamper_is_rejected_without_overwriting_current(self) -> None:
        publication = self._exporter("tamper-base", code_revision="stage8-tamper").publish()
        pointer_before = self._pointer().read_bytes()
        public = (
            self.export_root / "revisions" / publication.export_id / publication.revision_id / "public"
        )
        chunk = next((public / "data" / "chunks").glob("*.json"))
        chunk.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self._exporter("tamper-reuse", code_revision="stage8-tamper").publish()
        self.assertEqual(self._pointer().read_bytes(), pointer_before)

    def test_only_exact_staging_attempt_is_removed_and_permissions_are_private(self) -> None:
        first = self._exporter("permissions", code_revision="stage8-permissions").publish()
        staging = self.export_root / ".staging"
        sibling = staging / "unrelated-attempt"
        sibling.mkdir()
        sentinel = sibling / "preserve.txt"
        sentinel.write_text("preserve", encoding="utf-8")

        def fail_after_query(phase: str) -> None:
            if phase == "after_query":
                raise RuntimeError("stop")

        with self.assertRaisesRegex(RuntimeError, "stop"):
            self._exporter("exact-cleanup", failure_hook=fail_after_query).publish()
        self.assertTrue(sentinel.is_file())
        self.assertFalse((staging / "exact-cleanup").exists())
        self.assertEqual(os.stat(staging).st_mode & 0o777, 0o700)
        receipt = self.export_root / "private-receipts" / first.export_id / "permissions.json"
        self.assertEqual(os.stat(receipt.parent).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(receipt).st_mode & 0o777, 0o600)
        public_root = self.export_root / "revisions" / first.export_id / first.revision_id / "public"
        self.assertEqual(os.stat(public_root).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(public_root / "data" / "manifest.json").st_mode & 0o777, 0o644)

    def test_cross_process_publication_serializes_pointer_read_and_commit(self) -> None:
        context = multiprocessing.get_context("fork")
        first_read = context.Event()
        second_read = context.Event()
        release_first = context.Event()
        results = context.Queue()
        first = context.Process(
            target=_publish_in_process,
            args=(
                str(self.root / "stores"),
                str(self.export_root),
                "lock-first",
                "stage8-lock-first",
                first_read,
                release_first,
                results,
            ),
        )
        second = context.Process(
            target=_publish_in_process,
            args=(
                str(self.root / "stores"),
                str(self.export_root),
                "lock-second",
                "stage8-lock-second",
                second_read,
                None,
                results,
            ),
        )
        first.start()
        try:
            self.assertTrue(first_read.wait(timeout=10))
            second.start()
            # The second publisher cannot read a stale pointer while the first
            # holds the private export lock after its own pointer read.
            self.assertFalse(second_read.wait(timeout=1))
            release_first.set()
            first.join(timeout=15)
            second.join(timeout=15)
        finally:
            release_first.set()
            for process in (first, second):
                if process.pid is None:
                    continue
                process.join(timeout=3)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=3)

        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        outcomes = {
            record[0]: record[1:]
            for record in (results.get(timeout=5), results.get(timeout=5))
        }
        self.assertEqual(outcomes["lock-first"][0], "ok", outcomes)
        self.assertEqual(outcomes["lock-second"][0], "ok", outcomes)
        first_revision = outcomes["lock-first"][1]
        second_revision = outcomes["lock-second"][1]
        self.assertNotEqual(first_revision, second_revision)
        pointer = json.loads(self._pointer().read_text(encoding="utf-8"))
        self.assertEqual(pointer["revision_id"], second_revision)
        second_receipt = json.loads(
            (
                self.export_root
                / "private-receipts"
                / "atlas.fixture_snapshot"
                / "lock-second.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(second_receipt["prior_current_revision_id"], first_revision)
        lock_root = self.export_root / "private-receipts" / ".locks"
        lock_path = lock_root / "atlas.fixture_snapshot.lock"
        self.assertEqual(os.stat(lock_root).st_mode & 0o777, 0o700)
        self.assertTrue(stat.S_ISREG(os.stat(lock_path).st_mode))
        self.assertEqual(os.stat(lock_path).st_mode & 0o777, 0o600)


    def test_unsafe_roots_collisions_and_alternate_project_roots_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            validate_export_root("relative", self.registry)
        with self.assertRaises(ValidationError):
            validate_export_root(PROJECT_ROOT, self.registry)
        unmanaged = self.root / "unmanaged"
        unmanaged.mkdir()
        (unmanaged / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(ConflictError):
            validate_export_root(unmanaged, self.registry)
        target = self.root / "symlink-target"
        target.mkdir()
        link = self.root / "unsafe-link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValidationError):
            validate_export_root(link, self.registry)
        dangling_ancestor = self.root / "dangling-ancestor"
        dangling_ancestor.symlink_to(
            self.root / "missing-symlink-target",
            target_is_directory=True,
        )
        with self.assertRaises(ValidationError):
            validate_export_root(dangling_ancestor / "atlas-output", self.registry)
        alternate = self.root / "alternate-project"
        alternate.mkdir()
        with self.assertRaises(ValidationError):
            AtlasSnapshotExporter(
                self.registry,
                self.stores,
                alternate,
                self.root / "alternate-output",
                CUTOFF,
                lambda: "alternate",
                "stage8-alternate",
            )


if __name__ == "__main__":
    unittest.main()
