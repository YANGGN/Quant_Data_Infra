"""Deterministic, dependency-free offline Stage 12A authority gate.

This gate freezes the Market v1 authority and coverage boundary. It reads only
an explicit project registry and immutable Stage 12 scope manifest. It opens no
SQLite connection, reads no credential environment value, calls no provider,
starts no subprocess, and never creates its explicit work root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import ConflictError, Issue, ValidationError
from .json_codec import dumps_strict
from .market.stage12_scope import load_stage12_market_v1_scope
from .registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage10_registry_profile,
    stage12b_registry_profile,
)
from .stores import StoreRole, resolve_store_map


STAGE12_AUTHORITY_GATE_CONTRACT = "quant_data.stage12a_market_v1_authority_gate"
STAGE12_AUTHORITY_GATE_VERSION = "1.0.0"

# These named constants deliberately do not derive from current registry
# metadata. A registry change must be explicitly reviewed here before the
# authority gate can pass.
STAGE12_CANONICAL_REGISTRY_SCHEMA_VERSION = "1.8.0"
STAGE12_CANONICAL_REGISTRY_VERSION = "2.12.0"
STAGE12_STAGE10_REGISTRY_SCHEMA_VERSION = "1.6.0"
STAGE12_STAGE10_REGISTRY_VERSION = "2.8.0"
STAGE12_STAGE10_HISTORICAL_MARKET_DEFAULT = "data/market_data.sqlite"

_REQUIRED_STORE_DEFAULTS = (
    ("market", "data/market.sqlite"),
    ("macro", "data/macro_data.sqlite"),
    ("company", "data/company_data.sqlite"),
    ("news", "data/news_data.sqlite"),
)
_WORK_ROOT_POLICY = "explicit_absent_or_empty_never_created"


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _conflict(pointer: str, rule: str, message: str) -> ConflictError:
    return ConflictError(message, issues=(Issue(pointer or "/", rule, message),))


def _required_path(value: str | Path, *, name: str, strict: bool) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise _error(f"/{name}", "path", f"An explicit {name} path is required")
    if isinstance(value, str) and not value.strip():
        raise _error(f"/{name}", "path", f"An explicit {name} path is required")
    try:
        supplied = Path(value)
        if not supplied.is_absolute():
            raise _error(
                f"/{name}",
                "absolute_path",
                f"An absolute {name} path is required",
            )
        return supplied.resolve(strict=strict)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _error(f"/{name}", "path", f"The explicit {name} path is unavailable") from exc


def _require_project_root(project_root: str | Path) -> Path:
    project = _required_path(project_root, name="project_root", strict=True)
    if not project.is_dir():
        raise _error("/project_root", "directory", "Project root must be a directory")
    return project


def _require_neutral_work_root(work_root: str | Path, *, project: Path) -> Path:
    # Validate an explicit absent-or-empty root without creating or mutating it.
    root = _required_path(work_root, name="work_root", strict=False)
    broad_roots = {
        Path(root.anchor).resolve(strict=False),
        Path("/tmp").resolve(strict=False),
        Path("/var/tmp").resolve(strict=False),
    }
    protected_roots = {
        project,
        project / "data",
        *(project / default_path for _role, default_path in _REQUIRED_STORE_DEFAULTS),
    }
    if (
        root in broad_roots
        or root in project.parents
        or project in root.parents
        or root in protected_roots
    ):
        raise _conflict(
            "/work_root",
            "broad_root",
            "Stage 12A work root is too broad or overlaps a project data root",
        )
    try:
        if root.exists():
            if not root.is_dir():
                raise _conflict(
                    "/work_root",
                    "directory",
                    "Stage 12A work root must be a directory",
                )
            if next(root.iterdir(), None) is not None:
                raise _conflict(
                    "/work_root",
                    "empty",
                    "Stage 12A work root must be absent or empty and is left unchanged",
                )
    except ConflictError:
        raise
    except OSError as exc:
        raise _error("/work_root", "path", "Stage 12A work root cannot be inspected") from exc
    return root


def _validate_registry(project: Path, scope: Any) -> tuple[Any, Any]:
    # Resolve the current four-store map without opening any SQLite database.
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    registry = stage12b_registry_profile(registry)
    if (
        registry.schema_version,
        registry.registry_version,
    ) != (
        STAGE12_CANONICAL_REGISTRY_SCHEMA_VERSION,
        STAGE12_CANONICAL_REGISTRY_VERSION,
    ):
        raise _error(
            "/registry",
            "revision",
            "Canonical registry does not match the reviewed Stage 12A revision",
        )

    expected_defaults = _REQUIRED_STORE_DEFAULTS
    scope_defaults = tuple(
        (role, scope.store_defaults[role]) for role, _default in expected_defaults
    )
    if scope_defaults != expected_defaults:
        raise _error(
            "/scope/store_defaults",
            "store_defaults",
            "Stage 12A scope does not declare the reviewed four-store defaults",
        )

    registry_defaults = tuple(
        (role, registry.store(role).default_path)
        for role, _default in expected_defaults
    )
    if registry_defaults != expected_defaults:
        raise _error(
            "/registry/stores",
            "store_defaults",
            "Canonical registry does not use the reviewed Stage 12A store defaults",
        )

    resolved = resolve_store_map(
        registry,
        project_root=project,
        environment={},
    )
    expected_market_path = (project / "data" / "market.sqlite").resolve(strict=False)
    if resolved.path(StoreRole.MARKET) != expected_market_path:
        raise _error(
            "/registry/stores/market",
            "resolved_path",
            "Market store does not resolve to the reviewed project-local path",
        )
    for role, default_path in expected_defaults:
        expected_path = (project / default_path).resolve(strict=False)
        if resolved.path(StoreRole(role)) != expected_path:
            raise _error(
                f"/registry/stores/{role}",
                "resolved_path",
                "Store default did not resolve to its reviewed project-local path",
            )

    stage10 = stage10_registry_profile(registry)
    if (
        stage10.schema_version,
        stage10.registry_version,
    ) != (
        STAGE12_STAGE10_REGISTRY_SCHEMA_VERSION,
        STAGE12_STAGE10_REGISTRY_VERSION,
    ):
        raise _error(
            "/registry/stage10_projection",
            "revision",
            "Frozen Stage 10 registry projection drifted",
        )
    if (
        stage10.store(StoreRole.MARKET.value).default_path
        != STAGE12_STAGE10_HISTORICAL_MARKET_DEFAULT
    ):
        raise _error(
            "/registry/stage10_projection/stores/market",
            "historical_path",
            "Frozen Stage 10 market default must remain historical",
        )
    return registry, stage10


def _sha256(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _path_free(serialized: str, *, project: Path, work_root: Path) -> None:
    if str(project) in serialized or str(work_root) in serialized:
        raise _error("/", "path_free", "Stage 12A evidence exposed a physical path")


def run_stage12a_authority_gate(
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    # Validate Stage 12A authority facts with zero operational side effects.
    project = _require_project_root(project_root)
    root = _require_neutral_work_root(work_root, project=project)
    scope = load_stage12_market_v1_scope(
        project / "config" / "stage12_market_v1_scope.json"
    )
    registry, stage10 = _validate_registry(project, scope)

    asset_type_counts = {
        asset_type: sum(
            instrument.asset_type == asset_type for instrument in scope.roster
        )
        for asset_type in ("equity", "etf", "index")
    }
    evidence: dict[str, object] = {
        "approval_status": "offline_authority_coverage_validated",
        "canonical_registry": {
            "revision": registry.registry_version,
            "schema_version": registry.schema_version,
            "source_sha256": registry.source_sha256,
        },
        "contract": STAGE12_AUTHORITY_GATE_CONTRACT,
        "contract_version": STAGE12_AUTHORITY_GATE_VERSION,
        "excluded_claims": list(scope.excluded_claims),
        "execution": {
            "credential_environment_reads": 0,
            "filesystem_mutations": 0,
            "network_calls": 0,
            "provider_requests": 0,
            "scheduler_actions": 0,
            "sqlite_stores_opened": 0,
            "subprocess_calls": 0,
            "work_root_policy": _WORK_ROOT_POLICY,
        },
        "market_v1_baseline": scope.baseline.manifest_mapping(),
        "retained_stage10": scope.retained_stage10.manifest_mapping(),
        "roster": {
            "asset_type_counts": asset_type_counts,
            "sha256": scope.roster_sha256,
            "symbol_count": len(scope.roster),
        },
        "scope_manifest_sha256": scope.manifest_sha256,
        "source_bindings": scope.source_bindings.manifest_mapping(),
        "stage10_projection": {
            "revision": stage10.registry_version,
            "schema_version": stage10.schema_version,
            "source_sha256": stage10.source_sha256,
        },
        "store_defaults": dict(scope.store_defaults),
        "successor_phases": [
            phase.manifest_mapping() for phase in scope.successor_phases
        ],
        "target_profile_id": scope.target_profile_id,
    }
    serialized = dumps_strict(evidence)
    _path_free(serialized, project=project, work_root=root)
    evidence["sha256"] = _sha256(evidence)
    dumps_strict(evidence)
    return evidence


def compare_stage12a_authority_gates(
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    # Require two independent neutral roots to produce identical evidence.
    project = _require_project_root(project_root)
    first_root = _require_neutral_work_root(first_work_root, project=project)
    second_root = _require_neutral_work_root(second_work_root, project=project)
    if first_root == second_root:
        raise _conflict(
            "/work_root",
            "distinct",
            "Stage 12A comparison requires two distinct physical work roots",
        )
    first = run_stage12a_authority_gate(project, first_root)
    second = run_stage12a_authority_gate(project, second_root)
    if dumps_strict(first) != dumps_strict(second):
        raise _error(
            "/",
            "determinism",
            "Two Stage 12A authority gates produced different evidence",
        )
    return first


__all__ = (
    "STAGE12_AUTHORITY_GATE_CONTRACT",
    "STAGE12_AUTHORITY_GATE_VERSION",
    "STAGE12_CANONICAL_REGISTRY_SCHEMA_VERSION",
    "STAGE12_CANONICAL_REGISTRY_VERSION",
    "STAGE12_STAGE10_HISTORICAL_MARKET_DEFAULT",
    "STAGE12_STAGE10_REGISTRY_SCHEMA_VERSION",
    "STAGE12_STAGE10_REGISTRY_VERSION",
    "compare_stage12a_authority_gates",
    "run_stage12a_authority_gate",
)
