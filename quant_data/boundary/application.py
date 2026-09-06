"""Dependency-light local HTTP and Overview surface for the Stage 1 slice."""

from __future__ import annotations

import html
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

from quant_data.errors import Issue, QuantDataError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.json_codec import MAX_JSON_BYTES, dumps_strict, loads_strict
from quant_data.registry import Registry
from quant_data.stores import STORE_ROLES, StoreMap, read_connection
from quant_data.temporal import DateOnlyPolicy, TemporalValue, parse_date
from quant_data.tool_platform.context import CancellationToken

from .dispatcher import ToolDispatcher, UnknownToolError


_CSP = "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
}
_LOOPBACK_HOSTS = {"127.0.0.1"}
_PRICE_QUERY_FIELDS = {
    "instrument_id",
    "provider_symbol",
    "provider",
    "price_variant",
    "currency_segment",
    "start_date",
    "end_date",
    "mode",
    "as_of",
    "date_only_policy",
    "limit",
}
_OVERVIEW_MAX_ROWS = 10
_OVERVIEW_MARKET_PREVIEWS = (
    (
        "SPY",
        {
            "provider": "fixture_fmp",
            "provider_symbol": "SPY",
            "price_variant": "raw",
            "currency_segment": "USD",
            "start_date": "2026-07-16",
            "end_date": "2026-07-17",
            "mode": "latest",
            "date_only_policy": "completed_date",
            "limit": str(_OVERVIEW_MAX_ROWS),
        },
    ),
    (
        "^GSPC",
        {
            "provider": "fixture_fmp",
            "provider_symbol": "^GSPC",
            "price_variant": "raw",
            "currency_segment": "USD",
            "start_date": "2026-07-16",
            "end_date": "2026-07-17",
            "mode": "latest",
            "date_only_policy": "completed_date",
            "limit": str(_OVERVIEW_MAX_ROWS),
        },
    ),
)
_OVERVIEW_MACRO_ARGUMENTS = {
    "series_id": "fixture:philadelphia_fed_rtdsm:EMPLOY",
    "start_date": "2026-01-01",
    "end_date": "2026-02-28",
    "vintage_mode": "as_of",
    "as_of": "2026-07-09",
    "date_only_policy": "completed_date",
    "limit": _OVERVIEW_MAX_ROWS,
}


class RouteNotFoundError(QuantDataError):
    code = "route_not_found"
    http_status = HTTPStatus.NOT_FOUND


class MethodNotAllowedError(QuantDataError):
    code = "method_not_allowed"
    http_status = HTTPStatus.METHOD_NOT_ALLOWED


class InternalServerError(QuantDataError):
    code = "internal_error"
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"


