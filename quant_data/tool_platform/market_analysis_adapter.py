"""Store-free public adapters for the Step 2--4 analytical foundation."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Mapping, Sequence

from quant_data.contracts import ExclusionV1, TimeSeries, TruncationV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)

from .analytics import align_series
from .arguments import (
    Stage10BootstrapArgumentsV1,
    Stage10CovarianceArgumentsV1,
    Stage10DataQualityArgumentsV2,
    Stage10DistributionArgumentsV1,
    Stage10MarketTransformArgumentsV2,
    Stage10PrincipalComponentsArgumentsV1,
)
from .context import ToolExecutionContext
from .data_quality import (
    MAX_DISCONTINUITY_LIMIT,
    DataQualitySeriesSummary,
    audit_time_series_quality,
)
from .general_statistics import (
    bootstrap_confidence_interval,
    covariance_correlation_matrix,
    distribution_diagnostics,
    principal_component_analysis,
)
from .market_returns import (
    RETURN_PRIMITIVE,
    RETURN_PRIMITIVE_VERSION,
    Stage10MarketQualityReport,
)
from .market_statistics import (
    _EXACT_AUDIT,
    _EXACT_METADATA,
    _EXACT_PROVENANCE,
    _common_metrics,
    _lineage,
    _nonempty_text,
    _require_exact_fields,
    _validate_selected_identity,
    _warnings,
    validate_stage10_return_inputs,
)
from .market_transform import (
    AutocorrelationPoint,
    autocorrelation,
    drawdown_episodes,
    ljung_box,
    partial_autocorrelation,
    rolling_statistic,
)
from .results import (
    DiagnosticV1,
    MatrixV1,
    QueryResult,
    RecordV1,
    fields_from_mapping,
)


ANALYSIS_FOUNDATION_TOOLS = (
    "data.quality_audit",
    "timeseries.transform",
    "stats.distribution_diagnostics",
    "stats.covariance_matrix",
    "stats.bootstrap_confidence_interval",
    "stats.principal_components",
)

_ROUTES: dict[str, tuple[type[Any], str, str]] = {
    "data.quality_audit": (
        Stage10DataQualityArgumentsV2, "2.0.0", "tool_platform.data.quality_audit.v2"
    ),
    "timeseries.transform": (
        Stage10MarketTransformArgumentsV2, "2.0.0",
        "tool_platform.timeseries.transform.v2",
    ),
    "stats.distribution_diagnostics": (
        Stage10DistributionArgumentsV1, "1.0.0",
        "tool_platform.stats.distribution_diagnostics.v1",
    ),
    "stats.covariance_matrix": (
        Stage10CovarianceArgumentsV1, "1.0.0",
        "tool_platform.stats.covariance_matrix.v1",
    ),
    "stats.bootstrap_confidence_interval": (
        Stage10BootstrapArgumentsV1, "1.0.0",
        "tool_platform.stats.bootstrap_confidence_interval.v1",
    ),
    "stats.principal_components": (
        Stage10PrincipalComponentsArgumentsV1, "1.0.0",
        "tool_platform.stats.principal_components.v1",
    ),
}


def _record(kind: str, values: Mapping[str, Any]) -> RecordV1:
    return RecordV1(kind, fields_from_mapping(values))


def _diagnostic(code: str, message: str, values: Mapping[str, Any]) -> DiagnosticV1:
    return DiagnosticV1(code, message, fields_from_mapping(values))


def _truncation(
    limit: int,
    returned: int,
    *,
    total: int | None = None,
    applied: bool = False,
) -> TruncationV1:
    if returned > limit:
        raise ResourceLimitError("Analysis result exceeds its declared row limit")
    return TruncationV1(
        applied, limit, returned, returned if total is None else total, applied
    )


def _not_established(reason: str | None, subject_id: str | None = None) -> ExclusionV1:
    return ExclusionV1(
        "not_established", reason or "analysis_not_established", subject_id
    )


def _validated_inputs(
    values: Sequence[TimeSeries], *, limit: int
) -> tuple[Any, ...]:
    reports = validate_stage10_return_inputs(values)
    if any(item.truncated for item in values):
        raise ValidationError("Step 2-4 analysis rejects truncated samples")
    if max((len(item.observations) for item in values), default=0) > limit:
        raise ResourceLimitError(
            "Step 2-4 analysis exceeds its declared observation limit"
        )
    if any(
        report.return_direction != "trailing"
        or report.return_target_status != "historical_transform"
        for report in reports
    ):
        raise ValidationError(
            "Step 2-4 analysis accepts trailing historical returns only"
        )
    return reports


def _quality_summary(summary: DataQualitySeriesSummary) -> RecordV1:
    first = summary.first_period or (None, None)
    last = summary.last_period or (None, None)
    requested = summary.requested_source_range
    availability = summary.availability_counts
    return _record(
        "stage10_quality_summary",
        {
            "availability_basis": summary.availability_basis,
            "availability_date_count": availability.date,
            "availability_datetime_count": availability.datetime,
            "availability_invalid_count": availability.invalid,
            "availability_missing_count": availability.missing,
            "availability_unknown_count": availability.unknown,
            "calendar_discontinuity_count": summary.calendar_discontinuity_count,
            "calendar_discontinuity_items_truncated": (
                summary.calendar_discontinuities_truncated
            ),
            "calendar_discontinuity_span_days": (
                summary.calendar_discontinuity_span_days
            ),
            "coverage_status": summary.coverage_status,
            "cutoff": summary.cutoff,
            "cutoff_precision": summary.cutoff_precision,
            "dataset_id": summary.dataset_id,
            "date_only_policy": summary.date_only_policy,
            "duplicate_evidence_id_count": summary.duplicate_evidence_id_count,
            "duplicate_period_count": summary.duplicate_period_count,
            "duplicate_run_id_count": summary.duplicate_run_id_count,
            "duplicate_snapshot_id_count": summary.duplicate_snapshot_id_count,
            "duplicate_version_id_count": summary.duplicate_version_id_count,
            "explicit_missing_count": summary.explicit_missing_count,
            "first_period_end": first[1],
            "first_period_start": first[0],
            "input_lineage_digest": summary.input_lineage_digest,
            "last_period_end": last[1],
            "last_period_start": last[0],
            "observation_count": summary.observation_count,
            "out_of_order_count": summary.out_of_order_count,
            "point_in_time_scope": summary.point_in_time_scope,
            "point_in_time_status": summary.point_in_time_status,
            "registry_revision": summary.registry_revision,
            "requested_end_date": requested[1],
            "requested_start_date": requested[0],
            "series_id": summary.series_id,
            "store_role": summary.store_role,
            "truncated": summary.truncated,
            "unsafe_reason_count": len(summary.unsafe_reasons),
        },
    )


def _quality(
    arguments: Stage10DataQualityArgumentsV2, context: ToolExecutionContext
) -> QueryResult:
    values = arguments.series
    for item in values:
        _validate_quality_return_contract(item)
    rows = max((len(item.observations) for item in values), default=0)
    context.budget.require(
        rows=max(rows, arguments.limit),
        series=len(values),
        operations=sum(len(item.observations) for item in values),
    )
    capacity = max(0, arguments.limit - len(values))
    summaries = audit_time_series_quality(
        values, discontinuity_limit=min(MAX_DISCONTINUITY_LIMIT, capacity)
    )
    details: list[RecordV1] = []
    total_details = 0
    for summary in summaries:
        total_details += len(summary.missing_reasons)
        total_details += summary.calendar_discontinuity_count
        details.extend(
            _record(
                "stage10_quality_missing_reason",
                {"count": count, "reason": reason, "series_id": summary.series_id},
            )
            for reason, count in summary.missing_reasons
        )
        details.extend(
            _record(
                "stage10_quality_calendar_discontinuity",
                {
                    "basis": "calendar_days_not_trading_sessions",
                    "calendar_day_count": item.calendar_day_count,
                    "calendar_span_end": item.calendar_span_end,
                    "calendar_span_start": item.calendar_span_start,
                    "next_period_start": item.next_period_start,
                    "previous_period_end": item.previous_period_end,
                    "series_id": summary.series_id,
                },
            )
            for item in summary.calendar_discontinuities
        )
    records = tuple(
        [*(_quality_summary(item) for item in summaries), *details[:capacity]]
    )
    total = len(summaries) + total_details
    applied = total > len(records)
    warning_codes = set()
    if applied:
        warning_codes.add("quality_detail_records_truncated")
    if any(item.truncated for item in summaries):
        warning_codes.add("input_series_truncated")
    if any(item.coverage_status != "observed_rows_only" for item in summaries):
        warning_codes.add("quality_coverage_not_established")
    if any(item.calendar_discontinuity_count for item in summaries):
        warning_codes.add("calendar_days_are_not_trading_sessions")
    return QueryResult(
        tool="data.quality_audit",
        records=records,
        diagnostics=(
            _diagnostic(
                "stage10_data_quality_audit",
                "The supplied Stage 10 returns were audited without repair.",
                {
                    "calendar_discontinuity_count": sum(
                        item.calendar_discontinuity_count for item in summaries
                    ),
                    "duplicate_period_count": sum(
                        item.duplicate_period_count for item in summaries
                    ),
                    "explicit_missing_count": sum(
                        item.explicit_missing_count for item in summaries
                    ),
                    "input_series_count": len(summaries),
                    "observation_count": sum(
                        item.observation_count for item in summaries
                    ),
                    "out_of_order_count": sum(
                        item.out_of_order_count for item in summaries
                    ),
                    "output_detail_truncated": applied,
                    "truncated_input_count": sum(
                        item.truncated for item in summaries
                    ),
                },
            ),
        ),
        warnings=_warnings(values, *sorted(warning_codes)),
        exclusions=tuple(
            ExclusionV1(
                "quality_limitation", item.coverage_status, item.series_id
            )
            for item in summaries
            if item.coverage_status != "observed_rows_only"
        ),
        lineage=_lineage(values),
        truncation=_truncation(
            arguments.limit, len(records), total=total, applied=applied
        ),
    )


def _validate_quality_return_contract(series: TimeSeries) -> None:
    """Validate Stage 10 semantics without imposing row order or uniqueness."""

    series.validate_lineage()
    if (
        series.metadata.get("value_representation") != "return"
        or series.metadata.get("return_direction") != "trailing"
        or series.metadata.get("return_target_status") != "historical_transform"
        or series.audit.get("return_direction") != "trailing"
        or series.audit.get("primitive") != RETURN_PRIMITIVE
        or series.audit.get("primitive_version") != RETURN_PRIMITIVE_VERSION
    ):
        raise ValidationError(
            "Stage 10 quality v2 accepts trailing historical returns only"
        )
    derivation = series.to_primitive()["provenance"].get("derivation")
    if (
        not isinstance(derivation, Mapping)
        or derivation.get("primitive") != RETURN_PRIMITIVE
        or derivation.get("primitive_version") != RETURN_PRIMITIVE_VERSION
    ):
        raise ValidationError("Stage 10 quality derivation is invalid")
    source = derivation.get("source_selection")
    digest = derivation.get("source_selection_sha256")
    if (
        not isinstance(source, Mapping)
        or not isinstance(digest, str)
        or hashlib.sha256(dumps_strict(source).encode("utf-8")).hexdigest()
        != digest
        or series.audit.get("source_lineage_digest")
        != source.get("lineage_digest")
    ):
        raise ValidationError("Stage 10 quality source receipt is invalid")
    selected = source.get("observations")
    selected_count = source.get("selected_count")
    if (
        not isinstance(selected, list)
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or selected_count != len(selected)
        or series.audit.get("source_selected_count") != selected_count
        or series.audit.get("selected_count") != len(series.observations)
        or series.audit.get("missing_count")
        != sum(item.value is None for item in series.observations)
        or series.audit.get("truncated") != series.truncated
    ):
        raise ValidationError("Stage 10 quality result-shape receipt is invalid")
    report = _quality_return_report(series, source=source, source_digest=digest)
    _validate_quality_stage10_core(series, report)
    _validate_quality_temporal_contract(series, report)


def _quality_return_report(
    series: TimeSeries,
    *,
    source: Mapping[str, Any],
    source_digest: str,
) -> Stage10MarketQualityReport:
    """Build the Stage 10 report without assuming ordered unique rows."""

    missing_reasons: dict[str, int] = {}
    for observation in series.observations:
        if observation.missing_reason is not None:
            missing_reasons[observation.missing_reason] = (
                missing_reasons.get(observation.missing_reason, 0) + 1
            )
    missing_count = sum(item.value is None for item in series.observations)
    cutoff = series.audit.get("cutoff")
    if cutoff is not None:
        cutoff = _nonempty_text(cutoff, "cutoff")
    return Stage10MarketQualityReport(
        series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        observation_count=len(series.observations),
        nonmissing_count=len(series.observations) - missing_count,
        missing_count=missing_count,
        missing_reasons=tuple(sorted(missing_reasons.items())),
        limit=series.audit.get("limit"),
        selected_count=series.audit.get("selected_count"),
        truncated=series.truncated,
        mode=_nonempty_text(series.audit.get("mode"), "mode"),
        requested_mode=_nonempty_text(
            series.audit.get("requested_mode"), "requested mode"
        ),
        actual_mode=_nonempty_text(
            series.audit.get("actual_mode"), "actual mode"
        ),
        cutoff=cutoff,
        date_only_policy=_nonempty_text(
            series.audit.get("date_only_policy"), "date-only policy"
        ),
        availability_basis=_nonempty_text(
            series.audit.get("availability_basis"), "availability basis"
        ),
        point_in_time_status=_nonempty_text(
            series.audit.get("point_in_time_status"), "point-in-time status"
        ),
        point_in_time_scope=_nonempty_text(
            series.audit.get("point_in_time_scope"), "point-in-time scope"
        ),
        unsafe_reasons=series.audit.get("unsafe_reasons", ()),
        return_definition=_nonempty_text(
            series.metadata.get("return_definition"), "definition"
        ),
        return_direction=_nonempty_text(
            series.metadata.get("return_direction"), "direction"
        ),
        return_method=_nonempty_text(
            series.metadata.get("return_method"), "method"
        ),
        return_horizon=series.metadata.get("return_horizon"),
        horizon_basis=_nonempty_text(
            series.metadata.get("horizon_basis"), "horizon basis"
        ),
        return_target_status=_nonempty_text(
            series.metadata.get("return_target_status"), "target status"
        ),
        source_lineage_digest=_nonempty_text(
            source.get("lineage_digest"), "source lineage"
        ),
        source_selection_sha256=source_digest,
    )


def _validate_quality_stage10_core(
    series: TimeSeries, report: Stage10MarketQualityReport
) -> None:
    """Use the Stage 10 core checks, except for sequence diagnostics.

    Quality audit intentionally accepts duplicate and out-of-order rows so it
    can report them.  Identity, temporal, dimensional, and source-selection
    checks remain fail-closed exactly as they do for analytical consumers.
    """

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
        raise ValidationError("Stage 10 quality input date range is invalid")
    try:
        policy = DateOnlyPolicy(report.date_only_policy)
    except ValueError as exc:
        raise ValidationError(
            "Stage 10 quality input date-only policy is invalid"
        ) from exc
    cutoff_precision = series.audit.get("cutoff_precision")
    cutoff: TemporalValue | None = None
    if report.cutoff is not None:
        cutoff = TemporalValue.parse(report.cutoff, pointer="/series/audit/cutoff")
        if cutoff_precision != cutoff.precision.value:
            raise ValidationError("Stage 10 quality input cutoff precision is inconsistent")
    elif cutoff_precision is not None:
        raise ValidationError("Stage 10 quality input cutoff precision is inconsistent")
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
        raise ValidationError("Stage 10 quality input source-selection bounds are invalid")
    _validate_selected_identity(series, cutoff=cutoff, policy=policy)
    _validate_quality_observations(
        series,
        cutoff=cutoff,
        policy=policy,
        requested_start=requested_start,
        requested_end=requested_end,
    )


def _validate_quality_observations(
    series: TimeSeries,
    *,
    cutoff: TemporalValue | None,
    policy: DateOnlyPolicy,
    requested_start: Any,
    requested_end: Any,
) -> None:
    """Revalidate Stage 10 rows without repairing or rejecting their order."""

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
        ):
            raise ValidationError("Stage 10 quality input observation periods are invalid")
        if (
            observation.unit != "fraction"
            or observation.value_representation != "return"
            or observation.scale != "1"
            or dict(observation.dimensions) != expected_dimensions
        ):
            raise ValidationError("Stage 10 quality input observation contract is invalid")
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
            raise ValidationError("Stage 10 quality input observation timing is invalid")
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
            raise ValidationError("Stage 10 quality input observation timing is invalid")
        if cutoff is not None and not availability_at_or_before(
            available, cutoff, policy
        ).included:
            raise ValidationError(
                "Stage 10 quality input observation is unavailable at the cutoff"
            )
        flags = observation.quality_flags
        if (
            any(not isinstance(flag, str) or not flag for flag in flags)
            or "availability_basis:local_capture" not in flags
            or transformation_flag not in flags
        ):
            raise ValidationError(
                "Stage 10 quality input observation lineage flags are invalid"
            )


def _validate_quality_temporal_contract(
    series: TimeSeries, report: Stage10MarketQualityReport
) -> None:
    if not (report.mode == report.requested_mode == report.actual_mode):
        raise ValidationError("Stage 10 quality input has a contradictory query mode")
    cutoff_precision = series.audit.get("cutoff_precision")
    if report.mode == "latest":
        valid = (
            report.cutoff is None
            and cutoff_precision is None
            and report.point_in_time_status == "not_applicable"
            and report.point_in_time_scope == "current_stored_knowledge"
        )
    else:
        valid = (
            report.mode == "as_of"
            and report.cutoff is not None
            and isinstance(cutoff_precision, str)
            and bool(cutoff_precision)
            and report.point_in_time_status == "safe"
            and report.point_in_time_scope == "retained_local_captures"
        )
    if not valid or report.unsafe_reasons:
        raise ValidationError(
            "Stage 10 quality input has a contradictory point-in-time contract"
        )


def _coefficient_matrix(
    name: str, values: Sequence[AutocorrelationPoint]
) -> MatrixV1:
    return MatrixV1(
        name,
        tuple(str(item.lag) for item in values),
        ("value", "missing_reason"),
        tuple((item.value, item.missing_reason) for item in values),
    )


def _transform(
    arguments: Stage10MarketTransformArgumentsV2,
    context: ToolExecutionContext,
) -> QueryResult:
    series = arguments.series
    reports = _validated_inputs((series,), limit=arguments.limit)
    operation = arguments.operation
    operations = len(series.observations)
    if operation == "rolling_statistic":
        operations *= int(arguments.window)
    elif operation in {"autocorrelation", "partial_autocorrelation"}:
        operations *= int(arguments.max_lag) + 1
    elif operation == "ljung_box":
        operations *= int(arguments.ljung_box_lag) + 1
    context.budget.require(
        rows=max(len(series.observations), arguments.limit),
        series=1,
        operations=operations,
    )
    common = _common_metrics((series,), reports)
    matrices: tuple[MatrixV1, ...] = ()
    records: tuple[RecordV1, ...] = ()
    exclusions: tuple[ExclusionV1, ...] = ()
    returned = 1

    if operation == "rolling_statistic":
        result = rolling_statistic(
            series,
            statistic=str(arguments.rolling_statistic),
            window=int(arguments.window),
        )
        records = tuple(
            _record(
                "stage10_rolling_statistic_point",
                {
                    "index": item.index,
                    "missing_reason": item.missing_reason,
                    "period_end": item.period_end,
                    "period_start": item.period_start,
                    "value": item.value,
                    "window_end_index": item.window_end_index,
                    "window_start_index": item.window_start_index,
                },
            )
            for item in result.points
        )
        returned = len(records)
        metrics = {
            "established_count": result.established_count,
            "not_established_count": result.not_established_count,
            "reason": result.reason,
            "statistic": result.statistic,
            "status": result.status,
            "window": result.window,
        }
        if result.not_established_count:
            exclusions = (
                ExclusionV1(
                    "rolling_points_not_established",
                    (
                        f"{result.not_established_count} anchors lacked a "
                        "complete fixed window"
                    ),
                    series.series_id,
                ),
            )
    elif operation == "autocorrelation":
        result = autocorrelation(series, max_lag=int(arguments.max_lag))
        if len(result.coefficients) > arguments.limit:
            raise ResourceLimitError(
                "Autocorrelation output exceeds its declared row limit"
            )
        matrices = (_coefficient_matrix("autocorrelation", result.coefficients),)
        returned = len(result.coefficients)
        metrics = {
            "centered_sum_squares": result.centered_sum_squares,
            "excluded_count": result.excluded_count,
            "interior_missing_count": result.interior_missing_count,
            "max_lag": result.max_lag,
            "mean": result.mean,
            "reason": result.reason,
            "sample_size": result.sample_size,
            "status": result.status,
            "trimmed_leading_count": result.trimmed_leading_count,
            "trimmed_trailing_count": result.trimmed_trailing_count,
        }
        if result.status != "established":
            exclusions = (_not_established(result.reason, series.series_id),)
    elif operation == "partial_autocorrelation":
        result = partial_autocorrelation(
            series, max_lag=int(arguments.max_lag)
        )
        if len(result.coefficients) > arguments.limit:
            raise ResourceLimitError(
                "Partial-autocorrelation output exceeds its declared row limit"
            )
        matrices = (
            _coefficient_matrix("autocorrelation", result.autocorrelations),
            _coefficient_matrix("partial_autocorrelation", result.coefficients),
        )
        returned = len(result.coefficients)
        metrics = {
            "centered_sum_squares": result.centered_sum_squares,
            "excluded_count": result.excluded_count,
            "innovation_variance": result.innovation_variance,
            "interior_missing_count": result.interior_missing_count,
            "max_lag": result.max_lag,
            "mean": result.mean,
            "reason": result.reason,
            "sample_size": result.sample_size,
            "status": result.status,
            "trimmed_leading_count": result.trimmed_leading_count,
            "trimmed_trailing_count": result.trimmed_trailing_count,
        }
        if result.status != "established":
            exclusions = (_not_established(result.reason, series.series_id),)
    elif operation == "ljung_box":
        result = ljung_box(series, lag=int(arguments.ljung_box_lag))
        if len(result.autocorrelations) > arguments.limit:
            raise ResourceLimitError(
                "Ljung-Box output exceeds its declared row limit"
            )
        matrices = (
            _coefficient_matrix("autocorrelation", result.autocorrelations),
        )
        records = (
            _record(
                "stage10_ljung_box_summary",
                {
                    "degrees_of_freedom": result.degrees_of_freedom,
                    "inference_backend": result.inference_backend,
                    "lag": result.lag,
                    "model_degrees_of_freedom": result.model_degrees_of_freedom,
                    "p_value": result.p_value,
                    "reference_distribution": result.reference_distribution,
                    "statistic": result.statistic,
                },
            ),
        )
        returned = len(result.autocorrelations)
        metrics = {
            "excluded_count": result.excluded_count,
            "interior_missing_count": result.interior_missing_count,
            "reason": result.reason,
            "sample_size": result.sample_size,
            "status": result.status,
            "trimmed_leading_count": result.trimmed_leading_count,
            "trimmed_trailing_count": result.trimmed_trailing_count,
        }
        if result.status != "established":
            exclusions = (_not_established(result.reason, series.series_id),)
    else:
        result = drawdown_episodes(series, method=reports[0].return_method)
        if len(result.points) > arguments.limit:
            raise ResourceLimitError(
                "Drawdown output exceeds its declared row limit"
            )
        if result.points:
            matrices = (
                MatrixV1(
                    "drawdown_path",
                    tuple(str(item.index) for item in result.points),
                    (
                        "wealth",
                        "high_water_mark",
                        "high_water_mark_index",
                        "drawdown",
                    ),
                    tuple(
                        (
                            item.wealth,
                            item.high_water_mark,
                            item.high_water_mark_index,
                            item.drawdown,
                        )
                        for item in result.points
                    ),
                ),
            )
        records = tuple(
            _record(
                "stage10_drawdown_episode",
                {
                    "maximum_drawdown": item.maximum_drawdown,
                    "peak_index": item.peak_index,
                    "peak_wealth": item.peak_wealth,
                    "recovered": item.recovered,
                    "recovery_index": item.recovery_index,
                    "recovery_wealth": item.recovery_wealth,
                    "start_index": item.start_index,
                    "trough_index": item.trough_index,
                    "trough_wealth": item.trough_wealth,
                },
            )
            for item in result.episodes
        )
        returned = len(result.points)
        metrics = {
            "episode_count": len(result.episodes),
            "excluded_count": result.excluded_count,
            "interior_missing_count": result.interior_missing_count,
            "method": result.method,
            "reason": result.reason,
            "sample_size": result.sample_size,
            "status": result.status,
            "terminal_high_water_mark": result.terminal_high_water_mark,
            "terminal_high_water_mark_index": (
                result.terminal_high_water_mark_index
            ),
            "terminal_wealth": result.terminal_wealth,
            "trimmed_leading_count": result.trimmed_leading_count,
            "trimmed_trailing_count": result.trimmed_trailing_count,
        }
        if result.status != "established":
            exclusions = (_not_established(result.reason, series.series_id),)

    return QueryResult(
        tool="timeseries.transform",
        status="ok" if result.status == "established" else "not_established",
        records=records,
        matrices=matrices,
        diagnostics=(
            _diagnostic(
                "stage10_time_series_transform",
                "One explicit Stage 10 time-series transform was evaluated.",
                {**common, "operation": operation, **metrics},
            ),
        ),
        warnings=_warnings((series,), *result.warnings),
        exclusions=exclusions,
        lineage=_lineage((series,)),
        truncation=_truncation(arguments.limit, returned),
    )


def _single_sample(
    series: TimeSeries,
) -> tuple[tuple[Decimal, ...], int, int, int]:
    raw = tuple(item.value for item in series.observations)
    present = tuple(index for index, value in enumerate(raw) if value is not None)
    if not present:
        return (), len(raw), 0, 0
    first, last = present[0], present[-1]
    interior = sum(value is None for value in raw[first : last + 1])
    if interior:
        return (), first, len(raw) - last - 1, interior
    return (
        tuple(value for value in raw[first : last + 1] if value is not None),
        first,
        len(raw) - last - 1,
        0,
    )


def _sample_exclusions(
    *,
    reason: str | None,
    leading: int,
    trailing: int,
    interior: int,
    series_id: str,
) -> tuple[ExclusionV1, ...]:
    result: list[ExclusionV1] = []
    if leading or trailing:
        result.append(
            ExclusionV1(
                "terminal_missing_observations_trimmed",
                (
                    f"{leading} leading and {trailing} trailing missing "
                    "observations were excluded"
                ),
                series_id,
            )
        )
    if interior:
        result.append(
            ExclusionV1(
                "not_established", "interior_missing_observations", series_id
            )
        )
    elif reason is not None:
        result.append(_not_established(reason, series_id))
    return tuple(result)


def _sample_warning_codes(
    *,
    kernel: Sequence[str],
    leading: int,
    trailing: int,
    interior: int,
) -> tuple[str, ...]:
    result = list(kernel)
    if leading or trailing:
        result.append("terminal_missing_observations_trimmed")
    if interior:
        result.append("interior_missing_observations")
    return tuple(result)


def _distribution(
    arguments: Stage10DistributionArgumentsV1,
    context: ToolExecutionContext,
) -> QueryResult:
    series = arguments.series
    reports = _validated_inputs((series,), limit=arguments.limit)
    values, leading, trailing, interior = _single_sample(series)
    context.budget.require(
        rows=max(len(series.observations), arguments.limit),
        series=1,
        operations=len(series.observations),
    )
    result = None if interior else distribution_diagnostics(values)
    reason = (
        "interior_missing_observations"
        if interior
        else None if result is None else result.reason
    )
    records = (
        _record(
            "stage10_distribution_summary",
            {
                "jarque_bera_statistic": (
                    None if result is None else result.jarque_bera_statistic
                ),
                "maximum": None if result is None else result.maximum,
                "mean": None if result is None else result.mean,
                "median": None if result is None else result.median,
                "minimum": None if result is None else result.minimum,
                "population_excess_kurtosis": (
                    None
                    if result is None
                    else result.population_excess_kurtosis
                ),
                "population_skewness": (
                    None if result is None else result.population_skewness
                ),
                "population_standard_deviation": (
                    None
                    if result is None
                    else result.population_standard_deviation
                ),
                "population_variance": (
                    None if result is None else result.population_variance
                ),
                "quantile_method": (
                    "hyndman_fan_type_7"
                    if result is None
                    else result.quantile_method
                ),
                "raw_median_absolute_deviation": (
                    None
                    if result is None
                    else result.raw_median_absolute_deviation
                ),
                "reason": reason,
                "sample_size": 0 if result is None else result.sample_size,
                "status": (
                    "not_established" if result is None else result.status
                ),
            },
        ),
    )
    if result is not None:
        records += tuple(
            _record(
                "stage10_distribution_quantile",
                {"probability": item.probability, "value": item.value},
            )
            for item in result.quantiles
        )
    kernel_warnings = () if result is None else result.warnings
    established = (
        not interior and result is not None and result.status == "established"
    )
    return QueryResult(
        tool="stats.distribution_diagnostics",
        status="ok" if established else "not_established",
        records=records,
        diagnostics=(
            _diagnostic(
                "stage10_distribution_diagnostics",
                "Distribution diagnostics used the declared complete sample.",
                {
                    **_common_metrics((series,), reports),
                    "interior_missing_count": interior,
                    "missing_policy": "trim_terminal_reject_interior",
                    "sample_size": 0 if result is None else result.sample_size,
                    "trimmed_leading_count": leading,
                    "trimmed_trailing_count": trailing,
                },
            ),
        ),
        warnings=_warnings(
            (series,),
            *_sample_warning_codes(
                kernel=kernel_warnings,
                leading=leading,
                trailing=trailing,
                interior=interior,
            ),
        ),
        exclusions=_sample_exclusions(
            reason=reason,
            leading=leading,
            trailing=trailing,
            interior=interior,
            series_id=series.series_id,
        ),
        lineage=_lineage((series,)),
        truncation=_truncation(arguments.limit, 1),
    )


def _bootstrap(
    arguments: Stage10BootstrapArgumentsV1,
    context: ToolExecutionContext,
) -> QueryResult:
    series = arguments.series
    reports = _validated_inputs((series,), limit=arguments.limit)
    values, leading, trailing, interior = _single_sample(series)
    context.budget.require(
        rows=max(len(series.observations), arguments.limit),
        series=1,
        operations=len(series.observations) * arguments.replicates,
    )
    confidence = Decimal(arguments.confidence_level)
    result = (
        None
        if interior
        else bootstrap_confidence_interval(
            values,
            statistic=arguments.statistic,
            seed=arguments.seed,
            replicates=arguments.replicates,
            confidence_level=confidence,
        )
    )
    reason = (
        "interior_missing_observations"
        if interior
        else None if result is None else result.reason
    )
    lower_probability = (Decimal("1") - confidence) / Decimal("2")
    record = _record(
        "stage10_bootstrap_confidence_interval",
        {
            "confidence_level": confidence,
            "lower_bound": None if result is None else result.lower_bound,
            "lower_probability": (
                lower_probability
                if result is None
                else result.lower_probability
            ),
            "method": (
                "iid_percentile_bootstrap" if result is None else result.method
            ),
            "point_estimate": None if result is None else result.point_estimate,
            "prng": "splitmix64_v1" if result is None else result.prng,
            "quantile_method": (
                "hyndman_fan_type_7"
                if result is None
                else result.quantile_method
            ),
            "reason": reason,
            "replicates": arguments.replicates,
            "sample_size": 0 if result is None else result.sample_size,
            "seed": arguments.seed,
            "statistic": arguments.statistic,
            "status": (
                "not_established" if result is None else result.status
            ),
            "upper_bound": None if result is None else result.upper_bound,
            "upper_probability": (
                Decimal("1") - lower_probability
                if result is None
                else result.upper_probability
            ),
        },
    )
    kernel_warnings = () if result is None else result.warnings
    established = (
        not interior and result is not None and result.status == "established"
    )
    return QueryResult(
        tool="stats.bootstrap_confidence_interval",
        status="ok" if established else "not_established",
        records=(record,),
        diagnostics=(
            _diagnostic(
                "stage10_bootstrap_confidence_interval",
                "The seeded bootstrap was evaluated deterministically.",
                {
                    **_common_metrics((series,), reports),
                    "interior_missing_count": interior,
                    "missing_policy": "trim_terminal_reject_interior",
                    "replicates": arguments.replicates,
                    "seed": arguments.seed,
                    "trimmed_leading_count": leading,
                    "trimmed_trailing_count": trailing,
                },
            ),
        ),
        warnings=_warnings(
            (series,),
            *_sample_warning_codes(
                kernel=kernel_warnings,
                leading=leading,
                trailing=trailing,
                interior=interior,
            ),
        ),
        exclusions=_sample_exclusions(
            reason=reason,
            leading=leading,
            trailing=trailing,
            interior=interior,
            series_id=series.series_id,
        ),
        lineage=_lineage((series,)),
        truncation=_truncation(arguments.limit, 1),
    )


def _joint_rows(
    values: Sequence[TimeSeries], *, limit: int
) -> tuple[
    tuple[str, ...],
    tuple[tuple[Decimal, ...], ...],
    tuple[str, ...],
    int,
    int,
    int,
]:
    aligned = align_series(values, join="outer")
    aligned_rows = tuple(aligned["rows"])
    if len(aligned_rows) > limit:
        raise ResourceLimitError(
            "Joint statistical sample exceeds its declared row limit"
        )
    complete: list[tuple[Decimal, ...]] = []
    period_labels: list[str] = []
    excluded = absent = explicit = 0
    for row in aligned_rows:
        raw = tuple(row["values"])
        reasons = tuple(row["missing_reasons"])
        if all(value is not None for value in raw):
            complete.append(tuple(value for value in raw if value is not None))
            period_labels.append(
                f"{row['period_start']}/{row['period_end']}"
            )
            continue
        excluded += 1
        for value, reason in zip(raw, reasons, strict=True):
            if value is None and reason == "absent_from_series":
                absent += 1
            elif value is None:
                explicit += 1
    return (
        tuple(aligned["series_ids"]),
        tuple(complete),
        tuple(period_labels),
        excluded,
        absent,
        explicit,
    )


def _optional_matrix(
    name: str,
    rows: Sequence[str],
    columns: Sequence[str],
    values: Sequence[Sequence[Any]] | None,
) -> MatrixV1 | None:
    if values is None:
        return None
    return MatrixV1(
        name,
        tuple(rows),
        tuple(columns),
        tuple(tuple(row) for row in values),
    )


def _joint_exclusions(
    *, excluded: int, status: str, reason: str | None
) -> tuple[ExclusionV1, ...]:
    values: list[ExclusionV1] = []
    if excluded:
        values.append(
            ExclusionV1(
                "joint_incomplete_rows_excluded",
                f"{excluded} outer-aligned rows were not joint-complete",
            )
        )
    if status != "established":
        values.append(_not_established(reason))
    return tuple(values)


def _covariance(
    arguments: Stage10CovarianceArgumentsV1,
    context: ToolExecutionContext,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values, limit=arguments.limit)
    labels, rows, _, excluded, absent, explicit = _joint_rows(
        values, limit=arguments.limit
    )
    largest = max(len(item.observations) for item in values)
    context.budget.require(
        rows=max(arguments.limit, largest),
        series=len(values),
        operations=max(arguments.limit, len(rows)) * len(values) ** 2,
    )
    result = covariance_correlation_matrix(rows)
    matrices = tuple(
        item
        for item in (
            _optional_matrix(
                "sample_covariance", labels, labels, result.covariance
            ),
            _optional_matrix(
                "sample_correlation", labels, labels, result.correlation
            ),
        )
        if item is not None
    )
    return QueryResult(
        tool="stats.covariance_matrix",
        status="ok" if result.status == "established" else "not_established",
        records=tuple(
            _record(
                "stage10_covariance_feature",
                {
                    "feature_index": index,
                    "mean": (
                        None if result.means is None else result.means[index]
                    ),
                    "series_id": label,
                    "zero_variance": index in result.zero_variance_indices,
                },
            )
            for index, label in enumerate(labels)
        ),
        matrices=matrices,
        diagnostics=(
            _diagnostic(
                "stage10_covariance_matrix",
                "Covariance and correlation use one joint-complete sample.",
                {
                    **_common_metrics(values, reports),
                    "absent_cell_count": absent,
                    "alignment_policy": "outer_joint_complete",
                    "explicit_missing_cell_count": explicit,
                    "feature_count": result.feature_count,
                    "joint_complete_sample_size": result.sample_size,
                    "joint_incomplete_row_count": excluded,
                    "reason": result.reason,
                    "sample_denominator": result.sample_denominator,
                    "status": result.status,
                    "zero_variance_feature_count": len(
                        result.zero_variance_indices
                    ),
                },
            ),
        ),
        warnings=_warnings(values, *result.warnings),
        exclusions=_joint_exclusions(
            excluded=excluded, status=result.status, reason=result.reason
        ),
        lineage=_lineage(values),
        truncation=_truncation(arguments.limit, result.sample_size),
    )


def _pca(
    arguments: Stage10PrincipalComponentsArgumentsV1,
    context: ToolExecutionContext,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values, limit=arguments.limit)
    labels, rows, period_labels, excluded, absent, explicit = _joint_rows(
        values, limit=arguments.limit
    )
    largest = max(len(item.observations) for item in values)
    context.budget.require(
        rows=max(arguments.limit, largest),
        series=len(values),
        operations=(
            max(arguments.limit, len(rows)) * len(values) ** 2
            + len(values) ** 3
        ),
    )
    result = principal_component_analysis(
        rows,
        method=arguments.basis,
        component_count=arguments.components,
    )
    component_labels = tuple(
        f"component_{index + 1}" for index in range(result.component_count)
    )
    matrices = tuple(
        item
        for item in (
            _optional_matrix(
                "sample_covariance", labels, labels, result.covariance
            ),
            _optional_matrix(
                "sample_correlation", labels, labels, result.correlation
            ),
            _optional_matrix(
                "pca_eigenvectors",
                component_labels,
                labels,
                result.eigenvectors,
            ),
            _optional_matrix(
                "pca_loadings", component_labels, labels, result.loadings
            ),
            (
                _optional_matrix(
                    "pca_scores",
                    period_labels,
                    component_labels,
                    result.scores,
                )
                if arguments.include_scores
                else None
            ),
        )
        if item is not None
    )
    records: list[RecordV1] = [
        _record(
            "stage10_pca_feature",
            {
                "feature_index": index,
                "mean": None if result.means is None else result.means[index],
                "sample_standard_deviation": (
                    None
                    if result.sample_standard_deviations is None
                    else result.sample_standard_deviations[index]
                ),
                "series_id": label,
                "zero_variance": index in result.zero_variance_indices,
            },
        )
        for index, label in enumerate(labels)
    ]
    if (
        result.eigenvalues is not None
        and result.explained_variance_ratios is not None
    ):
        records.extend(
            _record(
                "stage10_pca_component",
                {
                    "component": component_labels[index],
                    "eigenvalue": eigenvalue,
                    "explained_variance_ratio": (
                        result.explained_variance_ratios[index]
                    ),
                    "ordering_rule": (
                        "descending_eigenvalue_then_original_component_index"
                    ),
                    "sign_rule": (
                        "largest_absolute_loading_positive_ties_lowest_feature_index"
                    ),
                },
            )
            for index, eigenvalue in enumerate(result.eigenvalues)
        )
    return QueryResult(
        tool="stats.principal_components",
        status="ok" if result.status == "established" else "not_established",
        records=tuple(records),
        matrices=matrices,
        diagnostics=(
            _diagnostic(
                "stage10_principal_components",
                "PCA uses deterministic ordering, signs, and joint completeness.",
                {
                    **_common_metrics(values, reports),
                    "absent_cell_count": absent,
                    "alignment_policy": "outer_joint_complete",
                    "basis": result.method,
                    "component_count": result.component_count,
                    "eigenvector_ordering": (
                        "descending_eigenvalue_then_original_component_index"
                    ),
                    "eigenvector_sign_rule": (
                        "largest_absolute_loading_positive_ties_lowest_feature_index"
                    ),
                    "explicit_missing_cell_count": explicit,
                    "include_scores": arguments.include_scores,
                    "jacobi_rotations": result.jacobi_rotations,
                    "jacobi_tolerance": result.jacobi_tolerance,
                    "joint_complete_sample_size": result.sample_size,
                    "joint_incomplete_row_count": excluded,
                    "reason": result.reason,
                    "status": result.status,
                    "zero_variance_feature_count": len(
                        result.zero_variance_indices
                    ),
                },
            ),
        ),
        warnings=_warnings(values, *result.warnings),
        exclusions=_joint_exclusions(
            excluded=excluded, status=result.status, reason=result.reason
        ),
        lineage=_lineage(values),
        truncation=_truncation(arguments.limit, result.sample_size),
    )


def invoke_stage10_analysis_foundation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> QueryResult:
    """Invoke one exact-version Step 2--4 analytical tool."""

    route = _ROUTES.get(name)
    if route is None:
        raise LookupError("Step 2-4 analysis operation is not registered")
    expected_type, expected_version, expected_graph = route
    if (
        context.tool_name != name
        or context.tool_version != expected_version
        or context.operation_version != expected_version
        or context.operation_graph_id != expected_graph
    ):
        raise LookupError("Selected Step 2-4 analysis graph is invalid")
    if not isinstance(arguments, expected_type):
        raise ValidationError("Step 2-4 analysis arguments use the wrong contract")
    context.checkpoint()
    result = {
        "data.quality_audit": _quality,
        "timeseries.transform": _transform,
        "stats.distribution_diagnostics": _distribution,
        "stats.covariance_matrix": _covariance,
        "stats.bootstrap_confidence_interval": _bootstrap,
        "stats.principal_components": _pca,
    }[name](arguments, context)
    context.checkpoint()
    return result


__all__ = (
    "ANALYSIS_FOUNDATION_TOOLS",
    "invoke_stage10_analysis_foundation",
)
