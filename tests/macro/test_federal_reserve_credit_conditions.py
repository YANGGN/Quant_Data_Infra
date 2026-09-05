"""Focused offline tests for the fixed H.8 and SLOOS FRED CSV parsers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro import federal_reserve_credit_conditions as credit
from quant_data.macro.federal_reserve_credit_conditions import (
    H8_MANIFEST,
    H8_SOURCE_KEY,
    H8_SOURCE_METADATA,
    SLOOS_MANIFEST,
    SLOOS_SOURCE_KEY,
    SLOOS_SOURCE_METADATA,
    parse_federal_reserve_h8,
    parse_federal_reserve_sloos,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
H8_FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/federal_reserve_h8_mixed.json"
SLOOS_FIXTURE = PROJECT_ROOT / "tests/fixtures/macro/federal_reserve_sloos_core.csv"
CAPTURED_AT = "2026-08-24T14:00:00Z"
H8_V1_SEMANTIC_IDENTITY = (
    "2ab53883744eb5b63aa53717a7d4a1bdc1a71b26849d51a6030602ac3a9cb816"
)
SLOOS_SEMANTIC_IDENTITY = (
    "f31a3834b3606f41881be2530535ccda6c24e6a81f8eb1bb879befec1d499349"
)


def _singleton_bodies(
    path: Path,
    manifest: tuple[object, ...],
    *,
    bom: bool = False,
) -> tuple[bytes, ...]:
    codes = tuple(getattr(item, "provider_code") for item in manifest)
    if path.suffix == ".json":
        payload = json.loads(path.read_text())
        return tuple(((chr(0xfeff) if bom else "") + payload[code]).encode("utf-8")
                     for code in codes)

    rows = list(
        csv.reader(
            io.StringIO(path.read_bytes().decode("utf-8-sig"), newline="")
        )
    )
    if tuple(rows[0]) != ("observation_date", *codes):
        raise AssertionError("fixture header is outside the fixed manifest")
    bodies: list[bytes] = []
    for column, code in enumerate(codes, start=1):
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("observation_date", code))
        for row in rows[1:]:
            writer.writerow((row[0], row[column]))
        body = output.getvalue().encode("utf-8")
        bodies.append((b"\xef\xbb\xbf" + body) if bom else body)
    return tuple(bodies)


class FederalReserveCreditParseTests(unittest.TestCase):
    def test_h8_monthly_dates_require_month_start(self):
        bodies = list(_singleton_bodies(H8_FIXTURE, H8_MANIFEST))
        bodies[3] = bodies[3].replace(b"2026-07-01", b"2026-07-02")
        with self.assertRaisesRegex(ValidationError, "month start"):
            parse_federal_reserve_h8(tuple(bodies), captured_at=CAPTURED_AT,
                                     start_date="2026-07-01", end_date="2026-08-31")

    def test_h8_mixed_manifest_dates_missingness_and_semantic_replay(self) -> None:
        bodies = _singleton_bodies(H8_FIXTURE, H8_MANIFEST, bom=True)
        capture = parse_federal_reserve_h8(
            bodies,
            captured_at=CAPTURED_AT,
            start_date="2026-07-01",
            end_date="2026-08-31",
        )

        self.assertEqual(capture.source_key, H8_SOURCE_KEY)
        self.assertEqual(
            tuple(item.provider_code for item in H8_MANIFEST),
            (
                "TLAACBW027SBOG",
                "TOTBKCR",
                "TOTLL",
                "BUSLOANS",
                "REALLN",
                "CONSUMER",
                "DPSACBW027SBOG",
            ),
        )
        self.assertEqual(len(capture.observations), 14)
        self.assertEqual(
            (
                capture.observations[0].source_period,
                capture.observations[0].period_start,
                capture.observations[0].period_end,
            ),
            ("2026-07", "2026-07-01", "2026-07-31"),
        )
        missing = next(
            item
            for item in capture.observations
            if item.provider_code == "CONSUMER"
            and item.source_period == "2026-08"
        )
        self.assertEqual(
            (missing.value_text, missing.missing_reason),
            (None, "source_missing"),
        )
        self.assertEqual(capture.captured_at, "2026-08-24T14:00:00.000000Z")
        self.assertEqual(
            loads_strict(capture.request_scope_json)["availability_basis"],
            "local_capture",
        )
        self.assertEqual(H8_SOURCE_METADATA.max_requests, 7)
        self.assertEqual(H8_SOURCE_METADATA.configuration_env, ())
        self.assertEqual(
            tuple(part.name for part in capture.parts),
            tuple(f"fred_graph_{index:04d}" for index in range(1, 8)),
        )
        self.assertEqual(
            tuple(part.sha256 for part in capture.parts),
            tuple(hashlib.sha256(body).hexdigest() for body in bodies),
        )
        # Retain the prior v1 golden as historical evidence; mixed cadence is v2.
        self.assertNotEqual(capture.semantic_identity, H8_V1_SEMANTIC_IDENTITY)
        self.assertEqual(credit.H8_NORMALIZATION_VERSION, "federal_reserve_h8_v2")
        self.assertEqual({x.provider_code for x in H8_MANIFEST if x.frequency == "monthly"},
                         {"BUSLOANS", "REALLN", "CONSUMER"})
        replay = parse_federal_reserve_h8(
            tuple((chr(10).join(body.decode("utf-8-sig").splitlines()[:1]
                    + list(reversed(body.decode("utf-8-sig").splitlines()[1:]))) + chr(10)).encode()
                  for body in bodies),
            captured_at="2026-08-25T14:00:00Z", start_date="2026-07-01", end_date="2026-08-31")
        self.assertEqual(capture.semantic_identity, replay.semantic_identity)

    def test_sloos_quarters_values_missingness_and_semantic_replay(self) -> None:
        bodies = _singleton_bodies(SLOOS_FIXTURE, SLOOS_MANIFEST)
        capture = parse_federal_reserve_sloos(
            bodies,
            captured_at=CAPTURED_AT,
            start_date="2026-01-01",
            end_date="2026-04-30",
        )

        self.assertEqual(capture.source_key, SLOOS_SOURCE_KEY)
        self.assertEqual(
            tuple(item.provider_code for item in SLOOS_MANIFEST),
            (
                "DRTSCILM",
                "DRTSCIS",
                "DRSDCILM",
                "DRSDCIS",
                "DRTSCLCC",
                "DEMCC",
            ),
        )
        self.assertEqual(len(capture.observations), 12)
        april = next(
            item
            for item in capture.observations
            if item.provider_code == "DRTSCILM"
            and item.source_period == "2026-Q2"
        )
        self.assertEqual(
            (
                april.period_start,
                april.period_end,
                april.value_text,
                april.missing_reason,
            ),
            ("2026-04-01", "2026-06-30", None, "source_missing"),
        )
        negative = next(
            item
            for item in capture.observations
            if item.provider_code == "DRTSCIS"
            and item.source_period == "2026-Q1"
        )
        self.assertEqual(negative.value_text, "-5")
        credit_card_demand = next(
            item
            for item in capture.observations
            if item.provider_code == "DEMCC"
            and item.source_period == "2026-Q1"
        )
        self.assertEqual(credit_card_demand.value_text, "-3")
        self.assertEqual(SLOOS_SOURCE_METADATA.frequency, "quarterly")
        self.assertEqual(SLOOS_SOURCE_METADATA.max_requests, 6)
        self.assertEqual(capture.semantic_identity, SLOOS_SEMANTIC_IDENTITY)

    def test_count_header_duplicate_and_quarter_date_fail_closed(self) -> None:
        h8_bodies = _singleton_bodies(H8_FIXTURE, H8_MANIFEST)
        with self.assertRaisesRegex(ValidationError, "count"):
            parse_federal_reserve_h8(
                h8_bodies[:-1],
                captured_at=CAPTURED_AT,
                start_date="2026-07-01",
                end_date="2026-08-31",
            )

        bad_header = list(h8_bodies)
        bad_header[1] = bad_header[1].replace(
            b"TOTBKCR",
            b"UNEXPECTED",
            1,
        )
        with self.assertRaisesRegex(ValidationError, "header"):
            parse_federal_reserve_h8(
                tuple(bad_header),
                captured_at=CAPTURED_AT,
                start_date="2026-07-01",
                end_date="2026-08-31",
            )

        duplicate = list(h8_bodies)
        duplicate[0] += duplicate[0].splitlines(keepends=True)[-1]
        with self.assertRaisesRegex(ValidationError, "unique"):
            parse_federal_reserve_h8(
                tuple(duplicate),
                captured_at=CAPTURED_AT,
                start_date="2026-07-01",
                end_date="2026-08-31",
            )

        sloos_bodies = list(_singleton_bodies(SLOOS_FIXTURE, SLOOS_MANIFEST))
        sloos_bodies[0] = sloos_bodies[0].replace(
            b"2026-04-01",
            b"2026-05-01",
            1,
        )
        with self.assertRaisesRegex(ValidationError, "quarter start"):
            parse_federal_reserve_sloos(
                tuple(sloos_bodies),
                captured_at=CAPTURED_AT,
                start_date="2026-01-01",
                end_date="2026-05-01",
            )

    def test_manifest_completeness_and_aggregate_bounds_fail_closed(self) -> None:
        bodies = list(_singleton_bodies(H8_FIXTURE, H8_MANIFEST))
        bodies[0] = (
            b"observation_date,TLAACBW027SBOG\n"
            b"2025-01-01,1\n"
        )
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            parse_federal_reserve_h8(
                tuple(bodies),
                captured_at=CAPTURED_AT,
                start_date="2026-07-01",
                end_date="2026-08-31",
            )

        bodies = _singleton_bodies(H8_FIXTURE, H8_MANIFEST)
        with patch.object(
            credit,
            "MAX_TOTAL_RESPONSE_BYTES",
            sum(len(body) for body in bodies) - 1,
        ):
            with self.assertRaisesRegex(ResourceLimitError, "aggregate byte"):
                parse_federal_reserve_h8(
                    bodies,
                    captured_at=CAPTURED_AT,
                    start_date="2026-07-01",
                    end_date="2026-08-31",
                )

        with patch.object(credit, "MAX_RESPONSE_ROWS", 3):
            with self.assertRaisesRegex(ResourceLimitError, "aggregate row"):
                parse_federal_reserve_h8(
                    bodies,
                    captured_at=CAPTURED_AT,
                    start_date="2026-07-01",
                    end_date="2026-08-31",
                )


if __name__ == "__main__":
    unittest.main()