class Stage1Application:
    """Host-owned API and Overview implementation over explicit store paths.

    Construction opens no store.  Each route uses only a registered read
    gateway or a server-owned ``read_connection`` and never initializes,
    migrates, attaches, or writes a database.
    """

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        cancellation: CancellationToken | None = None,
        quote_fetcher=None,
    ) -> None:
        self._store_map = store_map
        self._registry = registry
        self._dispatcher = ToolDispatcher(
            store_map,
            registry,
            cancellation=cancellation,
            quote_fetcher=quote_fetcher,
        )

    @property
    def dispatcher(self) -> ToolDispatcher:
        return self._dispatcher

    @property
    def api_version(self) -> str:
        return self._dispatcher.api_version

    def handle(
        self,
        method: str,
        target: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> HttpResponse:
        """Serve one request without relying on a global process context."""

        try:
            if not isinstance(method, str):
                raise ValidationError("HTTP method is invalid")
            if not isinstance(target, str) or not target.startswith("/"):
                raise ValidationError("Request target is invalid")
            if not isinstance(body, bytes):
                raise ValidationError("HTTP body must be bytes")
            if len(body) > MAX_JSON_BYTES:
                raise ResourceLimitError("Request body exceeds the supported byte limit")
            request_headers = dict(headers or {})
            split = urlsplit(target)
            if split.scheme or split.netloc or split.fragment:
                raise ValidationError("Request target is invalid")
            path = split.path
            query = _parse_query(split.query)
            normalized_method = method.upper()

            if normalized_method == "GET":
                return self._handle_get(path, query, request_headers)
            if normalized_method == "POST":
                return self._handle_post(path, query, request_headers, body)
            raise MethodNotAllowedError("HTTP method is not supported")
        except QuantDataError as exc:
            return self.error_response(exc)
        except Exception:  # Do not surface implementation details at the public boundary.
            return self.error_response(InternalServerError("Internal server error"))

    def error_response(
        self, error: QuantDataError, *, receipt: Mapping[str, Any] | None = None
    ) -> HttpResponse:
        payload = {
            "api_version": self.api_version,
            "execution": "read_only",
            "error": error.to_dict(),
            "receipt": dict(receipt or {"registry_revision": self._registry.revision}),
        }
        return self._json_response(error.http_status, payload)

    def _handle_get(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        del headers  # Headers are deliberately not a source of store configuration.
        if path == "/":
            _reject_query(query)
            return HttpResponse(
                status=HTTPStatus.OK,
                body=self._overview_html().encode("utf-8"),
                content_type="text/html; charset=utf-8",
            )
        if path == "/api/health":
            _reject_query(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(self._health_payload(), route="health"),
            )
        if path == "/api/price-series":
            result = self._price_series(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(result, route="price_series"),
            )
        if path == "/api/agent-tools":
            _reject_query(query)
            # Discovery consumers use the manifest directly to generate forms.
            manifest = self._dispatcher.manifest()
            manifest["receipt"] = {"registry_revision": self._registry.revision}
            return self._json_response(HTTPStatus.OK, manifest)
        raise RouteNotFoundError("Route was not found")

    def _handle_post(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpResponse:
        del headers
        if path != "/api/agent-tools/call":
            if path in {"/", "/api/health", "/api/price-series", "/api/agent-tools"}:
                raise MethodNotAllowedError("HTTP method is not supported")
            raise RouteNotFoundError("Route was not found")
        _reject_query(query)
        if not body:
            raise ValidationError(
                "Request body is required",
                issues=(Issue("/", "required", "Provide a strict JSON tool envelope"),),
            )
        envelope = loads_strict(body, max_bytes=MAX_JSON_BYTES)
        if not isinstance(envelope, dict):
            raise ValidationError(
                "Tool envelope must be an object",
                issues=(Issue("/", "type", "Expected an object"),),
            )
        candidate_name = envelope.get("tool")
        has_tool_version = "tool_version" in envelope
        candidate_tool_version = envelope.get("tool_version")
        raw_arguments = envelope.get("arguments")
        receipt_arguments = raw_arguments if isinstance(raw_arguments, Mapping) else {}
        try:
            expected_fields = {"api_version", "tool", "arguments"}
            if has_tool_version:
                expected_fields.add("tool_version")
            _require_exact_keys(envelope, expected_fields, "/")
            if envelope["api_version"] != self.api_version:
                raise ValidationError(
                    "Unsupported API version",
                    issues=(
                        Issue(
                            "/api_version",
                            "const",
                            "Use the advertised API version",
                        ),
                    ),
                )
            if not isinstance(candidate_name, str):
                raise ValidationError(
                    "Tool name must be a string",
                    issues=(
                        Issue(
                            "/tool",
                            "type",
                            "Expected a registered public tool name",
                        ),
                    ),
                )
            if has_tool_version and (
                not isinstance(candidate_tool_version, str)
                or not candidate_tool_version
            ):
                raise ValidationError(
                    "Tool version must be a nonempty string",
                    issues=(
                        Issue(
                            "/tool_version",
                            "type",
                            "Expected an advertised semantic version",
                        ),
                    ),
                )
            if not isinstance(raw_arguments, dict):
                raise ValidationError(
                    "Tool arguments must be an object",
                    issues=(Issue("/arguments", "type", "Expected an object"),),
                )
            name = candidate_name
            result = self._dispatcher.call(
                name,
                raw_arguments,
                tool_version=(
                    candidate_tool_version if has_tool_version else None
                ),
            )
        except QuantDataError as exc:
            if not isinstance(candidate_name, str):
                raise
            if has_tool_version and not isinstance(candidate_tool_version, str):
                receipt = {"registry_revision": self._registry.revision}
            else:
                try:
                    receipt = self._dispatcher.receipt_for(
                        candidate_name,
                        receipt_arguments,
                        error_code=exc.code,
                        tool_version=(
                            candidate_tool_version
                            if has_tool_version
                            else None
                        ),
                    ).to_primitive()
                except QuantDataError:
                    receipt = {"registry_revision": self._registry.revision}
            return self.error_response(exc, receipt=receipt)
        except Exception:
            if not isinstance(candidate_name, str):
                raise
            error = InternalServerError("Internal server error")
            if has_tool_version and not isinstance(candidate_tool_version, str):
                receipt = {"registry_revision": self._registry.revision}
            else:
                try:
                    receipt = self._dispatcher.receipt_for(
                        candidate_name,
                        receipt_arguments,
                        error_code=error.code,
                        tool_version=(
                            candidate_tool_version
                            if has_tool_version
                            else None
                        ),
                    ).to_primitive()
                except QuantDataError:
                    receipt = {"registry_revision": self._registry.revision}
            return self.error_response(error, receipt=receipt)
        receipt = self._dispatcher.receipt_for(
            name,
            raw_arguments,
            result,
            tool_version=(
                candidate_tool_version if has_tool_version else None
            ),
        ).to_primitive()
        return self._json_response(
            HTTPStatus.OK,
            {
                "api_version": self.api_version,
                "execution": "read_only",
                "tool": {"name": name, "version": receipt["tool_version"]},
                "result": result,
                "receipt": receipt,
            },
        )

    def _generic_success(self, result: Mapping[str, Any], *, route: str) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "execution": "read_only",
            "result": dict(result),
            "receipt": {
                "registry_revision": self._registry.revision,
                "route": route,
            },
        }

    def _health_payload(self) -> dict[str, Any]:
        stores: list[dict[str, Any]] = []
        for role in STORE_ROLES:
            try:
                declaration = self._registry.store(role.value)
                with read_connection(
                    self._store_map,
                    role,
                    expected_anchor=declaration.anchor_relation,
                ) as connection:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                    migrations = [
                        row["migration_id"]
                        for row in connection.execute(
                            "SELECT migration_id FROM schema_migrations ORDER BY ordinal"
                        )
                    ]
                    datasets = [
                        {
                            "dataset_id": row["dataset_id"],
                            "last_successful_run_id": row["last_successful_run_id"],
                        }
                        for row in connection.execute(
                            "SELECT dataset_id, last_successful_run_id "
                            "FROM dataset_registry ORDER BY dataset_id"
                        )
                    ]
                stores.append(
                    {
                        "role": role.value,
                        "status": "ok" if integrity == "ok" else "unhealthy",
                        "integrity": integrity,
                        "migration_ids": migrations,
                        "datasets": datasets,
                    }
                )
            except QuantDataError as exc:
                stores.append(
                    {
                        "role": role.value,
                        "status": "unavailable",
                        "error": {"code": exc.code, "message": exc.safe_message},
                    }
                )
        return {"stores": stores}

    def _price_series(self, query: Mapping[str, str]) -> dict[str, Any]:
        _reject_unknown_query(query, _PRICE_QUERY_FIELDS)
        required = {
            "provider",
            "price_variant",
            "currency_segment",
            "start_date",
            "end_date",
            "mode",
            "date_only_policy",
            "limit",
        }
        for key in sorted(required - set(query)):
            raise ValidationError(
                "Price-series query is incomplete",
                issues=(Issue(f"/{key}", "required", "Required query field is missing"),),
            )
        instrument_id = query.get("instrument_id")
        provider_symbol = query.get("provider_symbol")
        if bool(instrument_id) == bool(provider_symbol):
            raise ValidationError(
                "Provide exactly one price identifier",
                issues=(Issue("/instrument_id", "exclusive_identifier", "Use instrument_id or provider_symbol"),),
            )
        start = parse_date(query["start_date"], pointer="/start_date")
        end = parse_date(query["end_date"], pointer="/end_date")
        if end < start:
            raise ValidationError(
                "end_date cannot precede start_date",
                issues=(Issue("/end_date", "range", "End date must be on or after start date"),),
            )
        mode = query["mode"]
        if mode not in {"latest", "as_of"}:
            raise ValidationError(
                "Unsupported price-series mode",
                issues=(Issue("/mode", "enum", "Use latest or as_of"),),
            )
        as_of = query.get("as_of")
        if mode == "as_of":
            if not as_of:
                raise ValidationError(
                    "as_of is required when mode is as_of",
                    issues=(Issue("/as_of", "required_for_as_of", "Provide an ISO date or aware datetime"),),
                )
            TemporalValue.parse(as_of, pointer="/as_of")
        elif as_of is not None:
            raise ValidationError(
                "as_of is only allowed when mode is as_of",
                issues=(Issue("/as_of", "ignored", "Remove as_of for latest mode"),),
            )
        try:
            policy = DateOnlyPolicy(query["date_only_policy"])
        except ValueError as exc:
            raise ValidationError(
                "Unsupported date-only policy",
                issues=(Issue("/date_only_policy", "enum", "Use a registered policy"),),
            ) from exc
        limit = _parse_bounded_int(query["limit"], "/limit", minimum=1, maximum=10_000)

        try:
            from quant_data.market import DailyPriceQuery, DailyPriceRepository
        except ImportError as exc:  # pragma: no cover - protects incomplete hosts
            raise StoreUnavailableError("Market read capability is unavailable") from exc
        price_query = DailyPriceQuery(
            start_date=query["start_date"],
            end_date=query["end_date"],
            provider=query["provider"],
            price_variant=query["price_variant"],
            currency_segment=query["currency_segment"],
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            limit=limit,
            instrument_id=instrument_id,
            provider_symbol=provider_symbol,
        )
        result = DailyPriceRepository(self._store_map, self._registry).get_prices(price_query)
        if not isinstance(result, dict):
            raise InternalServerError("Market read operation returned an invalid result")
        # Strict renderer is the finite-number/no-secret type boundary.  A
        # domain result exceeding the fixed registry response limit cannot be
        # emitted as a partial silent success.
        dumps_strict(result, max_bytes=MAX_JSON_BYTES)
        return result

    def _overview_html(self) -> str:
        """Render the single fixed, no-write Stage 1 Overview.

        The Overview deliberately issues only fixed, small repository/tool
        requests.  Its disabled selectors describe the preview's temporal
        contract; they are not a browser input surface and do not alter any
        server state.
        """

        health = self._health_payload()
        manifest = self._dispatcher.manifest()
        market_previews = self._overview_market_previews()
        macro_preview = self._overview_macro_preview()
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Quant Data Infrastructure — Overview</title></head><body>"
            "<nav id=\"primary-navigation\" aria-label=\"Persistent navigation\">"
            "<a href=\"/\" aria-current=\"page\">Overview</a>"
            "<a href=\"#store-health\">Stores</a>"
            "<a href=\"#market-fixture\">Market</a>"
            "<a href=\"#macro-fixture\">Macro</a>"
            "<a href=\"#tool-manifest\">Tools</a>"
            "</nav><main><h1>Quant Data Infrastructure</h1>"
            "<p>Stage 1 local, read-only inspection surface.</p>"
            f"{self._render_store_health(health)}"
            f"{self._render_dataset_state(health)}"
            f"{self._render_market_preview(market_previews)}"
            f"{self._render_macro_preview(macro_preview)}"
            f"{self._render_tool_manifest(manifest)}"
            "<section id=\"read-services\" aria-labelledby=\"read-services-title\">"
            "<h2 id=\"read-services-title\">Read services</h2>"
            "<ul><li><a href=\"/api/health\">Health JSON</a></li>"
            "<li><a href=\"/api/agent-tools\">Tool manifest JSON</a></li></ul></section>"
            "<p>This page exposes only fixed, read-only inspection links.</p>"
            "</main></body></html>"
        )

    def _overview_market_previews(self) -> list[dict[str, Any]]:
        """Retrieve the two bounded fixture previews without caller input."""

        previews: list[dict[str, Any]] = []
        for symbol, query in _OVERVIEW_MARKET_PREVIEWS:
            try:
                result = self._price_series(query)
            except QuantDataError as exc:
                previews.append(
                    {
                        "symbol": symbol,
                        "result": None,
                        "error": {"code": exc.code, "message": exc.safe_message},
                    }
                )
            else:
                previews.append({"symbol": symbol, "result": result, "error": None})
        return previews

    def _overview_macro_preview(self) -> dict[str, Any]:
        """Compose the fixed macro and describe previews through the tools."""

        try:
            series = self._dispatcher.call(
                "macro.get_series", dict(_OVERVIEW_MACRO_ARGUMENTS)
            )
            description = self._dispatcher.call(
                "timeseries.describe", {"series": series}
            )
        except QuantDataError as exc:
            return {
                "series": None,
                "description": None,
                "error": {"code": exc.code, "message": exc.safe_message},
            }
        return {"series": series, "description": description, "error": None}

    @staticmethod
    def _render_store_health(health: Mapping[str, Any]) -> str:
        rows: list[str] = []
        for store in health.get("stores", []):
            migrations = store.get("migration_ids", [])
            migration_text = ", ".join(str(item) for item in migrations) or "none"
            error = store.get("error")
            state = _html_value(store.get("status"))
            if isinstance(error, Mapping):
                state += "<br><small>" + _html_value(error.get("code")) + "</small>"
            rows.append(
                "<tr><th scope=\"row\"><code>"
                f"{_html_value(store.get('role'))}</code></th>"
                f"<td>{state}</td>"
                f"<td>{_html_value(store.get('integrity'))}</td>"
                f"<td><code>{_html_value(migration_text)}</code></td></tr>"
            )
        return (
            "<section id=\"store-health\" aria-labelledby=\"store-health-title\">"
            "<h2 id=\"store-health-title\">Store health</h2>"
            "<table id=\"store-health-table\"><thead><tr>"
            "<th>Store role</th><th>Status</th><th>Integrity</th><th>Applied migrations</th>"
            "</tr></thead><tbody>"
            f"{''.join(rows)}"
            "</tbody></table></section>"
        )

    @staticmethod
    def _render_dataset_state(health: Mapping[str, Any]) -> str:
        rows: list[str] = []
        for store in health.get("stores", []):
            datasets = store.get("datasets", [])
            if not datasets:
                rows.append(
                    "<tr><th scope=\"row\"><code>"
                    f"{_html_value(store.get('role'))}</code></th>"
                    "<td>none registered for this slice</td><td>—</td></tr>"
                )
                continue
            for dataset in datasets:
                rows.append(
                    "<tr><th scope=\"row\"><code>"
                    f"{_html_value(store.get('role'))}</code></th>"
                    f"<td><code>{_html_value(dataset.get('dataset_id'))}</code></td>"
                    "<td><code>"
                    f"{_html_value(dataset.get('last_successful_run_id'))}</code></td></tr>"
                )
        return (
            "<section id=\"dataset-state\" aria-labelledby=\"dataset-state-title\">"
            "<h2 id=\"dataset-state-title\">Dataset registrations and last successful runs</h2>"
            "<table id=\"dataset-state-table\"><thead><tr>"
            "<th>Store role</th><th>Dataset</th><th>Last successful run</th>"
            "</tr></thead><tbody>"
            f"{''.join(rows)}"
            "</tbody></table></section>"
        )

    @staticmethod
    def _render_market_preview(previews: list[dict[str, Any]]) -> str:
        articles: list[str] = []
        for index, preview in enumerate(previews, start=1):
            symbol = _html_value(preview.get("symbol"))
            result = preview.get("result")
            error = preview.get("error")
            if not isinstance(result, Mapping):
                message = error.get("message") if isinstance(error, Mapping) else "unavailable"
                code = error.get("code") if isinstance(error, Mapping) else "store_unavailable"
                articles.append(
                    f"<article id=\"market-instrument-{index}\"><h3>{symbol}</h3>"
                    "<p class=\"warning\">Market preview unavailable: "
                    f"{_html_value(code)} — {_html_value(message)}</p></article>"
                )
                continue
            instrument = result.get("instrument", {})
            observations = result.get("observations", [])
            rows = "".join(
                "<tr>"
                f"<td>{_html_value(row.get('trade_date'))}</td>"
                f"<td>{_html_value(row.get('open'))}</td>"
                f"<td>{_html_value(row.get('high'))}</td>"
                f"<td>{_html_value(row.get('low'))}</td>"
                f"<td>{_html_value(row.get('close'))}</td>"
                f"<td>{_html_value(row.get('volume'))}</td>"
                f"<td>{_html_value(row.get('available_at'))} "
                f"<small>({_html_value(row.get('available_precision'))})</small></td>"
                f"<td>{_html_value(row.get('captured_at'))} "
                f"<small>({_html_value(row.get('captured_precision'))})</small></td>"
                f"<td><code>{_html_value(row.get('version_id'))}</code></td>"
                f"<td><code>{_html_value(row.get('evidence_id'))}</code></td>"
                "</tr>"
                for row in observations[:_OVERVIEW_MAX_ROWS]
                if isinstance(row, Mapping)
            )
            warnings = _render_warnings(
                result.get("warnings", []),
                truncated=bool(result.get("truncated")),
                selected_count=len(observations),
                label="daily bars",
            )
            articles.append(
                f"<article id=\"market-instrument-{index}\"><h3>{symbol}</h3>"
                "<dl><dt>Instrument ID</dt><dd><code>"
                f"{_html_value(instrument.get('instrument_id') if isinstance(instrument, Mapping) else None)}</code></dd>"
                "<dt>Provider / symbol</dt><dd>"
                f"{_html_value(instrument.get('provider') if isinstance(instrument, Mapping) else None)} / "
                f"{_html_value(instrument.get('provider_symbol') if isinstance(instrument, Mapping) else None)}</dd>"
                "</dl><table><thead><tr><th>Trade date</th><th>Open</th><th>High</th>"
                "<th>Low</th><th>Close</th><th>Volume</th><th>Available / precision</th>"
                "<th>Captured / precision</th><th>Selected version ID</th><th>Evidence ID</th>"
                "</tr></thead><tbody>"
                f"{rows}</tbody></table>{warnings}</article>"
            )
        return (
            "<section id=\"market-fixture\" aria-labelledby=\"market-fixture-title\">"
            "<h2 id=\"market-fixture-title\">Bounded market fixture instruments and daily bars</h2>"
            "<p id=\"market-preview-bound\">Fixed SPY and ^GSPC previews; at most 10 daily bars "
            "per instrument are rendered.</p>"
            f"{''.join(articles)}</section>"
        )

    @staticmethod
    def _render_macro_preview(preview: Mapping[str, Any]) -> str:
        controls = (
            "<fieldset id=\"macro-preview-controls\" disabled>"
            "<legend>Fixed vintage preview controls</legend>"
            "<label for=\"macro-vintage-mode\">Vintage mode</label>"
            "<select id=\"macro-vintage-mode\" name=\"macro-vintage-mode\" disabled>"
            "<option>latest</option><option selected>as_of</option><option>first_release</option>"
            "</select>"
            "<label for=\"macro-cutoff\">Cutoff</label>"
            "<select id=\"macro-cutoff\" name=\"macro-cutoff\" disabled>"
            "<option selected>2026-07-09</option><option>2026-06-12</option>"
            "</select>"
            "<label for=\"macro-date-only-policy\">Date-only policy</label>"
            "<select id=\"macro-date-only-policy\" name=\"macro-date-only-policy\" disabled>"
            "<option selected>completed_date</option><option>calendar_date_inclusive</option>"
            "</select>"
            "</fieldset><p id=\"macro-control-note\">These controls document the fixed, "
            "read-only preview; browser values are not submitted or accepted.</p>"
        )
        series = preview.get("series")
        description = preview.get("description")
        error = preview.get("error")
        if not isinstance(series, Mapping) or not isinstance(description, Mapping):
            message = error.get("message") if isinstance(error, Mapping) else "unavailable"
            code = error.get("code") if isinstance(error, Mapping) else "store_unavailable"
            content = (
                "<p class=\"warning\">Macro preview unavailable: "
                f"{_html_value(code)} — {_html_value(message)}</p>"
            )
        else:
            metadata = series.get("metadata", {})
            audit = series.get("audit", {})
            observations = series.get("observations", [])
            rows = "".join(
                "<tr>"
                f"<td>{_html_value(row.get('period_start'))}</td>"
                f"<td>{_html_value(row.get('period_end'))}</td>"
                f"<td>{_html_value(row.get('value'))}</td>"
                f"<td>{_html_value(row.get('missing_reason'))}</td>"
                f"<td>{_html_value(row.get('vintage_at'))}</td>"
                f"<td>{_html_value(row.get('available_at'))} "
                f"<small>({_html_value(row.get('available_precision'))})</small></td>"
                f"<td>{_html_value(row.get('captured_at'))} "
                f"<small>({_html_value(row.get('captured_precision'))})</small></td>"
                f"<td><code>{_html_value(row.get('version_id'))}</code></td>"
                f"<td><code>{_html_value(row.get('evidence_id'))}</code></td>"
                "</tr>"
                for row in observations[:_OVERVIEW_MAX_ROWS]
                if isinstance(row, Mapping)
            )
            warning_values = list(series.get("warnings", []))
            missing_count = description.get("missing_count", 0)
            if missing_count:
                warning_values.append(
                    f"{missing_count} null observation(s) retained with explicit missingness; no fill is applied"
                )
            warnings = _render_warnings(
                warning_values,
                truncated=bool(series.get("truncated")),
                selected_count=len(observations),
                label="macro observations",
            )
            structured_preview = {
                "macro.get_series": series,
                "timeseries.describe": description,
            }
            content = (
                "<dl><dt>Series ID</dt><dd><code>"
                f"{_html_value(series.get('series_id'))}</code></dd>"
                "<dt>Provider / frequency / unit</dt><dd>"
                f"{_html_value(metadata.get('provider') if isinstance(metadata, Mapping) else None)} / "
                f"{_html_value(metadata.get('frequency') if isinstance(metadata, Mapping) else None)} / "
                f"{_html_value(metadata.get('unit') if isinstance(metadata, Mapping) else None)}</dd>"
                "<dt>Selected mode / cutoff / precision</dt><dd>"
                f"{_html_value(audit.get('mode') if isinstance(audit, Mapping) else None)} / "
                f"{_html_value(audit.get('cutoff') if isinstance(audit, Mapping) else None)} / "
                f"{_html_value(audit.get('cutoff_precision') if isinstance(audit, Mapping) else None)}</dd>"
                "</dl><table id=\"macro-observations\"><thead><tr>"
                "<th>Period start</th><th>Period end</th><th>Value</th><th>Missing reason</th>"
                "<th>Vintage</th><th>Available / precision</th><th>Captured / precision</th>"
                "<th>Selected version ID</th><th>Evidence ID</th>"
                "</tr></thead><tbody>"
                f"{rows}</tbody></table>{warnings}"
                "<details id=\"tool-result-preview\" open><summary>Structured tool result preview</summary>"
                f"<pre>{_html_value(dumps_strict(structured_preview))}</pre></details>"
            )
        return (
            "<section id=\"macro-fixture\" aria-labelledby=\"macro-fixture-title\">"
            "<h2 id=\"macro-fixture-title\">Macro fixture series and vintage selection</h2>"
            f"{controls}{content}</section>"
        )

    @staticmethod
    def _render_tool_manifest(manifest: Mapping[str, Any]) -> str:
        rows = "".join(
            "<tr><th scope=\"row\"><code>"
            f"{_html_value(tool.get('name'))}</code></th>"
            f"<td>{_html_value(tool.get('version'))}</td>"
            f"<td>{_html_value(tool.get('read_only'))}</td>"
            f"<td><code>{_html_value(', '.join(str(item) for item in tool.get('datasets', [])))}</code></td>"
            f"<td>{_html_value(dumps_strict(tool.get('workload_bounds', {})))}</td></tr>"
            for tool in manifest.get("tools", [])
            if isinstance(tool, Mapping)
        )
        return (
            "<section id=\"tool-manifest\" aria-labelledby=\"tool-manifest-title\">"
            "<h2 id=\"tool-manifest-title\">Two-tool manifest</h2>"
            "<table id=\"tool-manifest-table\"><thead><tr><th>Name</th><th>Version</th>"
            "<th>Read only</th><th>Datasets</th><th>Workload bounds</th>"
            "</tr></thead><tbody>"
            f"{rows}</tbody></table>"
            "<details id=\"tool-manifest-preview\" open><summary>Registry manifest preview</summary>"
            f"<pre>{_html_value(dumps_strict(manifest))}</pre></details></section>"
        )

    @staticmethod
    def _json_response(status: int, payload: Mapping[str, Any]) -> HttpResponse:
        return HttpResponse(status=status, body=dumps_strict(dict(payload)).encode("utf-8"))


def create_server(
    application: Stage1Application,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create a loopback-only server without starting its serving loop."""

    if not isinstance(application, Stage1Application):
        raise ValidationError("create_server requires a Stage1Application")
    if host not in _LOOPBACK_HOSTS:
        raise ValidationError("Stage 1 HTTP service may bind only to loopback")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValidationError("HTTP port must be an integer between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        server_version = "QuantDataStage1/1.0"
        sys_version = ""

        def __getattr__(self, name: str) -> Any:
            """Route every parsed HTTP verb through the application boundary.

            ``BaseHTTPRequestHandler`` normally writes a stock HTML 501 page
            when a ``do_<VERB>`` method is absent.  The public application owns
            method policy, so even unimplemented verbs must reach ``handle``
            and receive its sanitized 405 JSON envelope.
            """

            if name.startswith("do_"):
                return self._serve
            raise AttributeError(name)

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            """Replace stdlib HTML parser errors with bounded JSON responses."""

            del message, explain
            # ``parse_request`` invokes ``send_error`` before it accepts a
            # malformed client version.  Its default ``HTTP/0.9`` request
            # state suppresses response status lines and headers, so force a
            # server-owned protocol and close this invalid request.
            self.request_version = "HTTP/1.0"
            self.protocol_version = "HTTP/1.0"
            self.close_connection = True
            if code == HTTPStatus.REQUEST_URI_TOO_LONG:
                error: QuantDataError = ResourceLimitError(
                    "HTTP request line exceeds the supported limit"
                )
            elif code == HTTPStatus.NOT_IMPLEMENTED:
                error = MethodNotAllowedError("HTTP method is not supported")
            elif 400 <= code < 500:
                error = ValidationError("HTTP request is invalid")
            else:
                error = InternalServerError("Internal server error")
            self._write_response(application.error_response(error))

        def log_message(self, format: str, *args: object) -> None:
            # Avoid emitting request paths or arbitrary values into process logs.
            del format, args

        def _serve(self) -> None:
            try:
                body = self._read_body()
                response = application.handle(
                    self.command,
                    self.path,
                    headers={key: value for key, value in self.headers.items()},
                    body=body,
                )
            except QuantDataError as exc:
                response = application.error_response(exc)
            except Exception:
                response = application.error_response(InternalServerError("Internal server error"))
            self._write_response(response)

        def _write_response(self, response: HttpResponse) -> None:
            """Emit every handler response with the fixed public headers."""

            self.send_response(int(response.status))
            for key, value in _SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if str(getattr(self, "command", "")).upper() != "HEAD":
                self.wfile.write(response.body)

        def _read_body(self) -> bytes:
            if self.command.upper() != "POST":
                return b""
            if self.headers.get("Transfer-Encoding"):
                raise ValidationError("Transfer-encoded request bodies are not supported")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValidationError("POST requests require Content-Length")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValidationError("Content-Length is invalid") from exc
            if length < 0:
                raise ValidationError("Content-Length is invalid")
            if length > MAX_JSON_BYTES:
                raise ResourceLimitError("Request body exceeds the supported byte limit")
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValidationError("Request body is incomplete")
            return payload

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def _parse_query(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors="strict")
    except ValueError as exc:
        raise ValidationError("Query string is invalid") from exc
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(
                "Duplicate query field",
                issues=(Issue(f"/{key}", "unique_key", "Query fields must not repeat"),),
            )
        result[key] = value
    return result


def _reject_query(query: Mapping[str, str]) -> None:
    if query:
        key = sorted(query)[0]
        raise ValidationError(
            "This route does not accept query fields",
            issues=(Issue(f"/{key}", "additional_properties", "Query field is not supported"),),
        )


def _reject_unknown_query(query: Mapping[str, str], allowed: set[str]) -> None:
    unknown = sorted(set(query) - allowed)
    if unknown:
        raise ValidationError(
            "Price-series query contains an unknown field",
            issues=(Issue(f"/{unknown[0]}", "additional_properties", "Query field is not supported"),),
        )


def _require_exact_keys(value: Mapping[str, Any], required: set[str], pointer: str) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required)
    if missing:
        raise ValidationError(
            "Tool envelope is incomplete",
            issues=(Issue(f"{pointer}{missing[0]}", "required", "Required field is missing"),),
        )
    if unknown:
        raise ValidationError(
            "Tool envelope contains an unknown field",
            issues=(Issue(f"{pointer}{unknown[0]}", "additional_properties", "Unknown field"),),
        )


def _parse_bounded_int(raw: str, pointer: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(raw, str) or not raw or not raw.isascii() or not raw.isdecimal():
        raise ValidationError(
            "Query field must be an integer",
            issues=(Issue(pointer, "type", "Expected a decimal integer"),),
        )
    value = int(raw)
    if value < minimum or value > maximum:
        raise ValidationError(
            "Query field exceeds its supported range",
            issues=(Issue(pointer, "range", f"Expected {minimum} through {maximum}"),),
        )
    return value


def _html_value(value: Any) -> str:
    """Render a scalar into text-only HTML without trusting its contents."""

    if value is None:
        return '<span class="null">null</span>'
    return html.escape(str(value), quote=True)


def _render_warnings(
    values: Any,
    *,
    truncated: bool,
    selected_count: int,
    label: str,
) -> str:
    """Keep warning, missingness, and truncation state visible and bounded."""

    source = values if isinstance(values, (list, tuple)) else ()
    warnings = [item for item in source if isinstance(item, str)]
    items = [f"<li>{_html_value(item)}</li>" for item in warnings]
    if not items:
        items.append("<li>No domain warnings were returned.</li>")
    if truncated:
        items.append(
            "<li>Result is truncated at the fixed preview bound; "
            f"{_html_value(selected_count)} {html.escape(label)} rendered.</li>"
        )
    else:
        items.append(
            "<li>Result is not truncated; "
            f"{_html_value(selected_count)} {html.escape(label)} selected.</li>"
        )
    return (
        "<section class=\"warnings\" aria-label=\"Warnings and truncation\">"
        "<h4>Warnings, missingness, and truncation</h4><ul>"
        f"{''.join(items)}</ul></section>"
    )
