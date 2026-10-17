"""Bounded Stage 12C parser and publisher for one live FMP daily-price unit.

This module deliberately has no HTTP transport, credential lookup, scheduler,
or baseline-copy capability.  The operations lane owns durable request intent,
raw spooling, pacing, and response classification.  This domain lane accepts
only a durably captured complete response and publishes it through the
existing Stage 10 ``0010`` capture/version/current model.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from ..errors import ConflictError, Issue, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreWriteLock, stable_id
from .stage12_scope import Stage12MarketV1Scope
from .stage12b_scope import Stage12BIncrementalMarketScope
from .stage12c_scope import (
    STAGE12C_COLLECTOR_ID,
    STAGE12C_PRICE_VARIANT,
    Stage12CMarketGapV1Scope,
    require_stage12c_bindings,
)


_EVIDENCE_DATASET = "market.stage10.source_evidence"
_PRICE_DATASET = "market.stage10.daily_prices"
_PHYSICAL_CAPTURE_NORMALIZATION_VERSION = "stage10.fmp.daily_price.v1"
_ROW_KEYS = frozenset(
    {
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "change",
        "changePercent",
        "vwap",
    }
)
_REQUIRED_TABLES = frozenset(
    {
        "dataset_registry",
        "ingestion_runs",
        "ingestion_artifacts",
        "ingestion_snapshots",
        "ingestion_snapshot_artifacts",
        "ingestion_run_outputs",
        "stage10_instruments",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    }
)
_PREPARED: weakref.WeakKeyDictionary["PreparedStage12CPublication", "_PreparedState"] = weakref.WeakKeyDictionary()
_CANONICAL_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
_CANONICAL_MARKET_STORE = _CANONICAL_PROJECT_ROOT / "data" / "market.sqlite"
_CANONICAL_LIVE_CAPABILITY = object()
_FIXTURE_CONSTRUCTION_CAPABILITY = object()


def _issue(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_text(value: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _issue(pointer, "text", "Expected bounded nonempty trimmed text")
    return value


def _date_text(value: object, *, pointer: str) -> str:
    text = _require_text(value, pointer=pointer, maximum=10)
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise _issue(pointer, "date", "Expected an ISO calendar date")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise _issue(pointer, "date", "Expected an ISO calendar date") from exc
    return text


def _utc_datetime(value: object, *, pointer: str) -> datetime:
    text = _require_text(value, pointer=pointer, maximum=64)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _issue(pointer, "datetime", "Time must include an explicit offset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _issue(pointer, "datetime", "Time must include an explicit offset")
    return parsed.astimezone(timezone.utc)


def _captured_at_text(value: object) -> str:
    return _utc_datetime(value, pointer="/captured_at").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _number(value: object, *, pointer: str, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise _issue(pointer, "number", "Expected a finite JSON number")
    result = Decimal(value)
    if not result.is_finite():
        raise _issue(pointer, "finite", "Expected a finite JSON number")
    if nonnegative and result < 0:
        raise _issue(pointer, "nonnegative", "Expected a nonnegative JSON number")
    return result


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return "0" if value == 0 else text


def _volume(value: object, *, pointer: str) -> int:
    parsed = _number(value, pointer=pointer, nonnegative=True)
    if parsed != parsed.to_integral_value():
        raise _issue(pointer, "integral", "Volume must be an integral JSON number")
    result = int(parsed)
    if result > 9_223_372_036_854_775_807:
        raise _issue(pointer, "range", "Volume exceeds SQLite signed integer range")
    return result


@dataclass(frozen=True, slots=True)
class Stage12CLiveRequest:
    """One exact manual Stage 12C live request descriptor.

    It contains no credential, host, URL, headers, or caller-selected path.
    The operations lane records this descriptor before it starts transport.
    """

    symbol: str
    from_date: str
    to_date: str


@dataclass(frozen=True, slots=True)
class Stage12CLiveResponse:
    """One already-received response; the collector does not make a request."""

    status: int
    media_type: str
    body: bytes
    captured_at: str
    elapsed_seconds: int | float | Decimal


@dataclass(frozen=True, slots=True)
class Stage12CPublicationReceipt:
    outcome: str
    semantic_identity: str | None
    capture_id: str | None
    written_versions: int


class PreparedStage12CPublication:
    """Opaque parsed work bound to one collector and physical target store."""

    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _Row:
    symbol: str
    trade_date: str
    open_value: Decimal
    high_value: Decimal
    low_value: Decimal
    close_value: Decimal
    volume: int
    change: Decimal
    change_percent: Decimal
    vwap: Decimal

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "change": self.change,
            "changePercent": self.change_percent,
            "close": self.close_value,
            "date": self.trade_date,
            "high": self.high_value,
            "low": self.low_value,
            "open": self.open_value,
            "symbol": self.symbol,
            "volume": self.volume,
            "vwap": self.vwap,
        }


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner: object
    project_root: Path
    project_root_identity: tuple[int, int, int]
    store_path: Path
    store_identity: tuple[int, int, int]
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    response_body: bytes
    response_sha256: str
    captured_at: str
    rows: tuple[_Row, ...]
    semantic_identity: str
    scope_manifest_sha256: str


class Stage12CIncrementalCollector:
    """Prepare and atomically publish one complete two-session Stage 12C unit."""

    def __init__(
        self,
        *,
        market_store: str | Path,
        project_root: str | Path,
        scope: Stage12CMarketGapV1Scope,
        stage12a_scope: Stage12MarketV1Scope,
        stage12b_scope: Stage12BIncrementalMarketScope,
    ) -> None:
        """Construct only a temporary-fixture collector with explicit paths."""

        self._initialize(
            market_store=market_store,
            project_root=project_root,
            scope=scope,
            stage12a_scope=stage12a_scope,
            stage12b_scope=stage12b_scope,
            construction_capability=_FIXTURE_CONSTRUCTION_CAPABILITY,
        )

    @classmethod
    def _for_canonical_live(
        cls,
        *,
        scope: Stage12CMarketGapV1Scope,
        stage12a_scope: Stage12MarketV1Scope,
        stage12b_scope: Stage12BIncrementalMarketScope,
    ) -> Stage12CIncrementalCollector:
        """Create the hard-bound collector used by the canonical live operation."""

        if cls is not Stage12CIncrementalCollector:
            raise ValidationError("Stage 12C canonical collector cannot be subclassed")
        collector = object.__new__(cls)
        collector._initialize(
            market_store=_CANONICAL_MARKET_STORE,
            project_root=_CANONICAL_PROJECT_ROOT,
            scope=scope,
            stage12a_scope=stage12a_scope,
            stage12b_scope=stage12b_scope,
            construction_capability=_CANONICAL_LIVE_CAPABILITY,
        )
        return collector

    def _initialize(
        self,
        *,
        market_store: str | Path,
        project_root: str | Path,
        scope: Stage12CMarketGapV1Scope,
        stage12a_scope: Stage12MarketV1Scope,
        stage12b_scope: Stage12BIncrementalMarketScope,
        construction_capability: object,
    ) -> None:
        if isinstance(project_root, bool) or not isinstance(project_root, (str, Path)):
            raise ValidationError("An explicit Stage 12C project root is required")
        if isinstance(market_store, bool) or not isinstance(market_store, (str, Path)):
            raise ValidationError("An explicit Stage 12C market store is required")
        validated_scope, validated_stage12a, validated_stage12b = require_stage12c_bindings(
            scope, stage12a_scope, stage12b_scope
        )
        supplied_root = Path(project_root)
        supplied_store = Path(market_store)
        if not supplied_root.is_absolute() or not supplied_store.is_absolute():
            raise ValidationError("Stage 12C project root and market store must be absolute")
        expected_supplied_store = supplied_root / validated_scope.source_target.project_store_path
        if supplied_store != expected_supplied_store:
            raise ValidationError("Stage 12C requires exactly project_root/data/market.sqlite")
        fixture_temp_root: Path | None
        if construction_capability is _FIXTURE_CONSTRUCTION_CAPABILITY:
            fixture_temp_root = self._require_fixture_target(supplied_root)
        elif construction_capability is _CANONICAL_LIVE_CAPABILITY:
            if supplied_root != _CANONICAL_PROJECT_ROOT or supplied_store != _CANONICAL_MARKET_STORE:
                raise ValidationError("Stage 12C canonical collector target is fixed")
            fixture_temp_root = None
        else:
            raise ValidationError("Stage 12C collector construction capability is invalid")
        try:
            if supplied_root.is_symlink() or supplied_store.is_symlink():
                raise ValidationError("Stage 12C paths must name physical project-local objects")
            resolved_root = supplied_root.resolve(strict=True)
            resolved_store = supplied_store.resolve(strict=True)
            root_stat = resolved_root.stat()
            store_stat = resolved_store.stat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Explicit Stage 12C project paths are unavailable") from exc
        if supplied_root != resolved_root or supplied_store != resolved_store:
            raise ValidationError("Stage 12C rejects project-root or market-store aliases")
        expected_store = resolved_root / validated_scope.source_target.project_store_path
        if resolved_store != expected_store:
            raise ValidationError("Stage 12C requires exactly project_root/data/market.sqlite")
        if fixture_temp_root is not None:
            self._require_strict_child(
                resolved_root,
                fixture_temp_root,
                label="fixture project root",
            )
        if not resolved_root.is_dir() or not stat.S_ISREG(store_stat.st_mode):
            raise StoreUnavailableError("Stage 12C requires an existing regular project market store")
        if store_stat.st_nlink != 1:
            raise ConflictError("Stage 12C market store cannot have hard-link aliases")
        self._require_non_symlink_descendants(resolved_root, resolved_store)
        self._project_root = resolved_root
        self._store_path = resolved_store
        self._scope = validated_scope
        self._stage12a_scope = validated_stage12a
        self._stage12b_scope = validated_stage12b
        self._project_root_identity = self._physical_identity(root_stat)
        self._store_identity = self._physical_identity(store_stat)
        self._owner = object()

    @staticmethod
    def _require_fixture_target(project_root: Path) -> Path:
        temporary_root = Stage12CIncrementalCollector._resolved_system_temp_root()
        Stage12CIncrementalCollector._require_strict_child(
            project_root,
            temporary_root,
            label="fixture project root",
        )
        return temporary_root

    @staticmethod
    def _resolved_system_temp_root() -> Path:
        try:
            temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Stage 12C system temporary root is unavailable") from exc
        if not temporary_root.is_dir():
            raise StoreUnavailableError("Stage 12C system temporary root is unavailable")
        return temporary_root

    @staticmethod
    def _require_strict_child(path: Path, parent: Path, *, label: str) -> None:
        try:
            relative = path.relative_to(parent)
        except ValueError as exc:
            raise ValidationError(f"Stage 12C {label} must be below the system temporary root") from exc
        if not relative.parts or ".." in relative.parts:
            raise ValidationError(f"Stage 12C {label} must be below the system temporary root")

    @property
    def market_store(self) -> Path:
        return self._store_path

    def prepare(
        self,
        request: Stage12CLiveRequest,
        response: Stage12CLiveResponse,
    ) -> PreparedStage12CPublication:
        """Validate a complete response before any lock or SQLite activity."""

        if not isinstance(request, Stage12CLiveRequest):
            raise ValidationError("Stage 12C requires a live request descriptor")
        if not isinstance(response, Stage12CLiveResponse):
            raise ValidationError("Stage 12C requires a live response descriptor")
        request_scope, captured_at = self._request_scope(request, response)
        rows = self._parse_response(request_scope, response)
        semantic_identity = _sha256_json(
            {
                "scope_manifest_sha256": self._scope.manifest_sha256,
                "request_scope": dict(request_scope),
                "normalization_version": self._scope.publication.normalization_version,
                "normalized_complete_batch": [row.semantic_mapping() for row in rows],
            }
        )
        prepared = PreparedStage12CPublication()
        _PREPARED[prepared] = _PreparedState(
            owner=self._owner,
            project_root=self._project_root,
            project_root_identity=self._project_root_identity,
            store_path=self._store_path,
            store_identity=self._store_identity,
            request_scope=request_scope,
            request_scope_sha256=_sha256_json(request_scope),
            response_body=response.body,
            response_sha256=_sha256_bytes(response.body),
            captured_at=captured_at,
            rows=rows,
            semantic_identity=semantic_identity,
            scope_manifest_sha256=self._scope.manifest_sha256,
        )
        return prepared

    def publish(self, prepared: PreparedStage12CPublication) -> Stage12CPublicationReceipt:
        """Publish prepared complete work in one short locked transaction."""

        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner is not self._owner
            or state.project_root != self._project_root
            or state.project_root_identity != self._project_root_identity
            or state.store_path != self._store_path
            or state.store_identity != self._store_identity
            or state.scope_manifest_sha256 != self._scope.manifest_sha256
        ):
            raise ValidationError("Prepared Stage 12C publication is not bound to this collector")
        self._require_prepared_project_paths_unchanged(state)
        with StoreWriteLock(self._store_path):
            self._require_prepared_project_paths_unchanged(state)
            descriptor_snapshot = self._snapshot_process_fds()
            connection = sqlite3.connect(self._store_path, timeout=5.0, isolation_level=None)
            try:
                bound_descriptor = self._bind_open_connection_descriptor(
                    state,
                    descriptor_snapshot,
                )
                connection.row_factory = sqlite3.Row
                self._require_bound_connection_target(state, bound_descriptor)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA recursive_triggers=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                self._require_project_schema(connection)
                self._assert_raw_response_not_reused(connection, state)
                if self._has_semantic_replay(connection, state):
                    self._require_bound_connection_target(state, bound_descriptor)
                    return Stage12CPublicationReceipt("unchanged", state.semantic_identity, None, 0)
                instrument = self._require_preseeded_instrument(connection, state)
                self._before_transaction(state, bound_descriptor)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._assert_raw_response_not_reused(connection, state)
                    if self._has_semantic_replay(connection, state):
                        self._require_bound_connection_target(state, bound_descriptor)
                        connection.rollback()
                        return Stage12CPublicationReceipt("unchanged", state.semantic_identity, None, 0)
                    self._before_publication_write(state, bound_descriptor)
                    receipt = self._write_publication(connection, state, instrument)
                    self._before_commit(state, bound_descriptor)
                    self._commit(connection)
                    self._checkpoint_committed_publication(connection, state, bound_descriptor)
                    self._after_commit(state, bound_descriptor)
                    return receipt
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            finally:
                connection.close()

    @staticmethod
    def _physical_identity(stat_result: object) -> tuple[int, int, int]:
        return (
            int(getattr(stat_result, "st_dev")),
            int(getattr(stat_result, "st_ino")),
            int(getattr(stat_result, "st_nlink")),
        )

    @staticmethod
    def _require_non_symlink_descendants(project_root: Path, market_store: Path) -> None:
        try:
            relative = market_store.relative_to(project_root)
        except ValueError as exc:
            raise ValidationError("Stage 12C market store must stay beneath the project root") from exc
        current = project_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValidationError("Stage 12C rejects symlinked project-store path components")

    def _require_prepared_project_paths_unchanged(self, state: _PreparedState) -> None:
        """Revalidate target physical identity before and after lock acquisition."""

        try:
            if self._project_root.is_symlink() or self._store_path.is_symlink():
                raise ValidationError("Stage 12C project paths changed after preparation")
            if self._project_root.resolve(strict=True) != self._project_root:
                raise ValidationError("Stage 12C project root identity changed after preparation")
            if self._store_path.resolve(strict=True) != self._store_path:
                raise ValidationError("Stage 12C market store identity changed after preparation")
            expected_store = self._project_root / self._scope.source_target.project_store_path
            if expected_store != self._store_path:
                raise ValidationError("Stage 12C target declaration changed after preparation")
            self._require_non_symlink_descendants(self._project_root, self._store_path)
            root_stat = self._project_root.stat()
            store_stat = self._store_path.stat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Stage 12C project paths changed after preparation") from exc
        if (
            not self._project_root.is_dir()
            or not stat.S_ISREG(store_stat.st_mode)
            or self._physical_identity(root_stat) != self._project_root_identity
            or self._physical_identity(root_stat) != state.project_root_identity
            or self._physical_identity(store_stat) != self._store_identity
            or self._physical_identity(store_stat) != state.store_identity
            or int(store_stat.st_nlink) != 1
        ):
            raise ConflictError("Stage 12C project-local physical identity changed after preparation")

    @staticmethod
    def _descriptor_marker(descriptor: int) -> tuple[int, int, int, int]:
        try:
            info = os.fstat(descriptor)
        except OSError as exc:
            raise ConflictError("Stage 12C opened SQLite handle is unavailable") from exc
        return (
            stat.S_IFMT(int(info.st_mode)),
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_nlink),
        )

    @staticmethod
    def _snapshot_process_fds() -> dict[int, tuple[int, int, int, int]]:
        try:
            entries = os.listdir("/proc/self/fd")
        except OSError as exc:
            raise StoreUnavailableError("Stage 12C cannot inspect opened SQLite handles") from exc
        snapshot: dict[int, tuple[int, int, int, int]] = {}
        for entry in entries:
            if not entry.isdecimal():
                continue
            descriptor = int(entry)
            try:
                snapshot[descriptor] = Stage12CIncrementalCollector._descriptor_marker(
                    descriptor
                )
            except ConflictError:
                # A descriptor may close while the procfs listing is traversed.
                continue
        return snapshot

    def _require_bound_connection_target(
        self,
        state: _PreparedState,
        descriptor: int,
    ) -> None:
        """Bind every publication boundary to the same opened SQLite inode."""

        expected = (stat.S_IFREG, *state.store_identity)
        self._require_prepared_project_paths_unchanged(state)
        if self._descriptor_marker(descriptor) != expected:
            raise ConflictError("Stage 12C opened SQLite handle is not the prepared market store")
        self._require_prepared_project_paths_unchanged(state)
        if self._descriptor_marker(descriptor) != expected:
            raise ConflictError("Stage 12C opened SQLite handle changed during publication")

    def _bind_open_connection_descriptor(
        self,
        state: _PreparedState,
        before: Mapping[int, tuple[int, int, int, int]],
    ) -> int:
        """Find the newly opened SQLite descriptor for the exact prepared inode."""

        self._require_prepared_project_paths_unchanged(state)
        expected = (stat.S_IFREG, *state.store_identity)
        candidates = tuple(
            descriptor
            for descriptor, marker in self._snapshot_process_fds().items()
            if marker == expected and before.get(descriptor) != marker
        )
        if not candidates:
            raise ConflictError(
                "Stage 12C opened SQLite handle is not bound to the prepared market store"
            )
        descriptor = min(candidates)
        self._require_bound_connection_target(state, descriptor)
        return descriptor

    def _before_transaction(self, state: _PreparedState, descriptor: int) -> None:
        """Seam and guard immediately before the publication transaction starts."""

        self._require_bound_connection_target(state, descriptor)

    def _before_publication_write(self, state: _PreparedState, descriptor: int) -> None:
        """Seam and guard immediately before canonical rows can be written."""

        self._require_bound_connection_target(state, descriptor)

    def _before_commit(self, state: _PreparedState, descriptor: int) -> None:
        """Seam and guard immediately before the immutable publication commits."""

        self._require_bound_connection_target(state, descriptor)

    def _after_commit(self, state: _PreparedState, descriptor: int) -> None:
        """Fail closed if the pathname or opened target drifted at commit."""

        self._require_bound_connection_target(state, descriptor)

    def _checkpoint_committed_publication(
        self,
        connection: sqlite3.Connection,
        state: _PreparedState,
        descriptor: int,
    ) -> None:
        """Synchronously drain WAL facts before any path-dependent post-commit step."""

        self._require_bound_connection_target(state, descriptor)
        if connection.in_transaction:
            raise ConflictError("Stage 12C commit did not close its publication transaction")
        try:
            journal_row = connection.execute("PRAGMA journal_mode").fetchone()
        except sqlite3.Error as exc:
            raise StoreUnavailableError("Stage 12C SQLite journal mode cannot be inspected") from exc
        try:
            journal_values = tuple(journal_row) if journal_row is not None else ()
        except TypeError as exc:
            raise ConflictError("Stage 12C SQLite journal mode result is invalid") from exc
        if len(journal_values) != 1 or not isinstance(journal_values[0], str):
            raise ConflictError("Stage 12C SQLite journal mode result is invalid")
        journal_mode = journal_values[0].casefold()
        if journal_mode == "wal":
            try:
                checkpoint_row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.Error as exc:
                raise StoreUnavailableError("Stage 12C WAL checkpoint failed") from exc
            try:
                checkpoint_values = tuple(checkpoint_row) if checkpoint_row is not None else ()
            except TypeError as exc:
                raise ConflictError("Stage 12C WAL checkpoint result is invalid") from exc
            if (
                len(checkpoint_values) != 3
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in checkpoint_values
                )
            ):
                raise ConflictError("Stage 12C WAL checkpoint result is invalid")
            busy, logged_frames, checkpointed_frames = checkpoint_values
            if busy != 0 or logged_frames != checkpointed_frames:
                raise ConflictError("Stage 12C WAL checkpoint did not complete")
        elif journal_mode not in {"delete", "truncate", "persist"}:
            raise ConflictError("Stage 12C SQLite journal mode is outside the reviewed publication policy")
        if connection.in_transaction:
            raise ConflictError("Stage 12C journal checkpoint opened a publication transaction")
        self._require_bound_connection_target(state, descriptor)



    def _commit(self, connection: sqlite3.Connection) -> None:
        """Small seam for isolated atomicity tests that inject a commit failure."""

        connection.commit()

    def _request_scope(
        self,
        request: Stage12CLiveRequest,
        response: Stage12CLiveResponse,
    ) -> tuple[Mapping[str, object], str]:
        symbol = _require_text(request.symbol, pointer="/symbol", maximum=32)
        if symbol != symbol.upper() or any(character.isspace() for character in symbol):
            raise _issue("/symbol", "symbol", "Stage 12C symbol must be one frozen uppercase roster symbol")
        roster = {item.symbol: item for item in self._stage12a_scope.roster}
        if symbol not in roster:
            raise _issue("/symbol", "roster", "Stage 12C symbol is outside the frozen Stage 12A roster")
        from_date = _date_text(request.from_date, pointer="/from_date")
        to_date = _date_text(request.to_date, pointer="/to_date")
        if (
            from_date != self._scope.request_plan.from_date
            or to_date != self._scope.request_plan.to_date
        ):
            raise _issue("/", "window", "Stage 12C request must use exactly the reviewed two-session range")
        captured_at = _captured_at_text(response.captured_at)
        return (
            {
                "endpoint_path": self._scope.request_plan.endpoint_path,
                "from": from_date,
                "price_variant": STAGE12C_PRICE_VARIANT,
                "provider": "fmp",
                "scope_manifest_sha256": self._scope.manifest_sha256,
                "session_dates": list(self._scope.request_plan.session_dates),
                "symbol": symbol,
                "to": to_date,
            },
            captured_at,
        )

    def _parse_response(
        self,
        request_scope: Mapping[str, object],
        response: Stage12CLiveResponse,
    ) -> tuple[_Row, ...]:
        if isinstance(response.elapsed_seconds, bool) or not isinstance(
            response.elapsed_seconds, (int, float, Decimal)
        ):
            raise _issue("/response/elapsed_seconds", "number", "Stage 12C elapsed time must be numeric")
        elapsed_seconds = Decimal(str(response.elapsed_seconds))
        if not elapsed_seconds.is_finite() or elapsed_seconds < 0:
            raise _issue("/response/elapsed_seconds", "bound", "Stage 12C elapsed time must be finite and nonnegative")
        if elapsed_seconds > self._scope.collector.max_seconds:
            raise ResourceLimitError("Stage 12C response exceeds the reviewed elapsed-time bound")
        if isinstance(response.status, bool) or not isinstance(response.status, int) or response.status != 200:
            raise _issue("/response/status", "status", "Stage 12C publishable response must have HTTP status 200")
        media_type = _require_text(response.media_type, pointer="/response/media_type", maximum=128)
        if media_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise _issue("/response/media_type", "media_type", "Stage 12C publishable response must be application/json")
        if not isinstance(response.body, bytes):
            raise _issue("/response/body", "type", "Stage 12C response body must be immutable bytes")
        if len(response.body) > self._scope.collector.max_bytes:
            raise ResourceLimitError("Stage 12C response exceeds the reviewed byte bound")
        payload = loads_strict(response.body, max_bytes=self._scope.collector.max_bytes)
        if not isinstance(payload, list):
            raise _issue("/response/body", "shape", "Stage 12C response top level must be a list")
        if len(payload) > self._scope.collector.max_rows:
            raise _issue("/response/body", "rows", "Stage 12C response exceeds the reviewed row bound")
        if len(payload) != self._scope.response.manifest_mapping()["complete_row_count"]:
            raise _issue("/response/body", "completeness", "Stage 12C requires exactly two complete response rows")
        symbol = request_scope["symbol"]
        assert isinstance(symbol, str)
        rows = tuple(self._parse_row(value, index=index, expected_symbol=symbol) for index, value in enumerate(payload))
        rows = tuple(sorted(rows, key=lambda row: row.trade_date))
        expected_dates = tuple(request_scope["session_dates"])
        if tuple(row.trade_date for row in rows) != expected_dates:
            raise _issue("/response/body", "completeness", "Stage 12C batch must exactly match both reviewed session dates")
        return rows

    def _parse_row(self, value: object, *, index: int, expected_symbol: str) -> _Row:
        pointer = f"/response/body/{index}"
        if not isinstance(value, Mapping) or set(value) != _ROW_KEYS:
            raise _issue(pointer, "shape", "FMP row keys differ from the reviewed Stage 12C exact schema")
        symbol = _require_text(value["symbol"], pointer=f"{pointer}/symbol", maximum=32)
        if symbol != expected_symbol:
            raise _issue(f"{pointer}/symbol", "symbol", "FMP row symbol differs from the Stage 12C request")
        trade_date = _date_text(value["date"], pointer=f"{pointer}/date")
        open_value = _number(value["open"], pointer=f"{pointer}/open", nonnegative=True)
        high_value = _number(value["high"], pointer=f"{pointer}/high", nonnegative=True)
        low_value = _number(value["low"], pointer=f"{pointer}/low", nonnegative=True)
        close_value = _number(value["close"], pointer=f"{pointer}/close", nonnegative=True)
        if high_value < max(open_value, low_value, close_value) or low_value > min(open_value, high_value, close_value):
            raise _issue(pointer, "ohlc", "FMP Stage 12C OHLC values are internally inconsistent")
        return _Row(
            symbol=symbol,
            trade_date=trade_date,
            open_value=open_value,
            high_value=high_value,
            low_value=low_value,
            close_value=close_value,
            volume=_volume(value["volume"], pointer=f"{pointer}/volume"),
            change=_number(value["change"], pointer=f"{pointer}/change"),
            change_percent=_number(value["changePercent"], pointer=f"{pointer}/changePercent"),
            vwap=_number(value["vwap"], pointer=f"{pointer}/vwap", nonnegative=True),
        )

    @staticmethod
    def _require_project_schema(connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        present = {str(row[0]) for row in rows}
        if not _REQUIRED_TABLES.issubset(present):
            raise ValidationError("Project-local Stage 12C market store lacks the reviewed existing Stage 10 relations")
        required_datasets = {_EVIDENCE_DATASET, _PRICE_DATASET}
        datasets = {
            str(row[0])
            for row in connection.execute(
                "SELECT dataset_id FROM dataset_registry WHERE dataset_id IN (?, ?)",
                tuple(sorted(required_datasets)),
            )
        }
        if datasets != required_datasets:
            raise ValidationError("Project-local Stage 12C market store lacks required Stage 10 dataset registrations")

    @staticmethod
    def _assert_raw_response_not_reused(connection: sqlite3.Connection, state: _PreparedState) -> None:
        rows = connection.execute(
            "SELECT request_scope_sha256 FROM stage10_daily_price_captures WHERE response_sha256 = ?",
            (state.response_sha256,),
        ).fetchall()
        if any(str(row[0]) != state.request_scope_sha256 for row in rows):
            raise ConflictError("Stage 12C response bytes are already bound to another request descriptor")

    @staticmethod
    def _has_semantic_replay(connection: sqlite3.Connection, state: _PreparedState) -> bool:
        row = connection.execute(
            "SELECT 1 FROM stage10_daily_price_captures WHERE semantic_identity = ?",
            (state.semantic_identity,),
        ).fetchone()
        return row is not None

    def _require_preseeded_instrument(self, connection: sqlite3.Connection, state: _PreparedState) -> sqlite3.Row:
        symbol = state.request_scope["symbol"]
        assert isinstance(symbol, str)
        row = connection.execute(
            """
            SELECT instrument_id, provider, provider_symbol, asset_type, currency_segment
            FROM stage10_instruments
            WHERE provider = 'fmp' AND provider_symbol = ?
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            raise ValidationError("Stage 12C publication requires a preseeded frozen instrument")
        roster = {item.symbol: item for item in self._stage12a_scope.roster}
        roster_item = roster[symbol]
        if str(row["asset_type"]) != roster_item.asset_type or str(row["currency_segment"]) != "provider_native":
            raise ValidationError("Preseeded instrument does not match the frozen Stage 12A roster identity")
        return row

    def _write_publication(
        self,
        connection: sqlite3.Connection,
        state: _PreparedState,
        instrument: sqlite3.Row,
    ) -> Stage12CPublicationReceipt:
        instrument_id = str(instrument["instrument_id"])
        self._require_changed_rows_are_later(connection, state, instrument_id)
        semantic = state.semantic_identity
        run_id = stable_id("stage12c-run", semantic)
        artifact_id = stable_id("stage12c-artifact", semantic, state.response_sha256)
        snapshot_id = stable_id("stage12c-snapshot", semantic)
        capture_id = stable_id("stage12c-capture", semantic)
        scope_json = dumps_strict(dict(state.request_scope))
        connection.execute(
            """
            INSERT INTO ingestion_runs(
                run_id, dataset_id, semantic_identity, command, scope_json, status,
                started_at, fetched_count, written_count, code_version
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, 0, ?)
            """,
            (
                run_id,
                _PRICE_DATASET,
                semantic,
                STAGE12C_COLLECTOR_ID,
                scope_json,
                state.captured_at,
                len(state.rows),
                self._scope.version,
            ),
        )
        for dataset_id in (_EVIDENCE_DATASET, _PRICE_DATASET):
            connection.execute(
                "INSERT INTO ingestion_run_outputs(run_id, dataset_id, semantic_identity) VALUES (?, ?, ?)",
                (run_id, dataset_id, semantic),
            )
        connection.execute(
            """
            INSERT INTO stage10_daily_price_captures(
                capture_id, dataset_id, provider, instrument_id, provider_symbol, endpoint_path,
                scope_manifest_sha256, request_scope_json, request_scope_sha256,
                response_sha256, response_bytes, http_status, content_type, semantic_identity,
                completeness, artifact_id, snapshot_id, captured_at, captured_precision,
                earliest_trade_date, latest_trade_date, row_count, normalization_version, run_id
            ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, 200, 'application/json', ?,
                      'complete', ?, ?, ?, 'datetime', ?, ?, ?, ?, ?)
            """,
            (
                capture_id,
                _EVIDENCE_DATASET,
                instrument_id,
                state.request_scope["symbol"],
                self._scope.request_plan.endpoint_path,
                self._scope.manifest_sha256,
                scope_json,
                state.request_scope_sha256,
                state.response_sha256,
                state.response_body,
                semantic,
                artifact_id,
                snapshot_id,
                state.captured_at,
                state.rows[0].trade_date,
                state.rows[-1].trade_date,
                len(state.rows),
                _PHYSICAL_CAPTURE_NORMALIZATION_VERSION,
                run_id,
            ),
        )
        written_versions = 0
        for source_row, row in enumerate(state.rows, start=1):
            current = connection.execute(
                """
                SELECT version.version_id, version.correction_sequence, version.open_value,
                       version.high_value, version.low_value, version.close_value, version.volume
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id = current.current_version_id
                WHERE current.instrument_id = ? AND current.trade_date = ?
                  AND current.provider = 'fmp' AND current.price_variant = ?
                  AND current.currency_segment = 'provider_native'
                """,
                (instrument_id, row.trade_date, STAGE12C_PRICE_VARIANT),
            ).fetchone()
            if current is not None and self._same_current(current, row):
                continue
            sequence = 1 if current is None else int(current["correction_sequence"]) + 1
            previous = None if current is None else str(current["version_id"])
            version_id = stable_id("stage12c-price-version", semantic, row.trade_date, str(sequence))
            connection.execute(
                """
                INSERT INTO stage10_daily_price_versions(
                    version_id, instrument_id, trade_date, provider, price_variant, currency_segment,
                    open_value, high_value, low_value, close_value, volume, available_at,
                    available_precision, captured_at, captured_precision, correction_sequence,
                    supersedes_version_id, capture_id, artifact_id, snapshot_id, run_id, source_row
                ) VALUES (?, ?, ?, 'fmp', ?, 'provider_native', ?, ?, ?, ?, ?, ?,
                          'datetime', ?, 'datetime', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    instrument_id,
                    row.trade_date,
                    STAGE12C_PRICE_VARIANT,
                    _decimal_text(row.open_value),
                    _decimal_text(row.high_value),
                    _decimal_text(row.low_value),
                    _decimal_text(row.close_value),
                    row.volume,
                    state.captured_at,
                    state.captured_at,
                    sequence,
                    previous,
                    capture_id,
                    artifact_id,
                    snapshot_id,
                    run_id,
                    source_row,
                ),
            )
            if current is None:
                connection.execute(
                    """
                    INSERT INTO stage10_daily_prices(
                        instrument_id, trade_date, provider, price_variant, currency_segment, current_version_id
                    ) VALUES (?, ?, 'fmp', ?, 'provider_native', ?)
                    """,
                    (instrument_id, row.trade_date, STAGE12C_PRICE_VARIANT, version_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE stage10_daily_prices SET current_version_id = ?
                    WHERE instrument_id = ? AND trade_date = ? AND provider = 'fmp'
                      AND price_variant = ? AND currency_segment = 'provider_native'
                    """,
                    (version_id, instrument_id, row.trade_date, STAGE12C_PRICE_VARIANT),
                )
            written_versions += 1
        connection.execute(
            """
            INSERT INTO ingestion_artifacts(
                artifact_id, run_id, dataset_id, content_sha256, media_type, byte_count,
                source_reference, request_scope_json, captured_at, captured_precision,
                normalization_version
            ) VALUES (?, ?, ?, ?, 'application/json', ?, ?, ?, ?, 'datetime', ?)
            """,
            (
                artifact_id,
                run_id,
                _EVIDENCE_DATASET,
                state.response_sha256,
                len(state.response_body),
                "fmp.historical-price-eod.full",
                scope_json,
                state.captured_at,
                self._scope.publication.normalization_version,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingestion_snapshots(
                snapshot_id, run_id, dataset_id, semantic_identity, scope_json, completeness,
                row_count, captured_at, captured_precision, validation_state, warnings_json
            ) VALUES (?, ?, ?, ?, ?, 'complete', ?, ?, 'datetime', 'validated', '[]')
            """,
            (snapshot_id, run_id, _PRICE_DATASET, semantic, scope_json, len(state.rows), state.captured_at),
        )
        connection.execute(
            "INSERT INTO ingestion_snapshot_artifacts(snapshot_id, artifact_id, artifact_ordinal) VALUES (?, ?, 1)",
            (snapshot_id, artifact_id),
        )
        connection.execute(
            """
            UPDATE ingestion_runs
            SET status = 'succeeded', completed_at = ?, artifact_id = ?, snapshot_id = ?, written_count = ?
            WHERE run_id = ?
            """,
            (state.captured_at, artifact_id, snapshot_id, written_versions, run_id),
        )
        for dataset_id in (_EVIDENCE_DATASET, _PRICE_DATASET):
            connection.execute(
                """
                UPDATE dataset_registry
                SET last_successful_run_id = ?, last_semantic_identity = ?
                WHERE dataset_id = ?
                """,
                (run_id, semantic, dataset_id),
            )
        return Stage12CPublicationReceipt("published", semantic, capture_id, written_versions)

    def _require_changed_rows_are_later(
        self,
        connection: sqlite3.Connection,
        state: _PreparedState,
        instrument_id: str,
    ) -> None:
        candidate_time = _utc_datetime(state.captured_at, pointer="/captured_at")
        for row in state.rows:
            current = connection.execute(
                """
                SELECT version.open_value, version.high_value, version.low_value,
                       version.close_value, version.volume, version.available_at,
                       version.captured_at
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id = current.current_version_id
                WHERE current.instrument_id = ? AND current.trade_date = ?
                  AND current.provider = 'fmp' AND current.price_variant = ?
                  AND current.currency_segment = 'provider_native'
                """,
                (instrument_id, row.trade_date, STAGE12C_PRICE_VARIANT),
            ).fetchone()
            if current is None or self._same_current(current, row):
                continue
            available_at = _utc_datetime(current["available_at"], pointer="/current/available_at")
            captured_at = _utc_datetime(current["captured_at"], pointer="/current/captured_at")
            if candidate_time <= max(available_at, captured_at):
                raise ConflictError(
                    "Changed Stage 12C facts require a strictly later capture and availability time"
                )

    @staticmethod
    def _same_current(current: sqlite3.Row, row: _Row) -> bool:
        return (
            str(current["open_value"]) == _decimal_text(row.open_value)
            and str(current["high_value"]) == _decimal_text(row.high_value)
            and str(current["low_value"]) == _decimal_text(row.low_value)
            and str(current["close_value"]) == _decimal_text(row.close_value)
            and int(current["volume"]) == row.volume
        )


__all__ = (
    "PreparedStage12CPublication",
    "Stage12CIncrementalCollector",
    "Stage12CLiveRequest",
    "Stage12CLiveResponse",
    "Stage12CPublicationReceipt",
)
