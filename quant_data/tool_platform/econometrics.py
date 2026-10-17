"""Deterministic, bounded v2 econometric kernels over caller-supplied values.

This module deliberately has no store, provider, or public-tool concerns. It
keeps numerical work separate from frozen v1 helpers in analytics.py so later
adapters can expose a versioned contract without altering historical behaviour.

"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from functools import lru_cache
import math

from quant_data.errors import ResourceLimitError, ValidationError


DECIMAL_PRECISION = 34
MAX_REGRESSION_ROWS = 5_000
MAX_REGRESSION_PREDICTORS = 20
MAX_ADF_LAG = 18
MAX_KPSS_LAG = 18
MAX_HAC_LAG = 18
MAX_DIAGNOSTIC_LAG = 18
MAX_VAR_SERIES = 5
MAX_VAR_LAG = 4
MAX_ENGLE_GRANGER_LAG = 4
RANK_RELATIVE_TOLERANCE = Decimal("1e-24")
RANK_ABSOLUTE_TOLERANCE = Decimal("1e-28")
CLASSICAL_HOMOSKEDASTIC = "classical_homoskedastic"
HC1 = "hc1"
HC3 = "hc3"
NEWEY_WEST_HAC_BARTLETT = "newey_west_hac_bartlett"
STUDENT_T_INFERENCE_BACKEND = "python_math_binary64_rounded_12_significant_digits"
NORMAL_INFERENCE_BACKEND = "python_math_erfc_binary64_rounded_12_significant_digits"
CHI_SQUARE_INFERENCE_BACKEND = (
    "python_math_regularized_gamma_binary64_rounded_12_significant_digits"
)
F_INFERENCE_BACKEND = (
    "python_math_regularized_incomplete_beta_binary64_rounded_12_significant_digits"
)
F_REFERENCE_DISTRIBUTION = "fisher_snedecor_f_exact_finite_sample"
NORMAL_95_CRITICAL_VALUE = Decimal("1.95996398454")
DIAGNOSTIC_SIGNIFICANCE = Decimal("0.05")
MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD = (
    "mackinnon_2010_constant_N1_finite_sample_polynomial"
)
MAC_KINNON_2010_COINTEGRATION_CRITICAL_VALUE_METHOD = (
    "mackinnon_2010_tau_c_N2_finite_sample_polynomial"
)
KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD = (
    "kwiatkowski_phillips_schmidt_shin_1992_level_asymptotic_tabulation"
)


@dataclass(frozen=True, slots=True)
class CoefficientEstimate:
    """One fitted coefficient and its explicitly selected inference."""

    term: str
    estimate: Decimal | None
    standard_error: Decimal | None
    t_statistic: Decimal | None
    p_value: Decimal | None
    confidence_interval_lower: Decimal | None
    confidence_interval_upper: Decimal | None


@dataclass(frozen=True, slots=True)
class ResidualTestResult:
    """One explicitly specified residual diagnostic and its decision."""

    name: str
    status: str
    reason: str | None
    warnings: tuple[str, ...]
    statistic: Decimal | None
    p_value: Decimal | None
    degrees_of_freedom: int | None
    lag: int | None
    sample_size: int
    excluded_count: int
    significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    method: str
    reference_distribution: str
    inference_backend: str


@dataclass(frozen=True, slots=True)
class OLSResult:
    """Immutable result of a bounded complete-case ordinary least squares fit."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    coefficients: tuple[CoefficientEstimate, ...]
    coefficient_names: tuple[str, ...]
    fitted_values: tuple[Decimal | None, ...]
    residuals: tuple[Decimal | None, ...]
    complete_rows: tuple[bool, ...]
    excluded_indices: tuple[int, ...]
    sample_size: int
    excluded_count: int
    parameter_count: int
    rank: int | None
    rank_tolerance: Decimal | None
    degrees_of_freedom_model: int | None
    degrees_of_freedom_residual: int | None
    residual_sum_squares: Decimal | None
    total_sum_squares: Decimal | None
    r_squared: Decimal | None
    adjusted_r_squared: Decimal | None
    residual_variance: Decimal | None
    root_mean_squared_error: Decimal | None
    durbin_watson: Decimal | None
    covariance: tuple[tuple[Decimal, ...], ...] | None
    intercept_included: bool
    covariance_method: str
    confidence_level: Decimal
    missing_policy: str
    inference_backend: str
    t_critical_value: Decimal | None
    inference_distribution: str
    hac_lag: int
    residual_diagnostics: tuple[ResidualTestResult, ...]


@dataclass(frozen=True, slots=True)
class RollingOLSWindow:
    """An OLS result with inclusive source indices for its fixed window."""

    start_index: int
    end_index: int
    result: OLSResult


@dataclass(frozen=True, slots=True)
class ADFResult:
    """Fixed-lag, constant-only augmented Dickey-Fuller test result.

    The ADF statistic has a non-standard null distribution. p_value is
    intentionally always None: decisions use finite-sample MacKinnon critical
    values rather than a Student-t tail probability.
    """

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    sample_size: int
    excluded_count: int
    effective_sample_size: int
    lag: int
    deterministic: str
    adf_statistic: Decimal | None
    p_value: None
    critical_values: tuple[tuple[Decimal, Decimal], ...]
    rejections: tuple[tuple[Decimal, bool], ...]
    selected_significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    critical_value_method: str
    regression: OLSResult | None


@dataclass(frozen=True, slots=True)
class KPSSResult:
    """Fixed-lag, level-stationarity KPSS test result.

    The KPSS statistic has a non-standard null distribution. ``p_value`` is
    intentionally always ``None``: decisions use the published asymptotic
    critical-value table without interpolation.
    """

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    sample_size: int
    excluded_count: int
    lag: int
    deterministic: str
    kpss_statistic: Decimal | None
    p_value: None
    long_run_variance: Decimal | None
    partial_sum_squares: Decimal | None
    critical_values: tuple[tuple[Decimal, Decimal], ...]
    rejections: tuple[tuple[Decimal, bool], ...]
    selected_significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    critical_value_method: str


