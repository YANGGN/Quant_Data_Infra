"""Composable Stage 10 return-series statistics for explicit public v2 tools.

These operations never open a store.  They consume the exact typed series
emitted by the Stage 10 market-return v2 tools, revalidate its return and
point-in-time contracts, and then invoke the shared analytical primitives.
"""

from __future__ import annotations

import copy
import hashlib
from decimal import Decimal
from typing import Any, Mapping, Sequence

from quant_data.contracts import (
    ExclusionV1,
    LineageRef,
    Observation,
    TimeSeries,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)

from .analytics import align_series, correlate_series, describe_series
from .arguments import (
    Stage10MarketAlignArgumentsV2,
    Stage10MarketCorrelationArgumentsV2,
    Stage10MarketDescribeArgumentsV2,
)
from .context import ToolExecutionContext
from .market_returns import (
    RETURN_PRIMITIVE,
    RETURN_PRIMITIVE_VERSION,
    Stage10MarketQualityReport,
    audit_stage10_market_return,
    stage10_market_return_series_schema,
)
from .results import DiagnosticV1, QueryResult, RecordV1, fields_from_mapping


VERSIONED_STAGE10_TIMESERIES_TOOLS = (
    "timeseries.describe",
    "timeseries.align",
    "timeseries.correlation",
)

_COMPATIBILITY_FIELDS = (
    ("metadata", "frequency"),
    ("metadata", "unit"),
    ("metadata", "value_representation"),
    ("metadata", "scale"),
    ("metadata", "provider"),
    ("metadata", "price_variant"),
    ("metadata", "currency_segment"),
    ("metadata", "observation_field"),
    ("metadata", "availability_basis"),
    ("metadata", "horizon_basis"),
    ("metadata", "return_definition"),
    ("metadata", "return_direction"),
    ("metadata", "return_method"),
    ("metadata", "return_horizon"),
    ("metadata", "return_target_status"),
    ("audit", "mode"),
    ("audit", "requested_mode"),
    ("audit", "actual_mode"),
    ("audit", "cutoff"),
    ("audit", "cutoff_precision"),
    ("audit", "date_only_policy"),
    ("audit", "availability_basis"),
    ("audit", "point_in_time_status"),
    ("audit", "point_in_time_scope"),
    ("audit", "unsafe_reasons"),
    ("audit", "primitive"),
    ("audit", "primitive_version"),
    ("provenance", "registry_revision"),
)

_EXACT_METADATA = {
    "provider": "fmp",
    "frequency": "daily",
    "unit": "fraction",
    "value_representation": "return",
    "scale": "1",
    "price_variant": "fmp_full_eod_v1",
    "currency_segment": "provider_native",
    "observation_field": "close",
    "availability_basis": "local_capture",
    "horizon_basis": "observed_rows",
    "session_calendar_status": "not_established",
    "adjustment_status": "not_established",
    "return_definition": "close_to_close",
}
_EXACT_AUDIT = {
    "availability_basis": "local_capture",
    "period_range_rule": "trade_date",
    "provider": "fmp",
    "price_variant": "fmp_full_eod_v1",
    "currency_segment": "provider_native",
    "observation_field": "close",
    "horizon_basis": "observed_rows",
    "session_calendar_status": "not_established",
    "gap_fill_policy": "none",
    "primitive": RETURN_PRIMITIVE,
    "primitive_version": RETURN_PRIMITIVE_VERSION,
}
_EXACT_PROVENANCE = {
    "dataset_id": "market.stage10.daily_prices",
    "evidence_dataset_id": "market.stage10.source_evidence",
    "identity_dataset_id": "market.stage10.instruments",
    "store_role": "market",
}
_STAGE10_MIGRATION_ID = "market:0010_stage10_market_history"
_HEX_CHARACTERS = frozenset("0123456789abcdef")


