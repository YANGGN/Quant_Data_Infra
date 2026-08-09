"""Fixed read-only adapters for the Stage 1 public tool subset.

The dispatcher deliberately contains no SQL or store-path selection.  It
constructs typed, host-routed requests for the macro gateway and composes a
typed :class:`~quant_data.contracts.TimeSeries` in memory for description.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import Issue, QuantDataError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.registry import Registry, STAGE1_TOOL_NAMES
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap
from quant_data.temporal import DateOnlyPolicy, TemporalValue, parse_date


_TIME_SERIES_CONTRACT = "quant_data.timeseries"
_TIME_SERIES_VERSION = "1.0.0"
_DESCRIPTION_CONTRACT = "quant_data.timeseries_description"
_DESCRIPTION_VERSION = "1.0.0"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class UnknownToolError(QuantDataError):
    """A requested public name is not part of the frozen Stage 1 subset."""

    code = "unknown_tool"
    http_status = 404


class InternalOutputError(QuantDataError):
    """A trusted operation returned data outside its registered contract."""

    code = "internal_output_validation"
    http_status = 500


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    """Sanitized deterministic metadata for an in-process tool execution."""

    registry_revision: str
    tool_name: str
    tool_version: str
    execution: str = "read_only"

    def to_primitive(self) -> dict[str, str]:
        return {
            "execution": self.execution,
            "registry_revision": self.registry_revision,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
        }


class ToolDispatcher:
    """Dispatch the two fixed Stage 1 tools over host-controlled stores.

    ``call`` returns the registered tool result, not an HTTP envelope.  The
    HTTP layer owns request IDs and wraps it in the public success envelope.
    Passing a typed ``TimeSeries`` as ``{"series": value}`` to
    ``timeseries.describe`` preserves direct in-process composition; a public
    mapping is fully schema- and type-revalidated before it is accepted.
    """

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        names = tuple(tool["id"] for tool in registry.tools)
        if names != STAGE1_TOOL_NAMES:
            raise ValidationError("Registry does not expose the Stage 1 tool subset")

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def api_version(self) -> str:
        target = self._registry.raw.get("compatibility_target", {})
        version = target.get("api_version") if isinstance(target, Mapping) else None
        return version if isinstance(version, str) and version else "1.0"

    def manifest(self) -> dict[str, Any]:
        """Return only public, registry-owned discovery fields.

        Handler implementation identifiers are intentionally omitted: public
        clients need schemas, examples, bounds, and versions, not internal
        dispatch wiring.
        """

        public_tools: list[dict[str, Any]] = []
        for declaration in self._registry.tools:
            public_tools.append(
                {
                    "name": declaration["id"],
                    "version": declaration["version"],
                    "read_only": True,
                    "datasets": list(declaration["datasets"]),
                    "input_schema": copy.deepcopy(declaration["input_schema"]),
                    "output_schema": copy.deepcopy(declaration["output_schema"]),
                    "examples": copy.deepcopy(declaration["examples"]),
                    "workload_bounds": copy.deepcopy(declaration["workload_bounds"]),
                    "availability_policy": copy.deepcopy(declaration["availability_policy"]),
                }
            )
        result = {
            "api_version": self.api_version,
            "execution": "read_only",
            "milestone": {
                "id": "stage1",
                "status": self._registry.raw["compatibility_target"][
                    "stage1_milestone_status"
                ],
            },
            "registry_revision": self._registry.revision,
            "tools": public_tools,
        }
        # A manifest is also a public boundary object; do not hand callers a
        # value the strict renderer cannot safely return.
        dumps_strict(result)
        return result

    def receipt_for(self, name: str) -> ToolReceipt:
        declaration = self._tool_declaration(name)
        return ToolReceipt(
            registry_revision=self._registry.revision,
            tool_name=name,
            tool_version=str(declaration["version"]),
        )

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and execute exactly one registered read-only operation."""

        declaration = self._tool_declaration(name)
        if not isinstance(arguments, Mapping):
            raise ValidationError(
                "Tool arguments must be an object",
                issues=(Issue("/arguments", "type", "Expected an object"),),
            )

        if name == "timeseries.describe" and isinstance(arguments.get("series"), TimeSeries):
            if set(arguments) != {"series"}:
                raise ValidationError(
                    "Tool arguments contain an unknown field",
                    issues=(Issue("/arguments", "additional_properties", "Only series is allowed"),),
                )
            arguments["series"].validate_lineage()
            result = self._describe(arguments["series"])
        else:
            material = dict(arguments)
            validate_schema(material, declaration["input_schema"])
            if name == "macro.get_series":
                result = self._macro_get_series(material)
            elif name == "timeseries.describe":
                series = self._timeseries_from_public(material["series"])
                result = self._describe(series)
            else:  # Defensive: registry validation is expected to make this unreachable.
                raise UnknownToolError("Unknown tool")

        self._validate_output(result, declaration)
        return result

    def describe(self, series: TimeSeries) -> dict[str, Any]:
        """Typed convenience entry point for direct in-process composition."""

        if not isinstance(series, TimeSeries):
            raise ValidationError("timeseries.describe requires a TimeSeries value")
        series.validate_lineage()
        return self.call("timeseries.describe", {"series": series})

    def _tool_declaration(self, name: str) -> Mapping[str, Any]:
        if not isinstance(name, str) or name not in STAGE1_TOOL_NAMES:
            raise UnknownToolError("Unknown tool")
        return self._registry.tool(name)

    def _macro_get_series(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_macro_arguments(arguments)
        try:
            # The domain lane owns the gateway and its query type.  A lazy
            # import keeps importing the public boundary side-effect free and
            # avoids coupling package import order to domain installation.
            from quant_data.macro import MacroSeriesQuery, MacroSeriesRepository
        except ImportError as exc:  # pragma: no cover - protects incomplete hosts
            raise QuantDataError(
                "Macro read capability is unavailable", code="store_unavailable"
            ) from exc

        query = MacroSeriesQuery(
            series_id=arguments["series_id"],
            start_date=arguments["start_date"],
            end_date=arguments["end_date"],
            vintage_mode=arguments["vintage_mode"],
            as_of=arguments.get("as_of"),
            date_only_policy=arguments["date_only_policy"],
            limit=arguments["limit"],
        )
        series = MacroSeriesRepository(self._store_map, self._registry).get_series(query)
        if not isinstance(series, TimeSeries):
            raise InternalOutputError("Registered macro operation produced an invalid result")
        return series.to_primitive()

    @staticmethod
    def _validate_macro_arguments(arguments: Mapping[str, Any]) -> None:
        mode = arguments["vintage_mode"]
        as_of = arguments.get("as_of")
        if mode == "as_of":
            if not isinstance(as_of, str) or not as_of:
                raise ValidationError(
                    "as_of is required when vintage_mode is as_of",
                    issues=(Issue("/as_of", "required_for_as_of", "Provide an ISO date or aware datetime"),),
                )
            TemporalValue.parse(as_of, pointer="/as_of")
        elif as_of is not None:
            raise ValidationError(
                "as_of is only allowed when vintage_mode is as_of",
                issues=(Issue("/as_of", "ignored", "Remove as_of for this vintage mode"),),
            )

        start = parse_date(arguments["start_date"], pointer="/start_date")
        end = parse_date(arguments["end_date"], pointer="/end_date")
        if end < start:
            raise ValidationError(
                "end_date cannot precede start_date",
                issues=(Issue("/end_date", "range", "End date must be on or after start date"),),
            )
        try:
            DateOnlyPolicy(arguments["date_only_policy"])
        except ValueError as exc:  # schema normally catches this; retain typed defense.
            raise ValidationError("Unsupported date-only policy") from exc

    def _describe(self, series: TimeSeries) -> dict[str, Any]:
        if len(series.observations) > 10_000:
            raise ValidationError(
                "TimeSeries exceeds the Stage 1 describe limit",
                issues=(Issue("/series/observations", "max_items", "At most 10000 observations are supported"),),
            )
        values = [observation.value for observation in series.observations if observation.value is not None]
        decimals = [value for value in values if isinstance(value, Decimal)]
        # Observation rejects non-finite values, but keep the typed boundary
        # defensive in case a hostile subclass is passed by an in-process host.
        if any(not value.is_finite() for value in decimals):
            raise ValidationError("TimeSeries contains a non-finite value")

        warnings = list(series.warnings)
        if not series.observations:
            warnings.append("empty_series")
        elif not decimals:
            warnings.append("all_observations_missing")
        warnings = _stable_unique_strings(warnings)

        first_period = series.observations[0].period_start if series.observations else None
        last_period = series.observations[-1].period_end if series.observations else None
        missing_count = len(series.observations) - len(decimals)
        if decimals:
            total = sum(decimals, Decimal("0"))
            minimum: Decimal | None = min(decimals)
            maximum: Decimal | None = max(decimals)
            mean: Decimal | None = total / Decimal(len(decimals))
        else:
            minimum = None
            maximum = None
            mean = None

        return {
            "contract": _DESCRIPTION_CONTRACT,
            "contract_version": _DESCRIPTION_VERSION,
            "count": len(series.observations),
            "nonmissing_count": len(decimals),
            "missing_count": missing_count,
            "minimum": minimum,
            "maximum": maximum,
            "mean": mean,
            "first_period": first_period,
            "last_period": last_period,
            "warnings": warnings,
            "audit": {
                "input_lineage_digest": series.lineage_digest,
                "input_cutoff": series.audit.get("cutoff"),
                "input_missing_count": missing_count,
            },
        }

    def _timeseries_from_public(self, value: Any) -> TimeSeries:
        """Decode a public envelope into an immutable, fully validated value."""

        if isinstance(value, TimeSeries):
            return value
        macro_schema = self._registry.tool("macro.get_series")["output_schema"]
        validate_schema(value, macro_schema)
        if not isinstance(value, Mapping):  # The schema check gives the caller a pointer.
            raise ValidationError("TimeSeries must be an object")
        if value.get("contract") != _TIME_SERIES_CONTRACT or value.get("contract_version") != _TIME_SERIES_VERSION:
            raise ValidationError(
                "Unsupported TimeSeries contract",
                issues=(Issue("/series/contract", "const", "Expected the Stage 1 TimeSeries contract"),),
            )
        digest = value.get("lineage_digest")
        if not isinstance(digest, str) or not _HEX_DIGEST.fullmatch(digest):
            raise ValidationError(
                "TimeSeries lineage digest is invalid",
                issues=(Issue("/series/lineage_digest", "format", "Expected a lowercase SHA-256 digest"),),
            )
        observations = tuple(
            self._observation_from_public(item, index)
            for index, item in enumerate(value["observations"])
        )
        series = TimeSeries(
            series_id=value["series_id"],
            metadata=dict(value["metadata"]),
            observations=observations,
            warnings=tuple(value["warnings"]),
            audit=dict(value["audit"]),
            provenance=dict(value["provenance"]),
            truncated=value["truncated"],
            lineage_digest="",
        )
        if digest != series.lineage_digest:
            raise ValidationError(
                "TimeSeries lineage digest does not match its contents",
                issues=(Issue("/series/lineage_digest", "integrity", "Lineage digest mismatch"),),
            )
        return series

    @staticmethod
    def _observation_from_public(value: Any, index: int) -> Observation:
        if not isinstance(value, Mapping):
            raise ValidationError("TimeSeries observation must be an object")
        pointer = f"/series/observations/{index}"
        # Registry-owned schema has already enforced exact public keys and
        # scalar types.  These checks establish the typed domain invariants.
        start = parse_date(value["period_start"], pointer=f"{pointer}/period_start")
        end = parse_date(value["period_end"], pointer=f"{pointer}/period_end")
        if end < start:
            raise ValidationError(
                "Observation period is inverted",
                issues=(Issue(f"{pointer}/period_end", "range", "Period end must not precede period start"),),
            )
        raw_number = value["value"]
        decimal_value = _as_decimal(raw_number, f"{pointer}/value") if raw_number is not None else None
        dimensions = value["dimensions"]
        if not isinstance(dimensions, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in dimensions.items()
        ):
            raise ValidationError("TimeSeries dimensions must be a string map")
        quality_flags = value["quality_flags"]
        if not isinstance(quality_flags, (list, tuple)) or not all(
            isinstance(flag, str) for flag in quality_flags
        ):
            raise ValidationError("TimeSeries quality flags must be strings")
        return Observation(
            period_start=value["period_start"],
            period_end=value["period_end"],
            value=decimal_value,
            missing_reason=value["missing_reason"],
            unit=value["unit"],
            value_representation=value["value_representation"],
            scale=value["scale"],
            vintage_at=value["vintage_at"],
            available_at=value["available_at"],
            available_precision=value["available_precision"],
            captured_at=value["captured_at"],
            captured_precision=value["captured_precision"],
            version_id=value["version_id"],
            evidence_id=value["evidence_id"],
            snapshot_id=value["snapshot_id"],
            run_id=value["run_id"],
            dimensions=dict(dimensions),
            quality_flags=tuple(quality_flags),
        )

    @staticmethod
    def _validate_output(result: Mapping[str, Any], declaration: Mapping[str, Any]) -> None:
        try:
            validate_schema(dict(result), declaration["output_schema"], code="invalid_output")
            bounds = declaration["workload_bounds"]
            max_bytes = bounds.get("max_response_bytes", 8 * 1024 * 1024)
            if not isinstance(max_bytes, int) or max_bytes < 1:
                raise ValidationError("Registered response bound is invalid", code="invalid_output")
            dumps_strict(dict(result), max_bytes=max_bytes)
        except QuantDataError as exc:
            if exc.code == "resource_limit":
                raise
            raise InternalOutputError("Registered operation returned an invalid public result") from exc


def _as_decimal(value: Any, pointer: str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(
            "A numeric observation value is required",
            issues=(Issue(pointer, "type", "Expected a finite JSON number"),),
        )
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, float):
        # Strict public JSON is decoded directly to Decimal.  This branch is
        # solely for programmatic hosts and avoids hidden binary conversion.
        decimal_value = Decimal(str(value))
    else:
        raise ValidationError(
            "A numeric observation value is required",
            issues=(Issue(pointer, "type", "Expected a finite JSON number"),),
        )
    try:
        finite = decimal_value.is_finite()
    except InvalidOperation as exc:  # pragma: no cover - Decimal guards this
        raise ValidationError("Observation value is invalid") from exc
    if not finite:
        raise ValidationError(
            "Observation value must be finite",
            issues=(Issue(pointer, "finite", "NaN and infinity are not allowed"),),
        )
    return decimal_value


def _stable_unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value not in result:
            result.append(value)
    return result
