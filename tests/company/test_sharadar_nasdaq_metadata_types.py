"""Synthetic regressions for type spellings observed in Nasdaq metadata."""
import json
import unittest
from decimal import Decimal

from quant_data.company.sharadar_sf1 import _cell, DIMENSIONS
from quant_data.company.sharadar_definitions import parse_definition_metadata, parse_definition_page
from quant_data.errors import ValidationError
from quant_data.json_codec import loads_strict
from tests.company.test_sharadar_sf1 import AT, COLS, metadata, page, row


class NasdaqMetadataTypeTests(unittest.TestCase):
    def test_lowercase_text_and_double_preserve_native_schema_and_exact_values(self):
        columns = [(name, "text" if kind == "String" else
                    "double" if name in ("revenue", "eps") else kind)
                   for name, kind in COLS]
        schema = metadata(columns)
        parsed = page([row(dimension=d, revenue=Decimal("12345678901234567890.123456789"),
                           eps=Decimal("0.000000000123456789")) for d in sorted(DIMENSIONS)],
                      columns=columns, schema=schema)
        self.assertEqual(len(parsed.rows), 6)
        self.assertEqual(dict(schema.columns)["ticker"], "text")
        self.assertEqual(dict(schema.columns)["revenue"], "double")
        self.assertIn(b'"type":"text"', schema.metadata_body)
        for item in parsed.rows:
            values = loads_strict(item.values_json)
            self.assertEqual(values["ticker"], "AAPL")
            self.assertEqual(values["revenue"], "12345678901234567890.123456789")
            self.assertEqual(values["eps"], "0.000000000123456789")
        self.assertEqual(parsed, page([row(dimension=d,
            revenue=Decimal("12345678901234567890.123456789"),
            eps=Decimal("0.000000000123456789")) for d in sorted(DIMENSIONS)],
            columns=columns, schema=schema))
        # Original native type declarations continue to distinguish source keys.
        self.assertNotEqual(schema.key_contract_id, metadata().key_contract_id)

    def test_observed_aliases_keep_validation_and_unknown_types_lossless(self):
        self.assertIsNone(_cell(None, "text"))
        self.assertIsNone(_cell(None, "double"))
        for value, kind in ((12, "text"), (True, "double"),
                            ("NaN", "double"), ("Infinity", "double")):
            with self.subTest(value=value, kind=kind), self.assertRaises(ValidationError):
                _cell(value, kind)
        self.assertEqual(_cell("001.20", "vendor-text"), {"json_type":"string","value":"001.20"})
        self.assertEqual(_cell(Decimal("1.20"), "vendor-number"), {"json_type":"number","value":"1.2"})

    def test_lowercase_indicator_metadata_and_page_preserve_text_flags(self):
        names = ("table", "indicator", "isfilter", "isprimarykey", "title", "description", "unitstype")
        columns = [{"name":name, "type":"text"} for name in names]
        body = json.dumps({"datatable":{"vendor_code":"SHARADAR", "datatable_code":"INDICATORS",
            "columns":columns, "primary_key":["table","indicator"],
            "filters":["indicator","isfilter","isprimarykey","table"]}}).encode()
        schema = parse_definition_metadata(body, captured_at=AT, source_reference="fixture/metadata")
        raw = json.dumps({"datatable":{"columns":columns,
            "data":[["SF1","revenue","N","N","Revenue","Original definition",None]]},
            "meta":{"next_cursor_id":None}}).encode()
        parsed = parse_definition_page(raw, schema=schema, parameters={"table":"SF1"},
            captured_at=AT, source_reference="fixture/page")
        self.assertEqual(schema.body, body)
        self.assertEqual(parsed.body, raw)
        values = loads_strict(parsed.rows[0].values_json)
        self.assertEqual(values["indicator"], "revenue")
        self.assertEqual(values["isfilter"], "N")
        self.assertIsNone(values["unitstype"])
        self.assertEqual(parsed.rows[0].indicator, "revenue")

    def test_identity_types_still_reject_nontext_or_unsupported_spellings(self):
        for kind in ("Integer", "DOUBLE", "vendor-text"):
            columns = [(name, kind if name == "ticker" else old) for name, old in COLS]
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                metadata(columns)
