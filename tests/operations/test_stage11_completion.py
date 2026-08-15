"""Focused tests for lean Stage 11 completion evidence."""

from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.errors import ValidationError
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.macro.stage11_scope import load_stage11_macro_scope
from quant_data.migrations import initialize_all
from quant_data.operations.stage11_completion import (
    PrivateStage11CompletionCandidateState,
    Stage11CompletionEvidence,
    build_stage11_completion_evidence,
)
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map
from tests.operations.test_stage11_repopulation import (
    _bea_cohort,
    _retail_cohort,
    _weekly_capture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Stage11CompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = stage11_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        self.scope = load_stage11_macro_scope(
            PROJECT_ROOT / "config" / "stage11_macro_scope.json"
        )
        self.store_map = explicit_store_map(self.root / "stores")
        initialize_all(
            self.store_map,
            self.registry,
            applied_at="2026-08-12T12:00:00Z",
        )
        self.now = datetime(2026, 8, 12, 14, tzinfo=timezone.utc)
        self.importer = Stage11MacroImporter(
            self.store_map, self.registry, clock=lambda: self.now
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish_complete(self) -> None:
        values = (
            self.importer.prepare_bea_nipa_history(_bea_cohort()),
            self.importer.prepare_eia_retail_history(_retail_cohort()),
            self.importer.prepare_eia_weekly_history(_weekly_capture()),
        )
        self.assertEqual(
            tuple(self.importer.publish_prepared(item).outcome for item in values),
            ("succeeded", "succeeded", "succeeded"),
        )

    def test_builds_targeted_evidence_and_private_candidate(self) -> None:
        self._publish_complete()
        evidence = build_stage11_completion_evidence(
            self.store_map, self.registry, self.scope
        )
        self.assertIsInstance(evidence, Stage11CompletionEvidence)
        self.assertEqual(evidence.integrity_check, "ok")
        self.assertEqual(evidence.foreign_key_anomaly_count, 0)
        self.assertEqual(
            evidence.completion_policy,
            "provider_trusted_targeted_constraints",
        )
        self.assertIn("no_provider_release_timestamp_invented", evidence.vintage_policy)
        self.assertEqual(set(evidence.domains), {"bea_nipa", "eia_retail", "eia_weekly"})
        for counts in evidence.domains.values():
            self.assertGreater(counts["capture_count"], 0)
            self.assertGreater(counts["version_count"], 0)
            self.assertGreater(counts["current_count"], 0)
            self.assertFalse(
                any(
                    value
                    for name, value in counts.items()
                    if name.endswith("anomaly_count")
                )
            )

        state = PrivateStage11CompletionCandidateState(
            self.root / "private" / "candidate"
        )
        receipt = state.publish(
            evidence,
            self.registry.source_sha256,
            datetime(2026, 8, 12, 16, tzinfo=timezone.utc),
            "stage11-lean-completion-test",
        )
        path = (
            state.root
            / "promotion-candidates"
            / "stage11-lean-completion-test.json"
        )
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(state.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(receipt.evidence_sha256, evidence.sha256)

    def test_missing_scope_is_an_anomaly_not_a_reconciliation_job(self) -> None:
        self.importer.publish_prepared(
            self.importer.prepare_bea_nipa_history(_bea_cohort())
        )
        with self.assertRaises(ValidationError):
            build_stage11_completion_evidence(
                self.store_map, self.registry, self.scope
            )

    def test_stale_current_pointer_is_rejected(self) -> None:
        self._publish_complete()
        self.now += timedelta(hours=1)
        self.assertEqual(
            self.importer.publish_prepared(
                self.importer.prepare_bea_nipa_history(
                    _bea_cohort(real_value="1.6")
                )
            ).outcome,
            "succeeded",
        )
        macro = self.store_map.path("macro")
        with sqlite3.connect(macro) as connection:
            old = connection.execute(
                "SELECT version_id FROM stage11_bea_nipa_observation_versions "
                "WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct' "
                "AND period='2020Q1' AND correction_sequence=1"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER stage11_bea_current_update")
            connection.execute(
                "UPDATE stage11_bea_nipa_observations SET current_version_id=? "
                "WHERE canonical_series_id='macro.gdp.real_qoq_saar_pct' "
                "AND period='2020Q1'",
                (old,),
            )
            connection.commit()
        with self.assertRaises(ValidationError):
            build_stage11_completion_evidence(
                self.store_map, self.registry, self.scope
            )

    def test_version_capture_lineage_mismatch_is_rejected(self) -> None:
        self._publish_complete()
        macro = self.store_map.path("macro")
        with sqlite3.connect(macro) as connection:
            connection.execute("DROP TRIGGER stage11_bea_version_immutable_update")
            connection.execute(
                "UPDATE stage11_bea_nipa_observation_versions "
                "SET artifact_id=? WHERE version_id=("
                "SELECT version_id FROM stage11_bea_nipa_observation_versions "
                "ORDER BY version_id LIMIT 1)",
                ("f" * 64,),
            )
            connection.commit()
        with self.assertRaises(ValidationError):
            build_stage11_completion_evidence(
                self.store_map, self.registry, self.scope
            )


if __name__ == "__main__":
    unittest.main()
