from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from quant_data.errors import ValidationError
from quant_data.macro.fmp_release_surprises import (
    CPI_HEADLINE_MOM_KIND,
    EVENT_DATE_AVAILABILITY_ASSUMPTION,
    GDP_ADVANCE_KIND,
    NONFARM_PAYROLLS_KIND,
    UNEMPLOYMENT_RATE_KIND,
    ReleaseSurprise,
)
from quant_data.stores import StoreMap
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.domain_operations import invoke_domain_operation


class MacroReleaseSurpriseToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self._temporary.name)
        paths = {
            role: root / f"{role}.sqlite"
            for role in ("market", "macro", "company", "news")
        }
        for path in paths.values():
            path.write_bytes(b"read-only-tool-fixture")
        self.stores = StoreMap.four_explicit(**paths)
        self.registry = object()
        self._baseline = {path: path.read_bytes() for path in paths.values()}

    def tearDown(self) -> None:
        for path, expected in self._baseline.items():
            self.assertEqual(path.read_bytes(), expected)
        self._temporary.cleanup()

    def _context(self) -> ToolExecutionContext:
        return ToolExecutionContext(
            store_map=self.stores,
            registry_revision="2.21.0",
            registry_sha256="0" * 64,
            request_id="macro-release-surprise-tool-test",
            api_version="1.0",
            tool_name="macro.release_surprises",
            tool_version="1.0.0",
            operation_graph_id="tool_platform.macro.release_surprises",
            operation_version="1.0.0",
            budget=ExecutionBudget(),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="1970-01-01T00:00:00Z",
            ),
        )

    @staticmethod
    def _arguments(
        *,
        identifiers: list[str] | None = None,
        mode: str = "latest",
        as_of: str | None = None,
        start_date: str | None = "2024-01-01",
        end_date: str | None = "2024-12-31",
        limit: int = 100,
    ) -> dict[str, object]:
        return {
            "identifiers": identifiers or [],
            "mode": mode,
            "as_of": as_of,
            "start_date": start_date,
            "end_date": end_date,
            "parameters": [],
            "limit": limit,
        }

    @staticmethod
    def _gdp() -> ReleaseSurprise:
        return ReleaseSurprise(
            event_id="calendar-event-gdp",
            event_version_id="calendar-version-gdp",
            kind=GDP_ADVANCE_KIND,
            event_at="2024-01-25T13:30:00Z",
            reference_period="2023Q4",
            consensus=Decimal("2.1"),
            consensus_mapping_basis="exact_bea_advance_date",
            fmp_actual=Decimal("4.0"),
            official_actual=Decimal("3.3"),
            surprise=Decimal("1.2"),
            unit="%",
            actual_source="bea_gdp_vintage",
            availability_assumption=EVENT_DATE_AVAILABILITY_ASSUMPTION,
            status="ok",
            official_version_id="bea-advance-version",
            release_stage="advance",
            is_fallback=False,
        )

    @staticmethod
    def _cpi() -> ReleaseSurprise:
        return ReleaseSurprise(
            event_id="calendar-event-cpi",
            event_version_id="calendar-version-cpi",
            kind=CPI_HEADLINE_MOM_KIND,
            event_at="2024-02-13T13:30:00Z",
            reference_period="2024-01",
            consensus=Decimal("0.2"),
            consensus_mapping_basis="same_fmp_event",
            fmp_actual=Decimal("0.3"),
            official_actual=Decimal("0.3"),
            surprise=Decimal("0.1"),
            unit="%",
            actual_source="fmp_calendar",
            availability_assumption=EVENT_DATE_AVAILABILITY_ASSUMPTION,
            status="ok",
            official_version_id=None,
        )

    @staticmethod
    def _payroll(
        *,
        consensus_mapping_basis: str = "same_fmp_payroll_reference_period",
    ) -> ReleaseSurprise:
        return ReleaseSurprise(
            event_id="calendar-event-payroll",
            event_version_id="calendar-version-payroll",
            kind=NONFARM_PAYROLLS_KIND,
            event_at="2024-06-07T12:30:00Z",
            reference_period="2024-05",
            consensus=Decimal("150"),
            consensus_mapping_basis=consensus_mapping_basis,
            fmp_actual=Decimal("999"),
            official_actual=Decimal("999"),
            surprise=Decimal("849"),
            unit="thousands_persons",
            actual_source="fmp_calendar",
            availability_assumption=EVENT_DATE_AVAILABILITY_ASSUMPTION,
            status="ok",
            official_version_id=None,
        )

    @staticmethod
    def _unemployment(
        *,
        consensus_mapping_basis: str = "same_fmp_unemployment_reference_period",
    ) -> ReleaseSurprise:
        return ReleaseSurprise(
            event_id="calendar-event-unemployment",
            event_version_id="calendar-version-unemployment",
            kind=UNEMPLOYMENT_RATE_KIND,
            event_at="2024-07-05T12:30:00Z",
            reference_period="2024-06",
            consensus=Decimal("4.0"),
            consensus_mapping_basis=consensus_mapping_basis,
            fmp_actual=Decimal("4.1"),
            official_actual=Decimal("4.1"),
            surprise=Decimal("0.1"),
            unit="percent",
            actual_source="fmp_calendar",
            availability_assumption=EVENT_DATE_AVAILABILITY_ASSUMPTION,
            status="ok",
            official_version_id=None,
        )

    def test_projects_fixed_mapping_filters_and_compact_lineage(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                self._gdp(),
                self._cpi(),
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(
                    identifiers=[GDP_ADVANCE_KIND, CPI_HEADLINE_MOM_KIND],
                    limit=1,
                ),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        repository_type.assert_called_once_with(
            macro_store=self.stores.macro,
            registry=self.registry,
        )
        repository_type.return_value.query.assert_called_once_with(
            start_date="2024-01-01",
            end_date="2024-12-31",
            kinds=(GDP_ADVANCE_KIND, CPI_HEADLINE_MOM_KIND),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.records), 1)
        fields = {field.name: field.value for field in result.records[0].fields}
        self.assertEqual(
            set(fields),
            {
                "actual_source",
                "availability_assumption",
                "consensus",
                "consensus_mapping_basis",
                "event_at",
                "event_id",
                "event_version_id",
                "fmp_actual",
                "kind",
                "official_actual",
                "official_prior_version_id",
                "official_version_id",
                "is_fallback",
                "reference_period",
                "release_stage",
                "status",
                "surprise",
                "unit",
            },
        )
        self.assertEqual(fields["actual_source"], "bea_gdp_vintage")
        self.assertIsNone(fields["official_prior_version_id"])
        self.assertEqual(fields["official_actual"], Decimal("3.3"))
        self.assertEqual(fields["fmp_actual"], Decimal("4.0"))
        self.assertEqual(fields["surprise"], Decimal("1.2"))
        self.assertEqual(fields["reference_period"], "2023Q4")
        self.assertEqual(fields["release_stage"], "advance")
        self.assertFalse(fields["is_fallback"])
        self.assertEqual(
            fields["consensus_mapping_basis"], "exact_bea_advance_date"
        )
        self.assertEqual(
            fields["availability_assumption"],
            EVENT_DATE_AVAILABILITY_ASSUMPTION,
        )
        self.assertEqual(
            tuple(warning.code for warning in result.warnings),
            (
                "historical_consensus_availability_assumption",
                "gdp_actual_source",
            ),
        )
        self.assertEqual(len(result.lineage), 2)
        self.assertEqual(result.lineage[0].dataset_id, "fixture.macro.economic_calendar")
        self.assertEqual(result.lineage[0].semantic_id, "calendar-version-gdp")
        self.assertEqual(result.lineage[0].canonical_version_id, "calendar-version-gdp")
        self.assertEqual(result.lineage[1].dataset_id, "macro.official_vintages")
        self.assertEqual(result.lineage[1].semantic_id, "bea-advance-version")
        self.assertEqual(result.lineage[1].canonical_version_id, "bea-advance-version")
        self.assertTrue(result.truncation.applied)
        self.assertEqual(result.truncation.returned_count, 1)
        self.assertEqual(result.truncation.total_known_count, 2)

    def test_projects_cpi_fmp_fields_with_calendar_lineage(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (self._cpi(),)
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[CPI_HEADLINE_MOM_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = {field.name: field.value for field in result.records[0].fields}
        self.assertEqual(fields["actual_source"], "fmp_calendar")
        self.assertEqual(fields["reference_period"], "2024-01")
        self.assertEqual(
            fields["consensus_mapping_basis"],
            "same_fmp_event",
        )
        self.assertEqual(fields["fmp_actual"], Decimal("0.3"))
        self.assertEqual(fields["official_actual"], Decimal("0.3"))
        self.assertIsNone(fields["official_version_id"])
        self.assertEqual(
            tuple(warning.code for warning in result.warnings),
            (
                "historical_consensus_availability_assumption",
                "cpi_actual_source",
            ),
        )
        self.assertEqual(len(result.lineage), 1)
        self.assertEqual(
            result.lineage[0].dataset_id,
            "fixture.macro.economic_calendar",
        )
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            ("calendar-version-cpi",),
        )

    def test_projects_second_release_gdp_fallback(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                replace(
                    self._gdp(),
                    consensus_mapping_basis="exact_bea_second_release_date",
                    release_stage="second",
                    is_fallback=True,
                ),
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[GDP_ADVANCE_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = {field.name: field.value for field in result.records[0].fields}
        self.assertEqual(
            fields["consensus_mapping_basis"],
            "exact_bea_second_release_date",
        )
        self.assertEqual(fields["reference_period"], "2023Q4")
        self.assertEqual(fields["release_stage"], "second")
        self.assertTrue(fields["is_fallback"])
        self.assertEqual(result.lineage[1].dataset_id, "macro.official_vintages")
        self.assertIn(
            "gdp_later_release_fallback",
            tuple(warning.code for warning in result.warnings),
        )

    def test_rejects_non_latest_modes_without_constructing_repository(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(mode="as_of", as_of="2024-02-01T00:00:00Z"),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        self.assertEqual(result.status, "not_established")
        repository_type.assert_not_called()

    def test_rejects_unknown_identifier_without_constructing_repository(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            with self.assertRaisesRegex(ValidationError, "canonical kinds"):
                invoke_domain_operation(
                    "macro.release_surprises",
                    self._arguments(identifiers=["gdp"]),
                    self._context(),
                    self.registry,  # type: ignore[arg-type]
                )

        repository_type.assert_not_called()

    def test_fails_closed_if_repository_violates_gdp_actual_mapping(self) -> None:
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                replace(self._gdp(), actual_source="fmp_calendar"),
            )
            with self.assertRaisesRegex(ValidationError, "fixed source mapping"):
                invoke_domain_operation(
                    "macro.release_surprises",
                    self._arguments(identifiers=[GDP_ADVANCE_KIND]),
                    self._context(),
                    self.registry,  # type: ignore[arg-type]
                )

    def test_fails_closed_for_invalid_period_basis_or_official_gdp_lineage(self) -> None:
        invalid_rows = (
            replace(self._gdp(), reference_period="2024Q5"),
            replace(self._gdp(), consensus_mapping_basis="same_fmp_event"),
            replace(self._gdp(), release_stage="second"),
            replace(self._gdp(), is_fallback=True),
            replace(self._gdp(), official_version_id=None),
            replace(self._cpi(), reference_period="2024Q1"),
            replace(self._cpi(), consensus_mapping_basis="exact_bea_advance_date"),
            replace(self._cpi(), event_id=""),
            replace(self._cpi(), event_version_id=""),
            replace(self._cpi(), event_version_id=123),
            replace(self._cpi(), official_version_id="unexpected-official-id"),
            replace(
                self._cpi(),
                status="missing_actual",
                fmp_actual=None,
                surprise=None,
            ),
            replace(
                self._cpi(),
                consensus_mapping_basis=(
                    "same_day_fmp_cpi_complementary_fields"
                ),
            ),
            replace(
                self._cpi(),
                coalesced_event_version_ids=("calendar-version-companion",),
            ),
            replace(
                self._cpi(),
                consensus_mapping_basis=(
                    "same_day_fmp_cpi_complementary_fields"
                ),
                coalesced_event_version_ids=("calendar-version-cpi",),
            ),
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            for row in invalid_rows:
                with self.subTest(row=row):
                    repository_type.return_value.query.return_value = (row,)
                    with self.assertRaisesRegex(ValidationError, "fixed source mapping"):
                        invoke_domain_operation(
                            "macro.release_surprises",
                            self._arguments(identifiers=[row.kind]),
                            self._context(),
                            self.registry,  # type: ignore[arg-type]
                        )

    def test_projects_cpi_missing_and_coalesced_calendar_lineage(self) -> None:
        missing_consensus = replace(
            self._cpi(),
            consensus=None,
            status="missing_consensus",
            surprise=None,
        )
        missing_actual = replace(
            self._cpi(),
            event_id="calendar-event-cpi-missing-actual",
            event_version_id="calendar-version-cpi-missing-actual",
            fmp_actual=None,
            official_actual=None,
            status="missing_actual",
            surprise=None,
        )
        coalesced = replace(
            self._cpi(),
            event_id="calendar-event-cpi-coalesced",
            event_version_id="calendar-version-cpi-coalesced-consensus",
            event_at="2025-12-18T13:30:00Z",
            reference_period="2025-11",
            consensus=Decimal("0.3"),
            fmp_actual=Decimal("0.1"),
            official_actual=Decimal("0.1"),
            surprise=Decimal("-0.2"),
            consensus_mapping_basis=(
                "same_day_fmp_cpi_complementary_fields"
            ),
            coalesced_event_version_ids=(
                "calendar-version-cpi-coalesced-actual",
            ),
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                missing_consensus,
                missing_actual,
                coalesced,
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[CPI_HEADLINE_MOM_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = [
            {field.name: field.value for field in record.fields}
            for record in result.records
        ]
        self.assertIsNone(fields[0]["consensus"])
        self.assertIsNone(fields[0]["surprise"])
        self.assertIsNone(fields[1]["official_actual"])
        self.assertIsNone(fields[1]["official_version_id"])
        self.assertEqual(fields[1]["status"], "missing_actual")
        self.assertEqual(fields[2]["official_actual"], Decimal("0.1"))
        self.assertEqual(fields[2]["surprise"], Decimal("-0.2"))
        self.assertEqual(
            fields[2]["consensus_mapping_basis"],
            "same_day_fmp_cpi_complementary_fields",
        )
        self.assertIn(
            "cpi_complementary_fields_coalesced",
            tuple(warning.code for warning in result.warnings),
        )
        self.assertTrue(
            all(
                reference.dataset_id == "fixture.macro.economic_calendar"
                for reference in result.lineage
            )
        )
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            (
                "calendar-version-cpi",
                "calendar-version-cpi-missing-actual",
                "calendar-version-cpi-coalesced-consensus",
                "calendar-version-cpi-coalesced-actual",
            ),
        )

    def test_projects_employment_mapping_lineage_and_warnings(self) -> None:
        payroll = self._payroll()
        unemployment = self._unemployment()
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                payroll,
                unemployment,
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(
                    identifiers=[
                        NONFARM_PAYROLLS_KIND,
                        UNEMPLOYMENT_RATE_KIND,
                    ],
                ),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        self.assertEqual(len(result.records), 2)
        fields_by_kind = {
            fields["kind"]: fields
            for fields in (
                {field.name: field.value for field in record.fields}
                for record in result.records
            )
        }
        payroll_fields = fields_by_kind[NONFARM_PAYROLLS_KIND]
        self.assertIsNone(payroll_fields["official_version_id"])
        self.assertIsNone(payroll_fields["official_prior_version_id"])
        self.assertEqual(payroll_fields["unit"], "thousands_persons")
        self.assertEqual(payroll_fields["reference_period"], "2024-05")
        self.assertEqual(payroll_fields["actual_source"], "fmp_calendar")
        self.assertEqual(payroll_fields["official_actual"], Decimal("999"))
        self.assertEqual(payroll_fields["surprise"], Decimal("849"))

        unemployment_fields = fields_by_kind[UNEMPLOYMENT_RATE_KIND]
        self.assertIsNone(unemployment_fields["official_version_id"])
        self.assertIsNone(unemployment_fields["official_prior_version_id"])
        self.assertEqual(unemployment_fields["unit"], "percent")
        self.assertEqual(unemployment_fields["reference_period"], "2024-06")
        self.assertEqual(unemployment_fields["actual_source"], "fmp_calendar")
        self.assertEqual(
            unemployment_fields["consensus_mapping_basis"],
            "same_fmp_unemployment_reference_period",
        )
        self.assertEqual(unemployment_fields["fmp_actual"], Decimal("4.1"))
        self.assertEqual(unemployment_fields["official_actual"], Decimal("4.1"))
        self.assertEqual(unemployment_fields["surprise"], Decimal("0.1"))

        self.assertEqual(
            tuple(warning.code for warning in result.warnings),
            (
                "historical_consensus_availability_assumption",
                "payroll_actual_source",
                "unemployment_actual_source",
            ),
        )
        self.assertIn(
            "same reference-period FMP calendar event",
            result.warnings[1].message,
        )
        self.assertIn(
            "same reference-period FMP calendar event",
            result.warnings[2].message,
        )
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            (
                "calendar-version-payroll",
                "calendar-version-unemployment",
            ),
        )
        self.assertTrue(
            all(
                reference.dataset_id == "fixture.macro.economic_calendar"
                for reference in result.lineage
            )
        )

    def test_projects_payroll_missing_values_without_official_lineage(
        self,
    ) -> None:
        missing_consensus = replace(
            self._payroll(),
            consensus=None,
            surprise=None,
            status="missing_consensus",
        )
        missing_actual = replace(
            self._payroll(),
            event_id="calendar-event-payroll-missing-actual",
            event_version_id="calendar-version-payroll-missing-actual",
            reference_period="2024-06",
            fmp_actual=None,
            official_actual=None,
            surprise=None,
            status="missing_actual",
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                missing_consensus,
                missing_actual,
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[NONFARM_PAYROLLS_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = tuple(
            {field.name: field.value for field in record.fields}
            for record in result.records
        )
        self.assertEqual(
            tuple(row["status"] for row in fields),
            ("missing_consensus", "missing_actual"),
        )
        self.assertTrue(
            all(row["official_version_id"] is None for row in fields)
        )
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            (
                "calendar-version-payroll",
                "calendar-version-payroll-missing-actual",
            ),
        )
        self.assertEqual(
            tuple(warning.code for warning in result.warnings),
            (
                "historical_consensus_availability_assumption",
                "payroll_actual_source",
            ),
        )

    def test_projects_unemployment_missing_values_without_official_lineage(
        self,
    ) -> None:
        missing_consensus = replace(
            self._unemployment(),
            consensus=None,
            surprise=None,
            status="missing_consensus",
        )
        missing_actual = replace(
            self._unemployment(),
            event_id="calendar-event-unemployment-missing-actual",
            event_version_id="calendar-version-unemployment-missing-actual",
            reference_period="2024-07",
            consensus=Decimal("4.4"),
            fmp_actual=None,
            official_actual=None,
            surprise=None,
            status="missing_actual",
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (
                missing_consensus,
                missing_actual,
            )
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[UNEMPLOYMENT_RATE_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = tuple(
            {field.name: field.value for field in record.fields}
            for record in result.records
        )
        self.assertEqual(
            tuple(row["status"] for row in fields),
            ("missing_consensus", "missing_actual"),
        )
        self.assertTrue(all(row["official_version_id"] is None for row in fields))
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            (
                "calendar-version-unemployment",
                "calendar-version-unemployment-missing-actual",
            ),
        )

    def test_projects_same_timestamp_unemployment_reference_months_separately(
        self,
    ) -> None:
        october = replace(
            self._unemployment(),
            event_id="calendar-event-unemployment-2025-10",
            event_version_id="calendar-version-unemployment-2025-10",
            event_at="2025-12-16T13:30:00Z",
            reference_period="2025-10",
            consensus=Decimal("4.4"),
            fmp_actual=None,
            official_actual=None,
            surprise=None,
            status="missing_actual",
        )
        november = replace(
            self._unemployment(),
            event_id="calendar-event-unemployment-2025-11",
            event_version_id="calendar-version-unemployment-2025-11",
            event_at="2025-12-16T13:30:00Z",
            reference_period="2025-11",
            consensus=Decimal("4.4"),
            fmp_actual=Decimal("4.6"),
            official_actual=Decimal("4.6"),
            surprise=Decimal("0.2"),
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            repository_type.return_value.query.return_value = (october, november)
            result = invoke_domain_operation(
                "macro.release_surprises",
                self._arguments(identifiers=[UNEMPLOYMENT_RATE_KIND]),
                self._context(),
                self.registry,  # type: ignore[arg-type]
            )

        fields = tuple(
            {field.name: field.value for field in record.fields}
            for record in result.records
        )
        self.assertEqual(
            tuple(row["event_at"] for row in fields),
            ("2025-12-16T13:30:00Z", "2025-12-16T13:30:00Z"),
        )
        self.assertEqual(
            tuple(row["reference_period"] for row in fields),
            ("2025-10", "2025-11"),
        )
        self.assertEqual(
            tuple(row["status"] for row in fields),
            ("missing_actual", "ok"),
        )
        self.assertEqual(fields[1]["surprise"], Decimal("0.2"))
        self.assertEqual(
            tuple(reference.semantic_id for reference in result.lineage),
            (
                "calendar-version-unemployment-2025-10",
                "calendar-version-unemployment-2025-11",
            ),
        )

    def test_fails_closed_for_invalid_employment_mapping(self) -> None:
        invalid_rows = (
            replace(self._payroll(), actual_source="bls_employment_vintage"),
            replace(self._payroll(), consensus_mapping_basis="same_fmp_event"),
            replace(self._payroll(), reference_period="2024Q2"),
            replace(
                self._payroll(),
                official_version_id="unexpected-official-version",
            ),
            replace(
                self._payroll(),
                official_prior_version_id="unexpected-prior-version",
            ),
            replace(self._payroll(), unit="percent"),
            replace(self._payroll(), official_actual=Decimal("200")),
            replace(self._payroll(), surprise=Decimal("50")),
            replace(self._payroll(), fmp_actual=None),
            replace(self._unemployment(), actual_source="bls_employment_vintage"),
            replace(
                self._unemployment(), consensus_mapping_basis="same_day_bls_capture"
            ),
            replace(
                self._unemployment(),
                official_version_id="unexpected-official-version",
            ),
            replace(
                self._unemployment(),
                official_prior_version_id="unexpected-prior-version",
            ),
            replace(self._unemployment(), unit="thousands_persons"),
            replace(self._unemployment(), reference_period="2024-13"),
            replace(self._unemployment(), status="corrupt"),
            replace(self._unemployment(), official_actual=Decimal("99")),
            replace(self._unemployment(), surprise=Decimal("123")),
            replace(self._unemployment(), release_stage="third"),
            replace(self._unemployment(), is_fallback=True),
            replace(self._unemployment(), consensus=None),
            replace(self._unemployment(), fmp_actual=Decimal("NaN")),
            replace(self._unemployment(), status="missing_actual"),
            replace(
                self._unemployment(),
                fmp_actual=None,
                official_actual=None,
                consensus=Decimal("NaN"),
                surprise=None,
                status="missing_actual",
            ),
        )
        with patch(
            "quant_data.tool_platform.domain_operations.MacroReleaseSurpriseRepository"
        ) as repository_type:
            for row in invalid_rows:
                with self.subTest(row=row):
                    repository_type.return_value.query.return_value = (row,)
                    with self.assertRaisesRegex(ValidationError, "fixed source mapping"):
                        invoke_domain_operation(
                            "macro.release_surprises",
                            self._arguments(identifiers=[row.kind]),
                            self._context(),
                            self.registry,  # type: ignore[arg-type]
                        )


if __name__ == "__main__":
    unittest.main()
