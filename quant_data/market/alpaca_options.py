"""Bounded Alpaca SPY option-surface normalization and publication.

This module owns neither credentials nor HTTP.  The operation adapter supplies
four bounded responses; all parsing finishes before the shared ingestion
coordinator obtains the market-store write lock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import http.client
import re
from pathlib import Path
import sqlite3
from time import monotonic
from typing import Any, Final, Protocol, runtime_checkable
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from ..contracts import IngestionReceipt
from ..errors import (
    ConflictError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..ingestion import ArtifactWrite, IngestionCoordinator, QualityWrite, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, read_connection, stable_id
from ..temporal import TemporalPrecision, TemporalValue
from .stage4_options import _write_capture_inputs, _write_surface_rows


ALPACA_SPY_OPTIONS_COLLECTOR_ID: Final = "alpaca.market.spy_option_surface"
ALPACA_SPY_OPTIONS_HANDLER: Final = "market.alpaca_spy_option_surface"
ALPACA_ETF_OPTIONS_HANDLER: Final = "market.alpaca_etf_option_surface_grid"
ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID: Final = "fixture.market.option_capture_evidence"
ALPACA_OPTIONS_RAW_EVIDENCE_DATASET_ID: Final = "market.alpaca.option_raw_evidence"
ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID: Final = "fixture.market.options"
ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID: Final = "fixture.market.instruments"
ALPACA_PROVIDER: Final = "alpaca"
ALPACA_REQUESTED_FEED: Final = "indicative"
ALPACA_RESOLVED_FEED: Final = "alpaca_indicative"
ALPACA_ENVIRONMENT: Final = "paper"
ALPACA_NORMALIZATION_VERSION: Final = "alpaca_spy_option_surface.v3"
ALPACA_ETF_OPTIONS_COLLECTOR_ID: Final = "alpaca.market.etf_option_surface_grid"
ALPACA_ETF_OPTIONS_NORMALIZATION_VERSION: Final = "alpaca_etf_option_surface_grid.v2"
SPY_SYMBOL: Final = "SPY"
TARGET_DTE: Final = 30
MIN_DTE: Final = 23
MAX_DTE: Final = 37
ALPACA_ETF_OPTIONS_UNIVERSE: Final = (
    "SPY", "QQQ", "IWM", "DIA", "XLB", "XLC", "XLE", "XLF",
    "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
ALPACA_ETF_OPTIONS_DTE_TARGETS: Final = (1, 2, 3, 7, 14, 30, 60, 90, 180, 365)
# Searching through twice the largest target is sufficient for a global nearest
# choice: any later positive expiry is farther from 365 DTE than every eligible
# expiry admitted by this window, with ties resolved toward the earlier expiry.
ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS: Final = 2 * max(ALPACA_ETF_OPTIONS_DTE_TARGETS)
LOWER_STRIKE_MULTIPLIER: Final = Decimal("0.80")
UPPER_STRIKE_MULTIPLIER: Final = Decimal("1.20")
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_CONTRACTS: Final = 10_000
MAX_CHAIN_SNAPSHOTS: Final = 1_000
MAX_ETF_OPTIONS_CONTRACT_PAGES: Final = 4
MAX_ETF_OPTIONS_CHAIN_PAGES: Final = 2
MAX_ETF_OPTIONS_CONTRACT_ROWS: Final = MAX_CONTRACTS * MAX_ETF_OPTIONS_CONTRACT_PAGES
MAX_ETF_OPTIONS_CHAIN_ROWS: Final = MAX_CHAIN_SNAPSHOTS * MAX_ETF_OPTIONS_CHAIN_PAGES
MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES: Final = 256 * 1024 * 1024
MAX_ETF_OPTIONS_NORMALIZED_BYTES: Final = MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES
MAX_ETF_OPTIONS_RUN_SECONDS: Final = 900
MAX_ETF_OPTIONS_REQUESTS: Final = (
    2
    + len(ALPACA_ETF_OPTIONS_UNIVERSE) * MAX_ETF_OPTIONS_CONTRACT_PAGES
    + len(ALPACA_ETF_OPTIONS_UNIVERSE)
    * len(ALPACA_ETF_OPTIONS_DTE_TARGETS)
    * MAX_ETF_OPTIONS_CHAIN_PAGES
)
_SQLITE_MAX_INTEGER: Final = 9_223_372_036_854_775_807
_MAX_NUMERIC_TEXT_LENGTH: Final = 128
_MAX_DECIMAL_DIGITS: Final = 128
_MAX_DECIMAL_EXPONENT: Final = 128
_DECIMAL_TEXT = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
_NANOSECOND_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6})\d{1,3}(Z|[+-]\d{2}:\d{2})$"
)
_REQUIRED_DATASETS: Final = frozenset(
    {
        ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID,
        ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
        ALPACA_OPTIONS_RAW_EVIDENCE_DATASET_ID,
        ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
    }
)


def _fail(message: str) -> ValidationError:
    return ValidationError(f"Alpaca SPY options {message}")


def _text(value: object, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _fail(f"{label} is invalid")
    return value.strip()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _fail(f"{label} is invalid")
    return value


def _array(value: object, label: str, *, empty: bool = False) -> Sequence[Any]:
    if not isinstance(value, list) or len(value) > MAX_CONTRACTS or (not empty and not value):
        raise _fail(f"{label} is invalid")
    return value


def _date(value: object, label: str) -> str:
    text = _text(value, label, 10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise _fail(f"{label} is invalid") from exc
    if parsed.isoformat() != text:
        raise _fail(f"{label} is invalid")
    return text


def _instant(value: datetime, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail(f"{label} is invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _source_time(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None:
        if nullable:
            return None
        raise _fail(f"{label} is invalid")
    text = _text(value, label, 128)
    # Retain Alpaca's source-native lexeme; only validation and session routing
    # use the transient microsecond-compatible representation.
    validation_text = text
    match = _NANOSECOND_TIME.fullmatch(text)
    if match is not None:
        validation_text = f"{match.group(1)}{match.group(2)}"
    try:
        parsed = TemporalValue.parse(validation_text, pointer=f"/{label}")
    except ValidationError as exc:
        raise _fail(f"{label} is invalid") from exc
    if parsed.precision is not TemporalPrecision.DATETIME:
        raise _fail(f"{label} is invalid")
    return text


def _session_for_timestamp(value: object, *, label: str) -> str:
    text = _source_time(value, label)
    assert text is not None
    match = _NANOSECOND_TIME.fullmatch(text)
    validation_text = text if match is None else f"{match.group(1)}{match.group(2)}"
    parsed = TemporalValue.parse(validation_text, pointer=f"/{label}")
    if not isinstance(parsed.value, datetime):
        raise _fail(f"{label} is invalid")
    return parsed.value.astimezone(
        ZoneInfo("America/New_York")
    ).date().isoformat()


def _require_session_timestamp(value: object, *, session_date: str, label: str) -> None:
    observed_session = _session_for_timestamp(value, label=label)
    if observed_session != session_date:
        raise _fail(f"{label} is not current for session {session_date}")


def _source_datetime(value: object, label: str) -> datetime:
    text = _source_time(value, label)
    assert text is not None
    match = _NANOSECOND_TIME.fullmatch(text)
    validation_text = text if match is None else f"{match.group(1)}{match.group(2)}"
    parsed = TemporalValue.parse(validation_text, pointer=f"/{label}")
    if not isinstance(parsed.value, datetime):
        raise _fail(f"{label} is invalid")
    return parsed.value.astimezone(timezone.utc)


def _require_not_after(value: object, *, cutoff: object, label: str) -> None:
    if _source_datetime(value, label) > _source_datetime(cutoff, f"{label} cutoff"):
        raise _fail(f"{label} is later than capture completion")


def _decimal(
    value: object,
    label: str,
    *,
    minimum: Decimal | None = None,
    nullable: bool = False,
) -> str | None:
    if value is None:
        if nullable:
            return None
        raise _fail(f"{label} is invalid")
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise _fail(f"{label} is invalid")
    if isinstance(value, str):
        if (
            not value
            or len(value) > _MAX_NUMERIC_TEXT_LENGTH
            or not value.isascii()
            or _DECIMAL_TEXT.fullmatch(value) is None
        ):
            raise _fail(f"{label} is invalid")
    elif isinstance(value, int) and value.bit_length() > 425:
        raise _fail(f"{label} is invalid")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"{label} is invalid") from exc
    sign, digits, exponent = parsed.as_tuple()
    if (
        not parsed.is_finite()
        or not isinstance(exponent, int)
        or len(digits) > _MAX_DECIMAL_DIGITS
        or abs(exponent) > _MAX_DECIMAL_EXPONENT
        or abs(parsed.adjusted()) > _MAX_DECIMAL_EXPONENT
        or (minimum is not None and parsed < minimum)
    ):
        raise _fail(f"{label} is invalid")
    if parsed.is_zero():
        return "0"
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if sign and not normalized.startswith("-"):
        normalized = f"-{normalized}"
    if len(normalized) > _MAX_NUMERIC_TEXT_LENGTH:
        raise _fail(f"{label} is invalid")
    return normalized


def _integer(value: object, label: str, *, nullable: bool = False) -> int | None:
    if value is None:
        if nullable:
            return None
        raise _fail(f"{label} is invalid")
    if isinstance(value, str):
        if (
            not value
            or len(value) > 19
            or not value.isascii()
            or not value.isdecimal()
        ):
            raise _fail(f"{label} is invalid")
        try:
            value = int(value)
        except ValueError as exc:
            raise _fail(f"{label} is invalid") from exc
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _SQLITE_MAX_INTEGER
    ):
        raise _fail(f"{label} is invalid")
    return value


def _json(body: object, label: str) -> Mapping[str, Any]:
    if not isinstance(body, bytes) or not body or len(body) > MAX_RESPONSE_BYTES:
        raise _fail(f"{label} response is invalid")
    return _mapping(loads_strict(body, max_bytes=MAX_RESPONSE_BYTES), f"{label} response")


def _one_page(payload: Mapping[str, Any], label: str) -> None:
    for field in ("next_page_token", "page_token"):
        if field not in payload or payload[field] is None:
            continue
        token = payload[field]
        if not isinstance(token, str) or not token:
            raise _fail(f"{label} pagination marker is invalid")
        raise ResourceLimitError(
            "Alpaca SPY options response exceeds the one-page request bound"
        )


def is_alpaca_opra_trading_day(body: bytes, *, session_date: str) -> bool:
    """Return whether the bounded OPRA calendar response names the session."""

    requested = _date(session_date, "session date")
    value = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if isinstance(value, list):
        rows = _array(value, "calendar response", empty=True)
    else:
        rows = _array(_mapping(value, "calendar response").get("calendar"), "calendar response", empty=True)
    if len(rows) > 1:
        raise _fail("calendar response exceeds the exact-session scope")
    if not rows:
        return False
    observed = _date(
        _mapping(rows[0], "calendar row 0").get("date"),
        "calendar row 0 date",
    )
    if observed != requested:
        raise _fail("calendar response is outside the exact-session scope")
    return True


@dataclass(frozen=True, slots=True)
class AlpacaUnderlyingSnapshot:
    spot_price: str
    spot_method: str
    quote: Mapping[str, Any]


def _snapshot_for_spy(payload: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    nested = payload.get("snapshots")
    if isinstance(nested, Mapping):
        return _mapping(nested.get(SPY_SYMBOL), f"{label} SPY snapshot")
    direct = payload.get(SPY_SYMBOL)
    if isinstance(direct, Mapping):
        return _mapping(direct, f"{label} SPY snapshot")
    if "latestQuote" in payload or "latestTrade" in payload:
        return payload
    raise _fail(f"{label} SPY snapshot is invalid")


def _underlying_quote(
    snapshot: Mapping[str, Any],
    completed_at: str,
    *,
    enforce_completion_cutoff: bool = False,
) -> tuple[Mapping[str, Any], str, str]:
    raw_quote = snapshot.get("latestQuote")
    raw_trade = snapshot.get("latestTrade")
    quote = None if raw_quote is None else _mapping(raw_quote, "underlying quote")
    trade = None if raw_trade is None else _mapping(raw_trade, "underlying trade")
    bid = ask = last = None
    bid_size = ask_size = last_size = None
    quote_at = trade_at = None
    if quote is not None:
        bid = _decimal(quote.get("bp"), "underlying bid", minimum=Decimal("0"), nullable=True)
        ask = _decimal(quote.get("ap"), "underlying ask", minimum=Decimal("0"), nullable=True)
        bid_size = _integer(quote.get("bs"), "underlying bid size", nullable=True)
        ask_size = _integer(quote.get("as"), "underlying ask size", nullable=True)
        quote_at = _source_time(quote.get("t"), "underlying quote time", nullable=True)
    if trade is not None:
        last = _decimal(trade.get("p"), "underlying trade price", minimum=Decimal("0"), nullable=True)
        last_size = _integer(trade.get("s"), "underlying trade size", nullable=True)
        trade_at = _source_time(trade.get("t"), "underlying trade time", nullable=True)
    if all(value is None for value in (bid, ask, last)):
        raise _fail("underlying snapshot contains no usable quote")
    if enforce_completion_cutoff:
        for source_time, label in (
            (quote_at, "underlying quote time"),
            (trade_at, "underlying trade time"),
        ):
            if source_time is not None:
                _require_not_after(source_time, cutoff=completed_at, label=label)
    if bid is not None and ask is not None and Decimal(bid) > 0 and Decimal(ask) > 0:
        if quote_at is None:
            raise _fail("underlying midpoint has no source timestamp")
        spot = _decimal((Decimal(bid) + Decimal(ask)) / Decimal("2"), "underlying midpoint")
        method = "iex_quote_midpoint"
        observed_at = quote_at
    elif last is not None and Decimal(last) > 0:
        if trade_at is None:
            raise _fail("underlying trade has no source timestamp")
        spot = last
        method = "iex_latest_trade"
        observed_at = trade_at
    else:
        raise _fail("underlying snapshot contains no positive spot reference")
    assert spot is not None
    if method == "iex_quote_midpoint":
        last = None
        last_size = None
        trade_at = None
    if quote_at is None:
        quote_at = observed_at
    return (
        {
            "state": "present",
            "missing_reason": None,
            "observed_at": observed_at,
            "observed_precision": "datetime",
            "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT,
            "bid_price": bid,
            "ask_price": ask,
            "last_price": last,
            "trade_price": last,
            "quote_at": quote_at,
            "quote_precision": "datetime",
            "available_at": completed_at,
            "available_precision": "datetime",
            "_bid_size": bid_size,
            "_ask_size": ask_size,
            "_last_size": last_size,
            "_trade_at": trade_at,
        },
        spot,
        method,
    )


def parse_alpaca_underlying_snapshot(body: bytes, *, completed_at: datetime) -> AlpacaUnderlyingSnapshot:
    completed = _instant(completed_at, "underlying completion time")
    quote, spot, method = _underlying_quote(
        _snapshot_for_spy(_json(body, "underlying"), "underlying"),
        completed,
    )
    return AlpacaUnderlyingSnapshot(spot_price=spot, spot_method=method, quote=quote)


def _catalog(body: bytes) -> Sequence[Any]:
    payload = _json(body, "contracts")
    _one_page(payload, "contracts")
    return _array(payload.get("option_contracts"), "contracts response")


def _catalog_expiration(row: Mapping[str, Any], label: str) -> str:
    if _text(row.get("underlying_symbol"), f"{label} underlying symbol", 32) != SPY_SYMBOL:
        raise _fail(f"{label} underlying symbol is outside the fixed scope")
    if _text(row.get("status"), f"{label} status", 32) != "active":
        raise _fail(f"{label} status is outside the fixed scope")
    return _date(row.get("expiration_date"), f"{label} expiration")


def select_alpaca_expiration(
    contracts_body: bytes,
    *,
    session_date: str,
    spot_price: str,
) -> str:
    """Select the listed expiration nearest the fixed 30-DTE target."""

    session = date.fromisoformat(_date(session_date, "session date"))
    spot = Decimal(_decimal(spot_price, "spot price", minimum=Decimal("0.0000000001")))
    lower, upper = spot * LOWER_STRIKE_MULTIPLIER, spot * UPPER_STRIKE_MULTIPLIER
    eligible: set[date] = set()
    for index, raw in enumerate(_catalog(contracts_body)):
        row = _mapping(raw, f"contract row {index}")
        expiration = date.fromisoformat(_catalog_expiration(row, f"contract row {index}"))
        strike = Decimal(
            _decimal(
                row.get("strike_price"),
                f"contract row {index} strike",
                minimum=Decimal("0.0000000001"),
            )
        )
        dte = (expiration - session).days
        if MIN_DTE <= dte <= MAX_DTE and lower <= strike <= upper:
            eligible.add(expiration)
    if not eligible:
        raise _fail("contracts response has no listed expiration in the bounded scope")
    return min(eligible, key=lambda item: (abs((item - session).days - TARGET_DTE), item)).isoformat()


def _explicit_standard_deliverable(
    deliverables: Sequence[Any], *, underlying_symbol: str
) -> bool:
    """Recognize Alpaca's explicit representation of ordinary 100-share delivery."""

    if len(deliverables) != 1 or not isinstance(deliverables[0], Mapping):
        return False
    item = deliverables[0]
    symbol = item.get("symbol")
    kind = item.get("type")
    delayed_settlement = item.get("delayed_settlement")
    if (
        not isinstance(symbol, str)
        or symbol.strip() != underlying_symbol
        or not isinstance(kind, str)
        or kind.strip().casefold() != "equity"
        or not (delayed_settlement is None or delayed_settlement is False)
    ):
        return False
    try:
        amount = _decimal(item.get("amount"), "deliverable amount")
        allocation = _decimal(
            item.get("allocation_percentage"),
            "deliverable allocation percentage",
        )
    except (ResourceLimitError, ValidationError):
        return False
    return Decimal(amount) == Decimal("100") and Decimal(allocation) == Decimal("100")


