from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from quant_data.errors import StoreUnavailableError, ValidationError
from quant_data.operations import macro_history_extension as operation


FIXED_NOW = datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Capture:
    parser: str
    body: bytes
    arguments: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class _PublishReport:
    outcome: str
    semantic_identity: str
    written_versions: int


class _Parsers:
    def __init__(self, lock_state: dict[str, bool]) -> None:
        self._lock_state = lock_state
        self.calls: list[_Capture] = []

    def _parse(self, parser: str, body: bytes, **kwargs: object) -> _Capture:
        if self._lock_state["held"]:
            raise AssertionError("parser ran while publisher write section was active")
        capture = _Capture(parser, body, tuple(sorted(kwargs.items())))
        self.calls.append(capture)
        return capture

    def bls(self, body: bytes, **kwargs: object) -> _Capture:
        return self._parse("bls", body, **kwargs)

    def noutput(self, body: bytes, **kwargs: object) -> _Capture:
        return self._parse("noutput", body, **kwargs)

    def routput(self, body: bytes, **kwargs: object) -> _Capture:
        return self._parse("routput", body, **kwargs)

    def pcpi(self, body: bytes, **kwargs: object) -> _Capture:
        return self._parse("pcpi", body, **kwargs)

    def pcpix(self, body: bytes, **kwargs: object) -> _Capture:
        return self._parse("pcpix", body, **kwargs)

    def mapping(self) -> dict[str, object]:
        return {
            "bls_cpi_history": self.bls,
            "rtdsm_nominal_output": self.noutput,
            "rtdsm_real_output": self.routput,
            "rtdsm_cpi_all_items": self.pcpi,
            "rtdsm_cpi_core": self.pcpix,
        }


class _Publisher:
    def __init__(self, lock_state: dict[str, bool], *, replay: bool = False) -> None:
        self._lock_state = lock_state
        self._replay = replay
        self._published: set[tuple[str, bytes]] = set()
        self.calls: list[_Capture] = []

    def publish(self, capture: object) -> _PublishReport:
        if not isinstance(capture, _Capture):
            raise AssertionError("runner did not pass the opaque parser result")
        self._lock_state["held"] = True
        try:
            self.calls.append(capture)
            identity = (capture.parser, capture.body)
            if self._replay and identity in self._published:
                return _PublishReport("unchanged", "semantic-replay", 0)
            self._published.add(identity)
            return _PublishReport("published", "semantic-" + capture.parser, 1)
        finally:
            self._lock_state["held"] = False


class _Transport:
    def __init__(
        self,
        responses: dict[tuple[str, str], operation.MacroHistoryTransportResponse],
        lock_state: dict[str, bool],
        *,
        fail_at: int | None = None,
    ) -> None:
        self._responses = responses
        self._lock_state = lock_state
        self._fail_at = fail_at
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.MacroHistoryTransportResponse:
        if self._lock_state["held"]:
            raise AssertionError("transport ran while publisher write section was active")
        self.calls.append(dict(kwargs))
        if self._fail_at == len(self.calls):
            raise StoreUnavailableError("injected one-attempt failure")
        key = (str(kwargs["method"]), str(kwargs["url"]))
        try:
            return self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected transport call {key}") from exc


class MacroHistoryExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.lock_state = {"held": False}
        self.parsers = _Parsers(self.lock_state)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _response_for(
        source: operation.MacroHistorySource,
    ) -> operation.MacroHistoryTransportResponse:
        body = ("response:" + source.identifier).encode("utf-8")
        return operation.MacroHistoryTransportResponse(
            200,
            source.media_type + "; charset=utf-8",
            body,
        )

    def _responses(self) -> dict[tuple[str, str], operation.MacroHistoryTransportResponse]:
        return {
            (source.method, source.source_resource): self._response_for(source)
            for source in operation.MACRO_HISTORY_SOURCES
        }

    @staticmethod
    def _sealed(source: operation.MacroHistorySource, captured_at: str) -> operation.SealedMacroHistoryResponse:
        body = ("sealed:" + source.identifier).encode("utf-8")
        return operation.SealedMacroHistoryResponse(
            identifier=source.identifier,
            body=body,
            response_sha256=hashlib.sha256(body).hexdigest(),
            captured_at=captured_at,
        )

    def _runner(
        self,
        transport: _Transport,
        publisher: _Publisher,
        *,
        sealed: tuple[operation.SealedMacroHistoryResponse, ...] = (),
    ) -> operation.MacroHistoryExtensionRunner:
        return operation.MacroHistoryExtensionRunner(
            project_root=self.project,
            macro_store=self.target,
            sources=operation.MACRO_HISTORY_SOURCES,
            parsers=self.parsers.mapping(),
            publisher_factory=lambda: publisher,
            transport=transport,
            preseeded_responses=sealed,
            utcnow=lambda: FIXED_NOW,
        )

    def test_backfill_uses_exact_fixed_windows_and_reuses_sealed_responses(self) -> None:
        sources = operation.MACRO_HISTORY_SOURCES
        sealed = (
            self._sealed(sources[0], "2026-08-17T16:20:53.745543Z"),
            self._sealed(sources[7], "2026-08-17T16:15:36.774677Z"),
            self._sealed(sources[8], "2026-08-17T16:15:49.245983Z"),
            self._sealed(sources[9], "2026-08-17T16:15:49.719992Z"),
            self._sealed(sources[10], "2026-08-17T16:15:50.097609Z"),
        )
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(transport, publisher, sealed=sealed).run(operation.BACKFILL_MODE)

        self.assertEqual(
            (report.mode, report.requested, report.published, report.unchanged),
            (operation.BACKFILL_MODE, 11, 11, 0),
        )
        self.assertEqual([call["url"] for call in transport.calls], [source.source_resource for source in sources[1:7]])
        self.assertEqual(len(self.parsers.calls), 11)
        self.assertEqual(len(publisher.calls), 11)
        self.assertFalse(self.lock_state["held"])
        self.assertEqual([unit.identifier for unit in report.units], [source.identifier for source in sources])

        for source, call in zip(sources[1:7], transport.calls, strict=True):
            self.assertEqual(
                call,
                {
                    "method": "POST",
                    "url": operation.BLS_CPI_API_URL,
                    "headers": {
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 (compatible; QuantDataInfra/1.0; +https://www.bls.gov/)",
                        "Content-Type": "application/json",
                    },
                    "body": source.request_body,
                    "timeout_seconds": 60,
                    "max_bytes": operation._MAX_BLS_API_BYTES,
                },
            )
            request = json.loads(bytes(call["body"]))
            self.assertEqual(request["startyear"], str(source.start_year))
            self.assertEqual(request["endyear"], str(source.end_year))
            self.assertEqual(request["seriesid"], ["CUSR0000SA0", "CUSR0000SA0L1E"])

        bls_calls = [capture for capture in self.parsers.calls if capture.parser == "bls"]
        self.assertEqual(len(bls_calls), 7)
        self.assertEqual(
            dict(bls_calls[0].arguments),
            {
                "captured_at": "2026-08-17T16:20:53.745543Z",
                "end_year": 1956,
                "start_year": 1947,
            },
        )
        self.assertTrue(
            all(
                dict(capture.arguments)["captured_at"] == "2026-08-17T18:00:00.000000Z"
                for capture in bls_calls[1:]
            )
        )
        self.assertEqual(
            [capture.parser for capture in self.parsers.calls[7:]],
            ["noutput", "routput", "pcpi", "pcpix"],
        )
        self.assertEqual(
            [dict(capture.arguments)["captured_at"] for capture in self.parsers.calls[7:]],
            [item.captured_at for item in sealed[1:]],
        )

    def test_cpi_mode_uses_sealed_first_window_and_no_other_source_family(self) -> None:
        first = operation.MACRO_HISTORY_SOURCES[0]
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        report = self._runner(
            transport,
            publisher,
            sealed=(self._sealed(first, "2026-08-17T16:20:53.745543Z"),),
        ).run(operation.CPI_CURRENT_HISTORY_MODE)

        self.assertEqual(report.requested, 7)
        self.assertEqual(len(transport.calls), 6)
        self.assertTrue(all(call["url"] == operation.BLS_CPI_API_URL for call in transport.calls))
        self.assertTrue(all(capture.parser == "bls" for capture in self.parsers.calls))

    def test_rejects_bad_response_without_retry_or_publication(self) -> None:
        first = operation.MACRO_HISTORY_SOURCES[0]
        responses = self._responses()
        responses[(first.method, first.source_resource)] = operation.MacroHistoryTransportResponse(
            200,
            "text/html",
            b"unexpected",
        )
        publisher = _Publisher(self.lock_state)
        transport = _Transport(responses, self.lock_state)

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run(operation.CPI_CURRENT_HISTORY_MODE)

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(self.parsers.calls, [])

    def test_rejects_redirect_without_retry_or_publication(self) -> None:
        first = operation.MACRO_HISTORY_SOURCES[0]
        responses = self._responses()
        responses[(first.method, first.source_resource)] = operation.MacroHistoryTransportResponse(
            302,
            "application/json",
            b"redirect",
            redirected=True,
        )
        publisher = _Publisher(self.lock_state)
        transport = _Transport(responses, self.lock_state)

        with self.assertRaises(StoreUnavailableError):
            self._runner(transport, publisher).run(operation.CPI_CURRENT_HISTORY_MODE)

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(publisher.calls, [])
        self.assertEqual(self.parsers.calls, [])

    def test_rejects_sealed_digest_mismatch_before_network(self) -> None:
        source = operation.MACRO_HISTORY_SOURCES[0]
        body = b"sealed body"
        bad = operation.SealedMacroHistoryResponse(
            identifier=source.identifier,
            body=body,
            response_sha256="0" * 64,
            captured_at="2026-08-17T16:20:53.745543Z",
        )
        publisher = _Publisher(self.lock_state)
        transport = _Transport(self._responses(), self.lock_state)

        with self.assertRaises(ValidationError):
            self._runner(transport, publisher, sealed=(bad,))

        self.assertEqual(transport.calls, [])
        self.assertEqual(publisher.calls, [])

    def test_sealed_file_loader_rejects_digest_drift(self) -> None:
        path = Path(self.temporary.name) / "sealed.json"
        initial = b'{"retained":true}'
        path.write_bytes(initial)
        os.chmod(path, 0o600)
        result = operation.load_sealed_preflight_response(
            path=path,
            identifier="bls-cpi-history-1947-1956",
            expected_sha256=hashlib.sha256(initial).hexdigest(),
            expected_bytes=len(initial),
            captured_at="2026-08-17T16:20:53.745543Z",
        )
        self.assertEqual(result.body, initial)

        path.write_bytes(b'{"retained":false}')
        os.chmod(path, 0o600)
        with self.assertRaises(StoreUnavailableError):
            operation.load_sealed_preflight_response(
                path=path,
                identifier="bls-cpi-history-1947-1956",
                expected_sha256=hashlib.sha256(initial).hexdigest(),
                expected_bytes=len(initial),
                captured_at="2026-08-17T16:20:53.745543Z",
            )

    def test_canonical_source_table_has_seven_nonoverlapping_ten_year_bls_windows(self) -> None:
        sources = operation.MACRO_HISTORY_SOURCES
        bls = sources[:7]
        self.assertEqual(
            [(source.start_year, source.end_year) for source in bls],
            [
                (1947, 1956),
                (1957, 1966),
                (1967, 1976),
                (1977, 1986),
                (1987, 1996),
                (1997, 2006),
                (2007, 2007),
            ],
        )
        self.assertEqual(json.loads(bytes(bls[0].request_body))["seriesid"], ["CUSR0000SA0"])
        self.assertTrue(
            all(
                json.loads(bytes(source.request_body))["seriesid"]
                == ["CUSR0000SA0", "CUSR0000SA0L1E"]
                for source in bls[1:]
            )
        )
        self.assertEqual([source.method for source in sources[7:]], ["GET"] * 4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
