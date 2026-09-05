"""Bounded scheduled refresh for the manual macro-current collectors.

Each invocation calls every fixed collector once and lets the existing
collector/publisher path decide whether the normalized result is a semantic
no-op. A failed source is recorded without its exception text and does not
prevent the remaining independent sources from running. This module neither
retries providers nor accepts a caller-selected store or source scope.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import sys
from typing import Final
from zoneinfo import ZoneInfo

from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict


_VERSION: Final = "1.0.0"
_NEW_YORK: Final = ZoneInfo("America/New_York")
_ROLLING_WINDOW_DAYS: Final = 45
REQUEST_CAP: Final = 94
_INDUSTRIAL_PRODUCTION_START_DATE: Final = "1919-01-01"
_CHICAGO_START_DATE: Final = "1971-01-08"
_CFNAI_START_DATE: Final = "2026-01-01"
_CMDI_START_DATE: Final = "2005-01-07"
_BIS_START_PERIOD: Final = "1961-Q1"
_PRIMARY_DEALER_SERIES_BREAK: Final = "SBN2024"
_SOURCE_IDS: Final = (
    "fmp_treasury_curve",
    "nyfed_overnight_rates",
    "nyfed_repo_facilities",
    "nyfed_soma",
    "federal_reserve_h41",
    "federal_reserve_h8",
    "federal_reserve_sloos",
    "federal_reserve_policy_rates",
    "industrial_production",
    "chicagofed_financial_conditions",
    "cfnai",
    "bis_credit_conditions",
    "nyfed_cmdi",
    "treasury_tga",
    "treasury_debt",
    "treasury_fiscal_balance",
    "eia_natural_gas_storage",
    "eia_petroleum_weekly_stock",
    "eia_total_motor_gasoline_stocks",
    "eia_distillate_fuel_oil_stocks",
    "eia_finished_motor_gasoline_product_supplied",
    "eia_electricity_retail",
    "nber_us_recession",
    "bls_price_wage_productivity",
    "bea_personal_income",
    "cftc_tff_futures_only",
    "cftc_disaggregated_futures_only",
    "treasury_securities_auctions",
    "nyfed_primary_dealer_statistics",
)
_FAILURE_PRECEDENCE: Final = (64, 69, 74, 75, 70)

Collector = Callable[..., object]


@dataclass(frozen=True, slots=True)
class MacroCurrentRefreshStepReport:
    """Sanitized outcome for one fixed source invocation."""

    source: str
    request_cap: int
    outcome: str
    exit_code: int
    error: str | None = None

    def mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source": self.source,
            "request_cap": self.request_cap,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class MacroCurrentRefreshReport:
    """One complete, fixed-scope macro-current refresh attempt."""

    local_date: str
    steps: tuple[MacroCurrentRefreshStepReport, ...]
    exit_code: int

    def mapping(self) -> dict[str, object]:
        failed = sum(step.outcome == "failed" for step in self.steps)
        return {
            "contract": "quant_data.macro_current_refresh",
            "version": _VERSION,
            "local_date": self.local_date,
            "request_cap": REQUEST_CAP,
            "attempted_steps": len(self.steps),
            "failed_steps": failed,
            "outcome": "succeeded" if failed == 0 else "partial",
            "exit_code": self.exit_code,
            "steps": [step.mapping() for step in self.steps],
        }


def _live_collectors() -> dict[str, Collector]:
    """Load canonical collector entrypoints only for a live invocation."""

    from .federal_reserve_credit_conditions_history import (
        populate_federal_reserve_credit_conditions_live,
    )
    from .cftc_cot_history import populate_cftc_cot_live
    from .eia_electricity_retail_history import (
        populate_eia_electricity_retail_live,
    )
    from .eia_petroleum_weekly_history import (
        populate_eia_petroleum_weekly_stock_live,
    )
    from .fmp_treasury_curve_history import (
        populate_fmp_treasury_curve_history_live,
    )
    from .nyfed_overnight_rates_history import populate_nyfed_overnight_rates_live
    from .nyfed_primary_dealer_statistics_history import (
        populate_nyfed_primary_dealer_statistics_live,
    )
    from .nyfed_repo_facilities_history import populate_nyfed_repo_facilities_live
    from .nyfed_soma_history import populate_nyfed_soma_live
    from .official_conditions_history import (
        populate_bea_personal_income_live,
        populate_bis_credit_conditions_live,
        populate_bls_price_wage_productivity_live,
        populate_chicagofed_financial_conditions_live,
        populate_chicagofed_national_activity_live,
        populate_eia_natural_gas_storage_live,
        populate_eia_total_motor_gasoline_stocks_live,
        populate_eia_distillate_fuel_oil_stocks_live,
        populate_eia_finished_motor_gasoline_product_supplied_live,
        populate_federal_reserve_h41_live,
        populate_federal_reserve_industrial_production_live,
        populate_federal_reserve_policy_rates_live,
        populate_nber_us_recession_live,
        populate_nyfed_cmdi_live,
        populate_treasury_debt_live,
        populate_treasury_fiscal_balance_live,
        populate_treasury_tga_live,
    )
    from .treasury_securities_auctions_history import (
        populate_treasury_securities_auctions_history_live,
    )

    return {
        "fmp_treasury_curve": populate_fmp_treasury_curve_history_live,
        "nyfed_overnight_rates": populate_nyfed_overnight_rates_live,
        "nyfed_repo_facilities": populate_nyfed_repo_facilities_live,
        "nyfed_soma": populate_nyfed_soma_live,
        "federal_reserve_h41": populate_federal_reserve_h41_live,
        "federal_reserve_h8": populate_federal_reserve_credit_conditions_live,
        "federal_reserve_sloos": populate_federal_reserve_credit_conditions_live,
        "federal_reserve_policy_rates": populate_federal_reserve_policy_rates_live,
        "industrial_production": (
            populate_federal_reserve_industrial_production_live
        ),
        "chicagofed_financial_conditions": populate_chicagofed_financial_conditions_live,
        "cfnai": (
            populate_chicagofed_national_activity_live
        ),
        "bis_credit_conditions": populate_bis_credit_conditions_live,
        "nyfed_cmdi": populate_nyfed_cmdi_live,
        "treasury_tga": populate_treasury_tga_live,
        "treasury_debt": populate_treasury_debt_live,
        "treasury_fiscal_balance": populate_treasury_fiscal_balance_live,
        "eia_natural_gas_storage": populate_eia_natural_gas_storage_live,
        "eia_petroleum_weekly_stock": populate_eia_petroleum_weekly_stock_live,
        "eia_total_motor_gasoline_stocks": (
            populate_eia_total_motor_gasoline_stocks_live
        ),
        "eia_distillate_fuel_oil_stocks": (
            populate_eia_distillate_fuel_oil_stocks_live
        ),
        "eia_finished_motor_gasoline_product_supplied": (
            populate_eia_finished_motor_gasoline_product_supplied_live
        ),
        "eia_electricity_retail": populate_eia_electricity_retail_live,
        "nber_us_recession": populate_nber_us_recession_live,
        "bls_price_wage_productivity": populate_bls_price_wage_productivity_live,
        "bea_personal_income": populate_bea_personal_income_live,
        "cftc_tff_futures_only": populate_cftc_cot_live,
        "cftc_disaggregated_futures_only": populate_cftc_cot_live,
        "treasury_securities_auctions": (
            populate_treasury_securities_auctions_history_live
        ),
        "nyfed_primary_dealer_statistics": (
            populate_nyfed_primary_dealer_statistics_live
        ),
    }


def _new_york_date(observed_at: datetime) -> date:
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise ValidationError("Macro current refresh time must include an offset")
    return observed_at.astimezone(_NEW_YORK).date()


def _quarter(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _quarter_bounds(value: date) -> tuple[date, date]:
    start_month = ((value.month - 1) // 3) * 3 + 1
    start = date(value.year, start_month, 1)
    if start_month == 10:
        next_quarter = date(value.year + 1, 1, 1)
    else:
        next_quarter = date(value.year, start_month + 3, 1)
    return start, next_quarter - timedelta(days=1)


def _planned_calls(
    local_date: date,
) -> tuple[tuple[str, int, dict[str, str]], ...]:
    quarter_start, quarter_end = _quarter_bounds(local_date)
    credit_start = _quarter_bounds(quarter_start - timedelta(days=1))[0].isoformat()
    envelope_start = quarter_start - timedelta(days=_ROLLING_WINDOW_DAYS - 1)
    start_date = envelope_start.isoformat()
    end_date = quarter_end.isoformat()
    cftc_anchor = local_date - timedelta(days=(local_date.weekday() - 4) % 7)
    cftc_end_date = cftc_anchor.isoformat()
    cftc_start_date = (cftc_anchor - timedelta(days=20)).isoformat()
    return (
        ("fmp_treasury_curve", 1, {"start_date": start_date, "end_date": end_date}),
        ("nyfed_overnight_rates", 1, {"start_date": start_date, "end_date": end_date}),
        ("nyfed_repo_facilities", 1, {"start_date": start_date, "end_date": end_date}),
        ("nyfed_soma", 1, {"start_date": start_date, "end_date": end_date}),
        ("federal_reserve_h41", 1, {"start_date": start_date, "end_date": end_date}),
        ("federal_reserve_h8", 7, {"source_key": "federal_reserve_h8", "start_date": credit_start, "end_date": end_date}),
        ("federal_reserve_sloos", 6, {"source_key": "federal_reserve_sloos", "start_date": credit_start, "end_date": end_date}),
        ("federal_reserve_policy_rates", 1, {"start_date": start_date, "end_date": end_date}),
        (
            "industrial_production",
            1,
            {
                "start_date": _INDUSTRIAL_PRODUCTION_START_DATE,
                "end_date": end_date,
            },
        ),
        (
            "chicagofed_financial_conditions",
            1,
            {"start_date": _CHICAGO_START_DATE, "end_date": end_date},
        ),
        (
            "cfnai",
            1,
            {"start_date": _CFNAI_START_DATE, "end_date": end_date},
        ),
        (
            "bis_credit_conditions",
            2,
            {"start_period": _BIS_START_PERIOD, "end_period": _quarter(local_date)},
        ),
        ("nyfed_cmdi", 1, {"start_date": _CMDI_START_DATE, "end_date": end_date}),
        ("treasury_tga", 1, {"start_date": start_date, "end_date": end_date}),
        ("treasury_debt", 1, {"start_date": start_date, "end_date": end_date}),
        ("treasury_fiscal_balance", 1, {"start_date": start_date, "end_date": end_date}),
        ("eia_natural_gas_storage", 1, {}),
        ("eia_petroleum_weekly_stock", 1, {}),
        ("eia_total_motor_gasoline_stocks", 1, {}),
        ("eia_distillate_fuel_oil_stocks", 1, {}),
        ("eia_finished_motor_gasoline_product_supplied", 1, {}),
        ("eia_electricity_retail", 8, {}),
        ("nber_us_recession", 1, {}),
        ("bls_price_wage_productivity", 1, {}),
        ("bea_personal_income", 1, {}),
        (
            "cftc_tff_futures_only",
            8,
            {
                "report_family": "tff_futures_only",
                "start_date": cftc_start_date,
                "end_date": cftc_end_date,
            },
        ),
        (
            "cftc_disaggregated_futures_only",
            8,
            {
                "report_family": "disaggregated_futures_only",
                "start_date": cftc_start_date,
                "end_date": cftc_end_date,
            },
        ),
        (
            "treasury_securities_auctions",
            1,
            {"start_date": start_date, "end_date": end_date},
        ),
        (
            "nyfed_primary_dealer_statistics",
            33,
            {
                "series_break": _PRIMARY_DEALER_SERIES_BREAK,
                "start_date": start_date,
                "end_date": end_date,
            },
        ),
    )


def _validated_collectors(collectors: Mapping[str, Collector]) -> dict[str, Collector]:
    if not isinstance(collectors, Mapping) or set(collectors) != set(_SOURCE_IDS):
        raise ValidationError("Macro current refresh collectors are invalid")
    result: dict[str, Collector] = {}
    for source in _SOURCE_IDS:
        collector = collectors[source]
        if not callable(collector):
            raise ValidationError("Macro current refresh collectors are invalid")
        result[source] = collector
    return result


def _outcome_from_report(report: object) -> str:
    outcome = getattr(report, "outcome", None)
    if outcome in {"published", "unchanged"}:
        return outcome
    for name, resolved in (
        ("published_releases", "published"),
        ("published", "published"),
        ("unchanged_releases", "unchanged"),
        ("unchanged", "unchanged"),
    ):
        value = getattr(report, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return resolved
    return "succeeded"


def _failure_details(error: Exception) -> tuple[int, str]:
    if isinstance(error, ValidationError):
        return 64, "invalid_request"
    if isinstance(error, StoreUnavailableError):
        return 69, "store_unavailable"
    if isinstance(error, ResourceLimitError):
        return 74, "local_io"
    if isinstance(error, (RegistryError, ConflictError)):
        return 75, "temporary_conflict"
    return 70, "internal_failure"


def _aggregate_exit_code(steps: tuple[MacroCurrentRefreshStepReport, ...]) -> int:
    failures = {step.exit_code for step in steps if step.exit_code != 0}
    for code in _FAILURE_PRECEDENCE:
        if code in failures:
            return code
    return 0


def run_macro_current_refresh(
    *,
    collectors: Mapping[str, Collector],
    utcnow: Callable[[], datetime],
) -> MacroCurrentRefreshReport:
    """Run each fixed macro-current source once without retries.

    The collectors and utcnow arguments are injected for focused offline tests.
    The live entrypoint below is intentionally zero-argument and supplies the
    canonical collector bindings itself.
    """

    if not callable(utcnow):
        raise ValidationError("Macro current refresh clock is invalid")
    bound_collectors = _validated_collectors(collectors)
    local_date = _new_york_date(utcnow())
    calls = _planned_calls(local_date)
    if sum(request_cap for _, request_cap, _ in calls) != REQUEST_CAP:
        raise RuntimeError("Macro current refresh request cap is invalid")

    steps: list[MacroCurrentRefreshStepReport] = []
    for source, request_cap, arguments in calls:
        try:
            report = bound_collectors[source](**arguments)
        except Exception as error:
            exit_code, error_name = _failure_details(error)
            steps.append(
                MacroCurrentRefreshStepReport(
                    source=source,
                    request_cap=request_cap,
                    outcome="failed",
                    exit_code=exit_code,
                    error=error_name,
                )
            )
        else:
            steps.append(
                MacroCurrentRefreshStepReport(
                    source=source,
                    request_cap=request_cap,
                    outcome=_outcome_from_report(report),
                    exit_code=0,
                )
            )

    frozen_steps = tuple(steps)
    return MacroCurrentRefreshReport(
        local_date=local_date.isoformat(),
        steps=frozen_steps,
        exit_code=_aggregate_exit_code(frozen_steps),
    )


def refresh_macro_current_live() -> MacroCurrentRefreshReport:
    """Run the approved, fixed live macro-current refresh."""

    return run_macro_current_refresh(
        collectors=_live_collectors(),
        utcnow=lambda: datetime.now(timezone.utc),
    )


class _ArgumentFailure(Exception):
    """Sanitized zero-argument CLI rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def main(argv: list[str] | None = None) -> int:
    """Run the fixed refresh and print one strict, sanitized summary."""

    try:
        _SafeArgumentParser(add_help=False).parse_args(argv)
    except _ArgumentFailure:
        sys.stderr.write(dumps_strict({"error": "invalid_arguments"}) + "\n")
        sys.stderr.flush()
        return 2
    try:
        report = refresh_macro_current_live()
    except Exception as error:
        exit_code, error_name = _failure_details(error)
        sys.stderr.write(
            dumps_strict(
                {
                    "contract": "quant_data.macro_current_refresh_error",
                    "version": _VERSION,
                    "error": error_name,
                    "exit_code": exit_code,
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return exit_code
    sys.stdout.write(dumps_strict(report.mapping()) + "\n")
    sys.stdout.flush()
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = (
    "MacroCurrentRefreshReport",
    "MacroCurrentRefreshStepReport",
    "REQUEST_CAP",
    "refresh_macro_current_live",
    "run_macro_current_refresh",
)
