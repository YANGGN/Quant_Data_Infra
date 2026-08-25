"""Store-free econometrics over strictly validated Stage 10 return series.

The public v2 operations consume caller-supplied typed return series,
revalidate their lineage and point-in-time contracts, and never open a store.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, localcontext
from typing import Any, Mapping, Sequence

from quant_data.contracts import (
    ExclusionV1,
    ResearchContractV1,
    TemporalQuery,
    TimeSeries,
    TruncationV1,
)
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict

from .analytics import align_series
from .arguments import (
    Stage10MarketRegressionArgumentsV2,
    Stage10MarketRegressionArgumentsV21,
    Stage10MarketRollingRegressionArgumentsV2,
    Stage10MarketRollingRegressionArgumentsV21,
    Stage10MarketStationarityArgumentsV2,
    Stage10MarketStationarityArgumentsV21,
    Stage10MarketStructuralBreakArgumentsV2,
    Stage10MarketRegressionModelSuiteArgumentsV3,
)
from .context import ToolExecutionContext
from .econometrics import (
    ADFResult,
    EngleGrangerCointegrationResult,
    GrangerCausalityResult,
    VectorAutoregressionResult,
    ChowBreakResult,
    KPSSResult,
    OLSResult,
    _not_established_ols,
    augmented_dickey_fuller,
    chow_break_test,
    fit_ols,
    engle_granger_cointegration,
    granger_causality,
    vector_autoregression,
    kpss_level_stationarity,
    rolling_ols,
)
from .market_returns import Stage10MarketQualityReport
from .market_statistics import (
    _common_metrics,
    _lineage,
    _warnings,
    validate_stage10_return_inputs,
)
from .results import (
    DiagnosticV1,
    MatrixV1,
    QueryResult,
    RecordV1,
    ResearchEnvelopeV1,
    fields_from_mapping,
)


VERSIONED_STAGE10_ECONOMETRICS_TOOLS = (
    "econometrics.regression",
    "econometrics.rolling_regression",
    "econometrics.stationarity",
    "econometrics.structural_breaks",
)
_MODEL_VERSION = "2.0.0"
_MODEL_VERSION_V21 = "2.1.0"
_EXECUTION_POLICY = "caller_supplied_in_memory_read_only_no_store"

_MODEL_VERSION_V3 = "3.0.0"

def _validated_inputs(
    values: Sequence[TimeSeries],
) -> tuple[Stage10MarketQualityReport, ...]:
    reports = validate_stage10_return_inputs(values)
    if any(item.truncated for item in values):
        raise ValidationError(
            "Stage 10 econometrics rejects truncated statistical samples"
        )
    first = reports[0]
    if (
        first.return_direction != "trailing"
        or first.return_target_status != "historical_transform"
    ):
        raise ValidationError(
            "Stage 10 econometrics v2 accepts trailing historical returns only"
        )
    return reports


def _aligned_rows(
    values: Sequence[TimeSeries], *, limit: int
) -> tuple[Mapping[str, Any], ...]:
    rows = tuple(align_series(values, join="outer")["rows"])
    if len(rows) > limit:
        raise ResourceLimitError(
            "Stage 10 econometrics exceeds its declared observation limit"
        )
    return rows


def _complete_record_truncation(
    *, observation_limit: int, returned_count: int
) -> TruncationV1:
    """Account for a fixed analytical report independently of its row cap."""

    return TruncationV1(
        False,
        max(observation_limit, returned_count),
        returned_count,
        returned_count,
        False,
    )


def _regression_vectors(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    tuple[Decimal | None, ...],
    tuple[tuple[Decimal | None, ...], ...],
]:
    dependent: list[Decimal | None] = []
    predictors: list[tuple[Decimal | None, ...]] = []
    for row in rows:
        values = tuple(row["values"])
        dependent.append(values[0])
        predictors.append(tuple(values[1:]))
    return tuple(dependent), tuple(predictors)


def _analysis_id(
    name: str,
    model_id: str,
    parameters: Mapping[str, Any],
    values: Sequence[TimeSeries],
    *,
    model_version: str = _MODEL_VERSION,
) -> str:
    material = {
        "analysis_kind": name,
        "model_id": model_id,
        "model_version": model_version,
        "parameters": dict(sorted(parameters.items())),
        "input_lineage": [item.lineage_digest for item in values],
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _research_envelope(
    *,
    name: str,
    model_id: str,
    parameters: Mapping[str, Decimal | str | int | bool | None],
    sample: Mapping[str, Decimal | str | int | bool | None],
    uncertainty: Mapping[str, Decimal | str | int | bool | None],
    values: Sequence[TimeSeries],
    reports: Sequence[Stage10MarketQualityReport],
    exclusions: tuple[ExclusionV1, ...],
    model_version: str = _MODEL_VERSION,
) -> ResearchEnvelopeV1:
    first = reports[0]
    unsafe_reasons = tuple(first.unsafe_reasons)
    temporal = TemporalQuery(
        mode=first.mode,
        cutoff=first.cutoff,
        date_only_policy=first.date_only_policy,
        availability_basis=first.availability_basis,
        point_in_time_status=first.point_in_time_status,
        unsafe_reasons=unsafe_reasons,
    )
    contract = ResearchContractV1(
        analysis_id=_analysis_id(
            name, model_id, parameters, values, model_version=model_version
        ),
        analysis_kind=name,
        model_id=model_id,
        model_version=model_version,
        parameters=parameters,
        temporal=temporal,
        point_in_time_status=first.point_in_time_status,
        vintage_policy=(
            "retained_local_captures_as_of"
            if first.mode == "as_of"
            else "current_stored_knowledge"
        ),
        availability_policy="local_capture",
        execution_policy=_EXECUTION_POLICY,
        unsafe_reasons=unsafe_reasons,
        sample=sample,
        exclusions=exclusions,
        uncertainty=uncertainty,
        input_lineage=_lineage(values),
    )
    return ResearchEnvelopeV1("declared", contract)


def _reason_exclusion(result: OLSResult, *, subject_id: str | None = None) -> tuple[ExclusionV1, ...]:
    if result.status == "established":
        if result.excluded_count:
            return (
                ExclusionV1(
                    code="complete_case_rows_dropped",
                    reason=f"{result.excluded_count} incomplete aligned rows were excluded.",
                    subject_id=subject_id,
                ),
            )
        return ()
    return (
        ExclusionV1(
            code="not_established",
            reason=result.reason or "ols_not_established",
            subject_id=subject_id,
        ),
    )


def _ols_metrics(result: OLSResult) -> dict[str, Any]:
    return {
        "adjusted_r_squared": result.adjusted_r_squared,
        "covariance_method": result.covariance_method,
        "degrees_of_freedom_model": result.degrees_of_freedom_model,
        "degrees_of_freedom_residual": result.degrees_of_freedom_residual,
        "durbin_watson": result.durbin_watson,
        "excluded_count": result.excluded_count,
        "inference_backend": result.inference_backend,
        "intercept_included": result.intercept_included,
        "missing_policy": result.missing_policy,
        "parameter_count": result.parameter_count,
        "r_squared": result.r_squared,
        "rank": result.rank,
        "rank_tolerance": result.rank_tolerance,
        "residual_sum_squares": result.residual_sum_squares,
        "residual_variance": result.residual_variance,
        "root_mean_squared_error": result.root_mean_squared_error,
        "sample_size": result.sample_size,
        "status": result.status,
        "t_critical_value": result.t_critical_value,
        "total_sum_squares": result.total_sum_squares,
    }


def _coefficient_records(result: OLSResult) -> tuple[RecordV1, ...]:
    if result.status != "established":
        return (
            RecordV1(
                "stage10_ols_summary",
                fields_from_mapping(
                    {
                        "reason": result.reason,
                        "status": result.status,
                    }
                ),
            ),
        )
    return tuple(
        RecordV1(
            "stage10_ols_coefficient",
            fields_from_mapping(
                {
                    "confidence_interval_lower": item.confidence_interval_lower,
                    "confidence_interval_upper": item.confidence_interval_upper,
                    "estimate": item.estimate,
                    "p_value": item.p_value,
                    "standard_error": item.standard_error,
                    "t_statistic": item.t_statistic,
                    "term": item.term,
                }
            ),
        )
        for item in result.coefficients
    )


def _ols_metrics_v21(result: OLSResult) -> dict[str, Any]:
    metrics = _ols_metrics(result)
    critical_value = metrics.pop("t_critical_value")
    metrics.update(
        {
            "critical_value": critical_value,
            "hac_lag": result.hac_lag,
            "inference_distribution": result.inference_distribution,
            "inference_status": (
                "established"
                if result.status == "established"
                and bool(result.coefficients)
                and all(
                    item.standard_error is not None
                    for item in result.coefficients
                )
                else "not_established"
            ),
            "standardized_statistic_kind": (
                "t"
                if result.inference_distribution == "student_t"
                else "z"
            ),
        }
    )
    return metrics


def _coefficient_records_v21(result: OLSResult) -> tuple[RecordV1, ...]:
    if result.status != "established":
        return (
            RecordV1(
                "stage10_ols_summary_v2_1",
                fields_from_mapping(
                    {
                        "reason": result.reason,
                        "status": result.status,
                    }
                ),
            ),
        )
    statistic_kind = (
        "t" if result.inference_distribution == "student_t" else "z"
    )
    return tuple(
        RecordV1(
            "stage10_ols_coefficient_v2_1",
            fields_from_mapping(
                {
                    "confidence_interval_lower": item.confidence_interval_lower,
                    "confidence_interval_upper": item.confidence_interval_upper,
                    "estimate": item.estimate,
                    "p_value": item.p_value,
                    "reference_distribution": result.inference_distribution,
                    "standard_error": item.standard_error,
                    "standardized_statistic": item.t_statistic,
                    "statistic_kind": statistic_kind,
                    "term": item.term,
                }
            ),
        )
        for item in result.coefficients
    )


def _residual_diagnostic_entries(
    result: OLSResult,
) -> tuple[DiagnosticV1, ...]:
    summaries = {
        "ljung_box": (
            "Ljung-Box Q tested centered residual autocorrelation at the fixed lag."
        ),
        "breusch_pagan": (
            "Koenker-Breusch-Pagan LM tested residual variance against predictors."
        ),
        "jarque_bera": (
            "Jarque-Bera tested residual skewness and kurtosis using population moments."
        ),
    }
    return tuple(
        DiagnosticV1(
            f"stage10_{item.name}_v2_1",
            summaries[item.name],
            fields_from_mapping(
                {
                    "alternative_hypothesis": item.alternative_hypothesis,
                    "decision": item.decision,
                    "degrees_of_freedom": item.degrees_of_freedom,
                    "excluded_count": item.excluded_count,
                    "inference_backend": item.inference_backend,
                    "lag": item.lag,
                    "method": item.method,
                    "null_hypothesis": item.null_hypothesis,
                    "p_value": item.p_value,
                    "reason": item.reason,
                    "reference_distribution": item.reference_distribution,
                    "sample_size": item.sample_size,
                    "significance": item.significance,
                    "statistic": item.statistic,
                    "status": item.status,
                    "warning_count": len(item.warnings),
                }
            ),
        )
        for item in result.residual_diagnostics
    )


def _covariance_matrix_name(method: str) -> str:
    return {
        "classical_homoskedastic": "ols_classical_covariance",
        "hc1": "ols_hc1_covariance",
        "hc3": "ols_hc3_covariance",
        "newey_west_hac_bartlett": "ols_newey_west_hac_bartlett_covariance",
    }[method]


def _regression_matrices(
    rows: Sequence[Mapping[str, Any]],
    values: Sequence[TimeSeries],
    result: OLSResult,
    covariance_name: str = "ols_classical_covariance",
) -> tuple[MatrixV1, ...]:
    row_labels = tuple(
        f"{index}:{row['period_start']}:{row['period_end']}"
        for index, row in enumerate(rows)
    )
    matrices: list[MatrixV1] = []
    if rows:
        observation_values = []
        predictor_values = []
        for index, row in enumerate(rows):
            missing_reasons = tuple(row["missing_reasons"])
            missing_summary = ",".join(
                f"{position}:{reason}"
                for position, reason in enumerate(missing_reasons)
                if reason is not None
            ) or None
            observation_values.append(
                (
                    row["values"][0],
                    result.fitted_values[index],
                    result.residuals[index],
                    result.complete_rows[index],
                    missing_summary,
                )
            )
            predictor_values.append(tuple(row["values"][1:]))
        matrices.append(
            MatrixV1(
                "ols_observations",
                row_labels,
                (
                    "dependent",
                    "fitted",
                    "residual",
                    "complete_row",
                    "missing_summary",
                ),
                tuple(observation_values),
            )
        )
        matrices.append(
            MatrixV1(
                "ols_predictors",
                row_labels,
                tuple(item.series_id for item in values[1:]),
                tuple(predictor_values),
            )
        )
    if result.covariance is not None:
        matrices.append(
            MatrixV1(
                covariance_name,
                result.coefficient_names,
                result.coefficient_names,
                result.covariance,
            )
        )
    return tuple(matrices)


def _regression(
    arguments: Stage10MarketRegressionArgumentsV2,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    rows = _aligned_rows(values, limit=arguments.limit)
    if rows:
        dependent, predictors = _regression_vectors(rows)
        result = fit_ols(
            dependent,
            predictors,
            intercept=arguments.intercept,
            covariance=arguments.covariance,
            confidence_level=Decimal(arguments.confidence_level),
        )
    else:
        coefficient_names = (
            (("intercept",) if arguments.intercept else ())
            + tuple(f"x_{index}" for index in range(1, len(values)))
        )
        result = _not_established_ols(
            reason="no_aligned_observations",
            warnings=("no_aligned_observations",),
            names=coefficient_names,
            rows=0,
            complete_rows=(),
            excluded_indices=(),
            parameter_count=len(coefficient_names),
            rank=None,
            rank_tolerance=None,
            intercept=arguments.intercept,
            covariance_method=arguments.covariance,
            confidence_level=Decimal(arguments.confidence_level),
            missing_policy="drop_complete_rows",
        )
    exclusions = _reason_exclusion(result, subject_id=values[0].series_id)
    records = _coefficient_records(result)
    parameters = {
        "confidence_level": arguments.confidence_level,
        "covariance": arguments.covariance,
        "dependent_series_id": values[0].series_id,
        "intercept": arguments.intercept,
        "missing_policy": result.missing_policy,
        "predictor_count": len(values) - 1,
    }
    return QueryResult(
        tool="econometrics.regression",
        status="ok" if result.status == "established" else "not_established",
        records=records,
        matrices=_regression_matrices(rows, values, result),
        diagnostics=(
            DiagnosticV1(
                "stage10_ols_v2",
                "Rank-aware OLS completed over outer-aligned complete rows.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        **_ols_metrics(result),
                        "alignment_policy": "outer_complete_rows",
                        "model_version": _MODEL_VERSION,
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *result.warnings,
            "classical_homoskedastic_inference",
            "contemporaneous_regression_not_predictive_or_causal",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=TruncationV1(False, arguments.limit, len(records), len(records), False),
        research_contract=_research_envelope(
            name="econometrics.regression",
            model_id="ordinary_least_squares_rank_aware_qr",
            parameters=parameters,
            sample={
                "aligned_row_count": len(rows),
                "excluded_count": result.excluded_count,
                "sample_size": result.sample_size,
            },
            uncertainty={
                "confidence_level": arguments.confidence_level,
                "covariance": arguments.covariance,
                "inference_backend": result.inference_backend,
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
        ),
    )

def _regression_v21(
    arguments: Stage10MarketRegressionArgumentsV21,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    rows = _aligned_rows(values, limit=arguments.limit)
    if rows:
        dependent, predictors = _regression_vectors(rows)
        result = fit_ols(
            dependent,
            predictors,
            intercept=arguments.intercept,
            covariance=arguments.covariance,
            hac_lag=arguments.hac_lag,
            diagnostic_lag=arguments.diagnostic_lag,
            confidence_level=Decimal(arguments.confidence_level),
        )
    else:
        coefficient_names = (
            (("intercept",) if arguments.intercept else ())
            + tuple(f"x_{index}" for index in range(1, len(values)))
        )
        result = _not_established_ols(
            reason="no_aligned_observations",
            warnings=("no_aligned_observations",),
            names=coefficient_names,
            rows=0,
            complete_rows=(),
            excluded_indices=(),
            parameter_count=len(coefficient_names),
            rank=None,
            rank_tolerance=None,
            intercept=arguments.intercept,
            covariance_method=arguments.covariance,
            confidence_level=Decimal(arguments.confidence_level),
            missing_policy="drop_complete_rows",
            hac_lag=arguments.hac_lag,
            diagnostic_lag=arguments.diagnostic_lag,
        )
    exclusions = _reason_exclusion(result, subject_id=values[0].series_id)
    records = _coefficient_records_v21(result)
    parameters = {
        "confidence_level": arguments.confidence_level,
        "covariance": arguments.covariance,
        "dependent_series_id": values[0].series_id,
        "diagnostic_lag": arguments.diagnostic_lag,
        "hac_lag": arguments.hac_lag,
        "intercept": arguments.intercept,
        "missing_policy": result.missing_policy,
        "predictor_count": len(values) - 1,
    }
    main_diagnostic = DiagnosticV1(
        "stage10_ols_v2_1",
        "Rank-aware OLS used explicit covariance inference and fixed residual diagnostics.",
        fields_from_mapping(
            {
                **_common_metrics(values, reports),
                **_ols_metrics_v21(result),
                "alignment_policy": "outer_complete_rows",
                "diagnostic_lag": arguments.diagnostic_lag,
                "hac_finite_sample_correction": (
                    "n_over_n_minus_k"
                    if arguments.covariance
                    in {"hc1", "newey_west_hac_bartlett"}
                    else "none"
                ),
                "hac_kernel": (
                    "bartlett_fixed_lag"
                    if arguments.covariance == "newey_west_hac_bartlett"
                    else "not_applicable"
                ),
                "model_version": _MODEL_VERSION_V21,
                "residual_diagnostics": (
                    "ljung_box|breusch_pagan|jarque_bera"
                ),
            }
        ),
    )
    return QueryResult(
        tool="econometrics.regression",
        status="ok" if result.status == "established" else "not_established",
        records=records,
        matrices=_regression_matrices(
            rows,
            values,
            result,
            covariance_name=_covariance_matrix_name(result.covariance_method),
        ),
        diagnostics=(
            main_diagnostic,
            *_residual_diagnostic_entries(result),
        ),
        warnings=_warnings(
            values,
            *result.warnings,
            *(
                warning
                for diagnostic in result.residual_diagnostics
                for warning in diagnostic.warnings
            ),
            (
                "classical_homoskedastic_inference"
                if result.inference_distribution == "student_t"
                else "robust_asymptotic_normal_inference"
            ),
            "fixed_lag_residual_diagnostics",
            "asymptotic_chi_square_residual_diagnostics",
            "contemporaneous_regression_not_predictive_or_causal",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=TruncationV1(
            False, arguments.limit, len(records), len(records), False
        ),
        research_contract=_research_envelope(
            name="econometrics.regression",
            model_id="ordinary_least_squares_rank_aware_qr_robust_inference",
            parameters=parameters,
            sample={
                "aligned_row_count": len(rows),
                "excluded_count": result.excluded_count,
                "sample_size": result.sample_size,
            },
            uncertainty={
                "confidence_level": arguments.confidence_level,
                "covariance": arguments.covariance,
                "hac_lag": arguments.hac_lag,
                "inference_backend": result.inference_backend,
                "inference_distribution": result.inference_distribution,
                "residual_diagnostic_reference": "chi_square_asymptotic",
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
            model_version=_MODEL_VERSION_V21,
        ),
    )



def _rolling_matrix(
    name: str,
    row_labels: tuple[str, ...],
    terms: tuple[str, ...],
    windows: Sequence[Any],
    field: str,
) -> MatrixV1:
    return MatrixV1(
        name,
        row_labels,
        terms,
        tuple(
            tuple(getattr(coefficient, field) for coefficient in window.result.coefficients)
            for window in windows
        ),
    )


def _rolling_regression(
    arguments: Stage10MarketRollingRegressionArgumentsV2,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    rows = _aligned_rows(values, limit=5_000)
    if rows:
        dependent, predictors = _regression_vectors(rows)
        windows = rolling_ols(
            dependent,
            predictors,
            window=arguments.window,
            intercept=arguments.intercept,
            covariance=arguments.covariance,
            confidence_level=Decimal(arguments.confidence_level),
        )
    else:
        windows = ()
    if len(windows) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 rolling regression exceeds its declared window limit"
        )
    established_count = sum(
        window.result.status == "established" for window in windows
    )
    records = tuple(
        RecordV1(
            "stage10_rolling_ols_window",
            fields_from_mapping(
                {
                    "adjusted_r_squared": window.result.adjusted_r_squared,
                    "degrees_of_freedom_residual": window.result.degrees_of_freedom_residual,
                    "end_index": window.end_index,
                    "end_period": rows[window.end_index]["period_end"],
                    "excluded_count": window.result.excluded_count,
                    "r_squared": window.result.r_squared,
                    "reason": window.result.reason,
                    "residual_sum_squares": window.result.residual_sum_squares,
                    "sample_size": window.result.sample_size,
                    "start_index": window.start_index,
                    "start_period": rows[window.start_index]["period_start"],
                    "status": window.result.status,
                }
            ),
        )
        for window in windows
    )
    if not records:
        records = (
            RecordV1(
                "stage10_rolling_ols_summary",
                fields_from_mapping(
                    {
                        "reason": (
                            "no_aligned_observations"
                            if not rows
                            else "no_complete_window_range"
                        ),
                        "status": "not_established",
                        "window": arguments.window,
                    }
                ),
            ),
        )
    window_labels = tuple(
        f"{index}:{rows[window.start_index]['period_start']}:{rows[window.end_index]['period_end']}"
        for index, window in enumerate(windows)
    )
    terms = (
        windows[0].result.coefficient_names
        if windows
        else (("intercept",) if arguments.intercept else ())
        + tuple(f"x_{index}" for index in range(1, len(values)))
    )
    matrices = ()
    if windows:
        matrices = tuple(
            _rolling_matrix(name, window_labels, terms, windows, field)
            for name, field in (
                ("rolling_coefficient_estimate", "estimate"),
                ("rolling_standard_error", "standard_error"),
                ("rolling_t_statistic", "t_statistic"),
                ("rolling_p_value", "p_value"),
                ("rolling_confidence_interval_lower", "confidence_interval_lower"),
                ("rolling_confidence_interval_upper", "confidence_interval_upper"),
            )
        )
    not_established_count = len(windows) - established_count
    exclusions = (
        ()
        if not_established_count == 0 and windows
        else (
            ExclusionV1(
                code="rolling_windows_not_established",
                reason=(
                    f"{not_established_count} fixed windows were not established."
                    if windows
                    else "No fixed rolling windows were available."
                ),
            ),
        )
    )
    parameters = {
        "confidence_level": arguments.confidence_level,
        "covariance": arguments.covariance,
        "dependent_series_id": values[0].series_id,
        "intercept": arguments.intercept,
        "missing_policy": "reject_incomplete_rows",
        "predictor_count": len(values) - 1,
        "window": arguments.window,
    }
    status = "ok" if established_count else "not_established"
    return QueryResult(
        tool="econometrics.rolling_regression",
        status=status,
        records=records,
        matrices=matrices,
        diagnostics=(
            DiagnosticV1(
                "stage10_rolling_ols_v2",
                "The exact OLS v2 kernel ran over every fixed contiguous window.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "alignment_policy": "outer_fixed_contiguous_windows",
                        "established_window_count": established_count,
                        "inference_backend": (
                            windows[0].result.inference_backend if windows else None
                        ),
                        "model_version": _MODEL_VERSION,
                        "not_established_window_count": not_established_count,
                        "window": arguments.window,
                        "window_count": len(windows),
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *(
                warning
                for window in windows
                for warning in window.result.warnings
            ),
            "no_aligned_observations" if not rows else "",
            "classical_homoskedastic_inference",
            "fixed_window_samples_not_shrunk",
            "contemporaneous_regression_not_predictive_or_causal",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=TruncationV1(False, arguments.limit, len(records), len(records), False),
        research_contract=_research_envelope(
            name="econometrics.rolling_regression",
            model_id="rolling_ordinary_least_squares_rank_aware_qr",
            parameters=parameters,
            sample={
                "aligned_row_count": len(rows),
                "established_window_count": established_count,
                "not_established_window_count": not_established_count,
                "window_count": len(windows),
            },
            uncertainty={
                "confidence_level": arguments.confidence_level,
                "covariance": arguments.covariance,
                "inference_backend": (
                    windows[0].result.inference_backend if windows else None
                ),
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
        ),
    )
def _rolling_residual_fields(result: OLSResult) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for diagnostic in result.residual_diagnostics:
        prefix = diagnostic.name
        fields.update(
            {
                f"{prefix}_decision": diagnostic.decision,
                f"{prefix}_p_value": diagnostic.p_value,
                f"{prefix}_reason": diagnostic.reason,
                f"{prefix}_statistic": diagnostic.statistic,
                f"{prefix}_status": diagnostic.status,
            }
        )
    return fields


def _rolling_regression_v21(
    arguments: Stage10MarketRollingRegressionArgumentsV21,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    rows = _aligned_rows(values, limit=5_000)
    if rows:
        dependent, predictors = _regression_vectors(rows)
        windows = rolling_ols(
            dependent,
            predictors,
            window=arguments.window,
            intercept=arguments.intercept,
            covariance=arguments.covariance,
            hac_lag=arguments.hac_lag,
            diagnostic_lag=arguments.diagnostic_lag,
            confidence_level=Decimal(arguments.confidence_level),
        )
    else:
        windows = ()
    if len(windows) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 rolling regression exceeds its declared window limit"
        )
    established_count = sum(
        window.result.status == "established" for window in windows
    )
    inference_established_count = sum(
        window.result.status == "established"
        and bool(window.result.coefficients)
        and all(
            item.standard_error is not None
            for item in window.result.coefficients
        )
        for window in windows
    )
    records = tuple(
        RecordV1(
            "stage10_rolling_ols_window_v2_1",
            fields_from_mapping(
                {
                    "adjusted_r_squared": window.result.adjusted_r_squared,
                    "covariance_method": window.result.covariance_method,
                    "degrees_of_freedom_residual": (
                        window.result.degrees_of_freedom_residual
                    ),
                    "end_index": window.end_index,
                    "end_period": rows[window.end_index]["period_end"],
                    "excluded_count": window.result.excluded_count,
                    "hac_lag": window.result.hac_lag,
                    "inference_distribution": (
                        window.result.inference_distribution
                    ),
                    "r_squared": window.result.r_squared,
                    "reason": window.result.reason,
                    "residual_sum_squares": (
                        window.result.residual_sum_squares
                    ),
                    "sample_size": window.result.sample_size,
                    "standardized_statistic_kind": (
                        "t"
                        if window.result.inference_distribution == "student_t"
                        else "z"
                    ),
                    "start_index": window.start_index,
                    "start_period": rows[window.start_index]["period_start"],
                    "status": window.result.status,
                    **_rolling_residual_fields(window.result),
                }
            ),
        )
        for window in windows
    )
    if not records:
        records = (
            RecordV1(
                "stage10_rolling_ols_summary_v2_1",
                fields_from_mapping(
                    {
                        "reason": (
                            "no_aligned_observations"
                            if not rows
                            else "no_complete_window_range"
                        ),
                        "status": "not_established",
                        "window": arguments.window,
                    }
                ),
            ),
        )
    window_labels = tuple(
        f"{index}:{rows[window.start_index]['period_start']}:{rows[window.end_index]['period_end']}"
        for index, window in enumerate(windows)
    )
    terms = (
        windows[0].result.coefficient_names
        if windows
        else (("intercept",) if arguments.intercept else ())
        + tuple(f"x_{index}" for index in range(1, len(values)))
    )
    matrices = ()
    if windows:
        matrices = tuple(
            _rolling_matrix(name, window_labels, terms, windows, field)
            for name, field in (
                ("rolling_coefficient_estimate", "estimate"),
                ("rolling_standard_error", "standard_error"),
                ("rolling_standardized_statistic", "t_statistic"),
                ("rolling_p_value", "p_value"),
                (
                    "rolling_confidence_interval_lower",
                    "confidence_interval_lower",
                ),
                (
                    "rolling_confidence_interval_upper",
                    "confidence_interval_upper",
                ),
            )
        )
    not_established_count = len(windows) - established_count
    exclusions = (
        ()
        if not_established_count == 0 and windows
        else (
            ExclusionV1(
                code="rolling_windows_not_established",
                reason=(
                    f"{not_established_count} fixed windows were not established."
                    if windows
                    else "No fixed rolling windows were available."
                ),
            ),
        )
    )
    parameters = {
        "confidence_level": arguments.confidence_level,
        "covariance": arguments.covariance,
        "dependent_series_id": values[0].series_id,
        "diagnostic_lag": arguments.diagnostic_lag,
        "hac_lag": arguments.hac_lag,
        "intercept": arguments.intercept,
        "missing_policy": "reject_incomplete_rows",
        "predictor_count": len(values) - 1,
        "window": arguments.window,
    }
    diagnostic_counts = {
        name: sum(
            any(
                diagnostic.name == name
                and diagnostic.status == "established"
                for diagnostic in window.result.residual_diagnostics
            )
            for window in windows
        )
        for name in ("ljung_box", "breusch_pagan", "jarque_bera")
    }
    status = "ok" if established_count else "not_established"
    inference_distribution = (
        windows[0].result.inference_distribution
        if windows
        else (
            "student_t"
            if arguments.covariance == "classical_homoskedastic"
            else "normal_asymptotic"
        )
    )
    return QueryResult(
        tool="econometrics.rolling_regression",
        status=status,
        records=records,
        matrices=matrices,
        diagnostics=(
            DiagnosticV1(
                "stage10_rolling_ols_v2_1",
                "Robust OLS and fixed diagnostics ran independently in each contiguous window.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "alignment_policy": "outer_fixed_contiguous_windows",
                        "breusch_pagan_established_window_count": (
                            diagnostic_counts["breusch_pagan"]
                        ),
                        "diagnostic_lag": arguments.diagnostic_lag,
                        "established_window_count": established_count,
                        "hac_lag": arguments.hac_lag,
                        "inference_backend": (
                            windows[0].result.inference_backend
                            if windows
                            else None
                        ),
                        "inference_distribution": inference_distribution,
                        "inference_established_window_count": (
                            inference_established_count
                        ),
                        "jarque_bera_established_window_count": (
                            diagnostic_counts["jarque_bera"]
                        ),
                        "ljung_box_established_window_count": (
                            diagnostic_counts["ljung_box"]
                        ),
                        "model_version": _MODEL_VERSION_V21,
                        "not_established_window_count": (
                            not_established_count
                        ),
                        "window": arguments.window,
                        "window_count": len(windows),
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *(
                warning
                for window in windows
                for warning in window.result.warnings
            ),
            *(
                warning
                for window in windows
                for diagnostic in window.result.residual_diagnostics
                for warning in diagnostic.warnings
            ),
            "no_aligned_observations" if not rows else "",
            (
                "classical_homoskedastic_inference"
                if inference_distribution == "student_t"
                else "robust_asymptotic_normal_inference"
            ),
            "fixed_lag_residual_diagnostics",
            "asymptotic_chi_square_residual_diagnostics",
            "fixed_window_samples_not_shrunk",
            "contemporaneous_regression_not_predictive_or_causal",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=TruncationV1(
            False, arguments.limit, len(records), len(records), False
        ),
        research_contract=_research_envelope(
            name="econometrics.rolling_regression",
            model_id=(
                "rolling_ordinary_least_squares_rank_aware_qr_robust_inference"
            ),
            parameters=parameters,
            sample={
                "aligned_row_count": len(rows),
                "established_window_count": established_count,
                "not_established_window_count": not_established_count,
                "window_count": len(windows),
            },
            uncertainty={
                "confidence_level": arguments.confidence_level,
                "covariance": arguments.covariance,
                "hac_lag": arguments.hac_lag,
                "inference_backend": (
                    windows[0].result.inference_backend
                    if windows
                    else None
                ),
                "inference_distribution": inference_distribution,
                "residual_diagnostic_reference": "chi_square_asymptotic",
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
            model_version=_MODEL_VERSION_V21,
        ),
    )




def _stationarity(
    arguments: Stage10MarketStationarityArgumentsV2,
) -> QueryResult:
    series = arguments.series
    values = (series,)
    reports = _validated_inputs(values)
    if len(series.observations) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 stationarity exceeds its declared observation limit"
        )
    result: ADFResult = augmented_dickey_fuller(
        tuple(item.value for item in series.observations),
        lag=arguments.lag,
        deterministic=arguments.deterministic,
        significance=Decimal(arguments.significance),
    )
    critical_values = dict(result.critical_values)
    rejections = dict(result.rejections)
    regression = result.regression
    lagged_level = (
        regression.coefficients[1]
        if regression is not None and len(regression.coefficients) > 1
        else None
    )
    record = RecordV1(
        "stage10_adf_test",
        fields_from_mapping(
            {
                "adf_statistic": result.adf_statistic,
                "alternative_hypothesis": result.alternative_hypothesis,
                "critical_value_0_01": critical_values.get(Decimal("0.01")),
                "critical_value_0_05": critical_values.get(Decimal("0.05")),
                "critical_value_0_10": critical_values.get(Decimal("0.10")),
                "decision": result.decision,
                "deterministic": result.deterministic,
                "effective_sample_size": result.effective_sample_size,
                "excluded_count": result.excluded_count,
                "lag": result.lag,
                "lagged_level_coefficient": (
                    lagged_level.estimate if lagged_level is not None else None
                ),
                "lagged_level_standard_error": (
                    lagged_level.standard_error if lagged_level is not None else None
                ),
                "null_hypothesis": result.null_hypothesis,
                "p_value": result.p_value,
                "p_value_status": "not_provided_use_mackinnon_critical_values",
                "reason": result.reason,
                "reject_0_01": rejections.get(Decimal("0.01")),
                "reject_0_05": rejections.get(Decimal("0.05")),
                "reject_0_10": rejections.get(Decimal("0.10")),
                "sample_size": result.sample_size,
                "selected_significance": result.selected_significance,
                "status": result.status,
            }
        ),
    )
    exclusions = (
        ()
        if result.status == "established"
        else (
            ExclusionV1(
                code="not_established",
                reason=result.reason or "adf_not_established",
                subject_id=series.series_id,
            ),
        )
    )
    parameters = {
        "deterministic": arguments.deterministic,
        "lag": arguments.lag,
        "lag_policy": "fixed_no_autolag",
        "significance": arguments.significance,
    }
    return QueryResult(
        tool="econometrics.stationarity",
        status="ok" if result.status == "established" else "not_established",
        records=(record,),
        diagnostics=(
            DiagnosticV1(
                "stage10_adf_v2",
                "Fixed-lag ADF used MacKinnon finite-sample critical values.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "critical_value_method": result.critical_value_method,
                        "decision": result.decision,
                        "deterministic": result.deterministic,
                        "effective_sample_size": result.effective_sample_size,
                        "lag": result.lag,
                        "model_version": _MODEL_VERSION,
                        "p_value_status": "not_provided",
                        "selected_significance": result.selected_significance,
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *result.warnings,
            "fixed_lag_no_autolag",
            "stationarity_decision_is_in_sample_not_predictive",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=TruncationV1(False, arguments.limit, 1, 1, False),
        research_contract=_research_envelope(
            name="econometrics.stationarity",
            model_id="augmented_dickey_fuller_constant_fixed_lag",
            parameters=parameters,
            sample={
                "effective_sample_size": result.effective_sample_size,
                "excluded_count": result.excluded_count,
                "sample_size": result.sample_size,
            },
            uncertainty={
                "critical_value_method": result.critical_value_method,
                "p_value_status": "not_provided",
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
        ),
    )


def _stationarity_v21(
    arguments: Stage10MarketStationarityArgumentsV21,
) -> QueryResult:
    series = arguments.series
    values = (series,)
    reports = _validated_inputs(values)
    if len(series.observations) > arguments.limit:
        raise ResourceLimitError(
            "Stage 10 stationarity exceeds its declared observation limit"
        )

    significance = Decimal(arguments.significance)
    source_values = tuple(item.value for item in series.observations)
    adf = augmented_dickey_fuller(
        source_values,
        lag=arguments.adf_lag,
        deterministic=arguments.deterministic,
        significance=significance,
    )
    kpss: KPSSResult = kpss_level_stationarity(
        source_values,
        lag=arguments.kpss_lag,
        deterministic=arguments.deterministic,
        significance=significance,
    )

    legacy_adf = _stationarity(
        Stage10MarketStationarityArgumentsV2(
            series=series,
            deterministic=arguments.deterministic,
            lag=arguments.adf_lag,
            significance=arguments.significance,
            limit=arguments.limit,
        )
    )
    kpss_critical_values = dict(kpss.critical_values)
    kpss_rejections = dict(kpss.rejections)
    kpss_record = RecordV1(
        "stage10_kpss_test_v2_1",
        fields_from_mapping(
            {
                "alternative_hypothesis": kpss.alternative_hypothesis,
                "critical_value_0_01": kpss_critical_values.get(Decimal("0.01")),
                "critical_value_0_025": kpss_critical_values.get(Decimal("0.025")),
                "critical_value_0_05": kpss_critical_values.get(Decimal("0.05")),
                "critical_value_0_10": kpss_critical_values.get(Decimal("0.10")),
                "decision": kpss.decision,
                "deterministic": kpss.deterministic,
                "excluded_count": kpss.excluded_count,
                "kpss_statistic": kpss.kpss_statistic,
                "lag": kpss.lag,
                "long_run_variance": kpss.long_run_variance,
                "null_hypothesis": kpss.null_hypothesis,
                "p_value": kpss.p_value,
                "p_value_status": (
                    "not_provided_use_kpss_1992_level_critical_values"
                ),
                "partial_sum_squares": kpss.partial_sum_squares,
                "reason": kpss.reason,
                "reject_0_01": kpss_rejections.get(Decimal("0.01")),
                "reject_0_025": kpss_rejections.get(Decimal("0.025")),
                "reject_0_05": kpss_rejections.get(Decimal("0.05")),
                "reject_0_10": kpss_rejections.get(Decimal("0.10")),
                "sample_size": kpss.sample_size,
                "selected_significance": kpss.selected_significance,
                "status": kpss.status,
            }
        ),
    )

    joint_status = "not_established"
    interpretation: str | None = None
    if adf.status == "established" and kpss.status == "established":
        joint_status = "established"
        adf_rejects = dict(adf.rejections)[significance]
        kpss_rejects = dict(kpss.rejections)[significance]
        if adf_rejects and not kpss_rejects:
            interpretation = "evidence_consistent_with_level_stationarity"
        elif not adf_rejects and kpss_rejects:
            interpretation = "evidence_consistent_with_nonstationarity"
        elif not adf_rejects and not kpss_rejects:
            interpretation = "inconclusive"
        else:
            interpretation = "conflicting_evidence"

    joint_record = RecordV1(
        "stage10_stationarity_joint_v2_1",
        fields_from_mapping(
            {
                "adf_decision": adf.decision,
                "interpretation": interpretation,
                "kpss_decision": kpss.decision,
                "selected_significance": significance,
                "status": joint_status,
            }
        ),
    )
    exclusions: list[ExclusionV1] = []
    if adf.status != "established":
        exclusions.append(
            ExclusionV1(
                code="adf_not_established",
                reason=adf.reason or "adf_not_established",
                subject_id=series.series_id,
            )
        )
    if kpss.status != "established":
        exclusions.append(
            ExclusionV1(
                code="kpss_not_established",
                reason=kpss.reason or "kpss_not_established",
                subject_id=series.series_id,
            )
        )

    parameters = {
        "adf_lag": arguments.adf_lag,
        "adf_lag_policy": "fixed_no_autolag",
        "deterministic": arguments.deterministic,
        "kpss_lag": arguments.kpss_lag,
        "kpss_lag_policy": "fixed_bartlett_no_automatic_bandwidth",
        "observation_limit": arguments.limit,
        "significance": arguments.significance,
    }
    return QueryResult(
        tool="econometrics.stationarity",
        status="ok" if joint_status == "established" else "not_established",
        records=(legacy_adf.records[0], kpss_record, joint_record),
        diagnostics=(
            legacy_adf.diagnostics[0],
            DiagnosticV1(
                "stage10_kpss_v2_1",
                "Fixed-lag level KPSS used the published 1992 asymptotic critical values.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "critical_value_method": kpss.critical_value_method,
                        "decision": kpss.decision,
                        "deterministic": kpss.deterministic,
                        "lag": kpss.lag,
                        "model_version": _MODEL_VERSION_V21,
                        "p_value_status": "not_provided",
                        "selected_significance": kpss.selected_significance,
                        "status": kpss.status,
                    }
                ),
            ),
            DiagnosticV1(
                "stage10_stationarity_joint_v2_1",
                "ADF and KPSS decisions were interpreted jointly at one fixed significance level.",
                fields_from_mapping(
                    {
                        "adf_decision": adf.decision,
                        "interpretation": interpretation,
                        "joint_status": joint_status,
                        "kpss_decision": kpss.decision,
                        "model_version": _MODEL_VERSION_V21,
                        "selected_significance": significance,
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *adf.warnings,
            *kpss.warnings,
            "fixed_adf_lag_no_autolag",
            "fixed_kpss_bartlett_lag_no_automatic_bandwidth",
            "joint_stationarity_interpretation_is_in_sample_not_predictive",
            (
                "result_record_limit_raised_for_fixed_analytical_report"
                if arguments.limit < 3
                else ""
            ),
        ),
        exclusions=tuple(exclusions),
        lineage=_lineage(values),
        truncation=_complete_record_truncation(
            observation_limit=arguments.limit,
            returned_count=3,
        ),
        research_contract=_research_envelope(
            name="econometrics.stationarity",
            model_id="adf_constant_fixed_lag_plus_kpss_level_fixed_bartlett_lag",
            parameters=parameters,
            sample={
                "adf_effective_sample_size": adf.effective_sample_size,
                "complete_sample_size": kpss.sample_size,
                "excluded_count": max(adf.excluded_count, kpss.excluded_count),
                "joint_status": joint_status,
            },
            uncertainty={
                "adf_critical_value_method": adf.critical_value_method,
                "adf_p_value_status": "not_provided",
                "joint_interpretation": interpretation,
                "kpss_critical_value_method": kpss.critical_value_method,
                "kpss_p_value_status": "not_provided",
            },
            values=values,
            reports=reports,
            exclusions=tuple(exclusions),
            model_version=_MODEL_VERSION_V21,
        ),
    )


def _chow_fit_record(
    segment: str,
    fitted: OLSResult | None,
) -> RecordV1:
    if fitted is None:
        fields = {
            "reason": "fit_not_attempted",
            "segment": segment,
            "status": "not_established",
        }
    else:
        fields = {
            **_ols_metrics(fitted),
            "reason": fitted.reason,
            "segment": segment,
        }
    return RecordV1(
        "stage10_chow_ols_fit_v2",
        fields_from_mapping(fields),
    )


def _chow_coefficient_matrices(
    result: ChowBreakResult,
) -> tuple[MatrixV1, ...]:
    fitted = (
        result.pooled_fit,
        result.pre_break_fit,
        result.post_break_fit,
    )
    if any(item is None or item.status != "established" for item in fitted):
        return ()
    pooled, pre_break, post_break = fitted
    assert pooled is not None
    assert pre_break is not None
    assert post_break is not None
    if not (
        pooled.coefficient_names
        == pre_break.coefficient_names
        == post_break.coefficient_names
    ):
        return ()
    return (
        MatrixV1(
            "chow_coefficient_estimates",
            pooled.coefficient_names,
            ("pooled", "pre_break", "post_break"),
            tuple(
                (
                    pooled.coefficients[index].estimate,
                    pre_break.coefficients[index].estimate,
                    post_break.coefficients[index].estimate,
                )
                for index in range(len(pooled.coefficient_names))
            ),
        ),
    )


def _structural_breaks(
    arguments: Stage10MarketStructuralBreakArgumentsV2,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    rows = _aligned_rows(values, limit=arguments.limit)
    dependent, predictors = _regression_vectors(rows)
    result = chow_break_test(
        dependent,
        predictors,
        break_index=arguments.break_index,
        intercept=arguments.intercept,
        significance=Decimal(arguments.significance),
    )

    pre_break_end = (
        rows[arguments.break_index - 1]["period_end"]
        if rows and arguments.break_index <= len(rows)
        else None
    )
    post_break_start = (
        rows[arguments.break_index]["period_start"]
        if arguments.break_index < len(rows)
        else None
    )
    summary = RecordV1(
        "stage10_chow_test_v2",
        fields_from_mapping(
            {
                "alternative_hypothesis": result.alternative_hypothesis,
                "break_index": result.break_index,
                "break_index_semantics": "zero_based_first_post_break_row",
                "decision": result.decision,
                "denominator_degrees_of_freedom": (
                    result.denominator_degrees_of_freedom
                ),
                "excluded_count": result.excluded_count,
                "f_statistic": result.f_statistic,
                "inference_backend": result.inference_backend,
                "method": result.method,
                "null_hypothesis": result.null_hypothesis,
                "numerator_degrees_of_freedom": (
                    result.numerator_degrees_of_freedom
                ),
                "p_value": result.p_value,
                "parameter_count": result.parameter_count,
                "pooled_residual_sum_squares": (
                    result.pooled_residual_sum_squares
                ),
                "post_break_residual_sum_squares": (
                    result.post_break_residual_sum_squares
                ),
                "post_break_sample_size": result.post_break_sample_size,
                "post_break_start_period": post_break_start,
                "pre_break_end_period": pre_break_end,
                "pre_break_residual_sum_squares": (
                    result.pre_break_residual_sum_squares
                ),
                "pre_break_sample_size": result.pre_break_sample_size,
                "reason": result.reason,
                "reference_distribution": result.reference_distribution,
                "residual_sum_squares_reduction": (
                    result.residual_sum_squares_reduction
                ),
                "sample_size": result.sample_size,
                "significance": result.significance,
                "status": result.status,
                "unrestricted_residual_sum_squares": (
                    result.unrestricted_residual_sum_squares
                ),
            }
        ),
    )
    records = (
        summary,
        _chow_fit_record("pooled", result.pooled_fit),
        _chow_fit_record("pre_break", result.pre_break_fit),
        _chow_fit_record("post_break", result.post_break_fit),
    )
    exclusions = (
        (
            ExclusionV1(
                code="structural_break_not_established",
                reason=result.reason or "chow_test_not_established",
                subject_id=values[0].series_id,
            ),
        )
        if result.status != "established"
        else ()
    )
    parameters = {
        "break_index": arguments.break_index,
        "break_index_semantics": "zero_based_first_post_break_row",
        "break_search": "none_caller_declared_only",
        "covariance": "classical_homoskedastic",
        "intercept": arguments.intercept,
        "observation_limit": arguments.limit,
        "significance": arguments.significance,
    }
    return QueryResult(
        tool="econometrics.structural_breaks",
        status="ok" if result.status == "established" else "not_established",
        records=records,
        matrices=_chow_coefficient_matrices(result),
        diagnostics=(
            DiagnosticV1(
                "stage10_chow_fixed_break_v2",
                "Pooled and split OLS fits were compared at one caller-declared break.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "aligned_row_count": len(rows),
                        "break_index": arguments.break_index,
                        "break_search": "none",
                        "decision": result.decision,
                        "f_statistic": result.f_statistic,
                        "model_version": _MODEL_VERSION,
                        "p_value": result.p_value,
                        "reference_distribution": result.reference_distribution,
                        "status": result.status,
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *result.warnings,
            (
                "result_record_limit_raised_for_fixed_analytical_report"
                if arguments.limit < len(records)
                else ""
            ),
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=_complete_record_truncation(
            observation_limit=arguments.limit,
            returned_count=len(records),
        ),
        research_contract=_research_envelope(
            name="econometrics.structural_breaks",
            model_id="chow_pooled_vs_segmented_ols_fixed_break",
            parameters=parameters,
            sample={
                "aligned_sample_size": len(rows),
                "excluded_count": result.excluded_count,
                "post_break_sample_size": result.post_break_sample_size,
                "pre_break_sample_size": result.pre_break_sample_size,
            },
            uncertainty={
                "assumption": "gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
                "decision": result.decision,
                "inference_backend": result.inference_backend,
                "p_value": result.p_value,
                "reference_distribution": result.reference_distribution,
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
        ),
    )


def _v3_balanced_sample(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], int]:
    """Trim common incomplete edges; retain any interior gap for fail-closed kernels."""

    complete = tuple(
        all(value is not None for value in row["values"]) for row in rows
    )
    if not any(complete):
        return tuple(rows), 0
    first = complete.index(True)
    last = len(complete) - 1 - tuple(reversed(complete)).index(True)
    return tuple(rows[first : last + 1]), first + len(rows) - last - 1


def _v3_series_vectors(
    rows: Sequence[Mapping[str, Any]],
    *,
    series_count: int,
) -> tuple[tuple[Decimal | None, ...], ...]:
    return tuple(
        tuple(row["values"][series_index] for row in rows)
        for series_index in range(series_count)
    )


def _normalized_log_levels(
    vectors: Sequence[Sequence[Decimal | None]],
    *,
    return_method: str,
) -> tuple[tuple[tuple[Decimal | None, ...], ...], str | None]:
    levels: list[tuple[Decimal | None, ...]] = []
    with localcontext() as context:
        context.prec = 34
        for values in vectors:
            cumulative = Decimal("0")
            transformed: list[Decimal | None] = []
            for value in values:
                if value is None:
                    transformed.append(None)
                    continue
                if return_method == "log":
                    increment = value
                else:
                    factor = Decimal("1") + value
                    if factor <= 0:
                        return (), "nonpositive_simple_return_price_factor"
                    increment = factor.ln()
                cumulative += increment
                transformed.append(+cumulative)
            levels.append(tuple(transformed))
    return tuple(levels), None


def _v3_parameters(
    arguments: Stage10MarketRegressionModelSuiteArgumentsV3,
) -> dict[str, Decimal | str | int | bool | None]:
    return {
        "analysis": arguments.analysis,
        "deterministic": arguments.deterministic,
        "lag_order": arguments.lag_order,
        "lag_role": (
            "residual_adf_augmentation_lag"
            if arguments.analysis == "engle_granger_cointegration"
            else "var_lag_order"
        ),
        "missing_policy": (
            "trim_common_incomplete_edges_reject_any_interior_incomplete_row"
        ),
        "observation_limit": arguments.limit,
        "series_count": len(arguments.series),
        "significance": arguments.significance,
        "significance_role": (
            "cointegration_or_granger_decision"
            if arguments.analysis != "vector_autoregression"
            else "not_applicable_no_var_hypothesis_decision"
        ),
        "source_index": arguments.source_index,
        "target_index": arguments.target_index,
    }


def _v3_exclusions(
    *,
    edge_excluded_count: int,
    reason: str | None,
    subject_id: str,
) -> tuple[ExclusionV1, ...]:
    result: list[ExclusionV1] = []
    if edge_excluded_count:
        result.append(
            ExclusionV1(
                code="common_incomplete_edge_rows_trimmed",
                reason=(
                    f"{edge_excluded_count} incomplete rows outside the common "
                    "balanced coverage interval were excluded."
                ),
                subject_id=subject_id,
            )
        )
    if reason is not None:
        result.append(
            ExclusionV1(
                code="model_suite_not_established",
                reason=reason,
                subject_id=subject_id,
            )
        )
    return tuple(result)


def _v3_failure(
    *,
    arguments: Stage10MarketRegressionModelSuiteArgumentsV3,
    values: Sequence[TimeSeries],
    reports: Sequence[Stage10MarketQualityReport],
    aligned_row_count: int,
    balanced_row_count: int,
    edge_excluded_count: int,
    reason: str,
) -> QueryResult:
    parameters = _v3_parameters(arguments)
    exclusions = _v3_exclusions(
        edge_excluded_count=edge_excluded_count,
        reason=reason,
        subject_id=values[0].series_id,
    )
    records = (
        RecordV1(
            "stage10_econometric_model_suite_v3",
            fields_from_mapping(
                {
                    "analysis": arguments.analysis,
                    "reason": reason,
                    "status": "not_established",
                }
            ),
        ),
    )
    return QueryResult(
        tool="econometrics.regression",
        status="not_established",
        records=records,
        diagnostics=(
            DiagnosticV1(
                "stage10_econometric_model_suite_v3",
                "The selected fixed-specification model was not established.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "aligned_row_count": aligned_row_count,
                        "analysis": arguments.analysis,
                        "balanced_row_count": balanced_row_count,
                        "common_edge_excluded_count": edge_excluded_count,
                        "model_version": _MODEL_VERSION_V3,
                        "reason": reason,
                        "status": "not_established",
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            reason,
            "balanced_contiguous_observation_rows_not_calendar_adjacency",
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=_complete_record_truncation(
            observation_limit=arguments.limit,
            returned_count=len(records),
        ),
        research_contract=_research_envelope(
            name="econometrics.regression",
            model_id=f"fixed_specification_{arguments.analysis}_v3",
            parameters=parameters,
            sample={
                "aligned_row_count": aligned_row_count,
                "balanced_row_count": balanced_row_count,
                "common_edge_excluded_count": edge_excluded_count,
            },
            uncertainty={
                "reason": reason,
                "status": "not_established",
            },
            values=values,
            reports=reports,
            exclusions=exclusions,
            model_version=_MODEL_VERSION_V3,
        ),
    )


def _v3_ols_fit_fields(result: OLSResult | None) -> dict[str, Any]:
    if result is None:
        return {
            "adjusted_r_squared": None,
            "degrees_of_freedom_residual": None,
            "reason": "fit_not_available",
            "residual_sum_squares": None,
            "r_squared": None,
            "sample_size": 0,
            "status": "not_established",
        }
    return {
        "adjusted_r_squared": result.adjusted_r_squared,
        "degrees_of_freedom_residual": result.degrees_of_freedom_residual,
        "reason": result.reason,
        "residual_sum_squares": result.residual_sum_squares,
        "r_squared": result.r_squared,
        "sample_size": result.sample_size,
        "status": result.status,
    }


def _v3_engle_granger_records(
    result: EngleGrangerCointegrationResult,
    values: Sequence[TimeSeries],
) -> tuple[RecordV1, ...]:
    critical = dict(result.critical_values)
    rejections = dict(result.rejections)
    first_stage = result.first_stage_regression
    return (
        RecordV1(
            "stage10_engle_granger_cointegrating_regression_v3",
            fields_from_mapping(
                {
                    **_v3_ols_fit_fields(first_stage),
                    "dependent_series_id": values[0].series_id,
                    "direction": "series_0_on_series_1",
                    "intercept": result.cointegrating_intercept,
                    "regressor_series_id": values[1].series_id,
                    "slope": result.cointegrating_slope,
                    "transform": "normalized_log_level_from_horizon_one_returns",
                }
            ),
        ),
        RecordV1(
            "stage10_engle_granger_residual_adf_v3",
            fields_from_mapping(
                {
                    "alternative_hypothesis": result.alternative_hypothesis,
                    "critical_0_01": critical.get(Decimal("0.01")),
                    "critical_0_05": critical.get(Decimal("0.05")),
                    "critical_0_10": critical.get(Decimal("0.10")),
                    "critical_value_method": result.critical_value_method,
                    "critical_value_sample_size": max(
                        0, result.input_sample_size - 1
                    ),
                    "decision": result.decision,
                    "effective_sample_size": result.effective_sample_size,
                    "lag_order": result.lag,
                    "null_hypothesis": result.null_hypothesis,
                    "p_value": None,
                    "p_value_status": (
                        "not_provided_use_mackinnon_2010_cointegration_critical_values"
                    ),
                    "reject_0_01": rejections.get(Decimal("0.01")),
                    "reject_0_05": rejections.get(Decimal("0.05")),
                    "reject_0_10": rejections.get(Decimal("0.10")),
                    "residual_adf_gamma": result.residual_adf_gamma,
                    "residual_adf_gamma_standard_error": (
                        result.residual_adf_gamma_standard_error
                    ),
                    "residual_adf_statistic": result.residual_adf_statistic,
                    "selected_significance": result.selected_significance,
                    "status": result.status,
                }
            ),
        ),
    )


def _v3_engle_granger_matrix(
    rows: Sequence[Mapping[str, Any]],
    levels: Sequence[Sequence[Decimal | None]],
    result: EngleGrangerCointegrationResult,
) -> tuple[MatrixV1, ...]:
    if result.status != "established":
        return ()
    if not rows or not levels:
        return ()
    residuals = (
        result.first_stage_regression.residuals
        if result.first_stage_regression is not None
        else tuple(None for _ in rows)
    )
    return (
        MatrixV1(
            "engle_granger_normalized_log_levels",
            tuple(
                f"{index}:{row['period_start']}:{row['period_end']}"
                for index, row in enumerate(rows)
            ),
            ("dependent_level", "regressor_level", "cointegrating_residual"),
            tuple(
                (levels[0][index], levels[1][index], residuals[index])
                for index in range(len(rows))
            ),
        ),
    )


def _v3_var_records(
    result: VectorAutoregressionResult,
    values: Sequence[TimeSeries],
) -> tuple[RecordV1, ...]:
    if not result.equations:
        return (
            RecordV1(
                "stage10_var_equation_v3",
                fields_from_mapping(
                    {
                        "lag_order": result.lag_order,
                        "reason": result.reason,
                        "status": result.status,
                    }
                ),
            ),
        )
    return tuple(
        RecordV1(
            "stage10_var_equation_v3",
            fields_from_mapping(
                {
                    **_v3_ols_fit_fields(equation),
                    "equation_index": index,
                    "intercept": result.intercepts[index],
                    "lag_order": result.lag_order,
                    "target_series_id": values[index].series_id,
                }
            ),
        )
        for index, equation in enumerate(result.equations)
    )


def _v3_var_matrices(
    result: VectorAutoregressionResult | None,
    values: Sequence[TimeSeries],
) -> tuple[MatrixV1, ...]:
    if result is None or result.status != "established":
        return ()
    matrices: list[MatrixV1] = [
        MatrixV1(
            "var_equation_coefficients",
            result.coefficient_names,
            tuple(value.series_id for value in values),
            tuple(
                tuple(
                    equation.coefficients[row_index].estimate
                    for equation in result.equations
                )
                for row_index in range(len(result.coefficient_names))
            ),
        )
    ]
    assert result.lag_coefficient_matrices is not None
    for lag_index, matrix in enumerate(
        result.lag_coefficient_matrices, start=1
    ):
        matrices.append(
            MatrixV1(
                f"var_lag_{lag_index}_coefficients",
                tuple(value.series_id for value in values),
                tuple(value.series_id for value in values),
                matrix,
            )
        )
    if result.residual_covariance is not None:
        matrices.append(
            MatrixV1(
                "var_residual_covariance",
                tuple(value.series_id for value in values),
                tuple(value.series_id for value in values),
                result.residual_covariance,
            )
        )
    return tuple(matrices)


def _v3_granger_records(
    result: GrangerCausalityResult,
    values: Sequence[TimeSeries],
) -> tuple[RecordV1, ...]:
    records: list[RecordV1] = [
        RecordV1(
            "stage10_conditional_granger_causality_v3",
            fields_from_mapping(
                {
                    "alternative_hypothesis": result.alternative_hypothesis,
                    "decision": result.decision,
                    "denominator_degrees_of_freedom": (
                        result.denominator_degrees_of_freedom
                    ),
                    "f_statistic": result.f_statistic,
                    "lag_order": result.lag_order,
                    "null_hypothesis": result.null_hypothesis,
                    "numerator_degrees_of_freedom": (
                        result.numerator_degrees_of_freedom
                    ),
                    "p_value": result.p_value,
                    "reason": result.reason,
                    "reference_distribution": result.reference_distribution,
                    "restricted_residual_sum_squares": (
                        result.restricted_residual_sum_squares
                    ),
                    "significance": result.significance,
                    "source_index": result.source_index,
                    "source_series_id": values[result.source_index].series_id,
                    "status": result.status,
                    "target_index": result.target_index,
                    "target_series_id": values[result.target_index].series_id,
                    "unrestricted_residual_sum_squares": (
                        result.unrestricted_residual_sum_squares
                    ),
                }
            ),
        )
    ]
    for fit_name, fit in (
        ("unrestricted_target", result.unrestricted_target_regression),
        ("restricted_target", result.restricted_target_regression),
    ):
        if fit is not None:
            records.append(
                RecordV1(
                    "stage10_granger_target_equation_v3",
                    fields_from_mapping(
                        {
                            **_v3_ols_fit_fields(fit),
                            "fit": fit_name,
                            "target_series_id": (
                                values[result.target_index].series_id
                            ),
                        }
                    ),
                )
            )
    return tuple(records)


def _regression_model_suite_v3(
    arguments: Stage10MarketRegressionModelSuiteArgumentsV3,
) -> QueryResult:
    values = arguments.series
    reports = _validated_inputs(values)
    if any(report.return_horizon != 1 for report in reports):
        raise ValidationError(
            "Econometrics model suite v3 requires horizon-one returns"
        )
    all_rows = _aligned_rows(values, limit=arguments.limit)
    rows, edge_excluded_count = _v3_balanced_sample(all_rows)
    vectors = _v3_series_vectors(rows, series_count=len(values))
    parameters = _v3_parameters(arguments)
    model_id: str
    records: tuple[RecordV1, ...]
    matrices: tuple[MatrixV1, ...]
    reason: str | None
    warnings: tuple[str, ...]
    uncertainty: dict[str, Decimal | str | int | bool | None]
    effective_sample_size: int

    if arguments.analysis == "engle_granger_cointegration":
        levels, transform_reason = _normalized_log_levels(
            vectors,
            return_method=reports[0].return_method,
        )
        if transform_reason is not None:
            return _v3_failure(
                arguments=arguments,
                values=values,
                reports=reports,
                aligned_row_count=len(all_rows),
                balanced_row_count=len(rows),
                edge_excluded_count=edge_excluded_count,
                reason=transform_reason,
            )
        result = engle_granger_cointegration(
            levels,
            lag=arguments.lag_order,
            deterministic=arguments.deterministic,
            significance=Decimal(arguments.significance),
        )
        model_id = "engle_granger_two_step_mackinnon_2010_tau_c_n2"
        records = _v3_engle_granger_records(result, values)
        matrices = _v3_engle_granger_matrix(rows, levels, result)
        reason = result.reason
        warnings = (
            *result.warnings,
            "normalized_log_levels_reconstructed_from_horizon_one_returns",
            "unobserved_pre_sample_price_level_normalized_away",
        )
        uncertainty = {
            "critical_value_method": result.critical_value_method,
            "decision": result.decision,
            "p_value": None,
            "selected_significance": result.selected_significance,
        }
        effective_sample_size = result.effective_sample_size
    elif arguments.analysis == "vector_autoregression":
        result = vector_autoregression(
            vectors,
            lag_order=arguments.lag_order,
            deterministic=arguments.deterministic,
        )
        model_id = "fixed_order_reduced_form_var_ols_constant"
        records = _v3_var_records(result, values)
        matrices = _v3_var_matrices(result, values)
        reason = result.reason
        warnings = (
            *result.warnings,
            "significance_argument_not_used_for_var_estimation",
        )
        uncertainty = {
            "degrees_of_freedom_residual": (
                result.degrees_of_freedom_residual
            ),
            "inference": "equation_ols_no_joint_var_hypothesis_decision",
            "residual_covariance_denominator": (
                result.residual_covariance_denominator
            ),
        }
        effective_sample_size = result.effective_sample_size
    else:
        result = granger_causality(
            vectors,
            lag_order=arguments.lag_order,
            source_index=arguments.source_index,
            target_index=arguments.target_index,
            deterministic=arguments.deterministic,
            significance=Decimal(arguments.significance),
        )
        model_id = "conditional_granger_fixed_lag_exact_f"
        records = _v3_granger_records(result, values)
        matrices = _v3_var_matrices(result.unrestricted_var, values)
        reason = result.reason
        warnings = result.warnings
        uncertainty = {
            "assumption": (
                "gaussian_homoskedastic_independent_errors_exogenous_fixed_design"
            ),
            "decision": result.decision,
            "inference_backend": result.inference_backend,
            "p_value": result.p_value,
            "reference_distribution": result.reference_distribution,
            "significance": result.significance,
        }
        effective_sample_size = result.effective_sample_size

    exclusions = _v3_exclusions(
        edge_excluded_count=edge_excluded_count,
        reason=reason,
        subject_id=values[0].series_id,
    )
    status = "ok" if reason is None else "not_established"
    return QueryResult(
        tool="econometrics.regression",
        status=status,
        records=records,
        matrices=matrices,
        diagnostics=(
            DiagnosticV1(
                "stage10_econometric_model_suite_v3",
                "One explicit fixed-specification econometric model completed.",
                fields_from_mapping(
                    {
                        **_common_metrics(values, reports),
                        "aligned_row_count": len(all_rows),
                        "analysis": arguments.analysis,
                        "balanced_row_count": len(rows),
                        "common_edge_excluded_count": edge_excluded_count,
                        "effective_sample_size": effective_sample_size,
                        "lag_order": arguments.lag_order,
                        "model_version": _MODEL_VERSION_V3,
                        "reason": reason,
                        "status": (
                            "established"
                            if reason is None
                            else "not_established"
                        ),
                    }
                ),
            ),
        ),
        warnings=_warnings(
            values,
            *warnings,
            "balanced_contiguous_observation_rows_not_calendar_adjacency",
            (
                "common_incomplete_edge_rows_trimmed"
                if edge_excluded_count
                else ""
            ),
            (
                "result_record_limit_raised_for_fixed_analytical_report"
                if arguments.limit < len(records)
                else ""
            ),
        ),
        exclusions=exclusions,
        lineage=_lineage(values),
        truncation=_complete_record_truncation(
            observation_limit=arguments.limit,
            returned_count=len(records),
        ),
        research_contract=_research_envelope(
            name="econometrics.regression",
            model_id=model_id,
            parameters=parameters,
            sample={
                "aligned_row_count": len(all_rows),
                "balanced_row_count": len(rows),
                "common_edge_excluded_count": edge_excluded_count,
                "effective_sample_size": effective_sample_size,
                "interior_incomplete_row_count": sum(
                    any(value is None for value in row["values"])
                    for row in rows
                ),
            },
            uncertainty=uncertainty,
            values=values,
            reports=reports,
            exclusions=exclusions,
            model_version=_MODEL_VERSION_V3,
        ),
    )


def invoke_stage10_market_econometric(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> QueryResult:
    """Invoke one closed versioned econometric graph over supplied series."""

    if name not in VERSIONED_STAGE10_ECONOMETRICS_TOOLS:
        raise LookupError("Stage 10 econometric tool is not registered")
    route = {
        ("econometrics.regression", "2.0.0"): (
            Stage10MarketRegressionArgumentsV2,
            _regression,
            "v2",
        ),
        ("econometrics.rolling_regression", "2.0.0"): (
            Stage10MarketRollingRegressionArgumentsV2,
            _rolling_regression,
            "v2",
        ),
        ("econometrics.stationarity", "2.0.0"): (
            Stage10MarketStationarityArgumentsV2,
            _stationarity,
            "v2",
        ),
        ("econometrics.structural_breaks", "2.0.0"): (
            Stage10MarketStructuralBreakArgumentsV2,
            _structural_breaks,
            "v2",
        ),

        ("econometrics.stationarity", "2.1.0"): (
            Stage10MarketStationarityArgumentsV21,
            _stationarity_v21,
            "v2_1",
        ),
        ("econometrics.regression", "2.1.0"): (
            Stage10MarketRegressionArgumentsV21,
            _regression_v21,
            "v2_1",
        ),
        ("econometrics.rolling_regression", "2.1.0"): (
            Stage10MarketRollingRegressionArgumentsV21,
            _rolling_regression_v21,
            "v2_1",
        ),
        ("econometrics.regression", "3.0.0"): (
            Stage10MarketRegressionModelSuiteArgumentsV3,
            _regression_model_suite_v3,
            "v3",
        ),
    }.get((name, context.tool_version))
    if route is None:
        raise LookupError("Selected Stage 10 econometric version is invalid")
    expected_type, handler, graph_suffix = route
    expected_graph = f"tool_platform.{name}.{graph_suffix}"
    if context.operation_graph_id != expected_graph:
        raise LookupError("Selected Stage 10 econometric graph is invalid")
    if not isinstance(arguments, expected_type):
        raise ValidationError("Stage 10 econometric arguments use the wrong contract")
    raw_series = arguments["series"]
    values = raw_series if isinstance(raw_series, tuple) else (raw_series,)
    rows = max((len(item.observations) for item in values), default=0)
    requested = max(rows, arguments["limit"])
    operations = requested * max(len(values), 1) ** 2
    if isinstance(
        arguments,
        (
            Stage10MarketRollingRegressionArgumentsV2,
            Stage10MarketRollingRegressionArgumentsV21,
        ),
    ):
        operations *= arguments.window
    if isinstance(
        arguments,
        (
            Stage10MarketRegressionArgumentsV21,
            Stage10MarketRollingRegressionArgumentsV21,
        ),
    ):
        operations *= 1 + max(
            arguments.hac_lag,
            arguments.diagnostic_lag,
        )
    if isinstance(arguments, Stage10MarketStationarityArgumentsV21):
        operations *= 1 + arguments.adf_lag + arguments.kpss_lag
    if isinstance(arguments, Stage10MarketStructuralBreakArgumentsV2):
        operations *= 3
    if isinstance(arguments, Stage10MarketRegressionModelSuiteArgumentsV3):
        if arguments.analysis == "engle_granger_cointegration":
            operations = requested * (arguments.lag_order + 2) ** 2
        else:
            parameter_count = 1 + len(values) * arguments.lag_order
            operations = (
                requested * parameter_count**2
                + len(values) * requested * parameter_count
                + len(values) * parameter_count**2
            )
            if arguments.analysis == "granger_causality":
                restricted_count = parameter_count - arguments.lag_order
                operations += requested * restricted_count**2
    context.checkpoint()
    context.budget.require(
        rows=requested,
        series=len(values),
        operations=operations,
    )
    result = handler(arguments)
    context.checkpoint()
    return result


__all__ = (
    "VERSIONED_STAGE10_ECONOMETRICS_TOOLS",
    "invoke_stage10_market_econometric",
)
