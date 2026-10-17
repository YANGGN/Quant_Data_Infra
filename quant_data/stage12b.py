"""Dependency-free Stage 12B incremental-market fixture gate.

The public rebuild functions deliberately separate the authority-only dry run
from the fixture rehearsal.  The dry run reads only the explicit registry and
closed scope manifests.  It does not construct a store map, open SQLite, read
credentials, or create its supplied root.  The rehearsal wiring is kept in
this module so its evidence remains path-free and deterministic while the
market-domain implementation owns parsing and publication semantics.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

from .errors import ConflictError, Issue, ResourceLimitError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict
from .market import stage12_incremental as _stage12_incremental
from .market.stage12_incremental import (
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
)
from .market.stage12_scope import load_stage12_market_v1_scope
from .market.stage12b_scope import (
    STAGE12B_COLLECTOR_HANDLER,
    STAGE12B_COLLECTOR_ID,
    STAGE12B_NORMALIZATION_VERSION,
    STAGE12B_PRICE_VARIANT,
    load_stage12b_incremental_market_v1_scope,
    require_stage12a_binding,
)
from .migrations import initialize_all
from .registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage12b_registry_profile,
    stage12c_registry_profile,
    stage12_registry_profile,
)
from .stage1 import explicit_store_map
from .stores import (
    StoreMap,
    StoreRole,
    StoreWriteLock,
    read_connection,
    stable_id,
    writer_connection,
)


STAGE12B_GATE_CONTRACT = "quant_data.stage12b_incremental_market_v1_fixture_gate"
STAGE12B_GATE_VERSION = "1.0.0"
STAGE12B_CANONICAL_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12B_CANONICAL_REGISTRY_VERSION = "2.13.0"
STAGE12A_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12A_REGISTRY_VERSION = "2.12.0"
STAGE12A_REGISTRY_SOURCE_SHA256 = (
    "80a41e9f124cccb85bbdda665e33b1ef408f538b7e50b499b630b5ce6c761e7e"
)
STAGE12_PRE_PATH_REGISTRY_VERSION = "2.11.0"
STAGE12_PRE_PATH_MARKET_DEFAULT = "data/market_data.sqlite"
STAGE12B_WORK_ROOT_POLICY = "explicit_absent_or_empty_isolated_fixture_only"

_CURRENT_STORE_DEFAULTS = (
    ("market", "data/market.sqlite"),
    ("macro", "data/macro_data.sqlite"),
    ("company", "data/company_data.sqlite"),
    ("news", "data/news_data.sqlite"),
)
_RETAINED_CANDIDATE_PARENT = Path("/home/volatility/quant-data-nonprod")
_FIXTURE_SYMBOL = "AAPL"
_FIXTURE_FROM = "2026-08-10"
_FIXTURE_TO = "2026-08-12"
_FIXTURE_SESSIONS = ("2026-08-10", "2026-08-11", "2026-08-12")
_FIXED_MIGRATION_AT = "2026-08-15T12:00:00Z"
_FIXED_SEED_CAPTURED_AT = "2026-08-15T12:01:00Z"
_FIXED_INITIAL_CAPTURED_AT = "2026-08-15T12:02:00Z"
_FIXED_CORRECTION_CAPTURED_AT = "2026-08-15T12:03:00Z"
_FIXED_NEW_DATE_CAPTURED_AT = "2026-08-15T12:04:00Z"
_STAGE10_MARKET_MIGRATION_ID = "market:0010_stage10_market_history"


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _conflict(pointer: str, rule: str, message: str) -> ConflictError:
    return ConflictError(message, issues=(Issue(pointer or "/", rule, message),))


def _sha256(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _is_within(value: Path, root: Path) -> bool:
    try:
        value.relative_to(root)
    except ValueError:
        return False
    return True


def _require_absolute_path(value: str | Path, *, name: str, strict: bool) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _error(f"/{name}", "path", f"An explicit {name} path is required")
    if isinstance(value, str) and not value.strip():
        raise _error(f"/{name}", "path", f"An explicit {name} path is required")
    try:
        supplied = Path(value)
        if not supplied.is_absolute():
            raise _error(
                f"/{name}",
                "absolute_path",
                f"An absolute {name} path is required",
            )
        return supplied.resolve(strict=strict)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error(f"/{name}", "path", f"The explicit {name} path is unavailable") from exc


def _require_project_root(project_root: str | Path) -> Path:
    project = _require_absolute_path(project_root, name="project_root", strict=True)
    if not project.is_dir():
        raise _error("/project_root", "directory", "Project root must be a directory")
    return project


def _require_fixture_root(work_root: str | Path, *, project: Path) -> Path:
    """Validate an initially absent or empty root without mutating it."""

    if isinstance(work_root, bool) or not isinstance(work_root, (str, Path)):
        raise _error("/work_root", "path", "An explicit work_root path is required")
    try:
        supplied = Path(work_root)
        supplied_is_symlink = supplied.is_symlink()
    except OSError as exc:
        raise _error("/work_root", "path", "Stage 12B fixture root cannot be inspected") from exc
    root = _require_absolute_path(work_root, name="work_root", strict=False)
    if supplied_is_symlink:
        raise _conflict(
            "/work_root",
            "symlink",
            "Stage 12B fixture root must name its physical directory directly",
        )
    protected = (
        project,
        project / "data",
        *(project / default_path for _role, default_path in _CURRENT_STORE_DEFAULTS),
        _RETAINED_CANDIDATE_PARENT.resolve(strict=False),
    )
    broad = (Path(root.anchor).resolve(strict=False), Path("/tmp").resolve(strict=False))
    if (
        root in broad
        or any(_is_within(root, item) or _is_within(item, root) for item in protected)
    ):
        raise _conflict(
            "/work_root",
            "isolated_root",
            "Stage 12B fixture root overlaps a protected project or retained-candidate path",
        )
    try:
        if root.exists():
            if not root.is_dir():
                raise _conflict(
                    "/work_root",
                    "directory",
                    "Stage 12B fixture root must be a directory",
                )
            if root.is_symlink():
                raise _conflict(
                    "/work_root",
                    "symlink",
                    "Stage 12B fixture root must name its physical directory directly",
                )
            if next(root.iterdir(), None) is not None:
                raise _conflict(
                    "/work_root",
                    "empty",
                    "Stage 12B fixture root must be absent or empty before rehearsal",
                )
    except ConflictError:
        raise
    except OSError as exc:
        raise _error("/work_root", "path", "Stage 12B fixture root cannot be inspected") from exc
    return root


def _collector_mapping(registry: Any) -> Mapping[str, object]:
    matches = tuple(
        item
        for item in registry.collectors
        if isinstance(item, Mapping) and item.get("id") == STAGE12B_COLLECTOR_ID
    )
    if len(matches) != 1:
        raise _error(
            "/registry/collectors",
            "collector",
            "Canonical registry must declare exactly one Stage 12B fixture collector",
        )
    return matches[0]


def _validate_static_contract(project: Path) -> tuple[Any, Any, Any, Mapping[str, object]]:
    """Read all immutable Stage 12B authority material without store I/O."""

    registry = stage12c_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    if (
        registry.schema_version,
        registry.registry_version,
    ) != (
        STAGE12B_CANONICAL_REGISTRY_SCHEMA_VERSION,
        STAGE12B_CANONICAL_REGISTRY_VERSION,
    ):
        raise _error(
            "/registry",
            "revision",
            "Canonical registry does not match the reviewed Stage 12B revision",
        )
    current_defaults = tuple(
        (role, registry.store(role).default_path)
        for role, _expected in _CURRENT_STORE_DEFAULTS
    )
    if current_defaults != _CURRENT_STORE_DEFAULTS:
        raise _error(
            "/registry/stores",
            "store_defaults",
            "Canonical registry defaults drifted from the reviewed Stage 12 path",
        )

    stage12a = stage12b_registry_profile(registry)
    if (
        stage12a.schema_version,
        stage12a.registry_version,
        stage12a.source_sha256,
    ) != (
        STAGE12A_REGISTRY_SCHEMA_VERSION,
        STAGE12A_REGISTRY_VERSION,
        STAGE12A_REGISTRY_SOURCE_SHA256,
    ):
        raise _error(
            "/registry/stage12a_projection",
            "revision",
            "Stage 12A historical registry projection drifted",
        )
    if any(
        item.get("id") == STAGE12B_COLLECTOR_ID
        for item in stage12a.collectors
        if isinstance(item, Mapping)
    ):
        raise _error(
            "/registry/stage12a_projection/collectors",
            "historical_collector",
            "Stage 12A projection exposed the Stage 12B collector",
        )

    pre_path = stage12_registry_profile(registry)
    if (
        pre_path.registry_version != STAGE12_PRE_PATH_REGISTRY_VERSION
        or pre_path.store("market").default_path != STAGE12_PRE_PATH_MARKET_DEFAULT
    ):
        raise _error(
            "/registry/stage12_projection",
            "historical_path",
            "The exact pre-Stage 12 market default drifted",
        )

    stage12a_scope_source = project / "config" / "stage12_market_v1_scope.json"
    stage12a_scope = load_stage12_market_v1_scope(stage12a_scope_source)
    scope = load_stage12b_incremental_market_v1_scope(
        project / "config" / "stage12b_incremental_market_v1_scope.json"
    )
    require_stage12a_binding(
        scope,
        stage12a_scope,
        scope_source=stage12a_scope_source,
    )
    collector = _collector_mapping(registry)
    if (
        collector.get("handler") != STAGE12B_COLLECTOR_HANDLER
        or collector.get("network") is not False
        or tuple(collector.get("configuration_env", ()))
        or tuple(collector.get("input_datasets", ()))
        != tuple(scope.collector.input_datasets)
        or tuple(collector.get("output_datasets", ()))
        != tuple(scope.collector.output_datasets)
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _error(
            "/registry/collectors",
            "collector_policy",
            "Canonical Stage 12B collector differs from the reviewed fixture policy",
        )
    expected_bounds = {
        "max_requests": scope.collector.max_requests,
        "max_rows": scope.collector.max_rows,
        "max_bytes": scope.collector.max_bytes,
        "max_seconds": scope.collector.max_seconds,
    }
    if dict(collector.get("workload_bounds", {})) != expected_bounds:
        raise _error(
            "/registry/collectors/workload_bounds",
            "bounds",
            "Canonical Stage 12B collector resource bounds drifted",
        )
    return registry, stage12a_scope, scope, collector


def _dry_run_evidence(project: Path, root: Path) -> dict[str, object]:
    registry, stage12a_scope, scope, collector = _validate_static_contract(project)
    instrument = next(
        (item for item in stage12a_scope.roster if item.symbol == _FIXTURE_SYMBOL),
        None,
    )
    if instrument is None or instrument.asset_type != "equity":
        raise _error(
            "/stage12a_scope/roster",
            "fixture_symbol",
            "The reviewed Stage 12B fixture symbol is not a frozen equity",
        )
    if (
        scope.publication.normalization_version != STAGE12B_NORMALIZATION_VERSION
        or stage12a_scope.baseline.price_variant != STAGE12B_PRICE_VARIANT
    ):
        raise _error(
            "/scope/publication",
            "normalization",
            "Stage 12B normalization or price variant drifted",
        )
    plan: dict[str, object] = {
        "collector": {
            "handler": STAGE12B_COLLECTOR_HANDLER,
            "id": STAGE12B_COLLECTOR_ID,
            "max_attempts": scope.collector.max_requests,
            "retry_transient_classes": [],
        },
        "fixture_request": {
            "asset_type": instrument.asset_type,
            "endpoint_path": "/stable/historical-price-eod/full",
            "from": _FIXTURE_FROM,
            "price_variant": STAGE12B_PRICE_VARIANT,
            "provider": "fmp",
            "session_count": len(_FIXTURE_SESSIONS),
            "symbol": instrument.symbol,
            "to": _FIXTURE_TO,
        },
        "phase": "stage12b_offline_fixture_only",
        "store": {
            "lock_identity": "derived_from_explicit_fixture_market_store",
            "role": "market",
            "selection": "explicit_isolated_fixture_root",
        },
    }
    result: dict[str, object] = {
        "canonical_registry": {
            "revision": registry.registry_version,
            "schema_version": registry.schema_version,
            "source_sha256": registry.source_sha256,
        },
        "collector": {
            "handler": collector["handler"],
            "id": collector["id"],
            "max_attempts": scope.collector.max_requests,
            "max_bytes": scope.collector.max_bytes,
            "max_rows": scope.collector.max_rows,
            "max_seconds": scope.collector.max_seconds,
            "schedule_mode": "manual_only",
        },
        "contract": STAGE12B_GATE_CONTRACT,
        "contract_version": STAGE12B_GATE_VERSION,
        "dry_run": {
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "fixture_transport_calls": 0,
            "locks_acquired": 0,
            "network_calls": 0,
            "scheduler_actions": 0,
            "sqlite_stores_opened": 0,
            "subprocess_calls": 0,
            "transactions_started": 0,
            "work_root_policy": STAGE12B_WORK_ROOT_POLICY,
        },
        "plan": plan,
        "scope": {
            "stage12a_scope_file_sha256": scope.stage12a_binding.scope_file_sha256,
            "stage12a_manifest_sha256": stage12a_scope.manifest_sha256,
            "stage12a_roster_sha256": stage12a_scope.roster_sha256,
            "stage12b_manifest_sha256": scope.manifest_sha256,
            "target_profile_id": scope.target_profile_id,
        },
        "stage12a_projection": {
            "revision": STAGE12A_REGISTRY_VERSION,
            "schema_version": STAGE12A_REGISTRY_SCHEMA_VERSION,
            "source_sha256": STAGE12A_REGISTRY_SOURCE_SHA256,
        },
    }
    serialized = dumps_strict(result)
    for forbidden in (str(project), str(root)):
        if forbidden in serialized:
            raise _error("/", "path_free", "Stage 12B dry-run evidence exposed a physical path")
    return result


def run_stage12b_dry_run(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Validate the closed Stage 12B plan with zero operational side effects."""

    project = _require_project_root(project_root)
    root = _require_fixture_root(work_root, project=project)
    evidence = _dry_run_evidence(project, root)
    evidence["sha256"] = _sha256(evidence)
    dumps_strict(evidence)
    return evidence


