"""Offline-only Stage 7 fixture execution adapter.

This module binds the frozen Stage 7 collector identifiers to reviewed local
fixtures.  Preparation performs only fixture validation and parsing; the
opaque prepared value can be published only by the same executor instance and
only through the caller-provided complete write-lock session.
"""

from __future__ import annotations

import hashlib
import weakref
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from ..contracts import IngestionReceipt
from ..errors import ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..market.daily_prices import DailyPriceImporter
from ..market.stage4_options import Stage4OptionsFixtureImporter
from ..macro.stage3_fixture_importers import MacroStage3FixtureImporter
from ..company.stage4_importer import CompanyStage4FixtureImporter
from ..news.stage4_importer import NewsStage4FixtureImporter
from ..registry import JobStepDeclaration
from ..stores import HeldWriteLocks, StoreMap, StoreRole
from .job_runner import PreparedStep, StepPublication


@dataclass(frozen=True, slots=True)
class FixtureStepBinding:
    """One frozen collector-to-synthetic-fixture binding."""

    collector_id: str
    fixture_id: str
    write_stores: tuple[str, ...]


STAGE7_FIXTURE_STEP_BINDINGS: Mapping[str, FixtureStepBinding] = MappingProxyType(
    {
        "fixture.news.import": FixtureStepBinding(
            "fixture.news.import", "news_initial", ("news",)
        ),
        "fixture.company.sec_import": FixtureStepBinding(
            "fixture.company.sec_import", "sec_initial", ("company",)
        ),
        "fixture.macro.treasury_import": FixtureStepBinding(
            "fixture.macro.treasury_import", "treasury_base", ("macro",)
        ),
        "fixture.market.options_import": FixtureStepBinding(
            "fixture.market.options_import",
            "stage4.market.options.spy_complete",
            ("market",),
        ),
        "fixture.macro.calendar_import": FixtureStepBinding(
            "fixture.macro.calendar_import", "economic_calendar", ("macro",)
        ),
        "fixture.market.daily_price_import": FixtureStepBinding(
            "fixture.market.daily_price_import", "market.base", ("market",)
        ),
        "fixture.company.expectations_import": FixtureStepBinding(
            "fixture.company.expectations_import",
            "expectations_guidance",
            ("company",),
        ),
        "fixture.company.actions_import": FixtureStepBinding(
            "fixture.company.actions_import", "actions_and_shares", ("company",)
        ),
        "fixture.macro.gdp_import": FixtureStepBinding(
            "fixture.macro.gdp_import", "gdp_advance", ("macro",)
        ),
        "fixture.macro.eia_retail_import": FixtureStepBinding(
            "fixture.macro.eia_retail_import", "eia_retail_base", ("macro",)
        ),
        "fixture.macro.recession_import": FixtureStepBinding(
            "fixture.macro.recession_import", "recession_periods", ("macro",)
        ),
    }
)


