from __future__ import annotations

import gzip
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from quant_data.errors import ValidationError
from quant_data.migrations import initialize_all, migrate_and_register_store
from quant_data.macro import live_vintages
from quant_data.macro.live_vintages import (
    CPI_ALL_ITEMS_SERIES_ID,
    CPI_CORE_SERIES_ID,
    GDP_NOMINAL_SERIES_ID,
    GDP_REAL_SERIES_ID,
    GDI_NOMINAL_SERIES_ID,
    GDI_REAL_SERIES_ID,
    MacroLiveVintagePublisher,
    TOTAL_NONFARM_PAYROLLS_SERIES_ID,
    RTDSM_NOMINAL_OUTPUT_SERIES_ID,
    RTDSM_REAL_OUTPUT_SERIES_ID,
    UNEMPLOYMENT_RATE_SERIES_ID,
    parse_bea_gdp_vintage_xlsx,
    parse_bls_cpi_revision,
    parse_bls_current_json,
    parse_bls_cpi_history_json,
    parse_bls_employment_current_json,
    parse_rtdsm_payroll_vintage_xlsx,
    parse_rtdsm_nominal_output_vintage_xlsx,
    parse_rtdsm_real_output_vintage_xlsx,
    parse_rtdsm_cpi_all_items_vintage_xlsx,
    parse_rtdsm_cpi_core_vintage_xlsx,
    parse_rtdsm_unemployment_vintage_xlsx,
)
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    gdi_vintage_registry_profile,
    load_registry,
)
from quant_data.stage1 import explicit_store_map
from quant_data.stores import StoreRole


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0013_live_gdp_cpi_vintages.sql"
EMPLOYMENT_RESOURCE = (
    PROJECT_ROOT / "quant_data/migrations/macro/0014_live_employment_vintages.sql"
)
HISTORY_RESOURCE = (
    PROJECT_ROOT / "quant_data/migrations/macro/0015_live_macro_history_extension.sql"
)
GDI_RESOURCE = (
    PROJECT_ROOT / "quant_data/migrations/macro/0017_live_gdi_vintages.sql"
)


def _cell(reference: str, value: str, *, numeric: bool = False) -> str:
    if numeric:
        return f'<c r="{reference}"><v>{escape(value)}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    )


def _excel_column(index: int) -> str:
    if index <= 0:
        raise ValueError("Excel columns are one-based")
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _rtdsm_workbook(
    *,
    sheet_name: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[str, tuple[str | None, ...]], ...],
) -> bytes:
    if not headers or any(len(values) != len(headers) for _, values in rows):
        raise ValueError("RTDSM test matrix shape is invalid")
    rendered_rows: list[str] = []
    header_cells = [("A", "DATE", False)] + [
        (_excel_column(index), value, False)
        for index, value in enumerate(headers, start=2)
    ]
    for row_number, cells in enumerate((header_cells,), start=1):
        rendered_rows.append(
            f'<row r="{row_number}">' + "".join(
                _cell(f"{column}{row_number}", value, numeric=numeric)
                for column, value, numeric in cells
            ) + "</row>"
        )
    for row_number, (period, values) in enumerate(rows, start=2):
        cells = [("A", period, False)]
        for column_index, value in enumerate(values, start=2):
            if value is None:
                continue
            cells.append(
                (
                    _excel_column(column_index),
                    value,
                    value.strip().casefold() != "#n/a",
                )
            )
        rendered_rows.append(
            f'<row r="{row_number}">' + "".join(
                _cell(f"{column}{row_number}", value, numeric=numeric)
                for column, value, numeric in cells
            ) + "</row>"
        )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rendered_rows)}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return stream.getvalue()