def stage10_market_statistic_series_schema() -> dict[str, Any]:
    """Return the stricter v2 composition input without changing return v2."""

    schema = copy.deepcopy(stage10_market_return_series_schema())

    def require_nonempty_text(value: Any) -> None:
        if not isinstance(value, dict):
            return
        if value.get("type") == "string":
            value.setdefault("minLength", 1)
        properties = value.get("properties")
        if isinstance(properties, dict):
            for child in properties.values():
                require_nonempty_text(child)
        items = value.get("items")
        if isinstance(items, dict):
            require_nonempty_text(items)

    require_nonempty_text(schema)
    root = schema["properties"]
    observation = root["observations"]["items"]["properties"]
    for name in ("period_start", "period_end"):
        observation[name] = {"type": "string", "format": "date"}
    observation["available_at"] = {"type": "string", "minLength": 1}
    observation["available_precision"] = {
        "type": "string",
        "const": "datetime",
    }
    observation["captured_precision"] = {
        "type": "string",
        "const": "datetime",
    }
    audit = root["audit"]["properties"]
    for name in ("requested_start_date", "requested_end_date"):
        audit[name] = {"type": "string", "format": "date"}
    provenance = root["provenance"]["properties"]
    identity = provenance["store_receipt"]["properties"][
        "selected_instrument_identity"
    ]["properties"]
    identity["captured_precision"] = {
        "type": "string",
        "const": "datetime",
    }
    source_reference = provenance["derivation"]["properties"][
        "source_selection"
    ]["properties"]["observations"]["items"]["properties"]
    for name in ("period_start", "period_end"):
        source_reference[name] = {"type": "string", "format": "date"}
    return schema


def _contract_value(series: TimeSeries, section: str, field: str) -> Any:
    source = getattr(series, section)
    if not isinstance(source, Mapping) or field not in source:
        raise ValidationError("Stage 10 return-series contract is incomplete")
    return source[field]


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Stage 10 statistic input {field} is invalid")
    return value


def _sha256_text(value: Any, field: str) -> str:
    text = _nonempty_text(value, field)
    if len(text) != 64 or any(character not in _HEX_CHARACTERS for character in text):
        raise ValidationError(f"Stage 10 statistic input {field} is invalid")
    return text


def _require_exact_fields(
    source: Mapping[str, Any], expected: Mapping[str, Any], section: str
) -> None:
    for name, value in expected.items():
        if source.get(name) != value:
            raise ValidationError(
                f"Stage 10 statistic input {section} contract is invalid"
            )


def _validate_selected_identity(
    series: TimeSeries,
    *,
    cutoff: TemporalValue | None,
    policy: DateOnlyPolicy,
) -> None:
    receipt = series.provenance.get("store_receipt")
    if not isinstance(receipt, Mapping):
        raise ValidationError("Stage 10 statistic input store receipt is invalid")
    migration_ids = receipt.get("migration_ids")
    if (
        isinstance(migration_ids, (str, bytes))
        or not isinstance(migration_ids, (list, tuple))
        or _STAGE10_MIGRATION_ID not in migration_ids
        or any(not isinstance(value, str) or not value for value in migration_ids)
    ):
        raise ValidationError("Stage 10 statistic input migration receipt is invalid")
    _sha256_text(receipt.get("sha256"), "store receipt digest")
    identity = receipt.get("selected_instrument_identity")
    if not isinstance(identity, Mapping):
        raise ValidationError("Stage 10 statistic input identity receipt is invalid")
    for field in ("instrument_id", "provider_symbol", "asset_type"):
        if identity.get(field) != series.metadata.get(field):
            raise ValidationError("Stage 10 statistic input identity is inconsistent")
    for field in ("display_name", "exchange_code"):
        if identity.get(field) != series.metadata.get(field):
            raise ValidationError("Stage 10 statistic input identity is inconsistent")
    if identity.get("currency_segment") != "provider_native":
        raise ValidationError("Stage 10 statistic input identity is inconsistent")
    _sha256_text(identity.get("identity_seed_sha256"), "identity seed digest")
    _nonempty_text(identity.get("run_id"), "identity run")
    captured_at = _nonempty_text(identity.get("captured_at"), "identity capture")
    if identity.get("captured_precision") != TemporalPrecision.DATETIME.value:
        raise ValidationError("Stage 10 statistic input identity timing is invalid")
    captured = TemporalValue.parse(
        captured_at,
        pointer=(
            "/series/provenance/store_receipt/"
            "selected_instrument_identity/captured_at"
        ),
    )
    if captured.precision is not TemporalPrecision.DATETIME:
        raise ValidationError("Stage 10 statistic input identity timing is invalid")
    if cutoff is not None and not availability_at_or_before(
        captured, cutoff, policy
    ).included:
        raise ValidationError(
            "Stage 10 statistic input identity is unavailable at the cutoff"
        )


