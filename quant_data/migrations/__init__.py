"""Immutable, checksum-reconciled store-local migration runner."""

from __future__ import annotations

import sqlite3
import hashlib
from collections.abc import Iterator

from ..errors import MigrationError
from ..registry import MigrationDeclaration, Registry
from ..stores import STORE_ROLES, StoreMap, StoreRole, StoreWriteLock, writer_connection


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    store_role TEXT NOT NULL,
    ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal > 0),
    resource TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    reconstruction_state TEXT NOT NULL CHECK (
        reconstruction_state IN ('unresolved', 'fixture_validated', 'recovered_exact')
    ),
    applied_at TEXT NOT NULL,
    registry_revision TEXT NOT NULL
) STRICT;
"""


_REVIEWED_FTS_MIGRATION = (
    "news:0004_search_index",
    "quant_data/migrations/news/0004_search_index.sql",
)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _applied_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT migration_id, store_role, ordinal, resource, sha256,
                   reconstruction_state, registry_revision
            FROM schema_migrations
            ORDER BY ordinal
            """
        )
    )


def _reconcile_applied(
    connection: sqlite3.Connection,
    role: StoreRole,
    declarations: tuple[MigrationDeclaration, ...],
) -> int:
    rows = _applied_rows(connection)
    if len(rows) > len(declarations):
        raise MigrationError("Store contains an unexpected migration")
    for row, declaration in zip(rows, declarations):
        expected = (
            declaration.id,
            declaration.store,
            declaration.ordinal,
            declaration.resource,
            declaration.sha256,
            declaration.reconstruction_state,
        )
        actual = (
            row["migration_id"],
            row["store_role"],
            row["ordinal"],
            row["resource"],
            row["sha256"],
            row["reconstruction_state"],
        )
        if actual != expected or row["store_role"] != role.value:
            raise MigrationError("Applied migration does not match the reviewed registry")
    return len(rows)


def _load_reviewed_resources(
    registry: Registry,
    declarations: tuple[MigrationDeclaration, ...],
) -> dict[str, str]:
    """Read and verify every declared resource on every migration invocation."""

    result: dict[str, str] = {}
    for declaration in declarations:
        try:
            resource_path = (registry.project_root / declaration.resource).resolve(strict=True)
            resource_path.relative_to(registry.project_root)
            resource_bytes = resource_path.read_bytes()
            sql = resource_bytes.decode("utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            raise MigrationError("Migration resource is unavailable or not UTF-8") from exc
        if hashlib.sha256(resource_bytes).hexdigest() != declaration.sha256:
            raise MigrationError("Migration resource changed after registry validation")
        result[declaration.id] = sql
    return result


def _sql_statements(sql: str) -> Iterator[str]:
    """Split one SQLite script without executing multiple statements at once."""

    buffer: list[str] = []
    for character in sql:
        buffer.append(character)
        if character == ";" and sqlite3.complete_statement("".join(buffer)):
            statement = "".join(buffer).strip()
            if statement:
                yield statement
            buffer.clear()
    remainder = "".join(buffer).strip()
    if remainder:
        completed = remainder + ";"
        if not sqlite3.complete_statement(completed):
            raise MigrationError("Migration resource ends with incomplete SQL")
        yield completed


def _migration_authorizer(
    action: int,
    first: str | None,
    second: str | None,
    database: str | None,
    trigger: str | None,
    *,
    allow_fts_data_version: bool = False,
) -> int:
    del database, trigger
    if action == sqlite3.SQLITE_PRAGMA:
        # FTS5 reads SQLite's data-version counter while constructing a
        # virtual table. Permit that one read only for the exact reviewed
        # search-index migration; assignments and every other PRAGMA remain
        # denied.
        if allow_fts_data_version and first == "data_version" and second is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    forbidden_actions = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
    }
    if action in forbidden_actions:
        return sqlite3.SQLITE_DENY
    if first == "schema_migrations" or second == "schema_migrations":
        # Every foundation resource repeats the reviewed IF-NOT-EXISTS ledger
        # declaration.  It is harmless because the runner creates the exact
        # table first; all mutation actions against the ledger remain denied.
        if action not in {
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_READ,
        }:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _reviewed_migration_authorizer(
    declaration: MigrationDeclaration,
):
    allow_fts_data_version = (
        declaration.id,
        declaration.resource,
    ) == _REVIEWED_FTS_MIGRATION

    def authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        return _migration_authorizer(
            action,
            first,
            second,
            database,
            trigger,
            allow_fts_data_version=allow_fts_data_version,
        )

    return authorize


