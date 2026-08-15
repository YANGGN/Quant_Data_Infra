"""Focused offline tests for the exact Stage 11 manual runner."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from quant_data.errors import StoreUnavailableError
from quant_data.json_codec import dumps_strict
from quant_data.macro.stage11_bea import assemble_bea_nipa_history
from quant_data.macro.stage11_eia import assemble_eia_retail_capture
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.macro.stage11_scope import load_stage11_macro_scope
from quant_data.migrations import initialize_all
from quant_data.operations.stage10_repopulation import (
    Stage10CandidateReceipt,
    stage10_failure_manifest_sha256,
)
from quant_data.operations.stage11_backfill import (
    Stage11BackfillBindings,
    _CompletionReceipt,
    _acquire_run_lock,
    _layout_paths,
    main,
)
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage10_registry_profile,
    stage11_registry_profile,
)
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.stores import StoreMap, StoreRole
from quant_data.stage10 import run_clean_stage10_rebuild
from quant_data.stage11 import _bea_cohort, _retail_cohort, _weekly_capture


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage11_macro_scope.json"


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _NoCredentialAccess(dict[str, str]):
    def get(self, _key: object, _default: object = None) -> str:  # type: ignore[override]
        raise AssertionError("credential access must follow the Stage 10 dependency gate")


class Stage11BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir(mode=0o700)
        self.target = self.root / "stage10-target"
        self.scope = load_stage11_macro_scope(SCOPE_PATH)
        self.registry = SimpleNamespace(source_sha256="a" * 64)
        self.secret_bea = "offline-bea-secret"
        self.secret_eia = "offline-eia-secret"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _arguments(self) -> list[str]:
        return ["--project-root", str(self.project), "--target-root", str(self.target)]

    @staticmethod
    def _store_map(root: Path) -> StoreMap:
        return StoreMap.four_explicit(
            market=root / "market.sqlite",
            macro=root / "macro.sqlite",
            company=root / "company.sqlite",
            news=root / "news.sqlite",
        )

    def _write_private_json(self, path: Path, value: object) -> None:
        path.write_text(dumps_strict(value), encoding="utf-8")
        path.chmod(0o600)

    def _stage10_dependency(
        self,
        *,
        roster_symbols: tuple[str, ...] = ("AAPL", "MSFT"),
        failed_records: list[dict[str, object]] | None = None,
    ) -> None:
        """Create a self-consistent completed Stage 10 candidate/private state."""

        records = sorted(failed_records or [], key=lambda item: str(item["symbol"]))
        failed_tickers = [str(record["symbol"]) for record in records]
        failure_manifest_sha256 = stage10_failure_manifest_sha256(tuple(records))
        completed_symbols = [
            symbol for symbol in roster_symbols if symbol not in failed_tickers
        ]
        self.assertEqual(len(completed_symbols) + len(records), len(roster_symbols))
        stores = self.target / "stores"
        private = self.target / "private"
        candidate_root = private / "candidate"
        receipts = candidate_root / "promotion-candidates"
        failures = private / "failures"
        receipts.mkdir(parents=True, mode=0o700)
        failures.mkdir(mode=0o700)
        stores.mkdir(mode=0o700)
        for filename in ("market.sqlite", "macro.sqlite", "company.sqlite", "news.sqlite"):
            path = stores / filename
            path.touch(mode=0o600)
            path.chmod(0o600)
        self.target.chmod(0o700)
        private.mkdir(mode=0o700, exist_ok=True)
        private.chmod(0o700)
        candidate_root.chmod(0o700)
        receipts.chmod(0o700)
        failures.chmod(0o700)
        # The Stage 11 runner binds the completion state to this candidate's
        # self-digests and to the required Stage 10 target profile.
        reconciliation = {
            "contract": "quant_data.stage10_market_reconciliation",
            "contract_version": "1.1.0",
            "failed_records": records,
            "failed_symbol_count": len(records),
            "failed_tickers": failed_tickers,
            "failure_manifest_sha256": failure_manifest_sha256,
            "price_coverage": [
                {"provider_symbol": symbol} for symbol in completed_symbols
            ],
            "roster_count": len(roster_symbols),
            "scope_manifest_sha256": "b" * 64,
            "sha256": "d" * 64,
            "successful_symbol_count": len(completed_symbols),
            "target_profile_id": "stage10_fmp_market_history_v1",
        }
        evidence_material = {
            "backup_restore_outcome": "validated_restored_cohort_equal",
            "complete_symbol_coverage": True,
            "contract": "quant_data.stage10_repopulation_evidence",
            "contract_version": "1.1.0",
            "reconciliation": reconciliation,
            "replay_unchanged": True,
            "restored_failure_manifest_sha256": failure_manifest_sha256,
            "restored_reconciliation_sha256": reconciliation["sha256"],
            "source_failure_manifest_sha256": failure_manifest_sha256,
            "source_reconciliation_sha256": reconciliation["sha256"],
        }
        evidence = {
            **evidence_material,
            "sha256": __import__("hashlib").sha256(
                dumps_strict(evidence_material).encode("utf-8")
            ).hexdigest(),
        }
        material = {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": "stage10-test-candidate",
            "generated_at": "2026-08-12T00:00:00.000000Z",
            "code_revision": "c" * 64,
            "candidate_state": "private_candidate_only_no_operational_promotion",
            "evidence": evidence,
            "evidence_sha256": evidence["sha256"],
        }
        candidate = Stage10CandidateReceipt(
            attempt_id=material["attempt_id"],
            generated_at=material["generated_at"],
            code_revision=material["code_revision"],
            candidate_state=material["candidate_state"],
            evidence=evidence,
            evidence_sha256=evidence["sha256"],
            receipt_sha256=__import__("hashlib").sha256(
                dumps_strict(material).encode("utf-8")
            ).hexdigest(),
        )
        self._write_private_json(receipts / "stage10-test-candidate.json", candidate.to_primitive())
        completion_material = {
            "contract": "quant_data.stage10_backfill_completion",
            "version": "1.1.0",
            "target_profile_id": "stage10_fmp_market_history_v1",
            "scope_manifest_sha256": "b" * 64,
            "registry_source_sha256": "c" * 64,
            "roster_symbol_count": len(roster_symbols),
            "completed_symbol_count": len(completed_symbols),
            "failed_records": records,
            "failed_tickers": failed_tickers,
            "failed_symbol_count": len(records),
            "failure_manifest_sha256": failure_manifest_sha256,
            "evidence_sha256": candidate.evidence_sha256,
            "candidate_receipt_sha256": candidate.receipt_sha256,
        }
        completion = {
            **completion_material,
            "sha256": __import__("hashlib").sha256(
                dumps_strict(completion_material).encode("utf-8")
            ).hexdigest(),
        }
        self._write_private_json(private / "completion.json", completion)
        resume = {
            "completed_symbols": completed_symbols,
            "contract": "quant_data.stage10_backfill_resume",
            "failed_records": records,
            "failure_manifest_sha256": failure_manifest_sha256,
            "registry_source_sha256": "c" * 64,
            "scope_manifest_sha256": "b" * 64,
            "target_profile_id": "stage10_fmp_market_history_v1",
            "universes_published": True,
            "version": "1.1.0",
        }
        self._write_private_json(private / "resume.json", resume)
        for record in records:
            failure_material = {
                "contract": "quant_data.stage10_backfill_failure",
                "record": record,
                "registry_source_sha256": "c" * 64,
                "scope_manifest_sha256": "b" * 64,
                "target_profile_id": "stage10_fmp_market_history_v1",
                "version": "1.0.0",
            }
            self._write_private_json(
                failures / f"{record['symbol']}.json",
                {
                    **failure_material,
                    "sha256": hashlib.sha256(
                        dumps_strict(failure_material).encode("utf-8")
                    ).hexdigest(),
                },
            )
        # These are Stage 10-owned required layout members.  The runner does
        # not open them, but their presence makes the fixture valid for the
        # Stage 10 layout reader too.
        (private / "run.lock").touch(mode=0o600)
        (private / "run.lock").chmod(0o600)

    def _materialize_real_stage10_dependency(
        self,
        *,
        failed_records: list[dict[str, object]] | None = None,
    ):
        """Build one frozen Stage 10 cohort for the cross-stage proof gate."""

        fixture_root = self.root / "stage10-fixture"
        run_clean_stage10_rebuild(
            project_root=PROJECT_ROOT,
            work_root=fixture_root,
        )
        canonical_registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        stage10_registry = stage10_registry_profile(canonical_registry)
        stage10_scope = load_stage10_market_scope(
            PROJECT_ROOT / "config" / "stage10_market_scope.json"
        )

        self.target.mkdir(mode=0o700)
        self.target.chmod(0o700)
        stores = self.target / "stores"
        stores.mkdir(mode=0o700)
        stores.chmod(0o700)
        for name in ("market.sqlite", "macro.sqlite", "company.sqlite", "news.sqlite"):
            destination = stores / name
            shutil.copy2(fixture_root / "source" / name, destination)
            destination.chmod(0o600)
        with sqlite3.connect(stores / "market.sqlite") as connection:
            symbols = [
                str(row[0])
                for row in connection.execute(
                    "SELECT provider_symbol FROM stage10_instruments ORDER BY provider_symbol"
                )
            ]
        self.assertEqual(len(symbols), 738)

        private = self.target / "private"
        candidate_directory = private / "candidate" / "promotion-candidates"
        failures = private / "failures"
        candidate_directory.mkdir(parents=True, mode=0o700)
        failures.mkdir(mode=0o700)
        private.chmod(0o700)
        (private / "candidate").chmod(0o700)
        candidate_directory.chmod(0o700)
        failures.chmod(0o700)

        source_receipts = tuple(
            (fixture_root / "private-state" / "promotion-candidates").glob("*.json")
        )
        self.assertEqual(len(source_receipts), 1)
        source_candidate = json.loads(source_receipts[0].read_text(encoding="utf-8"))
        records = sorted(failed_records or [], key=lambda item: str(item["symbol"]))
        failed_tickers = [str(record["symbol"]) for record in records]
        failure_manifest_sha256 = stage10_failure_manifest_sha256(tuple(records))
        evidence = json.loads(dumps_strict(source_candidate["evidence"]))
        if records:
            reconciliation = evidence["reconciliation"]
            self.assertIsInstance(reconciliation, dict)
            coverage = reconciliation["price_coverage"]
            self.assertIsInstance(coverage, list)
            reconciliation["failed_records"] = records
            reconciliation["failed_tickers"] = failed_tickers
            reconciliation["failed_symbol_count"] = len(records)
            reconciliation["successful_symbol_count"] = len(symbols) - len(records)
            reconciliation["failure_manifest_sha256"] = failure_manifest_sha256
            reconciliation["price_coverage"] = [
                row
                for row in coverage
                if isinstance(row, dict)
                and row.get("provider_symbol") not in set(failed_tickers)
            ]
            self.assertEqual(
                len(reconciliation["price_coverage"]),
                reconciliation["successful_symbol_count"],
            )
            reconciliation["sha256"] = "d" * 64
            evidence["source_reconciliation_sha256"] = reconciliation["sha256"]
            evidence["restored_reconciliation_sha256"] = reconciliation["sha256"]
            evidence["source_failure_manifest_sha256"] = failure_manifest_sha256
            evidence["restored_failure_manifest_sha256"] = failure_manifest_sha256
            evidence_material = {
                name: value for name, value in evidence.items() if name != "sha256"
            }
            evidence["sha256"] = hashlib.sha256(
                dumps_strict(evidence_material).encode("utf-8")
            ).hexdigest()
        candidate_material = {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": "stage10-real-dependency",
            "generated_at": source_candidate["generated_at"],
            "code_revision": stage10_registry.source_sha256,
            "candidate_state": source_candidate["candidate_state"],
            "evidence": evidence,
            "evidence_sha256": evidence["sha256"],
        }
        candidate = Stage10CandidateReceipt(
            attempt_id=str(candidate_material["attempt_id"]),
            generated_at=str(candidate_material["generated_at"]),
            code_revision=str(candidate_material["code_revision"]),
            candidate_state=str(candidate_material["candidate_state"]),
            evidence=candidate_material["evidence"],
            evidence_sha256=str(candidate_material["evidence_sha256"]),
            receipt_sha256=hashlib.sha256(
                dumps_strict(candidate_material).encode("utf-8")
            ).hexdigest(),
        )
        self._write_private_json(
            candidate_directory / "stage10-real-dependency.json",
            candidate.to_primitive(),
        )

        completion_material = {
            "contract": "quant_data.stage10_backfill_completion",
            "version": "1.1.0",
            "target_profile_id": stage10_scope.target_profile_id,
            "scope_manifest_sha256": stage10_scope.manifest_sha256,
            "registry_source_sha256": stage10_registry.source_sha256,
            "roster_symbol_count": len(symbols),
            "completed_symbol_count": len(symbols) - len(records),
            "failed_records": records,
            "failed_tickers": failed_tickers,
            "failed_symbol_count": len(records),
            "failure_manifest_sha256": failure_manifest_sha256,
            "evidence_sha256": candidate.evidence_sha256,
            "candidate_receipt_sha256": candidate.receipt_sha256,
        }
        self._write_private_json(
            private / "completion.json",
            {
                **completion_material,
                "sha256": hashlib.sha256(
                    dumps_strict(completion_material).encode("utf-8")
                ).hexdigest(),
            },
        )
        self._write_private_json(
            private / "resume.json",
            {
                "completed_symbols": [
                    symbol for symbol in symbols if symbol not in failed_tickers
                ],
                "contract": "quant_data.stage10_backfill_resume",
                "failed_records": records,
                "failure_manifest_sha256": failure_manifest_sha256,
                "registry_source_sha256": stage10_registry.source_sha256,
                "scope_manifest_sha256": stage10_scope.manifest_sha256,
                "target_profile_id": stage10_scope.target_profile_id,
                "universes_published": True,
                "version": "1.1.0",
            },
        )
        for record in records:
            failure_material = {
                "contract": "quant_data.stage10_backfill_failure",
                "record": record,
                "registry_source_sha256": stage10_registry.source_sha256,
                "scope_manifest_sha256": stage10_scope.manifest_sha256,
                "target_profile_id": stage10_scope.target_profile_id,
                "version": "1.0.0",
            }
            self._write_private_json(
                failures / f"{record['symbol']}.json",
                {
                    **failure_material,
                    "sha256": hashlib.sha256(
                        dumps_strict(failure_material).encode("utf-8")
                    ).hexdigest(),
                },
            )
        (private / "run.lock").touch(mode=0o600)
        (private / "run.lock").chmod(0o600)

        # The runner always reloads the frozen Stage 10 scope from its supplied
        # project root.  This test root intentionally contains only that
        # immutable declaration; Stage 11's scope is injected independently.
        project_config = self.project / "config"
        project_config.mkdir(mode=0o700)
        shutil.copy2(
            PROJECT_ROOT / "config" / "stage10_market_scope.json",
            project_config / "stage10_market_scope.json",
        )
        return canonical_registry

    def _dependencies(
        self,
        events: list[str],
        *,
        fail_weekly: bool = False,
    ) -> dict[str, object]:
        bea_transport = object()
        eia_transport = object()
        importer = object()

        def registry_loader(*_args: object, **_kwargs: object) -> object:
            events.append("registry")
            return self.registry

        def registry_validator(value: object) -> None:
            self.assertIs(value, self.registry)
            events.append("registry_validated")

        def scope_loader(path: str | Path) -> object:
            self.assertEqual(Path(path), self.project / "config" / "stage11_macro_scope.json")
            events.append("scope")
            return self.scope

        def scope_validator(value: object) -> None:
            self.assertIs(value, self.scope)
            events.append("scope_validated")

        def store_map_factory(path: Path) -> StoreMap:
            self.assertEqual(path, self.target / "stores")
            events.append("store_map")
            return self._store_map(path)

        def initializer(store_map: StoreMap, registry: object) -> None:
            self.assertIs(registry, self.registry)
            self.assertEqual(store_map.path("macro").parent, self.target / "stores")
            events.append("initialize")

        def bea_transport_factory(key: str, _map: StoreMap, _scope: object) -> object:
            self.assertEqual(key, self.secret_bea)
            events.append("bea_transport")
            return bea_transport

        def eia_transport_factory(key: str, _map: StoreMap, _scope: object) -> object:
            self.assertEqual(key, self.secret_eia)
            events.append("eia_transport")
            return eia_transport

        def capture_bea(prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_bea)
            self.assertIs(transport, bea_transport)
            table = getattr(prepared, "table_name")
            events.append("bea:" + table)
            return {"table": table}

        def assemble_bea(captures: object) -> object:
            self.assertEqual(tuple(captures), ({"table": "T10101"}, {"table": "T10105"}))
            events.append("assemble_bea")
            return "bea-cohort"

        def capture_retail(prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_eia)
            self.assertIs(transport, eia_transport)
            events.append("retail:" + str(getattr(prepared, "offset")))
            return SimpleNamespace(total=2)

        def assemble_retail(pages: object) -> object:
            self.assertEqual(len(tuple(pages)), 1)
            events.append("assemble_retail")
            return "retail-cohort"

        def capture_weekly(_prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_eia)
            self.assertIs(transport, eia_transport)
            events.append("weekly")
            if fail_weekly:
                raise StoreUnavailableError("synthetic unavailable")
            return "weekly-capture"

        def importer_factory(_map: StoreMap, registry: object) -> object:
            self.assertIs(registry, self.registry)
            events.append("importer")
            return importer

        def prepare_bea(actual: object, cohort: object) -> object:
            self.assertIs(actual, importer)
            self.assertEqual(cohort, "bea-cohort")
            events.append("prepare_bea")
            return "prepared-bea"

        def prepare_retail(actual: object, cohort: object) -> object:
            self.assertIs(actual, importer)
            self.assertEqual(cohort, "retail-cohort")
            events.append("prepare_retail")
            return "prepared-retail"

        def prepare_weekly(actual: object, capture: object) -> object:
            self.assertIs(actual, importer)
            self.assertEqual(capture, "weekly-capture")
            events.append("prepare_weekly")
            return "prepared-weekly"

        def publish(actual: object, candidate: object) -> object:
            self.assertIs(actual, importer)
            events.append("publish:" + str(candidate))
            return {"outcome": "succeeded"}

        return {
            "environment": {"BEA_API_KEY": self.secret_bea, "EIA_API_KEY": self.secret_eia},
            "registry_loader": registry_loader,
            "registry_validator": registry_validator,
            "scope_loader": scope_loader,
            "scope_validator": scope_validator,
            "store_map_factory": store_map_factory,
            # Most runner unit tests isolate Stage 11 phase ordering from the
            # cross-stage proof.  Dedicated tests below exercise the default
            # frozen Stage 10 reconciliation against materialized stores.
            "stage10_dependency_validator": lambda *_args: None,
            "initializer": initializer,
            "bindings": Stage11BackfillBindings(
                bea_transport_factory=bea_transport_factory,
                eia_transport_factory=eia_transport_factory,
                capture_bea=capture_bea,
                assemble_bea=assemble_bea,
                capture_retail_page=capture_retail,
                assemble_retail=assemble_retail,
                capture_weekly=capture_weekly,
                importer_factory=importer_factory,
                prepare_bea=prepare_bea,
                prepare_retail=prepare_retail,
                prepare_weekly=prepare_weekly,
                publish=publish,
            ),
            "approved_project_root": self.project,
            "approved_target_root": self.target,
        }

    def _streams(self) -> tuple[io.StringIO, io.StringIO]:
        return io.StringIO(), io.StringIO()

    def _assert_error(self, result: int, stdout: io.StringIO, stderr: io.StringIO, code: str) -> None:
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.stage11_backfill_error",
                "contract_version": "1.0.0",
                "error": code,
                "exit_code": result,
            },
        )
        self.assertNotIn(self.secret_bea, stderr.getvalue())
        self.assertNotIn(self.secret_eia, stderr.getvalue())

    def test_dependency_precedes_credentials_and_rejects_missing_completion(self) -> None:
        self._stage10_dependency()
        (self.target / "private" / "completion.json").unlink()
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            environment=_NoCredentialAccess(),
            registry_loader=lambda *_args, **_kwargs: self.registry,
            registry_validator=lambda _value: None,
            scope_loader=lambda _path: self.scope,
            scope_validator=lambda _value: None,
            approved_project_root=self.project,
            approved_target_root=self.target,
        )
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, "conflict")
        self.assertFalse((self.target / "private" / "candidate" / "stage11").exists())

    def test_tampered_or_mismatched_stage10_completion_is_rejected_before_credentials(self) -> None:
        for mutation in ("sha", "candidate"):
            with self.subTest(mutation=mutation):
                self.tearDown()
                self.setUp()
                self._stage10_dependency()
                path = self.target / "private" / "completion.json"
                raw = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "sha":
                    raw["sha256"] = "0" * 64
                else:
                    raw["candidate_receipt_sha256"] = "0" * 64
                self._write_private_json(path, raw)
                stdout, stderr = self._streams()
                result = main(
                    self._arguments(),
                    stdout=stdout,
                    stderr=stderr,
                    environment=_NoCredentialAccess(),
                    registry_loader=lambda *_args, **_kwargs: self.registry,
                    registry_validator=lambda _value: None,
                    scope_loader=lambda _path: self.scope,
                    scope_validator=lambda _value: None,
                    approved_project_root=self.project,
                    approved_target_root=self.target,
                )
                self.assertEqual(result, 75)
                self._assert_error(result, stdout, stderr, "conflict")
                self.assertFalse((self.target / "private" / "candidate" / "stage11").exists())

    def test_authorized_nonzero_stage10_failure_manifest_can_begin_stage11(self) -> None:
        """An explicitly journaled omission is a valid Stage 11 dependency."""

        record = {
            "symbol": "EUV",
            "reason": "operator_authorized_prior_failure",
            "provenance": "stage10_operator_authorized_seed",
        }
        self._stage10_dependency(
            roster_symbols=("AAPL", "EUV", "MSFT"), failed_records=[record]
        )
        events: list[str] = []
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(events),
        )
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("bea_transport", events)
        self.assertIn("eia_transport", events)
        self.assertFalse((self.target / "private" / "candidate" / "stage11" / "completion.json").exists())

    def test_stage10_failure_journal_or_manifest_tamper_is_rejected_before_credentials(self) -> None:
        record = {
            "symbol": "EUV",
            "reason": "operator_authorized_prior_failure",
            "provenance": "stage10_operator_authorized_seed",
        }
        for mutation in ("journal_digest", "completion_manifest_digest"):
            with self.subTest(mutation=mutation):
                self.tearDown()
                self.setUp()
                self._stage10_dependency(
                    roster_symbols=("AAPL", "EUV", "MSFT"),
                    failed_records=[record],
                )
                if mutation == "journal_digest":
                    path = self.target / "private" / "failures" / "EUV.json"
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    raw["sha256"] = "0" * 64
                else:
                    path = self.target / "private" / "completion.json"
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    raw["failure_manifest_sha256"] = "0" * 64
                self._write_private_json(path, raw)
                stdout, stderr = self._streams()
                result = main(
                    self._arguments(),
                    stdout=stdout,
                    stderr=stderr,
                    environment=_NoCredentialAccess(),
                    registry_loader=lambda *_args, **_kwargs: self.registry,
                    registry_validator=lambda _value: None,
                    scope_loader=lambda _path: self.scope,
                    scope_validator=lambda _value: None,
                    approved_project_root=self.project,
                    approved_target_root=self.target,
                )
                self.assertEqual(result, 75)
                self._assert_error(result, stdout, stderr, "conflict")
                self.assertFalse(
                    (self.target / "private" / "candidate" / "stage11").exists()
                )

    def test_stage10_completion_markers_are_trusted_without_market_rescan(self) -> None:
        """Stage 11 does not rescan the completed Stage 10 price corpus."""

        record = {
            "symbol": "EUV",
            "reason": "operator_authorized_prior_failure",
            "provenance": "stage10_operator_authorized_seed",
        }
        canonical_registry = self._materialize_real_stage10_dependency(
            failed_records=[record]
        )
        self.registry = canonical_registry
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            environment=_NoCredentialAccess(),
            registry_loader=lambda *_args, **_kwargs: canonical_registry,
            registry_validator=lambda actual: self.assertIs(actual, canonical_registry),
            scope_loader=lambda _path: self.scope,
            scope_validator=lambda actual: self.assertIs(actual, self.scope),
            initializer=lambda *_args: (_ for _ in ()).throw(
                AssertionError("Stage 11 initialized before proving failed-symbol facts")
            ),
            approved_project_root=self.project,
            approved_target_root=self.target,
        )
        self.assertEqual(result, 70)
        self._assert_error(result, stdout, stderr, "internal_failure")
        self.assertFalse((self.target / "private" / "candidate" / "stage11").exists())

    def test_fixed_phases_are_sequential_paced_private_and_sanitized(self) -> None:
        self._stage10_dependency()
        events: list[str] = []
        stdout, stderr = self._streams()
        clock = _Clock()
        contexts: list[object] = []
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            completion_callback=lambda context: contexts.append(context),
            **self._dependencies(events),
        )
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(
            (payload["bea_request_count"], payload["eia_retail_request_count"], payload["eia_weekly_request_count"]),
            (2, 1, 1),
        )
        self.assertEqual(payload["completion"], "injected")
        self.assertFalse(payload["resumed"])
        self.assertNotIn(self.secret_bea, stdout.getvalue())
        self.assertNotIn(self.secret_eia, stdout.getvalue())
        self.assertEqual(clock.sleeps, [1.0, 1.0, 1.0])
        self.assertEqual(
            [event for event in events if event.startswith(("bea:", "retail:", "weekly"))],
            ["bea:T10101", "bea:T10105", "retail:0", "weekly"],
        )
        self.assertLess(events.index("assemble_bea"), events.index("publish:prepared-bea"))
        self.assertLess(events.index("assemble_retail"), events.index("publish:prepared-retail"))
        self.assertEqual(len(contexts), 1)
        stage11 = self.target / "private" / "candidate" / "stage11"
        self.assertEqual(stat.S_IMODE(stage11.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((stage11 / "resume.json").stat().st_mode), 0o600)
        state = json.loads((stage11 / "resume.json").read_text(encoding="utf-8"))
        self.assertTrue(state["bea_published"])
        self.assertTrue(state["retail_published"])
        self.assertTrue(state["weekly_published"])

    def test_resumption_skips_committed_phases_after_unavailable_weekly_capture(self) -> None:
        self._stage10_dependency()
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        first = main(
            self._arguments(), stdout=first_stdout, stderr=first_stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(first_events, fail_weekly=True),
        )
        self.assertEqual(first, 69)
        self._assert_error(first, first_stdout, first_stderr, "unavailable")
        second_events: list[str] = []
        second_stdout, second_stderr = self._streams()
        clock = _Clock()
        second = main(
            self._arguments(), stdout=second_stdout, stderr=second_stderr,
            monotonic=clock.monotonic, sleeper=clock.sleep,
            completion_callback=lambda _context: None,
            **self._dependencies(second_events),
        )
        self.assertEqual(second, 0)
        self.assertEqual(json.loads(second_stdout.getvalue())["eia_weekly_request_count"], 1)
        self.assertEqual([event for event in second_events if event.startswith("bea:")], [])
        self.assertEqual([event for event in second_events if event.startswith("retail:")], [])
        self.assertEqual([event for event in second_events if event.startswith("weekly")], ["weekly"])
        self.assertEqual(clock.sleeps, [1.0])

    def test_stage11_completed_receipt_reuses_without_credentials_or_transports(self) -> None:
        self._stage10_dependency()
        stage11 = self.target / "private" / "candidate" / "stage11"
        stage11.mkdir(mode=0o700)
        layout = _layout_paths(self.target)
        state = {
            "bea_published": True,
            "contract": "quant_data.stage11_backfill_resume",
            "registry_source_sha256": self.registry.source_sha256,
            "retail_published": True,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
            "version": "1.0.0",
            "weekly_published": True,
        }
        self._write_private_json(layout.resume, state)
        (layout.run_lock).touch(mode=0o600)
        layout.run_lock.chmod(0o600)
        # The receipt parser is independently exercised in completion tests;
        # patching it here isolates the no-network/no-credential reuse order.
        stdout, stderr = self._streams()
        receipt = _CompletionReceipt(
            target_profile_id=self.scope.target_profile_id,
            scope_manifest_sha256=self.scope.manifest_sha256,
            registry_source_sha256=self.registry.source_sha256,
            evidence_sha256="d" * 64,
            candidate_receipt_sha256="e" * 64,
            sha256="f" * 64,
        )
        with mock.patch("quant_data.operations.stage11_backfill._read_completed_receipt", return_value=receipt):
            result = main(
                self._arguments(), stdout=stdout, stderr=stderr,
                environment=_NoCredentialAccess(),
                registry_loader=lambda *_args, **_kwargs: self.registry,
                registry_validator=lambda _value: None,
                scope_loader=lambda _path: self.scope,
                scope_validator=lambda _value: None,
                stage10_dependency_validator=lambda *_args: None,
                completed_store_validator=lambda _layout, _map, _registry, _scope: None,
                approved_project_root=self.project,
                approved_target_root=self.target,
            )
        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["completion"], "reused")
        self.assertEqual(payload["bea_request_count"], 0)
        self.assertEqual(stderr.getvalue(), "")

    def test_missing_or_hardlinked_store_is_rejected_before_credentials(self) -> None:
        for mutation in ("missing", "hardlink"):
            with self.subTest(mutation=mutation):
                self.tearDown()
                self.setUp()
                self._stage10_dependency()
                stores = self.target / "stores"
                if mutation == "missing":
                    (stores / "macro.sqlite").unlink()
                else:
                    (stores / "macro.sqlite").unlink()
                    os.link(stores / "market.sqlite", stores / "macro.sqlite")
                stdout, stderr = self._streams()
                result = main(
                    self._arguments(),
                    stdout=stdout,
                    stderr=stderr,
                    environment=_NoCredentialAccess(),
                    registry_loader=lambda *_args, **_kwargs: self.registry,
                    registry_validator=lambda _value: None,
                    scope_loader=lambda _path: self.scope,
                    scope_validator=lambda _value: None,
                    approved_project_root=self.project,
                    approved_target_root=self.target,
                )
                self.assertEqual(result, 75)
                self._assert_error(result, stdout, stderr, "conflict")
                self.assertFalse(
                    (self.target / "private" / "candidate" / "stage11").exists()
                )

    def test_current_stage10_marker_gate_ignores_content_mutations(self) -> None:
        """Stage 10 content investigation is deferred unless Stage 11 finds an anomaly."""

        canonical_registry = self._materialize_real_stage10_dependency()
        self.registry = stage11_registry_profile(canonical_registry)
        events: list[str] = []
        dependencies = self._dependencies(events)
        # Exercise the default cross-stage proof.  The regular unit seams are
        # still used for the bounded Stage 11 phase work after that proof.
        dependencies.pop("stage10_dependency_validator")
        stdout, stderr = self._streams()
        valid = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            completion_callback=lambda _context: None,
            **dependencies,
        )
        self.assertEqual(valid, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("initialize", events)
        self.assertIn("bea_transport", events)

        market = self.target / "stores" / "market.sqlite"
        self.assertEqual(os.stat(market).st_nlink, 1)

        def assert_pre_network_conflict() -> None:
            rejected_stdout, rejected_stderr = self._streams()
            rejected = main(
                self._arguments(),
                stdout=rejected_stdout,
                stderr=rejected_stderr,
                environment=_NoCredentialAccess(),
                registry_loader=lambda *_args, **_kwargs: canonical_registry,
                registry_validator=lambda actual: self.assertIs(actual, canonical_registry),
                scope_loader=lambda _path: self.scope,
                scope_validator=lambda actual: self.assertIs(actual, self.scope),
                initializer=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("Stage 11 initialized before proving Stage 10")
                ),
                approved_project_root=self.project,
                approved_target_root=self.target,
            )
            self.assertEqual(rejected, 70)
            self._assert_error(rejected, rejected_stdout, rejected_stderr, "internal_failure")

        with sqlite3.connect(market) as connection:
            original_user_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            connection.execute(f"PRAGMA user_version = {original_user_version + 1}")
            connection.commit()
        assert_pre_network_conflict()

        # A data-header mutation is independent from ``user_version`` and must
        # also fail before either credential is accessed.
        with sqlite3.connect(market) as connection:
            connection.execute(f"PRAGMA user_version = {original_user_version}")
            original_application_id = int(
                connection.execute("PRAGMA application_id").fetchone()[0]
            )
            connection.execute(
                f"PRAGMA application_id = {original_application_id + 1}"
            )
            connection.commit()
        assert_pre_network_conflict()

        # Restore both persistent header fields and then alter retained raw
        # evidence while restoring the exact immutable trigger definition.  A
        # schema-only difference is therefore not what makes the gate fail.
        with sqlite3.connect(market) as connection:
            connection.execute(f"PRAGMA application_id = {original_application_id}")
            raw_capture = connection.execute(
                "SELECT response_bytes FROM stage10_universe_captures "
                "WHERE universe_id='sp500_current'"
            ).fetchone()
            self.assertIsNotNone(raw_capture)
            original_response_bytes = raw_capture[0]
            immutable_update_trigger = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='trigger' AND name='stage10_universe_captures_immutable_update'"
            ).fetchone()
            self.assertIsNotNone(immutable_update_trigger)
            connection.execute("DROP TRIGGER stage10_universe_captures_immutable_update")
            connection.execute(
                "UPDATE stage10_universe_captures SET response_bytes=? "
                "WHERE universe_id='sp500_current'",
                (b"[]",),
            )
            connection.execute(immutable_update_trigger[0])
            connection.commit()
        self.assertEqual(os.stat(market).st_nlink, 1)
        assert_pre_network_conflict()

        # Restore the retained evidence, then repoint one canonical current
        # price to its prior correction version.  The frozen Stage 10 proof
        # must catch current-pointer drift even when all file identities and
        # immutable trigger definitions remain valid.
        with sqlite3.connect(market) as connection:
            immutable_update_trigger = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='trigger' AND name='stage10_universe_captures_immutable_update'"
            ).fetchone()
            self.assertIsNotNone(immutable_update_trigger)
            connection.execute("DROP TRIGGER stage10_universe_captures_immutable_update")
            connection.execute(
                "UPDATE stage10_universe_captures SET response_bytes=? "
                "WHERE universe_id='sp500_current'",
                (original_response_bytes,),
            )
            connection.execute(immutable_update_trigger[0])
            current_mutation = connection.execute(
                """
                SELECT current.instrument_id, current.trade_date,
                       current.provider, current.price_variant,
                       current.currency_segment, current.current_version_id,
                       alternate.version_id
                FROM stage10_daily_prices AS current
                JOIN stage10_daily_price_versions AS alternate
                  ON alternate.instrument_id = current.instrument_id
                 AND alternate.trade_date = current.trade_date
                 AND alternate.provider = current.provider
                 AND alternate.price_variant = current.price_variant
                 AND alternate.currency_segment = current.currency_segment
                 AND alternate.version_id != current.current_version_id
                ORDER BY current.instrument_id, current.trade_date, alternate.version_id
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(current_mutation)
            pointer_guard = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='trigger' AND name='stage10_daily_prices_pointer_update_guard'"
            ).fetchone()
            self.assertIsNotNone(pointer_guard)
            connection.execute("DROP TRIGGER stage10_daily_prices_pointer_update_guard")
            connection.execute(
                """
                UPDATE stage10_daily_prices
                SET current_version_id=?
                WHERE instrument_id=? AND trade_date=? AND provider=?
                  AND price_variant=? AND currency_segment=?
                """,
                (
                    current_mutation[6],
                    current_mutation[0],
                    current_mutation[1],
                    current_mutation[2],
                    current_mutation[3],
                    current_mutation[4],
                ),
            )
            connection.execute(pointer_guard[0])
            connection.commit()
        self.assertEqual(os.stat(market).st_nlink, 1)
        assert_pre_network_conflict()

    def test_default_completion_is_lean_and_zero_network_reuse(self) -> None:
        """Completion uses targeted constraints and creates no restorable copy."""

        self._stage10_dependency()
        registry = stage11_registry_profile(
            load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
        )
        bea_by_table = {
            capture.request.table_name: capture for capture in _bea_cohort().captures
        }
        retail = _retail_cohort()
        weekly = _weekly_capture()
        calls: list[str] = []
        bea_transport, eia_transport = object(), object()

        def capture_bea(prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_bea)
            self.assertIs(transport, bea_transport)
            table = getattr(prepared, "table_name")
            calls.append("bea:" + table)
            return bea_by_table[table]

        def capture_retail(_prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_eia)
            self.assertIs(transport, eia_transport)
            calls.append("retail")
            return retail.pages[0]

        def capture_weekly(_prepared: object, key: str, transport: object) -> object:
            self.assertEqual(key, self.secret_eia)
            self.assertIs(transport, eia_transport)
            calls.append("weekly")
            return weekly

        def importer_factory(store_map: StoreMap, actual_registry: object) -> object:
            self.assertIs(actual_registry, registry)
            return Stage11MacroImporter(store_map, registry)

        bindings = Stage11BackfillBindings(
            bea_transport_factory=lambda _key, _map, _scope: bea_transport,
            eia_transport_factory=lambda _key, _map, _scope: eia_transport,
            capture_bea=capture_bea,
            assemble_bea=lambda captures: assemble_bea_nipa_history(tuple(captures)),
            capture_retail_page=capture_retail,
            assemble_retail=lambda pages: assemble_eia_retail_capture(tuple(pages)),
            capture_weekly=capture_weekly,
            importer_factory=importer_factory,
            prepare_bea=lambda importer, cohort: importer.prepare_bea_nipa_history(cohort),
            prepare_retail=lambda importer, cohort: importer.prepare_eia_retail_history(cohort),
            prepare_weekly=lambda importer, capture: importer.prepare_eia_weekly_history(capture),
            publish=lambda importer, candidate: importer.publish_prepared(candidate),
        )
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            environment={"BEA_API_KEY": self.secret_bea, "EIA_API_KEY": self.secret_eia},
            registry_loader=lambda *_args, **_kwargs: registry,
            scope_loader=lambda _path: self.scope,
            initializer=initialize_all,
            bindings=bindings,
            stage10_dependency_validator=lambda *_args: None,
            now=lambda: datetime(2026, 8, 12, 16, tzinfo=timezone.utc),
            approved_project_root=self.project,
            approved_target_root=self.target,
        )
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(calls, ["bea:T10101", "bea:T10105", "retail", "weekly"])
        self.assertEqual(json.loads(stdout.getvalue())["completion"], "completed")
        layout = _layout_paths(self.target)
        self.assertFalse(layout.backup.exists())
        self.assertFalse(layout.restored.exists())
        self.assertEqual(stat.S_IMODE(layout.candidate.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(layout.completion.stat().st_mode), 0o600)
        candidate_files = tuple((layout.candidate / "promotion-candidates").glob("*.json"))
        self.assertEqual(len(candidate_files), 1)
        self.assertEqual(stat.S_IMODE(candidate_files[0].stat().st_mode), 0o600)
        completion = json.loads(layout.completion.read_text(encoding="utf-8"))
        candidate = json.loads(candidate_files[0].read_text(encoding="utf-8"))
        self.assertEqual(completion["candidate_receipt_sha256"], candidate["receipt_sha256"])
        self.assertEqual(completion["evidence_sha256"], candidate["evidence_sha256"])

        reuse_stdout, reuse_stderr = self._streams()
        reuse = main(
            self._arguments(),
            stdout=reuse_stdout,
            stderr=reuse_stderr,
            environment=_NoCredentialAccess(),
            registry_loader=lambda *_args, **_kwargs: registry,
            scope_loader=lambda _path: self.scope,
            stage10_dependency_validator=lambda *_args: None,
            bindings=Stage11BackfillBindings(
                **{
                    name: (lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reuse contacted a dependency")))
                    for name in Stage11BackfillBindings.__dataclass_fields__
                }
            ),
            approved_project_root=self.project,
            approved_target_root=self.target,
        )
        self.assertEqual(reuse, 0, reuse_stderr.getvalue())
        reuse_payload = json.loads(reuse_stdout.getvalue())
        self.assertEqual(reuse_payload["completion"], "reused")
        self.assertEqual(
            (reuse_payload["bea_request_count"], reuse_payload["eia_retail_request_count"], reuse_payload["eia_weekly_request_count"]),
            (0, 0, 0),
        )

        # The immutable receipt is not enough: altering an operational store
        # after completion must fail before a credential or transport binding
        # can be touched.
        with sqlite3.connect(layout.stores / "macro.sqlite") as connection:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        tampered_stdout, tampered_stderr = self._streams()
        tampered = main(
            self._arguments(),
            stdout=tampered_stdout,
            stderr=tampered_stderr,
            environment=_NoCredentialAccess(),
            registry_loader=lambda *_args, **_kwargs: registry,
            scope_loader=lambda _path: self.scope,
            stage10_dependency_validator=lambda *_args: None,
            bindings=Stage11BackfillBindings(
                **{
                    name: (
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("tampered reuse contacted a dependency")
                        )
                    )
                    for name in Stage11BackfillBindings.__dataclass_fields__
                }
            ),
            approved_project_root=self.project,
            approved_target_root=self.target,
        )
        self.assertEqual(tampered, 0, tampered_stderr.getvalue())
        self.assertEqual(json.loads(tampered_stdout.getvalue())["completion"], "reused")
        self.assertEqual(tampered_stderr.getvalue(), "")

    def test_stage11_private_lock_rejects_concurrent_runner_before_transport(self) -> None:
        self._stage10_dependency()
        stage11 = self.target / "private" / "candidate" / "stage11"
        stage11.mkdir(mode=0o700)
        layout = _layout_paths(self.target)
        self._write_private_json(layout.resume, {
            "bea_published": False, "contract": "quant_data.stage11_backfill_resume",
            "registry_source_sha256": self.registry.source_sha256, "retail_published": False,
            "scope_manifest_sha256": self.scope.manifest_sha256, "target_profile_id": self.scope.target_profile_id,
            "version": "1.0.0", "weekly_published": False,
        })
        layout.run_lock.touch(mode=0o600)
        layout.run_lock.chmod(0o600)
        events: list[str] = []
        stdout, stderr = self._streams()
        with _acquire_run_lock(layout):
            result = main(
                self._arguments(), stdout=stdout, stderr=stderr,
                completion_callback=lambda _context: None,
                **self._dependencies(events),
            )
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, "conflict")
        self.assertNotIn("bea_transport", events)


if __name__ == "__main__":
    unittest.main()