def _fixture_rows(*, correction: bool = False, date_value: str | None = None) -> list[dict[str, object]]:
    dates = (date_value,) if date_value is not None else _FIXTURE_SESSIONS
    rows: list[dict[str, object]] = []
    for index, trade_date in enumerate(dates):
        open_value = 100 + index * 2
        close_value = open_value + 1
        if correction and trade_date == "2026-08-11":
            close_value += 2
        rows.append(
            {
                "symbol": _FIXTURE_SYMBOL,
                "date": trade_date,
                "open": open_value,
                "high": close_value + 1,
                "low": open_value - 1,
                "close": close_value,
                "volume": 1_000 + index,
                "change": close_value - open_value,
                "changePercent": 1,
                "vwap": open_value,
            }
        )
    return rows


def _fixture_response(
    rows: list[dict[str, object]], *, elapsed_seconds: int | float = 1
) -> Stage12BFixtureResponse:
    return Stage12BFixtureResponse(
        status=200,
        media_type="application/json; charset=utf-8",
        body=dumps_strict(rows).encode("utf-8"),
        elapsed_seconds=elapsed_seconds,
    )


def _fixture_request(
    *,
    from_date: str = _FIXTURE_FROM,
    to_date: str = _FIXTURE_TO,
    session_dates: tuple[str, ...] = _FIXTURE_SESSIONS,
    captured_at: str = _FIXED_INITIAL_CAPTURED_AT,
) -> Stage12BFixtureRequest:
    return Stage12BFixtureRequest(
        symbol=_FIXTURE_SYMBOL,
        from_date=from_date,
        to_date=to_date,
        session_dates=session_dates,
        captured_at=captured_at,
    )


