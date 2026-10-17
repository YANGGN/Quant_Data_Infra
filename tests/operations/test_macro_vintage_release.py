from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.macro import live_vintages
from quant_data.operations import macro_vintage_release as operation


FIXED_NOW = datetime(2026, 8, 17, 14, 5, tzinfo=timezone.utc)
LAST_MODIFIED = "Fri, 13 Feb 2026 13:30:00 GMT"


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

    def bea(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("bea", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed workbook")
        # The parser's normalized candidate intentionally omits volatile
        # response metadata; the publisher decides semantic replay.
        return _Capture("bea", "bea-normalized-values", body.decode("utf-8"))

    def revision(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("revision", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed archive")
        return _Capture("revision", str(kwargs["source_resource"]), body.decode("utf-8"))

    def current(self, body: bytes, **kwargs: object) -> _Capture:
        self.calls.append(("current", body, kwargs))
        if body == b"malformed":
            raise ValidationError("injected malformed API response")
        parsed = json.loads(body)
        return _Capture("current", "|".join(str(item) for item in parsed["values"]), parsed["message"])


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
        responses: dict[tuple[str, str], operation.MacroVintageTransportResponse],
        lock_state: dict[str, bool],
        *,
        fail_at: int | None = None,
    ) -> None:
        self._responses = responses
        self._lock_state = lock_state
        self._fail_at = fail_at
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.MacroVintageTransportResponse:
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


class MacroVintageReleaseTests(unittest.TestCase):
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
    def _archive_response(resource: object) -> operation.MacroVintageTransportResponse:
        media_type = str(getattr(resource, "media_type"))
        return operation.MacroVintageTransportResponse(
            200,
            media_type + "; charset=utf-8",
            ("archive:" + str(getattr(resource, "identifier"))).encode("utf-8"),
            source_published_at=LAST_MODIFIED,
        )

    def _responses(
        self,
        *,
        bea_body: bytes = b"bea response metadata one",
        current_body: bytes = b'{"message":"one","values":[1,2]}',
    ) -> dict[tuple[str, str], operation.MacroVintageTransportResponse]:
        result: dict[tuple[str, str], operation.MacroVintageTransportResponse] = {
            ("GET", operation.BEA_GDP_VINTAGE_URL): operation.MacroVintageTransportResponse(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                bea_body,
                source_published_at=LAST_MODIFIED,
            ),
            ("POST", operation.BLS_CPI_API_URL): operation.MacroVintageTransportResponse(
                200,
                "application/json; charset=utf-8",
                current_body,
            ),
        }
        result.update(
            {
                ("GET", resource.source_resource): self._archive_response(resource)
                for resource in operation._BLS_CPI_ARCHIVES
            }
        )
        return result

    def _runner(
        self,
        transport: _Transport,
        publisher: _Publisher,
    ) -> operation.MacroVintageReleaseRunner:
        return operation.MacroVintageReleaseRunner(
            project_root=self.project,
            macro_store=self.target,
            publisher_factory=lambda: publisher,
            transport=transport,
            parse_bea_gdp_vintage_xlsx=self.parsers.bea,
            parse_bls_cpi_revision=self.parsers.revision,
            parse_bls_current_json=self.parsers.current,
            utcnow=lambda: FIXED_NOW,
        )

    def test_backfill_uses_only_the_exact_official_scope_outside_publish(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(transport, publisher).run("backfill")

        self.assertEqual((report.mode, report.requested, report.published, report.unchanged), ("backfill", 16, 16, 0))
        self.assertEqual(len(transport.calls), 16)
        self.assertEqual(len(publisher.calls), 16)
        self.assertFalse(self.lock_state["held"])
        self.assertEqual(
            transport.calls[0],
            {
                "method": "GET",
                "url": operation.BEA_GDP_VINTAGE_URL,
                "headers": {
                    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                },
                "body": None,
                "timeout_seconds": 60,
                "max_bytes": operation._MAX_BEA_BYTES,
            },
        )
        for index, resource in enumerate(operation._BLS_CPI_ARCHIVES, start=1):
            self.assertEqual(
                transport.calls[index],
                {
                    "method": "GET",
                    "url": resource.source_resource,
                    "headers": {
                        "Accept": resource.media_type,
                        "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                    },
                    "body": None,
                    "timeout_seconds": 60,
                    "max_bytes": operation._MAX_BLS_ARCHIVE_BYTES,
                },
            )
        current = transport.calls[-1]
        self.assertEqual(
            {key: current[key] for key in ("method", "url", "headers", "timeout_seconds", "max_bytes")},
            {
                "method": "POST",
                "url": operation.BLS_CPI_API_URL,
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
                "seriesid": ["CUSR0000SA0", "CUSR0000SA0L1E"],
                "startyear": "2017",
            },
        )
        for call in transport.calls:
            headers = dict(call["headers"])
            self.assertEqual(
                headers.get("User-Agent"),
                "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
            )
            self.assertFalse({key.casefold() for key in headers} & {"apikey", "authorization"})
        archive_parser_calls = [call for call in self.parsers.calls if call[0] == "revision"]
        self.assertEqual(len(archive_parser_calls), len(operation._BLS_CPI_ARCHIVES))
        self.assertTrue(
            all(call[2]["vintage_at"] == "2026-02-13" for call in archive_parser_calls)
        )
        self.assertTrue(
            all(call[2]["captured_at"] == "2026-08-17T14:05:00.000000Z" for call in self.parsers.calls)
        )

    def test_refresh_uses_bea_and_bls_current_only(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(transport, publisher).run("refresh")

        self.assertEqual((report.mode, report.requested, report.published, report.unchanged), ("refresh", 2, 2, 0))
        self.assertEqual(
            [(call["method"], call["url"]) for call in transport.calls],
            [("GET", operation.BEA_GDP_VINTAGE_URL), ("POST", operation.BLS_CPI_API_URL)],
        )
        self.assertEqual([call[0] for call in self.parsers.calls], ["bea", "current"])

    def test_no_retry_and_malformed_capture_never_publishes(self) -> None:
        publisher = _Publisher(self.lock_state)
        transport = _Transport(
            self._responses(bea_body=b"malformed"), self.lock_state
        )

        with self.assertRaises(ValidationError):
            self._runner(transport, publisher).run("refresh")

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])

    def test_archive_requires_provider_last_modified_before_any_archive_publish(self) -> None:
        publisher = _Publisher(self.lock_state)
        responses = self._responses()
        first_archive = operation._BLS_CPI_ARCHIVES[0]
        responses[("GET", first_archive.source_resource)] = operation.MacroVintageTransportResponse(
            200,
            first_archive.media_type,
            b"archive without a source vintage",
        )
        transport = _Transport(responses, self.lock_state)

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run("backfill")

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual([capture.family for capture in publisher.calls], ["bea"])
        self.assertEqual([call[0] for call in self.parsers.calls], ["bea"])

        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state, fail_at=1)
        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run("refresh")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])

    def test_semantic_replay_ignores_changed_response_metadata(self) -> None:
        """A changed byte payload is still passed to the domain no-op decision."""

        publisher = _Publisher(self.lock_state, semantic_replay=True)
        first = _Transport(self._responses(), self.lock_state)
        first_report = self._runner(first, publisher).run("refresh")
        second = _Transport(
            self._responses(
                bea_body=b"bea response metadata two",
                current_body=b'{"message":"two","values":[1,2]}',
            ),
            self.lock_state,
        )
        second_report = self._runner(second, publisher).run("refresh")

        self.assertEqual((first_report.published, first_report.unchanged), (2, 0))
        self.assertEqual((second_report.published, second_report.unchanged), (0, 2))
        self.assertEqual([unit.written_versions for unit in second_report.units], [0, 0])
        self.assertEqual(len(second.calls), 2)

    def test_rejects_redirect_wrong_mime_and_oversized_injected_responses(self) -> None:
        for response in (
            operation.MacroVintageTransportResponse(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"body",
                redirected=True,
            ),
            operation.MacroVintageTransportResponse(200, "text/plain", b"body"),
            operation.MacroVintageTransportResponse(
                200,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                b"x" * (operation._MAX_BEA_BYTES + 1),
            ),
        ):
            with self.subTest(response=response):
                publisher = _Publisher(self.lock_state)
                responses = self._responses()
                responses[("GET", operation.BEA_GDP_VINTAGE_URL)] = response
                transport = _Transport(responses, self.lock_state)
                with self.assertRaises((StoreUnavailableError, operation.ResourceLimitError)):
                    self._runner(transport, publisher).run("refresh")
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(publisher.calls, [])

    def test_canonical_public_wrappers_are_zero_argument_dispatchers(self) -> None:
        expected = operation.MacroVintageReleaseReport("refresh", 2, 0, 2, ())
        with mock.patch.object(operation, "_run_canonical", return_value=expected) as run:
            self.assertIs(operation.populate_gdp_cpi_vintages_live(), expected)
            self.assertIs(operation.refresh_gdp_cpi_vintages_live(), expected)
        self.assertEqual(
            [call.args for call in run.call_args_list],
            [("backfill",), ("refresh",)],
        )

    def test_refresh_domain_factory_uses_fixed_publisher_without_registration(self) -> None:
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

    def test_cli_accepts_only_the_two_fixed_modes(self) -> None:
        report = operation.MacroVintageReleaseReport("refresh", 2, 0, 2, ())
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with mock.patch.object(operation, "refresh_gdp_cpi_vintages_live", return_value=report):
                self.assertEqual(operation.main(["--mode", "refresh"]), 0)
            self.assertEqual(operation.main([]), 64)
            self.assertEqual(
                operation.main(["--mode", "refresh", "--target", "/tmp/other.sqlite"]), 64
            )

    def test_systemd_units_are_fixed_target_nonpersistent_and_hardened(self) -> None:
        root = Path(__file__).resolve().parents[2]
        service = (root / "deploy/systemd/quant-data-macro-vintages.service").read_text(
            encoding="utf-8"
        )
        timer = (root / "deploy/systemd/quant-data-macro-vintages.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("ConditionPathExists=/home/volatility/Python_Projects/Quant_Data_Infra/data/macro.sqlite", service)
        self.assertIn("ExecStart=/usr/bin/python3 -m quant_data.operations.macro_vintage_release --mode refresh", service)
        for value in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "UMask=0077",
            "ReadWritePaths=/home/volatility/Python_Projects/Quant_Data_Infra/data",
        ):
            self.assertIn(value, service)
        self.assertIn("OnCalendar=Mon..Fri *-*-* 09:05:00 America/New_York", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("Unit=quant-data-macro-vintages.service", timer)


if __name__ == "__main__":
    unittest.main()
