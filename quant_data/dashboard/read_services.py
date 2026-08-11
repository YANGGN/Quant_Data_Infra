"""Bounded, read-only projections for the local Stage 6 dashboard.

This module intentionally accepts neither database paths nor SQL.  Its two
public methods are narrow adapters over the registered four-store topology:
the GDP page delegates semantic vintage selection to ``MacroStage3Repository``
and the table page uses a small, host-owned set of read-only SQLite templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import ResourceLimitError, ValidationError
from ..macro.stage3_repository import MacroStage3Repository, MacroStage3SeriesQuery
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection


GDP_QUERY_FIELDS = frozenset({"series", "mode", "as_of", "date_only_policy", "limit"})
TABLE_QUERY_FIELDS = frozenset({"view", "search", "sort", "direction", "page", "limit"})
TABLE_VIEW_NAMES = (
    "market-prices",
    "macro-observations",
    "company-filings",
    "news-items",
)

_GDP_SERIES = {
    "real-growth": "macro.gdp.real_qoq_saar_pct",
    "nominal-gdp": "macro.gdp.nominal_billions",
}
_GDP_PERIOD_START = "2026-01-01"
_GDP_PERIOD_END = "2026-03-31"
_GDP_COMPARISON_AS_OF = "2026-05-29"
_GDP_OBSERVATION_COLUMNS = (
    "period_start",
    "period_end",
    "value",
    "missing_reason",
    "unit",
    "value_representation",
    "scale",
    "vintage_at",
    "available_at",
    "available_precision",
    "captured_at",
    "captured_precision",
    "version_id",
    "evidence_id",
    "snapshot_id",
    "run_id",
    "dimensions",
    "quality_flags",
)
_MAX_TABLE_LIMIT = 100
_MAX_TABLE_PAGE = 100
_MAX_SEARCH_LENGTH = 128


@dataclass(frozen=True, slots=True)
class _TableView:
    """A fully reviewed table projection; no request value contributes SQL."""

    name: str
    label: str
    store: StoreRole
    relation: str
    columns: tuple[str, ...]
    default_sort: str
    sort_expressions: Mapping[str, str]
    tie_breaker: str
    select_sql: str
    count_sql: str
    search_expression: str


_TABLE_VIEWS: Mapping[str, _TableView] = {
    "market-prices": _TableView(
        name="market-prices",
        label="Market prices",
        store=StoreRole.MARKET,
        relation="prices_daily",
        columns=(
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "available_at",
            "available_precision",
            "captured_at",
            "captured_precision",
        ),
        default_sort="trade_date",
        sort_expressions={
            "trade_date": "price.trade_date",
            "instrument_id": "price.instrument_id",
            "provider": "price.provider",
            "close": "version.close_value",
            "volume": "version.volume",
            "available_at": "version.available_at",
        },
        tie_breaker=(
            "price.instrument_id ASC, price.trade_date ASC, price.provider ASC, "
            "price.price_variant ASC, price.currency_segment ASC"
        ),
        select_sql="""
            SELECT price.instrument_id AS instrument_id,
                   price.trade_date AS trade_date,
                   price.provider AS provider,
                   price.price_variant AS price_variant,
                   price.currency_segment AS currency_segment,
                   version.open_value AS open,
                   version.high_value AS high,
                   version.low_value AS low,
                   version.close_value AS close,
                   version.volume AS volume,
                   version.available_at AS available_at,
                   version.available_precision AS available_precision,
                   version.captured_at AS captured_at,
                   version.captured_precision AS captured_precision
            FROM prices_daily AS price
            JOIN prices_daily_versions AS version
              ON version.version_id = price.current_version_id
        """,
        count_sql="""
            SELECT COUNT(*) AS total
            FROM prices_daily AS price
            JOIN prices_daily_versions AS version
              ON version.version_id = price.current_version_id
        """,
        search_expression=(
            "LOWER(price.instrument_id || ' ' || price.trade_date || ' ' || "
            "price.provider || ' ' || price.price_variant || ' ' || "
            "price.currency_segment) LIKE ? ESCAPE '\\'"
        ),
    ),
    "macro-observations": _TableView(
        name="macro-observations",
        label="Macro observations",
        store=StoreRole.MACRO,
        relation="macro_observation_versions",
        columns=(
            "series_id",
            "period_start",
            "period_end",
            "source_vintage_identity",
            "value",
            "missing_reason",
            "unit",
            "available_at",
            "available_precision",
            "captured_at",
            "captured_precision",
            "state",
        ),
        default_sort="period_start",
        sort_expressions={
            "period_start": "version.period_start",
            "series_id": "version.series_id",
            "available_at": "version.available_at",
            "captured_at": "version.captured_at",
            "source_vintage_identity": "version.source_vintage_identity",
        },
        tie_breaker=(
            "version.series_id ASC, version.period_start ASC, version.period_end ASC, "
            "version.source_vintage_identity ASC, version.correction_sequence ASC, "
            "version.version_id ASC"
        ),
        select_sql="""
            SELECT version.series_id AS series_id,
                   version.period_start AS period_start,
                   version.period_end AS period_end,
                   version.source_vintage_identity AS source_vintage_identity,
                   version.value_text AS value,
                   version.missing_reason AS missing_reason,
                   version.unit AS unit,
                   version.available_at AS available_at,
                   version.available_precision AS available_precision,
                   version.captured_at AS captured_at,
                   version.captured_precision AS captured_precision,
                   version.state AS state
            FROM macro_observation_versions AS version
        """,
        count_sql="""
            SELECT COUNT(*) AS total
            FROM macro_observation_versions AS version
        """,
        search_expression=(
            "LOWER(version.series_id || ' ' || version.period_start || ' ' || "
            "version.period_end || ' ' || version.source_vintage_identity || ' ' || "
            "version.unit) LIKE ? ESCAPE '\\'"
        ),
    ),
    "company-filings": _TableView(
        name="company-filings",
        label="Company filings",
        store=StoreRole.COMPANY,
        relation="company_sec_filings",
        columns=(
            "accession_number",
            "first_observed_issuer_id",
            "form_type",
            "filing_date",
            "filing_date_precision",
            "accepted_at",
            "accepted_precision",
            "report_period_start",
            "report_period_end",
            "primary_document",
            "source_url",
            "available_at",
            "available_precision",
        ),
        default_sort="filing_date",
        sort_expressions={
            "filing_date": "filing.filing_date",
            "accepted_at": "filing.accepted_at",
            "available_at": "filing.available_at",
            "first_observed_issuer_id": "filing.first_observed_issuer_id",
            "form_type": "filing.form_type",
        },
        tie_breaker="filing.accession_number ASC",
        select_sql="""
            SELECT filing.accession_number AS accession_number,
                   filing.first_observed_issuer_id AS first_observed_issuer_id,
                   filing.form_type AS form_type,
                   filing.filing_date AS filing_date,
                   filing.filing_date_precision AS filing_date_precision,
                   filing.accepted_at AS accepted_at,
                   filing.accepted_precision AS accepted_precision,
                   filing.report_period_start AS report_period_start,
                   filing.report_period_end AS report_period_end,
                   filing.primary_document AS primary_document,
                   filing.source_url AS source_url,
                   filing.available_at AS available_at,
                   filing.available_precision AS available_precision
            FROM company_sec_filings AS filing
        """,
        count_sql="""
            SELECT COUNT(*) AS total
            FROM company_sec_filings AS filing
        """,
        search_expression=(
            "LOWER(filing.accession_number || ' ' || filing.first_observed_issuer_id || ' ' || "
            "filing.form_type || ' ' || COALESCE(filing.primary_document, '')) "
            "LIKE ? ESCAPE '\\'"
        ),
    ),
    "news-items": _TableView(
        name="news-items",
        label="News items",
        store=StoreRole.NEWS,
        relation="news_items",
        columns=(
            "source_name",
            "source_item_id",
            "source_kind",
            "headline",
            "published_at",
            "published_precision",
            "content_state",
            "item_state",
            "retraction_reason",
            "available_at",
            "available_precision",
        ),
        default_sort="published_at",
        sort_expressions={
            "source_name": "item.source_name",
            "source_item_id": "item.source_item_id",
            "published_at": "latest.published_at",
            "available_at": "latest.available_at",
        },
        tie_breaker="item.source_name ASC, item.source_item_id ASC",
        select_sql="""
            WITH latest_versions AS (
                SELECT version.item_id,
                       version.headline,
                       version.published_at,
                       version.published_precision,
                       version.content_state,
                       version.item_state,
                       version.retraction_reason,
                       version.available_at,
                       version.available_precision
                FROM news_item_versions AS version
                JOIN (
                    SELECT item_id, MAX(version_sequence) AS max_version_sequence
                    FROM news_item_versions
                    GROUP BY item_id
                ) AS sequence
                  ON sequence.item_id = version.item_id
                 AND sequence.max_version_sequence = version.version_sequence
            )
            SELECT item.source_name AS source_name,
                   item.source_item_id AS source_item_id,
                   item.source_kind AS source_kind,
                   latest.headline AS headline,
                   latest.published_at AS published_at,
                   latest.published_precision AS published_precision,
                   latest.content_state AS content_state,
                   latest.item_state AS item_state,
                   latest.retraction_reason AS retraction_reason,
                   latest.available_at AS available_at,
                   latest.available_precision AS available_precision
            FROM news_items AS item
            LEFT JOIN latest_versions AS latest ON latest.item_id = item.item_id
        """,
        count_sql="""
            WITH latest_versions AS (
                SELECT version.item_id,
                       version.headline,
                       version.published_at,
                       version.available_at
                FROM news_item_versions AS version
                JOIN (
                    SELECT item_id, MAX(version_sequence) AS max_version_sequence
                    FROM news_item_versions
                    GROUP BY item_id
                ) AS sequence
                  ON sequence.item_id = version.item_id
                 AND sequence.max_version_sequence = version.version_sequence
            )
            SELECT COUNT(*) AS total
            FROM news_items AS item
            LEFT JOIN latest_versions AS latest ON latest.item_id = item.item_id
        """,
        search_expression=(
            "LOWER(item.source_name || ' ' || item.source_item_id || ' ' || "
            "item.source_kind || ' ' || COALESCE(latest.headline, '')) "
            "LIKE ? ESCAPE '\\'"
        ),
    ),
}


def _validated_query(query: Mapping[str, str], *, fields: frozenset[str], name: str) -> dict[str, str]:
    if not isinstance(query, Mapping):
        raise ValidationError(f"{name} query must be a mapping")
    unknown = set(query) - fields
    if unknown:
        raise ValidationError(f"{name} query contains unsupported fields")
    normalized: dict[str, str] = {}
    for field, value in query.items():
        if not isinstance(field, str) or not isinstance(value, str):
            raise ValidationError(f"{name} query values must be strings")
        normalized[field] = value
    return normalized


def _bounded_positive_int(raw: str, *, field: str, maximum: int) -> int:
    if not raw or not raw.isascii() or not raw.isdecimal() or raw.startswith("0"):
        raise ValidationError(f"{field} must be a base-10 positive integer")
    value = int(raw)
    if not 1 <= value <= maximum:
        raise ResourceLimitError(f"{field} must be between 1 and {maximum}")
    return value


def _search_pattern(value: str) -> str:
    if len(value) > _MAX_SEARCH_LENGTH:
        raise ResourceLimitError(f"search must be at most {_MAX_SEARCH_LENGTH} characters")
    if any(ord(character) < 32 for character in value):
        raise ValidationError("search contains a control character")
    escaped = value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _primitive_sqlite_value(value: Any) -> str | int | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValidationError("Dashboard projection contains an unsupported stored value")


class Stage6DashboardReadService:
    """Narrow local dashboard reads over an explicit registered StoreMap."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap):
            raise ValidationError("Stage 6 dashboard requires an explicit StoreMap")
        if not isinstance(registry, Registry):
            raise ValidationError("Stage 6 dashboard requires a validated Registry")
        self._store_map = store_map
        self._registry = registry
        self._macro = MacroStage3Repository(store_map, registry)
        self._validate_table_registry_bindings()

    def gdp_vintages(self, query: Mapping[str, str]) -> dict[str, Any]:
        """Return one reviewed GDP selection plus its fixed vintage comparison trail."""

        request = _validated_query(query, fields=GDP_QUERY_FIELDS, name="GDP")
        series_slug = request.get("series", "real-growth")
        try:
            series_id = _GDP_SERIES[series_slug]
        except KeyError as exc:
            raise ValidationError("GDP series is not available in this dashboard") from exc
        mode = request.get("mode", "latest")
        if mode not in {"latest", "as_of", "first_release"}:
            raise ValidationError("GDP mode is unsupported")
        date_only_policy = request.get("date_only_policy", "completed_date")
        if date_only_policy not in {"completed_date", "calendar_date_inclusive"}:
            raise ValidationError("GDP date-only policy is unsupported")
        limit = _bounded_positive_int(request.get("limit", "25"), field="limit", maximum=100)
        as_of: str | None = request.get("as_of")
        if mode == "as_of":
            if not as_of:
                raise ValidationError("as_of is required when GDP mode is as_of")
        elif as_of is not None:
            raise ValidationError("as_of is only allowed when GDP mode is as_of")

        selected = self._macro.get_series(
            MacroStage3SeriesQuery(
                series_id=series_id,
                start_date=_GDP_PERIOD_START,
                end_date=_GDP_PERIOD_END,
                vintage_mode=mode,
                as_of=as_of,
                date_only_policy=date_only_policy,
                limit=limit,
            )
        )
        comparison_trail = {
            "first_release": self._macro.get_series(
                MacroStage3SeriesQuery(
                    series_id=series_id,
                    start_date=_GDP_PERIOD_START,
                    end_date=_GDP_PERIOD_END,
                    vintage_mode="first_release",
                    date_only_policy=date_only_policy,
                    limit=limit,
                )
            ).to_primitive(),
            "as_of": self._macro.get_series(
                MacroStage3SeriesQuery(
                    series_id=series_id,
                    start_date=_GDP_PERIOD_START,
                    end_date=_GDP_PERIOD_END,
                    vintage_mode="as_of",
                    as_of=_GDP_COMPARISON_AS_OF,
                    date_only_policy=date_only_policy,
                    limit=limit,
                )
            ).to_primitive(),
            "latest": self._macro.get_series(
                MacroStage3SeriesQuery(
                    series_id=series_id,
                    start_date=_GDP_PERIOD_START,
                    end_date=_GDP_PERIOD_END,
                    vintage_mode="latest",
                    date_only_policy=date_only_policy,
                    limit=limit,
                )
            ).to_primitive(),
        }
        selected_primitive = selected.to_primitive()
        return {
            "page": "gdp-vintages",
            "query": {
                "series": series_slug,
                "series_id": series_id,
                "mode": mode,
                "as_of": as_of,
                "date_only_policy": date_only_policy,
                "period_start": _GDP_PERIOD_START,
                "period_end": _GDP_PERIOD_END,
                "limit": limit,
            },
            "selected": selected_primitive,
            "comparison_trail": comparison_trail,
            "columns": list(_GDP_OBSERVATION_COLUMNS),
            "observations": list(selected_primitive["observations"]),
            "audit": selected_primitive["audit"],
            "provenance": selected_primitive["provenance"],
            "warnings": list(selected_primitive["warnings"]),
            "truncated": bool(selected_primitive["truncated"]),
        }

    def table_inspector(self, query: Mapping[str, str]) -> dict[str, Any]:
        """Return one bounded, server-owned public table projection."""

        request = _validated_query(query, fields=TABLE_QUERY_FIELDS, name="Table")
        view_name = request.get("view", "market-prices")
        try:
            view = _TABLE_VIEWS[view_name]
        except KeyError as exc:
            raise ValidationError("Table view is not available in this dashboard") from exc
        sort = request.get("sort", view.default_sort)
        if sort not in view.sort_expressions:
            raise ValidationError("Sort field is not available for this table view")
        direction = request.get("direction", "desc")
        if direction not in {"asc", "desc"}:
            raise ValidationError("Sort direction must be asc or desc")
        page = _bounded_positive_int(request.get("page", "1"), field="page", maximum=_MAX_TABLE_PAGE)
        limit = _bounded_positive_int(request.get("limit", "25"), field="limit", maximum=_MAX_TABLE_LIMIT)
        search = request.get("search", "")
        pattern: str | None = _search_pattern(search) if search else None
        where_sql = f" WHERE {view.search_expression}" if pattern is not None else ""
        parameters: tuple[Any, ...] = (pattern,) if pattern is not None else ()
        direction_sql = "ASC" if direction == "asc" else "DESC"
        order_sql = f" ORDER BY {view.sort_expressions[sort]} {direction_sql}, {view.tie_breaker}"
        offset = (page - 1) * limit

        with read_connection(self._store_map, view.store) as connection:
            total = int(connection.execute(view.count_sql + where_sql, parameters).fetchone()["total"])
            rows = connection.execute(
                view.select_sql + where_sql + order_sql + " LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()

        primitive_rows = [
            {column: _primitive_sqlite_value(row[column]) for column in view.columns}
            for row in rows
        ]
        page_count = (total + limit - 1) // limit if total else 0
        warnings: list[str] = []
        if total and page > page_count:
            warnings.append("page_out_of_range")
        return {
            "surface": "table-inspector",
            "view": view.name,
            "label": view.label,
            "columns": list(view.columns),
            "available_sorts": list(view.sort_expressions),
            "query": {
                "view": view.name,
                "search": search,
                "sort": sort,
                "direction": direction,
                "page": page,
                "limit": limit,
            },
            "rows": primitive_rows,
            "total": total,
            "page": page,
            "page_count": page_count,
            "limit": limit,
            "sort": sort,
            "direction": direction,
            "search": search,
            "warnings": warnings,
        }

    def _validate_table_registry_bindings(self) -> None:
        for view in _TABLE_VIEWS.values():
            declared_relations = {
                relation
                for dataset in self._registry.datasets_for(view.store.value)
                for relation in dataset.relations
            }
            if view.relation not in declared_relations:
                raise ValidationError("Dashboard table view is not bound to a registered relation")


__all__ = (
    "GDP_QUERY_FIELDS",
    "TABLE_QUERY_FIELDS",
    "TABLE_VIEW_NAMES",
    "Stage6DashboardReadService",
)
