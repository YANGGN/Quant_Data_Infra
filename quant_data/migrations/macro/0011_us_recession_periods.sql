-- Deliberate Stage 3 reconstruction of completed U.S. recession chronology.

CREATE TABLE us_recession_periods (
    recession_period_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    peak_month TEXT NOT NULL,
    trough_month TEXT,
    status TEXT NOT NULL CHECK (status IN ('completed', 'ongoing')),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    artifact_id TEXT REFERENCES macro_source_artifacts(artifact_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT REFERENCES macro_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    CHECK (trough_month IS NULL OR peak_month <= trough_month),
    CHECK (status != 'completed' OR trough_month IS NOT NULL),
    UNIQUE (provider, peak_month, trough_month)
) STRICT;

CREATE INDEX us_recession_periods_selection
ON us_recession_periods(peak_month, trough_month, available_at);

CREATE TRIGGER us_recession_periods_insert_guard
BEFORE INSERT ON us_recession_periods
WHEN EXISTS (
    SELECT 1
    FROM us_recession_periods
    WHERE recession_period_id = NEW.recession_period_id
       OR (
            provider = NEW.provider
            AND peak_month = NEW.peak_month
            AND COALESCE(trough_month, '') = COALESCE(NEW.trough_month, '')
       )
)
BEGIN
    SELECT RAISE(ABORT, 'U.S. recession period identity is immutable');
END;

CREATE TRIGGER us_recession_periods_lineage_guard
BEFORE INSERT ON us_recession_periods
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
    SELECT RAISE(ABORT, 'U.S. recession period provenance mismatch');
END;

CREATE TRIGGER us_recession_periods_immutable_update
BEFORE UPDATE ON us_recession_periods
BEGIN
    SELECT RAISE(ABORT, 'U.S. recession periods are immutable');
END;

CREATE TRIGGER us_recession_periods_immutable_delete
BEFORE DELETE ON us_recession_periods
BEGIN
    SELECT RAISE(ABORT, 'U.S. recession periods are immutable');
END;
