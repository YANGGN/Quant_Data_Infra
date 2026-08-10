"""Canonical logical store fingerprints for read-mutation and rebuild tests."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from .errors import ValidationError
from .json_codec import dumps_strict
from .registry import Registry
from .stores import STORE_ROLES, StoreMap, StoreRole, read_connection


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_RELATIONS = (
    "store_metadata",
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
_EXCLUDED_COLUMNS = {"applied_at", "registered_at", "started_at", "completed_at"}


def _table_rows(
    connection: sqlite3.Connection,
    relation: str,
    *,
    exclude_volatile: bool = True,
) -> dict[str, Any]:
    if not _IDENTIFIER.fullmatch(relation):
        raise ValidationError("Unsafe relation in fingerprint contract")
    columns = [
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({relation})")
        if not exclude_volatile or row["name"] not in _EXCLUDED_COLUMNS
    ]
    if not columns:
        return {"columns": [], "rows": []}
    select = ", ".join(f'"{column}"' for column in columns)
    values = [
        {column: row[column] for column in columns}
        for row in connection.execute(f'SELECT {select} FROM "{relation}"')
    ]
    values.sort(key=dumps_strict)
    return {"columns": columns, "rows": values}


def store_logical_manifest(
    store_map: StoreMap,
    registry: Registry,
    role: StoreRole | str,
) -> dict[str, Any]:
    normalized = StoreRole(role)
    relations = list(_CONTROL_RELATIONS)
    for dataset in registry.datasets_for(normalized.value):
        relations.extend(dataset.relations)
    relations = sorted(dict.fromkeys(relations))
    with read_connection(store_map, normalized) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if integrity != "ok" or foreign_keys:
            raise ValidationError("Store integrity check failed")
        relation_data = {
            relation: _table_rows(connection, relation) for relation in relations
        }
        schema_rows = [
            {"type": row["type"], "name": row["name"], "sql": row["sql"]}
            for row in connection.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE type IN ('table', 'view', 'trigger')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
    payload = {
        "role": normalized.value,
        "schema": schema_rows,
        "relations": relation_data,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(dumps_strict(payload).encode("utf-8")).hexdigest(),
    }


def logical_manifest(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    stores = {
        role.value: store_logical_manifest(store_map, registry, role)
        for role in STORE_ROLES
    }
    registry_declarations = dict(registry.raw)
    payload = {
        "registry": registry_declarations,
        "stores": stores,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(dumps_strict(payload).encode("utf-8")).hexdigest(),
    }


def store_mutation_fingerprint(
    store_map: StoreMap,
    role: StoreRole | str,
) -> dict[str, Any]:
    """Fingerprint every persistent store field without exclusions."""

    normalized = StoreRole(role)
    with read_connection(store_map, normalized) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if integrity != "ok" or foreign_keys:
            raise ValidationError("Store integrity check failed")
        table_names = [
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        relations = {
            name: _table_rows(connection, name, exclude_volatile=False)
            for name in table_names
        }
        schema_rows = [
            {"type": row["type"], "name": row["name"], "sql": row["sql"]}
            for row in connection.execute(
                """
                SELECT type, name, sql FROM sqlite_master
                WHERE type IN ('table', 'view', 'index', 'trigger')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
        header = {
            "application_id": int(connection.execute("PRAGMA application_id").fetchone()[0]),
            "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        }
    payload = {
        "role": normalized.value,
        "header": header,
        "schema": schema_rows,
        "relations": relations,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(dumps_strict(payload).encode("utf-8")).hexdigest(),
    }


def mutation_fingerprint(store_map: StoreMap) -> dict[str, Any]:
    """Fingerprint all four stores for public no-write assertions."""

    stores = {
        role.value: store_mutation_fingerprint(store_map, role)
        for role in STORE_ROLES
    }
    payload = {"stores": stores}
    return {
        **payload,
        "sha256": hashlib.sha256(dumps_strict(payload).encode("utf-8")).hexdigest(),
    }
