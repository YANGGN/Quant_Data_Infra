from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
import zlib

from quant_data.company.forward_pe import (
    SPLIT_ONLY, daily_series, effective_session, make_windows, period_mapping,
)
from quant_data.operations.forward_pe import materialize, session_calendar

CAPTURE = "2026-09-19T00:00:00Z"
CUTOFF = "2026-09-20T00:00:00Z"
SESSIONS = [
    ("2023-12-04", "2023-12-04T21:00:00Z"),
    ("2023-12-05", "2023-12-05T21:00:00Z"),
    ("2023-12-06", "2023-12-06T21:00:00Z"),
    ("2023-12-07", "2023-12-07T21:00:00Z"),
    ("2023-12-08", "2023-12-08T21:00:00Z"),
    ("2023-12-11", "2023-12-11T21:00:00Z"),
]


def fixture():
    ends = ["2023-10-31", "2024-01-31", "2024-04-30", "2024-07-31", "2024-10-31", "2025-01-31"]
    estimates = []
    statements = []
    for i, end in enumerate(ends):
        estimates.append(dict(natural_identity=end, observation_version_id="e" + str(i),
            captured_at=CAPTURE, target_period_end=end, currency="USD", estimate_basis="non_GAAP",
            payload_json=json.dumps(dict(epsAvg=i + 1, shareBasis="ordinary_split_adjusted",
                                         splitBasisDate="2026-09-19", numAnalystsEps=5))))
        statements.append(dict(natural_identity=end, period_end=end, captured_at=CAPTURE,
            fiscal_year=2024 if i < 4 else 2025, fiscal_period="Q" + str(i % 4 + 1),
            payload_json=json.dumps({"revenue": 1000 + i})))
    return dict(symbol="ODD", issuer_id="issuer", cik="0000000001", instrument_id="instrument",
        cutoff=CUTOFF, start="2023-12-04", end="2023-12-11",
        estimates=estimates, statements=statements, transcripts=[],
        earnings=[dict(natural_identity="earn1", observation_version_id="a1", captured_at=CAPTURE,
            source_event_date="2023-12-05", source_time_raw="2023-12-05T21:05:00Z",
            event_precision="datetime", payload_json=json.dumps(dict(epsActual=1, revenueActual=1000)))],
        prices=[dict(trade_date=d, version_id="p" + str(i), captured_at=CAPTURE, close_value=str(140 + i * 14))
                for i, (d, _) in enumerate(SESSIONS)],
        price_basis=SPLIT_ONLY, price_currency="USD",
        share_basis="ordinary_split_adjusted", split_basis_date="2026-09-19")


