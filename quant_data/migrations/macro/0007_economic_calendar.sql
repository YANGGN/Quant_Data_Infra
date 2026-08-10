-- Deliberate Stage 3 reconstruction of versioned economic-calendar events.

CREATE TABLE economic_calendar (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_at TEXT NOT NULL,
    event_precision TEXT NOT NULL CHECK (event_precision IN ('date', 'datetime')),
    country TEXT NOT NULL,
    name TEXT NOT NULL,
    series_id TEXT REFERENCES macro_series(series_id),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, provider_event_id)
) STRICT;

CREATE TABLE economic_calendar_event_versions (
    event_version_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES economic_calendar(event_id),
    actual_value TEXT,
    consensus_value TEXT,
    previous_value TEXT,
    unit TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_event_version_id TEXT
        REFERENCES economic_calendar_event_versions(event_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    source_snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots(snapshot_id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'tombstone')),
    CHECK (
        state = 'active'
        OR (actual_value IS NULL AND consensus_value IS NULL AND previous_value IS NULL)
    ),
    UNIQUE (event_id, correction_sequence)
) STRICT;

CREATE INDEX economic_calendar_events_selection
ON economic_calendar(country, event_at, provider, name);

CREATE INDEX economic_calendar_event_versions_selection
ON economic_calendar_event_versions(event_id, available_at, correction_sequence);

CREATE TRIGGER economic_calendar_insert_guard
BEFORE INSERT ON economic_calendar
WHEN EXISTS (
    SELECT 1
    FROM economic_calendar
    WHERE event_id = NEW.event_id
       OR (provider = NEW.provider AND provider_event_id = NEW.provider_event_id)
)
BEGIN
    SELECT RAISE(ABORT, 'economic calendar event identity is immutable');
END;

CREATE TRIGGER economic_calendar_identity_immutable_update
BEFORE UPDATE OF event_id, provider, provider_event_id, created_run_id
ON economic_calendar
BEGIN
    SELECT RAISE(ABORT, 'economic calendar event identity is immutable');
END;

CREATE TRIGGER economic_calendar_immutable_delete
BEFORE DELETE ON economic_calendar
BEGIN
    SELECT RAISE(ABORT, 'economic calendar events must be retired, not deleted');
END;

CREATE TRIGGER economic_calendar_event_versions_insert_guard
BEFORE INSERT ON economic_calendar_event_versions
WHEN EXISTS (
    SELECT 1
    FROM economic_calendar_event_versions
    WHERE event_version_id = NEW.event_version_id
       OR (event_id = NEW.event_id AND correction_sequence = NEW.correction_sequence)
)
BEGIN
    SELECT RAISE(ABORT, 'economic calendar event version is immutable');
END;

CREATE TRIGGER economic_calendar_event_versions_initial_sequence_guard
BEFORE INSERT ON economic_calendar_event_versions
WHEN NEW.supersedes_event_version_id IS NULL AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial calendar correction sequence must be one');
END;

CREATE TRIGGER economic_calendar_event_versions_supersession_guard
BEFORE INSERT ON economic_calendar_event_versions
WHEN NEW.supersedes_event_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM economic_calendar_event_versions AS previous
        WHERE previous.event_version_id = NEW.supersedes_event_version_id
          AND previous.event_id = NEW.event_id
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid economic calendar supersession') END;
END;

CREATE TRIGGER economic_calendar_event_versions_lineage_guard
BEFORE INSERT ON economic_calendar_event_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id AND run_id = NEW.run_id
)
 OR NOT EXISTS (
    SELECT 1
    FROM macro_source_artifacts
    WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'economic calendar provenance mismatch');
END;

CREATE TRIGGER economic_calendar_event_versions_immutable_update
BEFORE UPDATE ON economic_calendar_event_versions
BEGIN
    SELECT RAISE(ABORT, 'economic calendar event versions are immutable');
END;

CREATE TRIGGER economic_calendar_event_versions_immutable_delete
BEFORE DELETE ON economic_calendar_event_versions
BEGIN
    SELECT RAISE(ABORT, 'economic calendar event versions are immutable');
END;