def _validate_observations(
    series: TimeSeries,
    *,
    cutoff: TemporalValue | None,
    policy: DateOnlyPolicy,
    requested_start: Any,
    requested_end: Any,
) -> None:
    instrument_id = _nonempty_text(
        series.metadata.get("instrument_id"), "instrument identity"
    )
    expected_dimensions = {
        "instrument_id": instrument_id,
        "provider": "fmp",
        "price_variant": "fmp_full_eod_v1",
        "currency_segment": "provider_native",
        "observation_field": "close",
    }
    transformation_flag = (
        "transformation:return:"
        f"{series.metadata['return_direction']}:"
        f"{series.metadata['return_method']}:"
        f"horizon={series.metadata['return_horizon']}"
    )
    previous_period = None
    for index, observation in enumerate(series.observations):
        pointer = f"/series/observations/{index}"
        period_start = parse_date(
            observation.period_start, pointer=f"{pointer}/period_start"
        )
        period_end = parse_date(
            observation.period_end, pointer=f"{pointer}/period_end"
        )
        if (
            period_start != period_end
            or period_start < requested_start
            or period_end > requested_end
            or (previous_period is not None and period_start <= previous_period)
        ):
            raise ValidationError(
                "Stage 10 statistic input observation periods are invalid"
            )
        previous_period = period_start
        if (
            observation.unit != "fraction"
            or observation.value_representation != "return"
            or observation.scale != "1"
            or dict(observation.dimensions) != expected_dimensions
        ):
            raise ValidationError(
                "Stage 10 statistic input observation contract is invalid"
            )
        for field, value in (
            ("vintage identity", observation.vintage_at),
            ("version identity", observation.version_id),
            ("evidence identity", observation.evidence_id),
            ("snapshot identity", observation.snapshot_id),
            ("run identity", observation.run_id),
        ):
            _nonempty_text(value, field)
        if (
            observation.available_precision != TemporalPrecision.DATETIME.value
            or observation.captured_precision != TemporalPrecision.DATETIME.value
            or not isinstance(observation.available_at, str)
            or observation.available_at != observation.captured_at
        ):
            raise ValidationError(
                "Stage 10 statistic input observation timing is invalid"
            )
        available = TemporalValue.parse(
            observation.available_at, pointer=f"{pointer}/available_at"
        )
        captured = TemporalValue.parse(
            observation.captured_at, pointer=f"{pointer}/captured_at"
        )
        if (
            available.precision is not TemporalPrecision.DATETIME
            or captured.precision is not TemporalPrecision.DATETIME
        ):
            raise ValidationError(
                "Stage 10 statistic input observation timing is invalid"
            )
        if cutoff is not None and not availability_at_or_before(
            available, cutoff, policy
        ).included:
            raise ValidationError(
                "Stage 10 statistic input observation is unavailable at the cutoff"
            )
        flags = observation.quality_flags
        if (
            any(not isinstance(flag, str) or not flag for flag in flags)
            or "availability_basis:local_capture" not in flags
            or transformation_flag not in flags
        ):
            raise ValidationError(
                "Stage 10 statistic input observation lineage flags are invalid"
            )


