"""Focused offline tests for the bounded Stage 11 transient retry policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import http.client
import json
import unittest
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.macro.stage11_bea import (
    BEA_NIPA_MAX_RESPONSE_BYTES,
    BEA_NIPA_PATH,
    BEA_NIPA_TIMEOUT_SECONDS,
    CapturedBeaResponse,
    StdlibBeaTransport,
    capture_bea_nipa,
    prepare_bea_nipa_capture,
)
from quant_data.macro.stage11_eia import (
    EIA_RETAIL_MAX_RESPONSE_BYTES,
    EIA_RETAIL_PATH,
    EIA_TIMEOUT_SECONDS,
    CapturedEiaResponse,
    StdlibEiaTransport,
    capture_eia_retail_page,
    capture_eia_weekly,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)
from quant_data.macro.stage11_retry import (
    STAGE11_MAX_REQUEST_ATTEMPTS,
    STAGE11_RETRY_BACKOFF_SECONDS,
    Stage11TransientRequestError,
    transient_error_for_http_status,
    transient_error_from_transport_exception,
)
from quant_data.operations.stage11_backfill import _RequestPacer, _capture_with_retry, main
from tests.operations import test_stage11_backfill as stage11_backfill_tests


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
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    def get(self, **_kwargs: object) -> object:
        self.calls += 1
        return self.response


class _RaisingTransport:
    def __init__(self, error_factory: Callable[[], BaseException]) -> None:
        self._error_factory = error_factory
        self.calls = 0

    def get(self, **_kwargs: object) -> object:
        self.calls += 1
        raise self._error_factory()


class Stage11RetryTests(unittest.TestCase):
    def _pacer(self, clock: _Clock) -> _RequestPacer:
        return _RequestPacer(
            interval_seconds=1.0,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            force_initial_delay=False,
        )

    def test_policy_constants_and_exact_transient_classification(self) -> None:
        self.assertEqual(STAGE11_MAX_REQUEST_ATTEMPTS, 3)
        self.assertEqual(STAGE11_RETRY_BACKOFF_SECONDS, (1.0, 2.0))
        for status in (429, 500, 503, 599):
            with self.subTest(status=status):
                self.assertIsInstance(
                    transient_error_for_http_status(status),
                    Stage11TransientRequestError,
                )
        for status in (301, 400, 401, 403, 404, 408, 410, 422, 600):
            with self.subTest(status=status):
                self.assertIsNone(transient_error_for_http_status(status))
        self.assertEqual(
            transient_error_from_transport_exception(TimeoutError()).category,
            "timeout",
        )
        self.assertEqual(
            transient_error_from_transport_exception(http.client.RemoteDisconnected()).category,
            "connection",
        )

    def test_runner_retries_only_typed_transients_with_fixed_backoff(self) -> None:
        clock = _Clock()
        calls = 0

        def capture(_prepared: object, _key: str, _transport: object) -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise Stage11TransientRequestError("http_5xx")
            return "captured"

        value, attempts = _capture_with_retry(
            capture,
            object(),
            "credential-not-retained",
            object(),
            pacer=self._pacer(clock),
            sleeper=clock.sleep,
        )
        self.assertEqual((value, attempts, calls), ("captured", 3, 3))
        self.assertEqual(clock.sleeps, [1.0, 2.0])

        for error in (
            StoreUnavailableError("one shot"),
            ValidationError("one shot"),
            ResourceLimitError("one shot"),
        ):
            with self.subTest(error=type(error).__name__):
                one_shot_calls = 0

                def terminal(_prepared: object, _key: str, _transport: object) -> object:
                    nonlocal one_shot_calls
                    one_shot_calls += 1
                    raise error

                with self.assertRaises(type(error)):
                    _capture_with_retry(
                        terminal,
                        object(),
                        "credential-not-retained",
                        object(),
                        pacer=self._pacer(_Clock()),
                        sleeper=lambda _seconds: self.fail("terminal error slept"),
                    )
                self.assertEqual(one_shot_calls, 1)

    def test_runner_stops_after_third_transient_attempt(self) -> None:
        clock = _Clock()
        calls = 0

        def capture(_prepared: object, _key: str, _transport: object) -> object:
            nonlocal calls
            calls += 1
            raise Stage11TransientRequestError("http_429")

        with self.assertRaises(Stage11TransientRequestError):
            _capture_with_retry(
                capture,
                object(),
                "credential-not-retained",
                object(),
                pacer=self._pacer(clock),
                sleeper=clock.sleep,
            )
        self.assertEqual(calls, 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_injected_bea_and_eia_statuses_preserve_retry_boundary(self) -> None:
        bea_request = prepare_bea_nipa_capture("T10101")
        eia_request = prepare_eia_retail_page()
        for status in (429, 503):
            with self.subTest(provider="bea", status=status):
                transport = _Transport(CapturedBeaResponse(status, "text/plain", b"x"))
                with self.assertRaises(Stage11TransientRequestError):
                    capture_bea_nipa(bea_request, api_key="offline-key", transport=transport)
                self.assertEqual(transport.calls, 1)
            with self.subTest(provider="eia", status=status):
                transport = _Transport(CapturedEiaResponse(status, "text/plain", b"x"))
                with self.assertRaises(Stage11TransientRequestError):
                    capture_eia_retail_page(eia_request, api_key="offline-key", transport=transport)
                self.assertEqual(transport.calls, 1)

        for status in (302, 401, 408, 422):
            with self.subTest(provider="bea", status=status):
                transport = _Transport(CapturedBeaResponse(status, "application/json", b"{}"))
                with self.assertRaises(StoreUnavailableError) as raised:
                    capture_bea_nipa(bea_request, api_key="offline-key", transport=transport)
                self.assertNotIsInstance(raised.exception, Stage11TransientRequestError)
            with self.subTest(provider="eia", status=status):
                transport = _Transport(CapturedEiaResponse(status, "application/json", b"{}"))
                with self.assertRaises(StoreUnavailableError) as raised:
                    capture_eia_retail_page(eia_request, api_key="offline-key", transport=transport)
                self.assertNotIsInstance(raised.exception, Stage11TransientRequestError)

    def test_stdlib_transient_status_precedes_body_and_close_failure_is_ignored(self) -> None:
        bea_request = prepare_bea_nipa_capture("T10101")
        response = mock.Mock()
        response.status = 503
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch(
            "quant_data.macro.stage11_bea.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaises(Stage11TransientRequestError):
                StdlibBeaTransport().get(
                    path=BEA_NIPA_PATH,
                    query={**bea_request.query, "UserID": "offline-key"},
                    headers={"Accept": "application/json"},
                    timeout_seconds=BEA_NIPA_TIMEOUT_SECONDS,
                    max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES,
                )
        response.getheader.assert_not_called()
        response.read.assert_not_called()

        eia_request = prepare_eia_retail_page()
        response = mock.Mock()
        response.status = 302
        response.getheader.side_effect = lambda name: {
            "Content-Length": "2",
            "Content-Type": "application/json",
        }.get(name)
        response.read.return_value = b"{}"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        connection.close.side_effect = OSError("close after complete response")
        with mock.patch(
            "quant_data.macro.stage11_eia.http.client.HTTPSConnection",
            return_value=connection,
        ):
            captured = StdlibEiaTransport().get(
                path=EIA_RETAIL_PATH,
                query={**eia_request.query, "api_key": "offline-key"},
                headers={"Accept": "application/json"},
                timeout_seconds=EIA_TIMEOUT_SECONDS,
                max_bytes=EIA_RETAIL_MAX_RESPONSE_BYTES,
            )
        self.assertEqual(captured.status, 302)


    def _run_raw_eia_exhaustion(
        self,
        *,
        phase: str,
        error_factory: Callable[[], BaseException],
    ) -> None:
        fixture = stage11_backfill_tests.Stage11BackfillTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture._stage10_dependency()
        events: list[str] = []
        dependencies = fixture._dependencies(events)
        bindings = dependencies["bindings"]
        raw_transport = _RaisingTransport(error_factory)

        if phase == "retail":
            def capture_retail(prepared: object, key: str, _transport: object) -> object:
                return capture_eia_retail_page(
                    prepared,
                    api_key=key,
                    transport=raw_transport,
                )

            dependencies["bindings"] = replace(
                bindings,
                capture_retail_page=capture_retail,
            )
            expected_state = (True, False, False)
        elif phase == "weekly":
            def capture_weekly(prepared: object, key: str, _transport: object) -> object:
                return capture_eia_weekly(
                    prepared,
                    api_key=key,
                    transport=raw_transport,
                )

            dependencies["bindings"] = replace(
                bindings,
                capture_weekly=capture_weekly,
            )
            expected_state = (True, True, False)
        else:
            self.fail("unsupported EIA retry phase")

        stdout, stderr = fixture._streams()
        clock = _Clock()
        result = main(
            fixture._arguments(),
            stdout=stdout,
            stderr=stderr,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
            completion_callback=lambda _context: self.fail("completion after failed capture"),
            **dependencies,
        )
        self.assertEqual(result, 69)
        fixture._assert_error(result, stdout, stderr, "unavailable")
        self.assertEqual(raw_transport.calls, STAGE11_MAX_REQUEST_ATTEMPTS)
        self.assertEqual(
            clock.sleeps[-2:],
            list(STAGE11_RETRY_BACKOFF_SECONDS),
        )
        state = json.loads(
            (fixture.target / "private" / "candidate" / "stage11" / "resume.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            (state["bea_published"], state["retail_published"], state["weekly_published"]),
            expected_state,
        )
        self.assertNotIn("publish:prepared-weekly", events)
        if phase == "retail":
            self.assertNotIn("publish:prepared-retail", events)

    def test_raw_eia_transport_errors_retry_then_exit_unavailable(self) -> None:
        for phase, error_factory in (
            ("retail", lambda: TimeoutError("offline timeout")),
            ("weekly", lambda: http.client.HTTPException("offline transport")),
        ):
            with self.subTest(phase=phase):
                self._run_raw_eia_exhaustion(
                    phase=phase,
                    error_factory=error_factory,
                )

if __name__ == "__main__":
    unittest.main()
