from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
import unittest

from quant_data.errors import ValidationError
from quant_data.macro.conditions import (
    ConditionFact,
    MacroConditionsRepository,
    RevisionFact,
    StandardizedSurprise,
)
from quant_data.macro.fmp_release_surprises import (
    CPI_HEADLINE_MOM_KIND,
    ReleaseSurprise,
)
from quant_data.macro.live_vintages import GDP_REAL_SERIES_ID
from quant_data.tool_platform.macro_conditions import invoke_macro_conditions


def _record(period: str, value: str, version: str) -> dict[str, object]:
    return {
        "series_id": GDP_REAL_SERIES_ID,
        "period_start": period,
        "period_end": period,
        "value": Decimal(value),
        "missing_reason": None,
        "unit": "percent",
        "value_representation": "rate",
        "available_at": "2024-05-01T12:00:00Z",
        "available_precision": "datetime",
        "captured_at": "2024-05-01T12:00:00Z",
        "captured_precision": "datetime",
        "version_id": version,
        "capture_id": "capture-" + version,
        "snapshot_id": None,
        "source_vintage_identity": version,
    }


class _Canonical:
    def get_series(self, request: object) -> object:
        mode = getattr(request, "mode")
        if mode == "first_release":
            rows = (_record("2024-01-01", "1", "first"),)
        else:
            rows = (_record("2024-01-01", "3", "latest"),)
        return SimpleNamespace(
            records=rows,
            dataset_ids=("macro.official_vintages",),
            truncated=False,
        )


class _Surprises:
    def query(self, **_: object) -> tuple[ReleaseSurprise, ...]:
        return tuple(
            ReleaseSurprise(
                event_id=f"event-{value}",
                event_version_id=f"version-{value}",
                kind=CPI_HEADLINE_MOM_KIND,
                event_at=f"2024-0{index}-01T12:00:00Z",
                reference_period=f"2023-{12 - index:02d}",
                consensus_mapping_basis="same_fmp_event",
                consensus=Decimal("0"),
                fmp_actual=value,
                official_actual=value,
                surprise=value,
                unit="percent",
                actual_source="fmp_calendar",
                availability_assumption="event_at_utc",
                status="ok",
                official_version_id=None,
            )
            for index, value in enumerate(
                (Decimal("1"), Decimal("2"), Decimal("3")), start=1
            )
        )


def _fact(
    component: str,
    value: str,
    *,
    period: str = "2024-05-01",
    unit: str = "percent",
) -> ConditionFact:
    return ConditionFact(
        component=component,
        series_id="macro.test." + component,
        period_start=period,
        period_end=period,
        value=Decimal(value),
        missing_reason=None,
        unit=unit,
        value_representation="rate",
        available_at="2024-05-02T12:00:00Z",
        available_precision="datetime",
        captured_at="2024-05-02T12:00:00Z",
        captured_precision="datetime",
        version_id="version-" + component + "-" + period,
        dataset_id="macro.test",
        evidence_id="evidence-" + component,
        snapshot_id="snapshot-" + component,
    )


class _Gateway:
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

    def standardize_surprises(self, **_: object) -> tuple[StandardizedSurprise, ...]:
        return (
            StandardizedSurprise(
                event_at="2024-05-01T12:00:00Z",
                event_id="event-1",
                event_version_id="version-1",
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
                official_version_id=None,
            ),
        )

    def snapshot(
        self,
        components: tuple[tuple[str, str], ...],
        *,
        observation_date: str | None,
        include_soma: bool = False,
        **_: object,
    ) -> tuple[ConditionFact, ...]:
        base = Decimal("1") if observation_date == "2024-01-01" else Decimal("3")
        result = []
        for component, _series_id in components:
            if component == "NBER_recession_indicator":
                result.append(_fact(component, "0", unit="indicator"))
            elif component == "EFFR":
                result.append(_fact(component, "5.25"))
            elif component == "SOFR":
                result.append(_fact(component, "5.00"))
            else:
                result.append(_fact(component, str(base), unit="usd_millions"))
        if include_soma:
            result.append(_fact("SOMA_total", str(base), unit="thousands_usd"))
        return tuple(result)


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


