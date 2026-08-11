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
    build_tool_entries,
    schema_catalog,
)


REGISTRY_RESOURCE = Path("config/system_registry.json")
CATALOG_RESOURCE = Path("quant_data/generated/tool_contract_schemas_v1.json")


def _render(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def generated_bytes(project_root: Path) -> tuple[bytes, bytes]:
    registry_path = project_root / REGISTRY_RESOURCE
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    existing = {item["id"]: item for item in raw["tools"]}
    try:
        legacy = {name: existing[name] for name in LEGACY_TOOL_NAMES}
    except KeyError as exc:
        raise ValueError("Canonical registry lost a Stage 1 contract") from exc

    entries = build_tool_entries(legacy)
    catalog_payload = schema_catalog(entries)
    catalog_bytes = _render(catalog_payload)
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()

    if raw.get("schema_version") != "1.2.0" or raw.get("registry_version") != "2.4.0":
        raise ValueError("Tool generation requires the reviewed Stage 6 registry revision")
    raw["tool_schema_catalog"] = {
        "schema_id": CATALOG_ID,
        "schema_version": CATALOG_VERSION,
        "resource": CATALOG_RESOURCE.as_posix(),
        "sha256": catalog_sha,
    }
    raw["tools"] = list(entries)
    by_dataset: dict[str, list[str]] = {item["id"]: [] for item in raw["datasets"]}
    for entry in entries:
        for dataset_id in entry["datasets"]:
            if dataset_id not in by_dataset:
                raise ValueError(f"Tool {entry['id']} references unknown dataset {dataset_id}")
            by_dataset[dataset_id].append(entry["id"])
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = by_dataset[dataset["id"]]
    raw["presentation_order"]["tools"] = list(PUBLIC_TOOL_NAMES)
    return _render(raw), catalog_bytes


def generate(project_root: Path, *, check: bool = False) -> None:
    root = project_root.resolve(strict=True)
    registry_bytes, catalog_bytes = generated_bytes(root)
    expected = {
        root / REGISTRY_RESOURCE: registry_bytes,
        root / CATALOG_RESOURCE: catalog_bytes,
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
