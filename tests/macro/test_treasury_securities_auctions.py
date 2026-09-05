"""Focused offline tests for Treasury securities-auction normalization."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from quant_data.errors import ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro.treasury_securities_auctions import (
    TREASURY_SECURITIES_AUCTIONS_MANIFEST,
    parse_treasury_securities_auctions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/treasury_securities_auctions.json"
CAPTURED_AT = "2026-08-22T14:00:00Z"


def _capture(body: bytes, *, captured_at: str = CAPTURED_AT):
    return parse_treasury_securities_auctions(
        body,
        captured_at=captured_at,
        start_date="2026-08-20",
        end_date="2026-08-21",
    )


def _body(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class TreasurySecuritiesAuctionsParserTests(unittest.TestCase):
    def test_fixed_manifest_identity_and_explicit_nulls(self) -> None:
        capture = _capture(FIXTURE.read_bytes())

        self.assertEqual(len(TREASURY_SECURITIES_AUCTIONS_MANIFEST), 11)
        self.assertEqual(
            [
                (item.series_id, item.provider_code)
                for item in TREASURY_SECURITIES_AUCTIONS_MANIFEST[3:6]
            ],
            [
                ("macro.treasury_fiscal.auction.high_yield", "high_yield"),
                (
                    "macro.treasury_fiscal.auction.high_discount_rate",
                    "high_discnt_rate",
                ),
                (
                    "macro.treasury_fiscal.auction.high_discount_margin",
                    "high_discnt_margin",
                ),
            ],
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 33)
        self.assertEqual(
            capture.captured_at, "2026-08-22T14:00:00.000000Z"
        )
        same_day = {
            dict(item.dimensions)["cusip"]
            for item in capture.observations
            if item.source_period == "2026-08-20"
        }
        self.assertEqual(same_day, {"91282CMA4", "91282CMB2"})
        first = next(
            item
            for item in capture.observations
            if item.provider_code == "offering_amt"
            and dict(item.dimensions)["cusip"] == "91282CMA4"
        )
        self.assertEqual(
            dict(first.dimensions),
            {
                "auction_date": "2026-08-20",
                "auction_format": "Single Price",
                "cusip": "91282CMA4",
                "issue_date": "2026-08-25",
                "reopening": "No",
                "security_term": "13-Week",
                "security_type": "Bill",
            },
        )
        missing = next(
            item
            for item in capture.observations
            if item.provider_code == "high_discnt_rate"
            and dict(item.dimensions)["cusip"] == "91282CMA4"
        )
        self.assertEqual((missing.value_text, missing.missing_reason), (None, "source_missing"))
        soma = next(
            item
            for item in capture.observations
            if item.provider_code == "soma_accepted"
            and dict(item.dimensions)["cusip"] == "91282CMC0"
        )
        self.assertEqual((soma.value_text, soma.missing_reason), (None, "source_missing"))
        scope = loads_strict(capture.request_scope_json)
        self.assertEqual(scope["availability_basis"], "local_capture")
        self.assertEqual(scope["record_date"]["precision"], "date")

    def test_semantic_replay_is_order_and_capture_time_independent(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        original = _capture(FIXTURE.read_bytes())
        replay = _capture(
            _body({"meta": raw["meta"], "data": list(reversed(raw["data"]))}),
            captured_at="2026-08-23T14:00:00+00:00",
        )

        self.assertEqual(original.semantic_identity, replay.semantic_identity)
        self.assertNotEqual(original.captured_at, replay.captured_at)
        self.assertEqual(original.observations, replay.observations)

    def test_correction_changes_identity_but_keeps_auction_key(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        original = _capture(FIXTURE.read_bytes())
        raw["data"][0]["offering_amt"] = "76000000000"
        corrected = _capture(_body(raw))

        self.assertNotEqual(original.semantic_identity, corrected.semantic_identity)
        original_fact = next(
            item
            for item in original.observations
            if item.provider_code == "offering_amt"
            and dict(item.dimensions)["cusip"] == "91282CMA4"
        )
        corrected_fact = next(
            item
            for item in corrected.observations
            if item.provider_code == "offering_amt"
            and dict(item.dimensions)["cusip"] == "91282CMA4"
        )
        self.assertEqual(original_fact.dimensions, corrected_fact.dimensions)
        self.assertEqual(
            (original_fact.value_text, corrected_fact.value_text),
            ("75000000000", "76000000000"),
        )

    def test_incomplete_or_colliding_page_fails_closed(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["meta"]["total-pages"] = "2"
        with self.assertRaisesRegex(ValidationError, "one complete page"):
            _capture(_body(raw))

        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["data"].append(dict(raw["data"][0]))
        raw["meta"]["total-count"] = "4"
        with self.assertRaisesRegex(ValidationError, "identities must be unique"):
            _capture(_body(raw))


if __name__ == "__main__":
    unittest.main()
