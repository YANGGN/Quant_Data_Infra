"""Strict FMP capture primitives for the additive Stage 10 history extension.

This module deliberately has no store, roster, journal, or live-transport
binding responsibility.  It freezes the eight approved inclusive date
windows and validates one injected FMP response at a time.  The eventual
runner owns roster membership, durable no-retry journalling, pacing, and
publication.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final, Mapping

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from .fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_MAX_PRICE_ROWS,
    FMP_STAGE10_PRICE_PATH,
    FMP_STAGE10_TIMEOUT_SECONDS,
    FmpStage10PriceRow,
    FmpStage10Transport,
)


_HISTORY_WINDOW_SPECS: Final[tuple[tuple[str, str, str], ...]] = (
    ("1990-1994", "1990-01-01", "1994-12-31"),
    ("1995-1999", "1995-01-01", "1999-12-31"),
    ("2000-2004", "2000-01-01", "2004-12-31"),
    ("2005-2009", "2005-01-01", "2009-12-31"),
    ("2010-2014", "2010-01-01", "2014-12-31"),
    ("2015-2019", "2015-01-01", "2019-12-31"),
    ("2020-2024", "2020-01-01", "2024-12-31"),
    ("2025-2026", "2025-01-01", "2026-08-12"),
)
_HISTORY_WINDOW_SPEC_SET: Final[frozenset[tuple[str, str, str]]] = frozenset(
    _HISTORY_WINDOW_SPECS
)
_SYMBOL = re.compile(r"^[A-Z0-9^.-]{1,32}$")
_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_RESPONSE_KEYS: Final[frozenset[str]] = frozenset(
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
_MAX_SQLITE_INTEGER: Final[int] = 9_223_372_036_854_775_807


def _strict_iso_date(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"{field_name} must be an ISO date")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValidationError(f"FMP {field_name} must be a finite JSON number")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(
            f"FMP {field_name} must be a finite JSON number"
        ) from exc
    if not result.is_finite():
        raise ValidationError(f"FMP {field_name} must be finite")
    return result


def _volume(value: object) -> int:
    parsed = _decimal(value, "volume")
    if parsed != parsed.to_integral_value():
        raise ValidationError("FMP volume must be an integer")
    result = int(parsed)
    if result < 0 or result > _MAX_SQLITE_INTEGER:
        raise ValidationError("FMP volume is outside the supported range")
    return result


@dataclass(frozen=True, slots=True)
class Stage10HistoryWindow:
    """One exact, inclusive, reviewed Stage 10 historical request interval."""

    window_id: str
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        if (self.window_id, self.start_date, self.end_date) not in _HISTORY_WINDOW_SPEC_SET:
            raise ValidationError("Stage 10 history window is outside the approved plan")
        _strict_iso_date(self.start_date, "Stage 10 history start_date")
        _strict_iso_date(self.end_date, "Stage 10 history end_date")
        if self.start_date > self.end_date:
            raise ValidationError("Stage 10 history window is invalid")

    def query(self, symbol: str) -> dict[str, str]:
        """Return the complete, exact FMP query for ``symbol`` and this window."""

        normalized = _validated_symbol(symbol)
        return {
            "symbol": normalized,
            "from": self.start_date,
            "to": self.end_date,
        }


STAGE10_HISTORY_WINDOWS: Final[tuple[Stage10HistoryWindow, ...]] = tuple(
    Stage10HistoryWindow(*spec) for spec in _HISTORY_WINDOW_SPECS
)


def _validated_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ValidationError("Stage 10 history symbol is invalid")
    return symbol


@dataclass(frozen=True, slots=True)
class PreparedFmpStage10WindowCapture:
    """A credential-free immutable description of one reviewed request."""

    symbol: str
    window: Stage10HistoryWindow

    def __post_init__(self) -> None:
        _validated_symbol(self.symbol)
        if not isinstance(self.window, Stage10HistoryWindow):
            raise ValidationError("Stage 10 history capture requires an approved window")
        if self.window not in STAGE10_HISTORY_WINDOWS:
            raise ValidationError("Stage 10 history capture requires an approved window")

    @property
    def query(self) -> dict[str, str]:
        return self.window.query(self.symbol)

    def request_scope(self) -> dict[str, object]:
        """The non-secret provider scope used by the journal and evidence layer."""

        return {
            "provider": "fmp",
            "endpoint_path": FMP_STAGE10_PRICE_PATH,
            "symbol": self.symbol,
            "window_id": self.window.window_id,
            "start_date": self.window.start_date,
            "end_date": self.window.end_date,
        }


@dataclass(frozen=True, slots=True)
class FmpStage10WindowCapture:
    """One complete FMP response plus its strict normalized daily-price rows."""

    prepared: PreparedFmpStage10WindowCapture
    response: CapturedFmpStage10Response
    rows: tuple[FmpStage10PriceRow, ...]
    disposition: str
    response_sha256: str
    semantic_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.prepared, PreparedFmpStage10WindowCapture):
            raise ValidationError("Stage 10 history capture has an invalid request")
        if not isinstance(self.response, CapturedFmpStage10Response):
            raise ValidationError("Stage 10 history capture has an invalid response")
        if not isinstance(self.rows, tuple) or any(
            not isinstance(row, FmpStage10PriceRow) for row in self.rows
        ):
            raise ValidationError("Stage 10 history capture has invalid price rows")
        expected = "complete_empty" if not self.rows else "complete"
        if self.disposition != expected:
            raise ValidationError("Stage 10 history capture disposition is invalid")
        if (
            not isinstance(self.response_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.response_sha256) is None
            or self.response_sha256 != hashlib.sha256(self.response.body).hexdigest()
        ):
            raise ValidationError("Stage 10 history capture response digest is invalid")
        prior: str | None = None
        for row in self.rows:
            if (
                row.symbol != self.prepared.symbol
                or not self.prepared.window.start_date
                <= row.trade_date
                <= self.prepared.window.end_date
                or (prior is not None and row.trade_date <= prior)
            ):
                raise ValidationError("Stage 10 history capture rows are invalid")
            prior = row.trade_date
        expected_semantic = _capture_semantic_sha256(
            self.prepared, self.rows, self.disposition
        )
        if (
            not isinstance(self.semantic_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.semantic_sha256) is None
            or self.semantic_sha256 != expected_semantic
        ):
            raise ValidationError("Stage 10 history capture semantic digest is invalid")

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def raw_bytes(self) -> bytes:
        return self.response.body

    @property
    def raw_bytes_sha256(self) -> str:
        return self.response_sha256


def _capture_semantic_sha256(
    prepared: PreparedFmpStage10WindowCapture,
    rows: tuple[FmpStage10PriceRow, ...],
    disposition: str,
) -> str:
    material = {
        "provider": "fmp",
        "endpoint_path": FMP_STAGE10_PRICE_PATH,
        "query": prepared.query,
        "disposition": disposition,
        "normalized_complete_batch": [row.semantic_mapping() for row in rows],
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def prepare_fmp_stage10_window_capture(
    symbol: str,
    window: Stage10HistoryWindow,
) -> PreparedFmpStage10WindowCapture:
    """Prepare, but do not issue, one exact frozen historical FMP request."""

    _validated_symbol(symbol)
    if not isinstance(window, Stage10HistoryWindow) or window not in STAGE10_HISTORY_WINDOWS:
        raise ValidationError("Stage 10 history capture requires an approved window")
    return PreparedFmpStage10WindowCapture(symbol=symbol, window=window)


def parse_fmp_stage10_window_response(
    prepared: PreparedFmpStage10WindowCapture,
    body: bytes,
) -> tuple[FmpStage10PriceRow, ...]:
    """Strictly normalize a complete response without filtering provider rows.

    An empty JSON list is a complete empty historical window.  Any row outside
    the requested interval is a provider/contract failure rather than a value
    to trim silently.
    """

    if not isinstance(prepared, PreparedFmpStage10WindowCapture):
        raise ValidationError("Stage 10 history parsing requires a prepared request")
    if not isinstance(body, bytes):
        raise ValidationError("FMP response body must be bytes")
    if len(body) > FMP_STAGE10_MAX_PRICE_BYTES:
        raise ResourceLimitError("FMP response exceeds the reviewed byte bound")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("FMP response must be strict UTF-8") from exc
    try:
        value = loads_strict(text, max_bytes=FMP_STAGE10_MAX_PRICE_BYTES)
    except ResourceLimitError:
        raise
    except ValidationError:
        raise ValidationError("FMP response is not strict bounded JSON") from None
    if not isinstance(value, list):
        raise ValidationError("FMP response must be a JSON list")
    if len(value) > FMP_STAGE10_MAX_PRICE_ROWS:
        raise ResourceLimitError("FMP response exceeds the reviewed row bound")

    rows: list[FmpStage10PriceRow] = []
    seen_dates: set[str] = set()
    for source_row, raw in enumerate(value, start=1):
        if not isinstance(raw, dict) or frozenset(raw) != _RESPONSE_KEYS:
            raise ValidationError("FMP response row shape is invalid")
        if raw["symbol"] != prepared.symbol:
            raise ValidationError("FMP response contains an unexpected symbol")
        trade_date = _strict_iso_date(raw["date"], "FMP response date")
        if not prepared.window.start_date <= trade_date <= prepared.window.end_date:
            raise ValidationError("FMP response date is outside the requested interval")
        if trade_date in seen_dates:
            raise ValidationError("FMP response contains duplicate trade dates")
        seen_dates.add(trade_date)

        open_value = _decimal(raw["open"], "open")
        high_value = _decimal(raw["high"], "high")
        low_value = _decimal(raw["low"], "low")
        close_value = _decimal(raw["close"], "close")
        for field_name in ("change", "changePercent", "vwap"):
            _decimal(raw[field_name], field_name)
        volume = _volume(raw["volume"])
        if (
            min(open_value, high_value, low_value, close_value) <= 0
            or low_value > min(open_value, close_value)
            or high_value < max(open_value, close_value)
            or low_value > high_value
        ):
            raise ValidationError("FMP response contains inconsistent OHLC values")
        rows.append(
            FmpStage10PriceRow(
                symbol=prepared.symbol,
                trade_date=trade_date,
                open_value=open_value,
                high_value=high_value,
                low_value=low_value,
                close_value=close_value,
                volume=volume,
                source_row=source_row,
            )
        )
    rows.sort(key=lambda row: row.trade_date)
    return tuple(rows)


def capture_fmp_stage10_window(
    prepared: PreparedFmpStage10WindowCapture,
    api_key: str,
    transport: FmpStage10Transport,
) -> FmpStage10WindowCapture:
    """Issue exactly one injected transport request and parse its response.

    This primitive intentionally has no retry path.  The caller is responsible
    for writing its durable request-issued marker before invoking it.
    """

    if not isinstance(prepared, PreparedFmpStage10WindowCapture):
        raise ValidationError("Stage 10 history capture requires a prepared request")
    if not isinstance(api_key, str) or _API_KEY.fullmatch(api_key) is None:
        raise ValidationError("FMP credential is missing or invalid")
    if not isinstance(transport, FmpStage10Transport):
        raise ValidationError("FMP transport does not implement the reviewed interface")
    response = transport.get(
        path=FMP_STAGE10_PRICE_PATH,
        query=prepared.query,
        headers={"apikey": api_key},
        timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
    )
    if not isinstance(response, CapturedFmpStage10Response):
        raise ValidationError("FMP transport returned an invalid response type")
    if (
        response.status != 200
        or response.content_type.split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise StoreUnavailableError("FMP provider response was unavailable")
    rows = parse_fmp_stage10_window_response(prepared, response.body)
    disposition = "complete_empty" if not rows else "complete"
    return FmpStage10WindowCapture(
        prepared=prepared,
        response=response,
        rows=rows,
        disposition=disposition,
        response_sha256=hashlib.sha256(response.body).hexdigest(),
        semantic_sha256=_capture_semantic_sha256(prepared, rows, disposition),
    )



__all__ = (
    "FmpStage10WindowCapture",
    "PreparedFmpStage10WindowCapture",
    "STAGE10_HISTORY_WINDOWS",
    "Stage10HistoryWindow",
    "capture_fmp_stage10_window",
    "parse_fmp_stage10_window_response",
    "prepare_fmp_stage10_window_capture",
)
