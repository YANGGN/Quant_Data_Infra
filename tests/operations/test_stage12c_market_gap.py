from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from quant_data.errors import ConflictError, StoreUnavailableError, ValidationError
from quant_data.market.stage12c_incremental import (
    Stage12CIncrementalCollector,
)
from quant_data.market.stage12_scope import load_stage12_market_v1_scope
from quant_data.market.stage12b_scope import load_stage12b_incremental_market_v1_scope
from quant_data.market.stage12c_scope import load_stage12c_market_gap_v1_scope
from quant_data.migrations import initialize_all
from quant_data.operations import stage12c_market_gap as operations
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import StoreMap, StoreRole, writer_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TIME = "2026-08-15T00:00:00Z"
_SIDECAR_TIME = "2026-08-15T00:00:00.000000Z"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "stage10_daily_price_captures",
                "stage10_daily_price_versions",
                "stage10_daily_prices",
            )
        )


def _daily_body(symbol: str, *, dates: tuple[str, ...] = operations.STAGE12C_EXPECTED_SESSIONS) -> bytes:
    rows = []
    for offset, trade_date in enumerate(dates):
        base = 100 + offset
        rows.append(
            {
                "symbol": symbol,
                "date": trade_date,
                "open": base,
                "high": base + 2,
                "low": base - 1,
                "close": base + 1,
                "volume": 1000 + offset,
                "change": 1,
                "changePercent": 1,
                "vwap": base,
            }
        )
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


