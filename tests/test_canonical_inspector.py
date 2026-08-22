from __future__ import annotations

import hashlib
import http.client
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quant_data.boundary import create_server
from quant_data.canonical_inspector import CanonicalInspectorApplication
from quant_data.errors import ValidationError
from quant_data.json_codec import loads_strict
from quant_data.macro.fmp_release_surprises import (
    CPI_HEADLINE_MOM_KIND,
    NONFARM_PAYROLLS_KIND,
    UNEMPLOYMENT_RATE_KIND,
    ReleaseSurprise,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CanonicalInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.market = root / "market.sqlite"
        self.macro = root / "macro.sqlite"
        self._seed_market(self.market)
        self._seed_macro(self.macro)
        self.registry = load_registry(
            CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.stores = StoreMap.four_explicit(
            market=self.market,
            macro=self.macro,
            company=root / "company.sqlite",
            news=root / "news.sqlite",
        )
        self.application = CanonicalInspectorApplication(self.stores, self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _seed_market(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_metadata (singleton INTEGER PRIMARY KEY, store_role TEXT NOT NULL);
            INSERT INTO store_metadata VALUES (1, 'market');
            CREATE TABLE stage10_instruments (
                instrument_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                display_name TEXT,
                exchange_code TEXT,
                first_trade_date TEXT
            );
            CREATE TABLE stage10_daily_price_versions (
                version_id TEXT PRIMARY KEY,
                instrument_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open_value TEXT NOT NULL,
                high_value TEXT NOT NULL,
                low_value TEXT NOT NULL,
                close_value TEXT NOT NULL,
                volume INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE stage10_daily_prices (
                instrument_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO stage10_instruments VALUES
              ('i-aapl', 'fmp', 'AAPL', 'equity', 'Apple Inc.', 'NASDAQ', '1980-12-12'),
              ('i-spy', 'fmp', 'SPY', 'etf', 'SPDR S&P 500 ETF Trust', 'NYSE', '1993-01-29');
            INSERT INTO stage10_daily_price_versions VALUES
              ('v-aapl-1', 'i-aapl', '2026-08-14', '220.1', '224.0', '219.5', '223.7', 1000,
               '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z'),
              ('v-spy-1', 'i-spy', '2026-08-14', '640.0', '644.0', '639.0', '643.0', 2000,
               '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z');
            INSERT INTO stage10_daily_prices VALUES
              ('i-aapl', '2026-08-14', 'v-aapl-1'),
              ('i-spy', '2026-08-14', 'v-spy-1');
            """
        )
        connection.commit()
        connection.close()
        Path(str(path) + chr(45) + chr(119) + chr(97) + chr(108)).touch()

    @staticmethod
    def _seed_macro(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_metadata (singleton INTEGER PRIMARY KEY, store_role TEXT NOT NULL);
            INSERT INTO store_metadata VALUES (1, 'macro');
            CREATE TABLE macro_live_vintage_series (
                series_id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_releases (
                release_id TEXT PRIMARY KEY,
                source_vintage_identity TEXT NOT NULL,
                vintage_at TEXT,
                release_stage TEXT,
                is_first_release INTEGER,
                source_release_order TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_observation_versions (
                version_id TEXT PRIMARY KEY,
                series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                release_id TEXT NOT NULL,
                value_text TEXT NOT NULL,
                unit TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE macro_live_vintage_observations (
                series_id TEXT NOT NULL,
                period TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            INSERT INTO macro_live_vintage_series VALUES
              ('macro.gdp.real_qoq_saar_pct', 'Real GDP growth');
            INSERT INTO macro_live_vintage_releases VALUES
              ('r-2026q2', '2026-07-30-advance', '2026-07-30', 'advance', 1, '2026-07-30');
            INSERT INTO macro_live_vintage_observation_versions VALUES
              ('gdp-v1', 'macro.gdp.real_qoq_saar_pct', '2026Q2', 'r-2026q2', '3.0', 'percent', 1,
               '2026-07-30', '2026-08-17T12:00:00Z');
            INSERT INTO macro_live_vintage_observations VALUES
              ('macro.gdp.real_qoq_saar_pct', '2026Q2', 'gdp-v1');

            CREATE TABLE treasury_yield_curve_versions (
                curve_version_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                curve_date TEXT NOT NULL,
                curve_variant TEXT NOT NULL,
                tenor TEXT NOT NULL,
                series_id TEXT NOT NULL,
                yield_value TEXT,
                missing_reason TEXT,
                available_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                correction_sequence INTEGER NOT NULL
            );
            INSERT INTO treasury_yield_curve_versions VALUES
              (
                'curve-20260820-1m-v1', 'fmp', '2026-08-20', 'par_yield',
                '1M', 'macro.treasury.par_yield.1m', '4.20', NULL,
                '2026-08-20T22:00:00Z', '2026-08-20T22:00:00Z', 1
              ),
              (
                'curve-20260820-10y-v1', 'fmp', '2026-08-20', 'par_yield',
                '10Y', 'macro.treasury.par_yield.10y', '4.30', NULL,
                '2026-08-20T22:00:00Z', '2026-08-20T22:00:00Z', 1
              ),
              (
                'curve-20260820-10y-v2', 'fmp', '2026-08-20', 'par_yield',
                '10Y', 'macro.treasury.par_yield.10y', '4.40', NULL,
                '2026-08-21T12:00:00Z', '2026-08-21T12:00:00Z', 2
              ),
              (
                'curve-20260821-1m-v1', 'fmp', '2026-08-21', 'par_yield',
                '1M', 'macro.treasury.par_yield.1m', '4.10', NULL,
                '2026-08-21T22:00:00Z', '2026-08-21T22:00:00Z', 1
              );

            CREATE TABLE ingestion_runs (
                run_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE ingestion_artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE TABLE ingestion_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                completeness TEXT NOT NULL,
                validation_state TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_captures (
                capture_id TEXT PRIMARY KEY,
                request_start_date TEXT NOT NULL,
                request_end_date TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                semantic_identity TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL
            );
            CREATE TABLE fmp_economic_calendar_rows (
                capture_id TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                row_sha256 TEXT NOT NULL,
                raw_row_json TEXT NOT NULL,
                event_at TEXT NOT NULL,
                country TEXT NOT NULL,
                event_name TEXT NOT NULL,
                currency TEXT,
                unit TEXT,
                previous_json TEXT,
                estimate_json TEXT,
                actual_json TEXT,
                change_json TEXT,
                impact_json TEXT,
                change_percentage_json TEXT
            );

            INSERT INTO ingestion_runs VALUES
              (
                'raw-run-1', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'succeeded'
              ),
              (
                'raw-run-2', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'succeeded'
              ),
              (
                'raw-run-pending', 'macro.fmp.economic_calendar_evidence',
                'fmp.macro.us_economic_calendar_wholesale', 'running'
              );
            INSERT INTO ingestion_artifacts VALUES
              (
                'raw-artifact-1', 'raw-run-1',
                'macro.fmp.economic_calendar_evidence',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
              ),
              (
                'raw-artifact-2', 'raw-run-2',
                'macro.fmp.economic_calendar_evidence',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
              ),
              (
                'raw-artifact-pending', 'raw-run-pending',
                'macro.fmp.economic_calendar_evidence',
                'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
              );
            INSERT INTO ingestion_snapshots VALUES
              (
                'raw-snapshot-1', 'raw-run-1',
                'macro.fmp.economic_calendar_evidence',
                '1111111111111111111111111111111111111111111111111111111111111111',
                'complete', 'validated'
              ),
              (
                'raw-snapshot-2', 'raw-run-2',
                'macro.fmp.economic_calendar_evidence',
                '2222222222222222222222222222222222222222222222222222222222222222',
                'complete', 'validated'
              ),
              (
                'raw-snapshot-pending', 'raw-run-pending',
                'macro.fmp.economic_calendar_evidence',
                '3333333333333333333333333333333333333333333333333333333333333333',
                'complete', 'validated'
              );
            INSERT INTO fmp_economic_calendar_captures VALUES
              (
                'raw-capture-1', '2026-08-01', '2026-08-10',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '1111111111111111111111111111111111111111111111111111111111111111',
                '2026-08-11T12:00:00Z', 'raw-run-1', 'raw-artifact-1', 'raw-snapshot-1'
              ),
              (
                'raw-capture-2', '2026-08-01', '2026-08-17',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                '2222222222222222222222222222222222222222222222222222222222222222',
                '2026-08-18T12:00:00Z', 'raw-run-2', 'raw-artifact-2', 'raw-snapshot-2'
              ),
              (
                'raw-capture-pending', '2026-08-01', '2026-08-17',
                'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                '3333333333333333333333333333333333333333333333333333333333333333',
                '2026-08-18T12:01:00Z', 'raw-run-pending',
                'raw-artifact-pending', 'raw-snapshot-pending'
              );
            INSERT INTO fmp_economic_calendar_rows VALUES
              (
                'raw-capture-1', 1,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1',
                '{"actual":-23,"country":"US","event":"Non Farm Payrolls (Jul)","impact":"High","note":"<b>jobs</b>"}',
                '2026-08-07 12:30:00', 'US', 'Non Farm Payrolls (Jul)', 'USD', 'K',
                '14', '80', '-23', '-37', '"High"', NULL
              ),
              (
                'raw-capture-1', 2,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2',
                '{"actual":4.1,"country":"US","event":"Unemployment Rate (Jul)","impact":"High"}',
                '2026-08-07 12:30:00', 'US', 'Unemployment Rate (Jul)', 'USD', '%',
                '4.1', '4.2', '4.1', '-0.1', '"High"', NULL
              ),
              (
                'raw-capture-2', 1,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1',
                '{"actual":-22,"country":"US","event":"Non Farm Payrolls (Jul)","impact":"High","note":"jobs revision"}',
                '2026-08-07 12:30:00', 'US', 'Non Farm Payrolls (Jul)', 'USD', 'K',
                '14', '80', '-22', '-36', '"High"', NULL
              ),
              (
                'raw-capture-2', 2,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2',
                '{"actual":0.2,"country":"US","event":"CPI MoM (Jul)","impact":"Medium"}',
                '2026-08-13 12:30:00', 'US', 'CPI MoM (Jul)', 'USD', '%',
                '0.3', '0.2', '0.2', '-0.1', '"Medium"', NULL
              ),
              (
                'raw-capture-2', 3,
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb3',
                '{"actual":12,"country":"CA","event":"Employment Change","impact":"Low"}',
                '2026-08-14 12:30:00', 'CA', 'Employment Change', 'CAD', 'K',
                '8', '10', '12', '4', '"Low"', NULL
              ),
              (
                'raw-capture-pending', 1,
                'ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc1',
                '{"country":"US","event":"Hidden Pending Event","impact":"High"}',
                '2026-08-15 12:30:00', 'US', 'Hidden Pending Event', 'USD', NULL,
                NULL, NULL, NULL, NULL, '"High"', NULL
              );
            """
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_all_fixed_views_return_rows_without_mutating_stores(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        routes = (
            ("/api/rows?view=market-prices&symbol=AAPL", "AAPL"),
            ("/api/rows?view=market-instruments&search=Apple", "Apple Inc."),
            (
                "/api/rows?view=macro-current&series=macro.gdp.real_qoq_saar_pct",
                "2026Q2",
            ),
            (
                "/api/rows?view=macro-vintages&series=macro.gdp.real_qoq_saar_pct&period=2026Q2",
                "2026-07-30-advance",
            ),
            (
                "/api/rows?view=treasury-curve&tenor=10Y"
                "&start_date=2026-08-20&end_date=2026-08-20",
                "4.40",
            ),
            (
                "/api/rows?view=fmp-economic-calendar&country=US&priority=High"
                "&event_name=Non%20Farm&keyword=jobs&start_date=2026-08-07"
                "&end_date=2026-08-07",
                "Non Farm Payrolls",
            ),
        )
        for route, expected in routes:
            with self.subTest(route=route):
                response = self.application.handle("GET", route)
                self.assertEqual(response.status, 200)
                payload = loads_strict(response.body)
                self.assertEqual(payload["execution"], "read_only")
                self.assertIn(expected, str(payload["result"]["rows"]))

        treasury = self.application.handle(
            "GET",
            "/api/rows?view=treasury-curve&tenor=10Y"
            "&start_date=2026-08-20&end_date=2026-08-20",
        )
        treasury_result = loads_strict(treasury.body)["result"]
        self.assertEqual(treasury_result["total"], 1)
        self.assertEqual(treasury_result["rows"][0]["yield_percent"], "4.40")
        self.assertEqual(treasury_result["rows"][0]["correction"], 2)

        surprise = SimpleNamespace(
            event_at="2026-07-30T12:30:00Z",
            reference_period="2026Q2",
            kind="us_gdp_real_qoq_saar_advance",
            consensus=SimpleNamespace(),
            fmp_actual=None,
            official_actual=None,
            surprise=None,
            unit="percent",
            actual_source="bea_gdp_vintage",
            consensus_mapping_basis="exact_bea_advance_date",
            status="missing_consensus",
            official_version_id="gdp-official-version",
            official_prior_version_id=None,
            release_stage="advance",
            is_fallback=False,
            coalesced_event_version_ids=(),
        )
        surprise.consensus = None
        with patch("quant_data.canonical_inspector.MacroReleaseSurpriseRepository") as repository:
            repository.return_value.query.return_value = (surprise,)
            response = self.application.handle(
                "GET",
                "/api/rows?view=macro-surprises&stage=advance",
            )
        self.assertEqual(response.status, 200)
        surprise_row = loads_strict(response.body)["result"]["rows"][0]
        self.assertEqual(surprise_row["reference_period"], "2026Q2")
        self.assertEqual(surprise_row["official_version_id"], "gdp-official-version")
        self.assertIsNone(surprise_row["official_prior_version_id"])
        self.assertEqual(surprise_row["release_stage"], "advance")
        self.assertFalse(surprise_row["is_fallback"])
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_raw_fmp_calendar_filters_lineage_and_literal_search(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        target = (
            "/api/rows?view=fmp-economic-calendar&country=us&priority=High"
            "&event_name=Non%20Farm&keyword=jobs&start_date=2026-08-07"
            "&end_date=2026-08-07&direction=asc"
        )
        response = self.application.handle("GET", target)
        self.assertEqual(response.status, 200)
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            [row["capture_id"] for row in result["rows"]],
            ["raw-capture-1", "raw-capture-2"],
        )
        self.assertEqual(result["rows"][0]["priority"], "High")
        self.assertEqual(result["rows"][0]["estimate"], 80)
        self.assertEqual(result["rows"][0]["actual"], -23)
        self.assertIn("<b>jobs</b>", result["rows"][0]["raw_row_json"])
        self.assertNotIn("response_bytes", result["rows"][0])

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&country=CA&priority=Low",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["event_name"], "Employment Change")

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&priority=Medium",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["event_name"], "CPI MoM (Jul)")

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=%25",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            {row["event_name"] for row in result["rows"]},
            {"CPI MoM (Jul)", "Unemployment Rate (Jul)"},
        )

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=_",
        )
        self.assertEqual(loads_strict(response.body)["result"]["total"], 0)

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&keyword=Hidden",
        )
        self.assertEqual(loads_strict(response.body)["result"]["total"], 0)

        response = self.application.handle(
            "GET",
            "/api/rows?view=fmp-economic-calendar&direction=asc&limit=1&page=2",
        )
        result = loads_strict(response.body)["result"]
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["rows"][0]["capture_id"], "raw-capture-1")
        self.assertEqual(result["rows"][0]["event_name"], "Unemployment Rate (Jul)")

        document = self.application.handle(
            "GET",
            "/?view=fmp-economic-calendar&country=US&priority=High"
            "&event_name=Non%20Farm&keyword=jobs&direction=asc",
        ).body.decode("utf-8")
        self.assertIn("Raw FMP calendar", document)
        self.assertIn('name="country"', document)
        self.assertIn('name="priority"', document)
        self.assertIn('name="event_name"', document)
        self.assertIn('name="keyword"', document)
        self.assertIn("&lt;b&gt;jobs&lt;/b&gt;", document)
        self.assertNotIn("<b>jobs</b>", document)
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_macro_surprises_accept_employment_kinds_and_project_lineage_ids(
        self,
    ) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        payroll = ReleaseSurprise(
            event_id="event-payroll",
            event_version_id="event-version-payroll",
            kind=NONFARM_PAYROLLS_KIND,
            event_at="2024-06-07T12:30:00Z",
            reference_period="2024-05",
            consensus=Decimal("150"),
            consensus_mapping_basis="same_day_bls_capture",
            fmp_actual=Decimal("999"),
            official_actual=Decimal("200"),
            surprise=Decimal("50"),
            unit="thousands_persons",
            actual_source="bls_employment_vintage",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id="payroll-target-version",
            official_prior_version_id="payroll-prior-version",
        )
        unemployment = ReleaseSurprise(
            event_id="event-unemployment",
            event_version_id="event-version-unemployment",
            kind=UNEMPLOYMENT_RATE_KIND,
            event_at="2024-07-05T12:30:00Z",
            reference_period="2024-06",
            consensus=Decimal("4.0"),
            consensus_mapping_basis="first_rtdsm_vintage_proxy",
            fmp_actual=Decimal("9.9"),
            official_actual=Decimal("4.1"),
            surprise=Decimal("0.1"),
            unit="percent",
            actual_source="bls_employment_vintage",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id="unemployment-target-version",
        )
        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (payroll,)
            response = self.application.handle(
                "GET",
                f"/api/rows?view=macro-surprises&kind={NONFARM_PAYROLLS_KIND}",
            )
        self.assertEqual(response.status, 200)
        payload = loads_strict(response.body)
        row = payload["result"]["rows"][0]
        self.assertEqual(row["reference_period"], "2024-05")
        self.assertEqual(row["unit"], "thousands_persons")
        self.assertEqual(row["official_version_id"], "payroll-target-version")
        self.assertEqual(row["official_prior_version_id"], "payroll-prior-version")
        repository.return_value.query.assert_called_once_with(
            start_date=None,
            end_date=None,
            kinds=(NONFARM_PAYROLLS_KIND,),
        )

        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (unemployment,)
            html_response = self.application.handle(
                "GET",
                f"/?view=macro-surprises&kind={UNEMPLOYMENT_RATE_KIND}",
            )
        self.assertEqual(html_response.status, 200)
        document = html_response.body.decode("utf-8")
        self.assertIn(NONFARM_PAYROLLS_KIND, document)
        self.assertIn(UNEMPLOYMENT_RATE_KIND, document)
        self.assertIn("GDP growth — best available release", document)
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_macro_surprise_projects_coalesced_fmp_cpi_mapping(self) -> None:
        before = (self._sha256(self.market), self._sha256(self.macro))
        cpi = ReleaseSurprise(
            event_id="event-cpi",
            event_version_id="event-version-cpi",
            kind=CPI_HEADLINE_MOM_KIND,
            event_at="2025-12-18T13:30:00Z",
            reference_period="2025-11",
            consensus=Decimal("0.3"),
            consensus_mapping_basis=(
                "same_day_fmp_cpi_complementary_fields"
            ),
            fmp_actual=Decimal("0.1"),
            official_actual=Decimal("0.1"),
            surprise=Decimal("-0.2"),
            unit="%",
            actual_source="fmp_calendar",
            availability_assumption="event_at_utc",
            status="ok",
            official_version_id=None,
            coalesced_event_version_ids=("event-version-cpi-actual",),
        )
        with patch(
            "quant_data.canonical_inspector.MacroReleaseSurpriseRepository"
        ) as repository:
            repository.return_value.query.return_value = (cpi,)
            response = self.application.handle(
                "GET",
                f"/api/rows?view=macro-surprises&kind={CPI_HEADLINE_MOM_KIND}",
            )
        self.assertEqual(response.status, 200)
        result = loads_strict(response.body)["result"]
        row = result["rows"][0]
        self.assertEqual(row["actual_source"], "fmp_calendar")
        self.assertEqual(row["reference_period"], "2025-11")
        self.assertEqual(
            row["mapping"],
            "same_day_fmp_cpi_complementary_fields",
        )
        self.assertEqual(row["fmp_actual"], "0.1")
        self.assertEqual(row["official_actual"], "0.1")
        self.assertEqual(row["surprise"], "-0.2")
        self.assertIsNone(row["official_version_id"])
        self.assertEqual(
            row["coalesced_event_version_ids"],
            ["event-version-cpi-actual"],
        )
        self.assertIn("fmp_actual", result["columns"])
        self.assertIn("coalesced_event_version_ids", result["columns"])
        self.assertEqual(before, (self._sha256(self.market), self._sha256(self.macro)))

    def test_html_assets_and_local_server_security_headers(self) -> None:
        response = self.application.handle("GET", "/?view=market-prices&symbol=AAPL")
        self.assertEqual(response.status, 200)
        document = response.body.decode("utf-8")
        self.assertIn("Canonical Data Inspector", document)
        self.assertIn("Apple Inc.", document)
        self.assertIn("Treasury curve", document)
        self.assertIn("Raw FMP calendar", document)
        self.assertIn("Database paths, SQL, writes, and provider calls are not available", document)
        self.assertNotIn(str(self.market), document)
        self.assertEqual(self.application.handle("GET", "/assets/dashboard.css").status, 200)
        self.assertEqual(self.application.handle("GET", "/assets/inter-variable.woff2").status, 200)

        server = create_server(self.application, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            client.request("GET", "/?view=macro-current")
            served = client.getresponse()
            served.read()
            self.assertEqual(served.status, 200)
            self.assertEqual(served.getheader("Cache-Control"), "no-store")
            self.assertEqual(served.getheader("X-Frame-Options"), "DENY")
            self.assertIn("default-src 'self'", served.getheader("Content-Security-Policy"))
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        with self.assertRaises(ValidationError):
            create_server(self.application, host="0.0.0.0", port=0)

    def test_unknown_controls_methods_and_bounds_fail_closed(self) -> None:
        cases = (
            ("/api/rows?view=market-prices&sql=select", 400),
            ("/api/rows?view=market-prices&path=/tmp/store", 400),
            ("/api/rows?view=market-instruments&relation=sqlite_master", 400),
            ("/api/rows?view=market-prices&symbol=AAPL%20OR%201=1", 400),
            ("/api/rows?view=macro-current&series=unknown", 400),
            ("/api/rows?view=macro-surprises&stage=revised", 400),
            ("/api/rows?view=treasury-curve&tenor=overnight", 400),
            (
                f"/api/rows?view=macro-surprises&kind={NONFARM_PAYROLLS_KIND}"
                "&stage=second",
                400,
            ),
            ("/api/rows?view=fmp-economic-calendar&priority=Critical", 400),
            (
                "/api/rows?view=fmp-economic-calendar&start_date=2026-08-08"
                "&end_date=2026-08-07",
                400,
            ),
            ("/api/rows?view=fmp-economic-calendar&relation=sqlite_master", 400),
            ("/api/rows?view=market-prices&page=1001", 413),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                response = self.application.handle("GET", target)
                self.assertEqual(response.status, expected)
                payload = loads_strict(response.body)
                self.assertIn(payload["error"]["code"], {"invalid_request", "resource_limit"})
                self.assertNotIn(str(self.market), response.body.decode("utf-8"))

        self.assertEqual(self.application.handle("POST", "/", body=b"{}").status, 405)
        self.assertEqual(self.application.handle("DELETE", "/").status, 405)
        self.assertEqual(self.application.handle("GET", "/missing").status, 404)


if __name__ == "__main__":
    unittest.main()