def _deliverable_for_underlying(
    row: Mapping[str, Any], *, underlying_symbol: str, multiplier: int
) -> tuple[str, str | None]:
    root = row.get("root_symbol", underlying_symbol)
    root_symbol = (
        underlying_symbol
        if root is None
        else _text(root, "contract root symbol", 32)
    )
    deliverables = row.get("deliverables", [])
    if deliverables is None:
        deliverables = []
    if not isinstance(deliverables, list):
        raise _fail("contract deliverables are invalid")
    if (
        root_symbol == underlying_symbol
        and multiplier == 100
        and (
            not deliverables
            or _explicit_standard_deliverable(
                deliverables,
                underlying_symbol=underlying_symbol,
            )
        )
    ):
        return "standard", None
    return "nonstandard", dumps_strict(
        {"deliverables": deliverables, "root_symbol": root_symbol, "size": multiplier}
    )


def _deliverable(row: Mapping[str, Any], multiplier: int) -> tuple[str, str | None]:
    return _deliverable_for_underlying(
        row,
        underlying_symbol=SPY_SYMBOL,
        multiplier=multiplier,
    )


def _contracts(
    body: bytes,
    *,
    session_date: str,
    selected_expiration: str,
    spot_price: str,
    completed_at: str,
) -> tuple[Mapping[str, Any], ...]:
    session = date.fromisoformat(_date(session_date, "session date"))
    selected = _date(selected_expiration, "selected expiration")
    spot = Decimal(_decimal(spot_price, "spot price", minimum=Decimal("0.0000000001")))
    lower, upper = spot * LOWER_STRIKE_MULTIPLIER, spot * UPPER_STRIKE_MULTIPLIER
    result: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    symbols: set[str] = set()
    for index, raw in enumerate(_catalog(body)):
        label = f"contract row {index}"
        row = _mapping(raw, label)
        expiration = _catalog_expiration(row, label)
        if not MIN_DTE <= (date.fromisoformat(expiration) - session).days <= MAX_DTE:
            raise _fail("contracts response contains an out-of-scope expiration")
        strike = _decimal(
            row.get("strike_price"),
            f"{label} strike",
            minimum=Decimal("0.0000000001"),
        )
        assert strike is not None
        if not lower <= Decimal(strike) <= upper:
            raise _fail("contracts response contains a strike outside the bounded scope")
        if expiration != selected:
            continue
        provider_id = _text(row.get("id"), f"{label} id", 256)
        symbol = _text(row.get("symbol"), f"{label} symbol", 128)
        option_type = _text(row.get("type"), f"{label} type", 16).casefold()
        if option_type not in {"call", "put"}:
            raise _fail(f"{label} type is invalid")
        multiplier = _integer(row.get("size"), f"{label} size")
        if multiplier is None or multiplier < 1 or provider_id in ids or symbol in symbols:
            raise _fail("contracts response duplicates or invalidates a contract identity")
        ids.add(provider_id)
        symbols.add(symbol)
        kind, deliverable_json = _deliverable(row, multiplier)
        result.append(
            {
                "provider": ALPACA_PROVIDER,
                "provider_contract_id": provider_id,
                "contract_symbol": symbol,
                "expiration_date": expiration,
                "strike_price": strike,
                "option_type": option_type,
                "deliverable_kind": kind,
                "contract_multiplier": multiplier,
                "nonstandard_deliverable_json": deliverable_json,
                "contract_status": "active",
                "available_at": completed_at,
                "available_precision": "datetime",
                "_open_interest": row.get("open_interest"),
                "_open_interest_date": row.get("open_interest_date"),
                "_close_price": row.get("close_price"),
                "_close_price_date": row.get("close_price_date"),
            }
        )
    if not result:
        raise _fail("exact-expiration contracts response is empty")
    return tuple(sorted(result, key=lambda row: str(row["provider_contract_id"])))


def _chain(body: bytes) -> Mapping[str, Any]:
    payload = _json(body, "option chain")
    _one_page(payload, "option chain")
    snapshots = _mapping(payload.get("snapshots"), "option chain snapshots")
    if len(snapshots) > MAX_CHAIN_SNAPSHOTS:
        raise _fail("option chain exceeds the supported contract bound")
    return snapshots


def _empty_quote() -> Mapping[str, Any]:
    return {
        "bid_price": None, "ask_price": None, "bid_size": None, "ask_size": None,
        "last_price": None, "last_size": None, "volume": None,
        "implied_volatility": None, "delta": None, "gamma": None, "theta": None,
        "vega": None, "rho": None, "quote_at": None, "quote_precision": None,
        "trade_at": None, "trade_precision": None,
    }


def _surface_quote(
    snapshot: Mapping[str, Any],
    *,
    completed_at: str,
    enforce_completion_cutoff: bool,
) -> Mapping[str, Any] | None:
    raw_quote, raw_trade = snapshot.get("latestQuote"), snapshot.get("latestTrade")
    quote = None if raw_quote is None else _mapping(raw_quote, "option quote")
    trade = None if raw_trade is None else _mapping(raw_trade, "option trade")
    bid = ask = last = None
    bid_size = ask_size = last_size = None
    quote_at = trade_at = None
    if quote is not None:
        bid = _decimal(quote.get("bp"), "option bid", minimum=Decimal("0"), nullable=True)
        ask = _decimal(quote.get("ap"), "option ask", minimum=Decimal("0"), nullable=True)
        bid_size = _integer(quote.get("bs"), "option bid size", nullable=True)
        ask_size = _integer(quote.get("as"), "option ask size", nullable=True)
        quote_at = _source_time(quote.get("t"), "option quote time", nullable=True)
    if trade is not None:
        last = _decimal(trade.get("p"), "option last price", minimum=Decimal("0"), nullable=True)
        last_size = _integer(trade.get("s"), "option last size", nullable=True)
        trade_at = _source_time(trade.get("t"), "option trade time", nullable=True)
    if quote_at is None:
        quote_at = trade_at
    if quote_at is None or all(value is None for value in (bid, ask, last)):
        return None
    if enforce_completion_cutoff:
        for source_time, label in (
            (quote_at, "option quote time"),
            (trade_at, "option trade time"),
        ):
            if source_time is not None:
                _require_not_after(source_time, cutoff=completed_at, label=label)
    greeks_raw = snapshot.get("greeks")
    greeks = {} if greeks_raw is None else _mapping(greeks_raw, "option greeks")
    return {
        "bid_price": bid, "ask_price": ask, "bid_size": bid_size, "ask_size": ask_size,
        "last_price": last, "last_size": last_size, "volume": None,
        "implied_volatility": _decimal(
            snapshot.get("impliedVolatility"),
            "option implied volatility",
            minimum=Decimal("0"),
            nullable=True,
        ),
        "delta": _decimal(greeks.get("delta"), "option delta", nullable=True),
        "gamma": _decimal(greeks.get("gamma"), "option gamma", nullable=True),
        "theta": _decimal(greeks.get("theta"), "option theta", nullable=True),
        "vega": _decimal(greeks.get("vega"), "option vega", nullable=True),
        "rho": _decimal(greeks.get("rho"), "option rho", nullable=True),
        "quote_at": quote_at, "quote_precision": "datetime",
        "trade_at": trade_at, "trade_precision": None if trade_at is None else "datetime",
    }


def _missing(date_key: str, session_date: str, completed_at: str, reason: str) -> Mapping[str, Any]:
    return {
        date_key: session_date,
        "state": "missing",
        "value": None,
        "missing_reason": reason,
        "available_at": completed_at,
        "available_precision": "datetime",
    }