class _InjectedFixtureTransport:
    """One bounded local response source that observes its closed descriptor."""

    def __init__(
        self,
        response: Stage12BFixtureResponse,
        *,
        events: list[str] | None = None,
    ) -> None:
        self._response = response
        self._events = events
        self.calls = 0
        self.descriptors: list[dict[str, object]] = []

    def receive(self, request: Stage12BFixtureRequest) -> Stage12BFixtureResponse:
        self.calls += 1
        self.descriptors.append(
            {
                "from": request.from_date,
                "session_dates": list(request.session_dates),
                "symbol": request.symbol,
                "to": request.to_date,
            }
        )
        if self._events is not None:
            self._events.append("fixture_response_delivered")
        return self._response


def _seed_fixture_instrument(store_map: StoreMap) -> dict[str, str]:
    """Seed the minimum frozen Stage 10 identity without any sealed runner."""

    identity_seed = _sha256(
        {
            "identity_version": "stage10.fmp.instrument.v1",
            "provider": "fmp",
            "provider_symbol": _FIXTURE_SYMBOL,
            "asset_type": "equity",
            "currency_segment": "provider_native",
        }
    )
    instrument_id = stable_id("stage10_instrument", identity_seed)
    semantic_identity = _sha256(
        {
            "fixture_prerequisite": "stage12b_minimum_frozen_instrument",
            "instrument_id": instrument_id,
            "symbol": _FIXTURE_SYMBOL,
        }
    )
    run_id = stable_id("stage12b_fixture_seed", semantic_identity)
    scope_json = dumps_strict(
        {
            "asset_type": "equity",
            "fixture_prerequisite": "minimum_existing_stage10_instrument",
            "symbol": _FIXTURE_SYMBOL,
        }
    )
    with StoreWriteLock(store_map.path(StoreRole.MARKET)), writer_connection(
        store_map, StoreRole.MARKET
    ) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json, status,
                    started_at, completed_at, fetched_count, written_count, warnings_json,
                    code_version
                ) VALUES (?, 'market.stage10.instruments', ?, ?, ?, 'running', ?, NULL, 1, 0, '[]', ?)
                """,
                (
                    run_id,
                    semantic_identity,
                    STAGE12B_COLLECTOR_ID,
                    scope_json,
                    _FIXED_SEED_CAPTURED_AT,
                    "stage12b.fixture.seed.v1",
                ),
            )
            connection.execute(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type, display_name,
                    exchange_code, currency_segment, first_trade_date, identity_seed_sha256,
                    captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', ?, 'equity', 'Stage 12B fixture AAPL', NULL,
                          'provider_native', NULL, ?, ?, 'datetime', ?)
                """,
                (
                    instrument_id,
                    _FIXTURE_SYMBOL,
                    identity_seed,
                    _FIXED_SEED_CAPTURED_AT,
                    run_id,
                ),
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
    return {"asset_type": "equity", "instrument_id": instrument_id, "symbol": _FIXTURE_SYMBOL}


