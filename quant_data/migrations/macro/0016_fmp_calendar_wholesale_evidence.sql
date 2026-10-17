-- Immutable wholesale evidence for the FMP U.S.-scoped economic calendar.
-- The exact provider response remains the source authority.  Row projections
-- deliberately retain every returned object (including unknown labels and
-- unexpected countries) so reviewed aliases can be replayed locally later.

CREATE TABLE fmp_economic_calendar_captures (
    capture_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    source_resource TEXT NOT NULL CHECK (
        source_resource = 'fmp_stable_economic_calendar'
    ),
    request_country TEXT NOT NULL CHECK (request_country = 'US'),
    request_start_date TEXT NOT NULL CHECK (
        length(request_start_date) = 10
        AND request_start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
    ),
    request_end_date TEXT NOT NULL CHECK (
        length(request_end_date) = 10
        AND request_end_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
    ),
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64
        AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (
        length(response_bytes) BETWEEN 2 AND 1048576
    ),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'fmp_us_calendar_wholesale_evidence_v1'
    ),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 0 AND 2000),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    artifact_id TEXT NOT NULL UNIQUE
        REFERENCES ingestion_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
    snapshot_id TEXT NOT NULL UNIQUE
        REFERENCES ingestion_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (
        request_country, request_start_date, request_end_date, semantic_identity
    ),
    CHECK (request_start_date <= request_end_date)
) STRICT;

CREATE INDEX fmp_economic_calendar_captures_window
ON fmp_economic_calendar_captures(
    request_country, request_start_date, request_end_date, captured_at DESC
);

CREATE TABLE fmp_economic_calendar_rows (
    capture_id TEXT NOT NULL
        REFERENCES fmp_economic_calendar_captures(capture_id),
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 1 AND 2000),
    row_sha256 TEXT NOT NULL CHECK (
        length(row_sha256) = 64
        AND row_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    raw_row_json TEXT NOT NULL CHECK (length(raw_row_json) > 1),
    event_at TEXT NOT NULL CHECK (length(event_at) BETWEEN 1 AND 128),
    country TEXT NOT NULL CHECK (length(country) BETWEEN 1 AND 64),
    event_name TEXT NOT NULL CHECK (length(event_name) BETWEEN 1 AND 512),
    currency TEXT,
    unit TEXT,
    previous_json TEXT,
    estimate_json TEXT,
    actual_json TEXT,
    change_json TEXT,
    impact_json TEXT,
    change_percentage_json TEXT,
    PRIMARY KEY (capture_id, source_row)
) STRICT;

CREATE INDEX fmp_economic_calendar_rows_event
ON fmp_economic_calendar_rows(country, event_at, event_name);

CREATE TRIGGER fmp_economic_calendar_captures_immutable_update
BEFORE UPDATE ON fmp_economic_calendar_captures
BEGIN
    SELECT RAISE(ABORT, 'FMP wholesale calendar captures are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_captures_immutable_delete
BEFORE DELETE ON fmp_economic_calendar_captures
BEGIN
    SELECT RAISE(ABORT, 'FMP wholesale calendar captures are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_rows_capture_membership
BEFORE INSERT ON fmp_economic_calendar_rows
WHEN NEW.source_row > (
    SELECT row_count
    FROM fmp_economic_calendar_captures
    WHERE capture_id = NEW.capture_id
)
BEGIN
    SELECT RAISE(ABORT, 'FMP wholesale calendar row exceeds capture membership');
END;

CREATE TRIGGER fmp_economic_calendar_rows_immutable_update
BEFORE UPDATE ON fmp_economic_calendar_rows
BEGIN
    SELECT RAISE(ABORT, 'FMP wholesale calendar rows are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_rows_immutable_delete
BEFORE DELETE ON fmp_economic_calendar_rows
BEGIN
    SELECT RAISE(ABORT, 'FMP wholesale calendar rows are immutable');
END;

-- IngestionCoordinator inserts generic artifacts before the snapshot.  This
-- is the final atomic lineage/membership gate: every wholesale capture must
-- be completely represented by contiguous source rows before it can receive
-- a successful generic snapshot.
CREATE TRIGGER fmp_economic_calendar_wholesale_snapshot_lineage
BEFORE INSERT ON ingestion_snapshots
WHEN NEW.dataset_id = 'macro.fmp.economic_calendar_evidence'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fmp_economic_calendar_captures AS capture
        JOIN ingestion_artifacts AS artifact
          ON artifact.artifact_id = capture.artifact_id
        JOIN ingestion_runs AS run
          ON run.run_id = capture.run_id
        JOIN ingestion_run_outputs AS output
          ON output.run_id = run.run_id
         AND output.dataset_id = 'macro.fmp.economic_calendar_evidence'
        WHERE capture.snapshot_id = NEW.snapshot_id
          AND capture.run_id = NEW.run_id
          AND capture.artifact_id = artifact.artifact_id
          AND artifact.run_id = run.run_id
          AND artifact.dataset_id = NEW.dataset_id
          AND run.run_id = NEW.run_id
          AND run.dataset_id = 'macro.fmp.economic_calendar_evidence'
          AND run.command = 'fmp.macro.us_economic_calendar_wholesale'
          AND run.semantic_identity = capture.semantic_identity
          AND output.semantic_identity = capture.semantic_identity
          AND run.scope_json = artifact.request_scope_json
          AND run.scope_json = NEW.scope_json
          AND run.status = 'running'
          AND artifact.content_sha256 = capture.response_sha256
          AND artifact.byte_count = length(capture.response_bytes)
          AND artifact.captured_at = capture.captured_at
          AND artifact.captured_precision = capture.captured_precision
          AND artifact.normalization_version = capture.normalization_version
          AND artifact.source_reference = 'fmp/economic-calendar/us-wholesale.json'
          AND artifact.request_scope_json = NEW.scope_json
          AND capture.semantic_identity = NEW.semantic_identity
          AND capture.row_count = NEW.row_count
          AND capture.captured_at = NEW.captured_at
          AND capture.captured_precision = NEW.captured_precision
          AND NEW.completeness = 'complete'
          AND NEW.validation_state = 'validated'
          AND (
              SELECT count(*)
              FROM fmp_economic_calendar_rows AS row
              WHERE row.capture_id = capture.capture_id
          ) = capture.row_count
          AND NOT EXISTS (
              SELECT 1
              FROM fmp_economic_calendar_rows AS row
              WHERE row.capture_id = capture.capture_id
                AND row.source_row > capture.row_count
          )
    ) THEN RAISE(ABORT, 'FMP wholesale calendar snapshot lineage is incomplete') END;
END;
