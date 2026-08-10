"""Strict loader for the canonical four-store system registry."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import Issue, RegistryError, ValidationError
from .json_codec import MAX_JSON_BYTES, dumps_strict, loads_strict
from .schema import validate_schema
from .stores import STORE_ROLES


CANONICAL_REGISTRY_PATH = Path("config/system_registry.json")
REGISTRY_ENV = "QUANT_SYSTEM_REGISTRY_PATH"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STABLE_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[.:_-][a-z0-9]+)*$")
_HANDLER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_DURATION = re.compile(r"^P(?:0D|[1-9][0-9]*D)$")
_PROHIBITED_TOOL_KEYS = {
    "database_path",
    "db_path",
    "sqlite_uri",
    "sql",
    "pragma",
    "filesystem_root",
    "connection_string",
}
_MUTABLE_IDENTITY_FIELDS = {
    "active",
    "completed_at",
    "current_version_id",
    "last_semantic_identity",
    "last_successful_run_id",
    "status",
    "updated_at",
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
    write_coordination: Mapping[str, Any]
    backup: Mapping[str, Any]


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
    version: str
    store: str
    layer: str
    relations: tuple[str, ...]
    physical: Mapping[str, Any]
    identity: Mapping[str, Any]
    temporal: Mapping[str, Any]
    revision_policy: str
    freshness: Mapping[str, Any]
    quality_contract: Mapping[str, Any]
    collector_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    dashboard_ids: tuple[str, ...]
    export_ids: tuple[str, ...]
    active: bool

    @property
    def schema_version(self) -> str:
        """Store-local compatibility name for the dataset contract version."""

        return self.version

    @property
    def identity_sha256(self) -> str:
        material = {
            "id": self.id,
            "store": self.store,
            "layer": self.layer,
            "physical": dict(self.physical),
            "identity": dict(self.identity),
        }
        return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Registry:
    schema_id: str
    schema_version: str
    registry_version: str
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

    @property
    def revision(self) -> str:
        """Compatibility accessor used by the validated Stage 1 boundary."""

        return self.registry_version

    @property
    def registry_id(self) -> str:
        return self.schema_id

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
    normalized = path.as_posix()
    if normalized != raw or "\\" in raw:
        raise _error(pointer, "path", "Path must use normalized forward-slash form")
    return normalized


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


def _string_array(
    value: Any,
    pointer: str,
    *,
    allow_empty: bool = True,
    identifiers: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item for item in value)
        or len(value) != len(set(value))
    ):
        raise _error(pointer, "type", "Expected a unique string array")
    result = tuple(value)
    if identifiers and not all(_IDENTIFIER.fullmatch(item) for item in result):
        raise _error(pointer, "identifier", "Expected safe SQL identifiers")
    return result


def _stable_identifier(value: Any, pointer: str) -> str:
    if not isinstance(value, str) or not _STABLE_IDENTIFIER.fullmatch(value):
        raise _error(
            pointer,
            "identifier",
            "Expected a normalized lowercase stable identifier",
        )
    return value


def _stable_identifier_array(
    value: Any,
    pointer: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    result = _string_array(value, pointer, allow_empty=allow_empty)
    for index, item in enumerate(result):
        _stable_identifier(item, f"{pointer}/{index}")
    return result


def _nonempty_mapping(value: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not value:
        raise _error(pointer, "type", "Expected a nonempty object")
    return value


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
        "schema_id",
        "schema_version",
        "registry_version",
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
    schema_id = _stable_identifier(raw["schema_id"], "/schema_id")
    if (
        schema_id != "quant_data.system_registry"
        or raw["schema_version"] != "1.0.0"
        or not isinstance(raw["registry_version"], str)
        or not _SEMVER.fullmatch(raw["registry_version"])
        or raw["status"] != "validated"
    ):
        raise RegistryError("Unsupported registry schema, version, or lifecycle status")
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
    expected_defaults = {
        "market": "data/market_data.sqlite",
        "macro": "data/macro_data.sqlite",
        "company": "data/company_data.sqlite",
        "news": "data/news_data.sqlite",
    }
    expected_control = (
        "schema_migrations",
        "dataset_registry",
        "dataset_identity_contracts",
        "ingestion_runs",
        "ingestion_run_outputs",
        "ingestion_run_failures",
        "ingestion_artifacts",
        "ingestion_snapshots",
        "ingestion_snapshot_artifacts",
        "data_quality_results",
    )
    seen_roles: set[str] = set()
    for index, value in enumerate(raw["stores"]):
        pointer = f"/stores/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Store declaration must be an object")
        _require_keys(
            value,
            {
                "id",
                "default_path",
                "path_env",
                "anchor_relation",
                "control_tables",
                "migration_order",
                "write_coordination",
                "backup",
            },
            pointer,
        )
        role = value["id"]
        if not isinstance(role, str) or role not in expected_roles or role in seen_roles:
            raise _error(f"{pointer}/id", "store", "Store IDs must be the four unique roles")
        seen_roles.add(role)
        default_path = _safe_relative_path(value["default_path"], f"{pointer}/default_path")
        if default_path != expected_defaults[role]:
            raise _error(
                f"{pointer}/default_path",
                "const",
                "Store default path is not the accepted four-store path",
            )
        expected_env = f"QUANT_{role.upper()}_DB_PATH"
        if value["path_env"] != expected_env:
            raise _error(f"{pointer}/path_env", "environment", "Store path override is bound to the wrong role")
        if value["anchor_relation"] != "store_metadata":
            raise _error(f"{pointer}/anchor_relation", "const", "Unexpected store role anchor")
        control = _string_array(
            value["control_tables"],
            f"{pointer}/control_tables",
            allow_empty=False,
            identifiers=True,
        )
        if control != expected_control:
            raise _error(f"{pointer}/control_tables", "control_tables", "Unexpected control table set")
        migration_order = _stable_identifier_array(
            value["migration_order"], f"{pointer}/migration_order"
        )
        coordination = _nonempty_mapping(
            value["write_coordination"], f"{pointer}/write_coordination"
        )
        _require_keys(
            coordination,
            {"scope", "lock_key", "multi_store_order", "timeout_seconds"},
            f"{pointer}/write_coordination",
        )
        if (
            coordination["scope"] != "physical_store"
            or coordination["lock_key"] != "sha256_canonical_path_uri_v1"
            or coordination["multi_store_order"] != "canonical_path_uri"
            or isinstance(coordination["timeout_seconds"], bool)
            or not isinstance(coordination["timeout_seconds"], int)
            or not 1 <= coordination["timeout_seconds"] <= 300
        ):
            raise _error(
                f"{pointer}/write_coordination",
                "coordination",
                "Store write coordination is not the accepted physical-lock contract",
            )
        backup = _nonempty_mapping(value["backup"], f"{pointer}/backup")
        _require_keys(
            backup,
            {"method", "requires_explicit_target", "verify"},
            f"{pointer}/backup",
        )
        if (
            backup["method"] != "sqlite_online_backup"
            or backup["requires_explicit_target"] is not True
            or backup["verify"]
            != [
                "integrity_check",
                "foreign_key_check",
                "migration_ledger",
                "dataset_registry",
            ]
        ):
            raise _error(f"{pointer}/backup", "backup", "Unsupported backup contract")
        stores.append(
            StoreDeclaration(
                role,
                default_path,
                value["path_env"],
                value["anchor_relation"],
                control,
                migration_order,
                coordination,
                backup,
            )
        )
    if seen_roles != expected_roles:
        raise RegistryError("Registry must declare exactly four stores")

    migrations: list[MigrationDeclaration] = []
    migration_ids: set[str] = set()
    migration_resources: set[tuple[str, str]] = set()
    by_store: dict[str, list[MigrationDeclaration]] = {role: [] for role in expected_roles}
    for index, value in enumerate(raw["migrations"]):
        pointer = f"/migrations/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Migration declaration must be an object")
        _require_keys(value, {"id", "store", "ordinal", "resource", "sha256", "semantic_scope", "dependencies", "reconstruction_state"}, pointer)
        migration_id = _stable_identifier(value["id"], f"{pointer}/id")
        if (
            migration_id in migration_ids
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
        dependencies = _stable_identifier_array(
            value["dependencies"], f"{pointer}/dependencies"
        )
        migration_ids.add(migration_id)
        resource = _safe_relative_path(value["resource"], f"{pointer}/resource")
        resource_key = (value["store"], resource)
        if resource_key in migration_resources:
            raise _error(
                f"{pointer}/resource",
                "unique",
                "Migration resources must be unique within a store",
            )
        migration_resources.add(resource_key)
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
            dependencies,
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
        _require_keys(
            value,
            {
                "id",
                "version",
                "store",
                "layer",
                "physical",
                "identity",
                "temporal",
                "revision_policy",
                "freshness",
                "quality_contract",
                "collector_ids",
                "tool_ids",
                "dashboard_ids",
                "export_ids",
                "active",
            },
            pointer,
        )
        dataset_id = _stable_identifier(value["id"], f"{pointer}/id")
        if (
            dataset_id in dataset_ids
            or not isinstance(value["store"], str)
            or value["store"] not in expected_roles
        ):
            raise _error(f"{pointer}/id", "unique", "Dataset ID/store is invalid")
        if not isinstance(value["layer"], str) or value["layer"] not in {
            "evidence",
            "canonical",
            "derived",
        }:
            raise _error(f"{pointer}/layer", "enum", "Invalid dataset layer")
        dataset_ids.add(dataset_id)
        if (
            not isinstance(value["version"], str)
            or not _SEMVER.fullmatch(value["version"])
            or not isinstance(value["active"], bool)
        ):
            raise _error(pointer, "dataset", "Dataset lifecycle/version is invalid")

        physical = _nonempty_mapping(value["physical"], f"{pointer}/physical")
        _require_keys(physical, {"relations"}, f"{pointer}/physical")
        if not isinstance(physical["relations"], list) or not physical["relations"]:
            raise _error(
                f"{pointer}/physical/relations",
                "type",
                "Dataset physical relations must be nonempty",
            )
        relation_names: list[str] = []
        for relation_index, relation in enumerate(physical["relations"]):
            relation_pointer = f"{pointer}/physical/relations/{relation_index}"
            if not isinstance(relation, dict):
                raise _error(relation_pointer, "type", "Physical relation must be an object")
            _require_keys(relation, {"name", "kind"}, relation_pointer)
            if (
                not isinstance(relation["name"], str)
                or not _IDENTIFIER.fullmatch(relation["name"])
                or relation["kind"] not in {"table", "view", "materialization"}
            ):
                raise _error(relation_pointer, "relation", "Physical relation is unsafe")
            relation_names.append(relation["name"])
        if len(relation_names) != len(set(relation_names)):
            raise _error(
                f"{pointer}/physical/relations",
                "unique",
                "Dataset relations must be unique",
            )
        relations = tuple(relation_names)
        for relation in relations:
            key = (value["store"], relation)
            if key in owned_relations:
                raise RegistryError("A physical relation cannot have two dataset owners")
            owned_relations[key] = dataset_id

        identity = _nonempty_mapping(value["identity"], f"{pointer}/identity")
        _require_keys(identity, {"stable_fields", "version_fields"}, f"{pointer}/identity")
        stable_fields = _string_array(
            identity["stable_fields"],
            f"{pointer}/identity/stable_fields",
            allow_empty=False,
            identifiers=True,
        )
        version_fields = _string_array(
            identity["version_fields"],
            f"{pointer}/identity/version_fields",
            identifiers=True,
        )
        all_identity_fields = set(stable_fields).union(version_fields)
        if set(stable_fields).intersection(version_fields) or all_identity_fields.intersection(
            _MUTABLE_IDENTITY_FIELDS
        ):
            raise _error(
                f"{pointer}/identity",
                "immutable_identity",
                "Dataset identity contains overlapping or mutable fields",
            )

        temporal = _nonempty_mapping(value["temporal"], f"{pointer}/temporal")
        _require_keys(
            temporal,
            {
                "observation_fields",
                "observation_precision",
                "availability_fields",
                "availability_precision",
                "timezone_rule",
                "range_semantics",
                "vintage_modes",
                "history_basis",
                "missingness",
            },
            f"{pointer}/temporal",
        )
        _string_array(
            temporal["observation_fields"],
            f"{pointer}/temporal/observation_fields",
            allow_empty=False,
            identifiers=True,
        )
        _string_array(
            temporal["availability_fields"],
            f"{pointer}/temporal/availability_fields",
            allow_empty=False,
            identifiers=True,
        )
        vintage_modes = _string_array(
            temporal["vintage_modes"],
            f"{pointer}/temporal/vintage_modes",
            allow_empty=False,
        )
        if (
            temporal["observation_precision"] not in {"date", "datetime", "mixed"}
            or temporal["availability_precision"] not in {"date", "datetime", "mixed"}
            or temporal["timezone_rule"] != "source_native_no_conversion"
            or temporal["range_semantics"] != "inclusive"
            or not set(vintage_modes).issubset({"latest", "as_of", "first_release"})
            or temporal["history_basis"] not in {"source_vintage", "local_capture"}
            or temporal["missingness"] not in {"not_applicable", "explicit_null", "explicit_reason"}
        ):
            raise _error(f"{pointer}/temporal", "temporal", "Dataset temporal contract is invalid")
        if value["revision_policy"] not in {
            "immutable_capture",
            "append_version",
            "current_state_capture",
            "derived_rebuild",
        }:
            raise _error(
                f"{pointer}/revision_policy", "enum", "Dataset revision policy is invalid"
            )

        freshness = _nonempty_mapping(value["freshness"], f"{pointer}/freshness")
        _require_keys(
            freshness,
            {
                "cadence",
                "expected_lag",
                "stale_after",
                "measured_from",
                "if_new",
                "health_severity",
            },
            f"{pointer}/freshness",
        )
        if (
            freshness["cadence"]
            not in {"intraday", "daily", "weekly", "monthly", "event_driven", "manual"}
            or not isinstance(freshness["expected_lag"], str)
            or not _DURATION.fullmatch(freshness["expected_lag"])
            or not isinstance(freshness["stale_after"], str)
            or not _DURATION.fullmatch(freshness["stale_after"])
            or freshness["measured_from"]
            not in {"source_period", "source_published_at", "available_at", "successful_capture"}
            or not isinstance(freshness["if_new"], bool)
            or freshness["health_severity"] not in {"informational", "warning", "critical"}
        ):
            raise _error(f"{pointer}/freshness", "freshness", "Dataset freshness is invalid")

        quality = _nonempty_mapping(
            value["quality_contract"], f"{pointer}/quality_contract"
        )
        _require_keys(
            quality,
            {"missingness", "units", "rules", "required_warnings"},
            f"{pointer}/quality_contract",
        )
        if (
            not isinstance(quality["missingness"], str)
            or not quality["missingness"]
            or not isinstance(quality["units"], str)
            or not quality["units"]
        ):
            raise _error(
                f"{pointer}/quality_contract", "quality", "Dataset quality contract is invalid"
            )
        _string_array(
            quality["rules"], f"{pointer}/quality_contract/rules", allow_empty=False
        )
        _string_array(
            quality["required_warnings"], f"{pointer}/quality_contract/required_warnings"
        )
        collector_ids = _stable_identifier_array(
            value["collector_ids"], f"{pointer}/collector_ids"
        )
        tool_ids = _stable_identifier_array(value["tool_ids"], f"{pointer}/tool_ids")
        dashboard_ids = _stable_identifier_array(
            value["dashboard_ids"], f"{pointer}/dashboard_ids"
        )
        export_ids = _stable_identifier_array(
            value["export_ids"], f"{pointer}/export_ids"
        )
        if not value["active"] and any(
            (collector_ids, tool_ids, dashboard_ids, export_ids)
        ):
            raise _error(pointer, "inactive", "Inactive datasets cannot have active consumers")
        datasets.append(
            DatasetDeclaration(
                dataset_id,
                value["version"],
                value["store"],
                value["layer"],
                relations,
                physical,
                identity,
                temporal,
                value["revision_policy"],
                freshness,
                quality,
                collector_ids,
                tool_ids,
                dashboard_ids,
                export_ids,
                value["active"],
            )
        )

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
    reserved = _stable_identifier_array(
        compatibility.get("reserved_tool_names"),
        "/compatibility_target/reserved_tool_names",
        allow_empty=False,
    )
    if tuple(reserved) != PUBLIC_TOOL_NAMES or len(set(reserved)) != 57:
        raise RegistryError("Compatibility target must preserve the exact 57-name inventory")
    stage1_tool_names = _stable_identifier_array(
        compatibility.get("stage1_tool_names"),
        "/compatibility_target/stage1_tool_names",
        allow_empty=False,
    )
    if stage1_tool_names != STAGE1_TOOL_NAMES:
        raise RegistryError("Stage 1 compatibility subset must contain exactly two tools")

    tools: list[Mapping[str, Any]] = []
    tool_names: list[str] = []
    for index, value in enumerate(raw["tools"]):
        pointer = f"/tools/{index}"
        if not isinstance(value, dict):
            raise _error(pointer, "type", "Tool declaration must be an object")
        _require_keys(value, {"id", "version", "handler", "read_only", "datasets", "input_schema", "output_schema", "examples", "workload_bounds", "availability_policy"}, pointer)
        tool_id = _stable_identifier(value["id"], f"{pointer}/id")
        if (
            tool_id not in STAGE1_TOOL_NAMES
            or value["handler"] != _STAGE1_HANDLERS.get(tool_id)
        ):
            raise _error(f"{pointer}/handler", "handler", "Tool handler is not the reviewed Stage 1 operation")
        if not isinstance(value["handler"], str) or not _HANDLER.fullmatch(value["handler"]):
            raise _error(f"{pointer}/handler", "format", "Tool handler has an unsafe identifier")
        if not isinstance(value["version"], str) or not _SEMVER.fullmatch(value["version"]):
            raise _error(f"{pointer}/version", "format", "Tool version must be semantic")
        if value["read_only"] is not True:
            raise _error(f"{pointer}/read_only", "const", "Public tools must be read-only")
        tool_datasets = _stable_identifier_array(
            value["datasets"], f"{pointer}/datasets"
        )
        if not set(tool_datasets).issubset(dataset_ids):
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
        tool_names.append(tool_id)
        tools.append(value)
    if tuple(tool_names) != STAGE1_TOOL_NAMES or len(set(tool_names)) != 2:
        raise RegistryError("Stage 1 registry must expose exactly the two milestone tools")
    macro_output = tools[0]["output_schema"]
    describe_series = tools[1]["input_schema"]["properties"]["series"]
    if describe_series != macro_output:
        raise RegistryError("Composable describe input must exactly match macro TimeSeries output")

    collectors = tuple(raw["collectors"])
    collector_ids: set[str] = set()
    for index, collector in enumerate(collectors):
        if not isinstance(collector, dict):
            raise _error(f"/collectors/{index}", "type", "Collector must be an object")
        pointer = f"/collectors/{index}"
        _require_keys(
            collector,
            {
                "id",
                "version",
                "handler",
                "network",
                "input_datasets",
                "output_datasets",
                "semantic_identity",
                "mutation_policy",
                "workload_bounds",
                "retry_policy",
                "configuration_env",
                "physical_locks",
                "schedule_eligibility",
            },
            pointer,
        )
        collector_id = _stable_identifier(collector["id"], f"{pointer}/id")
        if (
            collector_id in collector_ids
            or not isinstance(collector["version"], str)
            or not _SEMVER.fullmatch(collector["version"])
            or not isinstance(collector["handler"], str)
            or not _HANDLER.fullmatch(collector["handler"])
            or collector["network"] is not False
        ):
            raise _error(pointer, "collector", "Collector metadata is invalid")
        collector_ids.add(collector_id)
        inputs = _stable_identifier_array(
            collector["input_datasets"], f"{pointer}/input_datasets"
        )
        outputs = _stable_identifier_array(
            collector["output_datasets"],
            f"{pointer}/output_datasets",
            allow_empty=False,
        )
        if not set(inputs).issubset(dataset_ids) or not set(outputs).issubset(dataset_ids):
            raise _error(f"/collectors/{index}/output_datasets", "reference", "Collector output is invalid")
        semantic_identity = _nonempty_mapping(
            collector["semantic_identity"], f"{pointer}/semantic_identity"
        )
        _require_keys(
            semantic_identity, {"includes", "excludes"}, f"{pointer}/semantic_identity"
        )
        includes = _string_array(
            semantic_identity["includes"],
            f"{pointer}/semantic_identity/includes",
            allow_empty=False,
        )
        excludes = _string_array(
            semantic_identity["excludes"], f"{pointer}/semantic_identity/excludes"
        )
        if set(includes).intersection(excludes) or "request_scope" not in includes:
            raise _error(
                f"{pointer}/semantic_identity",
                "semantic_identity",
                "Collector identity must be scope-bearing and non-overlapping",
            )
        mutation_policy = _nonempty_mapping(
            collector["mutation_policy"], f"{pointer}/mutation_policy"
        )
        _require_keys(mutation_policy, {"mode", "unchanged"}, f"{pointer}/mutation_policy")
        if (
            not isinstance(mutation_policy["mode"], str)
            or not mutation_policy["mode"]
            or mutation_policy["unchanged"] != "zero_persistent_writes"
        ):
            raise _error(
                f"{pointer}/mutation_policy", "mutation", "Collector mutation policy is invalid"
            )
        workload = _nonempty_mapping(
            collector["workload_bounds"], f"{pointer}/workload_bounds"
        )
        _require_keys(
            workload,
            {"max_requests", "max_rows", "max_bytes", "max_seconds"},
            f"{pointer}/workload_bounds",
        )
        if any(
            isinstance(workload[name], bool)
            or not isinstance(workload[name], int)
            or workload[name] < 1
            or workload[name] > MAX_JSON_BYTES
            for name in workload
        ) or workload["max_rows"] > 10_000:
            raise _error(f"{pointer}/workload_bounds", "bounds", "Collector bounds are invalid")
        retry = _nonempty_mapping(collector["retry_policy"], f"{pointer}/retry_policy")
        _require_keys(
            retry,
            {"transient_classes", "max_attempts", "backoff", "honor_retry_after"},
            f"{pointer}/retry_policy",
        )
        _string_array(retry["transient_classes"], f"{pointer}/retry_policy/transient_classes")
        if (
            isinstance(retry["max_attempts"], bool)
            or not isinstance(retry["max_attempts"], int)
            or not 1 <= retry["max_attempts"] <= 10
            or not isinstance(retry["backoff"], str)
            or not retry["backoff"]
            or not isinstance(retry["honor_retry_after"], bool)
        ):
            raise _error(f"{pointer}/retry_policy", "retry", "Collector retry policy is invalid")
        configuration_env = _string_array(
            collector["configuration_env"], f"{pointer}/configuration_env"
        )
        if any(not name.startswith("QUANT_") for name in configuration_env):
            raise _error(
                f"{pointer}/configuration_env", "environment", "Unsafe configuration name"
            )
        if (
            collector["physical_locks"] != "derived_from_output_store_paths"
            or collector["schedule_eligibility"] != {"mode": "manual_only"}
        ):
            raise _error(pointer, "routing", "Collector routing must be registry-derived")

    dashboard = tuple(raw["dashboard"])
    dashboard_ids: set[str] = set()
    for index, exposure in enumerate(dashboard):
        pointer = f"/dashboard/{index}"
        if not isinstance(exposure, dict):
            raise _error(pointer, "type", "Dashboard exposure must be an object")
        _require_keys(
            exposure,
            {"id", "route", "visibility", "datasets", "tools", "relations", "api_routes"},
            pointer,
        )
        dashboard_id = _stable_identifier(exposure["id"], f"{pointer}/id")
        dashboard_datasets = _stable_identifier_array(
            exposure["datasets"], f"{pointer}/datasets"
        )
        dashboard_tools = _stable_identifier_array(
            exposure["tools"], f"{pointer}/tools"
        )
        if (
            dashboard_id != "stage1.overview"
            or dashboard_id in dashboard_ids
            or exposure["route"] != "/"
            or exposure["visibility"] != "local_private"
            or not set(dashboard_datasets).issubset(dataset_ids)
            or not set(dashboard_tools).issubset(tool_names)
            or not isinstance(exposure["relations"], list)
            or not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in exposure["relations"])
            or not isinstance(exposure["api_routes"], list)
            or not all(isinstance(item, str) and item.startswith("/") for item in exposure["api_routes"])
        ):
            raise _error(f"/dashboard/{index}", "reference", "Dashboard exposure reference is invalid")
        dashboard_ids.add(dashboard_id)
        allowed_relations = {
            relation
            for dataset in datasets
            if dataset.id in exposure["datasets"]
            for relation in dataset.relations
        }
        if not set(exposure["relations"]).issubset(allowed_relations):
            raise _error(
                f"{pointer}/relations",
                "ownership",
                "Dashboard relation is not owned by a declared dataset",
            )

    export_ids: set[str] = set()
    if raw["jobs"] or raw["exports"]:
        raise RegistryError("Stage 2 foundation must not activate jobs or exports")

    tool_dataset_refs = {
        dataset_id: {tool["id"] for tool in tools if dataset_id in tool["datasets"]}
        for dataset_id in dataset_ids
    }
    collector_dataset_refs = {
        dataset_id: {
            collector["id"]
            for collector in collectors
            if dataset_id in collector["output_datasets"]
        }
        for dataset_id in dataset_ids
    }
    dashboard_dataset_refs = {
        dataset_id: {
            exposure["id"]
            for exposure in dashboard
            if dataset_id in exposure["datasets"]
        }
        for dataset_id in dataset_ids
    }
    for dataset in datasets:
        if (
            set(dataset.collector_ids) != collector_dataset_refs[dataset.id]
            or set(dataset.tool_ids) != tool_dataset_refs[dataset.id]
            or set(dataset.dashboard_ids) != dashboard_dataset_refs[dataset.id]
            or set(dataset.export_ids) != export_ids
        ):
            raise _error(
                f"/datasets/{dataset.id}",
                "reciprocal_reference",
                "Dataset producer/consumer references are not reciprocal",
            )

    presentation = raw["presentation_order"]
    if not isinstance(presentation, dict):
        raise RegistryError("Presentation order must be an object")
    _require_keys(presentation, {"stores", "tools"}, "/presentation_order")
    presentation_tools = _stable_identifier_array(
        presentation["tools"], "/presentation_order/tools", allow_empty=False
    )
    if presentation["stores"] != [role.value for role in STORE_ROLES] or tuple(
        presentation_tools
    ) != STAGE1_TOOL_NAMES:
        raise RegistryError("Presentation order must match the reviewed Stage 1 inventory")

    return Registry(
        schema_id=raw["schema_id"],
        schema_version=raw["schema_version"],
        registry_version=raw["registry_version"],
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
