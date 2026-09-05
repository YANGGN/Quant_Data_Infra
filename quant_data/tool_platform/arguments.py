"""Immutable typed input contracts for the Stage 5 tool platform.

Public JSON remains a boundary representation.  This module is the single
source for the typed non-legacy input shapes: the dataclass field declarations
drive generated schemas, and the same declarations are used to construct the
immutable mappings passed to operations.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, ClassVar

from quant_data.contracts import TimeSeries
from quant_data.errors import Issue, ValidationError
from quant_data.json_codec import ensure_number_within_limits
from quant_data.temporal import TemporalValue, parse_date


Scalar = str | int | Decimal | bool | None


@dataclass(frozen=True, slots=True)
class _InputField:
    """Schema-visible constraints declared beside one typed dataclass field."""

    types: tuple[str, ...] = ()
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    enum: tuple[str, ...] = ()
    item: _InputField | None = None
    form: str = "scalar"


def _typed_field(contract: _InputField, **kwargs: Any) -> Any:
    """Attach one immutable input declaration to a dataclass field."""

    return dataclasses.field(metadata={"schema": contract}, **kwargs)


class _ArgumentMapping(Mapping[str, Any]):
    """A frozen dataclass that retains the ordinary Mapping operation API."""

    __slots__ = ()

    def __getitem__(self, key: str) -> Any:
        if key not in self._field_names():
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._field_names())

    def __len__(self) -> int:
        return len(self._field_names())

    @classmethod
    def _field_names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in dataclasses.fields(cls))


@dataclass(frozen=True, slots=True)
class ArgumentParameter(_ArgumentMapping):
    """One immutable scalar parameter accepted by a composable operation."""

    name: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=100)
    )
    value: Scalar = _typed_field(
        _InputField(types=("string", "number", "integer", "boolean", "null"))
    )


@dataclass(frozen=True, slots=True)
class SearchArguments(_ArgumentMapping):
    """Typed contract for a bounded store-backed search."""

    INPUT_KIND: ClassVar[str] = "search"

    query: str = _typed_field(_InputField(types=("string",), max_length=500))
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=500)
    )


@dataclass(frozen=True, slots=True)
class CurrentNewsSearchArgumentsV2(_ArgumentMapping):
    """Search the retained current FMP stock-news feed."""

    INPUT_KIND: ClassVar[str] = "current_news_search_v2"

    query: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            max_length=500,
        ),
        default="",
    )
    symbols: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=64),
            form="array",
        ),
        default=(),
    )
    mode: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("latest", "as_of"),
        ),
        default="latest",
    )
    as_of: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("completed_date", "calendar_date_inclusive"),
        ),
        default="completed_date",
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=500,
        ),
        default=100,
    )


@dataclass(frozen=True, slots=True)
class CurrentNewsSearchArgumentsV21(CurrentNewsSearchArgumentsV2):
    """Search retained current news across the approved source set."""

    INPUT_KIND: ClassVar[str] = "current_news_search_v2_1"

    source_ids: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=8,
            item=_InputField(types=("string",), min_length=1, max_length=64),
            form="array",
        ),
        default=(),
    )


_CURRENT_NEWS_SOURCE_IDS = (
    "fmp_stock_latest",
    "fmp_press_releases",
    "fmp_general",
    "fed_press",
    "ecb_press",
    "bea_news",
    "eia_press",
    "alpaca_benzinga",
)


@dataclass(frozen=True, slots=True)
class CurrentNewsSearchArgumentsV22(CurrentNewsSearchArgumentsV21):
    """Paginate retained current news across the fixed source set."""

    INPUT_KIND: ClassVar[str] = "current_news_search_v2_2"

    source_ids: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=8,
            item=_InputField(
                types=("string",),
                min_length=1,
                max_length=64,
                enum=_CURRENT_NEWS_SOURCE_IDS,
            ),
            form="array",
        ),
        default=(),
    )
    cursor: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            required=False,
            max_length=1_024,
        ),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class NewsSourceStatusArgumentsV1(_ArgumentMapping):
    """Read deterministic retained status for the fixed news sources."""

    INPUT_KIND: ClassVar[str] = "news_source_status_v1"

    source_ids: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=8,
            item=_InputField(
                types=("string",),
                min_length=1,
                max_length=64,
                enum=_CURRENT_NEWS_SOURCE_IDS,
            ),
            form="array",
        ),
        default=(),
    )


_DATA_STATUS_STORES = ("market", "macro", "company", "news")
_DATA_STATUS_VALUES = ("current", "stale", "no_data", "unknown")
_OPTIONS_V2_UNDERLYINGS = (
    "SPY", "QQQ", "IWM", "DIA", "XLB", "XLC", "XLE", "XLF",
    "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
_OPTIONS_V2_TARGET_DTES = frozenset({1, 2, 3, 7, 14, 30, 60, 90, 180, 365})


@dataclass(frozen=True, slots=True)
class DatasetStatusArgumentsV1(_ArgumentMapping):
    """Filter retained dataset/control-plane status without probing providers."""

    INPUT_KIND: ClassVar[str] = "dataset_status_v1"

    stores: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=4,
            item=_InputField(types=("string",), enum=_DATA_STATUS_STORES),
            form="array",
        ),
        default=(),
    )
    dataset_ids: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=128,
            item=_InputField(types=("string",), min_length=1, max_length=256),
            form="array",
        ),
        default=(),
    )
    statuses: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=4,
            item=_InputField(types=("string",), enum=_DATA_STATUS_VALUES),
            form="array",
        ),
        default=(),
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=128
        ),
        default=128,
    )


@dataclass(frozen=True, slots=True)
class OptionsCaptureSearchArgumentsV2(_ArgumentMapping):
    """Search retained fixed-universe Alpaca option captures."""

    INPUT_KIND: ClassVar[str] = "options_capture_search_v2"

    underlying_symbols: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=15,
            item=_InputField(types=("string",), enum=_OPTIONS_V2_UNDERLYINGS),
            form="array",
        ),
        default=(),
    )
    mode: str = _typed_field(
        _InputField(types=("string",), required=False, enum=("latest", "as_of")),
        default="latest",
    )
    as_of: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("completed_date", "calendar_date_inclusive"),
        ),
        default="completed_date",
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), required=False, minimum=1, maximum=500),
        default=100,
    )


@dataclass(frozen=True, slots=True)
class OptionsContractSearchArgumentsV2(_ArgumentMapping):
    """Search standard contracts from one retained Alpaca underlying cohort."""

    INPUT_KIND: ClassVar[str] = "options_contract_search_v2"

    underlying_symbol: str = _typed_field(
        _InputField(types=("string",), enum=_OPTIONS_V2_UNDERLYINGS)
    )
    query: str = _typed_field(
        _InputField(types=("string",), required=False, max_length=256), default=""
    )
    expiration_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    option_type: str | None = _typed_field(
        _InputField(
            types=("string", "null"), required=False, enum=("call", "put")
        ),
        default=None,
    )
    mode: str = _typed_field(
        _InputField(types=("string",), required=False, enum=("latest", "as_of")),
        default="latest",
    )
    as_of: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("completed_date", "calendar_date_inclusive"),
        ),
        default="completed_date",
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), required=False, minimum=1, maximum=2_000),
        default=100,
    )


@dataclass(frozen=True, slots=True)
class OptionsSurfaceSnapshotArgumentsV2(_ArgumentMapping):
    """Read one coherent retained Alpaca surface capture."""

    INPUT_KIND: ClassVar[str] = "options_surface_snapshot_v2"

    underlying_symbol: str = _typed_field(
        _InputField(types=("string",), enum=_OPTIONS_V2_UNDERLYINGS)
    )
    capture_id: str | None = _typed_field(
        _InputField(
            types=("string", "null"), required=False, min_length=1, max_length=256
        ),
        default=None,
    )
    target_dte: int | None = _typed_field(
        _InputField(types=("integer", "null"), required=False, minimum=1, maximum=365),
        default=None,
    )
    expiration_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    option_type: str | None = _typed_field(
        _InputField(
            types=("string", "null"), required=False, enum=("call", "put")
        ),
        default=None,
    )
    surface_state: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            required=False,
            enum=("present", "missing", "excluded"),
        ),
        default=None,
    )
    mode: str = _typed_field(
        _InputField(types=("string",), required=False, enum=("latest", "as_of")),
        default="latest",
    )
    as_of: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("completed_date", "calendar_date_inclusive"),
        ),
        default="completed_date",
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), required=False, minimum=1, maximum=5_000),
        default=500,
    )


@dataclass(frozen=True, slots=True)
class NewsItemHistoryArgumentsV1(_ArgumentMapping):
    """Read the bounded immutable version history for one current-news item."""

    INPUT_KIND: ClassVar[str] = "news_item_history_v1"

    article_id: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=500,
        ),
        default=100,
    )


@dataclass(frozen=True, slots=True)
class NewsAnalysisArgumentsV1(CurrentNewsSearchArgumentsV21):
    """Select bounded retained headlines for a deterministic news analysis."""

    INPUT_KIND: ClassVar[str] = "news_analysis_v1"

    source_ids: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=8,
            item=_InputField(
                types=("string",),
                min_length=1,
                max_length=64,
                enum=_CURRENT_NEWS_SOURCE_IDS,
            ),
            form="array",
        ),
        default=(),
    )


@dataclass(frozen=True, slots=True)
class NewsStoryClusterArgumentsV1(NewsAnalysisArgumentsV1):
    """Select headlines for deterministic candidate-story clustering."""

    INPUT_KIND: ClassVar[str] = "news_story_clusters_v1"

    window_hours: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=168,
        ),
        default=24,
    )


@dataclass(frozen=True, slots=True)
class NewsAttentionArgumentsV1(NewsAnalysisArgumentsV1):
    """Select headlines for deterministic bucketed attention metrics."""

    INPUT_KIND: ClassVar[str] = "news_attention_v1"

    bucket: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            enum=("hour", "day"),
        ),
        default="day",
    )
    baseline_periods: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=2,
            maximum=90,
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class NewsEventImpactArgumentsV1(_ArgumentMapping):
    """Bind one exact current-news version to one stable Stage 10 instrument."""

    INPUT_KIND: ClassVar[str] = "news_event_impact_v1"

    article_version_id: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    instrument_id: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    pre_observations: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=0,
            maximum=20,
        ),
        default=5,
    )
    post_observations: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=0,
            maximum=20,
        ),
        default=5,
    )


@dataclass(frozen=True, slots=True)
class CompanyFilingSearchArgumentsV2(_ArgumentMapping):
    """Typed keyset-paginated filing search over one exact SEC CIK."""

    INPUT_KIND: ClassVar[str] = "company_filing_search_v2"

    query: str = _typed_field(
        _InputField(types=("string",), min_length=10, max_length=10)
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    cursor: str | None = _typed_field(
        _InputField(types=("string", "null"), max_length=1_024)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=500)
    )



@dataclass(frozen=True, slots=True)
class CompanyShareCountHistoryArgumentsV2(_ArgumentMapping):
    """Read the reviewed SEC share-count metric family for one exact CIK."""

    INPUT_KIND: ClassVar[str] = "company_share_count_history_v2"

    cik: str = _typed_field(
        _InputField(types=("string",), min_length=10, max_length=10)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketInstrumentSearchArgumentsV2(_ArgumentMapping):
    """Search the retained current Stage 10 FMP instrument universe."""

    INPUT_KIND: ClassVar[str] = "stage10_market_instrument_search_v2"

    query: str = _typed_field(
        _InputField(
            types=("string",),
            required=False,
            max_length=64,
        ),
        default="",
    )
    asset_type: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            required=False,
            enum=("equity", "etf", "index"),
        ),
        default=None,
    )
    cursor: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            required=False,
            max_length=1_024,
        ),
        default=None,
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=100,
        ),
        default=100,
    )


@dataclass(frozen=True, slots=True)
class QueryArguments(_ArgumentMapping):
    """Typed contract for a generic registered query."""

    INPUT_KIND: ClassVar[str] = "query"

    identifiers: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=200),
            form="array",
        )
    )
    mode: str = _typed_field(
        _InputField(
            types=("string",), enum=("latest", "as_of", "first_release")
        )
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"))
    )
    end_date: str | None = _typed_field(_InputField(types=("string", "null")))
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10AvailableTickerArgumentsV1(_ArgumentMapping):
    """List current Stage 10 tickers that have retrievable price rows."""

    INPUT_KIND: ClassVar[str] = "stage10_available_ticker_v1"

    limit: int = _typed_field(
        _InputField(
            types=("integer",),
            required=False,
            minimum=1,
            maximum=10_000,
        ),
        default=10_000,
    )



@dataclass(frozen=True, slots=True)
class Stage10MarketPriceArgumentsV1(_ArgumentMapping):
    """Read one ticker's canonical Stage 10 OHLC price series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_price_v1"

    ticker: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketVolumeArgumentsV1(_ArgumentMapping):
    """Read one ticker's canonical Stage 10 provider-reported volume."""

    INPUT_KIND: ClassVar[str] = "stage10_market_volume_v1"

    ticker: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV2(_ArgumentMapping):
    """Calculate one explicit technical-indicator specification over OHLCV."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV21(_ArgumentMapping):
    """Add clustered SuperTrend AI to the typed OHLCV indicator contract."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV22(_ArgumentMapping):
    """Add the causal BOSWaves swing-structure forecast contract."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV23(_ArgumentMapping):
    """Add KDJ to the typed OHLCV indicator contract."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_3"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
                "kdj",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV24(_ArgumentMapping):
    """Add Williams Vix Fix and its percentile thresholds."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_4"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
                "kdj",
                "williams_vix_fix",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    percentile_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    percentile_high_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=1)
    )
    percentile_low_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=1, maximum=10)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV25(_ArgumentMapping):
    """Add the WaveTrend oscillator and signed cross events."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_5"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
                "kdj",
                "williams_vix_fix",
                "wavetrend_crosses",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    percentile_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    percentile_high_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=1)
    )
    percentile_low_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=1, maximum=10)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )



