"""Offline acceptance checks for the frozen Stage 7 fixture executor."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.migrations import initialize_all
from quant_data.operations.fixture_executor import (
    STAGE7_FIXTURE_STEP_BINDINGS,
    Stage7FixtureStepExecutor,
)
from quant_data.operations.job_runner import PreparedStep
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stage6 import run_clean_stage6_rebuild
from quant_data.stores import StoreMap, StoreRole, StoreWriteLock, acquire_write_session


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MANIFEST_PATH = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"

_EXPECTED_JOB_BINDINGS = {
    "news-hourly": (
        ("fixture.news.import", "news_initial", (), ("news",)),
    ),
    "sec-daily": (
        ("fixture.company.sec_import", "sec_initial", (), ("company",)),
    ),
    "options-close": (
        ("fixture.macro.treasury_import", "treasury_base", (), ("macro",)),
        (
            "fixture.market.options_import",
            "stage4.market.options.spy_complete",
            ("fixture.macro.treasury_import",),
            ("market",),
        ),
    ),
    "macro-daily": (
        ("fixture.macro.calendar_import", "economic_calendar", (), ("macro",)),
    ),
    "market-close": (
        ("fixture.market.daily_price_import", "market.base", (), ("market",)),
    ),
    "expectations": (
        (
            "fixture.company.expectations_import",
            "expectations_guidance",
            (),
            ("company",),
        ),
    ),
    "company-weekly": (
        (
            "fixture.company.actions_import",
            "actions_and_shares",
            (),
            ("company",),
        ),
    ),
    "macro-monthly": (
        ("fixture.macro.gdp_import", "gdp_advance", (), ("macro",)),
        ("fixture.macro.eia_retail_import", "eia_retail_base", (), ("macro",)),
        (
            "fixture.macro.recession_import",
            "recession_periods",
            (),
            ("macro",),
        ),
    ),
}


def _store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class Stage7FixtureStepExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.manifest = FixtureManifest.load(
            FIXTURE_MANIFEST_PATH,
            project_root=PROJECT_ROOT,
        )
        self.store_map = _store_map(self.root / "stores")
        initialize_all(self.store_map, self.registry)
        self.executor = Stage7FixtureStepExecutor(self.store_map, self.manifest)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _lock_inventory(self, root: Path | None = None) -> tuple[str, ...]:
        target = root or self.root
        return tuple(
            sorted(
                path.relative_to(target).as_posix()
                for path in target.rglob(".quant_data_locks/*")
                if path.is_file()
            )
        )

    def _publish_job(
        self,
        executor: Stage7FixtureStepExecutor,
        store_map: StoreMap,
        job_id: str,
    ) -> tuple[object, ...]:
        job = self.registry.job(job_id)
        candidates = tuple(executor.prepare(step, 1) for step in job.steps)
        with acquire_write_session(store_map, job.required_stores) as held_locks:
            return tuple(
                executor.publish(step, candidate, held_locks)
                for step, candidate in zip(job.steps, candidates, strict=True)
            )

    def test_frozen_collector_mapping_and_prepare_are_exact_and_side_effect_free(self) -> None:
        expected_collectors = tuple(
            item[0] for values in _EXPECTED_JOB_BINDINGS.values() for item in values
        )
        self.assertEqual(tuple(STAGE7_FIXTURE_STEP_BINDINGS), expected_collectors)

        before = mutation_fingerprint(self.store_map)
        locks_before = self._lock_inventory()
        prepared: dict[str, PreparedStep] = {}
        with ExitStack() as stack:
            for target in (
                "quant_data.ingestion.IngestionCoordinator.execute",
                "quant_data.ingestion.read_connection",
                "quant_data.ingestion.writer_connection",
                "quant_data.stores.StoreWriteLock",
                "socket.create_connection",
            ):
                stack.enter_context(
                    patch(target, side_effect=AssertionError("preparation must be dry"))
                )
            for job_id, expected in _EXPECTED_JOB_BINDINGS.items():
                job = self.registry.job(job_id)
                observed = []
                for step in job.steps:
                    binding = STAGE7_FIXTURE_STEP_BINDINGS[step.collector_id]
                    candidate = self.executor.prepare(step, 1)
                    prepared[step.id] = candidate
                    observed.append(
                        (
                            step.collector_id,
                            binding.fixture_id,
                            step.depends_on,
                            step.write_stores,
                        )
                    )
                    self.assertEqual(
                        candidate.semantic_identity,
                        self.manifest.get(binding.fixture_id).expected_semantic_identity,
                    )
                with self.subTest(job=job_id):
                    self.assertEqual(tuple(observed), expected)

        self.assertEqual(before, mutation_fingerprint(self.store_map))
        self.assertEqual(locks_before, self._lock_inventory())
        self.assertEqual(set(prepared), set(expected_collectors))

    def test_publish_uses_the_supplied_complete_session_without_a_nested_lock(self) -> None:
        daily_step = self.registry.job("market-close").steps[0]
        daily_candidate = self.executor.prepare(daily_step, 1)
        with acquire_write_session(self.store_map, (StoreRole.MARKET,)) as held_locks:
            daily = self.executor.publish(daily_step, daily_candidate, held_locks)
        self.assertEqual(daily.outcome, "succeeded")

        options_job = self.registry.job("options-close")
        treasury_step, options_step = options_job.steps
        treasury_candidate = self.executor.prepare(treasury_step, 1)
        options_candidate = self.executor.prepare(options_step, 1)
        constructed: list[Path] = []
        real_lock = StoreWriteLock

        class RecordingLock:
            def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
                self._inner = real_lock(path, timeout_seconds=timeout_seconds)
                constructed.append(self._inner.path)

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
            patch(
                "quant_data.ingestion.StoreWriteLock",
                side_effect=AssertionError("publication must not acquire a nested lock"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("offline fixture publication must not use network"),
            ),
        ):
            with acquire_write_session(
                self.store_map,
                options_job.required_stores,
            ) as held_locks:
                self.assertTrue(held_locks.holds(StoreRole.MACRO))
                self.assertTrue(held_locks.holds(StoreRole.MARKET))
                self.assertEqual(len(constructed), 2)
                treasury = self.executor.publish(
                    treasury_step,
                    treasury_candidate,
                    held_locks,
                )
                options = self.executor.publish(
                    options_step,
                    options_candidate,
                    held_locks,
                )
                self.assertEqual(len(constructed), 2)

        self.assertEqual(treasury.outcome, "succeeded")
        self.assertEqual(options.outcome, "succeeded")
        self.assertEqual(treasury.committed_stores, ("macro",))
        self.assertEqual(options.committed_stores, ("market",))
        self.assertTrue(all(len(value) == 64 for value in options.ingestion_run_ids))

    def test_stage6_source_replay_is_unchanged_and_mutation_neutral(self) -> None:
        stage6_root = self.root / "stage6"
        run_clean_stage6_rebuild(project_root=PROJECT_ROOT, work_root=stage6_root)
        source_root = stage6_root / "stage5" / "stage4" / "stage3" / "stage2" / "source"
        source_map = _store_map(source_root)
        source_executor = Stage7FixtureStepExecutor(source_map, self.manifest)
        before = mutation_fingerprint(source_map)

        outcomes = []
        with patch(
            "socket.create_connection",
            side_effect=AssertionError("Stage 7 replay must remain offline"),
        ):
            for job in self.registry.jobs:
                outcomes.extend(self._publish_job(source_executor, source_map, job.id))

        self.assertTrue(outcomes)
        self.assertTrue(all(item.outcome == "unchanged" for item in outcomes))
        self.assertTrue(all(not item.committed_stores for item in outcomes))
        self.assertTrue(all(not item.ingestion_run_ids for item in outcomes))
        self.assertEqual(before, mutation_fingerprint(source_map))

    def test_unknown_or_foreign_prepared_candidates_fail_closed(self) -> None:
        step = self.registry.job("market-close").steps[0]
        candidate = self.executor.prepare(step, 1)
        foreign = Stage7FixtureStepExecutor(self.store_map, self.manifest)
        unknown = replace(
            step,
            id="fixture.unknown.import",
            collector_id="fixture.unknown.import",
        )
        self.assertRaises(ValidationError, self.executor.prepare, unknown, 1)
        self.assertRaises(
            ValidationError,
            self.executor.prepare,
            replace(step, write_stores=("macro",)),
            1,
        )

        with acquire_write_session(self.store_map, (StoreRole.MARKET,)) as held_locks:
            with self.assertRaises(ValidationError):
                foreign.publish(step, candidate, held_locks)
            with self.assertRaises(ValidationError):
                self.executor.publish(
                    step,
                    PreparedStep(candidate.semantic_identity, object()),
                    held_locks,
                )
            with self.assertRaises(ValidationError):
                self.executor.publish(
                    self.registry.job("macro-daily").steps[0],
                    candidate,
                    held_locks,
                )


if __name__ == "__main__":
    unittest.main()
