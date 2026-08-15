"""Deterministic offline Stage 11 BEA/EIA rebuild evidence.

This fixture gate uses only synthetic bytes handed directly to the capture
parsers.  It has no credential lookup, HTTP transport, default-store fallback,
or live-provider path.  The source, backup, restored, and candidate roots are
all explicit children of a caller-supplied clean work root.
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import stat
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from unittest import mock

from .errors import ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict
from .macro.stage11_bea import (
    BEA_NIPA_T10101,
    BEA_NIPA_T10105,
    assemble_bea_nipa_history,
    parse_bea_nipa_response,
    prepare_bea_nipa_capture,
)
from .macro.stage11_eia import (
    EIA_WEEKLY_SERIES_ID,
    assemble_eia_retail_capture,
    parse_eia_retail_page_response,
    parse_eia_weekly_response,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from .macro.stage11_publication import (
    BEA_NIPA_RELATIONS,
    EIA_RETAIL_RELATIONS,
    EIA_WEEKLY_RELATIONS,
    Stage11MacroImporter,
)
from .macro.stage11_scope import load_stage11_macro_scope
from .migrations import initialize_all
from .operations.backup import backup_all, restore_all
from .operations.stage11_repopulation import (
    PrivateStage11CandidateState,
    build_stage11_repopulation_evidence,
    reconcile_stage11_macro,
)
from .registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from .stage1 import explicit_store_map
from .stores import StoreMap, StoreRole, acquire_write_session, read_connection


_FIXED_MIGRATION_TIME = "2026-08-12T14:00:00Z"
_FIXED_BEA_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
_FIXED_RETAIL_TIME = datetime(2026, 8, 12, 14, 31, tzinfo=timezone.utc)
_FIXED_WEEKLY_TIME = datetime(2026, 8, 12, 14, 32, tzinfo=timezone.utc)
_FIXED_CORRECTION_TIME = datetime(2026, 8, 12, 14, 33, tzinfo=timezone.utc)
_FIXED_RECEIPT_TIME = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
_CODE_REVISION = hashlib.sha256(b"stage11-bea-eia-macro-v1").hexdigest()
_ATTEMPT_ID = "stage11-offline-bea-eia-v1"


def _sha256(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _prepare_clean_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ConflictError("Stage 11 work root is too broad")
    if root.exists():
        if not root.is_dir() or next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 11 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


@contextmanager
def _offline_guard() -> Iterator[list[str]]:
    """Make accidental sockets/subprocess calls an offline-gate failure."""

    calls: list[str] = []

    def blocked(name: str):
        def fail(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"Stage 11 offline guard blocked {name}")

        return fail

    with (
        mock.patch.object(socket, "create_connection", blocked("socket.create_connection")),
        mock.patch.object(socket.socket, "connect", blocked("socket.socket.connect")),
        mock.patch.object(http.client, "HTTPSConnection", blocked("HTTPSConnection")),
        mock.patch.object(subprocess, "Popen", blocked("subprocess.Popen")),
        mock.patch.object(subprocess, "run", blocked("subprocess.run")),
        mock.patch.object(subprocess, "call", blocked("subprocess.call")),
        mock.patch.object(subprocess, "check_call", blocked("subprocess.check_call")),
        mock.patch.object(subprocess, "check_output", blocked("subprocess.check_output")),
    ):
        yield calls


def _bea_body(table_name: str, *, correction: bool = False) -> bytes:
    series = "A191RL" if table_name == BEA_NIPA_T10101 else "A191RC"
    unit = "Percent" if series == "A191RL" else "Billions of dollars"
    first = "2.6" if series == "A191RL" else "22100.2"
    if correction and series == "A191RL":
        first = "2.7"
    rows = [
        {
            "TableName": table_name,
            "SeriesCode": series,
            "TimePeriod": "2024Q1",
            "DataValue": first,
            "CL_UNIT": unit,
            "UNIT_MULT": "0",
        },
        {
            "TableName": table_name,
            "SeriesCode": series,
            "TimePeriod": "2024Q2",
            "DataValue": "2.8" if series == "A191RL" else "22400.4",
            "CL_UNIT": unit,
            "UNIT_MULT": "0",
        },
    ]
    return dumps_strict({"BEAAPI": {"Results": {"Data": rows}}}).encode("utf-8")


def _retail_body() -> bytes:
    rows = []
    for period, base in (("2024-01", 100), ("2024-02", 110)):
        rows.append(
            {
                "period": period,
                "stateid": "US",
                "sectorid": "ALL",
                "sales": str(base),
                "sales-units": "million kWh",
                "revenue": str(base * 10),
                "revenue-units": "million dollars",
                "price": str(base / 10),
                "price-units": "cents per kWh",
                "customers": str(base * 100),
                "customers-units": "thousand customers",
            }
        )
    return dumps_strict(
        {"response": {"frequency": "monthly", "dateFormat": "YYYY-MM", "total": len(rows), "data": rows}}
    ).encode("utf-8")


def _weekly_body() -> bytes:
    rows = [
        {"period": "2024-01-05", "value": "410.2", "series": EIA_WEEKLY_SERIES_ID, "units": "thousand barrels"},
        {"period": "2024-01-12", "value": "411.1", "series": EIA_WEEKLY_SERIES_ID, "units": "thousand barrels"},
    ]
    return dumps_strict(
        {"response": {"frequency": "weekly", "dateFormat": "YYYY-MM-DD", "total": len(rows), "data": rows}}
    ).encode("utf-8")


def _bea_cohort(*, correction: bool = False):
    captures = tuple(
        parse_bea_nipa_response(
            body=_bea_body(table_name, correction=correction),
            prepared=prepare_bea_nipa_capture(table_name),
        )
        for table_name in (BEA_NIPA_T10101, BEA_NIPA_T10105)
    )
    return assemble_bea_nipa_history(captures)


def _retail_cohort():
    return assemble_eia_retail_capture(
        (parse_eia_retail_page_response(body=_retail_body(), prepared=prepare_eia_retail_page(0)),)
    )


def _weekly_capture():
    return parse_eia_weekly_response(body=_weekly_body(), prepared=prepare_eia_weekly_capture())


def _publish(importer: Stage11MacroImporter, candidates: tuple[object, ...], store_map: StoreMap) -> tuple[object, ...]:
    receipts: list[object] = []
    with acquire_write_session(store_map, (StoreRole.MACRO,)) as held_locks:
        for candidate in candidates:
            receipts.append(importer.publish_prepared(candidate, held_locks=held_locks))  # type: ignore[arg-type]
    return tuple(receipts)


def _require_outcome(receipts: tuple[object, ...], *, expected: str) -> int:
    if not receipts or any(getattr(item, "outcome", None) != expected for item in receipts):
        raise ValidationError("Stage 11 publication outcome drifted")
    written = 0
    for item in receipts:
        count = getattr(item, "written_count", None)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValidationError("Stage 11 publication count is invalid")
        written += count
    if expected == "succeeded" and written < 1:
        raise ValidationError("Stage 11 successful publication wrote nothing")
    if expected == "unchanged" and written != 0:
        raise ValidationError("Stage 11 replay was not a zero-write replay")
    return written


def _counts(store_map: StoreMap) -> dict[str, int]:
    relations = (*BEA_NIPA_RELATIONS, *EIA_RETAIL_RELATIONS, *EIA_WEEKLY_RELATIONS)
    with read_connection(store_map, StoreRole.MACRO) as connection:
        return {
            relation: int(connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0])
            for relation in relations
        }


def _candidate_summary(root: Path, receipt: object) -> dict[str, object]:
    directory = root / "promotion-candidates"
    files = tuple(sorted(directory.glob("*.json")))
    if len(files) != 1 or stat.S_IMODE(directory.stat().st_mode) != 0o700 or stat.S_IMODE(files[0].stat().st_mode) != 0o600:
        raise ValidationError("Stage 11 candidate receipt privacy drifted")
    payload = files[0].read_bytes()
    if payload != (dumps_strict(receipt.to_primitive()) + "\n").encode("utf-8"):
        raise ValidationError("Stage 11 candidate receipt bytes drifted")
    return {
        "count": 1,
        "directory_mode": "0700",
        "file_mode": "0600",
        "receipt_sha256": receipt.receipt_sha256,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
    }


def run_clean_stage11_rebuild(*, project_root: str | Path, work_root: str | Path) -> dict[str, object]:
    """Run the full accepted Stage 11 offline rehearsal from injected captures."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_root(work_root)
    registry = stage11_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    scope = load_stage11_macro_scope(project / "config" / "stage11_macro_scope.json")
    source_root = root / "source"
    source_root.mkdir(mode=0o700)
    source = explicit_store_map(source_root)
    migration_heads = initialize_all(source, registry, applied_at=_FIXED_MIGRATION_TIME)
    if migration_heads["macro"][-1] != "macro:0012_stage11_bea_eia_live_history":
        raise ValidationError("Stage 11 macro migration head is invalid")

    with _offline_guard() as blocked_calls:
        bea = _bea_cohort()
        retail = _retail_cohort()
        weekly = _weekly_capture()
        before_prepare = mutation_fingerprint(source)
        bea_importer = Stage11MacroImporter(source, registry, clock=lambda: _FIXED_BEA_TIME)
        retail_importer = Stage11MacroImporter(source, registry, clock=lambda: _FIXED_RETAIL_TIME)
        weekly_importer = Stage11MacroImporter(source, registry, clock=lambda: _FIXED_WEEKLY_TIME)
        candidates = (
            bea_importer.prepare_bea_nipa_history(bea),
            retail_importer.prepare_eia_retail_history(retail),
            weekly_importer.prepare_eia_weekly_history(weekly),
        )
        if mutation_fingerprint(source) != before_prepare:
            raise ValidationError("Stage 11 preparation mutated a store")
        published = _publish(bea_importer, candidates[:1], source) + _publish(retail_importer, candidates[1:2], source) + _publish(weekly_importer, candidates[2:], source)
        written = _require_outcome(published, expected="succeeded")
        before_replay = mutation_fingerprint(source)
        replay = _publish(bea_importer, candidates[:1], source) + _publish(retail_importer, candidates[1:2], source) + _publish(weekly_importer, candidates[2:], source)
        _require_outcome(replay, expected="unchanged")
        if mutation_fingerprint(source) != before_replay:
            raise ValidationError("Stage 11 exact replay mutated a store")
        corrected = _bea_cohort(correction=True)
        correction_importer = Stage11MacroImporter(source, registry, clock=lambda: _FIXED_CORRECTION_TIME)
        correction_receipt = correction_importer.publish_prepared(correction_importer.prepare_bea_nipa_history(corrected))
        if getattr(correction_receipt, "outcome", None) != "succeeded" or getattr(correction_receipt, "written_count", 0) < 1:
            raise ValidationError("Stage 11 correction did not append a new version")
        counts = _counts(source)
        reconciliation = reconcile_stage11_macro(source, registry, scope)
        backup = backup_all(source, registry, target_root=root / "backup")
        restored = restore_all(backup, registry, target_root=root / "restored")
        evidence = build_stage11_repopulation_evidence(source, restored.store_map, registry, scope, replay_unchanged=True)
        candidate_root = root / "private-state"
        candidate = PrivateStage11CandidateState(candidate_root).publish(evidence, _CODE_REVISION, _FIXED_RECEIPT_TIME, _ATTEMPT_ID)
        summary = _candidate_summary(candidate_root, candidate)
        if blocked_calls:
            raise ValidationError("Stage 11 offline gate attempted a live provider call")

    result: dict[str, object] = {
        "contract": "quant_data.stage11_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "registry_source_sha256": registry.source_sha256,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "providers": ["bea", "eia"],
        "synthetic_capture_counts": {"bea_requests": 2, "eia_retail_pages": 1, "eia_weekly_requests": 1},
        "publication_written_count": written,
        "semantic_replay_outcome": "unchanged",
        "correction_rehearsal": {
            "outcome": correction_receipt.outcome,
            "written_count": correction_receipt.written_count,
            "max_bea_correction_sequence": 2,
        },
        "canonical_counts": counts,
        "reconciliation_sha256": reconciliation.sha256,
        "repopulation_evidence_sha256": evidence.sha256,
        "candidate_receipt": summary,
        "migration_heads_sha256": _sha256(migration_heads),
        "macro_migration_head": migration_heads["macro"][-1],
        "backup_source_sha256": backup.source_logical_manifest_sha256,
        "backup_copy_sha256": backup.backup_logical_manifest_sha256,
        "restored_sha256": restored.restored_logical_manifest_sha256,
        "backup_restore_equal": True,
        "operational_promotion_performed": False,
        "old_store_retirement_performed": False,
        "live_provider_calls": 0,
        "credential_values_recorded": 0,
        "public_export_ids": [],
    }
    serialized = dumps_strict(result)
    for path in (project, root, source_root):
        if str(path) in serialized:
            raise ValidationError("Stage 11 evidence exposed a physical path")
    result["sha256"] = _sha256(result)
    return result


def compare_clean_stage11_rebuilds(*, project_root: str | Path, first_work_root: str | Path, second_work_root: str | Path) -> dict[str, object]:
    """Require two fresh Stage 11 runs to produce identical path-free evidence."""

    first = run_clean_stage11_rebuild(project_root=project_root, work_root=first_work_root)
    second = run_clean_stage11_rebuild(project_root=project_root, work_root=second_work_root)
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two clean Stage 11 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage11_rebuilds", "run_clean_stage11_rebuild")
