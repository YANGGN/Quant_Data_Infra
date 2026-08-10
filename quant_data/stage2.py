"""Deterministic offline acceptance harness for the Stage 2 foundation.

The harness requires one explicit, empty work root.  It creates separate
source, backup, and restored four-store cohorts below that root, runs only the
reviewed fixtures, and emits path-free evidence.  It never consults ambient
database configuration or a live provider.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .boundary import ToolDispatcher
from .company import company_foundation_status
from .composition import (
    BoundedCrossStoreComposer,
    CompositionContext,
    read_ingestion_run_component,
)
from .errors import ConflictError, ValidationError
from .fingerprint import logical_manifest, mutation_fingerprint
from .json_codec import dumps_strict
from .macro import MacroSeriesQuery, MacroSeriesRepository
from .market import DailyPriceQuery, DailyPriceRepository
from .news import news_foundation_status
from .operations import all_store_health, backup_all, restore_all
from .registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage2_registry_profile,
)
from .stage1 import explicit_store_map, run_clean_rebuild as run_clean_stage1_rebuild
from .stores import STORE_ROLES, StoreMap, StoreRole, read_connection


_MACRO_SERIES_ID = "fixture:philadelphia_fed_rtdsm:EMPLOY"
_CONTROL_COUNT_RELATIONS = (
    "store_metadata",
    "schema_migrations",
    "dataset_registry",
    "dataset_identity_contracts",
    "ingestion_runs",
    "ingestion_run_outputs",
    "ingestion_run_failures",
    "ingestion_artifacts",
    "ingestion_snapshots",
    "ingestion_snapshot_artifacts",
    "data_quality_results",
)
_EXPECTED_CONTROL_COUNTS = {
    "market": {
        "store_metadata": 1,
        "schema_migrations": 3,
        "dataset_registry": 3,
        "dataset_identity_contracts": 3,
        "ingestion_runs": 2,
        "ingestion_run_outputs": 6,
        "ingestion_run_failures": 0,
        "ingestion_artifacts": 2,
        "ingestion_snapshots": 2,
        "ingestion_snapshot_artifacts": 2,
        "data_quality_results": 2,
    },
    "macro": {
        "store_metadata": 1,
        "schema_migrations": 3,
        "dataset_registry": 2,
        "dataset_identity_contracts": 2,
        "ingestion_runs": 2,
        "ingestion_run_outputs": 4,
        "ingestion_run_failures": 0,
        "ingestion_artifacts": 2,
        "ingestion_snapshots": 2,
        "ingestion_snapshot_artifacts": 2,
        "data_quality_results": 2,
    },
    "company": {
        "store_metadata": 1,
        "schema_migrations": 2,
        "dataset_registry": 0,
        "dataset_identity_contracts": 0,
        "ingestion_runs": 0,
        "ingestion_run_outputs": 0,
        "ingestion_run_failures": 0,
        "ingestion_artifacts": 0,
        "ingestion_snapshots": 0,
        "ingestion_snapshot_artifacts": 0,
        "data_quality_results": 0,
    },
    "news": {
        "store_metadata": 1,
        "schema_migrations": 2,
        "dataset_registry": 0,
        "dataset_identity_contracts": 0,
        "ingestion_runs": 0,
        "ingestion_run_outputs": 0,
        "ingestion_run_failures": 0,
        "ingestion_artifacts": 0,
        "ingestion_snapshots": 0,
        "ingestion_snapshot_artifacts": 0,
        "data_quality_results": 0,
    },
}


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 2 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 2 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _control_plane_counts(store_map: StoreMap) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for role in STORE_ROLES:
        with read_connection(store_map, role) as connection:
            result[role.value] = {
                relation: int(
                    connection.execute(
                        f'SELECT count(*) FROM "{relation}"'
                    ).fetchone()[0]
                )
                for relation in _CONTROL_COUNT_RELATIONS
            }
    if result != _EXPECTED_CONTROL_COUNTS:
        raise ValidationError("Stage 2 shared control-plane counts drifted")
    return result


def _market_query(*, mode: str, as_of: str | None = None) -> DailyPriceQuery:
    return DailyPriceQuery(
        start_date="2026-07-16",
        end_date="2026-07-17",
        provider="fixture_fmp",
        price_variant="raw",
        currency_segment="USD",
        mode=mode,
        as_of=as_of,
        date_only_policy="completed_date",
        limit=100,
        provider_symbol="SPY",
    )


def _macro_query(vintage_mode: str, *, as_of: str | None = None) -> MacroSeriesQuery:
    return MacroSeriesQuery(
        series_id=_MACRO_SERIES_ID,
        start_date="2026-01-01",
        end_date="2026-02-28",
        vintage_mode=vintage_mode,
        as_of=as_of,
        date_only_policy="completed_date",
        limit=100,
    )


def _golden_query_snapshot(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    """Run the fixed Stage 1 read matrix against a Stage 2 store cohort."""

    before = mutation_fingerprint(store_map)
    market = DailyPriceRepository(store_map, registry)
    macro = MacroSeriesRepository(store_map, registry)
    dispatcher = ToolDispatcher(store_map, registry)
    tool_arguments = {
        "series_id": _MACRO_SERIES_ID,
        "start_date": "2026-01-01",
        "end_date": "2026-02-28",
        "vintage_mode": "first_release",
        "as_of": None,
        "date_only_policy": "completed_date",
        "limit": 100,
    }
    tool_series = dispatcher.call("macro.get_series", tool_arguments)
    payload: dict[str, Any] = {
        "market_latest": market.get_prices(_market_query(mode="latest")),
        "market_as_of": market.get_prices(
            _market_query(mode="as_of", as_of="2026-07-19")
        ),
        "macro_latest": macro.get_series(_macro_query("latest")).to_primitive(),
        "macro_first_release": macro.get_series(
            _macro_query("first_release")
        ).to_primitive(),
        "macro_as_of": macro.get_series(
            _macro_query("as_of", as_of="2026-07-09")
        ).to_primitive(),
        "tool_series": tool_series,
        "tool_description": dispatcher.call(
            "timeseries.describe", {"series": tool_series}
        ),
    }
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 2 golden reads mutated an operational store")
    payload["sha256"] = hashlib.sha256(
        dumps_strict(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _composition_snapshot(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    context = CompositionContext(
        cutoff="2026-07-21T00:00:00-04:00",
        date_only_policy="completed_date",
        component_limit=100,
        joined_limit=100,
    )
    before = mutation_fingerprint(store_map)
    components = (
        read_ingestion_run_component(
            store_map,
            registry,
            dataset_id="fixture.market.daily_prices",
            context=context,
        ),
        read_ingestion_run_component(
            store_map,
            registry,
            dataset_id="fixture.macro.rtdsm_employ",
            context=context,
        ),
    )
    result = BoundedCrossStoreComposer(registry).compose(
        context=context,
        components=components,
    )
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 2 composition mutated an operational store")
    return result


def run_clean_stage2_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, Any]:
    """Run the complete Stage 2 gate under one explicit empty work root."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    source_root = root / "source"
    stage1_evidence = run_clean_stage1_rebuild(
        project_root=project,
        store_root=source_root,
    )
    registry = stage2_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    source_map = explicit_store_map(source_root)

    source_mutation_before = mutation_fingerprint(source_map)
    source_health = all_store_health(source_map, registry)
    counts = _control_plane_counts(source_map)
    company_status = company_foundation_status(source_map, registry)
    news_status = news_foundation_status(source_map, registry)
    golden_queries = _golden_query_snapshot(source_map, registry)
    composition = _composition_snapshot(source_map, registry)
    source_mutation_after_reads = mutation_fingerprint(source_map)
    if source_mutation_before["sha256"] != source_mutation_after_reads["sha256"]:
        raise ValidationError("Stage 2 read acceptance mutated a source store")

    backup = backup_all(
        source_map,
        registry,
        target_root=root / "backup",
    )
    restored = restore_all(
        backup,
        registry,
        target_root=root / "restored",
    )

    source_manifest = logical_manifest(source_map, registry)
    backup_manifest = logical_manifest(backup.backup_store_map, registry)
    restored_manifest = logical_manifest(restored.restored_store_map, registry)
    if not (
        dumps_strict(source_manifest)
        == dumps_strict(backup_manifest)
        == dumps_strict(restored_manifest)
    ):
        raise ValidationError("Stage 2 backup or restore changed the logical manifest")

    restored_queries = _golden_query_snapshot(restored.restored_store_map, registry)
    restored_composition = _composition_snapshot(restored.restored_store_map, registry)
    restored_company = company_foundation_status(restored.restored_store_map, registry)
    restored_news = news_foundation_status(restored.restored_store_map, registry)
    if dumps_strict(restored_queries) != dumps_strict(golden_queries):
        raise ValidationError("Stage 2 restored golden queries changed")
    if dumps_strict(restored_composition) != dumps_strict(composition):
        raise ValidationError("Stage 2 restored composition changed")
    if restored_company != company_status or restored_news != news_status:
        raise ValidationError("Stage 2 restored empty-domain foundation changed")

    evidence: dict[str, Any] = {
        "contract": "quant_data.stage2_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "stage1_fixture_evidence_sha256": stage1_evidence["sha256"],
        "stage1_golden_results_sha256": stage1_evidence["golden_results_sha256"],
        "migration_heads": stage1_evidence["migration_heads"],
        "control_plane_counts": counts,
        "source_health": source_health.to_primitive(),
        "company_foundation": company_status,
        "news_foundation": news_status,
        "golden_query_sha256": golden_queries["sha256"],
        "composition": composition,
        "backup": backup.to_primitive(),
        "restore": restored.to_primitive(),
        "logical_manifest_sha256": source_manifest["sha256"],
        "source_reads_unchanged": True,
        "source_backup_unchanged": (
            backup.source_mutation_before_sha256
            == backup.source_mutation_after_sha256
        ),
        "backup_restore_unchanged": (
            restored.backup_mutation_before_sha256
            == restored.backup_mutation_after_sha256
        ),
        "restored_queries_equal": True,
        "restored_composition_equal": True,
    }
    evidence["sha256"] = hashlib.sha256(
        dumps_strict(evidence).encode("utf-8")
    ).hexdigest()
    return evidence


def compare_clean_stage2_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, Any]:
    """Run the Stage 2 gate twice and require byte-equal path-free evidence."""

    first = run_clean_stage2_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage2_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two Stage 2 clean rebuilds produced different evidence")
    return first
