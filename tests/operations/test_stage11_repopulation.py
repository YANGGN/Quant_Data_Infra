from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_data.errors import ConflictError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.json_codec import dumps_strict
from quant_data.macro.stage11_bea import (
    BEA_NIPA_T10101,
    BEA_NIPA_T10105,
    assemble_bea_nipa_history,
    parse_bea_nipa_response,
    prepare_bea_nipa_capture,
)
from quant_data.macro.stage11_eia import (
    assemble_eia_retail_capture,
    parse_eia_retail_page_response,
    parse_eia_weekly_response,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.macro.stage11_scope import load_stage11_macro_scope
from quant_data.migrations import initialize_all
from quant_data.operations.backup import backup_all, restore_all
from quant_data.operations.stage11_repopulation import (
    PrivateStage11CandidateState,
    build_stage11_repopulation_evidence,
    reconcile_stage11_macro,
)
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CODE_REVISION = "b" * 64
_ATTEMPT_ID = "stage11-repopulation-focused-v1"


def _bea_body(table_name: str, series_code: str, value: str) -> bytes:
    return json.dumps(
        {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "TableName": table_name,
                            "SeriesCode": series_code,
                            "TimePeriod": "2020Q1",
                            "DataValue": value,
                            "CL_UNIT": "Percent" if series_code == "A191RL" else "Billions of Dollars",
                            "UNIT_MULT": "0",
                        }
                    ]
                }
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _bea_cohort(*, real_value: str = "1.5"):
    return assemble_bea_nipa_history(
        (
            parse_bea_nipa_response(
                body=_bea_body(BEA_NIPA_T10101, "A191RL", real_value),
                prepared=prepare_bea_nipa_capture(BEA_NIPA_T10101),
            ),
            parse_bea_nipa_response(
                body=_bea_body(BEA_NIPA_T10105, "A191RC", "21000"),
                prepared=prepare_bea_nipa_capture(BEA_NIPA_T10105),
            ),
        )
    )


