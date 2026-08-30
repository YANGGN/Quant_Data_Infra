"""Read-only cross-sectional performance over explicit Stage 10 symbols.

The operation deliberately compares only caller-selected symbols.  It does
not infer a universe, construct weights, or imply any portfolio semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Any, Mapping, Sequence

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.stage10_series import (
    DATASET_ID,
    Stage10DailyPriceQuery,
    Stage10DailyPriceRepository,
)
from quant_data.registry import Registry
from quant_data.temporal import TemporalValue

from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, RecordV1, fields_from_mapping


TOOL_NAME = "market.cross_sectional_performance"
OPERATION_VERSION = "2.0.0"
OPERATION_VERSION_V21 = "2.1.0"
MAX_TICKERS = 50
MAX_ROLLING_WINDOW = 252
MAX_AS_OF_SOURCE_CANDIDATES = 200_000
_AS_OF_READ_BOUND_PER_TICKER = MAX_AS_OF_SOURCE_CANDIDATES + 1

_ARGUMENT_NAMES = frozenset(
    {
        "tickers",
        "start_date",
        "end_date",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    }
)
_ANALYTICS_ARGUMENT_NAMES = frozenset(
    {
        "tickers",
        "benchmark_ticker",
        "window",
        "start_date",
        "end_date",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    }
)
_UNAVAILABLE_MESSAGES = frozenset(
    {
        "Requested Stage 10 market instrument is unavailable",
        "Requested Stage 10 market instrument is unavailable at the cutoff",
    }
)
_DATE_ONLY_SAFETY_WARNING = "date_only_same_day_intraday_safety_not_established"


@dataclass(frozen=True, slots=True)
class _Coverage:
    """One requested symbol's bounded close-price coverage and result."""

    ticker: str
    asset_type: str | None
    instrument_id: str | None
    observation_count: int
    first_period: str | None
    last_period: str | None
    first_close: Decimal | None
    last_close: Decimal | None
    cumulative_return: Decimal | None
    coverage_status: str
    source_lineage_digest: str | None
    source_truncated: bool
    rank: int | None = None
    percentile: Decimal | None = None

    @property
    def ranking_eligible(self) -> bool:
        return self.coverage_status == "complete"

    def fields(self) -> dict[str, str | Decimal | int | bool | None]:
        return {
            "asset_type": self.asset_type,
            "coverage_status": self.coverage_status,
            "cumulative_return": self.cumulative_return,
            "first_close": self.first_close,
            "first_period": self.first_period,
            "instrument_id": self.instrument_id,
            "last_close": self.last_close,
            "last_period": self.last_period,
            "observation_count": self.observation_count,
            "percentile": self.percentile,
            "rank": self.rank,
            "ranking_eligible": self.ranking_eligible,
            "source_lineage_digest": self.source_lineage_digest,
            "source_truncated": self.source_truncated,
            "ticker": self.ticker,
        }


@dataclass(frozen=True, slots=True)
class _AnalyticsArguments:
    """Validated public inputs for the additive v2.1 analytics variant."""

    tickers: tuple[str, ...]
    benchmark_ticker: str
    window: int
    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: str
    limit: int