def _workbook(
    *,
    include_gdi: bool = True,
    gdi_missing: bool = False,
    gdi_missing_marker: str = "...",
) -> bytes:
    rows = {
        1: [
            ("A", "Reference period", False),
            ("B", "Vintage", False),
            ("C", "GDP", False),
            ("D", "GDI", False),
            ("E", "Real GDP", False),
            ("F", "Real GDI", False),
            ("G", "Release date", False),
        ],
        2: [("A", "2024:Q1", False)],
        3: [
            ("B", "Vintage", False),
            ("C", "GDP", False),
            ("D", "GDI", False),
            ("E", "Real GDP", False),
            ("F", "Real GDI", False),
            ("G", "Release date", False),
        ],
        4: [
            ("B", "Advance", False),
            ("C", "28296.967", True),
            ("E", "1.6", True),
            ("G", "Apr 25, 2024", False),
        ],
        5: [
            ("B", "Second", False),
            ("C", "28300.0", True),
            *(
                [
                    (
                        "D",
                        gdi_missing_marker if gdi_missing else "28298.0",
                        not gdi_missing,
                    )
                ]
                if include_gdi
                else []
            ),
            ("E", "1.5", True),
            *(
                [
                    (
                        "F",
                        gdi_missing_marker if gdi_missing else "1.4",
                        not gdi_missing,
                    )
                ]
                if include_gdi
                else []
            ),
            ("G", "May 30, 2024", False),
        ],
    }
    rendered_rows = "".join(
        f'<row r="{row_number}">' + "".join(
            _cell(f"{column}{row_number}", value, numeric=numeric)
            for column, value, numeric in cells
        ) + "</row>"
        for row_number, cells in rows.items()
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rendered_rows}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Vintage History" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        "</Types>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return stream.getvalue()


def _archive(*, all_items: str, core: str) -> bytes:
    return gzip.compress(
        (
            "CUSR0000SA0 CPI-U ALL ITEMS\n"
            "FINAL SEASONALLY ADJUSTED SERIES\n"
            f"2024 {all_items}\n"
            "CUSR0000SA0L1E CPI-U ALL ITEMS LESS FOOD AND ENERGY\n"
            "FINAL SEASONALLY ADJUSTED SERIES\n"
            f"2024 {core}\n"
        ).encode("utf-8")
    )


def _bls_workbook() -> bytes:
    headers = ("ITEM", "TITLE", "seriesid", "DATA_TYPE", "YEAR") + (
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
    )
    header_cells = [
        (chr(ord("A") + index), value, False)
        for index, value in enumerate(headers)
    ]
    rows = {
        1: header_cells,
        2: [
            ("C", "CUSR0000SA0", False),
            ("D", "SEASONALLY ADJUSTED INDEX", False),
            ("E", "2021", False),
            ("F", "262.64999999999998", True),
        ],
        3: [
            ("C", "CUSR0000SA0L1E", False),
            ("D", "SEASONALLY ADJUSTED INDEX", False),
            ("E", "2021", False),
            ("F", "270.38700000000001", True),
        ],
    }
    rendered_rows = "".join(
        f'<row r="{row_number}">' + "".join(
            _cell(f"{column}{row_number}", value, numeric=numeric)
            for column, value, numeric in cells
        ) + "</row>"
        for row_number, cells in rows.items()
    )
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rendered_rows}</sheetData></worksheet>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="U_S__CITY_AVG" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-02-10T19:47:11Z</dcterms:modified>'
        "</cp:coreProperties>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("docProps/core.xml", core)
    return stream.getvalue()


def _bls_history_body(
    start_year: int,
    end_year: int,
    provider_codes: tuple[str, ...],
) -> bytes:
    series = []
    for series_index, provider_code in enumerate(provider_codes, start=1):
        data = [
            {
                "year": str(year),
                "period": f"M{month:02d}",
                "value": str(year * 1000 + month * 10 + series_index),
            }
            for year in range(end_year, start_year - 1, -1)
            for month in range(12, 0, -1)
        ]
        series.append({"seriesID": provider_code, "data": data})
    return json.dumps(
        {
            "status": "REQUEST_SUCCEEDED",
            "responseTime": 5,
            "message": [],
            "Results": {"series": series},
        },
        separators=(",", ":"),
    ).encode("utf-8")