@dataclass(frozen=True, slots=True)
class ChowBreakResult:
    """Caller-declared single-break Chow test with its three OLS fits."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    sample_size: int
    excluded_count: int
    break_index: int
    pre_break_sample_size: int
    post_break_sample_size: int
    parameter_count: int
    numerator_degrees_of_freedom: int | None
    denominator_degrees_of_freedom: int | None
    pooled_residual_sum_squares: Decimal | None
    pre_break_residual_sum_squares: Decimal | None
    post_break_residual_sum_squares: Decimal | None
    unrestricted_residual_sum_squares: Decimal | None
    residual_sum_squares_reduction: Decimal | None
    f_statistic: Decimal | None
    p_value: Decimal | None
    significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    method: str
    reference_distribution: str
    inference_backend: str
    pooled_fit: OLSResult | None
    pre_break_fit: OLSResult | None
    post_break_fit: OLSResult | None


@dataclass(frozen=True, slots=True)
class _QRFactorization:
    q_columns: tuple[tuple[Decimal, ...], ...]
    upper_r: tuple[tuple[Decimal, ...], ...]
    permutation: tuple[int, ...]
    rank: int
    rank_tolerance: Decimal


_ADF_CONSTANT_CRITICAL_COEFFICIENTS: tuple[tuple[Decimal, tuple[Decimal, ...]], ...] = (
    (
        Decimal("0.01"),
        (Decimal("-3.43035"), Decimal("-6.5393"), Decimal("-16.786"), Decimal("-79.433")),
    ),
    (
        Decimal("0.05"),
        (Decimal("-2.86154"), Decimal("-2.8903"), Decimal("-4.234"), Decimal("-40.040")),
    ),
    (
        Decimal("0.10"),
        (Decimal("-2.56677"), Decimal("-1.5384"), Decimal("-2.809"), Decimal("0")),
    ),
)
_ADF_SIGNIFICANCE_VALUES = tuple(level for level, _ in _ADF_CONSTANT_CRITICAL_COEFFICIENTS)
_KPSS_LEVEL_CRITICAL_VALUES: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0.01"), Decimal("0.739")),
    (Decimal("0.025"), Decimal("0.574")),
    (Decimal("0.05"), Decimal("0.463")),
    (Decimal("0.10"), Decimal("0.347")),
)


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be a finite Decimal or integer")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise ValidationError(f"{field} must be a finite Decimal or integer; floats are rejected")


def _sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field} must be a bounded sequence")
    return value


def _clean_decimal(value: Decimal) -> Decimal:
    """Canonicalize negative zero without rounding a calculated statistic."""

    return Decimal("0") if value.is_zero() else +value


def _dot(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal:
    return sum((a * b for a, b in zip(left, right, strict=True)), Decimal("0"))


def _unique_warnings(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _validate_confidence_level(value: object) -> Decimal:
    confidence_level = _decimal(value, "confidence_level")
    if confidence_level != Decimal("0.95"):
        raise ValidationError("only the fixed 0.95 confidence level is supported")
    return confidence_level


def _validate_covariance(value: object) -> str:
    supported = {
        CLASSICAL_HOMOSKEDASTIC,
        HC1,
        HC3,
        NEWEY_WEST_HAC_BARTLETT,
    }
    if value not in supported:
        raise ValidationError("unsupported OLS covariance method")
    return str(value)


def _validate_hac_lag(value: object, covariance_method: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("hac_lag must be an integer")
    if not 0 <= value <= MAX_HAC_LAG:
        raise ResourceLimitError("hac_lag exceeds the supported bound")
    if covariance_method != NEWEY_WEST_HAC_BARTLETT and value != 0:
        raise ValidationError("hac_lag must be zero unless Newey-West HAC is selected")
    return value


def _validate_diagnostic_lag(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("diagnostic_lag must be an integer")
    if not 1 <= value <= MAX_DIAGNOSTIC_LAG:
        raise ResourceLimitError("diagnostic_lag exceeds the supported bound")
    return value


def _validate_missing_policy(value: object) -> str:
    if value not in {"drop_complete_rows", "reject_incomplete_rows"}:
        raise ValidationError("missing must be drop_complete_rows or reject_incomplete_rows")
    return str(value)


def _coefficient_names(width: int, intercept: bool) -> tuple[str, ...]:
    features = tuple(f"x_{index + 1}" for index in range(width))
    return (("intercept",) if intercept else ()) + features


def _empty_coefficients(names: Sequence[str]) -> tuple[CoefficientEstimate, ...]:
    return tuple(CoefficientEstimate(name, None, None, None, None, None, None) for name in names)


def _not_established_residual_tests(
    *,
    reason: str,
    lag: int,
    sample_size: int,
    excluded_count: int,
) -> tuple[ResidualTestResult, ...]:
    specifications = (
        (
            "ljung_box",
            lag,
            "no_residual_autocorrelation_through_lag",
            "residual_autocorrelation_present",
            "ljung_box_q_fixed_lag_centered_residuals_model_df_0",
        ),
        (
            "breusch_pagan",
            None,
            "homoskedastic_residual_variance",
            "heteroskedastic_residual_variance",
            "koenker_breusch_pagan_lm_n_r_squared",
        ),
        (
            "jarque_bera",
            None,
            "residuals_have_normal_skewness_and_kurtosis",
            "residuals_depart_from_normal_skewness_or_kurtosis",
            "jarque_bera_population_moments",
        ),
    )
    return tuple(
        ResidualTestResult(
            name=name,
            status="not_established",
            reason=reason,
            warnings=("residual_diagnostic_not_established",),
            statistic=None,
            p_value=None,
            degrees_of_freedom=None,
            lag=test_lag,
            sample_size=sample_size,
            excluded_count=excluded_count,
            significance=DIAGNOSTIC_SIGNIFICANCE,
            decision=None,
            null_hypothesis=null_hypothesis,
            alternative_hypothesis=alternative_hypothesis,
            method=method,
            reference_distribution="chi_square_asymptotic",
            inference_backend=CHI_SQUARE_INFERENCE_BACKEND,
        )
        for name, test_lag, null_hypothesis, alternative_hypothesis, method in specifications
    )


def _not_established_ols(
    *,
    reason: str,
    warnings: tuple[str, ...],
    names: tuple[str, ...],
    rows: int,
    complete_rows: tuple[bool, ...],
    excluded_indices: tuple[int, ...],
    parameter_count: int,
    rank: int | None,
    rank_tolerance: Decimal | None,
    intercept: bool,
    covariance_method: str,
    confidence_level: Decimal,
    missing_policy: str,
    hac_lag: int = 0,
    diagnostic_lag: int | None = None,
) -> OLSResult:
    return OLSResult(
        status="not_established",
        reason=reason,
        warnings=warnings,
        coefficients=_empty_coefficients(names),
        coefficient_names=names,
        fitted_values=(None,) * rows,
        residuals=(None,) * rows,
        complete_rows=complete_rows,
        excluded_indices=excluded_indices,
        sample_size=sum(complete_rows),
        excluded_count=len(excluded_indices),
        parameter_count=parameter_count,
        rank=rank,
        rank_tolerance=rank_tolerance,
        degrees_of_freedom_model=None,
        degrees_of_freedom_residual=None,
        residual_sum_squares=None,
        total_sum_squares=None,
        r_squared=None,
        adjusted_r_squared=None,
        residual_variance=None,
        root_mean_squared_error=None,
        durbin_watson=None,
        covariance=None,
        intercept_included=intercept,
        covariance_method=covariance_method,
        confidence_level=confidence_level,
        missing_policy=missing_policy,
        inference_backend=(
            STUDENT_T_INFERENCE_BACKEND
            if covariance_method == CLASSICAL_HOMOSKEDASTIC
            else NORMAL_INFERENCE_BACKEND
        ),
        t_critical_value=None,
        inference_distribution=(
            "student_t"
            if covariance_method == CLASSICAL_HOMOSKEDASTIC
            else "normal_asymptotic"
        ),
        hac_lag=hac_lag,
        residual_diagnostics=(
            _not_established_residual_tests(
                reason=reason,
                lag=diagnostic_lag,
                sample_size=sum(complete_rows),
                excluded_count=len(excluded_indices),
            )
            if diagnostic_lag is not None
            else ()
        ),
    )


def _coerce_regression_inputs(
    y: Sequence[Decimal | int | None],
    x: Sequence[Sequence[Decimal | int | None]],
) -> tuple[
    tuple[Decimal | None, ...],
    tuple[tuple[Decimal | None, ...], ...],
    tuple[bool, ...],
    tuple[int, ...],
]:
    targets = _sequence(y, "y")
    rows = _sequence(x, "x")
    if len(targets) != len(rows):
        raise ValidationError("OLS y and x must have equal row counts")
    if len(targets) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("OLS exceeds the supported row limit")
    if not rows:
        raise ValidationError("OLS requires at least one feature row")
    first = _sequence(rows[0], "x/0")
    width = len(first)
    if not 1 <= width <= MAX_REGRESSION_PREDICTORS:
        raise ValidationError("OLS requires between one and twenty predictors")

    coerced_targets: list[Decimal | None] = []
    coerced_rows: list[tuple[Decimal | None, ...]] = []
    complete_rows: list[bool] = []
    excluded_indices: list[int] = []
    for index, (target, row_value) in enumerate(zip(targets, rows, strict=True)):
        row = _sequence(row_value, f"x/{index}")
        if len(row) != width:
            raise ValidationError("OLS requires equal-width feature rows")
        coerced_target = None if target is None else _decimal(target, f"y/{index}")
        coerced_row = tuple(
            None if value is None else _decimal(value, f"x/{index}/{column}")
            for column, value in enumerate(row)
        )
        complete = coerced_target is not None and all(value is not None for value in coerced_row)
        coerced_targets.append(coerced_target)
        coerced_rows.append(coerced_row)
        complete_rows.append(complete)
        if not complete:
            excluded_indices.append(index)
    return (
        tuple(coerced_targets),
        tuple(coerced_rows),
        tuple(complete_rows),
        tuple(excluded_indices),
    )


def _pivoted_qr(design: Sequence[Sequence[Decimal]]) -> _QRFactorization:
    """Modified Gram-Schmidt QR with deterministic column pivoting."""

    rows = len(design)
    columns_count = len(design[0])
    columns = [[design[row][column] for row in range(rows)] for column in range(columns_count)]
    permutation = list(range(columns_count))
    upper_r = [[Decimal("0") for _ in range(columns_count)] for _ in range(columns_count)]
    squared_norms = [_dot(column, column) for column in columns]
    scale = max((value.sqrt() for value in squared_norms), default=Decimal("0"))
    rank_tolerance = max(RANK_ABSOLUTE_TOLERANCE, scale * RANK_RELATIVE_TOLERANCE)
    q_columns: list[tuple[Decimal, ...]] = []

    for position in range(columns_count):
        pivot = max(
            range(position, columns_count),
            key=lambda candidate: (squared_norms[candidate], -permutation[candidate]),
        )
        if pivot != position:
            columns[position], columns[pivot] = columns[pivot], columns[position]
            squared_norms[position], squared_norms[pivot] = squared_norms[pivot], squared_norms[position]
            permutation[position], permutation[pivot] = permutation[pivot], permutation[position]
            for previous in range(position):
                upper_r[previous][position], upper_r[previous][pivot] = (
                    upper_r[previous][pivot],
                    upper_r[previous][position],
                )

        vector = columns[position]
        # A second pass keeps near-collinear Decimal inputs deterministic.
        for prior, q_column in enumerate(q_columns):
            correction = _dot(q_column, vector)
            if correction:
                upper_r[prior][position] += correction
                vector = [value - correction * basis for value, basis in zip(vector, q_column, strict=True)]
        norm = _dot(vector, vector).sqrt()
        if norm <= rank_tolerance:
            break
        upper_r[position][position] = norm
        q_column = tuple(value / norm for value in vector)
        q_columns.append(q_column)

        for candidate in range(position + 1, columns_count):
            residual = columns[candidate]
            projection = _dot(q_column, residual)
            if projection:
                upper_r[position][candidate] = projection
                residual = [
                    value - projection * basis
                    for value, basis in zip(residual, q_column, strict=True)
                ]
            correction = _dot(q_column, residual)
            if correction:
                upper_r[position][candidate] += correction
                residual = [
                    value - correction * basis
                    for value, basis in zip(residual, q_column, strict=True)
                ]
            columns[candidate] = residual
            squared_norms[candidate] = _dot(residual, residual)

    return _QRFactorization(
        q_columns=tuple(q_columns),
        upper_r=tuple(tuple(row) for row in upper_r),
        permutation=tuple(permutation),
        rank=len(q_columns),
        rank_tolerance=_clean_decimal(rank_tolerance),
    )


def _solve_upper_triangular(
    upper_r: Sequence[Sequence[Decimal]], rhs: Sequence[Decimal]
) -> tuple[Decimal, ...]:
    size = len(rhs)
    answer = [Decimal("0")] * size
    for row in range(size - 1, -1, -1):
        subtotal = sum(
            (upper_r[row][column] * answer[column] for column in range(row + 1, size)),
            Decimal("0"),
        )
        answer[row] = (rhs[row] - subtotal) / upper_r[row][row]
    return tuple(answer)


def _inverse_upper_triangular(upper_r: Sequence[Sequence[Decimal]]) -> tuple[tuple[Decimal, ...], ...]:
    size = len(upper_r)
    inverse = [[Decimal("0") for _ in range(size)] for _ in range(size)]
    for column in range(size):
        rhs = [Decimal("0")] * size
        rhs[column] = Decimal("1")
        solved = _solve_upper_triangular(upper_r, rhs)
        for row, value in enumerate(solved):
            inverse[row][column] = value
    return tuple(tuple(row) for row in inverse)


def _covariance_from_qr(
    factorization: _QRFactorization,
    residual_variance: Decimal,
    parameter_count: int,
) -> tuple[tuple[Decimal, ...], ...]:
    upper_r = tuple(tuple(row[:parameter_count]) for row in factorization.upper_r[:parameter_count])
    inverse_r = _inverse_upper_triangular(upper_r)
    pivoted_covariance = [
        [
            residual_variance * sum(
                (
                    inverse_r[row][column] * inverse_r[other][column]
                    for column in range(parameter_count)
                ),
                Decimal("0"),
            )
            for other in range(parameter_count)
        ]
        for row in range(parameter_count)
    ]
    covariance = [[Decimal("0") for _ in range(parameter_count)] for _ in range(parameter_count)]
    for pivoted_row, original_row in enumerate(factorization.permutation):
        for pivoted_column, original_column in enumerate(factorization.permutation):
            covariance[original_row][original_column] = _clean_decimal(
                pivoted_covariance[pivoted_row][pivoted_column]
            )
    return tuple(tuple(row) for row in covariance)


def _accumulate_outer_product(
    target: list[list[Decimal]],
    left: Sequence[Decimal],
    right: Sequence[Decimal],
    *,
    weight: Decimal = Decimal("1"),
) -> None:
    for row_index, left_value in enumerate(left):
        scaled = weight * left_value
        for column_index, right_value in enumerate(right):
            target[row_index][column_index] += scaled * right_value


def _sandwich_covariance(
    *,
    design: Sequence[Sequence[Decimal]],
    residuals: Sequence[Decimal],
    factorization: _QRFactorization,
    parameter_count: int,
    method: str,
    hac_lag: int,
    has_gaps: bool,
) -> tuple[tuple[tuple[Decimal, ...], ...] | None, tuple[str, ...]]:
    if method not in {HC1, HC3, NEWEY_WEST_HAC_BARTLETT}:
        raise AssertionError("sandwich covariance requires a robust method")
    sample_size = len(design)
    if method == NEWEY_WEST_HAC_BARTLETT:
        if has_gaps:
            return None, ("hac_requires_contiguous_complete_observations",)
        if hac_lag >= sample_size:
            return None, ("hac_lag_exceeds_complete_sample",)

    bread = _covariance_from_qr(
        factorization, Decimal("1"), parameter_count
    )
    meat = [
        [Decimal("0") for _ in range(parameter_count)]
        for _ in range(parameter_count)
    ]
    if method == HC3:
        for observation_index, (row, residual) in enumerate(
            zip(design, residuals, strict=True)
        ):
            leverage = sum(
                (column[observation_index] ** 2 for column in factorization.q_columns),
                Decimal("0"),
            )
            complement = Decimal("1") - leverage
            if complement <= RANK_RELATIVE_TOLERANCE:
                return None, ("hc3_unit_leverage_inference_not_established",)
            adjusted_score = tuple(
                value * residual / complement for value in row
            )
            _accumulate_outer_product(meat, adjusted_score, adjusted_score)
        correction = Decimal("1")
        warnings = ("hc3_leverage_adjusted_covariance",)
    else:
        scores = tuple(
            tuple(value * residual for value in row)
            for row, residual in zip(design, residuals, strict=True)
        )
        for score in scores:
            _accumulate_outer_product(meat, score, score)
        if method == NEWEY_WEST_HAC_BARTLETT:
            for lag in range(1, hac_lag + 1):
                weight = Decimal(hac_lag + 1 - lag) / Decimal(hac_lag + 1)
                for index in range(lag, sample_size):
                    current = scores[index]
                    prior = scores[index - lag]
                    _accumulate_outer_product(
                        meat, current, prior, weight=weight
                    )
                    _accumulate_outer_product(
                        meat, prior, current, weight=weight
                    )
            warnings = (
                "newey_west_hac_bartlett_fixed_lag",
                "hac_finite_sample_n_over_n_minus_k_correction",
            )
        else:
            warnings = ("hc1_finite_sample_n_over_n_minus_k_correction",)
        correction = Decimal(sample_size) / Decimal(
            sample_size - parameter_count
        )

    scaled_meat = tuple(
        tuple(correction * value for value in row) for row in meat
    )
    left_product = tuple(
        tuple(
            sum(
                (bread[row][inner] * scaled_meat[inner][column]
                 for inner in range(parameter_count)),
                Decimal("0"),
            )
            for column in range(parameter_count)
        )
        for row in range(parameter_count)
    )
    raw_covariance = [
        [
            sum(
                (left_product[row][inner] * bread[inner][column]
                 for inner in range(parameter_count)),
                Decimal("0"),
            )
            for column in range(parameter_count)
        ]
        for row in range(parameter_count)
    ]
    for row in range(parameter_count):
        for column in range(row, parameter_count):
            symmetric = _clean_decimal(
                (raw_covariance[row][column] + raw_covariance[column][row])
                / Decimal("2")
            )
            raw_covariance[row][column] = symmetric
            raw_covariance[column][row] = symmetric
    return tuple(tuple(row) for row in raw_covariance), warnings


def _float_to_decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        if value > 0:
            return Decimal("Infinity")
        if value < 0:
            return Decimal("-Infinity")
        raise ArithmeticError("non-finite floating-point inference value")
    return Decimal(format(value, ".12g"))


def _normal_two_sided_p_value(statistic: Decimal) -> Decimal:
    if statistic.is_zero():
        return Decimal("1")
    if statistic.adjusted() > 300:
        return Decimal("0")
    absolute_value = float(abs(statistic))
    if not math.isfinite(absolute_value):
        return Decimal("0")
    return _float_to_decimal(math.erfc(absolute_value / math.sqrt(2.0)))


def _regularized_gamma_q(shape: float, value: float) -> float:
    """Evaluate the upper regularized gamma function in bounded binary64."""

    if shape <= 0.0 or value < 0.0:
        raise ValueError("regularized gamma inputs are invalid")
    if value == 0.0:
        return 1.0
    maximum_iterations = 512
    epsilon = 3.0e-14
    floor = 1.0e-300
    log_scale = -value + shape * math.log(value) - math.lgamma(shape)
    if value < shape + 1.0:
        term = 1.0 / shape
        total = term
        denominator = shape
        for _ in range(maximum_iterations):
            denominator += 1.0
            term *= value / denominator
            total += term
            if abs(term) <= abs(total) * epsilon:
                break
        lower = total * math.exp(log_scale)
        return min(1.0, max(0.0, 1.0 - lower))

    b_value = value + 1.0 - shape
    c_value = 1.0 / floor
    d_value = 1.0 / max(abs(b_value), floor)
    if b_value < 0.0:
        d_value = -d_value
    result = d_value
    for iteration in range(1, maximum_iterations + 1):
        coefficient = -float(iteration) * (float(iteration) - shape)
        b_value += 2.0
        d_value = coefficient * d_value + b_value
        if abs(d_value) < floor:
            d_value = floor
        c_value = b_value + coefficient / c_value
        if abs(c_value) < floor:
            c_value = floor
        d_value = 1.0 / d_value
        delta = d_value * c_value
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            break
    upper = math.exp(log_scale) * result
    return min(1.0, max(0.0, upper))


def _chi_square_survival(statistic: Decimal, degrees_of_freedom: int) -> Decimal:
    if degrees_of_freedom <= 0:
        raise ValueError("chi-square degrees of freedom must be positive")
    if statistic < 0:
        raise ValueError("chi-square statistic cannot be negative")
    if statistic.is_zero():
        return Decimal("1")
    if statistic.adjusted() > 300:
        return Decimal("0")
    value = float(statistic) / 2.0
    if not math.isfinite(value):
        return Decimal("0")
    probability = _regularized_gamma_q(
        float(degrees_of_freedom) / 2.0, value
    )
    return _float_to_decimal(probability)


def _continued_fraction_beta(a: float, b: float, x: float) -> float:
    """Evaluate an incomplete-beta continued fraction in binary64 arithmetic."""

    maximum_iterations = 256
    epsilon = 3.0e-14
    floor = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < floor:
        d = floor
    d = 1.0 / d
    value = d
    for iteration in range(1, maximum_iterations + 1):
        doubled = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + doubled) * (a + doubled))
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        value *= d * c

        numerator = -(a + iteration) * (qab + iteration) * x / ((a + doubled) * (qap + doubled))
        d = 1.0 + numerator * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + numerator / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        delta = d * c
        value *= delta
        if abs(delta - 1.0) <= epsilon:
            return value
    return value


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    leading = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        result = leading * _continued_fraction_beta(a, b, x) / a
    else:
        result = 1.0 - leading * _continued_fraction_beta(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, result))


def _student_t_two_sided_p_value(t_statistic: Decimal, degrees_of_freedom: int) -> Decimal:
    if degrees_of_freedom <= 0:
        raise ValueError("student t degrees of freedom must be positive")
    if t_statistic.is_zero():
        return Decimal("1")
    if t_statistic.adjusted() > 300:
        return Decimal("0")
    absolute_t = float(abs(t_statistic))
    if not math.isfinite(absolute_t):
        return Decimal("0")
    degrees = float(degrees_of_freedom)
    x_value = degrees / (degrees + absolute_t * absolute_t)
    probability = _regularized_incomplete_beta(x_value, degrees / 2.0, 0.5)
    return _float_to_decimal(probability)


@lru_cache(maxsize=64)
def _student_t_critical_value(degrees_of_freedom: int) -> Decimal:
    """Return a two-sided 95% Student-t critical value rounded deterministically."""

    if degrees_of_freedom <= 0:
        raise ValueError("student t degrees of freedom must be positive")
    lower = 0.0
    upper = 1.0
    while _student_t_two_sided_p_value(
        Decimal(format(upper, ".17g")), degrees_of_freedom
    ) > Decimal("0.05"):
        upper *= 2.0
        if upper > 1.0e12:
            raise ArithmeticError("unable to bracket Student-t critical value")
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        probability = _student_t_two_sided_p_value(
            Decimal(format(midpoint, ".17g")), degrees_of_freedom
        )
        if probability > Decimal("0.05"):
            lower = midpoint
        else:
            upper = midpoint
    return _float_to_decimal((lower + upper) / 2.0)


def _unavailable_residual_test(
    name: str,
    *,
    reason: str,
    lag: int,
    sample_size: int,
    excluded_count: int,
) -> ResidualTestResult:
    return next(
        result
        for result in _not_established_residual_tests(
            reason=reason,
            lag=lag,
            sample_size=sample_size,
            excluded_count=excluded_count,
        )
        if result.name == name
    )


def _established_residual_test(
    *,
    name: str,
    statistic: Decimal,
    p_value: Decimal,
    degrees_of_freedom: int,
    lag: int | None,
    sample_size: int,
    excluded_count: int,
    null_hypothesis: str,
    alternative_hypothesis: str,
    method: str,
) -> ResidualTestResult:
    return ResidualTestResult(
        name=name,
        status="established",
        reason=None,
        warnings=("asymptotic_chi_square_reference",),
        statistic=_clean_decimal(statistic),
        p_value=p_value,
        degrees_of_freedom=degrees_of_freedom,
        lag=lag,
        sample_size=sample_size,
        excluded_count=excluded_count,
        significance=DIAGNOSTIC_SIGNIFICANCE,
        decision=(
            "reject_null"
            if p_value < DIAGNOSTIC_SIGNIFICANCE
            else "fail_to_reject_null"
        ),
        null_hypothesis=null_hypothesis,
        alternative_hypothesis=alternative_hypothesis,
        method=method,
        reference_distribution="chi_square_asymptotic",
        inference_backend=CHI_SQUARE_INFERENCE_BACKEND,
    )


def _compute_residual_diagnostics(
    residuals: Sequence[Decimal],
    predictors: Sequence[Sequence[Decimal]],
    *,
    lag: int,
    excluded_count: int,
) -> tuple[ResidualTestResult, ...]:
    sample_size = len(residuals)
    if sample_size < 8:
        return _not_established_residual_tests(
            reason="insufficient_residual_sample_minimum_8",
            lag=lag,
            sample_size=sample_size,
            excluded_count=excluded_count,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        mean_residual = sum(residuals, Decimal("0")) / Decimal(sample_size)
        centered = tuple(value - mean_residual for value in residuals)
        centered_sum_squares = _dot(centered, centered)
        if centered_sum_squares == 0:
            return _not_established_residual_tests(
                reason="zero_residual_variance",
                lag=lag,
                sample_size=sample_size,
                excluded_count=excluded_count,
            )

        if excluded_count:
            ljung_box = _unavailable_residual_test(
                "ljung_box",
                reason="missing_observations_break_residual_sequence",
                lag=lag,
                sample_size=sample_size,
                excluded_count=excluded_count,
            )
        elif sample_size < max(8, 2 * lag + 1):
            ljung_box = _unavailable_residual_test(
                "ljung_box",
                reason="insufficient_sample_for_fixed_ljung_box_lag",
                lag=lag,
                sample_size=sample_size,
                excluded_count=excluded_count,
            )
        else:
            statistic = Decimal(sample_size * (sample_size + 2)) * sum(
                (
                    (
                        sum(
                            (
                                centered[index] * centered[index - current_lag]
                                for index in range(current_lag, sample_size)
                            ),
                            Decimal("0"),
                        )
                        / centered_sum_squares
                    )
                    ** 2
                    / Decimal(sample_size - current_lag)
                    for current_lag in range(1, lag + 1)
                ),
                Decimal("0"),
            )
            ljung_box = _established_residual_test(
                name="ljung_box",
                statistic=statistic,
                p_value=_chi_square_survival(statistic, lag),
                degrees_of_freedom=lag,
                lag=lag,
                sample_size=sample_size,
                excluded_count=excluded_count,
                null_hypothesis="no_residual_autocorrelation_through_lag",
                alternative_hypothesis="residual_autocorrelation_present",
                method="ljung_box_q_fixed_lag_centered_residuals_model_df_0",
            )

        width = len(predictors[0])
        nonconstant_columns = tuple(
            column
            for column in range(width)
            if any(
                row[column] != predictors[0][column]
                for row in predictors[1:]
            )
        )
        if not nonconstant_columns:
            breusch_pagan = _unavailable_residual_test(
                "breusch_pagan",
                reason="no_nonconstant_variance_predictors",
                lag=lag,
                sample_size=sample_size,
                excluded_count=excluded_count,
            )
        else:
            auxiliary_features = tuple(
                tuple(row[column] for column in nonconstant_columns)
                for row in predictors
            )
            auxiliary = fit_ols(
                tuple(value * value for value in residuals),
                auxiliary_features,
                intercept=True,
                covariance=CLASSICAL_HOMOSKEDASTIC,
                diagnostic_lag=None,
            )
            if auxiliary.status != "established" or auxiliary.r_squared is None:
                breusch_pagan = _unavailable_residual_test(
                    "breusch_pagan",
                    reason=(
                        "breusch_pagan_auxiliary_not_established"
                        if auxiliary.status != "established"
                        else "constant_squared_residual_target"
                    ),
                    lag=lag,
                    sample_size=sample_size,
                    excluded_count=excluded_count,
                )
            else:
                bounded_r_squared = min(
                    Decimal("1"), max(Decimal("0"), auxiliary.r_squared)
                )
                statistic = Decimal(sample_size) * bounded_r_squared
                degrees = len(nonconstant_columns)
                breusch_pagan = _established_residual_test(
                    name="breusch_pagan",
                    statistic=statistic,
                    p_value=_chi_square_survival(statistic, degrees),
                    degrees_of_freedom=degrees,
                    lag=None,
                    sample_size=sample_size,
                    excluded_count=excluded_count,
                    null_hypothesis="homoskedastic_residual_variance",
                    alternative_hypothesis="heteroskedastic_residual_variance",
                    method="koenker_breusch_pagan_lm_n_r_squared",
                )

        second_moment = centered_sum_squares / Decimal(sample_size)
        third_moment = sum((value ** 3 for value in centered), Decimal("0")) / Decimal(
            sample_size
        )
        fourth_moment = sum((value ** 4 for value in centered), Decimal("0")) / Decimal(
            sample_size
        )
        skewness = third_moment / (second_moment * second_moment.sqrt())
        excess_kurtosis = fourth_moment / (second_moment ** 2) - Decimal("3")
        statistic = Decimal(sample_size) / Decimal("6") * (
            skewness ** 2 + excess_kurtosis ** 2 / Decimal("4")
        )
        jarque_bera = _established_residual_test(
            name="jarque_bera",
            statistic=statistic,
            p_value=_chi_square_survival(statistic, 2),
            degrees_of_freedom=2,
            lag=None,
            sample_size=sample_size,
            excluded_count=excluded_count,
            null_hypothesis="residuals_have_normal_skewness_and_kurtosis",
            alternative_hypothesis="residuals_depart_from_normal_skewness_or_kurtosis",
            method="jarque_bera_population_moments",
        )
    return (ljung_box, breusch_pagan, jarque_bera)


def fit_ols(
    y: Sequence[Decimal | int | None],
    x: Sequence[Sequence[Decimal | int | None]],
    *,
    intercept: bool = True,
    covariance: str = CLASSICAL_HOMOSKEDASTIC,
    hac_lag: int = 0,
    diagnostic_lag: int | None = None,
    confidence_level: Decimal = Decimal("0.95"),
    missing: str = "drop_complete_rows",
) -> OLSResult:
    """Fit rank-aware OLS with complete-case inputs and explicit inference.

    Source vectors are never mutated. Missing rows are dropped only under
    drop_complete_rows; callers needing fixed samples use reject_incomplete_rows.
    """

    if not isinstance(intercept, bool):
        raise ValidationError("intercept must be boolean")
    covariance_method = _validate_covariance(covariance)
    checked_hac_lag = _validate_hac_lag(hac_lag, covariance_method)
    checked_diagnostic_lag = _validate_diagnostic_lag(diagnostic_lag)
    checked_confidence = _validate_confidence_level(confidence_level)
    missing_policy = _validate_missing_policy(missing)
    targets, rows, complete_rows, excluded_indices = _coerce_regression_inputs(y, x)
    width = len(rows[0])
    names = _coefficient_names(width, intercept)
    parameter_count = len(names)
    warning_values: list[str] = []
    if excluded_indices and missing_policy == "drop_complete_rows":
        warning_values.append("complete_case_rows_dropped")
    if excluded_indices and missing_policy == "reject_incomplete_rows":
        return _not_established_ols(
            reason="missing_observations_rejected",
            warnings=("incomplete_input_rejected",),
            names=names,
            rows=len(targets),
            complete_rows=complete_rows,
            excluded_indices=excluded_indices,
            parameter_count=parameter_count,
            rank=None,
            rank_tolerance=None,
            intercept=intercept,
            covariance_method=covariance_method,
            confidence_level=checked_confidence,
            missing_policy=missing_policy,
            hac_lag=checked_hac_lag,
            diagnostic_lag=checked_diagnostic_lag,
        )

    accepted_indices = tuple(index for index, complete in enumerate(complete_rows) if complete)
    if len(accepted_indices) <= parameter_count:
        return _not_established_ols(
            reason="insufficient_degrees_of_freedom",
            warnings=tuple(warning_values),
            names=names,
            rows=len(targets),
            complete_rows=complete_rows,
            excluded_indices=excluded_indices,
            parameter_count=parameter_count,
            rank=None,
            rank_tolerance=None,
            intercept=intercept,
            covariance_method=covariance_method,
            confidence_level=checked_confidence,
            missing_policy=missing_policy,
            hac_lag=checked_hac_lag,
            diagnostic_lag=checked_diagnostic_lag,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        accepted_targets = tuple(targets[index] for index in accepted_indices)
        accepted_rows = tuple(rows[index] for index in accepted_indices)
        numeric_targets = tuple(value for value in accepted_targets if value is not None)
        numeric_rows = tuple(
            tuple(value for value in row if value is not None) for row in accepted_rows
        )
        design = tuple(
            (Decimal("1"),) + row if intercept else row for row in numeric_rows
        )
        factorization = _pivoted_qr(design)
        if factorization.rank != parameter_count:
            return _not_established_ols(
                reason="rank_deficient_design",
                warnings=_unique_warnings(*warning_values, "rank_deficient_design"),
                names=names,
                rows=len(targets),
                complete_rows=complete_rows,
                excluded_indices=excluded_indices,
                parameter_count=parameter_count,
                rank=factorization.rank,
                rank_tolerance=factorization.rank_tolerance,
                intercept=intercept,
                covariance_method=covariance_method,
                confidence_level=checked_confidence,
                missing_policy=missing_policy,
                hac_lag=checked_hac_lag,
                diagnostic_lag=checked_diagnostic_lag,
            )
        q_targets = tuple(_dot(column, numeric_targets) for column in factorization.q_columns)
        pivoted_coefficients = _solve_upper_triangular(factorization.upper_r, q_targets)
        coefficients_by_original = [Decimal("0")] * parameter_count
        for pivoted_index, original_index in enumerate(factorization.permutation):
            coefficients_by_original[original_index] = _clean_decimal(
                pivoted_coefficients[pivoted_index]
            )
        coefficients = tuple(coefficients_by_original)
        fitted_complete = tuple(_clean_decimal(_dot(row, coefficients)) for row in design)
        residuals_complete = tuple(
            _clean_decimal(target - fitted)
            for target, fitted in zip(numeric_targets, fitted_complete, strict=True)
        )
        residual_sum_squares = _clean_decimal(_dot(residuals_complete, residuals_complete))
        degrees_of_freedom_residual = len(numeric_targets) - parameter_count
        degrees_of_freedom_model = parameter_count - int(intercept)
        residual_variance = _clean_decimal(
            residual_sum_squares / Decimal(degrees_of_freedom_residual)
        )
        root_mean_squared_error = _clean_decimal(residual_variance.sqrt())
        if intercept:
            mean_target = sum(numeric_targets, Decimal("0")) / Decimal(len(numeric_targets))
            total_sum_squares = _clean_decimal(
                sum(((target - mean_target) ** 2 for target in numeric_targets), Decimal("0"))
            )
        else:
            total_sum_squares = _clean_decimal(_dot(numeric_targets, numeric_targets))
            warning_values.append("uncentered_r_squared")
        if total_sum_squares == 0:
            r_squared = None
            adjusted_r_squared = None
            warning_values.append("constant_target")
        else:
            r_squared = _clean_decimal(Decimal("1") - residual_sum_squares / total_sum_squares)
            adjusted_r_squared = _clean_decimal(
                Decimal("1")
                - (Decimal("1") - r_squared)
                * Decimal(len(numeric_targets) - int(intercept))
                / Decimal(degrees_of_freedom_residual)
            )
        if residual_sum_squares == 0 or len(residuals_complete) < 2:
            durbin_watson = None
        else:
            durbin_watson = _clean_decimal(
                sum(
                    (
                        (residuals_complete[index] - residuals_complete[index - 1]) ** 2
                        for index in range(1, len(residuals_complete))
                    ),
                    Decimal("0"),
                )
                / residual_sum_squares
            )
            if excluded_indices:
                warning_values.append("durbin_watson_after_complete_case_filter")
        if covariance_method == CLASSICAL_HOMOSKEDASTIC:
            covariance_matrix = _covariance_from_qr(
                factorization, residual_variance, parameter_count
            )
            covariance_warnings: tuple[str, ...] = ()
            inference_backend = STUDENT_T_INFERENCE_BACKEND
            inference_distribution = "student_t"
        else:
            covariance_matrix, covariance_warnings = _sandwich_covariance(
                design=design,
                residuals=residuals_complete,
                factorization=factorization,
                parameter_count=parameter_count,
                method=covariance_method,
                hac_lag=checked_hac_lag,
                has_gaps=bool(excluded_indices),
            )
            inference_backend = NORMAL_INFERENCE_BACKEND
            inference_distribution = "normal_asymptotic"
        warning_values.extend(covariance_warnings)

        if residual_sum_squares == 0:
            warning_values.append("perfect_fit_inference_not_established")
            coefficient_results = tuple(
                CoefficientEstimate(name, estimate, None, None, None, None, None)
                for name, estimate in zip(names, coefficients, strict=True)
            )
            t_critical_value = None
        elif covariance_matrix is None:
            warning_values.append("robust_covariance_inference_not_established")
            coefficient_results = tuple(
                CoefficientEstimate(name, estimate, None, None, None, None, None)
                for name, estimate in zip(names, coefficients, strict=True)
            )
            t_critical_value = None
        else:
            if covariance_method == CLASSICAL_HOMOSKEDASTIC:
                t_critical_value = _student_t_critical_value(
                    degrees_of_freedom_residual
                )
            else:
                t_critical_value = NORMAL_95_CRITICAL_VALUE
            coefficient_values: list[CoefficientEstimate] = []
            for index, (name, estimate) in enumerate(zip(names, coefficients, strict=True)):
                variance = covariance_matrix[index][index]
                if variance < 0:
                    warning_values.append("nonpositive_covariance_diagonal")
                    coefficient_values.append(
                        CoefficientEstimate(name, estimate, None, None, None, None, None)
                    )
                    continue
                standard_error = _clean_decimal(variance.sqrt())
                if standard_error == 0:
                    warning_values.append("zero_standard_error_inference_not_established")
                    coefficient_values.append(
                        CoefficientEstimate(name, estimate, None, None, None, None, None)
                    )
                    continue
                t_statistic = _clean_decimal(estimate / standard_error)
                if covariance_method == CLASSICAL_HOMOSKEDASTIC:
                    p_value = _student_t_two_sided_p_value(
                        t_statistic, degrees_of_freedom_residual
                    )
                else:
                    p_value = _normal_two_sided_p_value(t_statistic)
                coefficient_values.append(
                    CoefficientEstimate(
                        term=name,
                        estimate=estimate,
                        standard_error=standard_error,
                        t_statistic=t_statistic,
                        p_value=p_value,
                        confidence_interval_lower=_clean_decimal(
                            estimate - t_critical_value * standard_error
                        ),
                        confidence_interval_upper=_clean_decimal(
                            estimate + t_critical_value * standard_error
                        ),
                    )
                )
            coefficient_results = tuple(coefficient_values)

        if checked_diagnostic_lag is None:
            residual_diagnostics: tuple[ResidualTestResult, ...] = ()
        else:
            residual_diagnostics = _compute_residual_diagnostics(
                residuals_complete,
                numeric_rows,
                lag=checked_diagnostic_lag,
                excluded_count=len(excluded_indices),
            )
            warning_values.extend(
                f"{diagnostic.name}_not_established"
                for diagnostic in residual_diagnostics
                if diagnostic.status != "established"
            )

    fitted_values: list[Decimal | None] = [None] * len(targets)
    residuals: list[Decimal | None] = [None] * len(targets)
    for index, fitted, residual in zip(
        accepted_indices, fitted_complete, residuals_complete, strict=True
    ):
        fitted_values[index] = fitted
        residuals[index] = residual
    return OLSResult(
        status="established",
        reason=None,
        warnings=_unique_warnings(*warning_values),
        coefficients=coefficient_results,
        coefficient_names=names,
        fitted_values=tuple(fitted_values),
        residuals=tuple(residuals),
        complete_rows=complete_rows,
        excluded_indices=excluded_indices,
        sample_size=len(accepted_indices),
        excluded_count=len(excluded_indices),
        parameter_count=parameter_count,
        rank=factorization.rank,
        rank_tolerance=factorization.rank_tolerance,
        degrees_of_freedom_model=degrees_of_freedom_model,
        degrees_of_freedom_residual=degrees_of_freedom_residual,
        residual_sum_squares=residual_sum_squares,
        total_sum_squares=total_sum_squares,
        r_squared=r_squared,
        adjusted_r_squared=adjusted_r_squared,
        residual_variance=residual_variance,
        root_mean_squared_error=root_mean_squared_error,
        durbin_watson=durbin_watson,
        covariance=covariance_matrix,
        intercept_included=intercept,
        covariance_method=covariance_method,
        confidence_level=checked_confidence,
        missing_policy=missing_policy,
        inference_backend=inference_backend,
        t_critical_value=t_critical_value,
        inference_distribution=inference_distribution,
        hac_lag=checked_hac_lag,
        residual_diagnostics=residual_diagnostics,
    )


def rolling_ols(
    y: Sequence[Decimal | int | None],
    x: Sequence[Sequence[Decimal | int | None]],
    *,
    window: int,
    intercept: bool = True,
    covariance: str = CLASSICAL_HOMOSKEDASTIC,
    hac_lag: int = 0,
    diagnostic_lag: int | None = None,
    confidence_level: Decimal = Decimal("0.95"),
) -> tuple[RollingOLSWindow, ...]:
    """Fit the exact OLS kernel over contiguous fixed-size source windows.

    Any missing observation makes that window visibly not established. No row is
    silently dropped from a rolling sample.
    """

    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise ValidationError("window must be an integer of at least two")
    targets = _sequence(y, "y")
    covariance_method = _validate_covariance(covariance)
    checked_hac_lag = _validate_hac_lag(hac_lag, covariance_method)
    checked_diagnostic_lag = _validate_diagnostic_lag(diagnostic_lag)
    rows = _sequence(x, "x")
    if len(targets) != len(rows):
        raise ValidationError("rolling OLS y and x must have equal row counts")
    if len(targets) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("rolling OLS exceeds the supported row limit")
    if window > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("rolling OLS window exceeds the supported row limit")
    _coerce_regression_inputs(y, x)
    count = max(0, len(targets) - window + 1)
    result: list[RollingOLSWindow] = []
    for start_index in range(count):
        end_index = start_index + window - 1
        fitted = fit_ols(
            y[start_index:end_index + 1],
            x[start_index:end_index + 1],
            intercept=intercept,
            covariance=covariance_method,
            confidence_level=confidence_level,
            missing="reject_incomplete_rows",
            hac_lag=checked_hac_lag,
            diagnostic_lag=checked_diagnostic_lag,
        )
        if fitted.reason == "missing_observations_rejected":
            fitted = replace(
                fitted,
                reason="incomplete_rolling_window",
                warnings=_unique_warnings(
                    *fitted.warnings, "fixed_window_sample_not_established"
                ),
            )
        result.append(RollingOLSWindow(start_index, end_index, fitted))
    return tuple(result)


def _validate_adf_significance(value: object) -> Decimal:
    significance = _decimal(value, "significance")
    if significance not in _ADF_SIGNIFICANCE_VALUES:
        raise ValidationError("significance must be one of 0.01, 0.05, or 0.10")
    return significance


def _adf_critical_values(effective_sample_size: int) -> tuple[tuple[Decimal, Decimal], ...]:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        inverse_nobs = Decimal("1") / Decimal(effective_sample_size)
        return tuple(
            (
                significance,
                _clean_decimal(
                    sum(
                        (
                            coefficient * (inverse_nobs ** power)
                            for power, coefficient in enumerate(coefficients)
                        ),
                        Decimal("0"),
                    )
                ),
            )
            for significance, coefficients in _ADF_CONSTANT_CRITICAL_COEFFICIENTS
        )


def _not_established_adf(
    *,
    reason: str,
    warnings: tuple[str, ...],
    sample_size: int,
    excluded_count: int,
    lag: int,
    significance: Decimal,
    regression: OLSResult | None = None,
    effective_sample_size: int = 0,
) -> ADFResult:
    return ADFResult(
        status="not_established",
        reason=reason,
        warnings=_unique_warnings(
            *warnings, "adf_p_value_not_provided_use_mackinnon_critical_values"
        ),
        sample_size=sample_size,
        excluded_count=excluded_count,
        effective_sample_size=effective_sample_size,
        lag=lag,
        deterministic="constant",
        adf_statistic=None,
        p_value=None,
        critical_values=(),
        rejections=(),
        selected_significance=significance,
        decision=None,
        null_hypothesis="unit_root",
        alternative_hypothesis="stationary",
        critical_value_method=MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD,
        regression=regression,
    )


def augmented_dickey_fuller(
    values: Sequence[Decimal | int | None],
    *,
    lag: int = 0,
    deterministic: str = "constant",
    significance: Decimal = Decimal("0.05"),
) -> ADFResult:
    """Run a fixed-lag, constant-only ADF regression with no automatic lag choice.

    The ADF decision uses MacKinnon 2010 finite-sample critical values. It is an
    in-sample stationarity diagnostic, never a predictive or causal conclusion.
    """

    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise ValidationError("lag must be a nonnegative integer")
    if lag > MAX_ADF_LAG:
        raise ResourceLimitError("ADF lag exceeds the supported bound")
    if deterministic != "constant":
        raise ValidationError("only a constant ADF deterministic term is supported")
    checked_significance = _validate_adf_significance(significance)
    source = _sequence(values, "values")
    if len(source) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("ADF exceeds the supported row limit")
    numeric_values: list[Decimal | None] = []
    missing_count = 0
    for index, value in enumerate(source):
        if value is None:
            numeric_values.append(None)
            missing_count += 1
        else:
            numeric_values.append(_decimal(value, f"values/{index}"))
    if missing_count:
        return _not_established_adf(
            reason="missing_observations_prevent_contiguous_test",
            warnings=("contiguous_adf_sample_required",),
            sample_size=len(source) - missing_count,
            excluded_count=missing_count,
            lag=lag,
            significance=checked_significance,
        )
    sample = tuple(value for value in numeric_values if value is not None)
    effective_sample_size = len(sample) - lag - 1
    parameter_count = lag + 2
    if effective_sample_size <= parameter_count:
        return _not_established_adf(
            reason="insufficient_degrees_of_freedom",
            warnings=(),
            sample_size=len(sample),
            excluded_count=0,
            lag=lag,
            significance=checked_significance,
            effective_sample_size=max(0, effective_sample_size),
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        differences = tuple(sample[index] - sample[index - 1] for index in range(1, len(sample)))
        dependent: list[Decimal] = []
        features: list[tuple[Decimal, ...]] = []
        for time_index in range(lag + 1, len(sample)):
            dependent.append(differences[time_index - 1])
            features.append(
                (
                    sample[time_index - 1],
                    *(
                        differences[time_index - difference_lag - 1]
                        for difference_lag in range(1, lag + 1)
                    ),
                )
            )
    regression = fit_ols(
        tuple(dependent), tuple(features), intercept=True, missing="reject_incomplete_rows"
    )
    if regression.status != "established":
        return _not_established_adf(
            reason=regression.reason or "adf_regression_not_established",
            warnings=regression.warnings,
            sample_size=len(sample),
            excluded_count=0,
            lag=lag,
            significance=checked_significance,
            regression=regression,
            effective_sample_size=effective_sample_size,
        )
    # Coefficient one is the lagged-level gamma term in the ADF equation.
    adf_statistic = regression.coefficients[1].t_statistic
    if adf_statistic is None:
        return _not_established_adf(
            reason="adf_statistic_inference_not_established",
            warnings=regression.warnings,
            sample_size=len(sample),
            excluded_count=0,
            lag=lag,
            significance=checked_significance,
            regression=regression,
            effective_sample_size=effective_sample_size,
        )
    critical_values = _adf_critical_values(effective_sample_size)
    rejections = tuple((level, adf_statistic < critical) for level, critical in critical_values)
    selected_rejection = dict(rejections)[checked_significance]
    return ADFResult(
        status="established",
        reason=None,
        warnings=_unique_warnings(
            *regression.warnings,
            "adf_p_value_not_provided_use_mackinnon_critical_values",
            "adf_decision_is_in_sample_and_not_predictive",
        ),
        sample_size=len(sample),
        excluded_count=0,
        effective_sample_size=effective_sample_size,
        lag=lag,
        deterministic="constant",
        adf_statistic=adf_statistic,
        p_value=None,
        critical_values=critical_values,
        rejections=rejections,
        selected_significance=checked_significance,
        decision="reject_unit_root" if selected_rejection else "fail_to_reject_unit_root",
        null_hypothesis="unit_root",
        alternative_hypothesis="stationary",
        critical_value_method=MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD,
        regression=regression,
    )




def _not_established_kpss(
    *,
    reason: str,
    warnings: tuple[str, ...],
    sample_size: int,
    excluded_count: int,
    lag: int,
    significance: Decimal,
    long_run_variance: Decimal | None = None,
    partial_sum_squares: Decimal | None = None,
) -> KPSSResult:
    return KPSSResult(
        status="not_established",
        reason=reason,
        warnings=_unique_warnings(
            *warnings,
            "kpss_p_value_not_provided_use_1992_level_critical_values",
        ),
        sample_size=sample_size,
        excluded_count=excluded_count,
        lag=lag,
        deterministic="constant",
        kpss_statistic=None,
        p_value=None,
        long_run_variance=long_run_variance,
        partial_sum_squares=partial_sum_squares,
        critical_values=(),
        rejections=(),
        selected_significance=significance,
        decision=None,
        null_hypothesis="level_stationary",
        alternative_hypothesis="unit_root_or_nonstationary",
        critical_value_method=KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD,
    )


def kpss_level_stationarity(
    values: Sequence[Decimal | int | None],
    *,
    lag: int = 0,
    deterministic: str = "constant",
    significance: Decimal = Decimal("0.05"),
) -> KPSSResult:
    """Run fixed-lag, constant-only level KPSS without bandwidth selection."""

    if isinstance(lag, bool) or not isinstance(lag, int) or lag < 0:
        raise ValidationError("lag must be a nonnegative integer")
    if lag > MAX_KPSS_LAG:
        raise ResourceLimitError("KPSS lag exceeds the supported bound")
    if deterministic != "constant":
        raise ValidationError("only a constant KPSS deterministic term is supported")
    checked_significance = _validate_adf_significance(significance)
    source = _sequence(values, "values")
    if len(source) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("KPSS exceeds the supported row limit")

    numeric_values: list[Decimal | None] = []
    missing_count = 0
    for index, value in enumerate(source):
        if value is None:
            numeric_values.append(None)
            missing_count += 1
        else:
            numeric_values.append(_decimal(value, f"values/{index}"))
    if missing_count:
        return _not_established_kpss(
            reason="missing_observations_prevent_contiguous_test",
            warnings=("contiguous_kpss_sample_required",),
            sample_size=len(source) - missing_count,
            excluded_count=missing_count,
            lag=lag,
            significance=checked_significance,
        )

    sample = tuple(value for value in numeric_values if value is not None)
    if len(sample) < 2:
        return _not_established_kpss(
            reason="insufficient_observations_for_level_kpss",
            warnings=(),
            sample_size=len(sample),
            excluded_count=0,
            lag=lag,
            significance=checked_significance,
        )
    if lag >= len(sample):
        return _not_established_kpss(
            reason="kpss_lag_exceeds_complete_sample",
            warnings=(),
            sample_size=len(sample),
            excluded_count=0,
            lag=lag,
            significance=checked_significance,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        count = Decimal(len(sample))
        mean = sum(sample, Decimal("0")) / count
        residuals = tuple(value - mean for value in sample)
        demeaned_sum_squares = sum(
            (residual * residual for residual in residuals), Decimal("0")
        )
        if demeaned_sum_squares.is_zero():
            return _not_established_kpss(
                reason="zero_demeaned_variance",
                warnings=(),
                sample_size=len(sample),
                excluded_count=0,
                lag=lag,
                significance=checked_significance,
                long_run_variance=Decimal("0"),
                partial_sum_squares=Decimal("0"),
            )

        cumulative = Decimal("0")
        partial_sum_squares = Decimal("0")
        for residual in residuals:
            cumulative += residual
            partial_sum_squares += cumulative * cumulative

        long_run_variance = demeaned_sum_squares / count
        for covariance_lag in range(1, lag + 1):
            covariance = sum(
                (
                    residuals[index] * residuals[index - covariance_lag]
                    for index in range(covariance_lag, len(residuals))
                ),
                Decimal("0"),
            ) / count
            weight = Decimal(lag + 1 - covariance_lag) / Decimal(lag + 1)
            long_run_variance += Decimal("2") * weight * covariance

        long_run_variance = _clean_decimal(long_run_variance)
        partial_sum_squares = _clean_decimal(partial_sum_squares)
        if long_run_variance <= 0:
            return _not_established_kpss(
                reason="nonpositive_long_run_variance",
                warnings=(),
                sample_size=len(sample),
                excluded_count=0,
                lag=lag,
                significance=checked_significance,
                long_run_variance=long_run_variance,
                partial_sum_squares=partial_sum_squares,
            )
        statistic = _clean_decimal(
            partial_sum_squares / (count * count * long_run_variance)
        )

    critical_values = _KPSS_LEVEL_CRITICAL_VALUES
    rejections = tuple(
        (level, statistic > critical) for level, critical in critical_values
    )
    selected_rejection = dict(rejections)[checked_significance]
    return KPSSResult(
        status="established",
        reason=None,
        warnings=(
            "kpss_p_value_not_provided_use_1992_level_critical_values",
            "fixed_bartlett_lag_no_automatic_bandwidth",
            "kpss_decision_is_in_sample_and_not_predictive",
        ),
        sample_size=len(sample),
        excluded_count=0,
        lag=lag,
        deterministic="constant",
        kpss_statistic=statistic,
        p_value=None,
        long_run_variance=long_run_variance,
        partial_sum_squares=partial_sum_squares,
        critical_values=critical_values,
        rejections=rejections,
        selected_significance=checked_significance,
        decision=(
            "reject_level_stationarity"
            if selected_rejection
            else "fail_to_reject_level_stationarity"
        ),
        null_hypothesis="level_stationary",
        alternative_hypothesis="unit_root_or_nonstationary",
        critical_value_method=KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD,
    )


def _not_established_chow(
    *,
    reason: str,
    warnings: tuple[str, ...],
    break_index: int,
    significance: Decimal,
    sample_size: int = 0,
    excluded_count: int = 0,
    pre_break_sample_size: int = 0,
    post_break_sample_size: int = 0,
    parameter_count: int = 0,
    numerator_degrees_of_freedom: int | None = None,
    denominator_degrees_of_freedom: int | None = None,
    pooled_residual_sum_squares: Decimal | None = None,
    pre_break_residual_sum_squares: Decimal | None = None,
    post_break_residual_sum_squares: Decimal | None = None,
    unrestricted_residual_sum_squares: Decimal | None = None,
    residual_sum_squares_reduction: Decimal | None = None,
    pooled_fit: OLSResult | None = None,
    pre_break_fit: OLSResult | None = None,
    post_break_fit: OLSResult | None = None,
) -> ChowBreakResult:
    return ChowBreakResult(
        status="not_established",
        reason=reason,
        warnings=_unique_warnings(
            *warnings,
            "caller_declared_break_index_no_search",
            "chow_exact_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
        ),
        sample_size=sample_size,
        excluded_count=excluded_count,
        break_index=break_index,
        pre_break_sample_size=pre_break_sample_size,
        post_break_sample_size=post_break_sample_size,
        parameter_count=parameter_count,
        numerator_degrees_of_freedom=numerator_degrees_of_freedom,
        denominator_degrees_of_freedom=denominator_degrees_of_freedom,
        pooled_residual_sum_squares=pooled_residual_sum_squares,
        pre_break_residual_sum_squares=pre_break_residual_sum_squares,
        post_break_residual_sum_squares=post_break_residual_sum_squares,
        unrestricted_residual_sum_squares=unrestricted_residual_sum_squares,
        residual_sum_squares_reduction=residual_sum_squares_reduction,
        f_statistic=None,
        p_value=None,
        significance=significance,
        decision=None,
        null_hypothesis="coefficients_are_stable_across_declared_break",
        alternative_hypothesis="at_least_one_coefficient_differs_across_declared_break",
        method="chow_pooled_vs_segmented_ols_fixed_break",
        reference_distribution=F_REFERENCE_DISTRIBUTION,
        inference_backend=F_INFERENCE_BACKEND,
        pooled_fit=pooled_fit,
        pre_break_fit=pre_break_fit,
        post_break_fit=post_break_fit,
    )


def _f_survival_probability(
    statistic: Decimal,
    numerator_degrees_of_freedom: int,
    denominator_degrees_of_freedom: int,
) -> Decimal:
    if statistic < 0:
        raise ValueError("F statistic must be nonnegative")
    if numerator_degrees_of_freedom <= 0 or denominator_degrees_of_freedom <= 0:
        raise ValueError("F degrees of freedom must be positive")
    if statistic.is_zero():
        return Decimal("1")
    if statistic.adjusted() > 300:
        return Decimal("0")
    value = float(statistic)
    if not math.isfinite(value):
        return Decimal("0")
    numerator_df = float(numerator_degrees_of_freedom)
    denominator_df = float(denominator_degrees_of_freedom)
    x_value = denominator_df / (
        denominator_df + numerator_df * value
    )
    probability = _regularized_incomplete_beta(
        x_value,
        denominator_df / 2.0,
        numerator_df / 2.0,
    )
    return _float_to_decimal(probability)


def chow_break_test(
    y: Sequence[Decimal | int | None],
    x: Sequence[Sequence[Decimal | int | None]],
    *,
    break_index: int,
    intercept: bool = True,
    significance: Decimal = Decimal("0.05"),
) -> ChowBreakResult:
    """Test one caller-declared coefficient break using pooled and split OLS."""

    if not isinstance(intercept, bool):
        raise ValidationError("intercept must be boolean")
    if (
        isinstance(break_index, bool)
        or not isinstance(break_index, int)
        or break_index < 1
    ):
        raise ValidationError("break_index must be a positive integer")
    checked_significance = _validate_adf_significance(significance)
    targets_source = _sequence(y, "y")
    rows_source = _sequence(x, "x")
    if len(targets_source) != len(rows_source):
        raise ValidationError("Chow y and x must have equal row counts")
    if len(targets_source) > MAX_REGRESSION_ROWS:
        raise ResourceLimitError("Chow test exceeds the supported row limit")
    if not targets_source:
        return _not_established_chow(
            reason="no_observations",
            warnings=(),
            break_index=break_index,
            significance=checked_significance,
        )

    targets, rows, _, _ = _coerce_regression_inputs(y, x)
    parameter_count = len(rows[0]) + int(intercept)
    pooled_fit = fit_ols(
        targets,
        rows,
        intercept=intercept,
        covariance=CLASSICAL_HOMOSKEDASTIC,
        missing="reject_incomplete_rows",
    )
    if break_index >= len(targets):
        return _not_established_chow(
            reason="break_index_outside_sample",
            warnings=pooled_fit.warnings,
            break_index=break_index,
            significance=checked_significance,
            sample_size=pooled_fit.sample_size,
            excluded_count=pooled_fit.excluded_count,
            parameter_count=parameter_count,
            pooled_residual_sum_squares=pooled_fit.residual_sum_squares,
            pooled_fit=pooled_fit,
        )

    pre_break_fit = fit_ols(
        targets[:break_index],
        rows[:break_index],
        intercept=intercept,
        covariance=CLASSICAL_HOMOSKEDASTIC,
        missing="reject_incomplete_rows",
    )
    post_break_fit = fit_ols(
        targets[break_index:],
        rows[break_index:],
        intercept=intercept,
        covariance=CLASSICAL_HOMOSKEDASTIC,
        missing="reject_incomplete_rows",
    )
    common = {
        "break_index": break_index,
        "significance": checked_significance,
        "sample_size": pooled_fit.sample_size,
        "excluded_count": pooled_fit.excluded_count,
        "pre_break_sample_size": pre_break_fit.sample_size,
        "post_break_sample_size": post_break_fit.sample_size,
        "parameter_count": parameter_count,
        "pooled_residual_sum_squares": pooled_fit.residual_sum_squares,
        "pre_break_residual_sum_squares": pre_break_fit.residual_sum_squares,
        "post_break_residual_sum_squares": post_break_fit.residual_sum_squares,
        "pooled_fit": pooled_fit,
        "pre_break_fit": pre_break_fit,
        "post_break_fit": post_break_fit,
    }
    all_warnings = _unique_warnings(
        *pooled_fit.warnings,
        *pre_break_fit.warnings,
        *post_break_fit.warnings,
    )
    if pooled_fit.excluded_count:
        return _not_established_chow(
            reason="missing_observations_rejected",
            warnings=all_warnings,
            **common,
        )
    for label, fitted in (
        ("pooled", pooled_fit),
        ("pre_break", pre_break_fit),
        ("post_break", post_break_fit),
    ):
        if fitted.status != "established":
            return _not_established_chow(
                reason=f"{label}_{fitted.reason or 'ols_not_established'}",
                warnings=all_warnings,
                **common,
            )

    pooled_rss = pooled_fit.residual_sum_squares
    pre_break_rss = pre_break_fit.residual_sum_squares
    post_break_rss = post_break_fit.residual_sum_squares
    if pooled_rss is None or pre_break_rss is None or post_break_rss is None:
        return _not_established_chow(
            reason="residual_sum_squares_not_established",
            warnings=all_warnings,
            **common,
        )

    denominator_degrees_of_freedom = len(targets) - 2 * parameter_count
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        unrestricted_rss = _clean_decimal(pre_break_rss + post_break_rss)
        rss_reduction = _clean_decimal(pooled_rss - unrestricted_rss)
        tolerance = max(
            abs(pooled_rss),
            abs(unrestricted_rss),
        ) * RANK_RELATIVE_TOLERANCE + RANK_ABSOLUTE_TOLERANCE
        if rss_reduction < -tolerance:
            return _not_established_chow(
                reason="negative_restricted_rss_difference",
                warnings=all_warnings,
                numerator_degrees_of_freedom=parameter_count,
                denominator_degrees_of_freedom=denominator_degrees_of_freedom,
                unrestricted_residual_sum_squares=unrestricted_rss,
                residual_sum_squares_reduction=rss_reduction,
                **common,
            )
        if rss_reduction < 0:
            rss_reduction = Decimal("0")
            all_warnings = _unique_warnings(
                *all_warnings,
                "roundoff_negative_rss_difference_clamped_to_zero",
            )
        if unrestricted_rss <= tolerance:
            return _not_established_chow(
                reason="zero_unrestricted_residual_sum_squares",
                warnings=all_warnings,
                numerator_degrees_of_freedom=parameter_count,
                denominator_degrees_of_freedom=denominator_degrees_of_freedom,
                unrestricted_residual_sum_squares=unrestricted_rss,
                residual_sum_squares_reduction=rss_reduction,
                **common,
            )
        f_statistic = _clean_decimal(
            (rss_reduction / Decimal(parameter_count))
            / (
                unrestricted_rss
                / Decimal(denominator_degrees_of_freedom)
            )
        )
    p_value = _f_survival_probability(
        f_statistic,
        parameter_count,
        denominator_degrees_of_freedom,
    )
    return ChowBreakResult(
        status="established",
        reason=None,
        warnings=_unique_warnings(
            *all_warnings,
            "caller_declared_break_index_no_search",
            "chow_exact_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
            "break_decision_is_in_sample_and_not_predictive",
        ),
        sample_size=len(targets),
        excluded_count=0,
        break_index=break_index,
        pre_break_sample_size=break_index,
        post_break_sample_size=len(targets) - break_index,
        parameter_count=parameter_count,
        numerator_degrees_of_freedom=parameter_count,
        denominator_degrees_of_freedom=denominator_degrees_of_freedom,
        pooled_residual_sum_squares=pooled_rss,
        pre_break_residual_sum_squares=pre_break_rss,
        post_break_residual_sum_squares=post_break_rss,
        unrestricted_residual_sum_squares=unrestricted_rss,
        residual_sum_squares_reduction=rss_reduction,
        f_statistic=f_statistic,
        p_value=p_value,
        significance=checked_significance,
        decision=(
            "reject_parameter_stability"
            if p_value <= checked_significance
            else "fail_to_reject_parameter_stability"
        ),
        null_hypothesis="coefficients_are_stable_across_declared_break",
        alternative_hypothesis="at_least_one_coefficient_differs_across_declared_break",
        method="chow_pooled_vs_segmented_ols_fixed_break",
        reference_distribution=F_REFERENCE_DISTRIBUTION,
        inference_backend=F_INFERENCE_BACKEND,
        pooled_fit=pooled_fit,
        pre_break_fit=pre_break_fit,
        post_break_fit=post_break_fit,
    )


ordinary_least_squares_v2 = fit_ols
rolling_ordinary_least_squares_v2 = rolling_ols
adf_test_v2 = augmented_dickey_fuller
kpss_test_v2_1 = kpss_level_stationarity
chow_test_v2 = chow_break_test


__all__ = [
    "ADFResult",
    "ChowBreakResult",
    "F_INFERENCE_BACKEND",
    "CLASSICAL_HOMOSKEDASTIC",
    "CoefficientEstimate",
    "DECIMAL_PRECISION",
    "KPSSResult",
    "KPSS_1992_LEVEL_CRITICAL_VALUE_METHOD",
    "MAC_KINNON_2010_CONSTANT_CRITICAL_VALUE_METHOD",
    "MAX_ADF_LAG",
    "MAX_KPSS_LAG",
    "MAX_REGRESSION_PREDICTORS",
    "MAX_REGRESSION_ROWS",
    "OLSResult",
    "RANK_ABSOLUTE_TOLERANCE",
    "RANK_RELATIVE_TOLERANCE",
    "RollingOLSWindow",
    "STUDENT_T_INFERENCE_BACKEND",
    "adf_test_v2",
    "augmented_dickey_fuller",
    "fit_ols",
    "chow_break_test",
    "chow_test_v2",
    "kpss_level_stationarity",
    "kpss_test_v2_1",
    "ordinary_least_squares_v2",
    "rolling_ols",
    "rolling_ordinary_least_squares_v2",
]

_ENGLE_GRANGER_N2_CRITICAL_COEFFICIENTS: tuple[
    tuple[Decimal, tuple[Decimal, Decimal, Decimal]], ...
] = (
    (Decimal("0.01"), (Decimal("-3.89644"), Decimal("-10.9519"), Decimal("-22.527"))),
    (Decimal("0.05"), (Decimal("-3.33613"), Decimal("-6.1101"), Decimal("-6.823"))),
    (Decimal("0.10"), (Decimal("-3.04445"), Decimal("-4.2412"), Decimal("-2.720"))),
)


@dataclass(frozen=True, slots=True)
class EngleGrangerCointegrationResult:
    """Two-step Engle--Granger result over two ordered level series."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_sample_size: int
    sample_size: int
    excluded_count: int
    effective_sample_size: int
    lag: int
    deterministic: str
    first_stage_deterministic: str
    residual_test_deterministic: str
    dependent_series_index: int
    regressor_series_index: int
    first_stage_regression: OLSResult | None
    residual_adf_regression: OLSResult | None
    cointegrating_intercept: Decimal | None
    cointegrating_slope: Decimal | None
    residual_adf_gamma: Decimal | None
    residual_adf_gamma_standard_error: Decimal | None
    residual_adf_statistic: Decimal | None
    p_value: None
    critical_values: tuple[tuple[Decimal, Decimal], ...]
    rejections: tuple[tuple[Decimal, bool], ...]
    selected_significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    critical_value_method: str


