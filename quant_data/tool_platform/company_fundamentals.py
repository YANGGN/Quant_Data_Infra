"""Typed v2 reader for normalized, reviewed SEC company facts.

This is deliberately a fact reader, not a ratio or earnings-model layer.
Every returned value is an existing normalized observation with its original
period, unit, availability, mapping, and source references preserved.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final, Mapping

from quant_data.company.sec_companyfacts import SEC_CORE_MAPPINGS, SecCoreMapping
from quant_data.company.stage4_repository import (
    CompanyStage4Query,
    CompanyStage4Repository,
)
from quant_data.contracts import ExclusionV1, LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.registry import Registry
from quant_data.stores import stable_id
from quant_data.temporal import parse_date

from .arguments import (
    CompanyFundamentalRatiosArgumentsV21,
    CompanyFundamentalsArgumentsV2,
)
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, Scalar, fields_from_mapping, records_from_mappings


TOOL_NAME: Final = "company.get_fundamentals"
OPERATION_VERSION: Final = "2.0.0"
OPERATION_VERSION_V21: Final = "2.1.0"
_MAX_REPOSITORY_LIMIT: Final = 10_000
_MAPPINGS_BY_CODE: Final[dict[str, SecCoreMapping]] = {
    item.metric_code: item for item in SEC_CORE_MAPPINGS
}
METRIC_IDS_BY_CODE: Final[dict[str, str]] = {
    metric_code: stable_id("company_metric", metric_code)
    for metric_code in _MAPPINGS_BY_CODE
}
_METRIC_CODE_BY_ID: Final[dict[str, str]] = {
    metric_id: metric_code for metric_code, metric_id in METRIC_IDS_BY_CODE.items()
}
_DATE_ONLY_SAFETY_WARNING: Final = (
    "date_only_same_day_intraday_safety_not_established"
)
_RATIO_REQUIREMENTS: Final[dict[str, tuple[str, str, str]]] = {
    "liabilities_to_assets": ("total_liabilities", "total_assets", "instant"),
    "net_margin": ("net_income", "revenue", "duration"),
}



def _validate_arguments(arguments: CompanyFundamentalsArgumentsV2) -> tuple[str, ...]:
    if arguments.mode not in {"latest", "as_of"}:
        raise ValidationError("Company fundamentals mode is unsupported")
    if arguments.mode == "as_of" and arguments.as_of is None:
        raise ValidationError("Company fundamentals as_of mode requires a cutoff")
    if arguments.mode == "latest" and arguments.as_of is not None:
        raise ValidationError("Company fundamentals latest mode does not accept a cutoff")
    if arguments.start_date is not None:
        parse_date(arguments.start_date, pointer="/start_date")
    if arguments.end_date is not None:
        parse_date(arguments.end_date, pointer="/end_date")
    if (
        arguments.start_date is not None
        and arguments.end_date is not None
        and arguments.end_date < arguments.start_date
    ):
        raise ValidationError("Company fundamentals end_date cannot precede start_date")
    raw_codes = arguments.metric_codes
    if isinstance(raw_codes, (str, bytes)) or not isinstance(raw_codes, tuple):
        raise ValidationError("Company fundamental metric_codes must be an array")
    if len(raw_codes) > 50:
        raise ValidationError("Company fundamental metric_codes exceed the supported limit")
    if any(not isinstance(code, str) or code not in _MAPPINGS_BY_CODE for code in raw_codes):
        raise ValidationError("Company fundamental metric code is unsupported")
    if len(raw_codes) != len(set(raw_codes)):
        raise ValidationError("Company fundamental metric_codes must be unique")
    return raw_codes or tuple(_MAPPINGS_BY_CODE)



def _validate_ratio_arguments(
    arguments: CompanyFundamentalRatiosArgumentsV21,
) -> tuple[str, ...]:
    if arguments.mode not in {"latest", "as_of"}:
        raise ValidationError("Company fundamental-ratio mode is unsupported")
    if arguments.mode == "as_of" and arguments.as_of is None:
        raise ValidationError("Company fundamental-ratio as_of mode requires a cutoff")
    if arguments.mode == "latest" and arguments.as_of is not None:
        raise ValidationError(
            "Company fundamental-ratio latest mode does not accept a cutoff"
        )
    if arguments.start_date is not None:
        parse_date(arguments.start_date, pointer="/start_date")
    if arguments.end_date is not None:
        parse_date(arguments.end_date, pointer="/end_date")
    if (
        arguments.start_date is not None
        and arguments.end_date is not None
        and arguments.end_date < arguments.start_date
    ):
        raise ValidationError(
            "Company fundamental-ratio end_date cannot precede start_date"
        )
    codes = arguments.ratio_codes
    if isinstance(codes, (str, bytes)) or not isinstance(codes, tuple):
        raise ValidationError("Company fundamental ratio_codes must be an array")
    if not 1 <= len(codes) <= 2:
        raise ValidationError("Company fundamental ratio_codes require one or two values")
    if (
        any(not isinstance(code, str) or code not in _RATIO_REQUIREMENTS for code in codes)
        or len(codes) != len(set(codes))
    ):
        raise ValidationError("Company fundamental ratio code is unsupported or repeated")
    if isinstance(arguments.limit, bool) or not 1 <= arguments.limit <= 1_000:
        raise ResourceLimitError("Company fundamental-ratio limit is invalid")
    return codes

def _metric_code_for(row: Mapping[str, object]) -> str:
    metric_id = row.get("metric_id")
    metric_code = _METRIC_CODE_BY_ID.get(metric_id) if isinstance(metric_id, str) else None
    if metric_code is None:
        raise ValidationError("Company fundamentals returned an unreviewed metric")
    mapping = _MAPPINGS_BY_CODE[metric_code]
    if (
        row.get("metric_label") != mapping.display_name
        or row.get("base_unit") != mapping.unit
        or row.get("share_semantics") != mapping.share_semantics
    ):
        raise ValidationError("Company fundamentals mapping is inconsistent")
    return metric_code


def _record_fields(
    row: Mapping[str, object],
    *,
    metric_code: str,
) -> dict[str, Scalar]:
    return {
        "accession_number": row.get("accession_number"),
        "available_at": row.get("available_at"),
        "available_precision": row.get("available_precision"),
        "base_unit": row.get("base_unit"),
        "fiscal_period": row.get("fiscal_period"),
        "fiscal_year": row.get("fiscal_year"),
        "fundamental_version_id": row.get("fundamental_version_id"),
        "mapping_id": row.get("mapping_id"),
        "mapping_version": row.get("mapping_version"),
        "metric_code": metric_code,
        "metric_id": row.get("metric_id"),
        "metric_label": row.get("metric_label"),
        "missing_reason": row.get("missing_reason"),
        "reference_period_end": row.get("reference_period_end"),
        "reference_period_start": row.get("reference_period_start"),
        "share_semantics": row.get("share_semantics"),
        "source_fact_version_id": row.get("source_fact_version_id"),
        "source_snapshot_id": row.get("source_snapshot_id"),
        "value": row.get("value"),
        "value_state": row.get("value_state"),
    }


def _source_warning_messages(rows: tuple[Mapping[str, object], ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                warning
                for row in rows
                for warning in row.get("warnings", ())
                if isinstance(warning, str) and warning
            }
        )
    )


def _warnings(messages: tuple[str, ...]) -> tuple[WarningV1, ...]:
    return tuple(
        WarningV1(code="source_selection_warning", message=message)
        for message in messages
    )


def _point_in_time_selection(
    *,
    mode: str,
    source_warning_messages: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if mode == "latest":
        return "not_applicable", ()
    unsafe_reasons = tuple(
        message
        for message in source_warning_messages
        if message == _DATE_ONLY_SAFETY_WARNING
    )
    return ("unsafe" if unsafe_reasons else "safe"), unsafe_reasons


def _lineage(rows: tuple[Mapping[str, object], ...]) -> tuple[LineageRef, ...]:
    return tuple(
        LineageRef(
            dataset_id="fixture.company.fundamentals",
            store_role="company",
            semantic_id=str(row["fundamental_version_id"]),
            snapshot_id=(
                str(row["source_snapshot_id"])
                if row.get("source_snapshot_id") is not None
                else None
            ),
            canonical_version_id=str(row["fundamental_version_id"]),
        )
        for row in rows
    )




def _ratio_record(
    *,
    ratio_code: str,
    numerator: Mapping[str, object],
    denominator: Mapping[str, object],
    period_semantics: str,
) -> dict[str, Scalar]:
    numerator_value = numerator["value"]
    denominator_value = denominator["value"]
    assert isinstance(numerator_value, Decimal)
    assert isinstance(denominator_value, Decimal)
    return {
        "denominator_accession_number": denominator.get("accession_number"),
        "denominator_available_at": denominator.get("available_at"),
        "denominator_available_precision": denominator.get("available_precision"),
        "denominator_fundamental_version_id": denominator.get(
            "fundamental_version_id"
        ),
        "denominator_mapping_id": denominator.get("mapping_id"),
        "denominator_mapping_version": denominator.get("mapping_version"),
        "denominator_metric_code": denominator.get("metric_code"),
        "denominator_source_fact_version_id": denominator.get(
            "source_fact_version_id"
        ),
        "denominator_source_snapshot_id": denominator.get("source_snapshot_id"),
        "denominator_unit": denominator.get("base_unit"),
        "denominator_value": denominator_value,
        "fiscal_period": numerator.get("fiscal_period"),
        "fiscal_year": numerator.get("fiscal_year"),
        "numerator_accession_number": numerator.get("accession_number"),
        "numerator_available_at": numerator.get("available_at"),
        "numerator_available_precision": numerator.get("available_precision"),
        "numerator_fundamental_version_id": numerator.get(
            "fundamental_version_id"
        ),
        "numerator_mapping_id": numerator.get("mapping_id"),
        "numerator_mapping_version": numerator.get("mapping_version"),
        "numerator_metric_code": numerator.get("metric_code"),
        "numerator_source_fact_version_id": numerator.get(
            "source_fact_version_id"
        ),
        "numerator_source_snapshot_id": numerator.get("source_snapshot_id"),
        "numerator_unit": numerator.get("base_unit"),
        "numerator_value": numerator_value,
        "period_semantics": period_semantics,
        "ratio_code": ratio_code,
        "ratio_value": numerator_value / denominator_value,
        "reference_period_end": numerator.get("reference_period_end"),
        "reference_period_start": numerator.get("reference_period_start"),
    }


def _ratio_pair(
    *,
    ratio_code: str,
    numerator: Mapping[str, object],
    denominator: Mapping[str, object],
    semantics: str,
) -> tuple[dict[str, Scalar] | None, str | None, str | None]:
    if (
        numerator.get("value_state") not in {"observed", "present"}
        or denominator.get("value_state") not in {"observed", "present"}
        or not isinstance(numerator.get("value"), Decimal)
        or not isinstance(denominator.get("value"), Decimal)
    ):
        return (
            None,
            "non_observed_value",
            "A required retained source value is missing or non-observed.",
        )
    if (
        numerator.get("base_unit") != "USD"
        or denominator.get("base_unit") != "USD"
        or numerator.get("reference_period_end")
        != denominator.get("reference_period_end")
        or numerator.get("fiscal_year") != denominator.get("fiscal_year")
        or numerator.get("fiscal_period") != denominator.get("fiscal_period")
    ):
        return (
            None,
            "incompatible_period_or_unit",
            "Required reviewed facts do not have compatible period semantics or USD units.",
        )
    numerator_start = numerator.get("reference_period_start")
    denominator_start = denominator.get("reference_period_start")
    if semantics == "duration":
        compatible = (
            numerator_start is not None
            and numerator_start == denominator_start
        )
        period_semantics = "same_duration_start_and_end"
    else:
        compatible = numerator_start is None and denominator_start is None
        period_semantics = "same_instant_end"
    if not compatible:
        return (
            None,
            "incompatible_period_or_unit",
            "Required reviewed facts do not have compatible period semantics or USD units.",
        )
    denominator_value = denominator["value"]
    assert isinstance(denominator_value, Decimal)
    if denominator_value == 0:
        return (
            None,
            "zero_denominator",
            "The retained denominator is zero, so the ratio is unavailable.",
        )
    return (
        _ratio_record(
            ratio_code=ratio_code,
            numerator=numerator,
            denominator=denominator,
            period_semantics=period_semantics,
        ),
        None,
        None,
    )


def derive_company_fundamental_ratios(
    rows: tuple[Mapping[str, object], ...],
    *,
    ratio_codes: tuple[str, ...],
) -> tuple[tuple[dict[str, Scalar], ...], tuple[ExclusionV1, ...]]:
    """Derive only reviewed same-period SEC fact ratios.

    This deliberately does not annualize, average balances, calendarize, or
    fill missing data. A missing, incompatible, or zero-base ratio is emitted
    as a typed exclusion rather than guessed.
    """

    if (
        not ratio_codes
        or len(ratio_codes) != len(set(ratio_codes))
        or any(code not in _RATIO_REQUIREMENTS for code in ratio_codes)
    ):
        raise ValidationError("Company fundamental ratio code is unsupported")
    required_metric_codes = {
        metric_code
        for ratio_code in ratio_codes
        for metric_code in _RATIO_REQUIREMENTS[ratio_code][:2]
    }
    by_period: dict[str, dict[str, Mapping[str, object]]] = {}
    for row in rows:
        metric_code = row.get("metric_code")
        if not isinstance(metric_code, str) or metric_code not in required_metric_codes:
            continue
        period_end = row.get("reference_period_end")
        if not isinstance(period_end, str) or not period_end:
            raise ValidationError("Company fundamental period is invalid")
        period_rows = by_period.setdefault(period_end, {})
        if metric_code in period_rows:
            raise ValidationError("Company fundamental ratio source is ambiguous")
        period_rows[metric_code] = row

    records: list[dict[str, Scalar]] = []
    exclusions: list[ExclusionV1] = []
    for ratio_code in sorted(ratio_codes):
        numerator_code, denominator_code, semantics = _RATIO_REQUIREMENTS[ratio_code]
        candidate_periods = tuple(
            sorted(
                period
                for period, period_rows in by_period.items()
                if numerator_code in period_rows or denominator_code in period_rows
            )
        )
        if not candidate_periods:
            exclusions.append(
                ExclusionV1(
                    code="not_established",
                    subject_id=ratio_code,
                    reason="Required retained reviewed facts are unavailable.",
                )
            )
            continue
        for period_end in candidate_periods:
            period_rows = by_period[period_end]
            numerator = period_rows.get(numerator_code)
            denominator = period_rows.get(denominator_code)
            subject_id = f"{ratio_code}:{period_end}"
            if numerator is None or denominator is None:
                missing = (
                    numerator_code if numerator is None else denominator_code
                )
                exclusions.append(
                    ExclusionV1(
                        code="missing_required_metric",
                        subject_id=subject_id,
                        reason=f"Required retained reviewed metric is unavailable: {missing}.",
                    )
                )
                continue
            record, exclusion_code, exclusion_reason = _ratio_pair(
                ratio_code=ratio_code,
                numerator=numerator,
                denominator=denominator,
                semantics=semantics,
            )
            if record is not None:
                records.append(record)
                continue
            assert exclusion_code is not None and exclusion_reason is not None
            exclusions.append(
                ExclusionV1(
                    code=exclusion_code,
                    subject_id=subject_id,
                    reason=exclusion_reason,
                )
            )
    records.sort(
        key=lambda record: (
            str(record["reference_period_end"]),
            str(record["ratio_code"]),
        )
    )
    if len(exclusions) > 1_000:
        raise ResourceLimitError("Company fundamental-ratio exclusions exceed the result limit")
    return tuple(records), tuple(exclusions)


def _ratio_lineage(records: tuple[Mapping[str, Scalar], ...]) -> tuple[LineageRef, ...]:
    lineage: list[LineageRef] = []
    seen: set[str] = set()
    for record in records:
        for prefix in ("numerator", "denominator"):
            fundamental_version_id = record.get(f"{prefix}_fundamental_version_id")
            if (
                fundamental_version_id is None
                or str(fundamental_version_id) in seen
            ):
                continue
            seen.add(str(fundamental_version_id))
            source_snapshot_id = record.get(f"{prefix}_source_snapshot_id")
            lineage.append(
                LineageRef(
                    dataset_id="fixture.company.fundamentals",
                    store_role="company",
                    semantic_id=str(fundamental_version_id),
                    snapshot_id=(
                        None
                        if source_snapshot_id is None
                        else str(source_snapshot_id)
                    ),
                    canonical_version_id=str(fundamental_version_id),
                )
            )
    if len(lineage) > 1_000:
        raise ResourceLimitError(
            "Company fundamental-ratio lineage exceeds the result limit"
        )
    return tuple(lineage)
def invoke_company_fundamentals(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Read raw normalized SEC facts for one exact CIK."""

    if name != TOOL_NAME:
        raise LookupError("Company fundamentals operation is not registered")
    if not isinstance(arguments, CompanyFundamentalsArgumentsV2):
        raise ValidationError("Company fundamentals requires typed v2 arguments")
    selected_codes = _validate_arguments(arguments)
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=0,
        operations=arguments.limit,
    )
    query = CompanyStage4Query(
        cik=arguments.cik,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        limit=_MAX_REPOSITORY_LIMIT,
    )
    rows = tuple(
        CompanyStage4Repository(context.store_map, registry).get_fundamentals(
            query,
            metric_codes=tuple(METRIC_IDS_BY_CODE[code] for code in selected_codes),
        )
    )
    rows = tuple(
        row
        for row in rows
        if (
            arguments.start_date is None
            or str(row["reference_period_end"]) >= arguments.start_date
        )
        and (
            arguments.end_date is None
            or str(row["reference_period_end"]) <= arguments.end_date
        )
    )
    if len(rows) > arguments.limit:
        raise ResourceLimitError("Company fundamentals exceed the query limit")
    metric_codes = tuple(_metric_code_for(row) for row in rows)
    source_warning_messages = _source_warning_messages(rows)
    point_in_time_status, unsafe_reasons = _point_in_time_selection(
        mode=arguments.mode,
        source_warning_messages=source_warning_messages,
    )
    cutoff = query.cutoff
    records = records_from_mappings(
        "company_fundamental",
        tuple(
            _record_fields(row, metric_code=metric_code)
            for row, metric_code in zip(rows, metric_codes, strict=True)
        ),
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="normalized_company_fundamental_selection",
                message=(
                    "Returned retained normalized SEC facts; no ratios, TTM "
                    "values, calendarization, or inferred adjustments were made."
                ),
                metrics=fields_from_mapping(
                    {
                        "actual_mode": arguments.mode,
                        "availability_basis": "source_evidenced",
                        "cutoff": None if cutoff is None else cutoff.raw,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": arguments.date_only_policy,
                        "metric_codes": ",".join(selected_codes),
                        "period_range_rule": "reference_period_end",
                        "point_in_time_status": point_in_time_status,
                        "requested_end_date": arguments.end_date,
                        "requested_mode": arguments.mode,
                        "requested_start_date": arguments.start_date,
                        "returned_count": len(records),
                        "selected_accession_numbers": dumps_strict(
                            [str(row["accession_number"]) for row in rows]
                        ),
                        "selected_fundamental_version_ids": dumps_strict(
                            [str(row["fundamental_version_id"]) for row in rows]
                        ),
                        "selected_source_fact_version_ids": dumps_strict(
                            [str(row["source_fact_version_id"]) for row in rows]
                        ),
                        "selected_source_snapshot_ids": dumps_strict(
                            [str(row["source_snapshot_id"]) for row in rows]
                        ),
                        "source_warning_count": len(source_warning_messages),
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                    }
                ),
            ),
        ),
        warnings=_warnings(source_warning_messages),
        lineage=_lineage(rows),
        truncation=TruncationV1(
            applied=False,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=len(records),
            has_more=False,
        ),
    )