def _field_map(result: object, index: int = 0) -> dict[str, object]:
    record = getattr(result, "records")[index]
    return {field.name: field.value for field in record.fields}


class MacroConditionsV2Tests(unittest.TestCase):
    def test_official_revision_is_current_first_vs_latest(self) -> None:
        repository = MacroConditionsRepository(
            None,
            None,
            canonical_reader=_Canonical(),
            surprise_reader=_Surprises(),
        )

        records = repository.revision_analysis(
            series_id=GDP_REAL_SERIES_ID,
            start_date=None,
            end_date=None,
            limit=10,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].signed_revision, Decimal("2"))
        self.assertEqual(records[0].absolute_revision, Decimal("2"))

    def test_surprise_standardization_uses_population_standard_deviation(self) -> None:
        repository = MacroConditionsRepository(
            None,
            None,
            canonical_reader=_Canonical(),
            surprise_reader=_Surprises(),
        )

        records = repository.standardize_surprises(
            kind=CPI_HEADLINE_MOM_KIND,
            release_stage=None,
            start_date=None,
            end_date=None,
            limit=10,
        )

        self.assertEqual(records[1].z_score, Decimal("0"))
        self.assertAlmostEqual(float(records[0].z_score), -1.224744871, places=8)
        self.assertAlmostEqual(
            float(records[0].population_standard_deviation), 0.816496581, places=8
        )

    def test_adapter_returns_raw_funding_facts_and_one_direct_spread(self) -> None:
        result = invoke_macro_conditions(
            "rates.get_funding_conditions",
            _arguments(spread_left="EFFR", spread_right="SOFR"),
            _Context(),
            None,
            repository=_Gateway(),  # type: ignore[arg-type]
        )

        self.assertEqual(result.status, "ok")
        spread = _field_map(result, -1)
        self.assertEqual(spread["spread"], Decimal("0.25"))
        self.assertEqual(spread["calculation"], "left_minus_right")

    def test_result_limit_is_explicitly_truncated(self) -> None:
        result = invoke_macro_conditions(
            "rates.get_funding_conditions",
            _arguments(limit=1),
            _Context(),
            None,
            repository=_Gateway(),  # type: ignore[arg-type]
        )

        self.assertEqual(len(result.records), 1)
        self.assertTrue(result.truncation.applied)
        self.assertTrue(result.truncation.has_more)
        self.assertEqual(result.truncation.returned_count, 1)
        self.assertGreater(result.truncation.total_known_count or 0, 1)

    def test_liquidity_impulse_has_component_deltas_only(self) -> None:
        result = invoke_macro_conditions(
            "macro.get_liquidity_impulse",
            _arguments(start_date="2024-01-01", end_date="2024-05-01"),
            _Context(),
            None,
            repository=_Gateway(),  # type: ignore[arg-type]
        )

        self.assertEqual(result.status, "ok")
        fields = _field_map(result)
        self.assertEqual(fields["delta"], Decimal("2"))
        self.assertEqual(
            fields["methodology"], "component_end_minus_start_no_aggregate"
        )

    def test_regime_is_a_direct_stored_indicator_not_a_classifier(self) -> None:
        result = invoke_macro_conditions(
            "macro.regime_snapshot",
            _arguments(),
            _Context(),
            None,
            repository=_Gateway(),  # type: ignore[arg-type]
        )

        fields = _field_map(result)
        self.assertEqual(fields["regime"], "expansion")
        self.assertEqual(fields["methodology"], "direct_stored_nber_indicator")

    def test_research_vector_declares_no_score_or_classification(self) -> None:
        result = invoke_macro_conditions(
            "research.liquidity_credit_state",
            _arguments(),
            _Context(),
            None,
            repository=_Gateway(),  # type: ignore[arg-type]
        )

        fields = _field_map(result)
        self.assertEqual(fields["methodology"], "no_score_no_classification")
        self.assertEqual(result.research_contract.status, "declared")


if __name__ == "__main__":
    unittest.main()
