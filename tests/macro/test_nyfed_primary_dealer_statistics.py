"""Offline tests for NY Fed Primary Dealer Statistics normalization."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro.nyfed_primary_dealer_statistics import (
    PRIMARY_DEALER_MANIFEST,
    PRIMARY_DEALER_SOURCE_METADATA,
    parse_nyfed_primary_dealer_catalog,
    parse_nyfed_primary_dealer_statistics,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = (
    PROJECT_ROOT
    / "tests/fixtures/macro/nyfed_primary_dealer_catalog.json"
)
OPTIONAL_METADATA_CATALOG = (
    PROJECT_ROOT
    / "tests/fixtures/macro/nyfed_primary_dealer_catalog_optional_metadata.json"
)
HISTORY = (
    PROJECT_ROOT
    / "tests/fixtures/macro/nyfed_primary_dealer_history.json"
)
CAPTURED_AT = "2026-08-20T20:15:00Z"
SERIES_BREAK = "SBN2024"
START_DATE = "2026-08-01"
END_DATE = "2026-08-31"


def _body(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        indent=2 if pretty else None,
        sort_keys=pretty,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")


def _parse(
    catalog: bytes = CATALOG.read_bytes(),
    history: bytes = HISTORY.read_bytes(),
    *,
    captured_at: str = CAPTURED_AT,
):
    return parse_nyfed_primary_dealer_statistics(
        catalog,
        (history,),
        captured_at=captured_at,
        series_break=SERIES_BREAK,
        start_date=START_DATE,
        end_date=END_DATE,
    )


class NyFedPrimaryDealerStatisticsTests(unittest.TestCase):
    def test_manifest_catalog_and_capture_preserve_source_dimensions(self) -> None:
        catalog = parse_nyfed_primary_dealer_catalog(
            CATALOG.read_bytes(), series_break=SERIES_BREAK
        )
        self.assertEqual(
            tuple(item.provider_code for item in PRIMARY_DEALER_MANIFEST),
            ("positions", "transactions", "financing", "settlement_fails"),
        )
        self.assertEqual(
            PRIMARY_DEALER_SOURCE_METADATA.manifest,
            PRIMARY_DEALER_MANIFEST,
        )
        self.assertEqual(
            tuple((item.family, item.key_id) for item in catalog),
            (
                ("positions", "PDPOSMBS-TOT"),
                ("transactions", "PDTRGS-EXTB"),
                ("financing", "PDSORA-UTSETTOT"),
                ("settlement_fails", "PDFTD-USTET"),
            ),
        )

        capture = _parse()
        self.assertEqual(capture.captured_at, "2026-08-20T20:15:00.000000Z")
        self.assertEqual(len(capture.observations), 5)
        self.assertEqual(
            tuple(
                (
                    item.source_period,
                    item.provider_code,
                    item.value_text,
                    item.missing_reason,
                )
                for item in capture.observations
            ),
            (
                ("2026-08-12", "positions", "12000.25", None),
                ("2026-08-19", "positions", "12500.25", None),
                ("2026-08-19", "transactions", "54321", None),
                ("2026-08-19", "financing", None, "source_missing"),
                ("2026-08-19", "settlement_fails", None, "source_missing"),
            ),
        )
        dimensions = dict(capture.observations[0].dimensions)
        self.assertEqual(dimensions["official_series_key"], "PDPOSMBS-TOT")
        self.assertEqual(dimensions["category"], "Net Positions")
        self.assertEqual(dimensions["maturity"], "All maturities")
        self.assertEqual(dimensions["series_break"], SERIES_BREAK)
        scope = loads_strict(capture.request_scope_json, max_bytes=16_384)
        self.assertEqual(scope["availability_basis"], "local_capture")
        self.assertIs(scope["tombstone_authoritative"], False)

    def test_catalog_optional_metadata_is_absent_and_unknown_rows_skip(self) -> None:
        catalog = parse_nyfed_primary_dealer_catalog(
            OPTIONAL_METADATA_CATALOG.read_bytes(), series_break=SERIES_BREAK
        )
        self.assertEqual(
            tuple(
                (
                    item.family,
                    item.key_id,
                    item.description,
                    item.category,
                    item.maturity,
                )
                for item in catalog
            ),
            (
                ("positions", "PDPOS-EMPTY", None, None, None),
                ("transactions", "PDTR-MISSING", None, None, None),
                ("financing", "PDSORA-NULL", None, None, None),
                (
                    "settlement_fails",
                    "PDFT-DESCRIBED",
                    "Settlement fails to deliver",
                    "Fails",
                    "All maturities",
                ),
            ),
        )
        self.assertNotIn("PDPOS-UNSELECTED", {item.key_id for item in catalog})
        self.assertNotIn("PDSI-UNKNOWN", {item.key_id for item in catalog})

        history = _body(
            {
                "pd": {
                    "timeseries": [
                        {
                            "keyid": item.key_id,
                            "asofdate": "2026-08-19",
                            "value": "1",
                        }
                        for item in catalog
                    ]
                }
            }
        )
        capture = parse_nyfed_primary_dealer_statistics(
            OPTIONAL_METADATA_CATALOG.read_bytes(),
            (history,),
            captured_at=CAPTURED_AT,
            series_break=SERIES_BREAK,
            start_date=START_DATE,
            end_date=END_DATE,
        )
        dimensions = {
            dict(item.dimensions)["official_series_key"]: dict(item.dimensions)
            for item in capture.observations
        }
        for key_id in ("PDPOS-EMPTY", "PDTR-MISSING", "PDSORA-NULL"):
            self.assertNotIn("official_description", dimensions[key_id])
            self.assertNotIn("category", dimensions[key_id])
            self.assertNotIn("maturity", dimensions[key_id])
        self.assertEqual(
            dimensions["PDFT-DESCRIBED"]["official_description"],
            "Settlement fails to deliver",
        )

    def test_known_prefix_metadata_is_best_effort_and_omitted(self) -> None:
        raw = json.loads(OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8"))
        known = raw["pd"]["timeseries"][0]
        known["description"] = {}
        known["category"] = "Positions"
        known["seriescategory"] = "Transactions"
        known["maturity"] = " short "

        catalog = parse_nyfed_primary_dealer_catalog(
            _body(raw), series_break=SERIES_BREAK
        )
        positions = next(item for item in catalog if item.key_id == "PDPOS-EMPTY")
        self.assertEqual(
            (positions.description, positions.category, positions.maturity),
            (None, None, None),
        )

        history = _body(
            {
                "pd": {
                    "timeseries": [
                        {
                            "keyid": item.key_id,
                            "asofdate": "2026-08-19",
                            "value": "1",
                        }
                        for item in catalog
                    ]
                }
            }
        )
        capture = parse_nyfed_primary_dealer_statistics(
            _body(raw),
            (history,),
            captured_at=CAPTURED_AT,
            series_break=SERIES_BREAK,
            start_date=START_DATE,
            end_date=END_DATE,
        )
        dimensions = next(
            dict(item.dimensions)
            for item in capture.observations
            if dict(item.dimensions)["official_series_key"] == "PDPOS-EMPTY"
        )
        self.assertNotIn("official_description", dimensions)
        self.assertNotIn("category", dimensions)
        self.assertNotIn("maturity", dimensions)

    def test_unknown_prefix_malformed_metadata_skips_but_identity_remains_strict(
        self,
    ) -> None:
        raw = json.loads(OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8"))
        unknown = raw["pd"]["timeseries"][-1]
        unknown["category"] = "Positions"
        unknown["description"] = {}
        catalog = parse_nyfed_primary_dealer_catalog(
            _body(raw), series_break=SERIES_BREAK
        )
        self.assertNotIn(
            unknown["keyid"],
            {item.key_id for item in catalog},
        )

        identity = json.loads(
            OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8")
        )
        identity["pd"]["timeseries"][0]["keyid"] = "PDPOS-EMPTY "
        with self.assertRaisesRegex(ValidationError, "keyid"):
            parse_nyfed_primary_dealer_catalog(
                _body(identity), series_break=SERIES_BREAK
            )

    def test_valid_unknown_prefix_classifies_and_duplicate_still_rejects(
        self,
    ) -> None:
        raw = json.loads(OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8"))
        unknown = raw["pd"]["timeseries"][-1]
        unknown["category"] = "Positions"
        unknown["description"] = "Other aggregate positions"
        catalog = parse_nyfed_primary_dealer_catalog(
            _body(raw), series_break=SERIES_BREAK
        )
        selected = next(
            item for item in catalog if item.key_id == unknown["keyid"]
        )
        self.assertEqual(selected.family, "positions")

        unknown["description"] = {}
        raw["pd"]["timeseries"].append(dict(unknown))
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            parse_nyfed_primary_dealer_catalog(
                _body(raw), series_break=SERIES_BREAK
            )

    def test_catalog_still_requires_all_four_families(self) -> None:
        raw = json.loads(OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8"))
        raw["pd"]["timeseries"] = [
            row
            for row in raw["pd"]["timeseries"]
            if not row["keyid"].startswith("PDFT")
        ]
        with self.assertRaisesRegex(ValidationError, "family coverage"):
            parse_nyfed_primary_dealer_catalog(
                _body(raw), series_break=SERIES_BREAK
            )

    def test_catalog_key_prefixes_take_precedence_over_text_fallback(self) -> None:
        raw = json.loads(OPTIONAL_METADATA_CATALOG.read_text(encoding="utf-8"))
        raw["pd"]["timeseries"][0]["description"] = "Settlement fails"
        raw["pd"]["timeseries"][0]["category"] = "Fails"

        catalog = parse_nyfed_primary_dealer_catalog(
            _body(raw), series_break=SERIES_BREAK
        )
        positions = next(item for item in catalog if item.key_id == "PDPOS-EMPTY")
        self.assertEqual(positions.family, "positions")

    def test_semantic_identity_ignores_wire_order_capture_and_unknowns(self) -> None:
        original = _parse()
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
        catalog["pd"]["timeseries"].reverse()
        history["pd"]["timeseries"] = [
            {**row, "futureField": "ignored"}
            for row in reversed(history["pd"]["timeseries"])
        ]
        equivalent = _parse(
            _body(catalog, pretty=True),
            _body(history, pretty=True),
            captured_at="2026-08-21T20:15:00+00:00",
        )
        self.assertEqual(original.semantic_identity, equivalent.semantic_identity)
        self.assertEqual(original.observations, equivalent.observations)
        self.assertNotEqual(original.artifact_sha256, equivalent.artifact_sha256)

    def test_corrections_omissions_and_invalid_rows_are_explicit(self) -> None:
        original = _parse()
        history = json.loads(HISTORY.read_text(encoding="utf-8"))

        corrected = json.loads(json.dumps(history))
        corrected["pd"]["timeseries"][-1]["value"] = "12501"
        self.assertNotEqual(
            original.semantic_identity,
            _parse(history=_body(corrected)).semantic_identity,
        )

        omitted = json.loads(json.dumps(history))
        omitted["pd"]["timeseries"].pop(0)
        omitted_capture = _parse(history=_body(omitted))
        self.assertNotEqual(original.semantic_identity, omitted_capture.semantic_identity)
        scope = loads_strict(
            omitted_capture.request_scope_json, max_bytes=16_384
        )
        self.assertIs(scope["tombstone_authoritative"], False)

        duplicate = json.loads(json.dumps(history))
        duplicate["pd"]["timeseries"].append(
            dict(duplicate["pd"]["timeseries"][0])
        )
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            _parse(history=_body(duplicate))

        outside = json.loads(json.dumps(history))
        outside["pd"]["timeseries"][0]["asofdate"] = "2026-09-01"
        outside_capture = _parse(history=_body(outside))
        self.assertNotIn(
            "2026-09-01",
            {item.source_period for item in outside_capture.observations},
        )

    def test_history_window_filters_locally_and_includes_boundaries(self) -> None:
        rows = [
            {
                "keyid": "PDPOSMBS-TOT",
                "asofdate": as_of,
                "value": "1",
            }
            for as_of in (
                "2026-07-31",
                START_DATE,
                END_DATE,
                "2026-09-01",
            )
        ]
        capture = _parse(history=_body({"pd": {"timeseries": rows}}))
        self.assertEqual(
            tuple(item.source_period for item in capture.observations),
            (START_DATE, END_DATE),
        )

        outside_only = [
            row
            for row in rows
            if row["asofdate"] not in {START_DATE, END_DATE}
        ]
        with self.assertRaisesRegex(ValidationError, "selected history is empty"):
            _parse(
                history=_body({"pd": {"timeseries": outside_only}})
            )

    def test_raw_history_row_cap_precedes_local_window_filter(self) -> None:
        rows = [
            {
                "keyid": "PDPOSMBS-TOT",
                "asofdate": as_of,
                "value": "1",
            }
            for as_of in (
                "2026-07-29",
                "2026-07-30",
                "2026-07-31",
            )
        ]
        with patch(
            "quant_data.macro.nyfed_primary_dealer_statistics.MAX_RESPONSE_ROWS",
            2,
        ):
            with self.assertRaisesRegex(ResourceLimitError, "row bound"):
                _parse(history=_body({"pd": {"timeseries": rows}}))


if __name__ == "__main__":
    unittest.main()
