"""Deterministic, offline acceptance harness for the authorized Stage 1 slice.

The harness has no database defaults.  Callers must provide a clean store root,
and all four SQLite paths are derived beneath that explicit root.  It performs
only packaged fixture work and host-routed read operations; it has no provider
or network integration.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .boundary import Stage1Application, ToolDispatcher
from .errors import ConflictError, ValidationError
from .fingerprint import logical_manifest, mutation_fingerprint
from .fixtures import FixtureManifest
from .json_codec import dumps_strict, loads_strict
from .macro import MacroFixtureImporter, MacroSeriesQuery, MacroSeriesRepository
from .market import DailyPriceImporter, DailyPriceQuery, DailyPriceRepository
from .migrations import initialize_all
from .registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from .stores import STORE_ROLES, StoreMap, StoreRole, read_connection


FIXTURE_MANIFEST_PATH = Path("tests/fixtures/manifest.json")
_STORE_FILENAMES = {
    StoreRole.MARKET: "market.sqlite",
    StoreRole.MACRO: "macro.sqlite",
    StoreRole.COMPANY: "company.sqlite",
    StoreRole.NEWS: "news.sqlite",
}
_MACRO_SERIES_ID = "fixture:philadelphia_fed_rtdsm:EMPLOY"


def explicit_store_map(store_root: str | Path) -> StoreMap:
    """Build all four paths from a caller-supplied root, never an environment."""

    root = Path(store_root).expanduser().resolve(strict=False)
    return StoreMap.four_explicit(
        market=root / _STORE_FILENAMES[StoreRole.MARKET],
        macro=root / _STORE_FILENAMES[StoreRole.MACRO],
        company=root / _STORE_FILENAMES[StoreRole.COMPANY],
        news=root / _STORE_FILENAMES[StoreRole.NEWS],
    )


def _prepare_clean_root(store_root: str | Path) -> Path:
    root = Path(store_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 1 store root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 1 clean rebuild requires an empty store root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _fixture_evidence(manifest: FixtureManifest) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for fixture_id in (
        "market.base",
        "market.correction",
        "macro.first_vintage",
        "macro.revised_vintage",
    ):
        fixture = manifest.get(fixture_id)
        result.append(
            {
                "id": fixture.id,
                "sha256": fixture.sha256,
                "semantic_identity": fixture.expected_semantic_identity,
                "byte_count": fixture.byte_count,
                "captured_at": fixture.captured_at,
                "test_fixture": fixture.test_fixture,
                "promotable": fixture.promotable,
                "expected_warnings": list(fixture.expected_warnings),
            }
        )
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


def _macro_query(
    vintage_mode: str,
    *,
    as_of: str | None = None,
    date_only_policy: str = "completed_date",
) -> MacroSeriesQuery:
    return MacroSeriesQuery(
        series_id=_MACRO_SERIES_ID,
        start_date="2026-01-01",
        end_date="2026-02-28",
        vintage_mode=vintage_mode,
        as_of=as_of,
        date_only_policy=date_only_policy,
        limit=100,
    )


def _assert_receipt(receipt: Any, outcome: str, written_count: int) -> None:
    if receipt.outcome != outcome or receipt.written_count != written_count:
        raise ValidationError("Stage 1 ingestion receipt violated the golden transition")
    if outcome == "unchanged" and any(
        value is not None for value in (receipt.run_id, receipt.artifact_id, receipt.snapshot_id)
    ):
        raise ValidationError("Stage 1 replay receipt reported a write identity")


def _store_runtime_evidence(store_map: StoreMap, registry: Registry) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for role in STORE_ROLES:
        declaration = registry.store(role.value)
        with read_connection(
            store_map,
            role,
            expected_anchor=declaration.anchor_relation,
        ) as connection:
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            migrations = [
                {"id": str(row["migration_id"]), "sha256": str(row["sha256"])}
                for row in connection.execute(
                    "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
                )
            ]
        if journal_mode != "wal" or synchronous != 2 or integrity != "ok" or foreign_keys:
            raise ValidationError("Stage 1 store runtime or integrity policy failed")
        result.append(
            {
                "role": role.value,
                "journal_mode": journal_mode,
                "synchronous": "full",
                "integrity": integrity,
                "foreign_key_violations": 0,
                "migrations": migrations,
            }
        )
    return result


def _stage1_state_counts(store_map: StoreMap) -> dict[str, dict[str, int]]:
    expected_relations = {
        StoreRole.MARKET: (
            "instruments",
            "instrument_identifiers",
            "market_price_ingestion_requests",
            "prices_daily",
            "prices_daily_versions",
            "ingestion_runs",
        ),
        StoreRole.MACRO: (
            "macro_series",
            "macro_releases",
            "macro_source_artifacts",
            "macro_source_snapshots",
            "macro_snapshot_scopes",
            "macro_observation_versions",
            "macro_snapshot_observation_membership",
            "ingestion_runs",
        ),
        StoreRole.COMPANY: ("ingestion_runs",),
        StoreRole.NEWS: ("ingestion_runs",),
    }
    result: dict[str, dict[str, int]] = {}
    for role, relations in expected_relations.items():
        with read_connection(store_map, role) as connection:
            result[role.value] = {
                relation: int(
                    connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0]
                )
                for relation in relations
            }
    expected = {
        "market": {
            "instruments": 2,
            "instrument_identifiers": 2,
            "market_price_ingestion_requests": 2,
            "prices_daily": 4,
            "prices_daily_versions": 5,
            "ingestion_runs": 2,
        },
        "macro": {
            "macro_series": 1,
            "macro_releases": 2,
            "macro_source_artifacts": 2,
            "macro_source_snapshots": 2,
            "macro_snapshot_scopes": 2,
            "macro_observation_versions": 4,
            "macro_snapshot_observation_membership": 4,
            "ingestion_runs": 2,
        },
        "company": {"ingestion_runs": 0},
        "news": {"ingestion_runs": 0},
    }
    if result != expected:
        raise ValidationError("Stage 1 store counts violated the golden state transition")
    return result


def run_clean_rebuild(
    *,
    project_root: str | Path,
    store_root: str | Path,
) -> dict[str, Any]:
    """Run one complete Stage 1 rebuild and return path-free evidence."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_root(store_root)
    store_map = explicit_store_map(root)
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    fixture_manifest = FixtureManifest.load(
        project / FIXTURE_MANIFEST_PATH,
        project_root=project,
    )
    fixtures = _fixture_evidence(fixture_manifest)
    migration_heads = initialize_all(store_map, registry)

    market_importer = DailyPriceImporter(store_map, fixture_manifest)
    market_base = market_importer.import_fixture("market.base")
    market_replay = market_importer.import_fixture("market.base")
    market_correction = market_importer.import_fixture("market.correction")
    _assert_receipt(market_base, "succeeded", 4)
    _assert_receipt(market_replay, "unchanged", 0)
    _assert_receipt(market_correction, "succeeded", 1)
    if market_base.semantic_identity != fixture_manifest.get("market.base").expected_semantic_identity:
        raise ValidationError("Market base fixture semantic identity drifted")
    if market_correction.semantic_identity != fixture_manifest.get("market.correction").expected_semantic_identity:
        raise ValidationError("Market correction fixture semantic identity drifted")

    macro_importer = MacroFixtureImporter(store_map, fixture_manifest)
    macro_first = macro_importer.import_fixture("macro.first_vintage")
    macro_replay = macro_importer.import_fixture("macro.first_vintage")
    macro_revised = macro_importer.import_fixture("macro.revised_vintage")
    _assert_receipt(macro_first, "succeeded", 2)
    _assert_receipt(macro_replay, "unchanged", 0)
    _assert_receipt(macro_revised, "succeeded", 2)
    if macro_first.semantic_identity != fixture_manifest.get("macro.first_vintage").expected_semantic_identity:
        raise ValidationError("Macro first-vintage semantic identity drifted")
    if macro_revised.semantic_identity != fixture_manifest.get("macro.revised_vintage").expected_semantic_identity:
        raise ValidationError("Macro revised-vintage semantic identity drifted")

    market_repository = DailyPriceRepository(store_map, registry)
    market_latest = market_repository.get_prices(_market_query(mode="latest"))
    market_as_of = market_repository.get_prices(
        _market_query(mode="as_of", as_of="2026-07-19")
    )
    if str(market_latest["observations"][1]["close"]) != "640.25":
        raise ValidationError("Stage 1 latest price golden result failed")
    if str(market_as_of["observations"][1]["close"]) != "640":
        raise ValidationError("Stage 1 as-of price golden result failed")

    macro_repository = MacroSeriesRepository(store_map, registry)
    macro_latest = macro_repository.get_series(_macro_query("latest")).to_primitive()
    macro_first_release = macro_repository.get_series(
        _macro_query("first_release")
    ).to_primitive()
    macro_as_of = macro_repository.get_series(
        _macro_query("as_of", as_of="2026-07-09")
    ).to_primitive()
    macro_same_day_inclusive = macro_repository.get_series(
        _macro_query(
            "as_of",
            as_of="2026-07-10T12:00:00-04:00",
            date_only_policy="calendar_date_inclusive",
        )
    ).to_primitive()

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
    tool_description = dispatcher.call("timeseries.describe", {"series": tool_series})
    if tool_description["missing_count"] != 1:
        raise ValidationError("Stage 1 tool composition lost explicit missingness")

    application = Stage1Application(store_map, registry)
    before_public_reads = mutation_fingerprint(store_map)
    overview = application.handle("GET", "/")
    health = application.handle("GET", "/api/health")
    manifest_response = application.handle("GET", "/api/agent-tools")
    price_target = (
        "/api/price-series?provider_symbol=SPY&provider=fixture_fmp"
        "&price_variant=raw&currency_segment=USD&start_date=2026-07-16"
        "&end_date=2026-07-17&mode=latest&date_only_policy=completed_date&limit=100"
    )
    price_response = application.handle("GET", price_target)
    call_body = dumps_strict(
        {"api_version": dispatcher.api_version, "tool": "macro.get_series", "arguments": tool_arguments}
    ).encode("utf-8")
    call_response = application.handle("POST", "/api/agent-tools/call", body=call_body)
    responses = (overview, health, manifest_response, price_response, call_response)
    if any(int(response.status) != 200 for response in responses):
        raise ValidationError("Stage 1 public read acceptance request failed")
    if loads_strict(call_response.body)["result"] != tool_series:
        raise ValidationError("HTTP and in-process tool results are not equivalent")
    after_public_reads = mutation_fingerprint(store_map)
    if before_public_reads["sha256"] != after_public_reads["sha256"]:
        raise ValidationError("Stage 1 public read surface mutated a store")

    store_runtime = _store_runtime_evidence(store_map, registry)
    state_counts = _stage1_state_counts(store_map)
    final_manifest = logical_manifest(store_map, registry)
    golden_results = {
        "market_latest": market_latest,
        "market_as_of": market_as_of,
        "macro_latest": macro_latest,
        "macro_first_release": macro_first_release,
        "macro_as_of": macro_as_of,
        "macro_same_day_inclusive": macro_same_day_inclusive,
        "tool_series": tool_series,
        "tool_description": tool_description,
        "http_body_sha256": [
            hashlib.sha256(response.body).hexdigest() for response in responses
        ],
    }
    evidence: dict[str, Any] = {
        "contract": "quant_data.stage1_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "fixture_evidence": fixtures,
        "migration_heads": migration_heads,
        "ingestion_receipts": {
            "market_base": market_base.to_primitive(),
            "market_replay": market_replay.to_primitive(),
            "market_correction": market_correction.to_primitive(),
            "macro_first": macro_first.to_primitive(),
            "macro_replay": macro_replay.to_primitive(),
            "macro_revised": macro_revised.to_primitive(),
        },
        "store_runtime": store_runtime,
        "state_counts": state_counts,
        "public_reads_unchanged": True,
        "golden_results_sha256": hashlib.sha256(
            dumps_strict(golden_results).encode("utf-8")
        ).hexdigest(),
        "logical_manifest": final_manifest,
    }
    evidence["sha256"] = hashlib.sha256(dumps_strict(evidence).encode("utf-8")).hexdigest()
    return evidence


def compare_clean_rebuilds(
    *,
    project_root: str | Path,
    first_store_root: str | Path,
    second_store_root: str | Path,
) -> dict[str, Any]:
    """Run the acceptance harness twice and require identical path-free evidence."""

    first = run_clean_rebuild(project_root=project_root, store_root=first_store_root)
    second = run_clean_rebuild(project_root=project_root, store_root=second_store_root)
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two Stage 1 clean rebuilds produced different evidence")
    return first
