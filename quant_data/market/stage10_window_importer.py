"""Additive, offline-safe publisher for frozen Stage 10 history windows.

This module has no provider transport, credential, roster-journal, scheduler,
or default-path logic.  It accepts one already parsed
``FmpStage10WindowCapture`` at a time, binds it to a previously published
Stage 10 instrument, and writes immutable evidence plus canonical OHLCV facts
through :class:`~quant_data.ingestion.IngestionCoordinator`.

The existing Stage 10 ``0010`` relations remain authoritative.  A successful
window capture always gets an immutable raw capture/artifact/snapshot; rows
that exactly match the current canonical value make no new version, while a
changed overlap appends the next correction and advances only its current
pointer.  Empty windows are intentionally rejected here: the manual runner
owns their durable, no-retry journal outcome rather than manufacturing a
canonical price publication from no facts.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import (
    HeldWriteLocks,
    StoreMap,
    StoreRole,
    canonical_path_uri,
    read_connection,
    stable_id,
)
from .fmp_bulk_daily_prices import FMP_STAGE10_PRICE_PATH, FmpStage10PriceRow
from .stage10_history_importer import (
    STAGE10_CURRENCY_SEGMENT,
    STAGE10_DAILY_HISTORY_COLLECTOR_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_RESOURCE,
    STAGE10_MIGRATION_SHA256,
    STAGE10_PRICE_NORMALIZATION_VERSION,
    STAGE10_PRICE_VARIANT,
    STAGE10_UNIVERSES_DATASET_ID,
)
from .stage10_history_windows import (
    FmpStage10WindowCapture,
    STAGE10_HISTORY_WINDOWS,
)
from .stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    Stage10MarketScope,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")
_ASSET_TYPES = frozenset({"equity", "etf", "index"})
_WINDOW_HISTORY_POLICY = "explicit_inclusive_five_year_windows_from_1990"
_WINDOW_SOURCE_REFERENCE = "fmp/stable/historical-price-eod/full"
_WINDOW_OUTPUT_DATASETS = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 window {field_name} is invalid")
    return value


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"Stage 10 window {field_name} must be an aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 10 window importer requires explicit stores")
    return tuple(
        (role.value, canonical_path_uri(path)) for role, path in store_map.items()
    )


def _validate_scope(scope: object) -> Stage10MarketScope:
    if not isinstance(scope, Stage10MarketScope):
        raise ValidationError("Stage 10 window importer requires a reviewed scope")
    if (
        scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or _sha256_json(scope.manifest_mapping()) != REVIEWED_STAGE10_MANIFEST_SHA256
        or scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or scope.provider != "fmp"
        or scope.price_history.endpoint_path != FMP_STAGE10_PRICE_PATH
        or scope.price_history.interval != "daily"
        or scope.price_history.history_policy
        != "earliest_available_per_provider_symbol"
        or scope.price_history.price_variant != STAGE10_PRICE_VARIANT
        or scope.price_history.currency_policy != "provider_declared_no_conversion"
        or scope.price_history.volume_policy
        != "provider_value_nonnegative_zero_allowed"
        or len(scope.curated_etfs) != 95
        or len(scope.major_indexes) != 15
    ):
        raise ValidationError("Stage 10 window scope is not the reviewed immutable profile")
    return scope


def _validate_registry(registry: object) -> Registry:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.6.0"
        or registry.registry_version != "2.8.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 10 window importer requires registry 1.6.0/2.8.0")
    migrations = [item for item in registry.migrations if item.id == STAGE10_MIGRATION_ID]
    if len(migrations) != 1:
        raise ValidationError("Stage 10 window migration is not registered exactly once")
    migration = migrations[0]
    if (
        migration.store != StoreRole.MARKET.value
        or migration.ordinal != 10
        or migration.resource != STAGE10_MIGRATION_RESOURCE
        or migration.sha256 != STAGE10_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
    ):
        raise ValidationError("Stage 10 window migration declaration is invalid")
    datasets = {item.id: item for item in registry.datasets}
    for dataset_id in _WINDOW_OUTPUT_DATASETS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != StoreRole.MARKET.value
            or not dataset.active
            or dataset.tool_ids
            or dataset.dashboard_ids
            or dataset.export_ids
        ):
            raise ValidationError("Stage 10 window dataset declaration is invalid")
    evidence = datasets[STAGE10_EVIDENCE_DATASET_ID]
    prices = datasets[STAGE10_DAILY_PRICES_DATASET_ID]
    if (
        STAGE10_DAILY_HISTORY_COLLECTOR_ID not in evidence.collector_ids
        or tuple(prices.collector_ids) != (STAGE10_DAILY_HISTORY_COLLECTOR_ID,)
        or STAGE10_INSTRUMENTS_DATASET_ID not in datasets
        or STAGE10_UNIVERSES_DATASET_ID not in datasets
    ):
        raise ValidationError("Stage 10 window collector bindings are invalid")
    collectors = [
        item
        for item in registry.collectors
        if item.get("id") == STAGE10_DAILY_HISTORY_COLLECTOR_ID
    ]
    if len(collectors) != 1:
        raise ValidationError("Stage 10 window collector is not registered exactly once")
    collector = collectors[0]
    if (
        collector.get("handler") != "market.stage10_fmp_daily_history"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("input_datasets", ()))
        != (STAGE10_INSTRUMENTS_DATASET_ID, STAGE10_UNIVERSES_DATASET_ID)
        or tuple(collector.get("output_datasets", ())) != _WINDOW_OUTPUT_DATASETS
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise ValidationError("Stage 10 window collector declaration is invalid")
    return registry


@dataclass(frozen=True, slots=True)
class _PublishedInstrument:
    instrument_id: str
    provider_symbol: str
    asset_type: str
    display_name: str | None
    first_trade_date: str | None

    @property
    def identity_seed_sha256(self) -> str:
        return _sha256_json(
            {
                "identity_version": "stage10.fmp.instrument.v1",
                "provider": "fmp",
                "provider_symbol": self.provider_symbol,
                "asset_type": self.asset_type,
                "currency_segment": STAGE10_CURRENCY_SEGMENT,
            }
        )


@dataclass(frozen=True, slots=True)
class _WindowPublication:
    instrument: _PublishedInstrument
    capture: FmpStage10WindowCapture
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    normalized_window_sha256: str
    semantic_identity: str
    captured_at: str
    started_at: str
    run_id: str
    artifact_id: str
    snapshot_id: str
    capture_id: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    store_map: StoreMap
    registry: Registry
    scope: Stage10MarketScope
    store_map_identity: tuple[tuple[str, str], ...]
    publication: _WindowPublication


class PreparedStage10WindowPublication:
    """Opaque, importer-bound candidate whose raw response is never repr'd."""

    __slots__ = ("__weakref__",)


