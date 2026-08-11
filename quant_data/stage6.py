"""Deterministic offline acceptance harness for the Stage 6 local portal."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from .dashboard import (
    INTER_FONT_SHA256,
    INTER_LICENSE_SHA256,
    Stage6Application,
)
from .errors import ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict, loads_strict
from .migrations import initialize_all
from .registry import CANONICAL_REGISTRY_PATH, load_registry, stage6_registry_profile
from .stage1 import explicit_store_map
from .stage5 import run_clean_stage5_rebuild
from .stores import StoreMap


_PAGE_ROUTES = (
    "/",
    "/gdp-vintages",
    "/table-inspector",
    "/agent-tools",
)
_API_ROUTES = (
    "/api/health",
    (
        "/api/gdp-vintages?series=real-growth&mode=latest"
        "&date_only_policy=completed_date&limit=25"
    ),
    (
        "/api/table-inspector?view=market-prices&sort=trade_date"
        "&direction=desc&page=1&limit=25"
    ),
    "/api/agent-tools",
)
_ASSET_ROUTES = (
    "/assets/dashboard.css",
    "/assets/dashboard.js",
    "/assets/inter-variable.woff2",
)
_DASHBOARD_IDS = (
    "stage1.overview",
    "stage6.gdp_vintages",
    "stage6.table_inspector",
    "stage6.agent_tools",
)


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 6 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 6 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _assert_unchanged(
    store_map: StoreMap,
    before: Mapping[str, Any],
    message: str,
) -> None:
    if dumps_strict(before) != dumps_strict(mutation_fingerprint(store_map)):
        raise ValidationError(message)


def _response_summary(
    application: Stage6Application,
    route: str,
    *,
    expected_content_prefix: str,
) -> dict[str, Any]:
    response = application.handle("GET", route)
    if response.status != 200:
        raise ValidationError("Stage 6 portal route did not return a successful response")
    if not response.content_type.startswith(expected_content_prefix):
        raise ValidationError("Stage 6 portal route returned the wrong media type")
    return {
        "status": response.status,
        "content_type": response.content_type,
        "byte_count": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
    }


def _portal_snapshot(
    application: Stage6Application,
) -> dict[str, Any]:
    pages: dict[str, Any] = {}
    for route in _PAGE_ROUTES:
        response = application.handle("GET", route)
        if response.status != 200 or response.content_type != "text/html; charset=utf-8":
            raise ValidationError("Stage 6 page route did not return reviewed HTML")
        document = response.body.decode("utf-8")
        for navigation_route in _PAGE_ROUTES:
            if f'href="{navigation_route}"' not in document:
                raise ValidationError("Stage 6 page omitted persistent navigation")
        if "http://" in document or "https://" in document:
            raise ValidationError("Stage 6 page referenced an external runtime resource")
        pages[route] = {
            "status": response.status,
            "content_type": response.content_type,
            "byte_count": len(response.body),
            "sha256": hashlib.sha256(response.body).hexdigest(),
        }

    apis: dict[str, Any] = {}
    for route in _API_ROUTES:
        response = application.handle("GET", route)
        if response.status != 200 or not response.content_type.startswith(
            "application/json"
        ):
            raise ValidationError("Stage 6 API route did not return reviewed JSON")
        payload = loads_strict(response.body)
        if not isinstance(payload, dict):
            raise ValidationError("Stage 6 API route returned a non-object payload")
        apis[route] = {
            "status": response.status,
            "content_type": response.content_type,
            "byte_count": len(response.body),
            "sha256": hashlib.sha256(response.body).hexdigest(),
        }

    assets = {
        route: _response_summary(
            application,
            route,
            expected_content_prefix={
                "/assets/dashboard.css": "text/css",
                "/assets/dashboard.js": "application/javascript",
                "/assets/inter-variable.woff2": "font/woff2",
            }[route],
        )
        for route in _ASSET_ROUTES
    }
    if assets["/assets/inter-variable.woff2"]["sha256"] != INTER_FONT_SHA256:
        raise ValidationError("Stage 6 font response changed from its approved pin")
    result: dict[str, Any] = {
        "pages": pages,
        "apis": apis,
        "assets": assets,
    }
    result["sha256"] = hashlib.sha256(
        dumps_strict(result).encode("utf-8")
    ).hexdigest()
    return result


def run_clean_stage6_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, Any]:
    """Run Stage 5, project the 2.4 registry, and exercise the portal read-only."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    stage5_evidence = run_clean_stage5_rebuild(
        project_root=project,
        work_root=root / "stage5",
    )
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage6_registry_profile(registry)
    if (
        registry.registry_version != "2.4.0"
        or registry.schema_version != "1.2.0"
        or tuple(item["id"] for item in registry.dashboard) != _DASHBOARD_IDS
    ):
        raise ValidationError("Stage 6 requires the reviewed 2.4.0 dashboard registry")

    source = explicit_store_map(
        root / "stage5" / "stage4" / "stage3" / "stage2" / "source"
    )
    restored = explicit_store_map(root / "stage5" / "stage4" / "restored")
    source_heads = initialize_all(source, registry)
    restored_heads = initialize_all(restored, registry)
    if source_heads != restored_heads:
        raise ValidationError("Stage 6 source and restored migration heads diverged")

    source_before = mutation_fingerprint(source)
    source_snapshot = _portal_snapshot(Stage6Application(source, registry))
    _assert_unchanged(source, source_before, "Stage 6 source portal changed a store")

    restored_before = mutation_fingerprint(restored)
    restored_snapshot = _portal_snapshot(Stage6Application(restored, registry))
    _assert_unchanged(
        restored,
        restored_before,
        "Stage 6 restored portal changed a store",
    )
    if dumps_strict(source_snapshot) != dumps_strict(restored_snapshot):
        raise ValidationError("Stage 6 source and restored portal outputs diverged")

    license_path = project / "quant_data/dashboard/licenses/INTER-OFL-1.1.txt"
    if (
        not license_path.is_file()
        or hashlib.sha256(license_path.read_bytes()).hexdigest()
        != INTER_LICENSE_SHA256
    ):
        raise ValidationError("Stage 6 Inter license pin is unavailable")

    evidence: dict[str, Any] = {
        "contract": "quant_data.stage6_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "stage5_evidence_sha256": stage5_evidence["sha256"],
        "dashboard_ids": [item["id"] for item in registry.dashboard],
        "dashboard_routes": [item["route"] for item in registry.dashboard],
        "dashboard_api_routes": [
            route
            for item in registry.dashboard
            for route in item["api_routes"]
        ],
        "portal_snapshot_sha256": source_snapshot["sha256"],
        "page_sha256": {
            route: item["sha256"]
            for route, item in source_snapshot["pages"].items()
        },
        "api_sha256": {
            route: item["sha256"]
            for route, item in source_snapshot["apis"].items()
        },
        "asset_sha256": {
            route: item["sha256"]
            for route, item in source_snapshot["assets"].items()
        },
        "inter_font_sha256": INTER_FONT_SHA256,
        "inter_license_sha256": INTER_LICENSE_SHA256,
        "migration_heads": source_heads,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "source_restored_equal": True,
        "loopback_only": True,
        "runtime_network_assets": 0,
        "jobs": len(registry.raw["jobs"]),
        "exports": len(registry.raw["exports"]),
    }
    evidence["sha256"] = hashlib.sha256(
        dumps_strict(evidence).encode("utf-8")
    ).hexdigest()
    return evidence


def compare_clean_stage6_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, Any]:
    first = run_clean_stage6_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage6_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two clean Stage 6 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage6_rebuilds", "run_clean_stage6_rebuild")
