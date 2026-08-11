"""Stage 6 local portal over the established read-only HTTP boundary."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from typing import Any, Mapping

from ..boundary.application import (
    HttpResponse,
    MethodNotAllowedError,
    Stage1Application,
)
from ..errors import Issue, QuantDataError, ValidationError
from ..registry import Registry
from ..stores import StoreMap
from ..tool_platform.context import CancellationToken
from .presentation import (
    render_agent_tools_page,
    render_gdp_vintages_page,
    render_overview_page,
    render_table_inspector_page,
)
from .read_services import Stage6DashboardReadService


INTER_FONT_SHA256 = "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3"
INTER_LICENSE_SHA256 = "262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a"
_DASHBOARD_IDS = (
    "stage1.overview",
    "stage6.gdp_vintages",
    "stage6.table_inspector",
    "stage6.agent_tools",
)
_ASSET_ROOT = Path(__file__).with_name("static")
_LICENSE_PATH = Path(__file__).with_name("licenses") / "INTER-OFL-1.1.txt"
_ASSET_DECLARATIONS = {
    "/assets/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/assets/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
    "/assets/inter-variable.woff2": ("inter-variable.woff2", "font/woff2"),
}
_HTML_ROUTES = {"/", "/gdp-vintages", "/table-inspector", "/agent-tools"}
_GET_ONLY_ROUTES = {
    *_HTML_ROUTES,
    *_ASSET_DECLARATIONS,
    "/api/gdp-vintages",
    "/api/table-inspector",
}


class Stage6Application(Stage1Application):
    """Four-route, local-only portal with no write or path selection surface."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if (
            registry.registry_version != "2.4.0"
            or registry.schema_version != "1.2.0"
            or tuple(item["id"] for item in registry.dashboard) != _DASHBOARD_IDS
        ):
            raise ValidationError("Stage 6 requires the reviewed 2.4.0 dashboard registry")
        super().__init__(store_map, registry, cancellation=cancellation)
        self._dashboard_reads = Stage6DashboardReadService(store_map, registry)
        self._assets = self._load_assets()

    def _handle_get(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        del headers
        asset = self._assets.get(path)
        if asset is not None:
            _reject_query(query)
            body, content_type = asset
            return HttpResponse(HTTPStatus.OK, body, content_type)
        if path == "/":
            _reject_query(query)
            context = {
                "health": self._health_payload(),
                "receipt": {
                    "registry_revision": self._registry.revision,
                    "route": "overview",
                },
            }
            return _html_response(render_overview_page(context))
        if path == "/gdp-vintages":
            result = self._page_result(
                lambda: self._dashboard_reads.gdp_vintages(query),
                query=query,
            )
            return _html_response(render_gdp_vintages_page(result))
        if path == "/table-inspector":
            result = self._page_result(
                lambda: self._dashboard_reads.table_inspector(query),
                query=query,
            )
            return _html_response(render_table_inspector_page(result))
        if path == "/agent-tools":
            _reject_query(query)
            manifest = self.dispatcher.manifest()
            manifest["receipt"] = {
                "registry_revision": self._registry.revision,
                "route": "agent_tools",
            }
            return _html_response(render_agent_tools_page(manifest))
        if path == "/api/gdp-vintages":
            result = self._dashboard_reads.gdp_vintages(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(result, route="gdp_vintages"),
            )
        if path == "/api/table-inspector":
            result = self._dashboard_reads.table_inspector(query)
            return self._json_response(
                HTTPStatus.OK,
                self._generic_success(result, route="table_inspector"),
            )
        return super()._handle_get(path, query, {})

    def _handle_post(
        self,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpResponse:
        if path in _GET_ONLY_ROUTES:
            raise MethodNotAllowedError("HTTP method is not supported")
        return super()._handle_post(path, query, headers, body)

    @staticmethod
    def _page_result(
        loader: Any,
        *,
        query: Mapping[str, str],
    ) -> dict[str, Any]:
        try:
            return loader()
        except QuantDataError as exc:
            return {
                "state": "error",
                "error": exc.to_dict(),
                "query": dict(query),
                "rows": [],
                "observations": [],
                "warnings": [],
            }

    @staticmethod
    def _load_assets() -> dict[str, tuple[bytes, str]]:
        root = _ASSET_ROOT.resolve(strict=True)
        assets: dict[str, tuple[bytes, str]] = {}
        for route, (filename, content_type) in _ASSET_DECLARATIONS.items():
            path = (root / filename).resolve(strict=True)
            try:
                path.relative_to(root)
            except ValueError as exc:  # pragma: no cover - constant-path defense
                raise ValidationError("Dashboard asset escaped its package root") from exc
            assets[route] = (path.read_bytes(), content_type)
        if hashlib.sha256(assets["/assets/inter-variable.woff2"][0]).hexdigest() != INTER_FONT_SHA256:
            raise ValidationError("Bundled Inter font checksum mismatch")
        if (
            not _LICENSE_PATH.is_file()
            or hashlib.sha256(_LICENSE_PATH.read_bytes()).hexdigest()
            != INTER_LICENSE_SHA256
        ):
            raise ValidationError("Bundled Inter license checksum mismatch")
        return assets


def _reject_query(query: Mapping[str, str]) -> None:
    if query:
        field = sorted(query)[0]
        raise ValidationError(
            "This route does not accept query fields",
            issues=(
                Issue(
                    f"/{field}",
                    "additional_properties",
                    "Query field is not supported",
                ),
            ),
        )


def _html_response(value: str) -> HttpResponse:
    if not isinstance(value, str):
        raise ValidationError("Dashboard renderer returned an invalid document")
    return HttpResponse(
        status=HTTPStatus.OK,
        body=value.encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )


__all__ = (
    "INTER_FONT_SHA256",
    "INTER_LICENSE_SHA256",
    "Stage6Application",
)
