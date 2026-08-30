"""Stage 10 close-to-close return engine and explicit public v2 adapter.

The adapter is reachable only through the reviewed ``tool_version=2.0.0``
selector. The existing public v1 contracts remain the omitted-selector default
and their result semantics are unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from quant_data.contracts import (
    LineageRef,
    Observation,
    TimeSeries,
    TruncationV1,
    WarningV1,
    exact_decimal_quality_flag,
)
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.market.stage10_series import (
    Stage10DailyPriceQuery,
    Stage10DailyPriceRepository,
)
from quant_data.registry import Registry
from quant_data.stores import StoreMap
from quant_data.temporal import TemporalPrecision, TemporalValue

from .arguments import Stage10MarketReturnArgumentsV2
from .analytics import AnalyticsSeries, describe_series, return_series
from .context import ToolExecutionContext
from .results import DiagnosticV1, QueryResult, fields_from_mapping


RETURN_PRIMITIVE = "quant_data.tool_platform.analytics.return_series"
RETURN_PRIMITIVE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Stage10MarketReturnRequest:
    """One typed request over a fixed Stage 10 close-price selection."""

    prices: Stage10DailyPriceQuery
    direction: str
    method: str
    horizon: int

    def __post_init__(self) -> None:
        if not isinstance(self.prices, Stage10DailyPriceQuery):
            raise ValidationError("Market returns require a typed Stage 10 price query")
        if self.direction not in {"trailing", "forward"}:
            raise ValidationError("Market return direction is unsupported")
        if self.method not in {"simple", "log"}:
            raise ValidationError("Market return method is unsupported")
        if (
            isinstance(self.horizon, bool)
            or not isinstance(self.horizon, int)
            or not 1 <= self.horizon <= 252
        ):
            raise ValidationError("Market return horizon must be from 1 through 252")
        if self.direction == "forward" and self.prices.limit + self.horizon > 10_000:
            raise ResourceLimitError(
                "Forward market returns exceed the bounded lookahead limit"
            )


@dataclass(frozen=True, slots=True)
class Stage10MarketQualityReport:
    """Path-free quality summary that retains the input temporal contract."""

    series_id: str
    input_lineage_digest: str
    observation_count: int
    nonmissing_count: int
    missing_count: int
    missing_reasons: tuple[tuple[str, int], ...]
    limit: int
    selected_count: int
    truncated: bool
    mode: str
    requested_mode: str
    actual_mode: str
    cutoff: str | None
    date_only_policy: str
    availability_basis: str
    point_in_time_status: str
    point_in_time_scope: str
    unsafe_reasons: tuple[str, ...]
    return_definition: str
    return_direction: str
    return_method: str
    return_horizon: int
    horizon_basis: str
    return_target_status: str
    source_lineage_digest: str
    source_selection_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.unsafe_reasons, (tuple, list)):
            raise ValidationError("Stage 10 quality unsafe reasons are invalid")
        unsafe_reasons = tuple(self.unsafe_reasons)
        if any(
            not isinstance(reason, str) or not reason
            for reason in unsafe_reasons
        ):
            raise ValidationError("Stage 10 quality unsafe reasons are invalid")
        object.__setattr__(self, "unsafe_reasons", unsafe_reasons)
        hashes = (self.input_lineage_digest, self.source_lineage_digest, self.source_selection_sha256)
        if not self.series_id or any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in hashes
        ):
            raise ValidationError("Stage 10 quality identity is invalid")
        counts = (self.observation_count, self.nonmissing_count, self.missing_count)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValidationError("Stage 10 quality counts are invalid")
        if self.observation_count != self.nonmissing_count + self.missing_count:
            raise ValidationError("Stage 10 quality counts are inconsistent")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= 10_000
            or isinstance(self.selected_count, bool)
            or not isinstance(self.selected_count, int)
            or self.selected_count != self.observation_count
            or self.selected_count > self.limit
            or not isinstance(self.truncated, bool)
        ):
            raise ValidationError("Stage 10 quality result shape is invalid")
        if (
            tuple(sorted(self.missing_reasons)) != self.missing_reasons
            or len({reason for reason, _ in self.missing_reasons})
            != len(self.missing_reasons)
            or any(
                not isinstance(reason, str)
                or not reason
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 1
                for reason, count in self.missing_reasons
            )
            or sum(count for _, count in self.missing_reasons) != self.missing_count
        ):
            raise ValidationError("Stage 10 quality missingness is inconsistent")
        if (
            self.mode not in {"latest", "as_of"}
            or self.requested_mode != self.mode
            or self.actual_mode != self.mode
            or (self.mode == "as_of" and not isinstance(self.cutoff, str))
            or (self.mode == "as_of" and not self.cutoff)
            or (self.mode == "latest" and self.cutoff is not None)
        ):
            raise ValidationError("Stage 10 quality temporal mode is invalid")
        if self.date_only_policy not in {
            "completed_date",
            "calendar_date_inclusive",
        } or self.availability_basis != "local_capture" or (
            self.point_in_time_status
            != ("safe" if self.mode == "as_of" else "not_applicable")
        ) or self.point_in_time_scope != (
            "retained_local_captures"
            if self.mode == "as_of"
            else "current_stored_knowledge"
        ) or self.unsafe_reasons:
            raise ValidationError("Stage 10 quality temporal policy is invalid")
        if (
            self.return_definition != "close_to_close"
            or self.return_direction not in {"trailing", "forward"}
            or self.return_method not in {"simple", "log"}
            or isinstance(self.return_horizon, bool)
            or not isinstance(self.return_horizon, int)
            or not 1 <= self.return_horizon <= 252
            or self.horizon_basis != "observed_rows"
            or self.return_target_status
            != (
                "outcome_label"
                if self.return_direction == "forward"
                else "historical_transform"
            )
        ):
            raise ValidationError("Stage 10 quality return contract is invalid")

    def to_primitive(self) -> dict[str, Any]:
        return {
            "contract": "quant_data.stage10_market_quality_report",
            "contract_version": "1.0.0",
            "status": "established",
            "series_id": self.series_id,
            "input_lineage_digest": self.input_lineage_digest,
            "observation_count": self.observation_count,
            "nonmissing_count": self.nonmissing_count,
            "missing_count": self.missing_count,
            "missing_reasons": [
                {"reason": reason, "count": count}
                for reason, count in self.missing_reasons
            ],
            "result_shape": {
                "limit": self.limit,
                "selected_count": self.selected_count,
                "truncated": self.truncated,
            },
            "temporal_contract": {
                "mode": self.mode,
                "requested_mode": self.requested_mode,
                "actual_mode": self.actual_mode,
                "cutoff": self.cutoff,
                "date_only_policy": self.date_only_policy,
                "availability_basis": self.availability_basis,
                "point_in_time_status": self.point_in_time_status,
                "point_in_time_scope": self.point_in_time_scope,
                "unsafe_reasons": list(self.unsafe_reasons),
            },
            "return_contract": {
                "return_definition": self.return_definition,
                "return_direction": self.return_direction,
                "return_method": self.return_method,
                "return_horizon": self.return_horizon,
                "horizon_basis": self.horizon_basis,
                "return_target_status": self.return_target_status,
            },
            "source_contract": {
                "source_lineage_digest": self.source_lineage_digest,
                "source_selection_sha256": self.source_selection_sha256,
            },
        }


def _source_reference(item: Observation) -> dict[str, str]:
    return {
        "period_start": item.period_start,
        "period_end": item.period_end,
        "version_id": item.version_id,
        "evidence_id": item.evidence_id,
        "snapshot_id": item.snapshot_id,
        "run_id": item.run_id,
    }


def _latest_stage10_time(
    contributors: Sequence[Observation],
    *,
    value_field: str,
    precision_field: str,
) -> str:
    parsed: list[tuple[TemporalValue, str]] = []
    for item in contributors:
        raw = getattr(item, value_field)
        precision = getattr(item, precision_field)
        if not isinstance(raw, str) or precision != TemporalPrecision.DATETIME.value:
            raise ValidationError("Stage 10 return inputs require exact capture timing")
        value = TemporalValue.parse(raw, pointer=f"/return_source/{value_field}")
        if value.precision is not TemporalPrecision.DATETIME:
            raise ValidationError("Stage 10 return input precision is inconsistent")
        parsed.append((value, raw))
    return max(parsed, key=lambda item: item[0].value)[1]


def _contributors(
    source: TimeSeries,
    *,
    index: int,
    direction: str,
    horizon: int,
) -> tuple[Observation, ...]:
    anchor = source.observations[index]
    other_index = index - horizon if direction == "trailing" else index + horizon
    if not 0 <= other_index < len(source.observations):
        return (anchor,)
    return (anchor, source.observations[other_index])


def _derived_observation(
    source: TimeSeries,
    analytical: AnalyticsSeries,
    *,
    index: int,
    direction: str,
    method: str,
    horizon: int,
) -> Observation:
    item = analytical.observations[index]
    anchor = source.observations[index]
    if (item.period_start, item.period_end) != (
        anchor.period_start,
        anchor.period_end,
    ):
        raise ValidationError("Return primitive changed the Stage 10 observation anchor")
    contributors = _contributors(
        source,
        index=index,
        direction=direction,
        horizon=horizon,
    )
    dimensions = dict(contributors[0].dimensions)
    if any(dict(candidate.dimensions) != dimensions for candidate in contributors[1:]):
        raise ValidationError("Market returns cannot combine contradictory dimensions")
    contributor_material = [_source_reference(value) for value in contributors]
    contributor_sha256 = hashlib.sha256(
        dumps_strict(contributor_material).encode("utf-8")
    ).hexdigest()
    identity_material = {
        "source_observations": contributor_material,
        "period_start": item.period_start,
        "period_end": item.period_end,
        "value": item.value,
        "missing_reason": item.missing_reason,
        "primitive": RETURN_PRIMITIVE,
        "primitive_version": RETURN_PRIMITIVE_VERSION,
        "direction": direction,
        "method": method,
        "horizon": horizon,
    }
    identity = "derived:" + hashlib.sha256(
        dumps_strict(identity_material).encode("utf-8")
    ).hexdigest()
    flags = {
        flag
        for contributor in contributors
        for flag in contributor.quality_flags
        if isinstance(flag, str) and flag
    }
    flags.update(
        {
            f"source_contributors:{contributor_sha256}",
            f"transformation:return:{direction}:{method}:horizon={horizon}",
        }
    )
    if item.value is not None:
        flags.add(exact_decimal_quality_flag(item.value))
    return Observation(
        period_start=item.period_start,
        period_end=item.period_end,
        value=item.value,
        missing_reason=item.missing_reason,
        unit="fraction",
        value_representation="return",
        scale="1",
        vintage_at=identity,
        available_at=_latest_stage10_time(
            contributors,
            value_field="available_at",
            precision_field="available_precision",
        ),
        available_precision=TemporalPrecision.DATETIME.value,
        captured_at=_latest_stage10_time(
            contributors,
            value_field="captured_at",
            precision_field="captured_precision",
        ),
        captured_precision=TemporalPrecision.DATETIME.value,
        version_id=identity,
        evidence_id=identity,
        snapshot_id=identity,
        run_id=identity,
        dimensions=dimensions,
        quality_flags=tuple(sorted(flags)),
    )


def _contract_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Stage 10 return {field} is unavailable")
    return value


def audit_stage10_market_return(series: TimeSeries) -> Stage10MarketQualityReport:
    """Summarize one internal return series without relabeling its time contract."""

    if not isinstance(series, TimeSeries):
        raise ValidationError("Stage 10 return quality requires a typed series")
    series.validate_lineage()
    if series.metadata.get("value_representation") != "return":
        raise ValidationError("Stage 10 return quality requires return-valued input")
    derivation = series.to_primitive()["provenance"].get("derivation")
    if not isinstance(derivation, Mapping):
        raise ValidationError("Stage 10 return derivation provenance is unavailable")
    if (
        derivation.get("primitive") != RETURN_PRIMITIVE
        or derivation.get("primitive_version") != RETURN_PRIMITIVE_VERSION
        or series.audit.get("primitive") != RETURN_PRIMITIVE
        or series.audit.get("primitive_version") != RETURN_PRIMITIVE_VERSION
    ):
        raise ValidationError("Stage 10 return primitive contract is inconsistent")
    source_selection = derivation.get("source_selection")
    source_selection_sha256 = derivation.get("source_selection_sha256")
    if not isinstance(source_selection, Mapping) or not isinstance(
        source_selection_sha256, str
    ):
        raise ValidationError("Stage 10 return source selection is unavailable")
    if hashlib.sha256(dumps_strict(source_selection).encode("utf-8")).hexdigest() != (
        source_selection_sha256
    ):
        raise ValidationError("Stage 10 return source selection receipt is invalid")
    source_lineage_digest = _contract_text(
        source_selection.get("lineage_digest"), "source lineage"
    )
    if series.audit.get("source_lineage_digest") != source_lineage_digest:
        raise ValidationError("Stage 10 return source lineage is inconsistent")
    selected_observations = source_selection.get("observations")
    selected_count = source_selection.get("selected_count")
    if (
        not isinstance(selected_observations, list)
        or isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or selected_count != len(selected_observations)
        or selected_count > 10_000
        or series.audit.get("source_selected_count") != selected_count
    ):
        raise ValidationError("Stage 10 return source selection is inconsistent")

    summary = describe_series(series)
    reasons: dict[str, int] = {}
    for item in series.observations:
        if item.missing_reason is not None:
            reasons[item.missing_reason] = reasons.get(item.missing_reason, 0) + 1
    return_horizon = series.metadata.get("return_horizon")
    if isinstance(return_horizon, bool) or not isinstance(return_horizon, int):
        raise ValidationError("Stage 10 return horizon is unavailable")
    for name in (
        "return_definition",
        "return_direction",
        "return_method",
        "return_horizon",
        "horizon_basis",
    ):
        if series.audit.get(name) != series.metadata.get(name):
            raise ValidationError("Stage 10 return metadata and audit are inconsistent")
    unsafe_reasons = series.audit.get("unsafe_reasons", ())
    if isinstance(unsafe_reasons, (str, bytes)) or not isinstance(
        unsafe_reasons, (list, tuple)
    ) or any(not isinstance(item, str) or not item for item in unsafe_reasons):
        raise ValidationError("Stage 10 return point-in-time reasons are invalid")
    limit = series.audit.get("limit")
    audited_selected_count = series.audit.get("selected_count")
    audited_missing_count = series.audit.get("missing_count")
    audited_truncated = series.audit.get("truncated")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or isinstance(audited_selected_count, bool)
        or not isinstance(audited_selected_count, int)
        or isinstance(audited_missing_count, bool)
        or not isinstance(audited_missing_count, int)
        or not isinstance(audited_truncated, bool)
        or audited_selected_count != len(series.observations)
        or audited_missing_count != int(summary["missing_count"])
        or audited_truncated != series.truncated
    ):
        raise ValidationError("Stage 10 return result-shape audit is inconsistent")
    return Stage10MarketQualityReport(
        series_id=series.series_id,
        input_lineage_digest=series.lineage_digest,
        observation_count=int(summary["count"]),
        nonmissing_count=int(summary["nonmissing_count"]),
        missing_count=int(summary["missing_count"]),
        missing_reasons=tuple(sorted(reasons.items())),
        limit=limit,
        selected_count=audited_selected_count,
        truncated=audited_truncated,
        mode=_contract_text(series.audit.get("mode"), "mode"),
        requested_mode=_contract_text(
            series.audit.get("requested_mode"), "requested mode"
        ),
        actual_mode=_contract_text(
            series.audit.get("actual_mode"), "actual mode"
        ),
        cutoff=(
            None
            if series.audit.get("cutoff") is None
            else _contract_text(series.audit.get("cutoff"), "cutoff")
        ),
        date_only_policy=_contract_text(
            series.audit.get("date_only_policy"), "date-only policy"
        ),
        availability_basis=_contract_text(
            series.audit.get("availability_basis"), "availability basis"
        ),
        point_in_time_status=_contract_text(
            series.audit.get("point_in_time_status"), "point-in-time status"
        ),
        point_in_time_scope=_contract_text(
            series.audit.get("point_in_time_scope"), "point-in-time scope"
        ),
        unsafe_reasons=tuple(unsafe_reasons),
        return_definition=_contract_text(
            series.metadata.get("return_definition"), "definition"
        ),
        return_direction=_contract_text(
            series.metadata.get("return_direction"), "direction"
        ),
        return_method=_contract_text(series.metadata.get("return_method"), "method"),
        return_horizon=return_horizon,
        horizon_basis=_contract_text(
            series.metadata.get("horizon_basis"), "horizon basis"
        ),
        return_target_status=_contract_text(
            series.metadata.get("return_target_status"), "target status"
        ),
        source_lineage_digest=source_lineage_digest,
        source_selection_sha256=source_selection_sha256,
    )


class Stage10MarketReturnEngine:
    """Compose the immutable Stage 10 reader with the shared return primitive."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Market return engine dependencies are invalid")
        self._repository = Stage10DailyPriceRepository(store_map, registry)

    def calculate(self, request: Stage10MarketReturnRequest) -> TimeSeries:
        if not isinstance(request, Stage10MarketReturnRequest):
            raise ValidationError("Market return engine requires a typed request")
        output_limit = request.prices.limit
        source_limit = (
            output_limit + request.horizon
            if request.direction == "forward"
            else output_limit
        )
        source_query = (
            replace(request.prices, limit=source_limit)
            if source_limit != output_limit
            else request.prices
        )
        source = self._repository.get_close_series(source_query)
        analytical = return_series(
            source,
            direction=request.direction,
            method=request.method,
            horizon=request.horizon,
        )
        if len(analytical.observations) != len(source.observations):
            raise ValidationError("Return primitive changed the Stage 10 series length")
        derived = tuple(
            _derived_observation(
                source,
                analytical,
                index=index,
                direction=request.direction,
                method=request.method,
                horizon=request.horizon,
            )
            for index in range(len(analytical.observations))
        )
        payload = source.to_primitive()
        observations = derived[:output_limit]
        truncated = source.truncated or len(derived) > output_limit
        missing_count = sum(
            item.missing_reason is not None for item in observations
        )
        metadata = payload["metadata"]
        metadata.update(
            {
                "unit": "fraction",
                "value_representation": "return",
                "scale": "1",
                "return_definition": "close_to_close",
                "return_direction": request.direction,
                "return_method": request.method,
                "return_horizon": request.horizon,
                "horizon_basis": "observed_rows",
                "return_target_status": (
                    "outcome_label"
                    if request.direction == "forward"
                    else "historical_transform"
                ),
            }
        )
        audit = payload["audit"]
        audit.update(
            {
                "return_definition": "close_to_close",
                "return_direction": request.direction,
                "return_method": request.method,
                "return_horizon": request.horizon,
                "horizon_basis": "observed_rows",
                "gap_fill_policy": "none",
                "limit": output_limit,
                "selected_count": len(observations),
                "missing_count": missing_count,
                "truncated": truncated,
                "source_selection_limit": source_limit,
                "source_selected_count": len(source.observations),
                "source_lineage_digest": source.lineage_digest,
                "primitive": RETURN_PRIMITIVE,
                "primitive_version": RETURN_PRIMITIVE_VERSION,
            }
        )
        provenance = payload["provenance"]
        source_selection = {
            "series_id": source.series_id,
            "lineage_digest": source.lineage_digest,
            "selected_count": len(source.observations),
            "observations": [
                _source_reference(item) for item in source.observations
            ],
        }
        provenance["derivation"] = {
            "primitive": RETURN_PRIMITIVE,
            "primitive_version": RETURN_PRIMITIVE_VERSION,
            "source_selection": source_selection,
            "source_selection_sha256": hashlib.sha256(
                dumps_strict(source_selection).encode("utf-8")
            ).hexdigest(),
        }
        warnings = set(source.warnings)
        warnings.add(
            f"transformation:return:{request.direction}:{request.method}:horizon={request.horizon}"
        )
        warnings.add("observed_row_horizon_not_trading_day_horizon")
        if truncated:
            warnings.add("result_truncated")
        if request.direction == "forward":
            warnings.add("forward_return_is_outcome_label")
        return TimeSeries(
            series_id=analytical.series_id,
            metadata=metadata,
            observations=observations,
            warnings=tuple(sorted(warnings)),
            audit=audit,
            provenance=provenance,
            truncated=truncated,
        )


