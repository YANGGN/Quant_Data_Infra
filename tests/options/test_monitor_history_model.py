"""Synthetic acceptance checks for saved daily ETF chart projection."""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
import unittest

from quant_data.options.monitor.history_model import DERIVATION_VERSION, project_daily

SESSION = date(2026, 9, 25)
DIGEST = "a" * 64


def capture():
    return {
        "capture_id": 17,
        "symbol": "SPY",
        "session_date": SESSION.isoformat(),
        "semantic_sha256": DIGEST,
        "captured_at": "2026-09-26T02:00:00+00:00",
        "underlying_price": "500",
    }


def summaries(call_volume=120, put_volume=60, call_oi=300, put_oi=150,
              call_missing=0, put_missing=0, call_oi_missing=0, put_oi_missing=0):
    return [
        {"population": "full", "dimension": "overall", "bucket": "all", "right": right,
         "stats": {"volume": volume, "volume_missing": missing,
                   "open_interest": oi, "oi_missing": oi_missing}}
        for right, volume, missing, oi, oi_missing in (
            ("C", call_volume, call_missing, call_oi, call_oi_missing),
            ("P", put_volume, put_missing, put_oi, put_oi_missing),
        )
    ]


def detail(dte, right, iv, *, strike="500", quality="usable_root_proxy",
           bid="2", ask="2.2", reasons=None, oi=10, oi_date="2026-09-24",
           observed="2026-09-25T16:00:00-04:00"):
    expiry = (SESSION + timedelta(days=dte)).isoformat()
    return {
        "contract_id": f"SPY|{expiry}|{strike}|{right}",
        "expiration": expiry,
        "strike": strike,
        "right": right,
        "reasons": ["anchor:1"] if reasons is None else reasons,
        "bid": bid, "ask": ask, "implied_vol": iv,
        "iv_quality": quality, "oi": oi, "oi_effective_date": oi_date,
        "eod_greeks": {
            "symbol": "SPY", "expiration": expiry, "strike": strike,
            "right": {"C": "CALL", "P": "PUT"}[right],
            "underlying_price": "500", "underlying_timestamp": observed,
        },
    }


def pair(dte, call_iv, put_iv, **kwargs):
    return [detail(dte, "C", call_iv, **kwargs), detail(dte, "P", put_iv, **kwargs)]