class _FixtureImporter(Protocol):
    def prepare_fixture(self, fixture_id: str) -> object:
        """Prepare a bounded fixture without opening or writing a store."""

    def publish_prepared(
        self,
        prepared: object,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Publish an opaque prepared fixture through an optional lock session."""


class _ExecutorCandidate:
    """Opaque wrapper for one importer-owned prepared candidate."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("Executor candidates are created by prepare")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("Executor candidates cannot be subclassed")


@dataclass(frozen=True, slots=True)
class _ExecutorCandidateState:
    owner_token: object
    binding: FixtureStepBinding
    fixture: Fixture
    importer: _FixtureImporter
    importer_candidate: object


_EXECUTOR_CANDIDATE_STATES: weakref.WeakKeyDictionary[
    _ExecutorCandidate, _ExecutorCandidateState
] = weakref.WeakKeyDictionary()


def _new_executor_candidate(state: _ExecutorCandidateState) -> _ExecutorCandidate:
    candidate = object.__new__(_ExecutorCandidate)
    _EXECUTOR_CANDIDATE_STATES[candidate] = state
    return candidate


def _ingestion_run_digest(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValidationError("Succeeded fixture publication requires an ingestion run ID")
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()


class Stage7FixtureStepExecutor:
    """Run only the frozen synthetic fixtures behind Stage 7 job steps."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        if not isinstance(store_map, StoreMap):
            raise ValidationError("Stage 7 fixture executor requires an explicit store map")
        if not isinstance(fixture_manifest, FixtureManifest):
            raise ValidationError("Stage 7 fixture executor requires a fixture manifest")
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._owner_token = object()
        self._importers: Mapping[str, _FixtureImporter] = MappingProxyType(
            {
                "fixture.news.import": NewsStage4FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.company.sec_import": CompanyStage4FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.macro.treasury_import": MacroStage3FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.market.options_import": Stage4OptionsFixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.macro.calendar_import": MacroStage3FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.market.daily_price_import": DailyPriceImporter(
                    store_map, fixture_manifest
                ),
                "fixture.company.expectations_import": CompanyStage4FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.company.actions_import": CompanyStage4FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.macro.gdp_import": MacroStage3FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.macro.eia_retail_import": MacroStage3FixtureImporter(
                    store_map, fixture_manifest
                ),
                "fixture.macro.recession_import": MacroStage3FixtureImporter(
                    store_map, fixture_manifest
                ),
            }
        )
        self._fixtures: Mapping[str, Fixture] = MappingProxyType(
            {
                collector_id: fixture_manifest.get(binding.fixture_id)
                for collector_id, binding in STAGE7_FIXTURE_STEP_BINDINGS.items()
            }
        )
        self._validate_bindings()

    def _validate_bindings(self) -> None:
        if set(self._importers) != set(STAGE7_FIXTURE_STEP_BINDINGS):
            raise ValidationError("Stage 7 fixture importer bindings are incomplete")
        for collector_id, binding in STAGE7_FIXTURE_STEP_BINDINGS.items():
            fixture = self._fixtures[collector_id]
            if (
                binding.collector_id != collector_id
                or fixture.id != binding.fixture_id
                or fixture.ingestion_family_id != collector_id
                or fixture.store not in binding.write_stores
                or tuple(dict.fromkeys(binding.write_stores)) != binding.write_stores
                or not fixture.test_fixture
                or fixture.promotable
            ):
                raise ValidationError("Stage 7 fixture binding does not match its manifest")

    @staticmethod
    def _binding_for_step(step: JobStepDeclaration) -> FixtureStepBinding:
        if not isinstance(step, JobStepDeclaration):
            raise ValidationError("Stage 7 executor requires a typed job step")
        binding = STAGE7_FIXTURE_STEP_BINDINGS.get(step.collector_id)
        if (
            binding is None
            or step.id != step.collector_id
            or step.collector_id != binding.collector_id
            or tuple(step.write_stores) != binding.write_stores
            or step.network_mode != "fixture_only_no_network"
            or step.configuration_env
            or not step.if_new
            or step.identity_version != "collector_semantic_identity_v1"
        ):
            raise ValidationError("Job step is not a frozen Stage 7 fixture binding")
        return binding

    def prepare(self, step: JobStepDeclaration, attempt: int) -> PreparedStep:
        """Parse one reviewed fixture before a write session is acquired."""

        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValidationError("Stage 7 preparation attempt must be a positive integer")
        binding = self._binding_for_step(step)
        fixture = self._fixtures[binding.collector_id]
        importer = self._importers[binding.collector_id]
        importer_candidate = importer.prepare_fixture(binding.fixture_id)
        if importer_candidate is None:
            raise ValidationError("Fixture importer returned an empty prepared candidate")
        candidate = _new_executor_candidate(
            _ExecutorCandidateState(
                owner_token=self._owner_token,
                binding=binding,
                fixture=fixture,
                importer=importer,
                importer_candidate=importer_candidate,
            )
        )
        return PreparedStep(
            semantic_identity=fixture.expected_semantic_identity,
            payload=candidate,
        )

    def _candidate_state(
        self,
        step: JobStepDeclaration,
        candidate: PreparedStep,
    ) -> _ExecutorCandidateState:
        binding = self._binding_for_step(step)
        if not isinstance(candidate, PreparedStep):
            raise ValidationError("Stage 7 fixture publication requires a prepared step")
        payload = candidate.payload
        if not isinstance(payload, _ExecutorCandidate):
            raise ValidationError("Prepared fixture candidate is not owned by this executor")
        state = _EXECUTOR_CANDIDATE_STATES.get(payload)
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.binding != binding
            or state.fixture is not self._fixtures[binding.collector_id]
            or state.importer is not self._importers[binding.collector_id]
            or candidate.semantic_identity != state.fixture.expected_semantic_identity
        ):
            raise ValidationError("Prepared fixture candidate is foreign or mismatched")
        return state

    @staticmethod
    def _require_held_stores(
        held_locks: HeldWriteLocks,
        binding: FixtureStepBinding,
    ) -> None:
        if not isinstance(held_locks, HeldWriteLocks) or not held_locks.active:
            raise ValidationError("Fixture publication requires an active held write session")
        if not all(held_locks.holds(StoreRole(role)) for role in binding.write_stores):
            raise ValidationError("Held write session does not cover the fixture write store")

    @staticmethod
    def _publication_from_receipt(
        receipt: IngestionReceipt,
        state: _ExecutorCandidateState,
    ) -> StepPublication:
        if not isinstance(receipt, IngestionReceipt):
            raise ValidationError("Fixture importer returned an invalid ingestion receipt")
        expected_store = state.binding.write_stores
        if (
            receipt.store not in expected_store
            or receipt.dataset_id != state.fixture.canonical_dataset_id
            or receipt.semantic_identity != state.fixture.expected_semantic_identity
        ):
            raise ValidationError("Fixture ingestion receipt does not match the prepared candidate")
        warnings = tuple(receipt.warnings)
        if receipt.outcome == "unchanged":
            if (
                receipt.run_id is not None
                or receipt.written_count != 0
                or receipt.artifact_id is not None
                or receipt.snapshot_id is not None
            ):
                raise ValidationError("Unchanged fixture receipt reported a persistent write")
            return StepPublication("unchanged", warnings=warnings)
        if receipt.outcome == "succeeded":
            if receipt.written_count < 1:
                raise ValidationError("Succeeded fixture receipt must report a persistent write")
            return StepPublication(
                "succeeded",
                committed_stores=(receipt.store,),
                ingestion_run_ids=(_ingestion_run_digest(receipt.run_id or ""),),
                warnings=warnings,
            )
        if receipt.outcome == "partial":
            stores = (receipt.store,) if receipt.run_id is not None else ()
            run_ids = (
                (_ingestion_run_digest(receipt.run_id),)
                if receipt.run_id is not None
                else ()
            )
            return StepPublication(
                "partial",
                committed_stores=stores,
                ingestion_run_ids=run_ids,
                warnings=warnings,
            )
        raise ValidationError("Fixture ingestion receipt has an unsupported outcome")

    def publish(
        self,
        step: JobStepDeclaration,
        candidate: PreparedStep,
        held_locks: HeldWriteLocks,
    ) -> StepPublication:
        """Publish one same-executor candidate through the supplied session."""

        state = self._candidate_state(step, candidate)
        self._require_held_stores(held_locks, state.binding)
        receipt = state.importer.publish_prepared(
            state.importer_candidate,
            held_locks=held_locks,
        )
        return self._publication_from_receipt(receipt, state)


__all__ = (
    "FixtureStepBinding",
    "STAGE7_FIXTURE_STEP_BINDINGS",
    "Stage7FixtureStepExecutor",
)