def _normalized_tickers(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("Market cross-sectional tickers must be an array")
    if not 2 <= len(value) <= MAX_TICKERS:
        raise ValidationError("Market cross-sectional tickers must contain 2 through 50 symbols")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValidationError("Market cross-sectional ticker is invalid")
        ticker = raw.strip().upper()
        if (
            not ticker
            or len(ticker) > 200
            or any(ord(character) < 32 or ord(character) == 127 for character in ticker)
        ):
            raise ValidationError("Market cross-sectional ticker is invalid")
        if ticker in seen:
            raise ValidationError(
                "Market cross-sectional tickers must be unique after normalization"
            )
        normalized.append(ticker)
        seen.add(ticker)
    return tuple(normalized)


def _arguments(
    arguments: Mapping[str, Any],
) -> tuple[tuple[str, ...], str | None, str | None, str, str | None, str, int]:
    if not isinstance(arguments, Mapping) or set(arguments) != _ARGUMENT_NAMES:
        raise ValidationError(
            "Market cross-sectional performance requires typed v2 arguments"
        )
    tickers = _normalized_tickers(arguments["tickers"])
    limit = arguments["limit"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 10_000:
        raise ResourceLimitError(
            "Market cross-sectional per-ticker limit must be from 2 through 10000"
        )
    start_date = arguments["start_date"]
    end_date = arguments["end_date"]
    mode = arguments["mode"]
    as_of = arguments["as_of"]
    date_only_policy = arguments["date_only_policy"]
    # Validate all shared temporal inputs without touching a store.  The
    # repository query remains the single source of selection semantics.
    Stage10DailyPriceQuery(
        identifier=tickers[0],
        identifier_kind="provider_symbol",
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        as_of=as_of,
        date_only_policy=date_only_policy,
        limit=limit,
    )
    return tickers, start_date, end_date, mode, as_of, date_only_policy, limit



def _normalized_benchmark_ticker(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("Market cross-sectional benchmark ticker is invalid")
    ticker = value.strip().upper()
    if (
        not ticker
        or len(ticker) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in ticker)
    ):
        raise ValidationError("Market cross-sectional benchmark ticker is invalid")
    return ticker


def _analytics_arguments(arguments: Mapping[str, Any]) -> _AnalyticsArguments:
    if not isinstance(arguments, Mapping) or set(arguments) != _ANALYTICS_ARGUMENT_NAMES:
        raise ValidationError(
            "Market cross-sectional analytics requires typed v2.1 arguments"
        )
    tickers = _normalized_tickers(arguments["tickers"])
    benchmark_ticker = _normalized_benchmark_ticker(arguments["benchmark_ticker"])
    if benchmark_ticker not in tickers:
        raise ValidationError(
            "Market cross-sectional benchmark ticker must be one of tickers"
        )
    window = arguments["window"]
    if (
        isinstance(window, bool)
        or not isinstance(window, int)
        or not 2 <= window <= MAX_ROLLING_WINDOW
    ):
        raise ResourceLimitError(
            "Market cross-sectional analytics window must be from 2 through 252"
        )
    limit = arguments["limit"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 10_000:
        raise ResourceLimitError(
            "Market cross-sectional per-ticker limit must be from 2 through 10000"
        )
    start_date = arguments["start_date"]
    end_date = arguments["end_date"]
    mode = arguments["mode"]
    as_of = arguments["as_of"]
    date_only_policy = arguments["date_only_policy"]
    Stage10DailyPriceQuery(
        identifier=tickers[0],
        identifier_kind="provider_symbol",
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        as_of=as_of,
        date_only_policy=date_only_policy,
        limit=limit,
    )
    return _AnalyticsArguments(
        tickers=tickers,
        benchmark_ticker=benchmark_ticker,
        window=window,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        as_of=as_of,
        date_only_policy=date_only_policy,
        limit=limit,
    )


def _is_unavailable(error: ValidationError) -> bool:
    return str(error) in _UNAVAILABLE_MESSAGES


def _coverage_for_series(ticker: str, series: Any) -> _Coverage:
    observations = series.observations
    metadata = series.metadata
    instrument_id = metadata.get("instrument_id")
    asset_type = metadata.get("asset_type")
    if (
        not isinstance(instrument_id, str)
        or not instrument_id
        or not isinstance(asset_type, str)
        or not asset_type
    ):
        raise ValidationError("Stage 10 close-price metadata is incomplete")
    if not observations:
        return _Coverage(
            ticker=ticker,
            asset_type=asset_type,
            instrument_id=instrument_id,
            observation_count=0,
            first_period=None,
            last_period=None,
            first_close=None,
            last_close=None,
            cumulative_return=None,
            coverage_status="insufficient_observations",
            source_lineage_digest=series.lineage_digest,
            source_truncated=series.truncated,
        )
    first = observations[0]
    last = observations[-1]
    if first.value is None or last.value is None:
        raise ValidationError("Stage 10 close-price selection is unexpectedly missing")
    if len(observations) < 2:
        status = "insufficient_observations"
        cumulative_return = None
    elif first.value == 0:
        status = "zero_base_price"
        cumulative_return = None
    else:
        with localcontext() as decimal_context:
            decimal_context.prec = 34
            cumulative_return = last.value / first.value - Decimal(1)
        status = "truncated" if series.truncated else "complete"
    return _Coverage(
        ticker=ticker,
        asset_type=asset_type,
        instrument_id=instrument_id,
        observation_count=len(observations),
        first_period=first.period_start,
        last_period=last.period_end,
        first_close=first.value,
        last_close=last.value,
        cumulative_return=cumulative_return,
        coverage_status=status,
        source_lineage_digest=series.lineage_digest,
        source_truncated=series.truncated,
    )


def _rank(coverage: Sequence[_Coverage]) -> tuple[_Coverage, ...]:
    eligible = sorted(
        (item for item in coverage if item.ranking_eligible),
        key=lambda item: (-item.cumulative_return, item.ticker),
    )
    ranked: list[_Coverage] = []
    count = len(eligible)
    for index, item in enumerate(eligible, start=1):
        if count == 1:
            percentile = Decimal(1)
        else:
            with localcontext() as decimal_context:
                decimal_context.prec = 34
                percentile = Decimal(count - index) / Decimal(count - 1)
        ranked.append(replace(item, rank=index, percentile=percentile))
    excluded = sorted(
        (item for item in coverage if not item.ranking_eligible),
        key=lambda item: item.ticker,
    )
    return tuple((*ranked, *excluded))


def _warnings(
    coverage: Sequence[_Coverage],
    *,
    mode: str,
    source_warning_codes: Sequence[str],
) -> tuple[WarningV1, ...]:
    warning_messages = {
        "insufficient_source_observations_excluded_from_ranking": (
            "A requested symbol had fewer than two selected close observations."
        ),
        "market_instrument_unavailable": (
            "A requested symbol is not available in the selected Stage 10 view."
        ),
        "point_in_time_safe_only_for_retained_local_captures": (
            "As-of results are safe only for retained local captures."
        ),
        "source_series_truncated_excluded_from_ranking": (
            "A requested symbol reached its per-ticker source limit and was excluded from ranking."
        ),
        "zero_base_price_excluded_from_ranking": (
            "A requested symbol has a zero first close, so its simple return is undefined."
        ),
    }
    # The Stage 10 reader owns source selection and its caveats. Retain every
    # emitted source warning, while adding only the cross-sectional consequence
    # of a selected source result. A set keeps shared warnings deduplicated.
    codes = {code for code in source_warning_codes if code}
    if mode == "as_of":
        codes.add("point_in_time_safe_only_for_retained_local_captures")
    for item in coverage:
        if item.coverage_status == "insufficient_observations":
            codes.add("insufficient_source_observations_excluded_from_ranking")
        elif item.coverage_status == "unavailable":
            codes.add("market_instrument_unavailable")
        elif item.coverage_status == "truncated":
            codes.add("source_series_truncated_excluded_from_ranking")
        elif item.coverage_status == "zero_base_price":
            codes.add("zero_base_price_excluded_from_ranking")
    return tuple(
        WarningV1(
            code=code,
            message=warning_messages.get(
                code,
                f"Stage 10 source-series warning: {code}.",
            ),
        )
        for code in sorted(codes)
    )


def _point_in_time_selection(
    *,
    mode: str,
    source_warning_codes: Sequence[str],
) -> tuple[str, tuple[str, ...]]:
    """Report the accepted date-only caveat without changing source selection."""

    if mode == "latest":
        return "not_applicable", ()
    unsafe_reasons = tuple(
        sorted(
            {
                code
                for code in source_warning_codes
                if code == _DATE_ONLY_SAFETY_WARNING
            }
        )
    )
    return ("unsafe" if unsafe_reasons else "safe"), unsafe_reasons



def _daily_return_history(series: Any) -> tuple[tuple[str, Decimal | None], ...]:
    """Return observed daily simple returns keyed by their ending trade date."""

    history: list[tuple[str, Decimal | None]] = []
    periods: set[str] = set()
    observations = series.observations
    for previous, current in zip(observations, observations[1:]):
        if previous.value is None or current.value is None:
            raise ValidationError("Stage 10 close-price selection is unexpectedly missing")
        period = current.period_end
        if period in periods:
            raise ValidationError("Stage 10 close-price selection has duplicate dates")
        periods.add(period)
        if previous.value == 0:
            history.append((period, None))
            continue
        with localcontext() as decimal_context:
            decimal_context.prec = 34
            history.append((period, current.value / previous.value - Decimal(1)))
    return tuple(history)


def _realized_volatility(
    series: Any | None,
    *,
    window: int,
) -> tuple[Decimal | None, str, int]:
    """Calculate trailing sample daily-return volatility without filling gaps."""

    if series is None:
        return None, "unavailable", 0
    if series.truncated:
        return None, "source_truncated", 0
    history = _daily_return_history(series)
    if len(history) < window:
        return None, "insufficient_history", len(history)
    trailing = history[-window:]
    if any(value is None for _, value in trailing):
        return None, "zero_base_price", len(trailing)
    values = tuple(value for _, value in trailing if value is not None)
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        count = Decimal(len(values))
        mean = sum(values, Decimal(0)) / count
        squared_deviations = sum(
            ((value - mean) * (value - mean) for value in values),
            Decimal(0),
        )
        variance = squared_deviations / Decimal(len(values) - 1)
        volatility = variance.sqrt() * Decimal(252).sqrt()
    status = "zero_variance" if variance == 0 else "computed"
    return volatility, status, len(values)


def _rolling_beta(
    series: Any | None,
    benchmark_series: Any | None,
    *,
    window: int,
) -> tuple[Decimal | None, str, int]:
    """Calculate beta from the most recent explicitly aligned daily returns."""

    if series is None:
        return None, "unavailable", 0
    if benchmark_series is None:
        return None, "benchmark_unavailable", 0
    if series.truncated:
        return None, "source_truncated", 0
    if benchmark_series.truncated:
        return None, "benchmark_source_truncated", 0
    series_history = dict(_daily_return_history(series))
    benchmark_history = dict(_daily_return_history(benchmark_series))
    aligned = tuple(
        (
            period,
            series_history[period],
            benchmark_history[period],
        )
        for period in sorted(set(series_history).intersection(benchmark_history))
    )
    if len(aligned) < window:
        return None, "insufficient_history", len(aligned)
    trailing = aligned[-window:]
    if any(series_return is None for _, series_return, _ in trailing):
        return None, "zero_base_price", len(trailing)
    if any(benchmark_return is None for _, _, benchmark_return in trailing):
        return None, "benchmark_zero_base_price", len(trailing)
    series_returns = tuple(
        series_return
        for _, series_return, _ in trailing
        if series_return is not None
    )
    benchmark_returns = tuple(
        benchmark_return
        for _, _, benchmark_return in trailing
        if benchmark_return is not None
    )
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        count = Decimal(len(trailing))
        series_mean = sum(series_returns, Decimal(0)) / count
        benchmark_mean = sum(benchmark_returns, Decimal(0)) / count
        covariance = sum(
            (
                (series_return - series_mean)
                * (benchmark_return - benchmark_mean)
                for series_return, benchmark_return in zip(
                    series_returns,
                    benchmark_returns,
                )
            ),
            Decimal(0),
        ) / Decimal(len(trailing) - 1)
        benchmark_variance = sum(
            (
                (benchmark_return - benchmark_mean)
                * (benchmark_return - benchmark_mean)
                for benchmark_return in benchmark_returns
            ),
            Decimal(0),
        ) / Decimal(len(trailing) - 1)
        if benchmark_variance == 0:
            return None, "zero_variance", len(trailing)
        beta = covariance / benchmark_variance
    return beta, "computed", len(trailing)


def _benchmark_relative_cumulative_return(
    item: _Coverage,
    benchmark: _Coverage,
) -> tuple[Decimal | None, str]:
    """Compare only like-for-like observed endpoint returns."""

    if item.coverage_status != "complete" or item.cumulative_return is None:
        return None, f"source_{item.coverage_status}"
    if benchmark.coverage_status != "complete" or benchmark.cumulative_return is None:
        return None, f"benchmark_{benchmark.coverage_status}"
    if (
        item.first_period != benchmark.first_period
        or item.last_period != benchmark.last_period
    ):
        return None, "endpoint_misaligned"
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        return item.cumulative_return - benchmark.cumulative_return, "computed"


def _market_breadth(coverage: Sequence[_Coverage]) -> dict[str, Decimal | int | None]:
    """Summarize the explicitly requested, rank-eligible symbol set."""

    eligible = tuple(item for item in coverage if item.ranking_eligible)
    positive_count = sum(
        item.cumulative_return is not None and item.cumulative_return > 0
        for item in eligible
    )
    negative_count = sum(
        item.cumulative_return is not None and item.cumulative_return < 0
        for item in eligible
    )
    unchanged_count = sum(
        item.cumulative_return == 0
        for item in eligible
    )
    with localcontext() as decimal_context:
        decimal_context.prec = 34
        positive_fraction = (
            None
            if not eligible
            else Decimal(positive_count) / Decimal(len(eligible))
        )
    return {
        "breadth_eligible_count": len(eligible),
        "breadth_negative_count": negative_count,
        "breadth_positive_count": positive_count,
        "breadth_positive_fraction": positive_fraction,
        "breadth_unchanged_count": unchanged_count,
    }


def _analytics_record_fields(
    item: _Coverage,
    *,
    series: Any | None,
    benchmark: _Coverage,
    benchmark_series: Any | None,
    window: int,
) -> dict[str, str | Decimal | int | bool | None]:
    fields = item.fields()
    realized_volatility, realized_volatility_status, realized_count = (
        _realized_volatility(series, window=window)
    )
    rolling_beta, rolling_beta_status, beta_count = _rolling_beta(
        series,
        benchmark_series,
        window=window,
    )
    benchmark_relative_return, relative_status = _benchmark_relative_cumulative_return(
        item,
        benchmark,
    )
    fields.update(
        {
            "benchmark_relative_cumulative_return": benchmark_relative_return,
            "benchmark_relative_cumulative_return_status": relative_status,
            "realized_volatility": realized_volatility,
            "realized_volatility_observation_count": realized_count,
            "realized_volatility_status": realized_volatility_status,
            "rolling_beta": rolling_beta,
            "rolling_beta_observation_count": beta_count,
            "rolling_beta_status": rolling_beta_status,
        }
    )
    return fields


def invoke_stage10_cross_sectional_performance(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Compare simple close-to-close returns for an explicit bounded symbol set."""

    if name != TOOL_NAME:
        raise LookupError("Stage 10 cross-sectional operation is not registered")
    (
        tickers,
        start_date,
        end_date,
        mode,
        as_of,
        date_only_policy,
        limit,
    ) = _arguments(arguments)
    source_work = len(tickers) * (
        _AS_OF_READ_BOUND_PER_TICKER if mode == "as_of" else limit + 1
    )
    context.checkpoint()
    context.budget.require(
        rows=len(tickers),
        series=1,
        operations=source_work,
    )
    repository = Stage10DailyPriceRepository(context.store_map, registry)
    coverage: list[_Coverage] = []
    lineage: list[LineageRef] = []
    source_warning_codes: set[str] = set()
    for ticker in tickers:
        context.checkpoint()
        query = Stage10DailyPriceQuery(
            identifier=ticker,
            identifier_kind="provider_symbol",
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            as_of=as_of,
            date_only_policy=date_only_policy,
            limit=limit,
        )
        try:
            series = repository.get_close_series(query)
        except ValidationError as error:
            if not _is_unavailable(error):
                raise
            coverage.append(
                _Coverage(
                    ticker=ticker,
                    asset_type=None,
                    instrument_id=None,
                    observation_count=0,
                    first_period=None,
                    last_period=None,
                    first_close=None,
                    last_close=None,
                    cumulative_return=None,
                    coverage_status="unavailable",
                    source_lineage_digest=None,
                    source_truncated=False,
                )
            )
            continue
        source_warning_codes.update(series.warnings)
        item = _coverage_for_series(ticker, series)
        coverage.append(item)
        assert item.source_lineage_digest is not None
        lineage.append(
            LineageRef(
                dataset_id=DATASET_ID,
                store_role="market",
                semantic_id=item.source_lineage_digest,
            )
        )
    ranked = _rank(coverage)
    records = tuple(
        RecordV1(
            record_type="market_cross_sectional_performance",
            fields=fields_from_mapping(item.fields()),
        )
        for item in ranked
    )
    status_counts = {
        status: sum(item.coverage_status == status for item in ranked)
        for status in (
            "complete",
            "insufficient_observations",
            "truncated",
            "unavailable",
            "zero_base_price",
        )
    }
    cutoff = None if as_of is None else TemporalValue.parse(as_of, pointer="/as_of")
    point_in_time_status, unsafe_reasons = _point_in_time_selection(
        mode=mode,
        source_warning_codes=tuple(source_warning_codes),
    )
    selected_identities = [
        {
            "asset_type": item.asset_type,
            "instrument_id": item.instrument_id,
            "ticker": item.ticker,
        }
        for item in coverage
        if item.instrument_id is not None
    ]
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="market_cross_sectional_performance_summary",
                message=(
                    "Simple returns use the first and last selected close for "
                    "each explicit symbol; only complete source selections are ranked."
                ),
                metrics=fields_from_mapping(
                    {
                        "actual_mode": mode,
                        "availability_basis": "local_capture",
                        "complete_count": status_counts["complete"],
                        "cutoff": None if cutoff is None else cutoff.raw,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": date_only_policy,
                        "insufficient_observation_count": status_counts[
                            "insufficient_observations"
                        ],
                        "mode": mode,
                        "point_in_time_status": point_in_time_status,
                        "requested_end_date": end_date,
                        "requested_mode": mode,
                        "requested_start_date": start_date,
                        "requested_tickers": dumps_strict(list(tickers)),
                        "ranked_count": len(
                            tuple(item for item in ranked if item.ranking_eligible)
                        ),
                        "ranking_order": (
                            "descending_cumulative_return_then_ticker"
                        ),
                        "return_definition": (
                            "last_close_divided_by_first_close_minus_one"
                        ),
                        "selected_instrument_count": len(selected_identities),
                        "selected_instrument_identities": dumps_strict(
                            selected_identities
                        ),
                        "selected_observation_count": sum(
                            item.observation_count for item in coverage
                        ),
                        "source_limit_per_ticker": limit,
                        "source_series_warning_count": len(source_warning_codes),
                        "source_total_observation_bound": source_work,
                        "ticker_count": len(tickers),
                        "truncated_count": status_counts["truncated"],
                        "unavailable_count": status_counts["unavailable"],
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                        "zero_base_count": status_counts["zero_base_price"],
                    }
                ),
            ),
        ),
        warnings=_warnings(
            ranked,
            mode=mode,
            source_warning_codes=tuple(source_warning_codes),
        ),
        lineage=tuple(lineage),
        # Every requested symbol receives one record.  Per-ticker source
        # truncation is carried by the record rather than hiding any symbols.
        truncation=TruncationV1(
            applied=False,
            limit=len(records),
            returned_count=len(records),
            total_known_count=len(records),
            has_more=False,
        ),
    )



def invoke_stage10_cross_sectional_analytics_v21(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Add bounded breadth and trailing risk statistics to explicit symbols."""

    if name != TOOL_NAME:
        raise LookupError("Stage 10 cross-sectional operation is not registered")
    validated = _analytics_arguments(arguments)
    source_work = len(validated.tickers) * (
        _AS_OF_READ_BOUND_PER_TICKER
        if validated.mode == "as_of"
        else validated.limit + 1
    )
    analysis_work = len(validated.tickers) * validated.window * 3
    context.checkpoint()
    context.budget.require(
        rows=len(validated.tickers),
        series=1,
        operations=source_work + analysis_work,
    )
    repository = Stage10DailyPriceRepository(context.store_map, registry)
    coverage: list[_Coverage] = []
    series_by_ticker: dict[str, Any] = {}
    lineage: list[LineageRef] = []
    source_warning_codes: set[str] = set()
    for ticker in validated.tickers:
        context.checkpoint()
        query = Stage10DailyPriceQuery(
            identifier=ticker,
            identifier_kind="provider_symbol",
            start_date=validated.start_date,
            end_date=validated.end_date,
            mode=validated.mode,
            as_of=validated.as_of,
            date_only_policy=validated.date_only_policy,
            limit=validated.limit,
        )
        try:
            series = repository.get_close_series(query)
        except ValidationError as error:
            if not _is_unavailable(error):
                raise
            coverage.append(
                _Coverage(
                    ticker=ticker,
                    asset_type=None,
                    instrument_id=None,
                    observation_count=0,
                    first_period=None,
                    last_period=None,
                    first_close=None,
                    last_close=None,
                    cumulative_return=None,
                    coverage_status="unavailable",
                    source_lineage_digest=None,
                    source_truncated=False,
                )
            )
            continue
        source_warning_codes.update(series.warnings)
        item = _coverage_for_series(ticker, series)
        coverage.append(item)
        series_by_ticker[ticker] = series
        assert item.source_lineage_digest is not None
        lineage.append(
            LineageRef(
                dataset_id=DATASET_ID,
                store_role="market",
                semantic_id=item.source_lineage_digest,
            )
        )
    ranked = _rank(coverage)
    coverage_by_ticker = {item.ticker: item for item in ranked}
    benchmark = coverage_by_ticker[validated.benchmark_ticker]
    benchmark_series = series_by_ticker.get(validated.benchmark_ticker)
    record_mappings: dict[str, dict[str, str | Decimal | int | bool | None]] = {}
    for item in ranked:
        context.checkpoint()
        record_mappings[item.ticker] = _analytics_record_fields(
            item,
            series=series_by_ticker.get(item.ticker),
            benchmark=benchmark,
            benchmark_series=benchmark_series,
            window=validated.window,
        )
    records = tuple(
        RecordV1(
            record_type="market_cross_sectional_performance",
            fields=fields_from_mapping(record_mappings[item.ticker]),
        )
        for item in ranked
    )
    status_counts = {
        status: sum(item.coverage_status == status for item in ranked)
        for status in (
            "complete",
            "insufficient_observations",
            "truncated",
            "unavailable",
            "zero_base_price",
        )
    }
    cutoff = (
        None
        if validated.as_of is None
        else TemporalValue.parse(validated.as_of, pointer="/as_of")
    )
    point_in_time_status, unsafe_reasons = _point_in_time_selection(
        mode=validated.mode,
        source_warning_codes=tuple(source_warning_codes),
    )
    selected_identities = [
        {
            "asset_type": item.asset_type,
            "instrument_id": item.instrument_id,
            "ticker": item.ticker,
        }
        for item in coverage
        if item.instrument_id is not None
    ]
    breadth = _market_breadth(ranked)
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="market_cross_sectional_analytics_summary",
                message=(
                    "Breadth uses only complete explicit symbols. Trailing metrics "
                    "use observed, unfilled daily simple returns and report their "
                    "own history or variance status."
                ),
                metrics=fields_from_mapping(
                    {
                        **breadth,
                        "actual_mode": validated.mode,
                        "analysis_operation_bound": analysis_work,
                        "availability_basis": "local_capture",
                        "benchmark_relative_return_definition": (
                            "cumulative_return_minus_benchmark_cumulative_return_"
                            "with_matching_observed_endpoints"
                        ),
                        "benchmark_ticker": validated.benchmark_ticker,
                        "complete_count": status_counts["complete"],
                        "cutoff": None if cutoff is None else cutoff.raw,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": validated.date_only_policy,
                        "insufficient_observation_count": status_counts[
                            "insufficient_observations"
                        ],
                        "mode": validated.mode,
                        "point_in_time_status": point_in_time_status,
                        "realized_volatility_definition": (
                            "sample_standard_deviation_of_last_window_daily_simple_"
                            "returns_times_sqrt_252"
                        ),
                        "realized_volatility_zero_variance_count": sum(
                            fields["realized_volatility_status"] == "zero_variance"
                            for fields in record_mappings.values()
                        ),
                        "requested_end_date": validated.end_date,
                        "requested_mode": validated.mode,
                        "requested_start_date": validated.start_date,
                        "requested_tickers": dumps_strict(list(validated.tickers)),
                        "rolling_beta_definition": (
                            "sample_covariance_divided_by_sample_benchmark_variance_"
                            "over_last_window_aligned_daily_simple_returns"
                        ),
                        "rolling_beta_zero_variance_count": sum(
                            fields["rolling_beta_status"] == "zero_variance"
                            for fields in record_mappings.values()
                        ),
                        "source_limit_per_ticker": validated.limit,
                        "source_series_warning_count": len(source_warning_codes),
                        "source_total_observation_bound": source_work,
                        "ticker_count": len(validated.tickers),
                        "truncated_count": status_counts["truncated"],
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                        "window": validated.window,
                        "zero_base_count": status_counts["zero_base_price"],
                    }
                ),
            ),
        ),
        warnings=_warnings(
            ranked,
            mode=validated.mode,
            source_warning_codes=tuple(source_warning_codes),
        ),
        lineage=tuple(lineage),
        truncation=TruncationV1(
            applied=False,
            limit=len(records),
            returned_count=len(records),
            total_known_count=len(records),
            has_more=False,
        ),
    )


__all__ = (
    "MAX_AS_OF_SOURCE_CANDIDATES",
    "MAX_ROLLING_WINDOW",
    "MAX_TICKERS",
    "OPERATION_VERSION",
    "OPERATION_VERSION_V21",
    "TOOL_NAME",
    "invoke_stage10_cross_sectional_analytics_v21",
    "invoke_stage10_cross_sectional_performance",
)