@dataclass(frozen=True, slots=True)
class VectorAutoregressionResult:
    """Fixed-order reduced-form VAR over series[series_index][time_index]."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_sample_size: int
    sample_size: int
    excluded_count: int
    effective_sample_size: int
    series_count: int
    lag_order: int
    deterministic: str
    parameter_count: int
    degrees_of_freedom_residual: int | None
    coefficient_names: tuple[str, ...]
    equations: tuple[OLSResult, ...]
    intercepts: tuple[Decimal | None, ...]
    lag_coefficient_matrices: tuple[tuple[tuple[Decimal, ...], ...], ...] | None
    residual_covariance: tuple[tuple[Decimal, ...], ...] | None
    residual_covariance_denominator: int | None
    method: str


@dataclass(frozen=True, slots=True)
class GrangerCausalityResult:
    """Conditional fixed-order Granger F test for one declared direction."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    input_sample_size: int
    sample_size: int
    excluded_count: int
    effective_sample_size: int
    series_count: int
    lag_order: int
    deterministic: str
    source_index: int
    target_index: int
    restriction_count: int
    numerator_degrees_of_freedom: int | None
    denominator_degrees_of_freedom: int | None
    restricted_residual_sum_squares: Decimal | None
    unrestricted_residual_sum_squares: Decimal | None
    residual_sum_squares_reduction: Decimal | None
    f_statistic: Decimal | None
    p_value: Decimal | None
    significance: Decimal
    decision: str | None
    null_hypothesis: str
    alternative_hypothesis: str
    method: str
    reference_distribution: str
    inference_backend: str
    unrestricted_var: VectorAutoregressionResult | None
    unrestricted_target_regression: OLSResult | None
    restricted_target_regression: OLSResult | None


