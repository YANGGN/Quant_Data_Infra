"""Static authority preflight for the bounded Stage 12C market gap run.

This module reads only the reviewed registry and scope manifests.  It does not
open a SQLite database, inspect credentials, use the network, create a path, or
publish a fact.  The operations runner must call this gate before credential
access and transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from typing import Any
import tempfile
from unittest.mock import patch

from .errors import ConflictError, Issue, StoreUnavailableError, ValidationError
from .json_codec import dumps_strict, loads_strict
from .market.stage12_scope import load_stage12_market_v1_scope
from .market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from .market.stage12c_incremental import (
    Stage12CIncrementalCollector,
    Stage12CLiveRequest,
    Stage12CLiveResponse,
)
from .market.stage12c_scope import (
    STAGE12C_COLLECTOR_ID,
    load_stage12c_market_gap_v1_scope,
    require_stage12c_bindings,
    stage12c_ordered_symbols,
)
from .migrations import initialize_all
from .operations.stage12c_market_gap import (
    APPROVED_PRIVATE_RELATIVE_ROOT,
    STAGE12C_ENDPOINT_PATH,
    STAGE12C_EXPECTED_SESSIONS,
    STAGE12C_EXPECTED_SYMBOL_COUNT,
    STAGE12C_MAX_RESPONSE_BYTES,
    STAGE12C_MINIMUM_REQUEST_INTERVAL_SECONDS,
    STAGE12C_TIMEOUT_SECONDS,
    Stage12CBaselineExpectation,
    Stage12CMarketGapRunner,
    Stage12CTransportResponse,
)
from .registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage12c_registry_profile,
    stage12d_registry_profile,
)
from .stores import StoreMap, StoreRole, StoreWriteLock, writer_connection


STAGE12C_STATIC_PREFLIGHT_CONTRACT = "quant_data.stage12c_static_preflight"
STAGE12C_STATIC_PREFLIGHT_VERSION = "1.0.0"
STAGE12C_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12C_REGISTRY_VERSION = "2.14.0"
STAGE12C_REGISTRY_SOURCE_SHA256 = (
    "c24398b35bc9fe9553dd85346dd3879abd6b802e1dd255ffb186d4ed9e1ea769"
)
STAGE12B_REGISTRY_SOURCE_SHA256 = (
    "b39057d548d6ced6e7c0663ffafbee7e0c16c88a94baced584ff6949da7766f6"
)

_STAGE12A_SCOPE_PATH = Path("config/stage12_market_v1_scope.json")
_STAGE12B_SCOPE_PATH = Path("config/stage12b_incremental_market_v1_scope.json")
_STAGE12C_SCOPE_PATH = Path("config/stage12c_market_gap_v1_scope.json")
_EXPECTED_DEFAULTS = {
    "market": "data/market.sqlite",
    "macro": "data/macro_data.sqlite",
    "company": "data/company_data.sqlite",
    "news": "data/news_data.sqlite",
}
_EXPECTED_INPUTS = (
    "market.stage10.instruments",
    "market.stage10.daily_prices",
)
_EXPECTED_OUTPUTS = (
    "market.stage10.source_evidence",
    "market.stage10.daily_prices",
)


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _project_root(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _error("/project_root", "path", "An explicit Stage 12C project root is required")
    if isinstance(value, str) and not value.strip():
        raise _error("/project_root", "path", "An explicit Stage 12C project root is required")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise _error("/project_root", "absolute", "Stage 12C project root must be absolute")
    try:
        if supplied.is_symlink():
            raise _error("/project_root", "symlink", "Stage 12C project root must be physical")
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("/project_root", "path", "Stage 12C project root is unavailable") from exc
    if not resolved.is_dir():
        raise _error("/project_root", "directory", "Stage 12C project root must be a directory")
    return resolved


def _collector(registry: object) -> Mapping[str, object]:
    matches = tuple(
        item
        for item in registry.collectors
        if isinstance(item, Mapping) and item.get("id") == STAGE12C_COLLECTOR_ID
    )
    if len(matches) != 1:
        raise _error(
            "/registry/collectors",
            "collector",
            "Canonical registry must declare exactly one Stage 12C collector",
        )
    return matches[0]


def validate_stage12c_live_static_preflight(
    project_root: str | Path,
) -> Mapping[str, object]:
    """Validate all source-only Stage 12C authority before operational preflight."""

    project = _project_root(project_root)
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage12d_registry_profile(registry)
    if (
        registry.schema_version != STAGE12C_REGISTRY_SCHEMA_VERSION
        or registry.registry_version != STAGE12C_REGISTRY_VERSION
        or registry.source_sha256 != STAGE12C_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 34
        or len(registry.datasets) != 48
        or len(registry.collectors) != 28
    ):
        raise _error("/registry", "revision", "Canonical Stage 12C registry drifted")
    defaults = {
        role: registry.store(role).default_path for role in _EXPECTED_DEFAULTS
    }
    if defaults != _EXPECTED_DEFAULTS:
        raise _error("/registry/stores", "default", "Canonical store defaults drifted")

    collector = _collector(registry)
    if (
        tuple(collector["input_datasets"]) != _EXPECTED_INPUTS
        or tuple(collector["output_datasets"]) != _EXPECTED_OUTPUTS
        or tuple(collector["configuration_env"]) != ("FMP_API_KEY",)
        or collector["network"] is not True
    ):
        raise _error("/registry/collectors", "collector", "Stage 12C collector drifted")

    stage12b_registry = stage12c_registry_profile(registry)
    if (
        stage12b_registry.registry_version != "2.13.0"
        or stage12b_registry.schema_version != "1.8.0"
        or stage12b_registry.source_sha256 != STAGE12B_REGISTRY_SOURCE_SHA256
        or len(stage12b_registry.collectors) != 27
    ):
        raise _error(
            "/registry/historical_stage12b",
            "projection",
            "Exact Stage 12B registry projection drifted",
        )

    stage12a_scope = load_stage12_market_v1_scope(project / _STAGE12A_SCOPE_PATH)
    stage12b_scope = load_stage12b_incremental_market_v1_scope(
        project / _STAGE12B_SCOPE_PATH
    )
    stage12c_scope = load_stage12c_market_gap_v1_scope(
        project / _STAGE12C_SCOPE_PATH
    )
    require_stage12c_bindings(stage12c_scope, stage12a_scope, stage12b_scope)
    ordered_symbols = stage12c_ordered_symbols(
        stage12c_scope, stage12a_scope, stage12b_scope
    )

    return {
        "contract": STAGE12C_STATIC_PREFLIGHT_CONTRACT,
        "version": STAGE12C_STATIC_PREFLIGHT_VERSION,
        "registry": {
            "schema": registry.schema_version,
            "revision": registry.registry_version,
            "source_sha256": registry.source_sha256,
            "historical_stage12b_source_sha256": stage12b_registry.source_sha256,
        },
        "scope": {
            "contract": stage12c_scope.contract,
            "version": stage12c_scope.version,
            "manifest_sha256": stage12c_scope.manifest_sha256,
            "source_file_sha256": stage12c_scope.source_file_sha256,
        },
        "roster": {
            "count": len(ordered_symbols),
            "sha256": stage12a_scope.roster_sha256,
            "first_symbol": ordered_symbols[0],
        },
        "request": {
            "from": stage12c_scope.request_plan.from_date,
            "to": stage12c_scope.request_plan.to_date,
            "expected_session_dates": list(stage12c_scope.request_plan.session_dates),
            "logical_unit_count": len(ordered_symbols),
            "max_attempts": stage12c_scope.collector.max_requests,
        },
        "paths": {
            "market_store": stage12c_scope.source_target.project_store_path,
            "private_state_root": stage12c_scope.source_target.private_state_root,
        },
        "effects": {
            "sqlite_opens": 0,
            "credential_reads": 0,
            "network_requests": 0,
            "filesystem_mutations": 0,
        },
    }


__all__ = (
    "STAGE12B_REGISTRY_SOURCE_SHA256",
    "STAGE12C_REGISTRY_SCHEMA_VERSION",
    "STAGE12C_REGISTRY_SOURCE_SHA256",
    "STAGE12C_REGISTRY_VERSION",
    "STAGE12C_STATIC_PREFLIGHT_CONTRACT",
    "STAGE12C_STATIC_PREFLIGHT_VERSION",
    "validate_stage12c_live_static_preflight",
)


STAGE12C_OFFLINE_GATE_CONTRACT = "quant_data.stage12c_market_gap_offline_fixture_gate"
'''
STAGE12C_OFFLINE_GATE_VERSION = "1.0.0"
STAGE12C_FIXTURE_ROOT_POLICY = "explicit_absent_or_empty_isolated_fixture_only"
_FIXTURE_TIME = "2026-08-15T00:00:00Z"
_FIXTURE_CREDENTIAL = "stage12c-fixture-credential"
_RETAINED_CANDIDATE_PARENT = Path("/home/volatility/quant-data-nonprod")


def _offline_conflict(pointer: str, rule: str, message: str) -> ConflictError:
    return ConflictError(message, issues=(Issue(pointer, rule, message),))


def _offline_sha256(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _offline_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise _error("/fixture", "path", "Stage 12C fixture file is unavailable") from exc
    return digest.hexdigest()


def _is_within(value: Path, root: Path) -> bool:
    try:
        value.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _require_fixture_root(work_root: str | Path, *, project: Path) -> Path:
    """Accept only an initially absent or empty physical fixture root."""

    if isinstance(work_root, bool) or not isinstance(work_root, (str, Path)):
        raise _error("/work_root", "path", "An explicit Stage 12C fixture root is required")
    supplied = Path(work_root)
    if not supplied.is_absolute():
        raise _error("/work_root", "absolute", "Stage 12C fixture root must be absolute")
    if supplied.is_symlink():
        raise _offline_conflict("/work_root", "symlink", "Stage 12C fixture root must be physical")
    try:
        root = supplied.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("/work_root", "path", "Stage 12C fixture root is unavailable") from exc
    if root != supplied:
        raise _offline_conflict("/work_root", "alias", "Stage 12C fixture root must be canonical")
    protected = (
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        relative = root.relative_to(temporary_root)
    except ValueError as exc:
        raise _offline_conflict(
            "/work_root",
            "temporary_root",
            "Stage 12C fixture root must be a strict system-temporary child",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise _error(
            "/work_root",
            "temporary_root",
            "Stage 12C system temporary root is unavailable",
        ) from exc
    if not relative.parts:
        raise _offline_conflict(
            "/work_root",
            "temporary_root",
            "Stage 12C fixture root must be a strict system-temporary child",
        )
        project,
        project / "data",
        project / "data" / "market.sqlite",
        _RETAINED_CANDIDATE_PARENT.resolve(strict=False),
    )
    broad = (Path(root.anchor).resolve(strict=False), Path("/tmp").resolve(strict=False))
    if root in broad or any(_overlaps(root, item.resolve(strict=False)) for item in protected):
        raise _offline_conflict(
            "/work_root",
            "isolated_root",
            "Stage 12C fixture root overlaps a protected project or retained path",
        )
    try:
        if root.exists():
            if not root.is_dir():
                raise _offline_conflict("/work_root", "directory", "Stage 12C fixture root must be a directory")
            if next(root.iterdir(), None) is not None:
                raise _offline_conflict("/work_root", "empty", "Stage 12C fixture root must be empty")
        elif not root.parent.is_dir() or root.parent.is_symlink():
            raise _offline_conflict("/work_root", "parent", "Stage 12C fixture root parent is unsafe")
    except ConflictError:
        raise
    except OSError as exc:
        raise _error("/work_root", "path", "Stage 12C fixture root cannot be inspected") from exc
    return root


@dataclass(frozen=True, slots=True)
class _FixtureContext:
    project: Path
    source: Path
    target: Path
    stores: StoreMap
    collector: Stage12CIncrementalCollector
    baseline: Stage12CBaselineExpectation
    migration_heads: Mapping[str, tuple[str, ...]]
    stage12a_scope: object
    stage12b_scope: object
    stage12c_scope: object


def _checkpoint_fixture_store(path: Path) -> None:
    try:
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise _error("/fixture", "sqlite", "Stage 12C fixture store cannot be checkpointed") from exc


def _seed_frozen_instruments(stores: StoreMap, roster: tuple[object, ...]) -> None:
    """Seed the reviewed identity prerequisite only in a temporary fixture store."""

    run_id = "stage12c-offline-fixture-seed"
    with StoreWriteLock(stores.path(StoreRole.MARKET)), writer_connection(
        stores, StoreRole.MARKET
    ) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, 'market.stage10.instruments', ?, 'stage12c.fixture.seed',
                          '{}', 'running', ?, 'stage12c.fixture.v1')
                """,
                (run_id, _offline_sha256({"fixture": "stage12c", "roster": "frozen"}), _FIXTURE_TIME),
            )
            rows: list[tuple[str, str, str, str, str, str]] = []
            for item in roster:
                symbol = getattr(item, "symbol", None)
                asset_type = getattr(item, "asset_type", None)
                if not isinstance(symbol, str) or not isinstance(asset_type, str):
                    raise _error("/fixture/roster", "shape", "Frozen fixture roster is invalid")
                rows.append(
                    (
                        "stage12c-fixture-" + symbol,
                        symbol,
                        asset_type,
                        hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
                        _FIXTURE_TIME,
                        run_id,
                    )
                )
            connection.executemany(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type, display_name,
                    exchange_code, currency_segment, first_trade_date,
                    identity_seed_sha256, captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', ?, ?, NULL, NULL, 'provider_native', NULL,
                          ?, ?, 'datetime', ?)
                """,
                rows,
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def _fixture_rows(symbol: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for offset, trade_date in enumerate(STAGE12C_EXPECTED_SESSIONS):
        opening = 100 + offset * 10
        closing = opening + 1
        rows.append(
            {
                "symbol": symbol,
                "date": trade_date,
                "open": opening,
                "high": closing + 1,
                "low": opening - 1,
                "close": closing,
                "volume": 1_000 + offset,
                "change": closing - opening,
                "changePercent": 1,
                "vwap": opening,
            }
        )
    return rows


@dataclass(slots=True)
class _FixtureClock:
    now_value: datetime = datetime(2026, 8, 15, tzinfo=timezone.utc)
    monotonic_value: float = 0.0

    def now(self) -> datetime:
        return self.now_value

    def monotonic(self) -> float:
        return self.monotonic_value

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("Stage 12C fixture clock cannot sleep negatively")
        self.monotonic_value += seconds
        self.now_value += timedelta(seconds=seconds)


class _FixtureEnvironment(dict[str, str]):
    def __init__(self) -> None:
        super().__init__({"FMP_API_KEY": _FIXTURE_CREDENTIAL})
        self.reads = 0

    def get(self, key: str, default: object = None) -> object:
        self.reads += 1
        return super().get(key, default)


class _FixtureTransport:
    """A deterministic in-memory seam; it cannot make a network request."""

    def __init__(self, *, terminal_symbol: str) -> None:
        self._terminal_symbol = terminal_symbol
        self.calls: list[dict[str, str]] = []

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse:
        if (
            path != STAGE12C_ENDPOINT_PATH
            or set(query) != {"symbol", "from", "to"}
            or query.get("from") != STAGE12C_EXPECTED_SESSIONS[0]
            or query.get("to") != STAGE12C_EXPECTED_SESSIONS[1]
            or set(headers) != {"apikey"}
            or timeout_seconds != STAGE12C_TIMEOUT_SECONDS
            or max_bytes != STAGE12C_MAX_RESPONSE_BYTES
        ):
            raise AssertionError("Stage 12C fixture transport received an unreviewed request")
        symbol = query["symbol"]
        self.calls.append({"from": query["from"], "symbol": symbol, "to": query["to"]})
        body = b"[]" if symbol == self._terminal_symbol else dumps_strict(_fixture_rows(symbol)).encode("utf-8")
        return Stage12CTransportResponse(
            status=200,
            media_type="application/json",
            body=body,
            elapsed_seconds=Decimal("0.1"),
        )


def _fixture_counts(path: Path, terminal_symbol: str) -> dict[str, int]:
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            counts = {
                "daily_price_captures": int(connection.execute("SELECT count(*) FROM stage10_daily_price_captures").fetchone()[0]),
                "daily_price_versions": int(connection.execute("SELECT count(*) FROM stage10_daily_price_versions").fetchone()[0]),
                "daily_prices": int(connection.execute("SELECT count(*) FROM stage10_daily_prices").fetchone()[0]),
                "terminal_symbol_current_rows": int(
                    connection.execute(
                        """
                        SELECT count(*) FROM stage10_daily_prices AS price
                        JOIN stage10_instruments AS instrument
                          ON instrument.instrument_id = price.instrument_id
                        WHERE instrument.provider = 'fmp' AND instrument.provider_symbol = ?
                        """,
                        (terminal_symbol,),
                    ).fetchone()[0]
                ),
            }
    except sqlite3.Error as exc:
        raise _error("/fixture", "sqlite", "Stage 12C fixture counts cannot be read") from exc
    return counts


def _fixture_sidecars(project: Path) -> dict[str, object]:
    root = project / APPROVED_PRIVATE_RELATIVE_ROOT
    directories = ("intents", "bodies", "responses", "results", "resume")
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise _error("/fixture", "sidecar_mode", "Stage 12C fixture root mode drifted")
    counts: dict[str, int] = {}
    for name in directories:
        directory = root / name
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise _error(
                "/fixture",
                "sidecar_mode",
                "Stage 12C fixture sidecar directory mode drifted",
            )
        entries = tuple(directory.iterdir())
        if any(stat.S_IMODE(entry.stat().st_mode) != 0o600 for entry in entries):
            raise _error(
                "/fixture",
                "sidecar_mode",
                "Stage 12C fixture sidecar file mode drifted",
            )
        counts[name] = len(entries)
    files = tuple(path for path in root.rglob("*") if path.is_file())
    credential = _FIXTURE_CREDENTIAL.encode("utf-8")
    if any(credential in path.read_bytes() for path in files):
        raise _error("/fixture", "credential", "Stage 12C fixture sidecar retained a credential")
    results = tuple(sorted((root / "results").iterdir()))
    if not results:
        raise _error("/fixture", "sidecars", "Stage 12C fixture has no result sidecar")
    first_result = loads_strict(results[0].read_bytes())
    if not isinstance(first_result, Mapping) or not isinstance(first_result.get("captured_at"), str):
        raise _error("/fixture", "sidecars", "Stage 12C fixture result sidecar is invalid")
    return {
        "all_files_mode_0600": True,
        "directory_mode_0700": True,

def _fixture_body(symbol: str, *, corrected: bool = False) -> bytes:
    rows = _fixture_rows(symbol)
    if corrected:
        first = dict(rows[0])
        first["close"] = int(first["close"]) + 2
        first["high"] = int(first["close"]) + 1
        first["change"] = int(first["close"]) - int(first["open"])
        rows[0] = first
    return dumps_strict(rows).encode("utf-8")


def _fixture_response(
    symbol: str,
    *,
    captured_at: str,
    corrected: bool = False,
) -> Stage12CLiveResponse:
    return Stage12CLiveResponse(
        status=200,
        media_type="application/json",
        body=_fixture_body(symbol, corrected=corrected),
        captured_at=captured_at,
        elapsed_seconds=Decimal("0.1"),
    )


def _create_fixture_context(
    *,
    work_root: Path,
    name: str,
    source_project: Path,
    collector_type: type[Stage12CIncrementalCollector] = Stage12CIncrementalCollector,
) -> _FixtureContext:
    """Build one wholly temporary Stage 12C baseline equivalent."""

    fixture_root = work_root / name
    project = fixture_root / "project"
    data = project / "data"
    source = fixture_root / "retained" / "market.sqlite"
    try:
        data.mkdir(parents=True, mode=0o700)
        source.parent.mkdir(mode=0o700)
    except OSError as exc:
        raise _error("/fixture", "mkdir", "Stage 12C fixture project cannot be created") from exc

    stores = StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )
    registry = load_registry(
        source_project / CANONICAL_REGISTRY_PATH,
        project_root=source_project,
        environment={},
    )
    try:
        initialize_all(stores, registry, applied_at=_FIXTURE_TIME)
    except (sqlite3.Error, ValidationError) as exc:
        raise _error("/fixture", "schema", "Stage 12C fixture stores cannot be initialized") from exc

    stage12a_scope = load_stage12_market_v1_scope(
        source_project / _STAGE12A_SCOPE_PATH
    )
    stage12b_scope = load_stage12b_incremental_market_v1_scope(
        source_project / _STAGE12B_SCOPE_PATH
    )
    stage12c_scope = load_stage12c_market_gap_v1_scope(
        source_project / _STAGE12C_SCOPE_PATH
    )
    require_stage12c_bindings(stage12c_scope, stage12a_scope, stage12b_scope)
    _seed_frozen_instruments(stores, tuple(stage12a_scope.roster))
    target = stores.path(StoreRole.MARKET)
    _checkpoint_fixture_store(target)
    try:
        shutil.copy2(target, source)
    except OSError as exc:
        raise _error("/fixture", "baseline_copy", "Stage 12C fixture baseline cannot be created") from exc

    target_digest = _offline_file_sha256(target)
    source_digest = _offline_file_sha256(source)
    if source_digest != target_digest:
        raise _error("/fixture", "baseline_copy", "Stage 12C fixture baseline digest drifted")
    baseline_counts = _fixture_counts(target, "__no_terminal_symbol__")
    baseline = Stage12CBaselineExpectation(
        sha256=target_digest,
        current_price_rows=baseline_counts["daily_prices"],
        immutable_price_versions=baseline_counts["daily_price_versions"],
        daily_price_captures=baseline_counts["daily_price_captures"],
    )
    collector = collector_type(
        project_root=project,
        market_store=target,
        scope=stage12c_scope,
        stage12a_scope=stage12a_scope,
        stage12b_scope=stage12b_scope,
    )
    return _FixtureContext(
        project=project,
        source=source,
        target=target,
        stores=stores,
        collector=collector,
        baseline=baseline,
        migration_heads={},
        stage12a_scope=stage12a_scope,
        stage12b_scope=stage12b_scope,
        stage12c_scope=stage12c_scope,
    )


def _fixture_static_preflight(
    evidence: Mapping[str, object],
    expected_project: Path,
) -> Mapping[str, object]:
    def invoke(project_root: str | Path) -> Mapping[str, object]:
        if Path(project_root) != expected_project:
            raise _error("/fixture/static_preflight", "project", "Fixture runner project drifted")
        return dict(evidence)

    return invoke


def _fixture_runner(
    context: _FixtureContext,
    *,
    static_evidence: Mapping[str, object],
    transport: object,
    environment: Mapping[str, str],
    clock: _FixtureClock,
    collector: Stage12CIncrementalCollector | None = None,
) -> Stage12CMarketGapRunner:
    return Stage12CMarketGapRunner(
        project_root=context.project,
        retained_source=context.source,
        scope=context.stage12c_scope,
        stage12a_scope=context.stage12a_scope,
        stage12b_scope=context.stage12b_scope,
        collector=context.collector if collector is None else collector,
        transport=transport,
        environment=environment,
        static_preflight=_fixture_static_preflight(static_evidence, context.project),
        baseline=context.baseline,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        utcnow=clock.now,
    )


def _ordered_fixture_symbols(context: _FixtureContext) -> tuple[str, ...]:
    symbols = tuple(
        stage12c_ordered_symbols(
            context.stage12c_scope,
            context.stage12a_scope,
            context.stage12b_scope,
        )
    )
    if (
        len(symbols) != STAGE12C_EXPECTED_SYMBOL_COUNT
        or symbols[0] != "AAPL"
        or tuple(symbols[1:]) != tuple(sorted(symbols[1:]))
    ):
        raise _error("/fixture/order", "order", "Stage 12C fixture roster order drifted")
    return symbols


class _SystemicFixtureTransport:
    """A local one-response seam for an exact AAPL systemic failure."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse:
        if (
            path != STAGE12C_ENDPOINT_PATH
            or dict(query) != {
                "symbol": "AAPL",
                "from": STAGE12C_EXPECTED_SESSIONS[0],
                "to": STAGE12C_EXPECTED_SESSIONS[1],
            }
            or set(headers) != {"apikey"}
            or timeout_seconds != STAGE12C_TIMEOUT_SECONDS
            or max_bytes != STAGE12C_MAX_RESPONSE_BYTES
        ):
            raise AssertionError("Stage 12C systemic fixture request drifted")
        self.calls.append("AAPL")
        return Stage12CTransportResponse(
            status=503,
            media_type="application/json",
            body=b'{"Error Message":"fixture systemic failure"}',
            elapsed_seconds=Decimal("0.1"),
        )