class DailyProjectionTests(unittest.TestCase):
    def test_complete_totals_source_identity_and_exact_maturities(self):
        rows = pair(7, ".20", ".22") + pair(30, ".24", ".26") + pair(90, ".30", ".32")
        result = project_daily(capture(), summaries(), rows)
        self.assertEqual(set(result), {
            "symbol", "session", "source_capture_id", "source_semantic_sha256",
            "source_captured_at", "derivation_version", "call_volume", "put_volume",
            "put_call_ratio", "call_open_interest", "put_open_interest",
            "oi_effective_date", "atm_iv_7", "atm_iv_30", "atm_iv_90",
            "iv_contracts", "quality_flags",
        })
        self.assertEqual((result["symbol"], result["session"], result["source_capture_id"]),
                         ("SPY", SESSION.isoformat(), 17))
        self.assertEqual(result["source_semantic_sha256"], DIGEST)
        self.assertEqual(result["source_captured_at"], "2026-09-26T02:00:00+00:00")
        self.assertEqual(result["derivation_version"], DERIVATION_VERSION)
        self.assertEqual((result["call_volume"], result["put_volume"], result["put_call_ratio"]),
                         (120, 60, .5))
        self.assertEqual((result["call_open_interest"], result["put_open_interest"]), (300, 150))
        self.assertEqual(result["oi_effective_date"], "2026-09-24")
        self.assertAlmostEqual(result["atm_iv_7"], .21)
        self.assertAlmostEqual(result["atm_iv_30"], .25)
        self.assertAlmostEqual(result["atm_iv_90"], .31)
        self.assertEqual(result["iv_contracts"], 6)
        self.assertIn("selected_anchor_eod_iv_proxy", result["quality_flags"])
        self.assertIn("provider_root_deliverables_unverified", result["quality_flags"])
        self.assertNotIn("missing_iv_coverage", result["quality_flags"])
        json.dumps(result, allow_nan=False)

    def test_missing_and_zero_summary_values(self):
        missing = project_daily(capture(), summaries(call_missing=1, put_oi_missing=1), [])
        self.assertIsNone(missing["call_volume"])
        self.assertIsNone(missing["put_call_ratio"])
        self.assertIsNone(missing["put_open_interest"])
        self.assertEqual(missing["put_volume"], 60)
        self.assertIn("missing_volume", missing["quality_flags"])
        self.assertIn("missing_open_interest", missing["quality_flags"])
        zero = project_daily(capture(), summaries(call_volume=0, put_volume=0, call_oi=0, put_oi=0), [])
        self.assertEqual((zero["call_volume"], zero["put_volume"], zero["call_open_interest"]), (0, 0, 0))
        self.assertIsNone(zero["put_call_ratio"])
        invalid = project_daily(capture(), summaries(call_volume=-1, put_oi="NaN"), [])
        self.assertIsNone(invalid["call_volume"])
        self.assertIsNone(invalid["put_open_interest"])

    def test_exact_and_interpolated_total_variance_without_extrapolation(self):
        rows = pair(7, ".20", ".20") + pair(90, ".30", ".30")
        result = project_daily(capture(), summaries(), rows)
        expected_variance = .20 ** 2 * 7 + (.30 ** 2 * 90 - .20 ** 2 * 7) * 23 / 83
        self.assertAlmostEqual(result["atm_iv_7"], .20)
        self.assertAlmostEqual(result["atm_iv_30"], math.sqrt(expected_variance / 30))
        self.assertAlmostEqual(result["atm_iv_90"], .30)
        only_long = project_daily(capture(), summaries(), pair(90, ".30", ".30"))
        self.assertIsNone(only_long["atm_iv_7"])
        self.assertIsNone(only_long["atm_iv_30"])
        self.assertEqual(only_long["atm_iv_90"], .30)
        self.assertIn("missing_iv_coverage", only_long["quality_flags"])

    def test_only_paired_same_strike_usable_anchor_rights_count(self):
        rows = pair(30, ".24", ".26", strike="500")
        rows += pair(30, ".80", ".80", strike="510")
        rows += [detail(30, "C", ".9", strike="495")]
        rows += pair(30, ".9", ".9", strike="499", reasons=["leader:volume"])
        result = project_daily(capture(), summaries(), rows)
        self.assertAlmostEqual(result["atm_iv_30"], .25)
        self.assertEqual(result["iv_contracts"], 2)

    def test_bad_quotes_quality_and_activity_only_excluded(self):
        rows = [
            detail(7, "C", ".2", bid="0"),
            detail(7, "P", ".2"),
            detail(30, "C", ".3", ask="1"),
            detail(30, "P", ".3"),
            detail(90, "C", ".4", quality="excluded"),
            detail(90, "P", ".4"),
            *pair(12, ".5", ".5", reasons=["leader:volume"]),
        ]
        result = project_daily(capture(), summaries(), rows)
        self.assertEqual(result["iv_contracts"], 0)
        self.assertIsNone(result["atm_iv_7"])
        self.assertIsNone(result["atm_iv_30"])
        self.assertIsNone(result["atm_iv_90"])
        self.assertNotIn("selected_anchor_eod_iv_proxy", result["quality_flags"])

    def test_underlying_reference_quality_and_future_rejection(self):
        rows = pair(30, ".2", ".2")
        rows[0]["eod_greeks"]["underlying_price"] = "501"
        result = project_daily(capture(), summaries(), rows)
        self.assertEqual(result["iv_contracts"], 0)
        rows[0]["eod_greeks"]["underlying_price"] = "500"
        rows[0]["eod_greeks"]["underlying_timestamp"] = "2026-09-27T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "future_underlying_reference"):
            project_daily(capture(), summaries(), rows)

    def test_oi_date_requires_one_valid_prior_date(self):
        rows = pair(30, ".2", ".2")
        self.assertEqual(project_daily(capture(), summaries(), rows)["oi_effective_date"], "2026-09-24")
        rows[0]["oi_effective_date"] = "2026-09-23"
        result = project_daily(capture(), summaries(), rows)
        self.assertIsNone(result["oi_effective_date"])
        self.assertIn("oi_effective_date_unavailable", result["quality_flags"])
        rows[0]["oi"] = None
        self.assertEqual(project_daily(capture(), summaries(), rows)["oi_effective_date"], "2026-09-24")
        rows[1]["oi_effective_date"] = "2026-09-25"
        self.assertIsNone(project_daily(capture(), summaries(), rows)["oi_effective_date"])

    def test_corrupt_identity_or_duplicate_summary_cell_rejected(self):
        duplicate = summaries() + [summaries()[0]]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            project_daily(capture(), duplicate, [])
        bad_capture = capture()
        bad_capture["symbol"] = "AAPL"
        with self.assertRaisesRegex(ValueError, "unexpected_option_root"):
            project_daily(bad_capture, summaries(), [])
        row = detail(7, "C", ".2")
        row["contract_id"] = row["contract_id"].replace("SPY", "QQQ")
        with self.assertRaisesRegex(ValueError, "cross_symbol_contract"):
            project_daily(capture(), summaries(), [row])


if __name__ == "__main__":
    unittest.main()
