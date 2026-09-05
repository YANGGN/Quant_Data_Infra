"""Focused offline tests for the fixed official condition feeds."""

from __future__ import annotations

from dataclasses import replace
import json
from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import quant_data.macro.official_conditions as official_conditions
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.official_conditions import (
    BEA_PERSONAL_INCOME_MANIFEST,
    BIS_MANIFEST,
    BLS_PRICE_WAGE_PRODUCTIVITY_MANIFEST,
    CFNAI_MANIFEST,
    CMDI_MANIFEST,
    CHICAGO_MANIFEST,
    H41_MANIFEST,
    FED_POLICY_RATE_MANIFEST,
    INDUSTRIAL_PRODUCTION_MANIFEST,
    OUTPUT_DATASET_IDS,
    OfficialConditionsPublisher,
    parse_bea_personal_income,
    parse_bis_credit_conditions,
    parse_bls_price_wage_productivity,
    parse_chicagofed_financial_conditions,
    parse_chicagofed_national_activity,
    parse_federal_reserve_h41,
    parse_federal_reserve_industrial_production,
    parse_federal_reserve_policy_rates,
    parse_nyfed_cmdi,
    EIA_GAS_MANIFEST,
    EIA_PETROLEUM_FUNDAMENTALS_MANIFEST,
    NBER_RECESSION_MANIFEST,
    TREASURY_DEBT_MANIFEST,
    TREASURY_FISCAL_BALANCE_MANIFEST,
    TREASURY_TGA_MANIFEST,
    parse_eia_natural_gas_storage,
    parse_eia_total_motor_gasoline_stocks,
    parse_eia_distillate_fuel_oil_stocks,
    parse_eia_finished_motor_gasoline_product_supplied,
    parse_nber_us_recession,
    parse_treasury_debt,
    parse_treasury_fiscal_balance,
    parse_treasury_tga,
)
from quant_data.macro.nyfed_primary_dealer_statistics import (
    parse_nyfed_primary_dealer_statistics,
)
from quant_data.macro.treasury_securities_auctions import (
    parse_treasury_securities_auctions,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import StoreMap, StoreRole, read_connection


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
CAPTURED_AT = "2026-08-22T14:00:00Z"


TREASURY_SECURITIES_AUCTIONS_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/treasury_securities_auctions.json"
)
NYFED_PRIMARY_DEALER_CATALOG_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/nyfed_primary_dealer_catalog.json"
)
NYFED_PRIMARY_DEALER_HISTORY_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/macro/nyfed_primary_dealer_history.json"
)
_EXTERNAL_SOURCE_KEYS = (
    "treasury_securities_auctions",
    "nyfed_primary_dealer_statistics",
    "federal_reserve_h8",
    "federal_reserve_sloos",
)


def _bound_external_source_registry(registry, source_keys: tuple[str, ...]):
    definitions = []
    for source_key in source_keys:
        definition = official_conditions._definition(source_key)
        if definition is None:
            raise AssertionError(f"missing source definition: {source_key}")
        definitions.append(definition)
    collector_ids = tuple(item.collector_id for item in definitions)
    collectors = tuple(
        item
        for item in registry.collectors
        if str(item["id"]) not in collector_ids
    ) + tuple(
        {
            "id": definition.collector_id,
            "handler": definition.handler,
            "network": True,
            "version": "1.0.0",
            "output_datasets": list(OUTPUT_DATASET_IDS),
            "configuration_env": list(definition.configuration_env),
            "schedule_eligibility": {"mode": "manual_only"},
        }
        for definition in definitions
    )
    datasets = tuple(
        replace(
            item,
            collector_ids=(
                tuple(
                    collector_id
                    for collector_id in item.collector_ids
                    if collector_id not in collector_ids
                )
                + collector_ids
                if item.id in OUTPUT_DATASET_IDS
                else item.collector_ids
            ),
        )
        for item in registry.datasets
    )
    return replace(registry, collectors=collectors, datasets=datasets)


