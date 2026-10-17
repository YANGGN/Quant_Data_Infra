from __future__ import annotations

import hashlib
import sqlite3
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.operations.backup import backup_all, restore_all
from quant_data.operations.stage10_repopulation import (
    PrivateStage10CandidateState,
    build_stage10_repopulation_evidence,
    reconcile_stage10_market,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry, stage10_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stage10 import run_clean_stage10_rebuild
from quant_data.market.stage10_scope import load_stage10_market_scope


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CODE_REVISION = "a" * 64
_ATTEMPT_ID = "stage10-repopulation-focused-v1"


class Stage10RepopulationTests(unittest.TestCase):
    """Exercise the Stage 10 private proof against a complete synthetic cohort."""

    def _registry_scope(self):
        registry = stage10_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        scope = load_stage10_market_scope(
            PROJECT_ROOT / "config" / "stage10_market_scope.json"
        )
        return registry, scope

    def _complete_maps(self, root: Path):
        run_clean_stage10_rebuild(project_root=PROJECT_ROOT, work_root=root / "fixture")
        return (
            explicit_store_map(root / "fixture" / "source"),
            explicit_store_map(root / "fixture" / "restored"),
        )

    @staticmethod
    def _restored_copy(root: Path, source, registry, name: str):
        backup = backup_all(source, registry, target_root=root / f"{name}-backup")
        return restore_all(
            backup,
            registry,
            target_root=root / name,
        ).restored_store_map

    @staticmethod
    def _drop_and_execute(store_map, trigger: str, sql: str, parameters=()) -> None:
        connection = sqlite3.connect(store_map.path("market"))
        try:
            connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _operator_authorized_failure(symbol: str) -> tuple[dict[str, str], ...]:
        """One intentionally body-free omission record for offline proof tests."""

        return (
            {
                "symbol": symbol,
                "reason": "operator_authorized_prior_failure",
                "provenance": "stage10_operator_authorized_seed",
            },
        )

    @staticmethod
    def _remove_symbol_price_facts(store_map, symbol: str) -> None:
        """Create a legal no-price-facts omission shape for a scoped symbol."""

        connection = sqlite3.connect(store_map.path("market"))
        try:
            row = connection.execute(
                "SELECT instrument_id FROM stage10_instruments WHERE provider_symbol=?",
                (symbol,),
            ).fetchone()
            if row is None:
                raise AssertionError(f"fixture symbol is unavailable: {symbol}")
            instrument_id = row[0]
            trigger_names = (
                "stage10_daily_prices_immutable_delete",
                "stage10_daily_price_versions_immutable_delete",
                "stage10_daily_price_captures_immutable_delete",
            )
            trigger_sql = {}
            for trigger in trigger_names:
                definition = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    (trigger,),
                ).fetchone()
                if definition is None or not isinstance(definition[0], str):
                    raise AssertionError(f"fixture trigger is unavailable: {trigger}")
                trigger_sql[trigger] = definition[0]
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "DELETE FROM stage10_daily_prices WHERE instrument_id=?",
                (instrument_id,),
            )
            connection.execute(
                "DELETE FROM stage10_daily_price_versions WHERE instrument_id=?",
                (instrument_id,),
            )
            connection.execute(
                "DELETE FROM stage10_daily_price_captures WHERE instrument_id=?",
                (instrument_id,),
            )
            for trigger in trigger_names:
                connection.execute(trigger_sql[trigger])
            connection.commit()
            connection.execute("PRAGMA foreign_keys=ON")
            self_check = tuple(connection.execute("PRAGMA foreign_key_check"))
            if self_check:
                raise AssertionError("omission fixture has a foreign-key violation")
        finally:
            connection.close()

    def test_full_repopulation_proof_rejects_tampering_and_publishes_private_receipt(self) -> None:
        registry, scope = self._registry_scope()
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            source, restored = self._complete_maps(root)
            source_before = mutation_fingerprint(source)
            restored_before = mutation_fingerprint(restored)
            evidence = build_stage10_repopulation_evidence(
                source,
                restored,
                registry,
                scope,
                replay_unchanged=True,
            )
            self.assertEqual(source_before["sha256"], mutation_fingerprint(source)["sha256"])
            self.assertEqual(restored_before["sha256"], mutation_fingerprint(restored)["sha256"])
            self.assertTrue(evidence.source_restored_equal)
            self.assertTrue(evidence.raw_reparsed_equal)
            self.assertTrue(evidence.complete_symbol_coverage)
            self.assertFalse(evidence.historical_membership_inferred)
            self.assertTrue(evidence.russell_excluded)
            self.assertEqual(
                evidence.sha256,
                hashlib.sha256(dumps_strict(evidence.material()).encode("utf-8")).hexdigest(),
            )

            state = PrivateStage10CandidateState(root / "private-candidate")
            receipt = state.publish(
                evidence,
                code_revision=_CODE_REVISION,
                now=datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc),
                attempt_id=_ATTEMPT_ID,
            )
            receipt_path = state.root / "promotion-candidates" / f"{_ATTEMPT_ID}.json"
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(stat.S_IMODE(state.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            self.assertEqual(
                receipt.candidate_state,
                "private_candidate_only_no_operational_promotion",
            )
            self.assertNotIn(str(root), receipt_path.read_text(encoding="utf-8"))
            original = receipt_path.read_bytes()
            with self.assertRaises(ConflictError):
                state.publish(
                    evidence,
                    code_revision=_CODE_REVISION,
                    now=datetime(2026, 8, 12, 18, 1, tzinfo=timezone.utc),
                    attempt_id=_ATTEMPT_ID,
                )
            self.assertEqual(receipt_path.read_bytes(), original)
            self.assertFalse((state.root / "current").exists())

            with self.subTest("raw capture tamper"):
                hostile = self._restored_copy(root, source, registry, "raw-tampered")
                self._drop_and_execute(
                    hostile,
                    "stage10_universe_captures_immutable_update",
                    "UPDATE stage10_universe_captures SET response_bytes=? WHERE universe_id='sp500_current'",
                    (b"[]",),
                )
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(hostile, registry, scope)

            with self.subTest("incomplete current symbol"):
                hostile = self._restored_copy(root, source, registry, "incomplete-symbol")
                self._drop_and_execute(
                    hostile,
                    "stage10_daily_prices_immutable_delete",
                    "DELETE FROM stage10_daily_prices WHERE rowid=(SELECT rowid FROM stage10_daily_prices LIMIT 1)",
                )
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(hostile, registry, scope)

            with self.subTest("canonical OHLCV tamper"):
                hostile = self._restored_copy(root, source, registry, "canonical-tampered")
                self._drop_and_execute(
                    hostile,
                    "stage10_daily_price_versions_immutable_update",
                    "UPDATE stage10_daily_price_versions SET close_value='999999' "
                    "WHERE rowid=(SELECT rowid FROM stage10_daily_price_versions LIMIT 1)",
                )
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(hostile, registry, scope)

            with self.subTest("source restored mismatch"):
                hostile = self._restored_copy(root, source, registry, "mismatched-restored")
                self._drop_and_execute(
                    hostile,
                    "stage10_daily_prices_immutable_delete",
                    "DELETE FROM stage10_daily_prices WHERE rowid=(SELECT rowid FROM stage10_daily_prices LIMIT 1)",
                )
                with self.assertRaises(ValidationError):
                    build_stage10_repopulation_evidence(
                        source,
                        hostile,
                        registry,
                        scope,
                        replay_unchanged=True,
                    )

            with self.subTest("overlapping maps"):
                with self.assertRaises(ConflictError):
                    build_stage10_repopulation_evidence(
                        source,
                        source,
                        registry,
                        scope,
                        replay_unchanged=True,
                    )

            with self.subTest("authorized omission is an exact roster partition"):
                omitted_source = self._restored_copy(root, source, registry, "omitted-source")
                omitted_restored = self._restored_copy(root, source, registry, "omitted-restored")
                failed_records = self._operator_authorized_failure("AAPL")
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(
                        omitted_source,
                        registry,
                        scope,
                        failed_records=failed_records,
                    )
                self._remove_symbol_price_facts(omitted_source, "AAPL")
                self._remove_symbol_price_facts(omitted_restored, "AAPL")
                reconciliation = reconcile_stage10_market(
                    omitted_source,
                    registry,
                    scope,
                    failed_records=failed_records,
                )
                self.assertEqual(reconciliation.failed_tickers, ("AAPL",))
                self.assertEqual(reconciliation.failed_symbol_count, 1)
                self.assertEqual(
                    reconciliation.successful_symbol_count,
                    reconciliation.roster_count - 1,
                )
                self.assertEqual(
                    len(reconciliation.price_coverage),
                    reconciliation.successful_symbol_count,
                )
                self.assertNotIn(
                    "AAPL",
                    {item["provider_symbol"] for item in reconciliation.price_coverage},
                )
                evidence = build_stage10_repopulation_evidence(
                    omitted_source,
                    omitted_restored,
                    registry,
                    scope,
                    replay_unchanged=True,
                    failed_records=failed_records,
                )
                self.assertTrue(evidence.complete_symbol_coverage)
                self.assertEqual(
                    evidence.source_failure_manifest_sha256,
                    reconciliation.failure_manifest_sha256,
                )
                with self.assertRaises(ValidationError):
                    replace(reconciliation, failure_manifest_sha256="0" * 64)

            with self.subTest("failure manifest rejects duplicates and unscoped symbols"):
                duplicate = self._operator_authorized_failure("AAPL") * 2
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(
                        source,
                        registry,
                        scope,
                        failed_records=duplicate,
                    )
                with self.assertRaises(ValidationError):
                    reconcile_stage10_market(
                        source,
                        registry,
                        scope,
                        failed_records=self._operator_authorized_failure("NOT-A-ROSTER-TICKER"),
                    )

            with self.subTest("source and restored omissions must match exactly"):
                mismatch_source = self._restored_copy(root, source, registry, "mismatch-source")
                mismatch_restored = self._restored_copy(root, source, registry, "mismatch-restored")
                self._remove_symbol_price_facts(mismatch_source, "AAPL")
                self._remove_symbol_price_facts(mismatch_restored, "MSFT")
                with self.assertRaises(ValidationError):
                    build_stage10_repopulation_evidence(
                        mismatch_source,
                        mismatch_restored,
                        registry,
                        scope,
                        replay_unchanged=True,
                        failed_records=self._operator_authorized_failure("AAPL"),
                    )


if __name__ == "__main__":
    unittest.main()