@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV26(_ArgumentMapping):
    """Add the Pine-compatible Parabolic SAR price overlay."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_6"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
                "kdj",
                "williams_vix_fix",
                "wavetrend_crosses",
                "parabolic_sar",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    percentile_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    percentile_high_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=1)
    )
    percentile_low_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=1, maximum=10)
    )
    start: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    increment: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    maximum: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )



@dataclass(frozen=True, slots=True)
class Stage10TechnicalIndicatorArgumentsV27(_ArgumentMapping):
    """Add a caller-windowed rolling ordinary-least-squares regression line."""

    INPUT_KIND: ClassVar[str] = "stage10_technical_indicator_v2_7"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=5, form="series_array")
    )
    indicator: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "sma",
                "ema",
                "rolling_regression_line",
                "rolling_standard_deviation",
                "rolling_z_score",
                "true_range",
                "average_true_range",
                "rate_of_change",
                "relative_strength_index",
                "macd",
                "bollinger_bands",
                "donchian_channels",
                "stochastic_oscillator",
                "average_directional_index",
                "on_balance_volume",
                "accumulation_distribution",
                "supertrend_ai",
                "swing_structure_forecast",
                "kdj",
                "williams_vix_fix",
                "wavetrend_crosses",
                "parabolic_sar",
            ),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    fast_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    slow_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    signal_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    standard_deviation_multiplier: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    minimum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    maximum_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    factor_step: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=100)
    )
    performance_memory: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=2, maximum=10_000)
    )
    cluster: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("best", "average", "worst"),
        )
    )
    sample_count: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=3, maximum=20)
    )
    aggregation_method: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("weighted", "average", "median"),
        )
    )
    percentile_window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=10_000)
    )
    percentile_high_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=0, maximum=1)
    )
    percentile_low_factor: Decimal | None = _typed_field(
        _InputField(types=("number", "null"), minimum=1, maximum=10)
    )
    start: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    increment: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    maximum: Decimal | None = _typed_field(
        _InputField(types=("number", "null"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class CanonicalMacroSearchArgumentsV2(_ArgumentMapping):
    """Search the current retained canonical macro catalog."""

    INPUT_KIND: ClassVar[str] = "canonical_macro_search_v2"

    query: str = _typed_field(
        _InputField(types=("string",), required=False, max_length=500),
        default="",
    )
    provider: str | None = _typed_field(
        _InputField(
            types=("string",), required=False, max_length=100
        ),
        default=None,
    )
    frequency: str | None = _typed_field(
        _InputField(
            types=("string",), required=False, max_length=100
        ),
        default=None,
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=500
        ),
        default=500,
    )


@dataclass(frozen=True, slots=True)
class CanonicalMacroDescribeArgumentsV2(_ArgumentMapping):
    """Describe one exact canonical macro series."""

    INPUT_KIND: ClassVar[str] = "canonical_macro_describe_v2"

    series_id: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=1
        ),
        default=1,
    )


@dataclass(frozen=True, slots=True)
class CanonicalMacroSeriesArgumentsV2(_ArgumentMapping):
    """Read one canonical generic or official-vintage macro series."""

    INPUT_KIND: ClassVar[str] = "canonical_macro_series_v2"

    series_id: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    mode: str = _typed_field(
        _InputField(
            types=("string",), enum=("latest", "as_of", "first_release")
        )
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class MacroReleaseCalendarArgumentsV1(_ArgumentMapping):
    """Read the retained FMP U.S. release calendar by local availability."""

    INPUT_KIND: ClassVar[str] = "macro_release_calendar_v1"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    event_name: str | None = _typed_field(
        _InputField(
            types=("string",), required=False, max_length=256
        ),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class MacroReleaseCalendarArgumentsV2(_ArgumentMapping):
    """Continue the retained release calendar with a query-bound cursor."""

    INPUT_KIND: ClassVar[str] = "macro_release_calendar_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    event_name: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=256),
        default=None,
    )
    cursor: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=2_048),
        default=None,
    )


@dataclass(frozen=True, slots=True)
class Stage10CrossSectionalPerformanceArgumentsV2(_ArgumentMapping):
    """Compare endpoint returns for an explicit bounded ticker list."""

    INPUT_KIND: ClassVar[str] = "stage10_cross_sectional_performance_v2"

    tickers: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            min_items=2,
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=32),
            form="array",
        )
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=2, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class Stage10CrossSectionalAnalyticsArgumentsV21(_ArgumentMapping):
    """Compute bounded breadth and benchmark-relative risk diagnostics."""

    INPUT_KIND: ClassVar[str] = "stage10_cross_sectional_analytics_v2_1"

    tickers: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            min_items=2,
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=32),
            form="array",
        )
    )
    benchmark_ticker: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=32)
    )
    window: int = _typed_field(
        _InputField(types=("integer",), minimum=2, maximum=252)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=3, maximum=10_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class EnergyElectricityRetailArgumentsV2(_ArgumentMapping):
    """Read one populated U.S. all-sector electricity retail metric."""

    INPUT_KIND: ClassVar[str] = "energy_electricity_retail_v2"

    metric: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("sales", "revenue", "price", "customers"),
        )
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=1_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class EnergyWeeklyFundamentalsArgumentsV2(_ArgumentMapping):
    """Read the populated weekly U.S. petroleum-stock series."""

    INPUT_KIND: ClassVar[str] = "energy_weekly_fundamentals_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class CompanyFundamentalsArgumentsV2(_ArgumentMapping):
    """Read normalized reviewed company facts without derived ratios."""

    INPUT_KIND: ClassVar[str] = "company_fundamentals_v2"

    cik: str = _typed_field(
        _InputField(types=("string",), min_length=10, max_length=10)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=1_000)
    )
    metric_codes: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            required=False,
            max_items=50,
            item=_InputField(types=("string",), min_length=1, max_length=200),
            form="array",
        ),
        default=(),
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class CompanyFundamentalRatiosArgumentsV21(_ArgumentMapping):
    """Compute only reviewed same-period SEC fundamental ratios."""

    INPUT_KIND: ClassVar[str] = "company_fundamental_ratios_v2_1"

    cik: str = _typed_field(
        _InputField(types=("string",), min_length=10, max_length=10)
    )
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    ratio_codes: tuple[str, ...] = _typed_field(
        _InputField(
            types=("array",),
            min_items=1,
            max_items=2,
            item=_InputField(
                types=("string",),
                enum=("net_margin", "liabilities_to_assets"),
            ),
            form="array",
        )
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=1_000)
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )


@dataclass(frozen=True, slots=True)
class MacroRevisionArgumentsV2(_ArgumentMapping):
    """Compare evidenced first releases with current retained latest vintages."""

    INPUT_KIND: ClassVar[str] = "macro_revision_v2"

    series_id: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "macro.gdp.real_qoq_saar_pct",
                "macro.gdp.nominal_billions",
                "macro.gdi.real_qoq_saar_pct",
                "macro.gdi.nominal_billions",
                "macro.bls.cpi_u_all_items_sa",
                "macro.bls.cpi_u_core_sa",
                "macro.bls.total_nonfarm_payrolls_sa",
                "macro.bls.unemployment_rate_sa",
                "macro.philadelphia_fed.nominal_output",
                "macro.philadelphia_fed.real_output",
            ),
        )
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=10_000
        ),
        default=500,
    )


@dataclass(frozen=True, slots=True)
class MacroSurpriseStandardizationArgumentsV2(_ArgumentMapping):
    """Standardize one reviewed release-surprise kind ex post."""

    INPUT_KIND: ClassVar[str] = "macro_surprise_standardization_v2"

    kind: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "us_gdp_real_qoq_saar_advance",
                "us_cpi_headline_mom",
                "us_cpi_headline_yoy",
                "us_cpi_core_mom",
                "us_cpi_core_yoy",
                "us_nonfarm_payrolls_change_thousands",
                "us_unemployment_rate",
            ),
        )
    )
    release_stage: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            required=False,
            enum=("advance", "initial", "second", "third"),
        ),
        default=None,
    )
    start_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    end_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=2, maximum=2_000
        ),
        default=500,
    )


@dataclass(frozen=True, slots=True)
class MacroObservedSnapshotArgumentsV2(_ArgumentMapping):
    """Select raw observed macro components with explicit availability mode."""

    INPUT_KIND: ClassVar[str] = "macro_observed_snapshot_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    observation_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=100
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class FundingConditionsArgumentsV2(_ArgumentMapping):
    """Select observed funding rates and an optional direct spread."""

    INPUT_KIND: ClassVar[str] = "funding_conditions_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    observation_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    spread_left: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=100),
        default=None,
    )
    spread_right: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=100),
        default=None,
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=100
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class CurveAnalyticsArgumentsV2(_ArgumentMapping):
    """Select observed Treasury tenors and an optional direct spread."""

    INPUT_KIND: ClassVar[str] = "curve_analytics_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    observation_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    spread_left_tenor: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=20),
        default=None,
    )
    spread_right_tenor: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False, max_length=20),
        default=None,
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=100
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class LiquidityImpulseArgumentsV2(_ArgumentMapping):
    """Compare raw liquidity components between two explicit period bounds."""

    INPUT_KIND: ClassVar[str] = "liquidity_impulse_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    start_date: str = _typed_field(_InputField(types=("string",)))
    end_date: str = _typed_field(_InputField(types=("string",)))
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=100
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class MacroRegimeArgumentsV2(_ArgumentMapping):
    """Read direct NBER state and optionally raw contextual components."""

    INPUT_KIND: ClassVar[str] = "macro_regime_v2"

    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    observation_date: str | None = _typed_field(
        _InputField(types=("string", "null"), required=False), default=None
    )
    include_context: bool = _typed_field(
        _InputField(types=("boolean",), required=False), default=False
    )
    limit: int = _typed_field(
        _InputField(
            types=("integer",), required=False, minimum=1, maximum=100
        ),
        default=20,
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketReturnArgumentsV2(_ArgumentMapping):
    """Typed v2 contract for one Stage 10 close-to-close return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_return_v2"

    identifier: str = _typed_field(
        _InputField(types=("string",), min_length=1, max_length=200)
    )
    identifier_kind: str = _typed_field(
        _InputField(
            types=("string",), enum=("instrument_id", "provider_symbol")
        )
    )
    start_date: str = _typed_field(_InputField(types=("string",)))
    end_date: str = _typed_field(_InputField(types=("string",)))
    mode: str = _typed_field(
        _InputField(types=("string",), enum=("latest", "as_of"))
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    date_only_policy: str = _typed_field(
        _InputField(
            types=("string",),
            enum=("completed_date", "calendar_date_inclusive"),
        )
    )
    method: str = _typed_field(
        _InputField(types=("string",), enum=("simple", "log"))
    )
    horizon: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=252)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketDescribeArgumentsV2(_ArgumentMapping):
    """Describe one exact Stage 10 market-return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_describe_v2"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketAlignArgumentsV2(_ArgumentMapping):
    """Align one to twenty compatible Stage 10 market-return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_align_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    join: str = _typed_field(
        _InputField(types=("string",), enum=("inner", "outer"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketCorrelationArgumentsV2(_ArgumentMapping):
    """Correlate exactly two compatible, complete Stage 10 return samples."""

    INPUT_KIND: ClassVar[str] = "stage10_market_correlation_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=2, form="series_array")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10DataQualityArgumentsV2(_ArgumentMapping):
    """Audit one to twenty supplied Stage 10 return-series contracts."""

    INPUT_KIND: ClassVar[str] = "stage10_data_quality_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketTransformArgumentsV2(_ArgumentMapping):
    """Run one explicit statistic or drawdown transform over trailing returns."""

    INPUT_KIND: ClassVar[str] = "stage10_market_transform_v2"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    operation: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "rolling_statistic",
                "autocorrelation",
                "partial_autocorrelation",
                "ljung_box",
                "drawdown_episodes",
            ),
        )
    )
    rolling_statistic: str | None = _typed_field(
        _InputField(
            types=("string", "null"),
            enum=("mean", "sample_standard_deviation", "minimum", "maximum"),
        )
    )
    window: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=2, maximum=252)
    )
    max_lag: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=252)
    )
    ljung_box_lag: int | None = _typed_field(
        _InputField(types=("integer", "null"), minimum=1, maximum=252)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10DistributionArgumentsV1(_ArgumentMapping):
    """Describe one complete trailing Stage 10 return distribution."""

    INPUT_KIND: ClassVar[str] = "stage10_distribution_v1"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10BootstrapArgumentsV1(_ArgumentMapping):
    """Build a deterministic IID percentile interval for one statistic."""

    INPUT_KIND: ClassVar[str] = "stage10_bootstrap_v1"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    statistic: str = _typed_field(
        _InputField(types=("string",), enum=("mean", "median"))
    )
    seed: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=4_294_967_295)
    )
    replicates: int = _typed_field(
        _InputField(types=("integer",), minimum=100, maximum=5_000)
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.90", "0.95", "0.99"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10CovarianceArgumentsV1(_ArgumentMapping):
    """Estimate sample covariance/correlation over joint-complete returns."""

    INPUT_KIND: ClassVar[str] = "stage10_covariance_v1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10PrincipalComponentsArgumentsV1(_ArgumentMapping):
    """Run deterministic PCA over joint-complete trailing returns."""

    INPUT_KIND: ClassVar[str] = "stage10_principal_components_v1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    basis: str = _typed_field(
        _InputField(types=("string",), enum=("covariance", "correlation"))
    )
    components: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=20)
    )
    include_scores: bool = _typed_field(_InputField(types=("boolean",)))
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionArgumentsV2(_ArgumentMapping):
    """Fit contemporaneous OLS to compatible trailing Stage 10 returns."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(types=("string",), enum=("classical_homoskedastic",))
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRollingRegressionArgumentsV2(_ArgumentMapping):
    """Fit the same OLS kernel over fixed contiguous return windows."""

    INPUT_KIND: ClassVar[str] = "stage10_market_rolling_regression_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    window: int = _typed_field(
        _InputField(types=("integer",), minimum=3, maximum=5_000)
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(types=("string",), enum=("classical_homoskedastic",))
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )

@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionArgumentsV21(_ArgumentMapping):
    """Fit OLS with explicit robust inference and residual diagnostics."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_v2_1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "classical_homoskedastic",
                "hc1",
                "hc3",
                "newey_west_hac_bartlett",
            ),
        )
    )
    hac_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    diagnostic_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=18)
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRollingRegressionArgumentsV21(_ArgumentMapping):
    """Fit robust OLS and diagnostics over fixed contiguous windows."""

    INPUT_KIND: ClassVar[str] = "stage10_market_rolling_regression_v2_1"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    window: int = _typed_field(
        _InputField(types=("integer",), minimum=3, maximum=5_000)
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    covariance: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "classical_homoskedastic",
                "hc1",
                "hc3",
                "newey_west_hac_bartlett",
            ),
        )
    )
    hac_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    diagnostic_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=18)
    )
    confidence_level: str = _typed_field(
        _InputField(types=("string",), enum=("0.95",))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )



@dataclass(frozen=True, slots=True)
class Stage10MarketStationarityArgumentsV2(_ArgumentMapping):
    """Run one fixed-lag constant-only ADF test over trailing returns."""

    INPUT_KIND: ClassVar[str] = "stage10_market_stationarity_v2"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketStationarityArgumentsV21(_ArgumentMapping):
    """Run fixed-lag ADF and level-KPSS over one trailing return series."""

    INPUT_KIND: ClassVar[str] = "stage10_market_stationarity_v2_1"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    adf_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    kpss_lag: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=18)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketStructuralBreakArgumentsV2(_ArgumentMapping):
    """Run a Chow test at one caller-declared first post-break row."""

    INPUT_KIND: ClassVar[str] = "stage10_market_structural_breaks_v2"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=20, form="series_array")
    )
    intercept: bool = _typed_field(_InputField(types=("boolean",)))
    break_index: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=4_999)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class Stage10MarketRegressionModelSuiteArgumentsV3(_ArgumentMapping):
    """Run one explicit fixed-specification multivariate econometric model."""

    INPUT_KIND: ClassVar[str] = "stage10_market_regression_model_suite_v3"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=2, max_items=5, form="series_array")
    )
    analysis: str = _typed_field(
        _InputField(
            types=("string",),
            enum=(
                "engle_granger_cointegration",
                "vector_autoregression",
                "granger_causality",
            ),
        )
    )
    deterministic: str = _typed_field(
        _InputField(types=("string",), enum=("constant",))
    )
    lag_order: int = _typed_field(
        _InputField(types=("integer",), minimum=0, maximum=4)
    )
    significance: str = _typed_field(
        _InputField(types=("string",), enum=("0.01", "0.05", "0.10"))
    )
    source_index: int = _typed_field(
        _InputField(types=("integer",), minimum=-1, maximum=4)
    )
    target_index: int = _typed_field(
        _InputField(types=("integer",), minimum=-1, maximum=4)
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=5_000)
    )


@dataclass(frozen=True, slots=True)
class SingleSeriesArguments(_ArgumentMapping):
    """Typed contract for one directly composable ``TimeSeries`` input."""

    INPUT_KIND: ClassVar[str] = "single_series"

    series: TimeSeries = _typed_field(_InputField(form="series"))
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class MultiSeriesArguments(_ArgumentMapping):
    """Typed contract for a bounded sequence of composable ``TimeSeries`` values."""

    INPUT_KIND: ClassVar[str] = "multi_series"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )


@dataclass(frozen=True, slots=True)
class ResearchSeriesArguments(_ArgumentMapping):
    """Typed multi-series contract with explicit research safety inputs."""

    INPUT_KIND: ClassVar[str] = "research"

    series: tuple[TimeSeries, ...] = _typed_field(
        _InputField(min_items=1, max_items=20, form="series_array")
    )
    parameters: tuple[ArgumentParameter, ...] = _typed_field(
        _InputField(types=("array",), max_items=50, form="parameters")
    )
    limit: int = _typed_field(
        _InputField(types=("integer",), minimum=1, maximum=10_000)
    )
    as_of: str | None = _typed_field(_InputField(types=("string", "null")))
    unsafe_ok: bool = _typed_field(_InputField(types=("boolean",)))


