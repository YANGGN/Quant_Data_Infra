"""FRED CFNAI mirror parsing, date precision and replay identity."""
import unittest
from quant_data.errors import ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro.official_conditions import parse_fred_chicagofed_national_activity as parse


class CfnaiFredParsingTests(unittest.TestCase):
    def capture(self, body):
        return parse(body, captured_at="2026-09-05T04:00:00Z",
                     start_date="2026-01-01", end_date="2026-09-30")

    def test_new_months_preserve_month_precision_missingness_and_distributor(self):
        capture = self.capture(b"observation_date,CFNAI\n2026-05-01,-0.12\n2026-06-01,.\n2026-07-01,-0.08\n")
        self.assertEqual(capture.observations[-1].source_period, "2026-07")
        self.assertEqual(capture.observations[-1].period_end, "2026-07-31")
        self.assertEqual(capture.observations[1].missing_reason, "source_missing")
        self.assertEqual(loads_strict(capture.request_scope_json)["distributor"], "fred")
        self.assertEqual(capture.captured_at, "2026-09-05T04:00:00.000000Z")

    def test_row_order_and_capture_time_do_not_change_semantic_identity(self):
        first = self.capture(b"observation_date,CFNAI\n2026-06-01,0.06\n2026-07-01,-0.08\n")
        second = parse(b"observation_date,CFNAI\n2026-07-01,-0.080\n2026-06-01,0.060\n",
                       captured_at="2026-09-06T04:00:00Z",start_date="2026-01-01",end_date="2026-09-30")
        self.assertEqual(first.semantic_identity, second.semantic_identity)
        self.assertNotEqual(first.artifact_sha256, second.artifact_sha256)

    def test_wrong_series_duplicate_dates_out_of_window_and_nonmonthly_dates_fail(self):
        for body in (
            b"observation_date,OTHER\n2026-07-01,1\n",
            b"observation_date,CFNAI\n2026-07-01,1\n2026-07-01,2\n",
            b"observation_date,CFNAI\n2025-12-01,1\n",
            b"observation_date,CFNAI\n2026-07-02,1\n",
            b"observation_date,CFNAI\n",
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                self.capture(body)
