"""Offline tests for the lean FMP GDP/CPI release-calendar mapping."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.macro.fmp_release_surprises import (
    ALL_SURPRISE_KINDS,
    CPI_CORE_MOM_KIND,
    CPI_CORE_YOY_KIND,
    CPI_HEADLINE_MOM_KIND,
    CPI_HEADLINE_YOY_KIND,
    EMPLOYMENT_CALENDAR_COLLECTOR_ID,
    EMPLOYMENT_KINDS,
    EVENT_DATE_AVAILABILITY_ASSUMPTION,
    GDP_ADVANCE_KIND,
    NONFARM_PAYROLLS_KIND,
    UNEMPLOYMENT_RATE_KIND,
    FmpEmploymentCalendarPublisher,
    FmpMacroCalendarPublisher,
    MacroReleaseSurpriseRepository,
    parse_fmp_us_employment_calendar as _parse_fmp_us_employment_calendar,
    parse_fmp_us_gdp_cpi_calendar as _parse_fmp_us_gdp_cpi_calendar,
)
from quant_data.macro.live_vintages import (
    BLS_CURRENT_RESOURCE,
    PHILADELPHIA_FED_PAYROLL_RTDSM_RESOURCE,
    PHILADELPHIA_FED_UNEMPLOYMENT_RTDSM_RESOURCE,
    TOTAL_NONFARM_PAYROLLS_SERIES_ID,
    UNEMPLOYMENT_RATE_SERIES_ID,
    MacroLiveVintagePublisher,
    VintageObservation,
    _build_capture,
)
from quant_data.migrations import migrate_and_register_store
from quant_data.registry import load_registry
from quant_data.stores import (
    StoreMap,
    StoreRole,
    StoreWriteLock,
    read_connection,
    stable_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
_MIGRATION_TIME = "2026-08-17T12:00:00Z"


def _stores(root: Path) -> StoreMap:
    data = root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _row(
    event: str,
    date: str,
    *,
    actual: str | None = "1.5",
    estimate: str | None = "1.0",
    previous: str | None = "0.5",
) -> dict[str, object]:
    return {
        "date": date,
        "country": "US",
        "event": event,
        "currency": "USD",
        "previous": previous,
        "estimate": estimate,
        "actual": actual,
        "unit": "%",
    }



def _employment_row(
    event: str,
    date: str,
    *,
    actual: str | None = "200",
    estimate: str | None = "180",
    previous: str | None = "170",
    unit: str = "K",
    currency: str = "USD",
) -> dict[str, object]:
    return {
        "date": date,
        "country": "US",
        "event": event,
        "currency": currency,
        "previous": previous,
        "estimate": estimate,
        "actual": actual,
        "unit": unit,
    }


def _body(*rows: dict[str, object]) -> bytes:
    return json.dumps(list(rows), separators=(",", ":"), sort_keys=True).encode("utf-8")


def parse_fmp_us_gdp_cpi_calendar(
    body: bytes,
    *,
    captured_at: str,
    start_date: str = "2000-01-01",
    end_date: str = "2030-12-31",
):
    return _parse_fmp_us_gdp_cpi_calendar(
        body,
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


def parse_fmp_us_employment_calendar(
    body: bytes,
    *,
    captured_at: str,
    start_date: str = "2000-01-01",
    end_date: str = "2030-12-31",
):
    return _parse_fmp_us_employment_calendar(
        body,
        captured_at=captured_at,
        start_date=start_date,
        end_date=end_date,
    )


class FmpCalendarAliasTests(unittest.TestCase):
    def test_cleveland_cpi_is_not_a_bls_target_or_semantic_change(self) -> None:
        target = _row("Inflation Rate MoM (Aug)", "2026-09-11 12:30:00")
        core = _row("Core Inflation Rate MoM (Aug)", "2026-09-11 12:30:00")
        cleveland = _row("Cleveland CPI MoM (Aug)", "2026-09-11 13:00:00")
        unadjusted = _row("CPI n.s.a MoM (Aug)", "2026-09-11 12:30:00", actual=None, estimate=None, previous="-0.01")
        kwargs = {"captured_at": "2026-09-05T12:00:00Z"}
        baseline = parse_fmp_us_gdp_cpi_calendar(_body(target, core), **kwargs)
        mixed = parse_fmp_us_gdp_cpi_calendar(_body(target, core, cleveland, unadjusted), **kwargs)
        self.assertEqual(mixed.semantic_identity, baseline.semantic_identity)
        self.assertNotEqual(mixed.response_sha256, baseline.response_sha256)
        self.assertEqual(len(mixed.events), 2)
        with self.assertRaisesRegex(ValidationError, "contains no reviewed GDP/CPI events"):
            parse_fmp_us_gdp_cpi_calendar(_body(cleveland), **kwargs)
        with self.assertRaisesRegex(ValidationError, "contains no reviewed GDP/CPI events"):
            parse_fmp_us_gdp_cpi_calendar(_body(unadjusted), **kwargs)
        with self.assertRaisesRegex(ValidationError, "alias is not reviewed"):
            parse_fmp_us_gdp_cpi_calendar(
                _body(target, _row("Regional CPI MoM", "2026-09-11 13:00:00")),
                **kwargs,
            )

    def test_request_window_and_row_bounds_are_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "outside its request window"):
            parse_fmp_us_gdp_cpi_calendar(
                _body(
                    _row(
                        "Inflation Rate MoM (Feb)",
                        "2013-03-15 12:30:00",
                    )
                ),
                captured_at="2026-08-17T12:00:00Z",
                start_date="2013-01-01",
                end_date="2013-01-31",
            )
        oversized = _body(
            *(
                _row(
                    "Inflation Rate MoM (Feb)",
                    "2013-03-15 12:30:00",
                )
                for _ in range(2_001)
            )
        )
        with self.assertRaisesRegex(ResourceLimitError, "row bound"):
            parse_fmp_us_gdp_cpi_calendar(
                oversized,
                captured_at="2026-08-17T12:00:00Z",
            )

    def test_historical_cpi_aliases_resolve_by_release_context(self) -> None:
        captured = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Inflation Rate MoM (Feb)",
                    "2013-03-15 12:30:00",
                    actual="0.7",
                    estimate=None,
                    previous="0.0",
                ),
                _row(
                    "CPI MoM (Feb)",
                    "2013-03-15 12:30:00",
                    actual="0.2",
                    estimate=None,
                    previous="0.3",
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        by_kind = {event.kind: event for event in captured.events}
        self.assertEqual(by_kind[CPI_HEADLINE_MOM_KIND].actual, Decimal("0.7"))
        self.assertEqual(by_kind[CPI_CORE_MOM_KIND].actual, Decimal("0.2"))

        overlap = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Inflation Rate MoM (Dec)",
                    "2025-01-15 13:30:00",
                    actual="0.4",
                    estimate="0.3",
                    previous="0.3",
                ),
                _row(
                    "CPI MoM (Dec)",
                    "2025-01-15 13:30:00",
                    actual="0.4",
                    estimate="0.4",
                    previous="0.3",
                ),
                _row(
                    "Core Inflation Rate MoM (Dec)",
                    "2025-01-15 13:30:00",
                    actual="0.2",
                    estimate="0.2",
                    previous="0.3",
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        by_kind = {event.kind: event for event in overlap.events}
        self.assertEqual(by_kind[CPI_HEADLINE_MOM_KIND].consensus, Decimal("0.3"))
        self.assertEqual(by_kind[CPI_CORE_MOM_KIND].actual, Decimal("0.2"))

        stale_core = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Core CPI MoM (Oct)",
                    "2025-01-15 13:30:00",
                    actual=None,
                    estimate=None,
                    previous="0.3",
                ),
                _row(
                    "Core CPI MoM (Dec)",
                    "2025-01-15 13:30:00",
                    actual="0.2",
                    estimate="0.2",
                    previous="0.3",
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertEqual(len(stale_core.events), 1)
        selected = stale_core.events[0]
        self.assertEqual(selected.kind, CPI_CORE_MOM_KIND)
        self.assertEqual(selected.source_name, "Core CPI MoM (Dec)")
        self.assertEqual(selected.actual, Decimal("0.2"))
        self.assertEqual(selected.consensus, Decimal("0.2"))

    def test_aliases_deduplicate_and_preserve_alias_independent_identity(self) -> None:
        captured = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="1.6",
                    estimate="2.0",
                ),
                _row(
                    "Gross Domestic Product QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="1.6",
                    estimate="2.0",
                ),
                _row(
                    "Inflation Rate MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.3",
                    estimate="0.2",
                ),
                _row(
                    "CPI MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.3",
                    estimate="0.2",
                ),
                _row(
                    "Core CPI YoY (Apr)",
                    "2024-05-15 12:30:00",
                    actual="3.6",
                    estimate="3.5",
                ),
                _row("GDPNow (Q1)", "2024-04-25 12:30:00"),
                _row("CPI (Apr)", "2024-05-15 12:30:00"),
                _row("GDP Price Index (Q1)", "2024-04-25 12:30:00"),
                _row("GDP Consumer Spending YoY (Q1)", "2024-04-25 12:30:00"),
                _row("GDP Consumer Spending QoQ (Q1)", "2024-04-25 12:30:00"),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )

        self.assertEqual(
            {event.kind for event in captured.events},
            {GDP_ADVANCE_KIND, CPI_HEADLINE_MOM_KIND, CPI_CORE_YOY_KIND},
        )
        by_kind = {event.kind: event for event in captured.events}
        gdp = by_kind[GDP_ADVANCE_KIND]
        cpi = by_kind[CPI_HEADLINE_MOM_KIND]
        core = by_kind[CPI_CORE_YOY_KIND]
        self.assertEqual(gdp.source_rows, (1, 2))
        self.assertEqual(cpi.source_rows, (3, 4))
        self.assertEqual(core.source_rows, (5,))
        self.assertEqual(gdp.actual, Decimal("1.6"))
        self.assertEqual(cpi.consensus, Decimal("0.2"))

        alternate_alias = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Gross Domestic Product QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="1.6",
                    estimate="2.0",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        original_alias = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="1.6",
                    estimate="2.0",
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertEqual(alternate_alias.semantic_identity, original_alias.semantic_identity)
        self.assertNotEqual(alternate_alias.response_sha256, original_alias.response_sha256)

    def test_conflicting_alias_duplicates_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "duplicate aliases conflict"):
            parse_fmp_us_gdp_cpi_calendar(
                _body(
                    _row(
                        "GDP Growth Rate QoQ (Q1)",
                        "2024-04-25 12:30:00",
                        actual="1.6",
                        estimate="2.0",
                    ),
                    _row(
                        "Gross Domestic Product QoQ (Q1)",
                        "2024-04-25 12:30:00",
                        actual="1.6",
                        estimate="2.1",
                    ),
                ),
                captured_at="2026-08-17T12:00:00Z",
            )

    def test_unreviewed_candidate_reports_its_sanitized_label(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "not reviewed: gdp unreviewed label qoq",
        ):
            parse_fmp_us_gdp_cpi_calendar(
                _body(
                    _row(
                        "GDP Unreviewed/Label QoQ (Q1)",
                        "2024-04-25 12:30:00",
                    )
                ),
                captured_at="2026-08-17T12:00:00Z",
            )



    def test_employment_aliases_units_and_closed_rejections(self) -> None:
        capture = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Non-Farm Payrolls (Mon)",
                    "2024-06-07 12:30:00",
                    actual="200",
                    estimate="180",
                    previous="170",
                ),
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-06-07 12:30:00",
                    actual="200",
                    estimate="180",
                    previous="170",
                ),
                _employment_row(
                    "Unemployment Rate (Mon)",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    previous="3.8",
                    unit="%",
                ),
                _employment_row(
                    "ADP Employment Change",
                    "2024-06-05 12:15:00",
                    unit="K",
                ),
                _employment_row(
                    "Initial Jobless Claims",
                    "2024-06-06 12:30:00",
                    unit="K",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        by_kind = {event.kind: event for event in capture.events}
        self.assertEqual(set(by_kind), set(EMPLOYMENT_KINDS))
        self.assertEqual(
            by_kind[NONFARM_PAYROLLS_KIND].unit, "thousands_persons"
        )
        self.assertEqual(by_kind[UNEMPLOYMENT_RATE_KIND].unit, "percent")
        self.assertEqual(by_kind[NONFARM_PAYROLLS_KIND].source_rows, (1, 2))
        self.assertTrue(set(EMPLOYMENT_KINDS).issubset(ALL_SURPRISE_KINDS))

        with self.assertRaisesRegex(
            ValidationError,
            "employment event alias is not reviewed: nonfarm payrolls revised",
        ):
            parse_fmp_us_employment_calendar(
                _body(
                    _employment_row(
                        "Nonfarm Payrolls Revised",
                        "2024-06-07 12:30:00",
                    )
                ),
                captured_at="2026-08-18T12:00:00Z",
            )
        with self.assertRaisesRegex(ValidationError, "currency or unit"):
            parse_fmp_us_employment_calendar(
                _body(
                    _employment_row(
                        "Nonfarm Payrolls",
                        "2024-06-07 12:30:00",
                        unit="%",
                    )
                ),
                captured_at="2026-08-18T12:00:00Z",
            )
        with self.assertRaisesRegex(ValidationError, "duplicate aliases conflict"):
            parse_fmp_us_employment_calendar(
                _body(
                    _employment_row(
                        "Non-Farm Payrolls",
                        "2024-06-07 12:30:00",
                        estimate="180",
                    ),
                    _employment_row(
                        "Nonfarm Payrolls",
                        "2024-06-07 12:30:00",
                        estimate="181",
                    ),
                ),
                captured_at="2026-08-18T12:00:00Z",
            )

    def test_employment_historical_non_target_aliases_are_excluded(self) -> None:
        capture = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Non Farm Payrolls (Mon)",
                    "2024-06-07 12:30:00",
                ),
                _employment_row(
                    "Unemployment Rate (Mon)",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    previous="3.8",
                    unit="%",
                ),
                _employment_row(
                    "Nonfarm Payrolls Private (Mon)",
                    "2024-06-07 12:30:00",
                ),
                _employment_row(
                    "U-6 Unemployment Rate (Mon)",
                    "2024-06-07 12:30:00",
                    actual="7.4",
                    estimate="7.3",
                    previous="7.2",
                    unit="%",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )

        self.assertEqual(len(capture.events), 2)
        by_kind = {event.kind: event for event in capture.events}
        self.assertEqual(set(by_kind), set(EMPLOYMENT_KINDS))
        self.assertEqual(
            by_kind[NONFARM_PAYROLLS_KIND].unit, "thousands_persons"
        )
        self.assertEqual(by_kind[UNEMPLOYMENT_RATE_KIND].unit, "percent")
        self.assertEqual(by_kind[NONFARM_PAYROLLS_KIND].source_rows, (1,))
        self.assertEqual(by_kind[UNEMPLOYMENT_RATE_KIND].source_rows, (2,))

    def test_employment_benchmark_revision_remains_raw_only(self) -> None:
        capture = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Non Farm Payrolls (Jul)",
                    "2024-08-02 12:30:00",
                    actual="114",
                    estimate="175",
                ),
                _employment_row(
                    "Non Farm Payrolls (Mar)",
                    "2024-08-21 14:00:00",
                    actual="-818",
                    estimate=None,
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
            start_date="2024-07-22",
            end_date="2024-10-19",
        )

        self.assertEqual(len(capture.events), 1)
        self.assertEqual(capture.events[0].kind, NONFARM_PAYROLLS_KIND)
        self.assertEqual(capture.events[0].reference_period, "2024-07")
        self.assertEqual(capture.events[0].event_at, "2024-08-02T12:30:00Z")

    def test_employment_delayed_double_release_retains_each_reference_month(self) -> None:
        capture = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Non Farm Payrolls (Sep)",
                    "2025-11-20 13:30:00",
                    actual="119",
                    estimate="50",
                ),
                _employment_row(
                    "Unemployment Rate (Sep)",
                    "2025-11-20 13:30:00",
                    actual="4.4",
                    estimate="4.3",
                    unit="%",
                ),
                _employment_row(
                    "Non Farm Payrolls (Oct)",
                    "2025-12-16 13:30:00",
                    actual="-105",
                    estimate="55",
                ),
                _employment_row(
                    "Non Farm Payrolls (Nov)",
                    "2025-12-16 13:30:00",
                    actual="64",
                    estimate="50",
                ),
                _employment_row(
                    "Unemployment Rate (Nov)",
                    "2025-12-16 13:30:00",
                    actual="4.6",
                    estimate="4.4",
                    unit="%",
                ),
                _employment_row(
                    "Non Farm Payrolls (Dec)",
                    "2026-01-09 13:30:00",
                    actual="50",
                    estimate="60",
                ),
                _employment_row(
                    "Unemployment Rate (Dec)",
                    "2026-01-09 13:30:00",
                    actual="4.4",
                    estimate="4.5",
                    unit="%",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
            start_date="2025-10-25",
            end_date="2026-01-22",
        )

        keys = {
            (event.kind, event.event_at, event.reference_period)
            for event in capture.events
        }
        self.assertEqual(len(capture.events), 7)
        self.assertIn(
            (
                NONFARM_PAYROLLS_KIND,
                "2025-12-16T13:30:00Z",
                "2025-10",
            ),
            keys,
        )
        self.assertIn(
            (
                NONFARM_PAYROLLS_KIND,
                "2025-12-16T13:30:00Z",
                "2025-11",
            ),
            keys,
        )
        self.assertIn(
            (
                UNEMPLOYMENT_RATE_KIND,
                "2025-12-16T13:30:00Z",
                "2025-11",
            ),
            keys,
        )
        self.assertIn(
            (
                NONFARM_PAYROLLS_KIND,
                "2025-11-20T13:30:00Z",
                "2025-09",
            ),
            keys,
        )
        self.assertIn(
            (
                NONFARM_PAYROLLS_KIND,
                "2026-01-09T13:30:00Z",
                "2025-12",
            ),
            keys,
        )

class FmpCalendarPublicationAndSurpriseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self._temporary.name)
        self.stores = _stores(self.root)
        self.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        migrate_and_register_store(
            self.stores,
            self.registry,
            StoreRole.MACRO,
            applied_at=_MIGRATION_TIME,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @property
    def _macro_store(self) -> Path:
        return self.stores.macro

    def _publisher(self) -> FmpMacroCalendarPublisher:
        return FmpMacroCalendarPublisher(
            macro_store=self._macro_store,
            project_root=self.root,
            registry=self.registry,
        )

    def _employment_publisher(self) -> FmpEmploymentCalendarPublisher:
        return FmpEmploymentCalendarPublisher(
            macro_store=self._macro_store,
            project_root=self.root,
            registry=self.registry,
        )

    @staticmethod
    def _employment_observation(
        *,
        series_id: str,
        period: str,
        value_text: str,
        source_vintage_identity: str,
        source_release_order: str,
        captured_at: str,
        source_row: int,
    ) -> VintageObservation:
        if series_id == TOTAL_NONFARM_PAYROLLS_SERIES_ID:
            provider_series_code = "CES0000000001"
            unit = "thousands_persons"
        elif series_id == UNEMPLOYMENT_RATE_SERIES_ID:
            provider_series_code = "LNS14000000"
            unit = "percent"
        else:
            raise AssertionError(f"unexpected employment series: {series_id}")
        normalized_value_text = format(Decimal(value_text).normalize(), "f")
        return VintageObservation(
            series_id=series_id,
            provider_series_code=provider_series_code,
            period=period,
            value_text=normalized_value_text,
            unit=unit,
            source_vintage_identity=source_vintage_identity,
            vintage_at=None,
            vintage_precision=None,
            source_release_order=source_release_order,
            available_at=captured_at,
            available_precision="datetime",
            availability_basis="local_capture",
            release_stage=None,
            is_first_release=None,
            first_release_evidence=None,
            source_published_at=None,
            source_published_precision=None,
            source_row=source_row,
        )

    @staticmethod
    def _canonical_live_capture_time(value: str) -> str:
        if value.endswith("Z") and "." not in value:
            return f"{value[:-1]}.000000Z"
        return value

    def _publish_bls_employment(
        self,
        *,
        captured_at: str,
        payroll: dict[str, str],
        unemployment: dict[str, str],
    ) -> str:
        captured_at = self._canonical_live_capture_time(captured_at)
        observations: list[VintageObservation] = []
        source_row = 1
        source_vintage_identity = f"fixture-bls-employment:{captured_at}"
        source_release_order = f"observed:{captured_at}"
        for series_id, values in (
            (TOTAL_NONFARM_PAYROLLS_SERIES_ID, payroll),
            (UNEMPLOYMENT_RATE_SERIES_ID, unemployment),
        ):
            for period, value_text in values.items():
                observations.append(
                    self._employment_observation(
                        series_id=series_id,
                        period=period,
                        value_text=value_text,
                        source_vintage_identity=source_vintage_identity,
                        source_release_order=source_release_order,
                        captured_at=captured_at,
                        source_row=source_row,
                    )
                )
                source_row += 1
        capture = _build_capture(
            provider="bls",
            family="employment",
            source_resource=BLS_CURRENT_RESOURCE,
            media_type="application/json",
            response_bytes=b'{"fixture":"bls-employment"}',
            captured_at=captured_at,
            source_published_at=None,
            source_published_precision=None,
            availability_basis="local_capture",
            observations=observations,
        )
        report = MacroLiveVintagePublisher(
            market_store=self._macro_store,
            project_root=self.root,
            registry=self.registry,
        ).publish(capture)
        self.assertEqual(report.outcome, "published")
        return report.capture_id

    def _publish_rtdsm_employment(
        self,
        *,
        series_id: str,
        source_vintage_identity: str,
        source_release_order: str,
        captured_at: str,
        values: dict[str, str],
    ) -> str:
        captured_at = self._canonical_live_capture_time(captured_at)
        if series_id == TOTAL_NONFARM_PAYROLLS_SERIES_ID:
            source_resource = PHILADELPHIA_FED_PAYROLL_RTDSM_RESOURCE
        elif series_id == UNEMPLOYMENT_RATE_SERIES_ID:
            source_resource = PHILADELPHIA_FED_UNEMPLOYMENT_RTDSM_RESOURCE
        else:
            raise AssertionError(f"unexpected employment series: {series_id}")
        observations = tuple(
            self._employment_observation(
                series_id=series_id,
                period=period,
                value_text=value_text,
                source_vintage_identity=source_vintage_identity,
                source_release_order=source_release_order,
                captured_at=captured_at,
                source_row=source_row,
            )
            for source_row, (period, value_text) in enumerate(values.items(), start=1)
        )
        capture = _build_capture(
            provider="philadelphia_fed",
            family="employment",
            source_resource=source_resource,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            response_bytes=f'{{"fixture":"{source_vintage_identity}"}}'.encode("utf-8"),
            captured_at=captured_at,
            source_published_at=None,
            source_published_precision=None,
            availability_basis="local_capture",
            observations=observations,
        )
        report = MacroLiveVintagePublisher(
            market_store=self._macro_store,
            project_root=self.root,
            registry=self.registry,
        ).publish(capture)
        self.assertEqual(report.outcome, "published")
        return report.capture_id

    def _membership_version(
        self,
        *,
        capture_id: str,
        series_id: str,
        period: str,
    ) -> str:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT version.version_id
                FROM macro_live_vintage_capture_membership AS membership
                JOIN macro_live_vintage_observation_versions AS version
                  ON version.version_id=membership.version_id
                WHERE membership.capture_id=?
                  AND membership.series_id=?
                  AND version.period=?
                ORDER BY version.version_id
                """,
                (capture_id, series_id, period),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        return str(rows[0]["version_id"])

    def _schema_signature(self) -> tuple[tuple[object, ...], ...]:
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            return tuple(
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE type IN ('table', 'index', 'trigger')
                    ORDER BY type, name
                    """
                )
            )

    def _seed_bea_advance(self) -> str:
        """Seed one schema-valid official advance value for repository-only tests."""

        capture_id = "test-bea-advance-capture"
        release_id = "test-bea-advance-release"
        version_id = "test-bea-advance-version"
        source_vintage = "2024Q1-advance"
        available_at = "2024-04-25T12:30:00Z"
        captured_at = "2024-04-25T13:00:00Z"
        response_bytes = b'{"offline":"bea advance"}'
        with StoreWriteLock(self._macro_store):
            connection = sqlite3.connect(self._macro_store, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_captures (
                        capture_id, provider, source_resource, media_type,
                        response_sha256, response_bytes, semantic_identity,
                        captured_at, captured_precision, source_published_at,
                        source_published_precision, availability_basis,
                        normalization_version, observation_count
                    ) VALUES (?, 'bea', 'offline/bea/advance', 'application/json',
                              ?, ?, ?, ?, 'datetime', '2024-04-25', 'date',
                              'source_release', 'macro.live_vintage.v1', 1)
                    """,
                    (
                        capture_id,
                        hashlib.sha256(response_bytes).hexdigest(),
                        response_bytes,
                        hashlib.sha256(b"semantic:bea:advance").hexdigest(),
                        captured_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_series (
                        series_id, provider, provider_series_code, title, frequency,
                        unit, value_representation, availability_basis,
                        created_capture_id
                    ) VALUES (
                        'macro.gdp.real_qoq_saar_pct', 'bea', 'A191RL',
                        'Real gross domestic product, percent change from preceding period',
                        'quarterly', 'percent', 'rate', 'source_release', ?
                    )
                    """,
                    (capture_id,),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_releases (
                        release_id, series_id, source_vintage_identity, vintage_at,
                        vintage_precision, source_release_order, available_at,
                        available_precision, availability_basis, is_first_release,
                        first_release_evidence, release_stage, source_published_at,
                        source_published_precision, capture_id
                    ) VALUES (?, 'macro.gdp.real_qoq_saar_pct', ?, '2024-04-25',
                              'date', '2024-04-25:advance', ?, 'datetime',
                              'source_release', 1, 'offline-bea-advance', 'advance',
                              '2024-04-25', 'date', ?)
                    """,
                    (release_id, source_vintage, available_at, capture_id),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_observation_versions (
                        version_id, series_id, period, release_id,
                        source_vintage_identity, correction_sequence, value_text,
                        value_sha256, unit, available_at, available_precision,
                        captured_at, captured_precision, supersedes_version_id,
                        capture_id, source_row
                    ) VALUES (?, 'macro.gdp.real_qoq_saar_pct', '2024Q1', ?, ?, 1,
                              '1.6', ?, 'percent', ?, 'datetime', ?, 'datetime',
                              NULL, ?, 1)
                    """,
                    (
                        version_id,
                        release_id,
                        source_vintage,
                        hashlib.sha256(b"1.6").hexdigest(),
                        available_at,
                        captured_at,
                        capture_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_capture_membership (
                        capture_id, version_id, series_id, source_row
                    ) VALUES (?, ?, 'macro.gdp.real_qoq_saar_pct', 1)
                    """,
                    (capture_id, version_id),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_gdp_provenance (
                        provenance_id, release_id, capture_id, reference_period,
                        release_stage, source_vintage_identity, source_row
                    ) VALUES ('test-bea-advance-provenance', ?, ?, '2024Q1',
                              'advance', ?, 1)
                    """,
                    (release_id, capture_id, source_vintage),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_observations (
                        series_id, period, current_version_id
                    ) VALUES ('macro.gdp.real_qoq_saar_pct', '2024Q1', ?)
                    """,
                    (version_id,),
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
        return version_id

    def _seed_bea_followup(
        self,
        *,
        suffix: str,
        period: str,
        value: str,
        available_at: str,
        release_stage: str = "advance",
        is_first_release: bool = True,
    ) -> str:
        """Seed an additional schema-valid GDP release after the base series exists."""

        capture_id = f"test-bea-{suffix}-capture"
        release_id = f"test-bea-{suffix}-release"
        version_id = f"test-bea-{suffix}-version"
        source_vintage = f"{period}-{suffix}"
        release_date = available_at[:10]
        captured_at = f"{release_date}T13:00:00Z"
        response_bytes = f'{{"offline":"{suffix}"}}'.encode("utf-8")
        with StoreWriteLock(self._macro_store):
            connection = sqlite3.connect(self._macro_store, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_captures (
                        capture_id, provider, source_resource, media_type,
                        response_sha256, response_bytes, semantic_identity,
                        captured_at, captured_precision, source_published_at,
                        source_published_precision, availability_basis,
                        normalization_version, observation_count
                    ) VALUES (?, 'bea', ?, 'application/json', ?, ?, ?, ?,
                              'datetime', ?, 'date', 'source_release',
                              'macro.live_vintage.v1', 1)
                    """,
                    (
                        capture_id,
                        f"offline/bea/{suffix}",
                        hashlib.sha256(response_bytes).hexdigest(),
                        response_bytes,
                        hashlib.sha256(f"semantic:{suffix}".encode("utf-8")).hexdigest(),
                        captured_at,
                        release_date,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_releases (
                        release_id, series_id, source_vintage_identity, vintage_at,
                        vintage_precision, source_release_order, available_at,
                        available_precision, availability_basis, is_first_release,
                        first_release_evidence, release_stage, source_published_at,
                        source_published_precision, capture_id
                    ) VALUES (?, 'macro.gdp.real_qoq_saar_pct', ?, ?, 'date', ?,
                              ?, 'datetime', 'source_release', ?, ?, ?, ?, 'date', ?)
                    """,
                    (
                        release_id,
                        source_vintage,
                        release_date,
                        f"{release_date}:{suffix}",
                        available_at,
                        int(is_first_release),
                        f"offline-bea-{suffix}" if is_first_release else None,
                        release_stage,
                        release_date,
                        capture_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_observation_versions (
                        version_id, series_id, period, release_id,
                        source_vintage_identity, correction_sequence, value_text,
                        value_sha256, unit, available_at, available_precision,
                        captured_at, captured_precision, supersedes_version_id,
                        capture_id, source_row
                    ) VALUES (?, 'macro.gdp.real_qoq_saar_pct', ?, ?, ?, 1, ?, ?,
                              'percent', ?, 'datetime', ?, 'datetime', NULL, ?, 1)
                    """,
                    (
                        version_id,
                        period,
                        release_id,
                        source_vintage,
                        value,
                        hashlib.sha256(value.encode("utf-8")).hexdigest(),
                        available_at,
                        captured_at,
                        capture_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_capture_membership (
                        capture_id, version_id, series_id, source_row
                    ) VALUES (?, ?, 'macro.gdp.real_qoq_saar_pct', 1)
                    """,
                    (capture_id, version_id),
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_gdp_provenance (
                        provenance_id, release_id, capture_id, reference_period,
                        release_stage, source_vintage_identity, source_row
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        f"test-bea-{suffix}-provenance",
                        release_id,
                        capture_id,
                        period,
                        release_stage,
                        source_vintage,
                    ),
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
        return version_id

    def test_gdp_best_available_prefers_first_then_second_then_third(self) -> None:
        capture = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "GDP Growth Rate QoQ (Q3)",
                    "2025-12-23 13:30:00",
                    actual="9.9",
                    estimate="3.3",
                ),
                _row(
                    "GDP Growth Rate QoQ (Q4)",
                    "2019-03-28 12:30:00",
                    actual="9.8",
                    estimate="2.4",
                ),
                _row(
                    "GDP Growth Rate QoQ (Q4)",
                    "2021-01-28 13:30:00",
                    actual="9.7",
                    estimate=None,
                ),
                _row(
                    "GDP Growth Rate QoQ (Q4)",
                    "2021-02-25 13:30:00",
                    actual="9.6",
                    estimate="4.2",
                ),
                _row(
                    "GDP Growth Rate QoQ (Q4)",
                    "2021-03-25 12:30:00",
                    actual="9.5",
                    estimate="4.1",
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        self.assertEqual(self._publisher().publish(capture).written_events, 5)
        self._seed_bea_advance()
        initial_version_id = self._seed_bea_followup(
            suffix="2025-q3-initial",
            period="2025Q3",
            value="4.4",
            available_at="2025-12-23T13:30:00Z",
            release_stage="initial",
        )
        third_2018_version_id = self._seed_bea_followup(
            suffix="2018-q4-third",
            period="2018Q4",
            value="2.2",
            available_at="2019-03-28T12:30:00Z",
            release_stage="third",
            is_first_release=False,
        )
        self._seed_bea_followup(
            suffix="2020-q4-advance",
            period="2020Q4",
            value="4.0",
            available_at="2021-01-28T13:30:00Z",
        )
        second_2020_version_id = self._seed_bea_followup(
            suffix="2020-q4-second",
            period="2020Q4",
            value="4.1",
            available_at="2021-02-25T13:30:00Z",
            release_stage="second",
            is_first_release=False,
        )
        self._seed_bea_followup(
            suffix="2020-q4-third",
            period="2020Q4",
            value="4.3",
            available_at="2021-03-25T12:30:00Z",
            release_stage="third",
            is_first_release=False,
        )

        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        by_period = {
            item.reference_period: item
            for item in repository.query(kinds=(GDP_ADVANCE_KIND,))
        }
        self.assertEqual(set(by_period), {"2018Q4", "2020Q4", "2025Q3"})

        initial = by_period["2025Q3"]
        self.assertEqual(initial.event_at[:10], "2025-12-23")
        self.assertEqual(initial.release_stage, "initial")
        self.assertFalse(initial.is_fallback)
        self.assertEqual(initial.consensus_mapping_basis, "exact_bea_initial_date")
        self.assertEqual(initial.official_version_id, initial_version_id)
        self.assertEqual(initial.surprise, Decimal("1.1"))

        third = by_period["2018Q4"]
        self.assertEqual(third.event_at[:10], "2019-03-28")
        self.assertEqual(third.release_stage, "third")
        self.assertTrue(third.is_fallback)
        self.assertEqual(
            third.consensus_mapping_basis,
            "exact_bea_third_release_date",
        )
        self.assertEqual(third.official_version_id, third_2018_version_id)
        self.assertEqual(third.surprise, Decimal("-0.2"))

        earlier_alias = replace(
            third,
            event_id="earlier-third-alias",
            event_version_id="earlier-third-alias-version",
            event_at="2019-03-28T06:30:00Z",
        )
        self.assertEqual(
            repository._best_gdp_release((earlier_alias, third)),
            third,
        )
        conflicting_alias = replace(
            earlier_alias,
            consensus=Decimal("2.5"),
            surprise=Decimal("-0.3"),
        )
        self.assertIsNone(
            repository._best_gdp_release((conflicting_alias, third))
        )

        second = by_period["2020Q4"]
        self.assertEqual(second.event_at[:10], "2021-02-25")
        self.assertEqual(second.release_stage, "second")
        self.assertTrue(second.is_fallback)
        self.assertEqual(
            second.consensus_mapping_basis,
            "exact_bea_second_release_date",
        )
        self.assertEqual(second.official_version_id, second_2020_version_id)
        self.assertEqual(second.surprise, Decimal("-0.1"))
        self.assertEqual(
            repository.query(
                start_date="2021-03-01",
                end_date="2021-03-31",
                kinds=(GDP_ADVANCE_KIND,),
            ),
            (),
        )

    def test_existing_calendar_replay_correction_and_on_demand_surprises(self) -> None:
        first = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2019-05-30 12:30:00",
                    actual="9.1",
                    estimate="2.0",
                ),
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="9.9",
                    estimate="2.0",
                ),
                _row(
                    "Gross Domestic Product QoQ (Q2)",
                    "2024-07-25 12:30:00",
                    actual="7.7",
                    estimate="3.0",
                ),
                _row(
                    "Inflation Rate MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.3",
                    estimate="0.2",
                ),
                _row(
                    "Core CPI YoY (Apr)",
                    "2024-05-15 12:30:00",
                    actual="3.6",
                    estimate=None,
                ),
            ),
            captured_at="2026-08-17T12:00:00Z",
        )
        publisher = self._publisher()
        published = publisher.publish(first)
        self.assertEqual(published.outcome, "published")
        self.assertEqual((published.written_events, published.written_versions), (5, 5))

        replay = publisher.publish(first)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual((replay.written_events, replay.written_versions), (0, 0))

        corrected = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2019-05-30 12:30:00",
                    actual="9.1",
                    estimate="2.0",
                ),
                _row(
                    "GDP Growth Rate QoQ (Q1)",
                    "2024-04-25 12:30:00",
                    actual="9.9",
                    estimate="2.0",
                ),
                _row(
                    "Gross Domestic Product QoQ (Q2)",
                    "2024-07-25 12:30:00",
                    actual="7.7",
                    estimate="3.0",
                ),
                _row(
                    "CPI MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.4",
                    estimate="0.2",
                ),
                _row(
                    "Core Inflation Rate YoY (Apr)",
                    "2024-05-15 12:30:00",
                    actual="3.6",
                    estimate=None,
                ),
            ),
            captured_at="2026-08-17T12:05:00Z",
        )
        correction = publisher.publish(corrected)
        self.assertEqual(correction.outcome, "published")
        self.assertEqual((correction.written_events, correction.written_versions), (0, 1))
        self.assertEqual(
            publisher.completed_windows(),
            (("2000-01-01", "2030-12-31"),),
        )

        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM economic_calendar").fetchone()[0],
                5,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM economic_calendar_event_versions"
                ).fetchone()[0],
                6,
            )
            self.assertEqual(
                {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM economic_calendar WHERE provider='fmp'"
                    )
                },
                {GDP_ADVANCE_KIND, CPI_HEADLINE_MOM_KIND, CPI_CORE_YOY_KIND},
            )

        exact_version_id = self._seed_bea_advance()
        advance_version_id = self._seed_bea_followup(
            suffix="2019-q1-advance",
            period="2019Q1",
            value="1.1",
            available_at="2019-04-26T12:30:00Z",
        )
        fallback_version_id = self._seed_bea_followup(
            suffix="2019-q1-second",
            period="2019Q1",
            value="8.8",
            available_at="2019-05-30T12:30:00Z",
            release_stage="second",
            is_first_release=False,
        )
        self._seed_bea_followup(
            suffix="2024-q2-advance-nonexact",
            period="2024Q2",
            value="2.8",
            available_at="2024-07-26T12:30:00Z",
        )
        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        surprises = repository.query(
            kinds=(GDP_ADVANCE_KIND, CPI_HEADLINE_MOM_KIND, CPI_CORE_YOY_KIND)
        )
        by_key = {(item.kind, item.event_at[:10]): item for item in surprises}

        gdp_advance = by_key[(GDP_ADVANCE_KIND, "2024-04-25")]
        self.assertEqual(gdp_advance.fmp_actual, Decimal("9.9"))
        self.assertEqual(gdp_advance.official_actual, Decimal("1.6"))
        self.assertEqual(gdp_advance.surprise, Decimal("-0.4"))
        self.assertEqual(gdp_advance.actual_source, "bea_gdp_vintage")
        self.assertEqual(gdp_advance.official_version_id, exact_version_id)
        self.assertEqual(gdp_advance.reference_period, "2024Q1")
        self.assertEqual(gdp_advance.consensus_mapping_basis, "exact_bea_advance_date")
        self.assertEqual(gdp_advance.status, "ok")
        self.assertEqual(gdp_advance.release_stage, "advance")
        self.assertFalse(gdp_advance.is_fallback)

        gdp_fallback = by_key[(GDP_ADVANCE_KIND, "2019-05-30")]
        self.assertEqual(gdp_fallback.fmp_actual, Decimal("9.1"))
        self.assertEqual(gdp_fallback.official_actual, Decimal("8.8"))
        self.assertEqual(gdp_fallback.surprise, Decimal("6.8"))
        self.assertEqual(gdp_fallback.actual_source, "bea_gdp_vintage")
        self.assertEqual(gdp_fallback.official_version_id, fallback_version_id)
        self.assertNotEqual(gdp_fallback.official_version_id, advance_version_id)
        self.assertEqual(gdp_fallback.reference_period, "2019Q1")
        self.assertEqual(
            gdp_fallback.consensus_mapping_basis,
            "exact_bea_second_release_date",
        )
        self.assertEqual(gdp_fallback.release_stage, "second")
        self.assertTrue(gdp_fallback.is_fallback)
        self.assertEqual(gdp_fallback.status, "ok")
        self.assertNotIn((GDP_ADVANCE_KIND, "2024-07-25"), by_key)

        cpi = by_key[(CPI_HEADLINE_MOM_KIND, "2024-05-15")]
        self.assertEqual(cpi.fmp_actual, Decimal("0.4"))
        self.assertEqual(cpi.official_actual, Decimal("0.4"))
        self.assertEqual(cpi.surprise, Decimal("0.2"))
        self.assertEqual(cpi.actual_source, "fmp_calendar")
        self.assertIsNone(cpi.official_version_id)
        self.assertEqual(cpi.reference_period, "2024-04")
        self.assertEqual(
            cpi.consensus_mapping_basis,
            "same_fmp_event",
        )
        self.assertEqual(cpi.status, "ok")
        self.assertEqual(cpi.availability_assumption, EVENT_DATE_AVAILABILITY_ASSUMPTION)

        missing_consensus = by_key[(CPI_CORE_YOY_KIND, "2024-05-15")]
        self.assertEqual(missing_consensus.fmp_actual, Decimal("3.6"))
        self.assertEqual(missing_consensus.official_actual, Decimal("3.6"))
        self.assertIsNone(missing_consensus.official_version_id)
        self.assertEqual(
            missing_consensus.actual_source,
            "fmp_calendar",
        )
        self.assertIsNone(missing_consensus.consensus)
        self.assertIsNone(missing_consensus.surprise)
        self.assertEqual(missing_consensus.status, "missing_consensus")

        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(tuple(connection.execute("PRAGMA foreign_key_check")), ())
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


    def test_cpi_surprises_use_same_fmp_event_for_all_kinds(self) -> None:
        calendar = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Inflation Rate MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.9",
                    estimate="0.2",
                ),
                _row(
                    "Inflation Rate YoY (Apr)",
                    "2024-05-15 12:30:00",
                    actual=None,
                    estimate="3.4",
                ),
                _row(
                    "Core Inflation Rate MoM (Apr)",
                    "2024-05-15 12:30:00",
                    actual="0.8",
                    estimate="0.3",
                ),
                _row(
                    "Core Inflation Rate YoY (Apr)",
                    "2024-05-15 12:30:00",
                    actual="3.6",
                    estimate="3.5",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        self.assertEqual(self._publisher().publish(calendar).outcome, "published")
        by_kind = {
            item.kind: item
            for item in MacroReleaseSurpriseRepository(
                macro_store=self._macro_store,
                registry=self.registry,
            ).query(kinds=(
                CPI_HEADLINE_MOM_KIND,
                CPI_HEADLINE_YOY_KIND,
                CPI_CORE_MOM_KIND,
                CPI_CORE_YOY_KIND,
            ))
        }
        expected = {
            CPI_HEADLINE_MOM_KIND: (Decimal("0.9"), Decimal("0.2"), "ok"),
            CPI_HEADLINE_YOY_KIND: (None, Decimal("3.4"), "missing_actual"),
            CPI_CORE_MOM_KIND: (Decimal("0.8"), Decimal("0.3"), "ok"),
            CPI_CORE_YOY_KIND: (Decimal("3.6"), Decimal("3.5"), "ok"),
        }
        self.assertEqual(set(by_kind), set(expected))
        for kind, (actual, consensus, status) in expected.items():
            with self.subTest(kind=kind):
                item = by_kind[kind]
                self.assertEqual(item.fmp_actual, actual)
                self.assertEqual(item.official_actual, actual)
                self.assertEqual(item.consensus, consensus)
                expected_surprise = (
                    None if actual is None else actual - consensus
                )
                self.assertEqual(item.surprise, expected_surprise)
                self.assertEqual(item.actual_source, "fmp_calendar")
                self.assertEqual(item.reference_period, "2024-04")
                self.assertEqual(
                    item.consensus_mapping_basis,
                    "same_fmp_event",
                )
                self.assertEqual(item.status, status)
                self.assertIsNone(item.official_version_id)
                self.assertEqual(item.coalesced_event_version_ids, ())

    def test_cpi_fmp_values_do_not_require_external_actual_source(self) -> None:
        calendar = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "Inflation Rate MoM (Nov)",
                    "2025-12-10 13:30:00",
                    actual="0.7",
                    estimate="0.2",
                ),
                _row(
                    "Inflation Rate MoM (Oct)",
                    "2025-11-13 13:30:00",
                    actual=None,
                    estimate="0.2",
                ),
                _row(
                    "Inflation Rate YoY (Oct)",
                    "2025-11-13 13:30:00",
                    actual=None,
                    estimate="3.0",
                ),
                _row(
                    "Core Inflation Rate MoM (Oct)",
                    "2025-11-13 13:30:00",
                    actual=None,
                    estimate="0.3",
                ),
                _row(
                    "Core Inflation Rate YoY (Oct)",
                    "2025-11-13 13:30:00",
                    actual=None,
                    estimate="3.1",
                ),
                _row(
                    "Inflation Rate MoM (May)",
                    "2024-06-12 12:30:00",
                    actual="0.9",
                    estimate="0.2",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        self.assertEqual(self._publisher().publish(calendar).outcome, "published")
        by_key = {
            (item.kind, item.event_at[:10]): item
            for item in MacroReleaseSurpriseRepository(
                macro_store=self._macro_store,
                registry=self.registry,
            ).query(kinds=(
                CPI_HEADLINE_MOM_KIND,
                CPI_HEADLINE_YOY_KIND,
                CPI_CORE_MOM_KIND,
                CPI_CORE_YOY_KIND,
            ))
        }

        unavailable = by_key[(CPI_HEADLINE_MOM_KIND, "2025-12-10")]
        self.assertEqual(unavailable.fmp_actual, Decimal("0.7"))
        self.assertEqual(unavailable.consensus, Decimal("0.2"))
        self.assertEqual(unavailable.official_actual, Decimal("0.7"))
        self.assertEqual(unavailable.surprise, Decimal("0.5"))
        self.assertIsNone(unavailable.official_version_id)
        self.assertEqual(unavailable.actual_source, "fmp_calendar")
        self.assertEqual(unavailable.reference_period, "2025-11")
        self.assertEqual(
            unavailable.consensus_mapping_basis,
            "same_fmp_event",
        )
        self.assertEqual(unavailable.status, "ok")

        for kind in (
            CPI_HEADLINE_MOM_KIND,
            CPI_HEADLINE_YOY_KIND,
            CPI_CORE_MOM_KIND,
            CPI_CORE_YOY_KIND,
        ):
            with self.subTest(marker_kind=kind):
                marker = by_key[(kind, "2025-11-13")]
                self.assertIsNone(marker.fmp_actual)
                self.assertIsNotNone(marker.consensus)
                self.assertIsNone(marker.official_actual)
                self.assertIsNone(marker.official_version_id)
                self.assertIsNone(marker.surprise)
                self.assertEqual(marker.actual_source, "fmp_calendar")
                self.assertEqual(marker.reference_period, "2025-10")
                self.assertEqual(
                    marker.consensus_mapping_basis,
                    "same_fmp_event",
                )
                self.assertEqual(marker.status, "missing_actual")

        unmatched = by_key[(CPI_HEADLINE_MOM_KIND, "2024-06-12")]
        self.assertEqual(unmatched.fmp_actual, Decimal("0.9"))
        self.assertEqual(unmatched.consensus, Decimal("0.2"))
        self.assertEqual(unmatched.official_actual, Decimal("0.9"))
        self.assertIsNone(unmatched.official_version_id)
        self.assertEqual(unmatched.surprise, Decimal("0.7"))
        self.assertEqual(unmatched.actual_source, "fmp_calendar")
        self.assertEqual(unmatched.reference_period, "2024-05")
        self.assertEqual(
            unmatched.consensus_mapping_basis,
            "same_fmp_event",
        )
        self.assertEqual(unmatched.status, "ok")

    def test_cpi_complementary_same_day_fields_coalesce_with_lineage(self) -> None:
        calendar = parse_fmp_us_gdp_cpi_calendar(
            _body(
                _row(
                    "CPI MoM (Nov)",
                    "2025-12-18 10:00:00",
                    actual="0.1",
                    estimate=None,
                ),
                _row(
                    "Inflation Rate MoM (Nov)",
                    "2025-12-18 13:30:00",
                    actual=None,
                    estimate="0.3",
                ),
                _row(
                    "Core Inflation Rate MoM (Nov)",
                    "2025-12-18 13:30:00",
                    actual=None,
                    estimate="0.3",
                ),
                _row(
                    "Inflation Rate MoM (Jun)",
                    "2024-07-11 08:00:00",
                    actual="0.1",
                    estimate=None,
                ),
                _row(
                    "Consumer Price Index MoM (Jun)",
                    "2024-07-11 09:00:00",
                    actual="0.2",
                    estimate=None,
                ),
                _row(
                    "Inflation Rate MoM (Jun)",
                    "2024-07-11 10:00:00",
                    actual=None,
                    estimate="0.3",
                ),
            ),
            captured_at="2026-08-18T12:00:00Z",
        )
        self.assertEqual(self._publisher().publish(calendar).outcome, "published")
        rows = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        ).query(kinds=(CPI_HEADLINE_MOM_KIND, CPI_CORE_MOM_KIND))

        headline = [
            item for item in rows if item.kind == CPI_HEADLINE_MOM_KIND
        ]
        self.assertEqual(len(headline), 1)
        coalesced = headline[0]
        self.assertEqual(coalesced.reference_period, "2025-11")
        self.assertEqual(coalesced.event_at, "2025-12-18T13:30:00Z")
        self.assertEqual(coalesced.fmp_actual, Decimal("0.1"))
        self.assertEqual(coalesced.official_actual, Decimal("0.1"))
        self.assertEqual(coalesced.consensus, Decimal("0.3"))
        self.assertEqual(coalesced.surprise, Decimal("-0.2"))
        self.assertEqual(coalesced.status, "ok")
        self.assertEqual(
            coalesced.consensus_mapping_basis,
            "same_day_fmp_cpi_complementary_fields",
        )
        self.assertEqual(len(coalesced.coalesced_event_version_ids), 1)

        with read_connection(self.stores, StoreRole.MACRO) as connection:
            source_versions = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT version.event_version_id
                    FROM economic_calendar AS event
                    JOIN economic_calendar_event_versions AS version
                      ON version.event_id=event.event_id
                    WHERE event.name=?
                      AND substr(event.event_at, 1, 10)='2025-12-18'
                    ORDER BY version.event_version_id
                    """,
                    (CPI_HEADLINE_MOM_KIND,),
                )
            }
        self.assertEqual(
            {coalesced.event_version_id, *coalesced.coalesced_event_version_ids},
            source_versions,
        )

        core = next(item for item in rows if item.kind == CPI_CORE_MOM_KIND)
        self.assertEqual(core.reference_period, "2025-11")
        self.assertEqual(core.status, "missing_actual")
        self.assertEqual(core.consensus_mapping_basis, "same_fmp_event")
        self.assertEqual(core.coalesced_event_version_ids, ())
        same_side = replace(
            core,
            event_id="same-side-core-alias",
            event_version_id="same-side-core-alias-version",
        )
        self.assertEqual(
            MacroReleaseSurpriseRepository._coalesce_cpi_candidates(
                (core, same_side)
            ),
            (core, same_side),
        )

    def test_employment_calendar_publish_replay_correction_and_schema_stability(
        self,
    ) -> None:
        self.assertTrue(
            any(
                str(item["id"]) == EMPLOYMENT_CALENDAR_COLLECTOR_ID
                for item in self.registry.collectors
            )
        )
        invalid_registry = replace(
            self.registry,
            datasets=tuple(
                replace(
                    dataset,
                    collector_ids=tuple(
                        collector_id
                        for collector_id in dataset.collector_ids
                        if collector_id != EMPLOYMENT_CALENDAR_COLLECTOR_ID
                    ),
                )
                if dataset.id == "fixture.macro.economic_calendar"
                else dataset
                for dataset in self.registry.datasets
            ),
            collectors=tuple(
                collector
                for collector in self.registry.collectors
                if str(collector["id"]) != EMPLOYMENT_CALENDAR_COLLECTOR_ID
            ),
        )
        with self.assertRaisesRegex(ValidationError, "registry binding"):
            FmpEmploymentCalendarPublisher(
                macro_store=self._macro_store,
                project_root=self.root,
                registry=invalid_registry,
            )

        first = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-06-07 12:30:00",
                    actual="200",
                    estimate="180",
                    previous="170",
                ),
                _employment_row(
                    "Unemployment Rate",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    previous="3.8",
                    unit="%",
                ),
            ),
            captured_at="2024-06-08T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        schema_before = self._schema_signature()
        publisher = self._employment_publisher()
        published = publisher.publish(first)
        self.assertEqual(published.outcome, "published")
        self.assertEqual((published.written_events, published.written_versions), (2, 2))
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            provider_event_ids = {
                str(row["name"]): str(row["provider_event_id"])
                for row in connection.execute(
                    "SELECT name, provider_event_id FROM economic_calendar"
                )
            }
        self.assertRegex(
            provider_event_ids[NONFARM_PAYROLLS_KIND],
            r"^fmp_employment_v2:2024-05:[0-9a-f]{64}$",
        )
        self.assertRegex(
            provider_event_ids[UNEMPLOYMENT_RATE_KIND],
            r"^fmp_unemployment_v3:2024-05:[0-9a-f]{64}$",
        )
        replay = publisher.publish(first)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual((replay.written_events, replay.written_versions), (0, 0))

        corrected = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Non-Farm Payrolls",
                    "2024-06-07 12:30:00",
                    actual="201",
                    estimate="180",
                    previous="170",
                ),
                _employment_row(
                    "Unemployment Rate",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    previous="3.8",
                    unit="%",
                ),
            ),
            captured_at="2024-06-08T12:05:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        correction = publisher.publish(corrected)
        self.assertEqual(correction.outcome, "published")
        self.assertEqual((correction.written_events, correction.written_versions), (0, 1))
        self.assertEqual(publisher.completed_windows(), (("2024-06-01", "2024-06-30"),))
        self.assertEqual(self._schema_signature(), schema_before)

        with read_connection(self.stores, StoreRole.MACRO) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM economic_calendar").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM economic_calendar_event_versions"
                ).fetchone()[0],
                3,
            )
            current_units = {
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT event.name, version.unit
                    FROM economic_calendar AS event
                    JOIN economic_calendar_event_versions AS version
                      ON version.event_id=event.event_id
                    WHERE version.correction_sequence=(
                        SELECT MAX(later.correction_sequence)
                        FROM economic_calendar_event_versions AS later
                        WHERE later.event_id=event.event_id
                    )
                    """
                )
            }
        self.assertEqual(
            current_units,
            {
                (NONFARM_PAYROLLS_KIND, "thousands_persons"),
                (UNEMPLOYMENT_RATE_KIND, "percent"),
            },
        )

    def test_employment_surprises_use_same_fmp_rows(
        self,
    ) -> None:
        calendar = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-06-07 12:30:00",
                    actual="999",
                    estimate="150",
                    previous="170",
                ),
                _employment_row(
                    "Unemployment Rate",
                    "2024-06-07 12:30:00",
                    actual="9.9",
                    estimate="3.9",
                    previous="3.8",
                    unit="%",
                ),
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-07-05 12:30:00",
                    actual="888",
                    estimate=None,
                    previous="999",
                ),
                _employment_row(
                    "Unemployment Rate",
                    "2024-07-05 12:30:00",
                    actual="8.8",
                    estimate="4.0",
                    previous="9.9",
                    unit="%",
                ),
            ),
            captured_at="2024-07-06T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-07-31",
        )
        self.assertEqual(self._employment_publisher().publish(calendar).written_events, 4)

        bls_capture_id = self._publish_bls_employment(
            captured_at="2024-06-07T13:00:00Z",
            payroll={"2024-04": "1000", "2024-05": "1200"},
            unemployment={"2024-04": "3.8", "2024-05": "4.0"},
        )
        prior_payroll_capture_id = self._publish_rtdsm_employment(
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            source_vintage_identity="EMPLOY24M6",
            source_release_order="000001:EMPLOY24M6",
            captured_at="2024-06-30T12:00:00Z",
            values={"2024-04": "1000", "2024-05": "1200"},
        )
        target_payroll_capture_id = self._publish_rtdsm_employment(
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            source_vintage_identity="EMPLOY24M7",
            source_release_order="000002:EMPLOY24M7",
            captured_at="2024-07-31T12:00:00Z",
            values={"2024-06": "1210"},
        )
        self._publish_rtdsm_employment(
            series_id=UNEMPLOYMENT_RATE_SERIES_ID,
            source_vintage_identity="RUC24Q2",
            source_release_order="000001:RUC24Q2",
            captured_at="2024-06-30T12:00:00Z",
            values={"2024-05": "4.0"},
        )
        target_unemployment_capture_id = self._publish_rtdsm_employment(
            series_id=UNEMPLOYMENT_RATE_SERIES_ID,
            source_vintage_identity="RUC24Q3",
            source_release_order="000002:RUC24Q3",
            captured_at="2024-07-31T12:00:00Z",
            values={"2024-06": "4.1"},
        )

        bls_payroll_prior = self._membership_version(
            capture_id=bls_capture_id,
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            period="2024-04",
        )
        bls_payroll_target = self._membership_version(
            capture_id=bls_capture_id,
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            period="2024-05",
        )
        bls_unemployment_target = self._membership_version(
            capture_id=bls_capture_id,
            series_id=UNEMPLOYMENT_RATE_SERIES_ID,
            period="2024-05",
        )
        rtdsm_payroll_prior = self._membership_version(
            capture_id=prior_payroll_capture_id,
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            period="2024-05",
        )
        rtdsm_payroll_target = self._membership_version(
            capture_id=target_payroll_capture_id,
            series_id=TOTAL_NONFARM_PAYROLLS_SERIES_ID,
            period="2024-06",
        )
        rtdsm_unemployment_target = self._membership_version(
            capture_id=target_unemployment_capture_id,
            series_id=UNEMPLOYMENT_RATE_SERIES_ID,
            period="2024-06",
        )

        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        surprises = repository.query(kinds=EMPLOYMENT_KINDS)
        by_key = {(item.kind, item.event_at[:10]): item for item in surprises}

        june_payroll = by_key[(NONFARM_PAYROLLS_KIND, "2024-06-07")]
        self.assertEqual(june_payroll.fmp_actual, Decimal("999"))
        self.assertEqual(june_payroll.official_actual, Decimal("999"))
        self.assertEqual(june_payroll.surprise, Decimal("849"))
        self.assertEqual(june_payroll.actual_source, "fmp_calendar")
        self.assertEqual(
            june_payroll.consensus_mapping_basis,
            "same_fmp_payroll_reference_period",
        )
        self.assertIsNone(june_payroll.official_version_id)
        self.assertIsNone(june_payroll.official_prior_version_id)
        self.assertEqual(june_payroll.reference_period, "2024-05")
        self.assertEqual(june_payroll.status, "ok")

        june_unemployment = by_key[(UNEMPLOYMENT_RATE_KIND, "2024-06-07")]
        self.assertEqual(june_unemployment.fmp_actual, Decimal("9.9"))
        self.assertEqual(june_unemployment.official_actual, Decimal("9.9"))
        self.assertEqual(june_unemployment.surprise, Decimal("6.0"))
        self.assertEqual(june_unemployment.actual_source, "fmp_calendar")
        self.assertEqual(
            june_unemployment.consensus_mapping_basis,
            "same_fmp_unemployment_reference_period",
        )
        self.assertIsNone(june_unemployment.official_version_id)
        self.assertIsNone(june_unemployment.official_prior_version_id)
        self.assertEqual(june_unemployment.reference_period, "2024-05")
        self.assertEqual(june_unemployment.status, "ok")

        july_payroll = by_key[(NONFARM_PAYROLLS_KIND, "2024-07-05")]
        self.assertEqual(july_payroll.fmp_actual, Decimal("888"))
        self.assertEqual(july_payroll.official_actual, Decimal("888"))
        self.assertIsNone(july_payroll.consensus)
        self.assertIsNone(july_payroll.surprise)
        self.assertEqual(july_payroll.status, "missing_consensus")
        self.assertEqual(
            july_payroll.consensus_mapping_basis,
            "same_fmp_payroll_reference_period",
        )
        self.assertEqual(july_payroll.actual_source, "fmp_calendar")
        self.assertEqual(july_payroll.reference_period, "2024-06")
        self.assertIsNone(july_payroll.official_version_id)
        self.assertIsNone(july_payroll.official_prior_version_id)

        july_unemployment = by_key[(UNEMPLOYMENT_RATE_KIND, "2024-07-05")]
        self.assertEqual(july_unemployment.fmp_actual, Decimal("8.8"))
        self.assertEqual(july_unemployment.official_actual, Decimal("8.8"))
        self.assertEqual(july_unemployment.surprise, Decimal("4.8"))
        self.assertEqual(july_unemployment.actual_source, "fmp_calendar")
        self.assertEqual(
            july_unemployment.consensus_mapping_basis,
            "same_fmp_unemployment_reference_period",
        )
        self.assertIsNone(july_unemployment.official_version_id)
        self.assertIsNone(july_unemployment.official_prior_version_id)
        self.assertEqual(july_unemployment.reference_period, "2024-06")
        self.assertEqual(july_unemployment.status, "ok")

    def test_unemployment_delayed_and_current_rows_use_same_fmp_data(
        self,
    ) -> None:
        calendar = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Unemployment Rate (Feb)",
                    "2013-03-08 13:30:00",
                    actual="7.7",
                    estimate=None,
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Sep)",
                    "2013-10-22 12:30:00",
                    actual="7.2",
                    estimate="7.3",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Nov)",
                    "2022-12-02 13:30:00",
                    actual="3.7",
                    estimate="3.7",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Sep)",
                    "2025-11-20 13:30:00",
                    actual="4.4",
                    estimate="4.3",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Oct)",
                    "2025-12-16 13:30:00",
                    actual=None,
                    estimate="4.4",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Nov)",
                    "2025-12-16 13:30:00",
                    actual="4.6",
                    estimate="4.4",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Jan)",
                    "2026-02-11 13:30:00",
                    actual="4.3",
                    estimate="4.4",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (May)",
                    "2026-06-05 12:30:00",
                    actual="4.3",
                    estimate="4.3",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Jun)",
                    "2026-07-02 12:30:00",
                    actual="4.2",
                    estimate="4.3",
                    unit="%",
                ),
                _employment_row(
                    "Unemployment Rate (Jul)",
                    "2026-08-07 12:30:00",
                    actual="4.1",
                    estimate="4.2",
                    unit="%",
                ),
            ),
            captured_at="2026-08-20T12:00:00Z",
            start_date="2013-01-01",
            end_date="2026-08-31",
        )
        self.assertEqual(
            self._employment_publisher().publish(calendar).written_events,
            10,
        )
        self._publish_bls_employment(
            captured_at="2022-12-02T13:00:00Z",
            payroll={"2022-11": "1000"},
            unemployment={"2022-11": "3.6"},
        )

        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        rows = repository.query(kinds=(UNEMPLOYMENT_RATE_KIND,))
        by_period = {row.reference_period: row for row in rows}
        self.assertEqual(
            set(by_period),
            {
                "2013-02",
                "2013-09",
                "2022-11",
                "2025-09",
                "2025-10",
                "2025-11",
                "2026-01",
                "2026-05",
                "2026-06",
                "2026-07",
            },
        )
        expected = {
            "2013-09": ("7.2", "7.3", "-0.1"),
            "2022-11": ("3.7", "3.7", "0.0"),
            "2025-09": ("4.4", "4.3", "0.1"),
            "2025-11": ("4.6", "4.4", "0.2"),
            "2026-01": ("4.3", "4.4", "-0.1"),
            "2026-05": ("4.3", "4.3", "0.0"),
            "2026-06": ("4.2", "4.3", "-0.1"),
            "2026-07": ("4.1", "4.2", "-0.1"),
        }
        for period, (actual, consensus, surprise) in expected.items():
            with self.subTest(period=period):
                row = by_period[period]
                self.assertEqual(row.fmp_actual, Decimal(actual))
                self.assertEqual(row.official_actual, Decimal(actual))
                self.assertEqual(row.consensus, Decimal(consensus))
                self.assertEqual(row.surprise, Decimal(surprise))
                self.assertEqual(row.unit, "percent")
                self.assertEqual(row.actual_source, "fmp_calendar")
                self.assertEqual(
                    row.consensus_mapping_basis,
                    "same_fmp_unemployment_reference_period",
                )
                self.assertEqual(row.status, "ok")
                self.assertIsNone(row.official_version_id)
                self.assertIsNone(row.official_prior_version_id)
                self.assertIsNone(row.release_stage)
                self.assertFalse(row.is_fallback)

        february = by_period["2013-02"]
        self.assertEqual(february.fmp_actual, Decimal("7.7"))
        self.assertEqual(february.official_actual, Decimal("7.7"))
        self.assertIsNone(february.consensus)
        self.assertIsNone(february.surprise)
        self.assertEqual(february.status, "missing_consensus")

        october = by_period["2025-10"]
        self.assertIsNone(october.fmp_actual)
        self.assertIsNone(october.official_actual)
        self.assertEqual(october.consensus, Decimal("4.4"))
        self.assertIsNone(october.surprise)
        self.assertEqual(october.status, "missing_actual")
        self.assertEqual(october.actual_source, "fmp_calendar")
        self.assertEqual(
            october.consensus_mapping_basis,
            "same_fmp_unemployment_reference_period",
        )
        self.assertIsNone(october.official_version_id)
        self.assertIsNone(october.official_prior_version_id)

    def test_unemployment_v3_replay_has_162_unique_reference_periods(
        self,
    ) -> None:
        month_names = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        source_rows: list[dict[str, object]] = []
        first_month_index = 2013 * 12 + 1
        for offset in range(162):
            month_index = first_month_index + offset
            reference_year, zero_based_month = divmod(month_index, 12)
            reference_month = zero_based_month + 1
            release_year = reference_year + int(reference_month == 12)
            release_month = 1 if reference_month == 12 else reference_month + 1
            source_rows.append(
                _employment_row(
                    f"Unemployment Rate ({month_names[zero_based_month]})",
                    f"{release_year}-{release_month:02d}-07 12:30:00",
                    actual="4.0",
                    estimate="4.0",
                    unit="%",
                )
            )
        capture = parse_fmp_us_employment_calendar(
            _body(*source_rows),
            captured_at="2026-08-20T12:00:00Z",
            start_date="2013-01-01",
            end_date="2026-08-31",
        )
        self.assertEqual(len(capture.events), 162)
        publisher = self._employment_publisher()
        first = publisher.publish(capture)
        self.assertEqual((first.written_events, first.written_versions), (162, 162))
        replay = publisher.publish(capture)
        self.assertEqual(replay.outcome, "unchanged")
        self.assertEqual((replay.written_events, replay.written_versions), (0, 0))

        rows = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        ).query(kinds=(UNEMPLOYMENT_RATE_KIND,))
        self.assertEqual(len(rows), 162)
        periods = {row.reference_period for row in rows}
        self.assertEqual(len(periods), 162)
        self.assertEqual(min(periods), "2013-02")
        self.assertEqual(max(periods), "2026-07")
        self.assertTrue(all(row.status == "ok" for row in rows))

    def test_unemployment_legacy_identity_is_ignored_by_surprise_selection(
        self,
    ) -> None:
        capture = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Unemployment Rate (May)",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    unit="%",
                )
            ),
            captured_at="2024-06-08T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        self.assertEqual(self._employment_publisher().publish(capture).written_events, 1)
        with read_connection(self.stores, StoreRole.MACRO) as connection:
            lineage = connection.execute(
                """
                SELECT event.created_run_id, version.artifact_id,
                       version.source_snapshot_id, version.run_id
                FROM economic_calendar AS event
                JOIN economic_calendar_event_versions AS version
                  ON version.event_id=event.event_id
                WHERE event.provider_event_id LIKE 'fmp_unemployment_v3:%'
                """
            ).fetchone()
        self.assertIsNotNone(lineage)
        assert lineage is not None
        legacy_event_at = "2024-06-08T12:30:00Z"
        legacy_provider_event_id = stable_id(
            "fmp_employment_calendar_provider_event",
            UNEMPLOYMENT_RATE_KIND,
            legacy_event_at,
        )
        legacy_event_id = stable_id(
            "calendar_event",
            "fmp",
            legacy_provider_event_id,
        )
        legacy_version_id = stable_id(
            "calendar_event_version",
            legacy_event_id,
            "1",
            "legacy",
        )
        with StoreWriteLock(self._macro_store):
            connection = sqlite3.connect(self._macro_store, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO economic_calendar (
                        event_id, provider, provider_event_id, event_at,
                        event_precision, country, name, series_id, created_run_id
                    ) VALUES (?, 'fmp', ?, ?, 'datetime', 'US', ?, NULL, ?)
                    """,
                    (
                        legacy_event_id,
                        legacy_provider_event_id,
                        legacy_event_at,
                        UNEMPLOYMENT_RATE_KIND,
                        str(lineage["created_run_id"]),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO economic_calendar_event_versions (
                        event_version_id, event_id, actual_value, consensus_value,
                        previous_value, unit, available_at, available_precision,
                        captured_at, captured_precision, correction_sequence,
                        supersedes_event_version_id, artifact_id, source_snapshot_id,
                        run_id, source_row, state
                    ) VALUES (?, ?, '9.9', '3.9', '3.8', 'percent', ?,
                              'datetime', ?, 'datetime', 1, NULL, ?, ?, ?, 1,
                              'active')
                    """,
                    (
                        legacy_version_id,
                        legacy_event_id,
                        legacy_event_at,
                        legacy_event_at,
                        str(lineage["artifact_id"]),
                        str(lineage["source_snapshot_id"]),
                        str(lineage["run_id"]),
                    ),
                )
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()

        rows = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        ).query(kinds=(UNEMPLOYMENT_RATE_KIND,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].reference_period, "2024-05")
        self.assertEqual(rows[0].official_actual, Decimal("4.0"))

    def test_unemployment_duplicate_v3_period_fails_closed(self) -> None:
        publisher = self._employment_publisher()
        first = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Unemployment Rate (May)",
                    "2024-06-07 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    unit="%",
                )
            ),
            captured_at="2024-06-08T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        second = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Unemployment Rate (May)",
                    "2024-06-08 12:30:00",
                    actual="4.1",
                    estimate="3.9",
                    unit="%",
                )
            ),
            captured_at="2024-06-09T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        self.assertEqual(publisher.publish(first).outcome, "published")
        self.assertEqual(publisher.publish(second).outcome, "published")
        rows = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        ).query(kinds=(UNEMPLOYMENT_RATE_KIND,))
        self.assertEqual(rows, ())

    def test_payroll_delayed_reference_months_use_same_fmp_row(self) -> None:
        publisher = self._employment_publisher()
        fixtures = (
            (
                _body(
                    _employment_row(
                        "Non Farm Payrolls (Feb)",
                        "2013-03-08 13:30:00",
                        actual="236",
                        estimate=None,
                    )
                ),
                "2013-01-01",
                "2013-03-31",
            ),
            (
                _body(
                    _employment_row(
                        "Non Farm Payrolls (Sep)",
                        "2013-10-22 12:30:00",
                        actual="148",
                        estimate="180",
                    )
                ),
                "2013-10-01",
                "2013-12-29",
            ),
            (
                _body(
                    _employment_row(
                        "Non Farm Payrolls (Sep)",
                        "2025-11-20 13:30:00",
                        actual="119",
                        estimate="50",
                    ),
                    _employment_row(
                        "Non Farm Payrolls (Oct)",
                        "2025-12-16 13:30:00",
                        actual="-105",
                        estimate="55",
                    ),
                    _employment_row(
                        "Non Farm Payrolls (Nov)",
                        "2025-12-16 13:30:00",
                        actual="64",
                        estimate="50",
                    ),
                ),
                "2025-11-01",
                "2026-01-22",
            ),
            (
                _body(
                    _employment_row(
                        "Non Farm Payrolls (Jan)",
                        "2026-02-11 13:30:00",
                        actual="130",
                        estimate="150",
                    )
                ),
                "2026-01-23",
                "2026-04-22",
            ),
            (
                _body(
                    _employment_row(
                        "Non Farm Payrolls (Jul)",
                        "2026-08-07 12:30:00",
                        actual="73",
                        estimate="80",
                    )
                ),
                "2026-06-25",
                "2026-09-22",
            ),
        )
        for body, start_date, end_date in fixtures:
            capture = parse_fmp_us_employment_calendar(
                body,
                captured_at="2026-08-18T23:24:45Z",
                start_date=start_date,
                end_date=end_date,
            )
            self.assertEqual(publisher.publish(capture).outcome, "published")

        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        rows = repository.query(kinds=(NONFARM_PAYROLLS_KIND,))
        by_period = {row.reference_period: row for row in rows}
        self.assertEqual(
            set(by_period),
            {
                "2013-02",
                "2013-09",
                "2025-09",
                "2025-10",
                "2025-11",
                "2026-01",
                "2026-07",
            },
        )
        expected = {
            "2013-09": ("148", "180", "-32"),
            "2025-09": ("119", "50", "69"),
            "2025-10": ("-105", "55", "-160"),
            "2025-11": ("64", "50", "14"),
            "2026-01": ("130", "150", "-20"),
            "2026-07": ("73", "80", "-7"),
        }
        for period, (actual, consensus, surprise) in expected.items():
            row = by_period[period]
            self.assertEqual(row.fmp_actual, Decimal(actual))
            self.assertEqual(row.official_actual, Decimal(actual))
            self.assertEqual(row.consensus, Decimal(consensus))
            self.assertEqual(row.surprise, Decimal(surprise))
            self.assertEqual(row.status, "ok")
            self.assertEqual(row.actual_source, "fmp_calendar")
            self.assertEqual(
                row.consensus_mapping_basis,
                "same_fmp_payroll_reference_period",
            )
            self.assertIsNone(row.official_version_id)
            self.assertIsNone(row.official_prior_version_id)

        february = by_period["2013-02"]
        self.assertEqual(february.official_actual, Decimal("236"))
        self.assertIsNone(february.consensus)
        self.assertIsNone(february.surprise)
        self.assertEqual(february.status, "missing_consensus")

    def test_employment_surprises_require_one_early_month_event(self) -> None:
        calendar = parse_fmp_us_employment_calendar(
            _body(
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-06-07 12:30:00",
                    actual="200",
                    estimate="180",
                ),
                _employment_row(
                    "Nonfarm Payrolls",
                    "2024-06-08 12:30:00",
                    actual="200",
                    estimate="180",
                ),
                _employment_row(
                    "Unemployment Rate",
                    "2024-06-15 12:30:00",
                    actual="4.0",
                    estimate="3.9",
                    unit="%",
                ),
            ),
            captured_at="2024-06-16T12:00:00Z",
            start_date="2024-06-01",
            end_date="2024-06-30",
        )
        self._employment_publisher().publish(calendar)
        self._publish_bls_employment(
            captured_at="2024-06-15T13:00:00Z",
            payroll={"2024-04": "1000", "2024-05": "1200"},
            unemployment={"2024-04": "3.8", "2024-05": "4.0"},
        )
        repository = MacroReleaseSurpriseRepository(
            macro_store=self._macro_store,
            registry=self.registry,
        )
        self.assertEqual(repository.query(kinds=EMPLOYMENT_KINDS), ())


if __name__ == "__main__":
    unittest.main()