_PREPARED: "weakref.WeakKeyDictionary[PreparedStage10WindowPublication, _PreparedState]" = (
    weakref.WeakKeyDictionary()
)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("Stage 10 window price value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _current_version(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    trade_date: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT version.version_id, version.correction_sequence,
               version.open_value, version.high_value, version.low_value,
               version.close_value, version.volume
        FROM stage10_daily_prices AS current
        JOIN stage10_daily_price_versions AS version
          ON version.version_id=current.current_version_id
        WHERE current.instrument_id=? AND current.trade_date=?
          AND current.provider='fmp' AND current.price_variant=?
          AND current.currency_segment=?
        """,
        (instrument_id, trade_date, STAGE10_PRICE_VARIANT, STAGE10_CURRENCY_SEGMENT),
    ).fetchone()


def _same_price_values(current: sqlite3.Row, row: FmpStage10PriceRow) -> bool:
    return (
        current["open_value"] == _decimal_text(row.open_value)
        and current["high_value"] == _decimal_text(row.high_value)
        and current["low_value"] == _decimal_text(row.low_value)
        and current["close_value"] == _decimal_text(row.close_value)
        and current["volume"] == row.volume
    )


class Stage10HistoryWindowImporter:
    """Publish nonempty immutable Stage 10 window captures without transport.

    The public preparation method performs only read-only validation.  The
    public publication method then enters one coordinator-owned, short market
    transaction.  Candidates are issuer-bound and cannot be replayed through
    another importer or another physical store map.
    """

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        scope: Stage10MarketScope,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("Stage 10 window importer requires explicit stores and a clock")
        self.store_map = store_map
        self.registry = _validate_registry(registry)
        self.scope = _validate_scope(scope)
        self._clock = clock
        self._coordinator = IngestionCoordinator(
            store_map, code_version="stage10.window.1.0.0"
        )
        self._owner_token = object()

    def _published_instrument(self, symbol: str) -> _PublishedInstrument:
        if (
            not isinstance(symbol, str)
            or _SYMBOL.fullmatch(symbol) is None
            or symbol in {"^RUT", "IWM"}
            or "russell" in symbol.casefold()
        ):
            raise ValidationError("Stage 10 window symbol is outside the approved scope")
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            row = connection.execute(
                """
                SELECT instrument_id, provider, provider_symbol, asset_type,
                       display_name, exchange_code, currency_segment,
                       first_trade_date, identity_seed_sha256
                FROM stage10_instruments
                WHERE provider='fmp' AND provider_symbol=?
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            raise ValidationError("Stage 10 window requires a published scoped instrument")
        plan = _PublishedInstrument(
            instrument_id=str(row["instrument_id"]),
            provider_symbol=str(row["provider_symbol"]),
            asset_type=str(row["asset_type"]),
            display_name=None if row["display_name"] is None else str(row["display_name"]),
            first_trade_date=(
                None if row["first_trade_date"] is None else str(row["first_trade_date"])
            ),
        )
        if (
            plan.provider_symbol != symbol
            or plan.asset_type not in _ASSET_TYPES
            or row["provider"] != "fmp"
            or row["exchange_code"] is not None
            or row["currency_segment"] != STAGE10_CURRENCY_SEGMENT
            or row["identity_seed_sha256"] != plan.identity_seed_sha256
        ):
            raise ConflictError("Published Stage 10 window instrument identity is invalid")
        return plan

    @staticmethod
    def _validate_capture(capture: object) -> FmpStage10WindowCapture:
        if not isinstance(capture, FmpStage10WindowCapture):
            raise ValidationError("Stage 10 window capture must already be parsed")
        if (
            capture.prepared.window not in STAGE10_HISTORY_WINDOWS
            or capture.disposition != "complete"
            or not capture.rows
            or capture.response.status != 200
            or capture.response.content_type.split(";", 1)[0].strip().lower()
            != "application/json"
            or capture.raw_bytes_sha256 != _sha256_bytes(capture.raw_bytes)
            or _SHA256.fullmatch(capture.semantic_sha256) is None
        ):
            raise ValidationError("Stage 10 window capture is incomplete or invalid")
        symbol = capture.prepared.symbol
        if symbol in {"^RUT", "IWM"} or "russell" in symbol.casefold():
            raise ValidationError("Stage 10 window symbol is outside the approved scope")
        prior_date: str | None = None
        source_rows: set[int] = set()
        for row in capture.rows:
            if (
                not isinstance(row, FmpStage10PriceRow)
                or row.symbol != symbol
                or not capture.prepared.window.start_date
                <= row.trade_date
                <= capture.prepared.window.end_date
                or (prior_date is not None and row.trade_date <= prior_date)
                or row.source_row in source_rows
            ):
                raise ValidationError("Stage 10 window rows are not a complete ordered capture")
            prior_date = row.trade_date
            source_rows.add(row.source_row)
        if source_rows != set(range(1, len(capture.rows) + 1)):
            raise ValidationError("Stage 10 window source-row lineage is invalid")
        return capture

    def _new_prepared(
        self, publication: _WindowPublication
    ) -> PreparedStage10WindowPublication:
        prepared = PreparedStage10WindowPublication()
        _PREPARED[prepared] = _PreparedState(
            owner_token=self._owner_token,
            store_map=self.store_map,
            registry=self.registry,
            scope=self.scope,
            store_map_identity=_store_map_identity(self.store_map),
            publication=publication,
        )
        return prepared

    def _prepared_state(self, prepared: object) -> _PreparedState:
        if not isinstance(prepared, PreparedStage10WindowPublication):
            raise ValidationError("Stage 10 window publication requires a prepared candidate")
        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.store_map is not self.store_map
            or state.registry is not self.registry
            or state.scope is not self.scope
            or state.store_map_identity != _store_map_identity(self.store_map)
        ):
            raise ValidationError("Stage 10 window prepared candidate is foreign or expired")
        return state

    def prepare_window_capture(
        self, capture: FmpStage10WindowCapture
    ) -> PreparedStage10WindowPublication:
        """Prepare one nonempty, already parsed historical-window capture.

        This method is read-only and does not request or parse provider data.
        It binds exact inclusive dates and the normalized window digest into
        the semantic identity before a later call to :meth:`publish_prepared`.
        """

        validated = self._validate_capture(capture)
        instrument = self._published_instrument(validated.prepared.symbol)
        captured_at = _utc_text(self._clock(), "captured_at")
        window = validated.prepared.window
        if window.end_date > captured_at[:10]:
            raise ValidationError("Stage 10 window extends beyond its capture time")
        if (
            instrument.first_trade_date is not None
            and validated.rows[0].trade_date < instrument.first_trade_date
        ):
            raise ValidationError("Stage 10 window history predates the instrument")
        base_request_scope = validated.prepared.request_scope()
        expected_base_request_scope = {
            "provider": "fmp",
            "endpoint_path": self.scope.price_history.endpoint_path,
            "symbol": instrument.provider_symbol,
            "window_id": window.window_id,
            "start_date": window.start_date,
            "end_date": window.end_date,
        }
        if base_request_scope != expected_base_request_scope:
            raise ValidationError("Stage 10 window base request scope is invalid")
        request_scope: dict[str, object] = {
            **base_request_scope,
            "from": window.start_date,
            "to": window.end_date,
            "interval": self.scope.price_history.interval,
            "history_policy": _WINDOW_HISTORY_POLICY,
            "price_variant": self.scope.price_history.price_variant,
            "currency_policy": self.scope.price_history.currency_policy,
            "volume_policy": self.scope.price_history.volume_policy,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
        }
        request_scope_sha256 = _sha256_json(request_scope)
        normalized_window_sha256 = _require_digest(
            validated.semantic_sha256, "capture semantic_sha256"
        )
        semantic_identity = _sha256_json(
            {
                "collector_id": STAGE10_DAILY_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "request_scope": request_scope,
                "normalized_window_history_sha256": normalized_window_sha256,
            }
        )
        _require_digest(semantic_identity, "semantic_identity")
        return self._new_prepared(
            _WindowPublication(
                instrument=instrument,
                capture=validated,
                request_scope=request_scope,
                request_scope_sha256=request_scope_sha256,
                normalized_window_sha256=normalized_window_sha256,
                semantic_identity=semantic_identity,
                captured_at=captured_at,
                started_at=captured_at,
                run_id=stable_id(
                    "stage10_daily_price_window_run",
                    STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity,
                ),
                artifact_id=stable_id(
                    "stage10_daily_price_window_artifact",
                    STAGE10_EVIDENCE_DATASET_ID,
                    instrument.provider_symbol,
                    window.window_id,
                    validated.raw_bytes_sha256,
                    request_scope_sha256,
                ),
                snapshot_id=stable_id(
                    "stage10_daily_price_window_snapshot",
                    STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity,
                ),
                capture_id=stable_id(
                    "stage10_daily_price_window_capture",
                    STAGE10_EVIDENCE_DATASET_ID,
                    semantic_identity,
                ),
            )
        )

    def publish_prepared(
        self,
        prepared: PreparedStage10WindowPublication,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Atomically publish a prior candidate with no provider activity."""

        state = self._prepared_state(prepared)
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self.store_map, StoreRole.MARKET)
        return self._publish_window(state.publication, held_locks=held_locks)

    def _publish_window(
        self,
        publication: _WindowPublication,
        *,
        held_locks: HeldWriteLocks | None,
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            row = connection.execute(
                """
                SELECT provider, provider_symbol, asset_type, display_name,
                       exchange_code, currency_segment, first_trade_date,
                       identity_seed_sha256
                FROM stage10_instruments WHERE instrument_id=?
                """,
                (publication.instrument.instrument_id,),
            ).fetchone()
            expected_identity = (
                "fmp",
                publication.instrument.provider_symbol,
                publication.instrument.asset_type,
                publication.instrument.display_name,
                None,
                STAGE10_CURRENCY_SEGMENT,
                publication.instrument.first_trade_date,
                publication.instrument.identity_seed_sha256,
            )
            if row is None or tuple(row) != expected_identity:
                raise ConflictError("Stage 10 window instrument binding is invalid")

            capture = publication.capture
            window = capture.prepared.window
            existing_raw = connection.execute(
                """
                SELECT capture_id, response_bytes
                FROM stage10_daily_price_captures
                WHERE instrument_id=? AND response_sha256=?
                """,
                (
                    publication.instrument.instrument_id,
                    capture.raw_bytes_sha256,
                ),
            ).fetchone()
            if existing_raw is not None:
                if bytes(existing_raw["response_bytes"]) != capture.raw_bytes:
                    raise ConflictError("Stage 10 window raw evidence digest conflicts")
                # The immutable 0010 schema permits a raw response digest once
                # per instrument.  It is safe to reuse only when that retained
                # evidence makes no new canonical assertion; a correction would
                # require a new capture/run/artifact lineage and must fail closed.
                for source in capture.rows:
                    current = _current_version(
                        connection,
                        instrument_id=publication.instrument.instrument_id,
                        trade_date=source.trade_date,
                    )
                    if current is None or not _same_price_values(current, source):
                        raise ConflictError(
                            "Stage 10 window raw evidence cannot bind a new correction"
                        )
                written = 0
            else:
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_captures (
                        capture_id, dataset_id, provider, instrument_id, provider_symbol,
                        endpoint_path, scope_manifest_sha256, request_scope_json,
                        request_scope_sha256, response_sha256, response_bytes, http_status,
                        content_type, semantic_identity, completeness, artifact_id,
                        snapshot_id, captured_at, captured_precision, earliest_trade_date,
                        latest_trade_date, row_count, normalization_version, run_id
                    ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, 200, 'application/json',
                              ?, 'complete', ?, ?, ?, 'datetime', ?, ?, ?, ?, ?)
                    """,
                    (
                        publication.capture_id,
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.instrument.instrument_id,
                        publication.instrument.provider_symbol,
                        FMP_STAGE10_PRICE_PATH,
                        self.scope.manifest_sha256,
                        dumps_strict(dict(publication.request_scope)),
                        publication.request_scope_sha256,
                        capture.raw_bytes_sha256,
                        capture.raw_bytes,
                        publication.semantic_identity,
                        publication.artifact_id,
                        publication.snapshot_id,
                        publication.captured_at,
                        capture.rows[0].trade_date,
                        capture.rows[-1].trade_date,
                        len(capture.rows),
                        STAGE10_PRICE_NORMALIZATION_VERSION,
                        active_run_id,
                    ),
                )
                written = 1
            changed_rows = 0
            for source in capture.rows:
                current = _current_version(
                    connection,
                    instrument_id=publication.instrument.instrument_id,
                    trade_date=source.trade_date,
                )
                if current is not None and _same_price_values(current, source):
                    continue
                correction_sequence = (
                    1 if current is None else int(current["correction_sequence"]) + 1
                )
                supersedes = None if current is None else str(current["version_id"])
                version_id = stable_id(
                    "stage10_daily_price_version",
                    publication.instrument.instrument_id,
                    source.trade_date,
                    STAGE10_PRICE_VARIANT,
                    STAGE10_CURRENCY_SEGMENT,
                    str(correction_sequence),
                    publication.semantic_identity,
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_versions (
                        version_id, instrument_id, trade_date, provider, price_variant,
                        currency_segment, open_value, high_value, low_value, close_value,
                        volume, available_at, available_precision, captured_at,
                        captured_precision, correction_sequence, supersedes_version_id,
                        capture_id, artifact_id, snapshot_id, run_id, source_row
                    ) VALUES (?, ?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?,
                              'datetime', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        publication.instrument.instrument_id,
                        source.trade_date,
                        STAGE10_PRICE_VARIANT,
                        STAGE10_CURRENCY_SEGMENT,
                        _decimal_text(source.open_value),
                        _decimal_text(source.high_value),
                        _decimal_text(source.low_value),
                        _decimal_text(source.close_value),
                        source.volume,
                        publication.captured_at,
                        publication.captured_at,
                        correction_sequence,
                        supersedes,
                        publication.capture_id,
                        publication.artifact_id,
                        publication.snapshot_id,
                        active_run_id,
                        source.source_row,
                    ),
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_prices (
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', ?, ?, ?)
                        """,
                        (
                            publication.instrument.instrument_id,
                            source.trade_date,
                            STAGE10_PRICE_VARIANT,
                            STAGE10_CURRENCY_SEGMENT,
                            version_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE stage10_daily_prices SET current_version_id=?
                        WHERE instrument_id=? AND trade_date=? AND provider='fmp'
                          AND price_variant=? AND currency_segment=?
                        """,
                        (
                            version_id,
                            publication.instrument.instrument_id,
                            source.trade_date,
                            STAGE10_PRICE_VARIANT,
                            STAGE10_CURRENCY_SEGMENT,
                        ),
                    )
                changed_rows += 1
                written += 1
            artifact = ArtifactWrite(
                artifact_id=publication.artifact_id,
                dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                content_sha256=capture.raw_bytes_sha256,
                media_type="application/json",
                byte_count=len(capture.raw_bytes),
                source_reference=_WINDOW_SOURCE_REFERENCE,
                request_scope=dict(publication.request_scope),
                captured_at=publication.captured_at,
                captured_precision="datetime",
                normalization_version=STAGE10_PRICE_NORMALIZATION_VERSION,
            )
            quality = (
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_window_price_quality",
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                    rule_id="response_digest_scope_and_window",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    artifact_id=publication.artifact_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "rows": len(capture.rows),
                        "window_id": window.window_id,
                        "from": window.start_date,
                        "to": window.end_date,
                    },
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_window_price_quality",
                        STAGE10_DAILY_PRICES_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    rule_id="window_ohlcv_and_corrections",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "rows": len(capture.rows),
                        "changed_rows": changed_rows,
                        "window_id": window.window_id,
                        "from": window.start_date,
                        "to": window.end_date,
                    },
                ),
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(capture.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(publication.artifact_id,),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
            output_dataset_ids=_WINDOW_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.started_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.capture.rows),
            writer=writer,
            held_locks=held_locks,
        )


__all__ = (
    "PreparedStage10WindowPublication",
    "Stage10HistoryWindowImporter",
)
