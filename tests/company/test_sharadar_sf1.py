"""Synthetic Nasdaq envelopes; these fixtures do not establish account metadata."""
from dataclasses import replace
from decimal import Decimal
import json
import unittest
from quant_data.company.sharadar_sf1 import *
from quant_data.json_codec import dumps_strict,loads_strict

AT="2026-09-09T01:00:00.000000Z"
LATER="2026-09-09T02:00:00.000000Z"
COLS=[("ticker","String"),("dimension","String"),("datekey","Date"),("reportperiod","Date"),
      ("calendardate","Date"),("lastupdated","Date"),("revenue","BigDecimal(30,6)"),("currency","String"),("eps","Double")]
PARAMS={"ticker":"AAPL","dimension":",".join(sorted(DIMENSIONS)),"qopts.per_page":"10000"}

def metadata(columns=COLS,keys=("ticker","dimension","datekey","reportperiod"),**kwargs):
    body=dumps_strict({"datatable":{"vendor_code":"SHARADAR","datatable_code":"SF1",
        "columns":[{"name":n,"type":t} for n,t in columns],"filters":["ticker","dimension","lastupdated"],
        "primary_key":list(keys)}}).encode()
    return parse_metadata(body,captured_at=kwargs.get("captured_at",AT),source_reference="fixtures/sf1-metadata.json")
def row(**changes):
    return {"ticker":"AAPL","dimension":"ARQ","datekey":"2026-07-31","reportperiod":"2026-06-27",
        "calendardate":"2026-06-30","lastupdated":"2026-09-08","revenue":Decimal("123456789012345678901234567890.123456"),
        "currency":"USD","eps":None,**changes}
def page(rows=None,columns=COLS,cursor=None,parameters=None,**kwargs):
    body=dumps_strict({"datatable":{"columns":[{"name":n,"type":t} for n,t in columns],
        "data":[[r.get(n) for n,t in columns] for r in (rows if rows is not None else [row()])]},
        "meta":{"next_cursor_id":cursor}}).encode()
    return parse_page(body,schema=kwargs.get("schema") or metadata(),parameters=parameters or PARAMS,
        captured_at=kwargs.get("captured_at",AT),source_reference=kwargs.get("source_reference","fixtures/page.json"))
def partition(*pages):
    return prepare_partition(pages,max_pages=10,max_rows=100000,max_bytes=32*1024*1024)

