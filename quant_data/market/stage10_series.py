"""Fixed read-only typed series over the canonical Stage 10 price model."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..contracts import Observation, TimeSeries
from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


DATASET_ID = "market.stage10.daily_prices"
EVIDENCE_DATASET_ID = "market.stage10.source_evidence"
IDENTITY_DATASET_ID = "market.stage10.instruments"
PROVIDER = "fmp"
PRICE_VARIANT = "fmp_full_eod_v1"
CURRENCY_SEGMENT = "provider_native"
OHLC_FIELDS = ("open", "high", "low", "close")
_MAX_AS_OF_CANDIDATES = 200_000
_STAGE10_DATASET_IDS = (
    DATASET_ID,
    EVIDENCE_DATASET_ID,
    IDENTITY_DATASET_ID,
)
_REQUIRED_RELATIONS = frozenset(
    {
        "stage10_universe_captures",
        "stage10_instruments",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    }
)


@dataclass(frozen=True, slots=True)
class Stage10DailyPriceQuery:
    """One bounded latest or local-capture as-of daily-price request."""

    identifier: str
    identifier_kind: str
    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    limit: int

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValidationError("Stage 10 market identifier is required")
        if len(self.identifier) > 200:
            raise ValidationError("Stage 10 market identifier is too long")
        if self.identifier_kind not in {"instrument_id", "provider_symbol"}:
            raise ValidationError("Stage 10 market identifier kind is unsupported")
        start = (
            None
            if self.start_date is None
            else parse_date(self.start_date, pointer="/start_date")
        )
        end = (
            None
            if self.end_date is None
            else parse_date(self.end_date, pointer="/end_date")
        )
        if start is not None and end is not None and end < start:
            raise ValidationError("Stage 10 market end date cannot precede start date")
        if self.mode not in {"latest", "as_of"}:
            raise ValidationError("Stage 10 prices support only latest and as_of modes")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Stage 10 market date-only policy is unsupported") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if self.mode == "as_of":
            if not isinstance(self.as_of, str) or not self.as_of:
                raise ValidationError("Stage 10 as_of mode requires a cutoff")
            TemporalValue.parse(self.as_of, pointer="/as_of")
        elif self.as_of is not None:
            raise ValidationError("Stage 10 latest mode does not accept a cutoff")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 10_000
        ):
            raise ResourceLimitError("Stage 10 market limit must be from 1 through 10000")

    @property
    def cutoff(self) -> TemporalValue | None:
        if self.as_of is None:
            return None
        return TemporalValue.parse(self.as_of, pointer="/as_of")

    @property
    def start_bound(self) -> str:
        """Return an internal inclusive bound without inventing public input."""

        return self.start_date if self.start_date is not None else "0001-01-01"

    @property
    def end_bound(self) -> str:
        """Return an internal inclusive bound without inventing public input."""

        return self.end_date if self.end_date is not None else "9999-12-31"


@dataclass(frozen=True, slots=True)
class Stage10AvailableTickerQuery:
    """One bounded request for the current retrievable Stage 10 universe."""

    limit: int = 10_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 10_000
        ):
            raise ResourceLimitError(
                "Stage 10 available-ticker limit must be from 1 through 10000"
            )


@dataclass(frozen=True, slots=True)
class Stage10AvailableTicker:
    """One current instrument that has at least one retrievable price row."""

    ticker: str
    instrument_id: str
    asset_type: str
    display_name: str | None
    exchange_code: str | None

    def __post_init__(self) -> None:
        if not self.ticker or not self.instrument_id or not self.asset_type:
            raise ValidationError("Stage 10 available-ticker identity is invalid")

    def receipt_mapping(self) -> dict[str, str | None]:
        return {
            "asset_type": self.asset_type,
            "display_name": self.display_name,
            "exchange_code": self.exchange_code,
            "instrument_id": self.instrument_id,
            "ticker": self.ticker,
        }


@dataclass(frozen=True, slots=True)
class Stage10AvailableTickerSelection:
    """Immutable current-universe selection with path-free provenance."""

    tickers: tuple[Stage10AvailableTicker, ...]
    migration_ids: tuple[str, ...]
    semantic_id: str
    truncated: bool

    def __post_init__(self) -> None:
        if not self.semantic_id or any(
            not isinstance(item, Stage10AvailableTicker) for item in self.tickers
        ):
            raise ValidationError("Stage 10 available-ticker selection is invalid")


def _sidecar_stamp(path: Path, name: str) -> tuple[int, ...] | None:
    suffix = {"wal": "-wal", "shm": "-shm", "journal": "-journal"}[name]
    candidate = Path(str(path) + suffix)
    try:
        value = os.stat(candidate, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _stable_stat(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@contextmanager
def _quiet_market_connection(store_map: StoreMap) -> Iterator[sqlite3.Connection]:
    """Open one held-descriptor immutable reader and prove no file-state change."""

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 10 reader requires an explicit store map")
    store_map.validate_distinct()
    path = store_map.market
    if path.is_symlink() or not path.is_file():
        raise StoreUnavailableError("Market store is unavailable")
    before_sidecars = {
        name: _sidecar_stamp(path, name) for name in ("wal", "shm", "journal")
    }
    for value in before_sidecars.values():
        if value is not None and (
            not stat.S_ISREG(value[2]) or value[3] != 1
        ):
            raise StoreUnavailableError("Market store sidecar identity is invalid")
    for name in ("wal", "journal"):
        value = before_sidecars[name]
        if value is not None and value[4] != 0:
            raise StoreUnavailableError("Market store is not quiet")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StoreUnavailableError("Market store is unavailable") from exc
    connection: sqlite3.Connection | None = None
    before: os.stat_result | None = None
    before_path: os.stat_result | None = None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StoreUnavailableError("Market store identity is invalid")
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise StoreUnavailableError("Market store identity changed")
        before_path = current
        connection = sqlite3.connect(
            f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1",
            uri=True,
            timeout=0.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise StoreUnavailableError("Market store reader is not query-only")
        connection.execute("BEGIN")
        role = connection.execute(
            "SELECT store_role FROM store_metadata WHERE singleton=1"
        ).fetchone()
        if role is None or role[0] != StoreRole.MARKET.value:
            raise StoreUnavailableError("Market store has the wrong role")
        relations = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (?, ?, ?, ?, ?)
                """,
                tuple(sorted(_REQUIRED_RELATIONS)),
            )
        }
        if relations != _REQUIRED_RELATIONS:
            raise StoreUnavailableError("Market store lacks the Stage 10 price model")
        yield connection
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Market store is unavailable") from exc
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        try:
            if before is not None and _stable_stat(os.fstat(descriptor)) != _stable_stat(before):
                raise StoreUnavailableError("Market store changed during the read")
            try:
                after_path = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise StoreUnavailableError(
                    "Market store path changed during the read"
                ) from exc
            if before_path is not None and _stable_stat(after_path) != _stable_stat(
                before_path
            ):
                raise StoreUnavailableError("Market store path changed during the read")
            if any(
                _sidecar_stamp(path, name) != before_sidecars[name]
                for name in before_sidecars
            ):
                raise StoreUnavailableError("Market store sidecar changed during the read")
        finally:
            os.close(descriptor)