class LiveVintageParserTests(unittest.TestCase):
    def test_bea_workbook_preserves_release_date_and_first_release(self) -> None:
        capture = parse_bea_gdp_vintage_xlsx(
            _workbook(),
            captured_at="2026-08-17T12:00:00Z",
            source_published_at="Thu, 30 Jul 2026 12:30:00 GMT",
        )
        self.assertEqual(capture.provider, "bea")
        self.assertEqual(capture.source_published_precision, "datetime")
        self.assertEqual(len(capture.observations), 6)
        first = next(
            item
            for item in capture.observations
            if item.series_id == GDP_REAL_SERIES_ID and item.release_stage == "advance"
        )
        second = next(
            item
            for item in capture.observations
            if item.series_id == GDP_NOMINAL_SERIES_ID and item.release_stage == "second"
        )
        self.assertEqual(first.period, "2024Q1")
        self.assertEqual(first.available_at, "2024-04-25")
        self.assertEqual(first.available_precision, "date")
        self.assertTrue(first.is_first_release)
        self.assertFalse(second.is_first_release)
        gdi_real = next(
            item
            for item in capture.observations
            if item.series_id == GDI_REAL_SERIES_ID and item.release_stage == "second"
        )
        gdi_nominal = next(
            item
            for item in capture.observations
            if item.series_id == GDI_NOMINAL_SERIES_ID and item.release_stage == "second"
        )
        self.assertEqual((gdi_real.value_text, gdi_nominal.value_text), ("1.4", "28298"))
        self.assertTrue(gdi_real.is_first_release)
        self.assertEqual(
            gdi_real.first_release_evidence,
            "bea_gdi_vintage_history:2024Q1:earliest_release_date:2024-05-30",
        )
        self.assertFalse(
            any(
                item.series_id in {GDI_REAL_SERIES_ID, GDI_NOMINAL_SERIES_ID}
                and item.release_stage == "advance"
                for item in capture.observations
            )
        )

    def test_bea_source_missing_gdi_marker_is_not_invented(self) -> None:
        for marker in ("...", ".....", "n.a.", "…"):
            with self.subTest(marker=marker):
                capture = parse_bea_gdp_vintage_xlsx(
                    _workbook(gdi_missing=True, gdi_missing_marker=marker),
                    captured_at="2026-08-17T12:00:00Z",
                )

                self.assertEqual(
                    {item.series_id for item in capture.observations},
                    {GDP_REAL_SERIES_ID, GDP_NOMINAL_SERIES_ID},
                )

    def test_bls_workbook_uses_embedded_vintage_and_display_precision(self) -> None:
        capture = parse_bls_cpi_revision(
            _bls_workbook(),
            source_resource="https://www.bls.gov/cpi/revised-2025.xlsx",
            vintage_at="2026-07-14",
            captured_at="2026-08-17T12:00:00Z",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(capture.source_published_at, "2026-02-10")
        self.assertEqual(
            {item.series_id: item.value_text for item in capture.observations},
            {
                CPI_ALL_ITEMS_SERIES_ID: "262.65",
                CPI_CORE_SERIES_ID: "270.387",
            },
        )

    def test_bls_current_uses_normalized_batch_not_capture_time_or_response_time(self) -> None:
        first = parse_bls_current_json(
            (
                b'{"status":"REQUEST_SUCCEEDED","responseTime":"9","Results":'
                b'{"series":[{"seriesID":"CUSR0000SA0","data":'
                b'[{"year":"2026","period":"M07","value":"323.0"}]},'
                b'{"seriesID":"CUSR0000SA0L1E","data":'
                b'[{"year":"2026","period":"M07","value":"330.0"}]}]}}'
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        second = parse_bls_current_json(
            (
                b'{"status":"REQUEST_SUCCEEDED","responseTime":"999","Results":'
                b'{"series":[{"seriesID":"CUSR0000SA0","data":'
                b'[{"year":"2026","period":"M07","value":"323.0"}]},'
                b'{"seriesID":"CUSR0000SA0L1E","data":'
                b'[{"year":"2026","period":"M07","value":"330.0"}]}]}}'
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        self.assertEqual(first.semantic_identity, second.semantic_identity)
        self.assertNotEqual(first.response_sha256, second.response_sha256)
        self.assertEqual(
            {item.source_vintage_identity for item in first.observations},
            {item.source_vintage_identity for item in second.observations},
        )
        self.assertEqual(
            {item.series_id for item in first.observations},
            {CPI_ALL_ITEMS_SERIES_ID, CPI_CORE_SERIES_ID},
        )

    def test_bls_current_missing_marker_is_not_a_numeric_fact(self) -> None:
        capture = parse_bls_current_json(
            (
                b'{"status":"REQUEST_SUCCEEDED","Results":{"series":['
                b'{"seriesID":"CUSR0000SA0","data":['
                b'{"year":"2025","period":"M10","value":"-"},'
                b'{"year":"2025","period":"M09","value":"324.245"}]},'
                b'{"seriesID":"CUSR0000SA0L1E","data":['
                b'{"year":"2025","period":"M10","value":"-"},'
                b'{"year":"2025","period":"M09","value":"330.418"}]}]}}'
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertEqual(
            {(item.series_id, item.period) for item in capture.observations},
            {
                (CPI_ALL_ITEMS_SERIES_ID, "2025-09"),
                (CPI_CORE_SERIES_ID, "2025-09"),
            },
        )


class MacroHistoryExtensionParserTests(unittest.TestCase):
    def test_bls_deep_history_requires_exact_fixed_complete_windows(self) -> None:
        first = parse_bls_cpi_history_json(
            _bls_history_body(1947, 1956, ("CUSR0000SA0",)),
            captured_at="2026-08-17T16:20:53.745543Z",
            start_year=1947,
            end_year=1956,
        )
        self.assertEqual(first.family, "cpi_history")
        self.assertEqual(len(first.observations), 120)
        self.assertEqual(
            {item.series_id for item in first.observations},
            {CPI_ALL_ITEMS_SERIES_ID},
        )
        pair = parse_bls_cpi_history_json(
            _bls_history_body(
                1957,
                1966,
                ("CUSR0000SA0", "CUSR0000SA0L1E"),
            ),
            captured_at="2026-08-17T16:30:00Z",
            start_year=1957,
            end_year=1966,
        )
        self.assertEqual(len(pair.observations), 240)
        self.assertIs(live_vintages._validate_capture(pair), pair)

        invalid = json.loads(
            _bls_history_body(1947, 1956, ("CUSR0000SA0",)).decode("utf-8")
        )
        invalid["Results"]["series"][0]["data"][0]["year"] = "1957"
        with self.assertRaises(ValidationError):
            parse_bls_cpi_history_json(
                json.dumps(invalid, separators=(",", ":")).encode("utf-8"),
                captured_at="2026-08-17T16:20:53.745543Z",
                start_year=1947,
                end_year=1956,
            )
        with self.assertRaises(ValidationError):
            parse_bls_cpi_history_json(
                _bls_history_body(1947, 1956, ("CUSR0000SA0",)),
                captured_at="2026-08-17T16:20:53.745543Z",
                start_year=1947,
                end_year=1966,
            )

    def test_source_native_rtdsm_gdp_and_cpi_series_remain_distinct(self) -> None:
        nominal = parse_rtdsm_nominal_output_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="NOUTPUT",
                headers=("NOUTPUT65Q4", "NOUTPUT66Q1"),
                rows=(("1947:Q1", ("250", "251")), ("1966:Q1", (None, "900"))),
            ),
            captured_at="2026-08-17T16:15:36Z",
        )
        real = parse_rtdsm_real_output_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="ROUTPUT",
                headers=("ROUTPUT65Q4", "ROUTPUT66Q1"),
                rows=(("1947:Q1", ("200", "201")), ("1966:Q1", (None, "700"))),
            ),
            captured_at="2026-08-17T16:15:49Z",
        )
        all_items = parse_rtdsm_cpi_all_items_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="pcpi",
                headers=("PCPI98M11", "PCPI98M12"),
                rows=(("1947:01", ("21.5", "21.6")),),
            ),
            captured_at="2026-08-17T16:15:49Z",
        )
        core = parse_rtdsm_cpi_core_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="pcpix",
                headers=("PCPIX98M11", "PCPIX98M12"),
                rows=(("1957:01", ("28.5", "28.6")),),
            ),
            captured_at="2026-08-17T16:15:50Z",
        )
        self.assertEqual(
            {item.series_id for item in nominal.observations},
            {RTDSM_NOMINAL_OUTPUT_SERIES_ID},
        )
        self.assertEqual(
            {item.series_id for item in real.observations},
            {RTDSM_REAL_OUTPUT_SERIES_ID},
        )
        self.assertEqual(
            {item.series_id for item in all_items.observations},
            {CPI_ALL_ITEMS_SERIES_ID},
        )
        self.assertEqual(
            {item.series_id for item in core.observations},
            {CPI_CORE_SERIES_ID},
        )
        for capture in (nominal, real, all_items, core):
            self.assertTrue(all(item.vintage_at is None for item in capture.observations))
            self.assertIs(live_vintages._validate_capture(capture), capture)