class _AmbiguousFixtureTransport:
    """A local seam that fails after durable intent and before response spool."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse:
        del path, headers, timeout_seconds, max_bytes
        self.calls.append(str(query.get("symbol")))
        raise StoreUnavailableError("fixture transport interrupted after durable intent")


class _CrashAfterPublishCollector(Stage12CIncrementalCollector):
    """Inject one local crash after canonical publication but before a result sidecar."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._crash_once = True

    def publish(self, prepared: object) -> object:
        receipt = super().publish(prepared)
        if self._crash_once:
            self._crash_once = False
            raise StoreUnavailableError("fixture crash after publication")
        return receipt


def _assert_source_neutral(context: _FixtureContext, before: str, *, label: str) -> None:
    if _offline_file_sha256(context.source) != before:
        raise _error("/fixture/source", "neutral", f"Stage 12C {label} mutated its retained fixture source")


def _run_main_rehearsal(
    *,
    work_root: Path,
    source_project: Path,
    static_evidence: Mapping[str, object],
) -> tuple[_FixtureContext, dict[str, object]]:
    context = _create_fixture_context(
        work_root=work_root,
        name="main",
        source_project=source_project,
    )
    symbols = _ordered_fixture_symbols(context)
    terminal_symbol = symbols[-1]
    source_before = _offline_file_sha256(context.source)
    clock = _FixtureClock()
    transport = _FixtureTransport(terminal_symbol=terminal_symbol)
    environment = _FixtureEnvironment()
    runner = _fixture_runner(
        context,
        static_evidence=static_evidence,
        transport=transport,
        environment=environment,
        clock=clock,
    )
    report = runner.run()
    calls = tuple(call["symbol"] for call in transport.calls)
    if (
        report.completion != "complete"
        or report.requests_issued != len(symbols)
        or report.published_unit_count != len(symbols) - 1
        or report.terminal_noncoverage_count != 1
        or calls != symbols
        or calls[0] != "AAPL"
        or clock.monotonic_value < len(symbols) - 1
        or environment.reads != 1
    ):
        raise _error("/fixture/main", "runner", "Stage 12C fixture run did not follow the reviewed plan")
    counts = _fixture_counts(context.target, terminal_symbol)
    expected_count = len(symbols) - 1
    if counts != {
        "daily_price_captures": expected_count,
        "daily_price_versions": expected_count * 2,
        "daily_prices": expected_count * 2,
        "terminal_symbol_current_rows": 0,
    }:
        raise _error("/fixture/main", "terminal_no_write", "Stage 12C terminal noncoverage wrote facts")
    target_before_replay = _offline_file_sha256(context.target)
    replay = runner.run()
    if (
        replay.requests_issued != 0
        or tuple(call["symbol"] for call in transport.calls) != symbols
        or _offline_file_sha256(context.target) != target_before_replay
    ):
        raise _error("/fixture/main", "semantic_replay", "Stage 12C semantic replay wrote or requested")
    _assert_source_neutral(context, source_before, label="main rehearsal")
    sidecars = _fixture_sidecars(context.project)
    return context, {
        "aapl_first": True,
        "aapl_strict_two_session_response": True,
        "expected_session_dates": list(STAGE12C_EXPECTED_SESSIONS),
        "full_request_count": len(calls),
        "ordered_remaining_symbols": True,
        "published_complete_count": report.published_unit_count,
        "semantic_replay_requests": replay.requests_issued,
        "semantic_replay_zero_write": True,
        "terminal_noncoverage_count": report.terminal_noncoverage_count,
        "terminal_noncoverage_zero_facts": True,
        "temporary_store_counts": counts,
        "sidecars": sidecars,
        "source_neutral": True,
    }


