"""Strict immutable loader for the reviewed Stage 10 market scope.

The scope is a deliberately narrow, provider-facing manifest.  This module
does not discover a path, read credentials, construct headers, or make network
requests: a caller must supply the one file it intends to validate.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from ..errors import Issue, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..temporal import parse_date


STAGE10_SCOPE_CONTRACT = "quant_data.stage10_market_scope"
STAGE10_SCOPE_VERSION = "1.0.0"
STAGE10_SCOPE_PROVIDER = "fmp"
STAGE10_TARGET_PROFILE_ID = "stage10_fmp_market_history_v1"

# This is the SHA-256 of the reviewed semantic manifest rendered with
# dumps_strict.  Whitespace and JSON object-key order are immaterial, while
# every declared scope value remains pinned.
REVIEWED_STAGE10_MANIFEST_SHA256 = (
    "0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd"
)

_EXPECTED_BOUNDS = (
    ("max_constituent_rows", 600),
    ("max_instruments", 800),
    ("max_price_response_bytes", 16_777_216),
    ("max_price_rows_per_instrument", 30_000),
    ("max_seconds_per_request", 45),
    ("minimum_request_interval_milliseconds", 1_000),
)
_EXPECTED_PRICE_HISTORY = (
    ("currency_policy", "provider_declared_no_conversion"),
    ("endpoint_path", "/stable/historical-price-eod/full"),
    ("history_policy", "earliest_available_per_provider_symbol"),
    ("interval", "daily"),
    ("price_variant", "fmp_full_eod_v1"),
    ("volume_policy", "provider_value_nonnegative_zero_allowed"),
)
_EXPECTED_UNIVERSE_SOURCE_IDS = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
)
_EXPECTED_UNIVERSE_SOURCE_SIGNATURES = (
    (
        "sp500_current",
        "sp500",
        "/stable/sp500-constituent",
        500,
        510,
        "provider_claimed_constituent",
        "sp_global_sp_dji",
        "commercial_provider",
        ("AAPL", "MSFT", "NVDA"),
        (),
    ),
    (
        "nasdaq100_current",
        "nasdaq100",
        "/stable/nasdaq-constituent",
        100,
        102,
        "provider_claimed_constituent_reconciled_to_official_change_notices",
        "nasdaq_global_indexes",
        "commercial_provider",
        ("ALAB", "CRWV", "LITE", "NBIS", "RKLB", "SNDK", "SPCX", "TER", "WMT"),
        ("AZN", "CHTR", "CSGP", "CTSH", "INSM", "TEAM", "VRSK", "ZS"),
    ),
    (
        "dow30_current",
        "djia",
        "/stable/dowjones-constituent",
        30,
        30,
        "provider_claimed_constituent",
        "sp_global_sp_dji",
        "commercial_provider",
        ("AAPL", "MSFT", "JPM"),
        (),
    ),
)
_EXPECTED_ETF_EXCEPTIONS = (
    (
        "DRAM",
        "thematic_industry",
        ("semiconductor_memory", "dram"),
        "2026-04-02",
    ),
    (
        "EUV",
        "thematic_industry",
        ("extreme_ultraviolet_lithography", "semiconductor_photonics"),
        "2026-05-06",
    ),
)
_EXPECTED_INDEX_IDENTITIES = (
    ("sp500_price", "^GSPC"),
    ("nasdaq100_price", "^NDX"),
    ("djia_price", "^DJI"),
    ("nasdaq_composite_price", "^IXIC"),
    ("vix", "^VIX"),
    ("tsx_composite_price", "^GSPTSE"),
    ("ftse100_price", "^FTSE"),
    ("dax_price", "^GDAXI"),
    ("cac40_price", "^FCHI"),
    ("euro_stoxx50_price", "^STOXX50E"),
    ("nikkei225_price", "^N225"),
    ("hang_seng_price", "^HSI"),
    ("asx200_price", "^AXJO"),
    ("kospi_price", "^KS11"),
    ("taiwan_weighted_price", "^TWII"),
)
_VALID_ETF_CATEGORIES = frozenset(
    {
        "asset_class_equity_us",
        "asset_class_equity_international",
        "asset_class_fixed_income",
        "asset_class_commodities",
        "asset_class_real_estate",
        "asset_class_currency",
        "asset_class_digital_assets",
        "sector_equity",
        "thematic_industry",
    }
)
_VALID_ETF_THEMES = frozenset(
    {
        "additive_manufacturing",
        "aerospace",
        "agribusiness",
        "agricultural_commodities",
        "artificial_intelligence",
        "automation",
        "autonomy",
        "batteries",
        "big_data",
        "biotechnology",
        "bitcoin",
        "blockchain",
        "broad_commodities",
        "clean_energy",
        "cloud_computing",
        "cloud_software",
        "communication_services",
        "consumer_discretionary",
        "consumer_staples",
        "convertible_bonds",
        "crude_oil",
        "cybersecurity",
        "defense",
        "developed_ex_us",
        "dram",
        "electric_vehicles",
        "emerging_markets",
        "energy",
        "ether",
        "euro",
        "extreme_ultraviolet_lithography",
        "financials",
        "fintech",
        "genomics",
        "global_equity",
        "gold",
        "health_care",
        "high_yield_credit",
        "industrials",
        "information_technology",
        "infrastructure",
        "investment_grade_credit",
        "japanese_yen",
        "lithium",
        "machine_learning",
        "materials",
        "municipal_bonds",
        "nuclear_energy",
        "preferred_securities",
        "quantum_computing",
        "real_estate",
        "robotics",
        "semiconductor_memory",
        "semiconductor_photonics",
        "semiconductors",
        "senior_loans",
        "silver",
        "software",
        "solar_energy",
        "space_economy",
        "uranium",
        "us_aggregate_bonds",
        "us_blue_chip",
        "us_dollar",
        "us_equal_weight_large_cap",
        "us_inflation_linked",
        "us_large_cap",
        "us_large_cap_growth",
        "us_real_estate",
        "us_small_cap",
        "us_total_market",
        "us_treasury_bills",
        "us_treasury_intermediate",
        "us_treasury_long",
        "us_treasury_short",
        "utilities",
        "water",
    }
)
_VALID_INDEX_REGIONS = frozenset(
    {
        "us",
        "canada",
        "uk",
        "germany",
        "france",
        "eurozone",
        "japan",
        "hong_kong",
        "australia",
        "south_korea",
        "taiwan",
    }
)
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ETF_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,14}$")
_INDEX_PROVIDER_SYMBOL = re.compile(r"^\^[A-Z][A-Z0-9]{0,14}$")
_SENSITIVE_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "header",
    "url",
    "uri",
    "path",
    "apikey",
)


@dataclass(frozen=True, slots=True)
class Stage10Bounds:
    max_constituent_rows: int
    max_instruments: int
    max_price_response_bytes: int
    max_price_rows_per_instrument: int
    max_seconds_per_request: int
    minimum_request_interval_milliseconds: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "max_constituent_rows": self.max_constituent_rows,
            "max_instruments": self.max_instruments,
            "max_price_response_bytes": self.max_price_response_bytes,
            "max_price_rows_per_instrument": self.max_price_rows_per_instrument,
            "max_seconds_per_request": self.max_seconds_per_request,
            "minimum_request_interval_milliseconds": self.minimum_request_interval_milliseconds,
        }


@dataclass(frozen=True, slots=True)
class UniverseSource:
    canonical_index_id: str
    endpoint_path: str
    expected_members_max: int
    expected_members_min: int
    id: str
    membership_relation: str
    publisher: str
    required_symbols: tuple[str, ...]
    source_authority: str
    forbidden_symbols: tuple[str, ...] = ()

    def manifest_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "canonical_index_id": self.canonical_index_id,
            "endpoint_path": self.endpoint_path,
            "expected_members_max": self.expected_members_max,
            "expected_members_min": self.expected_members_min,
            "id": self.id,
            "membership_relation": self.membership_relation,
            "publisher": self.publisher,
            "required_symbols": list(self.required_symbols),
            "source_authority": self.source_authority,
        }
        if self.forbidden_symbols:
            result["forbidden_symbols"] = list(self.forbidden_symbols)
        return result


@dataclass(frozen=True, slots=True)
class CuratedEtf:
    category: str
    symbol: str
    themes: tuple[str, ...]
    first_trade_date: str | None = None

    def manifest_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "category": self.category,
            "symbol": self.symbol,
            "themes": list(self.themes),
        }
        if self.first_trade_date is not None:
            result["first_trade_date"] = self.first_trade_date
        return result


@dataclass(frozen=True, slots=True)
class MajorIndex:
    canonical_id: str
    name: str
    provider_symbol: str
    region: str
    return_variant: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "canonical_id": self.canonical_id,
            "name": self.name,
            "provider_symbol": self.provider_symbol,
            "region": self.region,
            "return_variant": self.return_variant,
        }


@dataclass(frozen=True, slots=True)
class PriceHistory:
    currency_policy: str
    endpoint_path: str
    history_policy: str
    interval: str
    price_variant: str
    query_fields: tuple[str, ...]
    volume_policy: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "currency_policy": self.currency_policy,
            "endpoint_path": self.endpoint_path,
            "history_policy": self.history_policy,
            "interval": self.interval,
            "price_variant": self.price_variant,
            "query_fields": list(self.query_fields),
            "volume_policy": self.volume_policy,
        }


@dataclass(frozen=True, slots=True)
class Stage10MarketScope:
    bounds: Stage10Bounds
    contract: str
    curated_etfs: tuple[CuratedEtf, ...]
    major_indexes: tuple[MajorIndex, ...]
    price_history: PriceHistory
    provider: str
    target_profile_id: str
    universe_sources: tuple[UniverseSource, ...]
    version: str
    manifest_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "bounds": self.bounds.manifest_mapping(),
            "contract": self.contract,
            "curated_etfs": [item.manifest_mapping() for item in self.curated_etfs],
            "major_indexes": [item.manifest_mapping() for item in self.major_indexes],
            "price_history": self.price_history.manifest_mapping(),
            "provider": self.provider,
            "target_profile_id": self.target_profile_id,
            "universe_sources": [item.manifest_mapping() for item in self.universe_sources],
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        result = self.manifest_mapping()
        result["manifest_sha256"] = self.manifest_sha256
        return result


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _child_pointer(pointer: str, part: str | int) -> str:
    return f"{pointer}/{part}" if pointer else f"/{part}"


def _mapping(raw: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise _error(pointer, "type", "Expected an object with string keys")
    return raw


def _array(raw: object, *, pointer: str) -> list[Any]:
    if not isinstance(raw, list):
        raise _error(pointer, "type", "Expected an array")
    return raw


def _closed_object(
    raw: Mapping[str, Any],
    *,
    pointer: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    fields = set(raw)
    if not required.issubset(fields) or not fields.issubset(required | optional):
        raise _error(pointer, "shape", "Object fields do not match the reviewed scope")


def _text(raw: object, *, pointer: str) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or raw != raw.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise _error(pointer, "type", "Expected a nonempty trimmed text value")
    return raw


def _token(raw: object, *, pointer: str) -> str:
    value = _text(raw, pointer=pointer)
    if _TOKEN.fullmatch(value) is None:
        raise _error(pointer, "format", "Expected a lowercase identifier token")
    return value


def _etf_symbol(raw: object, *, pointer: str) -> str:
    value = _text(raw, pointer=pointer)
    if _ETF_SYMBOL.fullmatch(value) is None:
        raise _error(pointer, "format", "Expected an uppercase ETF or constituent symbol")
    return value


def _index_provider_symbol(raw: object, *, pointer: str) -> str:
    value = _text(raw, pointer=pointer)
    if _INDEX_PROVIDER_SYMBOL.fullmatch(value) is None:
        raise _error(pointer, "format", "Expected a caret-prefixed provider index symbol")
    return value


def _integer(raw: object, *, pointer: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _error(pointer, "type", "Expected an integer")
    return raw


def _endpoint_path(raw: object, *, pointer: str) -> str:
    value = _text(raw, pointer=pointer)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "://" in value
        or "?" in value
        or "#" in value
    ):
        raise _error(pointer, "format", "Expected a relative provider endpoint path")
    return value


def _symbol_list(
    raw: object,
    *,
    pointer: str,
    minimum: int,
    maximum: int,
) -> tuple[str, ...]:
    values = _array(raw, pointer=pointer)
    if not minimum <= len(values) <= maximum:
        raise _error(pointer, "length", "Symbol list is outside the reviewed bound")
    parsed = tuple(
        _etf_symbol(item, pointer=_child_pointer(pointer, index))
        for index, item in enumerate(values)
    )
    if len(set(parsed)) != len(parsed):
        raise _error(pointer, "unique", "Symbol list contains a duplicate")
    return parsed


def _theme_list(raw: object, *, pointer: str) -> tuple[str, ...]:
    values = _array(raw, pointer=pointer)
    if not 1 <= len(values) <= 8:
        raise _error(pointer, "length", "ETF themes are outside the reviewed bound")
    parsed = tuple(
        _token(item, pointer=_child_pointer(pointer, index))
        for index, item in enumerate(values)
    )
    if len(set(parsed)) != len(parsed):
        raise _error(pointer, "unique", "ETF themes contain a duplicate")
    if not set(parsed).issubset(_VALID_ETF_THEMES):
        raise _error(pointer, "enum", "ETF themes include an unreviewed value")
    return parsed


def _date_only(raw: object, *, pointer: str) -> str:
    return parse_date(_text(raw, pointer=pointer), pointer=pointer).isoformat()


def _looks_sensitive_or_path_like(key: str) -> bool:
    lowered = key.casefold()
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    return any(
        marker in lowered or marker in compact for marker in _SENSITIVE_KEY_MARKERS
    )


def _reject_sensitive_or_path_like_keys(raw: object, *, pointer: str) -> None:
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Object keys must be strings")
            key_pointer = _child_pointer(pointer, key)
            if key != "endpoint_path" and _looks_sensitive_or_path_like(key):
                raise _error(
                    key_pointer,
                    "forbidden_key",
                    "Secrets, headers, URLs, and paths are not scope fields",
                )
            _reject_sensitive_or_path_like_keys(value, pointer=key_pointer)
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            _reject_sensitive_or_path_like_keys(
                value,
                pointer=_child_pointer(pointer, index),
            )


def _reject_russell_universe(raw: object, *, pointer: str) -> None:
    if isinstance(raw, str):
        if "russell" in raw.casefold():
            raise _error(
                pointer,
                "forbidden_universe",
                "Russell constituent universes are outside the approved Stage 10 scope",
            )
        return
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(key, str):
                _reject_russell_universe(value, pointer=_child_pointer(pointer, key))
        return
    if isinstance(raw, list):
        for index, value in enumerate(raw):
            _reject_russell_universe(value, pointer=_child_pointer(pointer, index))


def _parse_bounds(raw: object) -> Stage10Bounds:
    pointer = "/bounds"
    value = _mapping(raw, pointer=pointer)
    expected_fields = frozenset(field for field, _ in _EXPECTED_BOUNDS)
    _closed_object(value, pointer=pointer, required=expected_fields)
    parsed: dict[str, int] = {}
    for field, expected in _EXPECTED_BOUNDS:
        actual = _integer(value[field], pointer=_child_pointer(pointer, field))
        if actual != expected:
            raise _error(
                _child_pointer(pointer, field),
                "constant",
                "Bounds must match the reviewed Stage 10 value",
            )
        parsed[field] = actual
    return Stage10Bounds(
        max_constituent_rows=parsed["max_constituent_rows"],
        max_instruments=parsed["max_instruments"],
        max_price_response_bytes=parsed["max_price_response_bytes"],
        max_price_rows_per_instrument=parsed["max_price_rows_per_instrument"],
        max_seconds_per_request=parsed["max_seconds_per_request"],
        minimum_request_interval_milliseconds=parsed[
            "minimum_request_interval_milliseconds"
        ],
    )


def _parse_price_history(raw: object) -> PriceHistory:
    pointer = "/price_history"
    value = _mapping(raw, pointer=pointer)
    required = frozenset(
        {
            "currency_policy",
            "endpoint_path",
            "history_policy",
            "interval",
            "price_variant",
            "query_fields",
            "volume_policy",
        }
    )
    _closed_object(value, pointer=pointer, required=required)
    parsed: dict[str, str] = {}
    for field, expected in _EXPECTED_PRICE_HISTORY:
        item_pointer = _child_pointer(pointer, field)
        actual = (
            _endpoint_path(value[field], pointer=item_pointer)
            if field == "endpoint_path"
            else _text(value[field], pointer=item_pointer)
        )
        if actual != expected:
            raise _error(
                item_pointer,
                "constant",
                "Price-history settings must match the reviewed Stage 10 value",
            )
        parsed[field] = actual
    raw_fields = _array(value["query_fields"], pointer="/price_history/query_fields")
    if len(raw_fields) != 1:
        raise _error(
            "/price_history/query_fields",
            "length",
            "Price-history query fields must contain exactly one value",
        )
    query_names = tuple(
        _token(item, pointer=_child_pointer("/price_history/query_fields", index))
        for index, item in enumerate(raw_fields)
    )
    if query_names != ("symbol",):
        raise _error(
            "/price_history/query_fields",
            "constant",
            "Price-history query fields must be exactly symbol",
        )
    return PriceHistory(
        currency_policy=parsed["currency_policy"],
        endpoint_path=parsed["endpoint_path"],
        history_policy=parsed["history_policy"],
        interval=parsed["interval"],
        price_variant=parsed["price_variant"],
        query_fields=query_names,
        volume_policy=parsed["volume_policy"],
    )


def _parse_universe_source(raw: object, *, pointer: str) -> UniverseSource:
    _reject_russell_universe(raw, pointer=pointer)
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        required=frozenset(
            {
                "canonical_index_id",
                "endpoint_path",
                "expected_members_max",
                "expected_members_min",
                "id",
                "membership_relation",
                "publisher",
                "required_symbols",
                "source_authority",
            }
        ),
        optional=frozenset({"forbidden_symbols"}),
    )
    expected_members_min = _integer(
        value["expected_members_min"],
        pointer=_child_pointer(pointer, "expected_members_min"),
    )
    expected_members_max = _integer(
        value["expected_members_max"],
        pointer=_child_pointer(pointer, "expected_members_max"),
    )
    if expected_members_min <= 0 or expected_members_max < expected_members_min:
        raise _error(
            pointer,
            "range",
            "Universe member bounds must be positive and non-inverted",
        )
    required_symbols = _symbol_list(
        value["required_symbols"],
        pointer=_child_pointer(pointer, "required_symbols"),
        minimum=1,
        maximum=16,
    )
    forbidden_symbols = (
        _symbol_list(
            value["forbidden_symbols"],
            pointer=_child_pointer(pointer, "forbidden_symbols"),
            minimum=1,
            maximum=16,
        )
        if "forbidden_symbols" in value
        else ()
    )
    if set(required_symbols) & set(forbidden_symbols):
        raise _error(
            pointer,
            "disjoint",
            "Required and forbidden universe symbols must be disjoint",
        )
    return UniverseSource(
        canonical_index_id=_token(
            value["canonical_index_id"],
            pointer=_child_pointer(pointer, "canonical_index_id"),
        ),
        endpoint_path=_endpoint_path(
            value["endpoint_path"],
            pointer=_child_pointer(pointer, "endpoint_path"),
        ),
        expected_members_max=expected_members_max,
        expected_members_min=expected_members_min,
        id=_token(value["id"], pointer=_child_pointer(pointer, "id")),
        membership_relation=_text(
            value["membership_relation"],
            pointer=_child_pointer(pointer, "membership_relation"),
        ),
        publisher=_token(
            value["publisher"],
            pointer=_child_pointer(pointer, "publisher"),
        ),
        required_symbols=required_symbols,
        source_authority=_token(
            value["source_authority"],
            pointer=_child_pointer(pointer, "source_authority"),
        ),
        forbidden_symbols=forbidden_symbols,
    )


def _universe_source_signature(source: UniverseSource) -> tuple[object, ...]:
    return (
        source.id,
        source.canonical_index_id,
        source.endpoint_path,
        source.expected_members_min,
        source.expected_members_max,
        source.membership_relation,
        source.publisher,
        source.source_authority,
        source.required_symbols,
        source.forbidden_symbols,
    )


def _parse_universe_sources(raw: object) -> tuple[UniverseSource, ...]:
    pointer = "/universe_sources"
    values = _array(raw, pointer=pointer)
    for index, item in enumerate(values):
        _reject_russell_universe(item, pointer=_child_pointer(pointer, index))
    if len(values) != 3:
        raise _error(pointer, "length", "Exactly three constituent universes are required")
    sources = tuple(
        _parse_universe_source(item, pointer=_child_pointer(pointer, index))
        for index, item in enumerate(values)
    )
    if tuple(item.id for item in sources) != _EXPECTED_UNIVERSE_SOURCE_IDS:
        raise _error(
            pointer,
            "order",
            "Universe sources must be sp500_current, nasdaq100_current, dow30_current",
        )
    if len({item.canonical_index_id for item in sources}) != len(sources):
        raise _error(pointer, "unique", "Universe canonical IDs must be unique")
    if len({item.endpoint_path for item in sources}) != len(sources):
        raise _error(pointer, "unique", "Universe endpoint paths must be unique")
    if tuple(_universe_source_signature(item) for item in sources) != (
        _EXPECTED_UNIVERSE_SOURCE_SIGNATURES
    ):
        raise _error(
            pointer,
            "immutable",
            "Universe sources differ from the reviewed Stage 10 scope",
        )
    return sources


def _parse_etf(raw: object, *, pointer: str) -> CuratedEtf:
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        required=frozenset({"category", "symbol", "themes"}),
        optional=frozenset({"first_trade_date"}),
    )
    category = _token(value["category"], pointer=_child_pointer(pointer, "category"))
    if category not in _VALID_ETF_CATEGORIES:
        raise _error(
            _child_pointer(pointer, "category"),
            "enum",
            "ETF category is not in the reviewed Stage 10 taxonomy",
        )
    return CuratedEtf(
        category=category,
        symbol=_etf_symbol(value["symbol"], pointer=_child_pointer(pointer, "symbol")),
        themes=_theme_list(value["themes"], pointer=_child_pointer(pointer, "themes")),
        first_trade_date=(
            _date_only(
                value["first_trade_date"],
                pointer=_child_pointer(pointer, "first_trade_date"),
            )
            if "first_trade_date" in value
            else None
        ),
    )


def _parse_curated_etfs(raw: object) -> tuple[CuratedEtf, ...]:
    pointer = "/curated_etfs"
    values = _array(raw, pointer=pointer)
    if len(values) != 95:
        raise _error(pointer, "length", "Exactly 95 curated ETFs are required")
    etfs = tuple(
        _parse_etf(item, pointer=_child_pointer(pointer, index))
        for index, item in enumerate(values)
    )
    if len({item.symbol for item in etfs}) != len(etfs):
        raise _error(pointer, "unique", "Curated ETF symbols must be unique")
    by_symbol = {item.symbol: item for item in etfs}
    for symbol, category, themes, first_trade_date in _EXPECTED_ETF_EXCEPTIONS:
        item = by_symbol.get(symbol)
        if item is None:
            raise _error(pointer, "required", f"{symbol} must be a curated ETF")
        if (
            item.category != category
            or item.themes != themes
            or item.first_trade_date != first_trade_date
        ):
            raise _error(
                pointer,
                "immutable",
                f"{symbol} must retain its reviewed category, themes, and first-trade date",
            )
    return etfs


def _parse_major_index(raw: object, *, pointer: str) -> MajorIndex:
    value = _mapping(raw, pointer=pointer)
    _closed_object(
        value,
        pointer=pointer,
        required=frozenset(
            {
                "canonical_id",
                "name",
                "provider_symbol",
                "region",
                "return_variant",
            }
        ),
    )
    region = _token(value["region"], pointer=_child_pointer(pointer, "region"))
    if region not in _VALID_INDEX_REGIONS:
        raise _error(
            _child_pointer(pointer, "region"),
            "enum",
            "Major-index region is not in the reviewed Stage 10 taxonomy",
        )
    return_variant = _token(
        value["return_variant"],
        pointer=_child_pointer(pointer, "return_variant"),
    )
    if return_variant != "price":
        raise _error(
            _child_pointer(pointer, "return_variant"),
            "constant",
            "Major indexes must use price-return variants",
        )
    result = MajorIndex(
        canonical_id=_token(
            value["canonical_id"],
            pointer=_child_pointer(pointer, "canonical_id"),
        ),
        name=_text(value["name"], pointer=_child_pointer(pointer, "name")),
        provider_symbol=_index_provider_symbol(
            value["provider_symbol"],
            pointer=_child_pointer(pointer, "provider_symbol"),
        ),
        region=region,
        return_variant=return_variant,
    )
    if "russell" in result.canonical_id or result.provider_symbol == "^RUT":
        raise _error(
            pointer,
            "forbidden_index",
            "Russell indexes are outside the approved Stage 10 scope",
        )
    return result


def _parse_major_indexes(raw: object) -> tuple[MajorIndex, ...]:
    pointer = "/major_indexes"
    values = _array(raw, pointer=pointer)
    if len(values) != 15:
        raise _error(pointer, "length", "Exactly 15 major indexes are required")
    indexes = tuple(
        _parse_major_index(item, pointer=_child_pointer(pointer, index))
        for index, item in enumerate(values)
    )
    if len({item.canonical_id for item in indexes}) != len(indexes):
        raise _error(pointer, "unique", "Major-index canonical IDs must be unique")
    if len({item.provider_symbol for item in indexes}) != len(indexes):
        raise _error(pointer, "unique", "Major-index provider symbols must be unique")
    if len({item.name for item in indexes}) != len(indexes):
        raise _error(pointer, "unique", "Major-index names must be unique")
    if tuple((item.canonical_id, item.provider_symbol) for item in indexes) != (
        _EXPECTED_INDEX_IDENTITIES
    ):
        raise _error(
            pointer,
            "immutable",
            "Major-index canonical and provider symbols differ from the reviewed scope",
        )
    return indexes


def _parse_scope(raw: object) -> Stage10MarketScope:
    value = _mapping(raw, pointer="/")
    _closed_object(
        value,
        pointer="/",
        required=frozenset(
            {
                "bounds",
                "contract",
                "curated_etfs",
                "major_indexes",
                "price_history",
                "provider",
                "target_profile_id",
                "universe_sources",
                "version",
            }
        ),
    )
    contract = _text(value["contract"], pointer="/contract")
    if contract != STAGE10_SCOPE_CONTRACT:
        raise _error("/contract", "constant", "Unexpected Stage 10 scope contract")
    version = _text(value["version"], pointer="/version")
    if version != STAGE10_SCOPE_VERSION:
        raise _error("/version", "constant", "Unexpected Stage 10 scope version")
    provider = _text(value["provider"], pointer="/provider")
    if provider != STAGE10_SCOPE_PROVIDER:
        raise _error("/provider", "constant", "Unexpected Stage 10 scope provider")
    target_profile_id = _token(
        value["target_profile_id"],
        pointer="/target_profile_id",
    )
    if target_profile_id != STAGE10_TARGET_PROFILE_ID:
        raise _error(
            "/target_profile_id",
            "constant",
            "Unexpected Stage 10 target profile",
        )
    scope = Stage10MarketScope(
        bounds=_parse_bounds(value["bounds"]),
        contract=contract,
        curated_etfs=_parse_curated_etfs(value["curated_etfs"]),
        major_indexes=_parse_major_indexes(value["major_indexes"]),
        price_history=_parse_price_history(value["price_history"]),
        provider=provider,
        target_profile_id=target_profile_id,
        universe_sources=_parse_universe_sources(value["universe_sources"]),
        version=version,
        manifest_sha256="",
    )
    if dumps_strict(value) != dumps_strict(scope.manifest_mapping()):
        raise _error(
            "/",
            "canonical",
            "Scope cannot be normalized to the reviewed closed schema",
        )
    manifest_sha256 = hashlib.sha256(
        dumps_strict(scope.manifest_mapping()).encode("utf-8")
    ).hexdigest()
    if manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256:
        raise _error(
            "/",
            "immutable",
            "Scope differs from the reviewed immutable Stage 10 manifest",
        )
    return replace(scope, manifest_sha256=manifest_sha256)


def load_stage10_market_scope(path: str | Path) -> Stage10MarketScope:
    """Load and validate one explicit Stage 10 market-scope JSON file."""

    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise _error("/", "path", "An explicit Stage 10 scope path is required")
    if isinstance(path, str) and not path.strip():
        raise _error("/", "path", "An explicit Stage 10 scope path is required")
    try:
        payload = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "path", "Stage 10 scope file is unavailable") from exc
    raw = loads_strict(payload)
    _reject_sensitive_or_path_like_keys(raw, pointer="")
    return _parse_scope(raw)


__all__ = (
    "CuratedEtf",
    "MajorIndex",
    "PriceHistory",
    "REVIEWED_STAGE10_MANIFEST_SHA256",
    "STAGE10_SCOPE_CONTRACT",
    "STAGE10_SCOPE_PROVIDER",
    "STAGE10_SCOPE_VERSION",
    "STAGE10_TARGET_PROFILE_ID",
    "Stage10Bounds",
    "Stage10MarketScope",
    "UniverseSource",
    "load_stage10_market_scope",
)
