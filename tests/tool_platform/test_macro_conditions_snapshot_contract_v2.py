from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import unittest
from unittest.mock import patch

from quant_data.macro.canonical_access import (
    MacroSeriesDescriptor,
    MacroSeriesSelection,
)
from quant_data.macro.conditions import (
    CREDIT_COMPONENTS,
    FUNDING_COMPONENTS,
    LIQUIDITY_COMPONENTS,
    NBER_RECESSION_SERIES_ID,
    ConditionFact,
    MacroConditionsRepository,
    RevisionFact,
    StandardizedSurprise,
)
from quant_data.macro.fmp_release_surprises import CPI_HEADLINE_MOM_KIND
from quant_data.macro.live_vintages import GDP_REAL_SERIES_ID
from quant_data.tool_platform.macro_conditions import invoke_macro_conditions


def _record(request: object) -> dict[str, object]:
    series_id = str(getattr(request, "series_id"))
    mode = str(getattr(request, "mode"))
    period = str(getattr(request, "end_date") or "2024-05-01")
    value = "0" if series_id == NBER_RECESSION_SERIES_ID else (
        "1" if mode == "first_release" or period == "2024-01-01" else "3"
    )
    unit = "indicator" if series_id == NBER_RECESSION_SERIES_ID else "percent"
    return {
        "series_id": series_id,
        "period_start": period,
        "period_end": period,
        "value": Decimal(value),
        "missing_reason": None,
        "unit": unit,
        "value_representation": "indicator" if unit == "indicator" else "rate",
        "available_at": "2024-05-02T12:00:00Z",
        "available_precision": "datetime",
        "captured_at": "2024-05-02T12:00:00Z",
        "captured_precision": "datetime",
        "version_id": f"{mode}-{series_id}-{period}",
        "capture_id": f"capture-{series_id}",
        "snapshot_id": f"snapshot-{series_id}",
        "source_vintage_identity": mode,
    }


def _selection(
    request: object,
    warnings: tuple[str, ...] = (),
) -> MacroSeriesSelection:
    row = _record(request)
    series_id = str(row["series_id"])
    mode = str(getattr(request, "mode"))
    descriptor = MacroSeriesDescriptor(
        series_id=series_id,
        provider="test",
        provider_series_code=series_id,
        title=series_id,
        description=None,
        category="test",
        frequency="daily",
        unit=str(row["unit"]),
        value_representation=str(row["value_representation"]),
        scale="1",
        availability_basis="source_evidenced",
        supported_modes=("latest", "as_of", "first_release"),
        storage_model="generic_version_core",
        active=True,
        coverage_start=str(row["period_start"]),
        coverage_end=str(row["period_end"]),
        observation_count=1,
        version_count=1,
        release_count=1,
        first_release_count=1,
    )
    warning_codes = set(warnings)
    if mode == "as_of":
        warning_codes.add("point_in_time_selection_applied")
    return MacroSeriesSelection(
        descriptor=descriptor,
        records=(row,),
        warnings=tuple(sorted(warning_codes)),
        truncated=False,
        total_selected_count=1,
        first_release_candidate_group_count=1 if mode == "first_release" else 0,
        first_release_evidenced_group_count=1 if mode == "first_release" else 0,
        first_release_missing_evidence_group_count=0,
        migration_ids=("macro:0001_test",),
        receipt_sha256="a" * 64,
        dataset_ids=("fixture.macro.rtdsm_employ",),
    )


class _BatchCanonical:
    def __init__(self, warnings: tuple[str, ...] = ()) -> None:
        self.warnings = warnings
        self.batch_calls: list[tuple[object, tuple[object, ...]]] = []

    def _get_series_batch_in_connection(
        self,
        connection: object,
        requests: tuple[object, ...],
    ) -> tuple[MacroSeriesSelection, ...]:
        batch = tuple(requests)
        self.batch_calls.append((connection, batch))
        return tuple(_selection(request, self.warnings) for request in batch)

    def get_series(self, request: object) -> object:
        raise AssertionError("composite selection must use the shared batch")


