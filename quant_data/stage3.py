"""Deterministic offline acceptance harness for Stage 3 market/macro restoration.

The harness upgrades a freshly verified Stage 2 source cohort in place, imports
only reviewed synthetic fixtures, exercises point-in-time readers, and proves
online backup/restore equivalence.  Every root is explicit; no ambient database
configuration, credential, network provider, scheduler, or export is used.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .company import company_foundation_status
from .errors import ConflictError, ValidationError
from .fingerprint import logical_manifest, mutation_fingerprint
from .fixtures import FixtureManifest
from .json_codec import dumps_strict
from .macro import (
    MacroSeriesQuery,
    MacroSeriesRepository,
    MacroStage3FixtureImporter,
    MacroStage3Repository,
    MacroStage3SeriesQuery,
)
from .market import (
    DailyPriceQuery,
    DailyPriceRepository,
    MarketCatalogFixtureImporter,
    MarketCatalogRepository,
)
from .migrations import initialize_all
from .news import news_foundation_status
from .operations import all_store_health, backup_all, restore_all
from .registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from .stage1 import explicit_store_map
from .stage2 import run_clean_stage2_rebuild
from .stores import StoreMap, StoreRole, read_connection, stable_id


FIXTURE_MANIFEST_PATH = Path("tests/fixtures/manifest.json")

_MARKET_FIXTURE_IDS = (
    "stage3.market.catalog_initial",
    "stage3.market.universe_partial_omission",
    "stage3.market.universe_complete_removal",
    "stage3.market.universe_complete_restoration",
)
_MACRO_FIXTURE_IDS = (
    "gdp_advance",
    "gdp_second",
    "gdp_second_correction",
    "treasury_base",
    "treasury_correction",
    "economic_calendar",
    "soma_summary",
    "eia_retail_base",
    "eia_retail_partial",
    "eia_retail_omission",
    "eia_retail_restore",
    "eia_weekly_base",
    "eia_weekly_scope_change",
    "recession_periods",
    "bls_base",
    "bls_response_time_only",
    "bis_base",
    "chicagofed_base",
    "chicagofed_scope_change",
    "bea_base",
    "bea_utc_only",
)
_STAGE3_DATASET_IDS = (
    "fixture.market.catalog_evidence",
    "fixture.market.instrument_classifications",
    "fixture.market.controlled_universes",
    "fixture.macro.stage3_catalog",
    "fixture.macro.gdp_vintages",
    "fixture.macro.treasury_yield_curves",
    "fixture.macro.economic_calendar",
    "fixture.macro.soma_evidence",
    "fixture.macro.soma_summary",
    "fixture.macro.eia_retail_evidence",
    "fixture.macro.eia_retail",
    "fixture.macro.eia_weekly_evidence",
    "fixture.macro.eia_weekly",
    "fixture.macro.recession_periods",
)


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 3 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 3 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _fixture_evidence(manifest: FixtureManifest) -> dict[str, Any]:
    fixtures: list[dict[str, Any]] = []
    for fixture_id in (*_MARKET_FIXTURE_IDS, *_MACRO_FIXTURE_IDS):
        fixture = manifest.get(fixture_id)
        fixtures.append(
            {
                "id": fixture.id,
                "store": fixture.store,
                "collector_id": fixture.ingestion_family_id,
                "resource": fixture.resource_name,
                "sha256": fixture.sha256,
                "semantic_identity": fixture.expected_semantic_identity,
                "byte_count": fixture.byte_count,
            }
        )
    result: dict[str, Any] = {"fixtures": fixtures}
    result["sha256"] = hashlib.sha256(
        dumps_strict(result).encode("utf-8")
    ).hexdigest()
    return result


def _assert_unchanged(
    store_map: StoreMap,
    before: dict[str, Any],
    message: str,
) -> None:
    if before["sha256"] != mutation_fingerprint(store_map)["sha256"]:
        raise ValidationError(message)


def _import_fixtures(
    store_map: StoreMap,
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], dict[str, bool]]:
    market = MarketCatalogFixtureImporter(store_map, manifest)
    macro = MacroStage3FixtureImporter(store_map, manifest)
    receipts: dict[str, Any] = {}

    for fixture_id in _MARKET_FIXTURE_IDS:
        receipt = market.import_fixture(fixture_id)
        if receipt.outcome != "succeeded":
            raise ValidationError("Stage 3 market publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    normal_macro_ids = (
        "gdp_advance",
        "gdp_second",
        "gdp_second_correction",
        "treasury_base",
        "treasury_correction",
        "economic_calendar",
        "soma_summary",
        "eia_retail_base",
    )
    for fixture_id in normal_macro_ids:
        receipt = macro.import_fixture(fixture_id)
        if receipt.outcome != "succeeded":
            raise ValidationError("Stage 3 macro publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    before_partial = mutation_fingerprint(store_map)
    try:
        macro.import_fixture("eia_retail_partial")
    except ValidationError:
        pass
    else:  # pragma: no cover - explicit fail-closed gate
        raise ValidationError("Incomplete EIA retail fixture was accepted")
    _assert_unchanged(
        store_map,
        before_partial,
        "Rejected EIA retail capture changed a store",
    )

    for fixture_id in (
        "eia_retail_omission",
        "eia_retail_restore",
        "eia_weekly_base",
        "eia_weekly_scope_change",
        "recession_periods",
        "bls_base",
    ):
        receipt = macro.import_fixture(fixture_id)
        if receipt.outcome != "succeeded":
            raise ValidationError("Stage 3 macro publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    before_bls = mutation_fingerprint(store_map)
    bls_volatile = macro.import_fixture("bls_response_time_only")
    if bls_volatile.outcome != "unchanged" or bls_volatile.written_count != 0:
        raise ValidationError("BLS volatile-only replay was not unchanged")
    _assert_unchanged(store_map, before_bls, "BLS volatile-only replay wrote data")
    receipts["bls_response_time_only"] = bls_volatile.to_primitive()

    for fixture_id in ("bis_base", "chicagofed_base", "chicagofed_scope_change", "bea_base"):
        receipt = macro.import_fixture(fixture_id)
        if receipt.outcome != "succeeded":
            raise ValidationError("Stage 3 macro publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    before_bea = mutation_fingerprint(store_map)
    bea_volatile = macro.import_fixture("bea_utc_only")
    if bea_volatile.outcome != "unchanged" or bea_volatile.written_count != 0:
        raise ValidationError("BEA volatile-only replay was not unchanged")
    _assert_unchanged(store_map, before_bea, "BEA volatile-only replay wrote data")
    receipts["bea_utc_only"] = bea_volatile.to_primitive()

    before_replay = mutation_fingerprint(store_map)
    market_replay = market.import_fixture("stage3.market.catalog_initial")
    macro_replay = macro.import_fixture("gdp_advance")
    if (
        market_replay.outcome != "unchanged"
        or macro_replay.outcome != "unchanged"
        or market_replay.written_count != 0
        or macro_replay.written_count != 0
    ):
        raise ValidationError("Exact Stage 3 replay was not a total no-op")
    _assert_unchanged(store_map, before_replay, "Exact Stage 3 replay changed a store")
    receipts["exact_replay"] = {
        "market": market_replay.to_primitive(),
        "macro": macro_replay.to_primitive(),
    }

    return receipts, {
        "exact_replay_unchanged": True,
        "incomplete_capture_unchanged": True,
        "bls_volatile_metadata_unchanged": True,
        "bea_volatile_metadata_unchanged": True,
    }


def _macro_query(
    series_id: str,
    mode: str,
    *,
    as_of: str | None = None,
) -> MacroStage3SeriesQuery:
    return MacroStage3SeriesQuery(
        series_id=series_id,
        start_date="2026-01-01",
        end_date="2026-12-31",
        vintage_mode=mode,
        as_of=as_of,
        date_only_policy="completed_date",
        limit=100,
    )


def _golden_query_snapshot(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    before = mutation_fingerprint(store_map)
    market = MarketCatalogRepository(store_map, registry)
    macro = MacroStage3Repository(store_map, registry)
    legacy_market = DailyPriceRepository(store_map, registry)
    legacy_macro = MacroSeriesRepository(store_map, registry)
    universe_id = stable_id(
        "market_universe",
        "fixture.market.instruments",
        "fixture.market.universe.liquid",
    )
    acme_id = stable_id(
        "instrument",
        "fixture.market.instruments",
        "fixture.market.catalog.binding.v1:fixture.market.instrument.acme",
    )
    result: dict[str, Any] = {
        "market_indexes": market.list_recovered_fmp_indexes(
            effective_date="2026-07-20"
        ),
        "market_universe_latest": market.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
        ),
        "market_universe_initial_as_of": market.get_universe_memberships(
            universe_id=universe_id,
            effective_date="2026-07-20",
            as_of="2026-07-21",
        ),
        "market_classification": market.get_classification(
            instrument_id=acme_id,
            classification_provider="fixture_fmp",
            effective_date="2026-07-01",
            as_of="2026-07-12T12:00:00-04:00",
        ),
        "macro_gdp_latest": macro.get_series(
            _macro_query("macro.gdp.real_qoq_saar_pct", "latest")
        ).to_primitive(),
        "macro_gdp_first_release": macro.get_series(
            _macro_query("macro.gdp.real_qoq_saar_pct", "first_release")
        ).to_primitive(),
        "macro_gdp_as_of": macro.get_series(
            _macro_query(
                "macro.gdp.real_qoq_saar_pct",
                "as_of",
                as_of="2026-05-29",
            )
        ).to_primitive(),
        "macro_treasury_latest": macro.get_series(
            _macro_query("macro.treasury.par_yield.2y", "latest")
        ).to_primitive(),
        "macro_treasury_as_of": macro.get_series(
            _macro_query(
                "macro.treasury.par_yield.2y",
                "as_of",
                as_of="2026-07-19T12:00:00-04:00",
            )
        ).to_primitive(),
        "macro_eia_retail_latest": macro.get_series(
            _macro_query("macro.eia.electricity.retail_sales", "latest")
        ).to_primitive(),
        "macro_eia_retail_as_of": macro.get_series(
            _macro_query(
                "macro.eia.electricity.retail_sales",
                "as_of",
                as_of="2026-06-24",
            )
        ).to_primitive(),
        "macro_eia_weekly": macro.get_series(
            _macro_query("macro.eia.weekly.petroleum_stock", "latest")
        ).to_primitive(),
        "macro_soma": macro.get_series(
            _macro_query("macro.soma.total.treasuries", "latest")
        ).to_primitive(),
        "recession_periods": list(macro.get_recession_periods()),
        "legacy_market": legacy_market.get_prices(
            DailyPriceQuery(
                start_date="2026-07-16",
                end_date="2026-07-17",
                provider="fixture_fmp",
                price_variant="raw",
                currency_segment="USD",
                mode="latest",
                as_of=None,
                date_only_policy="completed_date",
                limit=100,
                provider_symbol="SPY",
            )
        ),
        "legacy_macro": legacy_macro.get_series(
            MacroSeriesQuery(
                series_id="fixture:philadelphia_fed_rtdsm:EMPLOY",
                start_date="2026-01-01",
                end_date="2026-02-28",
                vintage_mode="latest",
                as_of=None,
                date_only_policy="completed_date",
                limit=100,
            )
        ).to_primitive(),
    }
    _assert_unchanged(store_map, before, "Stage 3 golden reads changed a store")
    result["sha256"] = hashlib.sha256(
        dumps_strict(result).encode("utf-8")
    ).hexdigest()
    return result


def _stage3_dataset_counts(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dataset_id in _STAGE3_DATASET_IDS:
        declaration = next(item for item in registry.datasets if item.id == dataset_id)
        role = StoreRole(declaration.store)
        with read_connection(store_map, role) as connection:
            result[dataset_id] = {
                relation: int(
                    connection.execute(
                        f'SELECT count(*) FROM "{relation}"'
                    ).fetchone()[0]
                )
                for relation in declaration.relations
            }
    return result


def run_clean_stage3_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, Any]:
    """Run the complete fixture-only Stage 3 gate under one empty work root."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    stage2_evidence = run_clean_stage2_rebuild(
        project_root=project,
        work_root=root / "stage2",
    )
    source_map = explicit_store_map(root / "stage2" / "source")
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    if registry.registry_version != "2.1.0":
        raise ValidationError("Stage 3 requires the reviewed registry revision")
    manifest = FixtureManifest.load(
        project / FIXTURE_MANIFEST_PATH,
        project_root=project,
    )
    fixture_evidence = _fixture_evidence(manifest)

    migration_heads = initialize_all(source_map, registry)
    after_upgrade = mutation_fingerprint(source_map)
    repeated_heads = initialize_all(source_map, registry)
    if repeated_heads != migration_heads:
        raise ValidationError("Repeated Stage 3 initialization changed migration heads")
    _assert_unchanged(
        source_map,
        after_upgrade,
        "Repeated Stage 3 initialization changed a store",
    )

    receipts, no_write = _import_fixtures(source_map, manifest)
    counts = _stage3_dataset_counts(source_map, registry)
    source_health = all_store_health(source_map, registry)
    company_status = company_foundation_status(source_map, registry)
    news_status = news_foundation_status(source_map, registry)
    golden_queries = _golden_query_snapshot(source_map, registry)
    source_manifest = logical_manifest(source_map, registry)

    before_backup = mutation_fingerprint(source_map)
    backup = backup_all(source_map, registry, target_root=root / "backup")
    _assert_unchanged(source_map, before_backup, "Stage 3 backup changed its source")
    restored = restore_all(backup, registry, target_root=root / "restored")

    backup_manifest = logical_manifest(backup.backup_store_map, registry)
    restored_manifest = logical_manifest(restored.restored_store_map, registry)
    if not (
        dumps_strict(source_manifest)
        == dumps_strict(backup_manifest)
        == dumps_strict(restored_manifest)
    ):
        raise ValidationError("Stage 3 backup or restore changed the logical manifest")

    restored_queries = _golden_query_snapshot(restored.restored_store_map, registry)
    if dumps_strict(restored_queries) != dumps_strict(golden_queries):
        raise ValidationError("Stage 3 restored golden queries changed")
    restored_health = all_store_health(restored.restored_store_map, registry)
    if dumps_strict(restored_health.to_primitive()) != dumps_strict(
        source_health.to_primitive()
    ):
        raise ValidationError("Stage 3 restored health evidence changed")

    evidence: dict[str, Any] = {
        "contract": "quant_data.stage3_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "stage2_evidence_sha256": stage2_evidence["sha256"],
        "stage3_fixture_evidence": fixture_evidence,
        "migration_heads": migration_heads,
        "dataset_counts": counts,
        "receipts": receipts,
        "no_write": no_write,
        "source_health": source_health.to_primitive(),
        "company_foundation": company_status,
        "news_foundation": news_status,
        "golden_query_sha256": golden_queries["sha256"],
        "logical_manifest_sha256": source_manifest["sha256"],
        "backup": backup.to_primitive(),
        "restore": restored.to_primitive(),
        "repeated_initialization_unchanged": True,
        "source_reads_unchanged": True,
        "source_backup_unchanged": True,
        "backup_restore_unchanged": True,
        "restored_queries_equal": True,
        "restored_health_equal": True,
    }
    evidence["sha256"] = hashlib.sha256(
        dumps_strict(evidence).encode("utf-8")
    ).hexdigest()
    return evidence


def compare_clean_stage3_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, Any]:
    """Run Stage 3 twice and require byte-equal path-free evidence."""

    first = run_clean_stage3_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage3_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two Stage 3 clean rebuilds produced different evidence")
    return first
