"""Historical US cash-equity sessions and pure ETF price features.

The calendar is an observed historical schedule, bounded to 2024 through
2026. It is based on the NYSE Group holiday and early-closing calendar
published November 10, 2023:
https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx

The January 9, 2025 special closure is based on the NYSE notice published
December 30, 2024:
https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx

This module makes no calendar promise outside that observed coverage and does
not fetch providers, read stores, or infer price adjustments.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Final
from zoneinfo import ZoneInfo

from quant_data.errors import ValidationError


ETF_SYMBOLS: Final[tuple[str, ...]] = (
    "SPY",
    "QQQ",
    "DIA",
    "IWM",
    "VEA",
    "VWO",
    "IEF",
    "TLT",
    "TIP",
    "LQD",
    "HYG",
    "GLD",
    "SLV",
    "PDBC",
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
)

FEATURE_NAMES: Final[tuple[str, ...]] = (
    "split_adjusted_price",
    "ten_month_sma",
    "return_12_to_1_month",
    "return_3_month",
    "return_6_month",
    "return_12_month",
    "realized_volatility_21",
    "realized_volatility_63",
    "realized_volatility_252",
)

CALENDAR_ID: Final[str] = "US_cash_equity_2024_2026"
CALENDAR_COVERAGE_START: Final[date] = date(2024, 1, 1)
CALENDAR_COVERAGE_END: Final[date] = date(2026, 12, 31)

_NEW_YORK = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)
_SPLIT_ADJUSTED_STATUS = "split_adjusted_excluding_distributions"
_MISSING = object()

_HOLIDAYS: Final[frozenset[date]] = frozenset(
    {
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
        date(2025, 1, 1),
        date(2025, 1, 9),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)

_EARLY_CLOSE_DATES: Final[frozenset[date]] = frozenset(
    {
        date(2024, 7, 3),
        date(2024, 11, 29),
        date(2024, 12, 24),
        date(2025, 7, 3),
        date(2025, 11, 28),
        date(2025, 12, 24),
        date(2026, 11, 27),
        date(2026, 12, 24),
    }
)


def _is_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _is_covered(day: date) -> bool:
    return CALENDAR_COVERAGE_START <= day <= CALENDAR_COVERAGE_END


def _is_session(day: date) -> bool:
    return _is_covered(day) and day.weekday() < 5 and day not in _HOLIDAYS


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _month_end_offset(endpoint: date, offset: int) -> date | None:
    ordinal = endpoint.year * 12 + endpoint.month - 1 - offset
    return month_end(ordinal // 12, ordinal % 12 + 1)


def _empty_results(reason: str) -> tuple[dict[str, Decimal | None], dict[str, str]]:
    return (
        {name: None for name in FEATURE_NAMES},
        {name: reason for name in FEATURE_NAMES},
    )


def _price_at(
    prices: Mapping[date, Decimal],
    day: date,
    *,
    missing_reason: str,
) -> tuple[Decimal | None, str]:
    value = prices.get(day, _MISSING)
    if value is _MISSING:
        return None, missing_reason
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= Decimal(0)
    ):
        return None, "invalid_price"
    return value, "computed"


def _monthly_price(
    prices: Mapping[date, Decimal],
    day: date,
    *,
    endpoint: date,
) -> tuple[Decimal | None, str]:
    return _price_at(
        prices,
        day,
        missing_reason=(
            "missing_endpoint_price"
            if day == endpoint
            else "missing_monthly_endpoint"
        ),
    )


def _ten_month_sma(
    prices: Mapping[date, Decimal],
    endpoint: date,
) -> tuple[Decimal | None, str]:
    endpoints = tuple(
        _month_end_offset(endpoint, offset) for offset in range(9, -1, -1)
    )
    if any(day is None for day in endpoints):
        return None, "insufficient_history"
    values: list[Decimal] = []
    for day in endpoints:
        assert day is not None
        value, reason = _monthly_price(prices, day, endpoint=endpoint)
        if value is None:
            return None, reason
        values.append(value)
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        return sum(values, Decimal(0)) / Decimal(len(values)), "computed"


def _simple_return(
    prices: Mapping[date, Decimal],
    numerator_day: date | None,
    denominator_day: date | None,
    *,
    endpoint: date,
) -> tuple[Decimal | None, str]:
    if numerator_day is None or denominator_day is None:
        return None, "insufficient_history"
    numerator, reason = _monthly_price(prices, numerator_day, endpoint=endpoint)
    if numerator is None:
        return None, reason
    denominator, reason = _monthly_price(prices, denominator_day, endpoint=endpoint)
    if denominator is None:
        return None, reason
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        return numerator / denominator - Decimal(1), "computed"


def _realized_volatility(
    prices: Mapping[date, Decimal],
    endpoint: date,
    *,
    window: int,
) -> tuple[Decimal | None, str]:
    expected_sessions = sessions(CALENDAR_COVERAGE_START, endpoint)
    if len(expected_sessions) < window + 1:
        return None, "insufficient_history"
    trailing_sessions = expected_sessions[-(window + 1) :]
    values: list[Decimal] = []
    for day in trailing_sessions:
        value, reason = _price_at(
            prices,
            day,
            missing_reason=(
                "missing_endpoint_price"
                if day == endpoint
                else "missing_daily_session"
            ),
        )
        if value is None:
            return None, reason
        values.append(value)
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        returns = tuple(
            current / previous - Decimal(1)
            for previous, current in zip(values, values[1:])
        )
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = (
            sum(
                ((value - mean) * (value - mean) for value in returns),
                Decimal(0),
            )
            / Decimal(len(returns) - 1)
        )
        volatility = variance.sqrt() * Decimal(252).sqrt()
    return volatility, "zero_variance" if variance == 0 else "computed"


def sessions(start: date, end: date) -> tuple[date, ...]:
    """Return inclusive regular cash-equity sessions within the known coverage."""

    if not _is_date(start) or not _is_date(end):
        raise ValidationError("Calendar session bounds must be dates")
    if not _is_covered(start) or not _is_covered(end):
        raise ValidationError("Calendar session bounds are outside supported coverage")
    if end < start:
        raise ValidationError("Calendar session end cannot precede start")
    current = start
    result: list[date] = []
    while current <= end:
        if _is_session(current):
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def month_end(year: int, month: int) -> date | None:
    """Return the final known regular cash-equity session in a calendar month."""

    if (
        isinstance(year, bool)
        or not isinstance(year, int)
        or isinstance(month, bool)
        or not isinstance(month, int)
        or not 1 <= month <= 12
        or not CALENDAR_COVERAGE_START.year <= year <= CALENDAR_COVERAGE_END.year
    ):
        return None
    current = _last_day_of_month(year, month)
    while not _is_session(current):
        current -= timedelta(days=1)
    return current


def completed_month_end(cutoff: datetime) -> date | None:
    """Return the latest month-end session closed by an aware cutoff instant."""

    if (
        not isinstance(cutoff, datetime)
        or cutoff.tzinfo is None
        or cutoff.utcoffset() is None
    ):
        raise ValidationError("ETF calendar cutoff must be an offset-aware datetime")
    try:
        local_cutoff = cutoff.astimezone(_NEW_YORK)
    except OverflowError:
        # Extreme valid datetimes can under/overflow during timezone conversion.
        return None
    local_day = local_cutoff.date()
    if not _is_covered(local_day):
        return None
    current_month_end = month_end(local_day.year, local_day.month)
    assert current_month_end is not None
    close = datetime.combine(
        current_month_end,
        _EARLY_CLOSE if current_month_end in _EARLY_CLOSE_DATES else _REGULAR_CLOSE,
        tzinfo=_NEW_YORK,
    )
    if local_cutoff >= close:
        return current_month_end
    return _month_end_offset(local_day, 1)


def calculate_features(
    prices: Mapping[date, Decimal],
    endpoint: date,
    *,
    adjustment_status: str,
) -> tuple[dict[str, Decimal | None], dict[str, str]]:
    """Calculate explicit, unfilled monthly and daily ETF price features.

    The caller must establish the split-only adjustment basis. Any other
    status fails closed and no supplied price is relabelled or transformed.
    """

    if adjustment_status != _SPLIT_ADJUSTED_STATUS:
        return _empty_results("adjustment_basis_not_established")
    if not isinstance(prices, Mapping):
        raise ValidationError("ETF prices must be a date-to-Decimal mapping")
    if not _is_date(endpoint):
        raise ValidationError("ETF feature endpoint must be a date")
    if not _is_covered(endpoint):
        return _empty_results("endpoint_outside_calendar_coverage")
    if not _is_session(endpoint):
        return _empty_results("endpoint_not_session")

    values, reasons = _empty_results("not_computed")
    endpoint_price, reason = _price_at(
        prices,
        endpoint,
        missing_reason="missing_endpoint_price",
    )
    if endpoint_price is not None:
        values["split_adjusted_price"] = endpoint_price
    reasons["split_adjusted_price"] = reason

    if month_end(endpoint.year, endpoint.month) == endpoint:
        value, reason = _ten_month_sma(prices, endpoint)
        values["ten_month_sma"] = value
        reasons["ten_month_sma"] = reason

        value, reason = _simple_return(
            prices,
            _month_end_offset(endpoint, 1),
            _month_end_offset(endpoint, 12),
            endpoint=endpoint,
        )
        values["return_12_to_1_month"] = value
        reasons["return_12_to_1_month"] = reason

        for name, offset in (
            ("return_3_month", 3),
            ("return_6_month", 6),
            ("return_12_month", 12),
        ):
            value, reason = _simple_return(
                prices,
                endpoint,
                _month_end_offset(endpoint, offset),
                endpoint=endpoint,
            )
            values[name] = value
            reasons[name] = reason
    else:
        for name in (
            "ten_month_sma",
            "return_12_to_1_month",
            "return_3_month",
            "return_6_month",
            "return_12_month",
        ):
            reasons[name] = "endpoint_not_month_end"

    for name, window in (
        ("realized_volatility_21", 21),
        ("realized_volatility_63", 63),
        ("realized_volatility_252", 252),
    ):
        value, reason = _realized_volatility(prices, endpoint, window=window)
        values[name] = value
        reasons[name] = reason
    return values, reasons