def _strict_object(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": dict(properties),
    }


def _text_schema(*, nullable: bool = False) -> dict[str, Any]:
    return {"type": ["string", "null"]} if nullable else {"type": "string"}


def stage10_market_return_series_schema() -> dict[str, Any]:
    """Return the closed public schema for the Stage 10 return ``TimeSeries``."""

    selected_identity = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider_symbol": _text_schema(),
            "asset_type": _text_schema(),
            "display_name": _text_schema(nullable=True),
            "exchange_code": _text_schema(nullable=True),
            "currency_segment": _text_schema(),
            "identity_seed_sha256": _text_schema(),
            "captured_at": _text_schema(),
            "captured_precision": _text_schema(),
            "run_id": _text_schema(),
        }
    )
    source_reference = _strict_object(
        {
            "period_start": _text_schema(),
            "period_end": _text_schema(),
            "version_id": _text_schema(),
            "evidence_id": _text_schema(),
            "snapshot_id": _text_schema(),
            "run_id": _text_schema(),
        }
    )
    dimensions = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider": _text_schema(),
            "price_variant": _text_schema(),
            "currency_segment": _text_schema(),
            "observation_field": _text_schema(),
        }
    )
    observation = _strict_object(
        {
            "period_start": _text_schema(),
            "period_end": _text_schema(),
            "value": {"type": ["number", "null"]},
            "missing_reason": {"type": ["string", "null"]},
            "unit": {"type": "string", "const": "fraction"},
            "value_representation": {"type": "string", "const": "return"},
            "scale": {"type": "string", "const": "1"},
            "vintage_at": _text_schema(),
            "available_at": _text_schema(nullable=True),
            "available_precision": _text_schema(),
            "captured_at": _text_schema(),
            "captured_precision": _text_schema(),
            "version_id": _text_schema(),
            "evidence_id": _text_schema(),
            "snapshot_id": _text_schema(),
            "run_id": _text_schema(),
            "dimensions": dimensions,
            "quality_flags": {
                "type": "array",
                "maxItems": 100,
                "items": _text_schema(),
            },
        }
    )
    metadata = _strict_object(
        {
            "instrument_id": _text_schema(),
            "provider_symbol": _text_schema(),
            "asset_type": _text_schema(),
            "display_name": _text_schema(nullable=True),
            "exchange_code": _text_schema(nullable=True),
            "provider": {"type": "string", "const": "fmp"},
            "frequency": {"type": "string", "const": "daily"},
            "unit": {"type": "string", "const": "fraction"},
            "value_representation": {"type": "string", "const": "return"},
            "scale": {"type": "string", "const": "1"},
            "price_variant": {"type": "string", "const": "fmp_full_eod_v1"},
            "currency_segment": {"type": "string", "const": "provider_native"},
            "observation_field": {"type": "string", "const": "close"},
            "availability_basis": {"type": "string", "const": "local_capture"},
            "horizon_basis": {"type": "string", "const": "observed_rows"},
            "session_calendar_status": {"type": "string", "const": "not_established"},
            "adjustment_status": {"type": "string", "const": "not_established"},
            "return_definition": {"type": "string", "const": "close_to_close"},
            "return_direction": {
                "type": "string",
                "enum": ["trailing", "forward"],
            },
            "return_method": {"type": "string", "enum": ["simple", "log"]},
            "return_horizon": {"type": "integer", "minimum": 1, "maximum": 252},
            "return_target_status": {
                "type": "string",
                "enum": ["historical_transform", "outcome_label"],
            },
        }
    )
    audit = _strict_object(
        {
            "mode": {"type": "string", "enum": ["latest", "as_of"]},
            "requested_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "actual_mode": {"type": "string", "enum": ["latest", "as_of"]},
            "cutoff": _text_schema(nullable=True),
            "cutoff_precision": _text_schema(nullable=True),
            "date_only_policy": {
                "type": "string",
                "enum": ["completed_date", "calendar_date_inclusive"],
            },
            "availability_basis": {"type": "string", "const": "local_capture"},
            "period_range_rule": {"type": "string", "const": "trade_date"},
            "requested_start_date": _text_schema(),
            "requested_end_date": _text_schema(),
            "provider": {"type": "string", "const": "fmp"},
            "price_variant": {"type": "string", "const": "fmp_full_eod_v1"},
            "currency_segment": {"type": "string", "const": "provider_native"},
            "observation_field": {"type": "string", "const": "close"},
            "horizon_basis": {"type": "string", "const": "observed_rows"},
            "session_calendar_status": {"type": "string", "const": "not_established"},
            "point_in_time_status": {
                "type": "string",
                "enum": ["safe", "not_applicable"],
            },
            "point_in_time_scope": {
                "type": "string",
                "enum": ["retained_local_captures", "current_stored_knowledge"],
            },
            "unsafe_reasons": {
                "type": "array",
                "maxItems": 100,
                "items": _text_schema(),
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            "selected_count": {"type": "integer", "minimum": 0, "maximum": 10000},
            "missing_count": {"type": "integer", "minimum": 0, "maximum": 10000},
            "truncated": {"type": "boolean"},
            "return_definition": {"type": "string", "const": "close_to_close"},
            "return_direction": {
                "type": "string",
                "enum": ["trailing", "forward"],
            },
            "return_method": {"type": "string", "enum": ["simple", "log"]},
            "return_horizon": {"type": "integer", "minimum": 1, "maximum": 252},
            "gap_fill_policy": {"type": "string", "const": "none"},
            "source_selection_limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            "source_selected_count": {"type": "integer", "minimum": 0, "maximum": 10000},
            "source_lineage_digest": _text_schema(),
            "primitive": {"type": "string", "const": RETURN_PRIMITIVE},
            "primitive_version": {"type": "string", "const": RETURN_PRIMITIVE_VERSION},
        }
    )
    provenance = _strict_object(
        {
            "dataset_id": {"type": "string", "const": "market.stage10.daily_prices"},
            "evidence_dataset_id": {"type": "string", "const": "market.stage10.source_evidence"},
            "identity_dataset_id": {"type": "string", "const": "market.stage10.instruments"},
            "store_role": {"type": "string", "const": "market"},
            "registry_revision": _text_schema(),
            "store_receipt": _strict_object(
                {
                    "migration_ids": {
                        "type": "array",
                        "maxItems": 100,
                        "items": _text_schema(),
                    },
                    "selected_instrument_identity": selected_identity,
                    "sha256": _text_schema(),
                }
            ),
            "derivation": _strict_object(
                {
                    "primitive": {"type": "string", "const": RETURN_PRIMITIVE},
                    "primitive_version": {"type": "string", "const": RETURN_PRIMITIVE_VERSION},
                    "source_selection": _strict_object(
                        {
                            "series_id": _text_schema(),
                            "lineage_digest": _text_schema(),
                            "selected_count": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 10000,
                            },
                            "observations": {
                                "type": "array",
                                "maxItems": 10000,
                                "items": source_reference,
                            },
                        }
                    ),
                    "source_selection_sha256": _text_schema(),
                }
            ),
        }
    )
    return _strict_object(
        {
            "contract": {"type": "string", "const": "quant_data.timeseries"},
            "contract_version": {"type": "string", "const": "1.0.0"},
            "series_id": _text_schema(),
            "metadata": metadata,
            "observations": {
                "type": "array",
                "maxItems": 10000,
                "items": observation,
            },
            "warnings": {
                "type": "array",
                "maxItems": 100,
                "items": _text_schema(),
            },
            "audit": audit,
            "provenance": provenance,
            "truncated": {"type": "boolean"},
            "lineage_digest": _text_schema(),
        }
    )


