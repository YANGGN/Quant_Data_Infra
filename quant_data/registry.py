"""Strict loader for the canonical Stage 1 system registry."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import Issue, RegistryError, ValidationError
from .json_codec import MAX_JSON_BYTES, loads_strict
from .schema import validate_schema
from .stores import STORE_ROLES


CANONICAL_REGISTRY_PATH = Path("config/system_registry.json")
REGISTRY_ENV = "QUANT_SYSTEM_REGISTRY_PATH"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HANDLER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PROHIBITED_TOOL_KEYS = {
    "database_path",
    "db_path",
    "sqlite_uri",
    "sql",
    "pragma",
    "filesystem_root",
    "connection_string",
}


PUBLIC_TOOL_NAMES = (
    "macro.search_series",
    "macro.describe_series",
    "macro.get_series",
    "macro.get_intraday_releases",
    "macro.release_surprises",
    "macro.revision_analysis",
    "macro.align_us_recessions",
    "macro.standardize_surprises",
    "macro.get_liquidity_snapshot",
    "macro.get_liquidity_impulse",
    "macro.get_credit_conditions",
    "macro.regime_snapshot",
    "timeseries.transform",
    "timeseries.describe",
    "timeseries.align",
    "timeseries.correlation",
    "econometrics.regression",
    "econometrics.stationarity",
    "econometrics.rolling_regression",
    "econometrics.structural_breaks",
    "econometrics.local_projection",
    "company.search_issuers",
    "company.search_filings",
    "company.get_fundamentals",
    "company.get_corporate_actions",
    "company.get_share_count_history",
    "company.get_earnings_calendar",
    "company.get_consensus_history",
    "company.get_guidance_history",
    "company.get_estimate_revisions",
    "company.get_earnings_setup",
    "energy.get_electricity_retail_sales",
    "energy.get_weekly_fundamentals",
    "market.search_instruments",
    "market.get_returns",
    "market.get_forward_returns",
    "market.technical_indicators",
    "market.cross_sectional_performance",
    "rates.get_funding_conditions",
    "rates.get_repo_facility_usage",
    "rates.curve_analytics",
    "options.search_captures",
    "options.search_contracts",
    "options.get_surface_snapshot",
    "options.surface_diagnostics",
    "options.screen_contracts",
    "options.strategy_scenario",
    "research.point_in_time_panel",
    "data.quality_audit",
    "research.event_study",
    "alpha.signal_diagnostics",
    "research.walk_forward_backtest",
    "research.robustness_suite",
    "stats.multiple_testing",
    "forecast.evaluate",
    "news.search",
    "research.liquidity_credit_state",
)

STAGE1_TOOL_NAMES = ("macro.get_series", "timeseries.describe")
_STAGE1_HANDLERS = {name: name for name in STAGE1_TOOL_NAMES}


@dataclass(frozen=True, slots=True)
class StoreDeclaration:
    id: str
    default_path: str
    path_env: str
    anchor_relation: str
    control_tables: tuple[str, ...]
    migration_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MigrationDeclaration:
    id: str
    store: str
    ordinal: int
    resource: str
    sha256: str
    semantic_scope: str
    dependencies: tuple[str, ...]
    reconstruction_state: str


@dataclass(frozen=True, slots=True)
class DatasetDeclaration:
    id: str
    store: str
    layer: str
    relations: tuple[str, ...]
    schema_version: str
    active: bool


@dataclass(frozen=True, slots=True)
class Registry:
    schema_version: str
    registry_id: str
    revision: str
    status: str
    project_root: Path
    source_path: Path
    stores: tuple[StoreDeclaration, ...]
    migrations: tuple[MigrationDeclaration, ...]
    datasets: tuple[DatasetDeclaration, ...]
    collectors: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...]
    dashboard: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]

    def store(self, role: str) -> StoreDeclaration:
        for declaration in self.stores:
            if declaration.id == role:
                return declaration
        raise RegistryError("Unknown store declaration")

    def migrations_for(self, role: str) -> tuple[MigrationDeclaration, ...]:
        return tuple(item for item in self.migrations if item.store == role)

    def datasets_for(self, role: str) -> tuple[DatasetDeclaration, ...]:
        return tuple(item for item in self.datasets if item.store == role)

    def tool(self, name: str) -> Mapping[str, Any]:
        for item in self.tools:
            if item["id"] == name:
                return item
        raise RegistryError("Unknown tool declaration")


def _error(pointer: str, rule: str, message: str) -> RegistryError:
    return RegistryError(message, issues=(Issue(pointer, rule, message),))


def _safe_relative_path(raw: Any, pointer: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise _error(pointer, "path", "Expected a nonempty relative path")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise _error(pointer, "path", "Path must be project-relative without traversal")
    return path.as_posix()


def _require_keys(value: Mapping[str, Any], required: set[str], pointer: str) -> None:
    missing = required - set(value)
    if missing:
        raise _error(pointer, "required", f"Missing required fields: {sorted(missing)}")
    unknown = set(value) - required
    if unknown:
        raise _error(
            pointer,
            "additional_properties",
            f"Unknown fields: {sorted(unknown)}",
        )


def _validate_strict_schema(schema: Any, pointer: str) -> None:
    if not isinstance(schema, dict):
        raise _error(pointer, "schema", "Schema must be an object")
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if (
            not schema_type
            or not all(isinstance(item, str) for item in schema_type)
            or not set(schema_type).issubset(
                {"string", "integer", "number", "boolean", "null"}
            )
            or len(schema_type) != len(set(schema_type))
        ):
            raise _error(pointer, "schema", "Unsupported union schema type")
        if set(schema) != {"type"}:
            raise _error(pointer, "schema", "Stage 1 union schemas may only declare type")
        return
    if schema_type == "object":
        allowed = {"type", "additionalProperties", "properties", "required"}
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Object schema contains an unsupported keyword")
        if schema.get("additionalProperties") is not False:
            raise _error(pointer, "strict_schema", "Every object schema must reject extra fields")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise _error(pointer, "schema", "Object schema requires properties")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or not all(isinstance(name, str) for name in required)
            or len(required) != len(set(required))
            or not set(required).issubset(properties)
        ):
            raise _error(pointer, "schema", "Object required fields must be unique properties")
        prohibited = _PROHIBITED_TOOL_KEYS.intersection(properties)
        if prohibited:
            raise _error(pointer, "prohibited", "Tool schema contains a prohibited field")
        for name, child in properties.items():
            _validate_strict_schema(child, f"{pointer}/properties/{name}")
    elif schema_type == "array":
        allowed = {"type", "items", "minItems", "maxItems"}
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Array schema contains an unsupported keyword")
        for bound in ("minItems", "maxItems"):
            if bound in schema and (
                not isinstance(schema[bound], int)
                or isinstance(schema[bound], bool)
                or schema[bound] < 0
            ):
                raise _error(pointer, "schema", "Array bounds must be nonnegative integers")
        if schema.get("minItems", 0) > schema.get("maxItems", MAX_JSON_BYTES):
            raise _error(pointer, "schema", "Array bounds are inverted")
        _validate_strict_schema(schema.get("items"), f"{pointer}/items")
    elif schema_type in {"string", "integer", "number", "boolean", "null"}:
        allowed = {"type", "const", "enum"}
        if schema_type == "string":
            allowed.update({"format", "minLength", "maxLength"})
        if schema_type in {"integer", "number"}:
            allowed.update({"minimum", "maximum"})
        if set(schema) - allowed:
            raise _error(pointer, "schema", "Scalar schema contains an unsupported keyword")
        if "enum" in schema and (
            not isinstance(schema["enum"], list) or not schema["enum"]
        ):
            raise _error(pointer, "schema", "Schema enum must be a nonempty array")
    else:
        raise _error(pointer, "schema", "Unsupported or missing schema type")


def _validate_top_level(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RegistryError("Registry root must be an object")
    required = {
        "schema_version",
        "registry_id",
        "revision",
        "status",
        "compatibility_target",
        "stores",
        "migrations",
        "datasets",
        "collectors",
        "jobs",
        "tools",
        "dashboard",
        "exports",
        "presentation_order",
    }
    _require_keys(raw, required, "/")
    if raw["schema_version"] != "1.0.0" or raw["status"] != "validated":
        raise RegistryError("Stage 1 requires registry schema 1.0.0 with validated status")
    for collection in ("stores", "migrations", "datasets", "collectors", "jobs", "tools", "dashboard", "exports"):
        if not isinstance(raw[collection], list):
            raise _error(f"/{collection}", "type", "Expected an array")
    return raw


def load_registry(
    path: str | Path,
    *,
    project_root: str | Path,
    environment: Mapping[str, str] | None = None,
) -> Registry:
    """Load a registry from an explicitly supplied path.

    The environment mapping is explicit so tests never consult ambient process
    state.  Its optional registry override is honored only when ``path`` is the
    canonical relative path.
    """

    root = Path(project_root).resolve(strict=True)
    supplied = Path(path)
    env = dict(environment or {})
    if supplied == CANONICAL_REGISTRY_PATH and REGISTRY_ENV in env:
        override = env[REGISTRY_ENV]
        if not override:
            raise RegistryError("Registry path override cannot be empty")
        supplied = Path(override)
    source = supplied if supplied.is_absolute() else root / supplied
    source = source.resolve(strict=True)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise RegistryError("Registry file is unavailable") from exc
    raw = _validate_top_level(loads_strict(payload, max_bytes=2 * 1024 * 1024))

    stores: list[StoreDeclaration] = []
    expected_roles = {role.value for role in STORE_ROLES}
    seen_roles: set[str] = set()
    for index, value in enumerate(raw["stores"]):
        pointer = f"/stores/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Store declaration must be an object")
        _require_keys(value, {"id", "default_path", "path_env", "anchor_relation", "control_tables", "migration_order"}, pointer)
        role = value["id"]
        if not isinstance(role, str) or role not in expected_roles or role in seen_roles:
            raise _error(f"{pointer}/id", "store", "Store IDs must be the four unique roles")
        seen_roles.add(role)
        default_path = _safe_relative_path(value["default_path"], f"{pointer}/default_path")
        expected_env = f"QUANT_{role.upper()}_DB_PATH"
        if value["path_env"] != expected_env:
            raise _error(f"{pointer}/path_env", "environment", "Store path override is bound to the wrong role")
        if value["anchor_relation"] != "store_metadata":
            raise _error(f"{pointer}/anchor_relation", "const", "Unexpected store role anchor")
        if not isinstance(value["control_tables"], list) or not all(
            isinstance(item, str) for item in value["control_tables"]
        ):
            raise _error(f"{pointer}/control_tables", "type", "Control tables must be strings")
        control = tuple(value["control_tables"])
        if control != ("schema_migrations", "dataset_registry", "ingestion_runs"):
            raise _error(f"{pointer}/control_tables", "control_tables", "Unexpected control table set")
        if not isinstance(value["migration_order"], list) or not all(
            isinstance(item, str) for item in value["migration_order"]
        ):
            raise _error(f"{pointer}/migration_order", "type", "Migration order must be a string array")
        stores.append(StoreDeclaration(role, default_path, value["path_env"], value["anchor_relation"], control, tuple(value["migration_order"])))
    if seen_roles != expected_roles:
        raise RegistryError("Registry must declare exactly four stores")

    migrations: list[MigrationDeclaration] = []
    migration_ids: set[str] = set()
    by_store: dict[str, list[MigrationDeclaration]] = {role: [] for role in expected_roles}
    for index, value in enumerate(raw["migrations"]):
        pointer = f"/migrations/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Migration declaration must be an object")
        _require_keys(value, {"id", "store", "ordinal", "resource", "sha256", "semantic_scope", "dependencies", "reconstruction_state"}, pointer)
        migration_id = value["id"]
        if (
            not isinstance(migration_id, str)
            or migration_id in migration_ids
            or not isinstance(value["store"], str)
            or value["store"] not in expected_roles
        ):
            raise _error(f"{pointer}/id", "unique", "Migration ID/store is invalid")
        if (
            not isinstance(value["ordinal"], int)
            or isinstance(value["ordinal"], bool)
            or value["ordinal"] < 1
        ):
            raise _error(f"{pointer}/ordinal", "type", "Migration ordinal must be positive")
        if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
            raise _error(f"{pointer}/sha256", "format", "Migration checksum must be lowercase SHA-256")
        if not isinstance(value["semantic_scope"], str) or not value["semantic_scope"]:
            raise _error(f"{pointer}/semantic_scope", "type", "Migration scope must be nonempty")
        if not isinstance(value["dependencies"], list) or not all(
            isinstance(item, str) for item in value["dependencies"]
        ) or len(value["dependencies"]) != len(set(value["dependencies"])):
            raise _error(f"{pointer}/dependencies", "type", "Migration dependencies must be unique strings")
        migration_ids.add(migration_id)
        resource = _safe_relative_path(value["resource"], f"{pointer}/resource")
        resource_path = (root / resource).resolve(strict=True)
        try:
            resource_path.relative_to(root)
        except ValueError as exc:
            raise _error(f"{pointer}/resource", "containment", "Migration must remain inside the project") from exc
        digest = hashlib.sha256(resource_path.read_bytes()).hexdigest()
        if digest != value["sha256"]:
            raise _error(f"{pointer}/sha256", "checksum", "Migration resource checksum mismatch")
        if value["reconstruction_state"] not in {"unresolved", "fixture_validated", "recovered_exact"}:
            raise _error(f"{pointer}/reconstruction_state", "enum", "Invalid reconstruction state")
        declaration = MigrationDeclaration(
            migration_id,
            value["store"],
            value["ordinal"],
            resource,
            value["sha256"],
            value["semantic_scope"],
            tuple(value["dependencies"]),
            value["reconstruction_state"],
        )
        migrations.append(declaration)
        by_store[declaration.store].append(declaration)
    for store in stores:
        ordered = sorted(by_store[store.id], key=lambda item: item.ordinal)
        if [item.ordinal for item in ordered] != list(range(1, len(ordered) + 1)):
            raise RegistryError("Migration ordinals must be total and start at one")
        if tuple(item.id for item in ordered) != store.migration_order:
            raise RegistryError("Store migration order does not match migration declarations")
        seen: set[str] = set()
        for item in ordered:
            if not set(item.dependencies).issubset(seen):
                raise RegistryError("Migration dependencies must refer to earlier same-store resources")
            seen.add(item.id)

    datasets: list[DatasetDeclaration] = []
    dataset_ids: set[str] = set()
    owned_relations: dict[tuple[str, str], str] = {}
    for index, value in enumerate(raw["datasets"]):
        pointer = f"/datasets/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Dataset declaration must be an object")
        _require_keys(value, {"id", "store", "layer", "relations", "schema_version", "active"}, pointer)
        if (
            not isinstance(value["id"], str)
            or value["id"] in dataset_ids
            or not isinstance(value["store"], str)
            or value["store"] not in expected_roles
        ):
            raise _error(f"{pointer}/id", "unique", "Dataset ID/store is invalid")
        if not isinstance(value["layer"], str) or value["layer"] not in {"evidence", "canonical", "research"}:
            raise _error(f"{pointer}/layer", "enum", "Invalid dataset layer")
        dataset_ids.add(value["id"])
        if value["active"] is not True or not isinstance(value["schema_version"], str) or not _SEMVER.fullmatch(value["schema_version"]):
            raise _error(pointer, "dataset", "Stage 1 datasets must be active and versioned")
        if not isinstance(value["relations"], list):
            raise _error(f"{pointer}/relations", "type", "Dataset relations must be an array")
        relations = tuple(value["relations"])
        if not relations or not all(isinstance(relation, str) and _IDENTIFIER.fullmatch(relation) for relation in relations):
            raise _error(f"{pointer}/relations", "identifier", "Dataset relations must be nonempty safe identifiers")
        for relation in relations:
            key = (value["store"], relation)
            if key in owned_relations:
                raise RegistryError("A physical relation cannot have two dataset owners")
            owned_relations[key] = value["id"]
        datasets.append(DatasetDeclaration(value["id"], value["store"], value["layer"], relations, value["schema_version"], value["active"]))

    compatibility = raw["compatibility_target"]
    if not isinstance(compatibility, dict):
        raise RegistryError("Compatibility target must be an object")
    _require_keys(
        compatibility,
        {
            "api_version",
            "status",
            "reserved_tool_names",
            "stage1_tool_names",
            "stage1_milestone_status",
        },
        "/compatibility_target",
    )
    if (
        compatibility["api_version"] != "1.0"
        or compatibility["status"] != "accepted_incremental"
        or compatibility["stage1_milestone_status"] != "validated_non_active"
    ):
        raise RegistryError("Stage 1 compatibility metadata is invalid")
    reserved = compatibility.get("reserved_tool_names")
    if not isinstance(reserved, list) or tuple(reserved) != PUBLIC_TOOL_NAMES or len(set(reserved)) != 57:
        raise RegistryError("Compatibility target must preserve the exact 57-name inventory")
    if tuple(compatibility.get("stage1_tool_names", ())) != STAGE1_TOOL_NAMES:
        raise RegistryError("Stage 1 compatibility subset must contain exactly two tools")

    tools: list[Mapping[str, Any]] = []
    tool_names: list[str] = []
    for index, value in enumerate(raw["tools"]):
        pointer = f"/tools/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Tool declaration must be an object")
        _require_keys(value, {"id", "version", "handler", "read_only", "datasets", "input_schema", "output_schema", "examples", "workload_bounds", "availability_policy"}, pointer)
        if (
            not isinstance(value["id"], str)
            or value["id"] not in STAGE1_TOOL_NAMES
            or value["handler"] != _STAGE1_HANDLERS.get(value["id"])
        ):
            raise _error(f"{pointer}/handler", "handler", "Tool handler is not the reviewed Stage 1 operation")
        if not isinstance(value["handler"], str) or not _HANDLER.fullmatch(value["handler"]):
            raise _error(f"{pointer}/handler", "format", "Tool handler has an unsafe identifier")
        if not isinstance(value["version"], str) or not _SEMVER.fullmatch(value["version"]):
            raise _error(f"{pointer}/version", "format", "Tool version must be semantic")
        if value["read_only"] is not True:
            raise _error(f"{pointer}/read_only", "const", "Public tools must be read-only")
        if not isinstance(value["datasets"], list) or not all(
            isinstance(dataset_id, str) for dataset_id in value["datasets"]
        ) or not set(value["datasets"]).issubset(dataset_ids):
            raise _error(f"{pointer}/datasets", "reference", "Tool references an unknown dataset")
        _validate_strict_schema(value["input_schema"], f"{pointer}/input_schema")
        _validate_strict_schema(value["output_schema"], f"{pointer}/output_schema")
        examples = value["examples"]
        if not isinstance(examples, list) or not examples:
            raise _error(f"{pointer}/examples", "examples", "Every Stage 1 tool needs an example")
        for example_index, example in enumerate(examples):
            try:
                validate_schema(example, value["input_schema"])
            except ValidationError as exc:
                raise _error(
                    f"{pointer}/examples/{example_index}",
                    "example",
                    "Tool example does not satisfy its input schema",
                ) from exc
        bounds = value["workload_bounds"]
        if not isinstance(bounds, dict):
            raise _error(f"{pointer}/workload_bounds", "type", "Workload bounds must be an object")
        _require_keys(
            bounds,
            {"max_rows", "max_request_bytes", "max_response_bytes"},
            f"{pointer}/workload_bounds",
        )
        if any(
            not isinstance(bounds[name], int)
            or isinstance(bounds[name], bool)
            or bounds[name] < 1
            or bounds[name] > MAX_JSON_BYTES
            for name in bounds
        ) or bounds["max_rows"] > 10_000:
            raise _error(f"{pointer}/workload_bounds", "bounds", "Tool workload bounds are invalid")
        availability = value["availability_policy"]
        if not isinstance(availability, dict):
            raise _error(f"{pointer}/availability_policy", "type", "Availability policy must be an object")
        if value["id"] == "macro.get_series":
            _require_keys(
                availability,
                {"modes", "default_date_only_policy", "period_range_rule"},
                f"{pointer}/availability_policy",
            )
            if (
                availability["modes"] != ["latest", "as_of", "first_release"]
                or availability["default_date_only_policy"] != "completed_date"
                or availability["period_range_rule"] != "period_start"
            ):
                raise _error(f"{pointer}/availability_policy", "const", "Macro availability policy drifted")
        else:
            _require_keys(availability, {"inherits_input"}, f"{pointer}/availability_policy")
            if availability["inherits_input"] is not True:
                raise _error(f"{pointer}/availability_policy", "const", "Describe must inherit input availability")
        tool_names.append(value["id"])
        tools.append(value)
    if tuple(tool_names) != STAGE1_TOOL_NAMES or len(set(tool_names)) != 2:
        raise RegistryError("Stage 1 registry must expose exactly the two milestone tools")
    macro_output = tools[0]["output_schema"]
    describe_series = tools[1]["input_schema"]["properties"]["series"]
    if describe_series != macro_output:
        raise RegistryError("Composable describe input must exactly match macro TimeSeries output")

    collectors = tuple(raw["collectors"])
    for index, collector in enumerate(collectors):
        if not isinstance(collector, dict):
            raise _error(f"/collectors/{index}", "type", "Collector must be an object")
        pointer = f"/collectors/{index}"
        _require_keys(
            collector,
            {"id", "version", "handler", "network", "output_datasets", "semantic_identity", "mutation_policy"},
            pointer,
        )
        if (
            not isinstance(collector["id"], str)
            or not isinstance(collector["version"], str)
            or not _SEMVER.fullmatch(collector["version"])
            or not isinstance(collector["handler"], str)
            or not _HANDLER.fullmatch(collector["handler"])
            or collector["network"] is not False
            or not isinstance(collector["semantic_identity"], str)
            or not isinstance(collector["mutation_policy"], str)
        ):
            raise _error(pointer, "collector", "Collector metadata is invalid")
        outputs = collector.get("output_datasets")
        if (
            not isinstance(outputs, list)
            or not outputs
            or not all(isinstance(item, str) for item in outputs)
            or not set(outputs).issubset(dataset_ids)
        ):
            raise _error(f"/collectors/{index}/output_datasets", "reference", "Collector output is invalid")

    dashboard = tuple(raw["dashboard"])
    for index, exposure in enumerate(dashboard):
        pointer = f"/dashboard/{index}"
        if not isinstance(exposure, dict):
            raise _error(pointer, "type", "Dashboard exposure must be an object")
        _require_keys(
            exposure,
            {"id", "route", "visibility", "datasets", "tools", "relations", "api_routes"},
            pointer,
        )
        if (
            exposure["id"] != "stage1.overview"
            or exposure["route"] != "/"
            or exposure["visibility"] != "local_private"
            or not isinstance(exposure["datasets"], list)
            or not all(isinstance(item, str) for item in exposure["datasets"])
            or not set(exposure["datasets"]).issubset(dataset_ids)
            or not isinstance(exposure["tools"], list)
            or not all(isinstance(item, str) for item in exposure["tools"])
            or not set(exposure["tools"]).issubset(tool_names)
            or not isinstance(exposure["relations"], list)
            or not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in exposure["relations"])
            or not isinstance(exposure["api_routes"], list)
            or not all(isinstance(item, str) and item.startswith("/") for item in exposure["api_routes"])
        ):
            raise _error(f"/dashboard/{index}", "reference", "Dashboard exposure reference is invalid")

    if raw["jobs"] or raw["exports"]:
        raise RegistryError("Stage 1 must not activate jobs or exports")
    presentation = raw["presentation_order"]
    if not isinstance(presentation, dict):
        raise RegistryError("Presentation order must be an object")
    _require_keys(presentation, {"stores", "tools"}, "/presentation_order")
    if presentation["stores"] != [role.value for role in STORE_ROLES] or tuple(
        presentation["tools"]
    ) != STAGE1_TOOL_NAMES:
        raise RegistryError("Presentation order must match the reviewed Stage 1 inventory")

    return Registry(
        schema_version=raw["schema_version"],
        registry_id=raw["registry_id"],
        revision=raw["revision"],
        status=raw["status"],
        project_root=root,
        source_path=source,
        stores=tuple(stores),
        migrations=tuple(migrations),
        datasets=tuple(datasets),
        collectors=collectors,
        tools=tuple(tools),
        dashboard=dashboard,
        raw=raw,
    )
