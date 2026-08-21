from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.errors import ConflictError, StoreUnavailableError
from quant_data.json_codec import dumps_strict
from quant_data.operations import fmp_macro_calendar_history as operation


FIXED_NOW = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)
_SECRET = "fixture-fmp-key-not-for-reports"


@dataclass(frozen=True, slots=True)
class _Capture:
    body: bytes
    captured_at: str
    start_date: str
    end_date: str


@dataclass(frozen=True, slots=True)
class _PublishReport:
    outcome: str
    semantic_identity: str
    written_versions: int


class _Parser:
    def __init__(self, lock_state: dict[str, bool], events: list[str]) -> None:
        self._lock_state = lock_state
        self._events = events
        self.calls: list[_Capture] = []

    def __call__(
        self,
        body: bytes,
        *,
        captured_at: str,
        start_date: str,
        end_date: str,
    ) -> _Capture:
        if self._lock_state["held"]:
            raise AssertionError("parser ran while publisher write section was active")
        self._events.append("parser")
        capture = _Capture(
            body=body,
            captured_at=captured_at,
            start_date=start_date,
            end_date=end_date,
        )
        self.calls.append(capture)
        return capture


class _Publisher:
    def __init__(
        self,
        lock_state: dict[str, bool],
        events: list[str],
        *,
        replay: bool = False,
    ) -> None:
        self._lock_state = lock_state
        self._events = events
        self._replay = replay
        self._published: set[bytes] = set()
        self._completed: list[tuple[str, str]] = []
        self.calls: list[_Capture] = []
        self.write_count = 0

    def completed_windows(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._completed)

    def publish(self, capture: object) -> _PublishReport:
        if not isinstance(capture, _Capture):
            raise AssertionError("runner did not pass the opaque parser result")
        self._lock_state["held"] = True
        try:
            self._events.append("publish")
            self.calls.append(capture)
            if self._replay and capture.body in self._published:
                return _PublishReport("unchanged", "fixture-replay", 0)
            self._published.add(capture.body)
            self._completed.append((capture.start_date, capture.end_date))
            self.write_count += 1
            return _PublishReport("published", "fixture-calendar", 1)
        finally:
            self._lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        lock_state: dict[str, bool],
        events: list[str],
        *,
        failure_at: int | None = None,
        response_at: dict[int, operation.FmpMacroCalendarTransportResponse] | None = None,
    ) -> None:
        self._lock_state = lock_state
        self._events = events
        self._failure_at = failure_at
        self._response_at = response_at or {}
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.FmpMacroCalendarTransportResponse:
        if self._lock_state["held"]:
            raise AssertionError("transport ran while publisher write section was active")
        self._events.append("transport")
        self.calls.append(dict(kwargs))
        if self._failure_at == len(self.calls):
            raise StoreUnavailableError("fixture one-attempt failure")
        configured = self._response_at.get(len(self.calls))
        if configured is not None:
            return configured
        parameters = kwargs["parameters"]
        if not isinstance(parameters, dict):
            raise AssertionError("runner did not provide fixed request parameters")
        body = json.dumps(
            {
                "country": parameters["country"],
                "from": parameters["from"],
                "to": parameters["to"],
            },
            sort_keys=True,
        ).encode("utf-8")
        return operation.FmpMacroCalendarTransportResponse(
            status=200,
            media_type="application/json; charset=utf-8",
            body=body,
        )


class _CredentialReader:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self._events.append("credential")
        self.calls.append(dict(kwargs))
        return _SECRET


class FmpMacroCalendarHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.lock_state = {"held": False}
        self.events: list[str] = []
        self.parser = _Parser(self.lock_state, self.events)
        self.credential = _CredentialReader(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        transport: _Transport,
        publisher: _Publisher,
    ) -> operation.FmpMacroCalendarHistoryRunner:
        def publisher_factory() -> _Publisher:
            self.events.append("publisher_factory")
            return publisher

        return operation.FmpMacroCalendarHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            parser=self.parser,
            publisher_factory=publisher_factory,
            transport=transport,
            credential_environment={"FMP_API_KEY": "fixture-environment-value"},
            credential_reader=self.credential,
            utcnow=lambda: FIXED_NOW,
        )

    def test_fixed_plan_uses_exact_serial_windows_and_redacts_key(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        transport = _Transport(self.lock_state, self.events)

        report = self._runner(transport, publisher).run()

        windows = operation.FMP_MACRO_CALENDAR_WINDOWS
        self.assertEqual(len(windows), 56)
        self.assertEqual(
            (windows[0].start_date.isoformat(), windows[0].end_date.isoformat()),
            ("2013-01-01", "2013-03-31"),
        )
        self.assertEqual(
            (windows[1].start_date.isoformat(), windows[1].end_date.isoformat()),
            ("2013-04-01", "2013-06-29"),
        )
        self.assertEqual(
            (windows[-1].start_date.isoformat(), windows[-1].end_date.isoformat()),
            ("2026-07-22", "2026-08-17"),
        )
        self.assertEqual(len(transport.calls), len(windows))
        self.assertEqual(len(self.parser.calls), len(windows))
        self.assertEqual(len(publisher.calls), len(windows))
        self.assertEqual(
            report.mapping(),
            {
                "requested": 56,
                "published": 56,
                "unchanged": 0,
                "written_versions": 56,
            },
        )
        self.assertEqual(len(self.credential.calls), 1)
        self.assertEqual(
            self.credential.calls,
            [
                {
                    "project_root": self.project,
                    "name": "FMP_API_KEY",
                    "environment": {"FMP_API_KEY": "fixture-environment-value"},
                }
            ],
        )
        self.assertEqual(
            self.events[:4],
            ["publisher_factory", "credential", "transport", "parser"],
        )
        self.assertEqual(self.events[4], "publish")
        self.assertFalse(self.lock_state["held"])

        for window, call in zip(windows, transport.calls, strict=True):
            self.assertEqual(
                call["url"],
                "https://financialmodelingprep.com/stable/economic-calendar",
            )
            self.assertEqual(call["parameters"], window.parameters)
            self.assertEqual(
                call["headers"],
                {
                    "Accept": "application/json",
                    "User-Agent": "QuantDataInfra/1.0",
                    "apikey": _SECRET,
                },
            )
            self.assertEqual(call["timeout_seconds"], 60)
            self.assertEqual(call["max_bytes"], 1024 * 1024)
            self.assertNotIn("apikey", call["parameters"])
        for preceding, following in zip(windows, windows[1:], strict=False):
            self.assertEqual(
                (following.start_date - preceding.end_date).days,
                1,
            )
            self.assertLessEqual(
                (preceding.end_date - preceding.start_date).days + 1,
                operation.MAX_WINDOW_DAYS,
            )
        self.assertLessEqual(
            (windows[-1].end_date - windows[-1].start_date).days + 1,
            operation.MAX_WINDOW_DAYS,
        )
        self.assertNotIn(_SECRET, dumps_strict(report.mapping()))

    def test_one_transport_failure_is_not_retried_or_published(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        transport = _Transport(self.lock_state, self.events, failure_at=1)

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run()

        self.assertEqual(len(self.credential.calls), 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])
        self.assertFalse(self.lock_state["held"])

    def test_malformed_json_never_reaches_parser_or_publisher(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        transport = _Transport(
            self.lock_state,
            self.events,
            response_at={
                1: operation.FmpMacroCalendarTransportResponse(
                    status=200,
                    media_type="application/json",
                    body=b"{malformed",
                )
            },
        )

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run()

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_redirect_is_rejected_without_retry_or_publication(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        transport = _Transport(
            self.lock_state,
            self.events,
            response_at={
                1: operation.FmpMacroCalendarTransportResponse(
                    status=302,
                    media_type="application/json",
                    body=b"{}",
                    redirected=True,
                )
            },
        )

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run()

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_stdlib_transport_keeps_apikey_out_of_query(self) -> None:
        calls: list[dict[str, object]] = []

        class _Response:
            status = 200

            @staticmethod
            def getheader(name: str) -> str | None:
                return {
                    "Content-Length": "2",
                    "Content-Type": "application/json",
                }.get(name)

            @staticmethod
            def read(limit: int) -> bytes:
                self.assertEqual(limit, 1024 * 1024 + 1)
                return b"{}"

        class _Connection:
            def __init__(self, host: str, *, timeout: int) -> None:
                calls.append({"host": host, "timeout": timeout})

            def request(
                self,
                method: str,
                target: str,
                body: bytes | None = None,
                headers: dict[str, str] | None = None,
            ) -> None:
                calls.append(
                    {
                        "method": method,
                        "target": target,
                        "body": body,
                        "headers": headers,
                    }
                )

            @staticmethod
            def getresponse() -> _Response:
                return _Response()

            @staticmethod
            def close() -> None:
                return None

        with patch.object(operation.http.client, "HTTPSConnection", _Connection):
            response = operation._StdlibTransport().request(
                url=operation.FMP_ECONOMIC_CALENDAR_URL,
                parameters={"country": "US", "from": "2013-01-01", "to": "2013-03-31"},
                headers={"Accept": "application/json", "apikey": _SECRET},
                timeout_seconds=60,
                max_bytes=1024 * 1024,
            )

        self.assertEqual(response.body, b"{}")
        self.assertEqual(calls[0], {"host": "financialmodelingprep.com", "timeout": 60})
        self.assertEqual(
            calls[1]["target"],
            "/stable/economic-calendar?country=US&from=2013-01-01&to=2013-03-31",
        )
        self.assertNotIn(_SECRET, str(calls[1]["target"]))
        self.assertEqual(calls[1]["headers"], {"Accept": "application/json", "apikey": _SECRET})

    def test_semantic_replay_stays_with_publisher_and_does_not_write(self) -> None:
        publisher = _Publisher(self.lock_state, self.events, replay=True)
        transport = _Transport(self.lock_state, self.events)
        runner = self._runner(transport, publisher)

        initial = runner.run()
        replay = runner.run()

        self.assertEqual(initial.published, 56)
        self.assertEqual(initial.unchanged, 0)
        self.assertEqual(replay.published, 0)
        self.assertEqual(replay.unchanged, 0)
        self.assertEqual(replay.requested, 0)
        self.assertEqual(replay.written_versions, 0)
        self.assertEqual(publisher.write_count, 56)
        self.assertEqual(len(transport.calls), 56)
        self.assertEqual(len(self.credential.calls), 1)
        self.assertFalse(self.lock_state["held"])

    def test_explicit_continuation_skips_the_completed_prefix(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        transport = _Transport(self.lock_state, self.events, failure_at=3)
        runner = self._runner(transport, publisher)

        with self.assertRaises(StoreUnavailableError):
            runner.run()

        self.assertEqual(
            publisher.completed_windows(),
            tuple(
                (window.start_date.isoformat(), window.end_date.isoformat())
                for window in operation.FMP_MACRO_CALENDAR_WINDOWS[:2]
            ),
        )
        first_call_count = len(transport.calls)

        report = runner.run()

        self.assertEqual(first_call_count, 3)
        self.assertEqual(report.requested, 54)
        self.assertEqual(report.published, 54)
        self.assertEqual(
            transport.calls[first_call_count]["parameters"],
            operation.FMP_MACRO_CALENDAR_WINDOWS[2].parameters,
        )
        self.assertEqual(len(self.credential.calls), 2)

    def test_nonprefix_completion_fails_before_credential_or_transport(self) -> None:
        publisher = _Publisher(self.lock_state, self.events)
        second = operation.FMP_MACRO_CALENDAR_WINDOWS[1]
        publisher._completed.append(
            (second.start_date.isoformat(), second.end_date.isoformat())
        )
        transport = _Transport(self.lock_state, self.events)

        with self.assertRaisesRegex(ConflictError, "not a plan prefix"):
            self._runner(transport, publisher).run()

        self.assertEqual(self.credential.calls, [])
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