def _exercise_correction_invariant(context: _FixtureContext) -> dict[str, object]:
    request = Stage12CLiveRequest(
        symbol="AAPL",
        from_date=STAGE12C_EXPECTED_SESSIONS[0],
        to_date=STAGE12C_EXPECTED_SESSIONS[1],
    )
    same_before = _offline_file_sha256(context.target)
    unchanged = context.collector.publish(
        context.collector.prepare(
            request,
            _fixture_response(
                "AAPL",
                captured_at="2026-08-16T00:00:00.000000Z",
            ),
        )
    )
    if (
        getattr(unchanged, "outcome", None) != "unchanged"
        or getattr(unchanged, "written_versions", None) != 0
        or _offline_file_sha256(context.target) != same_before
    ):
        raise _error("/fixture/correction", "semantic_replay", "Stage 12C collector replay wrote data")

    before_rejected = _offline_file_sha256(context.target)
    try:
        context.collector.publish(
            context.collector.prepare(
                request,
                _fixture_response(
                    "AAPL",
                    corrected=True,
                    captured_at="2026-08-15T00:00:00.000000Z",
                ),
            )
        )
    except ConflictError:
        pass
    else:
        raise _error("/fixture/correction", "later_time", "Stage 12C accepted a non-later correction")
    if _offline_file_sha256(context.target) != before_rejected:
        raise _error("/fixture/correction", "rollback", "Rejected correction changed the fixture store")

    corrected = context.collector.publish(
        context.collector.prepare(
            request,
            _fixture_response(
                "AAPL",
                corrected=True,
                captured_at="2026-08-16T00:00:00.000000Z",
            ),
        )
    )
    if (
        getattr(corrected, "outcome", None) != "published"
        or getattr(corrected, "written_versions", None) != 2
    ):
        raise _error("/fixture/correction", "later_time", "Stage 12C later correction did not publish two versions")
    return {
        "later_changed_capture_published": True,
        "non_later_changed_capture_rejected": True,
        "rejected_correction_rollback_zero_write": True,
        "semantic_replay_zero_write": True,
    }