def invoke_stage10_market_return(
    name: str,
    arguments: Stage10MarketReturnArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Execute one explicitly selected v2 market-return operation."""

    if name not in {"market.get_returns", "market.get_forward_returns"}:
        raise LookupError("Stage 10 market-return operation is not registered")
    if not isinstance(arguments, Stage10MarketReturnArgumentsV2):
        raise ValidationError("Stage 10 market returns require typed v2 arguments")
    context.checkpoint()
    context.budget.require(
        rows=arguments.limit,
        series=1,
        operations=arguments.limit + arguments.horizon,
    )
    request = Stage10MarketReturnRequest(
        prices=Stage10DailyPriceQuery(
            identifier=arguments.identifier,
            identifier_kind=arguments.identifier_kind,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            mode=arguments.mode,
            as_of=arguments.as_of,
            date_only_policy=arguments.date_only_policy,
            limit=arguments.limit,
        ),
        direction=(
            "forward" if name == "market.get_forward_returns" else "trailing"
        ),
        method=arguments.method,
        horizon=arguments.horizon,
    )
    series = Stage10MarketReturnEngine(context.store_map, registry).calculate(request)
    quality = audit_stage10_market_return(series)
    context.checkpoint()
    warnings = tuple(
        WarningV1(
            code=code,
            message=f"Stage 10 return series warning: {code}.",
        )
        for code in series.warnings
    )
    return QueryResult(
        tool=name,
        status="ok",
        series=(series,),
        diagnostics=(
            DiagnosticV1(
                code="stage10_market_return_quality",
                message="The Stage 10 return series passed its typed quality audit.",
                metrics=fields_from_mapping(
                    {
                        "input_lineage_digest": quality.input_lineage_digest,
                        "missing_count": quality.missing_count,
                        "nonmissing_count": quality.nonmissing_count,
                        "observation_count": quality.observation_count,
                        "point_in_time_status": quality.point_in_time_status,
                        "return_direction": quality.return_direction,
                        "return_horizon": quality.return_horizon,
                        "return_method": quality.return_method,
                        "source_lineage_digest": quality.source_lineage_digest,
                        "source_selection_sha256": quality.source_selection_sha256,
                        "truncated": quality.truncated,
                    }
                ),
            ),
        ),
        warnings=warnings,
        lineage=(
            LineageRef(
                dataset_id="market.stage10.daily_prices",
                store_role="market",
                semantic_id=series.lineage_digest,
            ),
        ),
        truncation=TruncationV1(
            applied=series.truncated,
            limit=arguments.limit,
            returned_count=len(series.observations),
            total_known_count=None,
            has_more=series.truncated,
        ),
    )


__all__ = (
    "RETURN_PRIMITIVE",
    "RETURN_PRIMITIVE_VERSION",
    "Stage10MarketQualityReport",
    "Stage10MarketReturnEngine",
    "Stage10MarketReturnRequest",
    "audit_stage10_market_return",
    "invoke_stage10_market_return",
    "stage10_market_return_series_schema",
)