def _h41_body() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("README.txt", "FRED H.4.1 fixture")
        archive.writestr(
            "weekly,_as_of_wednesday.csv",
            "observation_date,WALCL\n"
            "2026-08-05,6599999\n"
            "2026-08-12,6600000\n"
            "2026-08-19,6600001\n"
            "2026-08-26,6600002\n",
        )
        archive.writestr(
            "weekly,_ending_wednesday.csv",
            "observation_date,WRESBAL,WTREGEN\n"
            "2026-08-05,3299999,649999\n"
            "2026-08-12,3300000,\n"
            "2026-08-19,3300001,650001\n"
            "2026-08-26,3300002,650002\n",
        )
    return output.getvalue()


def _policy_rates_body() -> bytes:
    return (
        "observation_date,IORB,DFEDTARL,DFEDTARU\n"
        "2021-07-27,.,0.00,0.25\n"
        "2021-07-28,.,0.00,0.25\n"
        "2021-07-29,0.15,0.00,0.25\n"
        "2021-07-30,0.15,0.00,0.25\n"
    ).encode("utf-8")


def _industrial_production_body() -> bytes:
    return (
        "observation_date,INDPRO\n"
        "2026-06-01,102.7\n"
        "2026-07-01,.\n"
        "2026-09-01,102.9\n"
    ).encode("utf-8")


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


