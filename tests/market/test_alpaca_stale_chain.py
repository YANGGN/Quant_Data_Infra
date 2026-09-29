import copy
import json
import unittest
from quant_data.errors import ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.market.alpaca_options import _grid_catalog_snapshots
from quant_data.stores import read_connection
from tests.market.test_alpaca_etf_options import GridTransport


class StaleChainScopeTests(unittest.TestCase):
    def check(self, symbol="XLE261218C00072000", snapshot=None):
        if snapshot is None:
            snapshot = {"latestTrade": {"p": "1", "t": "2025-11-11T19:01:52Z"}}
        return _grid_catalog_snapshots(
            {symbol: snapshot}, [{"contract_symbol": "XLE261218C00050000"}],
            underlying_symbol="XLE", selected_expiration="2026-12-18",
            spot_price="61.82", session_date="2026-09-22",
            completed_at="2026-09-22T20:21:00Z",
        )

    def test_only_proven_stale_in_scope_extra_is_excluded_without_mutating_input(self):
        original = {"latestTrade": {"p": "1", "t": "2025-11-11T19:01:52Z"}}
        before = copy.deepcopy(original)
        filtered, excluded = self.check(snapshot=original)
        self.assertEqual(filtered, {})
        self.assertEqual(excluded, ("XLE261218C00072000",))
        self.assertEqual(original, before)

    def test_unknown_current_future_undated_and_invalid_observations_fail(self):
        cases = [
            {"latestTrade": {"t": "2026-09-22T19:00:00Z"}},
            {"latestTrade": {"t": "2026-09-23T19:00:00Z"}},
            {"latestTrade": {"p": "1"}},
            {},
            {"latestTrade": {"t": "2025-11-11"}},
            {"latestTrade": {"t": "2025-11-11T19:00:00Z"},
             "dailyBar": {"t": "2026-09-22T04:00:00Z"}},
            {"latestTrade": {"t": "2025-11-11T19:00:00Z"},
             "latestQuote": {"t": "2026-09-22T19:00:00Z"}},
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), self.assertRaises(ValidationError):
                self.check(snapshot=snapshot)

    def test_wrong_underlying_expiry_strike_and_malformed_contract_fail(self):
        for symbol in ("XLK261218C00072000", "XLE270319C00072000",
                       "XLE261218C00999000", "XLE1261218C00072000",
                       "XLE261218C0007200X"):
            with self.subTest(symbol=symbol), self.assertRaises(ValidationError):
                self.check(symbol=symbol)

    def test_known_quotes_stay_subject_to_existing_surface_validation(self):
        snapshot = {"latestTrade": {"t": "2026-09-22T19:00:00Z"}}
        values = {"XLE261218C00050000": snapshot}
        filtered, excluded = _grid_catalog_snapshots(
            values, [{"contract_symbol": "XLE261218C00050000"}],
            underlying_symbol="XLE", selected_expiration="2026-12-18",
            spot_price="61.82", session_date="2026-09-22",
            completed_at="2026-09-22T20:21:00Z")
        self.assertIs(filtered, values)
        self.assertEqual(excluded, ())

    def test_oversized_exclusion_scope_fails_before_publication(self):
        from datetime import datetime, timezone
        from quant_data.errors import ResourceLimitError
        from quant_data.market import alpaca_options as a

        transport = GridTransport()
        def response(path, **query):
            return transport.request(path=path, query=query).body

        completed = datetime(2026, 8, 3, 19, 55, tzinfo=timezone.utc)
        underlying_body = response("/v2/stocks/snapshots")
        underlying = a._grid_underlying_snapshot(
            a._grid_underlying_snapshot_payload(underlying_body),
            symbol="XLE", completed_at=completed)
        catalog = response("/v2/options/contracts", underlying_symbols="XLE")
        chain = json.loads(response("/v1beta1/options/snapshots/XLE",
                                    expiration_date="2026-08-04"))
        for strike in range(101000, 101800):
            chain["snapshots"][f"XLE260804C{strike:08d}"] = {
                "latestTrade": {"t": "2025-11-11T19:00:00Z"}}
        with self.assertRaisesRegex(ResourceLimitError, "JSON output.*byte limit"):
            a._parse_alpaca_etf_options_capture(
                calendar_body=response("/v3/calendar/OPRA"),
                underlying_body=underlying_body, underlying_snapshot=underlying,
                catalog_rows=a._grid_catalog_page(catalog)[0],
                catalog_bodies=(catalog,), chain_bodies=(json.dumps(chain).encode(),),
                underlying_symbol="XLE", selected_expiration="2026-08-04",
                target_dtes=(1,), session_date="2026-08-03",
                requested_at=completed, completed_at=completed)

    def test_publication_retains_original_raw_excludes_unknown_and_replays_zero_writes(self):
        from tests.market.test_alpaca_etf_options import AlpacaEtfOptionsTests
        fixture = AlpacaEtfOptionsTests("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        class StaleExtraTransport(GridTransport):
            def request(self, **kwargs):
                response = super().request(**kwargs)
                path = kwargs["path"]
                if path.endswith("/XLE") or path.endswith("/XLK"):
                    symbol = path.rsplit("/", 1)[-1]
                    expiration = kwargs["query"]["expiration_date"][2:].replace("-", "")
                    payload = json.loads(response.body)
                    payload["snapshots"][symbol + expiration + "C00101000"] = {
                        "latestTrade": {"p": "5", "t": "2025-11-11T19:00:00Z"}}
                    return self._response(payload)
                return response

        report = fixture._run(StaleExtraTransport())
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.failed_underlyings, ())
        self.assertEqual(report.contracts, 30)
        with read_connection(fixture.stores, "market") as c:
            scopes = [json.loads(r[0]) for r in c.execute(
                "SELECT request_scope_json FROM option_surface_captures")]
            excluded = [r for r in scopes if r.get("excluded_stale_chain_symbols")]
            self.assertEqual({r["underlying_symbol"] for r in excluded}, {"XLE", "XLK"})
            self.assertTrue(all(len(r["excluded_stale_chain_symbols"]) == 1 for r in excluded))
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM option_contracts WHERE contract_symbol LIKE '%C00101000'"
            ).fetchone()[0], 0)
            raw = [bytes(r[0]) for r in c.execute("SELECT response_body FROM option_raw_responses")]
            self.assertTrue(any(b"XLE260804C00101000" in body for body in raw))
            self.assertTrue(any(b"XLK260804C00101000" in body for body in raw))
        before = mutation_fingerprint(fixture.stores)
        again = fixture._run(StaleExtraTransport())
        self.assertEqual(again.written_count, 0)
        self.assertEqual(before, mutation_fingerprint(fixture.stores))