def _exercise_prepared_identity(
    *,
    work_root: Path,
    source_project: Path,
) -> dict[str, object]:
    context = _create_fixture_context(
        work_root=work_root,
        name="prepared-identity",
        source_project=source_project,
    )
    request = Stage12CLiveRequest(
        symbol="AAPL",
        from_date=STAGE12C_EXPECTED_SESSIONS[0],
        to_date=STAGE12C_EXPECTED_SESSIONS[1],
    )
    prepared = context.collector.prepare(
        request,
        _fixture_response("AAPL", captured_at="2026-08-15T00:00:00.000000Z"),
    )
    other = Stage12CIncrementalCollector(
        project_root=context.project,
        market_store=context.target,
        scope=context.stage12c_scope,
        stage12a_scope=context.stage12a_scope,
        stage12b_scope=context.stage12b_scope,
    )
    try:
        other.publish(prepared)
    except ValidationError:
        pass
    else:
        raise _error("/fixture/prepared", "owner", "Stage 12C accepted a foreign prepared publication")

    source_before = _offline_file_sha256(context.source)
    replacement = context.target.with_name("market.replacement.sqlite")
    shutil.copy2(context.target, replacement)
    os.replace(replacement, context.target)
    try:
        context.collector.publish(prepared)
    except (ConflictError, ValidationError):
        pass
    else:
        raise _error("/fixture/prepared", "store_identity", "Stage 12C accepted a replaced fixture target")
    _assert_source_neutral(context, source_before, label="prepared identity check")
    return {
        "foreign_prepared_rejected": True,
        "replaced_store_rejected": True,
        "source_neutral": True,
    }


