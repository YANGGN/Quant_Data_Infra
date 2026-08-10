"""Public adapters over the pure Stage 5 analytical primitives."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
from typing import Any, Mapping, Sequence

from quant_data.contracts import (
    ExclusionV1,
    LineageRef,
    Observation,
    TimeSeries,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.temporal import TemporalPrecision, TemporalValue

from .analytics import (
    AnalyticsSeries,
    align_series,
    correlate_series,
    describe_series,
    event_study,
    ordinary_least_squares,
    resample_series,
    return_series,
    rolling_regression,
    stationarity_diagnostics,
    walk_forward_summary,
)
from .results import (
    DiagnosticV1,
    QueryResult,
    RecordV1,
    fields_from_mapping,
    research_envelope,
)


ANALYTIC_OPERATION_NAMES = frozenset(
    {
        "timeseries.transform",
        "timeseries.align",
        "timeseries.correlation",
        "econometrics.regression",
        "econometrics.stationarity",
        "econometrics.rolling_regression",
        "econometrics.structural_breaks",
        "econometrics.local_projection",
        "research.point_in_time_panel",
        "data.quality_audit",
        "research.event_study",
        "alpha.signal_diagnostics",
        "research.walk_forward_backtest",
        "research.robustness_suite",
        "stats.multiple_testing",
        "forecast.evaluate",
    }
)


def _parameters(arguments: Mapping[str, Any]) -> dict[str, Any]:
    raw = arguments.get("parameters", ())
    if not isinstance(raw, (list, tuple)):
        raise ValidationError("parameters must be a bounded field list")
    result: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"name", "value"}:
            raise ValidationError("parameter fields are invalid")
        name = item["name"]
        if not isinstance(name, str) or not name or name in result:
            raise ValidationError("parameter names must be unique nonempty strings")
        result[name] = item["value"]
    return result


def _text_parameter(
    parameters: Mapping[str, Any], name: str, default: str
) -> str:
    value = parameters.get(name, default)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} must be a nonempty string")
    return value


def _integer_parameter(
    parameters: Mapping[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be an integer")
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise ValidationError(f"{name} must be an integer")
        value = int(value)
    if not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{name} is outside its supported bound")
    return value


def _series(arguments: Mapping[str, Any]) -> tuple[TimeSeries, ...]:
    value = arguments.get("series")
    raw: Sequence[Any] = value if isinstance(value, (list, tuple)) else (value,)
    if not raw or not all(isinstance(item, TimeSeries) for item in raw):
        raise ValidationError("operation requires typed TimeSeries inputs")
    values = tuple(raw)
    for item in values:
        item.validate_lineage()
    return values


def _lineage(values: Sequence[TimeSeries]) -> tuple[LineageRef, ...]:
    result: list[LineageRef] = []
    for item in values:
        dataset = item.provenance.get("dataset_id")
        role = item.provenance.get("store_role")
        if isinstance(dataset, str) and role in {"market", "macro", "company", "news"}:
            reference = LineageRef(dataset, role, item.series_id)
            if reference not in result:
                result.append(reference)
    return tuple(result)


def _summary_record(value: Mapping[str, Any]) -> RecordV1:
    fields = {
        name: item
        for name, item in value.items()
        if isinstance(item, (str, Decimal, int, bool, type(None)))
        and not isinstance(item, float)
    }
    return RecordV1("summary", fields_from_mapping(fields))


def _mapping_result(
    name: str,
    value: Mapping[str, Any],
    inputs: Sequence[TimeSeries],
    arguments: Mapping[str, Any],
    *,
    research: bool = False,
) -> QueryResult:
    established = value.get("status") not in {"not_established", "unsupported"}
    reason = value.get("reason")
    warnings = tuple(
        WarningV1(item, item.replace("_", " "))
        for item in sorted(set(value.get("warnings", ())))
        if isinstance(item, str) and item
    )
    records: list[RecordV1] = [_summary_record(value)]
    for field_name, items in sorted(value.items()):
        if isinstance(items, (list, tuple)) and all(
            isinstance(item, (str, Decimal, int, bool, type(None)))
            and not isinstance(item, float)
            for item in items
        ):
            records.extend(
                RecordV1(field_name, fields_from_mapping({"index": index, "value": item}))
                for index, item in enumerate(items)
            )
    exclusions = (
        (ExclusionV1("not_established", reason),)
        if not established and isinstance(reason, str)
        else ()
    )
    lineage = _lineage(inputs)
    research_result: dict[str, Any] = {}
    if research:
        research_result["research_contract"] = research_envelope(
            name,
            arguments,
            inputs,
            lineage,
            exclusions=exclusions,
        )
    return QueryResult(
        tool=name,
        status="ok" if established else "not_established",
        records=tuple(records),
        diagnostics=(
            DiagnosticV1(
                "analytic_result",
                "Deterministic offline analytical primitive completed.",
                fields_from_mapping({"input_series_count": len(inputs)}),
            ),
        ),
        warnings=warnings,
        exclusions=exclusions,
        lineage=lineage,
        truncation=TruncationV1(False, 10_000, len(records), len(records), False),
        **research_result,
    )


def _exact_temporal(raw: object, precision: object) -> TemporalValue | None:
    if not isinstance(raw, str) or not raw or precision not in {"date", "datetime"}:
        return None
    try:
        parsed = TemporalValue.parse(raw, pointer="/transformation/source_time")
    except ValidationError:
        return None
    return parsed if parsed.precision.value == precision else None


def _conservative_exact_time(values: Sequence[TemporalValue]) -> tuple[str, str]:
    if not values:
        raise ValidationError("transformation requires source timing values")
    if all(item.precision is TemporalPrecision.DATE for item in values):
        latest = max(values, key=lambda item: item.value)
        assert latest.raw is not None
        return latest.raw, TemporalPrecision.DATE.value
    if all(item.precision is TemporalPrecision.DATETIME for item in values):
        latest = max(values, key=lambda item: item.value)
        assert latest.raw is not None
        return latest.raw, TemporalPrecision.DATETIME.value

    # A mixed date/datetime set cannot support an exact derived instant. Keep
    # only the latest defensible calendar date rather than manufacturing one.
    calendar_dates: list[date] = []
    for item in values:
        if item.precision is TemporalPrecision.DATETIME:
            assert isinstance(item.value, datetime)
            calendar_dates.append(item.value.date())
        else:
            assert isinstance(item.value, date)
            calendar_dates.append(item.value)
    return max(calendar_dates).isoformat(), TemporalPrecision.DATE.value


def _conservative_availability(contributors: Sequence[Observation]) -> tuple[str | None, str]:
    parsed = tuple(
        _exact_temporal(item.available_at, item.available_precision)
        for item in contributors
    )
    if any(item is None for item in parsed):
        return None, TemporalPrecision.UNKNOWN.value
    return _conservative_exact_time(tuple(item for item in parsed if item is not None))


def _conservative_capture(contributors: Sequence[Observation]) -> tuple[str, str]:
    captures: list[str] = []
    parsed: list[TemporalValue | None] = []
    for item in contributors:
        if not isinstance(item.captured_at, str) or not item.captured_at:
            raise ValidationError("transformation requires source capture timing")
        captures.append(item.captured_at)
        parsed.append(_exact_temporal(item.captured_at, item.captured_precision))
    if any(item is None for item in parsed):
        # The source raw label remains visible, but its precision stays unknown
        # so callers cannot treat it as an invented exact capture instant.
        return max(captures), TemporalPrecision.UNKNOWN.value
    return _conservative_exact_time(tuple(item for item in parsed if item is not None))


def _source_observation_index(source: TimeSeries) -> dict[tuple[str, str, str], Observation]:
    result: dict[tuple[str, str, str], Observation] = {}
    for item in source.observations:
        key = (item.version_id, item.evidence_id, item.snapshot_id)
        if key in result:
            raise ValidationError("transformation source lineage is ambiguous")
        result[key] = item
    return result


def _contributors_for(
    item: Any,
    source_index: Mapping[tuple[str, str, str], Observation],
) -> tuple[Observation, ...]:
    tokens = item.source_lineage
    if not tokens:
        raise ValidationError("transformation source lineage is incomplete")

    # Preserve the normal version/evidence/snapshot triplet path when it is
    # available. AnalyticsObservation canonicalizes repeated strings, however,
    # so an observation whose three source identifiers coincide may retain one
    # uniquely resolvable token rather than an artificial triplet.
    if len(tokens) % 3 == 0:
        result: list[Observation] = []
        seen: set[tuple[str, str, str]] = set()
        for index in range(0, len(tokens), 3):
            key = (tokens[index], tokens[index + 1], tokens[index + 2])
            source = source_index.get(key)
            if source is None:
                break
            if key not in seen:
                result.append(source)
                seen.add(key)
        else:
            return tuple(result)

    result = []
    seen = set()
    for token in tokens:
        matches = [
            (key, source)
            for key, source in source_index.items()
            if token in key
        ]
        if len(matches) != 1:
            raise ValidationError("transformation source lineage is unavailable or ambiguous")
        key, source = matches[0]
        if key not in seen:
            result.append(source)
            seen.add(key)
    return tuple(result)


def _derived_observation_identity(
    source: TimeSeries,
    value: AnalyticsSeries,
    item: Any,
) -> str:
    material = {
        "input_lineage_digest": source.lineage_digest,
        "source_lineage": list(item.source_lineage),
        "period_start": item.period_start,
        "period_end": item.period_end,
        "value": item.value,
        "missing_reason": item.missing_reason,
        "transformations": list(value.transformations),
    }
    return "derived:" + hashlib.sha256(
        dumps_strict(material).encode("utf-8")
    ).hexdigest()


def _value_contract(source: TimeSeries, value: AnalyticsSeries) -> tuple[str, str]:
    transformation = value.transformations[-1] if value.transformations else ""
    if transformation.startswith("return:"):
        return "return", "1"
    representation = source.metadata.get("value_representation")
    scale = source.metadata.get("scale")
    if not isinstance(representation, str) or not representation:
        raise ValidationError("transformation requires source value_representation metadata")
    if not isinstance(scale, str) or not scale:
        raise ValidationError("transformation requires source scale metadata")
    return representation, scale


def _rehydrate_observation(
    item: Any,
    value: AnalyticsSeries,
    source: TimeSeries,
    source_index: Mapping[tuple[str, str, str], Observation],
    *,
    value_representation: str,
    scale: str,
) -> Observation:
    contributors = _contributors_for(item, source_index)
    dimensions = dict(contributors[0].dimensions)
    if any(dict(candidate.dimensions) != dimensions for candidate in contributors[1:]):
        raise ValidationError("transformation cannot combine contradictory dimensions")
    availability = _conservative_availability(contributors)
    if item.missing_reason == "resample_incomplete_bucket":
        # A partial source set does not prove when a full calendar bucket could
        # be declared missing, so do not emit a misleading availability instant.
        availability = (None, TemporalPrecision.UNKNOWN.value)
    captured_at, captured_precision = _conservative_capture(contributors)
    identity = _derived_observation_identity(source, value, item)
    flags = set(item.quality_flags)
    for contributor in contributors:
        flags.update(
            flag
            for flag in contributor.quality_flags
            if isinstance(flag, str) and flag
        )
    flags.add(f"source_lineage:{source.lineage_digest}")
    flags.update(f"transformation:{step}" for step in value.transformations)
    return Observation(
        period_start=item.period_start,
        period_end=item.period_end,
        value=item.value,
        missing_reason=item.missing_reason,
        unit=value.unit,
        value_representation=value_representation,
        scale=scale,
        vintage_at=identity,
        available_at=availability[0],
        available_precision=availability[1],
        captured_at=captured_at,
        captured_precision=captured_precision,
        version_id=identity,
        evidence_id=identity,
        snapshot_id=identity,
        run_id=identity,
        dimensions=dimensions,
        quality_flags=tuple(sorted(flags)),
    )


def _rehydrate_series(value: AnalyticsSeries, source: TimeSeries) -> TimeSeries:
    source_payload = source.to_primitive()
    source_index = _source_observation_index(source)
    value_representation, scale = _value_contract(source, value)
    metadata = source_payload["metadata"]
    metadata.update(
        {
            "unit": value.unit,
            "frequency": value.frequency,
            "value_representation": value_representation,
            "scale": scale,
        }
    )
    audit = source_payload["audit"]
    audit["selected_count"] = len(value.observations)
    markers = tuple(f"transformation:{step}" for step in value.transformations)
    warnings = tuple(sorted(set((*source.warnings, *markers))))
    return TimeSeries(
        series_id=value.series_id,
        metadata=metadata,
        observations=tuple(
            _rehydrate_observation(
                item,
                value,
                source,
                source_index,
                value_representation=value_representation,
                scale=scale,
            )
            for item in value.observations
        ),
        warnings=warnings,
        audit=audit,
        provenance=source_payload["provenance"],
        truncated=source.truncated,
    )


def _transformed_result(name: str, value: AnalyticsSeries, inputs: Sequence[TimeSeries]) -> QueryResult:
    transformed = _rehydrate_series(value, inputs[0])
    records = tuple(
        RecordV1(
            "observation",
            fields_from_mapping(
                {
                    "missing_reason": item.missing_reason,
                    "period_end": item.period_end,
                    "period_start": item.period_start,
                    "value": item.value,
                }
            ),
        )
        for item in value.observations
    )
    return QueryResult(
        tool=name,
        records=records,
        series=(transformed,),
        diagnostics=(
            DiagnosticV1(
                "transformation_lineage",
                "Transformation preserved immutable input lineage.",
                fields_from_mapping({"lineage_digest": transformed.lineage_digest}),
            ),
        ),
        lineage=_lineage(inputs),
        truncation=TruncationV1(False, 10_000, len(records), len(records), False),
    )


def _aligned(inputs: Sequence[TimeSeries]) -> tuple[list[Any], list[list[Any]]]:
    rows = align_series(inputs, join="inner")["rows"]
    return [row["values"][0] for row in rows], [list(row["values"][1:]) for row in rows]


def _not_established(
    name: str,
    inputs: Sequence[TimeSeries],
    arguments: Mapping[str, Any],
    reason: str,
) -> QueryResult:
    return _mapping_result(
        name,
        {"status": "not_established", "reason": reason, "warnings": ()},
        inputs,
        arguments,
        research=name.startswith(("research.", "data.", "alpha.", "stats.", "forecast.")),
    )


def _multiple_testing(values: Sequence[Decimal | None]) -> Mapping[str, Any]:
    sample = sorted(item for item in values if item is not None)
    if not sample:
        return {"status": "not_established", "reason": "no_p_values", "warnings": ()}
    if any(item < 0 or item > 1 for item in sample):
        raise ValidationError("p-values must be within zero and one")
    count = Decimal(len(sample))
    adjusted = [Decimal("1")] * len(sample)
    running = Decimal("1")
    for index in range(len(sample) - 1, -1, -1):
        running = min(running, Decimal("1"), sample[index] * count / Decimal(index + 1))
        adjusted[index] = running
    return {
        "status": "established",
        "method": "benjamini_hochberg",
        "sample_size": len(sample),
        "adjusted_p_values": tuple(adjusted),
        "warnings": (),
    }


def invoke_analytic(name: str, arguments: Mapping[str, Any]) -> QueryResult:
    if name not in ANALYTIC_OPERATION_NAMES:
        raise ValidationError("Unknown analytical operation")
    inputs = _series(arguments)
    parameters = _parameters(arguments)
    research = name.startswith(("research.", "data.", "alpha.", "stats.", "forecast."))

    if name == "timeseries.transform":
        operation = _text_parameter(parameters, "operation", "returns")
        if operation == "resample":
            value = resample_series(
                inputs[0],
                target_frequency=_text_parameter(
                    parameters, "target_frequency", "monthly"
                ),
                method=_text_parameter(parameters, "method", "mean"),
            )
        elif operation == "returns":
            value = return_series(
                inputs[0],
                horizon=_integer_parameter(
                    parameters,
                    "horizon",
                    1,
                    minimum=1,
                    maximum=252,
                ),
                direction=_text_parameter(parameters, "direction", "trailing"),
                method=_text_parameter(parameters, "method", "simple"),
            )
        else:
            raise ValidationError("Unsupported timeseries transformation")
        return _transformed_result(name, value, inputs)
    if name in {"timeseries.align", "research.point_in_time_panel"}:
        aligned = align_series(
            inputs, join=_text_parameter(parameters, "join", "inner")
        )
        records = tuple(
            RecordV1(
                "aligned_observation",
                fields_from_mapping(
                    {
                        "period_end": row["period_end"],
                        "period_start": row["period_start"],
                        **{f"value_{index}": item for index, item in enumerate(row["values"])},
                    }
                ),
            )
            for row in aligned["rows"]
        )
        lineage = _lineage(inputs)
        research_result: dict[str, Any] = {}
        if research:
            research_result["research_contract"] = research_envelope(
                name,
                arguments,
                inputs,
                lineage,
            )
        return QueryResult(
            tool=name,
            records=records,
            lineage=lineage,
            truncation=TruncationV1(False, 10_000, len(records), len(records), False),
            **research_result,
        )
    if name == "timeseries.correlation":
        if len(inputs) != 2:
            return _not_established(name, inputs, arguments, "two_series_required")
        return _mapping_result(name, correlate_series(inputs[0], inputs[1]), inputs, arguments)
    if name in {"econometrics.regression", "econometrics.rolling_regression"}:
        if len(inputs) < 2:
            return _not_established(name, inputs, arguments, "regression_requires_predictors")
        y, x = _aligned(inputs)
        value = ordinary_least_squares(y, x) if name.endswith("regression") and not name.endswith("rolling_regression") else {
            "status": "established",
            "estimate_count": len(
                rolling_regression(
                    y,
                    x,
                    window=_integer_parameter(
                        parameters,
                        "window",
                        3,
                        minimum=2,
                        maximum=10_000,
                    ),
                )
            ),
            "warnings": (),
        }
        return _mapping_result(name, value, inputs, arguments)
    if name == "econometrics.stationarity":
        return _mapping_result(name, stationarity_diagnostics([item.value for item in inputs[0].observations]), inputs, arguments)
    if name in {"econometrics.structural_breaks", "econometrics.local_projection"}:
        return _not_established(name, inputs, arguments, "fixture_semantics_not_established")
    if name == "data.quality_audit":
        summaries = [describe_series(item) for item in inputs]
        return _mapping_result(
            name,
            {
                "status": "established",
                "series_count": len(summaries),
                "missing_count": sum(int(item["missing_count"]) for item in summaries),
                "warnings": (),
            },
            inputs,
            arguments,
            research=True,
        )
    if name == "research.event_study":
        values = [item.value for item in inputs[0].observations]
        return _mapping_result(
            name,
            event_study(
                values,
                event_index=_integer_parameter(
                    parameters,
                    "event_index",
                    max(0, len(values) // 2),
                    minimum=0,
                    maximum=max(0, len(values) - 1),
                ),
                pre=_integer_parameter(
                    parameters,
                    "pre",
                    0,
                    minimum=0,
                    maximum=10_000,
                ),
                post=_integer_parameter(
                    parameters,
                    "post",
                    0,
                    minimum=0,
                    maximum=10_000,
                ),
            ),
            inputs,
            arguments,
            research=True,
        )
    if name in {"research.walk_forward_backtest", "forecast.evaluate"}:
        if len(inputs) != 2:
            return _not_established(name, inputs, arguments, "two_series_required")
        rows = align_series(inputs, join="inner")["rows"]
        value = walk_forward_summary(
            [row["values"][0] for row in rows], [row["values"][1] for row in rows]
        )
        return _mapping_result(name, value, inputs, arguments, research=True)
    if name == "stats.multiple_testing":
        return _mapping_result(
            name,
            _multiple_testing([item.value for item in inputs[0].observations]),
            inputs,
            arguments,
            research=True,
        )
    if name == "alpha.signal_diagnostics" and len(inputs) >= 2:
        return _mapping_result(name, correlate_series(inputs[0], inputs[1]), inputs, arguments, research=True)
    if name in {"alpha.signal_diagnostics", "research.robustness_suite"}:
        return _mapping_result(name, describe_series(inputs[0]), inputs, arguments, research=True)
    raise ValidationError("Analytical operation is not implemented")


__all__ = ("ANALYTIC_OPERATION_NAMES", "invoke_analytic")
