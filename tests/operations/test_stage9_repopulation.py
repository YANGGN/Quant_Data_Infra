from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.fmp_daily_prices import (
    CapturedFmpResponse,
    FmpDailyPriceImporter,
    FmpDailyPriceRequest,
)
from quant_data.ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.migrations import initialize_all
from quant_data.operations.backup import backup_all, restore_all
from quant_data.operations.repopulation import (
    FMP_CANONICAL_DATASET_ID,
    FMP_COLLECTOR_ID,
    FMP_CURRENCY_SEGMENT,
    FMP_ENDPOINT_PATH,
    FMP_EVIDENCE_DATASET_ID,
    FMP_EXPECTED_DATES,
    FMP_IDENTITY_DATASET_ID,
    FMP_PRICE_VARIANT,
    PrivatePromotionCandidateState,
    build_repopulation_evidence,
    publish_promotion_candidate,
    reconcile_fmp_spy_market,
)
from quant_data.registry import load_registry, stage9_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import stable_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_CAPTURED_AT = "2026-08-11T14:30:00Z"


def _scope(*, price_variant: str = FMP_PRICE_VARIANT) -> dict[str, object]:
    return {
        "provider": "fmp",
        "endpoint_path": FMP_ENDPOINT_PATH,
        "symbol": "SPY",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "price_variant": price_variant,
        "currency_segment": FMP_CURRENCY_SEGMENT,
    }


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _seed_fmp_cohort(
    store_map,
    registry,
    *,
    dates: tuple[str, ...] = FMP_EXPECTED_DATES,
    scope: dict[str, object] | None = None,
    declared_response_sha256: str | None = None,
    bad_ohlc: bool = False,
    response_mismatch: bool = False,
):
    request_scope = _scope() if scope is None else scope
    response_rows: list[dict[str, object]] = []
    for index, trade_date in enumerate(dates, start=1):
        open_value = 100 + index
        response_rows.append(
            {
                "symbol": "SPY",
                "date": trade_date,
                "open": open_value,
                "high": open_value + 2,
                "low": open_value - 1,
                "close": open_value + 1,
                "volume": 1_000_000 + index,
                "change": 1,
                "changePercent": 1,
                "vwap": open_value,
            }
        )
    if response_mismatch:
        response_rows[0]["close"] = int(response_rows[0]["close"]) + 1
    response_bytes = dumps_strict(response_rows).encode("utf-8")
    response_sha256 = _sha256(response_bytes)
    if declared_response_sha256 is not None:
        response_sha256 = declared_response_sha256
    request_scope_sha256 = _sha256(dumps_strict(request_scope))
    semantic_identity = _sha256(
        dumps_strict(
            {
                "scope": request_scope,
                "dates": list(dates),
                "normalization_version": "fmp.daily_price.v1",
            }
        )
    )
    run_id = "stage9-fmp-run-" + semantic_identity[:20]
    artifact_id = "stage9-fmp-artifact-" + semantic_identity[:20]
    snapshot_id = "stage9-fmp-snapshot-" + semantic_identity[:20]
    capture_id = "stage9-fmp-capture-" + semantic_identity[:20]
    identity_seed_sha256 = _sha256(
        dumps_strict(
            {
                "provider": "fmp",
                "provider_symbol": "SPY",
                "asset_type": "etf",
                "currency_segment": "USD",
                "identity_version": "1.0.0",
            }
        )
    )
    instrument_id = stable_id("fmp_instrument", identity_seed_sha256)

    def writer(connection, active_run_id: str) -> WriteResult:
        connection.execute(
            """
            INSERT INTO fmp_instrument_identities (
                instrument_id, provider, provider_symbol, asset_type,
                currency_segment, identity_seed_sha256, effective_from,
                captured_at, captured_precision, run_id
            ) VALUES (?, 'fmp', 'SPY', 'etf', 'USD', ?, '2026-07-01', ?, 'datetime', ?)
            """,
            (instrument_id, identity_seed_sha256, _CAPTURED_AT, active_run_id),
        )
        connection.execute(
            """
            INSERT INTO fmp_daily_price_captures (
                capture_id, dataset_id, provider, endpoint_path, request_scope_json,
                request_scope_sha256, response_sha256, response_bytes, http_status,
                content_type, semantic_identity, completeness, artifact_id, snapshot_id,
                captured_at, captured_precision, row_count, normalization_version, run_id
            ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, 200, 'application/json', ?, 'complete',
                      ?, ?, ?, 'datetime', 22, 'fmp.daily_price.v1', ?)
            """,
            (
                capture_id,
                FMP_EVIDENCE_DATASET_ID,
                FMP_ENDPOINT_PATH,
                dumps_strict(request_scope),
                request_scope_sha256,
                response_sha256,
                response_bytes,
                semantic_identity,
                artifact_id,
                snapshot_id,
                _CAPTURED_AT,
                active_run_id,
            ),
        )
        for source_row, trade_date in enumerate(dates, start=1):
            open_value = 100 + source_row
            close_value = open_value + 1
            high_value = close_value + 1
            low_value = open_value - 1
            if bad_ohlc and source_row == 1:
                high_value = open_value
            version_id = f"stage9-fmp-version-{semantic_identity[:16]}-{source_row:02d}"
            connection.execute(
                """
                INSERT INTO fmp_daily_price_versions (
                    version_id, instrument_id, trade_date, provider, price_variant,
                    currency_segment, open_value, high_value, low_value, close_value,
                    volume, available_at, available_precision, captured_at,
                    captured_precision, correction_sequence, supersedes_version_id,
                    capture_id, artifact_id, snapshot_id, run_id, source_row
                ) VALUES (?, ?, ?, 'fmp', ?, 'USD', ?, ?, ?, ?, ?, ?, 'datetime', ?,
                          'datetime', 1, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    instrument_id,
                    trade_date,
                    request_scope["price_variant"],
                    str(open_value),
                    str(high_value),
                    str(low_value),
                    str(close_value),
                    1_000_000 + source_row,
                    _CAPTURED_AT,
                    _CAPTURED_AT,
                    capture_id,
                    artifact_id,
                    snapshot_id,
                    active_run_id,
                    source_row,
                ),
            )
            connection.execute(
                """
                INSERT INTO fmp_daily_prices (
                    instrument_id, trade_date, provider, price_variant,
                    currency_segment, current_version_id
                ) VALUES (?, ?, 'fmp', ?, 'USD', ?)
                """,
                (instrument_id, trade_date, request_scope["price_variant"], version_id),
            )
        return WriteResult(
            written_count=len(dates),
            artifacts=(
                ArtifactWrite(
                    artifact_id=artifact_id,
                    dataset_id=FMP_EVIDENCE_DATASET_ID,
                    content_sha256=response_sha256,
                    media_type="application/json",
                    byte_count=len(response_bytes),
                    source_reference="fmp/stage9-spy-202607.json",
                    request_scope=request_scope,
                    captured_at=_CAPTURED_AT,
                    captured_precision="datetime",
                    normalization_version="fmp.daily_price.v1",
                ),
            ),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=FMP_CANONICAL_DATASET_ID,
                semantic_identity=semantic_identity,
                scope=request_scope,
                completeness="complete",
                row_count=len(dates),
                captured_at=_CAPTURED_AT,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id="stage9-fmp-quality-" + semantic_identity[:20],
                    dataset_id=FMP_CANONICAL_DATASET_ID,
                    rule_id="expected_trading_dates",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={"rows": len(dates)},
                ),
            ),
        )

    receipt = IngestionCoordinator(store_map, code_version="stage9-test").execute(
        role="market",
        dataset_id=FMP_CANONICAL_DATASET_ID,
        output_dataset_ids=(
            FMP_EVIDENCE_DATASET_ID,
            FMP_IDENTITY_DATASET_ID,
            FMP_CANONICAL_DATASET_ID,
        ),
        semantic_identity=semantic_identity,
        run_id=run_id,
        command=FMP_COLLECTOR_ID,
        scope=request_scope,
        started_at=_CAPTURED_AT,
        completed_at=_CAPTURED_AT,
        fetched_count=len(dates),
        writer=writer,
    )
    return {
        "scope": request_scope,
        "response_sha256": response_sha256,
        "semantic_identity": semantic_identity,
        "receipt": receipt,
    }


class _CorrectionTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedFmpResponse:
        del kwargs
        self.calls += 1
        return CapturedFmpResponse(200, "application/json", self.body)


def _correction_body() -> bytes:
    rows: list[dict[str, object]] = []
    for index, trade_date in enumerate(FMP_EXPECTED_DATES, start=1):
        open_value = 100 + index
        rows.append(
            {
                "symbol": "SPY",
                "date": trade_date,
                "open": open_value,
                "high": open_value + 2,
                "low": open_value - 1,
                "close": open_value + 1,
                "volume": 1_000_000 + index,
                "change": 1,
                "changePercent": 1,
                "vwap": open_value,
            }
        )
    rows[10]["close"] = int(rows[10]["close"]) + 1
    return dumps_strict(rows).encode("utf-8")


def _publish_scratch_correction(store_map, registry) -> None:
    transport = _CorrectionTransport(_correction_body())
    importer = FmpDailyPriceImporter(
        store_map,
        registry,
        clock=lambda: datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc),
    )
    candidate = importer.prepare(
        FmpDailyPriceRequest(), api_key="offline-test-key", transport=transport
    )
    receipt = importer.publish_prepared(candidate)
    if receipt.outcome != "succeeded" or transport.calls != 1:
        raise AssertionError("scratch correction did not publish exactly once")


class Stage9RepopulationTests(unittest.TestCase):
    def _registry(self):
        return stage9_registry_profile(
            load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
        )

    def _source(self, root: Path, registry, **seed_kwargs):
        source = explicit_store_map(root / "source")
        initialize_all(source, registry)
        seed = _seed_fmp_cohort(source, registry, **seed_kwargs)
        return source, seed

    def _restored(self, root: Path, source, registry):
        backup = backup_all(source, registry, target_root=root / "backup")
        return restore_all(backup, registry, target_root=root / "restored").restored_store_map

    def _corrected_rehearsal(self, root: Path, source, registry):
        backup = backup_all(
            source, registry, target_root=root / "rehearsal-backup"
        )
        rehearsal = restore_all(
            backup, registry, target_root=root / "rehearsal"
        ).restored_store_map
        _publish_scratch_correction(rehearsal, registry)
        return rehearsal

    def test_reconciliation_is_exact_path_free_and_non_mutating(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            before = mutation_fingerprint(source)
            reconciliation = reconcile_fmp_spy_market(source, registry)
            after = mutation_fingerprint(source)
            self.assertEqual(before["sha256"], after["sha256"])
            self.assertEqual(reconciliation.expected_dates, FMP_EXPECTED_DATES)
            self.assertEqual(reconciliation.observed_dates, FMP_EXPECTED_DATES)
            self.assertEqual(reconciliation.row_count, 22)
            self.assertEqual(reconciliation.version_count, 22)
            self.assertEqual(reconciliation.capture_count, 1)
            self.assertEqual(reconciliation.response_sha256, seed["response_sha256"])
            self.assertEqual(reconciliation.semantic_identity, seed["semantic_identity"])
            self.assertTrue(reconciliation.capture_boundary_verified)
            self.assertTrue(reconciliation.correction_lineage_verified)
            self.assertTrue(reconciliation.captured_response_verified)
            rendered = dumps_strict(reconciliation.to_primitive())
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("response_bytes", rendered)

    def test_reconciliation_rejects_hostile_capture_and_row_states(self) -> None:
        cases = (
            (
                "captured_response_mismatch",
                {"response_mismatch": True},
            ),
            (
                "mismatched_response_digest",
                {"declared_response_sha256": "0" * 64},
            ),
            (
                "wrong_variant_scope",
                {"scope": _scope(price_variant="raw")},
            ),
            (
                "credential_scope_field",
                {"scope": {**_scope(), "api_key": "super-secret-value"}},
            ),
            (
                "headers_scope_field",
                {
                    "scope": {
                        **_scope(),
                        "headers": {"Authorization": "Bearer super-secret-value"},
                    }
                },
            ),
            (
                "url_scope_field",
                {"scope": {**_scope(), "url": "https://example.invalid/fmp"}},
            ),
            (
                "path_scope_field",
                {"scope": {**_scope(), "path": "/tmp/stage9-fmp-response.json"}},
            ),
            (
                "missing_expected_date",
                {"dates": FMP_EXPECTED_DATES[:-1]},
            ),
            ("invalid_ohlc", {"bad_ohlc": True}),
        )
        registry = self._registry()
        for name, kwargs in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory(dir="/tmp") as directory:
                with self.assertRaises((ValidationError, sqlite3.IntegrityError)):
                    source, _seed = self._source(Path(directory), registry, **kwargs)
                    reconcile_fmp_spy_market(source, registry)

    def test_repopulation_evidence_binds_restoration_without_mutating_either_cohort(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            self.assertEqual(seed["receipt"].outcome, "succeeded")
            replay = _seed_fmp_cohort(source, registry)
            self.assertEqual(replay["receipt"].outcome, "unchanged")
            restored = self._restored(root, source, registry)
            rehearsal = self._corrected_rehearsal(root, source, registry)
            source_before = mutation_fingerprint(source)
            restored_before = mutation_fingerprint(restored)
            evidence = build_repopulation_evidence(
                source,
                restored,
                registry,
                request_scope=seed["scope"],
                response_sha256=seed["response_sha256"],
                semantic_identity=seed["semantic_identity"],
                rehearsal_map=rehearsal,
                replay_unchanged=True,
            )
            self.assertEqual(source_before["sha256"], mutation_fingerprint(source)["sha256"])
            self.assertEqual(restored_before["sha256"], mutation_fingerprint(restored)["sha256"])
            self.assertTrue(evidence.source_restored_equal)
            self.assertEqual(evidence.backup_restore_outcome, "validated_restored_cohort_equal")
            self.assertEqual(evidence.configuration_env_names, ("FMP_API_KEY",))
            self.assertEqual(evidence.sha256, _sha256(dumps_strict(evidence.material())))
            rendered = dumps_strict(evidence.to_primitive())
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("super-secret-value", rendered)

    def test_scratch_proof_rejects_missing_fabricated_and_raw_mismatched_correction(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            restored = self._restored(root, source, registry)
            uncorrected_backup = backup_all(
                source, registry, target_root=root / "uncorrected-backup"
            )
            uncorrected = restore_all(
                uncorrected_backup,
                registry,
                target_root=root / "uncorrected",
            ).restored_store_map
            arguments = {
                "request_scope": seed["scope"],
                "response_sha256": seed["response_sha256"],
                "semantic_identity": seed["semantic_identity"],
                "replay_unchanged": True,
            }
            with self.assertRaises(ValidationError):
                build_repopulation_evidence(
                    source,
                    restored,
                    registry,
                    rehearsal_map=uncorrected,
                    **arguments,
                )

            corrected = self._corrected_rehearsal(root, source, registry)
            with self.assertRaises(TypeError):
                build_repopulation_evidence(
                    source,
                    restored,
                    registry,
                    rehearsal_map=corrected,
                    correction_rehearsed=True,
                    correction_evidence={"outcome": "succeeded"},
                    **arguments,
                )

            connection = sqlite3.connect(
                corrected.path("market")
            )
            try:
                connection.execute(
                    "DROP TRIGGER fmp_daily_price_versions_immutable_update"
                )
                connection.execute(
                    """
                    UPDATE fmp_daily_price_versions
                    SET close_value='999'
                    WHERE trade_date='2026-07-16' AND correction_sequence=2
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(ValidationError):
                build_repopulation_evidence(
                    source,
                    restored,
                    registry,
                    rehearsal_map=corrected,
                    **arguments,
                )

    def test_candidate_receipt_is_private_atomic_no_overwrite_and_never_promotes(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            restored = self._restored(root, source, registry)
            rehearsal = self._corrected_rehearsal(root, source, registry)
            evidence = build_repopulation_evidence(
                source,
                restored,
                registry,
                request_scope=seed["scope"],
                response_sha256=seed["response_sha256"],
                semantic_identity=seed["semantic_identity"],
                rehearsal_map=rehearsal,
                replay_unchanged=True,
            )
            source_before = mutation_fingerprint(source)
            restored_before = mutation_fingerprint(restored)
            state = PrivatePromotionCandidateState(root / "private-state")
            with mock.patch("quant_data.operations.repopulation.os.rename") as rename, mock.patch(
                "quant_data.operations.repopulation.os.replace"
            ) as replace:
                receipt = publish_promotion_candidate(
                    state,
                    evidence,
                    code_revision="abcdef1234567",
                    now=datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc),
                    attempt_id="stage9-spy-202607-candidate",
                )
            rename.assert_not_called()
            replace.assert_not_called()
            self.assertEqual(source_before["sha256"], mutation_fingerprint(source)["sha256"])
            self.assertEqual(restored_before["sha256"], mutation_fingerprint(restored)["sha256"])
            receipt_path = state.root / "promotion-candidates" / "stage9-spy-202607-candidate.json"
            self.assertEqual(loads_strict(receipt_path.read_bytes()), receipt.to_primitive())
            self.assertEqual(stat.S_IMODE(state.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            rendered = dumps_strict(receipt.to_primitive())
            self.assertNotIn(str(root), rendered)
            self.assertNotIn("super-secret-value", rendered)
            self.assertEqual(receipt.promotion_state, "candidate_only_no_operational_promotion")
            self.assertFalse((state.root / "current").exists())
            original = receipt_path.read_bytes()
            with self.assertRaises(ConflictError):
                publish_promotion_candidate(
                    state,
                    evidence,
                    code_revision="abcdef1234567",
                    now=datetime(2026, 8, 11, 15, 1, tzinfo=timezone.utc),
                    attempt_id="stage9-spy-202607-candidate",
                )
            self.assertEqual(receipt_path.read_bytes(), original)

    def test_receipt_io_failure_never_reports_candidate_success(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            restored = self._restored(root, source, registry)
            rehearsal = self._corrected_rehearsal(root, source, registry)
            evidence = build_repopulation_evidence(
                source,
                restored,
                registry,
                request_scope=seed["scope"],
                response_sha256=seed["response_sha256"],
                semantic_identity=seed["semantic_identity"],
                rehearsal_map=rehearsal,
                replay_unchanged=True,
            )
            state = PrivatePromotionCandidateState(root / "private-state")
            attempt_id = "stage9-io-failure"
            with mock.patch(
                "quant_data.operations.repopulation.os.link",
                side_effect=OSError("injected receipt link failure"),
            ):
                with self.assertRaises(OSError):
                    publish_promotion_candidate(
                        state,
                        evidence,
                        code_revision="abcdef1234567",
                        now=datetime(2026, 8, 11, 15, 2, tzinfo=timezone.utc),
                        attempt_id=attempt_id,
                    )
            self.assertFalse((state.root / "promotion-candidates" / f"{attempt_id}.json").exists())

    def test_candidate_refuses_unproven_replay_or_correction(self) -> None:
        registry = self._registry()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, seed = self._source(root, registry)
            restored = self._restored(root, source, registry)
            rehearsal = self._corrected_rehearsal(root, source, registry)
            evidence = build_repopulation_evidence(
                source,
                restored,
                registry,
                request_scope=seed["scope"],
                response_sha256=seed["response_sha256"],
                semantic_identity=seed["semantic_identity"],
                rehearsal_map=rehearsal,
                replay_unchanged=False,
            )
            with self.assertRaises(ValidationError):
                publish_promotion_candidate(
                    PrivatePromotionCandidateState(root / "private-state"),
                    evidence,
                    code_revision="abcdef1234567",
                    now=datetime(2026, 8, 11, 15, 3, tzinfo=timezone.utc),
                    attempt_id="stage9-unproven",
                )


if __name__ == "__main__":
    unittest.main()