def _initialize_fixture_store(
    root: Path,
    registry: Any,
) -> tuple[StoreMap, dict[str, tuple[str, ...]], dict[str, str]]:
    try:
        root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise _error("/work_root", "create", "Stage 12B fixture root cannot be created") from exc
    store_map = explicit_store_map(root)
    if any(not _is_within(path, root) for _role, path in store_map.items()):
        raise _error("/work_root", "store_map", "Fixture stores escaped the explicit root")
    heads = initialize_all(store_map, registry, applied_at=_FIXED_MIGRATION_AT)
    if heads.get("market", ())[-1:] != (_STAGE10_MARKET_MIGRATION_ID,):
        raise _error("/fixture/migrations", "market_head", "Fixture market store lacks the reviewed Stage 10 model")
    seed = _seed_fixture_instrument(store_map)
    return store_map, heads, seed


def _market_integrity(store_map: StoreMap) -> tuple[dict[str, int], dict[str, object]]:
    relations = (
        "ingestion_runs",
        "ingestion_artifacts",
        "ingestion_snapshots",
        "stage10_instruments",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    )
    with read_connection(store_map, StoreRole.MARKET) as connection:
        counts = {
            relation: int(connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0])
            for relation in relations
        }
        integrity_rows = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_check"))
        duplicate_current = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    SELECT instrument_id, trade_date, provider, price_variant, currency_segment,
                           count(*) AS item_count
                    FROM stage10_daily_prices
                    GROUP BY instrument_id, trade_date, provider, price_variant, currency_segment
                    HAVING item_count > 1
                )
                """
            ).fetchone()[0]
        )
        invalid_current = int(
            connection.execute(
                """
                SELECT count(*)
                FROM stage10_daily_prices AS current
                LEFT JOIN stage10_daily_price_versions AS version
                  ON version.version_id = current.current_version_id
                WHERE version.version_id IS NULL
                """
            ).fetchone()[0]
        )
        broken_chain = int(
            connection.execute(
                """
                SELECT count(*)
                FROM stage10_daily_price_versions AS version
                LEFT JOIN stage10_daily_price_versions AS previous
                  ON previous.version_id = version.supersedes_version_id
                WHERE (version.correction_sequence = 1 AND version.supersedes_version_id IS NOT NULL)
                   OR (version.correction_sequence > 1 AND (
                        previous.version_id IS NULL
                        OR previous.instrument_id != version.instrument_id
                        OR previous.trade_date != version.trade_date
                        OR previous.correction_sequence != version.correction_sequence - 1
                   ))
                """
            ).fetchone()[0]
        )
        maximum_sequence = int(
            connection.execute(
                "SELECT coalesce(max(correction_sequence), 0) FROM stage10_daily_price_versions"
            ).fetchone()[0]
        )
    checks: dict[str, object] = {
        "broken_supersession_count": broken_chain,
        "duplicate_current_count": duplicate_current,
        "foreign_key_violation_count": len(foreign_keys),
        "integrity_check": list(integrity_rows),
        "invalid_current_pointer_count": invalid_current,
        "maximum_correction_sequence": maximum_sequence,
    }
    if integrity_rows != ("ok",) or foreign_keys or duplicate_current or invalid_current or broken_chain:
        raise _error("/fixture/integrity", "integrity", "Stage 12B fixture integrity checks failed")
    return counts, checks


def _assert_unchanged(before: dict[str, object], after: dict[str, object], *, label: str) -> None:
    if before != after:
        raise _error("/fixture", "no_write", f"Stage 12B {label} mutated fixture state")


def _initial_publish_with_order_proof(
    collector: Stage12BIncrementalCollector,
    request: Stage12BFixtureRequest,
    response: Stage12BFixtureResponse,
) -> tuple[object, tuple[str, ...], int]:
    """Instrument only the fixture collector's preparation/publish boundary."""

    events: list[str] = []
    transport = _InjectedFixtureTransport(response, events=events)
    original_parse = _stage12_incremental.Stage12BIncrementalCollector._parse_response
    original_write = _stage12_incremental.Stage12BIncrementalCollector._write_publication
    original_connect = _stage12_incremental.sqlite3.connect

    def observed_parse(self: object, *args: object, **kwargs: object) -> object:
        events.append("parser_started")
        result = original_parse(self, *args, **kwargs)
        events.append("parser_completed")
        return result

    def observed_write(self: object, *args: object, **kwargs: object) -> object:
        events.append("write_transaction_open")
        return original_write(self, *args, **kwargs)

    def observed_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        events.append("market_connection_opened")
        return original_connect(*args, **kwargs)

    class ObservedLock:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._lock = StoreWriteLock(*args, **kwargs)

        def __enter__(self) -> object:
            events.append("physical_market_lock_acquired")
            return self._lock.__enter__()

        def __exit__(self, *args: object) -> None:
            self._lock.__exit__(*args)

    with (
        patch.object(_stage12_incremental.Stage12BIncrementalCollector, "_parse_response", observed_parse),
        patch.object(_stage12_incremental.Stage12BIncrementalCollector, "_write_publication", observed_write),
        patch.object(_stage12_incremental, "StoreWriteLock", ObservedLock),
        patch.object(_stage12_incremental.sqlite3, "connect", observed_connect),
    ):
        delivered = transport.receive(request)
        prepared = collector.prepare(request, delivered)
        events.append("candidate_prepared")
        receipt = collector.publish(prepared)
    expected = (
        "fixture_response_delivered",
        "parser_started",
        "parser_completed",
        "candidate_prepared",
        "physical_market_lock_acquired",
        "market_connection_opened",
        "write_transaction_open",
    )
    if tuple(events) != expected:
        raise _error("/fixture/ordering", "lock_boundary", "Stage 12B preparation crossed a lock or transaction boundary")
    return receipt, tuple(events), transport.calls


