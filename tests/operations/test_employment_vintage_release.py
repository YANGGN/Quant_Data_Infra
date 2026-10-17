from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.macro import live_vintages
from quant_data.operations import employment_vintage_release as operation


BACKFILL_NOW = datetime(2026, 8, 17, 14, 5, tzinfo=timezone.utc)
RELEASE_NOW = datetime(2026, 8, 7, 14, 5, tzinfo=timezone.utc)
OFF_WINDOW_NOW = datetime(2026, 8, 10, 14, 5, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    family: str
    normalized: str
    metadata: str


@dataclass(frozen=True, slots=True)
class _PublishReport:
    outcome: str
    semantic_identity: str
    written_versions: int


class _Parsers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict[str, object]]] = []

    def payroll(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("payroll", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed payroll workbook")
        return _Capture("payroll", "payroll-normalized-values", body.decode("utf-8"))

    def unemployment(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("unemployment", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed unemployment workbook")
        return _Capture("unemployment", "unemployment-normalized-values", body.decode("utf-8"))

    def current(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("current", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed BLS response")
        parsed = json.loads(body)
        return _Capture(
            "current",
            "|".join(str(item) for item in parsed["values"]),
            str(parsed["message"]),
        )


class _Publisher:
    def __init__(self, lock_state: dict[str, bool], *, semantic_replay: bool = False) -> None:
        self._lock_state = lock_state
        self._semantic_replay = semantic_replay
        self.calls: list[_Capture] = []
        self._published: set[tuple[str, str]] = set()

    def publish(self, capture: object) -> _PublishReport:
        if not isinstance(capture, _Capture):
            raise AssertionError("runner did not pass an opaque parser capture")
        self._lock_state["held"] = True
        try:
            self.calls.append(capture)
            identity = (capture.family, capture.normalized)
            if self._semantic_replay and identity in self._published:
                return _PublishReport("unchanged", "semantic-" + capture.normalized, 0)
            self._published.add(identity)
            return _PublishReport("published", "semantic-" + capture.normalized, 1)
        finally:
            self._lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        responses: dict[tuple[str, str], operation.EmploymentVintageTransportResponse],
        lock_state: dict[str, bool],
        *,
        fail_at: int | None = None,
    ) -> None:
        self._responses = responses
        self._lock_state = lock_state
        self._fail_at = fail_at
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.EmploymentVintageTransportResponse:
        if self._lock_state["held"]:
            raise AssertionError("transport ran while the publisher write section was active")
        self.calls.append(dict(kwargs))
        if self._fail_at == len(self.calls):
            raise StoreUnavailableError("injected one-attempt transport failure")
        key = (str(kwargs["method"]), str(kwargs["url"]))
        try:
            return self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected request {key}") from exc


class EmploymentVintageReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.lock_state = {"held": False}
        self.parsers = _Parsers()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _xlsx_response(body: bytes) -> operation.EmploymentVintageTransportResponse:
        return operation.EmploymentVintageTransportResponse(
            200,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            body,
        )

    def _responses(
        self,
        *,
        payroll_body: bytes = b"payroll metadata one",
        unemployment_body: bytes = b"unemployment metadata one",
        current_body: bytes = b'{"message":"one","values":[1,2]}',
    ) -> dict[tuple[str, str], operation.EmploymentVintageTransportResponse]:
        return {
            ("GET", operation.PHILADELPHIA_FED_PAYROLL_URL): self._xlsx_response(
                payroll_body
            ),
            ("GET", operation.PHILADELPHIA_FED_UNEMPLOYMENT_URL): self._xlsx_response(
                unemployment_body
            ),
            ("POST", operation.BLS_EMPLOYMENT_API_URL): operation.EmploymentVintageTransportResponse(
                200,
                "application/json; charset=utf-8",
                current_body,
            ),
        }

    def _runner(
        self,
        transport: _Transport,
        publisher: _Publisher,
        *,
        now: datetime,
    ) -> operation.EmploymentVintageReleaseRunner:
        return operation.EmploymentVintageReleaseRunner(
            project_root=self.project,
            macro_store=self.target,
            publisher_factory=lambda: publisher,
            transport=transport,
            parse_rtdsm_payroll_vintage_xlsx=self.parsers.payroll,
            parse_rtdsm_unemployment_vintage_xlsx=self.parsers.unemployment,
            parse_bls_employment_current_json=self.parsers.current,
            utcnow=lambda: now,
        )

    def test_manual_backfill_uses_exact_three_request_scope_outside_publish(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(transport, publisher, now=BACKFILL_NOW).run("backfill")

        self.assertEqual(
            (report.mode, report.outcome, report.requested, report.published, report.unchanged),
            ("backfill", "complete", 3, 3, 0),
        )
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(len(publisher.calls), 3)
        self.assertFalse(self.lock_state["held"])
        self.assertEqual(
            transport.calls[0],
            {
                "method": "GET",
                "url": operation.PHILADELPHIA_FED_PAYROLL_URL,
                "headers": {
                    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                },
                "body": None,
                "timeout_seconds": 60,
                "max_bytes": operation._MAX_RTDSM_BYTES,
            },
        )
        self.assertEqual(
            transport.calls[1],
            {
                "method": "GET",
                "url": operation.PHILADELPHIA_FED_UNEMPLOYMENT_URL,
                "headers": {
                    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                },
                "body": None,
                "timeout_seconds": 60,
                "max_bytes": operation._MAX_RTDSM_BYTES,
            },
        )
        current = transport.calls[2]
        self.assertEqual(
            {key: current[key] for key in ("method", "url", "headers", "timeout_seconds", "max_bytes")},
            {
                "method": "POST",
                "url": operation.BLS_EMPLOYMENT_API_URL,
                "headers": {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                },
                "timeout_seconds": 60,
                "max_bytes": operation._MAX_BLS_API_BYTES,
            },
        )
        self.assertEqual(
            json.loads(bytes(current["body"])),
            {
                "endyear": "2026",
                "seriesid": ["CES0000000001", "LNS14000000"],
                "startyear": "2017",
            },
        )
        for call in transport.calls:
            headers = dict(call["headers"])
            self.assertIn("User-Agent", headers)
            self.assertFalse({key.casefold() for key in headers} & {"apikey", "authorization"})
        self.assertEqual([call[0] for call in self.parsers.calls], ["payroll", "unemployment", "current"])
        self.assertTrue(
            all(call[2]["captured_at"] == "2026-08-17T14:05:00.000000Z" for call in self.parsers.calls)
        )

    def test_monthly_refresh_issues_exactly_one_bls_request_in_release_window(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(transport, publisher, now=RELEASE_NOW).run("refresh")

        self.assertEqual(
            (report.mode, report.outcome, report.requested, report.published),
            ("refresh", "complete", 1, 1),
        )
        self.assertEqual(
            [(call["method"], call["url"]) for call in transport.calls],
            [("POST", operation.BLS_EMPLOYMENT_API_URL)],
        )
        self.assertEqual([call[0] for call in self.parsers.calls], ["current"])

    def test_refresh_outside_first_friday_window_skips_before_factory_or_transport(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)
        runner = self._runner(transport, publisher, now=OFF_WINDOW_NOW)
        factory = mock.Mock(return_value=publisher)
        runner._publisher_factory = factory

        report = runner.run("refresh")

        self.assertEqual(
            (report.outcome, report.requested, report.published, report.unchanged, report.units),
            ("skipped_not_release_window", 0, 0, 0, ()),
        )
        factory.assert_not_called()
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.parsers.calls, [])

    def test_no_retry_and_malformed_capture_never_publishes(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(payroll_body=b"malformed"), self.lock_state)

        with self.assertRaises(ValidationError):
            self._runner(transport, publisher, now=BACKFILL_NOW).run("backfill")

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])

        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state, fail_at=1)
        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher, now=BACKFILL_NOW).run("backfill")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])

    def test_semantic_replay_delegates_changed_metadata_to_publisher_noop(self) -> None:
        publisher = _Publisher(self.lock_state, semantic_replay=True)
        first = _Transport(self._responses(), self.lock_state)
        first_report = self._runner(first, publisher, now=RELEASE_NOW).run("refresh")
        second = _Transport(
            self._responses(current_body=b'{"message":"two","values":[1,2]}'),
            self.lock_state,
        )
        second_report = self._runner(second, publisher, now=RELEASE_NOW).run("refresh")

        self.assertEqual((first_report.published, first_report.unchanged), (1, 0))
        self.assertEqual((second_report.published, second_report.unchanged), (0, 1))
        self.assertEqual([unit.written_versions for unit in second_report.units], [0])
        self.assertEqual(len(second.calls), 1)

    def test_rejects_redirect_wrong_mime_and_oversized_injected_responses(self) -> None:
        for response in (
            operation.EmploymentVintageTransportResponse(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"body",
                redirected=True,
            ),
            operation.EmploymentVintageTransportResponse(200, "text/plain", b"body"),
            operation.EmploymentVintageTransportResponse(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"x" * (operation._MAX_RTDSM_BYTES + 1),
            ),
        ):
            with self.subTest(response=response):
                publisher = _Publisher(self.lock_state)
                responses = self._responses()
                responses[("GET", operation.PHILADELPHIA_FED_PAYROLL_URL)] = response
                transport = _Transport(responses, self.lock_state)
                with self.assertRaises((StoreUnavailableError, operation.ResourceLimitError)):
                    self._runner(transport, publisher, now=BACKFILL_NOW).run("backfill")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(publisher.calls, [])

    def test_release_window_requires_first_friday_at_or_after_1005_new_york(self) -> None:
        self.assertTrue(operation.is_monthly_employment_release_window(RELEASE_NOW))
        for value in (
            datetime(2026, 8, 7, 14, 4, tzinfo=timezone.utc),
            datetime(2026, 8, 14, 14, 5, tzinfo=timezone.utc),
            OFF_WINDOW_NOW,
        ):
            with self.subTest(value=value):
                self.assertFalse(operation.is_monthly_employment_release_window(value))
        with self.assertRaises(ValidationError):
            operation.is_monthly_employment_release_window(datetime(2026, 8, 7, 10, 5))

    def test_canonical_domain_factory_is_fixed_and_refresh_never_registers(self) -> None:
        registry = object()
        publisher = mock.Mock()
        with mock.patch.multiple(
            operation,
            PROJECT_ROOT=self.project,
            MACRO_STORE=self.target,
        ), mock.patch.object(operation, "load_registry", return_value=registry) as load, mock.patch.object(
            operation, "migrate_and_register_store"
        ) as migrate, mock.patch.object(
            live_vintages.MacroLiveVintagePublisher,
            "for_canonical",
            return_value=publisher,
        ) as canonical:
            _, _, _, publisher_factory = operation._domain_api(register_target=False)
            self.assertIs(publisher_factory(), publisher)

        load.assert_called_once_with(
            self.project / operation.CANONICAL_REGISTRY_PATH,
            project_root=self.project,
            environment={},
        )
        migrate.assert_not_called()
        canonical.assert_called_once_with(registry=registry)

    def test_backfill_domain_setup_registers_before_any_provider_work(self) -> None:
        registry = object()
        publisher = mock.Mock()
        with mock.patch.multiple(
            operation,
            PROJECT_ROOT=self.project,
            MACRO_STORE=self.target,
        ), mock.patch.object(operation, "load_registry", return_value=registry), mock.patch.object(
            operation, "migrate_and_register_store"
        ) as migrate, mock.patch.object(
            live_vintages.MacroLiveVintagePublisher,
            "for_canonical",
            return_value=publisher,
        ):
            _, _, _, publisher_factory = operation._domain_api(register_target=True)
            self.assertIs(publisher_factory(), publisher)

        migrate.assert_called_once()
        args = migrate.call_args.args
        self.assertIs(args[1], registry)
        self.assertEqual(args[2], operation.StoreRole.MACRO)
        self.assertIsInstance(migrate.call_args.kwargs["applied_at"], str)
        self.assertTrue(migrate.call_args.kwargs["applied_at"].endswith("Z"))

    def test_canonical_public_wrappers_are_zero_argument_dispatchers(self) -> None:
        expected = operation.EmploymentVintageReleaseReport("backfill", "complete", 3, 3, 0, ())
        with mock.patch.object(operation, "_run_canonical", return_value=expected) as run:
            self.assertIs(operation.populate_employment_vintages_live(), expected)
        run.assert_called_once_with("backfill")

        skipped = operation.EmploymentVintageReleaseReport(
            "refresh", "skipped_not_release_window", 0, 0, 0, ()
        )
        with mock.patch.object(operation, "is_monthly_employment_release_window", return_value=False), mock.patch.object(
            operation, "_run_canonical"
        ) as run:
            self.assertEqual(operation.refresh_employment_vintages_live(), skipped)
        run.assert_not_called()

    def test_cli_accepts_only_two_fixed_modes(self) -> None:
        report = operation.EmploymentVintageReleaseReport("refresh", "complete", 1, 0, 1, ())
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with mock.patch.object(operation, "refresh_employment_vintages_live", return_value=report):
                self.assertEqual(operation.main(["--mode", "refresh"]), 0)
            self.assertEqual(operation.main([]), 64)
            self.assertEqual(
                operation.main(["--mode", "refresh", "--target", "/tmp/other.sqlite"]), 64
            )

    def test_systemd_units_are_fixed_target_nonpersistent_and_hardened(self) -> None:
        root = Path(__file__).resolve().parents[2]
        service = (root / "deploy/systemd/quant-data-employment-vintages.service").read_text(
            encoding="utf-8"
        )
        timer = (root / "deploy/systemd/quant-data-employment-vintages.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ConditionPathExists=/home/volatility/Python_Projects/Quant_Data_Infra/data/macro.sqlite",
            service,
        )
        self.assertIn(
            "ExecStart=/usr/bin/python3 -m quant_data.operations.employment_vintage_release --mode refresh",
            service,
        )
        self.assertNotIn(operation.PHILADELPHIA_FED_PAYROLL_URL, service + timer)
        self.assertNotIn(operation.PHILADELPHIA_FED_UNEMPLOYMENT_URL, service + timer)
        for value in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "UMask=0077",
            "ReadWritePaths=/home/volatility/Python_Projects/Quant_Data_Infra/data",
        ):
            self.assertIn(value, service)
        self.assertIn("OnCalendar=Fri *-*-01..07 10:05:00 America/New_York", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("Unit=quant-data-employment-vintages.service", timer)


if __name__ == "__main__":
    unittest.main()
