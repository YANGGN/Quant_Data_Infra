"""Manual, bounded refresh for the six macro-database expansion sources.

This aggregate deliberately reuses the existing canonical live wrappers.  It
does not add a scheduler, retry policy, provider configuration, or caller
selected storage path.  Each wrapper may commit independently; once a source
fails the aggregate stops and makes no cross-source rollback claim.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
import sys
from typing import Final

from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict


_VERSION: Final = "1.0.0"
REQUEST_CAP: Final = 63
_SERIES_BREAK = re.compile(r"SBN[0-9]{4}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SOURCE_IDS: Final = (
    "cftc_tff_futures_only",
    "cftc_disaggregated_futures_only",
    "treasury_securities_auctions",
    "nyfed_primary_dealer_statistics",
    "federal_reserve_h8",
    "federal_reserve_sloos",
)
_REQUEST_CAP_BY_SOURCE: Final = {
    "cftc_tff_futures_only": 8,
    "cftc_disaggregated_futures_only": 8,
    "treasury_securities_auctions": 1,
    "nyfed_primary_dealer_statistics": 33,
    "federal_reserve_h8": 7,
    "federal_reserve_sloos": 6,
}

Collector = Callable[..., object]


@dataclass(frozen=True, slots=True)
class MacroDatabaseExpansionRefreshCall:
    """One fixed wrapper invocation in the manual aggregate."""

    source: str
    request_cap: int
    arguments: tuple[tuple[str, str], ...]

    def keyword_arguments(self) -> dict[str, str]:
        return dict(self.arguments)


@dataclass(frozen=True, slots=True)
class MacroDatabaseExpansionRefreshPlan:
    """Fully validated bounded scope, built before live wrappers are loaded."""

    as_of: date
    anchor_date: date
    series_break: str
    calls: tuple[MacroDatabaseExpansionRefreshCall, ...]


@dataclass(frozen=True, slots=True)
class MacroDatabaseExpansionRefreshStepReport:
    """Sanitized outcome for one attempted source."""

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
class MacroDatabaseExpansionRefreshReport:
    """Summary of the serial bounded manual aggregate."""

    as_of: date
    anchor_date: date
    series_break: str
    steps: tuple[MacroDatabaseExpansionRefreshStepReport, ...]
    exit_code: int

    def mapping(self) -> dict[str, object]:
        failed = sum(step.outcome == "failed" for step in self.steps)
        return {
            "contract": "quant_data.macro_database_expansion_refresh",
            "version": _VERSION,
            "as_of": self.as_of.isoformat(),
            "anchor_date": self.anchor_date.isoformat(),
            "series_break": self.series_break,
            "request_cap": REQUEST_CAP,
            "attempted_steps": len(self.steps),
            "failed_steps": failed,
            "outcome": "succeeded" if failed == 0 else "failed",
            "exit_code": self.exit_code,
            "steps": [step.mapping() for step in self.steps],
        }


def _as_date(value: date | str, *, name: str) -> date:
    if isinstance(value, datetime):
        raise ValidationError(f"{name} must be an ISO calendar date")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _ISO_DATE.fullmatch(value) is None:
        raise ValidationError(f"{name} must be an ISO calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{name} must be an ISO calendar date") from exc


def _validated_series_break(value: object) -> str:
    if not isinstance(value, str) or _SERIES_BREAK.fullmatch(value) is None:
        raise ValidationError("Macro database expansion series break is invalid")
    return value


def _latest_friday(value: date) -> date:
    try:
        return value - timedelta(days=(value.weekday() - 4) % 7)
    except OverflowError as exc:
        raise ValidationError("Macro database expansion as-of date is invalid") from exc


def _call(
    source: str,
    arguments: Mapping[str, str],
) -> MacroDatabaseExpansionRefreshCall:
    request_cap = _REQUEST_CAP_BY_SOURCE[source]
    return MacroDatabaseExpansionRefreshCall(
        source=source,
        request_cap=request_cap,
        arguments=tuple(sorted(arguments.items())),
    )


def _validate_plan(plan: MacroDatabaseExpansionRefreshPlan) -> None:
    if (
        not isinstance(plan.as_of, date)
        or isinstance(plan.as_of, datetime)
        or not isinstance(plan.anchor_date, date)
        or isinstance(plan.anchor_date, datetime)
        or plan.anchor_date > plan.as_of
        or plan.anchor_date.weekday() != 4
        or _validated_series_break(plan.series_break) != plan.series_break
        or tuple(call.source for call in plan.calls) != _SOURCE_IDS
        or any(
            call.request_cap != _REQUEST_CAP_BY_SOURCE[call.source]
            for call in plan.calls
        )
        or sum(call.request_cap for call in plan.calls) != REQUEST_CAP
    ):
        raise ValidationError("Macro database expansion plan is invalid")

    calls = {call.source: call.keyword_arguments() for call in plan.calls}
    anchor = plan.anchor_date.isoformat()
    try:
        cftc_start = (plan.anchor_date - timedelta(days=20)).isoformat()
        standard_start = (plan.anchor_date - timedelta(days=44)).isoformat()
        sloos_start = (plan.anchor_date - timedelta(days=370)).isoformat()
    except OverflowError as exc:
        raise ValidationError("Macro database expansion plan is invalid") from exc
    expected = {
        "cftc_tff_futures_only": {
            "report_family": "tff_futures_only",
            "start_date": cftc_start,
            "end_date": anchor,
        },
        "cftc_disaggregated_futures_only": {
            "report_family": "disaggregated_futures_only",
            "start_date": cftc_start,
            "end_date": anchor,
        },
        "treasury_securities_auctions": {
            "start_date": standard_start,
            "end_date": anchor,
        },
        "nyfed_primary_dealer_statistics": {
            "series_break": plan.series_break,
            "start_date": standard_start,
            "end_date": anchor,
        },
        "federal_reserve_h8": {
            "source_key": "federal_reserve_h8",
            "start_date": standard_start,
            "end_date": anchor,
        },
        "federal_reserve_sloos": {
            "source_key": "federal_reserve_sloos",
            "start_date": sloos_start,
            "end_date": anchor,
        },
    }
    if calls != expected:
        raise ValidationError("Macro database expansion plan is invalid")


def build_macro_database_expansion_refresh_plan(
    *,
    as_of: date | str,
    series_break: str,
) -> MacroDatabaseExpansionRefreshPlan:
    """Build all six bounded calls before loading or invoking live wrappers."""

    as_of_date = _as_date(as_of, name="as_of")
    resolved_series_break = _validated_series_break(series_break)
    anchor = _latest_friday(as_of_date)
    try:
        cftc_start = (anchor - timedelta(days=20)).isoformat()
        standard_start = (anchor - timedelta(days=44)).isoformat()
        sloos_start = (anchor - timedelta(days=370)).isoformat()
    except OverflowError as exc:
        raise ValidationError("Macro database expansion as-of date is invalid") from exc
    anchor_text = anchor.isoformat()
    plan = MacroDatabaseExpansionRefreshPlan(
        as_of=as_of_date,
        anchor_date=anchor,
        series_break=resolved_series_break,
        calls=(
            _call(
                "cftc_tff_futures_only",
                {
                    "report_family": "tff_futures_only",
                    "start_date": cftc_start,
                    "end_date": anchor_text,
                },
            ),
            _call(
                "cftc_disaggregated_futures_only",
                {
                    "report_family": "disaggregated_futures_only",
                    "start_date": cftc_start,
                    "end_date": anchor_text,
                },
            ),
            _call(
                "treasury_securities_auctions",
                {"start_date": standard_start, "end_date": anchor_text},
            ),
            _call(
                "nyfed_primary_dealer_statistics",
                {
                    "series_break": resolved_series_break,
                    "start_date": standard_start,
                    "end_date": anchor_text,
                },
            ),
            _call(
                "federal_reserve_h8",
                {
                    "source_key": "federal_reserve_h8",
                    "start_date": standard_start,
                    "end_date": anchor_text,
                },
            ),
            _call(
                "federal_reserve_sloos",
                {
                    "source_key": "federal_reserve_sloos",
                    "start_date": sloos_start,
                    "end_date": anchor_text,
                },
            ),
        ),
    )
    _validate_plan(plan)
    return plan


def _live_collectors() -> dict[str, Collector]:
    """Lazily load only the six existing canonical live wrapper bindings."""

    from .cftc_cot_history import populate_cftc_cot_live
    from .federal_reserve_credit_conditions_history import (
        populate_federal_reserve_credit_conditions_live,
    )
    from .nyfed_primary_dealer_statistics_history import (
        populate_nyfed_primary_dealer_statistics_live,
    )
    from .treasury_securities_auctions_history import (
        populate_treasury_securities_auctions_history_live,
    )

    return {
        "cftc_tff_futures_only": populate_cftc_cot_live,
        "cftc_disaggregated_futures_only": populate_cftc_cot_live,
        "treasury_securities_auctions": (
            populate_treasury_securities_auctions_history_live
        ),
        "nyfed_primary_dealer_statistics": (
            populate_nyfed_primary_dealer_statistics_live
        ),
        "federal_reserve_h8": populate_federal_reserve_credit_conditions_live,
        "federal_reserve_sloos": populate_federal_reserve_credit_conditions_live,
    }


def _validated_collectors(collectors: Mapping[str, Collector]) -> dict[str, Collector]:
    if not isinstance(collectors, Mapping) or set(collectors) != set(_SOURCE_IDS):
        raise ValidationError("Macro database expansion collectors are invalid")
    result: dict[str, Collector] = {}
    for source in _SOURCE_IDS:
        collector = collectors[source]
        if not callable(collector):
            raise ValidationError("Macro database expansion collectors are invalid")
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


def _run_plan(
    *,
    plan: MacroDatabaseExpansionRefreshPlan,
    collectors: Mapping[str, Collector],
) -> MacroDatabaseExpansionRefreshReport:
    _validate_plan(plan)
    bound_collectors = _validated_collectors(collectors)
    steps: list[MacroDatabaseExpansionRefreshStepReport] = []
    for call in plan.calls:
        try:
            report = bound_collectors[call.source](**call.keyword_arguments())
        except Exception as error:
            exit_code, error_name = _failure_details(error)
            steps.append(
                MacroDatabaseExpansionRefreshStepReport(
                    source=call.source,
                    request_cap=call.request_cap,
                    outcome="failed",
                    exit_code=exit_code,
                    error=error_name,
                )
            )
            break
        steps.append(
            MacroDatabaseExpansionRefreshStepReport(
                source=call.source,
                request_cap=call.request_cap,
                outcome=_outcome_from_report(report),
                exit_code=0,
            )
        )
    frozen_steps = tuple(steps)
    exit_code = next(
        (step.exit_code for step in frozen_steps if step.exit_code != 0), 0
    )
    return MacroDatabaseExpansionRefreshReport(
        as_of=plan.as_of,
        anchor_date=plan.anchor_date,
        series_break=plan.series_break,
        steps=frozen_steps,
        exit_code=exit_code,
    )


def run_macro_database_expansion_refresh(
    *,
    as_of: date | str,
    series_break: str,
    collectors: Mapping[str, Collector],
) -> MacroDatabaseExpansionRefreshReport:
    """Run one injected, serial six-source attempt with no retry behavior."""

    plan = build_macro_database_expansion_refresh_plan(
        as_of=as_of,
        series_break=series_break,
    )
    return _run_plan(plan=plan, collectors=collectors)


def refresh_macro_database_expansion_live(
    *,
    as_of: date | str,
    series_break: str,
) -> MacroDatabaseExpansionRefreshReport:
    """Run the reviewed manual aggregate through existing canonical wrappers."""

    plan = build_macro_database_expansion_refresh_plan(
        as_of=as_of,
        series_break=series_break,
    )
    return _run_plan(plan=plan, collectors=_live_collectors())


class _ArgumentFailure(Exception):
    """Sanitized CLI parse rejection."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=False)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--series-break", required=True)
    return parser


def _emit_error(exit_code: int, error: str) -> int:
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.macro_database_expansion_refresh_error",
                "version": _VERSION,
                "error": error,
                "exit_code": exit_code,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Validate the full scope before loading or invoking any live wrapper."""

    try:
        arguments = _parser().parse_args(argv)
        report = refresh_macro_database_expansion_live(
            as_of=arguments.as_of,
            series_break=arguments.series_break,
        )
    except _ArgumentFailure:
        return _emit_error(64, "invalid_request")
    except Exception as error:
        exit_code, error_name = _failure_details(error)
        return _emit_error(exit_code, error_name)
    sys.stdout.write(dumps_strict(report.mapping()) + "\n")
    sys.stdout.flush()
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = (
    "MacroDatabaseExpansionRefreshCall",
    "MacroDatabaseExpansionRefreshPlan",
    "MacroDatabaseExpansionRefreshReport",
    "MacroDatabaseExpansionRefreshStepReport",
    "REQUEST_CAP",
    "build_macro_database_expansion_refresh_plan",
    "refresh_macro_database_expansion_live",
    "run_macro_database_expansion_refresh",
)
