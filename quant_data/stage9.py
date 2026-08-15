"""Deterministic offline gate for the bounded Stage 9 FMP repopulation."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping
from unittest import mock

from .errors import ConflictError, ValidationError
from .fingerprint import mutation_fingerprint
from .json_codec import dumps_strict
from .market.fmp_daily_prices import (
    CapturedFmpResponse,
    FmpDailyPriceImporter,
    FmpDailyPriceRequest,
)
from .migrations import initialize_all
from .operations.backup import backup_all, restore_all
from .operations.repopulation import (
    PrivatePromotionCandidateState,
    build_repopulation_evidence,
    publish_promotion_candidate,
    reconcile_fmp_spy_market,
)
from .registry import CANONICAL_REGISTRY_PATH, load_registry, stage9_registry_profile
from .stage1 import explicit_store_map
from .stage8 import run_clean_stage8_rebuild
from .stores import StoreMap, StoreRole, read_connection


_STAGE8_EVIDENCE_SHA256 = (
    "e1151f92176a6b5a9d57d18a215497baab7405187fa798537cd947be1dd80a3c"
)
_FIXED_MIGRATION_TIME = "2026-08-11T14:00:00Z"
_FIXED_CAPTURE_TIME = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)
_FIXED_CORRECTION_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)
_FIXED_RECEIPT_TIME = datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc)
_CODE_REVISION = hashlib.sha256(b"stage9-fmp-spy-202607-v1").hexdigest()
_EXPECTED_DATES = (
    "2026-07-01", "2026-07-02", "2026-07-06", "2026-07-07",
    "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-13",
    "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
    "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23",
    "2026-07-24", "2026-07-27", "2026-07-28", "2026-07-29",
    "2026-07-30", "2026-07-31",
)


def _sha256_primitive(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _prepare_clean_work_root(work_root: str | Path) -> Path:
    root = Path(work_root).expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise ConflictError("Stage 9 work root is too broad")
    if root.exists():
        if not root.is_dir():
            raise ConflictError("Stage 9 work root must be a directory")
        if next(root.iterdir(), None) is not None:
            raise ConflictError("Stage 9 clean rebuild requires an empty work root")
    else:
        root.mkdir(parents=True, exist_ok=False)
    return root


def _rows(*, correction: bool = False) -> list[dict[str, object]]:
    rows = [
        {
            "symbol": "SPY",
            "date": trade_date,
            "open": 600 + index,
            "high": 603 + index,
            "low": 599 + index,
            "close": 602 + index,
            "volume": 1_000_000 + index,
            "change": 2,
            "changePercent": 1,
            "vwap": 601 + index,
        }
        for index, trade_date in enumerate(_EXPECTED_DATES)
    ]
    if correction:
        rows[10]["close"] = 613
    return rows


def _response_bytes(*, correction: bool = False) -> bytes:
    return json.dumps(_rows(correction=correction), separators=(",", ":")).encode("utf-8")


class _OfflineTransport:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedFmpResponse:
        self.calls += 1
        if (
            kwargs.get("path") != "/stable/historical-price-eod/full"
            or kwargs.get("query")
            != {"symbol": "SPY", "from": "2026-07-01", "to": "2026-07-31"}
            or kwargs.get("timeout_seconds") != 30
            or kwargs.get("max_bytes") != 262_144
        ):
            raise ValidationError("Stage 9 offline transport request drifted")
        headers = kwargs.get("headers")
        if not isinstance(headers, Mapping) or set(headers) != {"apikey", "Accept"}:
            raise ValidationError("Stage 9 offline transport headers drifted")
        return CapturedFmpResponse(200, "application/json", self._body)


@contextmanager
def _offline_guard() -> Iterator[list[str]]:
    calls: list[str] = []

    def blocked(name: str):
        def fail(*args: object, **kwargs: object) -> None:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"Stage 9 offline guard blocked {name}")

        return fail

    with (
        mock.patch.object(socket, "create_connection", blocked("socket.create_connection")),
        mock.patch.object(socket.socket, "connect", blocked("socket.socket.connect")),
        mock.patch.object(http.client, "HTTPSConnection", blocked("HTTPSConnection")),
    ):
        yield calls


def _correction_rehearsal(rehearsal: StoreMap, registry: object) -> None:
    transport = _OfflineTransport(_response_bytes(correction=True))
    importer = FmpDailyPriceImporter(
        rehearsal,
        registry,  # type: ignore[arg-type]
        clock=lambda: _FIXED_CORRECTION_TIME,
    )
    candidate = importer.prepare(
        FmpDailyPriceRequest(), api_key="offline-test-key", transport=transport
    )
    receipt = importer.publish_prepared(candidate)
    if receipt.outcome != "succeeded" or transport.calls != 1:
        raise ValidationError("Stage 9 correction rehearsal did not publish exactly once")
    with read_connection(rehearsal, StoreRole.MARKET) as connection:
        captures = connection.execute(
            "SELECT count(*) FROM fmp_daily_price_captures"
        ).fetchone()[0]
        versions = connection.execute(
            "SELECT count(*) FROM fmp_daily_price_versions"
        ).fetchone()[0]
        maximum = connection.execute(
            "SELECT max(correction_sequence) FROM fmp_daily_price_versions"
        ).fetchone()[0]
        corrected = connection.execute(
            """
            SELECT version.close_value
            FROM fmp_daily_prices AS current
            JOIN fmp_daily_price_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.trade_date='2026-07-16'
            """
        ).fetchone()[0]
    if (captures, versions, maximum, corrected) != (2, 23, 2, "613"):
        raise ValidationError("Stage 9 correction rehearsal lineage is invalid")


def _receipt_summary(root: Path, receipt: object) -> dict[str, object]:
    directory = root / "promotion-candidates"
    files = tuple(sorted(directory.glob("*.json")))
    if len(files) != 1:
        raise ValidationError("Stage 9 did not publish exactly one candidate receipt")
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValidationError("Stage 9 candidate receipt directory is not private")
    if stat.S_IMODE(files[0].stat().st_mode) != 0o600:
        raise ValidationError("Stage 9 candidate receipt is not private")
    payload = files[0].read_bytes()
    expected = (dumps_strict(receipt.to_primitive()) + "\n").encode("utf-8")
    if payload != expected:
        raise ValidationError("Stage 9 candidate receipt bytes do not match its result")
    return {
        "count": 1,
        "receipt_sha256": receipt.receipt_sha256,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "directory_mode": "0700",
        "file_mode": "0600",
    }


def run_clean_stage9_rebuild(
    *,
    project_root: str | Path,
    work_root: str | Path,
) -> dict[str, object]:
    """Rebuild the bounded Stage 9 slice with synthetic FMP-shaped bytes only."""

    project = Path(project_root).expanduser().resolve(strict=True)
    root = _prepare_clean_work_root(work_root)
    stage8 = run_clean_stage8_rebuild(project_root=project, work_root=root / "stage8")
    if stage8.get("sha256") != _STAGE8_EVIDENCE_SHA256:
        raise ValidationError("Stage 9 did not preserve the approved Stage 8 evidence")
    registry = stage9_registry_profile(
        load_registry(
            project / CANONICAL_REGISTRY_PATH,
            project_root=project,
            environment={},
        )
    )
    if (
        registry.schema_version != "1.5.0"
        or registry.registry_version != "2.7.0"
        or len(registry.collectors) != 20
    ):
        raise ValidationError("Stage 9 requires the reviewed 1.5.0/2.7.0 registry")

    source_root = root / "source"
    source_root.mkdir(mode=0o700)
    source = explicit_store_map(source_root)
    migration_heads = initialize_all(source, registry, applied_at=_FIXED_MIGRATION_TIME)
    before_provider = mutation_fingerprint(source)
    base_transport = _OfflineTransport(_response_bytes())
    importer = FmpDailyPriceImporter(
        source,
        registry,
        clock=lambda: _FIXED_CAPTURE_TIME,
    )
    with _offline_guard() as blocked_calls:
        candidate = importer.prepare(
            FmpDailyPriceRequest(),
            api_key="offline-test-key",
            transport=base_transport,
        )
        if mutation_fingerprint(source) != before_provider:
            raise ValidationError("Stage 9 preparation mutated a store")
        publication = importer.publish_prepared(candidate)
        if publication.outcome != "succeeded" or base_transport.calls != 1:
            raise ValidationError("Stage 9 base publication did not succeed exactly once")
        source_after_publish = mutation_fingerprint(source)
        replay = importer.publish_prepared(candidate)
        if replay.outcome != "unchanged" or replay.written_count != 0:
            raise ValidationError("Stage 9 exact replay was not a total no-write")
        if mutation_fingerprint(source) != source_after_publish:
            raise ValidationError("Stage 9 exact replay changed a store")
        coverage = reconcile_fmp_spy_market(source, registry)
        backup = backup_all(source, registry, target_root=root / "backup")
        restored = restore_all(backup, registry, target_root=root / "restored")
        rehearsal = restore_all(backup, registry, target_root=root / "rehearsal")
        _correction_rehearsal(rehearsal.store_map, registry)
        evidence = build_repopulation_evidence(
            source,
            restored.store_map,
            registry,
            request_scope=FmpDailyPriceRequest().request_scope(),
            response_sha256=coverage.response_sha256,
            semantic_identity=coverage.semantic_identity,
            rehearsal_map=rehearsal.store_map,
            replay_unchanged=True,
        )
        correction = dict(evidence.correction_evidence)
        receipt_root = root / "private-state"
        receipt = publish_promotion_candidate(
            PrivatePromotionCandidateState(receipt_root),
            evidence,
            code_revision=_CODE_REVISION,
            now=_FIXED_RECEIPT_TIME,
            attempt_id="stage9-offline-fmp-spy-202607",
        )
        receipt_summary = _receipt_summary(receipt_root, receipt)
        if mutation_fingerprint(source) != source_after_publish:
            raise ValidationError("Stage 9 safety gates changed the source cohort")
        if blocked_calls:
            raise ValidationError("Stage 9 offline gate attempted a live provider call")

    result: dict[str, object] = {
        "contract": "quant_data.stage9_rebuild_evidence",
        "contract_version": "1.0.0",
        "registry_revision": registry.revision,
        "registry_schema_version": registry.schema_version,
        "registry_source_sha256": registry.source_sha256,
        "stage8_evidence_sha256": stage8["sha256"],
        "collector_id": "fmp.market.daily_price_backfill",
        "provider": "fmp",
        "symbol": "SPY",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "expected_dates": list(_EXPECTED_DATES),
        "request_count": base_transport.calls,
        "response_kind": "synthetic_fmp_shaped_offline_only",
        "publication_outcome": publication.outcome,
        "replay_outcome": replay.outcome,
        "coverage": coverage.to_primitive(),
        "repopulation_evidence_sha256": evidence.sha256,
        "candidate_receipt": receipt_summary,
        "promotion_state": receipt.promotion_state,
        "correction_rehearsal": correction,
        "migration_heads": migration_heads,
        "backup_source_sha256": backup.source_logical_manifest_sha256,
        "backup_copy_sha256": backup.backup_logical_manifest_sha256,
        "restored_sha256": restored.restored_logical_manifest_sha256,
        "source_unchanged_after_publication": True,
        "backup_restore_equal": True,
        "correction_rehearsed_on_isolated_restore": True,
        "operational_promotion_performed": False,
        "old_store_retirement_performed": False,
        "live_provider_calls": 0,
        "credential_values_recorded": 0,
        "public_export_ids": [],
    }
    serialized = dumps_strict(result)
    for path in (project, root, source_root):
        if str(path) in serialized:
            raise ValidationError("Stage 9 evidence exposed a physical path")
    if "offline-test-key" in serialized:
        raise ValidationError("Stage 9 evidence exposed a credential value")
    result["sha256"] = _sha256_primitive(result)
    return result


def compare_clean_stage9_rebuilds(
    *,
    project_root: str | Path,
    first_work_root: str | Path,
    second_work_root: str | Path,
) -> dict[str, object]:
    first = run_clean_stage9_rebuild(project_root=project_root, work_root=first_work_root)
    second = run_clean_stage9_rebuild(project_root=project_root, work_root=second_work_root)
    if dumps_strict(first) != dumps_strict(second):
        raise ValidationError("Two clean Stage 9 rebuilds produced different evidence")
    return first


__all__ = ("compare_clean_stage9_rebuilds", "run_clean_stage9_rebuild")
