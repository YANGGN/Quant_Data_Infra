"""Typed public adapters for the retained Stage 11 EIA energy histories."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final, Mapping, Sequence

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.macro.energy_access import (
    EnergyCanonicalRepository,
    EnergyRetailQuery,
    EnergySelection,
    EnergyWeeklyQuery,
    RETAIL_DATASET_IDS,
    WEEKLY_DATASET_IDS,
)
from quant_data.registry import Registry

from .arguments import (
    EnergyElectricityRetailArgumentsV2,
    EnergyWeeklyFundamentalsArgumentsV2,
)
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, fields_from_mapping, records_from_mappings


ELECTRICITY_RETAIL_TOOL_NAME: Final = "energy.get_electricity_retail_sales"
WEEKLY_FUNDAMENTALS_TOOL_NAME: Final = "energy.get_weekly_fundamentals"
OPERATION_VERSION: Final = "2.0.0"
OPERATION_VERSION_V21: Final = "2.1.0"
_RETAIL_SEASONALITY_LAG: Final = 12
_WEEKLY_SEASONALITY_LAG: Final = 52


def _lineage(
    rows: tuple[dict[str, Any], ...],
    *,
    dataset_id: str,
) -> tuple[LineageRef, ...]:
    return tuple(
        LineageRef(
            dataset_id=dataset_id,
            store_role="macro",
            semantic_id=str(row["version_id"]),
            evidence_id=str(row["artifact_id"]),
            snapshot_id=str(row["snapshot_id"]),
            canonical_version_id=str(row["version_id"]),
        )
        for row in rows
    )


def _warnings(messages: tuple[str, ...]) -> tuple[WarningV1, ...]:
    return tuple(
        WarningV1(
            code=message,
            message=f"Energy source-selection warning: {message}.",
        )
        for message in messages
    )


def _result(
    *,
    name: str,
    selection: EnergySelection,
    mode: str,
    as_of: str | None,
    date_only_policy: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
    record_type: str,
    lineage_dataset_id: str,
) -> QueryResult:
    records = records_from_mappings(record_type, selection.records)
    cutoff_precision = (
        None if as_of is None else ("date" if len(as_of) == 10 else "datetime")
    )
    point_in_time_status = "safe" if mode == "as_of" else "not_applicable"
    return QueryResult(
        tool=name,
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="stage11_energy_selection",
                message=(
                    "Selected only retained Stage 11 EIA observations with "
                    "local-capture availability."
                ),
                metrics=fields_from_mapping(
                    {
                        "actual_mode": mode,
                        "availability_basis": "local_capture",
                        "cutoff": as_of,
                        "cutoff_precision": cutoff_precision,
                        "date_only_policy": date_only_policy,
                        "dataset_ids": dumps_strict(list(selection.dataset_ids)),
                        "point_in_time_status": point_in_time_status,
                        "requested_end_date": end_date,
                        "requested_mode": mode,
                        "requested_start_date": start_date,
                        "returned_count": len(records),
                        "total_selected_count": selection.total_selected_count,
                    }
                ),
            ),
        ),
        warnings=_warnings(selection.warnings),
        lineage=_lineage(selection.records, dataset_id=lineage_dataset_id),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=limit,
            returned_count=len(records),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
        ),
    )



def _period_in_bounds(
    period: str,
    *,
    start_date: str | None,
    end_date: str | None,
    monthly: bool,
) -> bool:
    comparable_period = f"{period}-01" if monthly else period
    return (
        (start_date is None or comparable_period >= start_date)
        and (end_date is None or comparable_period <= end_date)
    )


def derive_energy_seasonality_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    lag_observations: int,
) -> tuple[dict[str, Any], ...]:
    """Compare retained values with the exact earlier source observation.

    This intentionally uses the retained observation order rather than filling
    gaps or constructing a synthetic calendar. An unavailable comparison is
    returned explicitly when the required earlier retained observation does
    not exist.
    """

    if isinstance(lag_observations, bool) or lag_observations < 1:
        raise ValidationError("Energy seasonality lag must be positive")
    records = tuple(rows)
    if not records:
        return ()
    canonical_series_id = str(records[0]["canonical_series_id"])
    unit = str(records[0]["unit"])
    periods: set[str] = set()
    output: list[dict[str, Any]] = []
    for index, current in enumerate(records):
        period = str(current["period"])
        if period in periods:
            raise ValidationError("Energy seasonality source periods must be unique")
        periods.add(period)
        if str(current["canonical_series_id"]) != canonical_series_id:
            raise ValidationError("Energy seasonality requires one canonical series")
        if str(current["unit"]) != unit:
            raise ValidationError("Energy seasonality source units are inconsistent")
        current_level = current["value"]
        if not isinstance(current_level, Decimal):
            raise ValidationError("Stored energy value is invalid")
        reference = (
            records[index - lag_observations]
            if index >= lag_observations
            else None
        )
        fields: dict[str, Any] = {
            "canonical_series_id": canonical_series_id,
            "comparison_lag_observations": lag_observations,
            "comparison_status": "unavailable",
            "current_artifact_id": str(current["artifact_id"]),
            "current_level": current_level,
            "current_snapshot_id": str(current["snapshot_id"]),
            "current_version_id": str(current["version_id"]),
            "level_change": None,
            "metric": current.get("metric"),
            "percent_change": None,
            "period": period,
            "reference_artifact_id": None,
            "reference_level": None,
            "reference_period": None,
            "reference_snapshot_id": None,
            "reference_version_id": None,
            "unavailable_reason": "insufficient_retained_history",
            "unit": unit,
        }
        if reference is not None:
            reference_level = reference["value"]
            if not isinstance(reference_level, Decimal):
                raise ValidationError("Stored energy value is invalid")
            fields.update(
                {
                    "level_change": current_level - reference_level,
                    "reference_artifact_id": str(reference["artifact_id"]),
                    "reference_level": reference_level,
                    "reference_period": str(reference["period"]),
                    "reference_snapshot_id": str(reference["snapshot_id"]),
                    "reference_version_id": str(reference["version_id"]),
                }
            )
            if reference_level == 0:
                fields.update(
                    {
                        "comparison_status": "zero_base",
                        "unavailable_reason": "zero_reference_level",
                    }
                )
            else:
                fields.update(
                    {
                        "comparison_status": "ok",
                        "percent_change": (current_level - reference_level)
                        / reference_level,
                        "unavailable_reason": None,
                    }
                )
        output.append(fields)
    return tuple(output)


def _seasonality_lineage(
    records: Sequence[Mapping[str, Any]],
    *,
    dataset_id: str,
) -> tuple[LineageRef, ...]:
    lineage: list[LineageRef] = []
    seen: set[str] = set()
    for record in records:
        for prefix in ("current", "reference"):
            version_id = record.get(f"{prefix}_version_id")
            if version_id is None or str(version_id) in seen:
                continue
            seen.add(str(version_id))
            lineage.append(
                LineageRef(
                    dataset_id=dataset_id,
                    store_role="macro",
                    semantic_id=str(version_id),
                    evidence_id=(
                        None
                        if record.get(f"{prefix}_artifact_id") is None
                        else str(record[f"{prefix}_artifact_id"])
                    ),
                    snapshot_id=(
                        None
                        if record.get(f"{prefix}_snapshot_id") is None
                        else str(record[f"{prefix}_snapshot_id"])
                    ),
                    canonical_version_id=str(version_id),
                )
            )
    if len(lineage) > 1_000:
        raise ResourceLimitError("Energy seasonality lineage exceeds the result limit")
    return tuple(lineage)


def _seasonality_result(
    *,
    name: str,
    records: Sequence[Mapping[str, Any]],
    source_warning_messages: tuple[str, ...],
    mode: str,
    as_of: str | None,
    date_only_policy: str,
    start_date: str | None,
    end_date: str | None,
    limit: int,
    total_selected_count: int,
    lag_observations: int,
    record_type: str,
    lineage_dataset_id: str,
) -> QueryResult:
    result_records = records_from_mappings(record_type, records)
    cutoff_precision = (
        None if as_of is None else ("date" if len(as_of) == 10 else "datetime")
    )
    statuses = [str(record["comparison_status"]) for record in records]
    unsafe_reasons = tuple(
        message
        for message in source_warning_messages
        if message == "date_only_same_day_intraday_safety_not_established"
    )
    point_in_time_status = "not_applicable" if mode == "latest" else (
        "unsafe" if unsafe_reasons else "safe"
    )
    return QueryResult(
        tool=name,
        records=result_records,
        diagnostics=(
            DiagnosticV1(
                code="stage11_energy_fixed_lag_seasonality",
                message=(
                    "Compared retained same-series EIA levels at a fixed "
                    "observation lag without interpolation or unit conversion."
                ),
                metrics=fields_from_mapping(
                    {
                        "actual_mode": mode,
                        "availability_basis": "local_capture",
                        "comparison_lag_observations": lag_observations,
                        "cutoff": as_of,
                        "cutoff_precision": cutoff_precision,
                        "date_only_policy": date_only_policy,
                        "ok_count": statuses.count("ok"),
                        "point_in_time_status": point_in_time_status,
                        "requested_end_date": end_date,
                        "requested_mode": mode,
                        "requested_start_date": start_date,
                        "returned_count": len(result_records),
                        "total_selected_count": total_selected_count,
                        "unavailable_count": statuses.count("unavailable"),
                        "zero_base_count": statuses.count("zero_base"),
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                    }
                ),
            ),
        ),
        warnings=_warnings(source_warning_messages),
        lineage=_seasonality_lineage(records, dataset_id=lineage_dataset_id),
        truncation=TruncationV1(
            applied=total_selected_count > limit,
            limit=limit,
            returned_count=len(result_records),
            total_known_count=total_selected_count,
            has_more=total_selected_count > limit,
        ),
    )

def invoke_energy_access(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Read one explicitly registered EIA energy-history successor."""

    context.checkpoint()
    if name == ELECTRICITY_RETAIL_TOOL_NAME:
        if not isinstance(arguments, EnergyElectricityRetailArgumentsV2):
            raise ValidationError("Energy retail requires typed v2 arguments")
        query = EnergyRetailQuery(
            metric=arguments.metric,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        )
        context.budget.require(
            rows=query.limit,
            series=0,
            operations=query.limit,
        )
        selection = EnergyCanonicalRepository(
            context.store_map, registry
        ).get_retail(query)
        context.checkpoint()
        return _result(
            name=name,
            selection=selection,
            mode=query.mode,
            as_of=query.as_of,
            date_only_policy=query.date_only_policy.value,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=query.limit,
            record_type="energy_electricity_retail",
            lineage_dataset_id=RETAIL_DATASET_IDS[1],
        )
    if name == WEEKLY_FUNDAMENTALS_TOOL_NAME:
        if not isinstance(arguments, EnergyWeeklyFundamentalsArgumentsV2):
            raise ValidationError("Energy weekly requires typed v2 arguments")
        query = EnergyWeeklyQuery(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        )
        context.budget.require(
            rows=query.limit,
            series=0,
            operations=query.limit,
        )
        selection = EnergyCanonicalRepository(
            context.store_map, registry
        ).get_weekly(query)
        context.checkpoint()
        return _result(
            name=name,
            selection=selection,
            mode=query.mode,
            as_of=query.as_of,
            date_only_policy=query.date_only_policy.value,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=query.limit,
            record_type="energy_weekly_fundamental",
            lineage_dataset_id=WEEKLY_DATASET_IDS[1],
        )
    raise LookupError("Energy operation is not registered")



