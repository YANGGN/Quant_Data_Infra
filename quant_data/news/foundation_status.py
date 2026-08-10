"""Read-only Stage 2 foundation status for the news store."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import StoreUnavailableError
from ..registry import MigrationDeclaration, Registry
from ..stores import StoreMap, StoreRole, read_connection


_COMPATIBLE_REGISTRY_VERSIONS = frozenset({"2.0.0", "2.1.0"})
_EXPECTED_STORE_ROLE = StoreRole.NEWS
_EXPECTED_MIGRATIONS = (
    (
        "news:0001_foundation",
        "521f54561714ba9b3c67dec4b6c9eaf703ec521a8b1250193ffb40c9e9eb1097",
    ),
    (
        "news:0002_control_plane",
        "144a0daf4926eb43a1d59e2aa23d190f2becd235ffa098267f972bc62c7a056b",
    ),
)
_EXPECTED_MIGRATION_IDS = tuple(item[0] for item in _EXPECTED_MIGRATIONS)
_EXPECTED_CONTROL_TABLES = (
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
_EXPECTED_ROW_COUNTS = {
    "store_metadata": 1,
    "schema_migrations": 2,
    "dataset_registry": 0,
    "dataset_identity_contracts": 0,
    "ingestion_runs": 0,
    "ingestion_run_outputs": 0,
    "ingestion_run_failures": 0,
    "ingestion_artifacts": 0,
    "ingestion_snapshots": 0,
    "ingestion_snapshot_artifacts": 0,
    "data_quality_results": 0,
}


def _unavailable(reason: str) -> StoreUnavailableError:
    return StoreUnavailableError(f"News foundation status is unavailable: {reason}")


def _approved_migrations(registry: Registry) -> tuple[MigrationDeclaration, ...]:
    """Return the reviewed Stage 2 declarations or fail before opening SQLite."""

    if registry.registry_version not in _COMPATIBLE_REGISTRY_VERSIONS:
        raise _unavailable("registry version is not compatible with the Stage 2 foundation")
    store = registry.store(_EXPECTED_STORE_ROLE.value)
    if (
        store.id != _EXPECTED_STORE_ROLE.value
        or store.anchor_relation != "store_metadata"
        or store.control_tables != _EXPECTED_CONTROL_TABLES[1:]
        or store.migration_order != _EXPECTED_MIGRATION_IDS
    ):
        raise _unavailable("registry news-store declaration is not the approved foundation")
    if registry.datasets_for(_EXPECTED_STORE_ROLE.value):
        raise _unavailable("registry declares news datasets before the domain stage")

    declarations = tuple(
        sorted(registry.migrations_for(_EXPECTED_STORE_ROLE.value), key=lambda item: item.ordinal)
    )
    if (
        tuple(item.id for item in declarations) != _EXPECTED_MIGRATION_IDS
        or tuple(item.store for item in declarations)
        != (_EXPECTED_STORE_ROLE.value, _EXPECTED_STORE_ROLE.value)
        or tuple(item.ordinal for item in declarations) != (1, 2)
        or tuple((item.id, item.sha256) for item in declarations) != _EXPECTED_MIGRATIONS
    ):
        raise _unavailable("registry news migration declarations are not the approved order")
    return declarations


def _table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row["name"])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    )


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in _EXPECTED_CONTROL_TABLES
    }


def news_foundation_status(store_map: StoreMap, registry: Registry) -> dict[str, Any]:
    """Return a path-free, read-only proof of the empty Stage 2 news foundation.

    The caller supplies the shared host-routed ``StoreMap`` and validated
    registry; this boundary does not accept a database path, connection string,
    or SQL.  It never initializes or migrates a store.
    """

    declarations = _approved_migrations(registry)
    expected_ledger = tuple(
        (item.id, item.store, item.ordinal, item.sha256) for item in declarations
    )
    with read_connection(store_map, _EXPECTED_STORE_ROLE) as connection:
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        if query_only != 1:
            raise _unavailable("read connection is not query-only")

        metadata = tuple(
            (int(row["singleton"]), str(row["store_role"]), str(row["contract_version"]))
            for row in connection.execute(
                "SELECT singleton, store_role, contract_version FROM store_metadata"
            )
        )
        if metadata != ((1, _EXPECTED_STORE_ROLE.value, "stage2"),):
            raise _unavailable("store metadata is not the approved Stage 2 news anchor")

        actual_tables = _table_names(connection)
        if actual_tables != tuple(sorted(_EXPECTED_CONTROL_TABLES)):
            raise _unavailable("store relations are not the exact shared control plane")

        actual_ledger = tuple(
            (
                str(row["migration_id"]),
                str(row["store_role"]),
                int(row["ordinal"]),
                str(row["sha256"]),
            )
            for row in connection.execute(
                """
                SELECT migration_id, store_role, ordinal, sha256
                FROM schema_migrations
                ORDER BY ordinal
                """
            )
        )
        if actual_ledger != expected_ledger:
            raise _unavailable("applied news migrations differ from the approved registry")

        row_counts = _row_counts(connection)
        if row_counts != _EXPECTED_ROW_COUNTS:
            raise _unavailable("news foundation contains unexpected domain or control rows")

        integrity_rows = list(connection.execute("PRAGMA integrity_check"))
        if len(integrity_rows) != 1 or integrity_rows[0][0] != "ok":
            raise _unavailable("integrity check did not pass")
        foreign_key_violations = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_violations:
            raise _unavailable("foreign key check found violations")

    return {
        "contract": "quant_data.news.foundation_status",
        "registry_version": registry.registry_version,
        "store_role": _EXPECTED_STORE_ROLE.value,
        "store_contract_version": "stage2",
        "migrations": [
            {"id": item.id, "sha256": item.sha256} for item in declarations
        ],
        "control_tables": list(_EXPECTED_CONTROL_TABLES),
        "row_counts": row_counts,
        "registered_domain_datasets": 0,
        "integrity_check": "ok",
        "foreign_key_violations": 0,
        "query_only": True,
    }
