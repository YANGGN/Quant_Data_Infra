from __future__ import annotations

import unittest
from decimal import Decimal

from quant_data.errors import ValidationError
from quant_data.tool_platform.company_fundamentals import (
    derive_company_fundamental_ratios,
)


def _row(
    metric_code: str,
    value: Decimal,
    *,
    period_end: str = "2024-12-31",
    period_start: str | None,
    fiscal_period: str = "FY",
    fiscal_year: int = 2024,
) -> dict[str, object]:
    suffix = f"{metric_code}-{period_end}"
    return {
        "accession_number": f"accession-{suffix}",
        "available_at": "2025-02-01T12:00:00Z",
        "available_precision": "datetime",
        "base_unit": "USD",
        "fiscal_period": fiscal_period,
        "fiscal_year": fiscal_year,
        "fundamental_version_id": f"fundamental-{suffix}",
        "mapping_id": f"mapping-{metric_code}",
        "mapping_version": "sec-core-v2",
        "metric_code": metric_code,
        "reference_period_end": period_end,
        "reference_period_start": period_start,
        "source_fact_version_id": f"source-fact-{suffix}",
        "source_snapshot_id": f"snapshot-{suffix}",
        "value": value,
        "value_state": "observed",
    }


class CompanyFundamentalRatiosV21Tests(unittest.TestCase):
    def test_same_period_ratios_keep_source_lineage_and_exclude_bad_inputs(self) -> None:
        rows = (
            _row("net_income", Decimal("20"), period_start="2024-01-01"),
            _row("revenue", Decimal("100"), period_start="2024-01-01"),
            _row("total_liabilities", Decimal("40"), period_start=None),
            _row("total_assets", Decimal("100"), period_start=None),
            _row(
                "net_income",
                Decimal("2"),
                period_end="2025-12-31",
                period_start="2025-01-01",
                fiscal_year=2025,
            ),
            _row(
                "revenue",
                Decimal("0"),
                period_end="2025-12-31",
                period_start="2025-01-01",
                fiscal_year=2025,
            ),
            _row(
                "net_income",
                Decimal("3"),
                period_end="2026-12-31",
                period_start="2026-01-01",
                fiscal_year=2026,
            ),
            _row(
                "revenue",
                Decimal("30"),
                period_end="2026-12-31",
                period_start="2026-02-01",
                fiscal_year=2026,
            ),
        )

        records, exclusions = derive_company_fundamental_ratios(
            rows,
            ratio_codes=("net_margin", "liabilities_to_assets"),
        )
        by_code = {str(record["ratio_code"]): record for record in records}

        self.assertEqual(by_code["net_margin"]["ratio_value"], Decimal("0.2"))
        self.assertEqual(
            by_code["net_margin"]["period_semantics"],
            "same_duration_start_and_end",
        )
        self.assertEqual(
            by_code["net_margin"]["numerator_fundamental_version_id"],
            "fundamental-net_income-2024-12-31",
        )
        self.assertEqual(
            by_code["net_margin"]["denominator_mapping_version"],
            "sec-core-v2",
        )
        self.assertEqual(
            by_code["liabilities_to_assets"]["ratio_value"], Decimal("0.4")
        )
        self.assertEqual(
            by_code["liabilities_to_assets"]["period_semantics"],
            "same_instant_end",
        )
        self.assertEqual(
            {item.code for item in exclusions},
            {"incompatible_period_or_unit", "zero_denominator"},
        )
        self.assertEqual(
            {item.subject_id for item in exclusions},
            {"net_margin:2025-12-31", "net_margin:2026-12-31"},
        )
        with self.assertRaises(ValidationError):
            derive_company_fundamental_ratios(
                rows,
                ratio_codes=("current_ratio",),
            )


if __name__ == "__main__":
    unittest.main()
