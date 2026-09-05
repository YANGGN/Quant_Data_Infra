"""Offline integration gate for Stage 12D no-transfer market adoption.

The static preflight in this module reads only the reviewed registry and scope
sources.  It deliberately does not inspect a SQLite file, an environment, a
receipt, a provider, or a project-local target.  The later fixture rehearsal
is intentionally isolated from the canonical proof runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

from .errors import ConflictError, Issue, ValidationError
from .json_codec import dumps_strict
from .market.stage12_scope import load_stage12_market_v1_scope
from .market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from .market.stage12c_scope import (
    load_stage12c_market_gap_v1_scope,
    require_stage12c_bindings,
)
from .market.stage12d_scope import (
    STAGE12D_SCOPE_CONTRACT,
    STAGE12D_SCOPE_VERSION,
    load_stage12d_market_no_transfer_adoption_scope,
    require_stage12d_market_no_transfer_adoption_scope,
)
from .migrations import initialize_all
from .operations.stage12d_market_proof import (
    Stage12DFileStamp,
    Stage12DFixtureHooks,
    Stage12DFixtureCompletion,
    Stage12DFixtureExpectations,
    Stage12DMarketProofRunner,
)
from .registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage12d_registry_profile,
)
from .stage1 import explicit_store_map
from .stores import StoreRole, StoreWriteLock, writer_connection

STAGE12D_GATE_CONTRACT = "quant_data.stage12d_no_transfer_offline_fixture_gate"
STAGE12D_GATE_VERSION = "1.0.0"

STAGE12D_STATIC_PREFLIGHT_CONTRACT = "quant_data.stage12d_static_preflight"
STAGE12D_STATIC_PREFLIGHT_VERSION = "1.0.0"
STAGE12D_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12D_REGISTRY_VERSION = "2.14.0"
STAGE12D_REGISTRY_SOURCE_SHA256 = (
    "c24398b35bc9fe9553dd85346dd3879abd6b802e1dd255ffb186d4ed9e1ea769"
)

_STAGE12A_SCOPE_PATH = Path("config/stage12_market_v1_scope.json")
_STAGE12B_SCOPE_PATH = Path("config/stage12b_incremental_market_v1_scope.json")
_STAGE12C_SCOPE_PATH = Path("config/stage12c_market_gap_v1_scope.json")
_STAGE12D_SCOPE_PATH = Path("config/stage12d_market_no_transfer_adoption_v1_scope.json")
_EXPECTED_DEFAULTS = {
    "market": "data/market.sqlite",
    "macro": "data/macro_data.sqlite",
    "company": "data/company_data.sqlite",
    "news": "data/news_data.sqlite",
}
_RETAINED_STAGE10_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
)
_FIXTURE_MIGRATION_AT = "2026-08-16T12:00:00Z"
_FIXTURE_CAPTURED_AT = "2026-08-16T12:01:00Z"
_FIXTURE_PROOF_AT = datetime(2026, 8, 16, 12, 2, tzinfo=timezone.utc)
_FIXTURE_EXPECTATION_ID = "fixture-stage12d-integration-expectations-v1"
_FIXTURE_COMPLETION_ID = "fixture-stage12d-integration-completion-v1"
_FIXTURE_PLAN_SHA256 = "d" * 64
_FIXTURE_SCOPE_SHA256 = "c" * 64
_FIXTURE_STAGE10_MIGRATION = "market:0010_stage10_market_history"


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _project_root(value: str | Path) -> Path:
    """Require a physical explicit project root without creating anything."""

    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _error("/project_root", "path", "An explicit Stage 12D project root is required")
    if isinstance(value, str) and not value.strip():
        raise _error("/project_root", "path", "An explicit Stage 12D project root is required")
    supplied = Path(value)
    if not supplied.is_absolute():
        raise _error("/project_root", "absolute", "Stage 12D project root must be absolute")
    try:
        if supplied.is_symlink():
            raise _error("/project_root", "symlink", "Stage 12D project root must be physical")
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("/project_root", "path", "Stage 12D project root is unavailable") from exc
    if not resolved.is_dir():
        raise _error("/project_root", "directory", "Stage 12D project root must be a directory")
    return resolved


def _is_within(value: Path, root: Path) -> bool:
    try:
        value.relative_to(root)
    except ValueError:
        return False
    return True


def _overlaps(first: Path, second: Path) -> bool:
    return _is_within(first, second) or _is_within(second, first)


def _fixture_conflict(pointer: str, rule: str, message: str) -> ConflictError:
    return ConflictError(message, issues=(Issue(pointer, rule, message),))


def _require_fixture_root(work_root: str | Path, *, project: Path) -> Path:
    """Accept only an empty physical temporary root for an offline rehearsal."""

    if isinstance(work_root, bool) or not isinstance(work_root, (str, Path)):
        raise _error("/work_root", "path", "An explicit Stage 12D fixture root is required")
    if isinstance(work_root, str) and not work_root.strip():
        raise _error("/work_root", "path", "An explicit Stage 12D fixture root is required")
    supplied = Path(work_root)
    if not supplied.is_absolute():
        raise _error("/work_root", "absolute", "Stage 12D fixture root must be absolute")
    try:
        if supplied.is_symlink():
            raise _fixture_conflict("/work_root", "symlink", "Stage 12D fixture root must be physical")
        root = supplied.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("/work_root", "path", "Stage 12D fixture root is unavailable") from exc
    if root != supplied:
        raise _fixture_conflict("/work_root", "alias", "Stage 12D fixture root must be canonical")
    try:
        temporary_root = Path("/tmp").resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error("/work_root", "temporary_root", "Stage 12D system temporary root is unavailable") from exc
    if root.parent != temporary_root:
        raise _fixture_conflict(
            "/work_root",
            "temporary_root",
            "Stage 12D fixture root must be a strict system-temporary child",
        )
    protected = (
        project,
        project / "data",
        project / "data" / "market.sqlite",
        project / "data" / "market_data.sqlite",
        _RETAINED_STAGE10_ROOT.resolve(strict=False),
    )
    if any(_overlaps(root, item.resolve(strict=False)) for item in protected):
        raise _fixture_conflict(
            "/work_root",
            "isolated_root",
            "Stage 12D fixture root overlaps a protected path",
        )
    if root.exists():
        try:
            if root.is_symlink() or not root.is_dir():
                raise _fixture_conflict(
                    "/work_root", "type", "Stage 12D fixture root must be a directory"
                )
            if any(root.iterdir()):
                raise _fixture_conflict(
                    "/work_root", "empty", "Stage 12D fixture root must be empty"
                )
        except OSError as exc:
            raise _error("/work_root", "path", "Stage 12D fixture root cannot be inspected") from exc
    elif not root.parent.is_dir() or root.parent.is_symlink():
        raise _fixture_conflict(
            "/work_root",
            "parent",
            "Stage 12D fixture root must have a physical existing parent",
        )
    return root


def validate_stage12d_static_preflight(project_root: str | Path) -> Mapping[str, object]:
    """Return path-free, source-only evidence for the Stage 12D authority gate.

    The caller supplies only an explicit project root.  Registry resolution is
    deliberately passed ``environment={}``, and this function has no SQLite,
    provider, credential, receipt, or filesystem-mutation capability.
    """

    project = _project_root(project_root)
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage12d_registry_profile(registry)
    if (
        registry.schema_version != STAGE12D_REGISTRY_SCHEMA_VERSION
        or registry.registry_version != STAGE12D_REGISTRY_VERSION
        or registry.source_sha256 != STAGE12D_REGISTRY_SOURCE_SHA256
        or len(registry.migrations) != 34
        or len(registry.datasets) != 48
        or len(registry.collectors) != 28
    ):
        raise _error("/registry", "revision", "Canonical Stage 12D registry drifted")
    defaults = {role: registry.store(role).default_path for role in _EXPECTED_DEFAULTS}
    if defaults != _EXPECTED_DEFAULTS:
        raise _error("/registry/stores", "default", "Canonical store defaults drifted")

    stage12a_scope = load_stage12_market_v1_scope(project / _STAGE12A_SCOPE_PATH)
    stage12b_scope = load_stage12b_incremental_market_v1_scope(
        project / _STAGE12B_SCOPE_PATH
    )
    stage12c_scope = load_stage12c_market_gap_v1_scope(project / _STAGE12C_SCOPE_PATH)
    require_stage12c_bindings(stage12c_scope, stage12a_scope, stage12b_scope)

    stage12d_scope = require_stage12d_market_no_transfer_adoption_scope(
        load_stage12d_market_no_transfer_adoption_scope(project / _STAGE12D_SCOPE_PATH)
    )
    if (
        stage12d_scope.contract != STAGE12D_SCOPE_CONTRACT
        or stage12d_scope.version != STAGE12D_SCOPE_VERSION
        or stage12d_scope.registry.revision != registry.registry_version
        or stage12d_scope.registry.schema_version != registry.schema_version
        or stage12d_scope.target.project_relative_path != defaults["market"]
        or stage12d_scope.stage12c.scope_file_sha256 != stage12c_scope.source_file_sha256
        or stage12d_scope.stage12c.scope_semantic_sha256 != stage12c_scope.manifest_sha256
        or stage12d_scope.stage12c.published_complete
        + stage12d_scope.stage12c.successful_empty
        + stage12d_scope.stage12c.authorized_http_402
        != stage12d_scope.stage12c.closed
    ):
        raise _error("/scope", "binding", "Stage 12D scope is not bound to reviewed Stage 12C authority")

    return {
        "contract": STAGE12D_STATIC_PREFLIGHT_CONTRACT,
        "version": STAGE12D_STATIC_PREFLIGHT_VERSION,
        "registry": {
            "revision": registry.registry_version,
            "schema": registry.schema_version,
            "source_sha256": registry.source_sha256,
        },
        "scope": {
            "contract": stage12d_scope.contract,
            "version": stage12d_scope.version,
            "manifest_sha256": stage12d_scope.manifest_sha256,
            "source_file_sha256": stage12d_scope.source_file_sha256,
        },
        "stage12c_binding": {
            "completion_receipt_sha256": stage12d_scope.stage12c.completion_receipt_sha256,
            "plan_sha256": stage12d_scope.stage12c.plan_sha256,
            "scope_file_sha256": stage12d_scope.stage12c.scope_file_sha256,
            "scope_semantic_sha256": stage12d_scope.stage12c.scope_semantic_sha256,
            "ledger": {
                "published_complete": stage12d_scope.stage12c.published_complete,
                "successful_empty": stage12d_scope.stage12c.successful_empty,
                "authorized_http_402": stage12d_scope.stage12c.authorized_http_402,
                "closed": stage12d_scope.stage12c.closed,
            },
        },
        "target_policy": {
            "project_relative_path": stage12d_scope.target.project_relative_path,
            "direct_regular_file": stage12d_scope.target.direct_regular_file,
            "non_symlink": stage12d_scope.target.non_symlink,
            "required_link_count": stage12d_scope.target.required_link_count,
            "wal_and_rollback_journal": stage12d_scope.target.wal_and_rollback_journal,
            "immutable_uri_query": stage12d_scope.access.connection_uri_query,
            "query_only": stage12d_scope.access.query_only,
        },
        "zero_effects": {
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "network_calls": 0,
            "sqlite_opens": 0,
            "subprocess_calls": 0,
        },
    }


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _error("/fixture/target", "read", "Stage 12D fixture target cannot be read") from exc
    return digest.hexdigest()


def _fixture_file_stamp(
    path: Path,
    *,
    label: str,
    absent_ok: bool,
) -> Stage12DFileStamp:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if absent_ok:
            return Stage12DFileStamp.absent()
        raise _error("/fixture/target", "missing", f"Stage 12D fixture {label} is missing")
    except OSError as exc:
        raise _error("/fixture/target", "stat", f"Stage 12D fixture {label} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _fixture_conflict(
            "/fixture/target",
            "direct_regular_file",
            f"Stage 12D fixture {label} must be a direct regular file",
        )
    return Stage12DFileStamp.from_stat(info)


def _fixture_target_stamps(target: Path) -> dict[str, Stage12DFileStamp]:
    stamps = {
        "journal": _fixture_file_stamp(
            target.with_name(target.name + "-journal"),
            label="rollback journal",
            absent_ok=True,
        ),
        "main": _fixture_file_stamp(target, label="market target", absent_ok=False),
        "shm": _fixture_file_stamp(
            target.with_name(target.name + "-shm"),
            label="shared-memory sidecar",
            absent_ok=True,
        ),
        "wal": _fixture_file_stamp(
            target.with_name(target.name + "-wal"),
            label="WAL sidecar",
            absent_ok=True,
        ),
    }
    if stamps["main"].link_count != 1:
        raise _fixture_conflict(
            "/fixture/target",
            "link_count",
            "Stage 12D fixture target must have one physical link",
        )
    for name in ("journal", "wal"):
        stamp = stamps[name]
        if stamp.exists and stamp.size != 0:
            raise _fixture_conflict(
                "/fixture/target",
                "quiet_sidecar",
                f"Stage 12D fixture {name} sidecar must be absent or zero bytes",
            )
    return stamps


def _create_fixture_root(root: Path) -> None:
    if root.exists():
        return
    try:
        root.mkdir(mode=0o700)
    except OSError as exc:
        raise _error("/work_root", "create", "Stage 12D fixture root cannot be created") from exc


def _seed_migration_fixture(root: Path, registry: object) -> Path:
    """Create the smallest lawful Stage 10 model under one temporary root."""

    data_root = root / "data"
    try:
        data_root.mkdir(mode=0o700)
    except FileExistsError:
        raise _fixture_conflict(
            "/work_root",
            "empty",
            "Stage 12D fixture data root already exists",
        )
    except OSError as exc:
        raise _error("/work_root", "create", "Stage 12D fixture data root cannot be created") from exc
    stores = explicit_store_map(data_root)
    target = stores.path(StoreRole.MARKET)
    if target != root / "data" / "market.sqlite":
        raise _error("/fixture/target", "path", "Stage 12D fixture target path drifted")
    try:
        migration_heads = initialize_all(
            stores,
            registry,  # type: ignore[arg-type]
            applied_at=_FIXTURE_MIGRATION_AT,
        )
    except Exception as exc:
        raise _error("/fixture/migrations", "initialize", "Stage 12D fixture migrations failed") from exc
    if migration_heads.get("market", ())[-1:] != (_FIXTURE_STAGE10_MIGRATION,):
        raise _error("/fixture/migrations", "market_head", "Stage 12D fixture market head drifted")

    run_id = "fixture-stage12d-run"
    instrument_id = "fixture-stage12d-aapl"
    capture_id = "fixture-stage12d-capture"
    artifact_id = "fixture-stage12d-artifact"
    snapshot_id = "fixture-stage12d-snapshot"
    scope_json = dumps_strict(
        {
            "fixture": "stage12d-no-transfer",
            "session_dates": ["2026-08-13", "2026-08-14"],
            "symbol": "AAPL",
        }
    )
    run_identity = _sha256_json({"fixture": "stage12d-run-v1"})
    instrument_identity = _sha256_json({"fixture": "stage12d-instrument-aapl-v1"})
    request_identity = _sha256_json({"fixture": "stage12d-request-aapl-v1"})
    response_identity = _sha256_json({"fixture": "stage12d-response-aapl-v1"})
    capture_identity = _sha256_json({"fixture": "stage12d-capture-aapl-v1"})
    try:
        with StoreWriteLock(target), writer_connection(stores, StoreRole.MARKET) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO ingestion_runs(
                        run_id, dataset_id, semantic_identity, command, scope_json, status,
                        started_at, fetched_count, written_count, code_version
                    ) VALUES (?, 'market.stage10.daily_prices', ?, 'fixture.stage12d', ?,
                              'running', ?, 2, 2, 'stage12d.fixture.v1')
                    """,
                    (run_id, run_identity, scope_json, _FIXTURE_CAPTURED_AT),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_instruments(
                        instrument_id, provider, provider_symbol, asset_type, display_name,
                        exchange_code, currency_segment, first_trade_date, identity_seed_sha256,
                        captured_at, captured_precision, run_id
                    ) VALUES (?, 'fmp', 'AAPL', 'equity', 'Stage 12D fixture AAPL', NULL,
                              'provider_native', NULL, ?, ?, 'datetime', ?)
                    """,
                    (instrument_id, instrument_identity, _FIXTURE_CAPTURED_AT, run_id),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_captures(
                        capture_id, dataset_id, provider, instrument_id, provider_symbol,
                        endpoint_path, scope_manifest_sha256, request_scope_json,
                        request_scope_sha256, response_sha256, response_bytes, http_status,
                        content_type, semantic_identity, completeness, artifact_id, snapshot_id,
                        captured_at, captured_precision, earliest_trade_date, latest_trade_date,
                        row_count, normalization_version, run_id
                    ) VALUES (
                        ?, 'market.stage10.source_evidence', 'fmp', ?, 'AAPL',
                        '/stable/historical-price-eod/full', ?, ?, ?, ?, X'5B5D', 200,
                        'application/json', ?, 'complete', ?, ?, ?, 'datetime',
                        '2026-08-13', '2026-08-14', 2, 'stage10.fmp.daily_price.v1', ?
                    )
                    """,
                    (
                        capture_id,
                        instrument_id,
                        _FIXTURE_SCOPE_SHA256,
                        scope_json,
                        request_identity,
                        response_identity,
                        capture_identity,
                        artifact_id,
                        snapshot_id,
                        _FIXTURE_CAPTURED_AT,
                        run_id,
                    ),
                )
                for source_row, trade_date in enumerate(
                    ("2026-08-13", "2026-08-14"),
                    start=1,
                ):
                    version_id = f"fixture-stage12d-version-{source_row}"
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_price_versions(
                            version_id, instrument_id, trade_date, provider, price_variant,
                            currency_segment, open_value, high_value, low_value, close_value,
                            volume, available_at, available_precision, captured_at,
                            captured_precision, correction_sequence, supersedes_version_id,
                            capture_id, artifact_id, snapshot_id, run_id, source_row
                        ) VALUES (?, ?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native',
                                  '1.00', '2.00', '1.00', '1.50', 10, ?, 'datetime', ?,
                                  'datetime', 1, NULL, ?, ?, ?, ?, ?)
                        """,
                        (
                            version_id,
                            instrument_id,
                            trade_date,
                            _FIXTURE_CAPTURED_AT,
                            _FIXTURE_CAPTURED_AT,
                            capture_id,
                            artifact_id,
                            snapshot_id,
                            run_id,
                            source_row,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_prices(
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', 'fmp_full_eod_v1', 'provider_native', ?)
                        """,
                        (instrument_id, trade_date, version_id),
                    )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
    except Exception as exc:
        raise _error("/fixture/seed", "insert", "Stage 12D fixture seed failed") from exc

    try:
        connection = sqlite3.connect(target, isolation_level=None)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=DELETE")
        finally:
            connection.close()
        os.chmod(target, 0o600)
    except (OSError, sqlite3.Error) as exc:
        raise _error("/fixture/target", "quiesce", "Stage 12D fixture target cannot be quiesced") from exc
    _fixture_target_stamps(target)
    return target


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Stage 12D fixture receipt write did not advance")
        view = view[written:]