def _stored_decimal(row: Mapping[str, Any], name: str) -> Decimal:
    try:
        value = Decimal(str(row[name]))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Stored Stage 10 price is invalid") from exc
    if not value.is_finite():
        raise ValidationError("Stored Stage 10 price is non-finite")
    return value


def _stored_temporal(
    row: Mapping[str, Any], value_name: str, precision_name: str
) -> TemporalValue:
    value = TemporalValue.parse(str(row[value_name]), pointer=f"/stored/{value_name}")
    if value.precision.value != str(row[precision_name]):
        raise ValidationError("Stored Stage 10 temporal precision is inconsistent")
    return value


def _version_rank(row: Mapping[str, Any]) -> tuple[int, str]:
    return int(row["correction_sequence"]), str(row["version_id"])


def _reconcile_store_contract(
    connection: sqlite3.Connection,
    registry: Registry,
) -> list[sqlite3.Row]:
    """Fail closed unless the live store matches reviewed migration and dataset facts."""

    migrations = list(
        connection.execute(
            """
            SELECT migration_id, store_role, ordinal, resource, sha256,
                   reconstruction_state
            FROM schema_migrations
            ORDER BY ordinal
            """
        )
    )
    expected_migrations = tuple(
        (
            item.id,
            item.store,
            item.ordinal,
            item.resource,
            item.sha256,
            item.reconstruction_state,
        )
        for item in sorted(
            registry.migrations_for(StoreRole.MARKET.value),
            key=lambda item: item.ordinal,
        )
    )
    actual_migrations = tuple(
        (
            str(row["migration_id"]),
            str(row["store_role"]),
            int(row["ordinal"]),
            str(row["resource"]),
            str(row["sha256"]),
            str(row["reconstruction_state"]),
        )
        for row in migrations
    )
    if actual_migrations != expected_migrations:
        raise StoreUnavailableError(
            "Market store migration ledger does not match the reviewed registry"
        )

    declarations = {
        item.id: item
        for item in registry.datasets_for(StoreRole.MARKET.value)
        if item.id in _STAGE10_DATASET_IDS
    }
    if set(declarations) != set(_STAGE10_DATASET_IDS):
        raise StoreUnavailableError(
            "Reviewed registry lacks the Stage 10 dataset contract"
        )
    dataset_rows = list(
        connection.execute(
            """
            SELECT dataset.dataset_id, dataset.store_role, dataset.layer,
                   dataset.schema_version, dataset.relations_json, dataset.active,
                   identity.identity_sha256, identity.registered_version
            FROM dataset_registry AS dataset
            JOIN dataset_identity_contracts AS identity
              ON identity.dataset_id=dataset.dataset_id
            WHERE dataset.dataset_id IN (?, ?, ?)
            ORDER BY dataset.dataset_id
            """,
            tuple(sorted(_STAGE10_DATASET_IDS)),
        )
    )
    expected_datasets = tuple(
        (
            declaration.id,
            declaration.store,
            declaration.layer,
            declaration.version,
            dumps_strict(list(declaration.relations)),
            int(declaration.active),
            declaration.identity_sha256,
            declaration.version,
        )
        for declaration in sorted(declarations.values(), key=lambda item: item.id)
    )
    actual_datasets = tuple(
        (
            str(row["dataset_id"]),
            str(row["store_role"]),
            str(row["layer"]),
            str(row["schema_version"]),
            str(row["relations_json"]),
            int(row["active"]),
            str(row["identity_sha256"]),
            str(row["registered_version"]),
        )
        for row in dataset_rows
    )
    if actual_datasets != expected_datasets:
        raise StoreUnavailableError(
            "Market store dataset declarations do not match the reviewed registry"
        )
    return migrations


