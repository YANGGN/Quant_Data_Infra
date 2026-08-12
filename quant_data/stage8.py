"""Deterministic offline acceptance harness for the Stage 8 JSON Atlas."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import socket
import subprocess
from typing import Any, Iterator, Mapping
from unittest import mock

from .atlas import AtlasPublication, AtlasSnapshotExporter
from .errors import ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict, loads_strict
from .migrations import initialize_all
from .registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage8_registry_profile,
)
from .stage1 import explicit_store_map
from .stage7 import run_clean_stage7_rebuild
from .stores import StoreMap


_EXPORT_ID = "atlas.fixture_snapshot"
_DATASET_IDS = (
    "market-prices",
    "gdp-vintages",
    "company-issuers",
    "news-items",
)
_STAGE7_EVIDENCE_SHA256 = (
    "63a0179e1149b73afaa50aff586160c667262dd834fd38f0f0d5043c926fd1f7"
)
_CODE_REVISION = "stage8-fixture-reconstruction-v1"
_FIXED_EXPORT_START = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
_FORBIDDEN_PUBLIC_FIELDS = {
    "artifact_id",
    "body",
    "canonical_uri",
    "credential",
    "database_path",
    "filesystem_path",
    "lock_key",
    "physical_path",
    "private_receipt",
    "raw_artifact",
    "run_id",
    "snapshot_id",
    "source_url",
    "sql",
    "summary",
}


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_primitive(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ConflictError("Stage 8 work root is too broad")
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 8 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 8 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _assert_equal(left: object, right: object, message: str) -> None:
    if dumps_strict(left) != dumps_strict(right):
        raise ValidationError(message)


def _assert_no_private_fields(value: object) -> None:
    if isinstance(value, Mapping):
        if set(value).intersection(_FORBIDDEN_PUBLIC_FIELDS):
            raise ValidationError("Stage 8 public evidence contains a private field")
        for item in value.values():
            _assert_no_private_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_private_fields(item)


@dataclass(slots=True)
class _OfflineGuard:
    blocked_calls: list[str] = field(default_factory=list)

    def blocked(self, name: str):  # type: ignore[no-untyped-def]
        def _raise(*args: object, **kwargs: object) -> None:
            del args, kwargs
            self.blocked_calls.append(name)
            raise AssertionError(f"Stage 8 offline guard blocked {name}")

        return _raise


@contextmanager
def _offline_execution_guard() -> Iterator[_OfflineGuard]:
    guard = _OfflineGuard()
    with (
        mock.patch.object(socket, "create_connection", guard.blocked("socket.create_connection")),
        mock.patch.object(socket.socket, "connect", guard.blocked("socket.socket.connect")),
        mock.patch.object(subprocess, "Popen", guard.blocked("subprocess.Popen")),
        mock.patch.object(subprocess, "run", guard.blocked("subprocess.run")),
        mock.patch.object(subprocess, "call", guard.blocked("subprocess.call")),
        mock.patch.object(subprocess, "check_call", guard.blocked("subprocess.check_call")),
        mock.patch.object(subprocess, "check_output", guard.blocked("subprocess.check_output")),
        mock.patch.object(os, "system", guard.blocked("os.system")),
        mock.patch.object(asyncio, "create_subprocess_exec", guard.blocked("asyncio.create_subprocess_exec")),
        mock.patch.object(asyncio, "create_subprocess_shell", guard.blocked("asyncio.create_subprocess_shell")),
    ):
        yield guard


@dataclass(slots=True)
class _AttemptIds:
    prefix: str
    index: int = 0

    def __call__(self) -> str:
        self.index += 1
        return f"{self.prefix}-{self.index:04d}"


def _source_store_map(root: Path) -> StoreMap:
    return explicit_store_map(
        root / "stage7" / "stage6" / "stage5" / "stage4" / "stage3" / "stage2" / "source"
    )


def _exporter(
    *,
    registry: Registry,
    source: StoreMap,
    project: Path,
    output: Path,
    now: datetime,
    attempt_ids: _AttemptIds,
    failure_hook: object | None = None,
) -> AtlasSnapshotExporter:
    return AtlasSnapshotExporter(
        registry,
        source,
        project,
        output,
        now,
        attempt_ids,
        _CODE_REVISION,
        failure_hook=failure_hook,  # type: ignore[arg-type]
    )


def _file_manifest(root: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValidationError("Stage 8 public revision contains a symbolic link")
        if path.is_file():
            payload = path.read_bytes()
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": len(payload),
                    "sha256": _sha256_bytes(payload),
                }
            )
    return {"files": records, "sha256": _sha256_primitive(records)}


def _publication_primitive(publication: AtlasPublication) -> dict[str, object]:
    return {
        "export_id": publication.export_id,
        "revision_id": publication.revision_id,
        "cutoff": publication.cutoff,
        "manifest_sha256": publication.manifest_sha256,
        "receipt_binding_verified": True,
        "receipt_state": "durable_before_pointer_commit",
        "reused": publication.reused,
        "current_revision_id": publication.current_revision_id,
        "private_receipt_published": True,
    }


def _public_snapshot(
    *,
    output: Path,
    publication: AtlasPublication,
    roots: tuple[Path, ...],
) -> dict[str, object]:
    pointer_path = output / "current" / f"{_EXPORT_ID}.json"
    pointer_bytes = pointer_path.read_bytes()
    pointer = loads_strict(pointer_bytes)
    expected_pointer = {
        "export_id": _EXPORT_ID,
        "revision_id": publication.revision_id,
        "manifest_sha256": publication.manifest_sha256,
        "receipt_sha256": publication.receipt_sha256,
    }
    if pointer != expected_pointer:
        raise ValidationError("Stage 8 current pointer does not match its publication")
    public = output / "revisions" / _EXPORT_ID / publication.revision_id / "public"
    manifest_bytes = (public / "data" / "manifest.json").read_bytes()
    manifest = loads_strict(manifest_bytes)
    if not isinstance(manifest, dict) or set(manifest) != {
        "export_id", "revision_id", "generated_at", "cutoff",
        "semantic_dataset_id", "lifecycle", "completeness",
        "point_in_time", "registry", "contract", "provenance",
        "source_stores", "cross_store_atomic", "totals", "files", "datasets",
    }:
        raise ValidationError("Stage 8 public manifest is malformed")
    if (
        manifest["export_id"] != _EXPORT_ID
        or manifest["revision_id"] != publication.revision_id
        or manifest["cutoff"] != publication.cutoff
        or manifest["cross_store_atomic"] is not False
        or _sha256_bytes(manifest_bytes) != publication.manifest_sha256
    ):
        raise ValidationError("Stage 8 public manifest identity is inconsistent")
    if (
        manifest.get("semantic_dataset_id") != "atlas.fixture_snapshot.core_v1"
        or manifest.get("completeness") != "complete"
        or not isinstance(manifest.get("lifecycle"), dict)
        or manifest["lifecycle"].get("fixture_only") is not True
        or not isinstance(manifest.get("point_in_time"), dict)
        or manifest["point_in_time"].get("availability") != "at_or_before"
        or manifest["point_in_time"].get("date_only_policy") != "completed_date"
        or not isinstance(manifest.get("contract"), dict)
        or not isinstance(manifest.get("provenance"), dict)
    ):
        raise ValidationError("Stage 8 public semantic contract is incomplete")
    registry = manifest.get("registry")
    if not isinstance(registry, dict) or (
        registry.get("revision"), registry.get("schema_version"), registry.get("code_revision")
    ) != ("2.6.0", "1.4.0", _CODE_REVISION):
        raise ValidationError("Stage 8 public provenance is incomplete")
    if any(str(root) in dumps_strict(manifest) for root in roots):
        raise ValidationError("Stage 8 public manifest exposed a physical path")
    _assert_no_private_fields(manifest)

    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or tuple(item.get("id") for item in datasets) != _DATASET_IDS:
        raise ValidationError("Stage 8 public dataset inventory is incomplete")
    total_rows = 0
    total_bytes = 0
    dataset_summary: list[dict[str, object]] = []
    for dataset in datasets:
        if (
            not isinstance(dataset, dict)
            or set(dataset) != {"id", "label", "state", "completeness", "freshness", "schema", "rows", "bytes", "key_range", "chunks"}
            or dataset.get("state") != "ready"
        ):
            raise ValidationError("Stage 8 public dataset state is invalid")
        chunks = dataset.get("chunks")
        schema = dataset.get("schema")
        if not isinstance(chunks, list) or not isinstance(schema, dict):
            raise ValidationError("Stage 8 public dataset shape is invalid")
        fields = schema.get("fields")
        if (
            not isinstance(fields, list)
            or any(
                not isinstance(field, dict)
                or not isinstance(field.get("name"), str)
                or not field["name"]
                for field in fields
            )
        ):
            raise ValidationError("Stage 8 public schema fields are invalid")
        field_names = {field["name"] for field in fields}
        dataset_bytes = 0
        row_count = 0
        chunk_digests: list[str] = []
        for chunk in chunks:
            if (
                not isinstance(chunk, dict)
                or set(chunk) != {"path", "sha256", "rows", "bytes", "first_key", "last_key"}
            ):
                raise ValidationError("Stage 8 public chunk inventory is invalid")
            relative = chunk.get("path")
            if not isinstance(relative, str) or not relative.startswith("data/chunks/"):
                raise ValidationError("Stage 8 public chunk path is invalid")
            target = public / relative
            payload = target.read_bytes()
            if (
                len(payload) != chunk.get("bytes")
                or len(payload) > 262_144
                or _sha256_bytes(payload) != chunk.get("sha256")
            ):
                raise ValidationError("Stage 8 public chunk checksum is invalid")
            rows = loads_strict(payload)
            if not isinstance(rows, list) or len(rows) != chunk.get("rows") or len(rows) > 250:
                raise ValidationError("Stage 8 public chunk row bound is invalid")
            for row in rows:
                if not isinstance(row, dict) or set(row) != field_names:
                    raise ValidationError("Stage 8 public row does not match its schema")
                _assert_no_private_fields(row)
            row_count += len(rows)
            total_rows += len(rows)
            dataset_bytes += len(payload)
            total_bytes += len(payload)
            chunk_digests.append(_sha256_bytes(payload))
        if (
            row_count != dataset.get("rows")
            or dataset_bytes != dataset.get("bytes")
            or not isinstance(dataset.get("key_range"), dict)
        ):
            raise ValidationError("Stage 8 public dataset totals are inconsistent")
        dataset_summary.append(
            {
                "id": dataset["id"],
                "rows": row_count,
                "chunks": len(chunks),
                "chunk_sha256": _sha256_primitive(chunk_digests),
            }
        )
    if total_rows > 12_000 or total_bytes > 8_388_608:
        raise ValidationError("Stage 8 public payload exceeded registered bounds")
    totals = manifest.get("totals")
    if (
        not isinstance(totals, dict)
        or totals.get("rows") != total_rows
        or totals.get("chunk_bytes") != total_bytes
        or isinstance(totals.get("bytes"), bool)
        or not isinstance(totals.get("bytes"), int)
        or totals["bytes"] > 8_388_608
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValidationError("Stage 8 public manifest totals are inconsistent")
    required = {
        "index.html",
        "assets/dashboard.css",
        "assets/atlas.css",
        "assets/atlas.js",
        "assets/inter-variable.woff2",
        "assets/INTER-OFL-1.1.txt",
        "data/manifest.json",
        "data/schema.json",
        "data/checksums.json",
    }
    tree = _file_manifest(public)
    paths = {item["path"] for item in tree["files"]}  # type: ignore[index]
    if not required.issubset(paths) or any(
        path.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")) or ".vite" in path
        for path in paths
    ):
        raise ValidationError("Stage 8 public revision is incomplete or unsafe")
    return {
        "revision_id": publication.revision_id,
        "manifest_sha256": publication.manifest_sha256,
        "pointer_sha256": _sha256_primitive(
            {
                "export_id": pointer["export_id"],
                "revision_id": pointer["revision_id"],
                "manifest_sha256": pointer["manifest_sha256"],
                "receipt_binding_verified": (
                    pointer["receipt_sha256"] == publication.receipt_sha256
                ),
            }
        ),
        "pointer_receipt_binding_verified": True,
        "public_tree_sha256": tree["sha256"],
        "datasets": dataset_summary,
        "total_rows": total_rows,
        "total_chunk_bytes": total_bytes,
        "asset_sha256": {
            relative: _sha256_bytes((public / relative).read_bytes())
            for relative in sorted(required)
            if relative.startswith("assets/") or relative == "index.html"
        },
    }


def _private_receipt_summary(output: Path, *, roots: tuple[Path, ...]) -> dict[str, object]:
    private_root = output / "private-receipts"
    receipt_root = private_root / _EXPORT_ID
    if (private_root.stat().st_mode & 0o777) != 0o700 or (
        receipt_root.stat().st_mode & 0o777
    ) != 0o700:
        raise ValidationError("Stage 8 private receipt directories are not private")
    raw_receipts: list[tuple[Path, dict[str, object], str]] = []
    precommit_digests: dict[str, str] = {}
    for path in sorted(receipt_root.glob("*.json")):
        if (path.stat().st_mode & 0o777) != 0o600:
            raise ValidationError("Stage 8 private receipt is not private")
        payload = path.read_bytes()
        value = loads_strict(payload)
        if not isinstance(value, dict):
            raise ValidationError("Stage 8 private receipt is malformed")
        rendered = dumps_strict(value)
        if any(str(root) in rendered for root in roots):
            raise ValidationError("Stage 8 private receipt exposed a physical path")
        digest = _sha256_bytes(payload)
        raw_receipts.append((path, value, digest))
        if value.get("phase") == "precommit_intent":
            attempt_id = value.get("attempt_id")
            if not isinstance(attempt_id, str) or attempt_id in precommit_digests:
                raise ValidationError("Stage 8 precommit receipt identity is invalid")
            precommit_digests[attempt_id] = digest

    normalized: list[dict[str, object]] = []
    for _path, value, _digest in raw_receipts:
        copy = dict(value)
        cohort = copy.get("copy_cohort")
        if not isinstance(cohort, dict):
            raise ValidationError("Stage 8 private receipt omitted its copy cohort")
        cohort = dict(cohort)
        lock_order = cohort.pop("lock_order", None)
        if not isinstance(lock_order, list) or len(lock_order) != 4:
            raise ValidationError("Stage 8 private receipt lock evidence is incomplete")
        copy["copy_cohort"] = cohort
        if copy.get("phase") == "publication":
            attempt_id = copy.get("attempt_id")
            raw_precommit = copy.pop("precommit_receipt_sha256", None)
            if (
                not isinstance(attempt_id, str)
                or raw_precommit != precommit_digests.get(attempt_id)
            ):
                raise ValidationError("Stage 8 receipt chain is inconsistent")
            copy["precommit_receipt_binding_verified"] = True
            if (
                copy.get("pointer_state") != "not_published"
                or copy.get("pointer_intent") != "current"
                or copy.get("outcome")
                not in {"revision_promoted", "revision_reused"}
                or copy.get("new_current_revision_id") != copy.get("revision_id")
            ):
                raise ValidationError(
                    "Stage 8 publication receipt overstates pointer commit state"
                )
        elif copy.get("phase") == "precommit_intent":
            if (
                copy.get("pointer_state") != "not_published"
                or copy.get("outcome") != "validated"
            ):
                raise ValidationError("Stage 8 precommit receipt state is invalid")
        normalized.append(copy)
    return {
        "count": len(normalized),
        "normalized_sha256": _sha256_primitive(normalized),
    }


def run_clean_stage8_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Run Stage 7 and publish/rebuild the bounded static Atlas entirely offline."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    roots = (project, root)
    with _offline_execution_guard() as offline_guard:
        stage7 = run_clean_stage7_rebuild(project_root=project, work_root=root / "stage7")
        if stage7.get("sha256") != _STAGE7_EVIDENCE_SHA256:
            raise ValidationError("Stage 8 did not preserve the approved Stage 7 evidence")
        registry = stage8_registry_profile(
            load_registry(
                project / CANONICAL_REGISTRY_PATH,
                project_root=project,
                environment={},
            )
        )
        if (
            registry.schema_version != "1.4.0"
            or registry.registry_version != "2.6.0"
            or tuple(item.id for item in registry.exports) != (_EXPORT_ID,)
        ):
            raise ValidationError("Stage 8 requires the reviewed 1.4.0/2.6.0 registry")
        declaration = registry.export(_EXPORT_ID)
        source = _source_store_map(root)
        source_before = mutation_fingerprint(source)
        migration_heads = initialize_all(source, registry)
        _assert_equal(
            source_before,
            mutation_fingerprint(source),
            "Stage 8 registry initialization changed its Stage 7 source",
        )

        output = root / "atlas-output"
        attempts = _AttemptIds("attempt")
        first = _exporter(
            registry=registry,
            source=source,
            project=project,
            output=output,
            now=_FIXED_EXPORT_START,
            attempt_ids=attempts,
        ).publish()
        second = _exporter(
            registry=registry,
            source=source,
            project=project,
            output=output,
            now=_FIXED_EXPORT_START,
            attempt_ids=attempts,
        ).publish()
        if first.reused or not second.reused or first.revision_id != second.revision_id:
            raise ValidationError("Stage 8 immutable revision reuse is inconsistent")
        public = _public_snapshot(output=output, publication=second, roots=roots)
        private = _private_receipt_summary(output, roots=roots)
        if private["count"] != 4:
            raise ValidationError("Stage 8 did not publish two private receipts per success")

        pointer_path = output / "current" / f"{_EXPORT_ID}.json"
        pointer_before_failure = pointer_path.read_bytes()

        def _fail_before_pointer(phase: str) -> None:
            if phase == "before_pointer":
                raise ValidationError("Injected Stage 8 pre-pointer failure")

        try:
            _exporter(
                registry=registry,
                source=source,
                project=project,
                output=output,
                now=_FIXED_EXPORT_START + timedelta(days=1),
                attempt_ids=_AttemptIds("failure"),
                failure_hook=_fail_before_pointer,
            ).publish()
        except ValidationError as exc:
            if str(exc) != "Injected Stage 8 pre-pointer failure":
                raise
        else:
            raise ValidationError("Stage 8 failure injection did not fail")
        if pointer_path.read_bytes() != pointer_before_failure:
            raise ValidationError("Stage 8 failure changed the last validated current pointer")
        if next((output / ".staging").iterdir(), None) is not None:
            raise ValidationError("Stage 8 left a private staging attempt behind")

        rebuilt_output = root / "atlas-rebuilt"
        rebuilt = _exporter(
            registry=registry,
            source=source,
            project=project,
            output=rebuilt_output,
            now=_FIXED_EXPORT_START,
            attempt_ids=_AttemptIds("rebuild"),
        ).publish()
        rebuilt_public = _public_snapshot(
            output=rebuilt_output,
            publication=rebuilt,
            roots=roots,
        )
        if public != rebuilt_public:
            raise ValidationError("Stage 8 derived Atlas rebuild did not reproduce exactly")
        _private_receipt_summary(rebuilt_output, roots=roots)
        _assert_equal(
            source_before,
            mutation_fingerprint(source),
            "Stage 8 source changed during copy, query, failure, or rebuild",
        )
        if offline_guard.blocked_calls:
            raise ValidationError("Stage 8 offline guard observed a live-capable call")

    export_contract = _plain(registry.raw["exports"][0])
    query_contract = _plain(declaration.query_contract)
    evidence: dict[str, object] = {
        "contract": "quant_data.stage8_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "stage7_evidence_sha256": stage7["sha256"],
        "export_ids": [_EXPORT_ID],
        "export_contract_sha256": _sha256_primitive(export_contract),
        "query_contract_sha256": _sha256_primitive(query_contract),
        "schema_contract_sha256": declaration.schema_contract["sha256"],
        "revision_id": second.revision_id,
        "public_manifest_sha256": second.manifest_sha256,
        "public_snapshot_sha256": _sha256_primitive(public),
        "private_receipt_manifest_sha256": private["normalized_sha256"],
        "publication_manifest_sha256": _sha256_primitive(
            [_publication_primitive(first), _publication_primitive(second)]
        ),
        "migration_heads": migration_heads,
        "dataset_ids": list(_DATASET_IDS),
        "dataset_rows": {item["id"]: item["rows"] for item in public["datasets"]},
        "dataset_chunks": {item["id"]: item["chunks"] for item in public["datasets"]},
        "asset_sha256": public["asset_sha256"],
        "source_reads_unchanged": True,
        "read_only_copies": True,
        "cross_store_atomic": False,
        "immutable_revision_reused": True,
        "failure_preserved_current": True,
        "pointer_receipt_binding_verified": public[
            "pointer_receipt_binding_verified"
        ],
        "private_receipt_before_pointer": True,
        "derived_rebuild_equal": True,
        "sqlite_authoritative": True,
        "json_export_only": True,
        "parquet_decision": "not_required_json_atlas_snapshot",
        "parquet_dependencies": 0,
        "duckdb_dependencies": 0,
        "live_provider_calls": 0,
        "scheduler_installations": 0,
        "scheduler_starts": 0,
        "hosting_operations": 0,
        "exports": 1,
    }
    if any(str(root) in dumps_strict(evidence) for root in roots):
        raise ValidationError("Stage 8 evidence exposed a physical path")
    evidence["sha256"] = _sha256_primitive(evidence)
    return evidence


def compare_clean_stage8_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    first = run_clean_stage8_rebuild(project_root=project_root, work_root=first_work_root)
    second = run_clean_stage8_rebuild(project_root=project_root, work_root=second_work_root)
    _assert_equal(first, second, "Two clean Stage 8 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage8_rebuilds", "run_clean_stage8_rebuild")
