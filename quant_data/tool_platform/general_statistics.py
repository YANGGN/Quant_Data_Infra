"""Deterministic, dependency-free statistical kernels for versioned tools.

The public tool adapter owns series validation, missing-data policy, and
response shaping. This module deliberately accepts only already complete,
finite :class:`~decimal.Decimal` values. It owns numerical precision and the
pseudo-random generator so replay does not depend on host settings.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from quant_data.errors import ResourceLimitError, ValidationError


DECIMAL_PRECISION = 50
MAX_STATISTICS_ROWS = 10_000
MAX_STATISTICS_COLUMNS = 32
MAX_DISTRIBUTION_QUANTILES = 32
MAX_BOOTSTRAP_REPLICATES = 10_000
MAX_BOOTSTRAP_DRAW_COUNT = 2_000_000
DEFAULT_BOOTSTRAP_REPLICATES = 1_000
MAX_JACOBI_ROTATIONS = 50_000
JACOBI_RELATIVE_TOLERANCE = Decimal("1e-28")
EIGENVALUE_RELATIVE_TOLERANCE = Decimal("1e-24")

TYPE_7_QUANTILE_METHOD = "hyndman_fan_type_7"
BOOTSTRAP_METHOD = "iid_percentile_bootstrap"
BOOTSTRAP_PRNG = "splitmix64_v1"
_MASK_64 = (1 << 64) - 1
_SPLITMIX_INCREMENT = 0x9E3779B97F4A7C15
_SPLITMIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_SPLITMIX_MULTIPLIER_2 = 0x94D049BB133111EB

DEFAULT_DISTRIBUTION_QUANTILES: tuple[Decimal, ...] = (
    Decimal("0.01"),
    Decimal("0.05"),
    Decimal("0.25"),
    Decimal("0.5"),
    Decimal("0.75"),
    Decimal("0.95"),
    Decimal("0.99"),
)


@dataclass(frozen=True, slots=True)
class QuantileEstimate:
    """One fixed type-7 quantile estimate."""

    probability: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class DistributionDiagnosticsResult:
    """Distributional moments with explicit non-establishment states."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    sample_size: int
    quantile_method: str
    quantiles: tuple[QuantileEstimate, ...]
    minimum: Decimal | None
    maximum: Decimal | None
    mean: Decimal | None
    median: Decimal | None
    raw_median_absolute_deviation: Decimal | None
    population_variance: Decimal | None
    population_standard_deviation: Decimal | None
    population_skewness: Decimal | None
    population_excess_kurtosis: Decimal | None
    jarque_bera_statistic: Decimal | None


@dataclass(frozen=True, slots=True)
class BootstrapConfidenceIntervalResult:
    """A bounded deterministic IID percentile-bootstrap confidence interval."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    statistic: str
    sample_size: int
    replicates: int
    seed: int
    confidence_level: Decimal
    lower_probability: Decimal
    upper_probability: Decimal
    point_estimate: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    method: str
    prng: str
    quantile_method: str


@dataclass(frozen=True, slots=True)
class CovarianceCorrelationResult:
    """Sample covariance/correlation matrices over joint-complete rows."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    sample_size: int
    feature_count: int
    sample_denominator: int | None
    means: tuple[Decimal, ...] | None
    covariance: tuple[tuple[Decimal, ...], ...] | None
    correlation: tuple[tuple[Decimal, ...], ...] | None
    zero_variance_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PCAResult:
    """Deterministic covariance- or correlation-based principal components."""

    status: str
    reason: str | None
    warnings: tuple[str, ...]
    method: str
    sample_size: int
    feature_count: int
    component_count: int
    means: tuple[Decimal, ...] | None
    sample_standard_deviations: tuple[Decimal, ...] | None
    covariance: tuple[tuple[Decimal, ...], ...] | None
    correlation: tuple[tuple[Decimal, ...], ...] | None
    zero_variance_indices: tuple[int, ...]
    eigenvalues: tuple[Decimal, ...] | None
    explained_variance_ratios: tuple[Decimal, ...] | None
    eigenvectors: tuple[tuple[Decimal, ...], ...] | None
    loadings: tuple[tuple[Decimal, ...], ...] | None
    scores: tuple[tuple[Decimal, ...], ...] | None
    jacobi_rotations: int | None
    jacobi_tolerance: Decimal | None