def invoke_company_fundamentals_v21(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Compute only transparent same-period ratios from reviewed SEC facts."""

    if name != TOOL_NAME:
        raise LookupError("Company fundamentals operation is not registered")
    if not isinstance(arguments, CompanyFundamentalRatiosArgumentsV21):
        raise ValidationError(
            "Company fundamental ratios require typed v2.1 arguments"
        )
    ratio_codes = _validate_ratio_arguments(arguments)
    required_metric_codes = tuple(
        sorted(
            {
                metric_code
                for ratio_code in ratio_codes
                for metric_code in _RATIO_REQUIREMENTS[ratio_code][:2]
            }
        )
    )
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=0,
        operations=arguments.limit,
    )
    query = CompanyStage4Query(
        cik=arguments.cik,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        limit=_MAX_REPOSITORY_LIMIT,
    )
    raw_rows = CompanyStage4Repository(
        context.store_map, registry
    ).get_fundamentals(
        query,
        metric_codes=tuple(
            METRIC_IDS_BY_CODE[metric_code]
            for metric_code in required_metric_codes
        ),
    )
    rows = tuple(
        {
            **row,
            "metric_code": _metric_code_for(row),
        }
        for row in raw_rows
        if (
            arguments.start_date is None
            or str(row["reference_period_end"]) >= arguments.start_date
        )
        and (
            arguments.end_date is None
            or str(row["reference_period_end"]) <= arguments.end_date
        )
    )
    source_warning_messages = _source_warning_messages(rows)
    point_in_time_status, unsafe_reasons = _point_in_time_selection(
        mode=arguments.mode,
        source_warning_messages=source_warning_messages,
    )
    all_records, exclusions = derive_company_fundamental_ratios(
        rows,
        ratio_codes=ratio_codes,
    )
    records = all_records[: arguments.limit]
    cutoff = query.cutoff
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok" if all_records else "not_established",
        records=records_from_mappings("company_fundamental_ratio", records),
        diagnostics=(
            DiagnosticV1(
                code="normalized_company_fundamental_ratios",
                message=(
                    "Computed only same-period divisions of retained reviewed "
                    "SEC facts; no TTM, calendarization, averaging, or imputation "
                    "was applied."
                ),
                metrics=fields_from_mapping(
                    {
                        "actual_mode": arguments.mode,
                        "availability_basis": "source_evidenced",
                        "cutoff": None if cutoff is None else cutoff.raw,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": arguments.date_only_policy,
                        "incompatible_period_or_unit_count": sum(
                            item.code == "incompatible_period_or_unit"
                            for item in exclusions
                        ),
                        "missing_required_metric_count": sum(
                            item.code == "missing_required_metric"
                            for item in exclusions
                        ),
                        "non_observed_value_count": sum(
                            item.code == "non_observed_value"
                            for item in exclusions
                        ),
                        "period_range_rule": "reference_period_end",
                        "point_in_time_status": point_in_time_status,
                        "ratio_codes": ",".join(ratio_codes),
                        "requested_end_date": arguments.end_date,
                        "requested_mode": arguments.mode,
                        "requested_start_date": arguments.start_date,
                        "returned_count": len(records),
                        "source_fact_count": len(rows),
                        "source_warning_count": len(source_warning_messages),
                        "total_compatible_ratio_count": len(all_records),
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                        "zero_denominator_count": sum(
                            item.code == "zero_denominator" for item in exclusions
                        ),
                    }
                ),
            ),
        ),
        warnings=_warnings(source_warning_messages),
        exclusions=exclusions,
        lineage=_ratio_lineage(records),
        truncation=TruncationV1(
            applied=len(all_records) > arguments.limit,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=len(all_records),
            has_more=len(all_records) > arguments.limit,
        ),
    )

__all__ = (
    "METRIC_IDS_BY_CODE",
    "OPERATION_VERSION",
    "OPERATION_VERSION_V21",
    "TOOL_NAME",
    "derive_company_fundamental_ratios",
    "invoke_company_fundamentals",
    "invoke_company_fundamentals_v21",
)