def _validate_stage10_core(
    series: TimeSeries, report: Stage10MarketQualityReport
) -> None:
    _require_exact_fields(series.metadata, _EXACT_METADATA, "metadata")
    _require_exact_fields(series.audit, _EXACT_AUDIT, "audit")
    _require_exact_fields(series.provenance, _EXACT_PROVENANCE, "provenance")
    for field in ("instrument_id", "provider_symbol", "asset_type"):
        _nonempty_text(series.metadata.get(field), f"metadata {field}")
    _nonempty_text(series.provenance.get("registry_revision"), "registry revision")

    requested_start = parse_date(
        _nonempty_text(series.audit.get("requested_start_date"), "requested start date"),
        pointer="/series/audit/requested_start_date",
    )
    requested_end = parse_date(
        _nonempty_text(series.audit.get("requested_end_date"), "requested end date"),
        pointer="/series/audit/requested_end_date",
    )
    if requested_end < requested_start:
        raise ValidationError("Stage 10 statistic input date range is invalid")
    try:
        policy = DateOnlyPolicy(report.date_only_policy)
    except ValueError as exc:
        raise ValidationError(
            "Stage 10 statistic input date-only policy is invalid"
        ) from exc
    cutoff_precision = series.audit.get("cutoff_precision")
    cutoff: TemporalValue | None = None
    if report.cutoff is not None:
        cutoff = TemporalValue.parse(report.cutoff, pointer="/series/audit/cutoff")
        if cutoff_precision != cutoff.precision.value:
            raise ValidationError(
                "Stage 10 statistic input cutoff precision is inconsistent"
            )
    elif cutoff_precision is not None:
        raise ValidationError(
            "Stage 10 statistic input cutoff precision is inconsistent"
        )
    source_limit = series.audit.get("source_selection_limit")
    source_count = series.audit.get("source_selected_count")
    if (
        isinstance(source_limit, bool)
        or not isinstance(source_limit, int)
        or not 1 <= source_limit <= 10_000
        or isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or not 0 <= source_count <= source_limit
    ):
        raise ValidationError(
            "Stage 10 statistic input source-selection bounds are invalid"
        )
    _validate_selected_identity(series, cutoff=cutoff, policy=policy)
    _validate_observations(
        series,
        cutoff=cutoff,
        policy=policy,
        requested_start=requested_start,
        requested_end=requested_end,
    )


def validate_stage10_return_inputs(
    values: Sequence[TimeSeries],
) -> tuple[Stage10MarketQualityReport, ...]:
    """Require exact, mutually compatible Stage 10 return-series contracts."""

    if isinstance(values, (str, bytes)) or not values or len(values) > 20:
        raise ValidationError("Stage 10 statistics require one to twenty series")
    series = tuple(values)
    if not all(isinstance(item, TimeSeries) for item in series):
        raise ValidationError("Stage 10 statistics require typed TimeSeries inputs")
    if len({item.series_id for item in series}) != len(series):
        raise ValidationError("Stage 10 statistics require distinct series identities")

    reports = tuple(audit_stage10_market_return(item) for item in series)
    for item, report in zip(series, reports, strict=True):
        _validate_stage10_core(item, report)
        if not (
            report.mode == report.requested_mode == report.actual_mode
        ):
            raise ValidationError(
                "Stage 10 statistic input has a contradictory query mode"
            )
        cutoff_precision = _contract_value(item, "audit", "cutoff_precision")
        if report.mode == "latest":
            temporal_contract_valid = (
                report.cutoff is None
                and cutoff_precision is None
                and report.point_in_time_status == "not_applicable"
                and report.point_in_time_scope == "current_stored_knowledge"
            )
        else:
            temporal_contract_valid = (
                report.mode == "as_of"
                and report.cutoff is not None
                and isinstance(cutoff_precision, str)
                and bool(cutoff_precision)
                and report.point_in_time_status == "safe"
                and report.point_in_time_scope == "retained_local_captures"
            )
        if not temporal_contract_valid or report.unsafe_reasons:
            raise ValidationError(
                "Stage 10 statistic input has a contradictory point-in-time contract"
            )
        expected_target = {
            "trailing": "historical_transform",
            "forward": "outcome_label",
        }.get(report.return_direction)
        if expected_target != report.return_target_status:
            raise ValidationError(
                "Stage 10 statistic input has a contradictory return target"
            )
    baseline = series[0]
    for candidate in series[1:]:
        for section, field in _COMPATIBILITY_FIELDS:
            if _contract_value(candidate, section, field) != _contract_value(
                baseline, section, field
            ):
                raise ValidationError(
                    "Stage 10 statistics reject contradictory return or temporal contracts"
                )
    return reports


