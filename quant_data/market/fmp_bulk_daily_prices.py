"""Bounded Stage 10 FMP capture and parsing primitives.

This module deliberately stops before any registry, database, publication, or
job integration.  It prepares exactly one permitted provider request at a
time, lets callers inject a transport for offline tests, and returns immutable
captures whose representations do not expose credentials or response bytes.
"""

from __future__ import annotations

import hashlib
import http.client
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, canonical_path_uri, read_connection
from .stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    Stage10MarketScope,
)


FMP_STAGE10_HOST = "financialmodelingprep.com"
FMP_STAGE10_SP500_CONSTITUENT_PATH = "/stable/sp500-constituent"
FMP_STAGE10_NASDAQ_CONSTITUENT_PATH = "/stable/nasdaq-constituent"
FMP_STAGE10_DOWJONES_CONSTITUENT_PATH = "/stable/dowjones-constituent"
FMP_STAGE10_UNIVERSE_PATHS = frozenset(
    {
        FMP_STAGE10_SP500_CONSTITUENT_PATH,
        FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
        FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    }
)
FMP_STAGE10_PRICE_PATH = "/stable/historical-price-eod/full"
FMP_STAGE10_TIMEOUT_SECONDS = 45
FMP_STAGE10_MAX_CONSTITUENT_BYTES = 4 * 1024 * 1024
FMP_STAGE10_MAX_CONSTITUENT_ROWS = 600
FMP_STAGE10_MAX_PRICE_BYTES = 16 * 1024 * 1024
FMP_STAGE10_MAX_PRICE_ROWS = 30_000
FMP_STAGE10_APPROVED_TARGET_ROOT = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1"
)

_STAGE10_UNIVERSE_IDS = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
    "curated_etfs",
    "major_indexes",
)

_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_PROVIDER_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807