class Stage10DailyPriceRepository:
    """Read Stage 10 daily prices through fixed immutable query templates."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Stage 10 repository dependencies are invalid")
        market_datasets = {item.id for item in registry.datasets_for(StoreRole.MARKET.value)}
        if not {DATASET_ID, EVIDENCE_DATASET_ID, IDENTITY_DATASET_ID} <= market_datasets:
            raise ValidationError("Stage 10 market datasets are not registered")
        self._store_map = store_map
        self._registry = registry

    @staticmethod
    def _instrument(
        connection: sqlite3.Connection, query: Stage10DailyPriceQuery
    ) -> tuple[sqlite3.Row, tuple[str, ...]]:
        field = (
            "instrument_id"
            if query.identifier_kind == "instrument_id"
            else "provider_symbol"
        )
        identifier = (
            query.identifier
            if query.identifier_kind == "instrument_id"
            else query.identifier.upper()
        )
        row = connection.execute(
            f"""
            SELECT instrument_id, provider_symbol, asset_type, display_name,
                   exchange_code, currency_segment, identity_seed_sha256,
                   captured_at, captured_precision, run_id
            FROM stage10_instruments
            WHERE provider=? AND {field}=?
            """,
            (PROVIDER, identifier),
        ).fetchone()
        if row is None:
            raise ValidationError("Requested Stage 10 market instrument is unavailable")
        if str(row["currency_segment"]) != CURRENCY_SEGMENT:
            raise ValidationError("Stage 10 instrument currency segment is inconsistent")
        captured = _stored_temporal(row, "captured_at", "captured_precision")
        if captured.precision is not TemporalPrecision.DATETIME:
            raise ValidationError(
                "Stage 10 instrument identity must preserve exact capture timing"
            )
        if query.mode != "as_of":
            return row, ()
        cutoff = query.cutoff
        assert cutoff is not None
        decision = availability_at_or_before(
            captured, cutoff, query.date_only_policy
        )
        if not decision.included:
            raise ValidationError(
                "Requested Stage 10 market instrument is unavailable at the cutoff"
            )
        return row, decision.warnings

    @staticmethod
    def _receipt_instrument(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "instrument_id": str(row["instrument_id"]),
            "provider_symbol": str(row["provider_symbol"]),
            "asset_type": str(row["asset_type"]),
            "display_name": (
                None if row["display_name"] is None else str(row["display_name"])
            ),
            "exchange_code": (
                None if row["exchange_code"] is None else str(row["exchange_code"])
            ),
            "currency_segment": str(row["currency_segment"]),
            "identity_seed_sha256": str(row["identity_seed_sha256"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "run_id": str(row["run_id"]),
        }

    @staticmethod
    def _latest_rows(
        connection: sqlite3.Connection,
        query: Stage10DailyPriceQuery,
        instrument_id: str,
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT version.*, capture.response_sha256,
                       capture.request_scope_sha256, capture.semantic_identity,
                       capture.normalization_version
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS version
                  ON version.version_id=current.current_version_id
                JOIN stage10_daily_price_captures AS capture
                  ON capture.capture_id=version.capture_id
                WHERE current.instrument_id=?
                  AND current.trade_date>=? AND current.trade_date<=?
                  AND current.provider=? AND current.price_variant=?
                  AND current.currency_segment=?
                ORDER BY current.trade_date, version.version_id
                LIMIT ?
                """,
                (
                    instrument_id,
                    query.start_bound,
                    query.end_bound,
                    PROVIDER,
                    PRICE_VARIANT,
                    CURRENCY_SEGMENT,
                    query.limit + 1,
                ),
            )
        )

    @staticmethod
    def _as_of_rows(
        connection: sqlite3.Connection,
        query: Stage10DailyPriceQuery,
        instrument_id: str,
    ) -> tuple[list[sqlite3.Row], tuple[str, ...]]:
        candidates = list(
            connection.execute(
                """
                SELECT version.*, capture.response_sha256,
                       capture.request_scope_sha256, capture.semantic_identity,
                       capture.normalization_version
                FROM stage10_daily_price_versions AS version
                JOIN stage10_daily_price_captures AS capture
                  ON capture.capture_id=version.capture_id
                WHERE version.instrument_id=?
                  AND version.trade_date>=? AND version.trade_date<=?
                  AND version.provider=? AND version.price_variant=?
                  AND version.currency_segment=?
                ORDER BY version.trade_date, version.correction_sequence,
                         version.version_id
                LIMIT ?
                """,
                (
                    instrument_id,
                    query.start_bound,
                    query.end_bound,
                    PROVIDER,
                    PRICE_VARIANT,
                    CURRENCY_SEGMENT,
                    _MAX_AS_OF_CANDIDATES + 1,
                ),
            )
        )
        if len(candidates) > _MAX_AS_OF_CANDIDATES:
            raise ResourceLimitError("Stage 10 price history exceeds the as-of bound")
        cutoff = query.cutoff
        assert cutoff is not None
        selected: dict[str, sqlite3.Row] = {}
        warnings: set[str] = set()
        for row in candidates:
            availability = _stored_temporal(row, "available_at", "available_precision")
            decision = availability_at_or_before(
                availability, cutoff, query.date_only_policy
            )
            if not decision.included:
                continue
            trade_date = str(row["trade_date"])
            current = selected.get(trade_date)
            if current is None or _version_rank(row) > _version_rank(current):
                selected[trade_date] = row
            warnings.update(decision.warnings)
        return [selected[key] for key in sorted(selected)], tuple(sorted(warnings))

    @staticmethod
    def _observation(row: Mapping[str, Any], field: str) -> Observation:
        if field not in OHLC_FIELDS:
            raise ValidationError("Stage 10 observation field is unsupported")
        available = _stored_temporal(row, "available_at", "available_precision")
        captured = _stored_temporal(row, "captured_at", "captured_precision")
        if (
            available.precision is not TemporalPrecision.DATETIME
            or captured.precision is not TemporalPrecision.DATETIME
            or available.raw != captured.raw
        ):
            raise ValidationError("Stage 10 prices must preserve exact local-capture timing")
        trade_date = str(row["trade_date"])
        parse_date(trade_date, pointer="/stored/trade_date")
        return Observation(
            period_start=trade_date,
            period_end=trade_date,
            value=_stored_decimal(row, f"{field}_value"),
            missing_reason=None,
            unit="provider_native_currency",
            value_representation="price",
            scale="1",
            vintage_at=str(row["captured_at"]),
            available_at=str(row["available_at"]),
            available_precision=str(row["available_precision"]),
            captured_at=str(row["captured_at"]),
            captured_precision=str(row["captured_precision"]),
            version_id=str(row["version_id"]),
            evidence_id=str(row["artifact_id"]),
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            dimensions={
                "instrument_id": str(row["instrument_id"]),
                "provider": str(row["provider"]),
                "price_variant": str(row["price_variant"]),
                "currency_segment": str(row["currency_segment"]),
                "observation_field": field,
            },
            quality_flags=(
                "adjustment_status:not_established",
                "availability_basis:local_capture",
                "horizon_basis:observed_rows",
                "session_calendar:not_established",
            ),
        )

    @staticmethod
    def _receipt_version(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "version_id": str(row["version_id"]),
            "instrument_id": str(row["instrument_id"]),
            "trade_date": str(row["trade_date"]),
            "provider": str(row["provider"]),
            "price_variant": str(row["price_variant"]),
            "currency_segment": str(row["currency_segment"]),
            "open_value": str(row["open_value"]),
            "high_value": str(row["high_value"]),
            "low_value": str(row["low_value"]),
            "close_value": str(row["close_value"]),
            "volume": int(row["volume"]),
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "correction_sequence": int(row["correction_sequence"]),
            "supersedes_version_id": (
                None
                if row["supersedes_version_id"] is None
                else str(row["supersedes_version_id"])
            ),
            "capture_id": str(row["capture_id"]),
            "artifact_id": str(row["artifact_id"]),
            "snapshot_id": str(row["snapshot_id"]),
            "run_id": str(row["run_id"]),
            "source_row": int(row["source_row"]),
            "response_sha256": str(row["response_sha256"]),
            "request_scope_sha256": str(row["request_scope_sha256"]),
            "semantic_identity": str(row["semantic_identity"]),
            "normalization_version": str(row["normalization_version"]),
        }

    def _selected_rows(
        self,
        query: Stage10DailyPriceQuery,
    ) -> tuple[
        list[sqlite3.Row],
        sqlite3.Row,
        list[sqlite3.Row],
        tuple[str, ...],
        tuple[str, ...],
        bool,
    ]:
        if not isinstance(query, Stage10DailyPriceQuery):
            raise ValidationError("Stage 10 repository requires a typed query")
        with _quiet_market_connection(self._store_map) as connection:
            migrations = _reconcile_store_contract(connection, self._registry)
            instrument, identity_warnings = self._instrument(connection, query)
            instrument_id = str(instrument["instrument_id"])
            if query.mode == "latest":
                rows = self._latest_rows(connection, query, instrument_id)
                selection_warnings: tuple[str, ...] = ()
            else:
                rows, selection_warnings = self._as_of_rows(
                    connection, query, instrument_id
                )
            truncated = len(rows) > query.limit
            rows = rows[: query.limit]
        return (
            migrations,
            instrument,
            rows,
            identity_warnings,
            selection_warnings,
            truncated,
        )

    @staticmethod
    def _validate_ohlc_row(row: Mapping[str, Any]) -> None:
        open_value = _stored_decimal(row, "open_value")
        high_value = _stored_decimal(row, "high_value")
        low_value = _stored_decimal(row, "low_value")
        close_value = _stored_decimal(row, "close_value")
        if (
            high_value < low_value
            or high_value < max(open_value, close_value)
            or low_value > min(open_value, close_value)
        ):
            raise ValidationError("Stored Stage 10 OHLC values are inconsistent")

    def _series_from_rows(
        self,
        *,
        query: Stage10DailyPriceQuery,
        field: str,
        migrations: list[sqlite3.Row],
        instrument: sqlite3.Row,
        rows: list[sqlite3.Row],
        identity_warnings: tuple[str, ...],
        selection_warnings: tuple[str, ...],
        truncated: bool,
    ) -> TimeSeries:
        if field not in OHLC_FIELDS:
            raise ValidationError("Stage 10 observation field is unsupported")
        instrument_id = str(instrument["instrument_id"])
        observations = tuple(self._observation(row, field) for row in rows)
        series_id = stable_id(
            "stage10_daily_price_series",
            instrument_id,
            PROVIDER,
            PRICE_VARIANT,
            CURRENCY_SEGMENT,
            field,
        )
        receipt_material = {
            "migrations": [
                {"migration_id": str(row["migration_id"]), "sha256": str(row["sha256"])}
                for row in migrations
            ],
            "series_contract": {
                "series_id": series_id,
                "instrument_id": instrument_id,
                "provider_symbol": str(instrument["provider_symbol"]),
                "provider": PROVIDER,
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": field,
                "availability_basis": "local_capture",
                "horizon_basis": "observed_rows",
            },
            "selected_instrument_identity": self._receipt_instrument(instrument),
            "selected_immutable_versions": [
                self._receipt_version(row) for row in rows
            ],
        }
        warnings = set(identity_warnings)
        warnings.update(selection_warnings)
        warnings.update(
            {
                "adjustment_and_total_return_semantics_not_established",
                "local_capture_availability",
                "observed_row_horizon",
                "session_calendar_not_established",
            }
        )
        if truncated:
            warnings.add("result_truncated")
        cutoff = query.cutoff
        point_in_time_status = "safe" if query.mode == "as_of" else "not_applicable"
        point_in_time_scope = (
            "retained_local_captures"
            if query.mode == "as_of"
            else "current_stored_knowledge"
        )
        if query.mode == "as_of":
            warnings.add("point_in_time_safe_only_for_retained_local_captures")
        return TimeSeries(
            series_id=series_id,
            metadata={
                "instrument_id": instrument_id,
                "provider_symbol": str(instrument["provider_symbol"]),
                "asset_type": str(instrument["asset_type"]),
                "display_name": (
                    None
                    if instrument["display_name"] is None
                    else str(instrument["display_name"])
                ),
                "exchange_code": (
                    None
                    if instrument["exchange_code"] is None
                    else str(instrument["exchange_code"])
                ),
                "provider": PROVIDER,
                "frequency": "daily",
                "unit": "provider_native_currency",
                "value_representation": "price",
                "scale": "1",
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": field,
                "availability_basis": "local_capture",
                "horizon_basis": "observed_rows",
                "session_calendar_status": "not_established",
                "adjustment_status": "not_established",
            },
            observations=observations,
            warnings=tuple(sorted(warnings)),
            audit={
                "mode": query.mode,
                "requested_mode": query.mode,
                "actual_mode": query.mode,
                "cutoff": None if cutoff is None else cutoff.raw,
                "cutoff_precision": (
                    None if cutoff is None else cutoff.precision.value
                ),
                "date_only_policy": query.date_only_policy.value,
                "availability_basis": "local_capture",
                "period_range_rule": "trade_date",
                "requested_start_date": query.start_date,
                "requested_end_date": query.end_date,
                "provider": PROVIDER,
                "price_variant": PRICE_VARIANT,
                "currency_segment": CURRENCY_SEGMENT,
                "observation_field": field,
                "horizon_basis": "observed_rows",
                "session_calendar_status": "not_established",
                "point_in_time_status": point_in_time_status,
                "point_in_time_scope": point_in_time_scope,
                "unsafe_reasons": [],
                "limit": query.limit,
                "selected_count": len(observations),
                "missing_count": 0,
                "truncated": truncated,
            },
            provenance={
                "dataset_id": DATASET_ID,
                "evidence_dataset_id": EVIDENCE_DATASET_ID,
                "identity_dataset_id": IDENTITY_DATASET_ID,
                "store_role": StoreRole.MARKET.value,
                "registry_revision": self._registry.revision,
                "store_receipt": {
                    "migration_ids": [str(row["migration_id"]) for row in migrations],
                    "selected_instrument_identity": self._receipt_instrument(
                        instrument
                    ),
                    "sha256": hashlib.sha256(
                        dumps_strict(receipt_material).encode("utf-8")
                    ).hexdigest(),
                },
            },
            truncated=truncated,
        )

    def get_close_series(self, query: Stage10DailyPriceQuery) -> TimeSeries:
        """Return the canonical close field for existing return consumers."""

        (
            migrations,
            instrument,
            rows,
            identity_warnings,
            selection_warnings,
            truncated,
        ) = self._selected_rows(query)
        return self._series_from_rows(
            query=query,
            field="close",
            migrations=migrations,
            instrument=instrument,
            rows=rows,
            identity_warnings=identity_warnings,
            selection_warnings=selection_warnings,
            truncated=truncated,
        )

    def get_ohlc_series(
        self, query: Stage10DailyPriceQuery
    ) -> tuple[TimeSeries, TimeSeries, TimeSeries, TimeSeries]:
        """Return open, high, low, and close from one immutable store read."""

        (
            migrations,
            instrument,
            rows,
            identity_warnings,
            selection_warnings,
            truncated,
        ) = self._selected_rows(query)
        for row in rows:
            self._validate_ohlc_row(row)
        series = tuple(
            self._series_from_rows(
                query=query,
                field=field,
                migrations=migrations,
                instrument=instrument,
                rows=rows,
                identity_warnings=identity_warnings,
                selection_warnings=selection_warnings,
                truncated=truncated,
            )
            for field in OHLC_FIELDS
        )
        return series[0], series[1], series[2], series[3]


    def list_available_tickers(
        self, query: Stage10AvailableTickerQuery
    ) -> Stage10AvailableTickerSelection:
        """List current Stage 10 instruments with a current daily-price row."""

        if not isinstance(query, Stage10AvailableTickerQuery):
            raise ValidationError(
                "Stage 10 available tickers require a typed query"
            )
        with _quiet_market_connection(self._store_map) as connection:
            migrations = _reconcile_store_contract(connection, self._registry)
            rows = list(
                connection.execute(
                    """
                    SELECT instrument.instrument_id,
                           instrument.provider_symbol,
                           instrument.asset_type,
                           instrument.display_name,
                           instrument.exchange_code
                    FROM stage10_instruments AS instrument
                    WHERE instrument.provider=?
                      AND instrument.currency_segment=?
                      AND EXISTS (
                          SELECT 1
                          FROM stage10_daily_prices AS price
                          JOIN stage10_daily_price_versions AS version
                            ON version.version_id=price.current_version_id
                          JOIN stage10_daily_price_captures AS capture
                            ON capture.capture_id=version.capture_id
                          WHERE price.instrument_id=instrument.instrument_id
                            AND price.provider=?
                            AND price.price_variant=?
                            AND price.currency_segment=?
                      )
                    ORDER BY instrument.provider_symbol COLLATE BINARY,
                             instrument.instrument_id
                    LIMIT ?
                    """,
                    (
                        PROVIDER,
                        CURRENCY_SEGMENT,
                        PROVIDER,
                        PRICE_VARIANT,
                        CURRENCY_SEGMENT,
                        query.limit + 1,
                    ),
                )
            )
        truncated = len(rows) > query.limit
        selected = tuple(
            Stage10AvailableTicker(
                ticker=str(row["provider_symbol"]),
                instrument_id=str(row["instrument_id"]),
                asset_type=str(row["asset_type"]),
                display_name=(
                    None
                    if row["display_name"] is None
                    else str(row["display_name"])
                ),
                exchange_code=(
                    None
                    if row["exchange_code"] is None
                    else str(row["exchange_code"])
                ),
            )
            for row in rows[: query.limit]
        )
        receipt_material = {
            "availability_scope": "current_retrievable_stage10_daily_price",
            "currency_segment": CURRENCY_SEGMENT,
            "migrations": [
                {
                    "migration_id": str(row["migration_id"]),
                    "sha256": str(row["sha256"]),
                }
                for row in migrations
            ],
            "price_variant": PRICE_VARIANT,
            "provider": PROVIDER,
            "selected": [item.receipt_mapping() for item in selected],
            "truncated": truncated,
        }
        semantic_id = stable_id(
            "stage10_available_tickers",
            hashlib.sha256(
                dumps_strict(receipt_material).encode("utf-8")
            ).hexdigest(),
        )
        return Stage10AvailableTickerSelection(
            tickers=selected,
            migration_ids=tuple(str(row["migration_id"]) for row in migrations),
            semantic_id=semantic_id,
            truncated=truncated,
        )


__all__ = (
    "CURRENCY_SEGMENT",
    "DATASET_ID",
    "EVIDENCE_DATASET_ID",
    "IDENTITY_DATASET_ID",
    "OHLC_FIELDS",
    "PRICE_VARIANT",
    "PROVIDER",
    "Stage10DailyPriceQuery",
    "Stage10DailyPriceRepository",
    "Stage10AvailableTicker",
    "Stage10AvailableTickerQuery",
    "Stage10AvailableTickerSelection",
)
