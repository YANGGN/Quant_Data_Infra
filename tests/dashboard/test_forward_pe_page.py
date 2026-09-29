"""Forward P/E reader/route boundary checks using only temporary exports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.canonical_inspector import CanonicalInspectorApplication
from quant_data.dashboard.forward_pe_page import parse_selection, read_forward_pe, render_forward_pe_page
from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from tests import test_canonical_inspector as fixture


class ForwardPePageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.name = "20260920T035241048695Z.sqlite"
        self.path = self.root / self.name
        (self.root / "current.json").write_text(json.dumps({"artifact": self.name}))
        self.window = {
            "announcement": "2026-07-30", "effective_date": "2026-07-31",
            "reported_period_end": "2026-06-27",
            "components": [
                {"period_end": end, "eps": eps} for end, eps in (
                    ("2026-09-26", "1.1"), ("2026-12-26", "2.2"),
                    ("2027-03-27", "3.3"), ("2027-06-26", "4.4"))],
            "flags": ["reconstructed_not_point_in_time", "currency_or_eps_share_basis_unverified"],
        }
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT);
                CREATE TABLE source_inputs(symbol TEXT);
                CREATE TABLE daily(symbol TEXT,trade_date TEXT,close REAL,forward_eps REAL,
                                   forward_pe_proxy REAL,status TEXT,window_id TEXT);
                CREATE INDEX daily_symbol_date ON daily(symbol,trade_date);
                CREATE TABLE windows(window_id TEXT PRIMARY KEY,details_json TEXT);
            """)
            db.executemany("INSERT INTO metadata VALUES (?,?)", [(k, json.dumps(v)) for k, v in {
                "contract": "announcement_forward_pe.v1", "cutoff": "2026-09-20T03:52:40Z",
                "is_point_in_time": False}.items()])
            db.executemany("INSERT INTO source_inputs VALUES (?)", [("AAPL",), ("EMPTY",), ("GAP",), ("LOSS",), ("ZERO",)])
            db.execute("INSERT INTO windows VALUES (?,?)", ("w1", json.dumps(self.window)))
            db.executemany("INSERT INTO daily VALUES (?,?,?,?,?,?,?)", [
                ("AAPL", "2020-01-02", 100, 10, 10, "unverified_basis", "w1"),
                ("AAPL", "2026-09-16", 220, 11, 20, "unverified_basis", "w1"),
                ("AAPL", "2026-09-17", 221, 11, 20.09, "unverified_basis", "w1"),
                ("AAPL", "2026-09-18", None, 11, None, "missing_price", "w1"),
                ("GAP", "2026-09-18", 30, None, None, "missing_estimate", None),
                ("LOSS", "2026-09-17", 30, 1, 30, "unverified_basis", "w1"),
                ("LOSS", "2026-09-18", 30, -1, -30, "expected_loss", "w1"),
                ("ZERO", "2026-09-18", 30, 0, None, "zero_forward_eps", "w1"),
            ])
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def read(self, symbol="AAPL", period="5y"):
        result = read_forward_pe(self.root, symbol, period)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.before)
        self.assertFalse(Path(str(self.path) + "-wal").exists())
        self.assertFalse(Path(str(self.path) + "-shm").exists())
        return result

    def test_range_latest_null_and_actual_fiscal_components_are_preserved(self):
        result = self.read()
        self.assertEqual([row[0] for row in result["days"]], ["2026-09-16", "2026-09-17", "2026-09-18"])
        self.assertIsNone(result["days"][-1][3])
        self.assertEqual(result["latest_price_date"], "2026-09-17")
        self.assertEqual(result["windows"]["w1"], self.window)
        self.assertEqual(len(self.read(period="all")["days"]), 4)
        page = render_forward_pe_page(result, revision="test", symbol="AAPL", period="5y")
        self.assertIn("2026-09-26", page)
        self.assertIn('class="fp-pe-value fp-pe-message">Data unavailable', page)
        self.assertIn('href="/forward-pe" aria-current="page"', page)
        self.assertIn("not the consensus known on those dates", page)
        self.assertIn("daily refresh not active", page)

    def test_missing_symbols_empty_history_and_all_gap_series(self):
        for symbol in ("UNKNOWN", "EMPTY"):
            result = self.read(symbol)
            self.assertEqual(result["days"], [])
            self.assertIn("message", result)
            page = render_forward_pe_page(result, revision="test", symbol=symbol, period="5y")
            self.assertIn("Series unavailable", page)
        result = self.read("GAP")
        self.assertIsNone(result["days"][0][3])
        self.assertIsNone(result["days"][0][2])
        page = render_forward_pe_page(result, revision="test", symbol="GAP", period="5y")
        self.assertIn("Data unavailable · missing estimate", page)
        self.assertIn("No announcement window", page)

    def test_negative_zero_and_unavailable_values_are_distinct(self):
        loss = self.read("LOSS")
        self.assertEqual(loss["days"][-1][3], -30)
        page = render_forward_pe_page(loss, revision="test", symbol="LOSS", period="5y")
        self.assertIn("-30.00×", page)
        self.assertIn("Expected loss", page)
        self.assertIn("2 of 2 ratios available", page)
        self.assertIn("1 negative (expected loss)", page)
        zero = self.read("ZERO")
        self.assertIsNone(zero["days"][-1][3])
        page = render_forward_pe_page(zero, revision="test", symbol="ZERO", period="5y")
        self.assertIn("Undefined — zero EPS", page)
        self.assertIn("0 of 1 ratios available", page)
        self.assertIn("1 undefined (zero EPS)", page)
        page = render_forward_pe_page(self.read(), revision="test", symbol="AAPL", period="5y")
        self.assertIn("2 of 3 ratios available", page)
        self.assertIn("1 data unavailable", page)

    def test_untrusted_source_text_cannot_inject_html(self):
        result = self.read()
        result["windows"]["w1"]["flags"] = ['</div><script>alert("x")</script>']
        page = render_forward_pe_page(result, revision="test", symbol="AAPL", period="5y")
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("&lt;script&gt;", page)

    def test_pointer_never_selects_paths_partial_or_symlinked_artifacts(self):
        for name in ("../escape.sqlite", self.name.replace(".sqlite", ".partial.sqlite"), "/tmp/test.sqlite"):
            (self.root / "current.json").write_text(json.dumps({"artifact": name}))
            with self.assertRaises(StoreUnavailableError):
                read_forward_pe(self.root, "AAPL", "5y")
        (self.root / "current.json").write_text(json.dumps({"artifact": self.name}))
        self.path.rename(self.root / "real.sqlite")
        self.path.symlink_to(self.root / "real.sqlite")
        with self.assertRaises(StoreUnavailableError):
            read_forward_pe(self.root, "AAPL", "5y")

    def test_missing_or_incompatible_export_fails_explicitly(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE metadata SET value_json='true' WHERE key='is_point_in_time'")
        with self.assertRaises(StoreUnavailableError):
            read_forward_pe(self.root, "AAPL", "5y")
        (self.root / "current.json").unlink()
        with self.assertRaises(StoreUnavailableError):
            read_forward_pe(self.root, "AAPL", "5y")

    def test_row_limit_fails_without_returning_a_truncated_series(self):
        with patch("quant_data.dashboard.forward_pe_page.MAX_DAYS", 2):
            with self.assertRaises(ResourceLimitError):
                read_forward_pe(self.root, "AAPL", "all")

    def test_query_bounds_and_normalization(self):
        self.assertEqual(parse_selection({"symbol": " brk.b "}), ("BRK.B", "5y"))
        for query in ({"path": "/tmp/x"}, {"sql": "SELECT 1"}, {"symbol": "../AAPL"},
                      {"symbol": "a"*21}, {"range": "50y"}, {"symbol": ""}):
            with self.assertRaises(ValidationError):
                parse_selection(query)


class ForwardPeRouteTests(unittest.TestCase):
    def setUp(self):
        self.seed = fixture.CanonicalInspectorTests()
        self.seed.setUp()
        self.addCleanup(self.seed.tearDown)
        self.app = CanonicalInspectorApplication(
            self.seed.stores, self.seed.registry,
            forward_pe_reader=lambda symbol, period: {"message": "Fixture: unavailable", "symbols": ["AAPL"]},
        )

    def test_route_assets_and_read_only_method_boundary(self):
        with patch.object(self.app.dispatcher, "call", side_effect=AssertionError("No canonical tool call")):
            response = self.app.handle("GET", "/forward-pe")
        self.assertEqual(response.status, 200)
        self.assertIn(b"Fixture: unavailable", response.body)
        self.assertEqual(self.app.handle("POST", "/forward-pe").status, 405)
        self.assertEqual(self.app.handle("DELETE", "/forward-pe").status, 405)
        for name in ("css", "js"):
            self.assertEqual(self.app.handle("GET", "/assets/forward-pe." + name).status, 200)

    def test_invalid_queries_do_not_reach_reader_and_errors_are_formatted(self):
        with patch.object(self.app, "_forward_pe_reader") as reader:
            for query in ("database=/tmp/a", "symbol=AAPL&symbol=MSFT", "range=no", "symbol=../../db"):
                self.assertEqual(self.app.handle("GET", "/forward-pe?" + query).status, 400)
            reader.assert_not_called()
            reader.side_effect = StoreUnavailableError("Snapshot unavailable")
            response = self.app.handle("GET", "/forward-pe")
        self.assertEqual(response.status, 503)
        self.assertIn(b"Snapshot unavailable", response.body)

    def test_freshness_projection_runs_after_analysis_and_keeps_the_snapshot(self):
        events=[]
        def analysis(symbol, period):
            events.append('analysis_finished')
            return dict(snapshot_id='a'*64, symbols=['AAPL'], cutoff='2026-09-22T10:30:42Z', latest_price_date='2026-09-21', days=[['2026-09-21',30,2,15,'unverified_basis',None]], windows={}, refresh={}, ticker_refresh={'outcome':'stale_inputs'})
        def projection(symbol, snapshot):
            self.assertEqual(events, ['analysis_finished'])
            self.assertEqual((symbol, snapshot), ('AAPL', 'a'*64))
            return dict(state='current', unchanged=True, note='Confirmed fixture check')
        self.app._forward_pe_reader = analysis
        self.app._derived_ticker_reader = projection
        response=self.app.handle('GET', '/forward-pe')
        self.assertEqual(response.status, 200)
        self.assertIn(b'Current', response.body)

    def test_no_ui_projection_without_a_reader_snapshot(self):
        with patch.object(self.app, '_derived_ticker_reader', side_effect=AssertionError('No snapshot')):
            self.assertEqual(self.app.handle('GET', '/forward-pe').status, 200)
