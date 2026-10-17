from __future__ import annotations

import unittest
from decimal import Decimal

from quant_data.tool_platform.energy_access import derive_energy_seasonality_records


def _row(
    period: str,
    value: Decimal,
    *,
    metric: str | None = "sales",
) -> dict[str, object]:
    return {
        "artifact_id": f"artifact-{period}",
        "canonical_series_id": "macro.eia.electricity.retail_sales",
        "metric": metric,
        "period": period,
        "snapshot_id": f"snapshot-{period}",
        "unit": "million_mwh",
        "value": value,
        "version_id": f"version-{period}",
    }


class EnergySeasonalityV21Tests(unittest.TestCase):
    def test_monthly_comparison_uses_exact_retained_observation_lag(self) -> None:
        rows = tuple(
            _row(f"2024-{month:02d}", Decimal(month))
            for month in range(1, 13)
        ) + (_row("2025-01", Decimal("13")),)
        result = derive_energy_seasonality_records(rows, lag_observations=12)

        self.assertEqual(len(result), 13)
        self.assertEqual(result[11]["comparison_status"], "unavailable")
        self.assertEqual(
            result[11]["unavailable_reason"], "insufficient_retained_history"
        )
        final = result[-1]
        self.assertEqual(final["comparison_lag_observations"], 12)
        self.assertEqual(final["comparison_status"], "ok")
        self.assertEqual(final["reference_period"], "2024-01")
        self.assertEqual(final["reference_level"], Decimal("1"))
        self.assertEqual(final["level_change"], Decimal("12"))
        self.assertEqual(final["percent_change"], Decimal("12"))

    def test_weekly_zero_base_is_explicit_without_a_percent_change(self) -> None:
        rows = tuple(
            _row(
                f"2025-W{week:02d}",
                Decimal("0") if week == 1 else Decimal(week),
                metric=None,
            )
            for week in range(1, 53)
        ) + (
            _row(
                "2026-W01",
                Decimal("53"),
                metric=None,
            ),
        )
        result = derive_energy_seasonality_records(rows, lag_observations=52)

        final = result[-1]
        self.assertEqual(final["comparison_lag_observations"], 52)
        self.assertEqual(final["reference_period"], "2025-W01")
        self.assertEqual(final["level_change"], Decimal("53"))
        self.assertEqual(final["percent_change"], None)
        self.assertEqual(final["comparison_status"], "zero_base")
        self.assertEqual(final["unavailable_reason"], "zero_reference_level")


if __name__ == "__main__":
    unittest.main()