class _Budget:
    def require(self, **_: object) -> None:
        return None


class _Context:
    budget = _Budget()

    def checkpoint(self) -> None:
        return None


def _arguments(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "mode": "latest",
        "as_of": None,
        "date_only_policy": "completed_date",
        "observation_date": None,
        "limit": 20,
    }
    result.update(overrides)
    return result


def _soma_fact() -> ConditionFact:
    return ConditionFact(
        component="SOMA_total",
        series_id="macro.nyfed.soma.total",
        period_start="2024-05-01",
        period_end="2024-05-01",
        value=Decimal("2"),
        missing_reason=None,
        unit="usd_millions",
        value_representation="amount",
        available_at="2024-05-02T12:00:00Z",
        available_precision="datetime",
        captured_at="2024-05-02T12:00:00Z",
        captured_precision="datetime",
        version_id="soma-version",
        dataset_id="fixture.macro.soma_summary",
        evidence_id="soma-evidence",
        snapshot_id="soma-snapshot",
    )


class _LineageGateway:
    def revision_analysis(self, **_: object) -> tuple[RevisionFact, ...]:
        return (
            RevisionFact(
                series_id=GDP_REAL_SERIES_ID,
                period_start="2024-01-01",
                period_end="2024-03-31",
                unit="percent",
                first_value=Decimal("1"),
                latest_value=Decimal("3"),
                signed_revision=Decimal("2"),
                absolute_revision=Decimal("2"),
                first_version_id="first",
                latest_version_id="latest",
                first_evidence_id="first-evidence",
                latest_evidence_id="latest-evidence",
                first_snapshot_id=None,
                latest_snapshot_id=None,
            ),
        )

    def standardize_surprises(
        self, **_: object
    ) -> tuple[StandardizedSurprise, ...]:
        return (
            StandardizedSurprise(
                event_at="2024-05-01T12:00:00Z",
                event_id="event-1",
                event_version_id="event-version-1",
                kind=CPI_HEADLINE_MOM_KIND,
                release_stage=None,
                reference_period="2024-04",
                unit="percent",
                surprise=Decimal("1"),
                z_score=Decimal("0"),
                sample_count=3,
                sample_mean=Decimal("1"),
                population_standard_deviation=Decimal("1"),
                availability_assumption="event_at_utc",
                official_version_id="official-version-1",
                event_evidence_id="event-artifact-1",
                event_snapshot_id="event-snapshot-1",
                official_evidence_id="official-capture-1",
                coalesced_event_lineage=(
                    (
                        "event-version-2",
                        "event-artifact-2",
                        "event-snapshot-2",
                    ),
                ),
            ),
        )