@dataclass
class _Clock:
    now_value: datetime = datetime(2026, 8, 15, tzinfo=timezone.utc)
    monotonic_value: float = 0.0

    def now(self) -> datetime:
        return self.now_value

    def monotonic(self) -> float:
        return self.monotonic_value

    def sleep(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.now_value += timedelta(seconds=seconds)


class _Transport:
    def __init__(self, responses: dict[str, operations.Stage12CTransportResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.headers: list[dict[str, str]] = []
        self.lock_active: callable[[], bool] | None = None

    def get(
        self,
        *,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> operations.Stage12CTransportResponse:
        if self.lock_active is not None and self.lock_active():
            raise AssertionError("transport called while database write lock is active")
        self.calls.append((path, dict(query)))
        self.headers.append(dict(headers))
        if path != operations.STAGE12C_ENDPOINT_PATH:
            raise AssertionError("unexpected endpoint")
        if set(query) != {"symbol", "from", "to"}:
            raise AssertionError("unexpected query keys")
        if set(headers) != {"apikey"}:
            raise AssertionError("unexpected credential header")
        if timeout_seconds != 45 or max_bytes != 65_536:
            raise AssertionError("unexpected transport bound")
        return self._responses[query["symbol"]]


class _TrackingEnvironment(dict[str, str]):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.reads = 0

    def get(self, key: str, default: object = None) -> object:
        self.reads += 1
        return super().get(key, default)


class Stage12CMarketGapOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        (self.root / "data").mkdir(parents=True)
        self.stores = StoreMap.four_explicit(
            market=self.root / "data" / "market.sqlite",
            macro=self.root / "data" / "macro.sqlite",
            company=self.root / "data" / "company.sqlite",
            news=self.root / "data" / "news.sqlite",
        )
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        initialize_all(self.stores, self.registry, applied_at=_TIME)
        self.stage12a = load_stage12_market_v1_scope(
            PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
        )
        self.stage12b = load_stage12b_incremental_market_v1_scope(
            PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
        )
        self.scope = load_stage12c_market_gap_v1_scope(
            PROJECT_ROOT / "config" / "stage12c_market_gap_v1_scope.json"
        )
        self._seed_frozen_instruments()
        self.source = Path(self.temporary.name) / "retained-baseline.sqlite"
        shutil.copy2(self.stores.market, self.source)
        self.baseline = operations.Stage12CBaselineExpectation(
            sha256=_sha256(self.stores.market),
            current_price_rows=0,
            immutable_price_versions=0,
            daily_price_captures=0,
        )
        self.collector = Stage12CIncrementalCollector(
            project_root=self.root,
            market_store=self.stores.market,
            scope=self.scope,
            stage12a_scope=self.stage12a,
            stage12b_scope=self.stage12b,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_frozen_instruments(self) -> None:
        with writer_connection(self.stores, StoreRole.MARKET) as connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs(
                    run_id, dataset_id, semantic_identity, command, scope_json,
                    status, started_at, code_version
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    "stage12c_operations_seed",
                    "market.stage10.instruments",
                    "a" * 64,
                    "fixture.seed",
                    "{}",
                    _TIME,
                    "fixture",
                ),
            )
            connection.executemany(
                """
                INSERT INTO stage10_instruments(
                    instrument_id, provider, provider_symbol, asset_type,
                    display_name, exchange_code, currency_segment, first_trade_date,
                    identity_seed_sha256, captured_at, captured_precision, run_id
                ) VALUES (?, 'fmp', ?, ?, NULL, NULL, 'provider_native', NULL,
                          ?, ?, 'datetime', ?)
                """,
                [
                    (
                        f"stage12c-{item.symbol}",
                        item.symbol,
                        item.asset_type,
                        hashlib.sha256(item.symbol.encode("utf-8")).hexdigest(),
                        _TIME,
                        "stage12c_operations_seed",
                    )
                    for item in self.stage12a.roster
                ],
            )

    def _prepare_aapl_spool(
        self,
        response: operations.Stage12CTransportResponse,
    ) -> tuple[object, object, object]:
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        layout = operations._open_or_create_layout(self.root)
        operations._ensure_plan_receipt(layout, plan)
        operations._ensure_baseline(
            layout=layout,
            plan=plan,
            target=self.stores.market,
            source=self.source,
            expectation=self.baseline,
        )
        unit = plan.units[0]
        _, intent_sha256 = operations._write_intent(
            layout,
            unit,
            issued_at=_SIDECAR_TIME,
        )
        operations._write_response_spool(
            layout,
            unit,
            intent_sha256=intent_sha256,
            response=response,
            captured_at=_SIDECAR_TIME,
        )
        return plan, layout, unit

    def _runner(
        self,
        transport: _Transport,
        *,
        environment: _TrackingEnvironment | None = None,
        static_preflight: callable | None = None,
        clock: _Clock | None = None,
    ) -> operations.Stage12CMarketGapRunner:
        active_clock = _Clock() if clock is None else clock
        return operations.Stage12CMarketGapRunner(
            project_root=self.root,
            retained_source=self.source,
            scope=self.scope,
            stage12a_scope=self.stage12a,
            stage12b_scope=self.stage12b,
            collector=self.collector,
            transport=transport,
            environment=environment if environment is not None else _TrackingEnvironment({"FMP_API_KEY": "fixture-key"}),
            static_preflight=static_preflight or (lambda project: {"gate": "fixture", "revision": "2.14.0"}),
            baseline=self.baseline,
            monotonic=active_clock.monotonic,
            sleeper=active_clock.sleep,
            utcnow=active_clock.now,
        )

    def _write_dotenv(self, source: str, *, mode: int = 0o600) -> Path:
        dotenv = self.root / ".env"
        dotenv.write_text(source, encoding="utf-8")
        dotenv.chmod(mode)
        return dotenv

    def _prepare_axjo_402_spool(self) -> tuple[object, object, object, tuple[int, int, int], bytes]:
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        unit = plan.units[614]
        self.assertEqual(
            (plan.sha256, unit.ordinal, unit.symbol, unit.from_date, unit.to_date, unit.identifier),
            (
                operations._STAGE12C_AXJO_402_PLAN_SHA256,
                615,
                "^AXJO",
                "2026-08-13",
                "2026-08-14",
                "stage12c-97582963f362dc4a7c8f41bd579f0975",
            ),
        )
        layout = operations._open_or_create_layout(self.root)
        operations._ensure_plan_receipt(layout, plan)
        _, target_identity = operations._ensure_baseline(
            layout=layout,
            plan=plan,
            target=self.stores.market,
            source=self.source,
            expectation=self.baseline,
        )
        _, intent_sha256 = operations._write_intent(
            layout,
            unit,
            issued_at=_SIDECAR_TIME,
        )
        body = b"x" * 215
        operations._write_response_spool(
            layout,
            unit,
            intent_sha256=intent_sha256,
            response=operations.Stage12CTransportResponse(
                status=402,
                media_type="application/json; charset=utf-8",
                body=body,
                elapsed_seconds=Decimal("0.1"),
            ),
            captured_at=_SIDECAR_TIME,
        )
        state = operations._load_journal(layout, plan)[unit.identifier]
        self.assertIsNotNone(state.intent_sha256)
        self.assertIsNotNone(state.response)
        return plan, layout, state, target_identity, body

    def _synthetic_axjo_402_patches(
        self,
        plan: object,
        state: object,
        body: bytes,
    ) -> dict[str, object]:
        return {
            "_STAGE12C_AXJO_402_PLAN_SHA256": plan.sha256,
            "_STAGE12C_AXJO_402_INTENT_SHA256": state.intent_sha256,
            "_STAGE12C_AXJO_402_RESPONSE_SHA256": hashlib.sha256(body).hexdigest(),
            "_STAGE12C_AXJO_402_SPOOL_SHA256": state.response["spool_sha256"],
        }

    def _prepare_continuation_402_spool(
        self,
        ordinal: int,
    ) -> tuple[object, object, object, tuple[int, int, int], bytes]:
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        unit = plan.units[ordinal - 1]
        layout = operations._open_or_create_layout(self.root)
        operations._ensure_plan_receipt(layout, plan)
        _, target_identity = operations._ensure_baseline(
            layout=layout,
            plan=plan,
            target=self.stores.market,
            source=self.source,
            expectation=self.baseline,
        )
        intent, intent_sha256 = operations._write_intent(
            layout,
            unit,
            issued_at=_SIDECAR_TIME,
        )
        body = b"x" * 215
        operations._write_response_spool(
            layout,
            unit,
            intent_sha256=intent_sha256,
            response=operations.Stage12CTransportResponse(
                status=402,
                media_type="application/json; charset=utf-8",
                body=body,
                elapsed_seconds=Decimal("0.1"),
            ),
            captured_at=_SIDECAR_TIME,
        )
        loaded_intent = operations._read_intent(layout, unit)
        self.assertIsNotNone(loaded_intent)
        self.assertEqual(loaded_intent[1], intent_sha256)
        loaded_spool = operations._read_response_spool(
            layout,
            unit,
            intent_sha256=intent_sha256,
        )
        self.assertIsNotNone(loaded_spool)
        response, loaded_body = loaded_spool
        return (
            plan,
            layout,
            operations._JournalState(
                unit=unit,
                intent=intent,
                intent_sha256=intent_sha256,
                body=loaded_body,
                response=response,
                result=None,
                resume=None,
            ),
            target_identity,
            body,
        )

    def _assert_no_request_journal_files(self) -> None:
        layout = operations._open_or_create_layout(self.root)
        for directory in (
            layout.intents,
            layout.bodies,
            layout.responses,
            layout.results,
            layout.resume,
        ):
            self.assertEqual(tuple(directory.iterdir()), ())
        self.assertFalse(layout.completion.exists() or layout.completion.is_symlink())

    def _complete_transport(self) -> _Transport:
        return _Transport(
            {
                item.symbol: operations.Stage12CTransportResponse(
                    status=200,
                    media_type="application/json",
                    body=_daily_body(item.symbol),
                    elapsed_seconds=Decimal("0.1"),
                )
                for item in self.stage12a.roster
            }
        )

    def test_full_fixture_run_is_durable_paced_source_neutral_and_resumable(self) -> None:
        transport = self._complete_transport()
        clock = _Clock()
        lock_state = {"active": False}
        original_lock = __import__(
            "quant_data.market.stage12c_incremental", fromlist=["StoreWriteLock"]
        ).StoreWriteLock

        class GuardLock:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self._inner = original_lock(*args, **kwargs)

            def __enter__(self) -> object:
                lock_state["active"] = True
                return self._inner.__enter__()

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
                try:
                    return self._inner.__exit__(exc_type, exc, traceback)
                finally:
                    lock_state["active"] = False

        transport.lock_active = lambda: lock_state["active"]
        source_before = _sha256(self.source)
        with mock.patch(
            "quant_data.market.stage12c_incremental.StoreWriteLock",
            GuardLock,
        ):
            report = self._runner(transport, clock=clock).run()
        self.assertEqual(report.completion, "complete")
        self.assertEqual(report.requests_issued, 629)
        self.assertEqual(report.published_unit_count, 629)
        self.assertEqual(report.terminal_noncoverage_count, 0)
        self.assertEqual(len(transport.calls), 629)
        self.assertEqual(transport.calls[0][1]["symbol"], "AAPL")
        self.assertEqual(
            tuple(call[1]["symbol"] for call in transport.calls[1:]),
            tuple(sorted(item.symbol for item in self.stage12a.roster if item.symbol != "AAPL")),
        )
        self.assertGreaterEqual(clock.monotonic_value, 628)
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_counts(self.source), (0, 0, 0))
        self.assertEqual(_counts(self.stores.market), (629, 1258, 1258))

        private = self.root / operations.APPROVED_PRIVATE_RELATIVE_ROOT
        self.assertEqual((private.stat().st_mode & 0o777), 0o700)
        for child in ("intents", "bodies", "responses", "results", "resume"):
            self.assertEqual((private / child).stat().st_mode & 0o777, 0o700)
        all_sidecars = tuple(path for path in private.rglob("*") if path.is_file())
        self.assertTrue(all_sidecars)
        self.assertTrue(all((path.stat().st_mode & 0o777) == 0o600 for path in all_sidecars))
        serialized = b"".join(path.read_bytes() for path in all_sidecars)
        self.assertNotIn(b"fixture-key", serialized)

        replay_environment = _TrackingEnvironment({})
        with mock.patch(
            "quant_data.operations.stage12c_market_gap.read_project_credential",
            side_effect=AssertionError("credential read during complete replay"),
        ) as read_credential:
            replay = self._runner(
                transport,
                environment=replay_environment,
                clock=clock,
            ).run()
        read_credential.assert_not_called()
        self.assertEqual(replay_environment.reads, 0)
        self.assertEqual(replay.requests_issued, 0)
        self.assertEqual(len(transport.calls), 629)

    def test_static_preflight_and_ambiguous_intent_stop_before_credential_or_transport(self) -> None:
        transport = self._complete_transport()
        environment = _TrackingEnvironment({"FMP_API_KEY": "fixture-key"})
        (self.root / ".env").write_text("FMP_API_KEY=dotenv-fixture-key\n", encoding="utf-8")
        runner = self._runner(
            transport,
            environment=environment,
            static_preflight=lambda project: (_ for _ in ()).throw(ConflictError("fixture gate")),
        )
        with mock.patch(
            "quant_data.operations.stage12c_market_gap.read_project_credential",
            side_effect=AssertionError("credential read before static preflight"),
        ) as read_credential:
            with self.assertRaises(ConflictError):
                runner.run()
        read_credential.assert_not_called()
        self.assertEqual(environment.reads, 0)
        self.assertEqual(transport.calls, [])

        # A durable intent with no complete response spool is ambiguous, and is
        # never sent again even if a credential is available.
        source_before = _sha256(self.source)
        target_before = _counts(self.stores.market)
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        layout = operations._open_or_create_layout(self.root)
        operations._ensure_plan_receipt(layout, plan)
        operations._ensure_baseline(
            layout=layout,
            plan=plan,
            target=self.stores.market,
            source=self.source,
            expectation=self.baseline,
        )
        operations._write_intent(layout, plan.units[0], issued_at=_SIDECAR_TIME)
        environment = _TrackingEnvironment({"FMP_API_KEY": "fixture-key"})
        static_calls: list[Path] = []
        runner = self._runner(
            transport,
            environment=environment,
            static_preflight=lambda project: static_calls.append(Path(project))
            or {"gate": "fixture"},
        )
        with self.assertRaises(ConflictError):
            runner.run()
        self.assertEqual(static_calls, [])
        self.assertEqual(environment.reads, 0)
        self.assertEqual(transport.calls, [])
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_counts(self.stores.market), target_before)

    def test_complete_durable_spool_replays_locally_before_credential_or_transport(self) -> None:
        response = operations.Stage12CTransportResponse(
            status=200,
            media_type="application/json",
            body=_daily_body("AAPL"),
            elapsed_seconds=Decimal("0.1"),
        )
        _, layout, unit = self._prepare_aapl_spool(response)
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        transport = self._complete_transport()
        environment = _TrackingEnvironment({"OTHER": "fixture"})
        static_calls: list[Path] = []
        runner = self._runner(
            transport,
            environment=environment,
            static_preflight=lambda project: static_calls.append(Path(project))
            or {"gate": "fixture"},
        )

        with self.assertRaises(ValidationError):
            runner.run()
        self.assertEqual(static_calls, [self.root])
        self.assertEqual(environment.reads, 1)
        self.assertEqual(transport.calls, [])
        self.assertEqual(_sha256(self.source), source_before)
        self.assertNotEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (1, 2, 2))
        result = json.loads(
            (layout.results / f"{unit.filename_stem}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["captured_at"], _SIDECAR_TIME)
        self.assertEqual(result["publication"]["outcome"], "published")

    def test_axjo_402_authorization_sha_constants_are_full_lowercase_hex(self) -> None:
        for label, value in (
            ("plan", operations._STAGE12C_AXJO_402_PLAN_SHA256),
            ("intent", operations._STAGE12C_AXJO_402_INTENT_SHA256),
            ("response", operations._STAGE12C_AXJO_402_RESPONSE_SHA256),
            ("spool", operations._STAGE12C_AXJO_402_SPOOL_SHA256),
        ):
            with self.subTest(label=label):
                self.assertRegex(value, r"\A[0-9a-f]{64}\Z")

    def test_exact_axjo_402_spool_replays_locally_and_next_ordinal_can_continue(self) -> None:
        plan, layout, state, target_identity, body = self._prepare_axjo_402_spool()
        self.assertEqual(
            (
                operations._STAGE12C_AXJO_402_AUTHORIZATION_ID,
                operations._STAGE12C_AXJO_402_PLAN_SHA256,
                operations._STAGE12C_AXJO_402_UNIT_IDENTIFIER,
                operations._STAGE12C_AXJO_402_INTENT_SHA256,
                operations._STAGE12C_AXJO_402_RESPONSE_SHA256,
                operations._STAGE12C_AXJO_402_SPOOL_SHA256,
            ),
            (
                "user_authorized_stage12c_axjo_402_entitlement_unavailable_v1",
                "5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1",
                "stage12c-97582963f362dc4a7c8f41bd579f0975",
                "d4638e71d1277279ab5e7d8d8208839fc7806e70400a9fd90f6a6713d5770137",
                "38e6a6ea2ed189c5d4cab610c93eefc962b31fffdae06dd65390b90d7c0cff7c",
                "e65c1a9f7cb51ebfcd5702145836612d8d1e5d02de4c79e79ef2849d3e84e5de",
            ),
        )
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        patches = self._synthetic_axjo_402_patches(plan, state, body)
        with (
            mock.patch.multiple(operations, **patches),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.read_project_credential",
                side_effect=AssertionError("credential read during local replay"),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.Stage12CLiveRequest",
                side_effect=AssertionError("publication preparation during terminal replay"),
            ),
        ):
            recovered = operations._replay_spooled_unit(
                layout=layout,
                state=state,
                collector=self.collector,
                max_seconds=self.scope.collector.max_seconds,
                target_identity=target_identity,
            )
            repeated = operations._replay_spooled_unit(
                layout=layout,
                state=recovered,
                collector=self.collector,
                max_seconds=self.scope.collector.max_seconds,
                target_identity=target_identity,
            )

        self.assertEqual(recovered.result, repeated.result)
        self.assertIsNotNone(recovered.result)
        self.assertEqual(recovered.result["outcome"], "noncoverage_http_402_authorized")
        self.assertEqual(recovered.result["row_count"], 0)
        self.assertIsNone(recovered.result["publication"])
        self.assertTrue(
            operations._private_file(layout, "results", state.unit, ".json").exists()
        )
        self.assertTrue(
            operations._private_file(layout, "resume", state.unit, ".json").exists()
        )
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (0, 0, 0))
        closed, published, terminal = operations._closed_result_counts(
            {recovered.unit.identifier: recovered}
        )
        self.assertEqual((closed, published), (1, 0))
        self.assertEqual(
            terminal,
            (
                {
                    "http_status": 402,
                    "ordinal": 615,
                    "outcome": "noncoverage_http_402_authorized",
                    "symbol": "^AXJO",
                },
            ),
        )

        next_unit = plan.units[615]
        self.assertEqual((next_unit.ordinal, next_unit.symbol), (616, "^DJI"))
        transport = _Transport(
            {
                next_unit.symbol: operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        clock = _Clock()
        runner = self._runner(transport, clock=clock)
        with self.assertRaises(StoreUnavailableError):
            runner._issue_one(
                layout=layout,
                unit=next_unit,
                target_identity=target_identity,
                pacer=operations._RequestPacer(
                    monotonic=clock.monotonic,
                    sleeper=clock.sleep,
                ),
                api_key="continuation-fixture-key",
            )
        self.assertEqual([query["symbol"] for _, query in transport.calls], ["^DJI"])
        serialized = b"".join(
            path.read_bytes()
            for path in (self.root / operations.APPROVED_PRIVATE_RELATIVE_ROOT).rglob("*")
            if path.is_file()
        )
        self.assertNotIn(b"continuation-fixture-key", serialized)
        self.assertEqual(_sha256(self.stores.market), target_before)

    def test_sealed_402_continuation_replays_locally_and_starts_at_618(self) -> None:
        plan, layout, state_617, target_identity, body = self._prepare_continuation_402_spool(617)
        _, layout_629, state_629, target_629, body_629 = self._prepare_continuation_402_spool(629)
        self.assertEqual(layout_629, layout)
        self.assertEqual(target_629, target_identity)
        self.assertEqual(body_629, body)
        self.assertEqual(plan.sha256, operations._STAGE12C_AXJO_402_PLAN_SHA256)
        self.assertEqual((state_617.unit.ordinal, state_617.unit.symbol), (617, "^FCHI"))
        self.assertEqual(state_629.unit.ordinal, 629)
        self.assertNotEqual(
            state_617.response["spool_sha256"],
            state_629.response["spool_sha256"],
        )
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        with (
            mock.patch.object(
                operations,
                "_STAGE12C_AXJO_402_RESPONSE_SHA256",
                hashlib.sha256(body).hexdigest(),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.read_project_credential",
                side_effect=AssertionError("credential read during local continuation replay"),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.Stage12CLiveRequest",
                side_effect=AssertionError("publication preparation during terminal continuation replay"),
            ),
        ):
            recovered_617 = operations._replay_spooled_unit(
                layout=layout,
                state=state_617,
                collector=self.collector,
                max_seconds=self.scope.collector.max_seconds,
                target_identity=target_identity,
            )
            repeated_617 = operations._replay_spooled_unit(
                layout=layout,
                state=recovered_617,
                collector=self.collector,
                max_seconds=self.scope.collector.max_seconds,
                target_identity=target_identity,
            )
            recovered_629 = operations._replay_spooled_unit(
                layout=layout,
                state=state_629,
                collector=self.collector,
                max_seconds=self.scope.collector.max_seconds,
                target_identity=target_identity,
            )

        self.assertEqual(recovered_617.result, repeated_617.result)
        for state in (recovered_617, recovered_629):
            self.assertIsNotNone(state.result)
            self.assertEqual(state.result["outcome"], "noncoverage_http_402_authorized")
            self.assertEqual(state.result["row_count"], 0)
            self.assertIsNone(state.result["publication"])
            self.assertTrue(
                operations._private_file(layout, "results", state.unit, ".json").exists()
            )
            self.assertTrue(
                operations._private_file(layout, "resume", state.unit, ".json").exists()
            )
        closed, published, terminal = operations._closed_result_counts(
            {
                recovered_617.unit.identifier: recovered_617,
                recovered_629.unit.identifier: recovered_629,
            }
        )
        self.assertEqual((closed, published), (2, 0))
        self.assertEqual(tuple(item["ordinal"] for item in terminal), (617, 629))

        terminal_ordinals = {615}
        terminal_ordinals.update(range(617, 630))
        partition_states = {
            unit.identifier: operations._JournalState(
                unit=unit,
                intent=None,
                intent_sha256=None,
                body=None,
                response=None,
                result={
                    "http_status": 402 if unit.ordinal in terminal_ordinals else 200,
                    "outcome": (
                        "noncoverage_http_402_authorized"
                        if unit.ordinal in terminal_ordinals
                        else "published_complete"
                    ),
                },
                resume=None,
            )
            for unit in plan.units
        }
        partition_closed, partition_published, partition_terminal = operations._closed_result_counts(
            partition_states
        )
        self.assertEqual(
            (partition_closed, partition_published, len(partition_terminal)),
            (629, 615, 14),
        )
        self.assertEqual(
            tuple(item["ordinal"] for item in partition_terminal),
            (615,) + tuple(range(617, 630)),
        )

        next_unit = plan.units[617]
        self.assertEqual(next_unit.ordinal, 618)
        transport = _Transport(
            {
                next_unit.symbol: operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        clock = _Clock()
        runner = self._runner(transport, clock=clock)
        with self.assertRaises(StoreUnavailableError):
            runner._issue_one(
                layout=layout,
                unit=next_unit,
                target_identity=target_identity,
                pacer=operations._RequestPacer(
                    monotonic=clock.monotonic,
                    sleeper=clock.sleep,
                ),
                api_key="continuation-fixture-key",
            )
        self.assertEqual([query["symbol"] for _, query in transport.calls], [next_unit.symbol])
        serialized = b"".join(
            path.read_bytes()
            for path in (self.root / operations.APPROVED_PRIVATE_RELATIVE_ROOT).rglob("*")
            if path.is_file()
        )
        self.assertNotIn(b"continuation-fixture-key", serialized)
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (0, 0, 0))

    def test_sealed_402_continuation_boundaries_and_descriptors_remain_strict(self) -> None:
        plan, layout, state_616, target_identity, body_616 = self._prepare_continuation_402_spool(616)
        _, _, state_617, _, body_617 = self._prepare_continuation_402_spool(617)
        _, _, state_629, _, body_629 = self._prepare_continuation_402_spool(629)
        _, _, state_outside, _, body_outside = self._prepare_continuation_402_spool(1)
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        cases = (
            ("ordinal_616_excluded", state_616, state_616.unit, state_616.response, body_616, False),
            ("ordinal_617_allowed", state_617, state_617.unit, state_617.response, body_617, True),
            ("ordinal_629_allowed", state_629, state_629.unit, state_629.response, body_629, True),
            ("ordinal_1_outside", state_outside, state_outside.unit, state_outside.response, body_outside, False),
            (
                "altered_plan",
                state_617,
                replace(state_617.unit, plan_sha256="0" * 64),
                state_617.response,
                body_617,
                False,
            ),
            (
                "altered_unit",
                state_617,
                replace(state_617.unit, symbol="^DJI"),
                state_617.response,
                body_617,
                False,
            ),
            (
                "altered_dates",
                state_617,
                replace(state_617.unit, from_date="2026-08-12"),
                state_617.response,
                body_617,
                False,
            ),
            (
                "status",
                state_617,
                state_617.unit,
                {**state_617.response, "http_status": 401},
                body_617,
                False,
            ),
            (
                "mime",
                state_617,
                state_617.unit,
                {**state_617.response, "content_type": "application/json"},
                body_617,
                False,
            ),
            (
                "redirect",
                state_617,
                state_617.unit,
                {**state_617.response, "redirected": True},
                body_617,
                False,
            ),
            (
                "body",
                state_617,
                state_617.unit,
                state_617.response,
                b"y" * 215,
                False,
            ),
        )
        with (
            mock.patch.object(
                operations,
                "_STAGE12C_AXJO_402_RESPONSE_SHA256",
                hashlib.sha256(body_617).hexdigest(),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.read_project_credential",
                side_effect=AssertionError("credential read during sealed 402 classification"),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.Stage12CLiveRequest",
                side_effect=AssertionError("publication preparation during sealed 402 classification"),
            ),
        ):
            for label, state, unit, spool, body, allowed in cases:
                with self.subTest(label=label):
                    if allowed:
                        admission = operations._classify_spooled_response(
                            unit,
                            spool,
                            body,
                            max_seconds=self.scope.collector.max_seconds,
                        )
                        self.assertEqual(admission.outcome, "noncoverage_http_402_authorized")
                        self.assertEqual(admission.row_count, 0)
                        continue
                    rejected = operations._JournalState(
                        unit=unit,
                        intent=state.intent,
                        intent_sha256=state.intent_sha256,
                        body=body,
                        response=spool,
                        result=None,
                        resume=None,
                    )
                    with self.assertRaises(StoreUnavailableError):
                        operations._replay_spooled_unit(
                            layout=layout,
                            state=rejected,
                            collector=self.collector,
                            max_seconds=self.scope.collector.max_seconds,
                            target_identity=target_identity,
                        )
                    self.assertFalse(
                        operations._private_file(layout, "results", unit, ".json").exists()
                    )
                    self.assertFalse(
                        operations._private_file(layout, "resume", unit, ".json").exists()
                    )
        self.assertEqual(tuple(layout.results.iterdir()), ())
        self.assertEqual(tuple(layout.resume.iterdir()), ())
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (0, 0, 0))

    def test_sealed_402_continuation_requires_valid_complete_bound_spools(self) -> None:
        _, layout, state_intent, _, _ = self._prepare_continuation_402_spool(617)
        _, _, state_digest, _, _ = self._prepare_continuation_402_spool(618)
        _, _, state_body, _, _ = self._prepare_continuation_402_spool(619)
        _, _, state_cross_source, _, _ = self._prepare_continuation_402_spool(620)
        _, _, state_cross_target, _, _ = self._prepare_continuation_402_spool(621)
        _, _, state_incomplete, _, _ = self._prepare_continuation_402_spool(622)
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        metadata_fields = (
            "body_byte_count",
            "captured_at",
            "content_type",
            "elapsed_seconds",
            "http_status",
            "intent_id",
            "intent_sha256",
            "redirected",
            "response_sha256",
        )

        def overwrite_response(state: object, receipt: dict[str, object]) -> None:
            operations._private_file(layout, "responses", state.unit, ".json").write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

        altered_intent = dict(state_intent.response)
        altered_intent["intent_sha256"] = "0" * 64
        altered_intent["spool_sha256"] = operations._sha256_json(
            {key: altered_intent[key] for key in metadata_fields}
        )
        overwrite_response(state_intent, altered_intent)
        with self.assertRaises(ConflictError):
            operations._read_response_spool(
                layout,
                state_intent.unit,
                intent_sha256=state_intent.intent_sha256,
            )

        altered_digest = dict(state_digest.response)
        altered_digest["spool_sha256"] = "0" * 64
        overwrite_response(state_digest, altered_digest)
        with self.assertRaises(ConflictError):
            operations._read_response_spool(
                layout,
                state_digest.unit,
                intent_sha256=state_digest.intent_sha256,
            )

        operations._private_file(layout, "bodies", state_body.unit, ".body").write_bytes(
            b"y" * 215
        )
        with self.assertRaises(ConflictError):
            operations._read_response_spool(
                layout,
                state_body.unit,
                intent_sha256=state_body.intent_sha256,
            )

        cross_bound_response = dict(state_cross_source.response)
        overwrite_response(state_cross_target, cross_bound_response)
        with self.assertRaises(ConflictError):
            operations._read_response_spool(
                layout,
                state_cross_target.unit,
                intent_sha256=state_cross_target.intent_sha256,
            )

        operations._private_file(layout, "bodies", state_incomplete.unit, ".body").unlink()
        with self.assertRaises(ConflictError):
            operations._read_response_spool(
                layout,
                state_incomplete.unit,
                intent_sha256=state_incomplete.intent_sha256,
            )

        self.assertEqual(tuple(layout.results.iterdir()), ())
        self.assertEqual(tuple(layout.resume.iterdir()), ())
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (0, 0, 0))

    def test_only_exact_axjo_402_fingerprint_is_locally_terminal(self) -> None:
        plan, layout, state, target_identity, body = self._prepare_axjo_402_spool()
        self.assertIsNotNone(state.intent)
        self.assertIsNotNone(state.intent_sha256)
        self.assertIsNotNone(state.response)
        response = dict(state.response)
        other_body = b"y" * 215
        other_metadata = operations._response_metadata_material(
            state.unit,
            intent_sha256=state.intent_sha256,
            response=operations.Stage12CTransportResponse(
                status=402,
                media_type="application/json; charset=utf-8",
                body=other_body,
                elapsed_seconds=Decimal("0.1"),
            ),
            captured_at=_SIDECAR_TIME,
        )
        other_spool = {
            **other_metadata,
            "spool_sha256": operations._sha256_json(other_metadata),
        }
        cases = (
            ("plan", replace(state.unit, plan_sha256="0" * 64), response, body),
            ("symbol", replace(state.unit, symbol="^DJI"), response, body),
            ("ordinal", replace(state.unit, ordinal=614), response, body),
            ("from_date", replace(state.unit, from_date="2026-08-12"), response, body),
            ("to_date", replace(state.unit, to_date="2026-08-15"), response, body),
            ("unit_id", replace(state.unit, identifier="stage12c-other"), response, body),
            ("intent", state.unit, {**response, "intent_sha256": "0" * 64}, body),
            ("status", state.unit, {**response, "http_status": 401}, body),
            ("mime", state.unit, {**response, "content_type": "application/json"}, body),
            ("redirect", state.unit, {**response, "redirected": True}, body),
            ("length", state.unit, {**response, "body_byte_count": 214}, body),
            ("response_sha256", state.unit, {**response, "response_sha256": "0" * 64}, body),
            ("spool_sha256", state.unit, {**response, "spool_sha256": "0" * 64}, body),
            (
                "spool_sha256_truncated",
                state.unit,
                {**response, "spool_sha256": response["spool_sha256"][:-1]},
                body,
            ),
            (
                "spool_sha256_one_character",
                state.unit,
                {**response, "spool_sha256": "0" + response["spool_sha256"][1:]},
                body,
            ),
            ("other_402", state.unit, other_spool, other_body),
        )
        target_before = _sha256(self.stores.market)
        patches = self._synthetic_axjo_402_patches(plan, state, body)
        with (
            mock.patch.multiple(operations, **patches),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.read_project_credential",
                side_effect=AssertionError("credential read during local replay"),
            ),
            mock.patch(
                "quant_data.operations.stage12c_market_gap.Stage12CLiveRequest",
                side_effect=AssertionError("publication preparation during rejected 402"),
            ),
        ):
            for label, unit, spool, candidate_body in cases:
                with self.subTest(label=label):
                    rejected = operations._JournalState(
                        unit=unit,
                        intent=state.intent,
                        intent_sha256=state.intent_sha256,
                        body=candidate_body,
                        response=spool,
                        result=None,
                        resume=None,
                    )
                    with self.assertRaises(StoreUnavailableError):
                        operations._replay_spooled_unit(
                            layout=layout,
                            state=rejected,
                            collector=self.collector,
                            max_seconds=self.scope.collector.max_seconds,
                            target_identity=target_identity,
                        )
                    self.assertFalse(
                        operations._private_file(layout, "results", unit, ".json").exists()
                    )
        self.assertEqual(tuple(layout.results.iterdir()), ())
        self.assertEqual(tuple(layout.resume.iterdir()), ())
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), (0, 0, 0))

    def test_project_dotenv_fallback_enables_only_the_fake_call_and_redacts_the_key(self) -> None:
        secret = "dotenv-stage12c-fixture-secret"
        self._write_dotenv(f"FMP_API_KEY={secret}\n")
        transport = _Transport(
            {
                "AAPL": operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        environment = _TrackingEnvironment({})
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)

        with self.assertRaises(StoreUnavailableError) as raised:
            self._runner(transport, environment=environment).run()

        self.assertEqual(environment.reads, 1)
        self.assertEqual([query["symbol"] for _, query in transport.calls], ["AAPL"])
        self.assertEqual(transport.headers, [{"apikey": secret}])
        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        private = self.root / operations.APPROVED_PRIVATE_RELATIVE_ROOT
        serialized = b"".join(path.read_bytes() for path in private.rglob("*") if path.is_file())
        self.assertNotIn(secret.encode("utf-8"), serialized)

    def test_process_environment_wins_without_opening_an_unsafe_project_dotenv(self) -> None:
        secret = "environment-stage12c-fixture-secret"
        unsafe_target = self.root / "unsafe-dotenv-source"
        unsafe_target.write_text("FMP_API_KEY=unsafe-secret\n", encoding="utf-8")
        unsafe_target.chmod(0o600)
        (self.root / ".env").symlink_to(unsafe_target)
        transport = _Transport(
            {
                "AAPL": operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        environment = _TrackingEnvironment({"FMP_API_KEY": secret})

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, environment=environment).run()

        self.assertEqual(environment.reads, 1)
        self.assertEqual([query["symbol"] for _, query in transport.calls], ["AAPL"])
        self.assertEqual(transport.headers, [{"apikey": secret}])

    def test_missing_unsafe_and_malformed_credentials_stop_before_transport_or_target_write(self) -> None:
        for case in ("missing", "unsafe", "malformed"):
            with self.subTest(case=case):
                dotenv = self.root / ".env"
                if dotenv.exists() or dotenv.is_symlink():
                    dotenv.unlink()
                if case == "unsafe":
                    unsafe_target = self.root / "unsafe-dotenv-source"
                    unsafe_target.write_text("FMP_API_KEY=unsafe-secret\n", encoding="utf-8")
                    unsafe_target.chmod(0o600)
                    dotenv.symlink_to(unsafe_target)
                elif case == "malformed":
                    self._write_dotenv('FMP_API_KEY="malformed-secret\n')
                transport = self._complete_transport()
                environment = _TrackingEnvironment({})
                source_before = _sha256(self.source)
                target_before = _sha256(self.stores.market)

                with self.assertRaisesRegex(
                    ValidationError,
                    r"\ACredential is missing or invalid\Z",
                ) as raised:
                    self._runner(transport, environment=environment).run()

                self.assertEqual(environment.reads, 1)
                self.assertEqual(transport.calls, [])
                self.assertEqual(_sha256(self.source), source_before)
                self.assertEqual(_sha256(self.stores.market), target_before)
                self.assertNotIn("unsafe-secret", str(raised.exception))
                self.assertNotIn("malformed-secret", str(raised.exception))

    def test_process_whitespace_credential_stops_before_journal_and_corrected_value_proceeds(self) -> None:
        environment = _TrackingEnvironment({"FMP_API_KEY": "process credential"})
        transport = _Transport(
            {
                "AAPL": operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        target_before = _sha256(self.stores.market)
        runner = self._runner(transport, environment=environment)

        with mock.patch(
            "quant_data.operations.stage12c_market_gap.http.client.HTTPSConnection",
            side_effect=AssertionError("unexpected HTTPS construction"),
        ):
            with self.assertRaisesRegex(
                ValidationError,
                r"\AStage 12C provider credential is invalid\Z",
            ) as raised:
                runner.run()

        self.assertEqual(environment.reads, 1)
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.headers, [])
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertNotIn("process credential", str(raised.exception))
        self._assert_no_request_journal_files()

        environment["FMP_API_KEY"] = "corrected-process-credential"
        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual([query["symbol"] for _, query in transport.calls], ["AAPL"])
        self.assertEqual(transport.headers, [{"apikey": "corrected-process-credential"}])
        self.assertEqual(_sha256(self.stores.market), target_before)

    def test_dotenv_whitespace_credential_stops_before_journal_and_corrected_value_proceeds(self) -> None:
        self._write_dotenv("FMP_API_KEY=dotenv credential\n")
        environment = _TrackingEnvironment({})
        transport = _Transport(
            {
                "AAPL": operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        target_before = _sha256(self.stores.market)
        runner = self._runner(transport, environment=environment)

        with mock.patch(
            "quant_data.operations.stage12c_market_gap.http.client.HTTPSConnection",
            side_effect=AssertionError("unexpected HTTPS construction"),
        ):
            with self.assertRaisesRegex(
                ValidationError,
                r"\AStage 12C provider credential is invalid\Z",
            ) as raised:
                runner.run()

        self.assertEqual(environment.reads, 1)
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.headers, [])
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertNotIn("dotenv credential", str(raised.exception))
        self._assert_no_request_journal_files()

        self._write_dotenv("FMP_API_KEY=corrected-dotenv-credential\n")
        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual([query["symbol"] for _, query in transport.calls], ["AAPL"])
        self.assertEqual(transport.headers, [{"apikey": "corrected-dotenv-credential"}])
        self.assertEqual(_sha256(self.stores.market), target_before)

    def test_systemic_response_stops_without_repeating_or_changing_either_store(self) -> None:
        transport = _Transport(
            {
                "AAPL": operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        environment = _TrackingEnvironment({"FMP_API_KEY": "fixture-key"})
        source_before = _sha256(self.source)
        target_before = _sha256(self.stores.market)
        target_counts = _counts(self.stores.market)
        runner = self._runner(transport, environment=environment)

        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual([call[1]["symbol"] for call in transport.calls], ["AAPL"])
        self.assertEqual(environment.reads, 1)
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)
        self.assertEqual(_counts(self.stores.market), target_counts)

        with self.assertRaises(StoreUnavailableError):
            runner.run()
        self.assertEqual([call[1]["symbol"] for call in transport.calls], ["AAPL"])
        self.assertEqual(environment.reads, 1)
        self.assertEqual(_sha256(self.source), source_before)
        self.assertEqual(_sha256(self.stores.market), target_before)

    def test_canonical_live_entrypoint_has_no_caller_selectable_paths(self) -> None:
        self.assertEqual(operations.run_stage12c_market_gap_live.__code__.co_argcount, 0)
        self.assertEqual(operations.run_stage12c_market_gap_live.__code__.co_kwonlyargcount, 0)

    def test_response_admission_boundaries_classify_only_reviewed_terminal_outcomes(self) -> None:
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        unit = plan.units[1]

        def spool(status: int, media_type: str, body: bytes, *, redirected: bool = False) -> dict[str, object]:
            return {
                "elapsed_seconds": "0.1",
                "http_status": status,
                "content_type": media_type,
                "redirected": redirected,
            }

        self.assertEqual(
            operations._classify_spooled_response(
                unit, spool(200, "application/json", b"[]"), b"[]", max_seconds=45
            ).outcome,
            "noncoverage_empty",
        )
        partial = _daily_body(unit.symbol, dates=("2026-08-13",))
        self.assertEqual(
            operations._classify_spooled_response(
                unit, spool(200, "application/json", partial), partial, max_seconds=45
            ).outcome,
            "noncoverage_partial",
        )
        error = b'{"Error Message":"not found"}'
        self.assertEqual(
            operations._classify_spooled_response(
                unit, spool(404, "application/json", error), error, max_seconds=45
            ).outcome,
            "noncoverage_http_404",
        )
        for status, media_type, body, redirected in (
            (503, "application/json", error, False),
            (200, "text/plain", _daily_body(unit.symbol), False),
            (200, "application/json", b"not-json", False),
            (302, "application/json", error, True),
        ):
            with self.subTest(status=status, media_type=media_type):
                with self.assertRaises((StoreUnavailableError, ValidationError)):
                    operations._classify_spooled_response(
                        unit,
                        spool(status, media_type, body, redirected=redirected),
                        body,
                        max_seconds=45,
                    )


    def test_public_runner_and_transport_reject_canonical_paths_without_live_capability(self) -> None:
        transport = self._complete_transport()
        common = {
            "scope": self.scope,
            "stage12a_scope": self.stage12a,
            "stage12b_scope": self.stage12b,
            "collector": self.collector,
            "transport": transport,
            "environment": _TrackingEnvironment({"FMP_API_KEY": "fixture-key"}),
            "static_preflight": lambda project: {"gate": "fixture"},
            "baseline": self.baseline,
        }
        with mock.patch(
            "quant_data.operations.stage12c_market_gap._require_direct_directory",
            side_effect=AssertionError("canonical project root was inspected"),
        ):
            with self.assertRaises(ValidationError):
                operations.Stage12CMarketGapRunner(
                    project_root=operations.APPROVED_PROJECT_ROOT,
                    retained_source=self.source,
                    **common,
                )
        with self.assertRaises(ValidationError):
            operations.Stage12CMarketGapRunner(
                project_root=self.root,
                retained_source=operations.APPROVED_RETAINED_SOURCE,
                **common,
            )
        with self.assertRaises(ValidationError):
            operations._CanonicalStdlibStage12CTransport(capability=object())
        self.assertEqual(
            tuple(
                inspect.signature(
                    operations._Stage12CMarketGapRunner._for_canonical_live
                ).parameters
            ),
            (),
        )

    def test_completion_rejects_target_replacement_before_receipt(self) -> None:
        transport = self._complete_transport()
        runner = self._runner(transport)
        market_store = self.stores.market
        replacement = self.root / "data" / "completion-replacement.sqlite"
        parked_original = self.root / "data" / "completion-parked.sqlite"
        shutil.copy2(market_store, replacement)
        original_lock = operations.StoreWriteLock

        class SwapOnEnterLock:
            def __init__(inner_self, path: Path, **kwargs: object) -> None:
                inner_self._lock = original_lock(path, **kwargs)

            def __enter__(inner_self) -> object:
                acquired = inner_self._lock.__enter__()
                market_store.replace(parked_original)
                replacement.replace(market_store)
                return acquired

            def __exit__(
                inner_self,
                exc_type: object,
                exc: object,
                traceback: object,
            ) -> object:
                return inner_self._lock.__exit__(exc_type, exc, traceback)

        with mock.patch(
            "quant_data.operations.stage12c_market_gap.StoreWriteLock",
            SwapOnEnterLock,
        ):
            with self.assertRaises(ConflictError):
                runner.run()
        layout = operations._open_or_create_layout(self.root)
        self.assertFalse(layout.completion.exists() or layout.completion.is_symlink())

    def test_completion_rejects_target_replacement_at_receipt_callback(self) -> None:
        transport = self._complete_transport()
        runner = self._runner(transport)
        market_store = self.stores.market
        replacement = self.root / "data" / "receipt-callback-replacement.sqlite"
        parked_original = self.root / "data" / "receipt-callback-parked.sqlite"
        shutil.copy2(market_store, replacement)
        original_completion = runner._completion_receipt
        callback_called = False

        def replace_before_original(*args: object, **kwargs: object) -> object:
            nonlocal callback_called
            callback_called = True
            market_store.replace(parked_original)
            replacement.replace(market_store)
            return original_completion(*args, **kwargs)

        with mock.patch.object(
            runner,
            "_completion_receipt",
            side_effect=replace_before_original,
        ):
            with self.assertRaises(ConflictError):
                runner.run()
        self.assertTrue(callback_called)
        layout = operations._open_or_create_layout(self.root)
        self.assertFalse(layout.completion.exists() or layout.completion.is_symlink())
    def test_completion_write_replacement_rolls_back_the_new_receipt(self) -> None:
        transport = self._complete_transport()
        runner = self._runner(transport)
        market_store = self.stores.market
        replacement = self.root / "data" / "completion-write-replacement.sqlite"
        parked_original = self.root / "data" / "completion-write-parked.sqlite"
        completion = self.root / operations.APPROVED_PRIVATE_RELATIVE_ROOT / "completion.json"
        shutil.copy2(market_store, replacement)
        original_write = operations._write_exclusive_json
        replaced = False

        def write_then_replace(
            path: Path,
            value: dict[str, object],
            *,
            maximum: int,
        ) -> None:
            nonlocal replaced
            original_write(path, value, maximum=maximum)
            if path == completion:
                self.assertFalse(replaced)
                market_store.replace(parked_original)
                replacement.replace(market_store)
                replaced = True

        with mock.patch(
            "quant_data.operations.stage12c_market_gap._write_exclusive_json",
            side_effect=write_then_replace,
        ):
            with self.assertRaises(ConflictError):
                runner.run()

        self.assertTrue(replaced)
        self.assertEqual(_counts(market_store), (0, 0, 0))
        layout = operations._open_or_create_layout(self.root)
        self.assertFalse(layout.completion.exists() or layout.completion.is_symlink())


    def test_post_intent_monotonic_pacing_uses_actual_transport_start_boundary(self) -> None:
        plan = operations._build_plan(self.scope, self.stage12a, self.stage12b)
        layout = operations._open_or_create_layout(self.root)
        operations._ensure_plan_receipt(layout, plan)
        _, target_identity = operations._ensure_baseline(
            layout=layout,
            plan=plan,
            target=self.stores.market,
            source=self.source,
            expectation=self.baseline,
        )
        clock = _Clock()
        starts: list[float] = []
        first, second = plan.units[:2]

        class TimedTransport(_Transport):
            def get(
                inner_self,
                *,
                path: str,
                query: dict[str, str],
                headers: dict[str, str],
                timeout_seconds: int,
                max_bytes: int,
            ) -> operations.Stage12CTransportResponse:
                starts.append(clock.monotonic())
                return super().get(
                    path=path,
                    query=query,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )

        transport = TimedTransport(
            {
                first.symbol: operations.Stage12CTransportResponse(
                    status=200,
                    media_type="application/json",
                    body=_daily_body(first.symbol),
                    elapsed_seconds=Decimal("0.1"),
                ),
                second.symbol: operations.Stage12CTransportResponse(
                    status=200,
                    media_type="application/json",
                    body=_daily_body(second.symbol),
                    elapsed_seconds=Decimal("0.1"),
                ),
            }
        )
        runner = self._runner(transport, clock=clock)
        pacer = operations._RequestPacer(
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        )
        original_write_intent = operations._write_intent
        intent_calls = 0

        def delayed_write_intent(*args: object, **kwargs: object) -> object:
            nonlocal intent_calls
            value = original_write_intent(*args, **kwargs)
            if intent_calls == 0:
                clock.sleep(5)
            intent_calls += 1
            return value

        with mock.patch(
            "quant_data.operations.stage12c_market_gap._write_intent",
            side_effect=delayed_write_intent,
        ):
            runner._issue_one(
                layout=layout,
                unit=first,
                target_identity=target_identity,
                pacer=pacer,
                api_key="fixture-key",
            )
            runner._issue_one(
                layout=layout,
                unit=second,
                target_identity=target_identity,
                pacer=pacer,
                api_key="fixture-key",
            )
        self.assertEqual(starts, [5.0, 6.0])
        self.assertGreaterEqual(starts[1] - starts[0], 1.0)

    def test_cold_resume_paces_first_live_attempt_after_local_spool_recovery(self) -> None:
        initial = operations.Stage12CTransportResponse(
            status=200,
            media_type="application/json",
            body=_daily_body("AAPL"),
            elapsed_seconds=Decimal("0.1"),
        )
        plan, _, first = self._prepare_aapl_spool(initial)
        second = plan.units[1]
        clock = _Clock()
        starts: list[float] = []

        class TimedTransport(_Transport):
            def get(
                inner_self,
                *,
                path: str,
                query: dict[str, str],
                headers: dict[str, str],
                timeout_seconds: int,
                max_bytes: int,
            ) -> operations.Stage12CTransportResponse:
                starts.append(clock.monotonic())
                return super().get(
                    path=path,
                    query=query,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )

        transport = TimedTransport(
            {
                second.symbol: operations.Stage12CTransportResponse(
                    status=503,
                    media_type="application/json",
                    body=b'{"Error Message":"temporary provider failure"}',
                    elapsed_seconds=Decimal("0.1"),
                )
            }
        )
        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, clock=clock).run()
        self.assertEqual([query["symbol"] for _, query in transport.calls], [second.symbol])
        self.assertEqual(first.symbol, "AAPL")
        self.assertEqual(starts, [1.0])
        self.assertGreaterEqual(starts[0], 1.0)

    def test_body_only_and_metadata_only_spools_stop_before_credential_or_transport(self) -> None:
        for omission in ("body", "metadata"):
            with self.subTest(omission=omission):
                response = operations.Stage12CTransportResponse(
                    status=200,
                    media_type="application/json",
                    body=_daily_body("AAPL"),
                    elapsed_seconds=Decimal("0.1"),
                )
                _, layout, unit = self._prepare_aapl_spool(response)
                path = (
                    operations._private_file(layout, "bodies", unit, ".body")
                    if omission == "body"
                    else operations._private_file(layout, "responses", unit, ".json")
                )
                path.unlink()
                transport = self._complete_transport()
                environment = _TrackingEnvironment({"FMP_API_KEY": "fixture-key"})
                with self.assertRaises(ConflictError):
                    self._runner(transport, environment=environment).run()
                self.assertEqual(environment.reads, 0)
                self.assertEqual(transport.calls, [])
                self.assertEqual(_counts(self.stores.market), (0, 0, 0))
            if omission == "body":
                self.temporary.cleanup()
                self.setUp()

    def test_symlinked_run_lock_is_rejected_without_touching_the_link_target(self) -> None:
        layout = operations._open_or_create_layout(self.root)
        source_mode = self.source.stat().st_mode
        layout.run_lock.symlink_to(self.source)
        with self.assertRaises(ConflictError):
            with operations._held_run_lock(layout):
                pass
        self.assertEqual(self.source.stat().st_mode, source_mode)

    def test_post_publish_pre_result_recovery_replays_locally_without_transport(self) -> None:
        response = operations.Stage12CTransportResponse(
            status=200,
            media_type="application/json",
            body=_daily_body("AAPL"),
            elapsed_seconds=Decimal("0.1"),
        )
        plan, layout, unit = self._prepare_aapl_spool(response)
        state = operations._load_journal(layout, plan)[unit.identifier]
        target_identity = operations._require_direct_regular_file(
            self.stores.market,
            "fixture market target",
        )
        with mock.patch(
            "quant_data.operations.stage12c_market_gap._write_result",
            side_effect=RuntimeError("injected post-publication interruption"),
        ):
            with self.assertRaises(RuntimeError):
                operations._replay_spooled_unit(
                    layout=layout,
                    state=state,
                    collector=self.collector,
                    max_seconds=self.scope.collector.max_seconds,
                    target_identity=target_identity,
                )
        result_path = operations._private_file(layout, "results", unit, ".json")
        self.assertFalse(result_path.exists() or result_path.is_symlink())
        target_after_publication = _sha256(self.stores.market)
        transport = self._complete_transport()
        environment = _TrackingEnvironment({"OTHER": "fixture"})
        with self.assertRaises(ValidationError):
            self._runner(transport, environment=environment).run()
        self.assertEqual(transport.calls, [])
        self.assertEqual(_sha256(self.stores.market), target_after_publication)
        self.assertTrue(result_path.exists())
        self.assertTrue(
            operations._private_file(layout, "resume", unit, ".json").exists()
        )


if __name__ == "__main__":
    unittest.main()
