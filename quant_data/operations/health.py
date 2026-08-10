"""Read-only operational health reconciliation for the four SQLite stores.

The checks in this module deliberately use the host-routed read connection
factory.  They neither initialize a missing store nor execute a writable
pragma, so an inspection can be used as evidence for the no-write boundary.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from ..errors import ValidationError
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import STORE_ROLES, StoreMap, StoreRole, read_connection


@dataclass(frozen=True, slots=True)
class MigrationHealth:
    """One registry-reconciled migration ledger entry, without a file path."""

    id: str
    ordinal: int
    sha256: str
    reconstruction_state: str

    def to_primitive(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            "sha256": self.sha256,
            "reconstruction_state": self.reconstruction_state,
        }


@dataclass(frozen=True, slots=True)
class DatasetHealth:
    """One registry-reconciled store-local dataset contract."""

    id: str
    version: str
    identity_sha256: str

    def to_primitive(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "identity_sha256": self.identity_sha256,
        }


@dataclass(frozen=True, slots=True)
class StoreInspection:
    """Deterministic, path-free health evidence for one operational store."""

    role: str
    contract_version: str
    control_relations: tuple[str, ...]
    migrations: tuple[MigrationHealth, ...]
    datasets: tuple[DatasetHealth, ...]
    integrity: str
    foreign_key_violations: int

    @property
    def healthy(self) -> bool:
        """A returned inspection has passed every fail-closed reconciliation."""

        return True

    def to_primitive(self) -> dict[str, object]:
        return {
            "role": self.role,
            "contract_version": self.contract_version,
            "control_relations": list(self.control_relations),
            "migrations": [item.to_primitive() for item in self.migrations],
            "datasets": [item.to_primitive() for item in self.datasets],
            "integrity": self.integrity,
            "foreign_key_violations": self.foreign_key_violations,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Path-free four-store health evidence in canonical role order."""

    registry_revision: str
    stores: tuple[StoreInspection, ...]

    @property
    def healthy(self) -> bool:
        return True

    def inspection_for(self, role: StoreRole | str) -> StoreInspection:
        normalized = StoreRole(role)
        for inspection in self.stores:
            if inspection.role == normalized.value:
                return inspection
        raise ValidationError("Health report is missing an operational store")

    def to_primitive(self) -> dict[str, object]:
        return {
            "registry_revision": self.registry_revision,
            "stores": [item.to_primitive() for item in self.stores],
        }


def _health_failure() -> ValidationError:
    """Keep health failures deterministic and free of physical paths."""

    return ValidationError("Store health reconciliation failed")


def _normalized_schema_sql(sql: str) -> str:
    value = sql.strip()
    return value[:-1].rstrip() if value.endswith(";") else value


def _reviewed_statements(registry: Registry, resource_name: str, sha256: str) -> tuple[str, ...]:
    try:
        resource = (registry.project_root / resource_name).resolve(strict=True)
        resource.relative_to(registry.project_root)
        resource_bytes = resource.read_bytes()
        if hashlib.sha256(resource_bytes).hexdigest() != sha256:
            raise _health_failure()
        sql = resource_bytes.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise _health_failure() from None

    statements: list[str] = []
    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character == ";" and sqlite3.complete_statement("".join(buffer)):
            statements.append("".join(buffer).strip())
            buffer.clear()
    if "".join(buffer).strip():
        raise _health_failure()
    return tuple(statements)