def _lineage(values: Sequence[TimeSeries]) -> tuple[LineageRef, ...]:
    return tuple(
        LineageRef(
            dataset_id="market.stage10.daily_prices",
            store_role="market",
            semantic_id=item.lineage_digest,
        )
        for item in values
    )


def _warnings(
    values: Sequence[TimeSeries], *extra: str
) -> tuple[WarningV1, ...]:
    codes = {
        code
        for item in values
        for code in item.warnings
        if isinstance(code, str) and code
    }
    codes.update(code for code in extra if isinstance(code, str) and code)
    if any(item.truncated for item in values):
        codes.add("input_series_truncated")
    if len(values) > 1:
        codes.add("client_composed_snapshot_coherence_not_established")
    if values[0].metadata.get("return_direction") == "forward":
        codes.add("forward_return_inputs_are_outcome_labels")
    return tuple(
        WarningV1(
            code=code,
            message=f"Stage 10 composable statistic warning: {code}.",
        )
        for code in sorted(codes)
    )


def _common_metrics(
    values: Sequence[TimeSeries], reports: Sequence[Stage10MarketQualityReport]
) -> dict[str, Any]:
    first = reports[0]
    metrics: dict[str, Any] = {
        "actual_mode": first.actual_mode,
        "availability_basis": first.availability_basis,
        "cutoff": first.cutoff,
        "cutoff_precision": values[0].audit.get("cutoff_precision"),
        "date_only_policy": first.date_only_policy,
        "gap_fill_policy": values[0].audit.get("gap_fill_policy"),
        "horizon_basis": first.horizon_basis,
        "input_series_count": len(values),
        "mode": first.mode,
        "point_in_time_status": first.point_in_time_status,
        "point_in_time_scope": first.point_in_time_scope,
        "registry_revision": values[0].provenance.get("registry_revision"),
        "requested_mode": first.requested_mode,
        "return_definition": first.return_definition,
        "return_direction": first.return_direction,
        "return_horizon": first.return_horizon,
        "return_method": first.return_method,
        "return_target_status": first.return_target_status,
        "truncated_input_count": sum(item.truncated for item in values),
    }
    for index, (series, report) in enumerate(zip(values, reports, strict=True)):
        metrics[f"input_lineage_digest_{index}"] = series.lineage_digest
        metrics[f"input_observation_count_{index}"] = report.observation_count
        metrics[f"input_series_id_{index}"] = series.series_id
    return metrics


def _describe(
    arguments: Stage10MarketDescribeArgumentsV2,
) -> QueryResult:
    series = arguments.series
    reports = validate_stage10_return_inputs((series,))
    if len(series.observations) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 description exceeds its declared observation limit"
        )
    summary = describe_series(series)
    established = summary["status"] == "established"
    reason = None if established else "no_nonmissing_observations"
    record = RecordV1(
        "stage10_return_description",
        fields_from_mapping(
            {
                "count": int(summary["count"]),
                "first_period": (
                    series.observations[0].period_start
                    if series.observations
                    else None
                ),
                "last_period": (
                    series.observations[-1].period_end
                    if series.observations
                    else None
                ),
                "maximum": summary["maximum"],
                "mean": summary["mean"],
                "minimum": summary["minimum"],
                "missing_count": int(summary["missing_count"]),
                "nonmissing_count": int(summary["nonmissing_count"]),
                "sample_variance": summary["sample_variance"],
                "status": str(summary["status"]),
            }
        ),
    )
    summary_warnings = tuple(
        item for item in summary["warnings"] if isinstance(item, str)
    )
    return QueryResult(
        tool="timeseries.describe",
        status="ok" if established else "not_established",
        records=(record,),
        diagnostics=(
            DiagnosticV1(
                code="stage10_return_description",
                message="The shared descriptive-statistics primitive completed.",
                metrics=fields_from_mapping(
                    {
                        **_common_metrics((series,), reports),
                        "input_lineage_digest": series.lineage_digest,
                    }
                ),
            ),
        ),
        warnings=_warnings((series,), *summary_warnings),
        exclusions=(
            ()
            if reason is None
            else (
                ExclusionV1(
                    code="not_established",
                    subject_id=series.series_id,
                    reason=reason,
                ),
            )
        ),
        lineage=_lineage((series,)),
        truncation=TruncationV1(False, arguments.limit, 1, 1, False),
    )