def _validate_fixed_lag(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ResourceLimitError(
            f"{field} must be between {minimum} and {maximum} inclusive"
        )
    return value


def _validate_constant_deterministic(value: object, *, field: str) -> str:
    if value != "constant":
        raise ValidationError(f"only a constant {field} is supported")
    return "constant"


def _coerce_multivariate_series(
    series: Sequence[Sequence[Decimal | int | None]],
    *,
    field: str,
    exact_series_count: int | None = None,
    minimum_series_count: int = 2,
    maximum_series_count: int = MAX_VAR_SERIES,
) -> tuple[tuple[Decimal | None, ...], ...]:
    """Validate the explicit series[series_index][time_index] convention."""

    source = _sequence(series, field)
    count = len(source)
    if exact_series_count is not None:
        if count != exact_series_count:
            raise ValidationError(
                f"{field} requires exactly {exact_series_count} ordered series"
            )
    elif not minimum_series_count <= count <= maximum_series_count:
        raise ValidationError(
            f"{field} requires between {minimum_series_count} and {maximum_series_count} series"
        )
    if not source:
        raise ValidationError(f"{field} requires at least one series")

    first = _sequence(source[0], f"{field}/0")
    observations = len(first)
    if observations > MAX_REGRESSION_ROWS:
        raise ResourceLimitError(f"{field} exceeds the supported row limit")
    result: list[tuple[Decimal | None, ...]] = []
    for series_index, values in enumerate(source):
        raw_values = _sequence(values, f"{field}/{series_index}")
        if len(raw_values) != observations:
            raise ValidationError(
                f"{field} requires equal observation counts for every series"
            )
        result.append(
            tuple(
                None
                if value is None
                else _decimal(value, f"{field}/{series_index}/{time_index}")
                for time_index, value in enumerate(raw_values)
            )
        )
    return tuple(result)


def _multivariate_missing_row_count(
    series: Sequence[Sequence[Decimal | None]],
) -> int:
    return sum(
        any(value is None for value in observation)
        for observation in zip(*series, strict=True)
    )


def _numeric_multivariate_series(
    series: Sequence[Sequence[Decimal | None]],
) -> tuple[tuple[Decimal, ...], ...]:
    """Return values after a caller has established that no row is incomplete."""

    return tuple(
        tuple(value for value in values if value is not None)
        for values in series
    )


def _engle_granger_critical_values(
    sample_size: int,
) -> tuple[tuple[Decimal, Decimal], ...]:
    """MacKinnon 2010 tau_c N=2 values using the requested n - 1 denominator."""

    if sample_size <= 1:
        raise ValueError("cointegration critical values require at least two observations")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        inverse = Decimal("1") / Decimal(sample_size - 1)
        return tuple(
            (
                significance,
                _clean_decimal(
                    coefficient_infinity
                    + coefficient_one * inverse
                    + coefficient_two * inverse * inverse
                ),
            )
            for significance, (
                coefficient_infinity,
                coefficient_one,
                coefficient_two,
            ) in _ENGLE_GRANGER_N2_CRITICAL_COEFFICIENTS
        )


def _cointegration_warnings(*warnings: str) -> tuple[str, ...]:
    return _unique_warnings(
        *warnings,
        "engle_granger_requires_i1_series_assumption_not_verified",
        "caller_series_order_defines_dependent_and_regressor",
        "fixed_residual_adf_lag_no_automatic_selection",
        "cointegration_p_value_not_provided_use_mackinnon_critical_values",
    )



def _not_established_engle_granger(
    *,
    reason: str,
    warnings: tuple[str, ...],
    input_sample_size: int,
    sample_size: int,
    excluded_count: int,
    lag: int,
    significance: Decimal,
    first_stage_regression: OLSResult | None = None,
    residual_adf_regression: OLSResult | None = None,
    effective_sample_size: int = 0,
) -> EngleGrangerCointegrationResult:
    intercept = (
        first_stage_regression.coefficients[0].estimate
        if first_stage_regression is not None
        and first_stage_regression.coefficients
        else None
    )
    slope = (
        first_stage_regression.coefficients[1].estimate
        if first_stage_regression is not None
        and len(first_stage_regression.coefficients) > 1
        else None
    )
    gamma = (
        residual_adf_regression.coefficients[0].estimate
        if residual_adf_regression is not None
        and residual_adf_regression.coefficients
        else None
    )
    gamma_standard_error = (
        residual_adf_regression.coefficients[0].standard_error
        if residual_adf_regression is not None
        and residual_adf_regression.coefficients
        else None
    )
    return EngleGrangerCointegrationResult(
        status="not_established",
        reason=reason,
        warnings=_cointegration_warnings(*warnings),
        input_sample_size=input_sample_size,
        sample_size=sample_size,
        excluded_count=excluded_count,
        effective_sample_size=effective_sample_size,
        lag=lag,
        deterministic="constant",
        first_stage_deterministic="constant",
        residual_test_deterministic="none",
        dependent_series_index=0,
        regressor_series_index=1,
        first_stage_regression=first_stage_regression,
        residual_adf_regression=residual_adf_regression,
        cointegrating_intercept=intercept,
        cointegrating_slope=slope,
        residual_adf_gamma=gamma,
        residual_adf_gamma_standard_error=gamma_standard_error,
        residual_adf_statistic=None,
        p_value=None,
        critical_values=(),
        rejections=(),
        selected_significance=significance,
        decision=None,
        null_hypothesis="no_cointegration",
        alternative_hypothesis="cointegration",
        critical_value_method=MAC_KINNON_2010_COINTEGRATION_CRITICAL_VALUE_METHOD,
    )


def engle_granger_cointegration(
    series: Sequence[Sequence[Decimal | int | None]],
    *,
    lag: int = 0,
    deterministic: str = "constant",
    significance: Decimal = Decimal("0.05"),
) -> EngleGrangerCointegrationResult:
    """Run two-step Engle--Granger on two ordered level series.

    series[0] is the dependent level and series[1] is the regressor level. The
    residual test is an ADF regression without a deterministic term and with the
    caller-declared augmentation lag; it does not establish the required I(1)
    precondition for either input.
    """

    checked_lag = _validate_fixed_lag(
        lag,
        field="lag",
        minimum=0,
        maximum=MAX_ENGLE_GRANGER_LAG,
    )
    _validate_constant_deterministic(
        deterministic,
        field="cointegration deterministic term",
    )
    checked_significance = _validate_adf_significance(significance)
    source = _coerce_multivariate_series(
        series,
        field="series",
        exact_series_count=2,
    )
    input_sample_size = len(source[0])
    excluded_count = _multivariate_missing_row_count(source)
    if excluded_count:
        return _not_established_engle_granger(
            reason="missing_observations_prevent_contiguous_test",
            warnings=("complete_pair_sample_required_no_row_deletion",),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size - excluded_count,
            excluded_count=excluded_count,
            lag=checked_lag,
            significance=checked_significance,
        )

    minimum_sample_size = max(21, 2 * checked_lag + 3)
    effective_sample_size = max(0, input_sample_size - checked_lag - 1)
    if input_sample_size < minimum_sample_size:
        return _not_established_engle_granger(
            reason="insufficient_observations_for_engle_granger",
            warnings=(
                "engle_granger_minimum_sample_size_is_max_21_or_2lag_plus_3",
            ),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            lag=checked_lag,
            significance=checked_significance,
            effective_sample_size=effective_sample_size,
        )

    numeric_series = _numeric_multivariate_series(source)
    dependent_levels, regressor_levels = numeric_series
    first_stage = fit_ols(
        dependent_levels,
        tuple((value,) for value in regressor_levels),
        intercept=True,
        covariance=CLASSICAL_HOMOSKEDASTIC,
        missing="reject_incomplete_rows",
    )
    if first_stage.status != "established":
        return _not_established_engle_granger(
            reason=f"first_stage_{first_stage.reason or 'ols_not_established'}",
            warnings=first_stage.warnings,
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            lag=checked_lag,
            significance=checked_significance,
            first_stage_regression=first_stage,
            effective_sample_size=effective_sample_size,
        )
    residuals = tuple(value for value in first_stage.residuals if value is not None)
    if len(residuals) != input_sample_size:
        return _not_established_engle_granger(
            reason="first_stage_residual_sample_not_contiguous",
            warnings=first_stage.warnings,
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            lag=checked_lag,
            significance=checked_significance,
            first_stage_regression=first_stage,
            effective_sample_size=effective_sample_size,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        differences = tuple(
            residuals[index] - residuals[index - 1]
            for index in range(1, len(residuals))
        )
        dependent_differences: list[Decimal] = []
        features: list[tuple[Decimal, ...]] = []
        for time_index in range(checked_lag + 1, len(residuals)):
            dependent_differences.append(differences[time_index - 1])
            features.append(
                (
                    residuals[time_index - 1],
                    *(
                        differences[time_index - difference_lag - 1]
                        for difference_lag in range(1, checked_lag + 1)
                    ),
                )
            )
    residual_adf = fit_ols(
        tuple(dependent_differences),
        tuple(features),
        intercept=False,
        covariance=CLASSICAL_HOMOSKEDASTIC,
        missing="reject_incomplete_rows",
    )
    if residual_adf.status != "established":
        return _not_established_engle_granger(
            reason=f"residual_adf_{residual_adf.reason or 'ols_not_established'}",
            warnings=_unique_warnings(*first_stage.warnings, *residual_adf.warnings),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            lag=checked_lag,
            significance=checked_significance,
            first_stage_regression=first_stage,
            residual_adf_regression=residual_adf,
            effective_sample_size=effective_sample_size,
        )
    residual_adf_statistic = residual_adf.coefficients[0].t_statistic
    if residual_adf_statistic is None:
        return _not_established_engle_granger(
            reason="residual_adf_statistic_inference_not_established",
            warnings=_unique_warnings(*first_stage.warnings, *residual_adf.warnings),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            lag=checked_lag,
            significance=checked_significance,
            first_stage_regression=first_stage,
            residual_adf_regression=residual_adf,
            effective_sample_size=effective_sample_size,
        )

    critical_values = _engle_granger_critical_values(input_sample_size)
    rejections = tuple(
        (level, residual_adf_statistic < critical)
        for level, critical in critical_values
    )
    selected_rejection = dict(rejections)[checked_significance]
    return EngleGrangerCointegrationResult(
        status="established",
        reason=None,
        warnings=_cointegration_warnings(
            *first_stage.warnings,
            *residual_adf.warnings,
            "engle_granger_decision_is_in_sample_and_not_predictive",
        ),
        input_sample_size=input_sample_size,
        sample_size=input_sample_size,
        excluded_count=0,
        effective_sample_size=effective_sample_size,
        lag=checked_lag,
        deterministic="constant",
        first_stage_deterministic="constant",
        residual_test_deterministic="none",
        dependent_series_index=0,
        regressor_series_index=1,
        first_stage_regression=first_stage,
        residual_adf_regression=residual_adf,
        cointegrating_intercept=first_stage.coefficients[0].estimate,
        cointegrating_slope=first_stage.coefficients[1].estimate,
        residual_adf_gamma=residual_adf.coefficients[0].estimate,
        residual_adf_gamma_standard_error=residual_adf.coefficients[0].standard_error,
        residual_adf_statistic=residual_adf_statistic,
        p_value=None,
        critical_values=critical_values,
        rejections=rejections,
        selected_significance=checked_significance,
        decision=(
            "reject_no_cointegration"
            if selected_rejection
            else "fail_to_reject_no_cointegration"
        ),
        null_hypothesis="no_cointegration",
        alternative_hypothesis="cointegration",
        critical_value_method=MAC_KINNON_2010_COINTEGRATION_CRITICAL_VALUE_METHOD,
    )



def _var_coefficient_names(series_count: int, lag_order: int) -> tuple[str, ...]:
    return ("intercept",) + tuple(
        f"lag_{lag}_series_{series_index}"
        for lag in range(1, lag_order + 1)
        for series_index in range(series_count)
    )


def _rename_ols_terms(
    result: OLSResult,
    coefficient_names: tuple[str, ...],
) -> OLSResult:
    if len(result.coefficients) != len(coefficient_names):
        raise AssertionError("OLS coefficient names did not match the design width")
    return replace(
        result,
        coefficient_names=coefficient_names,
        coefficients=tuple(
            replace(coefficient, term=name)
            for coefficient, name in zip(result.coefficients, coefficient_names, strict=True)
        ),
    )


def _build_var_design(
    series: Sequence[Sequence[Decimal]],
    lag_order: int,
) -> tuple[tuple[tuple[Decimal, ...], ...], tuple[tuple[Decimal, ...], ...]]:
    """Return common feature rows and per-target values in documented lag order."""

    series_count = len(series)
    input_sample_size = len(series[0])
    features = tuple(
        tuple(
            series[source_index][time_index - lag]
            for lag in range(1, lag_order + 1)
            for source_index in range(series_count)
        )
        for time_index in range(lag_order, input_sample_size)
    )
    targets = tuple(
        tuple(
            series[target_index][time_index]
            for time_index in range(lag_order, input_sample_size)
        )
        for target_index in range(series_count)
    )
    return features, targets


def _var_warnings(*warnings: str) -> tuple[str, ...]:
    return _unique_warnings(
        *warnings,
        "fixed_var_lag_order_no_automatic_selection",
        "reduced_form_var_no_structural_identification",
        "var_is_in_sample_not_a_forecast_or_causal_claim",
        "caller_time_order_is_assumed_contiguous",
    )


def _not_established_var(
    *,
    reason: str,
    warnings: tuple[str, ...],
    input_sample_size: int,
    sample_size: int,
    excluded_count: int,
    effective_sample_size: int,
    series_count: int,
    lag_order: int,
    parameter_count: int,
    equations: tuple[OLSResult, ...] = (),
) -> VectorAutoregressionResult:
    return VectorAutoregressionResult(
        status="not_established",
        reason=reason,
        warnings=_var_warnings(*warnings),
        input_sample_size=input_sample_size,
        sample_size=sample_size,
        excluded_count=excluded_count,
        effective_sample_size=effective_sample_size,
        series_count=series_count,
        lag_order=lag_order,
        deterministic="constant",
        parameter_count=parameter_count,
        degrees_of_freedom_residual=None,
        coefficient_names=_var_coefficient_names(series_count, lag_order),
        equations=equations,
        intercepts=(),
        lag_coefficient_matrices=None,
        residual_covariance=None,
        residual_covariance_denominator=None,
        method="fixed_order_reduced_form_var_ols_constant",
    )


def _vector_autoregression_from_coerced(
    source: tuple[tuple[Decimal | None, ...], ...],
    *,
    lag_order: int,
) -> VectorAutoregressionResult:
    series_count = len(source)
    input_sample_size = len(source[0])
    parameter_count = 1 + series_count * lag_order
    excluded_count = _multivariate_missing_row_count(source)
    if excluded_count:
        return _not_established_var(
            reason="missing_observations_prevent_contiguous_var",
            warnings=("complete_multivariate_sample_required_no_row_deletion",),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size - excluded_count,
            excluded_count=excluded_count,
            effective_sample_size=max(0, input_sample_size - lag_order),
            series_count=series_count,
            lag_order=lag_order,
            parameter_count=parameter_count,
        )

    effective_sample_size = max(0, input_sample_size - lag_order)
    if effective_sample_size <= parameter_count:
        return _not_established_var(
            reason="insufficient_degrees_of_freedom",
            warnings=(),
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            effective_sample_size=effective_sample_size,
            series_count=series_count,
            lag_order=lag_order,
            parameter_count=parameter_count,
        )

    numeric_series = _numeric_multivariate_series(source)
    features, targets = _build_var_design(numeric_series, lag_order)
    coefficient_names = _var_coefficient_names(series_count, lag_order)
    equations = tuple(
        _rename_ols_terms(
            fit_ols(
                target,
                features,
                intercept=True,
                covariance=CLASSICAL_HOMOSKEDASTIC,
                missing="reject_incomplete_rows",
            ),
            coefficient_names,
        )
        for target in targets
    )
    all_warnings = _unique_warnings(
        *(warning for equation in equations for warning in equation.warnings)
    )
    for target_index, equation in enumerate(equations):
        if equation.status != "established":
            return _not_established_var(
                reason=f"equation_{target_index}_{equation.reason or 'ols_not_established'}",
                warnings=all_warnings,
                input_sample_size=input_sample_size,
                sample_size=input_sample_size,
                excluded_count=0,
                effective_sample_size=effective_sample_size,
                series_count=series_count,
                lag_order=lag_order,
                parameter_count=parameter_count,
                equations=equations,
            )

    degrees_of_freedom = effective_sample_size - parameter_count
    residual_columns = tuple(
        tuple(value for value in equation.residuals if value is not None)
        for equation in equations
    )
    if any(len(values) != effective_sample_size for values in residual_columns):
        return _not_established_var(
            reason="equation_residual_sample_not_contiguous",
            warnings=all_warnings,
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            effective_sample_size=effective_sample_size,
            series_count=series_count,
            lag_order=lag_order,
            parameter_count=parameter_count,
            equations=equations,
        )

    intercepts = tuple(equation.coefficients[0].estimate for equation in equations)
    coefficient_values = tuple(
        tuple(coefficient.estimate for coefficient in equation.coefficients)
        for equation in equations
    )
    if any(value is None for values in coefficient_values for value in values):
        return _not_established_var(
            reason="var_coefficients_not_established",
            warnings=all_warnings,
            input_sample_size=input_sample_size,
            sample_size=input_sample_size,
            excluded_count=0,
            effective_sample_size=effective_sample_size,
            series_count=series_count,
            lag_order=lag_order,
            parameter_count=parameter_count,
            equations=equations,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        lag_matrices = tuple(
            tuple(
                tuple(
                    _clean_decimal(
                        coefficient_values[target_index][
                            1 + (lag_index - 1) * series_count + source_index
                        ]
                    )
                    for source_index in range(series_count)
                )
                for target_index in range(series_count)
            )
            for lag_index in range(1, lag_order + 1)
        )
        residual_covariance = tuple(
            tuple(
                _clean_decimal(
                    sum(
                        (
                            residual_columns[target_index][time_index]
                            * residual_columns[source_index][time_index]
                            for time_index in range(effective_sample_size)
                        ),
                        Decimal("0"),
                    )
                    / Decimal(degrees_of_freedom)
                )
                for source_index in range(series_count)
            )
            for target_index in range(series_count)
        )
    return VectorAutoregressionResult(
        status="established",
        reason=None,
        warnings=_var_warnings(*all_warnings),
        input_sample_size=input_sample_size,
        sample_size=input_sample_size,
        excluded_count=0,
        effective_sample_size=effective_sample_size,
        series_count=series_count,
        lag_order=lag_order,
        deterministic="constant",
        parameter_count=parameter_count,
        degrees_of_freedom_residual=degrees_of_freedom,
        coefficient_names=coefficient_names,
        equations=equations,
        intercepts=intercepts,
        lag_coefficient_matrices=lag_matrices,
        residual_covariance=residual_covariance,
        residual_covariance_denominator=degrees_of_freedom,
        method="fixed_order_reduced_form_var_ols_constant",
    )


def vector_autoregression(
    series: Sequence[Sequence[Decimal | int | None]],
    *,
    lag_order: int,
    deterministic: str = "constant",
) -> VectorAutoregressionResult:
    """Fit a fixed reduced-form VAR(p) using series[series_index][time_index].

    Coefficients are ordered as intercept, then every source series at lag one,
    every source series at lag two, and so on. No lag-order selection,
    structural identification, or forecast is performed.
    """

    checked_lag = _validate_fixed_lag(
        lag_order,
        field="lag_order",
        minimum=1,
        maximum=MAX_VAR_LAG,
    )
    _validate_constant_deterministic(deterministic, field="VAR deterministic term")
    source = _coerce_multivariate_series(series, field="series")
    return _vector_autoregression_from_coerced(source, lag_order=checked_lag)



def _not_established_granger(
    *,
    reason: str,
    warnings: tuple[str, ...],
    input_sample_size: int,
    sample_size: int,
    excluded_count: int,
    effective_sample_size: int,
    series_count: int,
    lag_order: int,
    source_index: int,
    target_index: int,
    significance: Decimal,
    unrestricted_var: VectorAutoregressionResult | None = None,
    unrestricted_target_regression: OLSResult | None = None,
    restricted_target_regression: OLSResult | None = None,
    restricted_residual_sum_squares: Decimal | None = None,
    unrestricted_residual_sum_squares: Decimal | None = None,
    residual_sum_squares_reduction: Decimal | None = None,
    numerator_degrees_of_freedom: int | None = None,
    denominator_degrees_of_freedom: int | None = None,
) -> GrangerCausalityResult:
    return GrangerCausalityResult(
        status="not_established",
        reason=reason,
        warnings=_unique_warnings(
            *warnings,
            "fixed_granger_lag_order_no_automatic_selection",
            "granger_is_predictive_precedence_not_structural_causality",
            "exact_granger_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
        ),
        input_sample_size=input_sample_size,
        sample_size=sample_size,
        excluded_count=excluded_count,
        effective_sample_size=effective_sample_size,
        series_count=series_count,
        lag_order=lag_order,
        deterministic="constant",
        source_index=source_index,
        target_index=target_index,
        restriction_count=lag_order,
        numerator_degrees_of_freedom=numerator_degrees_of_freedom,
        denominator_degrees_of_freedom=denominator_degrees_of_freedom,
        restricted_residual_sum_squares=restricted_residual_sum_squares,
        unrestricted_residual_sum_squares=unrestricted_residual_sum_squares,
        residual_sum_squares_reduction=residual_sum_squares_reduction,
        f_statistic=None,
        p_value=None,
        significance=significance,
        decision=None,
        null_hypothesis=(
            "source_series_does_not_granger_cause_target_series_conditional_on_included_series"
        ),
        alternative_hypothesis=(
            "source_series_granger_causes_target_series_conditional_on_included_series"
        ),
        method="conditional_granger_f_test_restricted_vs_unrestricted_var_target_equation",
        reference_distribution=F_REFERENCE_DISTRIBUTION,
        inference_backend=F_INFERENCE_BACKEND,
        unrestricted_var=unrestricted_var,
        unrestricted_target_regression=unrestricted_target_regression,
        restricted_target_regression=restricted_target_regression,
    )


def _validate_granger_index(value: object, *, field: str, series_count: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer series index")
    if not 0 <= value < series_count:
        raise ValidationError(f"{field} must identify an available series")
    return value


def granger_causality(
    series: Sequence[Sequence[Decimal | int | None]],
    *,
    lag_order: int,
    source_index: int,
    target_index: int,
    deterministic: str = "constant",
    significance: Decimal = Decimal("0.05"),
) -> GrangerCausalityResult:
    """Test one caller-selected conditional direction within a fixed VAR(p)."""

    checked_lag = _validate_fixed_lag(
        lag_order,
        field="lag_order",
        minimum=1,
        maximum=MAX_VAR_LAG,
    )
    _validate_constant_deterministic(
        deterministic,
        field="Granger deterministic term",
    )
    checked_significance = _validate_adf_significance(significance)
    source = _coerce_multivariate_series(series, field="series")
    series_count = len(source)
    checked_source = _validate_granger_index(
        source_index,
        field="source_index",
        series_count=series_count,
    )
    checked_target = _validate_granger_index(
        target_index,
        field="target_index",
        series_count=series_count,
    )
    if checked_source == checked_target:
        raise ValidationError("source_index and target_index must be distinct")

    unrestricted_var = _vector_autoregression_from_coerced(
        source,
        lag_order=checked_lag,
    )
    common = {
        "input_sample_size": unrestricted_var.input_sample_size,
        "sample_size": unrestricted_var.sample_size,
        "excluded_count": unrestricted_var.excluded_count,
        "effective_sample_size": unrestricted_var.effective_sample_size,
        "series_count": series_count,
        "lag_order": checked_lag,
        "source_index": checked_source,
        "target_index": checked_target,
        "significance": checked_significance,
        "unrestricted_var": unrestricted_var,
    }
    if unrestricted_var.status != "established":
        return _not_established_granger(
            reason=f"unrestricted_var_{unrestricted_var.reason or 'not_established'}",
            warnings=unrestricted_var.warnings,
            **common,
        )

    numeric_series = _numeric_multivariate_series(source)
    features, targets = _build_var_design(numeric_series, checked_lag)
    source_feature_indices = {
        (lag_index - 1) * series_count + checked_source
        for lag_index in range(1, checked_lag + 1)
    }
    restricted_features = tuple(
        tuple(
            value
            for feature_index, value in enumerate(row)
            if feature_index not in source_feature_indices
        )
        for row in features
    )
    full_names = unrestricted_var.coefficient_names
    restricted_names = ("intercept",) + tuple(
        name
        for feature_index, name in enumerate(full_names[1:])
        if feature_index not in source_feature_indices
    )
    restricted_target = _rename_ols_terms(
        fit_ols(
            targets[checked_target],
            restricted_features,
            intercept=True,
            covariance=CLASSICAL_HOMOSKEDASTIC,
            missing="reject_incomplete_rows",
        ),
        restricted_names,
    )
    unrestricted_target = unrestricted_var.equations[checked_target]
    common = {
        **common,
        "unrestricted_target_regression": unrestricted_target,
        "restricted_target_regression": restricted_target,
    }
    all_warnings = _unique_warnings(
        *unrestricted_var.warnings,
        *unrestricted_target.warnings,
        *restricted_target.warnings,
    )
    if restricted_target.status != "established":
        return _not_established_granger(
            reason=f"restricted_target_{restricted_target.reason or 'ols_not_established'}",
            warnings=all_warnings,
            **common,
        )

    unrestricted_rss = unrestricted_target.residual_sum_squares
    restricted_rss = restricted_target.residual_sum_squares
    denominator_degrees_of_freedom = unrestricted_target.degrees_of_freedom_residual
    if (
        unrestricted_rss is None
        or restricted_rss is None
        or denominator_degrees_of_freedom is None
    ):
        return _not_established_granger(
            reason="residual_sum_squares_or_degrees_not_established",
            warnings=all_warnings,
            unrestricted_residual_sum_squares=unrestricted_rss,
            restricted_residual_sum_squares=restricted_rss,
            denominator_degrees_of_freedom=denominator_degrees_of_freedom,
            numerator_degrees_of_freedom=checked_lag,
            **common,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        rss_reduction = _clean_decimal(restricted_rss - unrestricted_rss)
        tolerance = (
            max(abs(restricted_rss), abs(unrestricted_rss))
            * RANK_RELATIVE_TOLERANCE
            + RANK_ABSOLUTE_TOLERANCE
        )
        if rss_reduction < -tolerance:
            return _not_established_granger(
                reason="negative_restricted_rss_difference",
                warnings=all_warnings,
                restricted_residual_sum_squares=restricted_rss,
                unrestricted_residual_sum_squares=unrestricted_rss,
                residual_sum_squares_reduction=rss_reduction,
                numerator_degrees_of_freedom=checked_lag,
                denominator_degrees_of_freedom=denominator_degrees_of_freedom,
                **common,
            )
        if rss_reduction < 0:
            rss_reduction = Decimal("0")
            all_warnings = _unique_warnings(
                *all_warnings,
                "roundoff_negative_rss_difference_clamped_to_zero",
            )
        if unrestricted_rss <= tolerance:
            return _not_established_granger(
                reason="zero_unrestricted_residual_sum_squares",
                warnings=all_warnings,
                restricted_residual_sum_squares=restricted_rss,
                unrestricted_residual_sum_squares=unrestricted_rss,
                residual_sum_squares_reduction=rss_reduction,
                numerator_degrees_of_freedom=checked_lag,
                denominator_degrees_of_freedom=denominator_degrees_of_freedom,
                **common,
            )
        f_statistic = _clean_decimal(
            (rss_reduction / Decimal(checked_lag))
            / (unrestricted_rss / Decimal(denominator_degrees_of_freedom))
        )
    p_value = _f_survival_probability(
        f_statistic,
        checked_lag,
        denominator_degrees_of_freedom,
    )
    return GrangerCausalityResult(
        status="established",
        reason=None,
        warnings=_unique_warnings(
            *all_warnings,
            "fixed_granger_lag_order_no_automatic_selection",
            "granger_is_predictive_precedence_not_structural_causality",
            "exact_granger_f_requires_gaussian_homoskedastic_independent_errors_exogenous_fixed_design",
            "granger_decision_is_in_sample_and_not_a_structural_causal_claim",
        ),
        input_sample_size=unrestricted_var.input_sample_size,
        sample_size=unrestricted_var.sample_size,
        excluded_count=0,
        effective_sample_size=unrestricted_var.effective_sample_size,
        series_count=series_count,
        lag_order=checked_lag,
        deterministic="constant",
        source_index=checked_source,
        target_index=checked_target,
        restriction_count=checked_lag,
        numerator_degrees_of_freedom=checked_lag,
        denominator_degrees_of_freedom=denominator_degrees_of_freedom,
        restricted_residual_sum_squares=restricted_rss,
        unrestricted_residual_sum_squares=unrestricted_rss,
        residual_sum_squares_reduction=rss_reduction,
        f_statistic=f_statistic,
        p_value=p_value,
        significance=checked_significance,
        decision=(
            "reject_no_granger_causality"
            if p_value <= checked_significance
            else "fail_to_reject_no_granger_causality"
        ),
        null_hypothesis=(
            "source_series_does_not_granger_cause_target_series_conditional_on_included_series"
        ),
        alternative_hypothesis=(
            "source_series_granger_causes_target_series_conditional_on_included_series"
        ),
        method="conditional_granger_f_test_restricted_vs_unrestricted_var_target_equation",
        reference_distribution=F_REFERENCE_DISTRIBUTION,
        inference_backend=F_INFERENCE_BACKEND,
        unrestricted_var=unrestricted_var,
        unrestricted_target_regression=unrestricted_target,
        restricted_target_regression=restricted_target,
    )


engle_granger_test_v3 = engle_granger_cointegration
vector_autoregression_v3 = vector_autoregression
granger_causality_v3 = granger_causality

__all__ += [
    "EngleGrangerCointegrationResult",
    "GrangerCausalityResult",
    "MAC_KINNON_2010_COINTEGRATION_CRITICAL_VALUE_METHOD",
    "MAX_ENGLE_GRANGER_LAG",
    "MAX_VAR_LAG",
    "MAX_VAR_SERIES",
    "VectorAutoregressionResult",
    "engle_granger_cointegration",
    "engle_granger_test_v3",
    "granger_causality",
    "granger_causality_v3",
    "vector_autoregression",
    "vector_autoregression_v3",
]
