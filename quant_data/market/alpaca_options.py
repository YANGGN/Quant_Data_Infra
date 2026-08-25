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
ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID: Final = "fixture.market.option_capture_evidence"
ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID: Final = "fixture.market.options"
ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID: Final = "fixture.market.instruments"
ALPACA_PROVIDER: Final = "alpaca"
ALPACA_REQUESTED_FEED: Final = "indicative"
ALPACA_RESOLVED_FEED: Final = "alpaca_indicative"
ALPACA_ENVIRONMENT: Final = "paper"
ALPACA_NORMALIZATION_VERSION: Final = "alpaca_spy_option_surface.v2"
SPY_SYMBOL: Final = "SPY"
TARGET_DTE: Final = 30
MIN_DTE: Final = 23
MAX_DTE: Final = 37
LOWER_STRIKE_MULTIPLIER: Final = Decimal("0.80")
UPPER_STRIKE_MULTIPLIER: Final = Decimal("1.20")
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_CONTRACTS: Final = 10_000
MAX_CHAIN_SNAPSHOTS: Final = 1_000
_SQLITE_MAX_INTEGER: Final = 9_223_372_036_854_775_807
_NANOSECOND_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6})\d{1,3}(Z|[+-]\d{2}:\d{2})$"
)
_REQUIRED_DATASETS: Final = frozenset(
    {
        ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID,
        ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
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
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"{label} is invalid") from exc
    if not parsed.is_finite() or (minimum is not None and parsed < minimum):
        raise _fail(f"{label} is invalid")
    normalized = parsed.normalize()
    return "0" if normalized.is_zero() else format(normalized, "f")


def _integer(value: object, label: str, *, nullable: bool = False) -> int | None:
    if value is None:
        if nullable:
            return None
        raise _fail(f"{label} is invalid")
    if isinstance(value, str):
        if not value or not value.isascii() or not value.isdecimal():
            raise _fail(f"{label} is invalid")
        value = int(value)
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
    found = False
    for index, item in enumerate(rows):
        if _date(_mapping(item, f"calendar row {index}").get("date"), f"calendar row {index} date") == requested:
            found = True
    return found


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


def _underlying_quote(snapshot: Mapping[str, Any], completed_at: str) -> tuple[Mapping[str, Any], str, str]:
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


def _deliverable(row: Mapping[str, Any], multiplier: int) -> tuple[str, str | None]:
    root = row.get("root_symbol", SPY_SYMBOL)
    root_symbol = SPY_SYMBOL if root is None else _text(root, "contract root symbol", 32)
    deliverables = row.get("deliverables", [])
    if deliverables is None:
        deliverables = []
    if not isinstance(deliverables, list):
        raise _fail("contract deliverables are invalid")
    if root_symbol == SPY_SYMBOL and multiplier == 100 and not deliverables:
        return "standard", None
    return "nonstandard", dumps_strict(
        {"deliverables": deliverables, "root_symbol": root_symbol, "size": multiplier}
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


def _surface_quote(snapshot: Mapping[str, Any]) -> Mapping[str, Any] | None:
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
) -> Mapping[str, Any]:
    value, source_date = contract[value_key], contract[f"{value_key}_date"]
    if value is None:
        return _missing(date_key, session_date, completed_at, "source_not_provided")
    if source_date is None:
        return _missing(date_key, session_date, completed_at, "source_date_not_provided")
    observed_date = _date(source_date, f"contract {date_key}")
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
            raw = snapshots.get(symbol)
            quote = None if raw is None else _surface_quote(_mapping(raw, f"option snapshot {symbol}"))
            if quote is None:
                state, missing_reason, exclusion_reason, quote = (
                    "missing", "source_quote_not_returned", None, _empty_quote()
                )
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
                open_interest = _missing(
                    "as_of_date", session_date, completed_at, "surface_quote_not_current_session"
                )
                close_price = _missing(
                    "trade_date", session_date, completed_at, "surface_quote_not_current_session"
                )
            else:
                state, missing_reason, exclusion_reason = "present", None, None
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
    response_sha256, response_byte_count = _response_digest(
        {
            "calendar": calendar_body,
            "contracts": contracts_body,
            "option_chain": snapshots_body,
            "underlying": underlying_body,
        }
    )
    return ParsedAlpacaSpyOptionsCapture(
        capture=capture,
        contracts=contracts,
        surface_rows=surfaces,
        inputs=inputs,
        scope=scope,
        captured_at=TemporalValue.parse(completed, pointer="/completed_at"),
        semantic_identity=_semantic(scope, contracts, surfaces, inputs),
        response_sha256=response_sha256,
        response_byte_count=response_byte_count,
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
            _REQUEST_SHAPES.get((host, path)) != frozenset(query)
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
            or max_bytes != MAX_RESPONSE_BYTES
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


class AlpacaSpyOptionsPublisher:
    """Publish one fully prepared live capture through the existing options tables."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Alpaca SPY options publisher dependencies are invalid")
        datasets = {item.id for item in registry.datasets_for(StoreRole.MARKET.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise ValidationError("Alpaca SPY options datasets are not registered")
        self._store_map = store_map
        self._coordinator = IngestionCoordinator(store_map, code_version="alpaca_spy_options.1.1.0")

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


__all__ = (
    "ALPACA_ENVIRONMENT",
    "ALPACA_NORMALIZATION_VERSION",
    "ALPACA_PROVIDER",
    "ALPACA_REQUESTED_FEED",
    "ALPACA_RESOLVED_FEED",
    "ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID",
    "ALPACA_SPY_OPTIONS_COLLECTOR_ID",
    "ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID",
    "ALPACA_SPY_OPTIONS_HANDLER",
    "ALPACA_SPY_OPTIONS_IDENTITY_DATASET_ID",
    "AlpacaSpyOptionsPublisher",
    "AlpacaHttpResponse",
    "AlpacaHttpTransport",
    "AlpacaSpyOptionsRunReport",
    "run_alpaca_spy_option_surface",
    "AlpacaUnderlyingSnapshot",
    "MAX_DTE",
    "MAX_CHAIN_SNAPSHOTS",
    "MAX_RESPONSE_BYTES",
    "MAX_TOTAL_RESPONSE_BYTES",
    "MIN_DTE",
    "ParsedAlpacaSpyOptionsCapture",
    "SPY_SYMBOL",
    "TARGET_DTE",
    "is_alpaca_opra_trading_day",
    "parse_alpaca_spy_options_capture",
    "parse_alpaca_underlying_snapshot",
    "select_alpaca_expiration",
    "StdlibAlpacaHttpTransport",
)
