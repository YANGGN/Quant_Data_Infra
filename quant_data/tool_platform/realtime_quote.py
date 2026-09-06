"""One-request, nonpersistent FMP last-quote capability for the local agent host.

The public caller supplies only a ticker. Credentials, endpoint, timeout and
transport are host-owned. This is the provider's latest reported trade, not a
promise of an executable bid/ask or of an open exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import math
import re
from typing import Any, Callable, Mapping

from quant_data.errors import CapabilityUnavailableError, ValidationError
from quant_data.json_codec import loads_strict
from quant_data.tool_platform.results import QueryResult, RecordV1, fields_from_mapping
from quant_data.contracts import WarningV1, TruncationV1

CAPABILITY = "fmp_quote_live"
MAX_BYTES = 65536
TIMEOUT_SECONDS = 4
_SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9.^=\-]{0,31}")

def ticker_argument(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValidationError("ticker must be a single provider symbol")
    ticker = value.upper()
    if _SYMBOL.fullmatch(ticker) is None:
        raise ValidationError("ticker must be one bounded FMP symbol")
    return ticker

def quote_input_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": ["ticker"], "properties": {
                "ticker": {"type": "string", "minLength": 1, "maxLength": 32}}}

def parse_quote_arguments(arguments: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(arguments, Mapping) or set(arguments) != {"ticker"}:
        raise ValidationError("price_realtime accepts only ticker")
    return {"ticker": ticker_argument(arguments["ticker"])}

@dataclass(frozen=True, slots=True)
class QuoteCapture:
    body: bytes
    captured_at: str

def _instant(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Quote capture timestamp is invalid") from exc
    if value.tzinfo is None:
        raise ValidationError("Quote capture timestamp must be offset-aware")
    return value.astimezone(timezone.utc)

def _number(value: object, name: str, *, positive: bool = False) -> object:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int, Decimal)) or not math.isfinite(float(value)):
        raise ValidationError("FMP quote contains an invalid numeric field")
    if positive and value <= 0:
        raise ValidationError("FMP quote price must be positive")
    return Decimal(str(value)) if isinstance(value, float) else value

def quote_result(ticker: str, capture: QuoteCapture) -> QueryResult:
    ticker = ticker_argument(ticker)
    if not isinstance(capture, QuoteCapture):
        raise ValidationError("Quote host returned an invalid capture")
    now = _instant(capture.captured_at)
    payload = loads_strict(capture.body, max_bytes=MAX_BYTES)
    if not isinstance(payload, list) or len(payload) > 1:
        raise ValidationError("FMP quote returned an unsupported response")
    if not payload:
        return QueryResult(tool="price_realtime", status="not_established",
            warnings=(WarningV1("fmp_quote_unavailable", "FMP returned no quote for this symbol."),),
            truncation=TruncationV1(False, 1, 0, 0, False))
    row = payload[0]
    if not isinstance(row, dict) or row.get("symbol") != ticker:
        raise ValidationError("FMP quote identity does not match the requested ticker")
    price = _number(row.get("price"), "price", positive=True)
    stamp = row.get("timestamp")
    if price is None or stamp is None:
        return QueryResult(tool="price_realtime", status="not_established",
            warnings=(WarningV1("fmp_quote_missing_price_or_time", "FMP did not establish both price and quote time."),),
            truncation=TruncationV1(False, 1, 0, 0, False))
    if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= 0:
        raise ValidationError("FMP quote timestamp must be Unix seconds")
    try:
        quoted_at = datetime.fromtimestamp(stamp, timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValidationError("FMP quote timestamp is invalid") from exc
    age = (now - quoted_at).total_seconds()
    if age < -60:
        raise ValidationError("FMP quote timestamp is ahead of capture time")
    currency = row.get("currency")
    if currency is not None and (not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency)):
        raise ValidationError("FMP quote currency is invalid")
    values = dict(ticker=ticker, provider="fmp", endpoint="stable/quote",
        price=price, quote_timestamp=stamp, quoted_at=quoted_at.isoformat().replace("+00:00", "Z"),
        captured_at=capture.captured_at, quote_age_seconds=Decimal(str(max(0, age))),
        quote_type="latest_reported_trade", currency=currency or "SOURCE_UNSPECIFIED",
        content_sha256=hashlib.sha256(capture.body).hexdigest(),
        bid=_number(row.get("bid"), "bid"), ask=_number(row.get("ask"), "ask"),
        volume=_number(row.get("volume"), "volume"),
        previous_close=_number(row.get("previousClose"), "previousClose"),
        day_high=_number(row.get("dayHigh"), "dayHigh"),
        day_low=_number(row.get("dayLow"), "dayLow"),
        open=_number(row.get("open"), "open"))
    warnings = [WarningV1("last_trade_not_executable_quote",
        "FMP supplies its latest reported trade; bid/ask and exchange-open status may be unavailable.")]
    if age > 900:
        warnings.append(WarningV1("quote_older_than_15_minutes",
            "The provider quote predates this fetch by more than 15 minutes; use quoted_at and quote_age_seconds."))
    if currency is None:
        warnings.append(WarningV1("fmp_source_currency_unspecified", "This FMP quote did not state currency."))
    return QueryResult(tool="price_realtime",
        records=(RecordV1("fmp_quote", fields_from_mapping(values)),),
        warnings=tuple(warnings), truncation=TruncationV1(False, 1, 1, 1, False))

def local_quote_fetcher(project_root: Any) -> Callable[[str], QuoteCapture]:
    """Construct without probing a credential; resolve it only after validation."""
    def fetch(ticker: str) -> QuoteCapture:
        ticker = ticker_argument(ticker)
        import os
        from quant_data.credentials import read_project_credential
        from quant_data.operations.fmp_macro_calendar_history import _StdlibTransport
        try:
            key = read_project_credential(project_root=project_root, name="FMP_API_KEY", environment=os.environ)
            response = _StdlibTransport().request(
                url="https://financialmodelingprep.com/stable/quote",
                parameters={"symbol": ticker, "apikey": key},
                headers={"Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"},
                timeout_seconds=TIMEOUT_SECONDS, max_bytes=MAX_BYTES)
            if (response.status != 200 or response.redirected
                or response.media_type.split(";", 1)[0].strip().lower() != "application/json"
                or key.encode() in response.body):
                raise ValueError("Provider response policy failed")
            return QuoteCapture(response.body, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        except Exception:
            # Never serialize an exception carrying a credential-bearing URL/body.
            raise CapabilityUnavailableError("FMP quote request unavailable; no retry was attempted.",
                                             capability_id=CAPABILITY) from None
    return fetch

def invoke_quote(arguments: Mapping[str, Any], context: Any) -> QueryResult:
    parsed = parse_quote_arguments(arguments)
    context.checkpoint()
    context.capabilities.require(CAPABILITY)
    if context.quote_fetcher is None:
        raise CapabilityUnavailableError("The host does not enable live FMP quotes.", capability_id=CAPABILITY)
    capture = context.quote_fetcher(parsed["ticker"])
    context.checkpoint()
    return quote_result(parsed["ticker"], capture)

def price_realtime(ticker: str) -> dict[str, Any]:
    """Local Python convenience, same one-request host policy as the public tool."""
    from quant_data.tool_platform.local_agent_cli import create_local_application
    return create_local_application().dispatcher.call(
        "price_realtime", {"ticker": ticker_argument(ticker)}, tool_version="1.0.0")
