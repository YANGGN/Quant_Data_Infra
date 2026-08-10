"""Deterministic offline acceptance harness for the Stage 5 tool platform."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .boundary import Stage1Application, ToolDispatcher
from .errors import CapabilityUnavailableError, ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict, loads_strict
from .migrations import initialize_all
from .registry import CANONICAL_REGISTRY_PATH, PUBLIC_TOOL_NAMES, load_registry
from .stage1 import explicit_store_map
from .stage4 import run_clean_stage4_rebuild
from .stores import StoreMap
from .tool_platform.generate import CATALOG_RESOURCE


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 5 work root must be a directory")
        if any(root.iterdir()):
            raise ConflictError("Stage 5 work root must be empty")
    else:
        root.mkdir(parents=True)
    return root.resolve(strict=True)


def _assert_unchanged(store_map: StoreMap, before: dict[str, Any], message: str) -> None:
    after = mutation_fingerprint(store_map)
    if dumps_strict(after) != dumps_strict(before):
        raise ValidationError(message)


def _tool_matrix(store_map: StoreMap, registry: Any) -> dict[str, Any]:
    dispatcher = ToolDispatcher(store_map, registry)
    application = Stage1Application(store_map, registry)
    manifest = dispatcher.manifest()
    if tuple(item["name"] for item in manifest["tools"]) != PUBLIC_TOOL_NAMES:
        raise ValidationError("Stage 5 manifest inventory drifted")
    matrix: dict[str, Any] = {}
    for declaration in registry.tools:
        name = declaration["id"]
        arguments = declaration["examples"][0]
        envelope = {
            "api_version": "1.0",
            "tool": name,
            "arguments": arguments,
        }
        if name == "macro.get_intraday_releases":
            try:
                dispatcher.call(name, arguments)
            except CapabilityUnavailableError as exc:
                direct = exc.to_dict()
            else:  # pragma: no cover - gate assertion
                raise ValidationError("Offline intraday capability unexpectedly executed")
            response = application.handle(
                "POST",
                "/api/agent-tools/call",
                headers={"Content-Type": "application/json"},
                body=dumps_strict(envelope).encode("utf-8"),
            )
            payload = loads_strict(response.body)
            if response.status != 503 or payload["error"]["code"] != "capability_unavailable":
                raise ValidationError("Offline intraday HTTP failure contract drifted")
            matrix[name] = {
                "outcome": "capability_unavailable",
                "direct_sha256": hashlib.sha256(
                    dumps_strict(direct).encode("utf-8")
                ).hexdigest(),
                "http_status": response.status,
                "http_error_code": payload["error"]["code"],
                "parity": True,
            }
            continue

        result = dispatcher.call(name, arguments)
        response = application.handle(
            "POST",
            "/api/agent-tools/call",
            headers={"Content-Type": "application/json"},
            body=dumps_strict(envelope).encode("utf-8"),
        )
        if response.status != 200:
            raise ValidationError(f"Stage 5 example failed over HTTP: {name}")
        payload = loads_strict(response.body)
        if dumps_strict(payload["result"]) != dumps_strict(result):
            raise ValidationError(f"HTTP and in-process results diverged: {name}")
        strict_result = dumps_strict(result)
        stable_receipt = dict(payload["receipt"])
        for volatile_field in ("started_at", "finished_at", "elapsed_seconds"):
            stable_receipt.pop(volatile_field, None)
        strict_receipt = dumps_strict(stable_receipt)
        matrix[name] = {
            "outcome": result.get("status", "succeeded"),
            "result_sha256": hashlib.sha256(strict_result.encode("utf-8")).hexdigest(),
            "receipt_sha256": hashlib.sha256(strict_receipt.encode("utf-8")).hexdigest(),
            "http_status": response.status,
            "parity": True,
        }
    return {
        "manifest_sha256": hashlib.sha256(
            dumps_strict(manifest).encode("utf-8")
        ).hexdigest(),
        "tools": matrix,
        "sha256": hashlib.sha256(
            dumps_strict(matrix).encode("utf-8")
        ).hexdigest(),
    }


def run_clean_stage5_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, Any]:
    """Run Stage 4, upgrade declarations, and exercise all 57 tools read-only."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    stage4_evidence = run_clean_stage4_rebuild(
        project_root=project,
        work_root=root / "stage4",
    )
    registry = load_registry(
        project / CANONICAL_REGISTRY_PATH,
        project_root=project,
        environment={},
    )
    if registry.registry_version != "2.3.0" or len(registry.tools) != 57:
        raise ValidationError("Stage 5 requires the reviewed 2.3.0 registry")

    source = explicit_store_map(root / "stage4" / "stage3" / "stage2" / "source")
    restored = explicit_store_map(root / "stage4" / "restored")
    source_heads = initialize_all(source, registry)
    restored_heads = initialize_all(restored, registry)
    if source_heads != restored_heads:
        raise ValidationError("Stage 5 source and restored migration heads diverged")

    source_before = mutation_fingerprint(source)
    source_matrix = _tool_matrix(source, registry)
    _assert_unchanged(source, source_before, "Stage 5 source tools changed a store")
    restored_before = mutation_fingerprint(restored)
    restored_matrix = _tool_matrix(restored, registry)
    _assert_unchanged(restored, restored_before, "Stage 5 restored tools changed a store")
    if dumps_strict(source_matrix) != dumps_strict(restored_matrix):
        raise ValidationError("Stage 5 source and restored tool outputs diverged")

    catalog_bytes = (project / CATALOG_RESOURCE).read_bytes()
    outcomes: dict[str, int] = {}
    for item in source_matrix["tools"].values():
        outcome = str(item["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    evidence: dict[str, Any] = {
        "contract": "quant_data.stage5_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "stage4_evidence_sha256": stage4_evidence["sha256"],
        "schema_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "manifest_sha256": source_matrix["manifest_sha256"],
        "tool_matrix_sha256": source_matrix["sha256"],
        "tool_count": len(source_matrix["tools"]),
        "outcome_counts": outcomes,
        "migration_heads": source_heads,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "source_restored_equal": True,
        "http_in_process_parity": True,
        "offline_intraday_disabled": True,
        "jobs": len(registry.raw["jobs"]),
        "exports": len(registry.raw["exports"]),
    }
    evidence["sha256"] = hashlib.sha256(
        dumps_strict(evidence).encode("utf-8")
    ).hexdigest()
    return evidence


def compare_clean_stage5_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, Any]:
    first = run_clean_stage5_rebuild(
        project_root=project_root,
        work_root=first_work_root,
    )
    second = run_clean_stage5_rebuild(
        project_root=project_root,
        work_root=second_work_root,
    )
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two clean Stage 5 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage5_rebuilds", "run_clean_stage5_rebuild")