_ARGUMENT_TYPES: tuple[type[_ArgumentMapping], ...] = (
    SearchArguments,
    CurrentNewsSearchArgumentsV2,
    CurrentNewsSearchArgumentsV21,
    CurrentNewsSearchArgumentsV22,
    NewsSourceStatusArgumentsV1,
    DatasetStatusArgumentsV1,
    OptionsCaptureSearchArgumentsV2,
    OptionsContractSearchArgumentsV2,
    OptionsSurfaceSnapshotArgumentsV2,
    NewsItemHistoryArgumentsV1,
    NewsAnalysisArgumentsV1,
    NewsStoryClusterArgumentsV1,
    NewsAttentionArgumentsV1,
    NewsEventImpactArgumentsV1,
    CompanyFilingSearchArgumentsV2,
    CompanyShareCountHistoryArgumentsV2,
    Stage10MarketInstrumentSearchArgumentsV2,
    QueryArguments,
    Stage10AvailableTickerArgumentsV1,
    Stage10MarketPriceArgumentsV1,
    Stage10MarketVolumeArgumentsV1,
    Stage10TechnicalIndicatorArgumentsV2,
    Stage10TechnicalIndicatorArgumentsV21,
    Stage10TechnicalIndicatorArgumentsV22,
    Stage10TechnicalIndicatorArgumentsV23,
    Stage10TechnicalIndicatorArgumentsV25,
    Stage10TechnicalIndicatorArgumentsV26,
    Stage10TechnicalIndicatorArgumentsV27,
    Stage10TechnicalIndicatorArgumentsV24,
    CanonicalMacroSearchArgumentsV2,
    CanonicalMacroDescribeArgumentsV2,
    CanonicalMacroSeriesArgumentsV2,
    MacroReleaseCalendarArgumentsV1,
    MacroReleaseCalendarArgumentsV2,
    Stage10CrossSectionalPerformanceArgumentsV2,
    Stage10CrossSectionalAnalyticsArgumentsV21,
    EnergyElectricityRetailArgumentsV2,
    EnergyWeeklyFundamentalsArgumentsV2,
    CompanyFundamentalsArgumentsV2,
    CompanyFundamentalRatiosArgumentsV21,
    MacroRevisionArgumentsV2,
    MacroSurpriseStandardizationArgumentsV2,
    MacroObservedSnapshotArgumentsV2,
    FundingConditionsArgumentsV2,
    CurveAnalyticsArgumentsV2,
    LiquidityImpulseArgumentsV2,
    MacroRegimeArgumentsV2,
    Stage10MarketReturnArgumentsV2,
    Stage10MarketDescribeArgumentsV2,
    Stage10MarketAlignArgumentsV2,
    Stage10MarketCorrelationArgumentsV2,
    Stage10DataQualityArgumentsV2,
    Stage10MarketTransformArgumentsV2,
    Stage10DistributionArgumentsV1,
    Stage10BootstrapArgumentsV1,
    Stage10CovarianceArgumentsV1,
    Stage10PrincipalComponentsArgumentsV1,
    Stage10MarketRegressionArgumentsV2,
    Stage10MarketRollingRegressionArgumentsV2,
    Stage10MarketRegressionArgumentsV21,
    Stage10MarketRollingRegressionArgumentsV21,
    Stage10MarketStationarityArgumentsV2,
    Stage10MarketStationarityArgumentsV21,
    Stage10MarketStructuralBreakArgumentsV2,
    Stage10MarketRegressionModelSuiteArgumentsV3,
    SingleSeriesArguments,
    MultiSeriesArguments,
    ResearchSeriesArguments,
)


@dataclass(frozen=True, slots=True)
class _PreparedArguments:
    """Schema-shaped, decoder-free material validated before typed construction."""

    argument_type: type[_ArgumentMapping]
    values: Mapping[str, Any]
    raw_series: tuple[Any, ...] = ()
    source_rows: int = 0


def _argument_type_for(input_kind: str) -> type[_ArgumentMapping]:
    if not isinstance(input_kind, str):
        raise ValidationError("Tool input kind must be a string")
    for argument_type in _ARGUMENT_TYPES:
        if argument_type.INPUT_KIND == input_kind:
            return argument_type
    raise ValidationError("Unsupported Tool Platform input kind")


def _declared_fields(
    argument_type: type[_ArgumentMapping],
) -> tuple[tuple[dataclasses.Field[Any], _InputField], ...]:
    result: list[tuple[dataclasses.Field[Any], _InputField]] = []
    for declared in dataclasses.fields(argument_type):
        contract = declared.metadata.get("schema")
        if not isinstance(contract, _InputField):  # pragma: no cover - module invariant
            raise AssertionError(f"{argument_type.__name__}.{declared.name} lacks a schema contract")
        result.append((declared, contract))
    return tuple(result)


def _field_contract(
    argument_type: type[_ArgumentMapping], name: str
) -> _InputField:
    for declared, contract in _declared_fields(argument_type):
        if declared.name == name:
            return contract
    raise AssertionError(f"{argument_type.__name__} has no {name!r} field")


