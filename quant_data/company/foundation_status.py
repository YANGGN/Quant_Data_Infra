"""Read-only evidence for the empty Stage 2 company-store foundation."""

from __future__ import annotations

from typing import Final

from ..errors import ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection


_REGISTRY_VERSION: Final = "2.0.0"
_COMPANY_MIGRATION_IDS: Final = (
    "company:0001_foundation",
    "company:0002_control_plane",
)
_CONTROL_TABLES: Final = (
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
_EMPTY_DATA_TABLES: Final = (
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
_CONTRACT_VERSION: Final = "stage2"


def _expected_ledger(registry: Registry) -> tuple[tuple[int, str, str, str], ...]:
    """Resolve the frozen company migration contract from the validated registry."""

    if registry.registry_version != _REGISTRY_VERSION:
        raise ValidationError("Company foundation status requires registry version 2.0.0")
    declaration = registry.store(StoreRole.COMPANY.value)
    if (
        declaration.id != StoreRole.COMPANY.value
        or declaration.anchor_relation != "store_metadata"
        or declaration.migration_order != _COMPANY_MIGRATION_IDS
        or (declaration.anchor_relation, *declaration.control_tables) != _CONTROL_TABLES
    ):
        raise ValidationError("Company registry declaration violates the Stage 2 foundation")

    migrations = tuple(
        sorted(registry.migrations_for(StoreRole.COMPANY.value), key=lambda item: item.ordinal)
    )
    ledger = tuple(
        (migration.ordinal, migration.id, migration.store, migration.sha256)
        for migration in migrations
    )
    if (
        tuple(item[0] for item in ledger) != (1, 2)
        or tuple(item[1] for item in ledger) != _COMPANY_MIGRATION_IDS
        or tuple(item[2] for item in ledger) != ("company", "company")
    ):
        raise ValidationError("Company migration declarations violate the Stage 2 foundation")
    return ledger


def company_foundation_status(store_map: StoreMap, registry: Registry) -> dict[str, object]:
    """Return deterministic, path-free proof of an empty Stage 2 company store.

    This adapter is intentionally limited to host-routed read access.  It does
    not initialize stores, accept database paths or SQL, or expose company
    facts that belong to the later company-domain restoration stage.
    """

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Company foundation status requires a StoreMap")
    if not isinstance(registry, Registry):
        raise ValidationError("Company foundation status requires a validated registry")
    expected_ledger = _expected_ledger(registry)

    with read_connection(
        store_map,
        StoreRole.COMPANY,
        expected_anchor="store_metadata",
    ) as connection:
        metadata = connection.execute(
            """
            SELECT store_role, contract_version
            FROM store_metadata
            WHERE singleton=1
            """
        ).fetchone()
        if (
            metadata is None
            or metadata["store_role"] != StoreRole.COMPANY.value
            or metadata["contract_version"] != _CONTRACT_VERSION
        ):
            raise ValidationError("Company store metadata violates the Stage 2 foundation")

        relation_rows = list(
            connection.execute(
                """
                SELECT type, name
                FROM sqlite_master
                WHERE type IN ('table', 'view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        )
        table_names = {str(row["name"]) for row in relation_rows if row["type"] == "table"}
        view_names = {str(row["name"]) for row in relation_rows if row["type"] == "view"}
        if table_names != set(_CONTROL_TABLES) or view_names:
            raise ValidationError("Company store relations violate the Stage 2 foundation")

        actual_ledger = tuple(
            (
                int(row["ordinal"]),
                str(row["migration_id"]),
                str(row["store_role"]),
                str(row["sha256"]),
            )
            for row in connection.execute(
                """
                SELECT ordinal, migration_id, store_role, sha256
                FROM schema_migrations
                ORDER BY ordinal
                """
            )
        )
        if actual_ledger != expected_ledger:
            raise ValidationError("Company migration ledger violates the Stage 2 foundation")

        empty_table_row_counts = {
            relation: int(
                connection.execute(f'SELECT COUNT(*) FROM "{relation}"').fetchone()[0]
            )
            for relation in _EMPTY_DATA_TABLES
        }
        if any(empty_table_row_counts.values()):
            raise ValidationError("Company store contains data before the domain restoration stage")

        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = list(connection.execute("PRAGMA foreign_key_check"))

    if query_only != 1:
        raise ValidationError("Company foundation status reader is not query-only")
    if integrity != "ok" or foreign_key_violations:
        raise ValidationError("Company store integrity checks failed")

    return {
        "store_role": StoreRole.COMPANY.value,
        "registry_version": registry.registry_version,
        "contract_version": _CONTRACT_VERSION,
        "migrations": [
            {"id": migration_id, "sha256": sha256}
            for _, migration_id, _, sha256 in expected_ledger
        ],
        "control_tables": list(_CONTROL_TABLES),
        "empty_table_row_counts": empty_table_row_counts,
        "integrity": integrity,
        "foreign_key_violations": 0,
        "query_only": True,
    }