def _cfnai_body(*, header: str = "Date", duplicate: bool = False) -> bytes:
    headers = (
        header,
        "P_I",
        "EU_H",
        "C_H",
        "SO_I",
        "CFNAI",
        "CFNAI_MA3",
        "DIFFUSION",
    )
    periods = ("2026:06", "2026:06" if duplicate else "2026:07")
    shared_values = headers + periods
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        + "".join(f"<si><t>{item}</t></si>" for item in shared_values)
        + "</sst>"
    )
    header_cells = "".join(
        f'<c r="{chr(ord("A") + index)}1" t="s"><v>{index}</v></c>'
        for index in range(len(headers))
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        f'<row r="1">{header_cells}</row>'
        '<row r="2"><c r="A2" t="s"><v>8</v></c>'
        '<c r="F2"><v>-0.10</v></c></row>'
        '<row r="3"><c r="A3" t="s"><v>9</v></c>'
        '<c r="F3"><v>0.14</v></c></row>'
        "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


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
        b'{"year":"2026","period":"Q01","value":"2.4"}]},'
        b'{"seriesID":"CIS1010000000000I","data":['
        b'{"year":"2026","period":"Q01","value":"167.8"}]}]}}'
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
    def test_h41_filters_superset_and_preserves_missing_value(self) -> None:
        capture = parse_federal_reserve_h41(
            _h41_body(),
            captured_at=CAPTURED_AT,
            start_date="2026-08-12",
            end_date="2026-08-19",
        )

        self.assertEqual(len(H41_MANIFEST), 3)
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 6)
        self.assertEqual(
            {item.period_start for item in capture.observations},
            {"2026-08-12", "2026-08-19"},
        )
        missing = next(
            item
            for item in capture.observations
            if item.provider_code == "H41/H41/RESPPLLDT_XAW_N.WW"
            and item.period_start == "2026-08-12"
        )
        self.assertIsNone(missing.value_text)
        self.assertEqual(missing.missing_reason, "source_missing")

    def test_policy_rates_filter_superset_and_preserve_missingness(self) -> None:
        capture = parse_federal_reserve_policy_rates(
            _policy_rates_body(),
            captured_at=CAPTURED_AT,
            start_date="2021-07-28",
            end_date="2021-07-29",
        )

        self.assertEqual(
            tuple(item.provider_code for item in FED_POLICY_RATE_MANIFEST),
            ("IORB", "DFEDTARL", "DFEDTARU"),
        )
        self.assertEqual(capture.source_key, "policy_rates")
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 6)
        iorb_before_start = next(
            item
            for item in capture.observations
            if item.provider_code == "IORB"
            and item.period_start == "2021-07-28"
        )
        self.assertIsNone(iorb_before_start.value_text)
        self.assertEqual(iorb_before_start.missing_reason, "source_missing")

    def test_industrial_production_filters_superset_and_keeps_month_bounds(self) -> None:
        capture = parse_federal_reserve_industrial_production(
            _industrial_production_body(),
            captured_at=CAPTURED_AT,
            start_date="1919-01-01",
            end_date="2026-08-22",
        )

        self.assertEqual(
            tuple(
                item.provider_code
                for item in INDUSTRIAL_PRODUCTION_MANIFEST
            ),
            ("INDPRO",),
        )
        self.assertEqual(capture.source_key, "industrial_production")
        self.assertEqual(len(capture.observations), 2)
        self.assertEqual(
            (
                capture.observations[0].source_period,
                capture.observations[0].period_start,
                capture.observations[0].period_end,
            ),
            ("2026-06", "2026-06-01", "2026-06-30"),
        )
        self.assertIsNone(capture.observations[1].value_text)
        self.assertEqual(
            capture.observations[1].missing_reason, "source_missing"
        )

    def test_chicago_keeps_headlines_and_components(self) -> None:
        capture = parse_chicagofed_financial_conditions(
            _chicago_body(),
            captured_at=CAPTURED_AT,
            start_date="1971-01-08",
            end_date="2026-08-22",
        )

        self.assertEqual(
            tuple(item.provider_code for item in CHICAGO_MANIFEST),
            ("NFCI", "ANFCI", "Risk", "Credit", "Leverage"),
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 10)
        self.assertEqual(
            {item.provider_code for item in capture.observations},
            {"NFCI", "ANFCI", "Risk", "Credit", "Leverage"},
        )

    def test_cfnai_uses_month_bounds_and_headline_only(self) -> None:
        capture = parse_chicagofed_national_activity(
            _cfnai_body(),
            captured_at=CAPTURED_AT,
            start_date="1967-03-01",
            end_date="2026-08-22",
        )

        self.assertEqual(
            tuple(item.provider_code for item in CFNAI_MANIFEST),
            ("CFNAI",),
        )
        self.assertEqual(capture.source_key, "cfnai")
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 2)
        self.assertEqual(
            (
                capture.observations[1].source_period,
                capture.observations[1].period_start,
                capture.observations[1].period_end,
                capture.observations[1].value_text,
            ),
            ("2026-07", "2026-07-01", "2026-07-31", "0.14"),
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
            (
                "WPSFD4",
                "CES0500000003",
                "PRS85006092",
                "CIS1010000000000I",
            ),
        )
        self.assertEqual(len(capture.parts), 1)
        self.assertEqual(len(capture.observations), 4)
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
        eci = next(
            item
            for item in capture.observations
            if item.provider_code == "CIS1010000000000I"
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
        self.assertEqual(eci.value_text, "167.8")

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
        with self.assertRaisesRegex(ValidationError, "shape"):
            parse_federal_reserve_industrial_production(
                _industrial_production_body().replace(
                    b"observation_date", b"date", 1
                ),
                captured_at=CAPTURED_AT,
                start_date="1919-01-01",
                end_date="2026-08-22",
            )
        with self.assertRaisesRegex(ValidationError, "outside"):
            parse_federal_reserve_industrial_production(
                _industrial_production_body().replace(
                    b"2026-06-01", b"2026-06-02", 1
                ),
                captured_at=CAPTURED_AT,
                start_date="1919-01-01",
                end_date="2026-08-22",
            )
        with self.assertRaisesRegex(ValidationError, "unique"):
            parse_chicagofed_national_activity(
                _cfnai_body(duplicate=True),
                captured_at=CAPTURED_AT,
                start_date="1967-03-01",
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

    def test_cfnai_mirror_adds_current_data_and_preserves_existing_series(self):
        workbook = OfficialConditionsPublisher(
            source_key="cfnai", macro_store=self.stores.macro,
            project_root=self.root, registry=self.registry)
        workbook.publish(parse_chicagofed_national_activity(
            _cfnai_body(), captured_at=CAPTURED_AT,
            start_date="2026-01-01", end_date="2026-09-30"))
        publisher = OfficialConditionsPublisher(
            source_key="cfnai_fred", macro_store=self.stores.macro,
            project_root=self.root, registry=self.registry)
        capture = official_conditions.parse_fred_chicagofed_national_activity(
            b"observation_date,CFNAI\n2026-06-01,-0.10\n2026-07-01,0.14\n2026-08-01,0.2\n",
            captured_at="2026-09-05T04:00:00Z",
            start_date="2026-01-01", end_date="2026-09-30")
        result = publisher.publish(capture)
        self.assertEqual(result.written_series, 0)
        self.assertEqual(result.written_observation_versions, 1)
        before = self._counts()
        self.assertEqual(publisher.publish(capture).outcome, "unchanged")
        self.assertEqual(before, self._counts())
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            row = connection.execute(
                "SELECT source_resource,media_type FROM macro_source_artifacts "
                "WHERE normalization_version='chicagofed_cfnai_fred_v1'").fetchone()
        self.assertEqual(tuple(row), ("fred/graph/fredgraph.csv/CFNAI", "text/csv"))

    def test_initial_publish_exact_replay_and_one_correction(self) -> None:
        original = self.publisher.publish(self._capture())

        self.assertEqual(original.outcome, "published")
        self.assertEqual(
            (
                original.written_series,
                original.written_observation_versions,
            ),
            (5, 10),
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


    def test_observation_dimensions_are_persisted_and_registered(self) -> None:
        original = self._capture()
        observations = tuple(
            replace(
                item,
                dimensions=(("fixture_dimension", "preserved"),),
            )
            if index == 1
            else item
            for index, item in enumerate(original.observations, start=1)
        )
        semantic_identity = official_conditions._sha256_text(
            dumps_strict(
                official_conditions._semantic_material(
                    official_conditions._DEFINITIONS["chicago"],
                    loads_strict(original.request_scope_json),
                    observations,
                )
            )
        )
        capture = replace(
            original,
            observations=observations,
            semantic_identity=semantic_identity,
        )
        report = self.publisher.publish(capture)

        self.assertEqual(report.outcome, "published")
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            stored = connection.execute(
                """
                SELECT dimensions_json
                FROM macro_observation_versions
                WHERE series_id=? AND source_row=1
                """,
                (observations[0].series_id,),
            ).fetchone()
            dimension = connection.execute(
                """
                SELECT dimension_value
                FROM macro_series_dimensions
                WHERE series_id=? AND dimension_key='fixture_dimension'
                """,
                (observations[0].series_id,),
            ).fetchone()
        self.assertEqual(stored[0], '{"fixture_dimension":"preserved"}')
        self.assertEqual(dimension[0], "preserved")



    def test_same_value_dimensions_receive_distinct_version_ids(self) -> None:
        original = self._capture()
        seed = original.observations[0]
        observations = (
            replace(
                seed,
                dimensions=(("fixture_dimension", "first"),),
                source_row=1,
            ),
            replace(
                seed,
                dimensions=(("fixture_dimension", "second"),),
                source_row=2,
            ),
            *tuple(
                replace(item, source_row=ordinal)
                for ordinal, item in enumerate(original.observations[1:], start=3)
            ),
        )
        capture = replace(
            original,
            observations=observations,
            semantic_identity=official_conditions._sha256_text(
                dumps_strict(
                    official_conditions._semantic_material(
                        official_conditions._DEFINITIONS["chicago"],
                        loads_strict(original.request_scope_json),
                        observations,
                    )
                )
            ),
        )
        self.publisher.publish(capture)

        with read_connection(self.stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT version_id, dimensions_json
                FROM macro_observation_versions
                WHERE series_id=? AND period_start=?
                ORDER BY source_row
                """,
                (seed.series_id, seed.period_start),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["version_id"] for row in rows}), 2)
        self.assertEqual(
            {row["dimensions_json"] for row in rows},
            {
                '{"fixture_dimension":"first"}',
                '{"fixture_dimension":"second"}',
            },
        )


    def test_new_source_keys_reuse_existing_registry_bindings(self) -> None:
        captures = (
            (
                "industrial_production",
                parse_federal_reserve_industrial_production(
                    _industrial_production_body(),
                    captured_at=CAPTURED_AT,
                    start_date="1919-01-01",
                    end_date="2026-08-22",
                ),
            ),
            (
                "cfnai",
                parse_chicagofed_national_activity(
                    _cfnai_body(),
                    captured_at=CAPTURED_AT,
                    start_date="1967-03-01",
                    end_date="2026-08-22",
                ),
            ),
        )
        for source_key, capture in captures:
            publisher = OfficialConditionsPublisher(
                source_key=source_key,
                macro_store=self.stores.macro,
                project_root=self.root,
                registry=self.registry,
            )
            self.assertEqual(publisher.publish(capture).outcome, "published")


    def test_external_source_definitions_construct_with_bound_registry(
        self,
    ) -> None:
        registry = _bound_external_source_registry(
            self.registry, _EXTERNAL_SOURCE_KEYS
        )
        for source_key in _EXTERNAL_SOURCE_KEYS:
            with self.subTest(source_key=source_key):
                publisher = OfficialConditionsPublisher(
                    source_key=source_key,
                    macro_store=self.stores.macro,
                    project_root=self.root,
                    registry=registry,
                )
                self.assertIsInstance(publisher, OfficialConditionsPublisher)

    def test_primary_dealer_response_count_range_fails_closed(self) -> None:
        source_key = "nyfed_primary_dealer_statistics"
        registry = _bound_external_source_registry(
            self.registry, (source_key,)
        )
        publisher = OfficialConditionsPublisher(
            source_key=source_key,
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=registry,
        )
        definition = official_conditions._definition(source_key)
        self.assertIsNotNone(definition)
        self.assertEqual(
            (definition.minimum_requests, definition.max_requests),
            (2, 33),
        )
        capture = parse_nyfed_primary_dealer_statistics(
            NYFED_PRIMARY_DEALER_CATALOG_FIXTURE.read_bytes(),
            (NYFED_PRIMARY_DEALER_HISTORY_FIXTURE.read_bytes(),),
            captured_at=CAPTURED_AT,
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )
        self.assertEqual(len(capture.parts), 2)
        for parts in (capture.parts[:1], capture.parts * 17):
            with self.subTest(part_count=len(parts)):
                with self.assertRaisesRegex(ValidationError, "capture binding"):
                    publisher.publish(replace(capture, parts=parts))

    def test_dimensional_external_sources_replay_and_correct(self) -> None:
        source_keys = (
            "treasury_securities_auctions",
            "nyfed_primary_dealer_statistics",
        )
        registry = _bound_external_source_registry(self.registry, source_keys)
        auctions = OfficialConditionsPublisher(
            source_key=source_keys[0],
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=registry,
        )
        primary_dealers = OfficialConditionsPublisher(
            source_key=source_keys[1],
            macro_store=self.stores.macro,
            project_root=self.root,
            registry=registry,
        )
        auction_body = TREASURY_SECURITIES_AUCTIONS_FIXTURE.read_bytes()
        catalog_body = NYFED_PRIMARY_DEALER_CATALOG_FIXTURE.read_bytes()
        history_body = NYFED_PRIMARY_DEALER_HISTORY_FIXTURE.read_bytes()
        auction_original = parse_treasury_securities_auctions(
            auction_body,
            captured_at=CAPTURED_AT,
            start_date="2026-08-20",
            end_date="2026-08-21",
        )
        dealer_original = parse_nyfed_primary_dealer_statistics(
            catalog_body,
            (history_body,),
            captured_at=CAPTURED_AT,
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )
        self.assertEqual(
            (
                auctions.publish(auction_original).outcome,
                primary_dealers.publish(dealer_original).outcome,
            ),
            ("published", "published"),
        )
        before_replay = self._counts()
        auction_replay = parse_treasury_securities_auctions(
            auction_body,
            captured_at="2026-08-22T15:00:00Z",
            start_date="2026-08-20",
            end_date="2026-08-21",
        )
        dealer_replay = parse_nyfed_primary_dealer_statistics(
            catalog_body,
            (history_body,),
            captured_at="2026-08-22T15:00:00Z",
            series_break="SBN2024",
            start_date="2026-08-01",
            end_date="2026-08-31",
        )
        self.assertEqual(auctions.publish(auction_replay).outcome, "unchanged")
        self.assertEqual(
            primary_dealers.publish(dealer_replay).outcome, "unchanged"
        )
        self.assertEqual(before_replay, self._counts())

        auction_corrected = json.loads(auction_body)
        auction_corrected["data"][0]["offering_amt"] = "76000000000"
        dealer_corrected = json.loads(history_body)
        dealer_corrected["pd"]["timeseries"][-1]["value"] = "12501"
        auction_report = auctions.publish(
            parse_treasury_securities_auctions(
                json.dumps(
                    auction_corrected, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                captured_at="2026-08-22T16:00:00Z",
                start_date="2026-08-20",
                end_date="2026-08-21",
            )
        )
        dealer_report = primary_dealers.publish(
            parse_nyfed_primary_dealer_statistics(
                catalog_body,
                (
                    json.dumps(
                        dealer_corrected, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                ),
                captured_at="2026-08-22T16:00:00Z",
                series_break="SBN2024",
                start_date="2026-08-01",
                end_date="2026-08-31",
            )
        )
        self.assertEqual(
            (
                auction_report.written_series,
                auction_report.written_observation_versions,
                dealer_report.written_series,
                dealer_report.written_observation_versions,
            ),
            (0, 1, 0, 1),
        )
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            auction_versions = connection.execute(
                """
                SELECT correction_sequence, value_text
                FROM macro_observation_versions
                WHERE series_id='macro.treasury_fiscal.auction.offering_amount'
                  AND period_start='2026-08-20'
                  AND dimensions_json LIKE '%"cusip":"91282CMA4"%'
                ORDER BY correction_sequence
                """
            ).fetchall()
            dealer_versions = connection.execute(
                """
                SELECT correction_sequence, value_text
                FROM macro_observation_versions
                WHERE series_id='macro.nyfed.primary_dealer.positions'
                  AND period_start='2026-08-19'
                  AND dimensions_json LIKE '%"official_series_key":"PDPOSMBS-TOT"%'
                ORDER BY correction_sequence
                """
            ).fetchall()
            auction_dimensions = connection.execute(
                """
                SELECT count(DISTINCT dimensions_digest)
                FROM macro_observation_versions
                WHERE series_id='macro.treasury_fiscal.auction.offering_amount'
                  AND period_start='2026-08-20'
                """
            ).fetchone()[0]
            self.assertEqual(
                list(connection.execute("PRAGMA foreign_key_check")), []
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )
        self.assertEqual(
            [tuple(item) for item in auction_versions],
            [(1, "75000000000"), (2, "76000000000")],
        )
        self.assertEqual(
            [tuple(item) for item in dealer_versions],
            [(1, "12500.25"), (2, "12501")],
        )
        self.assertEqual(auction_dimensions, 2)


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

        debt = parse_treasury_debt(
            (
                b'{"data":[{"record_date":"2026-08-20",'
                b'"tot_pub_debt_out_amt":"37000123456789.01",'
                b'"debt_held_public_amt":"null",'
                b'"intragov_hold_amt":"null"},'
                b'{"record_date":"2026-08-21",'
                b'"tot_pub_debt_out_amt":"37001123456789.01",'
                b'"debt_held_public_amt":"29501123456789.01",'
                b'"intragov_hold_amt":"7500000000000.00"}],'
                b'"meta":{"total-pages":"1","total-count":"2"}}'
            ),
            captured_at=CAPTURED_AT,
            start_date="2026-08-20",
            end_date="2026-08-21",
        )
        self.assertEqual(len(TREASURY_DEBT_MANIFEST), 3)
        self.assertEqual(len(debt.observations), 6)
        self.assertEqual(
            [item.provider_code for item in debt.observations[:3]],
            [
                "tot_pub_debt_out_amt",
                "debt_held_public_amt",
                "intragov_hold_amt",
            ],
        )
        self.assertEqual(
            [item.value_text for item in debt.observations[:3]],
            ["37000123456789.01", None, None],
        )
        self.assertEqual(
            [item.missing_reason for item in debt.observations[:3]],
            [None, "source_missing", "source_missing"],
        )

        fiscal = parse_treasury_fiscal_balance(
            (
                b'{"data":['
                b'{"record_date":"2026-06-30","amt_category":"Receipts",'
                b'"mil_amt":"526000"},'
                b'{"record_date":"2026-06-30","amt_category":"Outlays",'
                b'"mil_amt":"499000"},'
                b'{"record_date":"2026-06-30",'
                b'"amt_category":"Deficit/Surplus (-)","mil_amt":"-27000"},'
                b'{"record_date":"2026-06-30",'
                b'"amt_category":"Borrowing from the Public","mil_amt":"100"},'
                b'{"record_date":"2026-07-31","amt_category":"Receipts",'
                b'"mil_amt":"334010"},'
                b'{"record_date":"2026-07-31","amt_category":"Outlays",'
                b'"mil_amt":"766318"},'
                b'{"record_date":"2026-07-31",'
                b'"amt_category":"Deficit/Surplus (-)","mil_amt":"432308"},'
                b'{"record_date":"2026-07-31",'
                b'"amt_category":"Borrowing from the Public","mil_amt":"100"}],'
                b'"meta":{"total-pages":"1","total-count":"8"}}'
            ),
            captured_at=CAPTURED_AT,
            start_date="2026-06-01",
            end_date="2026-07-31",
        )
        self.assertEqual(len(TREASURY_FISCAL_BALANCE_MANIFEST), 3)
        self.assertEqual(len(fiscal.observations), 6)
        june_deficit = next(
            item
            for item in fiscal.observations
            if item.provider_code == "Deficit/Surplus (-)"
            and item.source_period == "2026-06"
        )
        july_receipts = next(
            item
            for item in fiscal.observations
            if item.provider_code == "Receipts"
            and item.source_period == "2026-07"
        )
        self.assertEqual(june_deficit.value_text, "-27000")
        self.assertEqual(
            (
                july_receipts.period_start,
                july_receipts.period_end,
                july_receipts.value_text,
            ),
            ("2026-07-01", "2026-07-31", "334010"),
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

    def test_bea_personal_income_table_selects_three_series_and_redacts_key(
        self,
    ) -> None:
        secret = "fixture-bea-key-not-retained"
        payload = {
            "BEAAPI": {
                "Request": {
                    "RequestParam": [
                        {
                            "ParameterName": "UserID",
                            "ParameterValue": secret,
                        }
                    ]
                },
                "Results": {
                    "Data": [
                        {
                            "TableName": "T20600",
                            "SeriesCode": "A065RC",
                            "LineNumber": "1",
                            "LineDescription": "Personal income",
                            "TimePeriod": "2026M06",
                            "METRIC_NAME": "Current Dollars",
                            "CL_UNIT": "Level",
                            "UNIT_MULT": "6",
                            "DataValue": "26,500.1",
                        },
                        {
                            "TableName": "T20600",
                            "SeriesCode": "A067RC",
                            "LineNumber": "27",
                            "LineDescription": (
                                "Equals: Disposable personal income"
                            ),
                            "TimePeriod": "2026M06",
                            "METRIC_NAME": "Current Dollars",
                            "CL_UNIT": "Level",
                            "UNIT_MULT": "6",
                            "DataValue": "22,300.2",
                        },
                        {
                            "TableName": "T20600",
                            "SeriesCode": "DPCERC",
                            "LineNumber": "29",
                            "LineDescription": (
                                "Personal consumption expenditures"
                            ),
                            "TimePeriod": "2026M06",
                            "METRIC_NAME": "Current Dollars",
                            "CL_UNIT": "Level",
                            "UNIT_MULT": "6",
                            "DataValue": "20,900.3",
                        },
                    ]
                },
            }
        }
        capture = parse_bea_personal_income(
            dumps_strict(payload).encode("utf-8"),
            captured_at=CAPTURED_AT,
            credential=secret,
        )

        self.assertEqual(len(BEA_PERSONAL_INCOME_MANIFEST), 3)
        self.assertEqual(capture.source_key, "bea_personal_income")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["A065RC", "A067RC", "DPCERC"],
        )
        self.assertEqual(
            [item.value_text for item in capture.observations],
            ["26500.1", "22300.2", "20900.3"],
        )
        self.assertEqual(
            {
                (item.period_start, item.period_end)
                for item in capture.observations
            },
            {("2026-06-01", "2026-06-30")},
        )
        self.assertEqual(
            {item.unit for item in BEA_PERSONAL_INCOME_MANIFEST},
            {"usd_millions_saar"},
        )
        self.assertNotIn(secret.encode("utf-8"), capture.parts[0].body)

        payload["BEAAPI"]["Results"]["Data"][0]["LineNumber"] = "2"
        with self.assertRaises(ValidationError):
            parse_bea_personal_income(
                dumps_strict(payload).encode("utf-8"),
                captured_at=CAPTURED_AT,
                credential=secret,
            )

        payload["BEAAPI"]["Results"]["Data"][0]["LineNumber"] = "1"
        payload["BEAAPI"]["Results"]["Data"].append(
            {
                "TableName": "T20600",
                "SeriesCode": "A065RC",
                "LineNumber": "1",
                "LineDescription": "Personal income",
                "TimePeriod": "2026M07",
                "METRIC_NAME": "Current Dollars",
                "CL_UNIT": "Level",
                "UNIT_MULT": "6",
                "DataValue": "26,600.4",
            }
        )
        with self.assertRaises(ValidationError):
            parse_bea_personal_income(
                dumps_strict(payload).encode("utf-8"),
                captured_at=CAPTURED_AT,
                credential=secret,
            )

    def test_eia_weekly_petroleum_fundamentals_are_fixed_and_redacted(
        self,
    ) -> None:
        secret = "fixture-eia-key-not-retained"

        def body(series: str, units: str, value: str) -> bytes:
            return (
                f'{{"response":{{"frequency":"weekly","total":"1","api_key":'
                f'"{secret}","data":[{{"period":"2026-08-14",'
                f'"series":"{series}","units":"{units}",'
                f'"value":"{value}"}}]}}}}'
            ).encode("utf-8")

        captures = (
            parse_eia_total_motor_gasoline_stocks(
                body("WGTSTUS1", "MBBL", "235000"),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
            parse_eia_distillate_fuel_oil_stocks(
                body("WDISTUS1", "MBBL", "120000"),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
            parse_eia_finished_motor_gasoline_product_supplied(
                body(
                    "WGFUPUS2",
                    "MBBL/D",
                    "9100",
                ),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
        )

        self.assertEqual(len(EIA_PETROLEUM_FUNDAMENTALS_MANIFEST), 3)
        self.assertEqual(
            [capture.source_key for capture in captures],
            [
                "eia_total_motor_gasoline_stocks",
                "eia_distillate_fuel_oil_stocks",
                "eia_finished_motor_gasoline_product_supplied",
            ],
        )
        self.assertEqual(
            [capture.observations[0].provider_code for capture in captures],
            ["WGTSTUS1", "WDISTUS1", "WGFUPUS2"],
        )
        self.assertEqual(
            [capture.observations[0].value_text for capture in captures],
            ["235000", "120000", "9100"],
        )
        self.assertEqual(
            [item.unit for item in EIA_PETROLEUM_FUNDAMENTALS_MANIFEST],
            [
                "thousand_barrels",
                "thousand_barrels",
                "thousand_barrels_per_day",
            ],
        )
        for capture in captures:
            self.assertNotIn(secret.encode("utf-8"), capture.parts[0].body)

        legacy_units = (
            parse_eia_total_motor_gasoline_stocks(
                body("WGTSTUS1", "Thousand Barrels", "235000"),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
            parse_eia_distillate_fuel_oil_stocks(
                body("WDISTUS1", "Thousand Barrels", "120000"),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
            parse_eia_finished_motor_gasoline_product_supplied(
                body(
                    "WGFUPUS2",
                    "Thousand Barrels per Day",
                    "9100",
                ),
                captured_at=CAPTURED_AT,
                credential=secret,
            ),
        )
        self.assertEqual(
            [capture.observations[0].value_text for capture in legacy_units],
            ["235000", "120000", "9100"],
        )

        for series, units in (
            ("WRONG_SERIES", "Thousand Barrels"),
            ("WGTSTUS1", "Wrong Units"),
            ("WGTSTUS1", "MBBL/D"),
            ("WGTSTUS1", "MBBL/day"),
        ):
            with self.subTest(series=series, units=units):
                with self.assertRaises(ValidationError):
                    parse_eia_total_motor_gasoline_stocks(
                        body(series, units, "235000"),
                        captured_at=CAPTURED_AT,
                        credential=secret,
                    )

        with self.assertRaises(ValidationError):
            parse_eia_distillate_fuel_oil_stocks(
                b'{"response":{"frequency":"weekly","total":"2","data":['
                b'{"period":"2026-08-14","series":"WDISTUS1",'
                b'"units":"Thousand Barrels","value":"120000"},'
                b'{"period":"2026-08-14","series":"WDISTUS1",'
                b'"units":"Thousand Barrels","value":"120001"}]}}',
                captured_at=CAPTURED_AT,
                credential=secret,
            )


if __name__ == "__main__":
    unittest.main()
