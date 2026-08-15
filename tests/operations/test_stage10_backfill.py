"""Focused offline checks for the restricted Stage 10 manual runner."""

from __future__ import annotations

import http.client
import io
import json
import socket
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from quant_data.errors import ConflictError, StoreUnavailableError
from quant_data.market.fmp_bulk_daily_prices import FmpPriceHistoryUnavailable
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.operations.stage10_backfill import (
    _UNIVERSE_IDS,
    _CompletionReceipt,
    _acquire_run_lock,
    _new_layout,
    Stage10BackfillBindings,
    _default_roster_reader,
    _validate_materialized_store_files,
    _validate_reused_store_evidence,
    main,
)
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _Transport:
    pass


class Stage10BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.target = self.root / "stage10-target"
        self.scope = load_stage10_market_scope(SCOPE_PATH)
        self.registry = SimpleNamespace(source_sha256="a" * 64)
        self.secret = "stage10-test-secret"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _streams(self) -> tuple[io.StringIO, io.StringIO]:
        return io.StringIO(), io.StringIO()

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

    def _dependencies(
        self,
        events: list[str],
        *,
        roster: tuple[str, ...] = ("AAPL", "MSFT", "SPY"),
        fail_price_symbol: str | None = None,
        skip_price_symbol: str | None = None,
        fail_universe_publish_at: int | None = None,
        universe_outcome: str = "succeeded",
    ) -> dict[str, object]:
        transport = _Transport()

        def registry_loader(*_args: object, **_kwargs: object) -> object:
            events.append("registry")
            return self.registry

        def registry_validator(actual: object) -> None:
            self.assertIs(actual, self.registry)
            events.append("registry_validated")

        def scope_loader(path: str | Path):
            self.assertEqual(Path(path), self.project / "config" / "stage10_market_scope.json")
            events.append("scope")
            return self.scope

        def scope_validator(actual: object) -> None:
            self.assertIs(actual, self.scope)
            events.append("scope_validated")

        def store_map_factory(root: Path) -> StoreMap:
            self.assertEqual(root, self.target / "stores")
            events.append("store_map")
            return self._store_map(root)

        def initializer(actual_map: StoreMap, actual_registry: object) -> None:
            self.assertIs(actual_registry, self.registry)
            self.assertEqual(actual_map.path("market").parent, self.target / "stores")
            events.append("initialize")

        def transport_factory(api_key: str, actual_map: StoreMap, actual_scope: object) -> object:
            self.assertEqual(api_key, self.secret)
            self.assertEqual(actual_map.path("market").parent, self.target / "stores")
            self.assertIs(actual_scope, self.scope)
            events.append("transport")
            return transport

        def capture_universe(source: object, api_key: str, actual_transport: object) -> object:
            self.assertEqual(api_key, self.secret)
            self.assertIs(actual_transport, transport)
            source_id = getattr(source, "id")
            events.append("universe:" + source_id)
            return {"universe_id": source_id}

        def capture_price(symbol: str, api_key: str, actual_transport: object) -> object:
            self.assertEqual(api_key, self.secret)
            self.assertIs(actual_transport, transport)
            events.append("price:" + symbol)
            if symbol == fail_price_symbol:
                raise StoreUnavailableError("offline provider failure")
            if symbol == skip_price_symbol:
                raise FmpPriceHistoryUnavailable(
                    symbol=symbol,
                    status=200,
                    content_type="application/json",
                    body_sha256="d" * 64,
                    body_byte_count=2,
                )
            return {"symbol": symbol, "raw": self.secret}

        importer = object()

        def importer_factory(actual_map: StoreMap, actual_registry: object, actual_scope: object) -> object:
            self.assertIs(actual_registry, self.registry)
            self.assertIs(actual_scope, self.scope)
            self.assertEqual(actual_map.path("market").parent, self.target / "stores")
            events.append("importer")
            return importer

        def prepare_universes(actual_importer: object, captures: object):
            self.assertIs(actual_importer, importer)
            self.assertEqual(
                tuple(captures),
                ("sp500_current", "nasdaq100_current", "dow30_current"),
            )
            events.append("prepare_universes")
            return tuple(("universe", index) for index in range(5))

        def prepare_price(actual_importer: object, capture: object) -> object:
            self.assertIs(actual_importer, importer)
            self.assertIsInstance(capture, dict)
            events.append("prepare_price:" + str(capture["symbol"]))
            return ("price", capture["symbol"])

        def publish(actual_importer: object, candidate: object) -> object:
            self.assertIs(actual_importer, importer)
            if isinstance(candidate, tuple) and candidate[0] == "universe":
                events.append("publish_universe:" + str(candidate[1]))
                if candidate[1] == fail_universe_publish_at:
                    raise StoreUnavailableError("offline universe publication failure")
                return {"outcome": universe_outcome}
            else:
                self.assertIsInstance(candidate, tuple)
                events.append("publish_price:" + str(candidate[1]))
            return {"outcome": "succeeded"}

        def roster_reader(actual_map: StoreMap, actual_scope: object) -> tuple[str, ...]:
            self.assertIs(actual_scope, self.scope)
            self.assertEqual(actual_map.path("market").parent, self.target / "stores")
            events.append("roster")
            return roster

        def assert_no_partial(actual_map: StoreMap, actual_scope: object) -> None:
            self.assertIs(actual_scope, self.scope)
            self.assertEqual(actual_map.path("market").parent, self.target / "stores")
            events.append("partial_check")

        return {
            "environment": {"FMP_API_KEY": self.secret},
            "registry_loader": registry_loader,
            "registry_validator": registry_validator,
            "scope_loader": scope_loader,
            "scope_validator": scope_validator,
            "store_map_factory": store_map_factory,
            "store_file_validator": lambda _store_map, _stores_root: None,
            "completed_store_validator": (
                lambda _layout, _store_map, _registry, _scope: None
            ),
            "initializer": initializer,
            "bindings": Stage10BackfillBindings(
                transport_factory=transport_factory,
                capture_universe=capture_universe,
                capture_price=capture_price,
                importer_factory=importer_factory,
                prepare_universes=prepare_universes,
                prepare_price=prepare_price,
                publish=publish,
                roster_reader=roster_reader,
                assert_no_partial_universe_publication=assert_no_partial,
            ),
            "approved_project_root": self.project,
            "approved_target_root": self.target,
        }

    def _assert_error(
        self,
        result: int,
        stdout: io.StringIO,
        stderr: io.StringIO,
        *,
        code: str,
    ) -> None:
        self.assertEqual(stdout.getvalue(), "")
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            json.loads(lines[0]),
            {
                "contract": "quant_data.stage10_backfill_error",
                "contract_version": "1.0.0",
                "error": code,
                "exit_code": result,
            },
        )
        self.assertNotIn(self.secret, stderr.getvalue())
        self.assertNotIn(str(self.target), stderr.getvalue())

    def test_manual_run_is_sequential_paced_candidate_only_and_sanitized(self) -> None:
        events: list[str] = []
        stdout, stderr = self._streams()
        clock = _Clock()
        completion_contexts: list[object] = []

        def completion(context: object) -> None:
            completion_contexts.append(context)
            events.append("completion")

        dependencies = self._dependencies(events)
        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
            mock.patch.object(http.client, "HTTPSConnection", side_effect=AssertionError("network")),
        ):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
                completion_callback=completion,
                **dependencies,
            )

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["universe_request_count"], 3)
        self.assertEqual(payload["price_request_count"], 3)
        self.assertEqual(payload["roster_symbol_count"], 3)
        self.assertEqual(payload["completed_symbol_count"], 3)
        self.assertFalse(payload["resumed"])
        self.assertEqual(payload["completion"], "injected")
        self.assertEqual(payload["candidate_state"], "candidate_only_no_operational_promotion")
        self.assertNotIn(self.secret, stdout.getvalue())
        self.assertNotIn(str(self.target), stdout.getvalue())
        self.assertNotIn("raw", stdout.getvalue())
        self.assertNotIn("apikey", stdout.getvalue())
        self.assertEqual(len(completion_contexts), 1)
        self.assertEqual(
            [event for event in events if event.startswith("universe:")],
            [
                "universe:sp500_current",
                "universe:nasdaq100_current",
                "universe:dow30_current",
            ],
        )
        self.assertLess(events.index("prepare_universes"), events.index("price:AAPL"))
        self.assertEqual(clock.sleeps, [1.0] * 5)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.target / "stores").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.target / "private").stat().st_mode), 0o700)
        resume = json.loads((self.target / "private" / "resume.json").read_text())
        self.assertEqual(resume["completed_symbols"], ["AAPL", "MSFT", "SPY"])
        self.assertNotIn(self.secret, json.dumps(resume))

    def test_failure_resume_skips_completed_symbols_and_universe_refetches(self) -> None:
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        first_dependencies = self._dependencies(first_events, fail_price_symbol="MSFT")
        first_clock = _Clock()
        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
        ):
            first_result = main(
                self._arguments(),
                stdout=first_stdout,
                stderr=first_stderr,
                monotonic=first_clock.monotonic,
                sleeper=first_clock.sleep,
                completion_callback=lambda _context: None,
                **first_dependencies,
            )
        self.assertEqual(first_result, 69)
        self._assert_error(first_result, first_stdout, first_stderr, code="unavailable")
        self.assertEqual(
            [event for event in first_events if event.startswith("price:")],
            ["price:AAPL", "price:MSFT"],
        )
        partial_resume = json.loads((self.target / "private" / "resume.json").read_text())
        self.assertEqual(partial_resume["completed_symbols"], ["AAPL"])
        self.assertTrue(partial_resume["universes_published"])

        second_events: list[str] = []
        second_stdout, second_stderr = self._streams()
        second_dependencies = self._dependencies(second_events)
        second_clock = _Clock()
        second_result = main(
            self._arguments(),
            stdout=second_stdout,
            stderr=second_stderr,
            monotonic=second_clock.monotonic,
            sleeper=second_clock.sleep,
            completion_callback=lambda _context: None,
            **second_dependencies,
        )
        self.assertEqual(second_result, 0)
        self.assertEqual(second_stderr.getvalue(), "")
        second_payload = json.loads(second_stdout.getvalue())
        self.assertTrue(second_payload["resumed"])
        self.assertEqual(second_payload["universe_request_count"], 0)
        self.assertEqual(second_payload["price_request_count"], 2)
        self.assertEqual(
            [event for event in second_events if event.startswith("universe:")],
            [],
        )
        self.assertEqual(
            [event for event in second_events if event.startswith("price:")],
            ["price:MSFT", "price:SPY"],
        )
        self.assertEqual(second_clock.sleeps, [1.0, 1.0])
        final_resume = json.loads((self.target / "private" / "resume.json").read_text())
        self.assertEqual(final_resume["completed_symbols"], ["AAPL", "MSFT", "SPY"])

    def test_unavailable_price_history_is_journaled_then_skipped(self) -> None:
        events: list[str] = []
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(events, skip_price_symbol="MSFT"),
        )
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["completed_symbol_count"], 2)
        self.assertEqual(payload["failed_symbol_count"], 1)
        self.assertEqual(payload["failed_tickers"], ["MSFT"])
        self.assertEqual(payload["price_request_count"], 3)
        self.assertEqual(
            [event for event in events if event.startswith("price:")],
            ["price:AAPL", "price:MSFT", "price:SPY"],
        )
        resume = json.loads((self.target / "private" / "resume.json").read_text())
        self.assertEqual(resume["version"], "1.1.0")
        self.assertEqual(resume["failed_records"][0]["symbol"], "MSFT")
        receipt = json.loads((self.target / "private" / "failures" / "MSFT.json").read_text())
        self.assertEqual(receipt["record"], resume["failed_records"][0])
        self.assertEqual(stat.S_IMODE((self.target / "private" / "failures" / "MSFT.json").stat().st_mode), 0o600)

    def test_nonzero_failure_completion_reader_uses_candidate_json_primitive(self) -> None:
        from quant_data.operations import stage10_backfill

        result = main(
            self._arguments(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            completion_callback=lambda _context: None,
            **self._dependencies([], skip_price_symbol="MSFT"),
        )
        self.assertEqual(result, 0)
        layout = stage10_backfill._layout_paths(self.target)
        state = stage10_backfill._read_resume(
            layout,
            scope=self.scope,
            registry=self.registry,
        )
        self.assertEqual([record.symbol for record in state.failed_records], ["MSFT"])

        reconciliation_primitive = {
            "failed_records": [
                record.to_primitive() for record in state.failed_records
            ],
            "failed_tickers": ["MSFT"],
            "failed_symbol_count": 1,
            "successful_symbol_count": 2,
            "failure_manifest_sha256": state.failure_manifest_sha256,
        }

        class FrozenCandidate:
            evidence_sha256 = "b" * 64
            receipt_sha256 = "c" * 64
            evidence = {
                "reconciliation": {
                    **reconciliation_primitive,
                    "failed_records": tuple(
                        reconciliation_primitive["failed_records"]
                    ),
                    "failed_tickers": ("MSFT",),
                }
            }

            @staticmethod
            def to_primitive() -> dict[str, object]:
                return {"evidence": {"reconciliation": reconciliation_primitive}}

        candidate = FrozenCandidate()
        context = stage10_backfill.Stage10CompletionContext(
            store_map=self._store_map(layout.stores),
            registry=self.registry,
            scope=self.scope,
            private_root=layout.private,
            completed_symbols=state.completed_symbols,
            roster_symbol_count=3,
            resumed=False,
            failed_records=state.failed_records,
            failure_manifest_sha256=state.failure_manifest_sha256,
        )
        stage10_backfill._write_completion_receipt(layout, context, candidate)
        with mock.patch.object(
            stage10_backfill,
            "_read_candidate_receipt",
            return_value=candidate,
        ):
            completed = stage10_backfill._read_completed_receipt(
                layout,
                state,
                scope=self.scope,
                registry=self.registry,
            )
        self.assertIsNotNone(completed)
        self.assertEqual(
            [record.symbol for record in completed.failed_records],
            ["MSFT"],
        )

    def test_failure_journal_only_state_repairs_resume_before_continuing(self) -> None:
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        dependencies = self._dependencies(first_events, skip_price_symbol="MSFT")
        from quant_data.operations import stage10_backfill

        original_resume = stage10_backfill._write_resume
        writes = 0

        def fail_after_journal(layout: object, state: object) -> None:
            nonlocal writes
            writes += 1
            if writes == 4:
                raise OSError("synthetic journal-first crash")
            original_resume(layout, state)

        with mock.patch.object(stage10_backfill, "_write_resume", side_effect=fail_after_journal):
            result = main(
                self._arguments(),
                stdout=first_stdout,
                stderr=first_stderr,
                completion_callback=lambda _context: None,
                **dependencies,
            )
        self.assertEqual(result, 74)
        self.assertTrue((self.target / "private" / "failures" / "MSFT.json").is_file())
        retry_events: list[str] = []
        retry_stdout, retry_stderr = self._streams()
        retry = main(
            self._arguments(),
            stdout=retry_stdout,
            stderr=retry_stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(retry_events),
        )
        self.assertEqual(retry, 0)
        self.assertEqual(
            [event for event in retry_events if event.startswith("price:")], ["price:SPY"]
        )

    def test_resume_only_failure_record_fails_closed(self) -> None:
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            completion_callback=lambda _context: None,
            **self._dependencies([], skip_price_symbol="MSFT"),
        )
        self.assertEqual(result, 0)
        (self.target / "private" / "failures" / "MSFT.json").unlink()
        retry_out, retry_err = self._streams()
        retry = main(
            self._arguments(),
            stdout=retry_out,
            stderr=retry_err,
            completion_callback=lambda _context: None,
            **self._dependencies([]),
        )
        self.assertEqual(retry, 75)
        self._assert_error(retry, retry_out, retry_err, code="conflict")

    def test_failure_overlap_and_out_of_roster_records_fail_before_price_requests(self) -> None:
        stdout, stderr = self._streams()
        result = main(
            self._arguments(),
            stdout=stdout,
            stderr=stderr,
            completion_callback=lambda _context: None,
            **self._dependencies([], skip_price_symbol="MSFT"),
        )
        self.assertEqual(result, 0)
        resume_path = self.target / "private" / "resume.json"
        resume = json.loads(resume_path.read_text())
        resume["completed_symbols"].append("MSFT")
        resume["completed_symbols"].sort()
        resume_path.write_text(json.dumps(resume, separators=(",", ":")))
        resume_path.chmod(0o600)
        events: list[str] = []
        retry_out, retry_err = self._streams()
        retry = main(
            self._arguments(),
            stdout=retry_out,
            stderr=retry_err,
            completion_callback=lambda _context: None,
            **self._dependencies(events),
        )
        self.assertEqual(retry, 75)
        self.assertFalse(any(event.startswith("price:") for event in events))

    def test_exact_legacy_euv_transition_journals_without_requesting_euv(self) -> None:
        from quant_data.operations import stage10_backfill

        completed = tuple(f"A{number:03d}" for number in range(200))
        roster = (*completed, "EUV")
        layout = _new_layout(self.target, scope=self.scope, registry=self.registry)
        layout.failures.rmdir()
        legacy = {
            "completed_symbols": list(completed),
            "contract": "quant_data.stage10_backfill_resume",
            "registry_source_sha256": self.registry.source_sha256,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
            "universes_published": True,
            "version": "1.0.0",
        }
        layout.resume.write_text(json.dumps(legacy, separators=(",", ":")))
        layout.resume.chmod(0o600)
        events: list[str] = []
        stdout, stderr = self._streams()
        with mock.patch.object(
            stage10_backfill, "_legacy_euv_has_no_published_history", return_value=True
        ):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                completion_callback=lambda _context: None,
                **self._dependencies(events, roster=roster),
            )
        self.assertEqual(result, 0)
        self.assertNotIn("price:EUV", events)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["failed_tickers"], ["EUV"])
        receipt = json.loads((layout.failures / "EUV.json").read_text())
        self.assertEqual(
            receipt["record"],
            {
                "provenance": "stage10_operator_authorized_seed",
                "reason": "operator_authorized_prior_failure",
                "symbol": "EUV",
            },
        )

    def test_mid_universe_failure_refetches_all_and_accepts_unchanged_publications(self) -> None:
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        first_dependencies = self._dependencies(first_events, fail_universe_publish_at=2)
        first_result = main(
            self._arguments(),
            stdout=first_stdout,
            stderr=first_stderr,
            completion_callback=lambda _context: None,
            **first_dependencies,
        )
        self.assertEqual(first_result, 69)
        self._assert_error(first_result, first_stdout, first_stderr, code="unavailable")
        partial_resume = json.loads((self.target / "private" / "resume.json").read_text())
        self.assertFalse(partial_resume["universes_published"])
        self.assertEqual(partial_resume["completed_symbols"], [])
        self.assertEqual(
            [event for event in first_events if event.startswith("publish_universe:")],
            ["publish_universe:0", "publish_universe:1", "publish_universe:2"],
        )

        second_events: list[str] = []
        second_stdout, second_stderr = self._streams()
        second_dependencies = self._dependencies(second_events, universe_outcome="unchanged")
        second_result = main(
            self._arguments(),
            stdout=second_stdout,
            stderr=second_stderr,
            completion_callback=lambda _context: None,
            **second_dependencies,
        )
        self.assertEqual(second_result, 0)
        self.assertEqual(json.loads(second_stdout.getvalue())["universe_request_count"], 3)
        self.assertEqual(
            [event for event in second_events if event.startswith("universe:")],
            [
                "universe:sp500_current",
                "universe:nasdaq100_current",
                "universe:dow30_current",
            ],
        )
        self.assertNotIn("partial_check", second_events)

    def test_resume_write_error_cleans_its_unique_temp_and_retry_succeeds(self) -> None:
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        dependencies = self._dependencies(first_events)
        from quant_data.operations import stage10_backfill

        original_write_all = stage10_backfill._write_all
        calls = 0

        def fail_after_initial(descriptor: int, content: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic resume write failure")
            original_write_all(descriptor, content)

        with mock.patch.object(stage10_backfill, "_write_all", side_effect=fail_after_initial):
            first_result = main(
                self._arguments(),
                stdout=first_stdout,
                stderr=first_stderr,
                completion_callback=lambda _context: None,
                **dependencies,
            )
        self.assertEqual(first_result, 74)
        self._assert_error(first_result, first_stdout, first_stderr, code="local_io")
        self.assertEqual(
            [path.name for path in (self.target / "private").iterdir() if path.name.startswith(".resume.")],
            [],
        )
        retry_events: list[str] = []
        retry_stdout, retry_stderr = self._streams()
        retry_result = main(
            self._arguments(),
            stdout=retry_stdout,
            stderr=retry_stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(retry_events),
        )
        self.assertEqual(retry_result, 0)
        self.assertEqual(retry_stderr.getvalue(), "")

    def test_initial_resume_write_error_removes_only_empty_new_layout_for_retry(self) -> None:
        from quant_data.operations import stage10_backfill

        stdout, stderr = self._streams()
        dependencies = self._dependencies([])
        with mock.patch.object(stage10_backfill, "_write_all", side_effect=OSError("synthetic initial failure")):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                completion_callback=lambda _context: None,
                **dependencies,
            )
        self.assertEqual(result, 74)
        self._assert_error(result, stdout, stderr, code="local_io")
        self.assertFalse(self.target.exists())

        retry_stdout, retry_stderr = self._streams()
        retry_result = main(
            self._arguments(),
            stdout=retry_stdout,
            stderr=retry_stderr,
            completion_callback=lambda _context: None,
            **self._dependencies([]),
        )
        self.assertEqual(retry_result, 0)
        self.assertEqual(retry_stderr.getvalue(), "")

    def test_private_run_lock_rejects_a_second_runner_before_transport(self) -> None:
        layout = _new_layout(self.target, scope=self.scope, registry=self.registry)
        events: list[str] = []
        stdout, stderr = self._streams()
        with _acquire_run_lock(layout):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                completion_callback=lambda _context: None,
                **self._dependencies(events),
            )
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, code="conflict")
        self.assertNotIn("transport", events)
        self.assertFalse(any(event.startswith("universe:") for event in events))

    def test_completed_receipt_reuse_bypasses_initializer_transport_and_callback(self) -> None:
        first_events: list[str] = []
        first_stdout, first_stderr = self._streams()
        first_result = main(
            self._arguments(),
            stdout=first_stdout,
            stderr=first_stderr,
            completion_callback=lambda _context: None,
            **self._dependencies(first_events),
        )
        self.assertEqual(first_result, 0)
        reuse_events: list[str] = []
        reuse_stdout, reuse_stderr = self._streams()
        receipt = _CompletionReceipt(
            target_profile_id=self.scope.target_profile_id,
            scope_manifest_sha256=self.scope.manifest_sha256,
            registry_source_sha256=self.registry.source_sha256,
            roster_symbol_count=3,
            completed_symbol_count=3,
            evidence_sha256="b" * 64,
            candidate_receipt_sha256="c" * 64,
            sha256="d" * 64,
        )
        with mock.patch(
            "quant_data.operations.stage10_backfill._read_completed_receipt",
            return_value=receipt,
        ):
            result = main(
                self._arguments(),
                stdout=reuse_stdout,
                stderr=reuse_stderr,
                completion_callback=lambda _context: (_ for _ in ()).throw(
                    AssertionError("completion must not run")
                ),
                **self._dependencies(reuse_events),
            )
        self.assertEqual(result, 0)
        self.assertEqual(reuse_stderr.getvalue(), "")
        payload = json.loads(reuse_stdout.getvalue())
        self.assertEqual(payload["completion"], "reused")
        self.assertEqual(payload["universe_request_count"], 0)
        self.assertEqual(payload["price_request_count"], 0)
        self.assertNotIn("initialize", reuse_events)
        self.assertNotIn("transport", reuse_events)

    def test_completed_receipt_reuse_revalidates_stores_before_success(self) -> None:
        first_result = main(
            self._arguments(),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            completion_callback=lambda _context: None,
            **self._dependencies([]),
        )
        self.assertEqual(first_result, 0)
        receipt = _CompletionReceipt(
            target_profile_id=self.scope.target_profile_id,
            scope_manifest_sha256=self.scope.manifest_sha256,
            registry_source_sha256=self.registry.source_sha256,
            roster_symbol_count=3,
            completed_symbol_count=3,
            evidence_sha256="b" * 64,
            candidate_receipt_sha256="c" * 64,
            sha256="d" * 64,
        )
        events: list[str] = []
        dependencies = self._dependencies(events)
        dependencies["store_file_validator"] = (
            lambda _store_map, _stores_root: events.append("stores_validated")
        )

        def reject_stale(
            _layout: object,
            _store_map: object,
            _registry: object,
            _scope: object,
        ) -> None:
            events.append("evidence_revalidated")
            raise ConflictError("stale completed stores")

        dependencies["completed_store_validator"] = reject_stale
        stdout, stderr = self._streams()
        with mock.patch(
            "quant_data.operations.stage10_backfill._read_completed_receipt",
            return_value=receipt,
        ):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                completion_callback=lambda _context: None,
                **dependencies,
            )
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, code="conflict")
        self.assertEqual(events[-2:], ["stores_validated", "evidence_revalidated"])
        self.assertNotIn("transport", events)

    def test_materialized_store_guard_rejects_missing_and_hard_linked_files(self) -> None:
        stores = self.root / "guarded-stores"
        stores.mkdir()
        store_map = self._store_map(stores)
        with self.assertRaises(ConflictError):
            _validate_materialized_store_files(store_map, stores)
        for role in ("market", "macro", "company", "news"):
            (stores / f"{role}.sqlite").write_bytes(b"sqlite-placeholder")
        _validate_materialized_store_files(store_map, stores)
        alias = self.root / "external-live-alias.sqlite"
        alias.hardlink_to(stores / "market.sqlite")
        try:
            with self.assertRaises(ConflictError):
                _validate_materialized_store_files(store_map, stores)
        finally:
            alias.unlink()

    def test_reused_store_evidence_requires_current_reconciliation_and_store_hashes(self) -> None:
        reconciliation = {"contract": "stage10-reconciliation", "sha256": "1" * 64}
        health = {"contract": "store-health", "stores": []}
        health_sha256 = __import__("hashlib").sha256(
            json.dumps(health, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        evidence = {
            "reconciliation": reconciliation,
            "source_mutation_after_sha256": "2" * 64,
            "source_health_sha256": health_sha256,
            "source_logical_manifest_sha256": "3" * 64,
        }
        candidate = SimpleNamespace(evidence=evidence)
        actual_reconciliation = SimpleNamespace(
            to_primitive=lambda: dict(reconciliation)
        )
        actual_health = SimpleNamespace(to_primitive=lambda: dict(health))
        store_map = self._store_map(self.root / "evidence-stores")
        with (
            mock.patch(
                "quant_data.operations.stage10_backfill._read_candidate_receipt",
                return_value=candidate,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill._read_resume",
                return_value=SimpleNamespace(legacy=False, failed_records=()),
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.mutation_fingerprint",
                side_effect=({"sha256": "2" * 64}, {"sha256": "2" * 64}),
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.reconcile_stage10_market",
                return_value=actual_reconciliation,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.all_store_health",
                return_value=actual_health,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.logical_manifest",
                return_value={"sha256": "3" * 64},
            ),
        ):
            _validate_reused_store_evidence(
                SimpleNamespace(), store_map, self.registry, self.scope
            )

        stale = SimpleNamespace(evidence={**evidence, "source_logical_manifest_sha256": "4" * 64})
        with (
            mock.patch(
                "quant_data.operations.stage10_backfill._read_candidate_receipt",
                return_value=stale,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill._read_resume",
                return_value=SimpleNamespace(legacy=False, failed_records=()),
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.mutation_fingerprint",
                side_effect=({"sha256": "2" * 64}, {"sha256": "2" * 64}),
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.reconcile_stage10_market",
                return_value=actual_reconciliation,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.all_store_health",
                return_value=actual_health,
            ),
            mock.patch(
                "quant_data.operations.stage10_backfill.logical_manifest",
                return_value={"sha256": "3" * 64},
            ),
        ):
            with self.assertRaises(ConflictError):
                _validate_reused_store_evidence(
                    SimpleNamespace(), store_map, self.registry, self.scope
                )

    def test_default_completion_writes_receipt_after_private_backup_restore_and_candidate(self) -> None:
        from quant_data.operations import stage10_backfill

        layout = _new_layout(self.target, scope=self.scope, registry=self.registry)
        store_map = self._store_map(layout.stores)
        context = stage10_backfill.Stage10CompletionContext(
            store_map=store_map,
            registry=self.registry,
            scope=self.scope,
            private_root=layout.private,
            completed_symbols=("AAPL", "MSFT", "SPY"),
            roster_symbol_count=3,
            resumed=False,
            failure_manifest_sha256=stage10_backfill._failure_manifest_sha256(()),
        )
        calls: list[str] = []
        case = self

        def backup(source: object, registry: object, *, target_root: Path) -> object:
            self.assertIs(source, store_map)
            self.assertIs(registry, self.registry)
            self.assertEqual(target_root, layout.backup)
            target_root.mkdir(mode=0o700)
            calls.append("backup")
            return SimpleNamespace()

        def restore(cohort: object, registry: object, *, target_root: Path) -> object:
            self.assertIsInstance(cohort, SimpleNamespace)
            self.assertIs(registry, self.registry)
            self.assertEqual(target_root, layout.restored)
            target_root.mkdir(mode=0o700)
            calls.append("restore")
            return SimpleNamespace(store_map=self._store_map(target_root))

        def evidence(*args: object, **kwargs: object) -> object:
            case.assertEqual(args[:2], (store_map, case._store_map(layout.restored)))
            case.assertEqual(
                kwargs, {"replay_unchanged": True, "failed_records": ()}
            )
            calls.append("evidence")
            return object()

        class FakeCandidate:
            evidence_sha256 = "b" * 64
            receipt_sha256 = "c" * 64

        class FakeState:
            def __init__(self, root: Path) -> None:
                self.root = root
                root.mkdir(mode=0o700)

            def publish(self, evidence: object, **kwargs: object) -> FakeCandidate:
                self_outer = self
                case.assertIsNotNone(self_outer.root)
                case.assertIsNotNone(evidence)
                case.assertEqual(kwargs["code_revision"], case.registry.source_sha256)
                calls.append("candidate")
                return FakeCandidate()

        with (
            mock.patch.object(stage10_backfill, "PrivateStage10CandidateState", FakeState),
            mock.patch.object(stage10_backfill, "Stage10CandidateReceipt", FakeCandidate),
        ):
            completion = stage10_backfill._run_default_completion(
                context,
                layout,
                now=lambda: "2026-08-12T00:00:00.000000Z",
                backup=backup,
                restore=restore,
                evidence_builder=evidence,
                candidate_state_factory=FakeState,
            )
        self.assertEqual(calls, ["backup", "restore", "evidence", "candidate"])
        self.assertEqual(completion.completed_symbol_count, 3)
        self.assertTrue(layout.completion.is_file())
        self.assertEqual(stat.S_IMODE(layout.completion.stat().st_mode), 0o600)
        receipt = json.loads(layout.completion.read_text())
        self.assertEqual(receipt["evidence_sha256"], "b" * 64)
        self.assertEqual(receipt["candidate_receipt_sha256"], "c" * 64)

    def test_preflight_missing_key_creates_nothing_and_never_contacts_network(self) -> None:
        stdout, stderr = self._streams()
        events: list[str] = []

        def registry_loader(*_args: object, **_kwargs: object) -> object:
            events.append("registry")
            raise AssertionError("registry must not load before credential preflight")

        with (
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
            mock.patch.object(http.client, "HTTPSConnection", side_effect=AssertionError("network")),
        ):
            result = main(
                self._arguments(),
                stdout=stdout,
                stderr=stderr,
                environment={},
                registry_loader=registry_loader,
                approved_project_root=self.project,
                approved_target_root=self.target,
            )
        self.assertEqual(result, 78)
        self._assert_error(result, stdout, stderr, code="invalid_configuration")
        self.assertEqual(events, [])
        self.assertFalse(self.target.exists())

    def test_malformed_key_creates_nothing_and_never_contacts_network(self) -> None:
        stdout, stderr = self._streams()
        events: list[str] = []
        dependencies = self._dependencies(events)
        dependencies["environment"] = {"FMP_API_KEY": "invalid key"}
        result = main(self._arguments(), stdout=stdout, stderr=stderr, **dependencies)
        self.assertEqual(result, 78)
        self._assert_error(result, stdout, stderr, code="invalid_configuration")
        self.assertEqual(events, [])
        self.assertFalse(self.target.exists())

    def test_symlink_target_fails_before_credential_or_transport(self) -> None:
        linked_target = self.root / "linked-stage10-target"
        linked_target.symlink_to(self.root / "elsewhere")
        arguments = ["--project-root", str(self.project), "--target-root", str(linked_target)]
        stdout, stderr = self._streams()
        events: list[str] = []
        result = main(
            arguments,
            stdout=stdout,
            stderr=stderr,
            **self._dependencies(events),
        )
        self.assertEqual(result, 64)
        self._assert_error(result, stdout, stderr, code="invalid_arguments")
        self.assertEqual(events, [])

    def test_default_completion_is_used_when_callback_is_not_injected(self) -> None:
        stdout, stderr = self._streams()
        events: list[str] = []
        dependencies = self._dependencies(events)
        completed: list[object] = []

        def complete(context: object, _layout: object, *, now: object) -> object:
            self.assertIsNotNone(now)
            completed.append(context)
            return object()

        with mock.patch(
            "quant_data.operations.stage10_backfill._run_default_completion",
            side_effect=complete,
        ):
            result = main(self._arguments(), stdout=stdout, stderr=stderr, **dependencies)
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["completion"], "completed")
        self.assertEqual(len(completed), 1)


    def test_unknown_existing_target_fails_closed_before_transport(self) -> None:
        self.target.mkdir(mode=0o700)
        (self.target / "unreviewed.txt").write_text("no")
        events: list[str] = []
        stdout, stderr = self._streams()
        dependencies = self._dependencies(events)
        result = main(self._arguments(), stdout=stdout, stderr=stderr, completion_callback=lambda _context: None, **dependencies)
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, code="conflict")
        self.assertNotIn("transport", events)
        self.assertFalse(any(event.startswith("universe:") for event in events))

    def test_wrong_target_mode_fails_closed_before_transport(self) -> None:
        self.target.mkdir(mode=0o700)
        self.target.chmod(0o755)
        events: list[str] = []
        stdout, stderr = self._streams()
        dependencies = self._dependencies(events)
        result = main(self._arguments(), stdout=stdout, stderr=stderr, completion_callback=lambda _context: None, **dependencies)
        self.assertEqual(result, 75)
        self._assert_error(result, stdout, stderr, code="conflict")
        self.assertNotIn("transport", events)

    def test_store_map_outside_target_fails_before_initializer_or_transport(self) -> None:
        events: list[str] = []
        stdout, stderr = self._streams()
        dependencies = self._dependencies(events)
        outside = self.root / "outside"
        dependencies["store_map_factory"] = lambda _root: self._store_map(outside)
        result = main(self._arguments(), stdout=stdout, stderr=stderr, completion_callback=lambda _context: None, **dependencies)
        self.assertEqual(result, 70)
        self._assert_error(result, stdout, stderr, code="contract_failure")
        self.assertNotIn("initialize", events)
        self.assertNotIn("transport", events)

    def test_default_roster_reader_is_read_only_and_requires_one_complete_snapshot_per_universe(self) -> None:
        stores = self.root / "roster-stores"
        stores.mkdir(mode=0o700)
        store_map = self._store_map(stores)
        connection = sqlite3.connect(store_map.market)
        try:
            connection.executescript(
                """
                CREATE TABLE store_metadata (
                    singleton INTEGER,
                    store_role TEXT,
                    contract_version TEXT
                );
                CREATE TABLE stage10_scope_snapshots (
                    scope_snapshot_id TEXT,
                    scope_manifest_sha256 TEXT,
                    target_profile_id TEXT,
                    provider TEXT
                );
                CREATE TABLE stage10_universe_snapshots (
                    universe_snapshot_id TEXT,
                    universe_id TEXT,
                    scope_snapshot_id TEXT,
                    completeness TEXT
                );
                CREATE TABLE stage10_universe_snapshot_members (
                    universe_snapshot_id TEXT,
                    instrument_id TEXT
                );
                CREATE TABLE stage10_instruments (
                    instrument_id TEXT,
                    provider_symbol TEXT,
                    asset_type TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO store_metadata VALUES (1, 'market', 'synthetic-test')"
            )
            connection.execute(
                "INSERT INTO stage10_scope_snapshots VALUES (?, ?, ?, 'fmp')",
                ("scope", self.scope.manifest_sha256, self.scope.target_profile_id),
            )
            instruments = (
                ("aapl", "AAPL", "equity"),
                ("dram", "DRAM", "etf"),
                ("gspc", "^GSPC", "index"),
            )
            connection.executemany("INSERT INTO stage10_instruments VALUES (?, ?, ?)", instruments)
            for universe_id in _UNIVERSE_IDS:
                snapshot_id = "snapshot-" + universe_id
                instrument_id = (
                    "dram" if universe_id == "curated_etfs" else "gspc"
                    if universe_id == "major_indexes" else "aapl"
                )
                connection.execute(
                    "INSERT INTO stage10_universe_snapshots VALUES (?, ?, 'scope', 'complete')",
                    (snapshot_id, universe_id),
                )
                connection.execute(
                    "INSERT INTO stage10_universe_snapshot_members VALUES (?, ?)",
                    (snapshot_id, instrument_id),
                )
            connection.commit()
        finally:
            connection.close()
        before = store_map.market.read_bytes()
        roster = _default_roster_reader(store_map, self.scope)
        after = store_map.market.read_bytes()
        self.assertEqual(roster, ("AAPL", "DRAM", "^GSPC"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