class EmploymentLiveVintageParserTests(unittest.TestCase):
    def test_rtdsm_payroll_emits_only_first_and_changed_numeric_values(self) -> None:
        body = _rtdsm_workbook(
            sheet_name="employ",
            headers=("EMPLOY24M1", "EMPLOY24M2", "EMPLOY24M3"),
            rows=(
                ("2023:12", ("100", "100", "101")),
                ("2024:01", ("#N/A", "200", "200")),
                ("2024:02", (None, "#N/A", "201")),
            ),
        )
        first = parse_rtdsm_payroll_vintage_xlsx(
            body, captured_at="2026-08-17T12:00:00Z"
        )
        second = parse_rtdsm_payroll_vintage_xlsx(
            body, captured_at="2026-08-18T12:00:00Z"
        )
        self.assertEqual(first.provider, "philadelphia_fed")
        self.assertEqual(first.family, "employment")
        self.assertEqual(first.availability_basis, "local_capture")
        self.assertEqual(first.semantic_identity, second.semantic_identity)
        self.assertEqual(
            {
                (item.series_id, item.period, item.value_text, item.source_vintage_identity)
                for item in first.observations
            },
            {
                (TOTAL_NONFARM_PAYROLLS_SERIES_ID, "2023-12", "100", "EMPLOY24M1"),
                (TOTAL_NONFARM_PAYROLLS_SERIES_ID, "2023-12", "101", "EMPLOY24M3"),
                (TOTAL_NONFARM_PAYROLLS_SERIES_ID, "2024-01", "200", "EMPLOY24M2"),
                (TOTAL_NONFARM_PAYROLLS_SERIES_ID, "2024-02", "201", "EMPLOY24M3"),
            },
        )
        self.assertTrue(all(item.vintage_at is None for item in first.observations))
        self.assertTrue(
            all(item.available_at == "2026-08-17T12:00:00.000000Z" for item in first.observations)
        )
        self.assertEqual(
            len({item.source_row for item in first.observations}),
            len(first.observations),
        )
        self.assertIs(live_vintages._validate_capture(first), first)

    def test_rtdsm_unemployment_uses_quarterly_header_sequence(self) -> None:
        capture = parse_rtdsm_unemployment_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="ruc",
                headers=("RUC24Q1", "RUC24Q2", "RUC24Q3"),
                rows=(
                    ("2023:12", ("3.7", "3.7", "3.8")),
                    ("2024:01", (None, "3.9", "3.9")),
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertEqual(
            {
                (item.period, item.value_text, item.source_vintage_identity)
                for item in capture.observations
            },
            {
                ("2023-12", "3.7", "RUC24Q1"),
                ("2023-12", "3.8", "RUC24Q3"),
                ("2024-01", "3.9", "RUC24Q2"),
            },
        )
        self.assertEqual(
            {item.series_id for item in capture.observations},
            {UNEMPLOYMENT_RATE_SERIES_ID},
        )
        self.assertIs(live_vintages._validate_capture(capture), capture)

    def test_rtdsm_rejects_malformed_duplicate_noncontiguous_and_future_cells(self) -> None:
        invalid_matrices = {
            "malformed_header": _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M13",),
                rows=(("2024:01", ("100",)),),
            ),
            "duplicate_header": _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M1", "EMPLOY24M1"),
                rows=(("2024:01", ("100", "101")),),
            ),
            "noncontiguous_header": _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M1", "EMPLOY24M3"),
                rows=(("2024:01", ("100", "101")),),
            ),
            "duplicate_period": _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M1",),
                rows=(("2024:01", ("100",)), ("2024:01", ("101",))),
            ),
            "future_cell": _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M3",),
                rows=(("2024:04", ("100",)),),
            ),
        }
        for name, body in invalid_matrices.items():
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    parse_rtdsm_payroll_vintage_xlsx(
                        body, captured_at="2026-08-17T12:00:00Z"
                    )

    def test_employment_provider_alias_is_narrow(self) -> None:
        capture = parse_rtdsm_payroll_vintage_xlsx(
            _rtdsm_workbook(
                sheet_name="employ",
                headers=("EMPLOY24M1",),
                rows=(("2024:01", ("100",)),),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertIs(live_vintages._validate_capture(capture), capture)
        for invalid in (
            replace(capture, provider="bea"),
            replace(capture, family="cpi"),
            replace(capture, provider="bls"),
        ):
            with self.subTest(provider=invalid.provider, family=invalid.family):
                with self.assertRaises(ValidationError):
                    live_vintages._validate_capture(invalid)

    def test_bls_employment_current_requires_exact_pair_and_is_semantic_noop(self) -> None:
        first = parse_bls_employment_current_json(
            (
                b'{"status":"REQUEST_SUCCEEDED","responseTime":"5","Results":'
                b'{"series":[{"seriesID":"CES0000000001","data":'
                b'[{"year":"2026","period":"M13","value":"158000"},'
                b'{"year":"2026","period":"M07","value":"159000.0"},'
                b'{"year":"2026","period":"M06","value":"-"}]},'
                b'{"seriesID":"LNS14000000","data":'
                b'[{"year":"2026","period":"M13","value":"4.1"},'
                b'{"year":"2026","period":"M07","value":"4.2"},'
                b'{"year":"2026","period":"M06","value":"-"}]}]}}'
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        second = parse_bls_employment_current_json(
            (
                b'{"status":"REQUEST_SUCCEEDED","responseTime":"999","Results":'
                b'{"series":[{"seriesID":"CES0000000001","data":'
                b'[{"year":"2026","period":"M13","value":"158000"},'
                b'{"year":"2026","period":"M07","value":"159000.0"},'
                b'{"year":"2026","period":"M06","value":"-"}]},'
                b'{"seriesID":"LNS14000000","data":'
                b'[{"year":"2026","period":"M13","value":"4.1"},'
                b'{"year":"2026","period":"M07","value":"4.2"},'
                b'{"year":"2026","period":"M06","value":"-"}]}]}}'
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        self.assertEqual(first.semantic_identity, second.semantic_identity)
        self.assertNotEqual(first.response_sha256, second.response_sha256)
        self.assertEqual(
            {(item.series_id, item.period, item.value_text) for item in first.observations},
            {
                (TOTAL_NONFARM_PAYROLLS_SERIES_ID, "2026-07", "159000"),
                (UNEMPLOYMENT_RATE_SERIES_ID, "2026-07", "4.2"),
            },
        )
        with self.assertRaises(ValidationError):
            parse_bls_employment_current_json(
                (
                    b'{"status":"REQUEST_SUCCEEDED","Results":{"series":['
                    b'{"seriesID":"CES0000000001","data":'
                    b'[{"year":"2026","period":"M07","value":"159000"}]}]}}'
                ),
                captured_at="2026-08-17T12:00:00Z",
            )


class LiveVintagePublisherTests(unittest.TestCase):
    def _publisher(self, root: Path) -> MacroLiveVintagePublisher:
        store = root / "macro.sqlite"
        connection = sqlite3.connect(store)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(RESOURCE.read_text(encoding="utf-8"))
            connection.executescript(EMPLOYMENT_RESOURCE.read_text(encoding="utf-8"))
            connection.executescript(HISTORY_RESOURCE.read_text(encoding="utf-8"))
            connection.executescript(GDI_RESOURCE.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        return MacroLiveVintagePublisher(
            market_store=store,
            project_root=root,
            registry=object(),
        )

    def test_bea_gdi_publishes_through_additive_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = self._publisher(root)
            capture = parse_bea_gdp_vintage_xlsx(
                _workbook(),
                captured_at="2026-08-23T12:00:00Z",
            )

            report = publisher.publish(capture)

            self.assertEqual(report.outcome, "published")
            self.assertEqual(report.written_versions, 6)
            connection = sqlite3.connect(root / "macro.sqlite")
            try:
                self.assertEqual(
                    set(
                        connection.execute(
                            """
                            SELECT series_id, provider_series_code
                            FROM macro_live_vintage_series
                            WHERE provider='bea'
                            """
                        )
                    ),
                    {
                        (GDP_REAL_SERIES_ID, "A191RL"),
                        (GDP_NOMINAL_SERIES_ID, "A191RC"),
                        (GDI_REAL_SERIES_ID, "A261RL"),
                        (GDI_NOMINAL_SERIES_ID, "A261RC"),
                    },
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM macro_live_vintage_gdp_provenance"
                    ).fetchone()[0],
                    6,
                )
                self.assertEqual(
                    list(connection.execute("PRAGMA foreign_key_check")),
                    [],
                )
            finally:
                connection.close()

    def test_0017_upgrades_populated_v1_store_before_v2_gdi_publish(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            current = load_registry(
                PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                project_root=PROJECT_ROOT,
                environment={},
            )
            previous = gdi_vintage_registry_profile(current)
            stores = explicit_store_map(root)
            initialize_all(
                stores,
                previous,
                applied_at="2026-08-22T12:00:00Z",
            )
            publisher = MacroLiveVintagePublisher(
                market_store=stores.path("macro"),
                project_root=root,
                registry=previous,
            )
            bls_body = (
                b'{"status":"REQUEST_SUCCEEDED","Results":{"series":['
                b'{"seriesID":"CUSR0000SA0","data":'
                b'[{"year":"2026","period":"M07","value":"323.0"}]},'
                b'{"seriesID":"CUSR0000SA0L1E","data":'
                b'[{"year":"2026","period":"M07","value":"330.0"}]}]}}'
            )
            with patch.object(
                live_vintages,
                "NORMALIZATION_VERSION",
                "macro.live_vintage.v1",
            ):
                old_capture = parse_bea_gdp_vintage_xlsx(
                    _workbook(include_gdi=False),
                    captured_at="2026-08-22T12:00:00Z",
                )
                self.assertEqual(publisher.publish(old_capture).written_versions, 4)
                old_bls = parse_bls_current_json(
                    bls_body,
                    captured_at="2026-08-22T12:00:00Z",
                )
                self.assertEqual(publisher.publish(old_bls).written_versions, 2)

            tables = (
                "macro_live_vintage_captures",
                "macro_live_vintage_series",
                "macro_live_vintage_releases",
                "macro_live_vintage_observation_versions",
                "macro_live_vintage_capture_membership",
                "macro_live_vintage_gdp_provenance",
            )
            connection = sqlite3.connect(stores.path("macro"))
            try:
                before = tuple(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in tables
                )
            finally:
                connection.close()

            migrate_and_register_store(
                stores,
                current,
                StoreRole.MACRO,
                applied_at="2026-08-23T12:00:00Z",
            )

            connection = sqlite3.connect(stores.path("macro"))
            try:
                after_upgrade = tuple(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in tables
                )
                self.assertEqual(after_upgrade, before)
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            finally:
                connection.close()

            current_publisher = MacroLiveVintagePublisher(
                market_store=stores.path("macro"),
                project_root=root,
                registry=current,
            )
            report = current_publisher.publish(
                parse_bea_gdp_vintage_xlsx(
                    _workbook(),
                    captured_at="2026-08-23T12:00:00Z",
                )
            )
            self.assertEqual(report.outcome, "published")
            bls_report = current_publisher.publish(
                parse_bls_current_json(
                    bls_body,
                    captured_at="2026-08-23T12:00:00Z",
                )
            )
            self.assertEqual(bls_report.outcome, "published")
            self.assertEqual(bls_report.written_versions, 0)

            connection = sqlite3.connect(stores.path("macro"))
            try:
                self.assertEqual(
                    {
                        row[0]
                        for row in connection.execute(
                            "SELECT normalization_version FROM macro_live_vintage_captures"
                        )
                    },
                    {"macro.live_vintage.v1", "macro.live_vintage.v2"},
                )
                self.assertEqual(
                    {
                        row[0]
                        for row in connection.execute(
                            "SELECT series_id FROM macro_live_vintage_series WHERE provider='bea'"
                        )
                    },
                    {
                        GDP_REAL_SERIES_ID,
                        GDP_NOMINAL_SERIES_ID,
                        GDI_REAL_SERIES_ID,
                        GDI_NOMINAL_SERIES_ID,
                    },
                )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            finally:
                connection.close()

    def test_replay_corrections_and_out_of_order_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = self._publisher(root)
            recent = parse_bls_cpi_revision(
                _archive(all_items="300.0", core="310.0"),
                source_resource="https://download.bls.gov/cpi/date202501.gz",
                vintage_at="2025-01-31",
                captured_at="2025-02-01T12:00:00Z",
                media_type="application/gzip",
            )
            published = publisher.publish(recent)
            self.assertEqual(published.outcome, "published")
            self.assertEqual(published.written_versions, 2)
            replay = publisher.publish(recent)
            self.assertEqual(replay.outcome, "unchanged")
            self.assertEqual(replay.written_versions, 0)

            older = parse_bls_cpi_revision(
                _archive(all_items="290.0", core="300.0"),
                source_resource="https://download.bls.gov/cpi/date202401.gz",
                vintage_at="2024-01-31",
                captured_at="2026-08-17T12:00:00Z",
                media_type="application/gzip",
            )
            older_report = publisher.publish(older)
            self.assertEqual(older_report.written_versions, 2)

            corrected = parse_bls_cpi_revision(
                _archive(all_items="301.0", core="310.0"),
                source_resource="https://download.bls.gov/cpi/date202501.gz",
                vintage_at="2025-01-31",
                captured_at="2026-08-17T12:05:00Z",
                media_type="application/gzip",
            )
            correction_report = publisher.publish(corrected)
            self.assertEqual(correction_report.written_versions, 1)

            connection = sqlite3.connect(root / "macro.sqlite")
            try:
                current = {
                    row[0]: row[1]
                    for row in connection.execute(
                        """
                        SELECT current.series_id, version.value_text
                        FROM macro_live_vintage_observations AS current
                        JOIN macro_live_vintage_observation_versions AS version
                          ON version.version_id = current.current_version_id
                        WHERE current.period='2024-01'
                        """
                    )
                }
                self.assertEqual(
                    current,
                    {
                        CPI_ALL_ITEMS_SERIES_ID: "301",
                        CPI_CORE_SERIES_ID: "310",
                    },
                )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
            finally:
                connection.close()

    def test_employment_historical_alias_and_bls_current_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = self._publisher(root)
            payroll = parse_rtdsm_payroll_vintage_xlsx(
                _rtdsm_workbook(
                    sheet_name="employ",
                    headers=("EMPLOY64M12",),
                    rows=(("1964:12", ("70000",)),),
                ),
                captured_at="2026-08-17T12:00:00Z",
            )
            unemployment = parse_rtdsm_unemployment_vintage_xlsx(
                _rtdsm_workbook(
                    sheet_name="ruc",
                    headers=("RUC65Q4",),
                    rows=(("1965:10", ("4.2",)),),
                ),
                captured_at="2026-08-17T12:00:00Z",
            )
            current = parse_bls_employment_current_json(
                (
                    b'{"status":"REQUEST_SUCCEEDED","Results":{"series":['
                    b'{"seriesID":"CES0000000001","data":'
                    b'[{"year":"2026","period":"M07","value":"159000"}]},'
                    b'{"seriesID":"LNS14000000","data":'
                    b'[{"year":"2026","period":"M07","value":"4.2"}]}]}}'
                ),
                captured_at="2026-08-18T12:00:00Z",
            )
            self.assertEqual(publisher.publish(payroll).written_series, 1)
            self.assertEqual(publisher.publish(unemployment).written_series, 1)
            current_report = publisher.publish(current)
            self.assertEqual(current_report.outcome, "published")
            self.assertEqual(current_report.written_series, 0)
            self.assertEqual(current_report.written_versions, 2)
            self.assertEqual(publisher.publish(current).outcome, "unchanged")

            connection = sqlite3.connect(root / "macro.sqlite")
            try:
                series = {
                    row[0]: tuple(row[1:])
                    for row in connection.execute(
                        """
                        SELECT series_id, provider, provider_series_code, unit,
                               value_representation, availability_basis
                        FROM macro_live_vintage_series
                        WHERE series_id IN (?, ?)
                        ORDER BY series_id
                        """,
                        (TOTAL_NONFARM_PAYROLLS_SERIES_ID, UNEMPLOYMENT_RATE_SERIES_ID),
                    )
                }
                self.assertEqual(
                    series,
                    {
                        TOTAL_NONFARM_PAYROLLS_SERIES_ID: (
                            "bls",
                            "CES0000000001",
                            "thousands_persons",
                            "level",
                            "mixed",
                        ),
                        UNEMPLOYMENT_RATE_SERIES_ID: (
                            "bls",
                            "LNS14000000",
                            "percent",
                            "rate",
                            "mixed",
                        ),
                    },
                )
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM macro_live_vintage_captures
                        WHERE provider='philadelphia_fed'
                        """
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
            finally:
                connection.close()

    def test_deep_history_series_and_philadelphia_cpi_alias_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = self._publisher(root)
            nominal = parse_rtdsm_nominal_output_vintage_xlsx(
                _rtdsm_workbook(
                    sheet_name="NOUTPUT",
                    headers=("NOUTPUT65Q4",),
                    rows=(("1947:Q1", ("250",)),),
                ),
                captured_at="2026-08-17T16:15:36Z",
            )
            cpi = parse_rtdsm_cpi_all_items_vintage_xlsx(
                _rtdsm_workbook(
                    sheet_name="pcpi",
                    headers=("PCPI98M11",),
                    rows=(("1947:01", ("21.5",)),),
                ),
                captured_at="2026-08-17T16:15:49Z",
            )
            self.assertEqual(publisher.publish(nominal).written_series, 1)
            self.assertEqual(publisher.publish(cpi).written_series, 1)
            self.assertEqual(publisher.publish(nominal).outcome, "unchanged")
            connection = sqlite3.connect(root / "macro.sqlite")
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT provider FROM macro_live_vintage_series WHERE series_id=?",
                        (RTDSM_NOMINAL_OUTPUT_SERIES_ID,),
                    ).fetchone()[0],
                    "philadelphia_fed",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT provider FROM macro_live_vintage_series WHERE series_id=?",
                        (CPI_ALL_ITEMS_SERIES_ID,),
                    ).fetchone()[0],
                    "bls",
                )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                connection.close()