def _retail_body(*, sales: str = "100") -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 1,
                "dateFormat": "YYYY-MM",
                "frequency": "monthly",
                "data": [
                    {
                        "period": "2020-01",
                        "stateid": "US",
                        "sectorid": "ALL",
                        "sales": sales,
                        "revenue": "200",
                        "price": "2",
                        "customers": "3",
                        "sales-units": "million kilowatthours",
                        "revenue-units": "million dollars",
                        "price-units": "cents per kilowatthour",
                        "customers-units": "thousand customers",
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _retail_cohort(*, sales: str = "100"):
    return assemble_eia_retail_capture(
        (
            parse_eia_retail_page_response(
                body=_retail_body(sales=sales), prepared=prepare_eia_retail_page()
            ),
        )
    )


def _weekly_body(*, value: str = "2.5") -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 1,
                "dateFormat": "YYYY-MM-DD",
                "frequency": "weekly",
                "data": [
                    {
                        "period": "2020-01-03",
                        "value": value,
                        "series": "PET.WCESTUS1.W",
                        "units": "Dollars per Gallon",
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _weekly_capture(*, value: str = "2.5"):
    return parse_eia_weekly_response(
        body=_weekly_body(value=value), prepared=prepare_eia_weekly_capture()
    )


class Stage11RepopulationTests(unittest.TestCase):
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
        self.source = explicit_store_map(self.root / "source")
        initialize_all(self.source, self.registry, applied_at="2026-08-12T12:00:00Z")
        self.now = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
        self.importer = Stage11MacroImporter(
            self.source, self.registry, clock=lambda: self.now
        )
        self._publish_complete()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish_complete(self, *, real: str = "1.5", sales: str = "100", weekly: str = "2.5") -> None:
        receipts = (
            self.importer.publish_prepared(
                self.importer.prepare_bea_nipa_history(_bea_cohort(real_value=real))
            ),
            self.importer.publish_prepared(
                self.importer.prepare_eia_retail_history(_retail_cohort(sales=sales))
            ),
            self.importer.publish_prepared(
                self.importer.prepare_eia_weekly_history(_weekly_capture(value=weekly))
            ),
        )
        self.assertEqual(tuple(item.outcome for item in receipts), ("succeeded",) * 3)

    def _restored(self, name: str):
        backup = backup_all(self.source, self.registry, target_root=self.root / f"{name}-backup")
        return restore_all(backup, self.registry, target_root=self.root / name).restored_store_map

    @staticmethod
    def _drop_and_execute(store_map, trigger: str, sql: str, parameters=()) -> None:
        connection = sqlite3.connect(store_map.path("macro"))
        try:
            connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute(sql, parameters)
            connection.commit()
        finally:
            connection.close()

    def test_reconciles_replays_restores_and_writes_private_receipt(self) -> None:
        restored = self._restored("restored")
        before = mutation_fingerprint(self.source)
        evidence = build_stage11_repopulation_evidence(
            self.source, restored, self.registry, self.scope, replay_unchanged=True
        )
        self.assertEqual(before["sha256"], mutation_fingerprint(self.source)["sha256"])
        self.assertTrue(evidence.source_restored_equal)
        self.assertTrue(evidence.raw_reparsed_equal)
        self.assertTrue(evidence.complete_macro_coverage)
        self.assertFalse(evidence.stage3_relations_reinterpreted)
        self.assertTrue(evidence.replay_unchanged)
        self.assertEqual(
            evidence.sha256,
            hashlib.sha256(dumps_strict(evidence.material()).encode("utf-8")).hexdigest(),
        )
        state = PrivateStage11CandidateState(self.root / "private" / "candidate")
        receipt = state.publish(
            evidence,
            _CODE_REVISION,
            datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc),
            _ATTEMPT_ID,
        )
        receipt_path = state.root / "promotion-candidates" / f"{_ATTEMPT_ID}.json"
        self.assertTrue(receipt_path.is_file())
        self.assertEqual(stat.S_IMODE(state.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(receipt_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
        self.assertEqual(receipt.candidate_state, "private_candidate_only_no_operational_promotion")
        rendered = receipt_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("api_key", rendered.casefold())
        original = receipt_path.read_bytes()
        with self.assertRaises(ConflictError):
            state.publish(
                evidence,
                _CODE_REVISION,
                datetime(2026, 8, 12, 18, 1, tzinfo=timezone.utc),
                _ATTEMPT_ID,
            )
        self.assertEqual(receipt_path.read_bytes(), original)
        self.assertFalse((state.root / "current").exists())

    def test_rejects_raw_canonical_pagination_lineage_and_restore_tampering(self) -> None:
        with self.subTest("raw"):
            hostile = self._restored("raw")
            self._drop_and_execute(
                hostile,
                "stage11_bea_capture_immutable_update",
                "UPDATE stage11_bea_nipa_captures SET response_bytes=? WHERE table_name='T10101'",
                (b"{}",),
            )
            with self.assertRaises(ValidationError):
                reconcile_stage11_macro(hostile, self.registry, self.scope)
        with self.subTest("canonical"):
            hostile = self._restored("canonical")
            self._drop_and_execute(
                hostile,
                "stage11_eia_weekly_version_immutable_update",
                "UPDATE stage11_eia_weekly_observation_versions SET value_text='999'",
            )
            with self.assertRaises(ValidationError):
                reconcile_stage11_macro(hostile, self.registry, self.scope)
        with self.subTest("pagination"):
            hostile = self._restored("pagination")
            self._drop_and_execute(
                hostile,
                "stage11_eia_retail_capture_immutable_update",
                "UPDATE stage11_eia_retail_captures SET page_total=2",
            )
            with self.assertRaises(ValidationError):
                reconcile_stage11_macro(hostile, self.registry, self.scope)
        with self.subTest("lineage"):
            hostile = self._restored("lineage")
            self._drop_and_execute(
                hostile,
                "stage11_bea_version_immutable_update",
                "UPDATE stage11_bea_nipa_observation_versions SET correction_sequence=2 WHERE rowid=(SELECT rowid FROM stage11_bea_nipa_observation_versions LIMIT 1)",
            )
            with self.assertRaises(ValidationError):
                reconcile_stage11_macro(hostile, self.registry, self.scope)
        with self.subTest("restore mismatch"):
            hostile = self._restored("mismatch")
            self._drop_and_execute(
                hostile,
                "stage11_eia_weekly_current_update",
                "DELETE FROM stage11_eia_weekly_observations",
            )
            with self.assertRaises(ValidationError):
                build_stage11_repopulation_evidence(
                    self.source, hostile, self.registry, self.scope, replay_unchanged=True
                )

    def test_correction_and_disjoint_map_reconciliation(self) -> None:
        self.now += timedelta(minutes=1)
        self._publish_complete(real="1.7", sales="101", weekly="2.7")
        reconciliation = reconcile_stage11_macro(self.source, self.registry, self.scope)
        self.assertTrue(reconciliation.correction_lineage_verified)
        self.assertGreater(reconciliation.bea_version_count, reconciliation.bea_current_count)
        with self.assertRaises(ConflictError):
            build_stage11_repopulation_evidence(
                self.source, self.source, self.registry, self.scope, replay_unchanged=True
            )


if __name__ == "__main__":
    unittest.main()
