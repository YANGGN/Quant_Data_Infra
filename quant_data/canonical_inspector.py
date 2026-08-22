"""Loopback-only read inspector for the canonical market and macro stores.

This is intentionally separate from the frozen Stage 6 portal.  Browser input
selects only fixed, bounded read templates; it can never select a database
path, relation, SQL statement, provider, or write operation.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlencode

from .boundary.application import (
    HttpResponse,
    MethodNotAllowedError,
    RouteNotFoundError,
    Stage1Application,
    create_server,
)
from .dashboard.application import INTER_FONT_SHA256
from .errors import Issue, ResourceLimitError, StoreUnavailableError, ValidationError
from .json_codec import dumps_strict, loads_strict
from .macro.fmp_release_surprises import (
    ALL_SURPRISE_KINDS,
    GDP_ADVANCE_KIND,
    MacroReleaseSurpriseRepository,
)
from .macro.fmp_treasury_curve import CURVE_VARIANT, PROVIDER, TENOR_MANIFEST
from .registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from .stores import StoreMap, StoreRole, read_connection, resolve_store_map


_CURRENT_REGISTRY = "2.23.0"
_CURRENT_SCHEMA = "1.8.0"
_ASSET_ROOT = Path(__file__).with_name("dashboard") / "static"
_VIEWS = (
    "market-prices",
    "market-instruments",
    "macro-current",
    "macro-vintages",
    "macro-surprises",
    "treasury-curve",
    "fmp-economic-calendar",
)
_VIEW_LABELS = {
    "market-prices": "Market prices",
    "market-instruments": "Market symbols",
    "macro-current": "Macro current",
    "macro-vintages": "Macro vintages",
    "macro-surprises": "Release surprises",
    "treasury-curve": "Treasury curve",
    "fmp-economic-calendar": "Raw FMP calendar",
}
_TREASURY_TENORS = tuple(item.tenor for item in TENOR_MANIFEST)
_MACRO_SERIES = (
    "macro.gdp.real_qoq_saar_pct",
    "macro.gdp.nominal_billions",
    "macro.bls.cpi_u_all_items_sa",
    "macro.bls.cpi_u_core_sa",
    "macro.bls.total_nonfarm_payrolls_sa",
    "macro.bls.unemployment_rate_sa",
    "macro.philadelphia_fed.nominal_output",
    "macro.philadelphia_fed.real_output",
)
_DEFAULT_SERIES = "macro.gdp.real_qoq_saar_pct"
_FMP_PRIORITIES = ("High", "Medium", "Low", "None")
_GDP_RELEASE_STAGES = ("advance", "initial", "second", "third")
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=_-]{1,32}$")
_PERIOD = re.compile(r"^[0-9]{4}(?:Q[1-4]|-[0-9]{2})$")
_MAX_LIMIT = 100
_MAX_PAGE = 1000


class CanonicalInspectorReadService:
    """Fixed query templates over server-owned canonical store paths."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._stores = store_map
        self._registry = registry

    def inspect(self, query: Mapping[str, str]) -> dict[str, Any]:
        view = query.get("view", "market-prices")
        if view not in _VIEWS:
            raise ValidationError("Inspector view is invalid")
        allowed = {
            "market-prices": {"view", "symbol", "start_date", "end_date", "direction", "page", "limit"},
            "market-instruments": {"view", "search", "direction", "page", "limit"},
            "macro-current": {"view", "series", "period", "direction", "page", "limit"},
            "macro-vintages": {"view", "series", "period", "direction", "page", "limit"},
            "macro-surprises": {
                "view", "kind", "stage", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "treasury-curve": {
                "view", "tenor", "start_date", "end_date",
                "direction", "page", "limit",
            },
            "fmp-economic-calendar": {
                "view", "country", "priority", "event_name", "keyword",
                "start_date", "end_date", "direction", "page", "limit",
            },
        }[view]
        unknown = sorted(set(query) - allowed)
        if unknown:
            raise ValidationError(
                "Inspector query contains an unsupported field",
                issues=(Issue(f"/{unknown[0]}", "additional_properties", "Field is not supported"),),
            )
        direction = query.get("direction", "desc")
        if direction not in {"asc", "desc"}:
            raise ValidationError("Inspector direction is invalid")
        page = _bounded_int(query.get("page", "1"), "/page", 1, _MAX_PAGE)
        limit = _bounded_int(query.get("limit", "25"), "/limit", 1, _MAX_LIMIT)
        if view == "market-prices":
            result = self._market_prices(query, direction, page, limit)
        elif view == "market-instruments":
            result = self._market_instruments(query, direction, page, limit)
        elif view == "macro-current":
            result = self._macro_rows(query, direction, page, limit, current=True)
        elif view == "macro-vintages":
            result = self._macro_rows(query, direction, page, limit, current=False)
        elif view == "macro-surprises":
            result = self._macro_surprises(query, direction, page, limit)
        elif view == "treasury-curve":
            result = self._treasury_curve(query, direction, page, limit)
        else:
            result = self._fmp_economic_calendar(query, direction, page, limit)
        result.update({"view": view, "page": page, "limit": limit, "direction": direction})
        return result

    def _market_prices(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        symbol = query.get("symbol", "AAPL").strip().upper()
        if _SYMBOL.fullmatch(symbol) is None:
            raise ValidationError("Market symbol is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Market date range is invalid")
        where = ["instrument.provider='fmp'", "instrument.provider_symbol=?"]
        parameters: list[object] = [symbol]
        if start:
            where.append("price.trade_date>=?")
            parameters.append(start)
        if end:
            where.append("price.trade_date<=?")
            parameters.append(end)
        columns = (
            "symbol", "name", "type", "trade_date", "open", "high", "low", "close",
            "volume", "available_at", "captured_at",
        )
        sql = f"""
            SELECT instrument.provider_symbol AS symbol,
                   instrument.display_name AS name,
                   instrument.asset_type AS type,
                   price.trade_date,
                   version.open_value AS open,
                   version.high_value AS high,
                   version.low_value AS low,
                   version.close_value AS close,
                   version.volume,
                   version.available_at,
                   version.captured_at
            FROM stage10_daily_prices AS price
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=price.instrument_id
            JOIN stage10_daily_price_versions AS version
              ON version.version_id=price.current_version_id
            WHERE {' AND '.join(where)}
            ORDER BY price.trade_date {direction.upper()}
            LIMIT ? OFFSET ?
        """
        count_sql = f"""
            SELECT COUNT(*)
            FROM stage10_daily_prices AS price
            JOIN stage10_instruments AS instrument
              ON instrument.instrument_id=price.instrument_id
            WHERE {' AND '.join(where)}
        """
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(connection.execute(count_sql, tuple(parameters)).fetchone()[0])
            rows = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        return _result(columns, rows, total, page, limit, {"symbol": symbol, "start_date": start, "end_date": end})

    def _market_instruments(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        search = query.get("search", "").strip()
        if len(search) > 64 or any(ord(char) < 32 for char in search):
            raise ValidationError("Market symbol search is invalid")
        where = ["provider='fmp'"]
        parameters: list[object] = []
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(provider_symbol LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\')")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        columns = ("symbol", "name", "type", "exchange", "first_trade_date")
        sql = f"""
            SELECT provider_symbol AS symbol, display_name AS name, asset_type AS type,
                   exchange_code AS exchange, first_trade_date
            FROM stage10_instruments
            WHERE {' AND '.join(where)}
            ORDER BY provider_symbol {direction.upper()}
            LIMIT ? OFFSET ?
        """
        count_sql = f"SELECT COUNT(*) FROM stage10_instruments WHERE {' AND '.join(where)}"
        with _immutable_store_connection(
            self._stores.market, expected_role="market"
        ) as connection:
            total = int(connection.execute(count_sql, tuple(parameters)).fetchone()[0])
            rows = _rows(connection.execute(sql, (*parameters, limit, (page - 1) * limit)).fetchall())
        return _result(columns, rows, total, page, limit, {"search": search})

    def _macro_rows(
        self,
        query: Mapping[str, str],
        direction: str,
        page: int,
        limit: int,
        *,
        current: bool,
    ) -> dict[str, Any]:
        series = query.get("series", _DEFAULT_SERIES)
        if series not in _MACRO_SERIES:
            raise ValidationError("Macro series is invalid")
        period = query.get("period", "").strip()
        if period and _PERIOD.fullmatch(period) is None:
            raise ValidationError("Macro period is invalid")
        where = ["version.series_id=?"]
        parameters: list[object] = [series]
        if period:
            where.append("version.period=?")
            parameters.append(period)
        current_join = (
            "JOIN macro_live_vintage_observations AS observation "
            "ON observation.current_version_id=version.version_id"
            if current
            else ""
        )
        columns = (
            "series", "title", "period", "value", "unit", "source_vintage",
            "vintage_at", "release_stage", "first_release", "correction",
            "available_at", "captured_at",
        )
        base = f"""
            FROM macro_live_vintage_observation_versions AS version
            {current_join}
            JOIN macro_live_vintage_releases AS release
              ON release.release_id=version.release_id
            JOIN macro_live_vintage_series AS series
              ON series.series_id=version.series_id
            WHERE {' AND '.join(where)}
        """
        sql = f"""
            SELECT version.series_id AS series, series.title, version.period,
                   version.value_text AS value, version.unit,
                   release.source_vintage_identity AS source_vintage,
                   release.vintage_at, release.release_stage,
                   release.is_first_release AS first_release,
                   version.correction_sequence AS correction,
                   version.available_at, version.captured_at
            {base}
            ORDER BY version.period {direction.upper()},
                     release.source_release_order {direction.upper()},
                     version.correction_sequence {direction.upper()}
            LIMIT ? OFFSET ?
        """
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            total = int(connection.execute(f"SELECT COUNT(*) {base}", tuple(parameters)).fetchone()[0])
            rows = _rows(connection.execute(sql, (*parameters, limit, (page - 1) * limit)).fetchall())
        return _result(columns, rows, total, page, limit, {"series": series, "period": period})

    def _macro_surprises(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        kind = query.get("kind", GDP_ADVANCE_KIND)
        if kind not in ALL_SURPRISE_KINDS:
            raise ValidationError("Macro surprise kind is invalid")
        stage = query.get("stage", "")
        if stage and (kind != GDP_ADVANCE_KIND or stage not in _GDP_RELEASE_STAGES):
            raise ValidationError("Macro surprise release stage is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Macro surprise date range is invalid")
        repository = MacroReleaseSurpriseRepository(
            macro_store=self._stores.macro,
            registry=self._registry,
        )
        selected = list(repository.query(start_date=start, end_date=end, kinds=(kind,)))
        if stage:
            selected = [item for item in selected if item.release_stage == stage]
        if direction == "desc":
            selected.reverse()
        total = len(selected)
        page_rows = selected[(page - 1) * limit : page * limit]
        rows = [
            {
                "event_at": item.event_at,
                "reference_period": item.reference_period,
                "kind": item.kind,
                "consensus": _decimal(item.consensus),
                "fmp_actual": _decimal(item.fmp_actual),
                "official_actual": _decimal(item.official_actual),
                "surprise": _decimal(item.surprise),
                "unit": item.unit,
                "actual_source": item.actual_source,
                "mapping": item.consensus_mapping_basis,
                "status": item.status,
                "official_version_id": item.official_version_id,
                "official_prior_version_id": item.official_prior_version_id,
                "release_stage": item.release_stage,
                "is_fallback": item.is_fallback,
                "coalesced_event_version_ids": item.coalesced_event_version_ids,
            }
            for item in page_rows
        ]
        columns = (
            "event_at", "reference_period", "kind", "consensus", "official_actual",
            "fmp_actual", "surprise", "unit", "actual_source", "mapping", "status",
            "official_version_id", "official_prior_version_id",
            "release_stage", "is_fallback",
            "coalesced_event_version_ids",
        )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "kind": kind,
                "stage": stage,
                "start_date": start,
                "end_date": end,
            },
        )

    def _treasury_curve(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        tenor = query.get("tenor", "")
        if tenor and tenor not in _TREASURY_TENORS:
            raise ValidationError("Treasury tenor is invalid")
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("Treasury curve date range is invalid")

        where = ["provider=?", "curve_variant=?"]
        parameters: list[object] = [PROVIDER, CURVE_VARIANT]
        if tenor:
            where.append("tenor=?")
            parameters.append(tenor)
        if start:
            where.append("curve_date>=?")
            parameters.append(start)
        if end:
            where.append("curve_date<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        latest = f"""
            WITH latest AS (
                SELECT provider, curve_date, curve_variant, tenor,
                       MAX(correction_sequence) AS correction_sequence
                FROM treasury_yield_curve_versions
                WHERE {predicate}
                GROUP BY provider, curve_date, curve_variant, tenor
            )
        """
        base = """
            FROM treasury_yield_curve_versions AS version
            JOIN latest
              ON latest.provider=version.provider
             AND latest.curve_date=version.curve_date
             AND latest.curve_variant=version.curve_variant
             AND latest.tenor=version.tenor
             AND latest.correction_sequence=version.correction_sequence
        """
        tenor_order = "CASE version.tenor " + " ".join(
            f"WHEN '{item}' THEN {ordinal}"
            for ordinal, item in enumerate(_TREASURY_TENORS, start=1)
        ) + " ELSE 99 END"
        columns = (
            "curve_date", "tenor", "yield_percent", "missing_reason",
            "correction", "available_at", "captured_at",
        )
        sql = (
            latest
            + """
            SELECT version.curve_date, version.tenor,
                   version.yield_value AS yield_percent,
                   version.missing_reason,
                   version.correction_sequence AS correction,
                   version.available_at, version.captured_at
            """
            + base
            + f"""
            ORDER BY version.curve_date {direction.upper()}, {tenor_order} ASC
            LIMIT ? OFFSET ?
            """
        )
        with _immutable_store_connection(
            self._stores.macro, expected_role="macro"
        ) as connection:
            total = int(
                connection.execute(
                    latest + "SELECT COUNT(*) " + base,
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    sql,
                    (*parameters, limit, (page - 1) * limit),
                ).fetchall()
            )
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {"tenor": tenor, "start_date": start, "end_date": end},
        )

    def _fmp_economic_calendar(
        self, query: Mapping[str, str], direction: str, page: int, limit: int
    ) -> dict[str, Any]:
        country = _text_filter(query.get("country"), "/country", 64)
        priority = _text_filter(query.get("priority"), "/priority", 16)
        if priority and priority not in _FMP_PRIORITIES:
            raise ValidationError("FMP calendar priority is invalid")
        event_name = _text_filter(query.get("event_name"), "/event_name", 256)
        keyword = _text_filter(query.get("keyword"), "/keyword", 128)
        start = _optional_date(query.get("start_date"), "/start_date")
        end = _optional_date(query.get("end_date"), "/end_date")
        if start and end and start > end:
            raise ValidationError("FMP calendar date range is invalid")

        where = [
            "run.dataset_id='macro.fmp.economic_calendar_evidence'",
            "run.command='fmp.macro.us_economic_calendar_wholesale'",
            "run.status='succeeded'",
            "artifact.dataset_id='macro.fmp.economic_calendar_evidence'",
            "artifact.content_sha256=capture.response_sha256",
            "snapshot.dataset_id='macro.fmp.economic_calendar_evidence'",
            "snapshot.semantic_identity=capture.semantic_identity",
            "snapshot.completeness='complete'",
            "snapshot.validation_state='validated'",
        ]
        parameters: list[object] = []
        if country:
            where.append("LOWER(calendar.country)=LOWER(?)")
            parameters.append(country)
        if priority:
            where.append("calendar.impact_json=?")
            parameters.append(dumps_strict(priority))
        if event_name:
            where.append("calendar.event_name LIKE ? ESCAPE '\\'")
            parameters.append(_like_contains(event_name))
        if keyword:
            pattern = _like_contains(keyword)
            where.append(
                "(calendar.event_name LIKE ? ESCAPE '\\' "
                "OR calendar.country LIKE ? ESCAPE '\\' "
                "OR COALESCE(calendar.currency, '') LIKE ? ESCAPE '\\' "
                "OR COALESCE(calendar.unit, '') LIKE ? ESCAPE '\\' "
                "OR calendar.raw_row_json LIKE ? ESCAPE '\\')"
            )
            parameters.extend((pattern, pattern, pattern, pattern, pattern))
        if start:
            where.append("SUBSTR(calendar.event_at, 1, 10)>=?")
            parameters.append(start)
        if end:
            where.append("SUBSTR(calendar.event_at, 1, 10)<=?")
            parameters.append(end)
        predicate = " AND ".join(where)
        columns = (
            "event_at", "country", "priority", "event_name", "currency", "unit",
            "previous", "estimate", "actual", "change", "change_percentage",
            "captured_at", "request_start", "request_end", "capture_id",
            "source_row", "row_sha256", "response_sha256", "semantic_identity",
            "raw_row_json",
        )
        base = f"""
            FROM fmp_economic_calendar_rows AS calendar
            JOIN fmp_economic_calendar_captures AS capture
              ON capture.capture_id=calendar.capture_id
            JOIN ingestion_runs AS run
              ON run.run_id=capture.run_id
            JOIN ingestion_artifacts AS artifact
              ON artifact.artifact_id=capture.artifact_id
             AND artifact.run_id=run.run_id
            JOIN ingestion_snapshots AS snapshot
              ON snapshot.snapshot_id=capture.snapshot_id
             AND snapshot.run_id=run.run_id
            WHERE {predicate}
        """
        sql = f"""
            SELECT calendar.event_at, calendar.country,
                   calendar.impact_json AS priority,
                   calendar.event_name, calendar.currency, calendar.unit,
                   calendar.previous_json AS previous,
                   calendar.estimate_json AS estimate,
                   calendar.actual_json AS actual,
                   calendar.change_json AS change,
                   calendar.change_percentage_json AS change_percentage,
                   capture.captured_at,
                   capture.request_start_date AS request_start,
                   capture.request_end_date AS request_end,
                   calendar.capture_id, calendar.source_row,
                   calendar.row_sha256, capture.response_sha256,
                   capture.semantic_identity, calendar.raw_row_json
            {base}
            ORDER BY calendar.event_at {direction.upper()},
                     capture.captured_at {direction.upper()},
                     calendar.capture_id {direction.upper()},
                     calendar.source_row {direction.upper()}
            LIMIT ? OFFSET ?
        """
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) {base}", tuple(parameters)
                ).fetchone()[0]
            )
            stored = _rows(
                connection.execute(
                    sql, (*parameters, limit, (page - 1) * limit)
                ).fetchall()
            )
        rows: list[dict[str, Any]] = []
        for stored_row in stored:
            row = dict(stored_row)
            for field in (
                "priority", "previous", "estimate", "actual", "change",
                "change_percentage",
            ):
                row[field] = _stored_json_value(row[field])
            rows.append(row)
        return _result(
            columns,
            rows,
            total,
            page,
            limit,
            {
                "country": country,
                "priority": priority,
                "event_name": event_name,
                "keyword": keyword,
                "start_date": start,
                "end_date": end,
            },
        )


class CanonicalInspectorApplication(Stage1Application):
    """GET-only HTML/JSON surface for the fixed canonical inspection views."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if registry.registry_version != _CURRENT_REGISTRY or registry.schema_version != _CURRENT_SCHEMA:
            raise ValidationError("Canonical inspector requires the current registry")
        super().__init__(store_map, registry)
        self._reads = CanonicalInspectorReadService(store_map, registry)
        css = (_ASSET_ROOT / "dashboard.css").read_bytes()
        font = (_ASSET_ROOT / "inter-variable.woff2").read_bytes()
        if hashlib.sha256(font).hexdigest() != INTER_FONT_SHA256:
            raise StoreUnavailableError("Canonical inspector local assets are invalid")
        self._assets = {
            "/assets/dashboard.css": (css, "text/css; charset=utf-8"),
            "/assets/inter-variable.woff2": (font, "font/woff2"),
        }

    def _handle_get(
        self, path: str, query: Mapping[str, str], headers: Mapping[str, str]
    ) -> HttpResponse:
        del headers
        asset = self._assets.get(path)
        if asset is not None:
            if query:
                raise ValidationError("Inspector assets do not accept query fields")
            return HttpResponse(HTTPStatus.OK, asset[0], asset[1])
        if path == "/api/rows":
            result = self._reads.inspect(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(result, route="canonical_inspector"),
            )
        if path == "/":
            try:
                result = self._reads.inspect(query)
                error = None
            except (ValidationError, ResourceLimitError, StoreUnavailableError) as exc:
                fallback_view = query.get("view", "market-prices")
                if fallback_view not in _VIEWS:
                    fallback_view = "market-prices"
                result = {
                    "view": fallback_view,
                    "columns": (),
                    "rows": (),
                    "total": 0,
                    "page": 1,
                    "limit": 25,
                    "direction": "desc",
                    "query": {},
                    "truncated": False,
                }
                error = exc.safe_message
            return HttpResponse(
                HTTPStatus.OK,
                _render_page(result, query, self._registry.revision, error).encode("utf-8"),
                "text/html; charset=utf-8",
            )
        raise RouteNotFoundError("Route was not found")

    def _handle_post(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpResponse:
        del query, headers, body
        if path in {"/", "/api/rows", *self._assets}:
            raise MethodNotAllowedError("HTTP method is not supported")
        raise RouteNotFoundError("Route was not found")


def build_canonical_inspector(project_root: str | Path) -> CanonicalInspectorApplication:
    """Build the inspector from the current registry and exact canonical paths."""

    root = Path(project_root).expanduser().resolve(strict=True)
    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=root,
        environment={},
    )
    stores = resolve_store_map(registry, project_root=root, environment={})
    for role in (StoreRole.MARKET, StoreRole.MACRO):
        expected = root / "data" / f"{role.value}.sqlite"
        if expected.is_symlink() or not expected.is_file():
            raise StoreUnavailableError(f"Canonical {role.value} store is unavailable")
        if stores.path(role) != expected.resolve(strict=True):
            raise ValidationError("Canonical inspector store binding is invalid")
    return CanonicalInspectorApplication(stores, registry)


@contextmanager
def _immutable_store_connection(
    path: Path, *, expected_role: str
) -> Iterator[sqlite3.Connection]:
    """Open one quiet canonical store without touching its WAL/SHM state."""

    if expected_role not in {"market", "macro"}:
        raise ValidationError("Canonical inspector store role is invalid")
    if path.is_symlink() or not path.is_file():
        raise StoreUnavailableError("Canonical store is unavailable")
    before_sidecars = {name: _sidecar_stamp(path, name) for name in ("wal", "shm", "journal")}
    for name in ("wal", "journal"):
        stamp = before_sidecars[name]
        if stamp is not None and stamp[4] != 0:
            raise StoreUnavailableError("Canonical store is not quiet")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StoreUnavailableError("Canonical store is unavailable") from exc
    connection: sqlite3.Connection | None = None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StoreUnavailableError("Canonical store identity is invalid")
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise StoreUnavailableError("Canonical store identity changed")
        uri = f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=0.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise StoreUnavailableError("Canonical connection is not query-only")
        connection.execute("BEGIN")
        role = connection.execute(
            "SELECT store_role FROM store_metadata WHERE singleton=1"
        ).fetchone()
        if role is None or role[0] != expected_role:
            raise StoreUnavailableError("Canonical store has the wrong role")
        yield connection
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Canonical store is unavailable") from exc
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        try:
            after = os.fstat(descriptor)
            stable_before = (
                before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns,
            )
            stable_after = (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            )
            if stable_before != stable_after:
                raise StoreUnavailableError("Canonical store changed during inspection")
            if any(_sidecar_stamp(path, name) != before_sidecars[name] for name in before_sidecars):
                raise StoreUnavailableError("Canonical store sidecar changed during inspection")
        finally:
            os.close(descriptor)


def _sidecar_stamp(path: Path, name: str) -> tuple[int, ...] | None:
    suffix = {"wal": "-wal", "shm": "-shm", "journal": "-journal"}[name]
    candidate = Path(str(path) + suffix)
    try:
        value = os.stat(candidate, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _bounded_int(raw: str, pointer: str, minimum: int, maximum: int) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise ValidationError("Inspector pagination is invalid")
    value = int(raw)
    if value < minimum or value > maximum:
        raise ResourceLimitError(
            "Inspector pagination exceeds its fixed bound",
            issues=(Issue(pointer, "range", f"Expected {minimum} through {maximum}"),),
        )
    return value


def _optional_date(raw: str | None, pointer: str) -> str | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Inspector date is invalid",
            issues=(Issue(pointer, "format", "Expected YYYY-MM-DD"),),
        ) from exc


def _text_filter(raw: str | None, pointer: str, maximum: int) -> str:
    value = "" if raw is None else raw.strip()
    if len(value) > maximum or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValidationError(
            "Inspector text filter is invalid",
            issues=(Issue(pointer, "format", f"Expected at most {maximum} printable characters"),),
        )
    return value


def _like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _stored_json_value(raw: Any) -> Any:
    if raw is None:
        return None
    value = loads_strict(str(raw), max_bytes=4096)
    if isinstance(value, (dict, list)):
        return dumps_strict(value, max_bytes=4096)
    return value


def _rows(values: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(value) for value in values]


def _result(
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
    total: int,
    page: int,
    limit: int,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "columns": columns,
        "rows": rows,
        "total": total,
        "truncated": page * limit < total,
        "query": dict(query),
    }


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _render_page(
    result: Mapping[str, Any],
    raw_query: Mapping[str, str],
    revision: str,
    error: str | None,
) -> str:
    view = str(result["view"])
    navigation = "".join(
        '<a href="/?' + html.escape(urlencode({"view": item}), quote=True) + '"'
        + (' aria-current="page"' if item == view else "")
        + f">{html.escape(_VIEW_LABELS[item])}</a>"
        for item in _VIEWS
    )
    notice = (
        f'<div class="state-notice warning" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    form = _render_form(view, result.get("query", {}), result)
    columns = tuple(str(item) for item in result.get("columns", ()))
    rows = result.get("rows", ())
    header = "".join(f"<th>{html.escape(item.replace('_', ' ').title())}</th>" for item in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html_value(row.get(column))}</td>" for column in columns) + "</tr>"
        for row in rows
        if isinstance(row, Mapping)
    )
    if not body:
        body = f'<tr><td colspan="{max(1, len(columns))}">No rows match this selection.</td></tr>'
    page = int(result.get("page", 1))
    limit = int(result.get("limit", 25))
    total = int(result.get("total", 0))
    pager = _render_pager(raw_query, view, page, limit, total)
    api_query = dict(raw_query)
    api_query["view"] = view
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canonical Data Inspector</title><link rel="stylesheet" href="/assets/dashboard.css"></head>
<body><a class="skip-link" href="#main-content">Skip to content</a><div class="app-shell">
<header class="site-header"><a class="brand" href="/"><span class="brand-mark">QD</span><strong>Canonical Data Inspector</strong></a>
<p class="shell-status"><span aria-hidden="true">●</span> Local read-only · registry {html.escape(revision)}</p></header>
<nav class="primary-nav" aria-label="Inspector views">{navigation}</nav>
<main id="main-content"><section class="page-intro"><p class="eyebrow">Operational data · inspection only</p>
<h1>{html.escape(_VIEW_LABELS[view])}</h1><p>Fixed, bounded queries over the canonical market and macro databases. Database paths, SQL, writes, and provider calls are not available here.</p></section>
{notice}<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Filters</p><h2>Choose what to inspect</h2></div></div>{form}</section>
<section class="panel"><div class="panel-header"><div><p class="panel-kicker">Results</p><h2>{total:,} matching rows</h2></div>
<a href="/api/rows?{html.escape(urlencode(api_query), quote=True)}">View JSON</a></div>
<div class="table-scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>{pager}</section>
</main><footer class="site-footer"><p>Loopback only · GET only · no database mutation</p></footer></div></body></html>"""


def _render_form(view: str, query: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    direction = str(result.get("direction", "desc"))
    limit = str(result.get("limit", 25))
    fields: list[str] = [f'<input type="hidden" name="view" value="{html.escape(view, quote=True)}">']
    if view == "market-prices":
        fields.extend(
            (
                _input("symbol", "Symbol", query.get("symbol", "AAPL")),
                _input("start_date", "Start date", query.get("start_date"), input_type="date"),
                _input("end_date", "End date", query.get("end_date"), input_type="date"),
            )
        )
    elif view == "market-instruments":
        fields.append(_input("search", "Symbol or name", query.get("search")))
    elif view in {"macro-current", "macro-vintages"}:
        selected = str(query.get("series", _DEFAULT_SERIES))
        options = "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _MACRO_SERIES
        )
        fields.append(f'<label>Series<select name="series">{options}</select></label>')
        fields.append(_input("period", "Exact period", query.get("period"), placeholder="2026Q2 or 2026-07"))
    elif view == "treasury-curve":
        selected = str(query.get("tenor", ""))
        options = '<option value="">All tenors</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _TREASURY_TENORS
        )
        fields.append(f'<label>Tenor<select name="tenor">{options}</select></label>')
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    elif view == "macro-surprises":
        selected = str(query.get("kind", GDP_ADVANCE_KIND))
        options = "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape('GDP growth — best available release' if item == GDP_ADVANCE_KIND else item)}</option>"
            for item in ALL_SURPRISE_KINDS
        )
        fields.append(f'<label>Release type<select name="kind">{options}</select></label>')
        stage = str(query.get("stage", ""))
        stage_options = '<option value="">All selected stages</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == stage else "")
            + f">{html.escape(item.title())}</option>"
            for item in _GDP_RELEASE_STAGES
        )
        fields.append(f'<label>GDP release stage<select name="stage">{stage_options}</select></label>')
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    else:
        selected = str(query.get("priority", ""))
        options = '<option value="">All priorities</option>' + "".join(
            f'<option value="{html.escape(item, quote=True)}"'
            + (" selected" if item == selected else "")
            + f">{html.escape(item)}</option>"
            for item in _FMP_PRIORITIES
        )
        fields.append(_input("country", "Country", query.get("country"), placeholder="US"))
        fields.append(f'<label>Priority / impact<select name="priority">{options}</select></label>')
        fields.append(
            _input(
                "event_name", "Event name contains", query.get("event_name"),
                placeholder="Non Farm Payrolls",
            )
        )
        fields.append(
            _input(
                "keyword", "Keyword in raw row", query.get("keyword"),
                placeholder="inflation, housing, jobs",
            )
        )
        fields.append(_input("start_date", "Start date", query.get("start_date"), input_type="date"))
        fields.append(_input("end_date", "End date", query.get("end_date"), input_type="date"))
    fields.append(
        '<label>Order<select name="direction">'
        f'<option value="desc"{" selected" if direction == "desc" else ""}>Newest first</option>'
        f'<option value="asc"{" selected" if direction == "asc" else ""}>Oldest first</option>'
        "</select></label>"
    )
    fields.append(
        '<label>Rows<select name="limit">'
        + "".join(
            f'<option value="{item}"{" selected" if limit == str(item) else ""}>{item}</option>'
            for item in (10, 25, 50, 100)
        )
        + "</select></label>"
    )
    return '<form class="query-form" method="get" action="/"><fieldset><legend>Fixed query</legend><div class="form-grid">' + "".join(fields) + '</div><div class="form-actions"><button type="submit">Inspect</button></div></fieldset></form>'


def _input(
    name: str,
    label: str,
    value: Any,
    *,
    input_type: str = "text",
    placeholder: str = "",
) -> str:
    rendered = "" if value is None else str(value)
    return (
        f'<label>{html.escape(label)}<input type="{input_type}" name="{html.escape(name, quote=True)}" '
        f'value="{html.escape(rendered, quote=True)}" placeholder="{html.escape(placeholder, quote=True)}"></label>'
    )


def _render_pager(
    raw_query: Mapping[str, str], view: str, page: int, limit: int, total: int
) -> str:
    links: list[str] = []
    if page > 1:
        previous = dict(raw_query)
        previous.update({"view": view, "page": str(page - 1), "limit": str(limit)})
        links.append(f'<a href="/?{html.escape(urlencode(previous), quote=True)}">Previous</a>')
    if page * limit < total:
        following = dict(raw_query)
        following.update({"view": view, "page": str(page + 1), "limit": str(limit)})
        links.append(f'<a href="/?{html.escape(urlencode(following), quote=True)}">Next</a>')
    return '<div class="form-actions">' + " · ".join(links) + "</div>" if links else ""


def _html_value(value: Any) -> str:
    if value is None:
        return '<span class="null">—</span>'
    return html.escape(str(value), quote=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect canonical market and macro data locally")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args(argv)
    application = build_canonical_inspector(arguments.project_root)
    server = create_server(application, host="127.0.0.1", port=arguments.port)
    print(f"Canonical Data Inspector: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "CanonicalInspectorApplication",
    "CanonicalInspectorReadService",
    "build_canonical_inspector",
    "main",
)
