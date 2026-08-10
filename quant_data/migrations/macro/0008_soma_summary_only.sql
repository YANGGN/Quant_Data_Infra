-- Deliberate Stage 3 reconstruction for aggregate-only SOMA summaries.

CREATE TABLE soma_source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    request_scope_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE soma_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    as_of_date TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    source_published_at TEXT,
    source_published_precision TEXT CHECK (
        source_published_precision IS NULL
        OR source_published_precision IN ('date', 'datetime', 'unknown')
    ),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE soma_snapshot_artifacts (
    snapshot_id TEXT NOT NULL REFERENCES soma_snapshots(snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES soma_source_artifacts(artifact_id),
    artifact_ordinal INTEGER NOT NULL CHECK (artifact_ordinal > 0),
    PRIMARY KEY (snapshot_id, artifact_id),
    UNIQUE (snapshot_id, artifact_ordinal)
) STRICT;

CREATE TABLE soma_summary_components (
    component_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES soma_snapshots(snapshot_id),
    as_of_date TEXT NOT NULL,
    category TEXT NOT NULL,
    measure TEXT NOT NULL,
    value_text TEXT,
    missing_reason TEXT,
    unit TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK ((value_text IS NULL) <> (missing_reason IS NULL)),
    UNIQUE (snapshot_id, category, measure)
) STRICT;

CREATE INDEX soma_summary_components_selection
ON soma_summary_components(as_of_date, category, measure, available_at);

CREATE TRIGGER soma_source_artifacts_insert_guard
BEFORE INSERT ON soma_source_artifacts
WHEN EXISTS (
    SELECT 1 FROM soma_source_artifacts WHERE artifact_id = NEW.artifact_id
)
BEGIN
    SELECT RAISE(ABORT, 'SOMA source artifact identity is immutable');
END;

CREATE TRIGGER soma_source_artifacts_immutable_update
BEFORE UPDATE ON soma_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SOMA source artifacts are immutable');
END;

CREATE TRIGGER soma_source_artifacts_immutable_delete
BEFORE DELETE ON soma_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SOMA source artifacts are immutable');
END;

CREATE TRIGGER soma_snapshots_insert_guard
BEFORE INSERT ON soma_snapshots
WHEN EXISTS (
    SELECT 1
    FROM soma_snapshots
    WHERE snapshot_id = NEW.snapshot_id
       OR semantic_identity = NEW.semantic_identity
       OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshot identity is immutable');
END;

CREATE TRIGGER soma_snapshots_immutable_update
BEFORE UPDATE ON soma_snapshots
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshots are immutable');
END;

CREATE TRIGGER soma_snapshots_immutable_delete
BEFORE DELETE ON soma_snapshots
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshots are immutable');
END;

CREATE TRIGGER soma_snapshot_artifacts_insert_guard
BEFORE INSERT ON soma_snapshot_artifacts
WHEN EXISTS (
    SELECT 1
    FROM soma_snapshot_artifacts
    WHERE (snapshot_id = NEW.snapshot_id AND artifact_id = NEW.artifact_id)
       OR (snapshot_id = NEW.snapshot_id AND artifact_ordinal = NEW.artifact_ordinal)
)
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshot artifact membership is immutable');
END;

CREATE TRIGGER soma_snapshot_artifacts_lineage_guard
BEFORE INSERT ON soma_snapshot_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM soma_snapshots AS snapshot
    JOIN soma_source_artifacts AS artifact ON artifact.artifact_id = NEW.artifact_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.run_id = artifact.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshot artifact run mismatch');
END;

CREATE TRIGGER soma_snapshot_artifacts_immutable_update
BEFORE UPDATE ON soma_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshot artifact membership is immutable');
END;

CREATE TRIGGER soma_snapshot_artifacts_immutable_delete
BEFORE DELETE ON soma_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SOMA snapshot artifact membership is immutable');
END;

CREATE TRIGGER soma_summary_components_insert_guard
BEFORE INSERT ON soma_summary_components
WHEN EXISTS (
    SELECT 1
    FROM soma_summary_components
    WHERE component_id = NEW.component_id
       OR (
            snapshot_id = NEW.snapshot_id
            AND category = NEW.category
            AND measure = NEW.measure
       )
)
BEGIN
    SELECT RAISE(ABORT, 'SOMA summary component is immutable');
END;

CREATE TRIGGER soma_summary_components_immutable_update
BEFORE UPDATE ON soma_summary_components
BEGIN
    SELECT RAISE(ABORT, 'SOMA summary components are immutable');
END;

CREATE TRIGGER soma_summary_components_immutable_delete
BEFORE DELETE ON soma_summary_components
BEGIN
    SELECT RAISE(ABORT, 'SOMA summary components are immutable');
END;
