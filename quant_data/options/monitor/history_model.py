"""Pure projection of saved ETF daily options captures for charting.

IV is a selected-contract EOD proxy, never the intraday trade-derived monitor IV.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from zoneinfo import ZoneInfo

from quant_data.options.universe import TIER1_ETFS

DERIVATION_VERSION = "options_daily_chart_v1"
_NY = ZoneInfo("America/New_York")
_TARGETS = (7, 30, 90)


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _nonnegative_int(value):
    number = _decimal(value)
    return int(number) if number is not None and number >= 0 and number == int(number) else None


def _day(value):
    if not isinstance(value, str):
        raise ValueError("invalid_session_date")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("invalid_session_date")
    return parsed


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid_source_timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive_source_timestamp")
    return parsed


def _identity(capture):
    if not isinstance(capture, dict):
        raise ValueError("invalid_capture")
    capture_id = capture.get("capture_id")
    if isinstance(capture_id, bool) or not isinstance(capture_id, int) or capture_id <= 0:
        raise ValueError("invalid_capture_id")
    symbol = capture.get("symbol")
    if symbol not in TIER1_ETFS:
        raise ValueError("unexpected_option_root")
    session_text = capture.get("session_date")
    session = _day(session_text)
    digest = capture.get("semantic_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid_semantic_sha256")
    captured_at = _timestamp(capture.get("captured_at"))
    spot = _decimal(capture.get("underlying_price"))
    if spot is None or spot <= 0:
        raise ValueError("invalid_underlying_price")
    return capture_id, symbol, session_text, session, digest, captured_at, spot


def _summary_totals(summaries):
    if not isinstance(summaries, list):
        raise ValueError("invalid_summaries")
    seen = set()
    full = {}
    for row in summaries:
        if not isinstance(row, dict):
            raise ValueError("invalid_summary")
        cell = tuple(row.get(name) for name in ("population", "dimension", "bucket", "right"))
        if any(not isinstance(value, str) for value in cell) or cell in seen:
            raise ValueError("duplicate_or_invalid_summary_cell")
        seen.add(cell)
        if cell[:3] == ("full", "overall", "all"):
            if cell[3] not in ("C", "P") or not isinstance(row.get("stats"), dict):
                raise ValueError("invalid_full_summary")
            full[cell[3]] = row["stats"]

    def side(right, metric, missing):
        stats = full.get(right)
        if stats is None or _nonnegative_int(stats.get(missing)) != 0:
            return None
        return _nonnegative_int(stats.get(metric))

    def both(metric, missing):
        call, put = (side(right, metric, missing) for right in ("C", "P"))
        return call, put
    call_volume, put_volume = both("volume", "volume_missing")
    call_oi, put_oi = both("open_interest", "oi_missing")
    return call_volume, put_volume, call_oi, put_oi


def _detail_identity(row, symbol):
    if not isinstance(row, dict):
        raise ValueError("invalid_option_detail")
    expiry_text = row.get("expiration")
    expiry = _day(expiry_text)
    strike_text = row.get("strike")
    strike = _decimal(strike_text)
    right = row.get("right")
    if strike is None or strike <= 0 or right not in ("C", "P") or not isinstance(strike_text, str):
        raise ValueError("invalid_contract_identity")
    expected = "|".join((symbol, expiry_text, strike_text, right))
    if row.get("contract_id") != expected:
        raise ValueError("cross_symbol_contract")
    return expiry, strike, right


def _usable_iv(row, symbol, session, spot, captured_at):
    if row.get("iv_quality") != "usable_root_proxy" or "anchor:1" not in row.get("reasons", ()):
        return None
    bid, ask, iv = (_decimal(row.get(name)) for name in ("bid", "ask", "implied_vol"))
    if bid is None or ask is None or iv is None or bid <= 0 or ask < bid or iv <= 0:
        return None
    source = row.get("eod_greeks")
    if not isinstance(source, dict):
        return None
    if source.get("symbol") != symbol or source.get("right") not in (
        row["right"], {"C": "CALL", "P": "PUT"}[row["right"]]
    ):
        return None
    if source.get("expiration") != row["expiration"] or _decimal(source.get("strike")) != _decimal(row["strike"]):
        return None
    reference = _decimal(source.get("underlying_price"))
    if reference != spot:
        return None
    try:
        observed = _timestamp(source.get("underlying_timestamp"))
    except (TypeError, ValueError):
        return None
    if observed > captured_at:
        raise ValueError("future_underlying_reference")
    if observed.astimezone(_NY).date() != session:
        return None
    cutoff = datetime.combine(session, time.max, _NY)
    if observed > cutoff:
        return None
    return float(iv)


def _iv_terms(details, symbol, session, spot, captured_at):
    if not isinstance(details, list) or len(details) > 300:
        raise ValueError("invalid_option_details")
    seen = set()
    pairs = {}
    oi_dates = set()
    invalid_oi_date = False
    for row in details:
        expiry, strike, right = _detail_identity(row, symbol)
        identity = row["contract_id"]
        if identity in seen:
            raise ValueError("duplicate_option_detail")
        seen.add(identity)
        if _nonnegative_int(row.get("oi")) is not None:
            try:
                effective = _day(row.get("oi_effective_date"))
            except (TypeError, ValueError):
                invalid_oi_date = True
            else:
                if effective >= session:
                    invalid_oi_date = True
                else:
                    oi_dates.add(effective.isoformat())
        dte = (expiry - session).days
        if dte <= 0:
            continue
        iv = _usable_iv(row, symbol, session, spot, captured_at)
        if iv is not None:
            pairs.setdefault((dte, strike), {})[right] = iv
    per_expiry = {}
    for (dte, strike), rights in pairs.items():
        if set(rights) != {"C", "P"}:
            continue
        choice = (abs(strike - spot), strike)
        if dte not in per_expiry or choice < per_expiry[dte][0]:
            per_expiry[dte] = (choice, (rights["C"] + rights["P"]) / 2)
    terms = {dte: value for dte, (_, value) in per_expiry.items()}
    oi_date = next(iter(oi_dates)) if len(oi_dates) == 1 and not invalid_oi_date else None
    return terms, 2 * len(terms), oi_date


def _constant_maturity(terms, target):
    if target in terms:
        return terms[target]
    shorter = [dte for dte in terms if dte < target]
    longer = [dte for dte in terms if dte > target]
    if not shorter or not longer:
        return None
    low, high = max(shorter), min(longer)
    low_variance = terms[low] ** 2 * low
    high_variance = terms[high] ** 2 * high
    variance = low_variance + (high_variance - low_variance) * (target - low) / (high - low)
    return math.sqrt(max(0.0, variance / target))


def project_daily(capture: dict, summaries: list[dict], details: list[dict]) -> dict:
    """Derive finite daily chart values solely from one saved canonical capture."""
    capture_id, symbol, session_text, session, digest, captured_at, spot = _identity(capture)
    call_volume, put_volume, call_oi, put_oi = _summary_totals(summaries)
    terms, iv_contracts, oi_date = _iv_terms(details, symbol, session, spot, captured_at)
    ivs = {target: _constant_maturity(terms, target) for target in _TARGETS}
    ratio = put_volume / call_volume if call_volume not in (None, 0) and put_volume is not None else None
    flags = ["provider_root_deliverables_unverified"]
    if call_volume is None or put_volume is None:
        flags.append("missing_volume")
    if call_oi is None or put_oi is None:
        flags.append("missing_open_interest")
    if oi_date is None:
        flags.append("oi_effective_date_unavailable")
    if iv_contracts:
        flags.append("selected_anchor_eod_iv_proxy")
    if any(value is None for value in ivs.values()):
        flags.append("missing_iv_coverage")
    result = {
        "symbol": symbol,
        "session": session_text,
        "source_capture_id": capture_id,
        "source_semantic_sha256": digest,
        "source_captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "derivation_version": DERIVATION_VERSION,
        "call_volume": call_volume,
        "put_volume": put_volume,
        "put_call_ratio": ratio,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "oi_effective_date": oi_date,
        "atm_iv_7": ivs[7],
        "atm_iv_30": ivs[30],
        "atm_iv_90": ivs[90],
        "iv_contracts": iv_contracts,
        "quality_flags": flags,
    }
    json.dumps(result, allow_nan=False)
    return result