def invoke_energy_access_v21(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Return fixed-lag retained-row comparisons for the v2.1 energy routes."""

    context.checkpoint()
    repository = EnergyCanonicalRepository(context.store_map, registry)
    if name == ELECTRICITY_RETAIL_TOOL_NAME:
        if not isinstance(arguments, EnergyElectricityRetailArgumentsV2):
            raise ValidationError("Energy retail requires typed v2 arguments")
        query = EnergyRetailQuery(
            metric=arguments.metric,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        )
        context.budget.require(rows=query.limit, series=0, operations=query.limit)
        history = repository.get_retail(
            EnergyRetailQuery(
                metric=query.metric,
                start_date=None,
                end_date=None,
                mode=query.mode,
                as_of=query.as_of,
                date_only_policy=query.date_only_policy,
                limit=1_000,
            )
        )
        if history.truncated:
            raise ResourceLimitError(
                "Energy retail history exceeds the fixed-lag comparison limit"
            )
        derived = derive_energy_seasonality_records(
            history.records,
            lag_observations=_RETAIL_SEASONALITY_LAG,
        )
        selected = tuple(
            record
            for record in derived
            if _period_in_bounds(
                str(record["period"]),
                start_date=query.start_date,
                end_date=query.end_date,
                monthly=True,
            )
        )
        context.checkpoint()
        return _seasonality_result(
            name=name,
            records=selected[: query.limit],
            source_warning_messages=history.warnings,
            mode=query.mode,
            as_of=query.as_of,
            date_only_policy=query.date_only_policy.value,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=query.limit,
            total_selected_count=len(selected),
            lag_observations=_RETAIL_SEASONALITY_LAG,
            record_type="energy_electricity_retail_seasonality",
            lineage_dataset_id=RETAIL_DATASET_IDS[1],
        )
    if name == WEEKLY_FUNDAMENTALS_TOOL_NAME:
        if not isinstance(arguments, EnergyWeeklyFundamentalsArgumentsV2):
            raise ValidationError("Energy weekly requires typed v2 arguments")
        query = EnergyWeeklyQuery(
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        )
        context.budget.require(rows=query.limit, series=0, operations=query.limit)
        history = repository.get_weekly(
            EnergyWeeklyQuery(
                start_date=None,
                end_date=None,
                mode=query.mode,
                as_of=query.as_of,
                date_only_policy=query.date_only_policy,
                limit=5_000,
            )
        )
        if history.truncated:
            raise ResourceLimitError(
                "Energy weekly history exceeds the fixed-lag comparison limit"
            )
        derived = derive_energy_seasonality_records(
            history.records,
            lag_observations=_WEEKLY_SEASONALITY_LAG,
        )
        selected = tuple(
            record
            for record in derived
            if _period_in_bounds(
                str(record["period"]),
                start_date=query.start_date,
                end_date=query.end_date,
                monthly=False,
            )
        )
        context.checkpoint()
        return _seasonality_result(
            name=name,
            records=selected[: query.limit],
            source_warning_messages=history.warnings,
            mode=query.mode,
            as_of=query.as_of,
            date_only_policy=query.date_only_policy.value,
            start_date=query.start_date,
            end_date=query.end_date,
            limit=query.limit,
            total_selected_count=len(selected),
            lag_observations=_WEEKLY_SEASONALITY_LAG,
            record_type="energy_weekly_fundamental_seasonality",
            lineage_dataset_id=WEEKLY_DATASET_IDS[1],
        )
    raise LookupError("Energy operation is not registered")

__all__ = (
    "ELECTRICITY_RETAIL_TOOL_NAME",
    "OPERATION_VERSION",
    "OPERATION_VERSION_V21",
    "WEEKLY_FUNDAMENTALS_TOOL_NAME",
    "derive_energy_seasonality_records",
    "invoke_energy_access",
    "invoke_energy_access_v21",
)
