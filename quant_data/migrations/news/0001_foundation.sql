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

CREATE TABLE store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    store_role TEXT NOT NULL UNIQUE CHECK (store_role = 'news'),
    contract_version TEXT NOT NULL
) STRICT;

INSERT INTO store_metadata (singleton, store_role, contract_version)
VALUES (1, 'news', 'stage1');

CREATE TABLE dataset_registry (
    dataset_id TEXT PRIMARY KEY,
    store_role TEXT NOT NULL CHECK (store_role = 'news'),
    layer TEXT NOT NULL CHECK (layer IN ('evidence', 'canonical', 'research')),
    schema_version TEXT NOT NULL,
    relations_json TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    registered_at TEXT NOT NULL,
    last_successful_run_id TEXT,
    last_semantic_identity TEXT
) STRICT;

CREATE TABLE ingestion_runs (
    run_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    semantic_identity TEXT NOT NULL,
    command TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    artifact_id TEXT,
    snapshot_id TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0 CHECK (fetched_count >= 0),
    written_count INTEGER NOT NULL DEFAULT 0 CHECK (written_count >= 0),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    code_version TEXT NOT NULL,
    UNIQUE (dataset_id, semantic_identity)
) STRICT;

CREATE INDEX ingestion_runs_dataset_completion
ON ingestion_runs(dataset_id, completed_at);
