"""Focused offline proof for Stage 7 fixture preparation and publication.

The test deliberately uses the current canonical registry and synthetic fixture
manifest with explicit temporary four-store paths. It proves that all fixture
families can stage candidates before locks, and that macro plus options can
publish through one already-held multi-store capability without nested locks.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import quant_data.market.stage4_options as stage4_options_module
from quant_data.company.stage4_importer import CompanyStage4FixtureImporter
from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.macro.stage3_fixture_importers import MacroStage3FixtureImporter
from quant_data.market.daily_prices import DailyPriceImporter
from quant_data.market.stage4_options import Stage4OptionsFixtureImporter
from quant_data.migrations import initialize_all
from quant_data.news.stage4_importer import NewsStage4FixtureImporter
from quant_data.registry import load_registry
from quant_data.stores import (
    HeldWriteLocks,
    StoreMap,
    StoreRole,
    StoreWriteLock,
    acquire_write_session,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class Stage7FixturePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.store_map = _temporary_store_map(self.root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.store_map, self.registry)
        self.manifest = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self.importers = {
            "daily": (DailyPriceImporter(self.store_map, self.manifest), "market.base"),
            "macro": (MacroStage3FixtureImporter(self.store_map, self.manifest), "treasury_base"),
            "options": (
                Stage4OptionsFixtureImporter(self.store_map, self.manifest),
                "stage4.market.options.spy_complete",
            ),
            "company": (CompanyStage4FixtureImporter(self.store_map, self.manifest), "sec_initial"),
            "news": (NewsStage4FixtureImporter(self.store_map, self.manifest), "news_initial"),
        }
        self.assertTrue(str(self.root).startswith("/tmp/"))
        for role in StoreRole:
            self.assertEqual(self.store_map.path(role).parent, self.root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _lock_inventory(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob(".quant_data_locks/*")
                if path.is_file()
            )
        )

    def _prepare_all(self) -> dict[str, object]:
        return {
            name: importer.prepare_fixture(fixture_id)
            for name, (importer, fixture_id) in self.importers.items()
        }

    def test_prepare_is_store_lock_and_coordinator_free_and_candidates_are_bound(self) -> None:
        before = mutation_fingerprint(self.store_map)
        lock_before = self._lock_inventory()
        coordinator_targets = (
            "quant_data.market.daily_prices.IngestionCoordinator",
            "quant_data.market.stage4_options.IngestionCoordinator",
            "quant_data.macro.stage3_fixture_importers.IngestionCoordinator",
            "quant_data.company.stage4_importer.IngestionCoordinator",
            "quant_data.news.stage4_importer.IngestionCoordinator",
        )
        with ExitStack() as stack:
            for target in coordinator_targets:
                stack.enter_context(
                    patch(
                        target,
                        side_effect=AssertionError("preparation must not construct a coordinator"),
                    )
                )
            for target in (
                "quant_data.stores.StoreWriteLock",
                "quant_data.ingestion.StoreWriteLock",
            ):
                stack.enter_context(
                    patch(
                        target,
                        side_effect=AssertionError("preparation must not construct a write lock"),
                    )
                )
            candidates = self._prepare_all()

        self.assertEqual(before, mutation_fingerprint(self.store_map))
        self.assertEqual(lock_before, self._lock_inventory())

        for name, candidate in candidates.items():
            importer, _ = self.importers[name]
            forged = object.__new__(type(candidate))
            with self.subTest(candidate=f"{name}:forged"):
                with self.assertRaises(ValidationError):
                    importer.publish_prepared(forged)
            foreign_importer = type(importer)(self.store_map, self.manifest)
            with self.subTest(candidate=f"{name}:foreign-importer"):
                with self.assertRaises(ValidationError):
                    foreign_importer.publish_prepared(candidate)

        daily_candidate = candidates["daily"]
        macro_importer, _ = self.importers["macro"]
        with self.assertRaises(ValidationError):
            macro_importer.publish_prepared(daily_candidate)

        options_importer, _ = self.importers["options"]
        forged_locks = object.__new__(HeldWriteLocks)
        with patch(
            "quant_data.market.stage4_options._preflight_underlying",
            side_effect=AssertionError("invalid locks must fail before a store read"),
        ):
            with self.assertRaises(ConflictError):
                options_importer.publish_prepared(
                    candidates["options"],
                    held_locks=forged_locks,
                )

        self.assertEqual(before, mutation_fingerprint(self.store_map))
        self.assertEqual(lock_before, self._lock_inventory())

    def test_macro_and_options_publish_through_one_held_session_without_nested_locks(self) -> None:
        daily_importer, daily_fixture_id = self.importers["daily"]
        macro_importer, macro_fixture_id = self.importers["macro"]
        options_importer, options_fixture_id = self.importers["options"]

        # The option contract intentionally requires an existing reviewed market
        # identity. Seed it before the two candidates are prepared.
        self.assertEqual(
            daily_importer.import_fixture(daily_fixture_id).outcome,
            "succeeded",
        )

        constructed_paths: list[Path] = []
        real_lock = StoreWriteLock
        preflight_calls = 0
        actual_preflight = stage4_options_module._preflight_underlying

        def readonly_preflight(store_map: StoreMap, parsed: object) -> str:
            nonlocal preflight_calls
            before = mutation_fingerprint(self.store_map)
            result = actual_preflight(store_map, parsed)
            self.assertEqual(before, mutation_fingerprint(self.store_map))
            preflight_calls += 1
            return result

        class RecordingLock:
            def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
                self._inner = real_lock(path, timeout_seconds=timeout_seconds)
                constructed_paths.append(self._inner.path)

            def __enter__(self) -> "RecordingLock":
                self._inner.__enter__()
                return self

            def _acquire_until(self, deadline: float) -> "RecordingLock":
                self._inner._acquire_until(deadline)
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                self._inner.__exit__(exc_type, exc, traceback)

        with (
            patch("quant_data.stores.StoreWriteLock", RecordingLock),
            patch("quant_data.ingestion.StoreWriteLock", RecordingLock),
            patch("quant_data.market.stage4_options._preflight_underlying", readonly_preflight),
            patch("socket.create_connection", side_effect=AssertionError("fixture publication must not use a network socket")),
        ):
            macro_candidate = macro_importer.prepare_fixture(macro_fixture_id)
            options_candidate = options_importer.prepare_fixture(options_fixture_id)
            self.assertEqual(constructed_paths, [])

            with acquire_write_session(
                self.store_map,
                (StoreRole.MACRO, StoreRole.MARKET),
            ) as held_locks:
                self.assertTrue(held_locks.holds(StoreRole.MACRO))
                self.assertTrue(held_locks.holds(StoreRole.MARKET))
                self.assertEqual(
                    set(constructed_paths),
                    {
                        self.store_map.path(StoreRole.MACRO).resolve(strict=False),
                        self.store_map.path(StoreRole.MARKET).resolve(strict=False),
                    },
                )
                self.assertEqual(len(constructed_paths), 2)

                macro_receipt = macro_importer.publish_prepared(
                    macro_candidate,
                    held_locks=held_locks,
                )
                options_receipt = options_importer.publish_prepared(
                    options_candidate,
                    held_locks=held_locks,
                )
                self.assertEqual(macro_receipt.outcome, "succeeded")
                self.assertEqual(options_receipt.outcome, "succeeded")
                self.assertEqual(len(constructed_paths), 2)

                before_replay = mutation_fingerprint(self.store_map)
                macro_replay = macro_importer.publish_prepared(
                    macro_candidate,
                    held_locks=held_locks,
                )
                options_replay = options_importer.publish_prepared(
                    options_candidate,
                    held_locks=held_locks,
                )
                self.assertEqual(macro_replay.outcome, "unchanged")
                self.assertEqual(options_replay.outcome, "unchanged")
                self.assertEqual(macro_replay.written_count, 0)
                self.assertEqual(options_replay.written_count, 0)
                self.assertEqual(before_replay, mutation_fingerprint(self.store_map))
                self.assertEqual(preflight_calls, 2)
                self.assertEqual(len(constructed_paths), 2)

    def test_legacy_import_fixture_delegates_for_every_fixture_family(self) -> None:
        for name in ("daily", "macro", "options", "company", "news"):
            importer, fixture_id = self.importers[name]
            with self.subTest(family=name):
                receipt = importer.import_fixture(fixture_id)
                self.assertEqual(receipt.outcome, "succeeded")
                self.assertGreater(receipt.written_count, 0)


if __name__ == "__main__":
    unittest.main()