def _prepare_and_publish(
    collector: Stage12BIncrementalCollector,
    request: Stage12BFixtureRequest,
    response: Stage12BFixtureResponse,
) -> object:
    return collector.publish(collector.prepare(request, response))


def _receipt_summary(receipt: object) -> dict[str, object]:
    outcome = getattr(receipt, "outcome", None)
    written = getattr(receipt, "written_versions", None)
    if outcome not in {"published", "unchanged", "empty"} or isinstance(written, bool) or not isinstance(written, int):
        raise _error("/fixture/receipt", "receipt", "Stage 12B fixture receipt is invalid")
    return {"outcome": outcome, "written_versions": written}


def run_clean_stage12b_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    return _run_fixture_rehearsal(project_root=project_root, work_root=work_root)


def compare_clean_stage12b_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require two distinct isolated Stage 12B fixture roots."""

    project = _require_project_root(project_root)
    first = _require_fixture_root(first_work_root, project=project)
    second = _require_fixture_root(second_work_root, project=project)
    if first == second:
        raise _conflict(
            "/work_root",
            "distinct",
            "Stage 12B comparison requires two distinct physical fixture roots",
        )
    first_evidence = run_clean_stage12b_rebuild(
        project_root=project,
        work_root=first,
    )
    second_evidence = run_clean_stage12b_rebuild(
        project_root=project,
        work_root=second,
    )
    if dumps_strict(first_evidence) != dumps_strict(second_evidence):
        raise _error("/", "determinism", "Two Stage 12B fixture rehearsals produced different evidence")
    return first_evidence


__all__ = (
    "STAGE12B_CANONICAL_REGISTRY_SCHEMA_VERSION",
    "STAGE12B_CANONICAL_REGISTRY_VERSION",
    "STAGE12B_GATE_CONTRACT",
    "STAGE12B_GATE_VERSION",
    "compare_clean_stage12b_rebuilds",
    "run_clean_stage12b_rebuild",
    "run_stage12b_dry_run",
)

def _run_fixture_rehearsal(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    project = _require_project_root(project_root)
    root = _require_fixture_root(work_root, project=project)
    dry_run = _dry_run_evidence(project, root)
    registry, stage12a_scope, scope, _collector = _validate_static_contract(project)
    store_map, migration_heads, seed = _initialize_fixture_store(root, registry)
    baseline = mutation_fingerprint(store_map)
    collector = Stage12BIncrementalCollector(
        fixture_root=root,
        market_store=store_map.path(StoreRole.MARKET),
        scope=scope,
        stage12a_scope=stage12a_scope,
        stage12a_scope_source=project / "config" / "stage12_market_v1_scope.json",
    )
    initial_response = _fixture_response(_fixture_rows())
    initial, order, deliveries = _initial_publish_with_order_proof(
        collector, _fixture_request(), initial_response
    )
    if deliveries != 1:
        raise _error("/fixture/transport", "attempts", "Initial fixture transport call count drifted")
    initial_summary = _receipt_summary(initial)
    if initial_summary != {"outcome": "published", "written_versions": 3}:
        raise _error("/fixture/initial", "publication", "Initial Stage 12B fixture publication drifted")

    def deliver(
        request: Stage12BFixtureRequest,
        response: Stage12BFixtureResponse,
    ) -> object:
        nonlocal deliveries
        transport = _InjectedFixtureTransport(response)
        delivered = transport.receive(request)
        if transport.calls != 1 or len(transport.descriptors) != 1:
            raise _error("/fixture/transport", "attempts", "Fixture transport did not receive exactly one descriptor")
        deliveries += transport.calls
        return _prepare_and_publish(collector, request, delivered)

    before_replay = mutation_fingerprint(store_map)
    exact = deliver(_fixture_request(captured_at="2026-08-15T12:02:30Z"), initial_response)
    exact_summary = _receipt_summary(exact)
    if exact_summary != {"outcome": "unchanged", "written_versions": 0}:
        raise _error("/fixture/replay", "publication", "Exact replay was not a total no-write")
    _assert_unchanged(before_replay, mutation_fingerprint(store_map), label="exact replay")

    reordered = deliver(
        _fixture_request(captured_at="2026-08-15T12:02:45Z"),
        _fixture_response(list(reversed(_fixture_rows()))),
    )
    reordered_summary = _receipt_summary(reordered)
    if reordered_summary != {"outcome": "unchanged", "written_versions": 0}:
        raise _error("/fixture/reordered", "publication", "Reordered replay was not a total no-write")
    _assert_unchanged(before_replay, mutation_fingerprint(store_map), label="reordered replay")

    correction = deliver(
        _fixture_request(captured_at=_FIXED_CORRECTION_CAPTURED_AT),
        _fixture_response(_fixture_rows(correction=True)),
    )
    correction_summary = _receipt_summary(correction)
    if correction_summary != {"outcome": "published", "written_versions": 1}:
        raise _error("/fixture/correction", "publication", "Correction did not append one version")
    new_date = "2026-08-13"
    new = deliver(
        _fixture_request(
            from_date=new_date,
            to_date=new_date,
            session_dates=(new_date,),
            captured_at=_FIXED_NEW_DATE_CAPTURED_AT,
        ),
        _fixture_response(_fixture_rows(date_value=new_date)),
    )
    new_summary = _receipt_summary(new)
    if new_summary != {"outcome": "published", "written_versions": 1}:
        raise _error("/fixture/new_date", "publication", "New date did not append one version")
    before_empty = mutation_fingerprint(store_map)
    empty = deliver(
        _fixture_request(
            from_date="2026-08-14",
            to_date="2026-08-14",
            session_dates=("2026-08-14",),
            captured_at="2026-08-15T12:04:30Z",
        ),
        _fixture_response([]),
    )
    empty_summary = _receipt_summary(empty)
    if empty_summary != {"outcome": "empty", "written_versions": 0}:
        raise _error("/fixture/empty", "publication", "Empty fixture outcome drifted")
    _assert_unchanged(before_empty, mutation_fingerprint(store_map), label="empty response")

    def expect_rejection(
        label: str,
        expected_type: type[BaseException] | tuple[type[BaseException], ...],
        operation: object,
    ) -> str:
        before = mutation_fingerprint(store_map)
        calls_before = deliveries
        try:
            assert callable(operation)
            operation()
        except Exception as exc:
            if not isinstance(exc, expected_type):
                raise
        else:
            raise _error("/fixture/rejections", "rejected", f"Stage 12B {label} fixture was accepted")
        if deliveries != calls_before + 1:
            raise _error("/fixture/rejections", "attempts", f"Stage 12B {label} fixture made more than one attempt")
        _assert_unchanged(before, mutation_fingerprint(store_map), label=label)
        return label

    malformed = expect_rejection(
        "malformed_json",
        ValidationError,
        lambda: deliver(
            _fixture_request(captured_at="2026-08-15T12:05:00Z"),
            Stage12BFixtureResponse(200, "application/json", b"{", 1),
        ),
    )
    partial = expect_rejection(
        "partial_complete_batch",
        ValidationError,
        lambda: deliver(
            _fixture_request(captured_at="2026-08-15T12:05:10Z"),
            _fixture_response(_fixture_rows()[:-1]),
        ),
    )
    retry_classified = expect_rejection(
        "retry_classified_status_503",
        ValidationError,
        lambda: deliver(
            _fixture_request(captured_at="2026-08-15T12:05:20Z"),
            Stage12BFixtureResponse(503, "application/json", b'{"fixture":"retry-classified"}', 1),
        ),
    )
    raw_conflict = expect_rejection(
        "raw_bytes_different_descriptor",
        ConflictError,
        lambda: deliver(
            _fixture_request(
                from_date="2026-08-09",
                captured_at="2026-08-15T12:05:30Z",
            ),
            initial_response,
        ),
    )

    elapsed_bound = expect_rejection(
        "elapsed_seconds_bound",
        ResourceLimitError,
        lambda: deliver(
            _fixture_request(captured_at="2026-08-15T12:05:25Z"),
            _fixture_response(_fixture_rows(), elapsed_seconds=31),
        ),
    )
    backdated_rows = _fixture_rows(correction=True)
    backdated_rows[1].update(
        {"high": 108, "close": 107, "change": 5, "changePercent": 5, "vwap": 105}
    )
    backdated = expect_rejection(
        "backdated_changed_key",
        ConflictError,
        lambda: deliver(
            _fixture_request(captured_at="2026-08-15T12:02:59Z"),
            _fixture_response(backdated_rows),
        ),
    )

    class RefusedLock:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ConflictError("Injected Stage 12B fixture lock refusal")

    lock_date = "2026-08-15"
    def reject_lock() -> object:
        with patch.object(_stage12_incremental, "StoreWriteLock", RefusedLock):
            return deliver(
                _fixture_request(
                    from_date=lock_date,
                    to_date=lock_date,
                    session_dates=(lock_date,),
                    captured_at="2026-08-15T12:05:40Z",
                ),
                _fixture_response(_fixture_rows(date_value=lock_date)),
            )

    lock_failure = expect_rejection("lock_failure", ConflictError, reject_lock)
    commit_date = "2026-08-16"
    def reject_commit() -> object:
        with patch.object(
            collector,
            "_commit",
            side_effect=sqlite3.OperationalError("Injected Stage 12B fixture commit failure"),
        ):
            return deliver(
                _fixture_request(
                    from_date=commit_date,
                    to_date=commit_date,
                    session_dates=(commit_date,),
                    captured_at="2026-08-15T12:05:50Z",
                ),
                _fixture_response(_fixture_rows(date_value=commit_date)),
            )

    commit_failure = expect_rejection("commit_failure", sqlite3.OperationalError, reject_commit)
    final_fingerprint = mutation_fingerprint(store_map)
    for role in ("macro", "company", "news"):
        if baseline["stores"][role]["sha256"] != final_fingerprint["stores"][role]["sha256"]:  # type: ignore[index]
            raise _error("/fixture/nonmarket", "mutation", "Fixture publication mutated a nonmarket store")
    counts, integrity = _market_integrity(store_map)
    expected_counts = {
        "ingestion_runs": 4,
        "ingestion_artifacts": 3,
        "ingestion_snapshots": 3,
        "stage10_instruments": 1,
        "stage10_daily_price_captures": 3,
        "stage10_daily_price_versions": 5,
        "stage10_daily_prices": 4,
    }
    if counts != expected_counts or integrity["maximum_correction_sequence"] != 2:
        raise _error("/fixture/counts", "canonical", "Canonical fixture counts drifted")
    result: dict[str, object] = {
        **dry_run,
        "approval_status": "offline_fixture_rehearsal_validated_live_not_run",
        "execution": {
            "credential_environment_reads": 0,
            "fixture_transport_calls": deliveries,
            "live_provider_calls": 0,
            "network_calls": 0,
            "operational_sqlite_stores_opened": 0,
            "scheduler_actions": 0,
            "stage9_to_stage11_runner_calls": 0,
            "temporary_fixture_store_roles_initialized": 4,
        },
        "fixture_rehearsal": {
            "canonical_counts": counts,
            "integrity": integrity,
            "migration_heads": {role: list(head) for role, head in migration_heads.items()},
            "nonmarket_stores_unchanged": True,
            "operation_order": list(order),
            "preseeded_instrument": {"asset_type": seed["asset_type"], "symbol": seed["symbol"]},
            "publication": {
                "changed_overlap_correction": correction_summary,
                "empty": empty_summary,
                "exact_replay": exact_summary,
                "first_complete": initial_summary,
                "new_date": new_summary,
                "reordered_replay": reordered_summary,
            },
            "rejections": [
                malformed,
                partial,
                retry_classified,
                raw_conflict,
                elapsed_bound,
                backdated,
                lock_failure,
                commit_failure,
            ],
            "store_fingerprint_sha256": {
                role: final_fingerprint["stores"][role]["sha256"]  # type: ignore[index]
                for role in ("market", "macro", "company", "news")
            },
        },
    }
    serialized = dumps_strict(result)
    for forbidden in (str(project), str(root), "data/market.sqlite", "data/market_data.sqlite"):
        if forbidden in serialized:
            raise _error("/", "path_free", "Fixture evidence exposed a protected path")
    result["sha256"] = _sha256(result)
    dumps_strict(result)
    return result
