from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.operations import fmp_treasury_curve_history as operation
from quant_data.operations.fmp_macro_calendar_history import (
    FmpMacroCalendarTransportResponse,
)


FIXED_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
SECRET = "fixture-fmp-key-not-for-reports"


@dataclass(frozen=True, slots=True)
class _Capture:
    body: bytes
    captured_at: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class _Publication:
    outcome: str
    semantic_identity: str
    written_curves: int
    written_curve_versions: int
    written_observation_versions: int


class _Parser:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[_Capture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _Capture:
        if self.lock_state["held"]:
            raise AssertionError("parser ran while the publisher lock was held")
        self.events.append("parser")
        result = _Capture(body, captured_at, start_date, end_date)
        self.calls.append(result)
        return result


class _Publisher:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        outcome: str = "published",
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.outcome = outcome
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _Publication:
        if not isinstance(capture, _Capture):
            raise AssertionError("publisher received an invalid capture")
        self.lock_state["held"] = True
        try:
            self.events.append("publish")
            self.calls.append(capture)
            unchanged = self.outcome == "unchanged"
            return _Publication(
                self.outcome,
                "fixture-treasury-semantic-identity",
                0 if unchanged else 2,
                0 if unchanged else 24,
                0 if unchanged else 24,
            )
        finally:
            self.lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        *,
        response: FmpMacroCalendarTransportResponse | None = None,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.response = response or FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=b"[]",
        )
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> FmpMacroCalendarTransportResponse:
        if self.lock_state["held"]:
            raise AssertionError("network ran while the publisher lock was held")
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        return self.response


class _CredentialReader:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.events.append("credential")
        self.calls.append(dict(kwargs))
        return SECRET


class FmpTreasuryCurveHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.lock_state = {"held": False}
        self.parser = _Parser(self.events, self.lock_state)
        self.credential = _CredentialReader(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        publisher: _Publisher | None = None,
        transport: _Transport | None = None,
    ) -> tuple[operation.FmpTreasuryCurveHistoryRunner, _Publisher, _Transport]:
        selected_publisher = publisher or _Publisher(self.events, self.lock_state)
        selected_transport = transport or _Transport(self.events, self.lock_state)

        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return selected_publisher

        runner = operation.FmpTreasuryCurveHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.parser,
            publisher_factory=publisher_factory,
            transport=selected_transport,
            credential_environment={"FMP_API_KEY": "fixture-environment-value"},
            credential_reader=self.credential,
            utcnow=lambda: FIXED_NOW,
        )
        return runner, selected_publisher, selected_transport

    def test_one_bounded_request_redacts_key_and_parses_before_publish(self) -> None:
        runner, publisher, transport = self._runner()

        report = runner.run(start_date="2025-01-02", end_date="2025-12-31")

        self.assertEqual(
            report.mapping(),
            {
                "requested": 1,
                "published": 1,
                "unchanged": 0,
                "written_curves": 2,
                "written_curve_versions": 24,
                "written_observation_versions": 24,
                "start_date": "2025-01-02",
                "end_date": "2025-12-31",
            },
        )
        self.assertEqual(self.events, [
            "publisher_factory",
            "credential",
            "transport",
            "parser",
            "publish",
        ])
        self.assertEqual(len(self.credential.calls), 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(self.parser.calls), 1)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(
            self.parser.calls[0].captured_at, "2026-08-21T12:00:00.000000Z"
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], operation.FMP_TREASURY_RATES_URL)
        self.assertEqual(
            call["parameters"], {"from": "2025-01-02", "to": "2025-12-31"}
        )
        self.assertEqual(call["headers"]["apikey"], SECRET)
        self.assertNotIn("apikey", call["parameters"])
        self.assertEqual(call["timeout_seconds"], 60)
        self.assertEqual(call["max_bytes"], 16 * 1024 * 1024)
        self.assertNotIn(SECRET, str(report.mapping()))
        self.assertFalse(self.lock_state["held"])

    def test_unchanged_result_is_an_explicit_semantic_noop(self) -> None:
        publisher = _Publisher(self.events, self.lock_state, outcome="unchanged")
        runner, _, transport = self._runner(publisher=publisher)

        report = runner.run(
            start_date=date(2025, 1, 2), end_date=date(2025, 1, 2)
        )

        self.assertEqual(
            (report.requested, report.published, report.unchanged), (1, 0, 1)
        )
        self.assertEqual(
            (
                report.written_curves,
                report.written_curve_versions,
                report.written_observation_versions,
            ),
            (0, 0, 0),
        )
        self.assertEqual(len(transport.calls), 1)

    def test_published_empty_window_can_report_zero_curve_rows(self) -> None:
        self.assertEqual(
            operation._publication_result(
                _Publication(
                    "published",
                    "fixture-empty-treasury-semantic-identity",
                    0,
                    0,
                    0,
                )
            ),
            ("published", 0, 0, 0),
        )

    def test_span_and_date_rejections_happen_before_credential_or_network(self) -> None:
        runner, _, transport = self._runner()

        with self.assertRaises(ValidationError):
            runner.run(start_date="2024-01-01", end_date="2025-01-01")
        with self.assertRaises(ValidationError):
            runner.run(start_date="not-a-date", end_date="2025-01-01")

        self.assertEqual(self.credential.calls, [])
        self.assertEqual(transport.calls, [])

    def test_invalid_provider_response_never_reaches_parser_or_publisher(self) -> None:
        transport = _Transport(
            self.events,
            self.lock_state,
            response=FmpMacroCalendarTransportResponse(
                status=302,
                media_type="application/json",
                body=b"[]",
                redirected=True,
            ),
        )
        runner, publisher, _ = self._runner(transport=transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run(start_date="2025-01-02", end_date="2025-01-02")

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertNotIn(SECRET, self.events)

    def test_response_size_limit_is_enforced_before_parser_or_publisher(self) -> None:
        oversized = b"[" + b"0" * (16 * 1024 * 1024) + b"]"
        transport = _Transport(
            self.events,
            self.lock_state,
            response=FmpMacroCalendarTransportResponse(
                status=200,
                media_type="application/json",
                body=oversized,
            ),
        )
        runner, publisher, _ = self._runner(transport=transport)

        with self.assertRaises(ResourceLimitError):
            runner.run(start_date="2025-01-02", end_date="2025-01-02")

        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_fixture_target_must_be_exact_temporary_single_link_store(self) -> None:
        alternate = self.project / "other.sqlite"
        alternate.write_bytes(b"fixture alternate store")
        with self.assertRaises(ValidationError):
            operation.FmpTreasuryCurveHistoryRunner(
                project_root=self.project,
                macro_store=alternate,
                parser=self.parser,
                publisher_factory=lambda: _Publisher(self.events, self.lock_state),
                transport=_Transport(self.events, self.lock_state),
                credential_environment={},
            )

    def test_canonical_domain_api_binds_without_provider_work(self) -> None:
        registry = object()
        with patch.object(operation, "load_registry", return_value=registry) as load:
            parser, publisher_factory = operation._domain_api()

        from quant_data.macro.fmp_treasury_curve import parse_fmp_treasury_curve

        self.assertIs(parser, parse_fmp_treasury_curve)
        self.assertTrue(callable(publisher_factory))
        load.assert_called_once_with(
            operation.CANONICAL_REGISTRY_PATH,
            project_root=operation.PROJECT_ROOT,
            environment={},
        )

    def test_cli_accepts_one_window_and_rejects_bad_arguments_safely(self) -> None:
        report = operation.FmpTreasuryCurveHistoryReport(
            1, 1, 0, 2, 24, 24, date(2025, 1, 2), date(2025, 1, 2)
        )
        output = StringIO()
        with patch.object(
            operation, "populate_fmp_treasury_curve_history_live", return_value=report
        ):
            with redirect_stdout(output):
                self.assertEqual(
                    operation.main(["--from", "2025-01-02", "--to", "2025-01-02"]),
                    0,
                )
        self.assertIn('"requested":1', output.getvalue())

        error = StringIO()
        with patch.object(
            operation, "populate_fmp_treasury_curve_history_live"
        ) as populate:
            with redirect_stderr(error):
                self.assertEqual(operation.main([]), 64)
        populate.assert_not_called()
        self.assertIn('"error":"invalid_request"', error.getvalue())


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
