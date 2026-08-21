"""Offline adversarial integration checks for the Stage 10 window runner.

Every provider interaction in this module is an injected in-memory transport.
The temporary four-store cohort is deliberately separate from the approved
live target, and this test never reads process environment credentials.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from quant_data.errors import ConflictError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_DOWJONES_CONSTITUENT_PATH,
    FMP_STAGE10_NASDAQ_CONSTITUENT_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    parse_fmp_stage10_universe_response,
    parse_fmp_stage10_price_response,
)
from quant_data.market.stage10_history_importer import (
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_SHA256,
    Stage10HistoryImporter,
)
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.market.stage10_window_importer import Stage10HistoryWindowImporter
from quant_data.migrations import initialize_all
from quant_data.operations import stage10_backfill
from quant_data.operations import stage10_history_extension as extension
from quant_data.operations import stage10_history_extension_repopulation as repopulation
from quant_data.operations import stage10_history_extension_transition_empty_chain as transition_chain
from quant_data.operations.stage10_repopulation import (
    Stage10MarketReconciliation,
    stage10_failure_manifest_sha256,
)
from quant_data.operations.stage10_history_extension_transition_empty_chain import (
    KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
    PRIOR_402_TRANSITION_SHA256,
    TRANSITION_CONTRACT,
    build_stage10_history_extension_authorization_chain,
    build_stage10_history_extension_authorization_proof,
)
from quant_data.stage1 import explicit_store_map
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage10_registry_profile,
    stage11_registry_profile,
)
from quant_data.stores import StoreRole, read_connection


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCOPE_PATH = _PROJECT_ROOT / "config" / "stage10_market_scope.json"
_MIGRATION_TIME = "2026-08-12T12:00:00Z"
_EXECUTION_REVISION = transition_chain.stage10_history_extension_execution_revision()
_AUTHORIZATION_CHAIN = build_stage10_history_extension_authorization_chain(
    {
        "contract": TRANSITION_CONTRACT,
        "transition_sha256": PRIOR_402_TRANSITION_SHA256,
    },
    {
        "contract": TRANSITION_CONTRACT,
        "transition_sha256": KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
    },
)
_SUCCESSOR_TRANSITION_MATERIAL = {
    "authorization": transition_chain.TRANSITION_AUTHORIZATION,
    "authorization_chain": _AUTHORIZATION_CHAIN,
    "closed_request_count": transition_chain.CLOSED_REQUEST_COUNT,
    "contract": transition_chain.TRANSITION_CONTRACT,
    "live_manifest_sha256": transition_chain.LIVE_MANIFEST_SHA256,
    "new_execution_revision": _EXECUTION_REVISION,
    "old_execution_revision": transition_chain.OLD_EXECUTION_REVISION,
    "pending": {
        "from": transition_chain.PENDING_FROM,
        "intent_id": transition_chain.PENDING_INTENT_ID,
        "intent_sha256": transition_chain.PENDING_INTENT_SHA256,
        "ordinal": transition_chain.PENDING_ORDINAL,
        "symbol": transition_chain.PENDING_SYMBOL,
        "to": transition_chain.PENDING_TO,
        "window_id": transition_chain.PENDING_WINDOW_ID,
    },
    "plan_sha256": transition_chain.PLAN_SHA256,
    "prefix_ledger_sha256": transition_chain.PREFIX_LEDGER_SHA256,
    "prefix_raw_sidecar_manifest_sha256": (
        transition_chain.PREFIX_RAW_SIDECAR_MANIFEST_SHA256
    ),
    "response": {
        "content_type": transition_chain.PENDING_CONTENT_TYPE,
        "http_status": transition_chain.PENDING_HTTP_STATUS,
        "response_byte_count": transition_chain.PENDING_RESPONSE_BYTE_COUNT,
        "response_sha256": transition_chain.PENDING_RESPONSE_SHA256,
    },
    "terminal_reason": extension.TRANSITION_EMPTY_TERMINAL_REASON,
    "version": transition_chain.TRANSITION_VERSION,
}
_SUCCESSOR_TRANSITION = {
    **_SUCCESSOR_TRANSITION_MATERIAL,
    "transition_sha256": hashlib.sha256(
        dumps_strict(_SUCCESSOR_TRANSITION_MATERIAL).encode("utf-8")
    ).hexdigest(),
}
_AUTHORIZATION_PROOF = build_stage10_history_extension_authorization_proof(
    _SUCCESSOR_TRANSITION
)
_AAPL_WINDOW_BODY = dumps_strict(
    [
        {
            "symbol": "AAPL",
            "date": "1990-01-02",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 1,
            "change": 1,
            "changePercent": 100,
            "vwap": 1,
        }
    ]
).encode("utf-8")


def _sha_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _WindowTransport:
    """A no-network provider double retaining each exact dated query."""

    def __init__(self, *, aapl_body: bytes = _AAPL_WINDOW_BODY) -> None:
        self.calls: list[dict[str, object]] = []

        self._aapl_body = aapl_body
    def get(
        self,
        *,
        path: str,
        query: object,
        headers: object,
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response:
        recorded = {
            "path": path,
            "query": dict(query),
            "headers": dict(headers),
            "timeout_seconds": timeout_seconds,
            "max_bytes": max_bytes,
        }
        self.calls.append(recorded)
        if recorded["query"] == {
            "symbol": "AAPL",
            "from": "1990-01-01",
            "to": "1994-12-31",
        }:
            return CapturedFmpStage10Response(
                200,
                "application/json",
                self._aapl_body,
            )
        return CapturedFmpStage10Response(200, "application/json", b"[]")


class _PoisonEnvironment(dict[str, str]):
    """Raises if code attempts credential access before base verification."""

    def get(self, key: str, default: object = None) -> str:  # type: ignore[override]
        raise AssertionError(f"credential environment was read early: {key}")


class Stage10HistoryExtensionLiveTests(unittest.TestCase):
    """Use real temporary stores for the journal, importer, and prefix gates."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.target = self.root / "extension-target"
        self.target.mkdir(mode=0o700)
        (self.target / "private").mkdir(mode=0o700)
        (self.target / "private" / "candidate").mkdir(mode=0o700)
        for path in (
            self.target,
            self.target / "private",
            self.target / "private" / "candidate",
        ):
            os.chmod(path, 0o700)

        self.captured_at = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        self.clock = _Clock()
        current_registry = load_registry(
            _PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=_PROJECT_ROOT,
            environment={},
        )
        self.cohort_registry = stage11_registry_profile(current_registry)
        self.registry = stage10_registry_profile(self.cohort_registry)
        self.assertEqual(
            (current_registry.schema_version, current_registry.registry_version),
            ("1.8.0", "2.21.0"),
        )
        self.assertEqual(
            (self.cohort_registry.schema_version, self.cohort_registry.registry_version),
            ("1.7.0", "2.9.0"),
        )
        self.assertEqual(
            (self.registry.schema_version, self.registry.registry_version),
            ("1.6.0", "2.8.0"),
        )
        self.scope = load_stage10_market_scope(_SCOPE_PATH)
        self.store_map = explicit_store_map(self.root / "stores")
        initialize_all(
            self.store_map,
            self.cohort_registry,
            applied_at=_MIGRATION_TIME,
        )
        self._publish_exact_universe_fixture()
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self.roster = tuple(
                str(row["provider_symbol"])
                for row in connection.execute(
                    "SELECT provider_symbol FROM stage10_instruments "
                    "WHERE provider='fmp' ORDER BY provider_symbol"
                )
            )
        self.assertEqual(len(self.roster), 629)
        self.failed_symbols = self.roster[-8:]
        self.base_reconciliation = self._base_reconciliation()
        verified = extension.VerifiedStage10Base(
            target_profile_id=self.scope.target_profile_id,
            scope_manifest_sha256=self.scope.manifest_sha256,
            registry_source_sha256=self.registry.source_sha256,
            completion_sha256="c" * 64,
            reconciliation_sha256=self.base_reconciliation.sha256,
            failure_manifest_sha256=self.base_reconciliation.failure_manifest_sha256,
            roster_symbols=self.roster,
            inherited_failed_symbols=self.failed_symbols,
        )
        self.proof = extension.Stage10HistoryExtensionBaseProof(
            verified_base=verified,
            store_map=self.store_map,
            registry=self.registry,
            scope=self.scope,
            base_reconciliation=self.base_reconciliation,
            cohort_registry=self.cohort_registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _now(self) -> datetime:
        value = self.captured_at
        self.captured_at += timedelta(seconds=1)
        return value

    @staticmethod
    def _universe_capture(path: str, symbols: tuple[str, ...]):
        return parse_fmp_stage10_universe_response(
            endpoint_path=path,
            body=dumps_strict(
                [{"symbol": symbol, "name": f"Synthetic {symbol}"} for symbol in symbols]
            ).encode("utf-8"),
        )

    def _exact_scope_captures(self) -> dict[str, object]:
        sp500 = ("AAPL", "MSFT", "NVDA", "JPM") + tuple(
            f"S{number:03d}" for number in range(1, 497)
        )
        shared_nasdaq = ("AAPL",) + tuple(f"S{number:03d}" for number in range(1, 81))
        nasdaq = (
            "ALAB",
            "CRWV",
            "LITE",
            "NBIS",
            "RKLB",
            "SNDK",
            "SPCX",
            "TER",
            "WMT",
        ) + tuple(f"N{number:03d}" for number in range(1, 11)) + shared_nasdaq
        dow = ("AAPL", "MSFT", "JPM") + tuple(f"S{number:03d}" for number in range(1, 28))
        self.assertEqual((len(sp500), len(nasdaq), len(dow)), (500, 100, 30))
        self.assertEqual(len(set(sp500) | set(nasdaq) | set(dow)), 519)
        return {
            "sp500_current": self._universe_capture(
                FMP_STAGE10_SP500_CONSTITUENT_PATH, sp500
            ),
            "nasdaq100_current": self._universe_capture(
                FMP_STAGE10_NASDAQ_CONSTITUENT_PATH, nasdaq
            ),
            "dow30_current": self._universe_capture(
                FMP_STAGE10_DOWJONES_CONSTITUENT_PATH, dow
            ),
        }

    def _publish_exact_universe_fixture(self) -> None:
        importer = Stage10HistoryImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=self._now,
        )
        for prepared in importer.prepare_universe_publications(
            self._exact_scope_captures()  # type: ignore[arg-type]
        ):
            self.assertEqual(importer.publish_prepared(prepared).outcome, "succeeded")

    def _base_reconciliation(self) -> Stage10MarketReconciliation:
        failed_records = tuple(
            {
                "symbol": symbol,
                "reason": "operator_authorized_prior_failure",
                "provenance": "stage10_operator_authorized_seed",
            }
            for symbol in self.failed_symbols
        )
        successful = tuple(symbol for symbol in self.roster if symbol not in self.failed_symbols)
        values: dict[str, object] = {
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
            "registry_schema_version": self.registry.schema_version,
            "registry_version": self.registry.registry_version,
            "registry_source_sha256": self.registry.source_sha256,
            "migration_id": STAGE10_MIGRATION_ID,
            "migration_sha256": STAGE10_MIGRATION_SHA256,
            "universe_summaries": tuple(
                {"universe_id": value}
                for value in (
                    "sp500_current",
                    "nasdaq100_current",
                    "dow30_current",
                    "curated_etfs",
                    "major_indexes",
                )
            ),
            "roster_count": len(self.roster),
            "roster_sha256": _sha_json({"roster": list(self.roster)}),
            "failed_records": failed_records,
            "failed_tickers": self.failed_symbols,
            "failed_symbol_count": len(failed_records),
            "successful_symbol_count": len(successful),
            "failure_manifest_sha256": stage10_failure_manifest_sha256(failed_records),
            "price_coverage": tuple({"provider_symbol": symbol} for symbol in successful),
            "universe_capture_count": 3,
            "price_capture_count": len(successful),
            "current_price_count": len(successful),
            "immutable_version_count": len(successful),
            "historical_membership_inferred": False,
            "russell_excluded": True,
            "raw_captures_reparsed": True,
            "canonical_rows_match_raw": True,
            "correction_lineage_verified": True,
            "stores_unchanged": True,
        }
        material = {
            "contract": "quant_data.stage10_market_reconciliation",
            "contract_version": "1.1.0",
            "scope_manifest_sha256": values["scope_manifest_sha256"],
            "target_profile_id": values["target_profile_id"],
            "registry": {
                "schema_version": values["registry_schema_version"],
                "registry_version": values["registry_version"],
                "source_sha256": values["registry_source_sha256"],
            },
            "migration": {
                "id": values["migration_id"],
                "sha256": values["migration_sha256"],
            },
            "universe_summaries": list(values["universe_summaries"]),
            "roster_count": values["roster_count"],
            "roster_sha256": values["roster_sha256"],
            "failed_records": list(failed_records),
            "failed_tickers": list(self.failed_symbols),
            "failed_symbol_count": values["failed_symbol_count"],
            "successful_symbol_count": values["successful_symbol_count"],
            "failure_manifest_sha256": values["failure_manifest_sha256"],
            "price_coverage": list(values["price_coverage"]),
            "universe_capture_count": values["universe_capture_count"],
            "price_capture_count": values["price_capture_count"],
            "current_price_count": values["current_price_count"],
            "immutable_version_count": values["immutable_version_count"],
            "historical_membership_inferred": values["historical_membership_inferred"],
            "russell_excluded": values["russell_excluded"],
            "raw_captures_reparsed": values["raw_captures_reparsed"],
            "canonical_rows_match_raw": values["canonical_rows_match_raw"],
            "correction_lineage_verified": values["correction_lineage_verified"],
            "stores_unchanged": values["stores_unchanged"],
        }
        return Stage10MarketReconciliation(
            **values,
            sha256=_sha_json(material),
        )

    def _window_importer(self) -> Stage10HistoryWindowImporter:
        return Stage10HistoryWindowImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=self._now,
        )

    def _publish_base_aapl_fact(self) -> None:
        """Seed a pre-existing current fact without any provider transport."""

        importer = Stage10HistoryImporter(
            self.store_map,
            self.registry,
            self.scope,
            clock=self._now,
        )
        capture = parse_fmp_stage10_price_response(
            symbol="AAPL",
            body=_AAPL_WINDOW_BODY,
        )
        self.assertEqual(
            importer.publish_prepared(importer.prepare_price_capture(capture)).outcome,
            "succeeded",
        )

    def _new_target(self, name: str) -> Path:
        target = self.root / name
        target.mkdir(mode=0o700)
        (target / "private").mkdir(mode=0o700)
        (target / "private" / "candidate").mkdir(mode=0o700)
        for path in (target, target / "private", target / "private" / "candidate"):
            os.chmod(path, 0o700)
        return target

    def _run(
        self,
        transport: _WindowTransport,
        *,
        max_requests: int,
        target: Path | None = None,
        execution_revision: str | None = _EXECUTION_REVISION,
        require_completed_sentinel: bool = False,
        stop_after_completed_sentinel: bool = False,
    ):
        return extension.run_live_history_extension_rehearsal(
            self.target if target is None else target,
            proof=self.proof,
            api_key="offline-window-test-key",
            transport=transport,
            importer=self._window_importer(),
            max_requests=max_requests,
            now=self._now,
            execution_revision=execution_revision,
            require_completed_sentinel=require_completed_sentinel,
            stop_after_completed_sentinel=stop_after_completed_sentinel,
            monotonic=self.clock.monotonic,
            sleeper=self.clock.sleep,
        )

    def _public_bindings(
        self, transport: _WindowTransport,
    ) -> extension.Stage10HistoryExtensionBindings:
        return extension.Stage10HistoryExtensionBindings(
            base_verifier=lambda _target, _project: self.proof,
            transport_factory=lambda _key, _store_map, _scope: transport,
            importer_factory=lambda _store_map, _registry, _scope: self._window_importer(),
        )

    def _run_public(self, transport: _WindowTransport, *, mode: str):
        project = self.root / "approved-project"
        project.mkdir(exist_ok=True)
        with (
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT",
                project,
            ),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT",
                self.target,
            ),
            mock.patch.object(
                extension,
                "_extension_code_revision",
                return_value=_EXECUTION_REVISION,
            ),
        ):
            return extension.run_stage10_history_extension(
                project,
                self.target,
                environment={"FMP_API_KEY": "offline-window-test-key"},
                bindings=self._public_bindings(transport),
                now=self._now,
                mode=mode,
                monotonic=self.clock.monotonic,
                sleeper=self.clock.sleep,
            )

    def test_aapl_sentinel_is_the_only_first_request_and_leaves_partial_journal(self) -> None:
        transport = _WindowTransport()

        report = self._run(transport, max_requests=1)

        self.assertEqual(report.completion, "partial")
        self.assertEqual((report.requests_issued, report.closed_request_count), (1, 1))
        self.assertEqual(report.sentinel_status, "complete")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0]["query"],
            {"symbol": "AAPL", "from": "1990-01-01", "to": "1994-12-31"},
        )
        layout = extension._live_layout(self.target)
        for directory, suffix in (
            (layout.intents, ".json"),
            (layout.spools, ".json"),
            (layout.results, ".json"),
            (layout.raw, ".raw"),
        ):
            self.assertEqual(len(tuple(directory.glob(f"*{suffix}"))), 1)

    def test_resume_revalidates_the_published_prefix_then_issues_one_next_window(self) -> None:
        transport = _WindowTransport()
        first = self._run(transport, max_requests=1)
        self.assertEqual(first.closed_request_count, 1)

        with mock.patch.object(
            extension,
            "_validate_live_prefix",
            wraps=extension._validate_live_prefix,
        ) as prefix_gate:
            resumed = self._run(transport, max_requests=1)

        self.assertEqual(prefix_gate.call_count, 1)
        self.assertEqual((resumed.requests_issued, resumed.closed_request_count), (1, 2))
        self.assertEqual(resumed.completion, "partial")
        self.assertEqual(len(transport.calls), 2)
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        self.assertEqual(transport.calls[1]["query"], plan.intents[1].window.query(plan.intents[1].symbol))

    def test_issued_intent_without_spool_blocks_before_any_transport_call(self) -> None:
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        layout = extension._initialize_or_open_live_layout(self.target, plan, _EXECUTION_REVISION)
        extension._write_live_intent(layout, plan.intents[0], now=self._now)
        transport = _WindowTransport()

        with self.assertRaises(ConflictError):
            self._run(transport, max_requests=1)

        self.assertEqual(transport.calls, [])

    @mock.patch.object(
        extension, "_verified_live_authorization_proof", return_value=_AUTHORIZATION_PROOF
    )
    def test_empty_window_conflicting_with_current_fact_stops_before_second_transport(
        self, _authorization: object) -> None:
        self._publish_base_aapl_fact()
        transport = _WindowTransport(aapl_body=b"[]")

        report = self._run(transport, max_requests=2)

        self.assertEqual(report.completion, "sentinel_failed")
        self.assertEqual(report.terminal_failure_count, 1)
        self.assertEqual(len(transport.calls), 1)
        layout = extension._live_layout(self.target)
        self.assertEqual(len(tuple(layout.intents.glob("*.json"))), 1)
        self.assertEqual(len(tuple(layout.spools.glob("*.json"))), 1)
        self.assertEqual(len(tuple(layout.raw.glob("*.raw"))), 1)
        self.assertEqual(len(tuple(layout.results.glob("*.json"))), 1)

    def test_hard_linked_private_journal_files_reject_resume_before_transport(self) -> None:
        """Immutable journal receipt links must not escape the private root."""

        target = self._new_target("hard-link")
        first_transport = _WindowTransport()
        first = self._run(first_transport, max_requests=1, target=target)
        self.assertEqual(first.completion, "partial")
        self.assertEqual(len(first_transport.calls), 1)
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        layout = extension._live_layout(target)

        for label, accessor in (
            ("intent", extension._live_intent_path),
            ("spool", extension._spool_path),
            ("result", extension._live_result_path),
        ):
            with self.subTest(label=label):
                journal_path = accessor(layout, plan.intents[0])
                alias = self.root / f"external-{label}.alias"
                os.link(journal_path, alias)
                self.assertEqual(journal_path.stat().st_nlink, 2)

                try:
                    resumed_transport = _WindowTransport()
                    with self.assertRaises(ConflictError):
                        self._run(resumed_transport, max_requests=1, target=target)
                    self.assertEqual(resumed_transport.calls, [])
                finally:
                    alias.unlink()

    def test_candidate_completion_reuse_rechecks_sidecars_before_transport(self) -> None:
        """Exercise finalization/reuse with one isolated, monkeypatched plan.

        The real runner remains fixed at 5,032 requests.  This test patches
        only module-local cardinality checks for this process, preserving the
        real 629-symbol stores, the actual AAPL sentinel importer, canonical
        1.7/2.9 backup/restore, and all durable journal formats.
        """

        real_plan = extension.build_history_extension_plan(self.proof.verified_base)
        plan_sha256 = "f" * 64
        sentinel = extension.HistoryExtensionIntent(
            ordinal=1,
            symbol="AAPL",
            window=real_plan.intents[0].window,
            plan_sha256=plan_sha256,
            base_completion_sha256=self.proof.verified_base.completion_sha256,
            base_reconciliation_sha256=self.proof.base_reconciliation.sha256,
        )
        tiny_plan = SimpleNamespace(
            base=self.proof.verified_base,
            intents=(sentinel,),
            sha256=plan_sha256,
        )
        transport = _WindowTransport()
        with (
            mock.patch.object(extension, "HISTORY_EXTENSION_EXPECTED_REQUEST_COUNT", 1),
            mock.patch.object(repopulation, "_EXPECTED_LEDGER_COUNT", 1),
            mock.patch.object(extension, "build_history_extension_plan", return_value=tiny_plan),
        ):
            completed = extension.run_live_history_extension_rehearsal(
                self.target,
                proof=self.proof,
                api_key="offline-window-test-key",
                transport=transport,
                importer=self._window_importer(),
                max_requests=1,
                now=self._now,
                monotonic=self.clock.monotonic,
                sleeper=self.clock.sleep,
            )
            self.assertEqual(completed.completion, "finalized")
            self.assertEqual(len(transport.calls), 1)
            layout = extension._live_layout(self.target)
            for private_root in (layout.backup, layout.restored, layout.candidate):
                self.assertEqual(stat.S_IMODE(private_root.stat().st_mode), 0o700)
            candidate_receipts = layout.candidate / "candidate-receipts"
            self.assertEqual(stat.S_IMODE(candidate_receipts.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(layout.completion.stat().st_mode), 0o600)
            candidate_paths = tuple(candidate_receipts.glob("*.json"))
            self.assertEqual(len(candidate_paths), 1)
            self.assertEqual(stat.S_IMODE(candidate_paths[0].stat().st_mode), 0o600)
            candidate = extension._read_live_candidate(
                layout, tiny_plan, self.proof
            )
            evidence = candidate.to_primitive()["evidence"]
            self.assertNotIn("replay_unchanged", evidence)
            self.assertNotIn("replay_unchanged", evidence["reconciliation"])

            reused = extension.run_live_history_extension_rehearsal(
                self.target,
                proof=self.proof,
                api_key="offline-window-test-key",
                transport=transport,
                importer=self._window_importer(),
                max_requests=1,
                now=self._now,
                monotonic=self.clock.monotonic,
                sleeper=self.clock.sleep,
            )
            self.assertEqual(reused.completion, "finalized")
            self.assertEqual(reused.requests_issued, 0)
            self.assertEqual(len(transport.calls), 1)

            raw_path = extension._raw_path(extension._live_layout(self.target), sentinel)
            raw_path.write_bytes(b"[]")
            with self.assertRaises(ConflictError):
                extension.run_live_history_extension_rehearsal(
                    self.target,
                    proof=self.proof,
                    api_key="offline-window-test-key",
                    transport=transport,
                    importer=self._window_importer(),
                    max_requests=1,
                    now=self._now,
                    monotonic=self.clock.monotonic,
                    sleeper=self.clock.sleep,
                )
        self.assertEqual(len(transport.calls), 1)



    def test_recovered_noncomplete_sentinel_stops_without_subsequent_transport(self) -> None:
        target = self._new_target("recovered-terminal-sentinel")
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        layout = extension._initialize_or_open_live_layout(target, plan, _EXECUTION_REVISION)
        sentinel = plan.intents[0]
        intent_sha256 = extension._write_live_intent(layout, sentinel, now=self._now)
        extension._spool_response(
            layout,
            sentinel,
            intent_sha256=intent_sha256,
            api_key="offline-window-test-key",
            response=CapturedFmpStage10Response(
                404,
                "application/json",
                b'{"Error Message":"not found"}',
            ),
        )
        transport = _WindowTransport()

        report = self._run(transport, max_requests=1, target=target)

        self.assertEqual(report.completion, "sentinel_failed")
        self.assertEqual(report.requests_issued, 0)
        self.assertEqual(report.closed_request_count, 1)
        self.assertEqual(report.sentinel_status, "terminal_failure")
        self.assertEqual(report.terminal_failure_count, 1)
        self.assertEqual(transport.calls, [])
        result = extension._read_live_result(
            layout,
            sentinel,
            intent_sha256=intent_sha256,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["disposition"], "terminal_failure")  # type: ignore[index]

    @mock.patch.object(
        extension, "_verified_live_authorization_proof", return_value=_AUTHORIZATION_PROOF
    )
    def test_exact_402_transition_recovers_spool_without_network(
        self, _authorization: object) -> None:
        target = self._new_target("exact-402-transition")
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        intent = plan.intents[614]
        self.assertEqual(
            (intent.ordinal, intent.symbol, intent.window.window_id),
            (615, "^AXJO", "1990-1994"),
        )
        old_revision = "a" * 64
        new_revision = "b" * 64
        layout = extension._initialize_or_open_live_layout(
            target, plan, old_revision
        )
        intent_sha256 = extension._write_live_intent(layout, intent, now=self._now)
        body = b"Plain provider entitlement response"
        extension._spool_response(
            layout,
            intent,
            intent_sha256=intent_sha256,
            api_key="offline-window-test-key",
            response=CapturedFmpStage10Response(402, "application/json", body),
        )
        patches = {
            "TRANSITION_402_OLD_EXECUTION_REVISION": old_revision,
            "TRANSITION_402_PLAN_SHA256": plan.sha256,
            "TRANSITION_402_PENDING_INTENT_SHA256": intent_sha256,
            "TRANSITION_402_RESPONSE_SHA256": hashlib.sha256(body).hexdigest(),
            "TRANSITION_402_RESPONSE_BYTE_COUNT": len(body),
        }
        with (
            mock.patch.multiple(extension, **patches),
            mock.patch.object(
                extension,
                "_extension_code_revision",
                return_value=new_revision,
            ),
        ):
            with mock.patch.object(
                extension,
                "_verified_live_authorization_proof",
                side_effect=ConflictError("missing successor authorization"),
            ):
                with self.assertRaises(ConflictError):
                    extension._recover_or_close_spooled_response(
                        layout,
                        self.proof,
                        intent,
                        intent_sha256=intent_sha256,
                        importer=self._window_importer(),
                    )
            receipt = extension._transition_402_receipt(plan, new_revision)
            extension._write_json_exclusive(
                extension._transition_402_path(layout),
                receipt,
                maximum_bytes=extension._MAX_RECEIPT_BYTES,
            )
            extension._open_live_layout(target, plan, new_revision)
            result = extension._recover_or_close_spooled_response(
                layout,
                self.proof,
                intent,
                intent_sha256=intent_sha256,
                importer=self._window_importer(),
            )
            self.assertEqual(result["disposition"], "terminal_failure")
            self.assertEqual(
                result["terminal_reason"],
                "operator_authorized_entitlement_unavailable",
            )
            self.assertEqual(result["http_status"], 402)
            extension._validate_newly_closed_live_result(
                layout,
                self.proof,
                intent,
                result,
                importer=self._window_importer(),
            )

            later_intent = plan.intents[615]
            later_intent_sha256 = extension._write_live_intent(
                layout, later_intent, now=self._now
            )
            extension._spool_response(
                layout,
                later_intent,
                intent_sha256=later_intent_sha256,
                api_key="offline-window-test-key",
                response=CapturedFmpStage10Response(
                    402, "application/json", body
                ),
            )
            later_result = extension._recover_or_close_spooled_response(
                layout,
                self.proof,
                later_intent,
                intent_sha256=later_intent_sha256,
                importer=self._window_importer(),
            )
            self.assertEqual(later_result["disposition"], "terminal_failure")
            self.assertEqual(
                later_result["terminal_reason"],
                "operator_authorized_entitlement_unavailable",
            )
            extension._validate_newly_closed_live_result(
                layout,
                self.proof,
                later_intent,
                later_result,
                importer=self._window_importer(),
            )

            tampered = dict(receipt)
            tampered["transition_sha256"] = "f" * 64
            extension._transition_402_path(layout).write_text(
                dumps_strict(tampered), encoding="utf-8"
            )
            with self.assertRaises(ConflictError):
                extension._open_live_layout(target, plan, new_revision)

    @mock.patch.object(
        extension, "_verified_live_authorization_proof", return_value=_AUTHORIZATION_PROOF
    )
    def test_known_listed_empty_transition_authorizes_local_recovery(
        self, _authorization: object) -> None:
        target = self._new_target("known-listed-empty-transition")
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        old_revision = "1" * 64
        new_revision = "2" * 64
        prior_transition_sha256 = "3" * 64
        transport = _WindowTransport()
        prefix = self._run(
            transport,
            max_requests=1,
            target=target,
            execution_revision=old_revision,
        )
        self.assertEqual(prefix.closed_request_count, 1)
        self.assertEqual(len(transport.calls), 1)

        layout = extension._live_layout(target)
        intent = plan.intents[1]
        intent_sha256 = extension._write_live_intent(
            layout, intent, now=self._now
        )
        extension._spool_response(
            layout,
            intent,
            intent_sha256=intent_sha256,
            api_key="offline-window-test-key",
            response=CapturedFmpStage10Response(
                200, "application/json", b"[]"
            ),
        )
        patches = {
            "TRANSITION_402_OLD_EXECUTION_REVISION": old_revision,
            "TRANSITION_EMPTY_OLD_EXECUTION_REVISION": old_revision,
            "TRANSITION_EMPTY_PLAN_SHA256": plan.sha256,
            "TRANSITION_EMPTY_PRIOR_TRANSITION_SHA256": (
                prior_transition_sha256
            ),
            "TRANSITION_EMPTY_CLOSED_REQUEST_COUNT": 1,
            "TRANSITION_EMPTY_PENDING_ORDINAL": intent.ordinal,
            "TRANSITION_EMPTY_PENDING_SYMBOL": intent.symbol,
            "TRANSITION_EMPTY_PENDING_WINDOW_ID": intent.window.window_id,
            "TRANSITION_EMPTY_PENDING_FROM": intent.window.start_date,
            "TRANSITION_EMPTY_PENDING_TO": intent.window.end_date,
            "TRANSITION_EMPTY_PENDING_INTENT_SHA256": intent_sha256,
        }
        project = self.root / "approved-empty-transition-project"
        project.mkdir()
        with (
            mock.patch.multiple(extension, **patches),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT",
                project,
            ),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT",
                target,
            ),
            mock.patch.object(
                extension,
                "_load_verified_stage10_base",
                return_value=self.proof,
            ),
            mock.patch.object(
                extension,
                "_extension_code_revision",
                return_value=new_revision,
            ),
            mock.patch.object(
                extension,
                "_read_execution_transition_402",
                return_value={
                    "transition_sha256": prior_transition_sha256
                },
            ),
            mock.patch.object(
                extension,
                "_empty_window_listing_status",
                return_value=(True, False),
            ),
        ):
            with mock.patch.object(
                extension,
                "_verified_live_authorization_proof",
                side_effect=ConflictError("missing successor authorization"),
            ):
                with self.assertRaises(ConflictError):
                    extension._recover_or_close_spooled_response(
                        layout,
                        self.proof,
                        intent,
                        intent_sha256=intent_sha256,
                        importer=self._window_importer(),
                    )

            receipt = (
                extension.authorize_stage10_history_extension_empty_transition()
            )
            self.assertEqual(
                receipt["terminal_reason"],
                extension.TRANSITION_EMPTY_TERMINAL_REASON,
            )
            self.assertEqual(
                receipt["prefix_ledger_sha256"],
                extension._transition_empty_prefix_binding(
                    layout, plan
                )[0],
            )
            self.assertEqual(
                extension.authorize_stage10_history_extension_empty_transition(),
                receipt,
            )

            result = extension._recover_or_close_spooled_response(
                layout,
                self.proof,
                intent,
                intent_sha256=intent_sha256,
                importer=self._window_importer(),
            )
            self.assertEqual(result["disposition"], "terminal_failure")
            self.assertEqual(
                result["terminal_reason"],
                extension.TRANSITION_EMPTY_TERMINAL_REASON,
            )
            self.assertEqual(result["http_status"], 200)
            self.assertEqual(result["row_count"], 0)
            extension._reconcile_live_journal(layout, plan)
            extension._validate_newly_closed_live_result(
                layout,
                self.proof,
                intent,
                result,
                importer=self._window_importer(),
            )
        self.assertEqual(len(transport.calls), 1)

    def test_successor_chain_authorizer_is_receipt_only_and_idempotent(self) -> None:
        target = self._new_target("successor-chain-authorizer")
        old_revision = "4" * 64
        new_revision = "5" * 64
        transport = _WindowTransport()
        prefix = self._run(
            transport,
            max_requests=1,
            target=target,
            execution_revision=old_revision,
        )
        self.assertEqual(prefix.closed_request_count, 1)
        plan = extension.build_history_extension_plan(self.proof.verified_base)
        layout = extension._live_layout(target)
        pending = plan.intents[1]
        intent_sha256 = extension._write_live_intent(
            layout, pending, now=self._now
        )
        extension._spool_response(
            layout,
            pending,
            intent_sha256=intent_sha256,
            api_key="offline-window-test-key",
            response=CapturedFmpStage10Response(
                200, "application/json", b"[]"
            ),
        )
        recovered = extension._read_live_spool(
            layout,
            pending,
            intent_sha256=intent_sha256,
        )
        self.assertIsNotNone(recovered)
        spool, body = recovered  # type: ignore[misc]
        manifest = extension._read_json(
            layout.manifest, "live manifest", extension._MAX_STATE_BYTES
        )
        with mock.patch.object(
            extension, "TRANSITION_EMPTY_CLOSED_REQUEST_COUNT", 1
        ):
            prefix_ledger_sha256, prefix_raw_sha256 = (
                extension._transition_empty_prefix_binding(layout, plan)
            )

        prior = {
            "contract": TRANSITION_CONTRACT,
            "transition_sha256": PRIOR_402_TRANSITION_SHA256,
        }
        empty = {
            "contract": TRANSITION_CONTRACT,
            "transition_sha256": KNOWN_LISTED_EMPTY_TRANSITION_SHA256,
        }
        patches = {
            "TRANSITION_CHAIN_LIVE_EXECUTION_REVISION": old_revision,
            "TRANSITION_CHAIN_LIVE_MANIFEST_SHA256": manifest[
                "manifest_sha256"
            ],
            "TRANSITION_CHAIN_OLD_EXECUTION_REVISION": old_revision,
            "TRANSITION_CHAIN_PLAN_SHA256": plan.sha256,
            "TRANSITION_CHAIN_CLOSED_REQUEST_COUNT": 1,
            "TRANSITION_CHAIN_PENDING_ORDINAL": pending.ordinal,
            "TRANSITION_CHAIN_PENDING_SYMBOL": pending.symbol,
            "TRANSITION_CHAIN_PENDING_WINDOW_ID": pending.window.window_id,
            "TRANSITION_CHAIN_PENDING_FROM": pending.window.start_date,
            "TRANSITION_CHAIN_PENDING_TO": pending.window.end_date,
            "TRANSITION_CHAIN_PENDING_INTENT_SHA256": intent_sha256,
            "TRANSITION_CHAIN_PREFIX_LEDGER_SHA256": prefix_ledger_sha256,
            "TRANSITION_CHAIN_PREFIX_RAW_SHA256": prefix_raw_sha256,
            "TRANSITION_EMPTY_CLOSED_REQUEST_COUNT": 1,
            "TRANSITION_EMPTY_PLAN_SHA256": plan.sha256,
            "TRANSITION_EMPTY_PENDING_ORDINAL": pending.ordinal,
            "TRANSITION_EMPTY_PENDING_SYMBOL": pending.symbol,
            "TRANSITION_EMPTY_PENDING_WINDOW_ID": pending.window.window_id,
            "TRANSITION_EMPTY_PENDING_FROM": pending.window.start_date,
            "TRANSITION_EMPTY_PENDING_TO": pending.window.end_date,
            "TRANSITION_EMPTY_PENDING_INTENT_SHA256": intent_sha256,
        }
        project = self.root / "successor-chain-project"
        project.mkdir()

        def file_bytes() -> dict[str, bytes]:
            return {
                str(path.relative_to(layout.root)): path.read_bytes()
                for path in layout.root.rglob("*")
                if path.is_file()
            }

        before = file_bytes()
        resume_before = layout.resume.read_bytes()
        with (
            mock.patch.multiple(extension, **patches),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT",
                project,
            ),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT",
                target,
            ),
            mock.patch.object(
                extension,
                "_load_verified_stage10_base",
                return_value=self.proof,
            ),
            mock.patch.object(
                extension,
                "_extension_code_revision",
                return_value=new_revision,
            ),
            mock.patch.object(
                extension,
                "_read_execution_transition_402",
                return_value=prior,
            ),
            mock.patch.object(
                extension,
                "_read_execution_transition_empty",
                return_value=empty,
            ),
            mock.patch.object(
                extension,
                "_validate_transition_empty_source_evidence",
                return_value=(pending, spool, body),
            ),
        ):
            receipt = (
                extension.authorize_stage10_history_extension_empty_chain_transition()
            )
            receipt_path = extension._transition_empty_chain_path(layout)
            self.assertEqual(
                set(file_bytes()),
                set(before) | {str(receipt_path.relative_to(layout.root))},
            )
            self.assertEqual(layout.resume.read_bytes(), resume_before)
            self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
            self.assertEqual(receipt_path.stat().st_nlink, 1)
            self.assertEqual(
                receipt["authorization_chain"], _AUTHORIZATION_CHAIN
            )
            snapshot = file_bytes()
            self.assertEqual(
                extension.authorize_stage10_history_extension_empty_chain_transition(),
                receipt,
            )
            self.assertEqual(file_bytes(), snapshot)
            self.assertEqual(len(transport.calls), 1)

            extension._write_live_resume(layout, plan, ())
            stale_resume = layout.resume.read_bytes()
            with self.assertRaisesRegex(ConflictError, "resume cache"):
                extension.authorize_stage10_history_extension_empty_chain_transition()
            self.assertEqual(layout.resume.read_bytes(), stale_resume)

    def test_base_proof_precedes_credential_environment_read(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project = root / "project"
        target = root / "target"
        project.mkdir()
        target.mkdir()
        events: list[str] = []

        def base_verifier(actual_target: Path, actual_project: Path):
            events.append("base")
            self.assertEqual((actual_target, actual_project), (target, project))
            raise ConflictError("offline base proof rejection")

        bindings = extension.Stage10HistoryExtensionBindings(
            base_verifier=base_verifier,
            transport_factory=lambda *_args: AssertionError("transport must not be built"),
            importer_factory=lambda *_args: AssertionError("importer must not be built"),
        )
        with (
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_PROJECT_ROOT",
                project,
            ),
            mock.patch.object(
                extension,
                "APPROVED_STAGE10_HISTORY_EXTENSION_TARGET_ROOT",
                target,
            ),
            self.assertRaises(ConflictError),
        ):
            extension.run_stage10_history_extension(
                project,
                target,
                environment=_PoisonEnvironment(),
                bindings=bindings,
                mode="sentinel_only",
            )
        self.assertEqual(events, ["base"])

    def test_base_candidate_reader_accepts_sibling_extension_namespace(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        target = root / "target"
        candidate_root = target / "private" / "candidate"
        receipt_directory = candidate_root / "promotion-candidates"
        receipt_directory.mkdir(parents=True, mode=0o700)
        sibling = candidate_root / "stage10-history-extension-v1"
        sibling.mkdir(mode=0o700)
        for path in (target, target / "private", candidate_root, receipt_directory, sibling):
            os.chmod(path, 0o700)

        cohort_registry = load_registry(
            _PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=_PROJECT_ROOT,
            environment={},
        )
        registry = stage10_registry_profile(cohort_registry)
        scope = load_stage10_market_scope(_SCOPE_PATH)
        evidence_material = {
            "reconciliation": {
                "scope_manifest_sha256": scope.manifest_sha256,
                "target_profile_id": scope.target_profile_id,
            }
        }
        evidence = {**evidence_material, "sha256": _sha_json(evidence_material)}
        attempt_id = f"stage10-fmp-market-history-v1-{scope.manifest_sha256[:16]}"
        material = {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": attempt_id,
            "generated_at": "2026-08-12T12:00:00.000000Z",
            "code_revision": registry.source_sha256,
            "candidate_state": "private_candidate_only_no_operational_promotion",
            "evidence": evidence,
            "evidence_sha256": evidence["sha256"],
        }
        receipt = {**material, "receipt_sha256": _sha_json(material)}
        receipt_path = receipt_directory / f"{attempt_id}.json"
        receipt_path.write_text(dumps_strict(receipt), encoding="utf-8")
        os.chmod(receipt_path, 0o600)

        layout = stage10_backfill._layout_paths(target)
        loaded = stage10_backfill._read_candidate_receipt(
            layout,
            scope=scope,
            registry=registry,
        )

        self.assertEqual(loaded.to_primitive(), receipt)


    def test_cli_requires_exactly_one_final_execution_mode(self) -> None:
        prefix = [
            "--project-root", "project",
            "--target-root", "target",
        ]
        self.assertEqual(
            extension._parse_live_cli(prefix + ["--sentinel-only"]),
            ("project", "target", "sentinel_only"),
        )
        self.assertEqual(
            extension._parse_live_cli(prefix + ["--continue-after-sentinel"]),
            ("project", "target", "continue_after_sentinel"),
        )
        for argv in (
            prefix,
            prefix + ["--sentinel-only", "--continue-after-sentinel"],
            prefix + ["--continue-after-sentinel", "--sentinel-only"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(extension._LiveArgumentFailure):
                    extension._parse_live_cli(argv)

    def test_public_sentinel_only_rerun_issues_no_second_request(self) -> None:
        transport = _WindowTransport()

        first = self._run_public(transport, mode="sentinel_only")
        self.assertEqual((first.requests_issued, first.closed_request_count), (1, 1))
        self.assertEqual(
            transport.calls[0]["query"],
            {"symbol": "AAPL", "from": "1990-01-01", "to": "1994-12-31"},
        )

        rerun = self._run_public(transport, mode="sentinel_only")
        self.assertEqual(rerun.requests_issued, 0)
        self.assertEqual(rerun.closed_request_count, 1)
        self.assertEqual(len(transport.calls), 1)

    def test_public_continuation_requires_durable_sentinel_before_transport(self) -> None:
        transport = _WindowTransport()

        with self.assertRaisesRegex(
            ConflictError, "continuation requires the sentinel phase"
        ):
            self._run_public(transport, mode="continue_after_sentinel")

        self.assertEqual(transport.calls, [])

    def test_new_closure_is_revalidated_before_the_next_transport(self) -> None:
        transport = _WindowTransport()
        events: list[str] = []
        original_get = transport.get
        original_closed = extension._validate_newly_closed_live_result

        def traced_get(**kwargs: object) -> CapturedFmpStage10Response:
            events.append("request")
            return original_get(**kwargs)

        def traced_closed(*args: object, **kwargs: object) -> object:
            events.append("closed")
            return original_closed(*args, **kwargs)

        transport.get = traced_get  # type: ignore[method-assign]
        with mock.patch.object(
            extension,
            "_validate_newly_closed_live_result",
            side_effect=traced_closed,
        ):
            report = self._run(transport, max_requests=3)

        self.assertEqual(report.requests_issued, 3)
        request_indexes = [
            index for index, event in enumerate(events) if event == "request"
        ]
        self.assertEqual(len(request_indexes), 3)
        for earlier, later in zip(request_indexes, request_indexes[1:]):
            self.assertIn("closed", events[earlier + 1 : later])
        self.assertEqual(events[-1], "closed")

    def test_execution_revision_pins_manifest_and_drift_stops_next_request_and_reuse(
        self,
    ) -> None:
        transport = _WindowTransport()
        layout = extension._live_layout(self.target)
        original_get = transport.get

        def pinned_get(**kwargs: object) -> CapturedFmpStage10Response:
            manifest = extension._read_json(
                layout.manifest, "live manifest", extension._MAX_STATE_BYTES
            )
            self.assertEqual(manifest["execution_revision"], _EXECUTION_REVISION)
            return original_get(**kwargs)

        transport.get = pinned_get  # type: ignore[method-assign]
        with mock.patch.object(
            extension,
            "_extension_code_revision",
            side_effect=[_EXECUTION_REVISION, _EXECUTION_REVISION, "f" * 64],
        ):
            with self.assertRaisesRegex(ConflictError, "execution sources changed"):
                self._run(
                    transport,
                    max_requests=2,
                    execution_revision=None,
                )

        self.assertEqual(len(transport.calls), 1)
        replay_transport = _WindowTransport()
        with mock.patch.object(
            extension, "_extension_code_revision", return_value="f" * 64
        ):
            with self.assertRaises(ConflictError):
                self._run(
                    replay_transport,
                    max_requests=1,
                    execution_revision=None,
                )
        self.assertEqual(replay_transport.calls, [])

    @mock.patch.object(
        extension, "_verified_live_authorization_proof", return_value=_AUTHORIZATION_PROOF
    )
    def test_post_history_aapl_empty_is_skipped_before_next_transport(
        self, _authorization: object) -> None:
        plan_sha256 = "b" * 64
        intents = tuple(
            extension.HistoryExtensionIntent(
                ordinal=ordinal,
                symbol="AAPL",
                window=extension.HISTORY_EXTENSION_WINDOWS[index],
                plan_sha256=plan_sha256,
                base_completion_sha256=self.proof.verified_base.completion_sha256,
                base_reconciliation_sha256=self.proof.base_reconciliation.sha256,
            )
            for ordinal, index in enumerate((0, 1, 2), start=1)
        )
        tiny_plan = SimpleNamespace(
            base=self.proof.verified_base,
            intents=intents,
            sha256=plan_sha256,
        )
        transport = _WindowTransport()

        with mock.patch.object(
            extension, "build_history_extension_plan", return_value=tiny_plan
        ):
            report = self._run(transport, max_requests=2)

        self.assertEqual(report.completion, "partial")
        self.assertEqual(report.terminal_failure_count, 1)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(
            transport.calls[1]["query"],
            intents[1].window.query("AAPL"),
        )