def _align(arguments: Stage10MarketAlignArgumentsV2) -> QueryResult:
    values = arguments.series
    reports = validate_stage10_return_inputs(values)
    aligned = align_series(values, join=arguments.join)
    rows = tuple(aligned["rows"])
    if len(rows) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 alignment exceeds its declared output-row limit"
        )
    records: list[RecordV1] = []
    missing_cell_count = 0
    complete_row_count = 0
    for row in rows:
        fields: dict[str, Any] = {
            "period_end": row["period_end"],
            "period_start": row["period_start"],
        }
        row_complete = True
        for index, (value, reason) in enumerate(
            zip(row["values"], row["missing_reasons"], strict=True)
        ):
            fields[f"missing_reason_{index}"] = reason
            fields[f"value_{index}"] = value
            if value is None:
                missing_cell_count += 1
                row_complete = False
        complete_row_count += int(row_complete)
        records.append(
            RecordV1("stage10_aligned_return", fields_from_mapping(fields))
        )
    established = bool(rows)
    return QueryResult(
        tool="timeseries.align",
        status="ok" if established else "not_established",
        records=tuple(records),
        diagnostics=(
            DiagnosticV1(
                code="stage10_return_alignment",
                message="The shared alignment primitive completed without filling values.",
                metrics=fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "aligned_row_count": len(rows),
                        "complete_row_count": complete_row_count,
                        "join": arguments.join,
                        "missing_cell_count": missing_cell_count,
                    }
                ),
            ),
        ),
        warnings=_warnings(values),
        exclusions=(
            ()
            if established
            else (
                ExclusionV1(
                    code="not_established",
                    reason="no_aligned_periods",
                ),
            )
        ),
        lineage=_lineage(values),
        truncation=TruncationV1(
            False, arguments.limit, len(records), len(records), False
        ),
    )


def _correlation(
    arguments: Stage10MarketCorrelationArgumentsV2,
) -> QueryResult:
    values = arguments.series
    reports = validate_stage10_return_inputs(values)
    if any(item.truncated for item in values):
        raise ValidationError(
            "Stage 10 correlation rejects truncated statistical samples"
        )
    if any(len(item.observations) > arguments.limit for item in values):
        raise ResourceLimitError(
            "Stage 10 correlation exceeds its declared observation limit"
        )
    result = correlate_series(values[0], values[1], join="outer")
    established = result["status"] == "established"
    reason = result.get("reason")
    summary = {
        name: item
        for name, item in result.items()
        if isinstance(item, (str, Decimal, int, bool, type(None)))
        and not isinstance(item, float)
    }
    result_warnings = tuple(
        item
        for item in result.get("warnings", ())
        if isinstance(item, str) and item
    )
    return QueryResult(
        tool="timeseries.correlation",
        status="ok" if established else "not_established",
        records=(
            RecordV1(
                "stage10_return_correlation", fields_from_mapping(summary)
            ),
        ),
        diagnostics=(
            DiagnosticV1(
                code="stage10_return_correlation",
                message="Pearson correlation used complete pairs after outer alignment.",
                metrics=fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "alignment_policy": "outer_complete_pairs",
                        "excluded_count": int(result.get("excluded_count", 0)),
                        "sample_size": int(result.get("sample_size", 0)),
                    }
                ),
            ),
        ),
        warnings=_warnings(values, *result_warnings),
        exclusions=(
            ()
            if established
            else (
                ExclusionV1(
                    code="not_established",
                    reason=(
                        str(reason)
                        if isinstance(reason, str) and reason
                        else "correlation_not_established"
                    ),
                ),
            )
        ),
        lineage=_lineage(values),
        truncation=TruncationV1(False, arguments.limit, 1, 1, False),
    )


