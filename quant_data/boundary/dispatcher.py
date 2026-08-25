"""Fixed read-only adapters for the versioned public tool surface.

The dispatcher deliberately contains no SQL or store-path selection.  It
constructs typed, host-routed requests for the macro gateway and composes a
typed :class:`~quant_data.contracts.TimeSeries` in memory for description.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from threading import BoundedSemaphore, local
from time import perf_counter_ns
from typing import Any, Mapping

from quant_data.contracts import Observation, TimeSeries
from quant_data.errors import (
    ConcurrencyLimitError,
    InternalOutputError,
    Issue,
    QuantDataError,
    ResourceLimitError,
    ValidationError,
)
from quant_data.json_codec import dumps_strict
from quant_data.registry import (
    CURRENT_PUBLIC_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    Registry,
    STAGE1_TOOL_NAMES,
)
from quant_data.schema import validate_schema
from quant_data.stores import StoreMap
from quant_data.temporal import DateOnlyPolicy, TemporalValue, parse_date
from quant_data.tool_platform.arguments import (
    parse_arguments,
    preflight_dimensions,
    public_arguments,
)
from quant_data.tool_platform.catalog import (
    ADDITIVE_PUBLIC_TOOL_NAMES,
    VERSIONED_ECONOMETRICS_TOOLS,
    VERSIONED_MARKET_RETURN_TOOLS,
    VERSIONED_TIMESERIES_ANALYSIS_TOOLS,
    current_tool_profiles,
)

from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    HostClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.operations import invoke_operation


_TIME_SERIES_CONTRACT = "quant_data.timeseries"
_TIME_SERIES_VERSION = "1.0.0"
_DESCRIPTION_CONTRACT = "quant_data.timeseries_description"
_DESCRIPTION_VERSION = "1.0.0"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SEMANTIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


_STAGE5_INPUT_KINDS = {
    profile.name: profile.input_kind for profile in current_tool_profiles()
}
_VERSIONED_INPUT_KINDS = {
    **{
        (name, "2.0.0"): "stage10_market_return_v2"
        for name in VERSIONED_MARKET_RETURN_TOOLS
    },
    ("timeseries.describe", "2.0.0"): "stage10_market_describe_v2",
    ("timeseries.align", "2.0.0"): "stage10_market_align_v2",
    ("timeseries.correlation", "2.0.0"): "stage10_market_correlation_v2",
    ("econometrics.regression", "2.0.0"): "stage10_market_regression_v2",
    (
        "econometrics.rolling_regression",
        "2.0.0",
    ): "stage10_market_rolling_regression_v2",
    ("econometrics.stationarity", "2.0.0"): "stage10_market_stationarity_v2",
    ("econometrics.stationarity", "2.1.0"): "stage10_market_stationarity_v2_1",
    (
        "econometrics.structural_breaks",
        "2.0.0",
    ): "stage10_market_structural_breaks_v2",
    ("econometrics.regression", "2.1.0"): "stage10_market_regression_v2_1",
    ("econometrics.regression", "3.0.0"): "stage10_market_regression_model_suite_v3",
    (
        "econometrics.rolling_regression",
        "2.1.0",
    ): "stage10_market_rolling_regression_v2_1",
}

class UnknownToolError(QuantDataError):
    """A requested public name is not part of the frozen Stage 1 subset."""

    code = "unknown_tool"
    http_status = 404


class UnsupportedToolVersionError(QuantDataError):
    """A known logical tool does not expose the requested semantic version."""

    code = "unsupported_tool_version"
    http_status = 400


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    """Sanitized deterministic metadata for an in-process tool execution."""

    registry_revision: str
    tool_name: str
    tool_version: str
    execution: str = "read_only"
    details: Mapping[str, Any] | None = None

    def to_primitive(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "execution": self.execution,
            "registry_revision": self.registry_revision,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
        }
        if self.details is not None:
            result.update(copy.deepcopy(dict(self.details)))
        return result



class ToolDispatcher:
    """Dispatch the two fixed Stage 1 tools over host-controlled stores.

    ``call`` returns the registered tool result, not an HTTP envelope.  The
    HTTP layer owns request IDs and wraps it in the public success envelope.
    Passing a typed ``TimeSeries`` as ``{"series": value}`` to
    ``timeseries.describe`` preserves direct in-process composition; a public
    mapping is fully schema- and type-revalidated before it is accepted.
    """

    _max_concurrent_requests = 8
    _execution_slots = BoundedSemaphore(_max_concurrent_requests)

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        *,
        cancellation: CancellationToken | None = None,
    ) -> None:
        self._store_map = store_map
        self._registry = registry
        self._cancellation = (
            cancellation if cancellation is not None else CancellationToken()
        )
        self._receipt_state = local()
        self._names = tuple(tool["id"] for tool in registry.tools)
        if self._names not in (
            STAGE1_TOOL_NAMES,
            PUBLIC_TOOL_NAMES,
            CURRENT_PUBLIC_TOOL_NAMES,
        ):
            raise ValidationError("Registry does not expose a reviewed tool inventory")
        self._registry_sha256 = registry.source_sha256 or hashlib.sha256(
            registry.source_path.read_bytes()
        ).hexdigest()


    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def api_version(self) -> str:
        target = self._registry.raw.get("compatibility_target", {})
        version = target.get("api_version") if isinstance(target, Mapping) else None
        return version if isinstance(version, str) and version else "1.0"

    def _milestone(self) -> dict[str, str]:
        if self._names == STAGE1_TOOL_NAMES:
            return {
                "id": "stage1",
                "status": self._registry.raw["compatibility_target"][
                    "stage1_milestone_status"
                ],
            }
        if self._names == PUBLIC_TOOL_NAMES:
            return {"id": "stage5", "status": "fixture_validated"}
        return {"id": "tool_platform", "status": "active_read_only"}


    def manifest(self) -> dict[str, Any]:
        """Return only public, registry-owned discovery fields.

        Handler implementation identifiers are intentionally omitted: public
        clients need schemas, examples, bounds, and versions, not internal
        dispatch wiring.
        """

        public_tools: list[dict[str, Any]] = []
        for declaration in self._registry.tools:
            public = self._public_declaration(declaration)
            policy = self._registry.version_policy(str(declaration["id"]))
            if policy is not None:
                deprecations = {
                    item["version"]: item for item in policy["deprecations"]
                }
                versions: list[dict[str, Any]] = []
                for variant in self._registry.versions_for(str(declaration["id"])):
                    version_public = self._public_declaration(variant)
                    deprecation = deprecations.get(variant["version"])
                    if deprecation is not None:
                        version_public["deprecation"] = copy.deepcopy(deprecation)
                        version_public["lifecycle"] = "deprecated"
                    versions.append(version_public)
                public["version_selector"] = {
                    "field": policy["selector_field"],
                    "default_version": policy["default_version"],
                    "explicit_selection_required_for": [
                        item["version"]
                        for item in policy["variants"]
                    ],
                }
                public["versions"] = versions
                public["deprecation"] = copy.deepcopy(
                    deprecations[declaration["version"]]
                )
                public["lifecycle"] = "deprecated"
            public_tools.append(public)

        result = {
            "api_version": self.api_version,
            "execution": "read_only",
            "milestone": self._milestone(),
            "registry_revision": self._registry.revision,
            "tools": public_tools,
        }
        # A manifest is also a public boundary object; do not hand callers a
        # value the strict renderer cannot safely return.
        dumps_strict(result)
        return result

    @staticmethod
    def _public_declaration(declaration: Mapping[str, Any]) -> dict[str, Any]:
        public = {
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
        for field_name in (
            "family",
            "api_version",
            "operation_version",
            "lifecycle",
            "compatibility",
            "description",
            "assumptions",
            "operation_graph_id",
            "stores",
            "input_type",
            "input_schema_id",
            "output_type",
            "output_schema_id",
            "cost_model",
            "timeout_class",
            "live_capability",
            "contracts",
            "composable",
            "observability",
            "owner",
            "review_requirements",
        ):
            if field_name in declaration:
                public[field_name] = copy.deepcopy(declaration[field_name])
        return public

    def receipt_for(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        *,
        error_code: str | None = None,
        tool_version: str | None = None,
    ) -> ToolReceipt:
        try:
            declaration = self._tool_declaration(name, tool_version)
        except (UnknownToolError, UnsupportedToolVersionError):
            if (
                self._names not in (PUBLIC_TOOL_NAMES, CURRENT_PUBLIC_TOOL_NAMES)
                or error_code is None
                or not isinstance(name, str)
                or not name
            ):
                raise
            declaration = None
        timing = getattr(self._receipt_state, "timing", None)
        if timing is not None:
            del self._receipt_state.timing
        if not isinstance(timing, Mapping):
            timing = {
                "started_at": "1970-01-01T00:00:00Z", "finished_at": "1970-01-01T00:00:00Z", "elapsed_seconds": Decimal("0")
            }
        details: dict[str, Any] | None = None
        if self._names in (PUBLIC_TOOL_NAMES, CURRENT_PUBLIC_TOOL_NAMES):
            raw_arguments = dict(arguments or {})
            if error_code is None:
                public_arguments = self._public_arguments(raw_arguments)
                request_material = {
                    "api_version": self.api_version,
                    "tool": name,
                    "arguments": public_arguments,
                }
                if tool_version is not None:
                    request_material["tool_version"] = tool_version
            else:
                series_value = raw_arguments.get("series")
                public_arguments = {
                    "limit": raw_arguments.get("limit")
                    if isinstance(raw_arguments.get("limit"), int)
                    and not isinstance(raw_arguments.get("limit"), bool)
                    else None,
                    "series_count": len(series_value)
                    if isinstance(series_value, (list, tuple))
                    else int(series_value is not None),
                    "argument_names": sorted(
                        name for name in raw_arguments if isinstance(name, str)
                    )[:100],
                }
                request_material = {
                    "api_version": self.api_version,
                    "tool": name,
                    "error_code": error_code,
                    "shape": public_arguments,
                }
                if tool_version is not None:
                    request_material["tool_version"] = tool_version
            if declaration is None:
                details = {
                    "request_id": hashlib.sha256(
                        dumps_strict(request_material).encode("utf-8")
                    ).hexdigest(),
                    "api_version": self.api_version,
                    "registry_schema_sha256": self._registry_sha256,
                    "input_schema_id": None,
                    "output_schema_id": None,
                    "input_schema_sha256": None,
                    "output_schema_sha256": None,
                    "operation_graph_id": None,
                    "operation_version": None,
                    "started_at": timing["started_at"],
                    "finished_at": timing["finished_at"],
                    "elapsed_seconds": timing["elapsed_seconds"],
                    "outcome": error_code,
                    "logical_stores": [],
                    "logical_store_aliases": [],
                    "workload_dimensions": public_arguments,
                    "applied_limits": {},
                    "returned_shape": {
                        "record_count": 0,
                        "series_count": 0,
                    },
                    "truncation": None,
                    "warning_codes": [],
                    "error_codes": [error_code],
                    "analysis_id": None,
                    "lineage_count": 0,
                }
                return ToolReceipt(
                    registry_revision=self._registry.revision,
                    tool_name=name,
                    tool_version=tool_version or "unknown",
                    details=details,
                )
            public_result = dict(result or {})
            warnings = public_result.get("warnings", [])
            warning_codes = sorted(
                {
                    item.get("code")
                    for item in warnings
                    if isinstance(item, Mapping) and isinstance(item.get("code"), str)
                }
            )
            deprecation_warnings: list[dict[str, Any]] = []
            policy = self._registry.version_policy(name)
            if policy is not None:
                deprecation_warnings = [
                    copy.deepcopy(item)
                    for item in policy["deprecations"]
                    if item["version"] == declaration["version"]
                ]
                warning_codes = sorted(
                    {
                        *warning_codes,
                        *(item["code"] for item in deprecation_warnings),
                    }
                )
            records = public_result.get("records", [])
            series = public_result.get("series", [])
            research_contract = public_result.get("research_contract", {})
            contract = (
                research_contract.get("contract", {})
                if isinstance(research_contract, Mapping)
                else {}
            )
            analysis_id = (
                contract.get("analysis_id")
                if isinstance(contract, Mapping)
                and isinstance(contract.get("analysis_id"), str)
                else None
            )
            details = {
                "request_id": hashlib.sha256(
                    dumps_strict(request_material).encode("utf-8")
                ).hexdigest(),
                "api_version": self.api_version,
                "registry_schema_sha256": self._registry_sha256,
                "input_schema_id": declaration["input_schema_id"],
                "output_schema_id": declaration["output_schema_id"],
                "input_schema_sha256": hashlib.sha256(
                    dumps_strict(declaration["input_schema"]).encode("utf-8")
                ).hexdigest(),
                "output_schema_sha256": hashlib.sha256(
                    dumps_strict(declaration["output_schema"]).encode("utf-8")
                ).hexdigest(),
                "operation_graph_id": declaration["operation_graph_id"],
                "operation_version": declaration["operation_version"],
                "started_at": timing["started_at"],
                "finished_at": timing["finished_at"],
                "elapsed_seconds": timing["elapsed_seconds"],
                "outcome": error_code or "succeeded",
                "logical_stores": list(declaration["stores"]),
                "logical_store_aliases": [
                    f"{role}:host_selected" for role in declaration["stores"]
                ],
                "workload_dimensions": {
                    "requested_limit": public_arguments.get("limit"),
                    "series_count": public_arguments.get("series_count", len(public_arguments.get("series", [])) if isinstance(public_arguments.get("series"), list) else int("series" in public_arguments)),
                },
                "applied_limits": copy.deepcopy(declaration["workload_bounds"]),
                "returned_shape": {
                    "record_count": len(records) if isinstance(records, list) else 0,
                    "series_count": len(series) if isinstance(series, list) else 0,
                },
                "truncation": copy.deepcopy(public_result.get("truncation")),
                "warning_codes": warning_codes,
                "error_codes": [error_code] if error_code is not None else [],
                "analysis_id": analysis_id,
                "lineage_count": len(public_result.get("lineage", []))
                if isinstance(public_result.get("lineage"), list)
                else 0,
            }
            if policy is not None:
                details["deprecation_warnings"] = deprecation_warnings
        return ToolReceipt(
            registry_revision=self._registry.revision,
            tool_name=name,
            tool_version=str(declaration["version"]),
            details=details,
        )


    def call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        tool_version: str | None = None,
    ) -> dict[str, Any]:
        """Execute within the process-wide bounded read-only request pool."""

        started_ns = perf_counter_ns()
        started_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        acquired = self._execution_slots.acquire(blocking=False)
        try:
            if not acquired:
                raise ConcurrencyLimitError(
                    "The host read-only execution pool is at capacity"
                )
            return self._call_one(name, arguments, tool_version=tool_version)
        finally:
            if acquired:
                self._execution_slots.release()
            finished_ns = perf_counter_ns()
            self._receipt_state.timing = {
                "started_at": started_at,
                "finished_at": (
                    datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                ),
                "elapsed_seconds": Decimal(finished_ns - started_ns)
                / Decimal(1_000_000_000),
            }

    def _call_one(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        tool_version: str | None,
    ) -> dict[str, Any]:
        """Validate and execute exactly one registered read-only operation."""

        declaration = self._tool_declaration(name, tool_version)
        if not isinstance(arguments, Mapping):
            raise ValidationError(
                "Tool arguments must be an object",
                issues=(Issue("/arguments", "type", "Expected an object"),),
            )

        if (
            name == "timeseries.describe"
            and declaration["version"] == "1.0.0"
            and isinstance(arguments.get("series"), TimeSeries)
        ):
            if set(arguments) != {"series"}:
                raise ValidationError(
                    "Tool arguments contain an unknown field",
                    issues=(
                        Issue(
                            "/arguments",
                            "additional_properties",
                            "Only series is allowed",
                        ),
                    ),
                )
            arguments["series"].validate_lineage()
            result = self._describe(arguments["series"])
            self._validate_output(result, declaration)
            return result

        public_material = public_arguments(dict(arguments))
        validate_schema(public_material, declaration["input_schema"])
        if name in STAGE1_TOOL_NAMES and declaration["version"] == "1.0.0":
            typed_material = self._typed_arguments(dict(arguments))
        else:
            input_kind = _VERSIONED_INPUT_KINDS.get(
                (name, declaration["version"]), _STAGE5_INPUT_KINDS[name]
            )
            dimensions = preflight_dimensions(
                public_material, input_kind=input_kind
            )
            bounds = declaration["workload_bounds"]
            if (
                dimensions["rows"] > bounds["max_rows"]
                or dimensions["series"] > bounds["max_series"]
                or dimensions["operations"] > bounds["max_operations"]
            ):
                raise ResourceLimitError(
                    "Tool workload exceeds its registered preflight limits"
                )
            decode_series = self._timeseries_from_public
            if (
                (
                    declaration["version"] == "2.0.0"
                    and name in (
                        *VERSIONED_TIMESERIES_ANALYSIS_TOOLS,
                        *VERSIONED_ECONOMETRICS_TOOLS,
                    )
                )
                or declaration["version"] == "2.1.0"
                or (
                    declaration["version"] == "3.0.0"
                    and name == "econometrics.regression"
                )
            ):
                from quant_data.tool_platform.market_statistics import (
                    stage10_market_statistic_series_schema,
                )

                stage10_schema = stage10_market_statistic_series_schema()
                decode_series = lambda value: self._timeseries_from_public(
                    value, schema=stage10_schema
                )
            typed_material = parse_arguments(
                input_kind, public_material, decode_series
            )
        if name == "macro.get_series":
            result = self._macro_get_series(public_material)
        elif name == "timeseries.describe" and declaration["version"] == "1.0.0":
            result = self._describe(typed_material["series"])
        else:
            context = self._stage5_context(name, declaration)
            result = invoke_operation(
                name,
                typed_material,
                context,
                self._registry,
            ).to_primitive()

        self._validate_output(result, declaration)
        return result

    def describe(self, series: TimeSeries) -> dict[str, Any]:
        """Typed convenience entry point for direct in-process composition."""

        if not isinstance(series, TimeSeries):
            raise ValidationError("timeseries.describe requires a TimeSeries value")
        series.validate_lineage()
        return self.call("timeseries.describe", {"series": series})

    def _tool_declaration(
        self, name: str, tool_version: str | None = None
    ) -> Mapping[str, Any]:
        if not isinstance(name, str) or name not in self._names:
            raise UnknownToolError("Unknown tool")
        if tool_version is not None and (
            not isinstance(tool_version, str)
            or not _SEMANTIC_VERSION.fullmatch(tool_version)
        ):
            raise ValidationError(
                "Tool version must be a semantic version",
                issues=(Issue("/tool_version", "type", "Expected a semantic version"),),
            )
        for declaration in self._registry.versions_for(name):
            if tool_version is None or declaration["version"] == tool_version:
                return declaration
        raise UnsupportedToolVersionError("Unsupported tool version")

    def _stage5_context(
        self,
        name: str,
        declaration: Mapping[str, Any],
    ) -> ToolExecutionContext:
        bounds = declaration["workload_bounds"]
        clock = HostClock()
        deadline = Deadline(clock.monotonic() + Decimal("5"))
        return ToolExecutionContext(
            store_map=self._store_map,
            registry_revision=self._registry.revision,
            registry_sha256=self._registry_sha256,
            request_id="in_process",
            api_version=self.api_version,
            tool_name=name,
            tool_version=str(declaration["version"]),
            operation_graph_id=str(declaration["operation_graph_id"]),
            operation_version=str(declaration["operation_version"]),
            budget=ExecutionBudget(
                max_rows=int(bounds["max_rows"]),
                max_series=int(bounds["max_series"]),
                max_operations=int(bounds["max_operations"]),
                max_output_bytes=int(bounds["max_response_bytes"]),
            ),
            capabilities=CapabilitySet(),
            deadline=deadline,
            cancellation=self._cancellation,
            clock=clock,
            execution=(
                "read_only"
                if (
                    name in ADDITIVE_PUBLIC_TOOL_NAMES
                    or declaration["version"] in {"2.0.0", "2.1.0", "3.0.0"}
                )
                else "offline_fixture"
            ),
        )

    def _typed_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if "series" not in arguments:
            return arguments
        value = arguments["series"]
        if isinstance(value, (list, tuple)):
            arguments["series"] = tuple(
                item if isinstance(item, TimeSeries) else self._timeseries_from_public(item)
                for item in value
            )
            for item in arguments["series"]:
                item.validate_lineage()
        else:
            arguments["series"] = (
                value if isinstance(value, TimeSeries) else self._timeseries_from_public(value)
            )
            arguments["series"].validate_lineage()
        return arguments

    @staticmethod
    def _public_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
        return public_arguments(arguments)


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

    def _timeseries_from_public(
        self,
        value: Any,
        *,
        schema: Mapping[str, Any] | None = None,
    ) -> TimeSeries:
        """Decode a public envelope into an immutable, fully validated value."""

        if isinstance(value, TimeSeries):
            return value
        selected_schema = (
            self._registry.tool("macro.get_series")["output_schema"]
            if schema is None
            else schema
        )
        validate_schema(value, selected_schema)
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