@dataclass(frozen=True, slots=True)
class _SampleMatrices:
    means: tuple[Decimal, ...]
    covariance: tuple[tuple[Decimal, ...], ...]
    correlation: tuple[tuple[Decimal, ...], ...] | None
    sample_standard_deviations: tuple[Decimal, ...]
    zero_variance_indices: tuple[int, ...]


def _clean_decimal(value: Decimal) -> Decimal:
    return Decimal("0") if value.is_zero() else value


def _require_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    return value


def _require_sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field} must be a bounded sequence")
    return value


def _checked_values(values: object, field: str = "values") -> tuple[Decimal, ...]:
    source = _require_sequence(values, field)
    if len(source) > MAX_STATISTICS_ROWS:
        raise ResourceLimitError(f"{field} exceeds the supported observation limit")
    return tuple(
        _require_decimal(value, f"{field}/{index}")
        for index, value in enumerate(source)
    )


def _checked_rows(rows: object) -> tuple[tuple[tuple[Decimal, ...], ...], int]:
    source = _require_sequence(rows, "rows")
    if len(source) > MAX_STATISTICS_ROWS:
        raise ResourceLimitError("rows exceed the supported observation limit")
    if not source:
        return (), 0
    checked_rows: list[tuple[Decimal, ...]] = []
    width: int | None = None
    for row_index, row in enumerate(source):
        checked_source = _require_sequence(row, f"rows/{row_index}")
        if width is None:
            width = len(checked_source)
            if width == 0:
                raise ValidationError("rows require at least one feature column")
            if width > MAX_STATISTICS_COLUMNS:
                raise ResourceLimitError("rows exceed the supported feature limit")
        elif len(checked_source) != width:
            raise ValidationError("rows must have equal feature counts")
        checked_rows.append(
            tuple(
                _require_decimal(value, f"rows/{row_index}/{column_index}")
                for column_index, value in enumerate(checked_source)
            )
        )
    assert width is not None
    return tuple(checked_rows), width


def _checked_probability(value: object, field: str) -> Decimal:
    probability = _require_decimal(value, field)
    if probability < 0 or probability > 1:
        raise ValidationError(f"{field} must be from zero through one")
    return probability


def _checked_quantile_probabilities(value: object | None) -> tuple[Decimal, ...]:
    if value is None:
        return DEFAULT_DISTRIBUTION_QUANTILES
    source = _require_sequence(value, "quantile_probabilities")
    if not source:
        raise ValidationError("quantile_probabilities must not be empty")
    if len(source) > MAX_DISTRIBUTION_QUANTILES:
        raise ResourceLimitError("quantile_probabilities exceed the supported limit")
    result = tuple(
        _checked_probability(probability, f"quantile_probabilities/{index}")
        for index, probability in enumerate(source)
    )
    if len(set(result)) != len(result):
        raise ValidationError("quantile_probabilities must be distinct")
    return result


def _type7_quantile_from_checked(
    sample: Sequence[Decimal], probability: Decimal
) -> Decimal:
    if not sample:
        raise ValidationError("type-7 quantiles require at least one observation")
    ordered = tuple(sorted(sample))
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        rank = Decimal("1") + Decimal(len(ordered) - 1) * probability
        lower_index = int(rank) - 1
        fraction = rank - Decimal(lower_index + 1)
        if lower_index >= len(ordered) - 1:
            return _clean_decimal(ordered[-1])
        return _clean_decimal(
            ordered[lower_index]
            + fraction * (ordered[lower_index + 1] - ordered[lower_index])
        )


def type7_quantile(values: Sequence[Decimal], probability: Decimal) -> Decimal:
    """Return a Hyndman--Fan type-7 quantile from finite Decimals only."""

    sample = _checked_values(values)
    return _type7_quantile_from_checked(
        sample, _checked_probability(probability, "probability")
    )


