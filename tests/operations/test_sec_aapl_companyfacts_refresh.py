from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.operations import sec_aapl_companyfacts_refresh as operation
from quant_data.stores import StoreMap


FIXED_NOW = datetime(2026, 8, 25, 16, 30, tzinfo=timezone.utc)
AGENT_NAME = "fixture-sec-agent"
AGENT_EMAIL = "fixture@example.test"


@dataclass(frozen=True, slots=True)
class _Receipt:
    outcome: str = "published"

    def to_primitive(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "store": "company",
            "dataset_id": "fixture.company.fundamentals",
            "semantic_identity": "fixture-sec-receipt",
            "run_id": "fixture-run",
            "artifact_id": "fixture-artifact",
            "snapshot_id": "fixture-snapshot",
            "written_count": 8,
            "warnings": [],
        }


class _Transport:
    def __init__(
        self,
        events: list[str],
        lock_state: dict[str, bool],
        responses: list[operation.SecCompanyFactsTransportResponse] | None = None,
    ) -> None:
        self.events = events
        self.lock_state = lock_state
        self.responses = list(
            responses
            or [
                operation.SecCompanyFactsTransportResponse(
                    200, "application/json; charset=utf-8", b'{"filings":{}}'
                ),
                operation.SecCompanyFactsTransportResponse(
                    200, "application/json", b'{"facts":{}}'
                ),
            ]
        )
        self.calls: list[dict[str, object]] = []

    def request(
        self, **kwargs: object
    ) -> operation.SecCompanyFactsTransportResponse:
        if self.lock_state["held"]:
            raise AssertionError("network ran while the publisher lock was held")
        url = kwargs["url"]
        if url == operation.SEC_SUBMISSIONS_URL:
            self.events.append("request:submissions")
        elif url == operation.SEC_COMPANYFACTS_URL:
            self.events.append("request:companyfacts")
        else:  # pragma: no cover - operation binding assertion
            raise AssertionError("unexpected SEC endpoint")
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("unexpected retry")
        return self.responses.pop(0)


class _CredentialReader:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        name = kwargs["name"]
        self.events.append(f"credential:{name}")
        if name == "SEC_USER_AGENT_NAME":
            return AGENT_NAME
        if name == "SEC_USER_AGENT_EMAIL":
            return AGENT_EMAIL
        raise AssertionError("unexpected credential name")


class _Publisher:
    def __init__(self, events: list[str], lock_state: dict[str, bool]) -> None:
        self.events = events
        self.lock_state = lock_state
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> _Receipt:
        self.lock_state["held"] = True
        try:
            self.events.append("publish")
            self.calls.append(dict(kwargs))
            return _Receipt()
        finally:
            self.lock_state["held"] = False


class SecAaplCompanyFactsRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        data = self.project / "data"
        data.mkdir(parents=True)
        self.market = data / "market.sqlite"
        self.macro = data / "macro.sqlite"
        self.company = data / "company.sqlite"
        self.news = data / "news.sqlite"
        for path in (self.market, self.macro, self.company, self.news):
            path.write_bytes(b"fixture store")
        self.stores = StoreMap.four_explicit(
            market=self.market,
            macro=self.macro,
            company=self.company,
            news=self.news,
        )
        self.events: list[str] = []
        self.lock_state = {"held": False}
        self.credential_reader = _CredentialReader(self.events)
        self.publisher = _Publisher(self.events, self.lock_state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        transport: _Transport | None = None,
        monotonic_clock=None,
    ) -> tuple[operation.SecAaplCompanyFactsRunner, _Transport]:
        selected_transport = transport or _Transport(self.events, self.lock_state)
        runner = operation.SecAaplCompanyFactsRunner(
            project_root=self.project,
            company_store=self.company,
            stores=self.stores,
            registry=object(),
            parse_and_publish=self.publisher,
            transport=selected_transport,
            credential_environment={
                "SEC_USER_AGENT_NAME": AGENT_NAME,
                "SEC_USER_AGENT_EMAIL": AGENT_EMAIL,
            },
            credential_reader=self.credential_reader,
            utcnow=lambda: FIXED_NOW,
            monotonic_clock=monotonic_clock or operation.monotonic,
        )
        return runner, selected_transport

    def test_two_fixed_requests_are_complete_before_publication_and_redacted(self) -> None:
        runner, transport = self._runner()

        report = runner.run()

        self.assertEqual(
            self.events,
            [
                "credential:SEC_USER_AGENT_NAME",
                "credential:SEC_USER_AGENT_EMAIL",
                "request:submissions",
                "request:companyfacts",
                "publish",
            ],
        )
        self.assertEqual(len(self.credential_reader.calls), 2)
        self.assertEqual(len(transport.calls), operation.REQUEST_CAP)
        self.assertEqual(len(self.publisher.calls), 1)
        self.assertFalse(self.lock_state["held"])
        self.assertEqual(
            report.mapping(),
            {
                "requested": 2,
                "response_bytes": len(b'{"filings":{}}') + len(b'{"facts":{}}'),
                "receipt": _Receipt().to_primitive(),
            },
        )
        self.assertEqual(
            [call["url"] for call in transport.calls],
            [operation.SEC_SUBMISSIONS_URL, operation.SEC_COMPANYFACTS_URL],
        )
        self.assertEqual(
            transport.calls[0]["headers"],
            {"Accept": "application/json", "User-Agent": f"{AGENT_NAME} {AGENT_EMAIL}"},
        )
        self.assertEqual(transport.calls[0]["timeout_seconds"], 60)
        self.assertEqual(
            transport.calls[0]["max_bytes"], operation.MAX_TOTAL_RESPONSE_BYTES
        )
        self.assertEqual(
            transport.calls[1]["max_bytes"],
            operation.MAX_TOTAL_RESPONSE_BYTES - len(b'{"filings":{}}'),
        )
        publish = self.publisher.calls[0]
        self.assertIs(publish["stores"], self.stores)
        self.assertEqual(publish["submissions_body"], b'{"filings":{}}')
        self.assertEqual(publish["companyfacts_body"], b'{"facts":{}}')
        self.assertEqual(publish["captured_at"], FIXED_NOW)
        rendered = str(report.mapping())
        self.assertNotIn(AGENT_NAME, rendered)
        self.assertNotIn(AGENT_EMAIL, rendered)

    def test_second_request_failure_has_no_retry_and_never_publishes(self) -> None:
        transport = _Transport(
            self.events,
            self.lock_state,
            responses=[
                operation.SecCompanyFactsTransportResponse(
                    200, "application/json", b"{}"
                ),
                operation.SecCompanyFactsTransportResponse(
                    503, "application/json", b"{}"
                ),
            ],
        )
        runner, transport = self._runner(transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run()

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(self.publisher.calls, [])
        self.assertEqual(self.events[-1], "request:companyfacts")

    def test_deadline_stops_before_a_second_request_or_publish(self) -> None:
        ticks = iter((0.0, 0.0, 121.0))
        runner, transport = self._runner(
            monotonic_clock=lambda: next(ticks)
        )

        with self.assertRaises(ResourceLimitError):
            runner.run()

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.publisher.calls, [])

    def test_first_invalid_content_type_stops_before_second_request_or_publish(self) -> None:
        transport = _Transport(
            self.events,
            self.lock_state,
            responses=[
                operation.SecCompanyFactsTransportResponse(
                    200, "text/html", b"{}"
                )
            ],
        )
        runner, transport = self._runner(transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run()

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.publisher.calls, [])

    def test_response_byte_bound_and_total_bound_stop_before_publication(self) -> None:
        with (
            patch.object(operation, "MAX_RESPONSE_BYTES", 64),
            patch.object(operation, "MAX_TOTAL_RESPONSE_BYTES", 64),
        ):
            oversize_transport = _Transport(
                self.events,
                self.lock_state,
                responses=[
                    operation.SecCompanyFactsTransportResponse(
                        200, "application/json", b"x" * 65
                    )
                ],
            )
            runner, oversize_transport = self._runner(oversize_transport)
            with self.assertRaises(ResourceLimitError):
                runner.run()
            self.assertEqual(len(oversize_transport.calls), 1)
            self.assertEqual(self.publisher.calls, [])

        self.events.clear()
        with (
            patch.object(operation, "MAX_RESPONSE_BYTES", 64),
            patch.object(operation, "MAX_TOTAL_RESPONSE_BYTES", 64),
        ):
            total_transport = _Transport(
                self.events,
                self.lock_state,
                responses=[
                    operation.SecCompanyFactsTransportResponse(
                        200, "application/json", b"{}" + b" " * 38
                    ),
                    operation.SecCompanyFactsTransportResponse(
                        200, "application/json", b"x" * 25
                    ),
                ],
            )
            runner, total_transport = self._runner(total_transport)
            with self.assertRaises(ResourceLimitError):
                runner.run()
            self.assertEqual(len(total_transport.calls), 2)
            self.assertEqual(total_transport.calls[1]["max_bytes"], 24)
            self.assertEqual(self.publisher.calls, [])

    def test_invalid_json_stops_before_publish(self) -> None:
        transport = _Transport(
            self.events,
            self.lock_state,
            responses=[
                operation.SecCompanyFactsTransportResponse(
                    200, "application/json", b"not json"
                )
            ],
        )
        runner, transport = self._runner(transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run()

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.publisher.calls, [])

    def test_credential_shaped_values_are_rejected_before_network(self) -> None:
        def invalid_reader(**kwargs: object) -> str:
            if kwargs["name"] == "SEC_USER_AGENT_NAME":
                return "bad\r\nheader"
            return AGENT_EMAIL

        runner = operation.SecAaplCompanyFactsRunner(
            project_root=self.project,
            company_store=self.company,
            stores=self.stores,
            registry=object(),
            parse_and_publish=self.publisher,
            transport=_Transport(self.events, self.lock_state),
            credential_environment={},
            credential_reader=invalid_reader,
            utcnow=lambda: FIXED_NOW,
        )

        with self.assertRaises(ValidationError):
            runner.run()

        self.assertEqual(self.publisher.calls, [])

    def test_main_never_echoes_exception_or_scope_text(self) -> None:
        secret = "fixture-email-not-for-output@example.test"
        stdout, stderr = StringIO(), StringIO()
        with (
            patch.object(
                operation,
                "_run",
                side_effect=ValidationError(f"provider rejected {secret}"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, 64)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret, output)
        self.assertEqual(
            stderr.getvalue(),
            '{"contract":"quant_data.sec_aapl_companyfacts_error","error":"invalid_request","exit_code":64,"version":"1.0.0"}\n',
        )

    def test_main_rejects_caller_selected_arguments(self) -> None:
        stderr = StringIO()
        with patch.object(operation, "_run") as run, redirect_stderr(stderr):
            self.assertEqual(operation.main(["--cik", "0000789019"]), 64)
        run.assert_not_called()
        self.assertIn('"error":"invalid_request"', stderr.getvalue())

    def test_domain_api_binds_the_prefetched_bundle_publisher(self) -> None:
        from quant_data.company.sec_companyfacts import run_sec_aapl_companyfacts

        self.assertIs(operation._domain_api(), run_sec_aapl_companyfacts)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