def invoke_stage10_market_statistic(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> QueryResult:
    """Invoke one closed v2 statistic over caller-supplied typed series."""

    if name not in VERSIONED_STAGE10_TIMESERIES_TOOLS:
        raise LookupError("Stage 10 time-series statistic is not registered")
    expected_graph = f"tool_platform.{name}.v2"
    if context.operation_graph_id != expected_graph or context.tool_version != "2.0.0":
        raise LookupError("Selected Stage 10 statistic operation graph is invalid")
    expected_type = {
        "timeseries.describe": Stage10MarketDescribeArgumentsV2,
        "timeseries.align": Stage10MarketAlignArgumentsV2,
        "timeseries.correlation": Stage10MarketCorrelationArgumentsV2,
    }[name]
    if not isinstance(arguments, expected_type):
        raise ValidationError("Stage 10 statistic arguments use the wrong contract")
    raw_series = arguments["series"]
    values = raw_series if isinstance(raw_series, tuple) else (raw_series,)
    rows = max((len(item.observations) for item in values), default=0)
    context.checkpoint()
    context.budget.require(
        rows=max(rows, arguments["limit"]),
        series=len(values),
        operations=max(rows, arguments["limit"]) * max(len(values), 1) ** 2,
    )
    result = {
        "timeseries.describe": _describe,
        "timeseries.align": _align,
        "timeseries.correlation": _correlation,
    }[name](arguments)
    context.checkpoint()
    return result


def stage10_market_return_example_series(symbol: str) -> TimeSeries:
    """Build one self-consistent, executable offline public-contract example."""

    if not isinstance(symbol, str) or not symbol:
        raise ValidationError("Example symbol must be nonempty")
    token = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    source_lineage = hashlib.sha256(f"source:{symbol}".encode("utf-8")).hexdigest()
    dates = ("2026-08-10", "2026-08-11", "2026-08-12")
    source_observations = [
        {
            "period_start": day,
            "period_end": day,
            "version_id": f"source-version:{token}:{index}",
            "evidence_id": f"source-evidence:{token}:{index}",
            "snapshot_id": f"source-snapshot:{token}:{index}",
            "run_id": f"source-run:{token}:{index}",
        }
        for index, day in enumerate(dates)
    ]
    source_selection = {
        "series_id": f"example:stage10_price:{symbol.lower()}",
        "lineage_digest": source_lineage,
        "selected_count": len(source_observations),
        "observations": source_observations,
    }
    source_selection_sha256 = hashlib.sha256(
        dumps_strict(source_selection).encode("utf-8")
    ).hexdigest()
    instrument_id = f"example_stage10_{symbol.lower()}"
    values = (None, Decimal("1"), Decimal("-1"))
    missing = ("insufficient_history", None, None)
    observations = tuple(
        Observation(
            period_start=day,
            period_end=day,
            value=values[index],
            missing_reason=missing[index],
            unit="fraction",
            value_representation="return",
            scale="1",
            vintage_at=f"derived:{token}:{index}",
            available_at="2026-08-15T01:00:00Z",
            available_precision="datetime",
            captured_at="2026-08-15T01:00:00Z",
            captured_precision="datetime",
            version_id=f"derived-version:{token}:{index}",
            evidence_id=f"derived-evidence:{token}:{index}",
            snapshot_id=f"derived-snapshot:{token}:{index}",
            run_id=f"derived-run:{token}:{index}",
            dimensions={
                "instrument_id": instrument_id,
                "provider": "fmp",
                "price_variant": "fmp_full_eod_v1",
                "currency_segment": "provider_native",
                "observation_field": "close",
            },
            quality_flags=(
                "availability_basis:local_capture",
                "transformation:return:trailing:simple:horizon=1",
            ),
        )
        for index, day in enumerate(dates)
    )
    selected_identity = {
        "instrument_id": instrument_id,
        "provider_symbol": symbol,
        "asset_type": "equity",
        "display_name": f"{symbol} example",
        "exchange_code": "XNAS",
        "currency_segment": "provider_native",
        "identity_seed_sha256": token,
        "captured_at": "2026-08-15T01:00:00Z",
        "captured_precision": "datetime",
        "run_id": f"identity-run:{token}",
    }
    series = TimeSeries(
        series_id=f"example:stage10_return:{symbol.lower()}",
        metadata={
            "instrument_id": instrument_id,
            "provider_symbol": symbol,
            "asset_type": "equity",
            "display_name": f"{symbol} example",
            "exchange_code": "XNAS",
            "provider": "fmp",
            "frequency": "daily",
            "unit": "fraction",
            "value_representation": "return",
            "scale": "1",
            "price_variant": "fmp_full_eod_v1",
            "currency_segment": "provider_native",
            "observation_field": "close",
            "availability_basis": "local_capture",
            "horizon_basis": "observed_rows",
            "session_calendar_status": "not_established",
            "adjustment_status": "not_established",
            "return_definition": "close_to_close",
            "return_direction": "trailing",
            "return_method": "simple",
            "return_horizon": 1,
            "return_target_status": "historical_transform",
        },
        observations=observations,
        warnings=(
            "adjustment_and_total_return_semantics_not_established",
            "local_capture_availability",
            "observed_row_horizon_not_trading_day_horizon",
        ),
        audit={
            "mode": "latest",
            "requested_mode": "latest",
            "actual_mode": "latest",
            "cutoff": None,
            "cutoff_precision": None,
            "date_only_policy": "completed_date",
            "availability_basis": "local_capture",
            "period_range_rule": "trade_date",
            "requested_start_date": dates[0],
            "requested_end_date": dates[-1],
            "provider": "fmp",
            "price_variant": "fmp_full_eod_v1",
            "currency_segment": "provider_native",
            "observation_field": "close",
            "horizon_basis": "observed_rows",
            "session_calendar_status": "not_established",
            "point_in_time_status": "not_applicable",
            "point_in_time_scope": "current_stored_knowledge",
            "unsafe_reasons": [],
            "limit": 3,
            "selected_count": 3,
            "missing_count": 1,
            "truncated": False,
            "return_definition": "close_to_close",
            "return_direction": "trailing",
            "return_method": "simple",
            "return_horizon": 1,
            "gap_fill_policy": "none",
            "source_selection_limit": 3,
            "source_selected_count": 3,
            "source_lineage_digest": source_lineage,
            "primitive": RETURN_PRIMITIVE,
            "primitive_version": RETURN_PRIMITIVE_VERSION,
        },
        provenance={
            "dataset_id": "market.stage10.daily_prices",
            "evidence_dataset_id": "market.stage10.source_evidence",
            "identity_dataset_id": "market.stage10.instruments",
            "store_role": "market",
            "registry_revision": "2.33.0",
            "store_receipt": {
                "migration_ids": ["market:0010_stage10_market_history"],
                "selected_instrument_identity": selected_identity,
                "sha256": hashlib.sha256(
                    dumps_strict(selected_identity).encode("utf-8")
                ).hexdigest(),
            },
            "derivation": {
                "primitive": RETURN_PRIMITIVE,
                "primitive_version": RETURN_PRIMITIVE_VERSION,
                "source_selection": source_selection,
                "source_selection_sha256": source_selection_sha256,
            },
        },
        truncated=False,
    )
    audit_stage10_market_return(series)
    return series


def stage10_market_return_example(symbol: str) -> dict[str, Any]:
    """Return the executable example using only standard JSON scalar types."""

    payload = stage10_market_return_example_series(symbol).to_primitive()
    for observation in payload["observations"]:
        value = observation["value"]
        if isinstance(value, Decimal):
            if value != value.to_integral_value():  # pragma: no cover - fixture invariant
                raise AssertionError("Stage 10 example values must be integral")
            observation["value"] = int(value)
    return payload


__all__ = (
    "VERSIONED_STAGE10_TIMESERIES_TOOLS",
    "invoke_stage10_market_statistic",
    "stage10_market_return_example",
    "stage10_market_return_example_series",
    "stage10_market_statistic_series_schema",
    "validate_stage10_return_inputs",
)