class ForwardPeTests(unittest.TestCase):
    def test_actual_release_roll_noncalendar_fiscal_year_and_daily_price(self):
        inputs = fixture()
        windows = make_windows(inputs, SESSIONS)
        self.assertEqual(windows[0]["reported_period_end"], "2023-10-31")
        self.assertEqual(windows[0]["effective_date"], "2023-12-06")
        self.assertEqual(windows[0]["forward_eps"], "14")
        self.assertEqual([c["period_end"] for c in windows[0]["components"]],
                         ["2024-01-31", "2024-04-30", "2024-07-31", "2024-10-31"])
        rows = list(daily_series(inputs, SESSIONS, windows))
        self.assertIsNone(rows[1]["forward_pe_proxy"])
        self.assertEqual(rows[2]["forward_pe_proxy"], "12")
        self.assertEqual(rows[3]["forward_pe_proxy"], "13")

    def test_before_close_after_close_exact_close_date_only_and_weekend(self):
        for timestamp, expected in [
            ("2023-12-05T13:00:00Z", "2023-12-05"),
            ("2023-12-05T20:59:59Z", "2023-12-05"),
            ("2023-12-05T16:00:00-05:00", "2023-12-06"),
            ("2023-12-05", "2023-12-06"),
            ("2023-12-09", "2023-12-11"),
        ]:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(effective_session(timestamp, SESSIONS)[0], expected)

    def test_real_calendar_holiday_and_early_close(self):
        sessions = session_calendar("2024-12-03")
        self.assertEqual(effective_session("2024-11-28", sessions)[0], "2024-11-29")
        self.assertEqual(effective_session("2024-11-29T18:00:00Z", sessions)[0], "2024-12-02")
        self.assertEqual(effective_session("2024-11-29T17:59:00Z", sessions)[0], "2024-11-29")

    def test_scheduled_event_does_not_advance(self):
        inputs = fixture()
        inputs["earnings"][0]["payload_json"] = json.dumps(dict(epsActual=None, revenueActual=None))
        self.assertEqual(make_windows(inputs, SESSIONS), [])

    def test_duplicate_release_and_old_amendment_do_not_roll_back(self):
        inputs = fixture()
        duplicate = deepcopy(inputs["earnings"][0])
        duplicate.update(natural_identity="amendment", observation_version_id="a2",
                         source_event_date="2023-12-07", source_time_raw="2023-12-07")
        inputs["earnings"].append(duplicate)
        self.assertEqual(len(make_windows(inputs, SESSIONS)), 1)

    def test_missing_quarter_is_not_skipped_for_fifth(self):
        inputs = fixture()
        inputs["estimates"].pop(2)
        window = make_windows(inputs, SESSIONS)[0]
        self.assertIsNone(window["forward_eps"])
        self.assertIn("missing_estimate", window["flags"])

    def test_gap_in_both_period_sources_is_not_four_consecutive_quarters(self):
        inputs = fixture()
        inputs["estimates"].pop(2)
        inputs["statements"].pop(2)
        self.assertIn("nonconsecutive_quarters", make_windows(inputs, SESSIONS)[0]["flags"])

    def test_fifty_three_week_year_and_nearby_provider_date(self):
        inputs = fixture()
        inputs["statements"][1]["period_end"] = "2024-02-03"
        window = make_windows(inputs, SESSIONS)[0]
        self.assertEqual(window["components"][0]["period_end"], "2024-02-03")
        self.assertEqual(window["components"][0]["source_period_end"], "2024-01-31")
        self.assertEqual(window["forward_eps"], "14")

    def test_ambiguous_target_period_is_blocked(self):
        inputs = fixture()
        extra = deepcopy(inputs["estimates"][1])
        extra.update(natural_identity="ambiguous", target_period_end="2024-02-02",
                     observation_version_id="other")
        inputs["estimates"].append(extra)
        self.assertIn("ambiguous_estimate_period", make_windows(inputs, SESSIONS)[0]["flags"])

    def test_nonfinite_or_missing_eps_is_missing_not_zero(self):
        for value in (None, "NaN", "Infinity", True):
            inputs = fixture()
            payload = json.loads(inputs["estimates"][1]["payload_json"])
            payload["epsAvg"] = value
            inputs["estimates"][1]["payload_json"] = json.dumps(payload)
            self.assertIn("missing_eps", make_windows(inputs, SESSIONS)[0]["flags"])

    def test_signed_negative_and_undefined_zero_eps(self):
        for value, expected_status, expected_ratio in ((-1, "expected_loss", "-42"),
                                                       (0, "zero_forward_eps", None)):
            with self.subTest(value=value):
                inputs = fixture()
                for estimate in inputs["estimates"]:
                    payload = json.loads(estimate["payload_json"])
                    payload["epsAvg"] = value
                    estimate["payload_json"] = json.dumps(payload)
                window = make_windows(inputs, SESSIONS)[0]
                self.assertEqual(window["ratio_allowed"], value < 0)
                self.assertEqual(window["forward_eps"], str(value * 4))
                self.assertEqual(window["status"], expected_status)
                self.assertIn("negative_forward_eps" if value < 0 else "zero_forward_eps", window["flags"])
                row = list(daily_series(inputs, SESSIONS, [window]))[2]
                self.assertEqual(row["status"], expected_status)
                self.assertEqual(row["forward_pe_proxy"], expected_ratio)

    def test_expected_loss_preserves_missing_price_and_basis_blockers(self):
        inputs = fixture()
        for estimate in inputs["estimates"]:
            payload = json.loads(estimate["payload_json"])
            payload["epsAvg"] = -1
            estimate["payload_json"] = json.dumps(payload)
        for field, value, expected in (("price_currency", "EUR", "currency_mismatch"),
                                      ("share_basis", "other", "share_basis_mismatch"),
                                      ("price_basis", "not_established", "unsupported_price_basis")):
            with self.subTest(field=field):
                window = make_windows(dict(inputs, **{field: value}), SESSIONS, allow_unverified_basis=True)[0]
                self.assertFalse(window["ratio_allowed"])
                self.assertEqual(window["status"], expected)
                self.assertIn("negative_forward_eps", window["flags"])
        inputs["price_currency"] = None
        strict = make_windows(inputs, SESSIONS)[0]
        self.assertFalse(strict["ratio_allowed"])
        self.assertEqual(strict["status"], "unverified_basis")
        window = make_windows(inputs, SESSIONS, allow_unverified_basis=True)[0]
        self.assertTrue(window["ratio_allowed"])
        self.assertEqual(window["status"], "expected_loss")
        self.assertIn("currency_or_eps_share_basis_unverified", window["flags"])
        inputs["prices"].pop(2)
        row = list(daily_series(inputs, SESSIONS, [window]))[2]
        self.assertEqual(row["status"], "missing_price")
        self.assertIsNone(row["forward_pe_proxy"])

    def test_signed_policy_is_recorded_and_negative_ratio_is_saved(self):
        from quant_data.company.forward_pe import RATIO_POLICY
        inputs = fixture()
        for estimate in inputs["estimates"]:
            payload = json.loads(estimate["payload_json"])
            payload["epsAvg"] = -1
            estimate["payload_json"] = json.dumps(payload)
        with TemporaryDirectory() as root:
            output = Path(root) / "signed.sqlite"
            metadata = materialize(output, [inputs], lambda _: inputs, SESSIONS, cutoff=CUTOFF,
                start=inputs["start"], end=inputs["end"], allow_unverified_basis=False)
            self.assertEqual(metadata["ratio_policy"], RATIO_POLICY)
            self.assertEqual(metadata["summary"][0]["numeric_pe"], 4)
            with sqlite3.connect(output.resolve().as_uri()+"?mode=ro&immutable=1", uri=True) as db:
                self.assertEqual(db.execute("SELECT forward_pe_proxy,status FROM daily_forward_pe "
                    "WHERE trade_date='2023-12-06'").fetchone(), (-42.0, "expected_loss"))

    def test_missing_price_not_forward_filled(self):
        inputs = fixture()
        inputs["prices"].pop(3)
        rows = list(daily_series(inputs, SESSIONS, make_windows(inputs, SESSIONS)))
        self.assertEqual(rows[3]["status"], "missing_price")
        self.assertIsNone(rows[3]["forward_pe_proxy"])
        self.assertEqual(rows[4]["forward_pe_proxy"], "14")

    def test_unknown_basis_strict_and_explicit_research_opt_in(self):
        inputs = fixture()
        inputs["price_currency"] = None
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])
        window = make_windows(inputs, SESSIONS, allow_unverified_basis=True)[0]
        self.assertTrue(window["ratio_allowed"])
        self.assertEqual(window["status"], "unverified_basis")
        self.assertIn("currency_or_eps_share_basis_unverified", window["flags"])

    def test_currency_conflict_not_bypassed_by_opt_in(self):
        inputs = fixture()
        inputs["price_currency"] = "EUR"
        self.assertFalse(make_windows(inputs, SESSIONS, allow_unverified_basis=True)[0]["ratio_allowed"])

    def test_dividend_adjusted_price_is_not_accepted(self):
        inputs = fixture()
        inputs["price_basis"] = "split_and_dividend_adjusted"
        self.assertFalse(make_windows(inputs, SESSIONS, allow_unverified_basis=True)[0]["ratio_allowed"])

    def test_build_cutoff_and_revision_do_not_become_historical_availability(self):
        inputs = fixture()
        future = deepcopy(inputs["estimates"][1])
        future.update(captured_at="2026-10-01T00:00:00Z", observation_version_id="future",
                      payload_json=json.dumps({"epsAvg": 999}))
        inputs["estimates"].append(future)
        window = make_windows(inputs, SESSIONS)[0]
        self.assertEqual(window["forward_eps"], "14")
        self.assertIn("reconstructed_not_point_in_time", window["flags"])

    def test_unmapped_new_report_interrupts_old_anchor(self):
        inputs = fixture()
        unknown = deepcopy(inputs["earnings"][0])
        unknown.update(natural_identity="unknown", observation_version_id="a2",
                       source_event_date="2023-12-07", source_time_raw="2023-12-07",
                       event_precision="date", payload_json=json.dumps({"epsActual": 3, "revenueActual": 123456}))
        inputs["earnings"].append(unknown)
        rows = list(daily_series(inputs, SESSIONS, make_windows(inputs, SESSIONS)))
        self.assertIsNotNone(rows[3]["forward_pe_proxy"])
        self.assertIsNone(rows[4]["forward_pe_proxy"])
        self.assertEqual(rows[4]["status"], "unmapped_announcement")

    def test_transcript_conflict_and_fiscal_mapping(self):
        inputs = fixture()
        event = inputs["earnings"][0]
        inputs["transcripts"] = [dict(event_json=json.dumps({"callDate": "2023-12-05T00:00:00Z"}),
            fiscal_year=2024, fiscal_quarter=1, captured_at=CAPTURE)]
        event["payload_json"] = json.dumps({"epsActual": 1})
        self.assertEqual(period_mapping(event, inputs["statements"], inputs["transcripts"]),
                         ("2023-10-31", "transcript_fiscal_match"))


    def test_unique_revenue_overrides_wrong_transcript_fiscal_label_with_warning(self):
        inputs = fixture()
        # The March release is fiscal Q2; a calendar-quarter label calls it Q1.
        ends = ["2023-11-30", "2024-02-29", "2024-05-30", "2024-08-29", "2024-11-28", "2025-02-27"]
        for i, (statement, estimate) in enumerate(zip(inputs["statements"], inputs["estimates"])):
            statement.update(period_end=ends[i], fiscal_year=2024+i//4, fiscal_period="Q"+str(i%4+1))
            estimate["target_period_end"] = ends[i]
        inputs["earnings"][0].update(source_event_date="2024-03-20", source_time_raw="2024-03-20",
            event_precision="date", payload_json=json.dumps({"epsActual": 1, "revenueActual": 1001}))
        inputs["transcripts"] = [dict(event_json=json.dumps({"callDate": "2024-03-20"}),
            fiscal_year=2024, fiscal_quarter=1, captured_at=CAPTURE)]
        sessions = [("2024-03-20", "2024-03-20T20:00:00Z"), ("2024-03-21", "2024-03-21T20:00:00Z")]
        window = make_windows(inputs, sessions)[0]
        self.assertEqual(window["reported_period_end"], "2024-02-29")
        self.assertEqual(window["effective_date"], "2024-03-21")
        self.assertEqual(window["forward_eps"], "18")
        self.assertTrue(window["ratio_allowed"])
        self.assertEqual(window["mapping"], "reported_revenue_match_transcript_conflict")
        self.assertIn("transcript_fiscal_label_conflict", window["flags"])

    def test_ambiguous_revenue_cannot_be_narrowed_by_a_transcript_label(self):
        inputs = fixture()
        earlier = dict(inputs["statements"][0], natural_identity="earlier", period_end="2023-08-31",
                       fiscal_year=2023, fiscal_period="Q4")
        statements = inputs["statements"] + [earlier]
        call = dict(event_json=json.dumps({"callDate": "2023-12-05"}),
                    fiscal_year=2024, fiscal_quarter=1, captured_at=CAPTURE)
        self.assertIsNone(period_mapping(inputs["earnings"][0], statements, [call])[0])
        self.assertIsNone(period_mapping(inputs["earnings"][0], statements, [])[0])

    def test_equal_revenue_from_two_providers_for_same_period_is_not_ambiguous(self):
        inputs = fixture()
        duplicate = dict(inputs["statements"][0], natural_identity="second_provider",
                         fiscal_year=None, fiscal_period="quarter")
        self.assertEqual(period_mapping(inputs["earnings"][0], inputs["statements"]+[duplicate], []),
                         ("2023-10-31", "reported_revenue_match"))

    def test_explicit_period_is_not_overridden_by_revenue_or_transcript(self):
        inputs = fixture()
        event = dict(inputs["earnings"][0], payload_json=json.dumps({
            "fiscalDateEnding": "2023-09-30", "revenueActual": 1000}))
        self.assertEqual(period_mapping(event, inputs["statements"], []), (None, "unmapped_announcement"))

    def test_revenue_match_remains_inside_announcement_date_bound(self):
        inputs = fixture()
        event = dict(inputs["earnings"][0], source_event_date="2024-03-01")
        self.assertEqual(period_mapping(event, inputs["statements"], []), (None, "unmapped_announcement"))

    def test_materialized_artifact_is_reproducible_complete_and_non_overwriting(self):
        inputs = fixture()
        subject = {k: inputs[k] for k in ("symbol", "instrument_id")}
        with TemporaryDirectory() as root:
            output = Path(root) / "research.sqlite"
            materialize(output, [subject], lambda _: inputs, SESSIONS,
                        cutoff=CUTOFF, start=inputs["start"], end=inputs["end"], allow_unverified_basis=False)
            with sqlite3.connect(f"file:{output}?mode=ro&immutable=1", uri=True) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM daily_forward_pe").fetchone()[0], 6)
                self.assertEqual(c.execute("SELECT forward_pe_proxy FROM daily WHERE trade_date='2023-12-06'").fetchone()[0], 12)
                saved = json.loads(zlib.decompress(c.execute("SELECT inputs_zlib FROM source_inputs").fetchone()[0]))
                self.assertEqual(saved, inputs)
                hashes = json.loads(c.execute("SELECT value_json FROM metadata WHERE key='implementation_sha256'").fetchone()[0])
                self.assertEqual(set(hashes), {"quant_data/company/forward_pe.py", "quant_data/operations/forward_pe.py"})
                self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(c.execute("SELECT DISTINCT is_point_in_time FROM daily_forward_pe").fetchall(), [(0,)])
            with self.assertRaises(FileExistsError):
                materialize(output, [subject], lambda _: inputs, SESSIONS, cutoff=CUTOFF,
                            start=inputs["start"], end=inputs["end"], allow_unverified_basis=False)

class ForwardPeReaderTests(unittest.TestCase):
    def test_retained_reader_cutoff_identity_and_no_source_mutation(self):
        import hashlib
        from quant_data.operations.forward_pe import read_inputs, subjects
        from quant_data.stores import StoreMap
        inputs = fixture()
        subject = {k: inputs[k] for k in ("symbol", "issuer_id", "instrument_id", "cik")}
        def create(c, name, records, columns=None):
            keys = sorted(set(columns or ()) | {k for r in records for k in r})
            c.execute("CREATE TABLE " + name + " (" + ",".join(
                k + (" INTEGER" if k in ("correction_sequence", "version_sequence") else " TEXT")
                for k in keys) + ")")
            for r in records:
                c.execute("INSERT INTO " + name + " VALUES (" + ",".join("?" for _ in keys) + ")",
                          [r.get(k) for k in keys])
        with TemporaryDirectory() as root:
            paths = {r: Path(root) / (r + ".sqlite") for r in ("company", "market", "macro", "news")}
            for role, path in paths.items():
                with sqlite3.connect(path) as c:
                    c.execute("CREATE TABLE store_metadata (singleton INTEGER,store_role TEXT)")
                    c.execute("INSERT INTO store_metadata VALUES (1,?)", (role,))
            with sqlite3.connect(paths["company"]) as c:
                estimates = [dict(r, **subject, endpoint="analyst-estimates", request_period="quarter")
                             for r in inputs["estimates"]]
                earnings = [dict(r, **subject, endpoint="earnings", request_period="all")
                            for r in inputs["earnings"]]
                future = dict(estimates[0], captured_at="2026-10-01T00:00:00Z",
                              observation_version_id="future")
                foreign = dict(earnings[0], instrument_id="other", observation_version_id="foreign")
                create(c, "company_fmp_analyst_observation_versions", estimates + earnings + [future, foreign])
                c.execute("CREATE INDEX company_fmp_analyst_latest ON company_fmp_analyst_observation_versions(issuer_id,endpoint)")
                create(c, "company_fmp_analyst_captures",
                       [dict(subject, endpoint="analyst-estimates", request_period="quarter", captured_at=CAPTURE),
                        dict(subject, endpoint="analyst-estimates", request_period="annual",
                             captured_at="2026-09-19T12:00:00Z")])
                create(c, "company_fmp_research_rows",
                       [dict(r, cik=subject["cik"], endpoint="income-statement", request_period="quarter")
                        for r in inputs["statements"]])
                create(c, "company_equibles_transcripts", [], [
                    "capture_id", "event_json", "fiscal_year", "fiscal_quarter", "captured_at",
                    "symbol", "instrument_id"])
                create(c, "company_sharadar_sf1_observations", [dict(
                    observation_id="arq1", source_ticker="ODD", dimension="ARQ")])
                create(c, "company_sharadar_sf1_versions", [dict(
                    observation_id="arq1", version_id="sv1", reportperiod="2023-07-31",
                    source_datekey="2023-09-05", available_at=CAPTURE, ingested_at=CAPTURE,
                    values_json=json.dumps({"revenue": "999"}), version_sequence=1, capture_id="sc1")])
                create(c, "company_sharadar_identity_assertions", [dict(
                    capture_id="sc1", source_ticker="ODD", instrument_id=subject["instrument_id"],
                    cik=subject["cik"], available_at=CAPTURE, provider_subject="permanent", membership_snapshot_id="members")])
            with sqlite3.connect(paths["market"]) as c:
                create(c, "stage10_instruments", [dict(
                    instrument_id=subject["instrument_id"], exchange_code="NASDAQ", captured_at=CAPTURE)])
                prices = [dict(r, instrument_id=subject["instrument_id"], available_at=CAPTURE,
                    correction_sequence=1, capture_id="pc1", provider="fmp",
                    price_variant="fmp_full_eod_v1", currency_segment="provider_native")
                    for r in inputs["prices"]]
                create(c, "stage10_daily_price_versions", prices + [
                    dict(prices[0], correction_sequence=2, captured_at="2026-10-01T00:00:00Z",
                         available_at="2026-10-01T00:00:00Z", close_value="999", version_id="future")])
                create(c, "stage10_daily_price_captures", [dict(
                    capture_id="pc1", provider="fmp", instrument_id=subject["instrument_id"],
                    endpoint_path="/stable/historical-price-eod/full",
                    normalization_version="stage10.fmp.daily_price.v1")])
            before = {r: hashlib.sha256(p.read_bytes()).hexdigest() for r, p in paths.items()}
            stores = StoreMap.from_mapping(paths)
            self.assertEqual(subjects(stores, ["ODD"], CUTOFF), [subject])
            selected = read_inputs(stores, subject, CUTOFF, inputs["start"], inputs["end"])
            self.assertEqual(len(selected["estimates"]), 6)
            self.assertEqual(len(selected["earnings"]), 1)
            self.assertEqual(selected["prices"][0]["close_value"], "140")
            self.assertEqual(selected["price_basis"], SPLIT_ONLY)
            self.assertEqual(selected["statements"][-1]["period_end"], "2023-07-31")
            cached = read_inputs(stores, dict(subject, sharadar_observation_ids=["arq1"]),
                                 CUTOFF, inputs["start"], inputs["end"])
            self.assertEqual(selected["statements"], cached["statements"])
            self.assertEqual(before, {r: hashlib.sha256(p.read_bytes()).hexdigest() for r, p in paths.items()})
            self.assertEqual(selected["freshness"]["analyst-estimates"],CAPTURE)
            self.assertEqual(subjects(stores,["ODD","MISSING"],CUTOFF,strict=False),[subject])
            with self.assertRaises(ValueError):subjects(stores,["ODD","MISSING"],CUTOFF)
            # Correction committed after a prior read, with an older capture timestamp.
            with sqlite3.connect(paths["market"]) as c:
                c.execute("INSERT INTO stage10_daily_price_versions SELECT " +
                    ",".join("'delayed'" if r[1]=="version_id" else "2" if r[1]=="correction_sequence"
                             else "'280'" if r[1]=="close_value" else r[1]
                             for r in c.execute("PRAGMA table_info(stage10_daily_price_versions)"))+
                    " FROM stage10_daily_price_versions WHERE version_id='p0'")
            delayed=read_inputs(stores,subject,CUTOFF,"2023-12-11",inputs["end"],
                                changed_since=CUTOFF,history_start=inputs["start"])
            self.assertEqual(delayed["prices"][0]["version_id"],"delayed")
            with sqlite3.connect(paths["company"]) as c:
                c.execute("INSERT INTO company_fmp_analyst_captures SELECT "+
                    ",".join("'ambiguous'" if r[1]=="instrument_id" else r[1]
                             for r in c.execute("PRAGMA table_info(company_fmp_analyst_captures)"))+
                    " FROM company_fmp_analyst_captures WHERE request_period='quarter'")
            self.assertEqual(subjects(stores,["ODD"],CUTOFF,strict=False),[])
            with self.assertRaises(ValueError):subjects(stores,["ODD"],CUTOFF)


    def test_known_share_adjustment_conflict_is_blocked_even_for_proxy(self):
        inputs = fixture()
        inputs["split_basis_date"] = "2025-09-19"
        window = make_windows(inputs, SESSIONS, allow_unverified_basis=True)[0]
        self.assertFalse(window["ratio_allowed"])
        self.assertIn("share_basis_mismatch", window["flags"])

    def test_sharadar_actual_period_end_can_anchor_without_fiscal_year_labels(self):
        inputs = fixture()
        inputs["statements"][0].update(fiscal_year=None, fiscal_period="quarter", source="sharadar_ARQ")
        self.assertEqual(make_windows(inputs, SESSIONS)[0]["forward_eps"], "14")


if __name__ == "__main__":
    unittest.main()