def _exercise_systemic_failure(
    *,
    work_root: Path,
    source_project: Path,
    static_evidence: Mapping[str, object],
) -> dict[str, object]:
    context = _create_fixture_context(
        work_root=work_root,
        name="systemic-failure",
        source_project=source_project,
    )
    source_before = _offline_file_sha256(context.source)
    target_before = _offline_file_sha256(context.target)
    transport = _SystemicFixtureTransport()
    runner = _fixture_runner(
        context,
        static_evidence=static_evidence,
        transport=transport,
        environment=_FixtureEnvironment(),
        clock=_FixtureClock(),
    )
    try:
        runner.run()
    except StoreUnavailableError:
        pass
    else:
        raise _error("/fixture/failure", "systemic_stop", "Stage 12C fixture accepted HTTP 503")
    if (
        transport.calls != ["AAPL"]
        or _offline_file_sha256(context.target) != target_before
    ):
        raise _error("/fixture/failure", "rollback", "Stage 12C systemic failure wrote or retried")
    try:
        runner.run()
    except StoreUnavailableError:
        pass
    else:
        raise _error("/fixture/failure", "replay", "Stage 12C systemic spool resumed")
    if transport.calls != ["AAPL"]:
        raise _error("/fixture/failure", "retry", "Stage 12C systemic failure retried")
    _assert_source_neutral(context, source_before, label="systemic failure")
    return {
        "aapl_systemic_stop": True,
        "retry_count": 0,
        "target_rollback_zero_write": True,
        "source_neutral": True,
    }


def _exercise_ambiguous_and_replay(
    *,
    work_root: Path,
    source_project: Path,
    static_evidence: Mapping[str, object],
) -> dict[str, object]:
    ambiguous = _create_fixture_context(
        work_root=work_root,
        name="ambiguous",
        source_project=source_project,
    )
    ambiguous_source = _offline_file_sha256(ambiguous.source)
    ambiguous_target = _offline_file_sha256(ambiguous.target)
    interrupted = _AmbiguousFixtureTransport()
    runner = _fixture_runner(
        ambiguous,
        static_evidence=static_evidence,
        transport=interrupted,
        environment=_FixtureEnvironment(),
        clock=_FixtureClock(),
    )
    try:
        runner.run()
    except StoreUnavailableError:
        pass
    else:
        raise _error("/fixture/journal", "ambiguous", "Stage 12C fixture did not stop interrupted transport")
    try:
        runner.run()
    except ConflictError:
        pass
    else:
        raise _error("/fixture/journal", "ambiguous", "Stage 12C fixture retried ambiguous intent")
    if (
        interrupted.calls != ["AAPL"]
        or _offline_file_sha256(ambiguous.target) != ambiguous_target
    ):
        raise _error("/fixture/journal", "ambiguous", "Ambiguous request wrote or retried")
    _assert_source_neutral(ambiguous, ambiguous_source, label="ambiguous request")

    crash = _create_fixture_context(
        work_root=work_root,
        name="local-replay",
        source_project=source_project,
        collector_type=_CrashAfterPublishCollector,
    )
    crash_source = _offline_file_sha256(crash.source)
    crash_transport = _FixtureTransport(terminal_symbol="__never__")
    crashing_runner = _fixture_runner(
        crash,
        static_evidence=static_evidence,
        transport=crash_transport,
        environment=_FixtureEnvironment(),
        clock=_FixtureClock(),
    )
    try:
        crashing_runner.run()
    except StoreUnavailableError:
        pass
    else:
        raise _error("/fixture/journal", "crash", "Stage 12C fixture did not expose post-publication crash")
    if tuple(call["symbol"] for call in crash_transport.calls) != ("AAPL",):
        raise _error("/fixture/journal", "crash", "Post-publication crash issued an extra request")
    target_after_crash = _offline_file_sha256(crash.target)
    replay_transport = _FixtureTransport(terminal_symbol="__never__")
    normal_collector = Stage12CIncrementalCollector(
        project_root=crash.project,
        market_store=crash.target,
        scope=crash.stage12c_scope,
        stage12a_scope=crash.stage12a_scope,
        stage12b_scope=crash.stage12b_scope,
    )
    replay_runner = _fixture_runner(
        crash,
        static_evidence=static_evidence,
        transport=replay_transport,
        environment={},
        clock=_FixtureClock(),
        collector=normal_collector,
    )
    try:
        replay_runner.run()
    except ValidationError:
        pass
    else:
        raise _error("/fixture/journal", "replay", "Stage 12C fixture unexpectedly issued beyond local replay")
    if (
        replay_transport.calls
        or _offline_file_sha256(crash.target) != target_after_crash
    ):
        raise _error("/fixture/journal", "replay", "Stage 12C local spool replay wrote or transported")
    _assert_source_neutral(crash, crash_source, label="local spool replay")
    return {
        "ambiguous_intent_stops_without_retry": True,
        "local_spool_replay_no_transport": True,
        "local_spool_semantic_no_write": True,
        "source_neutral": True,
    }


def _assert_path_free(
    evidence: Mapping[str, object],
    *,
    project: Path,
    work_root: Path,
) -> None:
    serialized = dumps_strict(dict(evidence))
    forbidden = (
        str(project),
        str(work_root),
        "/home/volatility/quant-data-nonprod",
    )
    if any(value and value in serialized for value in forbidden):
        raise _error("/", "path_free", "Stage 12C offline evidence exposed a physical path")