def _write_fixture_completion(
    root: Path,
    scope: object,
    target: Path,
) -> Stage12DFixtureCompletion:
    completion_path = root / getattr(scope, "stage12c").completion_receipt_path
    try:
        completion_path.parent.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise _error(
            "/fixture/completion",
            "create",
            "Stage 12D fixture completion path cannot be created",
        ) from exc
    target_stamp = _fixture_file_stamp(target, label="market target", absent_ok=False)
    if target_stamp.size is None or target_stamp.mtime_ns is None:
        raise _error("/fixture/completion", "target", "Stage 12D fixture target stamp is incomplete")
    business_material: dict[str, object] = {
        "baseline_sha256": _sha256_json(
            {"fixture_id": _FIXTURE_COMPLETION_ID, "kind": "fixture_completion"}
        ),
        "final_target": {
            "byte_count": target_stamp.size,
            "identity_sha256": target_stamp.identity_sha256(),
            "mtime_ns": target_stamp.mtime_ns,
            "sha256": _sha256_file(target),
        },
        "final_target_checks": {
            "current_price_rows": 2,
            "daily_price_captures": 1,
            "foreign_key_violation_count": 0,
            "immutable_price_versions": 2,
            "integrity_check": "ok",
            "scoped_current_row_count": 2,
            "stage10_capture_duplicate_count": 0,
            "stage10_current_duplicate_count": 0,
            "stage10_current_pointer_anomaly_count": 0,
            "stage10_version_duplicate_count": 0,
        },
        "plan_sha256": _FIXTURE_PLAN_SHA256,
        "published_unit_count": 1,
        "result_sha256s": [_sha256_json({"fixture": "stage12d-result-v1"})],
        "scope_manifest_sha256": _FIXTURE_SCOPE_SHA256,
        "source_final": {
            "byte_count": 1,
            "identity_sha256": "2" * 64,
            "mtime_ns": 1,
            "sha256": "3" * 64,
        },
        "static_preflight_sha256": "4" * 64,
        "terminal_noncoverage_count": 0,
        "terminal_outcomes": [],
        "total_attempt_count": 1,
        "version": "1.0.0",
    }
    receipt = {
        "contract": "quant_data.stage12c_market_gap_completion",
        **business_material,
        "sha256": _sha256_json(business_material),
    }
    payload = dumps_strict(receipt).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not isinstance(no_follow, int) or no_follow == 0:
        raise _error("/fixture/completion", "no_follow", "Stage 12D fixture needs O_NOFOLLOW")
    descriptor: int | None = None
    try:
        descriptor = os.open(completion_path, flags | no_follow, 0o600)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except OSError as exc:
        raise _error(
            "/fixture/completion",
            "write",
            "Stage 12D fixture completion cannot be written",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return Stage12DFixtureCompletion(
        fixture_id=_FIXTURE_COMPLETION_ID,
        receipt_sha256=str(receipt["sha256"]),
        plan_sha256=_FIXTURE_PLAN_SHA256,
        scope_semantic_sha256=_FIXTURE_SCOPE_SHA256,
        published_complete=1,
        successful_empty=0,
        authorized_http_402=0,
        closed=1,
    )


@dataclass(frozen=True, slots=True)
class _FixtureRehearsal:
    evidence: dict[str, object]
    semantic_proof_sha256: str
    completion_receipt_sha256: str
    target_identity_sha256: str
    receipt_sha256s: tuple[str, str]


def _run_fixture_rehearsal(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> _FixtureRehearsal:
    project = _project_root(project_root)
    root = _require_fixture_root(work_root, project=project)
    static_preflight = validate_stage12d_static_preflight(project)
    _create_fixture_root(root)
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage12d_registry_profile(registry)
    scope = require_stage12d_market_no_transfer_adoption_scope(
        load_stage12d_market_no_transfer_adoption_scope(project / _STAGE12D_SCOPE_PATH)
    )
    target = _seed_migration_fixture(root, registry)
    completion = _write_fixture_completion(root, scope, target)
    expectations = Stage12DFixtureExpectations(
        fixture_id=_FIXTURE_EXPECTATION_ID,
        current_rows=2,
        version_rows=2,
        captures=1,
        stage12c_current_rows=2,
        stage12c_version_rows=2,
        stage12c_captures=1,
        aapl_current_rows=2,
    )
    before = _fixture_target_stamps(target)
    query_labels: list[str] = []
    connect_count = 0

    def before_connect() -> None:
        nonlocal connect_count
        connect_count += 1

    def before_query(label: str) -> None:
        query_labels.append(label)

    runner = Stage12DMarketProofRunner(
        project_root=root,
        scope=scope,
        expectations=expectations,
        completion=completion,
        utcnow=lambda: _FIXTURE_PROOF_AT,
        hooks=Stage12DFixtureHooks(
            before_connect=before_connect,
            before_query=before_query,
        ),
    )
    first = runner.run()
    second = runner.run()
    after = _fixture_target_stamps(target)
    if before != after:
        raise _error(
            "/fixture/proof",
            "stamp_neutrality",
            "Stage 12D fixture proof changed target or sidecar stamps",
        )
    if (
        first.proof_ordinal != 1
        or second.proof_ordinal != 2
        or first.receipt_sha256 == second.receipt_sha256
        or first.semantic_proof_sha256 != second.semantic_proof_sha256
        or first.completion_receipt_sha256 != completion.receipt_sha256
        or second.completion_receipt_sha256 != completion.receipt_sha256
        or first.target_identity_sha256 != second.target_identity_sha256
    ):
        raise _error("/fixture/proof", "serial", "Stage 12D fixture proof sequence drifted")
    try:
        runner.run()
    except ConflictError:
        third_proof_rejected = True
    else:
        third_proof_rejected = False
    if not third_proof_rejected or before != _fixture_target_stamps(target):
        raise _error("/fixture/proof", "limit", "Stage 12D fixture third proof limit drifted")
    expected_labels = (
        "relations",
        "integrity",
        "foreign_keys",
        "counts",
        "duplicates",
        "pointers",
        "stage12c_scope",
        "aapl",
    )
    if connect_count != 2 or tuple(query_labels) != expected_labels * 2:
        raise _error("/fixture/proof", "hooks", "Stage 12D fixture hook wiring drifted")
    evidence = {
        "contract": STAGE12D_GATE_CONTRACT,
        "version": STAGE12D_GATE_VERSION,
        "static_preflight_sha256": _sha256_json(static_preflight),
        "fixture": {
            "immutable_uri_query": scope.access.connection_uri_query,
            "migration_head": _FIXTURE_STAGE10_MIGRATION,
            "proof_ordinals": [1, 2],
            "query_only": scope.access.query_only,
            "semantic_proof_sha256": first.semantic_proof_sha256,
            "serial_receipts_distinct": True,
            "target_sidecar_stamps_unchanged": True,
            "third_proof_rejected": True,
        },
    }
    return _FixtureRehearsal(
        evidence=evidence,
        semantic_proof_sha256=first.semantic_proof_sha256,
        completion_receipt_sha256=completion.receipt_sha256,
        target_identity_sha256=first.target_identity_sha256,
        receipt_sha256s=(first.receipt_sha256, second.receipt_sha256),
    )


def run_clean_stage12d_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Run one isolated migration-backed Stage 12D fixture rehearsal."""

    return _run_fixture_rehearsal(
        project_root=project_root,
        work_root=work_root,
    ).evidence


def compare_clean_stage12d_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    """Require two separate temporary roots and compare only semantic proof facts."""

    project = _project_root(project_root)
    first = _require_fixture_root(first_work_root, project=project)
    second = _require_fixture_root(second_work_root, project=project)
    if first == second:
        raise _fixture_conflict(
            "/work_root",
            "distinct",
            "Stage 12D comparison requires two distinct physical fixture roots",
        )
    first_result = _run_fixture_rehearsal(project_root=project, work_root=first)
    second_result = _run_fixture_rehearsal(project_root=project, work_root=second)
    if (
        first_result.semantic_proof_sha256 != second_result.semantic_proof_sha256
        or first_result.completion_receipt_sha256 == second_result.completion_receipt_sha256
        or first_result.target_identity_sha256 == second_result.target_identity_sha256
        or len(set((*first_result.receipt_sha256s, *second_result.receipt_sha256s))) != 4
        or dumps_strict(first_result.evidence) != dumps_strict(second_result.evidence)
    ):
        raise _error("/comparison", "semantic", "Stage 12D two-root fixture evidence drifted")
    return {
        **first_result.evidence,
        "fixture": {
            **first_result.evidence["fixture"],  # type: ignore[arg-type]
            "cross_root_completion_receipts_distinct": True,
            "cross_root_semantic_proof_equal": True,
            "cross_root_target_identities_distinct": True,
        },
    }



__all__ = (
    "STAGE12D_GATE_CONTRACT",
    "STAGE12D_GATE_VERSION",
    "STAGE12D_REGISTRY_SCHEMA_VERSION",
    "STAGE12D_REGISTRY_SOURCE_SHA256",
    "STAGE12D_REGISTRY_VERSION",
    "STAGE12D_STATIC_PREFLIGHT_CONTRACT",
    "STAGE12D_STATIC_PREFLIGHT_VERSION",
    "compare_clean_stage12d_rebuilds",
    "run_clean_stage12d_rebuild",
    "validate_stage12d_static_preflight",
)