def _copy_series_schema(series_schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(series_schema, Mapping):
        raise ValidationError("TimeSeries schema must be an object")
    return copy.deepcopy(dict(series_schema))


def _schema_for_field(
    contract: _InputField, series_schema: Mapping[str, Any]
) -> dict[str, Any]:
    if contract.form == "series":
        return _copy_series_schema(series_schema)
    if contract.form == "series_array":
        schema: dict[str, Any] = {
            "type": "array",
            "items": _copy_series_schema(series_schema),
        }
        if contract.min_items is not None:
            schema["minItems"] = contract.min_items
        if contract.max_items is not None:
            schema["maxItems"] = contract.max_items
        return schema
    if contract.form == "parameters":
        schema = {
            "type": "array",
            "items": _object_schema(ArgumentParameter, series_schema),
        }
        if contract.max_items is not None:
            schema["maxItems"] = contract.max_items
        return schema

    schema = {
        "type": contract.types[0]
        if len(contract.types) == 1
        else list(contract.types)
    }
    if contract.minimum is not None:
        schema["minimum"] = contract.minimum
    if contract.maximum is not None:
        schema["maximum"] = contract.maximum
    if contract.min_length is not None:
        schema["minLength"] = contract.min_length
    if contract.max_length is not None:
        schema["maxLength"] = contract.max_length
    if contract.min_items is not None:
        schema["minItems"] = contract.min_items
    if contract.max_items is not None:
        schema["maxItems"] = contract.max_items
    if contract.enum:
        schema["enum"] = list(contract.enum)
        if "null" in contract.types:
            schema["enum"].append(None)
    if contract.form == "array":
        if contract.item is None:  # pragma: no cover - module invariant
            raise AssertionError("Array field lacks an item declaration")
        schema["items"] = _schema_for_field(contract.item, series_schema)
    return schema


def _object_schema(
    argument_type: type[_ArgumentMapping], series_schema: Mapping[str, Any]
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for declared, contract in _declared_fields(argument_type):
        properties[declared.name] = _schema_for_field(contract, series_schema)
        if contract.required:
            required.append(declared.name)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def input_schema(input_kind: str, series_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Generate one public JSON Schema from its immutable typed declaration.

    ``series_schema`` is deliberately supplied by the registry's typed
    ``TimeSeries`` contract.  It is copied into the composed schema without
    re-declaring (or weakening) its observation bound here.
    """

    return _object_schema(_argument_type_for(input_kind), series_schema)


def _validation_error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError("Tool arguments are invalid", issues=(Issue(pointer, rule, message),))


def _require_mapping(value: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _validation_error(pointer, "type", "Expected an object")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    argument_type: type[_ArgumentMapping],
    pointer: str,
) -> None:
    declared = _declared_fields(argument_type)
    expected = {field.name for field, _ in declared}
    required = {field.name for field, contract in declared if contract.required}
    actual = set(value)
    missing = required - actual
    extra = actual - expected
    issues: list[Issue] = []
    for name in sorted(missing, key=str):
        issues.append(Issue(f"{pointer}/{name}", "required", "Required field is missing"))
    for name in sorted(extra, key=str):
        suffix = str(name) if isinstance(name, str) else "field"
        issues.append(Issue(f"{pointer}/{suffix}", "additional_properties", "Unknown field"))
    if issues:
        raise ValidationError("Tool arguments are invalid", issues=issues)


def _validated_string(value: Any, contract: _InputField, pointer: str) -> str:
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected a string")
    if contract.min_length is not None and len(value) < contract.min_length:
        raise _validation_error(pointer, "min_length", "String is too short")
    if contract.max_length is not None and len(value) > contract.max_length:
        raise _validation_error(pointer, "max_length", "String exceeds the supported length")
    return value


def _validated_limit(value: Any, contract: _InputField, pointer: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _validation_error(pointer, "type", "Expected an integer")
    if contract.minimum is not None and value < contract.minimum:
        raise _validation_error(pointer, "minimum", "Number is below the supported limit")
    if contract.maximum is not None and value > contract.maximum:
        raise _validation_error(pointer, "maximum", "Number exceeds the supported limit")
    return value


def _validated_optional_limit(
    value: Any, contract: _InputField, pointer: str
) -> int | None:
    if value is None:
        return None
    return _validated_limit(value, contract, pointer)


def _validated_scalar(value: Any, pointer: str) -> Scalar:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        ensure_number_within_limits(value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _validation_error(pointer, "finite", "Numeric values must be finite")
        ensure_number_within_limits(value)
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _validation_error(pointer, "finite", "Numeric values must be finite")
        ensure_number_within_limits(value)
        return value
    raise _validation_error(pointer, "type", "Expected a JSON scalar value")


def _optional_temporal(value: Any, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected an ISO date or aware datetime")
    TemporalValue.parse(value, pointer=pointer)
    return value


def _optional_calendar_date(value: Any, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation_error(pointer, "type", "Expected a calendar date")
    parse_date(value, pointer=pointer)
    return value


def _optional_bounded_string(
    value: Any, contract: _InputField, pointer: str
) -> str | None:
    if value is None:
        return None
    return _validated_string(value, contract, pointer)


def _validated_string_array(
    value: Any,
    contract: _InputField,
    pointer: str,
    *,
    normalize_upper: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _validation_error(pointer, "type", "Expected an array")
    if contract.max_items is not None and len(value) > contract.max_items:
        raise _validation_error(
            pointer, "max_items", "Array exceeds the supported item limit"
        )
    assert contract.item is not None
    items = tuple(
        _validated_string(item, contract.item, f"{pointer}/{index}").strip()
        for index, item in enumerate(value)
    )
    if normalize_upper:
        items = tuple(item.upper() for item in items)
    if any(not item for item in items):
        raise _validation_error(
            pointer, "min_length", "Array values cannot be blank"
        )
    if contract.item.enum and any(item not in contract.item.enum for item in items):
        raise _validation_error(pointer, "enum", "Array contains an unsupported value")
    if len(items) != len(set(items)):
        raise _validation_error(pointer, "unique", "Array values must be unique")
    return items


def _validated_parameters(
    value: Any, argument_type: type[_ArgumentMapping], pointer: str
) -> tuple[ArgumentParameter, ...]:
    contract = _field_contract(argument_type, "parameters")
    if not isinstance(value, (list, tuple)):
        raise _validation_error(pointer, "type", "Expected an array")
    if contract.max_items is not None and len(value) > contract.max_items:
        raise _validation_error(pointer, "max_items", "Array exceeds the supported item limit")

    names: set[str] = set()
    parameters: list[ArgumentParameter] = []
    name_contract = _field_contract(ArgumentParameter, "name")
    for index, item in enumerate(value):
        item_pointer = f"{pointer}/{index}"
        mapping = _require_mapping(item, item_pointer)
        _require_exact_fields(mapping, ArgumentParameter, item_pointer)
        name = _validated_string(mapping["name"], name_contract, f"{item_pointer}/name")
        if not name.strip():
            raise _validation_error(
                f"{item_pointer}/name", "min_length", "Parameter names cannot be blank"
            )
        if name in names:
            raise _validation_error(
                f"{item_pointer}/name", "unique", "Parameter names must be unique"
            )
        names.add(name)
        parameters.append(
            ArgumentParameter(
                name=name,
                value=_validated_scalar(mapping["value"], f"{item_pointer}/value"),
            )
        )
    return tuple(parameters)


def _validated_series_material(
    value: Any,
    contract: _InputField,
    pointer: str,
) -> tuple[Any, ...]:
    many = contract.form == "series_array"
    if many:
        if not isinstance(value, (list, tuple)):
            raise _validation_error(pointer, "type", "Expected an array of TimeSeries values")
        if contract.min_items is not None and len(value) < contract.min_items:
            raise _validation_error(pointer, "min_items", "At least one TimeSeries is required")
        if contract.max_items is not None and len(value) > contract.max_items:
            raise _validation_error(pointer, "max_items", "Array exceeds the supported item limit")
        raw_series = tuple(value)
    else:
        raw_series = (value,)

    for index, raw in enumerate(raw_series):
        if not isinstance(raw, (TimeSeries, Mapping)):
            item_pointer = f"{pointer}/{index}" if many else pointer
            raise _validation_error(item_pointer, "type", "Expected a TimeSeries object")
    return raw_series


def _raw_observation_count(value: Any) -> int:
    if isinstance(value, TimeSeries):
        return len(value.observations)
    if isinstance(value, Mapping):
        observations = value.get("observations")
        if isinstance(observations, (list, tuple)):
            return len(observations)
    return 0


def _prepared(input_kind: str, public: Mapping[str, Any]) -> _PreparedArguments:
    argument_type = _argument_type_for(input_kind)
    mapping = _require_mapping(public, "/arguments")
    _require_exact_fields(mapping, argument_type, "/arguments")

    if argument_type is SearchArguments:
        query = _validated_string(
            mapping["query"], _field_contract(argument_type, "query"), "/query"
        )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"query": query, "as_of": as_of, "limit": limit}),
        )

    current_news_selection_types = {
        CurrentNewsSearchArgumentsV2,
        CurrentNewsSearchArgumentsV21,
        CurrentNewsSearchArgumentsV22,
        NewsAnalysisArgumentsV1,
        NewsStoryClusterArgumentsV1,
        NewsAttentionArgumentsV1,
    }
    if argument_type in current_news_selection_types:
        query = _validated_string(
            mapping.get("query", ""),
            _field_contract(argument_type, "query"),
            "/query",
        ).strip()
        if any(ord(character) < 32 or ord(character) == 127 for character in query):
            raise _validation_error(
                "/query",
                "control_character",
                "News search query cannot contain control characters",
            )
        symbols_contract = _field_contract(argument_type, "symbols")
        raw_symbols = mapping.get("symbols", ())
        if not isinstance(raw_symbols, (list, tuple)):
            raise _validation_error("/symbols", "type", "Expected an array")
        if (
            symbols_contract.max_items is not None
            and len(raw_symbols) > symbols_contract.max_items
        ):
            raise _validation_error(
                "/symbols",
                "max_items",
                "Array exceeds the supported item limit",
            )
        assert symbols_contract.item is not None  # module invariant
        symbols = tuple(
            _validated_string(
                item,
                symbols_contract.item,
                f"/symbols/{index}",
            ).strip().upper()
            for index, item in enumerate(raw_symbols)
        )
        if any(not symbol for symbol in symbols):
            raise _validation_error(
                "/symbols",
                "min_length",
                "News symbols cannot be blank",
            )
        if len(set(symbols)) != len(symbols):
            raise _validation_error(
                "/symbols",
                "unique",
                "News symbols must be unique",
            )
        source_ids: tuple[str, ...] = ()
        source_argument_types = {
            CurrentNewsSearchArgumentsV21,
            CurrentNewsSearchArgumentsV22,
            NewsAnalysisArgumentsV1,
            NewsStoryClusterArgumentsV1,
            NewsAttentionArgumentsV1,
        }
        if argument_type in source_argument_types:
            source_contract = _field_contract(argument_type, "source_ids")
            raw_source_ids = mapping.get("source_ids", ())
            if not isinstance(raw_source_ids, (list, tuple)):
                raise _validation_error(
                    "/source_ids", "type", "Expected an array"
                )
            if (
                source_contract.max_items is not None
                and len(raw_source_ids) > source_contract.max_items
            ):
                raise _validation_error(
                    "/source_ids",
                    "max_items",
                    "Array exceeds the supported item limit",
                )
            assert source_contract.item is not None  # module invariant
            source_ids = tuple(
                _validated_string(
                    item,
                    source_contract.item,
                    f"/source_ids/{index}",
                ).strip()
                for index, item in enumerate(raw_source_ids)
            )
            if any(not source_id for source_id in source_ids):
                raise _validation_error(
                    "/source_ids",
                    "min_length",
                    "News source identifiers cannot be blank",
                )
            if len(set(source_ids)) != len(source_ids):
                raise _validation_error(
                    "/source_ids",
                    "unique",
                    "News source identifiers must be unique",
                )
        mode_contract = _field_contract(argument_type, "mode")
        mode = _validated_string(
            mapping.get("mode", "latest"),
            mode_contract,
            "/mode",
        )
        if mode not in mode_contract.enum:
            raise _validation_error("/mode", "enum", "Unsupported news mode")
        as_of = _optional_temporal(mapping.get("as_of"), "/as_of")
        if mode == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of",
                "required_for_as_of",
                "as_of mode requires a cutoff",
            )
        if mode != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of",
                "only_for_as_of",
                "Only as_of mode may provide a cutoff",
            )
        date_only_policy_contract = _field_contract(
            argument_type,
            "date_only_policy",
        )
        date_only_policy = _validated_string(
            mapping.get("date_only_policy", "completed_date"),
            date_only_policy_contract,
            "/date_only_policy",
        )
        if date_only_policy not in date_only_policy_contract.enum:
            raise _validation_error(
                "/date_only_policy",
                "enum",
                "Unsupported date-only policy",
            )
        start_date = _optional_calendar_date(
            mapping.get("start_date"),
            "/start_date",
        )
        end_date = _optional_calendar_date(
            mapping.get("end_date"),
            "/end_date",
        )
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date",
                "range",
                "end_date cannot precede start_date",
            )
        limit = _validated_limit(
            mapping.get("limit", 100),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        cursor = None
        if argument_type is CurrentNewsSearchArgumentsV22:
            cursor = _optional_bounded_string(
                mapping.get("cursor"),
                _field_contract(argument_type, "cursor"),
                "/cursor",
            )
        window_hours = None
        if argument_type is NewsStoryClusterArgumentsV1:
            window_hours = _validated_limit(
                mapping.get("window_hours", 24),
                _field_contract(argument_type, "window_hours"),
                "/window_hours",
            )
        bucket = None
        baseline_periods = None
        if argument_type is NewsAttentionArgumentsV1:
            bucket_contract = _field_contract(argument_type, "bucket")
            bucket = _validated_string(
                mapping.get("bucket", "day"), bucket_contract, "/bucket"
            )
            if bucket not in bucket_contract.enum:
                raise _validation_error(
                    "/bucket", "enum", "Unsupported attention bucket"
                )
            baseline_periods = _validated_limit(
                mapping.get("baseline_periods", 20),
                _field_contract(argument_type, "baseline_periods"),
                "/baseline_periods",
            )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "query": query,
                    "symbols": symbols,
                    "mode": mode,
                    "as_of": as_of,
                    "date_only_policy": date_only_policy,
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                    **(
                        {"source_ids": source_ids}
                        if argument_type in source_argument_types
                        else {}
                    ),
                    **(
                        {"cursor": cursor}
                        if argument_type is CurrentNewsSearchArgumentsV22
                        else {}
                    ),
                    **(
                        {"window_hours": window_hours}
                        if argument_type is NewsStoryClusterArgumentsV1
                        else {}
                    ),
                    **(
                        {
                            "bucket": bucket,
                            "baseline_periods": baseline_periods,
                        }
                        if argument_type is NewsAttentionArgumentsV1
                        else {}
                    ),
                }
            ),
        )

    if argument_type is DatasetStatusArgumentsV1:
        stores = _validated_string_array(
            mapping.get("stores", ()),
            _field_contract(argument_type, "stores"),
            "/stores",
        )
        dataset_ids = _validated_string_array(
            mapping.get("dataset_ids", ()),
            _field_contract(argument_type, "dataset_ids"),
            "/dataset_ids",
        )
        statuses = _validated_string_array(
            mapping.get("statuses", ()),
            _field_contract(argument_type, "statuses"),
            "/statuses",
        )
        limit = _validated_limit(
            mapping.get("limit", 128),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "stores": stores,
                    "dataset_ids": dataset_ids,
                    "statuses": statuses,
                    "limit": limit,
                }
            ),
        )

    options_v2_types = {
        OptionsCaptureSearchArgumentsV2,
        OptionsContractSearchArgumentsV2,
        OptionsSurfaceSnapshotArgumentsV2,
    }
    if argument_type in options_v2_types:
        mode_contract = _field_contract(argument_type, "mode")
        mode = _validated_string(
            mapping.get("mode", "latest"), mode_contract, "/mode"
        )
        if mode not in mode_contract.enum:
            raise _validation_error("/mode", "enum", "Unsupported options mode")
        as_of = _optional_temporal(mapping.get("as_of"), "/as_of")
        if mode == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if mode != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        policy_contract = _field_contract(argument_type, "date_only_policy")
        date_only_policy = _validated_string(
            mapping.get("date_only_policy", "completed_date"),
            policy_contract,
            "/date_only_policy",
        )
        if date_only_policy not in policy_contract.enum:
            raise _validation_error(
                "/date_only_policy", "enum", "Unsupported date-only policy"
            )
        values: dict[str, Any] = {
            "mode": mode,
            "as_of": as_of,
            "date_only_policy": date_only_policy,
        }
        if argument_type is OptionsCaptureSearchArgumentsV2:
            values["underlying_symbols"] = _validated_string_array(
                mapping.get("underlying_symbols", ()),
                _field_contract(argument_type, "underlying_symbols"),
                "/underlying_symbols",
                normalize_upper=True,
            )
            default_limit = 100
        else:
            symbol_contract = _field_contract(argument_type, "underlying_symbol")
            underlying_symbol = _validated_string(
                mapping["underlying_symbol"], symbol_contract, "/underlying_symbol"
            ).strip().upper()
            if underlying_symbol not in symbol_contract.enum:
                raise _validation_error(
                    "/underlying_symbol", "enum", "Unsupported options underlying"
                )
            values["underlying_symbol"] = underlying_symbol
            expiration_date = _optional_calendar_date(
                mapping.get("expiration_date"), "/expiration_date"
            )
            values["expiration_date"] = expiration_date
            option_type = _optional_bounded_string(
                mapping.get("option_type"),
                _field_contract(argument_type, "option_type"),
                "/option_type",
            )
            if option_type is not None and option_type not in ("call", "put"):
                raise _validation_error(
                    "/option_type", "enum", "Unsupported option type"
                )
            values["option_type"] = option_type
            if argument_type is OptionsContractSearchArgumentsV2:
                query = _validated_string(
                    mapping.get("query", ""),
                    _field_contract(argument_type, "query"),
                    "/query",
                ).strip()
                if any(ord(character) < 32 or ord(character) == 127 for character in query):
                    raise _validation_error(
                        "/query", "control_character", "Options query cannot contain control characters"
                    )
                values["query"] = query
                default_limit = 100
            else:
                values["capture_id"] = _optional_bounded_string(
                    mapping.get("capture_id"),
                    _field_contract(argument_type, "capture_id"),
                    "/capture_id",
                )
                target_dte = _validated_optional_limit(
                    mapping.get("target_dte"),
                    _field_contract(argument_type, "target_dte"),
                    "/target_dte",
                )
                if target_dte is not None and target_dte not in _OPTIONS_V2_TARGET_DTES:
                    raise _validation_error(
                        "/target_dte", "enum", "Unsupported target DTE"
                    )
                values["target_dte"] = target_dte
                surface_state = _optional_bounded_string(
                    mapping.get("surface_state"),
                    _field_contract(argument_type, "surface_state"),
                    "/surface_state",
                )
                if surface_state is not None and surface_state not in (
                    "present", "missing", "excluded"
                ):
                    raise _validation_error(
                        "/surface_state", "enum", "Unsupported surface state"
                    )
                values["surface_state"] = surface_state
                default_limit = 500
        values["limit"] = _validated_limit(
            mapping.get("limit", default_limit),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(argument_type, MappingProxyType(values))

    if argument_type is NewsSourceStatusArgumentsV1:
        source_contract = _field_contract(argument_type, "source_ids")
        raw_source_ids = mapping.get("source_ids", ())
        if not isinstance(raw_source_ids, (list, tuple)):
            raise _validation_error("/source_ids", "type", "Expected an array")
        if (
            source_contract.max_items is not None
            and len(raw_source_ids) > source_contract.max_items
        ):
            raise _validation_error(
                "/source_ids", "max_items", "Array exceeds the supported item limit"
            )
        assert source_contract.item is not None
        source_ids = tuple(
            _validated_string(
                value,
                source_contract.item,
                f"/source_ids/{index}",
            ).strip()
            for index, value in enumerate(raw_source_ids)
        )
        if any(not value for value in source_ids):
            raise _validation_error(
                "/source_ids", "min_length", "News source identifiers cannot be blank"
            )
        if any(value not in _CURRENT_NEWS_SOURCE_IDS for value in source_ids):
            raise _validation_error(
                "/source_ids", "enum", "Unsupported news source identifier"
            )
        if len(source_ids) != len(set(source_ids)):
            raise _validation_error(
                "/source_ids", "unique", "News source identifiers must be unique"
            )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"source_ids": source_ids}),
        )

    if argument_type is NewsItemHistoryArgumentsV1:
        article_id = _validated_string(
            mapping["article_id"],
            _field_contract(argument_type, "article_id"),
            "/article_id",
        ).strip()
        if not article_id:
            raise _validation_error(
                "/article_id", "min_length", "Article identity cannot be blank"
            )
        limit = _validated_limit(
            mapping.get("limit", 100),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"article_id": article_id, "limit": limit}),
        )

    if argument_type is NewsEventImpactArgumentsV1:
        article_version_id = _validated_string(
            mapping["article_version_id"],
            _field_contract(argument_type, "article_version_id"),
            "/article_version_id",
        ).strip()
        instrument_id = _validated_string(
            mapping["instrument_id"],
            _field_contract(argument_type, "instrument_id"),
            "/instrument_id",
        ).strip()
        if not article_version_id or not instrument_id:
            raise _validation_error(
                "/article_version_id",
                "min_length",
                "Article-version and instrument identities cannot be blank",
            )
        pre_observations = _validated_limit(
            mapping.get("pre_observations", 5),
            _field_contract(argument_type, "pre_observations"),
            "/pre_observations",
        )
        post_observations = _validated_limit(
            mapping.get("post_observations", 5),
            _field_contract(argument_type, "post_observations"),
            "/post_observations",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "article_version_id": article_version_id,
                    "instrument_id": instrument_id,
                    "pre_observations": pre_observations,
                    "post_observations": post_observations,
                }
            ),
        )

    if argument_type is CompanyFilingSearchArgumentsV2:
        query = _validated_string(
            mapping["query"],
            _field_contract(argument_type, "query"),
            "/query",
        )
        if len(query) != 10 or not query.isdigit():
            raise _validation_error(
                "/query",
                "identity",
                "Filing search requires one exact ten-digit SEC CIK",
            )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        cursor = _optional_bounded_string(
            mapping["cursor"],
            _field_contract(argument_type, "cursor"),
            "/cursor",
        )
        limit = _validated_limit(
            mapping["limit"],
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "query": query,
                    "as_of": as_of,
                    "cursor": cursor,
                    "limit": limit,
                }
            ),
        )

    if argument_type is CompanyShareCountHistoryArgumentsV2:
        cik = _validated_string(
            mapping["cik"],
            _field_contract(argument_type, "cik"),
            "/cik",
        )
        if len(cik) != 10 or not cik.isdigit():
            raise _validation_error(
                "/cik",
                "identity",
                "Share-count history requires one exact ten-digit SEC CIK",
            )
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name],
                contract,
                f"/{field_name}",
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}",
                    "enum",
                    f"Unsupported {field_name}",
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of",
                "required_for_as_of",
                "as_of mode requires a cutoff",
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of",
                "only_for_as_of",
                "Only as_of mode may provide a cutoff",
            )
        limit = _validated_limit(
            mapping["limit"],
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "cik": cik,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "limit": limit,
                }
            ),
        )

    if argument_type is Stage10MarketInstrumentSearchArgumentsV2:
        query = _validated_string(
            mapping.get("query", ""),
            _field_contract(argument_type, "query"),
            "/query",
        )
        if any(ord(character) < 32 or ord(character) == 127 for character in query):
            raise _validation_error(
                "/query",
                "control_character",
                "Instrument search query cannot contain control characters",
            )
        asset_type = _optional_bounded_string(
            mapping.get("asset_type"),
            _field_contract(argument_type, "asset_type"),
            "/asset_type",
        )
        if asset_type is not None and asset_type not in {
            "equity",
            "etf",
            "index",
        }:
            raise _validation_error(
                "/asset_type",
                "enum",
                "Instrument asset_type is not supported",
            )
        cursor = _optional_bounded_string(
            mapping.get("cursor"),
            _field_contract(argument_type, "cursor"),
            "/cursor",
        )
        limit = _validated_limit(
            mapping.get("limit", 100),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "query": query.strip(),
                    "asset_type": asset_type,
                    "cursor": cursor,
                    "limit": limit,
                }
            ),
        )

    if argument_type is QueryArguments:
        identifiers_contract = _field_contract(argument_type, "identifiers")
        raw_identifiers = mapping["identifiers"]
        if not isinstance(raw_identifiers, (list, tuple)):
            raise _validation_error("/identifiers", "type", "Expected an array")
        if (
            identifiers_contract.max_items is not None
            and len(raw_identifiers) > identifiers_contract.max_items
        ):
            raise _validation_error(
                "/identifiers", "max_items", "Array exceeds the supported item limit"
            )
        assert identifiers_contract.item is not None  # module invariant
        identifiers = tuple(
            _validated_string(item, identifiers_contract.item, f"/identifiers/{index}")
            for index, item in enumerate(raw_identifiers)
        )
        if any(not identifier.strip() for identifier in identifiers):
            raise _validation_error(
                "/identifiers", "min_length", "Identifiers cannot be blank"
            )

        mode = _validated_string(
            mapping["mode"], _field_contract(argument_type, "mode"), "/mode"
        )
        mode_contract = _field_contract(argument_type, "mode")
        if mode not in mode_contract.enum:
            raise _validation_error("/mode", "enum", "Unsupported query mode")
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if mode == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if mode != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )

        start_date = _optional_calendar_date(mapping["start_date"], "/start_date")
        end_date = _optional_calendar_date(mapping["end_date"], "/end_date")
        if start_date is not None and end_date is not None:
            if parse_date(end_date, pointer="/end_date") < parse_date(
                start_date, pointer="/start_date"
            ):
                raise _validation_error(
                    "/end_date", "range", "end_date cannot precede start_date"
                )

        parameters = _validated_parameters(mapping["parameters"], argument_type, "/parameters")
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "identifiers": identifiers,
                    "mode": mode,
                    "as_of": as_of,
                    "start_date": start_date,
                    "end_date": end_date,
                    "parameters": parameters,
                    "limit": limit,
                }
            ),
        )

    if argument_type is Stage10AvailableTickerArgumentsV1:
        limit = _validated_limit(
            mapping.get("limit", 10_000),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"limit": limit}),
        )

    if argument_type in {
        Stage10MarketPriceArgumentsV1,
        Stage10MarketVolumeArgumentsV1,
    }:
        ticker = _validated_string(
            mapping["ticker"],
            _field_contract(argument_type, "ticker"),
            "/ticker",
        )
        if not ticker.strip():
            raise _validation_error(
                "/ticker", "min_length", "Ticker cannot be blank"
            )
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "ticker": ticker,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "limit": limit,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            ),
        )

    if argument_type is CanonicalMacroSearchArgumentsV2:
        query = _validated_string(
            mapping.get("query", ""),
            _field_contract(argument_type, "query"),
            "/query",
        )
        provider = _optional_bounded_string(
            mapping.get("provider"),
            _field_contract(argument_type, "provider"),
            "/provider",
        )
        frequency = _optional_bounded_string(
            mapping.get("frequency"),
            _field_contract(argument_type, "frequency"),
            "/frequency",
        )
        limit = _validated_limit(
            mapping.get("limit", 500),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "query": query,
                    "provider": provider,
                    "frequency": frequency,
                    "limit": limit,
                }
            ),
        )

    if argument_type is CanonicalMacroDescribeArgumentsV2:
        series_id = _validated_string(
            mapping["series_id"],
            _field_contract(argument_type, "series_id"),
            "/series_id",
        )
        if not series_id.strip():
            raise _validation_error(
                "/series_id", "min_length", "Series ID cannot be blank"
            )
        limit = _validated_limit(
            mapping.get("limit", 1),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType({"series_id": series_id, "limit": limit}),
        )

    if argument_type in {
        CanonicalMacroSeriesArgumentsV2,
        MacroReleaseCalendarArgumentsV1,
        MacroReleaseCalendarArgumentsV2,
    }:
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        values: dict[str, Any] = {
            "mode": enum_values["mode"],
            "as_of": as_of,
            "date_only_policy": enum_values["date_only_policy"],
            "limit": limit,
            "start_date": start_date,
            "end_date": end_date,
        }
        if argument_type is CanonicalMacroSeriesArgumentsV2:
            series_id = _validated_string(
                mapping["series_id"],
                _field_contract(argument_type, "series_id"),
                "/series_id",
            )
            if not series_id.strip():
                raise _validation_error(
                    "/series_id", "min_length", "Series ID cannot be blank"
                )
            values["series_id"] = series_id
        else:
            values["event_name"] = _optional_bounded_string(
                mapping.get("event_name"),
                _field_contract(argument_type, "event_name"),
                "/event_name",
            )
            if argument_type is MacroReleaseCalendarArgumentsV2:
                values["cursor"] = _optional_bounded_string(
                    mapping.get("cursor"),
                    _field_contract(argument_type, "cursor"),
                    "/cursor",
                )
        return _PreparedArguments(
            argument_type, MappingProxyType(values)
        )

    if argument_type in {
        Stage10CrossSectionalPerformanceArgumentsV2,
        Stage10CrossSectionalAnalyticsArgumentsV21,
    }:
        contract = _field_contract(argument_type, "tickers")
        raw_tickers = mapping["tickers"]
        if not isinstance(raw_tickers, (list, tuple)):
            raise _validation_error("/tickers", "type", "Expected an array")
        if (
            contract.min_items is not None
            and len(raw_tickers) < contract.min_items
        ):
            raise _validation_error(
                "/tickers", "min_items", "At least two tickers are required"
            )
        if (
            contract.max_items is not None
            and len(raw_tickers) > contract.max_items
        ):
            raise _validation_error(
                "/tickers", "max_items", "Ticker array exceeds the supported limit"
            )
        assert contract.item is not None
        tickers = tuple(
            _validated_string(
                item, contract.item, f"/tickers/{index}"
            ).strip().upper()
            for index, item in enumerate(raw_tickers)
        )
        if any(not ticker for ticker in tickers):
            raise _validation_error(
                "/tickers", "min_length", "Tickers cannot be blank"
            )
        if len(set(tickers)) != len(tickers):
            raise _validation_error(
                "/tickers", "duplicate", "Tickers must be unique"
            )
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            field_contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], field_contract, f"/{field_name}"
            )
            if value not in field_contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        if len(tickers) * limit > 10_000:
            raise _validation_error(
                "/limit",
                "resource_limit",
                "Ticker count multiplied by limit cannot exceed 10000",
            )
        analytics_values: dict[str, Any] = {}
        if argument_type is Stage10CrossSectionalAnalyticsArgumentsV21:
            benchmark_ticker = _validated_string(
                mapping["benchmark_ticker"],
                _field_contract(argument_type, "benchmark_ticker"),
                "/benchmark_ticker",
            ).strip().upper()
            if benchmark_ticker not in tickers:
                raise _validation_error(
                    "/benchmark_ticker",
                    "membership",
                    "benchmark_ticker must be included in tickers",
                )
            window = _validated_limit(
                mapping["window"],
                _field_contract(argument_type, "window"),
                "/window",
            )
            analytics_values = {
                "benchmark_ticker": benchmark_ticker,
                "window": window,
            }
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "tickers": tickers,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "limit": limit,
                    "start_date": start_date,
                    "end_date": end_date,
                    **analytics_values,
                }
            ),
        )

    if argument_type in {
        EnergyElectricityRetailArgumentsV2,
        EnergyWeeklyFundamentalsArgumentsV2,
        CompanyFundamentalsArgumentsV2,
        CompanyFundamentalRatiosArgumentsV21,
    }:
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        values: dict[str, Any] = {
            "mode": enum_values["mode"],
            "as_of": as_of,
            "date_only_policy": enum_values["date_only_policy"],
            "limit": limit,
            "start_date": start_date,
            "end_date": end_date,
        }
        if argument_type is EnergyElectricityRetailArgumentsV2:
            metric_contract = _field_contract(argument_type, "metric")
            metric = _validated_string(
                mapping["metric"], metric_contract, "/metric"
            )
            if metric not in metric_contract.enum:
                raise _validation_error(
                    "/metric", "enum", "Unsupported electricity retail metric"
                )
            values["metric"] = metric
        elif argument_type is CompanyFundamentalsArgumentsV2:
            cik = _validated_string(
                mapping["cik"], _field_contract(argument_type, "cik"), "/cik"
            )
            if len(cik) != 10 or not cik.isdigit():
                raise _validation_error(
                    "/cik",
                    "identity",
                    "Company fundamentals require one exact ten-digit SEC CIK",
                )
            metric_contract = _field_contract(argument_type, "metric_codes")
            raw_metrics = mapping.get("metric_codes", ())
            if isinstance(raw_metrics, (str, bytes)) or not isinstance(
                raw_metrics, (list, tuple)
            ):
                raise _validation_error(
                    "/metric_codes", "type", "Expected an array"
                )
            if (
                metric_contract.max_items is not None
                and len(raw_metrics) > metric_contract.max_items
            ):
                raise _validation_error(
                    "/metric_codes",
                    "max_items",
                    "Metric-code array exceeds the supported limit",
                )
            assert metric_contract.item is not None
            metric_codes = tuple(
                _validated_string(
                    item,
                    metric_contract.item,
                    f"/metric_codes/{index}",
                ).strip()
                for index, item in enumerate(raw_metrics)
            )
            if any(not item for item in metric_codes):
                raise _validation_error(
                    "/metric_codes", "min_length", "Metric codes cannot be blank"
                )
            if len(set(metric_codes)) != len(metric_codes):
                raise _validation_error(
                    "/metric_codes", "duplicate", "Metric codes must be unique"
                )
            values["cik"] = cik
            values["metric_codes"] = metric_codes
        elif argument_type is CompanyFundamentalRatiosArgumentsV21:
            cik = _validated_string(
                mapping["cik"],
                _field_contract(argument_type, "cik"),
                "/cik",
            )
            if len(cik) != 10 or not cik.isdigit():
                raise _validation_error(
                    "/cik",
                    "identity",
                    "Company ratios require one exact ten-digit SEC CIK",
                )
            ratio_contract = _field_contract(argument_type, "ratio_codes")
            raw_ratios = mapping["ratio_codes"]
            if not isinstance(raw_ratios, (list, tuple)):
                raise _validation_error("/ratio_codes", "type", "Expected an array")
            assert ratio_contract.item is not None
            ratio_codes = tuple(
                _validated_string(
                    item,
                    ratio_contract.item,
                    f"/ratio_codes/{index}",
                ).strip()
                for index, item in enumerate(raw_ratios)
            )
            if not (
                int(ratio_contract.min_items or 0)
                <= len(ratio_codes)
                <= int(ratio_contract.max_items or len(ratio_codes))
            ):
                raise _validation_error(
                    "/ratio_codes",
                    "item_count",
                    "Ratio-code array must contain one or two items",
                )
            if any(
                not code or code not in ratio_contract.item.enum
                for code in ratio_codes
            ):
                raise _validation_error(
                    "/ratio_codes",
                    "enum",
                    "Unsupported company ratio code",
                )
            if len(set(ratio_codes)) != len(ratio_codes):
                raise _validation_error(
                    "/ratio_codes",
                    "duplicate",
                    "Ratio codes must be unique",
                )
            values["cik"] = cik
            values["ratio_codes"] = ratio_codes
        return _PreparedArguments(argument_type, MappingProxyType(values))

    if argument_type in {
        MacroRevisionArgumentsV2,
        MacroSurpriseStandardizationArgumentsV2,
    }:
        identity_field = (
            "series_id"
            if argument_type is MacroRevisionArgumentsV2
            else "kind"
        )
        identity_contract = _field_contract(argument_type, identity_field)
        identity = _validated_string(
            mapping[identity_field],
            identity_contract,
            f"/{identity_field}",
        )
        if identity not in identity_contract.enum:
            raise _validation_error(
                f"/{identity_field}",
                "enum",
                f"Unsupported {identity_field}",
            )
        start_date = _optional_calendar_date(
            mapping.get("start_date"), "/start_date"
        )
        end_date = _optional_calendar_date(mapping.get("end_date"), "/end_date")
        if (
            start_date is not None
            and end_date is not None
            and parse_date(end_date, pointer="/end_date")
            < parse_date(start_date, pointer="/start_date")
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        limit = _validated_limit(
            mapping.get("limit", 500),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        values: dict[str, Any] = {
            identity_field: identity,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
        }
        if argument_type is MacroSurpriseStandardizationArgumentsV2:
            stage_contract = _field_contract(argument_type, "release_stage")
            release_stage = _optional_bounded_string(
                mapping.get("release_stage"),
                stage_contract,
                "/release_stage",
            )
            if release_stage is not None and release_stage not in stage_contract.enum:
                raise _validation_error(
                    "/release_stage", "enum", "Unsupported release stage"
                )
            if (
                release_stage is not None
                and identity != "us_gdp_real_qoq_saar_advance"
            ):
                raise _validation_error(
                    "/release_stage",
                    "not_applicable",
                    "release_stage applies only to GDP advance events",
                )
            values["release_stage"] = release_stage
        return _PreparedArguments(argument_type, MappingProxyType(values))

    if argument_type in {
        MacroObservedSnapshotArgumentsV2,
        FundingConditionsArgumentsV2,
        CurveAnalyticsArgumentsV2,
        MacroRegimeArgumentsV2,
    }:
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        observation_date = _optional_calendar_date(
            mapping.get("observation_date"), "/observation_date"
        )
        limit = _validated_limit(
            mapping.get("limit", 20),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        values = {
            "mode": enum_values["mode"],
            "as_of": as_of,
            "date_only_policy": enum_values["date_only_policy"],
            "observation_date": observation_date,
            "limit": limit,
        }
        if argument_type is FundingConditionsArgumentsV2:
            left = _optional_bounded_string(
                mapping.get("spread_left"),
                _field_contract(argument_type, "spread_left"),
                "/spread_left",
            )
            right = _optional_bounded_string(
                mapping.get("spread_right"),
                _field_contract(argument_type, "spread_right"),
                "/spread_right",
            )
            if (left is None) != (right is None):
                raise _validation_error(
                    "/spread_right",
                    "paired",
                    "Both funding spread components are required together",
                )
            values["spread_left"] = left
            values["spread_right"] = right
        elif argument_type is CurveAnalyticsArgumentsV2:
            left = _optional_bounded_string(
                mapping.get("spread_left_tenor"),
                _field_contract(argument_type, "spread_left_tenor"),
                "/spread_left_tenor",
            )
            right = _optional_bounded_string(
                mapping.get("spread_right_tenor"),
                _field_contract(argument_type, "spread_right_tenor"),
                "/spread_right_tenor",
            )
            if (left is None) != (right is None):
                raise _validation_error(
                    "/spread_right_tenor",
                    "paired",
                    "Both curve spread tenors are required together",
                )
            values["spread_left_tenor"] = left
            values["spread_right_tenor"] = right
        elif argument_type is MacroRegimeArgumentsV2:
            include_context = mapping.get("include_context", False)
            if not isinstance(include_context, bool):
                raise _validation_error(
                    "/include_context", "type", "Expected a boolean"
                )
            values["include_context"] = include_context
        return _PreparedArguments(argument_type, MappingProxyType(values))

    if argument_type is LiquidityImpulseArgumentsV2:
        enum_values: dict[str, str] = {}
        for field_name in ("mode", "date_only_policy"):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        start_date = _optional_calendar_date(mapping["start_date"], "/start_date")
        end_date = _optional_calendar_date(mapping["end_date"], "/end_date")
        if start_date is None or end_date is None:
            raise _validation_error(
                "/start_date", "required", "Both impulse dates are required"
            )
        if parse_date(end_date, pointer="/end_date") < parse_date(
            start_date, pointer="/start_date"
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        limit = _validated_limit(
            mapping.get("limit", 20),
            _field_contract(argument_type, "limit"),
            "/limit",
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                }
            ),
        )

    if argument_type is Stage10MarketReturnArgumentsV2:
        identifier = _validated_string(
            mapping["identifier"],
            _field_contract(argument_type, "identifier"),
            "/identifier",
        )
        if not identifier.strip():
            raise _validation_error(
                "/identifier", "min_length", "Identifier cannot be blank"
            )
        enum_values: dict[str, str] = {}
        for field_name in (
            "identifier_kind",
            "mode",
            "date_only_policy",
            "method",
        ):
            contract = _field_contract(argument_type, field_name)
            value = _validated_string(
                mapping[field_name], contract, f"/{field_name}"
            )
            if value not in contract.enum:
                raise _validation_error(
                    f"/{field_name}", "enum", f"Unsupported {field_name}"
                )
            enum_values[field_name] = value
        start_date = _optional_calendar_date(mapping["start_date"], "/start_date")
        end_date = _optional_calendar_date(mapping["end_date"], "/end_date")
        assert start_date is not None and end_date is not None
        if parse_date(end_date, pointer="/end_date") < parse_date(
            start_date, pointer="/start_date"
        ):
            raise _validation_error(
                "/end_date", "range", "end_date cannot precede start_date"
            )
        as_of = _optional_temporal(mapping["as_of"], "/as_of")
        if enum_values["mode"] == "as_of" and as_of is None:
            raise _validation_error(
                "/as_of", "required_for_as_of", "as_of mode requires a cutoff"
            )
        if enum_values["mode"] != "as_of" and as_of is not None:
            raise _validation_error(
                "/as_of", "only_for_as_of", "Only as_of mode may provide a cutoff"
            )
        horizon = _validated_limit(
            mapping["horizon"],
            _field_contract(argument_type, "horizon"),
            "/horizon",
        )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        return _PreparedArguments(
            argument_type,
            MappingProxyType(
                {
                    "identifier": identifier,
                    "identifier_kind": enum_values["identifier_kind"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "mode": enum_values["mode"],
                    "as_of": as_of,
                    "date_only_policy": enum_values["date_only_policy"],
                    "method": enum_values["method"],
                    "horizon": horizon,
                    "limit": limit,
                }
            ),
        )

    if argument_type in {
        Stage10MarketDescribeArgumentsV2,
        Stage10MarketAlignArgumentsV2,
        Stage10MarketCorrelationArgumentsV2,
        Stage10DataQualityArgumentsV2,
        Stage10TechnicalIndicatorArgumentsV2,
        Stage10TechnicalIndicatorArgumentsV21,
        Stage10TechnicalIndicatorArgumentsV22,
        Stage10TechnicalIndicatorArgumentsV23,
        Stage10TechnicalIndicatorArgumentsV24,
        Stage10TechnicalIndicatorArgumentsV25,
        Stage10TechnicalIndicatorArgumentsV26,
        Stage10TechnicalIndicatorArgumentsV27,
        Stage10MarketTransformArgumentsV2,
        Stage10DistributionArgumentsV1,
        Stage10BootstrapArgumentsV1,
        Stage10CovarianceArgumentsV1,
        Stage10PrincipalComponentsArgumentsV1,
        Stage10MarketRegressionArgumentsV2,
        Stage10MarketRollingRegressionArgumentsV2,
        Stage10MarketRegressionArgumentsV21,
        Stage10MarketRollingRegressionArgumentsV21,
        Stage10MarketStationarityArgumentsV2,
        Stage10MarketStationarityArgumentsV21,
        Stage10MarketStructuralBreakArgumentsV2,
        Stage10MarketRegressionModelSuiteArgumentsV3,
    }:
        series_contract = _field_contract(argument_type, "series")
        raw_series = _validated_series_material(
            mapping["series"], series_contract, "/series"
        )
        limit = _validated_limit(
            mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
        )
        values: dict[str, Any] = {"limit": limit}
        if (
            argument_type is Stage10DataQualityArgumentsV2
            and limit < len(raw_series)
        ):
            raise _validation_error(
                "/limit",
                "minimum",
                "limit must retain one quality summary per supplied series",
            )
        if argument_type in {
            Stage10TechnicalIndicatorArgumentsV2,
            Stage10TechnicalIndicatorArgumentsV21,
            Stage10TechnicalIndicatorArgumentsV22,
            Stage10TechnicalIndicatorArgumentsV23,
            Stage10TechnicalIndicatorArgumentsV24,
            Stage10TechnicalIndicatorArgumentsV25,
            Stage10TechnicalIndicatorArgumentsV26,
            Stage10TechnicalIndicatorArgumentsV27,
        }:
            indicator_contract = _field_contract(argument_type, "indicator")
            indicator = _validated_string(
                mapping["indicator"], indicator_contract, "/indicator"
            )
            if indicator not in indicator_contract.enum:
                raise _validation_error(
                    "/indicator", "enum", "Unsupported technical indicator"
                )
            values["indicator"] = indicator
            parameter_names = (
                "window",
                "fast_window",
                "slow_window",
                "signal_window",
            )
            for field_name in parameter_names:
                values[field_name] = _validated_optional_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )
            raw_multiplier = mapping["standard_deviation_multiplier"]
            multiplier: Decimal | None = None
            if raw_multiplier is not None:
                validated = _validated_scalar(
                    raw_multiplier, "/standard_deviation_multiplier"
                )
                if (
                    isinstance(validated, bool)
                    or not isinstance(validated, (int, Decimal))
                ):
                    raise _validation_error(
                        "/standard_deviation_multiplier",
                        "type",
                        "Expected a finite number or null",
                    )
                multiplier = (
                    validated
                    if isinstance(validated, Decimal)
                    else Decimal(validated)
                )
                if not Decimal("0") < multiplier <= Decimal("10"):
                    raise _validation_error(
                        "/standard_deviation_multiplier",
                        "range",
                        "Multiplier must be greater than zero and at most ten",
                    )
            values["standard_deviation_multiplier"] = multiplier
            supertrend_parameter_names: tuple[str, ...] = ()
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV21,
                Stage10TechnicalIndicatorArgumentsV22,
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                supertrend_parameter_names = (
                    "minimum_factor",
                    "maximum_factor",
                    "factor_step",
                    "performance_memory",
                    "cluster",
                )
                for field_name in supertrend_parameter_names[:-1]:
                    raw_value = mapping[field_name]
                    decimal_value: Decimal | None = None
                    if raw_value is not None:
                        validated = _validated_scalar(
                            raw_value, f"/{field_name}"
                        )
                        if (
                            isinstance(validated, bool)
                            or not isinstance(validated, (int, Decimal))
                        ):
                            raise _validation_error(
                                f"/{field_name}",
                                "type",
                                "Expected a finite number or null",
                            )
                        decimal_value = (
                            validated
                            if isinstance(validated, Decimal)
                            else Decimal(validated)
                        )
                    values[field_name] = decimal_value
                raw_cluster = mapping["cluster"]
                cluster: str | None = None
                if raw_cluster is not None:
                    cluster = _validated_string(
                        raw_cluster,
                        _field_contract(argument_type, "cluster"),
                        "/cluster",
                    )
                    if cluster not in {"best", "average", "worst"}:
                        raise _validation_error(
                            "/cluster",
                            "enum",
                            "Unsupported SuperTrend cluster",
                        )
                values["cluster"] = cluster
            swing_parameter_names: tuple[str, ...] = ()
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV22,
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                swing_parameter_names = (
                    "sample_count",
                    "aggregation_method",
                )
                values["sample_count"] = _validated_optional_limit(
                    mapping["sample_count"],
                    _field_contract(argument_type, "sample_count"),
                    "/sample_count",
                )
                raw_method = mapping["aggregation_method"]
                aggregation_method: str | None = None
                if raw_method is not None:
                    aggregation_method = _validated_string(
                        raw_method,
                        _field_contract(argument_type, "aggregation_method"),
                        "/aggregation_method",
                    )
                    if aggregation_method not in {
                        "weighted",
                        "average",
                        "median",
                    }:
                        raise _validation_error(
                            "/aggregation_method",
                            "enum",
                            "Unsupported swing-forecast aggregation method",
                        )
                values["aggregation_method"] = aggregation_method
            percentile_parameter_names: tuple[str, ...] = ()
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                percentile_parameter_names = (
                    "percentile_window",
                    "percentile_high_factor",
                    "percentile_low_factor",
                )
                values["percentile_window"] = _validated_optional_limit(
                    mapping["percentile_window"],
                    _field_contract(argument_type, "percentile_window"),
                    "/percentile_window",
                )
                for field_name in percentile_parameter_names[1:]:
                    raw_value = mapping[field_name]
                    decimal_value: Decimal | None = None
                    if raw_value is not None:
                        validated = _validated_scalar(
                            raw_value, f"/{field_name}"
                        )
                        if (
                            isinstance(validated, bool)
                            or not isinstance(validated, (int, Decimal))
                        ):
                            raise _validation_error(
                                f"/{field_name}",
                                "type",
                                "Expected a finite number or null",
                            )
                        decimal_value = (
                            validated
                            if isinstance(validated, Decimal)
                            else Decimal(validated)
                        )
                    values[field_name] = decimal_value
            sar_parameter_names: tuple[str, ...] = ()
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                sar_parameter_names = ("start", "increment", "maximum")
                for field_name in sar_parameter_names:
                    raw_value = mapping[field_name]
                    decimal_value: Decimal | None = None
                    if raw_value is not None:
                        validated = _validated_scalar(
                            raw_value, f"/{field_name}"
                        )
                        if (
                            isinstance(validated, bool)
                            or not isinstance(validated, (int, Decimal))
                        ):
                            raise _validation_error(
                                f"/{field_name}",
                                "type",
                                "Expected a finite number or null",
                            )
                        decimal_value = (
                            validated
                            if isinstance(validated, Decimal)
                            else Decimal(validated)
                        )
                    values[field_name] = decimal_value
            required_parameters = {
                "sma": ("window",),
                "ema": ("window",),
                "rolling_regression_line": ("window",),
                "rolling_standard_deviation": ("window",),
                "rolling_z_score": ("window",),
                "true_range": (),
                "average_true_range": ("window",),
                "rate_of_change": ("window",),
                "relative_strength_index": ("window",),
                "macd": ("fast_window", "slow_window", "signal_window"),
                "bollinger_bands": (
                    "window",
                    "standard_deviation_multiplier",
                ),
                "donchian_channels": ("window",),
                "stochastic_oscillator": ("window", "signal_window"),
                "average_directional_index": ("window",),
                "on_balance_volume": (),
                "accumulation_distribution": (),
            }
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV21,
                Stage10TechnicalIndicatorArgumentsV22,
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["supertrend_ai"] = (
                    "window",
                    *supertrend_parameter_names,
                )
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV22,
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["swing_structure_forecast"] = (
                    "window",
                    *swing_parameter_names,
                )
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["kdj"] = ("window", "signal_window")
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["williams_vix_fix"] = (
                    "window",
                    "signal_window",
                    "standard_deviation_multiplier",
                    *percentile_parameter_names,
                )
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["wavetrend_crosses"] = (
                    "window", "signal_window"
                )
            if argument_type in {
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10TechnicalIndicatorArgumentsV27,
            }:
                required_parameters["parabolic_sar"] = sar_parameter_names
            active = set(required_parameters[indicator])
            supplied = {
                name
                for name in (
                    *parameter_names,
                    "standard_deviation_multiplier",
                    *supertrend_parameter_names,
                    *swing_parameter_names,
                    *percentile_parameter_names,
                    *sar_parameter_names,
                )
                if values[name] is not None
            }
            missing = active - supplied
            if missing:
                name = sorted(missing)[0]
                raise _validation_error(
                    f"/{name}",
                    "required_for_indicator",
                    f"{name} is required for {indicator}",
                )
            extra = supplied - active
            if extra:
                name = sorted(extra)[0]
                raise _validation_error(
                    f"/{name}",
                    "only_for_indicator",
                    f"{name} is not used by {indicator}",
                )
            if (
                indicator
                in {
                    "rolling_standard_deviation",
                    "rolling_z_score",
                    "bollinger_bands",
                    "rolling_regression_line",
                }
                and int(values["window"]) < 2
            ):
                raise _validation_error(
                    "/window",
                    "minimum",
                    f"{indicator} requires a window of at least two",
                )
            if indicator == "swing_structure_forecast" and not (
                10 <= int(values["window"]) <= 5_000
            ):
                raise _validation_error(
                    "/window",
                    "range",
                    "swing_structure_forecast requires a window from 10 through 5,000",
                )
            if indicator == "williams_vix_fix":
                multiplier = values["standard_deviation_multiplier"]
                percentile_high_factor = values["percentile_high_factor"]
                percentile_low_factor = values["percentile_low_factor"]
                assert isinstance(multiplier, Decimal)
                assert isinstance(percentile_high_factor, Decimal)
                assert isinstance(percentile_low_factor, Decimal)
                if not Decimal("1") <= multiplier <= Decimal("5"):
                    raise _validation_error(
                        "/standard_deviation_multiplier",
                        "range",
                        "Williams Vix Fix multiplier must be from one through five",
                    )
                if not Decimal("0") < percentile_high_factor <= Decimal("1"):
                    raise _validation_error(
                        "/percentile_high_factor",
                        "range",
                        "percentile_high_factor must be greater than zero and at most one",
                    )
                if not Decimal("1") <= percentile_low_factor <= Decimal("10"):
                    raise _validation_error(
                        "/percentile_low_factor",
                        "range",
                        "percentile_low_factor must be from one through ten",
                    )
            if (
                indicator == "macd"
                and int(values["fast_window"]) >= int(values["slow_window"])
            ):
                raise _validation_error(
                    "/fast_window",
                    "ordering",
                    "MACD fast_window must be smaller than slow_window",
                )
            if indicator == "supertrend_ai":
                minimum_factor = values["minimum_factor"]
                maximum_factor = values["maximum_factor"]
                factor_step = values["factor_step"]
                performance_memory = values["performance_memory"]
                assert isinstance(minimum_factor, Decimal)
                assert isinstance(maximum_factor, Decimal)
                assert isinstance(factor_step, Decimal)
                assert isinstance(performance_memory, Decimal)
                if not Decimal("0") <= minimum_factor <= Decimal("100"):
                    raise _validation_error(
                        "/minimum_factor",
                        "range",
                        "minimum_factor must be from zero through 100",
                    )
                if not Decimal("0") <= maximum_factor <= Decimal("100"):
                    raise _validation_error(
                        "/maximum_factor",
                        "range",
                        "maximum_factor must be from zero through 100",
                    )
                if minimum_factor > maximum_factor:
                    raise _validation_error(
                        "/minimum_factor",
                        "ordering",
                        "minimum_factor must not exceed maximum_factor",
                    )
                if not Decimal("0") < factor_step <= Decimal("100"):
                    raise _validation_error(
                        "/factor_step",
                        "range",
                        "factor_step must be greater than zero and at most 100",
                    )
                if not Decimal("2") <= performance_memory <= Decimal("10000"):
                    raise _validation_error(
                        "/performance_memory",
                        "range",
                        "performance_memory must be from two through 10,000",
                    )
                factor_count = int(
                    (maximum_factor - minimum_factor) // factor_step
                ) + 1
                if factor_count < 3:
                    raise _validation_error(
                        "/factor_step",
                        "minimum_candidates",
                        "supertrend_ai requires at least three factor candidates",
                    )
                if factor_count > 101:
                    raise _validation_error(
                        "/factor_step",
                        "maximum_candidates",
                        "supertrend_ai supports at most 101 factor candidates",
                    )
        if argument_type is Stage10MarketAlignArgumentsV2:
            join_contract = _field_contract(argument_type, "join")
            join = _validated_string(mapping["join"], join_contract, "/join")
            if join not in join_contract.enum:
                raise _validation_error("/join", "enum", "Unsupported join policy")
            values["join"] = join
        if argument_type is Stage10MarketTransformArgumentsV2:
            operation_contract = _field_contract(argument_type, "operation")
            operation = _validated_string(
                mapping["operation"], operation_contract, "/operation"
            )
            if operation not in operation_contract.enum:
                raise _validation_error(
                    "/operation", "enum", "Unsupported transform operation"
                )
            values["operation"] = operation
            rolling_statistic = mapping["rolling_statistic"]
            if rolling_statistic is not None:
                rolling_contract = _field_contract(
                    argument_type, "rolling_statistic"
                )
                rolling_statistic = _validated_string(
                    rolling_statistic, rolling_contract, "/rolling_statistic"
                )
                if rolling_statistic not in rolling_contract.enum:
                    raise _validation_error(
                        "/rolling_statistic",
                        "enum",
                        "Unsupported rolling statistic",
                    )
            values["rolling_statistic"] = rolling_statistic
            for field_name in ("window", "max_lag", "ljung_box_lag"):
                values[field_name] = _validated_optional_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )
            required_fields = {
                "rolling_statistic": ("rolling_statistic", "window"),
                "autocorrelation": ("max_lag",),
                "partial_autocorrelation": ("max_lag",),
                "ljung_box": ("ljung_box_lag",),
                "drawdown_episodes": (),
            }
            active = set(required_fields[operation])
            supplied = {
                name
                for name in (
                    "rolling_statistic",
                    "window",
                    "max_lag",
                    "ljung_box_lag",
                )
                if values[name] is not None
            }
            missing = active - supplied
            if missing:
                name = sorted(missing)[0]
                raise _validation_error(
                    f"/{name}",
                    "required_for_operation",
                    f"{name} is required for {operation}",
                )
            extra = supplied - active
            if extra:
                name = sorted(extra)[0]
                raise _validation_error(
                    f"/{name}",
                    "only_for_operation",
                    f"{name} is not used by {operation}",
                )
            if (
                operation == "rolling_statistic"
                and int(values["window"]) > limit
            ):
                raise _validation_error(
                    "/window",
                    "range",
                    "window cannot exceed the output limit",
                )
            if (
                operation == "ljung_box"
                and limit < max(8, 2 * int(values["ljung_box_lag"]) + 1)
            ):
                raise _validation_error(
                    "/limit",
                    "minimum",
                    "limit is too short for the fixed Ljung-Box lag",
                )
        if argument_type is Stage10BootstrapArgumentsV1:
            statistic_contract = _field_contract(argument_type, "statistic")
            statistic = _validated_string(
                mapping["statistic"], statistic_contract, "/statistic"
            )
            if statistic not in statistic_contract.enum:
                raise _validation_error(
                    "/statistic", "enum", "Unsupported bootstrap statistic"
                )
            confidence_contract = _field_contract(
                argument_type, "confidence_level"
            )
            confidence_level = _validated_string(
                mapping["confidence_level"],
                confidence_contract,
                "/confidence_level",
            )
            if confidence_level not in confidence_contract.enum:
                raise _validation_error(
                    "/confidence_level",
                    "enum",
                    "Unsupported confidence level",
                )
            values.update(
                {
                    "statistic": statistic,
                    "seed": _validated_limit(
                        mapping["seed"],
                        _field_contract(argument_type, "seed"),
                        "/seed",
                    ),
                    "replicates": _validated_limit(
                        mapping["replicates"],
                        _field_contract(argument_type, "replicates"),
                        "/replicates",
                    ),
                    "confidence_level": confidence_level,
                }
            )
        if argument_type is Stage10PrincipalComponentsArgumentsV1:
            basis_contract = _field_contract(argument_type, "basis")
            basis = _validated_string(mapping["basis"], basis_contract, "/basis")
            if basis not in basis_contract.enum:
                raise _validation_error("/basis", "enum", "Unsupported PCA basis")
            components = _validated_limit(
                mapping["components"],
                _field_contract(argument_type, "components"),
                "/components",
            )
            if components > len(raw_series):
                raise _validation_error(
                    "/components",
                    "range",
                    "components cannot exceed the supplied series count",
                )
            if not isinstance(mapping["include_scores"], bool):
                raise _validation_error(
                    "/include_scores", "type", "Expected a boolean"
                )
            if mapping["include_scores"] and limit * components > 100_000:
                raise _validation_error(
                    "/include_scores",
                    "matrix_limit",
                    "Requested PCA scores exceed the result matrix limit",
                )
            values.update(
                {
                    "basis": basis,
                    "components": components,
                    "include_scores": mapping["include_scores"],
                }
            )
        if argument_type in {
            Stage10MarketRegressionArgumentsV2,
            Stage10MarketRollingRegressionArgumentsV2,
            Stage10MarketRegressionArgumentsV21,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            if not isinstance(mapping["intercept"], bool):
                raise _validation_error("/intercept", "type", "Expected a boolean")
            values["intercept"] = mapping["intercept"]
            for field_name in ("covariance", "confidence_level"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
        if argument_type in {
            Stage10MarketRegressionArgumentsV21,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            values["hac_lag"] = _validated_limit(
                mapping["hac_lag"],
                _field_contract(argument_type, "hac_lag"),
                "/hac_lag",
            )
            values["diagnostic_lag"] = _validated_limit(
                mapping["diagnostic_lag"],
                _field_contract(argument_type, "diagnostic_lag"),
                "/diagnostic_lag",
            )
            if (
                values["covariance"] != "newey_west_hac_bartlett"
                and values["hac_lag"] != 0
            ):
                raise _validation_error(
                    "/hac_lag",
                    "only_for_newey_west",
                    "hac_lag must be zero unless Newey-West HAC is selected",
                )
        if argument_type in {
            Stage10MarketRollingRegressionArgumentsV2,
            Stage10MarketRollingRegressionArgumentsV21,
        }:
            values["window"] = _validated_limit(
                mapping["window"],
                _field_contract(argument_type, "window"),
                "/window",
            )
        if argument_type is Stage10MarketRollingRegressionArgumentsV21:
            if values["hac_lag"] >= values["window"]:
                raise _validation_error(
                    "/hac_lag",
                    "range",
                    "hac_lag must be smaller than the rolling window",
                )
            minimum_diagnostic_window = max(
                8, 2 * int(values["diagnostic_lag"]) + 1
            )
            if values["window"] < minimum_diagnostic_window:
                raise _validation_error(
                    "/window",
                    "minimum",
                    "window is too short for the fixed diagnostic lag",
                )
        if argument_type in {
            Stage10MarketStationarityArgumentsV2,
            Stage10MarketStationarityArgumentsV21,
        }:
            for field_name in ("deterministic", "significance"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
            lag_fields = (
                ("lag",)
                if argument_type is Stage10MarketStationarityArgumentsV2
                else ("adf_lag", "kpss_lag")
            )
            for field_name in lag_fields:
                values[field_name] = _validated_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )
        if argument_type is Stage10MarketStructuralBreakArgumentsV2:
            if not isinstance(mapping["intercept"], bool):
                raise _validation_error(
                    "/intercept",
                    "type",
                    "Expected a boolean",
                )
            values["intercept"] = mapping["intercept"]
            values["break_index"] = _validated_limit(
                mapping["break_index"],
                _field_contract(argument_type, "break_index"),
                "/break_index",
            )
            significance_contract = _field_contract(
                argument_type,
                "significance",
            )
            significance = _validated_string(
                mapping["significance"],
                significance_contract,
                "/significance",
            )
            if significance not in significance_contract.enum:
                raise _validation_error(
                    "/significance",
                    "enum",
                    "Unsupported significance",
                )
            values["significance"] = significance
            if values["break_index"] >= limit:
                raise _validation_error(
                    "/break_index",
                    "range",
                    "break_index must be smaller than limit",
                )
        if argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
            for field_name in ("analysis", "deterministic", "significance"):
                contract = _field_contract(argument_type, field_name)
                selected = _validated_string(
                    mapping[field_name], contract, f"/{field_name}"
                )
                if selected not in contract.enum:
                    raise _validation_error(
                        f"/{field_name}", "enum", f"Unsupported {field_name}"
                    )
                values[field_name] = selected
            values["lag_order"] = _validated_limit(
                mapping["lag_order"],
                _field_contract(argument_type, "lag_order"),
                "/lag_order",
            )
            for field_name in ("source_index", "target_index"):
                values[field_name] = _validated_limit(
                    mapping[field_name],
                    _field_contract(argument_type, field_name),
                    f"/{field_name}",
                )

            analysis = values["analysis"]
            source_index = values["source_index"]
            target_index = values["target_index"]
            if analysis == "engle_granger_cointegration":
                if len(raw_series) != 2:
                    raise _validation_error(
                        "/series",
                        "item_count",
                        "Engle-Granger requires exactly two ordered series",
                    )
                if source_index != -1 or target_index != -1:
                    raise _validation_error(
                        "/source_index",
                        "only_for_granger_causality",
                        "Engle-Granger uses series[0] on series[1]; both indices must be -1",
                    )
                if limit < 21:
                    raise _validation_error(
                        "/limit",
                        "minimum",
                        "Engle-Granger requires an observation limit of at least 21",
                    )
            elif analysis == "vector_autoregression":
                if values["lag_order"] < 1:
                    raise _validation_error(
                        "/lag_order", "minimum", "VAR lag_order must be at least one"
                    )
                if source_index != -1 or target_index != -1:
                    raise _validation_error(
                        "/source_index",
                        "only_for_granger_causality",
                        "VAR does not select source or target indices; both must be -1",
                    )
            else:
                if values["lag_order"] < 1:
                    raise _validation_error(
                        "/lag_order",
                        "minimum",
                        "Granger-causality lag_order must be at least one",
                    )
                if source_index < 0 or target_index < 0:
                    raise _validation_error(
                        "/source_index",
                        "required_for_granger_causality",
                        "Granger causality requires source_index and target_index",
                    )
                if source_index == target_index:
                    raise _validation_error(
                        "/target_index",
                        "distinct",
                        "Granger source and target indices must be distinct",
                    )
                if source_index >= len(raw_series) or target_index >= len(raw_series):
                    raise _validation_error(
                        "/source_index",
                        "range",
                        "Granger source and target indices must identify supplied series",
                    )

        return _PreparedArguments(
            argument_type,
            MappingProxyType(values),
            raw_series=raw_series,
            source_rows=max(
                (_raw_observation_count(item) for item in raw_series),
                default=0,
            ),
        )

    series_contract = _field_contract(argument_type, "series")
    raw_series = _validated_series_material(mapping["series"], series_contract, "/series")
    parameters = _validated_parameters(mapping["parameters"], argument_type, "/parameters")
    limit = _validated_limit(
        mapping["limit"], _field_contract(argument_type, "limit"), "/limit"
    )
    values: dict[str, Any] = {"parameters": parameters, "limit": limit}
    if argument_type is ResearchSeriesArguments:
        values["as_of"] = _optional_temporal(mapping["as_of"], "/as_of")
        if not isinstance(mapping["unsafe_ok"], bool):
            raise _validation_error("/unsafe_ok", "type", "Expected a boolean")
        values["unsafe_ok"] = mapping["unsafe_ok"]
    return _PreparedArguments(
        argument_type,
        MappingProxyType(values),
        raw_series=raw_series,
        source_rows=max((_raw_observation_count(item) for item in raw_series), default=0),
    )


def _decode_one(
    raw: Any, decode_series: Callable[[Any], TimeSeries], pointer: str
) -> TimeSeries:
    if isinstance(raw, TimeSeries):
        result = raw
    else:
        if not callable(decode_series):
            raise _validation_error(pointer, "decoder", "TimeSeries decoder is unavailable")
        try:
            result = decode_series(raw)
        except ValidationError:
            raise
        except Exception as exc:
            raise _validation_error(pointer, "series", "TimeSeries input is invalid") from exc
    if not isinstance(result, TimeSeries):
        raise _validation_error(pointer, "type", "TimeSeries decoder returned an invalid value")
    result.validate_lineage()
    return result


def parse_arguments(
    input_kind: str,
    public: Mapping[str, Any],
    decode_series: Callable[[Any], TimeSeries],
) -> _ArgumentMapping:
    """Validate public material and return an immutable typed Mapping.

    JSON Schema validation normally precedes this function.  It nevertheless
    rechecks every scalar/container type and semantic cross-field rule so an
    in-process caller cannot bypass validation.  Series cardinality is checked
    before any decoder invocation.
    """

    prepared = _prepared(input_kind, public)
    values = prepared.values
    if prepared.argument_type is SearchArguments:
        return SearchArguments(**dict(values))
    if prepared.argument_type is CurrentNewsSearchArgumentsV2:
        return CurrentNewsSearchArgumentsV2(**dict(values))
    if prepared.argument_type is CurrentNewsSearchArgumentsV21:
        return CurrentNewsSearchArgumentsV21(**dict(values))
    if prepared.argument_type in {
        CurrentNewsSearchArgumentsV22,
        NewsSourceStatusArgumentsV1,
        DatasetStatusArgumentsV1,
        OptionsCaptureSearchArgumentsV2,
        OptionsContractSearchArgumentsV2,
        OptionsSurfaceSnapshotArgumentsV2,
        NewsItemHistoryArgumentsV1,
        NewsAnalysisArgumentsV1,
        NewsStoryClusterArgumentsV1,
        NewsAttentionArgumentsV1,
        NewsEventImpactArgumentsV1,
    }:
        return prepared.argument_type(**dict(values))
    if prepared.argument_type is CompanyFilingSearchArgumentsV2:
        return CompanyFilingSearchArgumentsV2(**dict(values))
    if prepared.argument_type is CompanyShareCountHistoryArgumentsV2:
        return CompanyShareCountHistoryArgumentsV2(**dict(values))
    if prepared.argument_type is Stage10MarketInstrumentSearchArgumentsV2:
        return Stage10MarketInstrumentSearchArgumentsV2(**dict(values))
    if prepared.argument_type is QueryArguments:
        return QueryArguments(**dict(values))
    if prepared.argument_type is Stage10AvailableTickerArgumentsV1:
        return Stage10AvailableTickerArgumentsV1(**dict(values))
    if prepared.argument_type is Stage10MarketPriceArgumentsV1:
        return Stage10MarketPriceArgumentsV1(**dict(values))
    if prepared.argument_type is Stage10MarketVolumeArgumentsV1:
        return Stage10MarketVolumeArgumentsV1(**dict(values))
    if prepared.argument_type is CanonicalMacroSearchArgumentsV2:
        return CanonicalMacroSearchArgumentsV2(**dict(values))
    if prepared.argument_type is CanonicalMacroDescribeArgumentsV2:
        return CanonicalMacroDescribeArgumentsV2(**dict(values))
    if prepared.argument_type is CanonicalMacroSeriesArgumentsV2:
        return CanonicalMacroSeriesArgumentsV2(**dict(values))
    if prepared.argument_type is MacroReleaseCalendarArgumentsV1:
        return MacroReleaseCalendarArgumentsV1(**dict(values))
    if prepared.argument_type is MacroReleaseCalendarArgumentsV2:
        return MacroReleaseCalendarArgumentsV2(**dict(values))
    if prepared.argument_type is Stage10CrossSectionalPerformanceArgumentsV2:
        return Stage10CrossSectionalPerformanceArgumentsV2(**dict(values))
    if prepared.argument_type is Stage10CrossSectionalAnalyticsArgumentsV21:
        return Stage10CrossSectionalAnalyticsArgumentsV21(**dict(values))
    if prepared.argument_type is EnergyElectricityRetailArgumentsV2:
        return EnergyElectricityRetailArgumentsV2(**dict(values))
    if prepared.argument_type is EnergyWeeklyFundamentalsArgumentsV2:
        return EnergyWeeklyFundamentalsArgumentsV2(**dict(values))
    if prepared.argument_type is CompanyFundamentalsArgumentsV2:
        return CompanyFundamentalsArgumentsV2(**dict(values))
    if prepared.argument_type is CompanyFundamentalRatiosArgumentsV21:
        return CompanyFundamentalRatiosArgumentsV21(**dict(values))
    if prepared.argument_type in {
        MacroRevisionArgumentsV2,
        MacroSurpriseStandardizationArgumentsV2,
        MacroObservedSnapshotArgumentsV2,
        FundingConditionsArgumentsV2,
        CurveAnalyticsArgumentsV2,
        LiquidityImpulseArgumentsV2,
        MacroRegimeArgumentsV2,
    }:
        return prepared.argument_type(**dict(values))
    if prepared.argument_type is Stage10MarketReturnArgumentsV2:
        return Stage10MarketReturnArgumentsV2(**dict(values))

    decoded = tuple(
        _decode_one(
            raw,
            decode_series,
            f"/series/{index}"
            if prepared.argument_type
            in {
                Stage10MarketAlignArgumentsV2,
                Stage10MarketCorrelationArgumentsV2,
                Stage10DataQualityArgumentsV2,
                Stage10TechnicalIndicatorArgumentsV2,
                Stage10TechnicalIndicatorArgumentsV21,
                Stage10TechnicalIndicatorArgumentsV22,
                Stage10TechnicalIndicatorArgumentsV23,
                Stage10TechnicalIndicatorArgumentsV24,
                Stage10TechnicalIndicatorArgumentsV25,
                Stage10TechnicalIndicatorArgumentsV26,
                Stage10CovarianceArgumentsV1,
                Stage10PrincipalComponentsArgumentsV1,
                Stage10MarketRegressionArgumentsV2,
                Stage10MarketRollingRegressionArgumentsV2,
                Stage10MarketRegressionArgumentsV21,
                Stage10MarketRollingRegressionArgumentsV21,
                Stage10MarketStructuralBreakArgumentsV2,
                Stage10MarketRegressionModelSuiteArgumentsV3,
                MultiSeriesArguments,
                ResearchSeriesArguments,
            }
            else "/series",
        )
        for index, raw in enumerate(prepared.raw_series)
    )
    if prepared.argument_type is Stage10MarketDescribeArgumentsV2:
        return Stage10MarketDescribeArgumentsV2(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketAlignArgumentsV2:
        return Stage10MarketAlignArgumentsV2(series=decoded, **dict(values))
    if prepared.argument_type is Stage10MarketCorrelationArgumentsV2:
        return Stage10MarketCorrelationArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10DataQualityArgumentsV2:
        return Stage10DataQualityArgumentsV2(series=decoded, **dict(values))
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV2:
        return Stage10TechnicalIndicatorArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV21:
        return Stage10TechnicalIndicatorArgumentsV21(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV22:
        return Stage10TechnicalIndicatorArgumentsV22(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV23:
        return Stage10TechnicalIndicatorArgumentsV23(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV24:
        return Stage10TechnicalIndicatorArgumentsV24(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV25:
        return Stage10TechnicalIndicatorArgumentsV25(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV26:
        return Stage10TechnicalIndicatorArgumentsV26(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10TechnicalIndicatorArgumentsV27:
        return Stage10TechnicalIndicatorArgumentsV27(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketTransformArgumentsV2:
        return Stage10MarketTransformArgumentsV2(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10DistributionArgumentsV1:
        return Stage10DistributionArgumentsV1(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10BootstrapArgumentsV1:
        return Stage10BootstrapArgumentsV1(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10CovarianceArgumentsV1:
        return Stage10CovarianceArgumentsV1(series=decoded, **dict(values))
    if prepared.argument_type is Stage10PrincipalComponentsArgumentsV1:
        return Stage10PrincipalComponentsArgumentsV1(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRegressionArgumentsV2:
        return Stage10MarketRegressionArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRollingRegressionArgumentsV2:
        return Stage10MarketRollingRegressionArgumentsV2(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRegressionArgumentsV21:
        return Stage10MarketRegressionArgumentsV21(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketRollingRegressionArgumentsV21:
        return Stage10MarketRollingRegressionArgumentsV21(
            series=decoded, **dict(values)
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV2:
        return Stage10MarketStationarityArgumentsV2(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV21:
        return Stage10MarketStationarityArgumentsV21(
            series=decoded[0], **dict(values)
        )
    if prepared.argument_type is Stage10MarketStructuralBreakArgumentsV2:
        return Stage10MarketStructuralBreakArgumentsV2(
            series=decoded,
            **dict(values),
        )
    if prepared.argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
        return Stage10MarketRegressionModelSuiteArgumentsV3(
            series=decoded,
            **dict(values),
        )

    if prepared.argument_type is SingleSeriesArguments:
        return SingleSeriesArguments(series=decoded[0], **dict(values))
    if prepared.argument_type is MultiSeriesArguments:
        return MultiSeriesArguments(series=decoded, **dict(values))
    if prepared.argument_type is ResearchSeriesArguments:
        return ResearchSeriesArguments(series=decoded, **dict(values))
    raise AssertionError("Unsupported typed argument declaration")  # pragma: no cover


def _public_value(value: Any, pointer: str) -> Any:
    if isinstance(value, TimeSeries):
        value.validate_lineage()
        return _public_value(value.to_primitive(), pointer)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for name, item in value.items():
            if not isinstance(name, str):
                raise _validation_error(pointer, "key_type", "JSON object keys must be strings")
            result[name] = _public_value(item, f"{pointer}/{name}")
        return result
    if isinstance(value, (list, tuple)):
        return [_public_value(item, f"{pointer}/{index}") for index, item in enumerate(value)]
    return _validated_scalar(value, pointer)


def public_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fresh JSON-native representation without mutating ``arguments``."""

    mapping = _require_mapping(arguments, "/arguments")
    series_value = mapping.get("series")
    if isinstance(series_value, (list, tuple)) and len(series_value) > 20:
        raise _validation_error(
            "/series",
            "max_items",
            "Array exceeds the supported TimeSeries item limit",
        )
    return _public_value(mapping, "/arguments")


def _inferred_input_kind(public: Mapping[str, Any]) -> str:
    mapping = _require_mapping(public, "/arguments")
    names = set(mapping)
    research_names = {item.name for item, _ in _declared_fields(ResearchSeriesArguments)}
    multi_names = {item.name for item, _ in _declared_fields(MultiSeriesArguments)}
    single_names = {item.name for item, _ in _declared_fields(SingleSeriesArguments)}
    query_names = {item.name for item, _ in _declared_fields(QueryArguments)}
    current_news_names = {
        item.name
        for item, _ in _declared_fields(CurrentNewsSearchArgumentsV2)
    }
    current_news_v21_names = {
        item.name
        for item, _ in _declared_fields(CurrentNewsSearchArgumentsV21)
    }
    available_ticker_names = {
        item.name
        for item, _ in _declared_fields(Stage10AvailableTickerArgumentsV1)
    }
    market_price_names = {
        item.name for item, _ in _declared_fields(Stage10MarketPriceArgumentsV1)
    }
    canonical_macro_search_names = {
        item.name
        for item, _ in _declared_fields(CanonicalMacroSearchArgumentsV2)
    }
    canonical_macro_describe_names = {
        item.name
        for item, _ in _declared_fields(CanonicalMacroDescribeArgumentsV2)
    }
    canonical_macro_series_names = {
        item.name
        for item, _ in _declared_fields(CanonicalMacroSeriesArgumentsV2)
    }
    macro_calendar_names = {
        item.name
        for item, _ in _declared_fields(MacroReleaseCalendarArgumentsV1)
    }
    macro_calendar_v2_names = {
        item.name
        for item, _ in _declared_fields(MacroReleaseCalendarArgumentsV2)
    }
    cross_sectional_names = {
        item.name
        for item, _ in _declared_fields(Stage10CrossSectionalPerformanceArgumentsV2)
    }
    cross_sectional_analytics_names = {
        item.name
        for item, _ in _declared_fields(Stage10CrossSectionalAnalyticsArgumentsV21)
    }
    energy_retail_names = {
        item.name
        for item, _ in _declared_fields(EnergyElectricityRetailArgumentsV2)
    }
    energy_weekly_names = {
        item.name
        for item, _ in _declared_fields(EnergyWeeklyFundamentalsArgumentsV2)
    }
    company_fundamental_names = {
        item.name
        for item, _ in _declared_fields(CompanyFundamentalsArgumentsV2)
    }
    company_ratio_names = {
        item.name
        for item, _ in _declared_fields(CompanyFundamentalRatiosArgumentsV21)
    }
    market_return_names = {
        item.name for item, _ in _declared_fields(Stage10MarketReturnArgumentsV2)
    }
    market_describe_names = {
        item.name for item, _ in _declared_fields(Stage10MarketDescribeArgumentsV2)
    }
    market_align_names = {
        item.name for item, _ in _declared_fields(Stage10MarketAlignArgumentsV2)
    }
    market_correlation_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketCorrelationArgumentsV2)
    }
    technical_indicator_names = {
        item.name
        for item, _ in _declared_fields(Stage10TechnicalIndicatorArgumentsV2)
    }
    technical_indicator_v21_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10TechnicalIndicatorArgumentsV21
        )
    }
    technical_indicator_v22_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10TechnicalIndicatorArgumentsV22
        )
    }
    technical_indicator_v24_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10TechnicalIndicatorArgumentsV24
        )
    }
    technical_indicator_v26_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10TechnicalIndicatorArgumentsV26
        )
    }
    market_regression_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRegressionArgumentsV2)
    }
    market_rolling_regression_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRollingRegressionArgumentsV2)
    }
    market_regression_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRegressionArgumentsV21)
    }
    market_rolling_regression_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketRollingRegressionArgumentsV21)
    }
    market_stationarity_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketStationarityArgumentsV2)
    }
    market_stationarity_v21_names = {
        item.name
        for item, _ in _declared_fields(Stage10MarketStationarityArgumentsV21)
    }
    market_structural_break_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10MarketStructuralBreakArgumentsV2
        )
    }
    market_model_suite_v3_names = {
        item.name
        for item, _ in _declared_fields(
            Stage10MarketRegressionModelSuiteArgumentsV3
        )
    }

    search_names = {item.name for item, _ in _declared_fields(SearchArguments)}
    if names == research_names:
        return ResearchSeriesArguments.INPUT_KIND
    if names == multi_names:
        raw_series = mapping.get("series")
        return (
            MultiSeriesArguments.INPUT_KIND
            if isinstance(raw_series, (list, tuple))
            else SingleSeriesArguments.INPUT_KIND
        )
    if names == single_names:
        return SingleSeriesArguments.INPUT_KIND
    if names == query_names:
        return QueryArguments.INPUT_KIND
    if names <= current_news_v21_names and "source_ids" in names:
        return CurrentNewsSearchArgumentsV21.INPUT_KIND
    if names <= current_news_names and (
        "symbols" in names
        or "mode" in names
        or "date_only_policy" in names
        or "start_date" in names
        or "end_date" in names
    ):
        return CurrentNewsSearchArgumentsV2.INPUT_KIND
    if names and names <= canonical_macro_search_names:
        return CanonicalMacroSearchArgumentsV2.INPUT_KIND
    if names <= canonical_macro_describe_names and "series_id" in names:
        return CanonicalMacroDescribeArgumentsV2.INPUT_KIND
    if names <= canonical_macro_series_names and {
        "series_id",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return CanonicalMacroSeriesArgumentsV2.INPUT_KIND
    if (
        "cursor" in names
        and names <= macro_calendar_v2_names
        and {"mode", "as_of", "date_only_policy", "limit"} <= names
    ):
        return MacroReleaseCalendarArgumentsV2.INPUT_KIND
    if names <= cross_sectional_names and {
        "tickers",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return Stage10CrossSectionalPerformanceArgumentsV2.INPUT_KIND
    if names == cross_sectional_analytics_names:
        return Stage10CrossSectionalAnalyticsArgumentsV21.INPUT_KIND
    if names <= energy_retail_names and {
        "metric",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return EnergyElectricityRetailArgumentsV2.INPUT_KIND
    if names <= energy_weekly_names and {
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return EnergyWeeklyFundamentalsArgumentsV2.INPUT_KIND
    if names <= company_fundamental_names and {
        "cik",
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return CompanyFundamentalsArgumentsV2.INPUT_KIND
    if names == company_ratio_names:
        return CompanyFundamentalRatiosArgumentsV21.INPUT_KIND
    if names <= macro_calendar_names and {
        "mode",
        "as_of",
        "date_only_policy",
        "limit",
    } <= names:
        return MacroReleaseCalendarArgumentsV1.INPUT_KIND
    if names <= available_ticker_names:
        return Stage10AvailableTickerArgumentsV1.INPUT_KIND
    if names == market_price_names:
        return Stage10MarketPriceArgumentsV1.INPUT_KIND
    if names == market_return_names:
        return Stage10MarketReturnArgumentsV2.INPUT_KIND
    if names == market_align_names:
        return Stage10MarketAlignArgumentsV2.INPUT_KIND
    if names == market_describe_names:
        raw_series = mapping.get("series")
        return (
            Stage10MarketCorrelationArgumentsV2.INPUT_KIND
            if isinstance(raw_series, (list, tuple))
            else Stage10MarketDescribeArgumentsV2.INPUT_KIND
        )
    if names == market_correlation_names:
        return Stage10MarketCorrelationArgumentsV2.INPUT_KIND
    if names == technical_indicator_names:
        return Stage10TechnicalIndicatorArgumentsV2.INPUT_KIND
    if names == technical_indicator_v21_names:
        return Stage10TechnicalIndicatorArgumentsV21.INPUT_KIND
    if names == technical_indicator_v26_names:
        return (
            Stage10TechnicalIndicatorArgumentsV27.INPUT_KIND
            if mapping.get("indicator") == "rolling_regression_line"
            else Stage10TechnicalIndicatorArgumentsV26.INPUT_KIND
        )
    if names == technical_indicator_v24_names:
        return (
            Stage10TechnicalIndicatorArgumentsV25.INPUT_KIND
            if mapping.get("indicator") == "wavetrend_crosses"
            else Stage10TechnicalIndicatorArgumentsV24.INPUT_KIND
        )
    if names == technical_indicator_v22_names:
        return (
            Stage10TechnicalIndicatorArgumentsV23.INPUT_KIND
            if mapping.get("indicator") == "kdj"
            else Stage10TechnicalIndicatorArgumentsV22.INPUT_KIND
        )
    if names == market_regression_names:
        return Stage10MarketRegressionArgumentsV2.INPUT_KIND
    if names == market_rolling_regression_names:
        return Stage10MarketRollingRegressionArgumentsV2.INPUT_KIND
    if names == market_stationarity_names:
        return Stage10MarketStationarityArgumentsV2.INPUT_KIND
    if names == market_stationarity_v21_names:
        return Stage10MarketStationarityArgumentsV21.INPUT_KIND
    if names == market_structural_break_names:
        return Stage10MarketStructuralBreakArgumentsV2.INPUT_KIND
    if names == market_model_suite_v3_names:
        return Stage10MarketRegressionModelSuiteArgumentsV3.INPUT_KIND
    if names == market_regression_v21_names:
        return Stage10MarketRegressionArgumentsV21.INPUT_KIND
    if names == market_rolling_regression_v21_names:
        return Stage10MarketRollingRegressionArgumentsV21.INPUT_KIND
    if names == search_names:
        return SearchArguments.INPUT_KIND
    raise _validation_error("/arguments", "shape", "Arguments do not match a registered input shape")


def preflight_dimensions(
    public: Mapping[str, Any], *, input_kind: str | None = None
) -> dict[str, int]:
    """Derive deterministic workload dimensions without decoding TimeSeries.

    Rows cover the larger of a bounded result limit and supplied observation
    material.  Multi-series work is conservatively quadratic in its series
    count, matching the platform's correlation/alignment workload ceiling.
    """

    prepared = _prepared(input_kind or _inferred_input_kind(public), public)
    default_rows = (
        8
        if prepared.argument_type is NewsSourceStatusArgumentsV1
        else 10_000
        if prepared.argument_type is NewsEventImpactArgumentsV1
        else 1
    )
    rows = max(
        int(prepared.values.get("limit", default_rows)),
        prepared.source_rows,
    )
    series = len(prepared.raw_series)
    operations = rows * max(series, 1) ** 2
    if prepared.argument_type in {
        Stage10CrossSectionalPerformanceArgumentsV2,
        Stage10CrossSectionalAnalyticsArgumentsV21,
    }:
        rows = len(prepared.values["tickers"]) * int(
            prepared.values["limit"]
        )
        series = 1
        operations = rows
    if prepared.argument_type in {
        Stage10MarketRollingRegressionArgumentsV2,
        Stage10MarketRollingRegressionArgumentsV21,
    }:
        operations *= int(prepared.values["window"])
    if prepared.argument_type in {
        Stage10MarketRegressionArgumentsV21,
        Stage10MarketRollingRegressionArgumentsV21,
    }:
        operations *= 1 + max(
            int(prepared.values["hac_lag"]),
            int(prepared.values["diagnostic_lag"]),
        )
    if prepared.argument_type is Stage10MarketStationarityArgumentsV21:
        operations *= 1 + int(prepared.values["adf_lag"])
        operations += rows * int(prepared.values["kpss_lag"])
    if prepared.argument_type is Stage10MarketStructuralBreakArgumentsV2:
        operations *= 3
    if prepared.argument_type in {
        Stage10TechnicalIndicatorArgumentsV2,
        Stage10TechnicalIndicatorArgumentsV21,
        Stage10TechnicalIndicatorArgumentsV22,
        Stage10TechnicalIndicatorArgumentsV23,
        Stage10TechnicalIndicatorArgumentsV24,
        Stage10TechnicalIndicatorArgumentsV25,
        Stage10TechnicalIndicatorArgumentsV26,
    }:
        output_count = {
            "macd": 3,
            "bollinger_bands": 3,
            "donchian_channels": 3,
            "stochastic_oscillator": 2,
            "kdj": 3,
            "williams_vix_fix": 4,
            "wavetrend_crosses": 4,
            "average_directional_index": 3,
            "supertrend_ai": 5,
            "swing_structure_forecast": 10,
        }.get(str(prepared.values["indicator"]), 1)
        effective_window = max(
            (
                int(prepared.values[name])
                for name in (
                    "window",
                    "fast_window",
                    "slow_window",
                    "signal_window",
                    "percentile_window",
                )
                if prepared.values.get(name) is not None
            ),
            default=1,
        )
        if prepared.values["indicator"] == "wavetrend_crosses":
            effective_window = max(effective_window, 4)
        if prepared.values["indicator"] == "supertrend_ai":
            minimum_factor = Decimal(prepared.values["minimum_factor"])
            maximum_factor = Decimal(prepared.values["maximum_factor"])
            factor_step = Decimal(prepared.values["factor_step"])
            factor_count = int(
                (maximum_factor - minimum_factor) // factor_step
            ) + 1
            partition_bound = (
                (factor_count + 1) * (factor_count + 2) // 2
            )
            assignment_count = min(1_001, partition_bound)
            operations = rows * (
                output_count + factor_count * assignment_count
            )
        elif prepared.values["indicator"] == "swing_structure_forecast":
            operations = rows * (
                series
                + output_count
                + int(prepared.values["sample_count"])
            )
        else:
            operations = rows * (
                series + output_count * effective_window
            )
    if prepared.argument_type is Stage10MarketTransformArgumentsV2:
        if prepared.values["operation"] == "rolling_statistic":
            operations *= int(prepared.values["window"])
        elif prepared.values["operation"] in {
            "autocorrelation",
            "partial_autocorrelation",
        }:
            operations *= int(prepared.values["max_lag"]) + 1
        elif prepared.values["operation"] == "ljung_box":
            operations *= int(prepared.values["ljung_box_lag"]) + 1
    if prepared.argument_type is Stage10BootstrapArgumentsV1:
        operations *= int(prepared.values["replicates"])
    if prepared.argument_type is Stage10PrincipalComponentsArgumentsV1:
        operations += max(series, 1) ** 3
    if prepared.argument_type is Stage10MarketRegressionModelSuiteArgumentsV3:
        lag_order = int(prepared.values["lag_order"])
        if prepared.values["analysis"] == "engle_granger_cointegration":
            operations = rows * (lag_order + 2) ** 2
        else:
            parameter_count = 1 + series * lag_order
            operations = (
                rows * parameter_count**2
                + series * rows * parameter_count
                + series * parameter_count**2
            )
            if prepared.values["analysis"] == "granger_causality":
                restricted_count = parameter_count - lag_order
                operations += rows * restricted_count**2

    return {"rows": rows, "series": series, "operations": operations}


__all__ = [
    "ArgumentParameter",
    "SearchArguments",
    "CurrentNewsSearchArgumentsV2",
    "CurrentNewsSearchArgumentsV21",
    "CurrentNewsSearchArgumentsV22",
    "NewsSourceStatusArgumentsV1",
    "DatasetStatusArgumentsV1",
    "OptionsCaptureSearchArgumentsV2",
    "OptionsContractSearchArgumentsV2",
    "OptionsSurfaceSnapshotArgumentsV2",
    "NewsItemHistoryArgumentsV1",
    "NewsAnalysisArgumentsV1",
    "NewsStoryClusterArgumentsV1",
    "NewsAttentionArgumentsV1",
    "NewsEventImpactArgumentsV1",
    "CompanyShareCountHistoryArgumentsV2",
    "CompanyFilingSearchArgumentsV2",
    "Stage10MarketInstrumentSearchArgumentsV2",
    "QueryArguments",
    "Stage10AvailableTickerArgumentsV1",
    "Stage10MarketPriceArgumentsV1",
    "Stage10MarketVolumeArgumentsV1",
    "Stage10TechnicalIndicatorArgumentsV2",
    "Stage10TechnicalIndicatorArgumentsV21",
    "Stage10TechnicalIndicatorArgumentsV22",
    "Stage10TechnicalIndicatorArgumentsV23",
    "Stage10TechnicalIndicatorArgumentsV24",
    "Stage10TechnicalIndicatorArgumentsV25",
    "Stage10TechnicalIndicatorArgumentsV26",
    "Stage10TechnicalIndicatorArgumentsV27",
    "CanonicalMacroSearchArgumentsV2",
    "CanonicalMacroDescribeArgumentsV2",
    "CanonicalMacroSeriesArgumentsV2",
    "MacroReleaseCalendarArgumentsV1",
    "MacroReleaseCalendarArgumentsV2",
    "Stage10CrossSectionalPerformanceArgumentsV2",
    "Stage10CrossSectionalAnalyticsArgumentsV21",
    "EnergyElectricityRetailArgumentsV2",
    "EnergyWeeklyFundamentalsArgumentsV2",
    "CompanyFundamentalsArgumentsV2",
    "CompanyFundamentalRatiosArgumentsV21",
    "MacroRevisionArgumentsV2",
    "MacroSurpriseStandardizationArgumentsV2",
    "MacroObservedSnapshotArgumentsV2",
    "FundingConditionsArgumentsV2",
    "CurveAnalyticsArgumentsV2",
    "LiquidityImpulseArgumentsV2",
    "MacroRegimeArgumentsV2",
    "Stage10MarketReturnArgumentsV2",
    "Stage10MarketDescribeArgumentsV2",
    "Stage10MarketAlignArgumentsV2",
    "Stage10MarketCorrelationArgumentsV2",
    "Stage10DataQualityArgumentsV2",
    "Stage10MarketTransformArgumentsV2",
    "Stage10DistributionArgumentsV1",
    "Stage10BootstrapArgumentsV1",
    "Stage10CovarianceArgumentsV1",
    "Stage10PrincipalComponentsArgumentsV1",
    "Stage10MarketRegressionArgumentsV2",
    "Stage10MarketRollingRegressionArgumentsV2",
    "Stage10MarketStationarityArgumentsV2",
    "Stage10MarketStationarityArgumentsV21",
    "Stage10MarketStructuralBreakArgumentsV2",
    "Stage10MarketRegressionModelSuiteArgumentsV3",
    "SingleSeriesArguments",
    "Stage10MarketRegressionArgumentsV21",
    "Stage10MarketRollingRegressionArgumentsV21",
    "MultiSeriesArguments",
    "ResearchSeriesArguments",
    "input_schema",
    "parse_arguments",
    "public_arguments",
    "preflight_dimensions",
]