def run_clean_stage12c_rehearsal(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Run one deterministic Stage 12C rehearsal against temporary fixture stores."""

    project = _project_root(project_root)
    root = _require_fixture_root(work_root, project=project)
    static_evidence = validate_stage12c_live_static_preflight(project)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        if not root.is_dir():
            raise _offline_conflict("/work_root", "directory", "Stage 12C fixture root is unavailable")
    except OSError as exc:
        raise _error("/work_root", "mkdir", "Stage 12C fixture root cannot be created") from exc
    try:
        os.chmod(root, 0o700)
    except OSError as exc:
        raise _error("/work_root", "mode", "Stage 12C fixture root mode cannot be set") from exc

    main_context, main = _run_main_rehearsal(
        work_root=root,
        source_project=project,
        static_evidence=static_evidence,
    )
    correction = _exercise_correction_invariant(main_context)
    prepared = _exercise_prepared_identity(
        work_root=root,
        source_project=project,
    )
    failure = _exercise_systemic_failure(
        work_root=root,
        source_project=project,
        static_evidence=static_evidence,
    )
    journal = _exercise_ambiguous_and_replay(
        work_root=root,
        source_project=project,
        static_evidence=static_evidence,
    )
    evidence: dict[str, object] = {
        "approval_status": "offline_fixture_rehearsal_validated",
        "contract": STAGE12C_OFFLINE_GATE_CONTRACT,
        "contract_version": STAGE12C_OFFLINE_GATE_VERSION,
        "execution": {
            "credential_environment_reads": 0,
            "live_provider_calls": 0,
            "network_calls": 0,
            "operational_sqlite_stores_opened": 0,
            "scheduler_actions": 0,
            "temporary_fixture_store_roles_initialized": 4,
        },
        "fixture_rehearsal": {
            "correction_invariant": correction,
            "failure_rollback": failure,
            "journal": journal,
            "main": main,
            "prepared_identity": prepared,
        },
        "static_preflight": dict(static_evidence),
        "scope": {
            "manifest_sha256": main_context.stage12c_scope.manifest_sha256,
            "source_file_sha256": main_context.stage12c_scope.source_file_sha256,
            "stage12a_roster_sha256": main_context.stage12a_scope.roster_sha256,
            "stage12b_manifest_sha256": main_context.stage12b_scope.manifest_sha256,
        },
    }
    _assert_path_free(evidence, project=project, work_root=root)
    evidence["sha256"] = _offline_sha256(evidence)
    return evidence


def compare_clean_stage12c_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require two independent temporary rehearsals to yield exact path-free evidence."""

    project = _project_root(project_root)
    first = _require_fixture_root(first_work_root, project=project)
    second = _require_fixture_root(second_work_root, project=project)
    if _overlaps(first, second):
        raise _offline_conflict(
            "/second_work_root",
            "distinct",
            "Stage 12C comparison requires two distinct non-overlapping fixture roots",
        )
    first_evidence = run_clean_stage12c_rehearsal(
        project_root=project,
        work_root=first,
    )
    second_evidence = run_clean_stage12c_rehearsal(
        project_root=project,
        work_root=second,
    )
    if dumps_strict(first_evidence) != dumps_strict(second_evidence):
        raise _offline_conflict(
            "/comparison",
            "determinism",
            "Stage 12C isolated rehearsals produced different evidence",
        )
    _assert_path_free(first_evidence, project=project, work_root=first)
    _assert_path_free(second_evidence, project=project, work_root=second)
    return first_evidence

        "file_counts": counts,
        "first_captured_at": first_result["captured_at"],
        "no_fixture_credential_persisted": True,
    }
