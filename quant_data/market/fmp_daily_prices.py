"""One bounded, manually invoked FMP daily-price backfill.

Network capture and normalization finish before the shared ingestion
coordinator opens a database lock or transaction.  The adapter is deliberately
limited to the reviewed SPY July-2026 request and the isolated Stage 9 market
relations; it cannot write the frozen fixture tables.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode

from ..contracts import IngestionReceipt
from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, canonical_path_uri, stable_id


FMP_HOST = "financialmodelingprep.com"
FMP_ENDPOINT_PATH = "/stable/historical-price-eod/full"
FMP_COLLECTOR_ID = "fmp.market.daily_price_backfill"
FMP_EVIDENCE_DATASET_ID = "market.fmp.daily_price_evidence"
FMP_IDENTITY_DATASET_ID = "market.fmp.instruments"
FMP_CANONICAL_DATASET_ID = "market.fmp.daily_prices"
FMP_SYMBOL = "SPY"
FMP_START_DATE = "2026-07-01"
FMP_END_DATE = "2026-07-31"
FMP_PRICE_VARIANT = "fmp_full_eod_v1"
FMP_CURRENCY_SEGMENT = "USD"
FMP_ASSET_TYPE = "etf"
FMP_NORMALIZATION_VERSION = "fmp.daily_price.v1"
FMP_MAX_BYTES = 262_144
FMP_TIMEOUT_SECONDS = 30
FMP_APPROVED_TARGET_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage9-fmp-spy-202607"
)
FMP_EXPECTED_DATES = (
    "2026-07-01",
    "2026-07-02",
    "2026-07-06",
    "2026-07-07",
    "2026-07-08",
    "2026-07-09",
    "2026-07-10",
    "2026-07-13",
    "2026-07-14",
    "2026-07-15",
    "2026-07-16",
    "2026-07-17",
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
    "2026-07-27",
    "2026-07-28",
    "2026-07-29",
    "2026-07-30",
    "2026-07-31",
)

_RESPONSE_KEYS = frozenset(
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
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807

def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("FMP live transport requires explicit stores")
    return tuple(
        (role.value, canonical_path_uri(path)) for role, path in store_map.items()
    )


def _approved_live_store_map_identity() -> tuple[tuple[str, str], ...]:
    stores = FMP_APPROVED_TARGET_ROOT / "stores"
    return tuple(
        (role.value, canonical_path_uri(stores / f"{role.value}.sqlite"))
        for role in StoreRole
    )


def _require_approved_live_store_map(
    store_map: StoreMap,
) -> tuple[tuple[str, str], ...]:
    identity = _store_map_identity(store_map)
    if identity != _approved_live_store_map_identity():
        raise ValidationError(
            "FMP live transport requires the approved Stage 9 target store layout"
        )
    return identity


def _live_transport_store_identity(
    transport: FmpDailyPriceTransport,
    store_map: StoreMap,
) -> tuple[tuple[str, str], ...] | None:
    """Validate an opt-in live transport before its first network call.

    Injected offline transports do not implement the private binding hook and
    remain usable with explicit temporary maps. The standard-library transport
    and any response-preserving wrapper around it do implement the hook.
    """

    validator = getattr(transport, "_validate_stage9_live_store_map", None)
    if validator is None:
        return None
    if not callable(validator):
        raise ValidationError("FMP live transport store binding is invalid")
    identity = validator(store_map)
    expected = _store_map_identity(store_map)
    if identity != expected or identity != _approved_live_store_map_identity():
        raise ValidationError("FMP live transport store binding is invalid")
    return identity
_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValidationError(f"FMP {field_name} must be a finite JSON number")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"FMP {field_name} must be a finite JSON number") from exc
    if not result.is_finite():
        raise ValidationError(f"FMP {field_name} must be finite")
    return result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("FMP price value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _volume(value: object) -> int:
    parsed = _decimal(value, "volume")
    if parsed != parsed.to_integral_value():
        raise ValidationError("FMP volume must be an integer")
    result = int(parsed)
    if result < 0 or result > _MAX_SQLITE_INTEGER:
        raise ValidationError("FMP volume is outside the supported range")
    return result


@dataclass(frozen=True, slots=True)
class FmpDailyPriceRequest:
    """The one reviewed provider request; fields cannot broaden the live scope."""

    symbol: str = FMP_SYMBOL
    start_date: str = FMP_START_DATE
    end_date: str = FMP_END_DATE
    price_variant: str = FMP_PRICE_VARIANT
    currency_segment: str = FMP_CURRENCY_SEGMENT

    def __post_init__(self) -> None:
        if (
            self.symbol != FMP_SYMBOL
            or self.start_date != FMP_START_DATE
            or self.end_date != FMP_END_DATE
            or self.price_variant != FMP_PRICE_VARIANT
            or self.currency_segment != FMP_CURRENCY_SEGMENT
        ):
            raise ValidationError("FMP request is outside the approved Stage 9 scope")

    def request_scope(self) -> dict[str, object]:
        return {
            "provider": "fmp",
            "endpoint_path": FMP_ENDPOINT_PATH,
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "price_variant": self.price_variant,
            "currency_segment": self.currency_segment,
        }


@dataclass(frozen=True, slots=True)
class CapturedFmpResponse:
    """Bounded response bytes returned by an injected or production transport."""

    status: int
    content_type: str
    body: bytes

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("FMP response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("FMP response content type is invalid")
        if not isinstance(self.body, bytes):
            raise ValidationError("FMP response body must be bytes")
        if not self.body:
            raise ValidationError("FMP response body is empty")
        if len(self.body) > FMP_MAX_BYTES:
            raise ResourceLimitError("FMP response exceeds the reviewed byte bound")


@runtime_checkable
class FmpDailyPriceTransport(Protocol):
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpResponse: ...


class StdlibFmpDailyPriceTransport:
    """TLS-verified, no-redirect stdlib transport for the one FMP host."""

    def __init__(self, store_map: StoreMap) -> None:
        self._store_map_identity = _require_approved_live_store_map(store_map)

    def _validate_stage9_live_store_map(
        self, store_map: StoreMap
    ) -> tuple[tuple[str, str], ...]:
        identity = _require_approved_live_store_map(store_map)
        if identity != self._store_map_identity:
            raise ValidationError("FMP live transport store binding is invalid")
        return identity
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpResponse:
        expected_query = {
            "symbol": FMP_SYMBOL,
            "from": FMP_START_DATE,
            "to": FMP_END_DATE,
        }
        if (
            path != FMP_ENDPOINT_PATH
            or dict(query) != expected_query
            or set(headers) != {"apikey", "Accept"}
            or headers.get("Accept") != "application/json"
            or not isinstance(headers.get("apikey"), str)
            or _API_KEY.fullmatch(headers["apikey"]) is None
            or timeout_seconds != FMP_TIMEOUT_SECONDS
            or max_bytes != FMP_MAX_BYTES
        ):
            raise ValidationError("FMP transport request is outside the approved scope")
        target = f"{path}?{urlencode(expected_query)}"
        connection = http.client.HTTPSConnection(FMP_HOST, timeout=timeout_seconds)
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise ResourceLimitError("FMP response exceeds the reviewed byte bound")
                except ValueError as exc:
                    raise StoreUnavailableError("FMP provider response was unavailable") from exc
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("FMP response exceeds the reviewed byte bound")
            return CapturedFmpResponse(
                status=response.status,
                content_type=response.getheader("Content-Type") or "application/octet-stream",
                body=body,
            )
        except (ResourceLimitError, ValidationError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP provider request failed") from exc
        finally:
            connection.close()


class PreparedFmpDailyPriceBackfill:
    """Opaque capability for one validated response; it exposes no raw bytes."""

    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _FmpRow:
    trade_date: str
    open_value: Decimal
    high_value: Decimal
    low_value: Decimal
    close_value: Decimal
    volume: int
    source_row: int

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "symbol": FMP_SYMBOL,
            "trade_date": self.trade_date,
            "open": _decimal_text(self.open_value),
            "high": _decimal_text(self.high_value),
            "low": _decimal_text(self.low_value),
            "close": _decimal_text(self.close_value),
            "volume": self.volume,
            "price_variant": FMP_PRICE_VARIANT,
            "currency_segment": FMP_CURRENCY_SEGMENT,
        }


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    store_map: StoreMap
    registry: Registry
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    response: CapturedFmpResponse
    response_sha256: str
    rows: tuple[_FmpRow, ...]
    semantic_identity: str
    started_at: str
    captured_at: str
    instrument_id: str
    identity_seed_sha256: str
    run_id: str
    capture_id: str
    artifact_id: str
    snapshot_id: str
    store_map_identity: tuple[tuple[str, str], ...]
    live_store_map_identity: tuple[tuple[str, str], ...] | None


_PREPARED: "weakref.WeakKeyDictionary[PreparedFmpDailyPriceBackfill, _PreparedState]" = (
    weakref.WeakKeyDictionary()
)


def _parse_rows(body: bytes) -> tuple[_FmpRow, ...]:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("FMP response must be strict UTF-8") from exc
    try:
        value = loads_strict(text, max_bytes=FMP_MAX_BYTES)
    except (ValidationError, ResourceLimitError):
        raise ValidationError("FMP response is not strict bounded JSON") from None
    if not isinstance(value, list) or len(value) != len(FMP_EXPECTED_DATES):
        raise ValidationError("FMP response must contain exactly 22 daily rows")
    rows: list[_FmpRow] = []
    seen: set[str] = set()
    for source_row, raw in enumerate(value, start=1):
        if not isinstance(raw, dict) or frozenset(raw) != _RESPONSE_KEYS:
            raise ValidationError("FMP response row shape is invalid")
        if raw["symbol"] != FMP_SYMBOL or not isinstance(raw["date"], str):
            raise ValidationError("FMP response contains an unexpected symbol or date")
        try:
            trade_date = date.fromisoformat(raw["date"]).isoformat()
        except ValueError as exc:
            raise ValidationError("FMP response date is invalid") from exc
        if not FMP_START_DATE <= trade_date <= FMP_END_DATE or trade_date in seen:
            raise ValidationError("FMP response date coverage is invalid")
        seen.add(trade_date)
        open_value = _decimal(raw["open"], "open")
        high_value = _decimal(raw["high"], "high")
        low_value = _decimal(raw["low"], "low")
        close_value = _decimal(raw["close"], "close")
        for name in ("change", "changePercent", "vwap"):
            _decimal(raw[name], name)
        if (
            min(open_value, high_value, low_value, close_value) <= 0
            or low_value > min(open_value, close_value)
            or high_value < max(open_value, close_value)
            or low_value > high_value
        ):
            raise ValidationError("FMP response contains inconsistent OHLC values")
        rows.append(
            _FmpRow(
                trade_date=trade_date,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                volume=_volume(raw["volume"]),
                source_row=source_row,
            )
        )
    rows.sort(key=lambda row: row.trade_date)
    if tuple(row.trade_date for row in rows) != FMP_EXPECTED_DATES:
        raise ValidationError("FMP response does not match the reviewed trading dates")
    return tuple(rows)


def normalized_fmp_daily_price_response(
    body: bytes,
) -> tuple[dict[str, object], ...]:
    """Return the exact stored OHLCV projection of reviewed response bytes.

    Reconciliation uses the same strict parser as ingestion so a capture digest
    and a plausible database state cannot be validated independently.
    """

    if not isinstance(body, bytes):
        raise ValidationError("FMP response body must be bytes")
    return tuple(row.semantic_mapping() for row in _parse_rows(body))


def _validate_registry(registry: Registry) -> None:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.5.0"
        or registry.registry_version != "2.7.0"
    ):
        raise ValidationError("FMP importer requires the reviewed Stage 9 registry")
    matches = [item for item in registry.collectors if item.get("id") == FMP_COLLECTOR_ID]
    if len(matches) != 1:
        raise ValidationError("FMP collector is not registered exactly once")
    collector = matches[0]
    if (
        collector.get("handler") != "market.fmp_daily_price"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("output_datasets", ()))
        != (FMP_EVIDENCE_DATASET_ID, FMP_IDENTITY_DATASET_ID, FMP_CANONICAL_DATASET_ID)
    ):
        raise ValidationError("FMP collector declaration is invalid")


def _current_version(
    connection: sqlite3.Connection,
    state: _PreparedState,
    row: _FmpRow,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT version.version_id, version.correction_sequence,
               version.open_value, version.high_value, version.low_value,
               version.close_value, version.volume
        FROM fmp_daily_prices AS current
        JOIN fmp_daily_price_versions AS version
          ON version.version_id=current.current_version_id
        WHERE current.instrument_id=? AND current.trade_date=?
          AND current.provider='fmp' AND current.price_variant=?
          AND current.currency_segment=?
        """,
        (
            state.instrument_id,
            row.trade_date,
            FMP_PRICE_VARIANT,
            FMP_CURRENCY_SEGMENT,
        ),
    ).fetchone()


