-- Deliberate Stage 3 reconstruction of GDP vintage provenance.

CREATE TABLE gdp_vintages (
    vintage_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_vintage_identity TEXT NOT NULL,
    release_stage TEXT,
    vintage_at TEXT NOT NULL,
    vintage_precision TEXT NOT NULL CHECK (
        vintage_precision IN ('date', 'datetime')
    ),
    source_published_at TEXT,
    source_published_precision TEXT CHECK (
        source_published_precision IS NULL
        OR source_published_precision IN ('date', 'datetime', 'unknown')
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    artifact_id TEXT REFERENCES macro_source_artifacts(artifact_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT REFERENCES macro_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (source, source_vintage_identity)
) STRICT;

CREATE INDEX gdp_vintages_selection
ON gdp_vintages(source, vintage_at, available_at, source_vintage_identity);

CREATE TRIGGER gdp_vintages_insert_guard
BEFORE INSERT ON gdp_vintages
WHEN EXISTS (
    SELECT 1
    FROM gdp_vintages
    WHERE vintage_id = NEW.vintage_id
       OR (
            source = NEW.source
            AND source_vintage_identity = NEW.source_vintage_identity
       )
)
BEGIN
    SELECT RAISE(ABORT, 'GDP vintage identity is immutable');
END;

CREATE TRIGGER gdp_vintages_lineage_guard
BEFORE INSERT ON gdp_vintages
WHEN (
    NEW.artifact_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1
        FROM macro_source_artifacts
        WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
    )
)
 OR (
    NEW.source_snapshot_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1
        FROM macro_source_snapshots
        WHERE snapshot_id = NEW.source_snapshot_id AND run_id = NEW.run_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'GDP vintage provenance mismatch');
END;

CREATE TRIGGER gdp_vintages_immutable_update
BEFORE UPDATE ON gdp_vintages
BEGIN
    SELECT RAISE(ABORT, 'GDP vintages are immutable');
END;

CREATE TRIGGER gdp_vintages_immutable_delete
BEFORE DELETE ON gdp_vintages
BEGIN
    SELECT RAISE(ABORT, 'GDP vintages are immutable');
END;