# No recovered Stage 10 fixture defines a byte-exact constituent payload in
# this repository.  These are the documented stable FMP constituent fields:
# symbol and name are required; every other documented field is optional but
# closed to this allowlist.  New provider fields must be reviewed explicitly.
_CONSTITUENT_REQUIRED_KEYS = frozenset({"symbol", "name"})
_CONSTITUENT_ALLOWED_KEYS = frozenset(
    {
        "symbol",
        "name",
        "sector",
        "subSector",
        "headQuarter",
        "dateFirstAdded",
        "cik",
        "founded",
    }
)
_PRICE_RESPONSE_KEYS = frozenset(
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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _validate_api_key(value: object) -> str:
    if not isinstance(value, str) or _API_KEY.fullmatch(value) is None:
        raise ValidationError("FMP credential is missing or invalid")
    return value


def _validate_symbol(value: object, field_name: str = "symbol") -> str:
    if not isinstance(value, str) or _PROVIDER_SYMBOL.fullmatch(value) is None:
        raise ValidationError(f"FMP {field_name} is invalid")
    return value


def _validate_text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"FMP {field_name} must be nonempty text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name)


def _iso_date(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"FMP {field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"FMP {field_name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"FMP {field_name} must be an ISO date")
    return value


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


def _require_bytes(value: object, *, max_bytes: int, kind: str) -> bytes:
    if not isinstance(value, bytes):
        raise ValidationError(f"FMP {kind} body must be bytes")
    if not value:
        raise ValidationError(f"FMP {kind} body is empty")
    if len(value) > max_bytes:
        raise ResourceLimitError(f"FMP {kind} response exceeds the reviewed byte bound")
    return value


def _load_response_list(body: object, *, max_bytes: int, kind: str) -> list[object]:
    payload = _require_bytes(body, max_bytes=max_bytes, kind=kind)
    value = loads_strict(payload, max_bytes=max_bytes)
    if not isinstance(value, list):
        raise ValidationError(f"FMP {kind} response must be a JSON array")
    return value


def _mapping_of_strings(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"FMP transport {field_name} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValidationError(f"FMP transport {field_name} must contain strings")
        result[key] = item
    return result


def _validate_timeout(value: object) -> int:
    if isinstance(value, bool) or value != FMP_STAGE10_TIMEOUT_SECONDS:
        raise ValidationError("FMP transport timeout is outside the approved scope")
    return FMP_STAGE10_TIMEOUT_SECONDS


def _validate_transport_request(
    *,
    path: object,
    query: object,
    headers: object,
    timeout_seconds: object,
    max_bytes: object,
) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(path, str):
        raise ValidationError("FMP transport path is invalid")
    query_values = _mapping_of_strings(query, "query")
    header_values = _mapping_of_strings(headers, "headers")
    if set(header_values) != {"apikey"}:
        raise ValidationError("FMP transport headers are outside the approved scope")
    _validate_api_key(header_values["apikey"])
    _validate_timeout(timeout_seconds)

    if path in FMP_STAGE10_UNIVERSE_PATHS:
        if query_values or max_bytes != FMP_STAGE10_MAX_CONSTITUENT_BYTES:
            raise ValidationError("FMP constituent request is outside the approved scope")
        return query_values, header_values
    if path == FMP_STAGE10_PRICE_PATH:
        query_fields = set(query_values)
        if query_fields not in ({"symbol"}, {"symbol", "from", "to"}):
            raise ValidationError("FMP price request is outside the approved scope")
        _validate_symbol(query_values["symbol"], "query symbol")
        if query_fields == {"symbol", "from", "to"}:
            # The window module imports this transport module, so use a local
            # import after both modules have initialized.
            from .stage10_history_windows import STAGE10_HISTORY_WINDOWS

            approved_windows = {
                (window.start_date, window.end_date)
                for window in STAGE10_HISTORY_WINDOWS
            }
            if (query_values["from"], query_values["to"]) not in approved_windows:
                raise ValidationError(
                    "FMP dated price request is outside the approved Stage 10 windows"
                )
        if max_bytes != FMP_STAGE10_MAX_PRICE_BYTES:
            raise ValidationError("FMP price request is outside the approved scope")
        return query_values, header_values
    raise ValidationError("FMP transport path is outside the approved scope")
def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("FMP Stage 10 live transport requires explicit stores")
    return tuple(
        (role.value, canonical_path_uri(path)) for role, path in store_map.items()
    )


def _approved_live_store_map_identity() -> tuple[tuple[str, str], ...]:
    stores = FMP_STAGE10_APPROVED_TARGET_ROOT / "stores"
    return tuple(
        (role.value, canonical_path_uri(stores / f"{role.value}.sqlite"))
        for role in StoreRole
    )


def _require_live_scope(scope: object) -> Stage10MarketScope:
    if (
        not isinstance(scope, Stage10MarketScope)
        or scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or scope.provider != "fmp"
        or tuple(source.id for source in scope.universe_sources)
        != _STAGE10_UNIVERSE_IDS[:3]
        or len(scope.curated_etfs) != 95
        or len(scope.major_indexes) != 15
        or any(item.provider_symbol == "^RUT" for item in scope.major_indexes)
    ):
        raise ValidationError("FMP Stage 10 live transport scope is invalid")
    return scope


def _require_approved_live_store_map(
    store_map: StoreMap,
) -> tuple[tuple[str, str], ...]:
    identity = _store_map_identity(store_map)
    if identity != _approved_live_store_map_identity():
        raise ValidationError(
            "FMP Stage 10 live transport requires the approved nonproduction target"
        )
    return identity


def _require_scoped_price_symbol(
    store_map: StoreMap,
    scope: Stage10MarketScope,
    symbol: str,
) -> None:
    if symbol in {"^RUT", "IWM"} or "russell" in symbol.casefold():
        raise ValidationError("Russell instruments are outside Stage 10 scope")
    with read_connection(store_map, StoreRole.MARKET) as connection:
        scopes = tuple(
            connection.execute(
                """
                SELECT scope_snapshot_id
                FROM stage10_scope_snapshots
                WHERE scope_manifest_sha256=? AND target_profile_id=?
                  AND provider='fmp'
                ORDER BY scope_snapshot_id
                """,
                (scope.manifest_sha256, scope.target_profile_id),
            )
        )
        if len(scopes) != 1:
            raise ValidationError("FMP Stage 10 live roster is not complete")
        scope_snapshot_id = str(scopes[0]["scope_snapshot_id"])
        snapshots = tuple(
            connection.execute(
                """
                SELECT universe_id, count(*) AS snapshot_count
                FROM stage10_universe_snapshots
                WHERE scope_snapshot_id=? AND completeness='complete'
                GROUP BY universe_id
                ORDER BY universe_id
                """,
                (scope_snapshot_id,),
            )
        )
        expected = tuple((item, 1) for item in sorted(_STAGE10_UNIVERSE_IDS))
        actual = tuple(
            (str(row["universe_id"]), int(row["snapshot_count"]))
            for row in snapshots
        )
        if actual != expected:
            raise ValidationError("FMP Stage 10 live roster is not complete")
        rows = tuple(
            connection.execute(
                """
                SELECT DISTINCT instrument.asset_type
                FROM stage10_universe_snapshot_members AS member
                JOIN stage10_universe_snapshots AS snapshot
                  ON snapshot.universe_snapshot_id=member.universe_snapshot_id
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=member.instrument_id
                WHERE snapshot.scope_snapshot_id=?
                  AND snapshot.completeness='complete'
                  AND instrument.provider='fmp'
                  AND instrument.provider_symbol=?
                """,
                (scope_snapshot_id, symbol),
            )
        )
    if len(rows) != 1 or str(rows[0]["asset_type"]) not in {
        "equity",
        "etf",
        "index",
    }:
        raise ValidationError("FMP price symbol is outside the reviewed Stage 10 roster")




@dataclass(frozen=True, slots=True)
class CapturedFmpStage10Response:
    """One bounded raw HTTP response; body bytes are intentionally not repr'd."""

    status: int
    content_type: str
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise ValidationError("FMP response status is invalid")
        if not isinstance(self.content_type, str) or not self.content_type.strip():
            raise ValidationError("FMP response content type is invalid")
        _require_bytes(
            self.body,
            max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
            kind="response",
        )


@runtime_checkable
class FmpStage10Transport(Protocol):
    """Injectable transport for one validated Stage 10 provider request."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response: ...


class StdlibFmpStage10Transport:
    """TLS-verified stdlib transport with no redirect or target binding.

    ``http.client.HTTPSConnection`` does not follow redirects.  This adapter
    accepts only the frozen Stage 10 route, query, header, timeout, and body
    limits, but deliberately has no operational-store or production-target
    knowledge.
    """

    def __init__(self, store_map: StoreMap, scope: Stage10MarketScope) -> None:
        self._store_map = store_map
        self._store_map_identity = _require_approved_live_store_map(store_map)
        self._scope = _require_live_scope(scope)

    def _validate_stage10_live_binding(self) -> None:
        if (
            _store_map_identity(self._store_map) != self._store_map_identity
            or self._store_map_identity != _approved_live_store_map_identity()
            or _require_live_scope(self._scope) is not self._scope
        ):
            raise ValidationError("FMP Stage 10 live transport binding is invalid")

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response:
        self._validate_stage10_live_binding()
        query_values, header_values = _validate_transport_request(
            path=path,
            query=query,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        if path == FMP_STAGE10_PRICE_PATH:
            _require_scoped_price_symbol(
                self._store_map,
                self._scope,
                query_values["symbol"],
            )
        target = path
        if query_values:
            target = f"{path}?{urlencode(query_values)}"
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(
                FMP_STAGE10_HOST,
                timeout=FMP_STAGE10_TIMEOUT_SECONDS,
            )
            connection.request("GET", target, headers=header_values)
            response = connection.getresponse()
            declared_size = response.getheader("Content-Length")
            if declared_size is not None:
                if not isinstance(declared_size, str) or not declared_size.isdecimal():
                    raise StoreUnavailableError("FMP provider response was unavailable")
                if int(declared_size) > max_bytes:
                    raise ResourceLimitError(
                        "FMP response exceeds the reviewed byte bound"
                    )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("FMP response exceeds the reviewed byte bound")
            return CapturedFmpStage10Response(
                status=response.status,
                content_type=response.getheader("Content-Type")
                or "application/octet-stream",
                body=body,
            )
        except (ResourceLimitError, ValidationError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP provider request failed") from exc
        finally:
            if connection is not None:
                connection.close()


@dataclass(frozen=True, slots=True)
class PreparedFmpStage10UniverseCapture:
    """An immutable, credential-free constituent request description."""

    endpoint_path: str

    def __post_init__(self) -> None:
        if self.endpoint_path not in FMP_STAGE10_UNIVERSE_PATHS:
            raise ValidationError("FMP constituent endpoint is outside the approved scope")

    @property
    def query(self) -> dict[str, str]:
        return {}


@dataclass(frozen=True, slots=True)
class PreparedFmpStage10PriceCapture:
    """An immutable, credential-free full-history price request description."""

    symbol: str

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)

    @property
    def endpoint_path(self) -> str:
        return FMP_STAGE10_PRICE_PATH

    @property
    def query(self) -> dict[str, str]:
        return {"symbol": self.symbol}


@dataclass(frozen=True, slots=True)
class FmpStage10Constituent:
    """One normalized current-provider constituent row."""

    symbol: str
    name: str
    sector: str | None
    sub_sector: str | None
    head_quarter: str | None
    date_first_added: str | None
    cik: str | None
    founded: str | None

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _validate_text(self.name, "constituent name")
        for field_name, value in (
            ("constituent sector", self.sector),
            ("constituent subSector", self.sub_sector),
            ("constituent headQuarter", self.head_quarter),
            ("constituent cik", self.cik),
            ("constituent founded", self.founded),
        ):
            _optional_text(value, field_name)
        if self.date_first_added is not None:
            _iso_date(self.date_first_added, "constituent dateFirstAdded")

    def provider_mapping(self) -> dict[str, str | None]:
        """Return the closed FMP field projection with provider key spelling."""

        return {
            "symbol": self.symbol,
            "name": self.name,
            "sector": self.sector,
            "subSector": self.sub_sector,
            "headQuarter": self.head_quarter,
            "dateFirstAdded": self.date_first_added,
            "cik": self.cik,
            "founded": self.founded,
        }


@dataclass(frozen=True, slots=True)
class FmpStage10PriceRow:
    """A validated FMP OHLCV row, normalized only for semantic comparison."""

    symbol: str
    trade_date: str
    open_value: Decimal
    high_value: Decimal
    low_value: Decimal
    close_value: Decimal
    volume: int
    source_row: int

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _iso_date(self.trade_date, "price date")
        open_value = _decimal(self.open_value, "open")
        high_value = _decimal(self.high_value, "high")
        low_value = _decimal(self.low_value, "low")
        close_value = _decimal(self.close_value, "close")
        if (
            min(open_value, high_value, low_value, close_value) <= 0
            or low_value > min(open_value, close_value)
            or high_value < max(open_value, close_value)
            or low_value > high_value
        ):
            raise ValidationError("FMP response contains inconsistent OHLC values")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int):
            raise ValidationError("FMP volume must be an integer")
        if self.volume < 0 or self.volume > _MAX_SQLITE_INTEGER:
            raise ValidationError("FMP volume is outside the supported range")
        if isinstance(self.source_row, bool) or not isinstance(self.source_row, int):
            raise ValidationError("FMP source row is invalid")
        if self.source_row < 1:
            raise ValidationError("FMP source row is invalid")

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "date": self.trade_date,
            "open": _decimal_text(self.open_value),
            "high": _decimal_text(self.high_value),
            "low": _decimal_text(self.low_value),
            "close": _decimal_text(self.close_value),
            "volume": self.volume,
        }


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError(f"FMP {field_name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class FmpStage10UniverseCapture:
    """Immutable parsed universe capture; raw bytes remain available but hidden."""

    endpoint_path: str
    raw_bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    constituents: tuple[FmpStage10Constituent, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if self.endpoint_path not in FMP_STAGE10_UNIVERSE_PATHS:
            raise ValidationError("FMP constituent endpoint is outside the approved scope")
        _validate_digest(self.raw_bytes_sha256, "raw response digest")
        _require_bytes(
            self.raw_bytes,
            max_bytes=FMP_STAGE10_MAX_CONSTITUENT_BYTES,
            kind="constituent",
        )
        if self.raw_bytes_sha256 != _sha256(self.raw_bytes):
            raise ValidationError("FMP constituent raw response digest is invalid")
        if (
            not isinstance(self.constituents, tuple)
            or not self.constituents
            or len(self.constituents) > FMP_STAGE10_MAX_CONSTITUENT_ROWS
        ):
            raise ValidationError("FMP constituent row count is invalid")
        symbols: set[str] = set()
        for row in self.constituents:
            if not isinstance(row, FmpStage10Constituent) or row.symbol in symbols:
                raise ValidationError("FMP constituent rows are invalid")
            symbols.add(row.symbol)

    @property
    def raw_bytes_size(self) -> int:
        return len(self.raw_bytes)


def _price_semantic_material(
    symbol: str,
    rows: tuple[FmpStage10PriceRow, ...],
) -> dict[str, object]:
    return {
        "provider": "fmp",
        "endpoint_path": FMP_STAGE10_PRICE_PATH,
        "symbol": symbol,
        "rows": [row.semantic_mapping() for row in rows],
    }


@dataclass(frozen=True, slots=True)
class FmpStage10PriceCapture:
    """Immutable parsed price capture; credentials and raw bytes stay out of repr."""

    symbol: str
    raw_bytes_sha256: str
    raw_bytes: bytes = field(repr=False)
    rows: tuple[FmpStage10PriceRow, ...] = field(repr=False)
    earliest_date: str
    latest_date: str
    semantic_sha256: str

    def __post_init__(self) -> None:
        _validate_symbol(self.symbol)
        _validate_digest(self.raw_bytes_sha256, "raw response digest")
        _require_bytes(
            self.raw_bytes,
            max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
            kind="price",
        )
        if self.raw_bytes_sha256 != _sha256(self.raw_bytes):
            raise ValidationError("FMP price raw response digest is invalid")
        if (
            not isinstance(self.rows, tuple)
            or not self.rows
            or len(self.rows) > FMP_STAGE10_MAX_PRICE_ROWS
        ):
            raise ValidationError("FMP price row count is invalid")
        prior_date: str | None = None
        for row in self.rows:
            if not isinstance(row, FmpStage10PriceRow) or row.symbol != self.symbol:
                raise ValidationError("FMP price rows are invalid")
            if prior_date is not None and row.trade_date <= prior_date:
                raise ValidationError("FMP price rows must be strictly ascending")
            prior_date = row.trade_date
        _iso_date(self.earliest_date, "earliest price date")
        _iso_date(self.latest_date, "latest price date")
        if (
            self.earliest_date != self.rows[0].trade_date
            or self.latest_date != self.rows[-1].trade_date
        ):
            raise ValidationError("FMP price coverage is invalid")
        _validate_digest(self.semantic_sha256, "price semantic digest")
        if self.semantic_sha256 != _sha256_json(
            _price_semantic_material(self.symbol, self.rows)
        ):
            raise ValidationError("FMP price semantic digest is invalid")

    @property
    def raw_bytes_size(self) -> int:
        return len(self.raw_bytes)


class FmpPriceHistoryUnavailable(Exception):
    """One explicitly skippable, bounded Stage 10 price-history outcome.

    This exception is intentionally *not* a general provider-error wrapper.
    It can be constructed only from a successful, bounded HTTP JSON response
    whose top-level value is a list but whose price history is empty or cannot
    satisfy the reviewed price-row contract.  In particular, malformed JSON,
    a non-list payload, an HTTP/provider failure, and a resource-bound breach
    remain fatal to the manual runner.

    The response bytes are deliberately never retained here.  The attributes
    are the narrow provenance material needed for an append-only private
    failure receipt.
    """

    __slots__ = (
        "symbol",
        "reason",
        "status",
        "content_type",
        "body_sha256",
        "body_byte_count",
    )

    def __init__(
        self,
        *,
        symbol: str,
        status: int,
        content_type: str,
        body_sha256: str,
        body_byte_count: int,
    ) -> None:
        self.symbol = _validate_symbol(symbol)
        if status != 200 or content_type != "application/json":
            raise ValidationError("FMP unavailable-history provenance is invalid")
        self.status = status
        self.content_type = content_type
        self.body_sha256 = _validate_digest(body_sha256, "raw response digest")
        if (
            isinstance(body_byte_count, bool)
            or not isinstance(body_byte_count, int)
            or body_byte_count < 0
            or body_byte_count > FMP_STAGE10_MAX_PRICE_BYTES
        ):
            raise ValidationError("FMP unavailable-history byte count is invalid")
        self.body_byte_count = body_byte_count
        self.reason = "fmp_price_history_unavailable"
        super().__init__("FMP price history is unavailable")

    def __repr__(self) -> str:
        return (
            "FmpPriceHistoryUnavailable("
            f"symbol={self.symbol!r}, status={self.status!r}, "
            f"content_type={self.content_type!r}, "
            f"body_sha256={self.body_sha256!r}, "
            f"body_byte_count={self.body_byte_count!r})"
        )


def _unavailable_price_history(
    *,
    symbol: str,
    response: CapturedFmpStage10Response,
) -> FmpPriceHistoryUnavailable:
    """Build the one safe, receipt-ready unavailable-history exception."""

    return FmpPriceHistoryUnavailable(
        symbol=symbol,
        status=response.status,
        content_type="application/json",
        body_sha256=_sha256(response.body),
        body_byte_count=len(response.body),
    )


def prepare_fmp_stage10_universe_capture(
    endpoint_path: str,
) -> PreparedFmpStage10UniverseCapture:
    """Prepare one credential-free current-constituent request."""

    return PreparedFmpStage10UniverseCapture(endpoint_path=endpoint_path)


def prepare_fmp_stage10_price_capture(symbol: str) -> PreparedFmpStage10PriceCapture:
    """Prepare one credential-free full-history request for an exact symbol."""

    return PreparedFmpStage10PriceCapture(symbol=symbol)


def parse_fmp_stage10_universe_response(
    *,
    endpoint_path: str,
    body: bytes,
) -> FmpStage10UniverseCapture:
    """Parse one bounded, closed-shape FMP constituent response."""

    if endpoint_path not in FMP_STAGE10_UNIVERSE_PATHS:
        raise ValidationError("FMP constituent endpoint is outside the approved scope")
    value = _load_response_list(
        body,
        max_bytes=FMP_STAGE10_MAX_CONSTITUENT_BYTES,
        kind="constituent",
    )
    if not value or len(value) > FMP_STAGE10_MAX_CONSTITUENT_ROWS:
        raise ValidationError("FMP constituent row count is invalid")
    rows: list[FmpStage10Constituent] = []
    seen_symbols: set[str] = set()
    for source_row, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"FMP constituent row {source_row} is invalid")
        keys = frozenset(raw)
        if (
            not _CONSTITUENT_REQUIRED_KEYS.issubset(keys)
            or not keys.issubset(_CONSTITUENT_ALLOWED_KEYS)
        ):
            raise ValidationError(f"FMP constituent row {source_row} shape is invalid")
        symbol = _validate_symbol(raw["symbol"], "constituent symbol")
        if symbol in seen_symbols:
            raise ValidationError("FMP constituent response contains duplicate symbols")
        seen_symbols.add(symbol)
        date_first_added = _optional_text(
            raw.get("dateFirstAdded"), "constituent dateFirstAdded"
        )
        if date_first_added is not None:
            _iso_date(date_first_added, "constituent dateFirstAdded")
        rows.append(
            FmpStage10Constituent(
                symbol=symbol,
                name=_validate_text(raw["name"], "constituent name"),
                sector=_optional_text(raw.get("sector"), "constituent sector"),
                sub_sector=_optional_text(
                    raw.get("subSector"), "constituent subSector"
                ),
                head_quarter=_optional_text(
                    raw.get("headQuarter"), "constituent headQuarter"
                ),
                date_first_added=date_first_added,
                cik=_optional_text(raw.get("cik"), "constituent cik"),
                founded=_optional_text(raw.get("founded"), "constituent founded"),
            )
        )
    return FmpStage10UniverseCapture(
        endpoint_path=endpoint_path,
        raw_bytes_sha256=_sha256(body),
        raw_bytes=body,
        constituents=tuple(rows),
    )


def parse_fmp_stage10_price_response(
    *,
    symbol: str,
    body: bytes,
) -> FmpStage10PriceCapture:
    """Parse complete available FMP price history for one exact provider symbol."""

    expected_symbol = _validate_symbol(symbol)
    value = _load_response_list(
        body,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
        kind="price",
    )
    if not value or len(value) > FMP_STAGE10_MAX_PRICE_ROWS:
        raise ValidationError("FMP price row count is invalid")
    rows: list[FmpStage10PriceRow] = []
    seen_dates: set[str] = set()
    for source_row, raw in enumerate(value, start=1):
        if not isinstance(raw, dict) or frozenset(raw) != _PRICE_RESPONSE_KEYS:
            raise ValidationError(f"FMP price row {source_row} shape is invalid")
        if raw["symbol"] != expected_symbol:
            raise ValidationError("FMP response contains an unexpected symbol")
        trade_date = _iso_date(raw["date"], "price date")
        if trade_date in seen_dates:
            raise ValidationError("FMP response contains duplicate price dates")
        seen_dates.add(trade_date)
        open_value = _decimal(raw["open"], "open")
        high_value = _decimal(raw["high"], "high")
        low_value = _decimal(raw["low"], "low")
        close_value = _decimal(raw["close"], "close")
        for field_name in ("change", "changePercent", "vwap"):
            _decimal(raw[field_name], field_name)
        rows.append(
            FmpStage10PriceRow(
                symbol=expected_symbol,
                trade_date=trade_date,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                volume=_volume(raw["volume"]),
                source_row=source_row,
            )
        )
    ordered_rows = tuple(sorted(rows, key=lambda row: row.trade_date))
    semantic_sha256 = _sha256_json(
        _price_semantic_material(expected_symbol, ordered_rows)
    )
    return FmpStage10PriceCapture(
        symbol=expected_symbol,
        raw_bytes_sha256=_sha256(body),
        raw_bytes=body,
        rows=ordered_rows,
        earliest_date=ordered_rows[0].trade_date,
        latest_date=ordered_rows[-1].trade_date,
        semantic_sha256=semantic_sha256,
    )


def _require_success_response(
    response: object,
    *,
    max_bytes: int,
    kind: str,
) -> CapturedFmpStage10Response:
    if not isinstance(response, CapturedFmpStage10Response):
        raise ValidationError("FMP transport returned an invalid response type")
    _require_bytes(response.body, max_bytes=max_bytes, kind=kind)
    content_type = response.content_type.split(";", 1)[0].strip().lower()
    if response.status != 200 or content_type != "application/json":
        raise StoreUnavailableError("FMP provider response was unavailable")
    return response


def _require_transport(value: object) -> FmpStage10Transport:
    if not isinstance(value, FmpStage10Transport):
        raise ValidationError("FMP transport does not implement the reviewed interface")
    return value


def capture_fmp_stage10_universe(
    prepared: PreparedFmpStage10UniverseCapture,
    *,
    api_key: str,
    transport: FmpStage10Transport,
) -> FmpStage10UniverseCapture:
    """Make exactly one injected constituent request and parse its response."""

    if not isinstance(prepared, PreparedFmpStage10UniverseCapture):
        raise ValidationError("FMP constituent capture requires a prepared request")
    transport = _require_transport(transport)
    key = _validate_api_key(api_key)
    response = transport.get(
        path=prepared.endpoint_path,
        query=prepared.query,
        headers={"apikey": key},
        timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
        max_bytes=FMP_STAGE10_MAX_CONSTITUENT_BYTES,
    )
    response = _require_success_response(
        response,
        max_bytes=FMP_STAGE10_MAX_CONSTITUENT_BYTES,
        kind="constituent",
    )
    return parse_fmp_stage10_universe_response(
        endpoint_path=prepared.endpoint_path,
        body=response.body,
    )


def capture_fmp_stage10_price(
    prepared: PreparedFmpStage10PriceCapture,
    *,
    api_key: str,
    transport: FmpStage10Transport,
) -> FmpStage10PriceCapture:
    """Make exactly one injected full-history price request and parse it."""

    if not isinstance(prepared, PreparedFmpStage10PriceCapture):
        raise ValidationError("FMP price capture requires a prepared request")
    transport = _require_transport(transport)
    key = _validate_api_key(api_key)
    response = transport.get(
        path=prepared.endpoint_path,
        query=prepared.query,
        headers={"apikey": key},
        timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
    )
    response = _require_success_response(
        response,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
        kind="price",
    )
    # Deliberately establish the narrow skippable predicate before invoking
    # the full parser.  ``_load_response_list`` preserves malformed JSON and
    # non-list failures, while an over-large *row* list is a resource-bound
    # failure rather than a reason to silently skip a symbol.
    value = _load_response_list(
        response.body,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
        kind="price",
    )
    if not value:
        raise _unavailable_price_history(symbol=prepared.symbol, response=response)
    if len(value) > FMP_STAGE10_MAX_PRICE_ROWS:
        raise ResourceLimitError("FMP price response exceeds the reviewed row bound")
    try:
        return parse_fmp_stage10_price_response(
            symbol=prepared.symbol,
            body=response.body,
        )
    except ValidationError as exc:
        # At this point the response is known to be HTTP 200,
        # application/json, bounded, and a nonempty top-level list.  The only
        # remaining parser failures are reviewed price-data contract failures
        # in its list rows, which the user has expressly authorized us to
        # journal and skip one symbol at a time.
        raise _unavailable_price_history(
            symbol=prepared.symbol,
            response=response,
        ) from exc