def _same_values(current: sqlite3.Row, row: _FmpRow) -> bool:
    return (
        current["open_value"] == _decimal_text(row.open_value)
        and current["high_value"] == _decimal_text(row.high_value)
        and current["low_value"] == _decimal_text(row.low_value)
        and current["close_value"] == _decimal_text(row.close_value)
        and current["volume"] == row.volume
    )


class FmpDailyPriceImporter:
    """Prepare one FMP response outside locks and publish it atomically."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("FMP importer requires explicit stores and a clock")
        _validate_registry(registry)
        self.store_map = store_map
        self.registry = registry
        self._clock = clock
        self._owner_token = object()

    def prepare(
        self,
        request: FmpDailyPriceRequest,
        *,
        api_key: str,
        transport: FmpDailyPriceTransport,
    ) -> PreparedFmpDailyPriceBackfill:
        if not isinstance(request, FmpDailyPriceRequest):
            raise ValidationError("FMP prepare requires the reviewed request type")
        if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
            raise ValidationError("FMP credential is missing or invalid")
        if not isinstance(transport, FmpDailyPriceTransport):
            raise ValidationError("FMP transport does not implement the reviewed interface")
        live_store_map_identity = _live_transport_store_identity(
            transport, self.store_map
        )
        started_at = _utc_text(self._clock(), "started_at")
        response = transport.get(
            path=FMP_ENDPOINT_PATH,
            query={"symbol": FMP_SYMBOL, "from": FMP_START_DATE, "to": FMP_END_DATE},
            headers={"apikey": api_key, "Accept": "application/json"},
            timeout_seconds=FMP_TIMEOUT_SECONDS,
            max_bytes=FMP_MAX_BYTES,
        )
        if not isinstance(response, CapturedFmpResponse):
            raise ValidationError("FMP transport returned an invalid response type")
        captured_at = _utc_text(self._clock(), "captured_at")
        if response.status != 200 or response.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise StoreUnavailableError("FMP provider response was unavailable")
        rows = _parse_rows(response.body)
        scope = request.request_scope()
        scope_sha256 = _sha256_json(scope)
        response_sha256 = _sha256(response.body)
        semantic_material = {
            "collector_id": FMP_COLLECTOR_ID,
            "canonical_dataset_id": FMP_CANONICAL_DATASET_ID,
            "request_scope": scope,
            "normalization_version": FMP_NORMALIZATION_VERSION,
            "normalized_complete_batch": [row.semantic_mapping() for row in rows],
        }
        semantic_identity = _sha256_json(semantic_material)
        identity_material = {
            "provider": "fmp",
            "provider_symbol": FMP_SYMBOL,
            "asset_type": FMP_ASSET_TYPE,
            "currency_segment": FMP_CURRENCY_SEGMENT,
            "identity_version": "1.0.0",
        }
        identity_seed_sha256 = _sha256_json(identity_material)
        instrument_id = stable_id("fmp_instrument", identity_seed_sha256)
        run_id = stable_id("ingestion_run", FMP_CANONICAL_DATASET_ID, semantic_identity)
        capture_id = stable_id("fmp_capture", FMP_EVIDENCE_DATASET_ID, semantic_identity)
        artifact_id = stable_id("fmp_artifact", FMP_EVIDENCE_DATASET_ID, response_sha256, scope_sha256)
        snapshot_id = stable_id("fmp_snapshot", FMP_CANONICAL_DATASET_ID, semantic_identity)
        candidate = PreparedFmpDailyPriceBackfill()
        _PREPARED[candidate] = _PreparedState(
            owner_token=self._owner_token,
            store_map=self.store_map,
            registry=self.registry,
            request_scope=scope,
            request_scope_sha256=scope_sha256,
            response=response,
            response_sha256=response_sha256,
            rows=rows,
            semantic_identity=semantic_identity,
            started_at=started_at,
            captured_at=captured_at,
            instrument_id=instrument_id,
            identity_seed_sha256=identity_seed_sha256,
            run_id=run_id,
            capture_id=capture_id,
            artifact_id=artifact_id,
            snapshot_id=snapshot_id,
            store_map_identity=_store_map_identity(self.store_map),
            live_store_map_identity=live_store_map_identity,
        )
        return candidate

    def publish_prepared(
        self,
        prepared: PreparedFmpDailyPriceBackfill,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        if not isinstance(prepared, PreparedFmpDailyPriceBackfill):
            raise ValidationError("FMP publication requires a prepared candidate")
        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.store_map is not self.store_map
            or state.registry is not self.registry
        ):
            raise ValidationError("FMP prepared candidate is foreign or expired")
        current_store_map_identity = _store_map_identity(self.store_map)
        if state.store_map_identity != current_store_map_identity:
            raise ValidationError("FMP prepared candidate store binding is invalid")
        if state.live_store_map_identity is not None and (
            state.live_store_map_identity != current_store_map_identity
            or state.live_store_map_identity != _approved_live_store_map_identity()
        ):
            raise ValidationError("FMP prepared live candidate store binding is invalid")

        def writer(connection: sqlite3.Connection, run_id: str) -> WriteResult:
            written = 0
            identity = connection.execute(
                """
                SELECT provider, provider_symbol, asset_type, currency_segment,
                       identity_seed_sha256
                FROM fmp_instrument_identities WHERE instrument_id=?
                """,
                (state.instrument_id,),
            ).fetchone()
            if identity is None:
                connection.execute(
                    """
                    INSERT INTO fmp_instrument_identities (
                        instrument_id, provider, provider_symbol, asset_type,
                        currency_segment, identity_seed_sha256, effective_from,
                        captured_at, captured_precision, run_id
                    ) VALUES (?, 'fmp', 'SPY', 'etf', 'USD', ?, ?, ?, 'datetime', ?)
                    """,
                    (
                        state.instrument_id,
                        state.identity_seed_sha256,
                        FMP_START_DATE,
                        state.captured_at,
                        run_id,
                    ),
                )
                written += 1
            elif tuple(identity) != (
                "fmp",
                FMP_SYMBOL,
                FMP_ASSET_TYPE,
                FMP_CURRENCY_SEGMENT,
                state.identity_seed_sha256,
            ):
                raise ValidationError("FMP stable instrument identity conflicts with the store")

            connection.execute(
                """
                INSERT INTO fmp_daily_price_captures (
                    capture_id, dataset_id, provider, endpoint_path,
                    request_scope_json, request_scope_sha256, response_sha256,
                    response_bytes, http_status, content_type, semantic_identity,
                    completeness, artifact_id, snapshot_id, captured_at,
                    captured_precision, row_count, normalization_version, run_id
                ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, 200, ?, ?, 'complete',
                          ?, ?, ?, 'datetime', ?, ?, ?)
                """,
                (
                    state.capture_id,
                    FMP_EVIDENCE_DATASET_ID,
                    FMP_ENDPOINT_PATH,
                    dumps_strict(dict(state.request_scope)),
                    state.request_scope_sha256,
                    state.response_sha256,
                    state.response.body,
                    state.response.content_type,
                    state.semantic_identity,
                    state.artifact_id,
                    state.snapshot_id,
                    state.captured_at,
                    len(state.rows),
                    FMP_NORMALIZATION_VERSION,
                    run_id,
                ),
            )
            written += 1

            for row in state.rows:
                current = _current_version(connection, state, row)
                if current is not None and _same_values(current, row):
                    continue
                correction_sequence = 1 if current is None else int(current["correction_sequence"]) + 1
                supersedes = None if current is None else str(current["version_id"])
                version_id = stable_id(
                    "fmp_daily_price_version",
                    state.instrument_id,
                    row.trade_date,
                    FMP_PRICE_VARIANT,
                    FMP_CURRENCY_SEGMENT,
                    str(correction_sequence),
                    state.semantic_identity,
                )
                connection.execute(
                    """
                    INSERT INTO fmp_daily_price_versions (
                        version_id, instrument_id, trade_date, provider,
                        price_variant, currency_segment, open_value, high_value,
                        low_value, close_value, volume, available_at,
                        available_precision, captured_at, captured_precision,
                        correction_sequence, supersedes_version_id, capture_id,
                        artifact_id, snapshot_id, run_id, source_row
                    ) VALUES (?, ?, ?, 'fmp', ?, 'USD', ?, ?, ?, ?, ?, ?,
                              'datetime', ?, 'datetime', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        state.instrument_id,
                        row.trade_date,
                        FMP_PRICE_VARIANT,
                        _decimal_text(row.open_value),
                        _decimal_text(row.high_value),
                        _decimal_text(row.low_value),
                        _decimal_text(row.close_value),
                        row.volume,
                        state.captured_at,
                        state.captured_at,
                        correction_sequence,
                        supersedes,
                        state.capture_id,
                        state.artifact_id,
                        state.snapshot_id,
                        run_id,
                        row.source_row,
                    ),
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO fmp_daily_prices (
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', ?, 'USD', ?)
                        """,
                        (state.instrument_id, row.trade_date, FMP_PRICE_VARIANT, version_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE fmp_daily_prices SET current_version_id=?
                        WHERE instrument_id=? AND trade_date=? AND provider='fmp'
                          AND price_variant=? AND currency_segment='USD'
                        """,
                        (version_id, state.instrument_id, row.trade_date, FMP_PRICE_VARIANT),
                    )
                written += 1

            artifacts = (
                ArtifactWrite(
                    artifact_id=state.artifact_id,
                    dataset_id=FMP_EVIDENCE_DATASET_ID,
                    content_sha256=state.response_sha256,
                    media_type=state.response.content_type,
                    byte_count=len(state.response.body),
                    source_reference="fmp/stable/historical-price-eod/full",
                    request_scope=dict(state.request_scope),
                    captured_at=state.captured_at,
                    captured_precision="datetime",
                    normalization_version=FMP_NORMALIZATION_VERSION,
                ),
            )
            snapshot = SnapshotWrite(
                snapshot_id=state.snapshot_id,
                dataset_id=FMP_CANONICAL_DATASET_ID,
                semantic_identity=state.semantic_identity,
                scope=dict(state.request_scope),
                completeness="complete",
                row_count=len(state.rows),
                captured_at=state.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(state.artifact_id,),
            )
            quality_results = tuple(
                QualityWrite(
                    quality_result_id=stable_id("fmp_quality", dataset_id, state.semantic_identity),
                    dataset_id=dataset_id,
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=state.snapshot_id,
                    observed={"rows": len(state.rows), "complete": True},
                    snapshot_id=state.snapshot_id,
                )
                for dataset_id, rule_id in (
                    (FMP_EVIDENCE_DATASET_ID, "response_digest_and_scope"),
                    (FMP_IDENTITY_DATASET_ID, "reviewed_spy_identity"),
                    (FMP_CANONICAL_DATASET_ID, "expected_dates_and_ohlc"),
                )
            )
            return WriteResult(
                written_count=written,
                artifacts=artifacts,
                snapshot=snapshot,
                quality_results=quality_results,
            )

        coordinator = IngestionCoordinator(self.store_map, code_version="stage9.0.0")
        return coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=FMP_CANONICAL_DATASET_ID,
            output_dataset_ids=(
                FMP_EVIDENCE_DATASET_ID,
                FMP_IDENTITY_DATASET_ID,
                FMP_CANONICAL_DATASET_ID,
            ),
            semantic_identity=state.semantic_identity,
            run_id=state.run_id,
            command=FMP_COLLECTOR_ID,
            scope=dict(state.request_scope),
            started_at=state.started_at,
            completed_at=state.captured_at,
            fetched_count=len(state.rows),
            writer=writer,
            held_locks=held_locks,
        )


__all__ = (
    "CapturedFmpResponse",
    "FmpDailyPriceImporter",
    "FmpDailyPriceRequest",
    "FmpDailyPriceTransport",
    "PreparedFmpDailyPriceBackfill",
    "StdlibFmpDailyPriceTransport",
    "normalized_fmp_daily_price_response",
)