def _mean(sample: Sequence[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return _clean_decimal(sum(sample, Decimal("0")) / Decimal(len(sample)))


def _median(sample: Sequence[Decimal]) -> Decimal:
    return _type7_quantile_from_checked(sample, Decimal("0.5"))


def distribution_diagnostics(
    values: Sequence[Decimal],
    *,
    quantile_probabilities: Sequence[Decimal] | None = None,
) -> DistributionDiagnosticsResult:
    """Calculate population moments, fixed type-7 quantiles, and Jarque--Bera."""

    sample = _checked_values(values)
    probabilities = _checked_quantile_probabilities(quantile_probabilities)
    if not sample:
        return DistributionDiagnosticsResult(
            status="not_established",
            reason="insufficient_sample",
            warnings=("insufficient_sample",),
            sample_size=0,
            quantile_method=TYPE_7_QUANTILE_METHOD,
            quantiles=(),
            minimum=None,
            maximum=None,
            mean=None,
            median=None,
            raw_median_absolute_deviation=None,
            population_variance=None,
            population_standard_deviation=None,
            population_skewness=None,
            population_excess_kurtosis=None,
            jarque_bera_statistic=None,
        )

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        mean = _mean(sample)
        median = _median(sample)
        quantiles = tuple(
            QuantileEstimate(probability, _type7_quantile_from_checked(sample, probability))
            for probability in probabilities
        )
        raw_mad = _median(tuple(abs(value - median) for value in sample))
        central_second = sum(
            ((value - mean) ** 2 for value in sample), Decimal("0")
        ) / Decimal(len(sample))
        standard_deviation = central_second.sqrt()
        common = {
            "sample_size": len(sample),
            "quantile_method": TYPE_7_QUANTILE_METHOD,
            "quantiles": quantiles,
            "minimum": min(sample),
            "maximum": max(sample),
            "mean": mean,
            "median": median,
            "raw_median_absolute_deviation": raw_mad,
            "population_variance": _clean_decimal(central_second),
            "population_standard_deviation": _clean_decimal(standard_deviation),
        }
        if len(sample) < 2:
            return DistributionDiagnosticsResult(
                status="not_established",
                reason="insufficient_sample",
                warnings=("insufficient_sample",),
                population_skewness=None,
                population_excess_kurtosis=None,
                jarque_bera_statistic=None,
                **common,
            )
        if central_second.is_zero():
            return DistributionDiagnosticsResult(
                status="not_established",
                reason="zero_variance",
                warnings=("zero_variance",),
                population_skewness=None,
                population_excess_kurtosis=None,
                jarque_bera_statistic=None,
                **common,
            )
        central_third = sum(
            ((value - mean) ** 3 for value in sample), Decimal("0")
        ) / Decimal(len(sample))
        central_fourth = sum(
            ((value - mean) ** 4 for value in sample), Decimal("0")
        ) / Decimal(len(sample))
        skewness = central_third / (central_second * standard_deviation)
        excess_kurtosis = central_fourth / (central_second * central_second) - Decimal("3")
        jarque_bera = Decimal(len(sample)) / Decimal("6") * (
            skewness * skewness + excess_kurtosis * excess_kurtosis / Decimal("4")
        )
        return DistributionDiagnosticsResult(
            status="established",
            reason=None,
            warnings=(),
            population_skewness=_clean_decimal(skewness),
            population_excess_kurtosis=_clean_decimal(excess_kurtosis),
            jarque_bera_statistic=_clean_decimal(jarque_bera),
            **common,
        )


class _SplitMix64:
    """Specified PRNG whose sequence is stable across supported Python hosts."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = seed

    def next_uint64(self) -> int:
        self._state = (self._state + _SPLITMIX_INCREMENT) & _MASK_64
        value = self._state
        value = ((value ^ (value >> 30)) * _SPLITMIX_MULTIPLIER_1) & _MASK_64
        value = ((value ^ (value >> 27)) * _SPLITMIX_MULTIPLIER_2) & _MASK_64
        return (value ^ (value >> 31)) & _MASK_64

    def bounded_index(self, upper_bound: int) -> int:
        """Draw uniformly with rejection instead of leaking modulo bias."""

        limit = (1 << 64) - ((1 << 64) % upper_bound)
        while True:
            candidate = self.next_uint64()
            if candidate < limit:
                return candidate % upper_bound


def _checked_seed(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("seed must be an integer")
    if value < 0 or value > _MASK_64:
        raise ValidationError("seed must be an unsigned 64-bit integer")
    return value


def _checked_replicates(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("replicates must be an integer")
    if value < 1:
        raise ValidationError("replicates must be positive")
    if value > MAX_BOOTSTRAP_REPLICATES:
        raise ResourceLimitError("replicates exceed the supported bootstrap limit")
    return value


def _checked_confidence_level(value: object) -> Decimal:
    confidence_level = _require_decimal(value, "confidence_level")
    if confidence_level <= 0 or confidence_level >= 1:
        raise ValidationError("confidence_level must be strictly between zero and one")
    return confidence_level


def _bootstrap_statistic(sample: Sequence[Decimal], statistic: str) -> Decimal:
    if statistic == "mean":
        return _mean(sample)
    if statistic == "median":
        return _median(sample)
    raise ValidationError("statistic must be mean or median")


def bootstrap_confidence_interval(
    values: Sequence[Decimal],
    *,
    statistic: str,
    seed: int,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    confidence_level: Decimal = Decimal("0.95"),
) -> BootstrapConfidenceIntervalResult:
    """Return a deterministic IID percentile-bootstrap interval for mean/median."""

    sample = _checked_values(values)
    if not isinstance(statistic, str) or statistic not in {"mean", "median"}:
        raise ValidationError("statistic must be mean or median")
    checked_seed = _checked_seed(seed)
    checked_replicates = _checked_replicates(replicates)
    checked_confidence_level = _checked_confidence_level(confidence_level)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        lower_probability = (Decimal("1") - checked_confidence_level) / Decimal("2")
        upper_probability = Decimal("1") - lower_probability
        if not sample:
            return BootstrapConfidenceIntervalResult(
                status="not_established",
                reason="insufficient_sample",
                warnings=("insufficient_sample",),
                statistic=statistic,
                sample_size=0,
                replicates=checked_replicates,
                seed=checked_seed,
                confidence_level=checked_confidence_level,
                lower_probability=lower_probability,
                upper_probability=upper_probability,
                point_estimate=None,
                lower_bound=None,
                upper_bound=None,
                method=BOOTSTRAP_METHOD,
                prng=BOOTSTRAP_PRNG,
                quantile_method=TYPE_7_QUANTILE_METHOD,
            )
        if len(sample) * checked_replicates > MAX_BOOTSTRAP_DRAW_COUNT:
            raise ResourceLimitError("bootstrap draw count exceeds the supported limit")
        generator = _SplitMix64(checked_seed)
        bootstrap_statistics = tuple(
            _bootstrap_statistic(
                tuple(
                    sample[generator.bounded_index(len(sample))]
                    for _ in range(len(sample))
                ),
                statistic,
            )
            for _ in range(checked_replicates)
        )
        return BootstrapConfidenceIntervalResult(
            status="established",
            reason=None,
            warnings=(),
            statistic=statistic,
            sample_size=len(sample),
            replicates=checked_replicates,
            seed=checked_seed,
            confidence_level=checked_confidence_level,
            lower_probability=lower_probability,
            upper_probability=upper_probability,
            point_estimate=_bootstrap_statistic(sample, statistic),
            lower_bound=_type7_quantile_from_checked(
                bootstrap_statistics, lower_probability
            ),
            upper_bound=_type7_quantile_from_checked(
                bootstrap_statistics, upper_probability
            ),
            method=BOOTSTRAP_METHOD,
            prng=BOOTSTRAP_PRNG,
            quantile_method=TYPE_7_QUANTILE_METHOD,
        )


def _sample_matrices(rows: Sequence[Sequence[Decimal]], width: int) -> _SampleMatrices:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        means = tuple(
            _clean_decimal(
                sum((row[column] for row in rows), Decimal("0")) / Decimal(len(rows))
            )
            for column in range(width)
        )
        denominator = Decimal(len(rows) - 1)
        covariance = [[Decimal("0") for _ in range(width)] for _ in range(width)]
        for row_index in range(width):
            for column_index in range(row_index, width):
                value = _clean_decimal(
                    sum(
                        (
                            (row[row_index] - means[row_index])
                            * (row[column_index] - means[column_index])
                            for row in rows
                        ),
                        Decimal("0"),
                    )
                    / denominator
                )
                covariance[row_index][column_index] = value
                covariance[column_index][row_index] = value
        covariance_tuple = tuple(tuple(row) for row in covariance)
        zero_variance_indices = tuple(
            index
            for index in range(width)
            if covariance_tuple[index][index].is_zero()
        )
        standard_deviations = tuple(
            Decimal("0")
            if index in zero_variance_indices
            else _clean_decimal(covariance_tuple[index][index].sqrt())
            for index in range(width)
        )
        if zero_variance_indices:
            return _SampleMatrices(
                means=means,
                covariance=covariance_tuple,
                correlation=None,
                sample_standard_deviations=standard_deviations,
                zero_variance_indices=zero_variance_indices,
            )
        correlation = [[Decimal("0") for _ in range(width)] for _ in range(width)]
        for row_index in range(width):
            for column_index in range(row_index, width):
                value = (
                    Decimal("1")
                    if row_index == column_index
                    else _clean_decimal(
                        covariance_tuple[row_index][column_index]
                        / (
                            standard_deviations[row_index]
                            * standard_deviations[column_index]
                        )
                    )
                )
                correlation[row_index][column_index] = value
                correlation[column_index][row_index] = value
        return _SampleMatrices(
            means=means,
            covariance=covariance_tuple,
            correlation=tuple(tuple(row) for row in correlation),
            sample_standard_deviations=standard_deviations,
            zero_variance_indices=(),
        )


def covariance_correlation_matrix(
    rows: Sequence[Sequence[Decimal]],
) -> CovarianceCorrelationResult:
    """Calculate n-1 sample matrices over finite, joint-complete rows."""

    checked_rows, width = _checked_rows(rows)
    if len(checked_rows) < 2:
        return CovarianceCorrelationResult(
            status="not_established",
            reason="insufficient_sample",
            warnings=("insufficient_sample",),
            sample_size=len(checked_rows),
            feature_count=width,
            sample_denominator=None,
            means=None,
            covariance=None,
            correlation=None,
            zero_variance_indices=(),
        )
    matrices = _sample_matrices(checked_rows, width)
    if matrices.zero_variance_indices:
        return CovarianceCorrelationResult(
            status="not_established",
            reason="zero_variance",
            warnings=("zero_variance",),
            sample_size=len(checked_rows),
            feature_count=width,
            sample_denominator=len(checked_rows) - 1,
            means=matrices.means,
            covariance=matrices.covariance,
            correlation=None,
            zero_variance_indices=matrices.zero_variance_indices,
        )
    return CovarianceCorrelationResult(
        status="established",
        reason=None,
        warnings=(),
        sample_size=len(checked_rows),
        feature_count=width,
        sample_denominator=len(checked_rows) - 1,
        means=matrices.means,
        covariance=matrices.covariance,
        correlation=matrices.correlation,
        zero_variance_indices=(),
    )


def _checked_component_count(value: object | None, width: int) -> int:
    if value is None:
        return width
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("component_count must be an integer")
    if value < 1 or value > width:
        raise ValidationError("component_count must be within the feature count")
    return value


def _checked_method(value: object) -> str:
    if not isinstance(value, str) or value not in {"covariance", "correlation"}:
        raise ValidationError("method must be covariance or correlation")
    return value


def _checked_max_rotations(value: object | None, width: int) -> int:
    default = min(MAX_JACOBI_ROTATIONS, max(64, width * width * 128))
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("max_rotations must be an integer")
    if value < 1 or value > MAX_JACOBI_ROTATIONS:
        raise ValidationError("max_rotations is outside the supported range")
    return value


def _largest_off_diagonal(
    matrix: Sequence[Sequence[Decimal]],
) -> tuple[int, int, Decimal]:
    pivot_row, pivot_column = 0, 1
    largest = abs(matrix[pivot_row][pivot_column])
    for row_index in range(len(matrix)):
        for column_index in range(row_index + 1, len(matrix)):
            candidate = abs(matrix[row_index][column_index])
            if candidate > largest:
                pivot_row, pivot_column, largest = (
                    row_index,
                    column_index,
                    candidate,
                )
    return pivot_row, pivot_column, largest


def _jacobi_eigensystem(
    matrix: Sequence[Sequence[Decimal]], *, max_rotations: int
) -> tuple[
    bool,
    tuple[Decimal, ...] | None,
    tuple[tuple[Decimal, ...], ...] | None,
    int,
    Decimal,
]:
    """Diagonalize a symmetric matrix with deterministic largest-pivot steps."""

    width = len(matrix)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        if width == 1:
            return (
                True,
                (_clean_decimal(matrix[0][0]),),
                ((Decimal("1"),),),
                0,
                Decimal("0"),
            )
        working = [list(row) for row in matrix]
        vectors = [
            [
                Decimal("1") if row_index == column_index else Decimal("0")
                for column_index in range(width)
            ]
            for row_index in range(width)
        ]
        scale = max(abs(value) for row in working for value in row)
        tolerance = _clean_decimal(scale * JACOBI_RELATIVE_TOLERANCE)
        rotations = 0
        while True:
            pivot_row, pivot_column, largest = _largest_off_diagonal(working)
            if largest <= tolerance:
                eigenvalues = tuple(
                    _clean_decimal(working[index][index]) for index in range(width)
                )
                eigenvectors = tuple(
                    tuple(
                        _clean_decimal(vectors[row_index][column_index])
                        for row_index in range(width)
                    )
                    for column_index in range(width)
                )
                return True, eigenvalues, eigenvectors, rotations, tolerance
            if rotations >= max_rotations:
                return False, None, None, rotations, tolerance

            app = working[pivot_row][pivot_row]
            aqq = working[pivot_column][pivot_column]
            apq = working[pivot_row][pivot_column]
            tau = (aqq - app) / (Decimal("2") * apq)
            square_root = (Decimal("1") + tau * tau).sqrt()
            tangent = (
                Decimal("1") / (tau + square_root)
                if tau >= 0
                else Decimal("-1") / (-tau + square_root)
            )
            cosine = Decimal("1") / (Decimal("1") + tangent * tangent).sqrt()
            sine = tangent * cosine

            for index in range(width):
                if index == pivot_row or index == pivot_column:
                    continue
                first = working[index][pivot_row]
                second = working[index][pivot_column]
                updated_first = _clean_decimal(cosine * first - sine * second)
                updated_second = _clean_decimal(sine * first + cosine * second)
                working[index][pivot_row] = updated_first
                working[pivot_row][index] = updated_first
                working[index][pivot_column] = updated_second
                working[pivot_column][index] = updated_second
            working[pivot_row][pivot_row] = _clean_decimal(app - tangent * apq)
            working[pivot_column][pivot_column] = _clean_decimal(aqq + tangent * apq)
            working[pivot_row][pivot_column] = Decimal("0")
            working[pivot_column][pivot_row] = Decimal("0")

            for index in range(width):
                first = vectors[index][pivot_row]
                second = vectors[index][pivot_column]
                vectors[index][pivot_row] = _clean_decimal(cosine * first - sine * second)
                vectors[index][pivot_column] = _clean_decimal(sine * first + cosine * second)
            rotations += 1


def _canonical_eigenvector(vector: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Normalize and orient by the largest absolute loading, breaking ties low."""

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        norm_squared = sum((value * value for value in vector), Decimal("0"))
        if norm_squared.is_zero():
            raise ArithmeticError("zero eigenvector")
        norm = norm_squared.sqrt()
        normalized = tuple(_clean_decimal(value / norm) for value in vector)
        anchor_index = 0
        anchor_magnitude = abs(normalized[0])
        for index, value in enumerate(normalized[1:], start=1):
            magnitude = abs(value)
            if magnitude > anchor_magnitude:
                anchor_index, anchor_magnitude = index, magnitude
        if normalized[anchor_index] < 0:
            return tuple(_clean_decimal(-value) for value in normalized)
        return normalized


def _pca_failure(
    *,
    reason: str,
    method: str,
    sample_size: int,
    feature_count: int,
    component_count: int,
    means: tuple[Decimal, ...] | None = None,
    standard_deviations: tuple[Decimal, ...] | None = None,
    covariance: tuple[tuple[Decimal, ...], ...] | None = None,
    correlation: tuple[tuple[Decimal, ...], ...] | None = None,
    zero_variance_indices: tuple[int, ...] = (),
    jacobi_rotations: int | None = None,
    jacobi_tolerance: Decimal | None = None,
) -> PCAResult:
    return PCAResult(
        status="not_established",
        reason=reason,
        warnings=(reason,),
        method=method,
        sample_size=sample_size,
        feature_count=feature_count,
        component_count=component_count,
        means=means,
        sample_standard_deviations=standard_deviations,
        covariance=covariance,
        correlation=correlation,
        zero_variance_indices=zero_variance_indices,
        eigenvalues=None,
        explained_variance_ratios=None,
        eigenvectors=None,
        loadings=None,
        scores=None,
        jacobi_rotations=jacobi_rotations,
        jacobi_tolerance=jacobi_tolerance,
    )


def principal_component_analysis(
    rows: Sequence[Sequence[Decimal]],
    *,
    method: str = "covariance",
    component_count: int | None = None,
    max_rotations: int | None = None,
) -> PCAResult:
    """Calculate bounded PCA using covariance or correlation as its matrix.

    Covariance PCA projects mean-centered values. Correlation PCA projects
    values standardized with their n-1 sample standard deviations. A constant
    feature is an explicit failure for both modes: callers must remove it
    rather than silently accepting an undefined correlation dimension.
    """

    checked_method = _checked_method(method)
    checked_rows, width = _checked_rows(rows)
    selected_component_count = (
        _checked_component_count(component_count, width) if width else 0
    )
    if len(checked_rows) < 2:
        return _pca_failure(
            reason="insufficient_sample",
            method=checked_method,
            sample_size=len(checked_rows),
            feature_count=width,
            component_count=selected_component_count,
        )
    checked_max_rotations = _checked_max_rotations(max_rotations, width)
    matrices = _sample_matrices(checked_rows, width)
    if matrices.zero_variance_indices:
        return _pca_failure(
            reason="zero_variance",
            method=checked_method,
            sample_size=len(checked_rows),
            feature_count=width,
            component_count=selected_component_count,
            means=matrices.means,
            standard_deviations=matrices.sample_standard_deviations,
            covariance=matrices.covariance,
            correlation=matrices.correlation,
            zero_variance_indices=matrices.zero_variance_indices,
        )
    assert matrices.correlation is not None
    source_matrix = (
        matrices.covariance
        if checked_method == "covariance"
        else matrices.correlation
    )
    converged, raw_eigenvalues, raw_eigenvectors, rotations, tolerance = (
        _jacobi_eigensystem(source_matrix, max_rotations=checked_max_rotations)
    )
    if not converged:
        return _pca_failure(
            reason="jacobi_nonconvergence",
            method=checked_method,
            sample_size=len(checked_rows),
            feature_count=width,
            component_count=selected_component_count,
            means=matrices.means,
            standard_deviations=matrices.sample_standard_deviations,
            covariance=matrices.covariance,
            correlation=matrices.correlation,
            jacobi_rotations=rotations,
            jacobi_tolerance=tolerance,
        )
    assert raw_eigenvalues is not None
    assert raw_eigenvectors is not None

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        matrix_scale = max(abs(value) for row in source_matrix for value in row)
        eigenvalue_tolerance = matrix_scale * EIGENVALUE_RELATIVE_TOLERANCE
        pairs: list[tuple[Decimal, tuple[Decimal, ...], int]] = []
        for index, (eigenvalue, vector) in enumerate(
            zip(raw_eigenvalues, raw_eigenvectors, strict=True)
        ):
            if eigenvalue < 0:
                if abs(eigenvalue) <= eigenvalue_tolerance:
                    eigenvalue = Decimal("0")
                else:
                    return _pca_failure(
                        reason="non_positive_semidefinite_matrix",
                        method=checked_method,
                        sample_size=len(checked_rows),
                        feature_count=width,
                        component_count=selected_component_count,
                        means=matrices.means,
                        standard_deviations=matrices.sample_standard_deviations,
                        covariance=matrices.covariance,
                        correlation=matrices.correlation,
                        jacobi_rotations=rotations,
                        jacobi_tolerance=tolerance,
                    )
            try:
                canonical_vector = _canonical_eigenvector(vector)
            except ArithmeticError:
                return _pca_failure(
                    reason="eigenvector_normalization_failed",
                    method=checked_method,
                    sample_size=len(checked_rows),
                    feature_count=width,
                    component_count=selected_component_count,
                    means=matrices.means,
                    standard_deviations=matrices.sample_standard_deviations,
                    covariance=matrices.covariance,
                    correlation=matrices.correlation,
                    jacobi_rotations=rotations,
                    jacobi_tolerance=tolerance,
                )
            pairs.append((_clean_decimal(eigenvalue), canonical_vector, index))
        pairs.sort(key=lambda item: (-item[0], item[2]))
        total_variance = sum((item[0] for item in pairs), Decimal("0"))
        if total_variance <= 0:
            return _pca_failure(
                reason="zero_variance",
                method=checked_method,
                sample_size=len(checked_rows),
                feature_count=width,
                component_count=selected_component_count,
                means=matrices.means,
                standard_deviations=matrices.sample_standard_deviations,
                covariance=matrices.covariance,
                correlation=matrices.correlation,
                jacobi_rotations=rotations,
                jacobi_tolerance=tolerance,
            )
        retained = pairs[:selected_component_count]
        eigenvalues = tuple(item[0] for item in retained)
        eigenvectors = tuple(item[1] for item in retained)
        explained = tuple(
            _clean_decimal(eigenvalue / total_variance)
            for eigenvalue in eigenvalues
        )
        loadings = tuple(
            tuple(_clean_decimal(value * eigenvalue.sqrt()) for value in vector)
            for eigenvalue, vector in zip(eigenvalues, eigenvectors, strict=True)
        )
        basis_rows = tuple(
            tuple(
                _clean_decimal(
                    (row[column] - matrices.means[column])
                    if checked_method == "covariance"
                    else (row[column] - matrices.means[column])
                    / matrices.sample_standard_deviations[column]
                )
                for column in range(width)
            )
            for row in checked_rows
        )
        scores = tuple(
            tuple(
                _clean_decimal(
                    sum(
                        (
                            basis_row[index] * vector[index]
                            for index in range(width)
                        ),
                        Decimal("0"),
                    )
                )
                for vector in eigenvectors
            )
            for basis_row in basis_rows
        )
        return PCAResult(
            status="established",
            reason=None,
            warnings=(),
            method=checked_method,
            sample_size=len(checked_rows),
            feature_count=width,
            component_count=selected_component_count,
            means=matrices.means,
            sample_standard_deviations=matrices.sample_standard_deviations,
            covariance=matrices.covariance,
            correlation=matrices.correlation,
            zero_variance_indices=(),
            eigenvalues=eigenvalues,
            explained_variance_ratios=explained,
            eigenvectors=eigenvectors,
            loadings=loadings,
            scores=scores,
            jacobi_rotations=rotations,
            jacobi_tolerance=tolerance,
        )


__all__ = [
    "BOOTSTRAP_METHOD",
    "BOOTSTRAP_PRNG",
    "BootstrapConfidenceIntervalResult",
    "CovarianceCorrelationResult",
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "DEFAULT_DISTRIBUTION_QUANTILES",
    "DistributionDiagnosticsResult",
    "MAX_BOOTSTRAP_REPLICATES",
    "MAX_JACOBI_ROTATIONS",
    "PCAResult",
    "QuantileEstimate",
    "TYPE_7_QUANTILE_METHOD",
    "bootstrap_confidence_interval",
    "covariance_correlation_matrix",
    "distribution_diagnostics",
    "principal_component_analysis",
    "type7_quantile",
]
