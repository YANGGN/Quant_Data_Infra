"""Focused offline tests for the fixed official condition feeds."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from quant_data.errors import ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro.official_conditions import (
    BIS_MANIFEST,
    BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
    CMDI_MANIFEST,
    CHICAGO_MANIFEST,
    H41_MANIFEST,
    OUTPUT_DATASET_IDS,
    OfficialConditionsPublisher,
    parse_bis_credit_conditions,
    parse_bls_price_wage_productivity,
    parse_chicagofed_financial_conditions,
    parse_federal_reserve_h41,
    parse_nyfed_cmdi,
    EIA_GAS_MANIFEST,
    NBER_RECESSION_MANIFEST,
    TREASURY_TGA_MANIFEST,
    parse_eia_natural_gas_storage,
    parse_nber_us_recession,
    parse_treasury_tga,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
CAPTURED_AT = "2026-08-22T14:00:00Z"


def _h41_body() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("README.txt", "FRED H.4.1 fixture")
        archive.writestr(
            "weekly,_as_of_wednesday.csv",
            "observation_date,WALCL\n"
            "2026-08-12,6600000\n"
            "2026-08-19,6600001\n",
        )
        archive.writestr(
            "weekly,_ending_wednesday.csv",
            "observation_date,WRESBAL,WTREGEN\n"
            "2026-08-12,3300000,\n"
            "2026-08-19,3300001,650001\n",
        )
    return output.getvalue()


def _chicago_body(*, corrected: bool = False, reverse: bool = False) -> bytes:
    rows = [
        "08/14/2026,-0.30,-0.15,0,0,0,0",
        f"08/21/2026,{'-0.29' if corrected else '-0.28'},-0.14,0,0,0,0",
    ]
    if reverse:
        rows.reverse()
    return (
        "Friday_of_Week,NFCI,ANFCI,Risk,Credit,Leverage,"
        "Nonfinancial_Leverage\n"
        + "\n".join(rows)
        + "\n"
    ).encode("utf-8")


def _bis_bodies() -> tuple[bytes, bytes]:
    credit = (
        "FREQ,BORROWERS_CTY,TC_BORROWERS,TC_LENDERS,CG_DTYPE,"
        "TIME_PERIOD,OBS_VALUE\n"
        "Q,US,P,A,A,2026-Q1,247.5\n"
        "Q,US,P,A,C,2026-Q1,-3.2\n"
        "Q,US,P,A,A,2026-Q2,248.1\n"
        "Q,US,P,A,C,2026-Q2,-2.7\n"
    ).encode("utf-8")
    dsr = (
        "FREQ,BORROWERS_CTY,DSR_BORROWERS,TIME_PERIOD,OBS_VALUE\n"
        "Q,US,P,2026-Q1,14.1\n"
        "Q,US,P,2026-Q2,14.2\n"
    ).encode("utf-8")
    return credit, dsr


def _bls_body() -> bytes:
    return (
        b'{"status":"REQUEST_SUCCEEDED","message":[],"Results":{"series":['
        b'{"seriesID":"WPSFD4","data":['
        b'{"year":"2026","period":"M02","value":"149.3"}]},'
        b'{"seriesID":"CES0500000003","data":['
        b'{"year":"2026","period":"M02","value":"37.62"}]},'
        b'{"seriesID":"PRS85006092","data":['
        b'{"year":"2026","period":"Q01","value":"2.4"}]}]}}'
    )


def _cmdi_body(
    *, header: str = "eow_friday", duplicate: bool = False
) -> bytes:
    strings = (
        header,
        "Market CMDI",
        "IG CMDI",
        "HY CMDI",
        "p5",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
        "p95",
        "p99",
        "Recession",
    )
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        + "".join(f"<si><t>{item}</t></si>" for item in strings)
        + "</sst>"
    )
    header_cells = "".join(
        f'<c r="{chr(ord("A") + index)}1" t="s"><v>{index}</v></c>'
        for index in range(len(strings))
    )
    second_serial = "38359" if duplicate else "38366"
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        f'<row r="1">{header_cells}</row>'
        '<row r="2"><c r="A2"><v>38359</v></c>'
        '<c r="B2"><v>0.35</v></c><c r="C2"><v>0.25</v></c>'
        '<c r="D2"><v>0.35</v></c></row>'
        f'<row r="3"><c r="A3"><v>{second_serial}</v></c>'
        '<c r="B3"><v>0.28000000000000003</v></c>'
        '<c r="C3"><v>0.26</v></c><c r="D3"><v></v></c></row>'
        "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


class OfficialConditionsParserTests(unittest.TestCase):
    def test_h41_three_components_and_missing_value(self) -> None:
        capture = parse_federal_reserve_h41(
            _h41_body(),
            captured_at=CAPTURED_AT,
            start_date="2026-08-12",
            end_date="2026-08-19",
        )

        self.assertEqual(len(H41_MANIFEST), 3)
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 6)
        missing = next(
            item
            for item in capture.observations
            if item.provider_code == "H41/H41/RESPPLLDT_XAW_N.WW"
            and item.period_start == "2026-08-12"
        )
        self.assertIsNone(missing.value_text)
        self.assertEqual(missing.missing_reason, "source_missing")

    def test_chicago_keeps_only_nfci_and_anfci(self) -> None:
        capture = parse_chicagofed_financial_conditions(
            _chicago_body(),
            captured_at=CAPTURED_AT,
            start_date="1971-01-08",
            end_date="2026-08-22",
        )

        self.assertEqual(
            tuple(item.provider_code for item in CHICAGO_MANIFEST),
            ("NFCI", "ANFCI"),
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 4)
        self.assertEqual(
            {item.provider_code for item in capture.observations},
            {"NFCI", "ANFCI"},
        )

    def test_bis_three_quarterly_series_and_period_bounds(self) -> None:
        credit, dsr = _bis_bodies()
        capture = parse_bis_credit_conditions(
            credit,
            dsr,
            captured_at=CAPTURED_AT,
            start_period="1961-Q1",
            end_period="2026-Q2",
        )

        self.assertEqual(len(BIS_MANIFEST), 3)
        self.assertEqual(len(capture.parts), 2)
        self.assertEqual(len(capture.observations), 6)
        second_quarter = [
            item
            for item in capture.observations
            if item.source_period == "2026-Q2"
        ]
        self.assertTrue(
            all(item.period_start == "2026-04-01" for item in second_quarter)
        )
        self.assertTrue(
            all(item.period_end == "2026-06-30" for item in second_quarter)
        )

    def test_bls_price_wage_and_productivity_periods(self) -> None:
        capture = parse_bls_price_wage_productivity(
            _bls_body(),
            captured_at=CAPTURED_AT,
            start_year=2017,
            end_year=2026,
        )

        self.assertEqual(
            tuple(
                item.provider_code
                for item in BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST
            ),
            ("WPSFD4", "CES0500000003", "PRS85006092"),
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 3)
        monthly = next(
            item
            for item in capture.observations
            if item.provider_code == "WPSFD4"
        )
        quarterly = next(
            item
            for item in capture.observations
            if item.provider_code == "PRS85006092"
        )
        self.assertEqual(
            (monthly.source_period, monthly.period_start, monthly.period_end),
            ("2026-02", "2026-02-01", "2026-02-28"),
        )
        self.assertEqual(
            (
                quarterly.source_period,
                quarterly.period_start,
                quarterly.period_end,
            ),
            ("2026-Q1", "2026-01-01", "2026-03-31"),
        )

    def test_bls_parser_accepts_fixed_historical_subset(self) -> None:
        body = (
            b'{"status":"REQUEST_SUCCEEDED","message":[],"Results":{"series":['
            b'{"seriesID":"PRS85006092","data":['
            b'{"year":"1956","period":"Q04","value":"1.8"}]}]}}'
        )
        capture = parse_bls_price_wage_productivity(
            body,
            captured_at=CAPTURED_AT,
            start_year=1947,
            end_year=1956,
            series_codes=("PRS85006092",),
        )

        self.assertEqual(len(capture.observations), 1)
        self.assertEqual(
            capture.observations[0].provider_code,
            "PRS85006092",
        )
        self.assertEqual(
            loads_strict(capture.request_scope_json)["series"],
            ["PRS85006092"],
        )

    def test_cmdi_three_weekly_series_and_missing_value(self) -> None:
        capture = parse_nyfed_cmdi(
            _cmdi_body(),
            captured_at=CAPTURED_AT,
            start_date="2005-01-07",
            end_date="2005-01-14",
        )

        self.assertEqual(
            tuple(item.provider_code for item in CMDI_MANIFEST),
            ("Market CMDI", "IG CMDI", "HY CMDI"),
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 6)
        market = next(
            item
            for item in capture.observations
            if item.provider_code == "Market CMDI"
            and item.period_start == "2005-01-14"
        )
        self.assertEqual(market.value_text, "0.28000000000000003")
        missing = next(
            item
            for item in capture.observations
            if item.provider_code == "HY CMDI"
            and item.period_start == "2005-01-14"
        )
        self.assertIsNone(missing.value_text)
        self.assertEqual(missing.missing_reason, "source_missing")

    def test_bad_fixed_headers_and_duplicate_rows_fail_before_store(self) -> None:
        with self.assertRaisesRegex(ValidationError, "header"):
            parse_chicagofed_financial_conditions(
                _chicago_body().replace(b"Friday_of_Week", b"Date", 1),
                captured_at=CAPTURED_AT,
                start_date="1971-01-08",
                end_date="2026-08-22",
            )
        credit, dsr = _bis_bodies()
        duplicate = credit + credit.splitlines(keepends=True)[1]
        with self.assertRaisesRegex(ValidationError, "unique"):
            parse_bis_credit_conditions(
                duplicate,
                dsr,
                captured_at=CAPTURED_AT,
                start_period="1961-Q1",
                end_period="2026-Q2",
            )
        with self.assertRaisesRegex(ValidationError, "header"):
            parse_nyfed_cmdi(
                _cmdi_body(header="date"),
                captured_at=CAPTURED_AT,
                start_date="2005-01-07",
                end_date="2005-01-14",
            )
        with self.assertRaisesRegex(ValidationError, "unique"):
            parse_nyfed_cmdi(
                _cmdi_body(duplicate=True),
                captured_at=CAPTURED_AT,
                start_date="2005-01-07",
                end_date="2005-01-14",
            )


class OfficialConditionsPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        data = self.root / "data"
        data.mkdir(parents=True)
        self.stores = StoreMap.four_explicit(
            market=data / "market.sqlite",
            macro=data / "macro.sqlite",
            company=data / "company.sqlite",
            news=data / "news.sqlite",
        )
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.MACRO,
            applied_at="2026-08-22T13:00:00Z",
        )
        self.publisher = OfficialConditionsPublisher(
            source_key="chicago",
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=self.registry,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _capture(
        self,
        *,
        corrected: bool = False,
        reverse: bool = False,
        captured_at: str = CAPTURED_AT,
    ):
        return parse_chicagofed_financial_conditions(
            _chicago_body(corrected=corrected, reverse=reverse),
            captured_at=captured_at,
            start_date="1971-01-08",
            end_date="2026-08-22",
        )

    def _counts(self) -> tuple[int, ...]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return tuple(
                int(
                    connection.execute(
                        f"SELECT count(*) FROM {relation}"
                    ).fetchone()[0]
                )
                for relation in (
                    "ingestion_runs",
                    "ingestion_artifacts",
                    "macro_source_artifacts",
                    "macro_source_snapshots",
                    "macro_observation_versions",
                    "macro_snapshot_observation_membership",
                )
            )

    def test_initial_publish_exact_replay_and_one_correction(self) -> None:
        original = self.publisher.publish(self._capture())

        self.assertEqual(original.outcome, "published")
        self.assertEqual(
            (
                original.written_series,
                original.written_observation_versions,
            ),
            (2, 4),
        )
        before = self._counts()
        replay = self.publisher.publish(
            self._capture(
                reverse=True,
                captured_at="2026-08-22T15:00:00Z",
            )
        )
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual(
            (
                replay.written_series,
                replay.written_observation_versions,
            ),
            (0, 0),
        )
        self.assertEqual(before, self._counts())

        correction = self.publisher.publish(
            self._capture(
                corrected=True,
                captured_at="2026-08-22T16:00:00Z",
            )
        )
        self.assertEqual(correction.outcome, "published")
        self.assertEqual(
            (
                correction.written_series,
                correction.written_observation_versions,
            ),
            (0, 1),
        )
        with read_connection(
            self.stores, StoreRole.MACRO
        ) as connection:
            rows = connection.execute(
                """
                SELECT correction_sequence, value_text
                FROM macro_observation_versions
                WHERE series_id='macro.chicagofed.nfci'
                  AND period_start='2026-08-21'
                ORDER BY correction_sequence
                """
            ).fetchall()
            outputs = connection.execute(
                """
                SELECT count(*) FROM ingestion_run_outputs
                WHERE run_id=?
                """,
                (correction.run_id,),
            ).fetchone()[0]
            self.assertEqual(
                list(connection.execute("PRAGMA foreign_key_check")),
                [],
            )
            self.assertEqual(
                connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0],
                "ok",
            )
        self.assertEqual(
            [tuple(item) for item in rows],
            [(1, "-0.28"), (2, "-0.29")],
        )
        self.assertEqual(outputs, len(OUTPUT_DATASET_IDS))


class OfficialConditionsAdditionalParserTests(unittest.TestCase):
    def test_treasury_eia_and_nber_fixed_semantics(self) -> None:
        treasury = parse_treasury_tga(
            (
                b'{"data":[{"record_date":"2026-08-20","account_type":"'
                b'Treasury General Account (TGA) Closing Balance",'
                b'"open_today_bal":"700000.5"},{"record_date":"2026-08-21",'
                b'"account_type":"Treasury General Account (TGA) Closing '
                b'Balance","open_today_bal":"700100"}],"meta":{"total-pages":'
                b'"1","total-count":"2"}}'
            ),
            captured_at=CAPTURED_AT,
            start_date="2026-08-20",
            end_date="2026-08-21",
        )
        self.assertEqual(len(TREASURY_TGA_MANIFEST), 1)
        self.assertEqual(
            [item.value_text for item in treasury.observations],
            ["700000.5", "700100"],
        )

        secret = "fixture-eia-key-not-retained"
        eia = parse_eia_natural_gas_storage(
            (
                f'{{"response":{{"frequency":"weekly","total":"2","api_key":'
                f'"{secret}","data":[{{"period":"2026-08-14","value":"3288"}},'
                f'{{"period":"2026-08-21","value":"3300"}}]}}}}'.encode("utf-8")
            ),
            captured_at=CAPTURED_AT,
            credential=secret,
        )
        self.assertEqual(len(EIA_GAS_MANIFEST), 1)
        self.assertNotIn(secret.encode("utf-8"), eia.parts[0].body)
        self.assertEqual(
            [item.value_text for item in eia.observations], ["3288", "3300"]
        )

        nber = parse_nber_us_recession(
            b'[{"peak":null,"trough":"2019-12-01"},'
            b'{"peak":"2020-02","trough":"2020-04"}]',
            captured_at="2020-06-15T12:00:00Z",
        )
        self.assertEqual(len(NBER_RECESSION_MANIFEST), 1)
        self.assertEqual(
            [item.source_period for item in nber.observations],
            [
                "2019-12", "2020-01", "2020-02", "2020-03",
                "2020-04", "2020-05", "2020-06",
            ],
        )
        self.assertEqual(
            [item.value_text for item in nber.observations],
            ["0", "0", "0", "1", "1", "0", "0"],
        )


if __name__ == "__main__":
    unittest.main()
