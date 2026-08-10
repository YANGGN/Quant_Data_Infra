"""Deterministic offline acceptance harness for Stage 4 domain restoration.

The harness upgrades a freshly verified Stage 3 source cohort in place, imports
only reviewed synthetic company, news, and options fixtures, exercises bounded
point-in-time readers, and proves online backup/restore equivalence. Every root
is explicit; no ambient database configuration, credential, network provider,
scheduler, export, promotion, or destructive operation is used.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .company.stage4_importer import CompanyStage4FixtureImporter
from .company.stage4_repository import CompanyStage4Query, CompanyStage4Repository
from .errors import ConflictError, ValidationError
from .fingerprint import logical_manifest, mutation_fingerprint
from .fixtures import FixtureManifest
from .json_codec import dumps_strict
from .market.stage4_options import (
    OptionsStage4Query,
    OptionsStage4Repository,
    Stage4OptionsFixtureImporter,
)
from .migrations import initialize_all
from .news.stage4_importer import NewsStage4FixtureImporter
from .news.stage4_repository import NewsStage4Query, NewsStage4Repository
from .operations import all_store_health, backup_all, restore_all
from .registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage4_registry_profile,
)
from .stage1 import explicit_store_map
from .stage3 import run_clean_stage3_rebuild
from .stores import StoreMap, StoreRole, read_connection, stable_id


FIXTURE_MANIFEST_PATH = Path("tests/fixtures/manifest.json")

_COMPANY_FIXTURE_IDS = (
    "sec_initial",
    "ticker_change",
    "joint_filing",
    "companyfacts_correction",
    "actions_and_shares",
    "expectations_guidance",
    "expectations_guidance_correction",
)
_OPTION_SUCCESS_FIXTURE_IDS = (
    "stage4.market.options.spy_complete",
    "stage4.market.options.nonstandard_exclusion",
)
_OPTION_REJECTION_FIXTURE_IDS = (
    "stage4.market.options.mixed_feed_environment",
    "stage4.market.options.unknown_contract",
    "stage4.market.options.incoherent_inputs",
)
_NEWS_FIXTURE_IDS = (
    "news_initial",
    "news_content_correction",
    "news_later_capture",
    "news_retraction",
    "news_missing_content",
)
_STAGE4_FIXTURE_IDS = (
    *_COMPANY_FIXTURE_IDS,
    *_OPTION_SUCCESS_FIXTURE_IDS,
    *_OPTION_REJECTION_FIXTURE_IDS,
    *_NEWS_FIXTURE_IDS,
)
_STAGE4_DATASET_IDS = (
    "fixture.market.option_capture_evidence",
    "fixture.market.options",
    "fixture.company.sec_evidence",
    "fixture.company.issuers",
    "fixture.company.filings",
    "fixture.company.fundamentals",
    "fixture.company.action_evidence",
    "fixture.company.corporate_actions",
    "fixture.company.expectation_evidence",
    "fixture.company.expectations",
    "fixture.company.filing_issuer_membership",
    "fixture.news.evidence",
    "fixture.news.items",
    "fixture.news.search_index",
)


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 4 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 4 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _fixture_evidence(manifest: FixtureManifest) -> dict[str, Any]:
    fixtures: list[dict[str, Any]] = []
    for fixture_id in _STAGE4_FIXTURE_IDS:
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
                "expected_outcome": fixture.metadata.get("expected_outcome", "succeeded"),
                "expected_error_rule": fixture.metadata.get("expected_error_rule"),
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


def _require_success(receipt: Any, message: str) -> None:
    if receipt.outcome != "succeeded":
        raise ValidationError(message)


def _import_fixtures(
    store_map: StoreMap,
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], dict[str, bool]]:
    company = CompanyStage4FixtureImporter(store_map, manifest)
    options = Stage4OptionsFixtureImporter(store_map, manifest)
    news = NewsStage4FixtureImporter(store_map, manifest)
    receipts: dict[str, Any] = {}

    for fixture_id in _COMPANY_FIXTURE_IDS:
        receipt = company.import_fixture(fixture_id)
        _require_success(receipt, "Stage 4 company publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    for fixture_id in _OPTION_SUCCESS_FIXTURE_IDS:
        receipt = options.import_fixture(fixture_id)
        _require_success(receipt, "Stage 4 options publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    rejected: dict[str, str] = {}
    for fixture_id in _OPTION_REJECTION_FIXTURE_IDS:
        fixture = manifest.get(fixture_id)
        before = mutation_fingerprint(store_map)
        try:
            options.import_fixture(fixture_id)
        except ValidationError as exc:
            if not exc.issues:
                raise ValidationError("Rejected option fixture has no structured issue") from exc
            actual_rule = exc.issues[0].rule
            expected_rule = fixture.metadata.get("expected_error_rule")
            if actual_rule != expected_rule:
                raise ValidationError("Rejected option fixture used the wrong error rule") from exc
            rejected[fixture_id] = actual_rule
        else:  # pragma: no cover - explicit fail-closed gate
            raise ValidationError("Invalid option fixture was accepted")
        _assert_unchanged(
            store_map,
            before,
            "Rejected option fixture changed a store",
        )
    receipts["rejected_options"] = rejected

    for fixture_id in _NEWS_FIXTURE_IDS:
        receipt = news.import_fixture(fixture_id)
        _require_success(receipt, "Stage 4 news publication did not succeed")
        receipts[fixture_id] = receipt.to_primitive()

    before_replay = mutation_fingerprint(store_map)
    company_replay = company.import_fixture("sec_initial")
    option_replay = options.import_fixture("stage4.market.options.spy_complete")
    news_replay = news.import_fixture("news_initial")
    for receipt in (company_replay, option_replay, news_replay):
        if receipt.outcome != "unchanged" or receipt.written_count != 0:
            raise ValidationError("Exact Stage 4 replay was not a total no-op")
    _assert_unchanged(store_map, before_replay, "Exact Stage 4 replay changed a store")
    receipts["exact_replay"] = {
        "company": company_replay.to_primitive(),
        "options": option_replay.to_primitive(),
        "news": news_replay.to_primitive(),
    }

    return receipts, {
        "exact_replay_unchanged": True,
        "invalid_option_captures_unchanged": True,
    }


def _golden_query_snapshot(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    before = mutation_fingerprint(store_map)
    company = CompanyStage4Repository(store_map, registry)
    options = OptionsStage4Repository(store_map, registry)
    news = NewsStage4Repository(store_map, registry)

    northstar = CompanyStage4Query("0001000001")
    aurora = CompanyStage4Query("0001000002")
    spy_id = stable_id(
        "instrument",
        "fixture.market.instruments",
        "fixture.market.instrument.spy.v1",
    )
    latest_options = OptionsStage4Query(
        underlying_instrument_id=spy_id,
        resolved_feed="fixture_options",
    )
    initial_options = OptionsStage4Query(
        underlying_instrument_id=spy_id,
        resolved_feed="fixture_options",
        mode="as_of",
        as_of="2026-08-03T16:30:00Z",
    )
    news_latest = NewsStage4Query(
        source_name="fixture_wire",
        limit=100,
    )
    news_historical = NewsStage4Query(
        source_name="fixture_wire",
        source_item_id="nw-100",
        as_of="2026-06-15T00:00:00Z",
        limit=100,
    )
    news_retracted = NewsStage4Query(
        source_name="fixture_wire",
        source_item_id="nw-100",
        include_retracted=True,
        limit=100,
    )

    result: dict[str, Any] = {
        "company_issuer_latest": company.get_issuer(northstar),
        "company_issuer_before_ticker_change": company.get_issuer(
            CompanyStage4Query(
                "0001000001",
                as_of="2026-03-03T12:00:00Z",
            )
        ),
        "company_northstar_filings": company.get_filings(northstar),
        "company_aurora_filings": company.get_filings(aurora),
        "company_fundamentals_latest": company.get_fundamentals(northstar),
        "company_fundamentals_as_of": company.get_fundamentals(
            CompanyStage4Query(
                "0001000001",
                as_of="2026-03-20T12:00:00Z",
            )
        ),
        "company_actions": company.get_corporate_actions(northstar),
        "company_earnings_latest": company.get_earnings(northstar),
        "company_earnings_as_of": company.get_earnings(
            CompanyStage4Query(
                "0001000001",
                as_of="2026-07-10T12:00:00Z",
            )
        ),
        "options_latest": options.get_capture(latest_options),
        "options_initial_as_of": options.get_capture(initial_options),
        "news_latest_active": news.get_items(news_latest),
        "news_historical": news.get_items(news_historical),
        "news_historical_search": news.search(news_historical, "Northstar"),
        "news_retracted_audit": news.get_items(news_retracted),
    }
    _assert_unchanged(store_map, before, "Stage 4 golden reads changed a store")
    result["sha256"] = hashlib.sha256(
        dumps_strict(result).encode("utf-8")
    ).hexdigest()
    return result


def _stage4_dataset_counts(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for dataset_id in _STAGE4_DATASET_IDS:
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


def run_clean_stage4_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, Any]:
    """Run the complete fixture-only Stage 4 gate under one empty work root."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    stage3_evidence = run_clean_stage3_rebuild(
        project_root=project,
        work_root=root / "stage3",
    )
    source_map = explicit_store_map(root / "stage3" / "stage2" / "source")
    registry = stage4_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    if registry.registry_version != "2.2.0":
        raise ValidationError("Stage 4 requires the reviewed registry revision")
    manifest = FixtureManifest.load(
        project / FIXTURE_MANIFEST_PATH,
        project_root=project,
    )
    fixture_evidence = _fixture_evidence(manifest)

    migration_heads = initialize_all(source_map, registry)
    after_upgrade = mutation_fingerprint(source_map)
    repeated_heads = initialize_all(source_map, registry)
    if repeated_heads != migration_heads:
        raise ValidationError("Repeated Stage 4 initialization changed migration heads")
    _assert_unchanged(
        source_map,
        after_upgrade,
        "Repeated Stage 4 initialization changed a store",
    )

    receipts, no_write = _import_fixtures(source_map, manifest)
    counts = _stage4_dataset_counts(source_map, registry)
    source_health = all_store_health(source_map, registry)
    golden_queries = _golden_query_snapshot(source_map, registry)
    source_manifest = logical_manifest(source_map, registry)

    before_backup = mutation_fingerprint(source_map)
    backup = backup_all(source_map, registry, target_root=root / "backup")
    _assert_unchanged(source_map, before_backup, "Stage 4 backup changed its source")
    restored = restore_all(backup, registry, target_root=root / "restored")

    backup_manifest = logical_manifest(backup.backup_store_map, registry)
    restored_manifest = logical_manifest(restored.restored_store_map, registry)
    if not (
        dumps_strict(source_manifest)
        == dumps_strict(backup_manifest)
        == dumps_strict(restored_manifest)
    ):
        raise ValidationError("Stage 4 backup or restore changed the logical manifest")

    restored_queries = _golden_query_snapshot(restored.restored_store_map, registry)
    if dumps_strict(restored_queries) != dumps_strict(golden_queries):
        raise ValidationError("Stage 4 restored golden queries changed")
    restored_health = all_store_health(restored.restored_store_map, registry)
    if dumps_strict(restored_health.to_primitive()) != dumps_strict(
        source_health.to_primitive()
    ):
        raise ValidationError("Stage 4 restored health evidence changed")

    evidence: dict[str, Any] = {
        "contract": "quant_data.stage4_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "stage3_evidence_sha256": stage3_evidence["sha256"],
        "stage4_fixture_evidence": fixture_evidence,
        "migration_heads": migration_heads,
        "dataset_counts": counts,
        "receipts": receipts,
        "no_write": no_write,
        "source_health": source_health.to_primitive(),
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


def compare_clean_stage4_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, Any]:
    """Run Stage 4 twice and require byte-equal path-free evidence."""

    first = run_clean_stage4_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage4_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two Stage 4 clean rebuilds produced different evidence")
    return first


__all__ = (
    "compare_clean_stage4_rebuilds",
    "run_clean_stage4_rebuild",
)
