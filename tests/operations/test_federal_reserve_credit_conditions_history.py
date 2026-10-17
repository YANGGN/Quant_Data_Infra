"""Focused offline tests for the injected H.8/SLOOS history operation."""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.operations import federal_reserve_credit_conditions_history as operation
from quant_data.operations.official_conditions_history import CsvResponse
from urllib.parse import parse_qs, urlsplit


FIXED_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    source_key: str
    observations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Publication:
    outcome: str
    written_series: int
    written_observation_versions: int


class _Parser:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        bodies: tuple[bytes, ...],
        *,
        source_key: str,
        **kwargs: object,
    ) -> _Capture:
        self.events.append("parser")
        self.calls.append(
            {"bodies": bodies, "source_key": source_key, **kwargs}
        )
        count = 7 if source_key == operation.H8_SOURCE_KEY else 6
        if len(bodies) != count:
            raise AssertionError("unexpected response count")
        return _Capture(source_key, tuple(str(item) for item in range(count)))


class _Publisher:
    def __init__(self, events: list[str], outcome: str = "published") -> None:
        self.events = events
        self.outcome = outcome
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _Publication:
        if not isinstance(capture, _Capture):
            raise AssertionError("unexpected capture")
        self.events.append("publish")
        self.calls.append(capture)
        unchanged = self.outcome == "unchanged"
        count = len(capture.observations)
        return _Publication(
            self.outcome,
            0 if unchanged else count,
            0 if unchanged else count,
        )


class _Transport:
    def __init__(
        self,
        events: list[str],
        response: CsvResponse | None = None,
        *,
        fail_at: int | None = None,
    ) -> None:
        self.events = events
        self.response = response
        self.fail_at = fail_at
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> CsvResponse:
        self.events.append("transport")
        self.calls.append(dict(kwargs))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            return CsvResponse(
                status=503,
                media_type="text/csv",
                redirected=False,
                body=b"unavailable",
            )
        if self.response is not None:
            return self.response
        parameters = {key: values[0] for key, values in parse_qs(urlsplit(str(kwargs["url"])).query).items()}
        if not isinstance(parameters, dict):
            raise AssertionError("missing parameters")
        code = parameters["id"]
        return CsvResponse(
            status=200,
            media_type="text/csv; charset=utf-8",
            redirected=False,
            body=(
                f"observation_date,{code}\n2026-08-21,1\n"
            ).encode("ascii"),
        )


class FederalReserveCreditHistoryRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.events: list[str] = []
        self.parser = _Parser(self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(
        self,
        *,
        publisher: _Publisher | None = None,
        transport: _Transport | None = None,
        canonical: bool = False,
    ):
        selected_publisher = publisher or _Publisher(self.events)
        selected_transport = transport or _Transport(self.events)

        def factory(source_key: str) -> _Publisher:
            self.events.append("publisher_factory")
            self.assertIn(
                source_key,
                {operation.H8_SOURCE_KEY, operation.SLOOS_SOURCE_KEY},
            )
            return selected_publisher

        return (
            operation.FederalReserveCreditHistoryRunner(
                project_root=self.project,
                macro_store=self.target,
                parser=self.parser,
                publisher_factory=factory,
                transport=selected_transport,
                utcnow=lambda: FIXED_NOW,
                _canonical=canonical,
            ),
            selected_publisher,
            selected_transport,
        )

    def test_h8_singleton_requests_all_finish_before_parse_and_publish(self) -> None:
        runner, publisher, transport = self._runner()

        report = runner.run_h8(
            start_date="2026-08-14",
            end_date="2026-08-21",
        )

        self.assertEqual(
            self.events,
            ["transport"] * 7 + ["parser", "publisher_factory", "publish"],
        )
        self.assertEqual(
            (
                report.requested,
                report.published,
                report.normalized_rows,
                report.written_series,
            ),
            (7, 1, 7, 7),
        )
        expected_codes = operation.SOURCE_METADATA_BY_KEY[
            operation.H8_SOURCE_KEY
        ].fred_series
        self.assertEqual(
            tuple(parse_qs(urlsplit(call["url"]).query)["id"][0] for call in transport.calls),
            expected_codes,
        )
        self.assertEqual(
            tuple(parse_qs(urlsplit(call["url"]).query)["cosd"][0] for call in transport.calls),
            ("2026-08-14",) * 7,
        )
        self.assertEqual(
            tuple(parse_qs(urlsplit(call["url"]).query)["coed"][0] for call in transport.calls),
            ("2026-08-21",) * 7,
        )
        self.assertTrue(
            all(call["url"].split("?", 1)[0] == operation.FRED_GRAPH_URL for call in transport.calls)
        )
        self.assertTrue(
            all(
                call["headers"]
                == {"Accept": "text/csv", "User-Agent": "QuantDataInfra/1.0"}
                for call in transport.calls
            )
        )
        self.assertTrue(
            all(call["timeout_seconds"] == 60 for call in transport.calls)
        )
        bodies = self.parser.calls[0]["bodies"]
        self.assertIsInstance(bodies, tuple)
        remaining = operation.MAX_TOTAL_RESPONSE_BYTES
        for call, body in zip(transport.calls, bodies):
            self.assertEqual(call["max_bytes"], remaining)
            remaining -= len(body)
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(operation.MAX_REQUESTS, 7)

    def test_sloos_unchanged_uses_six_singleton_requests(self) -> None:
        unchanged = _Publisher(self.events, outcome="unchanged")
        runner, _, transport = self._runner(publisher=unchanged)

        report = runner.run_sloos(
            start_date="2026-01-01",
            end_date="2026-04-30",
        )

        self.assertEqual(
            (report.requested, report.unchanged, report.written_series),
            (6, 1, 0),
        )
        self.assertEqual(
            tuple(parse_qs(urlsplit(call["url"]).query)["id"][0] for call in transport.calls),
            operation.SOURCE_METADATA_BY_KEY[
                operation.SLOOS_SOURCE_KEY
            ].fred_series,
        )

    def test_midstream_failure_stops_before_parser_or_publisher(self) -> None:
        transport = _Transport(self.events, fail_at=4)
        runner, publisher, _ = self._runner(transport=transport)

        with self.assertRaises(StoreUnavailableError):
            runner.run_h8(
                start_date="2026-08-21",
                end_date="2026-08-21",
            )

        self.assertEqual(self.events, ["transport"] * 4)
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_invalid_response_and_window_fail_closed(self) -> None:
        redirect = _Transport(
            self.events,
            CsvResponse(
                status=302,
                media_type="text/csv",
                body=b"observation_date,x\n",
                redirected=True,
            ),
        )
        runner, publisher, _ = self._runner(transport=redirect)
        with self.assertRaises(StoreUnavailableError):
            runner.run_h8(
                start_date="2026-08-21",
                end_date="2026-08-21",
            )
        self.assertEqual(self.parser.calls, [])
        self.assertEqual(publisher.calls, [])
        with self.assertRaises(ValidationError):
            runner.run_h8(
                start_date="2026-08-22",
                end_date="2026-08-21",
            )
        window = operation.FederalReserveCreditWindow(
            operation.H8_SOURCE_KEY,
            date(2026, 8, 21),
            date(2026, 8, 21),
        )
        with self.assertRaisesRegex(ValidationError, "fixed manifest"):
            window.parameters_for("OUTSIDE")

    def test_canonical_runner_rejects_fixture_target(self) -> None:
        with self.assertRaisesRegex(ValidationError, "canonical target"):
            self._runner(canonical=True)

    def test_live_wrapper_and_cli_shape_are_network_free(self) -> None:
        report = operation.FederalReserveCreditHistoryReport(
            requested=7,
            published=1,
            unchanged=0,
            normalized_rows=7,
            written_series=7,
            written_observation_versions=7,
            source_key=operation.H8_SOURCE_KEY,
            start_date=date(2026, 8, 14),
            end_date=date(2026, 8, 21),
        )
        with (
            patch.object(
                operation,
                "_require_canonical_target",
            ) as require_target,
            patch.object(
                operation,
                "_domain_api",
                return_value=(object(), object()),
            ) as domain_api,
            patch.object(
                operation,
                "FederalReserveCreditHistoryRunner",
            ) as runner_type,
        ):
            runner_type.return_value.run.return_value = report
            actual = operation.populate_federal_reserve_credit_conditions_live(
                source_key=operation.H8_SOURCE_KEY,
                start_date="2026-08-14",
                end_date="2026-08-21",
            )

        self.assertIs(actual, report)
        require_target.assert_called_once_with()
        domain_api.assert_called_once_with()
        construction = runner_type.call_args.kwargs
        self.assertEqual(
            (construction["project_root"], construction["macro_store"]),
            (operation.PROJECT_ROOT, operation.MACRO_STORE),
        )
        self.assertIsInstance(
            construction["transport"],
            operation.StdlibCsvTransport,
        )
        self.assertTrue(construction["_canonical"])
        runner_type.return_value.run.assert_called_once_with(
            source_key=operation.H8_SOURCE_KEY,
            start_date="2026-08-14",
            end_date="2026-08-21",
        )

        with patch.object(
            operation,
            "populate_federal_reserve_credit_conditions_live",
            return_value=report,
        ) as populate:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    operation.main(
                        [
                            "--source-key",
                            operation.H8_SOURCE_KEY,
                            "--from",
                            "2026-08-14",
                            "--to",
                            "2026-08-21",
                        ]
                    ),
                    0,
                )
        self.assertEqual(
            json.loads(output.getvalue())["source_key"],
            operation.H8_SOURCE_KEY,
        )
        populate.assert_called_once_with(
            source_key=operation.H8_SOURCE_KEY,
            start_date="2026-08-14",
            end_date="2026-08-21",
        )


if __name__ == "__main__":
    unittest.main()
