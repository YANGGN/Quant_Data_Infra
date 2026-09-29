"""Evidence-based period reconciliation; isolated saved-input fixtures only."""
from copy import deepcopy
from datetime import date
import json
import unittest

from quant_data.company.forward_pe import make_windows, period_mapping, daily_series, estimate_periods
from tests.company.test_forward_pe import fixture, SESSIONS, CAPTURE


def payload(row, **values):
    row["payload_json"] = json.dumps(dict(json.loads(row["payload_json"]), **values))


def call(day="2023-12-05", year=2024, quarter=1):
    return dict(capture_id="call", captured_at=CAPTURE, fiscal_year=year,
                fiscal_quarter=quarter, event_json=json.dumps({"callDate": day}))


class PeriodReconciliationTests(unittest.TestCase):
    def test_sharadar_year_alias_links_same_actual_period(self):
        inputs = fixture()
        payload(inputs["earnings"][0], revenueActual=None)
        inputs["statements"].append(dict(inputs["statements"][0], natural_identity="sf", source="sharadar_ARQ",
            fiscal_year=None, fiscal_period="quarter", payload_json=json.dumps({"revenue": 1000, "fiscalperiod": "2025-Q1"})))
        inputs["transcripts"] = [call(year=2025)]
        w = make_windows(inputs, SESSIONS)[0]
        self.assertEqual(w["reported_period_end"], "2023-10-31")
        self.assertEqual(w["mapping"], "transcript_fiscal_alias_match")
        self.assertEqual(w["effective_date"], "2023-12-06")
        self.assertEqual(w["forward_eps"], "14")
        self.assertEqual(w["mapping_evidence"]["transcripts"][0]["fiscal_year"], 2025)

    def test_nearby_transcript_preserves_actual_announcement_and_records_evidence(self):
        for day in ("2023-12-03", "2023-11-23", "2023-12-19"):
            with self.subTest(day=day):
                inputs = fixture(); payload(inputs["earnings"][0], revenueActual=None)
                inputs["transcripts"] = [call(day)]
                w = make_windows(inputs, SESSIONS)[0]
                self.assertEqual(w["mapping"], "transcript_fiscal_match_date_disagreement")
                self.assertEqual(w["effective_date"], "2023-12-06")
                self.assertEqual(w["announcement"], "2023-12-05T21:05:00Z")
                self.assertEqual(w["mapping_evidence"]["transcripts"][0]["call_date"], day)
                self.assertEqual(list(daily_series(inputs, SESSIONS, [w]))[2]["forward_pe_proxy"], "12")

    def test_nearby_date_requires_unique_event_bounded_date_and_captured_call(self):
        inputs = fixture(); payload(inputs["earnings"][0], revenueActual=None)
        event = inputs["earnings"][0]
        other = dict(event, natural_identity="second", source_event_date="2023-12-01")
        self.assertIsNone(period_mapping(event, inputs["statements"], [call("2023-12-03")],
                                        reported_events=[event, other])[0])
        self.assertIsNone(period_mapping(event, inputs["statements"], [call("2023-12-03")])[0])
        inputs["transcripts"] = [call("2023-12-20")]
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])
        inputs["transcripts"] = [dict(call("2023-12-03"), captured_at="2026-10-01T00:00:00Z")]
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])

    def test_repeated_revenue_requires_unique_filing_corroboration(self):
        inputs = fixture()
        earlier = dict(inputs["statements"][0], natural_identity="earlier", period_end="2023-08-31",
                       fiscal_year=2023, fiscal_period="Q4")
        inputs["statements"].append(earlier)
        inputs["transcripts"] = [call()]
        self.assertIsNone(make_windows(inputs, SESSIONS)[0]["reported_period_end"])
        payload(inputs["statements"][0], filingDate="2023-12-06")
        w = make_windows(inputs, SESSIONS)[0]
        self.assertEqual(w["mapping"], "reported_revenue_match_filing_corroborated")
        self.assertEqual(w["reported_period_end"], "2023-10-31")
        self.assertEqual(w["effective_date"], "2023-12-06")
        payload(earlier, filingDate="2023-12-07")
        self.assertIsNone(make_windows(inputs, SESSIONS)[0]["reported_period_end"])

    def test_filing_and_same_day_call_corroborate_bad_label_but_filing_alone_does_not(self):
        inputs = fixture(); payload(inputs["earnings"][0], revenueActual=None)
        payload(inputs["statements"][0], filingDate="2023-12-08")
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])
        inputs["transcripts"] = [call(year=2025)]
        w = make_windows(inputs, SESSIONS)[0]
        self.assertEqual(w["mapping"], "filing_period_with_transcript_date")
        self.assertEqual(w["effective_date"], "2023-12-06")  # Never the filing date.
        payload(inputs["statements"][0], filingDate="2023-12-13")
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])

    def test_two_nearby_labels_remain_ambiguous(self):
        inputs = fixture(); payload(inputs["earnings"][0], revenueActual=None)
        inputs["statements"].append(dict(inputs["statements"][0], natural_identity="earlier", period_end="2023-08-31",
                                        fiscal_year=2023, fiscal_period="Q4"))
        inputs["transcripts"] = [call("2023-12-03"), call("2023-12-04", 2023, 4)]
        self.assertFalse(make_windows(inputs, SESSIONS)[0]["ratio_allowed"])

    def test_new_capture_supersedes_shifted_date_and_preserves_both_version_ids(self):
        for remove_actual in (False, True):
            with self.subTest(future_period=remove_actual):
                inputs = fixture()
                old = deepcopy(inputs["estimates"][4])
                old.update(natural_identity="old-date", observation_version_id="old-version",
                           target_period_end="2024-10-30", captured_at="2026-09-18T00:00:00Z")
                payload(old, epsAvg=999)
                inputs["estimates"].append(old)
                if remove_actual:inputs["statements"].pop(4)
                w = make_windows(inputs, SESSIONS)[0]
                self.assertTrue(w["ratio_allowed"])
                self.assertEqual(w["forward_eps"], "14")
                component = w["components"][-1]
                self.assertEqual(component["period_end"], "2024-10-31")
                self.assertEqual(component["version_id"], "e4")
                self.assertEqual({a["version_id"] for a in component["period_date_aliases"]}, {"e4", "old-version"})

    def test_same_capture_collision_does_not_prefer_exact_date(self):
        inputs = fixture()
        extra = dict(inputs["estimates"][4], natural_identity="collision", observation_version_id="other",
                     target_period_end="2024-10-30")
        payload(extra, epsAvg=999)
        inputs["estimates"].append(extra)
        w = make_windows(inputs, SESSIONS)[0]
        self.assertFalse(w["ratio_allowed"])
        self.assertIn("ambiguous_estimate_period", w["flags"])

    def test_date_clusters_do_not_chain_or_choose_between_two_actual_periods(self):
        base = fixture()["estimates"][0]
        rows = [dict(base, target_period_end=d, observation_version_id=d) for d in
                ("2024-01-31", "2024-02-06", "2024-02-12")]
        self.assertEqual(set(estimate_periods(rows, [])), {r["target_period_end"] for r in rows})
        self.assertEqual(set(estimate_periods(rows[:1], ["2024-01-30", "2024-02-01"])), {"2024-01-31"})

    def test_long_retail_quarters_require_observed_consecutive_fiscal_periods(self):
        inputs = fixture()
        ends = ["2023-10-31", "2024-01-31", "2024-05-22", "2024-08-14", "2024-11-06", "2025-02-05"]
        self.assertEqual((date.fromisoformat(ends[2])-date.fromisoformat(ends[1])).days, 112)
        for statement, estimate, end in zip(inputs["statements"], inputs["estimates"], ends):
            statement["period_end"] = end; estimate["target_period_end"] = end
        w = make_windows(inputs, SESSIONS)[0]
        self.assertTrue(w["ratio_allowed"])
        self.assertIn("observed_long_fiscal_quarter", w["flags"])
        for statement in inputs["statements"]:statement["fiscal_year"] = None
        self.assertIn("nonconsecutive_quarters", make_windows(inputs, SESSIONS)[0]["flags"])

    def test_missing_full_quarter_is_not_repaired_by_date_deduplication(self):
        inputs = fixture();inputs["estimates"].pop(2);inputs["statements"].pop(2)
        w = make_windows(inputs, SESSIONS)[0]
        self.assertFalse(w["ratio_allowed"])
        self.assertIn("nonconsecutive_quarters", w["flags"])