class MacroConditionSnapshotContractTests(unittest.TestCase):
    def test_composite_routes_use_one_owned_macro_snapshot(self) -> None:
        opened: list[object] = []
        soma_connections: list[object] = []
        connection = object()

        @contextmanager
        def immutable_reader(*_: object, **__: object):
            opened.append(connection)
            yield connection

        canonical = _BatchCanonical()
        repository = MacroConditionsRepository(
            object(), object(), canonical_reader=canonical
        )

        def soma_reader(value: object) -> tuple[ConditionFact, ...]:
            soma_connections.append(value)
            return (_soma_fact(),)

        with patch(
            "quant_data.macro.conditions.quiet_immutable_read_connection",
            immutable_reader,
        ), patch.object(
            repository,
            "_soma_facts_in_connection",
            side_effect=soma_reader,
        ):
            invoke_macro_conditions(
                "rates.get_funding_conditions",
                _arguments(),
                _Context(),
                None,
                repository=repository,
            )
            self.assertEqual((len(opened), len(canonical.batch_calls)), (1, 1))
            self.assertEqual(
                len(canonical.batch_calls[-1][1]), len(FUNDING_COMPONENTS)
            )

            opened.clear()
            canonical.batch_calls.clear()
            invoke_macro_conditions(
                "macro.get_liquidity_impulse",
                _arguments(start_date="2024-01-01", end_date="2024-05-01"),
                _Context(),
                None,
                repository=repository,
            )
            self.assertEqual((len(opened), len(canonical.batch_calls)), (1, 1))
            self.assertEqual(
                len(canonical.batch_calls[-1][1]),
                2 * len(LIQUIDITY_COMPONENTS),
            )
            self.assertIs(soma_connections[-1], connection)

            opened.clear()
            canonical.batch_calls.clear()
            invoke_macro_conditions(
                "macro.regime_snapshot",
                _arguments(include_context=True),
                _Context(),
                None,
                repository=repository,
            )
            self.assertEqual((len(opened), len(canonical.batch_calls)), (1, 1))
            self.assertEqual(
                len(canonical.batch_calls[-1][1]),
                1 + len(LIQUIDITY_COMPONENTS) + len(CREDIT_COMPONENTS),
            )

            opened.clear()
            canonical.batch_calls.clear()
            repository.revision_analysis_with_audit(
                series_id=GDP_REAL_SERIES_ID,
                start_date=None,
                end_date=None,
                limit=10,
            )
            self.assertEqual((len(opened), len(canonical.batch_calls)), (1, 1))
            self.assertEqual(len(canonical.batch_calls[-1][1]), 2)

    def test_as_of_warning_and_research_pit_audit_are_preserved(self) -> None:
        warning = "date_only_same_day_intraday_safety_not_established"
        connection = object()

        @contextmanager
        def immutable_reader(*_: object, **__: object):
            yield connection

        repository = MacroConditionsRepository(
            object(),
            object(),
            canonical_reader=_BatchCanonical((warning,)),
        )
        with patch(
            "quant_data.macro.conditions.quiet_immutable_read_connection",
            immutable_reader,
        ), patch.object(
            repository,
            "_soma_facts_in_connection",
            return_value=(_soma_fact(),),
        ):
            result = invoke_macro_conditions(
                "research.liquidity_credit_state",
                _arguments(
                    mode="as_of",
                    as_of="2024-05-02",
                    date_only_policy="calendar_date_inclusive",
                ),
                _Context(),
                None,
                repository=repository,
            )

        self.assertIn(warning, {item.code for item in result.warnings})
        contract = result.research_contract.contract
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.point_in_time_status, "not_established")
        self.assertIn(warning, contract.unsafe_reasons)
        summary = {field.name: field.value for field in result.diagnostics[0].metrics}
        self.assertEqual(summary["cutoff_precision"], "date")
        self.assertEqual(
            summary["date_only_policy"], "calendar_date_inclusive"
        )
        self.assertEqual(summary["point_in_time_status"], "not_established")

    def test_revision_and_surprise_outputs_retain_source_lineage(self) -> None:
        gateway = _LineageGateway()
        revision = invoke_macro_conditions(
            "macro.revision_analysis",
            {
                "series_id": GDP_REAL_SERIES_ID,
                "start_date": None,
                "end_date": None,
                "limit": 20,
            },
            _Context(),
            None,
            repository=gateway,
        )
        self.assertTrue(
            {"first", "latest", "first-evidence", "latest-evidence"}
            <= {item.semantic_id for item in revision.lineage}
        )

        surprise = invoke_macro_conditions(
            "macro.standardize_surprises",
            {
                "kind": CPI_HEADLINE_MOM_KIND,
                "release_stage": None,
                "start_date": None,
                "end_date": None,
                "limit": 20,
            },
            _Context(),
            None,
            repository=gateway,
        )
        self.assertTrue(
            {
                "event-version-1",
                "event-version-2",
                "official-version-1",
                "official-capture-1",
            }
            <= {item.semantic_id for item in surprise.lineage}
        )


if __name__ == "__main__":
    unittest.main()