'''
# The interrupted implementation above is retained as inert recovery context.
# The executable Stage 12C gate below is deliberately small and fixture-only.

STAGE12C_OFFLINE_GATE_CONTRACT = "quant_data.stage12c_market_gap_offline_fixture_gate"
STAGE12C_OFFLINE_GATE_VERSION = "1.0.0"
STAGE12C_FIXTURE_ROOT_POLICY = "explicit_absent_or_empty_isolated_fixture_only"
STAGE12C_STATIC_PREFLIGHT_CONTRACT = "quant_data.stage12c_static_preflight"
STAGE12C_STATIC_PREFLIGHT_VERSION = "1.0.0"
STAGE12C_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12C_REGISTRY_VERSION = "2.14.0"
STAGE12C_SCOPE_RAW_SHA256 = "24d8448c124cedb5deb745b50943291d955f05e7a95ac1d9747a7d51b6760226"
STAGE12C_SCOPE_SEMANTIC_SHA256 = "2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392"
_FIXTURE_CAPTURED_AT = "2026-08-15T00:00:00.000000Z"
_FIXTURE_APPLIED_AT = "2026-08-15T00:00:00Z"
_FIXTURE_CREDENTIAL = "stage12c-offline-fixture-key"
_RETAINED_PARENT = Path("/home/volatility/quant-data-nonprod")


def _gate_error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _gate_conflict(pointer: str, rule: str, message: str) -> ConflictError:
    return ConflictError(message, issues=(Issue(pointer or "/", rule, message),))


def _gate_digest(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _gate_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise _gate_error("/fixture", "path", "Stage 12C fixture file is unavailable") from exc
    return digest.hexdigest()


def _inside(value: Path, root: Path) -> bool:
    try:
        value.relative_to(root)
    except ValueError:
        return False
    return True


def _overlap(first: Path, second: Path) -> bool:
    return _inside(first, second) or _inside(second, first)


def _require_gate_project_root(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _gate_error("/project_root", "path", "An explicit Stage 12C project root is required")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise _gate_error("/project_root", "absolute", "Stage 12C project root must be absolute and physical")
    try:
        project = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _gate_error("/project_root", "path", "Stage 12C project root is unavailable") from exc
    if not project.is_dir():
        raise _gate_error("/project_root", "directory", "Stage 12C project root must be a directory")
    return project


def _static_material(project: Path) -> tuple[object, object, object, object, tuple[str, ...]]:
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage12d_registry_profile(registry)
    if (
        registry.schema_version != STAGE12C_REGISTRY_SCHEMA_VERSION
        or registry.registry_version != STAGE12C_REGISTRY_VERSION
    ):
        raise _gate_error("/registry", "revision", "Canonical Stage 12C registry drifted")
    stage12a_scope = load_stage12_market_v1_scope(
        project / "config" / "stage12_market_v1_scope.json"
    )
    stage12b_scope = load_stage12b_incremental_market_v1_scope(
        project / "config" / "stage12b_incremental_market_v1_scope.json"
    )
    stage12c_scope = load_stage12c_market_gap_v1_scope(
        project / "config" / "stage12c_market_gap_v1_scope.json"
    )
    require_stage12c_bindings(stage12c_scope, stage12a_scope, stage12b_scope)
    ordered = stage12c_ordered_symbols(stage12c_scope, stage12a_scope, stage12b_scope)
    if (
        stage12c_scope.source_file_sha256 != STAGE12C_SCOPE_RAW_SHA256
        or stage12c_scope.manifest_sha256 != STAGE12C_SCOPE_SEMANTIC_SHA256
        or len(ordered) != STAGE12C_EXPECTED_SYMBOL_COUNT
        or ordered[0] != stage12c_scope.request_plan.sentinel_symbol
        or registry.store("market").default_path
        != stage12c_scope.source_target.project_store_path
    ):
        raise _gate_error("/scope", "binding", "Stage 12C authority material drifted")
    return registry, stage12a_scope, stage12b_scope, stage12c_scope, ordered


def validate_stage12c_live_static_preflight(
    project_root: str | Path,
) -> Mapping[str, object]:
    """Read and validate only immutable Stage 12C authority material."""

    project = _require_gate_project_root(project_root)
    registry, stage12a_scope, stage12b_scope, scope, ordered = _static_material(project)
    return {
        "contract": STAGE12C_STATIC_PREFLIGHT_CONTRACT,
        "version": STAGE12C_STATIC_PREFLIGHT_VERSION,
        "registry": {
            "schema_version": registry.schema_version,
            "version": registry.registry_version,
            "source_sha256": registry.source_sha256,
        },
        "scope": {
            "contract": scope.contract,
            "version": scope.version,
            "raw_sha256": scope.source_file_sha256,
            "semantic_sha256": scope.manifest_sha256,
            "target_profile_id": scope.target_profile_id,
        },
        "authority_bindings": {
            "stage12a_manifest_sha256": stage12a_scope.manifest_sha256,
            "stage12a_roster_sha256": stage12a_scope.roster_sha256,
            "stage12b_manifest_sha256": stage12b_scope.manifest_sha256,
        },
        "plan": {
            "collector": {
                "handler": scope.collector.handler,
                "id": scope.collector.id,
                "max_bytes": scope.collector.max_bytes,
                "max_rows": scope.collector.max_rows,
                "max_seconds": scope.collector.max_seconds,
                "one_attempt_per_symbol": scope.collector.max_requests == 1,
                "normalization_version": scope.publication.normalization_version,
            },
            "request": {
                "endpoint_path": scope.request_plan.endpoint_path,
                "from": scope.request_plan.from_date,
                "to": scope.request_plan.to_date,
                "session_dates": list(scope.request_plan.session_dates),
                "query_parameters": ["symbol", "from", "to"],
                "symbols_per_request": 1,
                "symbol_count": len(ordered),
                "sentinel_symbol": scope.request_plan.sentinel_symbol,
                "remaining_symbol_order": "stage12a_frozen_roster_lexicographic_excluding_sentinel",
            },
            "response": {
                "media_type": "application/json",
                "complete_row_count": 2,
                "row_keys": list(scope.response.row_keys),
            },
            "target": {
                "canonical_market_default": scope.source_target.project_store_path,
                "private_state_root_label": scope.source_target.private_state_root,
                "retained_baseline_sha256": scope.source_target.retained_source_initial_sha256,
            },
        },
        "zero_effects": {
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "network_calls": 0,
            "sqlite_opens": 0,
            "subprocess_calls": 0,
        },
    }


def _require_fixture_root(value: str | Path, *, project: Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _gate_error("/work_root", "path", "An explicit Stage 12C fixture root is required")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise _gate_conflict("/work_root", "physical", "Stage 12C fixture root must be absolute and physical")
    try:
        root = supplied.resolve(strict=False)
        temporary = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _gate_error("/work_root", "path", "Stage 12C fixture root is unavailable") from exc
    if root != supplied or root == temporary or not _inside(root, temporary):
        raise _gate_conflict("/work_root", "temporary", "Stage 12C fixture root must be a strict system-temporary child")
    protected = (
        project,
        project / "data",
        project / "data" / "market.sqlite",
        _RETAINED_PARENT,
    )
    if any(_overlap(root, item) for item in protected):
        raise _gate_conflict("/work_root", "isolated", "Stage 12C fixture root overlaps a protected path")
    try:
        if root.exists():
            if not root.is_dir() or root.is_symlink():
                raise _gate_conflict("/work_root", "directory", "Stage 12C fixture root must be a physical directory")
            if next(root.iterdir(), None) is not None:
                raise _gate_conflict("/work_root", "empty", "Stage 12C fixture root must be absent or empty")
        elif not root.parent.is_dir() or root.parent.is_symlink():
            raise _gate_conflict("/work_root", "parent", "Stage 12C fixture root parent is unsafe")
    except OSError as exc:
        raise _gate_error("/work_root", "path", "Stage 12C fixture root cannot be inspected") from exc
    return root


@dataclass(frozen=True, slots=True)
class _FixtureContext:
    project: Path
    source: Path
    target: Path
    stores: StoreMap
    collector: Stage12CIncrementalCollector
    baseline: Stage12CBaselineExpectation
    stage12a_scope: object
    stage12b_scope: object
    stage12c_scope: object
    symbols: tuple[str, ...]


@dataclass(slots=True)
class _FixtureClock:
    now_value: datetime = datetime(2026, 8, 15, tzinfo=timezone.utc)
    monotonic_value: float = 0.0

    def now(self) -> datetime:
        return self.now_value

    def monotonic(self) -> float:
        return self.monotonic_value

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("Stage 12C fixture clock cannot move backwards")
        self.monotonic_value += seconds
        self.now_value += timedelta(seconds=seconds)


class _FixtureEnvironment(dict[str, str]):
    def __init__(self) -> None:
        super().__init__({"FMP_API_KEY": _FIXTURE_CREDENTIAL})
        self.reads = 0

    def get(self, key: str, default: object = None) -> object:
        self.reads += 1
        return super().get(key, default)


class _FixtureTransport:
    """In-memory fixture transport with exact reviewed request admission."""

    def __init__(self, symbols: tuple[str, ...]) -> None:
        self._symbols = symbols
        self.calls: list[tuple[str, str, str]] = []

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse:
        index = len(self.calls)
        if (
            index >= len(self._symbols)
            or path != STAGE12C_ENDPOINT_PATH
            or tuple(query) != ("symbol", "from", "to")
            or query["symbol"] != self._symbols[index]
            or query["from"] != STAGE12C_EXPECTED_SESSIONS[0]
            or query["to"] != STAGE12C_EXPECTED_SESSIONS[1]
            or set(headers) != {"apikey"}
            or timeout_seconds != STAGE12C_TIMEOUT_SECONDS
            or max_bytes != STAGE12C_MAX_RESPONSE_BYTES
        ):
            raise AssertionError("Stage 12C fixture transport request drifted")
        symbol = query["symbol"]
        self.calls.append((symbol, query["from"], query["to"]))
        rows = []
        for offset, trade_date in enumerate(STAGE12C_EXPECTED_SESSIONS):
            opening = 100 + offset
            rows.append(
                {
                    "symbol": symbol,
                    "date": trade_date,
                    "open": opening,
                    "high": opening + 2,
                    "low": opening - 1,
                    "close": opening + 1,
                    "volume": 1_000 + offset,
                    "change": 1,
                    "changePercent": 1,
                    "vwap": opening,
                }
            )
        return Stage12CTransportResponse(
            status=200,
            media_type="application/json",
            body=dumps_strict(rows).encode("utf-8"),
            elapsed_seconds=Decimal("0.1"),
        )


def _checkpoint_fixture_market(path: Path) -> None:
    try:
        with sqlite3.connect(path, timeout=5.0, isolation_level=None) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE").fetchone()
    except sqlite3.Error as exc:
        raise _gate_error("/fixture", "sqlite", "Stage 12C fixture market cannot be checkpointed") from exc


def _seed_fixture_roster(stores: StoreMap, roster: tuple[object, ...]) -> None:
    run_id = "stage12c-offline-fixture-seed"
    with writer_connection(stores, StoreRole.MARKET) as connection:
        connection.execute(
            """
            INSERT INTO ingestion_runs(
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, code_version
            ) VALUES (?, 'market.stage10.instruments', ?, 'stage12c.fixture.seed',
                      '{}', 'running', ?, 'stage12c.fixture.v1')
            """,
            (
                run_id,
                _gate_digest({"fixture": "stage12c", "roster": "frozen"}),
                _FIXTURE_APPLIED_AT,
            ),
        )
        rows: list[tuple[str, str, str, str, str, str]] = []
        for item in roster:
            symbol = getattr(item, "symbol", None)
            asset_type = getattr(item, "asset_type", None)
            if not isinstance(symbol, str) or not isinstance(asset_type, str):
                raise _gate_error("/fixture/roster", "shape", "Stage 12C fixture roster is invalid")
            rows.append(
                (
                    "stage12c-fixture-" + symbol,
                    symbol,
                    asset_type,
                    hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
                    _FIXTURE_APPLIED_AT,
                    run_id,
                )
            )
        connection.executemany(
            """
            INSERT INTO stage10_instruments(
                instrument_id, provider, provider_symbol, asset_type, display_name,
                exchange_code, currency_segment, first_trade_date,
                identity_seed_sha256, captured_at, captured_precision, run_id
            ) VALUES (?, 'fmp', ?, ?, NULL, NULL, 'provider_native', NULL,
                      ?, ?, 'datetime', ?)
            """,
            rows,
        )


def _create_fixture_context(*, project_source: Path, work_root: Path) -> _FixtureContext:
    root = work_root / "fixture"
    project = root / "project"
    data = project / "data"
    source = root / "retained-source.sqlite"
    try:
        data.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise _gate_error("/fixture", "mkdir", "Stage 12C fixture project cannot be created") from exc
    stores = StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )
    registry, stage12a_scope, stage12b_scope, stage12c_scope, symbols = _static_material(
        project_source
    )
    try:
        initialize_all(stores, registry, applied_at=_FIXTURE_APPLIED_AT)
        _seed_fixture_roster(stores, tuple(stage12a_scope.roster))
        _checkpoint_fixture_market(stores.path(StoreRole.MARKET))
        shutil.copyfile(stores.path(StoreRole.MARKET), source)
    except (OSError, sqlite3.Error, ValidationError) as exc:
        raise _gate_error("/fixture", "baseline", "Stage 12C fixture baseline cannot be created") from exc
    target = stores.path(StoreRole.MARKET)
    target_digest = _gate_file_digest(target)
    if _gate_file_digest(source) != target_digest:
        raise _gate_error("/fixture", "baseline", "Stage 12C fixture source and target differ")
    baseline = Stage12CBaselineExpectation(
        sha256=target_digest,
        current_price_rows=0,
        immutable_price_versions=0,
        daily_price_captures=0,
    )
    collector = Stage12CIncrementalCollector(
        project_root=project,
        market_store=target,
        scope=stage12c_scope,
        stage12a_scope=stage12a_scope,
        stage12b_scope=stage12b_scope,
    )
    return _FixtureContext(
        project=project,
        source=source,
        target=target,
        stores=stores,
        collector=collector,
        baseline=baseline,
        stage12a_scope=stage12a_scope,
        stage12b_scope=stage12b_scope,
        stage12c_scope=stage12c_scope,
        symbols=symbols,
    )


def _fixture_static_preflight(
    evidence: Mapping[str, object],
    expected_project: Path,
) -> object:
    def invoke(project_root: str | Path) -> Mapping[str, object]:
        if Path(project_root) != expected_project:
            raise _gate_error("/fixture/preflight", "project", "Stage 12C fixture project drifted")
        return dict(evidence)

    return invoke


def _fixture_runner(
    context: _FixtureContext,
    *,
    static_evidence: Mapping[str, object],
    transport: _FixtureTransport,
    environment: _FixtureEnvironment,
    clock: _FixtureClock,
) -> Stage12CMarketGapRunner:
    return Stage12CMarketGapRunner(
        project_root=context.project,
        retained_source=context.source,
        scope=context.stage12c_scope,
        stage12a_scope=context.stage12a_scope,
        stage12b_scope=context.stage12b_scope,
        collector=context.collector,
        transport=transport,
        environment=environment,
        static_preflight=_fixture_static_preflight(static_evidence, context.project),
        baseline=context.baseline,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        utcnow=clock.now,
    )


def _assert_path_free(evidence: Mapping[str, object], *, project: Path, root: Path) -> None:
    serialized = dumps_strict(dict(evidence))
    forbidden = (
        str(project),
        str(root),
        str(_RETAINED_PARENT),
        _FIXTURE_CREDENTIAL,
    )
    if any(value and value in serialized for value in forbidden):
        raise _gate_error("/", "path_free", "Stage 12C offline evidence exposed a path or credential")


def run_clean_stage12c_rehearsal(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Run one fully local Stage 12C rehearsal in an explicit temporary root."""

    project = _require_gate_project_root(project_root)
    root = _require_fixture_root(work_root, project=project)
    static_evidence = validate_stage12c_live_static_preflight(project)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise _gate_error("/work_root", "mkdir", "Stage 12C fixture root cannot be created") from exc
    context = _create_fixture_context(project_source=project, work_root=root)
    source_before = _gate_file_digest(context.source)
    clock = _FixtureClock()
    transport = _FixtureTransport(context.symbols)
    environment = _FixtureEnvironment()
    runner = _fixture_runner(
        context,
        static_evidence=static_evidence,
        transport=transport,
        environment=environment,
        clock=clock,
    )
    report = runner.run()
    calls = tuple(symbol for symbol, _from, _to in transport.calls)
    expected_calls = context.symbols
    if (
        report.completion != "complete"
        or report.requests_issued != len(expected_calls)
        or report.published_unit_count != len(expected_calls)
        or report.terminal_noncoverage_count != 0
        or calls != expected_calls
        or calls[0] != "AAPL"
        or any(
            from_date != STAGE12C_EXPECTED_SESSIONS[0]
            or to_date != STAGE12C_EXPECTED_SESSIONS[1]
            for _symbol, from_date, to_date in transport.calls
        )
        or environment.reads != 1
        or clock.monotonic_value < len(expected_calls) - 1
    ):
        raise _gate_error("/fixture/rehearsal", "runner", "Stage 12C fixture rehearsal drifted")
    target_before_replay = _gate_file_digest(context.target)
    replay = runner.run()
    if (
        replay.requests_issued != 0
        or len(transport.calls) != len(expected_calls)
        or _gate_file_digest(context.target) != target_before_replay
        or _gate_file_digest(context.source) != source_before
    ):
        raise _gate_error("/fixture/rehearsal", "replay", "Stage 12C fixture replay drifted")
    evidence: dict[str, object] = {
        "approval_status": "offline_fixture_rehearsal_validated_live_not_run",
        "contract": STAGE12C_OFFLINE_GATE_CONTRACT,
        "contract_version": STAGE12C_OFFLINE_GATE_VERSION,
        "execution": {
            "credential_environment_reads": 0,
            "live_provider_calls": 0,
            "network_calls": 0,
            "operational_sqlite_stores_opened": 0,
            "scheduler_actions": 0,
            "temporary_fixture_store_roles_initialized": 4,
        },
        "fixture_rehearsal": {
            "aapl_first": True,
            "all_symbols_closed": len(expected_calls),
            "expected_session_dates": list(STAGE12C_EXPECTED_SESSIONS),
            "injected_environment_reads": environment.reads,
            "injected_transport_calls": len(transport.calls),
            "ordered_remaining_symbols": True,
            "published_complete_count": report.published_unit_count,
            "semantic_replay_requests": replay.requests_issued,
            "semantic_replay_zero_write": True,
            "source_neutral": True,
            "two_sessions_per_request": True,
            "virtual_pacing_seconds": int(clock.monotonic_value),
        },
        "static_preflight": dict(static_evidence),
    }
    _assert_path_free(evidence, project=project, root=root)
    evidence["sha256"] = _gate_digest(evidence)
    return evidence


def compare_clean_stage12c_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require two distinct temporary Stage 12C rehearsals to agree exactly."""

    project = _require_gate_project_root(project_root)
    first = _require_fixture_root(first_work_root, project=project)
    second = _require_fixture_root(second_work_root, project=project)
    if _overlap(first, second):
        raise _gate_conflict(
            "/second_work_root",
            "distinct",
            "Stage 12C comparison requires two distinct non-overlapping fixture roots",
        )
    first_evidence = run_clean_stage12c_rehearsal(
        project_root=project,
        work_root=first,
    )
    second_evidence = run_clean_stage12c_rehearsal(
        project_root=project,
        work_root=second,
    )
    if dumps_strict(first_evidence) != dumps_strict(second_evidence):
        raise _gate_conflict(
            "/comparison",
            "determinism",
            "Stage 12C isolated fixture rehearsals produced different evidence",
        )
    _assert_path_free(first_evidence, project=project, root=first)
    _assert_path_free(second_evidence, project=project, root=second)
    return first_evidence


__all__ = (
    "STAGE12C_OFFLINE_GATE_CONTRACT",
    "STAGE12C_OFFLINE_GATE_VERSION",
    "STAGE12C_SCOPE_RAW_SHA256",
    "STAGE12C_SCOPE_SEMANTIC_SHA256",
    "compare_clean_stage12c_rebuilds",
    "run_clean_stage12c_rehearsal",
    "validate_stage12c_live_static_preflight",
)