class SharadarParserTests(unittest.TestCase):
    def test_all_dimensions_exact_decimal_and_distinct_source_dates(self):
        parsed=page([row(dimension=d) for d in sorted(DIMENSIONS)])
        self.assertEqual(len({r.observation_id for r in parsed.rows}),6)
        item=parsed.rows[0];values=loads_strict(item.values_json)
        self.assertEqual(values["revenue"],"123456789012345678901234567890.123456")
        self.assertEqual(item.reportperiod,"2026-06-27")
        self.assertEqual(item.calendardate,"2026-06-30")
        self.assertEqual(item.source_datekey,"2026-07-31")
        self.assertIn("eps",loads_strict(item.missingness_json)["null_fields"])
        self.assertIn(b"123456789012345678901234567890.123456",parsed.body)

    def test_column_and_row_order_or_capture_time_do_not_change_semantic_state(self):
        original=partition(page([row(),row(dimension="MRQ")]))
        reordered=partition(page([row(dimension="MRQ"),row()],columns=list(reversed(COLS)),captured_at=LATER))
        self.assertEqual(original.semantic_hash,reordered.semantic_hash)
        self.assertNotEqual(original.acquisition_id,reordered.acquisition_id)
        self.assertEqual(metadata().schema_id,metadata(list(reversed(COLS))).schema_id)

    def test_null_absent_unknown_columns_and_metadata_change_are_distinct(self):
        original=page()
        missing=page(columns=[c for c in COLS if c[0]!="eps"])
        extra=page([row(new_metric=Decimal("1.200"))],columns=COLS+[("new_metric","Double")])
        self.assertEqual(loads_strict(missing.rows[0].missingness_json)["absent_columns"],["eps"])
        self.assertNotEqual(original.rows[0].row_semantic_hash,missing.rows[0].row_semantic_hash)
        self.assertEqual(loads_strict(extra.rows[0].unrecognized_fields_json)["new_metric"],{"type":"Double","value":"1.2"})
        updated=page([row(lastupdated="2026-09-09")])
        self.assertEqual(original.rows[0].value_hash,updated.rows[0].value_hash)
        self.assertNotEqual(original.rows[0].row_semantic_hash,updated.rows[0].row_semantic_hash)

    def test_source_key_is_metadata_pinned_and_conflicting_duplicates_fail(self):
        first=row();second=row(reportperiod="2026-06-28",revenue=Decimal("5"))
        self.assertEqual(len({r.observation_id for r in page([first,second]).rows}),2)
        with self.assertRaises(ConflictError):page([first,second],schema=metadata(keys=("ticker","dimension","datekey")))
        with self.assertRaises(ConflictError):page([first,row(revenue=Decimal("5"))])
        self.assertEqual(len(page([first,first]).rows),2)
        self.assertEqual(page([first,first]).rows[1].source_row_pointer,"/datatable/data/1")

    def test_complete_partial_and_changing_cursor_walks_are_not_confused(self):
        first=page([row()],cursor="next")
        partial=partition(first)
        self.assertFalse(partial.transport_complete)
        second=page([row(dimension="MRQ")],parameters={**PARAMS,"qopts.cursor_id":"next"},captured_at=LATER)
        complete=partition(first,second)
        self.assertTrue(complete.transport_complete);self.assertEqual(complete.coherence,"unproven")
        with self.assertRaises(ConflictError):partition(first,page([row(dimension="MRQ")],cursor="next",parameters={**PARAMS,"qopts.cursor_id":"next"}))
        with self.assertRaises(ConflictError):partition(first,page([row(revenue=Decimal("9"))],parameters={**PARAMS,"qopts.cursor_id":"next"}))
        with self.assertRaises(ConflictError):partition(page(),page())

    def test_parameter_scope_credentials_and_unsupported_filters_fail(self):
        for params in ({**PARAMS,"api_key":"secret"},{**PARAMS,"revenue.gt":"1"},
                       {**PARAMS,"ticker.gt":"A"},{**PARAMS,"dimension":"UNKNOWN"},
                       {**PARAMS,"qopts.per_page":"10001"}):
            with self.subTest(params=params),self.assertRaises((ValidationError,ResourceLimitError)):
                page(parameters=params)
        with self.assertRaises(ConflictError):page([row(ticker="MSFT")])
        with self.assertRaises(ConflictError):page(parameters={**PARAMS,"lastupdated.gte":"2026-09-09"})
        with self.assertRaises(ConflictError):page(captured_at="2026-09-08T00:00:00Z")

    def test_nonfinite_fractional_integer_and_changed_types_rejected(self):
        with self.assertRaises(ValidationError):page([row(revenue="NaN")])
        changed=[(n,"Integer" if n=="revenue" else t) for n,t in COLS]
        with self.assertRaises(ConflictError):page(columns=changed)
        with self.assertRaises(ValidationError):page([row(revenue=Decimal("1.1"))],columns=changed,schema=metadata(changed))
        with self.assertRaises(ValidationError):page([row(datekey=None)])
        with self.assertRaises(ValidationError):metadata(keys=("ticker",))

    def test_bounds_and_tampered_prepared_records_fail(self):
        with self.assertRaises(ResourceLimitError):prepare_partition((page(),),max_pages=1,max_rows=1,max_bytes=1)
        with self.assertRaises(ValidationError):partition("invalid")
        with self.assertRaises(ValidationError):partition(replace(page(),rows=()))
        with self.assertRaises(ValidationError):parse_metadata("{}",captured_at=AT,source_reference="fixture.json")
        with self.assertRaises(ValidationError):parse_page(page().body,schema=metadata(),parameters=PARAMS,captured_at=AT,source_reference="../escape")
