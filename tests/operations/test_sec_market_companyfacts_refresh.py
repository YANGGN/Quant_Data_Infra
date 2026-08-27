from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from quant_data.operations import sec_market_companyfacts_refresh as operation
from quant_data.stores import StoreMap


FIXED_NOW = datetime(2026, 8, 25, 17, 0, tzinfo=timezone.utc)
AGENT_NAME = "fixture-sec-market-agent"
AGENT_EMAIL = "fixture-market@example.test"


def _body(value: object) -> bytes:
    import json

    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _response(value: object, *, status: int = 200) -> operation.SecMarketCompanyFactsTransportResponse:
    return operation.SecMarketCompanyFactsTransportResponse(
        status=status,
        media_type="application/json; charset=utf-8",
        body=_body(value),
    )


@dataclass(frozen=True, slots=True)
class _Receipt:
    semantic_identity: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "semantic_identity": self.semantic_identity,
            "outcome": "published",
        }


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


class _Transport:
    def __init__(
        self,
        responses: list[operation.SecMarketCompanyFactsTransportResponse],
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(
        self, **kwargs: object
    ) -> operation.SecMarketCompanyFactsTransportResponse:
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("unexpected retry")
        return self.responses.pop(0)


class _CredentialReader:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        if kwargs["name"] == "SEC_USER_AGENT_NAME":
            return AGENT_NAME
        if kwargs["name"] == "SEC_USER_AGENT_EMAIL":
            return AGENT_EMAIL
        raise AssertionError("unexpected credential name")


class SecMarketCompanyFactsRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.data = self.project / "data"
        self.data.mkdir(parents=True)
        self.market = self.data / "market.sqlite"
        self.macro = self.data / "macro.sqlite"
        self.company = self.data / "company.sqlite"
        self.news = self.data / "news.sqlite"
        for path in (self.macro, self.news):
            path.write_bytes(b"fixture store")
        self._write_company(())
        self._write_market(
            (
                ("AAA", "equity"),
                ("BBB", "equity"),
                ("ETF", "etf"),
                ("IDX", "index"),
            )
        )
        self.stores = StoreMap.four_explicit(
            market=self.market,
            macro=self.macro,
            company=self.company,
            news=self.news,
        )
        self.clock = _Clock()
        self.credential_reader = _CredentialReader()
        self.domain_calls: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_market(self, rows: tuple[tuple[str, str], ...]) -> None:
        connection = sqlite3.connect(self.market)
        try:
            connection.execute(
                """
                CREATE TABLE stage10_instruments (
                    provider TEXT NOT NULL,
                    provider_symbol TEXT NOT NULL,
                    asset_type TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT INTO stage10_instruments VALUES ('fmp', ?, ?)", rows
            )
            connection.commit()
        finally:
            connection.close()

    def _write_company(self, ciks: tuple[str, ...]) -> None:
        connection = sqlite3.connect(self.company)
        try:
            connection.execute(
                """
                CREATE TABLE company_issuers (
                    cik TEXT NOT NULL UNIQUE
                )
                """
            )
            connection.executemany(
                "INSERT INTO company_issuers VALUES (?)", ((cik,) for cik in ciks)
            )
            connection.commit()
        finally:
            connection.close()


    def _domain(self, **kwargs: object) -> _Receipt:
        self.domain_calls.append(dict(kwargs))
        cik = str(kwargs["cik"])
        return _Receipt(semantic_identity=(cik * 7)[:64])

    def _runner(
        self,
        responses: list[operation.SecMarketCompanyFactsTransportResponse],
        *,
        domain_call=None,
        skip_ciks: frozenset[str] = frozenset(),
    ) -> tuple[operation.SecMarketCompanyFactsRunner, _Transport]:
        transport = _Transport(responses)
        runner = operation.SecMarketCompanyFactsRunner(
            project_root=self.project,
            market_store=self.market,
            company_store=self.company,
            stores=self.stores,
            registry=object(),
            domain_call=domain_call or self._domain,
            transport=transport,
            credential_environment={
                "SEC_USER_AGENT_NAME": AGENT_NAME,
                "SEC_USER_AGENT_EMAIL": AGENT_EMAIL,
            },
            credential_reader=self.credential_reader,
            skip_ciks=skip_ciks,
            utcnow=lambda: FIXED_NOW,
            monotonic_clock=self.clock.monotonic,
            sleeper=self.clock.sleep,
        )
        return runner, transport

    @staticmethod
    def _discovery(*records: tuple[int, str]) -> dict[str, object]:
        return {
            str(index): {"cik_str": cik, "ticker": ticker, "title": ticker}
            for index, (cik, ticker) in enumerate(records)
        }

    @staticmethod
    def _submissions(cik: str, *tickers: str) -> dict[str, object]:
        return {"cik": int(cik), "tickers": list(tickers), "filings": {}}

    @staticmethod
    def _facts(cik: str) -> dict[str, object]:
        return {"cik": int(cik), "facts": {}}

    def test_immutable_market_reader_preserves_quiet_sidecars_and_filters_equities(self) -> None:
        before = self.market.read_bytes()
        market_wal = self.market.with_name(self.market.name + "-wal")
        market_shm = self.market.with_name(self.market.name + "-shm")
        company_wal = self.company.with_name(self.company.name + "-wal")
        company_shm = self.company.with_name(self.company.name + "-shm")
        market_wal.write_bytes(b"")
        market_shm.write_bytes(b"s" * 32_768)
        company_wal.write_bytes(b"")
        company_shm.write_bytes(b"s" * 32_768)
        before_sidecars = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in (market_wal, market_shm, company_wal, company_shm)
        }
        seen_uris: list[str] = []
        original_connect = operation.sqlite3.connect

        def wrapped_connect(*args: object, **kwargs: object):
            seen_uris.append(str(args[0]))
            return original_connect(*args, **kwargs)

        with patch.object(operation.sqlite3, "connect", side_effect=wrapped_connect):
            symbols = operation.load_stage10_equity_symbols(self.market)

        self.assertEqual(symbols, ("AAA", "BBB"))
        self.assertEqual(self.market.read_bytes(), before)
        self.assertEqual(len(seen_uris), 1)
        self.assertTrue(seen_uris[0].startswith("file:/proc/self/fd/"))
        self.assertTrue(seen_uris[0].endswith("?mode=ro&immutable=1"))
        self.assertEqual(
            {
                path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                for path in before_sidecars
            },
            before_sidecars,
        )
        # The same quiet-sidecar policy applies to the company target during
        # runner preflight; a fake domain avoids opening it for this test.
        runner, _ = self._runner(
            [
                _response(self._discovery((111, "AAA"), (222, "BBB"))),
                _response(self._submissions("0000000111", "AAA")),
                _response(self._facts("0000000111")),
                _response(self._submissions("0000000222", "BBB")),
                _response(self._facts("0000000222")),
            ]
        )
        self.assertTrue(runner.run().complete)
        self.assertEqual(
            {
                path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                for path in before_sidecars
            },
            before_sidecars,
        )

    def test_residual_selector_skips_existing_company_ciks(self) -> None:
        connection = sqlite3.connect(self.company)
        try:
            connection.execute(
                "INSERT INTO company_issuers VALUES (?)", ("0000000111",)
            )
            connection.commit()
        finally:
            connection.close()

        before = self.company.read_bytes()
        company_wal = self.company.with_name(self.company.name + "-wal")
        company_shm = self.company.with_name(self.company.name + "-shm")
        company_wal.write_bytes(b"")
        company_shm.write_bytes(b"s" * 32_768)
        before_sidecars = {
            path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in (company_wal, company_shm)
        }
        existing = operation.load_company_issuer_ciks(self.company)
        self.assertEqual(existing, frozenset({"0000000111"}))

        runner, transport = self._runner(
            [
                _response(self._discovery((111, "AAA"), (222, "BBB"))),
                _response(self._submissions("0000000222", "BBB")),
                _response(self._facts("0000000222")),
            ],
            skip_ciks=existing,
        )
        report = runner.run()

        self.assertTrue(report.complete)
        self.assertEqual(report.unique_cik_count, 2)
        self.assertEqual(report.selected_cik_count, 1)
        self.assertEqual(report.skipped_existing_cik_count, 1)
        self.assertEqual(report.requested, 3)
        self.assertEqual([item["cik"] for item in self.domain_calls], ["0000000222"])
        self.assertEqual(
            [call["url"] for call in transport.calls],
            [
                operation.SEC_TICKERS_URL,
                "https://data.sec.gov/submissions/CIK0000000222.json",
                "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000222.json",
            ],
        )
        self.assertEqual(self.company.read_bytes(), before)
        self.assertEqual(
            {
                path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
                for path in before_sidecars
            },
            before_sidecars,
        )


    def test_exact_mapping_reports_unmatched_ambiguous_and_rejected_ciks(self) -> None:
        plans, unmatched, ambiguous, rejected = operation.plan_sec_market_issuers(
            symbols=("AAA", "BBB", "MISSING", "ZERO"),
            discovery_body=_body(
                self._discovery(
                    (111, "AAA"),
                    (222, "BBB"),
                    (333, "BBB"),
                    (0, "ZERO"),
                )
            ),
        )

        self.assertEqual(plans, (operation._IssuerPlan("0000000111", ("AAA",)),))
        self.assertEqual(unmatched, ("MISSING", "ZERO"))
        self.assertEqual(ambiguous, ("BBB",))
        self.assertEqual(rejected, 1)

    def test_multi_symbol_cik_is_deduplicated_and_urls_are_fixed(self) -> None:
        responses = [
            _response(self._discovery((111, "AAA"), (111, "BBB"))),
            _response(self._submissions("0000000111", "AAA", "BBB")),
            _response(self._facts("0000000111")),
        ]
        runner, transport = self._runner(responses)

        report = runner.run()

        self.assertTrue(report.complete)
        self.assertEqual(report.planned_symbol_count, 2)
        self.assertEqual(report.matched_symbol_count, 2)
        self.assertEqual(report.unique_cik_count, 1)
        self.assertEqual(report.succeeded_cik_count, 1)
        self.assertEqual(len(self.domain_calls), 1)
        self.assertEqual(self.domain_calls[0]["cik"], "0000000111")
        self.assertEqual(
            [call["url"] for call in transport.calls],
            [
                operation.SEC_TICKERS_URL,
                "https://data.sec.gov/submissions/CIK0000000111.json",
                "https://data.sec.gov/api/xbrl/companyfacts/CIK0000000111.json",
            ],
        )
        self.assertEqual(
            transport.calls[0]["headers"],
            {"Accept": "application/json", "User-Agent": f"{AGENT_NAME} {AGENT_EMAIL}"},
        )
        self.assertEqual(report.requested, 3)

    def test_global_pacing_and_no_retry_are_enforced(self) -> None:
        responses = [
            _response(self._discovery((111, "AAA"), (222, "BBB"))),
            _response(self._submissions("0000000111", "AAA")),
            _response(self._facts("0000000111")),
            _response(self._submissions("0000000222", "BBB")),
            _response(self._facts("0000000222")),
        ]
        runner, transport = self._runner(responses)

        report = runner.run()

        self.assertTrue(report.complete)
        self.assertEqual(len(transport.calls), 5)
        self.assertEqual(report.requested, 5)
        self.assertEqual(len(self.clock.sleeps), 4)
        self.assertTrue(
            all(
                abs(item - operation.MIN_REQUEST_INTERVAL_SECONDS) < 1e-9
                for item in self.clock.sleeps
            )
        )
        self.assertEqual(transport.responses, [])

    def test_failure_isolation_continues_after_one_issuer_pair_failure(self) -> None:
        connection = sqlite3.connect(self.market)
        try:
            connection.execute(
                "INSERT INTO stage10_instruments VALUES ('fmp', 'CCC', 'equity')"
            )
            connection.commit()
        finally:
            connection.close()
        responses = [
            _response(self._discovery((111, "AAA"), (222, "BBB"), (333, "CCC"))),
            _response(self._submissions("0000000111", "AAA")),
            _response(self._facts("0000000111")),
            _response(self._submissions("0000000222", "BBB")),
            _response({}, status=503),
            _response(self._submissions("0000000333", "CCC")),
            _response(self._facts("0000000333")),
        ]
        runner, transport = self._runner(responses)

        report = runner.run()

        self.assertFalse(report.complete)
        self.assertEqual(report.failed_cik_count, 1)
        self.assertEqual(dict(report.failure_stage_counts), {"companyfacts_request": 1})
        self.assertEqual(dict(report.failure_kind_counts), {"store_unavailable": 1})
        self.assertEqual(report.succeeded_cik_count, 2)
        self.assertEqual(len(transport.calls), 7)
        self.assertEqual([item["cik"] for item in self.domain_calls], ["0000000111", "0000000333"])
        self.assertEqual(transport.responses, [])

    def test_direct_submissions_ticker_mismatch_never_reaches_domain(self) -> None:
        responses = [
            _response(self._discovery((111, "AAA"), (222, "BBB"))),
            _response(self._submissions("0000000111", "OTHER")),
            _response(self._facts("0000000111")),
            _response(self._submissions("0000000222", "BBB")),
            _response(self._facts("0000000222")),
        ]
        runner, transport = self._runner(responses)

        report = runner.run()

        self.assertFalse(report.complete)
        self.assertEqual(report.ticker_mismatch_cik_count, 1)
        self.assertEqual(report.failed_cik_count, 1)
        self.assertEqual(dict(report.failure_stage_counts), {"ticker_confirmation": 1})
        self.assertEqual(dict(report.failure_kind_counts), {"validation": 1})
        self.assertEqual([item["cik"] for item in self.domain_calls], ["0000000222"])
        self.assertEqual(len(transport.calls), 5)

    def test_empty_submissions_tickers_preserve_unique_discovery_match(self) -> None:
        responses = [
            _response(self._discovery((111, "AAA"), (222, "BBB"))),
            _response(self._submissions("0000000111")),
            _response(self._facts("0000000111")),
            _response(self._submissions("0000000222", "BBB")),
            _response(self._facts("0000000222")),
        ]
        runner, transport = self._runner(responses)

        report = runner.run()

        self.assertTrue(report.complete)
        self.assertEqual(report.ticker_mismatch_cik_count, 0)
        self.assertEqual(
            [item["cik"] for item in self.domain_calls],
            ["0000000111", "0000000222"],
        )
        self.assertEqual(len(transport.calls), 5)

    def test_failure_receipt_and_cli_never_leak_user_agent_components(self) -> None:
        secret = f"{AGENT_NAME} {AGENT_EMAIL}"

        def failing_domain(**kwargs: object) -> _Receipt:
            raise RuntimeError(f"publisher refused {secret}")

        responses = [
            _response(self._discovery((111, "AAA"), (222, "BBB"))),
            _response(self._submissions("0000000111", "AAA")),
            _response(self._facts("0000000111")),
            _response(self._submissions("0000000222", "BBB")),
            _response(self._facts("0000000222")),
        ]
        runner, _ = self._runner(responses, domain_call=failing_domain)

        report = runner.run()

        self.assertFalse(report.complete)
        self.assertEqual(dict(report.failure_stage_counts), {"domain_call": 2})
        self.assertEqual(dict(report.failure_kind_counts), {"internal": 2})
        rendered = str(report.mapping())
        self.assertNotIn(AGENT_NAME, rendered)
        self.assertNotIn(AGENT_EMAIL, rendered)
        stdout, stderr = StringIO(), StringIO()
        with patch.object(operation, "_run", return_value=report), redirect_stdout(stdout), redirect_stderr(stderr):
            code = operation.main()
        self.assertEqual(code, 1)
        self.assertNotIn(AGENT_NAME, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(AGENT_EMAIL, stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