def _schema_objects(connection: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
    return {
        (str(row["type"]), str(row["name"]), str(row["tbl_name"])): (
            _normalized_schema_sql(str(row["sql"])) if row["sql"] is not None else ""
        )
        for row in connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name, tbl_name
            """
        )
    }


def _reviewed_schema(registry: Registry, role: StoreRole) -> dict[tuple[str, str, str], str]:
    declarations = tuple(
        sorted(registry.migrations_for(role.value), key=lambda item: item.ordinal)
    )
    expected = sqlite3.connect(":memory:", isolation_level=None)
    expected.row_factory = sqlite3.Row
    try:
        expected.execute("PRAGMA foreign_keys=OFF")
        for declaration in declarations:
            for statement in _reviewed_statements(
                registry,
                declaration.resource,
                declaration.sha256,
            ):
                expected.execute(statement)
        return _schema_objects(expected)
    except sqlite3.Error:
        raise _health_failure() from None
    finally:
        expected.close()


def _expected_relation_types(registry: Registry, role: StoreRole) -> dict[str, str]:
    declaration = registry.store(role.value)
    expected = {
        declaration.anchor_relation: "table",
        **{relation: "table" for relation in declaration.control_tables},
    }
    for dataset in registry.datasets_for(role.value):
        for relation in dataset.physical["relations"]:
            kind = relation["kind"]
            expected[relation["name"]] = "view" if kind == "view" else "table"
    return expected


def _check_declared_relations(
    connection: sqlite3.Connection,
    registry: Registry,
    role: StoreRole,
) -> None:
    expected = _expected_relation_types(registry, role)
    reviewed_schema = _reviewed_schema(registry, role)
    virtual_relations = {
        name
        for (kind, name, _), sql in reviewed_schema.items()
        if kind == "table"
        and name in expected
        and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
    }
    shadow_relations = {
        f"{name}_{suffix}"
        for name in virtual_relations
        for suffix in ("data", "idx", "content", "docsize", "config")
        if ("table", f"{name}_{suffix}", f"{name}_{suffix}") in reviewed_schema
    }
    actual_rows = tuple(
        (str(row["name"]), str(row["type"]))
        for row in connection.execute(
            """
            SELECT name, type
            FROM sqlite_master
            WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
            ORDER BY name, type
            """
        )
    )
    # FTS5 shadow tables are exact reviewed implementation objects, not
    # separately addressable dataset relations. Every other relation remains
    # subject to the flat one-owner registry check.
    visible_rows = tuple(
        row for row in actual_rows if row[0] not in shadow_relations
    )
    actual = dict(visible_rows)
    if len(actual) != len(visible_rows) or actual != expected:
        raise _health_failure()

    if _schema_objects(connection) != reviewed_schema:
        raise _health_failure()


def _reconcile_migrations(
    connection: sqlite3.Connection,
    registry: Registry,
    role: StoreRole,
) -> tuple[MigrationHealth, ...]:
    declarations = tuple(
        sorted(registry.migrations_for(role.value), key=lambda item: item.ordinal)
    )
    expected = tuple(
        (
            item.id,
            item.store,
            item.ordinal,
            item.resource,
            item.sha256,
            item.reconstruction_state,
        )
        for item in declarations
    )
    actual = tuple(
        (
            str(row["migration_id"]),
            str(row["store_role"]),
            int(row["ordinal"]),
            str(row["resource"]),
            str(row["sha256"]),
            str(row["reconstruction_state"]),
        )
        for row in connection.execute(
            """
            SELECT migration_id, store_role, ordinal, resource, sha256,
                   reconstruction_state
            FROM schema_migrations
            ORDER BY ordinal
            """
        )
    )
    if actual != expected:
        raise _health_failure()
    return tuple(
        MigrationHealth(
            id=item.id,
            ordinal=item.ordinal,
            sha256=item.sha256,
            reconstruction_state=item.reconstruction_state,
        )
        for item in declarations
    )


def _reconcile_datasets(
    connection: sqlite3.Connection,
    registry: Registry,
    role: StoreRole,
) -> tuple[DatasetHealth, ...]:
    declarations = tuple(sorted(registry.datasets_for(role.value), key=lambda item: item.id))
    expected_ids = tuple(item.id for item in declarations)
    registry_ids = tuple(
        str(row["dataset_id"])
        for row in connection.execute("SELECT dataset_id FROM dataset_registry ORDER BY dataset_id")
    )
    identity_ids = tuple(
        str(row["dataset_id"])
        for row in connection.execute(
            "SELECT dataset_id FROM dataset_identity_contracts ORDER BY dataset_id"
        )
    )
    if registry_ids != expected_ids or identity_ids != expected_ids:
        raise _health_failure()

    expected = tuple(
        (
            item.id,
            item.store,
            item.layer,
            item.schema_version,
            dumps_strict(list(item.relations)),
            int(item.active),
            item.identity_sha256,
            item.version,
        )
        for item in declarations
    )
    actual = tuple(
        (
            str(row["dataset_id"]),
            str(row["store_role"]),
            str(row["layer"]),
            str(row["schema_version"]),
            str(row["relations_json"]),
            int(row["active"]),
            str(row["identity_sha256"]),
            str(row["registered_version"]),
        )
        for row in connection.execute(
            """
            SELECT registry.dataset_id, registry.store_role, registry.layer,
                   registry.schema_version, registry.relations_json,
                   registry.active, identities.identity_sha256,
                   identities.registered_version
            FROM dataset_registry AS registry
            JOIN dataset_identity_contracts AS identities
              ON identities.dataset_id = registry.dataset_id
            ORDER BY registry.dataset_id
            """
        )
    )
    if actual != expected:
        raise _health_failure()
    return tuple(
        DatasetHealth(
            id=item.id,
            version=item.version,
            identity_sha256=item.identity_sha256,
        )
        for item in declarations
    )


def inspect_store(
    store_map: StoreMap,
    registry: Registry,
    role: StoreRole | str,
) -> StoreInspection:
    """Read and strictly reconcile one existing operational store.

    Missing, wrong-role, malformed, or tampered stores raise rather than being
    initialized or reported as a partially healthy result.
    """

    normalized = StoreRole(role)
    declaration = registry.store(normalized.value)
    expected_contract_version = f"stage{registry.registry_version.split('.', 1)[0]}"
    with read_connection(
        store_map,
        normalized,
        expected_anchor=declaration.anchor_relation,
    ) as connection:
        anchor_rows = tuple(
            (
                int(row["singleton"]),
                str(row["store_role"]),
                str(row["contract_version"]),
            )
            for row in connection.execute(
                """
                SELECT singleton, store_role, contract_version
                FROM store_metadata
                ORDER BY singleton
                """
            )
        )
        if (
            len(anchor_rows) != 1
            or anchor_rows[0][0] != 1
            or anchor_rows[0][1] != normalized.value
            or anchor_rows[0][2] != expected_contract_version
        ):
            raise _health_failure()

        integrity_rows = tuple(
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        )
        foreign_key_rows = tuple(connection.execute("PRAGMA foreign_key_check"))
        if integrity_rows != ("ok",) or foreign_key_rows:
            raise _health_failure()

        _check_declared_relations(connection, registry, normalized)
        migrations = _reconcile_migrations(connection, registry, normalized)
        datasets = _reconcile_datasets(connection, registry, normalized)

    return StoreInspection(
        role=normalized.value,
        contract_version=anchor_rows[0][2],
        control_relations=tuple(declaration.control_tables),
        migrations=migrations,
        datasets=datasets,
        integrity="ok",
        foreign_key_violations=0,
    )


def inspect_all_stores(store_map: StoreMap, registry: Registry) -> HealthReport:
    """Inspect all four stores in the canonical role order without writing."""

    return HealthReport(
        registry_revision=registry.revision,
        stores=tuple(inspect_store(store_map, registry, role) for role in STORE_ROLES),
    )


def all_store_health(store_map: StoreMap, registry: Registry) -> HealthReport:
    """Semantic name for the four-store health report."""

    return inspect_all_stores(store_map, registry)


# Concise aliases keep operational callers readable without creating a second
# implementation path.
health_all = all_store_health
inspect_all = inspect_all_stores