def _observation(
    contract: Mapping[str, Any],
    *,
    value_key: str,
    date_key: str,
    session_date: str,
    completed_at: str,
    integer: bool,
    enforce_session_cutoff: bool = False,
) -> Mapping[str, Any]:
    value, source_date = contract[value_key], contract[f"{value_key}_date"]
    if value is None:
        return _missing(date_key, session_date, completed_at, "source_not_provided")
    if source_date is None:
        return _missing(date_key, session_date, completed_at, "source_date_not_provided")
    observed_date = _date(source_date, f"contract {date_key}")
    if enforce_session_cutoff and observed_date > session_date:
        raise _fail(f"contract {date_key} is later than the capture session")
    parsed = _integer(value, f"contract {value_key}") if integer else _decimal(
        value, f"contract {value_key}", minimum=Decimal("0")
    )
    return {
        date_key: observed_date,
        "state": "present",
        "value": parsed,
        "missing_reason": None,
        "available_at": completed_at,
        "available_precision": "datetime",
    }


def _surfaces(
    contracts: Sequence[Mapping[str, Any]],
    *,
    snapshots: Mapping[str, Any],
    session_date: str,
    completed_at: str,
    preserve_dated_observations: bool = False,
    enforce_completion_cutoff: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    symbols = {str(item["contract_symbol"]) for item in contracts}
    if set(snapshots) - symbols:
        raise _fail("option chain response contains a contract outside the exact catalog scope")
    result: list[Mapping[str, Any]] = []
    for contract in contracts:
        provider_id = str(contract["provider_contract_id"])
        symbol = str(contract["contract_symbol"])
        if contract["deliverable_kind"] == "nonstandard":
            state, missing_reason, exclusion_reason, quote = (
                "excluded", None, "nonstandard_deliverable", _empty_quote()
            )
            open_interest = _missing(
                "as_of_date", session_date, completed_at, "surface_excluded_nonstandard_deliverable"
            )
            close_price = _missing(
                "trade_date", session_date, completed_at, "surface_excluded_nonstandard_deliverable"
            )
        else:
            if preserve_dated_observations:
                open_interest = _observation(
                    contract,
                    value_key="_open_interest",
                    date_key="as_of_date",
                    session_date=session_date,
                    completed_at=completed_at,
                    integer=True,
                    enforce_session_cutoff=True,
                )
                close_price = _observation(
                    contract,
                    value_key="_close_price",
                    date_key="trade_date",
                    session_date=session_date,
                    completed_at=completed_at,
                    integer=False,
                    enforce_session_cutoff=True,
                )
            raw = snapshots.get(symbol)
            quote = (
                None
                if raw is None
                else _surface_quote(
                    _mapping(raw, f"option snapshot {symbol}"),
                    completed_at=completed_at,
                    enforce_completion_cutoff=enforce_completion_cutoff,
                )
            )
            if quote is None:
                state, missing_reason, exclusion_reason, quote = (
                    "missing", "source_quote_not_returned", None, _empty_quote()
                )
                if not preserve_dated_observations:
                    open_interest = _missing(
                        "as_of_date", session_date, completed_at, "surface_quote_not_returned"
                    )
                    close_price = _missing(
                        "trade_date", session_date, completed_at, "surface_quote_not_returned"
                    )
            elif _session_for_timestamp(
                quote["quote_at"], label=f"option quote time {symbol}"
            ) != session_date:
                state, missing_reason, exclusion_reason, quote = (
                    "missing", "source_quote_not_current_session", None, _empty_quote()
                )
                if not preserve_dated_observations:
                    open_interest = _missing(
                        "as_of_date",
                        session_date,
                        completed_at,
                        "surface_quote_not_current_session",
                    )
                    close_price = _missing(
                        "trade_date",
                        session_date,
                        completed_at,
                        "surface_quote_not_current_session",
                    )
            else:
                state, missing_reason, exclusion_reason = "present", None, None
                if not preserve_dated_observations:
                    open_interest = _observation(
                        contract,
                        value_key="_open_interest",
                        date_key="as_of_date",
                        session_date=session_date,
                        completed_at=completed_at,
                        integer=True,
                    )
                    close_price = _observation(
                        contract,
                        value_key="_close_price",
                        date_key="trade_date",
                        session_date=session_date,
                        completed_at=completed_at,
                        integer=False,
                    )
        result.append(
            {
                "provider_contract_id": provider_id,
                "surface_state": state,
                "missing_reason": missing_reason,
                "exclusion_reason": exclusion_reason,
                "quote": quote,
                "available_at": completed_at,
                "available_precision": "datetime",
                "feed": ALPACA_RESOLVED_FEED,
                "environment": ALPACA_ENVIRONMENT,
                "open_interest": open_interest,
                "close_price": close_price,
                "bars": (),
            }
        )
    return tuple(sorted(result, key=lambda row: str(row["provider_contract_id"])))


def _response_digest(responses: Mapping[str, bytes]) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    for name in sorted(responses):
        body = responses[name]
        if not isinstance(body, bytes) or not body:
            raise _fail("response evidence is invalid")
        total += len(body)
        if total > MAX_TOTAL_RESPONSE_BYTES:
            raise ResourceLimitError("Alpaca SPY option capture exceeds the total response bound")
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(2, "big"))
        digest.update(encoded)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest(), total


