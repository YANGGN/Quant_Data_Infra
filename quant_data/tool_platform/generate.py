"""Deterministically generate the tool surface inside the evolving registry."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from quant_data.tool_platform.catalog import (
    CATALOG_ID,
    CATALOG_VERSION,
    LEGACY_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    VERSIONED_CATALOG_ID,
    VERSIONED_CATALOG_VERSION,
    build_additive_tool_entries,
    build_current_tool_entries,
    build_tool_entries,
    build_tool_version_policies,
    schema_catalog,
    versioned_schema_catalog,
)


REGISTRY_RESOURCE = Path("config/system_registry.json")
_REVIEWED_REGISTRY_SOURCE_SHA256 = {
    ("1.9.0", "2.37.0"): (
        "2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b"
    ),
    ("1.9.0", "2.38.0"): (
        "3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547"
    ),
    ("1.9.0", "2.39.0"): (
        "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
    ),
    ("1.9.0", "2.40.0"): (
        "72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7"
    ),
    ("1.9.0", "2.41.0"): (
        "835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3"
    ),
    ("1.9.0", "2.42.0"): (
        "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
    ),
    ("1.9.0", "2.43.0"): (
        "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
    ),
    ("1.9.0", "2.44.0"): (
        "af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182"
    ),
    ("1.9.0", "2.45.0"): (
        "f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2"
    ),
    ("1.9.0", "2.46.0"): (
        "b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264"
    ),
    ("1.9.0", "2.47.0"): (
        "eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8"
    ),
}
CATALOG_RESOURCE = Path("quant_data/generated/tool_contract_schemas_v1.json")
VERSIONED_CATALOG_RESOURCE = Path(
    "quant_data/generated/tool_contract_schemas_v2.json"
)
_ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS = frozenset(
    {
        "stats.distribution_diagnostics",
        "stats.covariance_matrix",
        "stats.bootstrap_confidence_interval",
        "stats.principal_components",
    }
)
_ANALYTICS_FOUNDATION_VERSIONED_TOOL_IDS = frozenset(
    {
        "data.quality_audit",
        "timeseries.transform",
    }
)


def _render(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def generated_bytes(project_root: Path) -> tuple[bytes, bytes, bytes]:
    registry_path = project_root / REGISTRY_RESOURCE
    source_bytes = registry_path.read_bytes()
    raw = json.loads(source_bytes)
    source_version = (
        raw.get("schema_version"),
        raw.get("registry_version"),
    )
    expected_source_sha256 = _REVIEWED_REGISTRY_SOURCE_SHA256.get(
        source_version
    )
    if (
        expected_source_sha256 is None
        or hashlib.sha256(source_bytes).hexdigest() != expected_source_sha256
    ):
        raise ValueError("Tool generation requires an exact reviewed registry source")
    existing = {item["id"]: item for item in raw["tools"]}
    try:
        legacy = {name: existing[name] for name in LEGACY_TOOL_NAMES}
    except KeyError as exc:
        raise ValueError("Canonical registry lost a Stage 1 contract") from exc

    recovered_entries = build_tool_entries(legacy)
    entries = build_current_tool_entries(legacy)
    additive_entries = build_additive_tool_entries()
    version_policies = build_tool_version_policies()
    catalog_version = VERSIONED_CATALOG_VERSION
    if source_version == ("1.9.0", "2.45.0"):
        entries = tuple(
            item
            for item in entries
            if item["id"] not in _ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS
        )
        additive_entries = tuple(
            item
            for item in additive_entries
            if item["id"] not in _ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS
        )
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in _ANALYTICS_FOUNDATION_VERSIONED_TOOL_IDS
        )
        catalog_version = "2.9.0"
    elif source_version not in {
        ("1.9.0", "2.45.0"),
        ("1.9.0", "2.46.0"),
        ("1.9.0", "2.47.0"),
    }:
        step1_additions = {
            "macro.get_release_calendar",
            "market.get_volume_series",
        }
        macro_v2_tools = {
            "macro.search_series",
            "macro.describe_series",
            "macro.get_series",
        }
        entries = tuple(
            item for item in entries if item["id"] not in step1_additions
        )
        additive_entries = tuple(
            item
            for item in additive_entries
            if item["id"] not in step1_additions
        )
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in macro_v2_tools
        )
        catalog_version = "2.8.0"
    catalog_payload = schema_catalog(recovered_entries)
    catalog_bytes = _render(catalog_payload)
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()
    versioned_catalog_payload = versioned_schema_catalog(
        version_policies,
        additive_entries,
    )
    versioned_catalog_payload["schema_version"] = catalog_version
    versioned_catalog_bytes = _render(versioned_catalog_payload)
    versioned_catalog_sha = hashlib.sha256(versioned_catalog_bytes).hexdigest()

    raw["schema_version"] = "1.9.0"
    raw["registry_version"] = (
        "2.47.0"
        if source_version
        in {("1.9.0", "2.46.0"), ("1.9.0", "2.47.0")}
        else (
            "2.46.0"
            if source_version == ("1.9.0", "2.45.0")
            else (
                "2.44.0"
                if source_version == ("1.9.0", "2.44.0")
                else (
                    "2.43.0"
                    if source_version == ("1.9.0", "2.43.0")
                    else (
                        "2.42.0"
                        if source_version
                        in {
                            ("1.9.0", "2.40.0"),
                            ("1.9.0", "2.41.0"),
                            ("1.9.0", "2.42.0"),
                        }
                        else "2.39.0"
                    )
                )
            )
        )
    )
    raw["tool_schema_catalog"] = {
        "schema_id": CATALOG_ID,
        "schema_version": CATALOG_VERSION,
        "resource": CATALOG_RESOURCE.as_posix(),
        "sha256": catalog_sha,
    }
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": catalog_version,
        "resource": VERSIONED_CATALOG_RESOURCE.as_posix(),
        "sha256": versioned_catalog_sha,
    }
    raw["tools"] = list(entries)
    raw["tool_versions"] = list(version_policies)
    by_dataset: dict[str, list[str]] = {item["id"]: [] for item in raw["datasets"]}
    for entry in entries:
        for dataset_id in entry["datasets"]:
            if dataset_id not in by_dataset:
                raise ValueError(f"Tool {entry['id']} references unknown dataset {dataset_id}")
            by_dataset[dataset_id].append(entry["id"])
    for policy in version_policies:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if dataset_id not in by_dataset:
                    raise ValueError(
                        f"Tool {variant['id']} references unknown dataset {dataset_id}"
                    )
                if variant["id"] not in by_dataset[dataset_id]:
                    by_dataset[dataset_id].append(variant["id"])
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = by_dataset[dataset["id"]]
    raw["presentation_order"]["tools"] = [item["id"] for item in entries]
    return _render(raw), catalog_bytes, versioned_catalog_bytes


def generate(project_root: Path, *, check: bool = False) -> None:
    root = project_root.resolve(strict=True)
    registry_bytes, catalog_bytes, versioned_catalog_bytes = generated_bytes(root)
    expected = {
        root / REGISTRY_RESOURCE: registry_bytes,
        root / CATALOG_RESOURCE: catalog_bytes,
        root / VERSIONED_CATALOG_RESOURCE: versioned_catalog_bytes,
    }
    stale = [
        path.relative_to(root).as_posix()
        for path, payload in expected.items()
        if not path.exists() or path.read_bytes() != payload
    ]
    if check:
        if stale:
            raise SystemExit("Generated Stage 5 artifacts are stale: " + ", ".join(stale))
        return
    for path, payload in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-stage5")
        temporary.write_bytes(payload)
        temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    generate(arguments.project_root, check=arguments.check)


if __name__ == "__main__":
    main()