def _apply_one(
    connection: sqlite3.Connection,
    declaration: MigrationDeclaration,
    registry: Registry,
    applied_at: str,
    sql: str,
) -> None:
    # SQLite table-rebuild migrations need foreign-key enforcement disabled
    # outside the transaction. Every resource is still atomic, and a complete
    # foreign-key check is mandatory before its ledger row can commit.
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.set_authorizer(_reviewed_migration_authorizer(declaration))
        for statement in _sql_statements(sql):
            connection.execute(statement)
        connection.set_authorizer(None)
        if tuple(connection.execute("PRAGMA foreign_key_check")):
            raise MigrationError("Migration leaves foreign-key violations")
        connection.execute(
            """
            INSERT INTO schema_migrations (
                migration_id, store_role, ordinal, resource, sha256,
                reconstruction_state, applied_at, registry_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                declaration.id,
                declaration.store,
                declaration.ordinal,
                declaration.resource,
                declaration.sha256,
                declaration.reconstruction_state,
                applied_at,
                registry.revision,
            ),
        )
        connection.commit()
    except (sqlite3.Error, MigrationError) as exc:
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.rollback()
        raise MigrationError(f"Migration {declaration.id} failed atomically") from exc
    finally:
        connection.set_authorizer(None)
        connection.execute("PRAGMA foreign_keys=ON")


def migrate_store(
    store_map: StoreMap,
    registry: Registry,
    role: StoreRole | str,
    *,
    applied_at: str,
) -> tuple[str, ...]:
    normalized = StoreRole(role)
    declarations = tuple(
        sorted(registry.migrations_for(normalized.value), key=lambda item: item.ordinal)
    )
    if tuple(item.id for item in declarations) != registry.store(normalized.value).migration_order:
        raise MigrationError("Migration declarations are not in reviewed store order")
    reviewed_sql = _load_reviewed_resources(registry, declarations)
    path = store_map.path(normalized)
    with StoreWriteLock(path), writer_connection(
        store_map, normalized, create_parent=True
    ) as connection:
        connection.execute(_LEDGER_DDL)
        connection.commit()
        applied_count = _reconcile_applied(connection, normalized, declarations)
        for declaration in declarations[applied_count:]:
            _apply_one(
                connection,
                declaration,
                registry,
                applied_at,
                reviewed_sql[declaration.id],
            )
        role_row = connection.execute(
            "SELECT store_role FROM store_metadata WHERE singleton=1"
        ).fetchone()
        if role_row is None or role_row["store_role"] != normalized.value:
            raise MigrationError("Store role anchor is missing or incorrect")
        return tuple(item.id for item in declarations)


def _register_datasets(
    store_map: StoreMap,
    registry: Registry,
    role: StoreRole,
    *,
    registered_at: str,
) -> None:
    declarations = registry.datasets_for(role.value)
    with StoreWriteLock(store_map.path(role)), writer_connection(
        store_map, role
    ) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for declaration in declarations:
                for relation in declaration.relations:
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
                        (relation,),
                    ).fetchone()
                    if exists is None:
                        raise MigrationError("Registry dataset relation is absent from its store")
                existing = connection.execute(
                    """
                    SELECT store_role, layer, schema_version, relations_json, active
                    FROM dataset_registry WHERE dataset_id=?
                    """,
                    (declaration.id,),
                ).fetchone()
                relations_json = "[" + ",".join(
                    _quote_literal(relation)[1:-1].join(('"', '"'))
                    for relation in declaration.relations
                ) + "]"
                # Relation identifiers contain only the registry-safe identifier
                # grammar, so this compact JSON representation is deterministic.
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO dataset_registry (
                            dataset_id, store_role, layer, schema_version,
                            relations_json, active, registered_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            declaration.id,
                            declaration.store,
                            declaration.layer,
                            declaration.schema_version,
                            relations_json,
                            int(declaration.active),
                            registered_at,
                        ),
                    )
                else:
                    actual = tuple(existing[:5])
                    expected = (
                        declaration.store,
                        declaration.layer,
                        declaration.schema_version,
                        relations_json,
                        int(declaration.active),
                    )
                    if actual != expected:
                        raise MigrationError("Store-local dataset declaration conflicts with registry")
                stored_identity = connection.execute(
                    """
                    SELECT identity_sha256, registered_version
                    FROM dataset_identity_contracts WHERE dataset_id=?
                    """,
                    (declaration.id,),
                ).fetchone()
                if stored_identity is None:
                    connection.execute(
                        """
                        INSERT INTO dataset_identity_contracts (
                            dataset_id, identity_sha256, registered_version
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            declaration.id,
                            declaration.identity_sha256,
                            declaration.version,
                        ),
                    )
                elif stored_identity["identity_sha256"] != declaration.identity_sha256:
                    raise MigrationError(
                        "Store-local dataset identity conflicts with registry"
                    )
            registered_ids = {
                str(row["dataset_id"])
                for row in connection.execute("SELECT dataset_id FROM dataset_registry")
            }
            expected_ids = {declaration.id for declaration in declarations}
            if registered_ids != expected_ids:
                raise MigrationError(
                    "Store-local dataset inventory does not match the registry"
                )
            identity_ids = {
                str(row["dataset_id"])
                for row in connection.execute(
                    "SELECT dataset_id FROM dataset_identity_contracts"
                )
            }
            if identity_ids != expected_ids:
                raise MigrationError(
                    "Store-local dataset identity inventory does not match the registry"
                )
            store = registry.store(role.value)
            actual_relation_rows = tuple(
                connection.execute(
                    """
                    SELECT name, type, sql FROM sqlite_master
                    WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
                    """
                )
            )
            declared_dataset_relations = {
                relation
                for declaration in declarations
                for relation in declaration.relations
            }
            virtual_relations = {
                str(row["name"])
                for row in actual_relation_rows
                if str(row["name"]) in declared_dataset_relations
                and row["sql"] is not None
                and str(row["sql"]).lstrip().upper().startswith(
                    "CREATE VIRTUAL TABLE"
                )
            }
            actual_names = {str(row["name"]) for row in actual_relation_rows}
            shadow_relations = {
                f"{name}_{suffix}"
                for name in virtual_relations
                for suffix in ("data", "idx", "content", "docsize", "config")
                if f"{name}_{suffix}" in actual_names
            }
            # Reviewed FTS5 shadow tables implement one registered virtual
            # relation; they are not separately addressable dataset relations.
            # No other physical relation is excluded from exact ownership.
            actual_relations = actual_names - shadow_relations
            expected_relations = {
                store.anchor_relation,
                *store.control_tables,
                *declared_dataset_relations,
            }
            if actual_relations != expected_relations:
                raise MigrationError(
                    "Store relation ownership does not match the registry"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def initialize_all(
    store_map: StoreMap,
    registry: Registry,
    *,
    applied_at: str = "2026-08-09T12:00:00-04:00",
) -> dict[str, tuple[str, ...]]:
    """Initialize all four explicit stores; never consult default paths."""

    store_map.validate_distinct()
    result: dict[str, tuple[str, ...]] = {}
    for role in STORE_ROLES:
        result[role.value] = migrate_store(
            store_map, registry, role, applied_at=applied_at
        )
    for role in STORE_ROLES:
        _register_datasets(
            store_map, registry, role, registered_at=applied_at
        )
    return result