def _semantic(
    scope: Mapping[str, Any],
    contracts: Sequence[Mapping[str, Any]],
    surface_rows: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
) -> str:
    def without_availability(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in row.items()
            if key not in {"available_at", "available_precision"}
        }

    contract_keys = (
        "provider", "provider_contract_id", "contract_symbol", "expiration_date",
        "strike_price", "option_type", "deliverable_kind", "contract_multiplier",
        "nonstandard_deliverable_json", "contract_status", "_open_interest",
        "_open_interest_date", "_close_price", "_close_price_date",
    )
    material = {
        "collector": ALPACA_SPY_OPTIONS_COLLECTOR_ID,
        "normalization_version": ALPACA_NORMALIZATION_VERSION,
        "scope": dict(scope),
        "contracts": [{key: row[key] for key in contract_keys} for row in contracts],
        "surface": [
            {
                "provider_contract_id": row["provider_contract_id"],
                "surface_state": row["surface_state"],
                "missing_reason": row["missing_reason"],
                "exclusion_reason": row["exclusion_reason"],
                "quote": dict(row["quote"]),
                "open_interest": without_availability(row["open_interest"]),
                "close_price": without_availability(row["close_price"]),
            }
            for row in surface_rows
        ],
        "inputs": {
            "underlying": dict(inputs["underlying"]),
            "underlying_quote": without_availability(inputs["underlying_quote"]),
            "rate_curve": without_availability(inputs["rate_curve"]),
            "dividend_set": without_availability(inputs["dividend_set"]),
            "expiry_inputs": [without_availability(row) for row in inputs["expiry_inputs"]],
        },
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedAlpacaSpyOptionsCapture:
    capture: Mapping[str, Any]
    contracts: tuple[Mapping[str, Any], ...]
    surface_rows: tuple[Mapping[str, Any], ...]
    inputs: Mapping[str, Any]
    scope: Mapping[str, Any]
    captured_at: TemporalValue
    semantic_identity: str
    response_sha256: str
    response_byte_count: int
    raw_responses: tuple[tuple[str, bytes], ...]

    @property
    def fetched_count(self) -> int:
        return 1 + len(self.contracts) + len(self.surface_rows) + len(self.inputs["expiry_inputs"])


def parse_alpaca_spy_options_capture(
    *,
    calendar_body: bytes,
    underlying_body: bytes,
    contracts_body: bytes,
    snapshots_body: bytes,
    session_date: str,
    requested_at: datetime,
    completed_at: datetime,
) -> ParsedAlpacaSpyOptionsCapture:
    """Normalize the exact one-session SPY capture before any store is opened."""

    session = _date(session_date, "session date")
    requested, completed = _instant(requested_at, "request time"), _instant(completed_at, "completion time")
    if datetime.fromisoformat(completed.replace("Z", "+00:00")) < datetime.fromisoformat(
        requested.replace("Z", "+00:00")
    ):
        raise _fail("completion time precedes request time")
    if not is_alpaca_opra_trading_day(calendar_body, session_date=session):
        raise _fail("calendar response does not authorize this session")
    underlying = parse_alpaca_underlying_snapshot(underlying_body, completed_at=completed_at)
    _require_session_timestamp(
        underlying.quote["observed_at"],
        session_date=session,
        label="underlying observation time",
    )
    expiration = select_alpaca_expiration(
        contracts_body, session_date=session, spot_price=underlying.spot_price
    )
    contracts = _contracts(
        contracts_body,
        session_date=session,
        selected_expiration=expiration,
        spot_price=underlying.spot_price,
        completed_at=completed,
    )
    surfaces = _surfaces(
        contracts, snapshots=_chain(snapshots_body), session_date=session, completed_at=completed
    )
    quote = dict(underlying.quote)
    for key in ("_bid_size", "_ask_size", "_last_size", "_trade_at"):
        quote.pop(key, None)
    scope = {
        "underlying_symbol": SPY_SYMBOL,
        "session_date": session,
        "requested_feed": ALPACA_REQUESTED_FEED,
        "resolved_feed": ALPACA_RESOLVED_FEED,
        "environment": ALPACA_ENVIRONMENT,
        "deliverable_policy": "standard_only",
        "completeness": "complete",
        "target_dte": TARGET_DTE,
        "minimum_dte": MIN_DTE,
        "maximum_dte": MAX_DTE,
        "lower_strike_multiplier": "0.8",
        "upper_strike_multiplier": "1.2",
        "selected_expiration": expiration,
        "spot_price": underlying.spot_price,
        "spot_method": underlying.spot_method,
    }
    inputs = {
        "underlying": {
            "state": "present", "missing_reason": None, "observed_at": quote["observed_at"],
            "observed_precision": "datetime", "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT,
        },
        "underlying_quote": quote,
        "rate_curve": {
            "state": "missing", "missing_reason": "not_collected_in_live_v1",
            "curve_date": None, "source_name": None, "available_at": completed,
            "available_precision": "datetime", "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT, "points": (),
        },
        "dividend_set": {
            "state": "missing", "missing_reason": "not_collected_in_live_v1",
            "source_name": None, "available_at": completed, "available_precision": "datetime",
            "feed": ALPACA_RESOLVED_FEED, "environment": ALPACA_ENVIRONMENT, "cashflows": (),
        },
        "expiry_inputs": (
            {
                "expiration_date": expiration, "state": "missing",
                "missing_reason": "synchronized_model_inputs_not_collected_in_live_v1",
                "spot_price": None, "risk_free_rate": None, "dividend_yield": None,
                "forward_price": None, "available_at": completed, "available_precision": "datetime",
                "feed": ALPACA_RESOLVED_FEED, "environment": ALPACA_ENVIRONMENT,
            },
        ),
    }
    capture = {
        "requested_feed": ALPACA_REQUESTED_FEED,
        "resolved_feed": ALPACA_RESOLVED_FEED,
        "environment": ALPACA_ENVIRONMENT,
        "requested_at": requested,
        "requested_precision": "datetime",
        "completed_at": completed,
        "completed_precision": "datetime",
        "available_at": completed,
        "available_precision": "datetime",
        "completeness": "complete",
        "deliverable_policy": "standard_only",
    }
    raw_responses = (
        ("calendar", calendar_body),
        ("underlying", underlying_body),
        ("contracts", contracts_body),
        ("option_chain", snapshots_body),
    )
    response_sha256, response_byte_count = _response_digest(dict(raw_responses))
    normalized_semantic = _semantic(scope, contracts, surfaces, inputs)
    semantic_identity = hashlib.sha256(
        dumps_strict(
            {
                "normalized_semantic": normalized_semantic,
                "raw_response_sha256": response_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()
    return ParsedAlpacaSpyOptionsCapture(
        capture=capture,
        contracts=contracts,
        surface_rows=surfaces,
        inputs=inputs,
        scope=scope,
        captured_at=TemporalValue.parse(completed, pointer="/completed_at"),
        semantic_identity=semantic_identity,
        response_sha256=response_sha256,
        response_byte_count=response_byte_count,
        raw_responses=raw_responses,
    )


@dataclass(frozen=True, slots=True)
class AlpacaHttpResponse:
    """One bounded Alpaca response supplied by the host-owned transport."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


@runtime_checkable
class AlpacaHttpTransport(Protocol):
    """One-attempt host-owned Alpaca HTTPS seam; it performs no retry."""

    def request(
        self,
        *,
        host: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> AlpacaHttpResponse: ...


@dataclass(frozen=True, slots=True)
class AlpacaSpyOptionsRunReport:
    outcome: str
    session_date: str
    requests_issued: int
    selected_expiration: str | None
    contracts: int
    surface_rows: int
    written_count: int

    def mapping(self) -> dict[str, object]:
        return {
            "contracts": self.contracts,
            "outcome": self.outcome,
            "requests_issued": self.requests_issued,
            "selected_expiration": self.selected_expiration,
            "session_date": self.session_date,
            "surface_rows": self.surface_rows,
            "written_count": self.written_count,
        }


_ALPACA_PAPER_HOST: Final = "paper-api.alpaca.markets"
_ALPACA_DATA_HOST: Final = "data.alpaca.markets"
_TIMEOUT_SECONDS: Final = 60
_MAX_RUN_SECONDS: Final = 120
_REQUEST_SHAPES: Final = {
    (_ALPACA_PAPER_HOST, "/v3/calendar/OPRA"): frozenset({"start", "end"}),
    (_ALPACA_DATA_HOST, "/v2/stocks/snapshots"): frozenset({"symbols", "feed"}),
    (_ALPACA_PAPER_HOST, "/v2/options/contracts"): frozenset(
        {
            "underlying_symbols",
            "expiration_date_gte",
            "expiration_date_lte",
            "strike_price_gte",
            "strike_price_lte",
            "status",
            "limit",
            "show_deliverables",
        }
    ),
    (_ALPACA_DATA_HOST, "/v1beta1/options/snapshots/SPY"): frozenset(
        {
            "feed",
            "expiration_date",
            "strike_price_gte",
            "strike_price_lte",
            "limit",
        }
    ),
}
_ETF_GRID_PAGE_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=:-]{1,512}$")


def _is_iso_date_query(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_nonnegative_decimal_query(value: object) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_NUMERIC_TEXT_LENGTH
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    ):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _is_grid_page_token(value: object) -> bool:
    return isinstance(value, str) and _ETF_GRID_PAGE_TOKEN.fullmatch(value) is not None


def _is_etf_grid_request(
    host: object, path: object, query: Mapping[str, str]
) -> bool:
    contract_fields = frozenset(
        {
            "underlying_symbols", "expiration_date_gte", "expiration_date_lte",
            "strike_price_gte", "strike_price_lte", "status", "limit",
            "show_deliverables",
        }
    )
    chain_fields = frozenset(
        {
            "feed", "expiration_date", "strike_price_gte", "strike_price_lte", "limit",
        }
    )
    if not isinstance(host, str) or not isinstance(path, str):
        return False
    query_keys = frozenset(query)
    if host == _ALPACA_PAPER_HOST and path == "/v2/options/contracts":
        if query_keys not in {contract_fields, contract_fields | {"page_token"}}:
            return False
        return (
            query.get("underlying_symbols") in ALPACA_ETF_OPTIONS_UNIVERSE
            and _is_iso_date_query(query.get("expiration_date_gte"))
            and _is_iso_date_query(query.get("expiration_date_lte"))
            and _is_nonnegative_decimal_query(query.get("strike_price_gte"))
            and _is_nonnegative_decimal_query(query.get("strike_price_lte"))
            and query.get("status") == "active"
            and query.get("limit") == str(MAX_CONTRACTS)
            and query.get("show_deliverables") == "true"
            and ("page_token" not in query or _is_grid_page_token(query["page_token"]))
        )
    prefix = "/v1beta1/options/snapshots/"
    if host == _ALPACA_DATA_HOST and path.startswith(prefix):
        symbol = path.removeprefix(prefix)
        if symbol not in ALPACA_ETF_OPTIONS_UNIVERSE:
            return False
        if query_keys not in {chain_fields, chain_fields | {"page_token"}}:
            return False
        return (
            query.get("feed") == ALPACA_REQUESTED_FEED
            and _is_iso_date_query(query.get("expiration_date"))
            and _is_nonnegative_decimal_query(query.get("strike_price_gte"))
            and _is_nonnegative_decimal_query(query.get("strike_price_lte"))
            and query.get("limit") == str(MAX_CHAIN_SNAPSHOTS)
            and ("page_token" not in query or _is_grid_page_token(query["page_token"]))
        )
    if host == _ALPACA_DATA_HOST and path == "/v2/stocks/snapshots":
        return query == {
            "symbols": ",".join(ALPACA_ETF_OPTIONS_UNIVERSE),
            "feed": "iex",
        }
    return False


class StdlibAlpacaHttpTransport:
    'Fixed-host, single-attempt HTTPS transport used by the scheduled wrapper.'

    def request(
        self,
        *,
        host: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> AlpacaHttpResponse:
        if (
            (
                _REQUEST_SHAPES.get((host, path)) != frozenset(query)
                and not _is_etf_grid_request(host, path, query)
            )
            or set(headers)
            != {
                "Accept",
                "User-Agent",
                "APCA-API-KEY-ID",
                "APCA-API-SECRET-KEY",
            }
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds < 1
            or timeout_seconds > _TIMEOUT_SECONDS
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_RESPONSE_BYTES
        ):
            raise ValidationError("Alpaca provider request is outside the fixed policy")
        target = f"{quote(path, safe='/')}?{urlencode(dict(query))}"
        connection = http.client.HTTPSConnection(host, timeout=timeout_seconds)
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError(
                        "Alpaca provider length is invalid"
                    ) from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError(
                        "Alpaca provider response exceeds its byte bound"
                    )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError(
                    "Alpaca provider response exceeds its byte bound"
                )
            return AlpacaHttpResponse(
                status=int(response.status),
                media_type=response.getheader("Content-Type") or "",
                body=body,
                redirected=(
                    300 <= int(response.status) < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Alpaca provider transport failed") from exc
        finally:
            connection.close()


def _credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError("Alpaca credential is missing or invalid")
    return value


def _capture_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValidationError("Alpaca capture time is invalid")
    try:
        parsed = TemporalValue.parse(value, pointer="/captured_at")
    except ValidationError as exc:
        raise ValidationError("Alpaca capture time is invalid") from exc
    if parsed.precision is not TemporalPrecision.DATETIME or not isinstance(parsed.value, datetime):
        raise ValidationError("Alpaca capture time is invalid")
    return parsed.value.astimezone(timezone.utc)


def _response_body(response: object) -> bytes:
    if not isinstance(response, AlpacaHttpResponse):
        raise ValidationError("Alpaca transport returned an invalid response")
    media_type = response.media_type.split(";", 1)[0].strip().casefold() if isinstance(response.media_type, str) else ""
    if (
        isinstance(response.status, bool)
        or response.status != 200
        or response.redirected
        or media_type != "application/json"
        or not isinstance(response.body, bytes)
        or not response.body
        or len(response.body) > MAX_RESPONSE_BYTES
    ):
        raise ValidationError("Alpaca provider response is outside the fixed request policy")
    return response.body


def _request(
    transport: AlpacaHttpTransport,
    *,
    host: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    deadline: float,
) -> bytes:
    timeout_seconds = min(_TIMEOUT_SECONDS, int(deadline - monotonic()))
    if timeout_seconds < 1:
        raise ResourceLimitError("Alpaca SPY option capture exceeded 120 seconds")
    body = _response_body(
        transport.request(
            host=host,
            path=path,
            query=dict(query),
            headers=dict(headers),
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_RESPONSE_BYTES,
        )
    )
    if monotonic() > deadline:
        raise ResourceLimitError("Alpaca SPY option capture exceeded 120 seconds")
    return body


def _fixed_scope_root(project_root: Path, stores: StoreMap) -> Path:
    if not isinstance(project_root, Path) or not isinstance(stores, StoreMap):
        raise ValidationError("Alpaca SPY options target binding is invalid")
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError("Alpaca SPY options target binding is invalid") from exc
    if root != project_root or project_root.is_symlink() or not root.is_dir():
        raise ValidationError("Alpaca SPY options target binding is invalid")
    if stores.path(StoreRole.MARKET) != root / "data" / "market.sqlite":
        raise ValidationError("Alpaca SPY options target binding is invalid")
    return root


def run_alpaca_spy_option_surface(
    *,
    project_root: Path,
    stores: StoreMap,
    registry: Registry,
    transport: AlpacaHttpTransport,
    captured_at: str,
    api_key: str,
    api_secret: str,
) -> AlpacaSpyOptionsRunReport:
    """Fetch exactly one paper/indicative SPY surface, then publish it once.

    The request plan is fixed: OPRA calendar, IEX SPY snapshot, bounded
    contracts catalog, and exact-expiry indicative chain.  A non-trading
    calendar result stops after its first request and touches no store.
    """

    deadline = monotonic() + _MAX_RUN_SECONDS
    _fixed_scope_root(project_root, stores)
    if not isinstance(registry, Registry) or not isinstance(transport, AlpacaHttpTransport):
        raise ValidationError("Alpaca SPY options runner dependencies are invalid")
    captured = _capture_time(captured_at)
    session = captured.astimezone(ZoneInfo("America/New_York")).date()
    session_text = session.isoformat()
    key, secret = _credential(api_key), _credential(api_secret)
    headers = {
        "Accept": "application/json",
        "User-Agent": "QuantDataInfra/1.0",
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    calendar_body = _request(
        transport,
        host=_ALPACA_PAPER_HOST,
        path="/v3/calendar/OPRA",
        query={"start": session_text, "end": session_text},
        headers=headers,
        deadline=deadline,
    )
    if not is_alpaca_opra_trading_day(calendar_body, session_date=session_text):
        return AlpacaSpyOptionsRunReport(
            outcome="skipped",
            session_date=session_text,
            requests_issued=1,
            selected_expiration=None,
            contracts=0,
            surface_rows=0,
            written_count=0,
        )
    underlying_body = _request(
        transport,
        host=_ALPACA_DATA_HOST,
        path="/v2/stocks/snapshots",
        query={"symbols": SPY_SYMBOL, "feed": "iex"},
        headers=headers,
        deadline=deadline,
    )
    underlying = parse_alpaca_underlying_snapshot(underlying_body, completed_at=captured)
    _require_session_timestamp(
        underlying.quote["observed_at"],
        session_date=session_text,
        label="underlying observation time",
    )
    lower = format(Decimal(underlying.spot_price) * LOWER_STRIKE_MULTIPLIER, "f")
    upper = format(Decimal(underlying.spot_price) * UPPER_STRIKE_MULTIPLIER, "f")
    contracts_body = _request(
        transport,
        host=_ALPACA_PAPER_HOST,
        path="/v2/options/contracts",
        query={
            "underlying_symbols": SPY_SYMBOL,
            "expiration_date_gte": (session.fromordinal(session.toordinal() + MIN_DTE)).isoformat(),
            "expiration_date_lte": (session.fromordinal(session.toordinal() + MAX_DTE)).isoformat(),
            "strike_price_gte": lower,
            "strike_price_lte": upper,
            "status": "active",
            "limit": str(MAX_CONTRACTS),
            "show_deliverables": "true",
        },
        headers=headers,
        deadline=deadline,
    )
    expiration = select_alpaca_expiration(
        contracts_body,
        session_date=session_text,
        spot_price=underlying.spot_price,
    )
    snapshots_body = _request(
        transport,
        host=_ALPACA_DATA_HOST,
        path="/v1beta1/options/snapshots/SPY",
        query={
            "feed": ALPACA_REQUESTED_FEED,
            "expiration_date": expiration,
            "strike_price_gte": lower,
            "strike_price_lte": upper,
            "limit": str(MAX_CHAIN_SNAPSHOTS),
        },
        headers=headers,
        deadline=deadline,
    )
    completed = datetime.now(timezone.utc)
    parsed = parse_alpaca_spy_options_capture(
        calendar_body=calendar_body,
        underlying_body=underlying_body,
        contracts_body=contracts_body,
        snapshots_body=snapshots_body,
        session_date=session_text,
        requested_at=captured,
        completed_at=completed,
    )
    if monotonic() > deadline:
        raise ResourceLimitError("Alpaca SPY option capture exceeded 120 seconds")
    receipt = AlpacaSpyOptionsPublisher(stores, registry).publish(parsed)
    if receipt.outcome not in {"succeeded", "unchanged"}:
        raise ConflictError("Alpaca SPY options publisher outcome is invalid")
    return AlpacaSpyOptionsRunReport(
        outcome=receipt.outcome,
        session_date=session_text,
        requests_issued=4,
        selected_expiration=str(parsed.scope["selected_expiration"]),
        contracts=len(parsed.contracts),
        surface_rows=len(parsed.surface_rows),
        written_count=receipt.written_count,
    )


def _stage10_spy(store_map: StoreMap) -> Mapping[str, Any]:
    with read_connection(store_map, StoreRole.MARKET) as connection:
        rows = list(
            connection.execute(
                """
                SELECT instrument_id, provider, provider_symbol, asset_type, display_name,
                       exchange_code, captured_at
                FROM stage10_instruments
                WHERE provider='fmp' AND provider_symbol=? AND asset_type='etf'
                """,
                (SPY_SYMBOL,),
            )
        )
    if len(rows) != 1:
        raise ValidationError("Canonical Stage10 SPY instrument is unavailable")
    return {key: rows[0][key] for key in rows[0].keys()}


def _ensure_bridge(
    connection: sqlite3.Connection,
    *,
    stage10_spy: Mapping[str, Any],
    run_id: str,
) -> tuple[str, int]:
    instrument_id = _text(stage10_spy.get("instrument_id"), "Stage10 SPY instrument id", 256)
    existing = connection.execute(
        "SELECT instrument_id, asset_type, canonical_symbol FROM instruments WHERE instrument_id=?",
        (instrument_id,),
    ).fetchone()
    if existing is not None:
        if existing["asset_type"] != "etf" or existing["canonical_symbol"] not in {None, SPY_SYMBOL}:
            raise ConflictError("Legacy option-underlying bridge conflicts with Stage10 SPY")
        return instrument_id, 0
    connection.execute(
        """
        INSERT INTO instruments (
            instrument_id, asset_type, created_run_id, canonical_symbol, display_name,
            exchange, currency, country, first_seen_at, last_seen_at, active
        ) VALUES (?, 'etf', ?, ?, ?, ?, 'USD', 'US', ?, ?, 1)
        """,
        (
            instrument_id, run_id, SPY_SYMBOL, stage10_spy.get("display_name"),
            stage10_spy.get("exchange_code"), stage10_spy.get("captured_at"),
            stage10_spy.get("captured_at"),
        ),
    )
    return instrument_id, 1


def _ensure_contract(
    connection: sqlite3.Connection,
    *,
    contract: Mapping[str, Any],
    underlying_instrument_id: str,
    run_id: str,
) -> tuple[str, int]:
    contract_id = stable_id("option_contract", ALPACA_PROVIDER, str(contract["provider_contract_id"]))
    expected = (
        contract_id, underlying_instrument_id, ALPACA_PROVIDER, str(contract["provider_contract_id"]),
        str(contract["contract_symbol"]), str(contract["expiration_date"]), str(contract["strike_price"]),
        str(contract["option_type"]), str(contract["deliverable_kind"]),
        int(contract["contract_multiplier"]), contract["nonstandard_deliverable_json"],
        str(contract["contract_status"]),
    )
    existing = connection.execute(
        """
        SELECT contract_id, underlying_instrument_id, provider, provider_contract_id,
               contract_symbol, expiration_date, strike_price, option_type,
               deliverable_kind, contract_multiplier, nonstandard_deliverable_json,
               contract_status
        FROM option_contracts
        WHERE provider=? AND provider_contract_id=?
        """,
        (ALPACA_PROVIDER, contract["provider_contract_id"]),
    ).fetchone()
    if existing is not None:
        if tuple(existing[key] for key in existing.keys()) != expected:
            raise ConflictError("Immutable Alpaca option contract conflicts with stored identity")
        return contract_id, 0
    connection.execute(
        """
        INSERT INTO option_contracts (
            contract_id, underlying_instrument_id, provider, provider_contract_id,
            contract_symbol, expiration_date, strike_price, option_type,
            deliverable_kind, contract_multiplier, nonstandard_deliverable_json,
            contract_status, available_at, available_precision, created_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?)
        """,
        (*expected, contract["available_at"], run_id),
    )
    return contract_id, 1


def _write_raw_option_responses(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedAlpacaSpyOptionsCapture | ParsedAlpacaEtfOptionsCapture,
    capture_id: str,
) -> int:
    """Write byte-faithful provider evidence before canonical option rows."""

    if (
        not parsed.raw_responses
        or len({name for name, _ in parsed.raw_responses}) != len(parsed.raw_responses)
    ):
        raise ValidationError("Alpaca option raw response evidence is invalid")
    written = 0
    for source_order, (response_name, body) in enumerate(
        parsed.raw_responses,
        start=1,
    ):
        name = _text(response_name, "raw response name", 128)
        if not isinstance(body, bytes) or not body:
            raise ValidationError("Alpaca option raw response evidence is invalid")
        content_sha256 = hashlib.sha256(body).hexdigest()
        raw_response_id = stable_id(
            "option_raw_response",
            ALPACA_PROVIDER,
            content_sha256,
        )
        existing = connection.execute(
            """
            SELECT provider, content_sha256, media_type, byte_count, response_body
            FROM option_raw_responses
            WHERE raw_response_id=?
            """,
            (raw_response_id,),
        ).fetchone()
        expected = (
            ALPACA_PROVIDER,
            content_sha256,
            "application/json",
            len(body),
            body,
        )
        if existing is None:
            connection.execute(
                """
                INSERT INTO option_raw_responses (
                    raw_response_id, provider, content_sha256, media_type,
                    byte_count, response_body
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (raw_response_id, *expected),
            )
            written += 1
        elif tuple(existing[key] for key in existing.keys()) != expected:
            raise ConflictError("Immutable Alpaca raw option response conflicts")
        connection.execute(
            """
            INSERT INTO option_capture_raw_responses (
                capture_id, response_name, raw_response_id, source_order,
                captured_at, captured_precision
            ) VALUES (?, ?, ?, ?, ?, 'datetime')
            """,
            (
                capture_id,
                name,
                raw_response_id,
                source_order,
                parsed.capture["completed_at"],
            ),
        )
        written += 1
    return written


class AlpacaSpyOptionsPublisher:
    """Publish one fully prepared live capture through the existing options tables."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Alpaca SPY options publisher dependencies are invalid")
        datasets = {item.id for item in registry.datasets_for(StoreRole.MARKET.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise ValidationError("Alpaca SPY options datasets are not registered")
        self._store_map = store_map
        self._coordinator = IngestionCoordinator(store_map, code_version="alpaca_spy_options.1.2.0")

    def publish(
        self,
        parsed: ParsedAlpacaSpyOptionsCapture,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        if not isinstance(parsed, ParsedAlpacaSpyOptionsCapture):
            raise ValidationError("Alpaca SPY options capture is invalid")
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self._store_map, StoreRole.MARKET)
        stage10_spy = _stage10_spy(self._store_map)
        run_id = stable_id(
            "alpaca_spy_option_surface_run",
            ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            parsed.semantic_identity,
        )

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise ValidationError("Alpaca SPY options coordinator supplied an unexpected run identity")
            underlying_id, written = _ensure_bridge(
                connection, stage10_spy=stage10_spy, run_id=active_run_id
            )
            artifact_id = stable_id(
                "alpaca_spy_option_surface_artifact",
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                parsed.response_sha256,
                parsed.semantic_identity,
            )
            snapshot_id = stable_id(
                "alpaca_spy_option_surface_snapshot",
                ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                parsed.semantic_identity,
            )
            capture_id = stable_id(
                "option_capture",
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                parsed.semantic_identity,
            )
            contract_ids: dict[str, str] = {}
            for contract in parsed.contracts:
                contract_id, inserted = _ensure_contract(
                    connection,
                    contract=contract,
                    underlying_instrument_id=underlying_id,
                    run_id=active_run_id,
                )
                contract_ids[str(contract["provider_contract_id"])] = contract_id
                written += inserted
            connection.execute(
                """
                INSERT INTO option_surface_captures (
                    capture_id, semantic_identity, underlying_instrument_id,
                    requested_feed, resolved_feed, environment, request_scope_json,
                    requested_at, requested_precision, completed_at, completed_precision,
                    available_at, available_precision, completeness, deliverable_policy,
                    artifact_id, source_snapshot_id, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id, parsed.semantic_identity, underlying_id,
                    parsed.capture["requested_feed"], parsed.capture["resolved_feed"],
                    parsed.capture["environment"], dumps_strict(dict(parsed.scope)),
                    parsed.capture["requested_at"], parsed.capture["requested_precision"],
                    parsed.capture["completed_at"], parsed.capture["completed_precision"],
                    parsed.capture["available_at"], parsed.capture["available_precision"],
                    parsed.capture["completeness"], parsed.capture["deliverable_policy"],
                    artifact_id, snapshot_id, active_run_id,
                ),
            )
            written += 1
            written += _write_raw_option_responses(
                connection,
                parsed=parsed,
                capture_id=capture_id,
            )
            input_count, _ = _write_capture_inputs(
                connection, parsed=parsed, capture_id=capture_id, underlying_instrument_id=underlying_id
            )
            written += input_count
            written += _write_surface_rows(
                connection, parsed=parsed, capture_id=capture_id, contract_ids=contract_ids
            )
            artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                content_sha256=parsed.response_sha256,
                media_type="application/json",
                byte_count=parsed.response_byte_count,
                source_reference="alpaca/spy-option-surface-v1.json",
                request_scope=dict(parsed.scope),
                captured_at=parsed.captured_at.raw or parsed.capture["completed_at"],
                captured_precision="datetime",
                normalization_version=ALPACA_NORMALIZATION_VERSION,
            )
            warnings = (
                "indicative_feed_not_executable",
                "rate_and_dividend_inputs_not_collected",
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=snapshot_id,
                    dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                    semantic_identity=parsed.semantic_identity,
                    scope=dict(parsed.scope),
                    completeness="complete",
                    row_count=parsed.fetched_count,
                    captured_at=parsed.captured_at.raw or parsed.capture["completed_at"],
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(artifact_id,),
                    warnings=warnings,
                ),
                quality_results=(
                    QualityWrite(
                        quality_result_id=stable_id(
                            "alpaca_spy_option_surface_quality",
                            active_run_id,
                            ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                        ),
                        dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                        rule_id="single_capture_cohort",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=snapshot_id,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        observed={
                            "contracts": len(parsed.contracts),
                            "surface_rows": len(parsed.surface_rows),
                            "selected_expiration": parsed.scope["selected_expiration"],
                            "written_count": written,
                        },
                    ),
                ),
                warnings=warnings,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            output_dataset_ids=(
                ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID,
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                ALPACA_OPTIONS_RAW_EVIDENCE_DATASET_ID,
                ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            ),
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=ALPACA_SPY_OPTIONS_COLLECTOR_ID,
            scope={
                "request_scope": dict(parsed.scope),
                "resolved_feed": ALPACA_RESOLVED_FEED,
                "environment": ALPACA_ENVIRONMENT,
            },
            started_at=parsed.capture["requested_at"],
            completed_at=parsed.capture["completed_at"],
            fetched_count=parsed.fetched_count,
            writer=writer,
            held_locks=held_locks,
        )

@dataclass(frozen=True, slots=True)
class ParsedAlpacaEtfOptionsCapture:
    capture: Mapping[str, Any]
    contracts: tuple[Mapping[str, Any], ...]
    surface_rows: tuple[Mapping[str, Any], ...]
    inputs: Mapping[str, Any]
    scope: Mapping[str, Any]
    captured_at: TemporalValue
    semantic_identity: str
    response_sha256: str
    response_byte_count: int
    raw_responses: tuple[tuple[str, bytes], ...]

    @property
    def fetched_count(self) -> int:
        return 1 + len(self.contracts) + len(self.surface_rows) + len(self.inputs["expiry_inputs"])


@dataclass(slots=True)
class _EtfGridBudget:
    deadline: float
    requests_issued: int = 0
    response_byte_count: int = 0
    terminal_response_limit: bool = False

    def ensure_deadline(self) -> None:
        if monotonic() >= self.deadline:
            raise ResourceLimitError(
                "Alpaca ETF option grid exceeded the overall deadline"
            )

    def request(
        self,
        transport: AlpacaHttpTransport,
        *,
        host: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> bytes:
        self.ensure_deadline()
        if self.requests_issued >= MAX_ETF_OPTIONS_REQUESTS:
            raise ResourceLimitError("Alpaca ETF option grid exceeds the request bound")
        if self.response_byte_count >= MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES:
            raise ResourceLimitError("Alpaca ETF option grid exceeds the aggregate response bound")
        remaining_bytes = (
            MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES - self.response_byte_count
        )
        admitted_bytes = min(MAX_RESPONSE_BYTES, remaining_bytes)
        timeout_seconds = min(_TIMEOUT_SECONDS, int(self.deadline - monotonic()))
        if timeout_seconds < 1:
            raise ResourceLimitError("Alpaca ETF option grid exceeded the deadline")
        self.requests_issued += 1
        try:
            response = transport.request(
                host=host,
                path=path,
                query=dict(query),
                headers=dict(headers),
                timeout_seconds=timeout_seconds,
                max_bytes=admitted_bytes,
            )
            body = _response_body(response)
        except ResourceLimitError:
            self.terminal_response_limit = True
            raise
        except ValidationError as exc:
            raise StoreUnavailableError(
                "Alpaca ETF option grid provider response failed the fixed policy"
            ) from exc
        if len(body) > admitted_bytes:
            self.terminal_response_limit = True
            raise ResourceLimitError("Alpaca ETF option grid exceeds the aggregate response bound")
        self.response_byte_count += len(body)
        self.ensure_deadline()
        return body


def _grid_page_token(payload: Mapping[str, Any], label: str) -> str | None:
    markers: list[str] = []
    for field in ("next_page_token", "page_token"):
        value = payload.get(field)
        if value is None:
            continue
        if not _is_grid_page_token(value):
            raise _fail(f"{label} pagination marker is invalid")
        markers.append(value)
    if len(set(markers)) > 1:
        raise _fail(f"{label} pagination markers conflict")
    return markers[0] if markers else None


def _grid_catalog_page(body: bytes) -> tuple[Sequence[Any], str | None]:
    payload = _json(body, "contracts")
    rows = _array(payload.get("option_contracts"), "contracts response", empty=True)
    token = _grid_page_token(payload, "contracts response")
    if not rows and token is not None:
        raise _fail("contracts response advances an empty page")
    return rows, token


def _grid_chain_page(body: bytes) -> tuple[Mapping[str, Any], str | None]:
    payload = _json(body, "option chain")
    snapshots = _mapping(payload.get("snapshots"), "option chain snapshots")
    if len(snapshots) > MAX_CHAIN_SNAPSHOTS:
        raise _fail("option chain exceeds the supported page bound")
    token = _grid_page_token(payload, "option chain response")
    if not snapshots and token is not None:
        raise _fail("option chain response advances an empty page")
    return snapshots, token


def _grid_underlying_snapshot_payload(body: bytes) -> Mapping[str, Any]:
    payload = _json(body, "underlying")
    nested = payload.get("snapshots")
    snapshots = (
        _mapping(nested, "underlying snapshots")
        if nested is not None
        else payload
    )
    if set(snapshots) - set(ALPACA_ETF_OPTIONS_UNIVERSE):
        raise _fail("underlying snapshots contain a symbol outside the fixed ETF universe")
    return snapshots


def _grid_underlying_snapshot(
    snapshots: Mapping[str, Any], *, symbol: str, completed_at: datetime
) -> AlpacaUnderlyingSnapshot:
    if symbol not in ALPACA_ETF_OPTIONS_UNIVERSE or symbol not in snapshots:
        raise _fail(f"underlying {symbol} snapshot is missing")
    completed = _instant(completed_at, "underlying completion time")
    quote, spot, method = _underlying_quote(
        _mapping(snapshots[symbol], f"underlying {symbol} snapshot"),
        completed,
        enforce_completion_cutoff=True,
    )
    return AlpacaUnderlyingSnapshot(
        spot_price=spot, spot_method=method, quote=quote
    )


def _grid_catalog_expiration(
    row: Mapping[str, Any], *, underlying_symbol: str, label: str
) -> str:
    if _text(row.get("underlying_symbol"), f"{label} underlying symbol", 32) != underlying_symbol:
        raise _fail(f"{label} underlying symbol is outside the fixed scope")
    if _text(row.get("status"), f"{label} status", 32) != "active":
        raise _fail(f"{label} status is outside the fixed scope")
    return _date(row.get("expiration_date"), f"{label} expiration")


def _grid_deliverable(
    row: Mapping[str, Any], *, underlying_symbol: str, multiplier: int
) -> tuple[str, str | None]:
    return _deliverable_for_underlying(
        row,
        underlying_symbol=underlying_symbol,
        multiplier=multiplier,
    )


def select_alpaca_etf_expirations(
    catalog_rows: Sequence[Any],
    *,
    session_date: str,
    spot_price: str,
    underlying_symbol: str,
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Map every fixed DTE target to its nearest positive listed expiry."""

    if underlying_symbol not in ALPACA_ETF_OPTIONS_UNIVERSE:
        raise _fail("underlying symbol is outside the fixed ETF universe")
    session = date.fromisoformat(_date(session_date, "session date"))
    spot = Decimal(_decimal(spot_price, "spot price", minimum=Decimal("0.0000000001")))
    lower, upper = spot * LOWER_STRIKE_MULTIPLIER, spot * UPPER_STRIKE_MULTIPLIER
    listed_standard_types: dict[date, set[str]] = {}
    for index, raw in enumerate(catalog_rows):
        label = f"contract row {index}"
        row = _mapping(raw, label)
        expiration = date.fromisoformat(
            _grid_catalog_expiration(row, underlying_symbol=underlying_symbol, label=label)
        )
        dte = (expiration - session).days
        if not 1 <= dte <= ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS:
            raise _fail("contracts response contains an out-of-scope expiration")
        strike = Decimal(
            _decimal(
                row.get("strike_price"),
                f"{label} strike",
                minimum=Decimal("0.0000000001"),
            )
        )
        if not lower <= strike <= upper:
            raise _fail("contracts response contains a strike outside the fixed scope")
        option_type = _text(row.get("type"), f"{label} type", 16).casefold()
        if option_type not in {"call", "put"}:
            raise _fail(f"{label} type is invalid")
        multiplier = _integer(row.get("size"), f"{label} size")
        if multiplier is None or multiplier < 1:
            raise _fail(f"{label} size is invalid")
        deliverable_kind, _ = _grid_deliverable(
            row,
            underlying_symbol=underlying_symbol,
            multiplier=multiplier,
        )
        if deliverable_kind == "standard":
            listed_standard_types.setdefault(expiration, set()).add(option_type)
    eligible = {
        expiration
        for expiration, option_types in listed_standard_types.items()
        if option_types == {"call", "put"}
    }
    if not eligible:
        raise _fail("contracts response has no positive listed expiration in the fixed scope")
    grouped: dict[date, list[int]] = {}
    for target in ALPACA_ETF_OPTIONS_DTE_TARGETS:
        selected = min(
            eligible,
            key=lambda expiration: (abs((expiration - session).days - target), expiration),
        )
        grouped.setdefault(selected, []).append(target)
    return tuple(
        (expiration.isoformat(), tuple(grouped[expiration]))
        for expiration in sorted(grouped)
    )


def _grid_contracts(
    catalog_rows: Sequence[Any],
    *,
    session_date: str,
    selected_expiration: str,
    spot_price: str,
    completed_at: str,
    underlying_symbol: str,
) -> tuple[Mapping[str, Any], ...]:
    session = date.fromisoformat(_date(session_date, "session date"))
    selected = _date(selected_expiration, "selected expiration")
    spot = Decimal(_decimal(spot_price, "spot price", minimum=Decimal("0.0000000001")))
    lower, upper = spot * LOWER_STRIKE_MULTIPLIER, spot * UPPER_STRIKE_MULTIPLIER
    result: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    symbols: set[str] = set()
    for index, raw in enumerate(catalog_rows):
        label = f"contract row {index}"
        row = _mapping(raw, label)
        expiration = _grid_catalog_expiration(
            row, underlying_symbol=underlying_symbol, label=label
        )
        dte = (date.fromisoformat(expiration) - session).days
        if not 1 <= dte <= ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS:
            raise _fail("contracts response contains an out-of-scope expiration")
        strike = _decimal(
            row.get("strike_price"),
            f"{label} strike",
            minimum=Decimal("0.0000000001"),
        )
        assert strike is not None
        if not lower <= Decimal(strike) <= upper:
            raise _fail("contracts response contains a strike outside the fixed scope")
        if expiration != selected:
            continue
        provider_id = _text(row.get("id"), f"{label} id", 256)
        symbol = _text(row.get("symbol"), f"{label} symbol", 128)
        option_type = _text(row.get("type"), f"{label} type", 16).casefold()
        if option_type not in {"call", "put"}:
            raise _fail(f"{label} type is invalid")
        multiplier = _integer(row.get("size"), f"{label} size")
        if multiplier is None or multiplier < 1 or provider_id in ids or symbol in symbols:
            raise _fail("contracts response duplicates or invalidates a contract identity")
        ids.add(provider_id)
        symbols.add(symbol)
        kind, deliverable_json = _grid_deliverable(
            row, underlying_symbol=underlying_symbol, multiplier=multiplier
        )
        result.append(
            {
                "provider": ALPACA_PROVIDER,
                "provider_contract_id": provider_id,
                "contract_symbol": symbol,
                "expiration_date": expiration,
                "strike_price": strike,
                "option_type": option_type,
                "deliverable_kind": kind,
                "contract_multiplier": multiplier,
                "nonstandard_deliverable_json": deliverable_json,
                "contract_status": "active",
                "available_at": completed_at,
                "available_precision": "datetime",
                "_open_interest": row.get("open_interest"),
                "_open_interest_date": row.get("open_interest_date"),
                "_close_price": row.get("close_price"),
                "_close_price_date": row.get("close_price_date"),
            }
        )
    if not result:
        raise _fail("exact-expiration contracts response is empty")
    return tuple(sorted(result, key=lambda row: str(row["provider_contract_id"])))


def _grid_chain_snapshots(bodies: Sequence[bytes]) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for body in bodies:
        snapshots, _ = _grid_chain_page(body)
        for symbol, snapshot in snapshots.items():
            if symbol in result:
                raise _fail("option chain response duplicates a contract snapshot")
            result[symbol] = snapshot
    if len(result) > MAX_ETF_OPTIONS_CHAIN_ROWS:
        raise _fail("option chain exceeds the fixed aggregate row bound")
    return result


def _grid_response_digest(responses: Mapping[str, bytes]) -> tuple[str, int]:
    digest, total = hashlib.sha256(), 0
    for name in sorted(responses):
        body = responses[name]
        if not isinstance(body, bytes) or not body:
            raise _fail("response evidence is invalid")
        total += len(body)
        if total > MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES:
            raise ResourceLimitError("Alpaca ETF option capture exceeds the aggregate response bound")
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(2, "big"))
        digest.update(encoded)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest(), total


def _grid_semantic(
    scope: Mapping[str, Any],
    contracts: Sequence[Mapping[str, Any]],
    surface_rows: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
) -> str:
    def without_availability(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if key not in {"available_at", "available_precision"}
        }

    contract_keys = (
        "provider", "provider_contract_id", "contract_symbol", "expiration_date",
        "strike_price", "option_type", "deliverable_kind", "contract_multiplier",
        "nonstandard_deliverable_json", "contract_status",
    )
    header = {
        "collector": ALPACA_ETF_OPTIONS_COLLECTOR_ID,
        "normalization_version": ALPACA_ETF_OPTIONS_NORMALIZATION_VERSION,
        "scope": dict(scope),
    }
    canonical_inputs = {
        "underlying": dict(inputs["underlying"]),
        "underlying_quote": without_availability(inputs["underlying_quote"]),
        "rate_curve": without_availability(inputs["rate_curve"]),
        "dividend_set": without_availability(inputs["dividend_set"]),
        "expiry_inputs": [
            without_availability(row) for row in inputs["expiry_inputs"]
        ],
    }
    digest = hashlib.sha256()
    normalized_bytes = 0

    def add_frame(kind: str, value: object) -> None:
        nonlocal normalized_bytes
        label = kind.encode("ascii")
        encoded = dumps_strict(
            value, max_bytes=MAX_ETF_OPTIONS_NORMALIZED_BYTES
        ).encode("utf-8")
        frame_bytes = 2 + len(label) + 8 + len(encoded)
        normalized_bytes += frame_bytes
        if normalized_bytes > MAX_ETF_OPTIONS_NORMALIZED_BYTES:
            raise ResourceLimitError(
                "Alpaca ETF option capture exceeds the normalized semantic byte bound"
            )
        digest.update(len(label).to_bytes(2, "big"))
        digest.update(label)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)

    add_frame("header", header)
    for row in contracts:
        add_frame("contract", {key: row[key] for key in contract_keys})
    for row in surface_rows:
        add_frame(
            "surface",
            {
                "provider_contract_id": row["provider_contract_id"],
                "surface_state": row["surface_state"],
                "missing_reason": row["missing_reason"],
                "exclusion_reason": row["exclusion_reason"],
                "quote": dict(row["quote"]),
                "open_interest": without_availability(row["open_interest"]),
                "close_price": without_availability(row["close_price"]),
            },
        )
    add_frame("inputs", canonical_inputs)
    return digest.hexdigest()


def _parse_alpaca_etf_options_capture(
    *,
    calendar_body: bytes,
    underlying_body: bytes,
    underlying_snapshot: AlpacaUnderlyingSnapshot,
    catalog_rows: Sequence[Any],
    catalog_bodies: Sequence[bytes],
    chain_bodies: Sequence[bytes],
    underlying_symbol: str,
    selected_expiration: str,
    target_dtes: Sequence[int],
    session_date: str,
    requested_at: datetime,
    completed_at: datetime,
) -> ParsedAlpacaEtfOptionsCapture:
    session = _date(session_date, "session date")
    if underlying_symbol not in ALPACA_ETF_OPTIONS_UNIVERSE:
        raise _fail("underlying symbol is outside the fixed ETF universe")
    canonical_target_dtes = tuple(target_dtes)
    if (
        not canonical_target_dtes
        or any(
            isinstance(target, bool)
            or not isinstance(target, int)
            or target not in ALPACA_ETF_OPTIONS_DTE_TARGETS
            for target in canonical_target_dtes
        )
        or canonical_target_dtes != tuple(sorted(set(canonical_target_dtes)))
    ):
        raise _fail("selected target DTEs are not canonical")
    requested = _instant(requested_at, "request time")
    completed = _instant(completed_at, "completion time")
    if datetime.fromisoformat(completed.replace("Z", "+00:00")) < datetime.fromisoformat(
        requested.replace("Z", "+00:00")
    ):
        raise _fail("completion time precedes request time")
    if not is_alpaca_opra_trading_day(calendar_body, session_date=session):
        raise _fail("calendar response does not authorize this session")
    if not isinstance(underlying_snapshot, AlpacaUnderlyingSnapshot):
        raise _fail("underlying snapshot is invalid")
    underlying = underlying_snapshot
    _require_session_timestamp(
        underlying.quote["observed_at"],
        session_date=session,
        label="underlying observation time",
    )
    contracts = _grid_contracts(
        catalog_rows,
        session_date=session,
        selected_expiration=selected_expiration,
        spot_price=underlying.spot_price,
        completed_at=completed,
        underlying_symbol=underlying_symbol,
    )
    surfaces = _surfaces(
        contracts,
        snapshots=_grid_chain_snapshots(chain_bodies),
        session_date=session,
        completed_at=completed,
        preserve_dated_observations=True,
        enforce_completion_cutoff=True,
    )
    quote = dict(underlying.quote)
    for key in ("_bid_size", "_ask_size", "_last_size", "_trade_at"):
        quote.pop(key, None)
    scope = {
        "underlying_symbol": underlying_symbol,
        "session_date": session,
        "requested_feed": ALPACA_REQUESTED_FEED,
        "resolved_feed": ALPACA_RESOLVED_FEED,
        "environment": ALPACA_ENVIRONMENT,
        "deliverable_policy": "standard_only",
        "completeness": "complete",
        "target_dtes": list(canonical_target_dtes),
        "catalog_search_horizon_days": ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS,
        "positive_minimum_dte": 1,
        "lower_strike_multiplier": "0.8",
        "upper_strike_multiplier": "1.2",
        "selected_expiration": _date(selected_expiration, "selected expiration"),
        "spot_price": underlying.spot_price,
        "spot_method": underlying.spot_method,
    }
    inputs = {
        "underlying": {
            "state": "present",
            "missing_reason": None,
            "observed_at": quote["observed_at"],
            "observed_precision": "datetime",
            "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT,
        },
        "underlying_quote": quote,
        "rate_curve": {
            "state": "missing",
            "missing_reason": "not_collected_in_live_etf_grid",
            "curve_date": None,
            "source_name": None,
            "available_at": completed,
            "available_precision": "datetime",
            "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT,
            "points": (),
        },
        "dividend_set": {
            "state": "missing",
            "missing_reason": "not_collected_in_live_etf_grid",
            "source_name": None,
            "available_at": completed,
            "available_precision": "datetime",
            "feed": ALPACA_RESOLVED_FEED,
            "environment": ALPACA_ENVIRONMENT,
            "cashflows": (),
        },
        "expiry_inputs": (
            {
                "expiration_date": scope["selected_expiration"],
                "state": "missing",
                "missing_reason": "synchronized_model_inputs_not_collected_in_live_etf_grid",
                "spot_price": None,
                "risk_free_rate": None,
                "dividend_yield": None,
                "forward_price": None,
                "available_at": completed,
                "available_precision": "datetime",
                "feed": ALPACA_RESOLVED_FEED,
                "environment": ALPACA_ENVIRONMENT,
            },
        ),
    }
    capture = {
        "requested_feed": ALPACA_REQUESTED_FEED,
        "resolved_feed": ALPACA_RESOLVED_FEED,
        "environment": ALPACA_ENVIRONMENT,
        "requested_at": requested,
        "requested_precision": "datetime",
        "completed_at": completed,
        "completed_precision": "datetime",
        "available_at": completed,
        "available_precision": "datetime",
        "completeness": "complete",
        "deliverable_policy": "standard_only",
    }
    responses: dict[str, bytes] = {
        "calendar": calendar_body,
        "underlying": underlying_body,
    }
    responses.update(
        {f"catalog_{index:02d}": body for index, body in enumerate(catalog_bodies, start=1)}
    )
    responses.update(
        {f"chain_{index:02d}": body for index, body in enumerate(chain_bodies, start=1)}
    )
    response_sha256, response_byte_count = _grid_response_digest(responses)
    normalized_semantic = _grid_semantic(scope, contracts, surfaces, inputs)
    semantic_identity = hashlib.sha256(
        dumps_strict(
            {
                "normalized_semantic": normalized_semantic,
                "raw_response_sha256": response_sha256,
            }
        ).encode("utf-8")
    ).hexdigest()
    return ParsedAlpacaEtfOptionsCapture(
        capture=capture,
        contracts=contracts,
        surface_rows=surfaces,
        inputs=inputs,
        scope=scope,
        captured_at=TemporalValue.parse(completed, pointer="/completed_at"),
        semantic_identity=semantic_identity,
        response_sha256=response_sha256,
        response_byte_count=response_byte_count,
        raw_responses=tuple(responses.items()),
    )


def _fetch_etf_grid_catalog(
    budget: _EtfGridBudget,
    transport: AlpacaHttpTransport,
    *,
    headers: Mapping[str, str],
    underlying_symbol: str,
    query: Mapping[str, str],
) -> tuple[tuple[bytes, ...], tuple[Any, ...]]:
    if query.get("underlying_symbols") != underlying_symbol:
        raise ValidationError("Alpaca ETF option catalog scope is invalid")
    bodies: list[bytes] = []
    rows: list[Any] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    for _ in range(MAX_ETF_OPTIONS_CONTRACT_PAGES):
        current_query = dict(query)
        if token is not None:
            current_query["page_token"] = token
        body = budget.request(
            transport,
            host=_ALPACA_PAPER_HOST,
            path="/v2/options/contracts",
            query=current_query,
            headers=headers,
        )
        page_rows, next_token = _grid_catalog_page(body)
        rows.extend(page_rows)
        if len(rows) > MAX_ETF_OPTIONS_CONTRACT_ROWS:
            raise ResourceLimitError("Alpaca ETF option catalog exceeds the row bound")
        bodies.append(body)
        if next_token is None:
            return tuple(bodies), tuple(rows)
        if next_token in seen_tokens:
            raise _fail("contracts response repeats a pagination marker")
        seen_tokens.add(next_token)
        token = next_token
    raise ResourceLimitError("Alpaca ETF option catalog exceeds the page bound")


def _fetch_etf_grid_chain(
    budget: _EtfGridBudget,
    transport: AlpacaHttpTransport,
    *,
    headers: Mapping[str, str],
    underlying_symbol: str,
    query: Mapping[str, str],
) -> tuple[bytes, ...]:
    if underlying_symbol not in ALPACA_ETF_OPTIONS_UNIVERSE:
        raise ValidationError("Alpaca ETF option chain scope is invalid")
    bodies: list[bytes] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    path = f"/v1beta1/options/snapshots/{underlying_symbol}"
    for _ in range(MAX_ETF_OPTIONS_CHAIN_PAGES):
        current_query = dict(query)
        if token is not None:
            current_query["page_token"] = token
        body = budget.request(
            transport,
            host=_ALPACA_DATA_HOST,
            path=path,
            query=current_query,
            headers=headers,
        )
        _, next_token = _grid_chain_page(body)
        bodies.append(body)
        if next_token is None:
            return tuple(bodies)
        if next_token in seen_tokens:
            raise _fail("option chain response repeats a pagination marker")
        seen_tokens.add(next_token)
        token = next_token
    raise ResourceLimitError("Alpaca ETF option chain exceeds the page bound")


def _fixed_etf_grid_scope_root(project_root: Path, stores: StoreMap) -> Path:
    if not isinstance(project_root, Path) or not isinstance(stores, StoreMap):
        raise ValidationError("Alpaca ETF option grid target binding is invalid")
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError("Alpaca ETF option grid target binding is invalid") from exc
    if root != project_root or project_root.is_symlink() or not root.is_dir():
        raise ValidationError("Alpaca ETF option grid target binding is invalid")
    if stores.path(StoreRole.MARKET) != root / "data" / "market.sqlite":
        raise ValidationError("Alpaca ETF option grid target binding is invalid")
    return root


def _stage10_etf_identities(store_map: StoreMap) -> Mapping[str, Mapping[str, Any]]:
    """Preflight every fixed ETF identity before any provider request occurs."""

    result: dict[str, Mapping[str, Any]] = {}
    with read_connection(store_map, StoreRole.MARKET) as connection:
        for symbol in ALPACA_ETF_OPTIONS_UNIVERSE:
            rows = list(
                connection.execute(
                    """
                    SELECT instrument_id, provider, provider_symbol, asset_type, display_name,
                           exchange_code, captured_at
                    FROM stage10_instruments
                    WHERE provider='fmp' AND provider_symbol=? AND asset_type='etf'
                    """,
                    (symbol,),
                )
            )
            if len(rows) != 1:
                raise ValidationError(
                    "Canonical Stage10 ETF identity is unavailable or ambiguous"
                )
            result[symbol] = {key: rows[0][key] for key in rows[0].keys()}
    return result


def _ensure_etf_grid_bridge(
    connection: sqlite3.Connection,
    *,
    stage10_etf: Mapping[str, Any],
    underlying_symbol: str,
    run_id: str,
) -> tuple[str, int]:
    instrument_id = _text(stage10_etf.get("instrument_id"), "Stage10 ETF instrument id", 256)
    existing = connection.execute(
        "SELECT instrument_id, asset_type, canonical_symbol FROM instruments WHERE instrument_id=?",
        (instrument_id,),
    ).fetchone()
    if existing is not None:
        if (
            existing["asset_type"] != "etf"
            or existing["canonical_symbol"] not in {None, underlying_symbol}
        ):
            raise ConflictError("Option-underlying bridge conflicts with the Stage10 ETF")
        return instrument_id, 0
    connection.execute(
        """
        INSERT INTO instruments (
            instrument_id, asset_type, created_run_id, canonical_symbol, display_name,
            exchange, currency, country, first_seen_at, last_seen_at, active
        ) VALUES (?, 'etf', ?, ?, ?, ?, 'USD', 'US', ?, ?, 1)
        """,
        (
            instrument_id,
            run_id,
            underlying_symbol,
            stage10_etf.get("display_name"),
            stage10_etf.get("exchange_code"),
            stage10_etf.get("captured_at"),
            stage10_etf.get("captured_at"),
        ),
    )
    return instrument_id, 1


class AlpacaEtfOptionsPublisher:
    """Publish one coherent, single-expiry ETF-grid capture."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        stage10_etfs: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Alpaca ETF option grid publisher dependencies are invalid")
        if (
            not isinstance(stage10_etfs, Mapping)
            or set(stage10_etfs) != set(ALPACA_ETF_OPTIONS_UNIVERSE)
            or not all(isinstance(value, Mapping) for value in stage10_etfs.values())
        ):
            raise ValidationError("Alpaca ETF option grid identities are invalid")
        datasets = {item.id for item in registry.datasets_for(StoreRole.MARKET.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise ValidationError("Alpaca ETF option grid datasets are not registered")
        self._store_map = store_map
        self._stage10_etfs = dict(stage10_etfs)
        self._coordinator = IngestionCoordinator(
            store_map, code_version="alpaca_etf_options.1.1.0"
        )

    def publish(
        self,
        parsed: ParsedAlpacaEtfOptionsCapture,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        if not isinstance(parsed, ParsedAlpacaEtfOptionsCapture):
            raise ValidationError("Alpaca ETF option grid capture is invalid")
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self._store_map, StoreRole.MARKET)
        symbol = parsed.scope.get("underlying_symbol")
        if symbol not in ALPACA_ETF_OPTIONS_UNIVERSE:
            raise ValidationError("Alpaca ETF option grid capture is invalid")
        stage10_etf = self._stage10_etfs[symbol]
        run_id = stable_id(
            "alpaca_etf_option_surface_grid_run",
            ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            parsed.semantic_identity,
        )

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise ValidationError(
                    "Alpaca ETF option grid coordinator supplied an unexpected run identity"
                )
            underlying_id, written = _ensure_etf_grid_bridge(
                connection,
                stage10_etf=stage10_etf,
                underlying_symbol=symbol,
                run_id=active_run_id,
            )
            artifact_id = stable_id(
                "alpaca_etf_option_surface_grid_artifact",
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                parsed.response_sha256,
                parsed.semantic_identity,
            )
            snapshot_id = stable_id(
                "alpaca_etf_option_surface_grid_snapshot",
                ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                parsed.semantic_identity,
            )
            capture_id = stable_id(
                "option_capture",
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                parsed.semantic_identity,
            )
            contract_ids: dict[str, str] = {}
            for contract in parsed.contracts:
                contract_id, inserted = _ensure_contract(
                    connection,
                    contract=contract,
                    underlying_instrument_id=underlying_id,
                    run_id=active_run_id,
                )
                contract_ids[str(contract["provider_contract_id"])] = contract_id
                written += inserted
            connection.execute(
                """
                INSERT INTO option_surface_captures (
                    capture_id, semantic_identity, underlying_instrument_id,
                    requested_feed, resolved_feed, environment, request_scope_json,
                    requested_at, requested_precision, completed_at, completed_precision,
                    available_at, available_precision, completeness, deliverable_policy,
                    artifact_id, source_snapshot_id, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id, parsed.semantic_identity, underlying_id,
                    parsed.capture["requested_feed"], parsed.capture["resolved_feed"],
                    parsed.capture["environment"], dumps_strict(dict(parsed.scope)),
                    parsed.capture["requested_at"], parsed.capture["requested_precision"],
                    parsed.capture["completed_at"], parsed.capture["completed_precision"],
                    parsed.capture["available_at"], parsed.capture["available_precision"],
                    parsed.capture["completeness"], parsed.capture["deliverable_policy"],
                    artifact_id, snapshot_id, active_run_id,
                ),
            )
            written += 1
            written += _write_raw_option_responses(
                connection,
                parsed=parsed,
                capture_id=capture_id,
            )
            input_count, _ = _write_capture_inputs(
                connection,
                parsed=parsed,
                capture_id=capture_id,
                underlying_instrument_id=underlying_id,
            )
            written += input_count
            written += _write_surface_rows(
                connection,
                parsed=parsed,
                capture_id=capture_id,
                contract_ids=contract_ids,
            )
            artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                content_sha256=parsed.response_sha256,
                media_type="application/json",
                byte_count=parsed.response_byte_count,
                source_reference="alpaca/etf-option-surface-grid-v1.json",
                request_scope=dict(parsed.scope),
                captured_at=parsed.captured_at.raw or parsed.capture["completed_at"],
                captured_precision="datetime",
                normalization_version=ALPACA_ETF_OPTIONS_NORMALIZATION_VERSION,
            )
            warnings = (
                "indicative_feed_not_executable",
                "rate_inputs_not_collected",
                "dividend_inputs_not_collected",
                "synchronized_model_inputs_not_collected",
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=snapshot_id,
                    dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                    semantic_identity=parsed.semantic_identity,
                    scope=dict(parsed.scope),
                    completeness="complete",
                    row_count=parsed.fetched_count,
                    captured_at=parsed.captured_at.raw or parsed.capture["completed_at"],
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(artifact_id,),
                    warnings=warnings,
                ),
                quality_results=(
                    QualityWrite(
                        quality_result_id=stable_id(
                            "alpaca_etf_option_surface_grid_quality",
                            active_run_id,
                            ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                        ),
                        dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                        rule_id="single_capture_cohort",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=snapshot_id,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        observed={
                            "contracts": len(parsed.contracts),
                            "surface_rows": len(parsed.surface_rows),
                            "selected_expiration": parsed.scope["selected_expiration"],
                            "target_dtes": parsed.scope["target_dtes"],
                            "written_count": written,
                        },
                    ),
                ),
                warnings=warnings,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            output_dataset_ids=(
                ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID,
                ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                ALPACA_OPTIONS_RAW_EVIDENCE_DATASET_ID,
                ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
            ),
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=ALPACA_ETF_OPTIONS_COLLECTOR_ID,
            scope={
                "request_scope": dict(parsed.scope),
                "resolved_feed": ALPACA_RESOLVED_FEED,
                "environment": ALPACA_ENVIRONMENT,
            },
            started_at=parsed.capture["requested_at"],
            completed_at=parsed.capture["completed_at"],
            fetched_count=parsed.fetched_count,
            writer=writer,
            held_locks=held_locks,
        )


@dataclass(frozen=True, slots=True)
class AlpacaEtfOptionsRunReport:
    outcome: str
    session_date: str
    requests_issued: int
    captures: int
    contracts: int
    surface_rows: int
    written_count: int
    completed_underlyings: tuple[str, ...]
    failed_underlyings: tuple[str, ...]

    def mapping(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "session_date": self.session_date,
            "requests_issued": self.requests_issued,
            "captures": self.captures,
            "contracts": self.contracts,
            "surface_rows": self.surface_rows,
            "written_count": self.written_count,
            "completed_underlyings": list(self.completed_underlyings),
            "failed_underlyings": list(self.failed_underlyings),
        }


def run_alpaca_etf_option_surface_grid(
    *,
    project_root: Path,
    stores: StoreMap,
    registry: Registry,
    transport: AlpacaHttpTransport,
    captured_at: str,
    api_key: str,
    api_secret: str,
) -> AlpacaEtfOptionsRunReport:
    """Fetch the fixed 15-ETF, 10-DTE grid through bounded one-attempt calls."""

    budget = _EtfGridBudget(deadline=monotonic() + MAX_ETF_OPTIONS_RUN_SECONDS)
    _fixed_etf_grid_scope_root(project_root, stores)
    if not isinstance(registry, Registry) or not isinstance(transport, AlpacaHttpTransport):
        raise ValidationError("Alpaca ETF option grid runner dependencies are invalid")
    captured = _capture_time(captured_at)
    session = captured.astimezone(ZoneInfo("America/New_York")).date()
    session_text = session.isoformat()
    key, secret = _credential(api_key), _credential(api_secret)
    stage10_etfs = _stage10_etf_identities(stores)
    budget.ensure_deadline()
    headers = {
        "Accept": "application/json",
        "User-Agent": "QuantDataInfra/1.0",
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    calendar_body = budget.request(
        transport,
        host=_ALPACA_PAPER_HOST,
        path="/v3/calendar/OPRA",
        query={"start": session_text, "end": session_text},
        headers=headers,
    )
    if not is_alpaca_opra_trading_day(calendar_body, session_date=session_text):
        return AlpacaEtfOptionsRunReport(
            outcome="skipped",
            session_date=session_text,
            requests_issued=budget.requests_issued,
            captures=0,
            contracts=0,
            surface_rows=0,
            written_count=0,
            completed_underlyings=(),
            failed_underlyings=(),
        )
    underlying_body = budget.request(
        transport,
        host=_ALPACA_DATA_HOST,
        path="/v2/stocks/snapshots",
        query={"symbols": ",".join(ALPACA_ETF_OPTIONS_UNIVERSE), "feed": "iex"},
        headers=headers,
    )
    underlying_completed_at = datetime.now(timezone.utc)
    underlying_snapshots = _grid_underlying_snapshot_payload(underlying_body)
    publisher = AlpacaEtfOptionsPublisher(
        stores, registry, stage10_etfs=stage10_etfs
    )
    completed_underlyings: list[str] = []
    failed_underlyings: list[str] = []
    capture_count = contract_count = surface_count = written_count = 0
    for index, symbol in enumerate(ALPACA_ETF_OPTIONS_UNIVERSE):
        try:
            underlying = _grid_underlying_snapshot(
                underlying_snapshots,
                symbol=symbol,
                completed_at=underlying_completed_at,
            )
            _require_session_timestamp(
                underlying.quote["observed_at"],
                session_date=session_text,
                label=f"underlying {symbol} observation time",
            )
            lower = format(
                Decimal(underlying.spot_price) * LOWER_STRIKE_MULTIPLIER, "f"
            )
            upper = format(
                Decimal(underlying.spot_price) * UPPER_STRIKE_MULTIPLIER, "f"
            )
            catalog_bodies, catalog_rows = _fetch_etf_grid_catalog(
                budget,
                transport,
                headers=headers,
                underlying_symbol=symbol,
                query={
                    "underlying_symbols": symbol,
                    "expiration_date_gte": date.fromordinal(
                        session.toordinal() + 1
                    ).isoformat(),
                    "expiration_date_lte": date.fromordinal(
                        session.toordinal() + ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS
                    ).isoformat(),
                    "strike_price_gte": lower,
                    "strike_price_lte": upper,
                    "status": "active",
                    "limit": str(MAX_CONTRACTS),
                    "show_deliverables": "true",
                },
            )
            expiry_groups = select_alpaca_etf_expirations(
                catalog_rows,
                session_date=session_text,
                spot_price=underlying.spot_price,
                underlying_symbol=symbol,
            )
            for expiration, target_dtes in expiry_groups:
                chain_bodies = _fetch_etf_grid_chain(
                    budget,
                    transport,
                    headers=headers,
                    underlying_symbol=symbol,
                    query={
                        "feed": ALPACA_REQUESTED_FEED,
                        "expiration_date": expiration,
                        "strike_price_gte": lower,
                        "strike_price_lte": upper,
                        "limit": str(MAX_CHAIN_SNAPSHOTS),
                    },
                )
                parsed = _parse_alpaca_etf_options_capture(
                    calendar_body=calendar_body,
                    underlying_body=underlying_body,
                    underlying_snapshot=underlying,
                    catalog_rows=catalog_rows,
                    catalog_bodies=catalog_bodies,
                    chain_bodies=chain_bodies,
                    underlying_symbol=symbol,
                    selected_expiration=expiration,
                    target_dtes=target_dtes,
                    session_date=session_text,
                    requested_at=captured,
                    completed_at=datetime.now(timezone.utc),
                )
                budget.ensure_deadline()
                receipt = publisher.publish(parsed)
                if receipt.outcome not in {"succeeded", "unchanged"}:
                    raise ConflictError(
                        "Alpaca ETF option grid publisher outcome is invalid"
                    )
                capture_count += 1
                contract_count += len(parsed.contracts)
                surface_count += len(parsed.surface_rows)
                written_count += receipt.written_count
                budget.ensure_deadline()
            completed_underlyings.append(symbol)
        except (ConflictError, StoreUnavailableError):
            failed_underlyings.append(symbol)
            failed_underlyings.extend(ALPACA_ETF_OPTIONS_UNIVERSE[index + 1 :])
            break
        except (ResourceLimitError, ValidationError):
            failed_underlyings.append(symbol)
            if (
                budget.requests_issued >= MAX_ETF_OPTIONS_REQUESTS
                or budget.terminal_response_limit
                or budget.response_byte_count >= MAX_ETF_OPTIONS_TOTAL_RESPONSE_BYTES
                or monotonic() >= budget.deadline
            ):
                failed_underlyings.extend(ALPACA_ETF_OPTIONS_UNIVERSE[index + 1 :])
                break
    outcome = (
        "succeeded"
        if (
            len(completed_underlyings) == len(ALPACA_ETF_OPTIONS_UNIVERSE)
            and monotonic() < budget.deadline
        )
        else "partial"
    )
    return AlpacaEtfOptionsRunReport(
        outcome=outcome,
        session_date=session_text,
        requests_issued=budget.requests_issued,
        captures=capture_count,
        contracts=contract_count,
        surface_rows=surface_count,
        written_count=written_count,
        completed_underlyings=tuple(completed_underlyings),
        failed_underlyings=tuple(failed_underlyings),
    )


__all__ = (
    "ALPACA_ENVIRONMENT",
    "ALPACA_ETF_OPTIONS_COLLECTOR_ID",
    "ALPACA_ETF_OPTIONS_DTE_TARGETS",
    "ALPACA_ETF_OPTIONS_HANDLER",
    "ALPACA_ETF_OPTIONS_NORMALIZATION_VERSION",
    "ALPACA_ETF_OPTIONS_SEARCH_HORIZON_DAYS",
    "ALPACA_ETF_OPTIONS_UNIVERSE",
    "ALPACA_NORMALIZATION_VERSION",
    "ALPACA_PROVIDER",
    "ALPACA_REQUESTED_FEED",
    "ALPACA_RESOLVED_FEED",
    "ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID",
    "ALPACA_SPY_OPTIONS_COLLECTOR_ID",
    "ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID",
    "ALPACA_SPY_OPTIONS_HANDLER",
    "ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID",
    "AlpacaEtfOptionsPublisher",
    "AlpacaEtfOptionsRunReport",
    "AlpacaHttpResponse",
    "AlpacaHttpTransport",
    "AlpacaSpyOptionsPublisher",
    "AlpacaSpyOptionsRunReport",
    "AlpacaUnderlyingSnapshot",
    "MAX_CHAIN_SNAPSHOTS",
    "MAX_DTE",
    "MAX_ETF_OPTIONS_CHAIN_PAGES",
    "MAX_ETF_OPTIONS_CONTRACT_PAGES",
    "MAX_ETF_OPTIONS_NORMALIZED_BYTES",
    "MAX_ETF_OPTIONS_REQUESTS",
    "MAX_RESPONSE_BYTES",
    "MAX_TOTAL_RESPONSE_BYTES",
    "MIN_DTE",
    "ParsedAlpacaEtfOptionsCapture",
    "ParsedAlpacaSpyOptionsCapture",
    "SPY_SYMBOL",
    "TARGET_DTE",
    "is_alpaca_opra_trading_day",
    "parse_alpaca_spy_options_capture",
    "parse_alpaca_underlying_snapshot",
    "run_alpaca_etf_option_surface_grid",
    "run_alpaca_spy_option_surface",
    "select_alpaca_etf_expirations",
    "select_alpaca_expiration",
    "StdlibAlpacaHttpTransport",
)
