-- Replace future wholesale FMP calendar snapshots with compact event versions.
-- Migration 0016 remains immutable and readable as legacy evidence. This
-- successor stores one immutable receipt per changed batch, only new/changed
-- raw event versions, and one non-authoritative latest-response cache row.

CREATE TABLE fmp_economic_calendar_fetch_receipts (
    receipt_id TEXT PRIMARY KEY,
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
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    source_semantic_identity TEXT NOT NULL CHECK (
        length(source_semantic_identity) = 64
        AND source_semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    receipt_manifest_sha256 TEXT NOT NULL CHECK (
        length(receipt_manifest_sha256) = 64
        AND receipt_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    receipt_manifest_byte_count INTEGER NOT NULL CHECK (
        receipt_manifest_byte_count BETWEEN 1 AND 8192
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'fmp_us_calendar_wholesale_evidence_v1'
    ),
    persistence_version TEXT NOT NULL CHECK (
        persistence_version = 'fmp_us_calendar_incremental_events_v2'
    ),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 0 AND 2000),
    changed_event_count INTEGER NOT NULL CHECK (
        changed_event_count BETWEEN 0 AND row_count
    ),
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

CREATE INDEX fmp_economic_calendar_fetch_receipts_window
ON fmp_economic_calendar_fetch_receipts(
    request_country, request_start_date, request_end_date, captured_at DESC
);

CREATE TABLE fmp_economic_calendar_raw_events (
    event_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    country TEXT NOT NULL CHECK (length(country) BETWEEN 1 AND 64),
    event_at TEXT NOT NULL CHECK (length(event_at) BETWEEN 1 AND 128),
    event_name TEXT NOT NULL CHECK (length(event_name) BETWEEN 1 AND 512),
    currency_key TEXT NOT NULL CHECK (length(currency_key) <= 128),
    created_receipt_id TEXT NOT NULL
        REFERENCES fmp_economic_calendar_fetch_receipts(receipt_id),
    UNIQUE (provider, country, event_at, event_name, currency_key)
) STRICT;

CREATE INDEX fmp_economic_calendar_raw_events_selection
ON fmp_economic_calendar_raw_events(country, event_at, event_name);

CREATE TABLE fmp_economic_calendar_raw_event_versions (
    event_version_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES fmp_economic_calendar_raw_events(event_id),
    receipt_id TEXT NOT NULL
        REFERENCES fmp_economic_calendar_fetch_receipts(receipt_id),
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 1 AND 2000),
    row_sha256 TEXT NOT NULL CHECK (
        length(row_sha256) = 64
        AND row_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    raw_row_json TEXT NOT NULL CHECK (length(raw_row_json) > 1),
    currency TEXT,
    unit TEXT,
    previous_json TEXT,
    estimate_json TEXT,
    actual_json TEXT,
    change_json TEXT,
    impact_json TEXT,
    change_percentage_json TEXT,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_event_version_id TEXT
        REFERENCES fmp_economic_calendar_raw_event_versions(event_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (event_id, correction_sequence)
) STRICT;

CREATE INDEX fmp_economic_calendar_raw_event_versions_selection
ON fmp_economic_calendar_raw_event_versions(
    event_id, correction_sequence DESC, captured_at DESC
);

-- This is a replaceable operational cache, not immutable evidence. The
-- singleton key prevents one retained response per request window.
CREATE TABLE fmp_economic_calendar_latest_response_cache (
    feed_id TEXT PRIMARY KEY CHECK (feed_id = 'fmp_us'),
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
    semantic_identity TEXT NOT NULL CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    source_semantic_identity TEXT NOT NULL CHECK (
        length(source_semantic_identity) = 64
        AND source_semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'fmp_us_calendar_wholesale_evidence_v1'
    ),
    persistence_version TEXT NOT NULL CHECK (
        persistence_version = 'fmp_us_calendar_incremental_events_v2'
    ),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 0 AND 2000),
    receipt_id TEXT NOT NULL UNIQUE
        REFERENCES fmp_economic_calendar_fetch_receipts(receipt_id),
    CHECK (request_start_date <= request_end_date)
) STRICT;

CREATE TRIGGER fmp_economic_calendar_fetch_receipts_immutable_update
BEFORE UPDATE ON fmp_economic_calendar_fetch_receipts
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar fetch receipts are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_fetch_receipts_immutable_delete
BEFORE DELETE ON fmp_economic_calendar_fetch_receipts
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar fetch receipts are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_raw_events_immutable_update
BEFORE UPDATE ON fmp_economic_calendar_raw_events
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar raw event identities are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_raw_events_immutable_delete
BEFORE DELETE ON fmp_economic_calendar_raw_events
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar raw events must not be deleted');
END;

CREATE TRIGGER fmp_economic_calendar_raw_event_versions_initial_guard
BEFORE INSERT ON fmp_economic_calendar_raw_event_versions
WHEN NEW.supersedes_event_version_id IS NULL AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial FMP raw event correction sequence must be one');
END;

CREATE TRIGGER fmp_economic_calendar_raw_event_versions_supersession_guard
BEFORE INSERT ON fmp_economic_calendar_raw_event_versions
WHEN NEW.supersedes_event_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fmp_economic_calendar_raw_event_versions AS previous
        WHERE previous.event_version_id = NEW.supersedes_event_version_id
          AND previous.event_id = NEW.event_id
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid FMP raw event supersession') END;
END;

CREATE TRIGGER fmp_economic_calendar_raw_event_versions_immutable_update
BEFORE UPDATE ON fmp_economic_calendar_raw_event_versions
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar raw event versions are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_raw_event_versions_immutable_delete
BEFORE DELETE ON fmp_economic_calendar_raw_event_versions
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar raw event versions are immutable');
END;

CREATE TRIGGER fmp_economic_calendar_latest_cache_insert_guard
BEFORE INSERT ON fmp_economic_calendar_latest_response_cache
WHEN NOT EXISTS (
    SELECT 1
    FROM fmp_economic_calendar_fetch_receipts AS receipt
    WHERE receipt.receipt_id = NEW.receipt_id
      AND receipt.request_country = NEW.request_country
      AND receipt.request_start_date = NEW.request_start_date
      AND receipt.request_end_date = NEW.request_end_date
      AND receipt.response_sha256 = NEW.response_sha256
      AND receipt.semantic_identity = NEW.semantic_identity
      AND receipt.source_semantic_identity = NEW.source_semantic_identity
      AND receipt.captured_at = NEW.captured_at
      AND receipt.row_count = NEW.row_count
)
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar latest cache receipt is invalid');
END;

CREATE TRIGGER fmp_economic_calendar_latest_cache_update_guard
BEFORE UPDATE ON fmp_economic_calendar_latest_response_cache
WHEN NEW.feed_id != OLD.feed_id
 OR NEW.receipt_id = OLD.receipt_id
 OR NEW.captured_at < OLD.captured_at
 OR NOT EXISTS (
    SELECT 1
    FROM fmp_economic_calendar_fetch_receipts AS receipt
    WHERE receipt.receipt_id = NEW.receipt_id
      AND receipt.request_country = NEW.request_country
      AND receipt.request_start_date = NEW.request_start_date
      AND receipt.request_end_date = NEW.request_end_date
      AND receipt.response_sha256 = NEW.response_sha256
      AND receipt.semantic_identity = NEW.semantic_identity
      AND receipt.source_semantic_identity = NEW.source_semantic_identity
      AND receipt.captured_at = NEW.captured_at
      AND receipt.row_count = NEW.row_count
)
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar latest cache replacement is invalid');
END;

CREATE TRIGGER fmp_economic_calendar_latest_cache_delete_guard
BEFORE DELETE ON fmp_economic_calendar_latest_response_cache
BEGIN
    SELECT RAISE(ABORT, 'FMP calendar latest response cache must not be deleted');
END;

-- Final atomic lineage gate for the compact live path. The generic control
-- snapshot is created after the writer callback, so all receipt, version, and
-- cache state must already agree before the transaction can succeed.
CREATE TRIGGER fmp_economic_calendar_incremental_snapshot_lineage
BEFORE INSERT ON ingestion_snapshots
WHEN NEW.dataset_id = 'macro.fmp.economic_calendar_incremental_evidence'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fmp_economic_calendar_fetch_receipts AS receipt
        JOIN ingestion_artifacts AS artifact
          ON artifact.artifact_id = receipt.artifact_id
        JOIN ingestion_runs AS run
          ON run.run_id = receipt.run_id
        JOIN ingestion_run_outputs AS evidence_output
          ON evidence_output.run_id = run.run_id
         AND evidence_output.dataset_id =
             'macro.fmp.economic_calendar_incremental_evidence'
        JOIN ingestion_run_outputs AS event_output
          ON event_output.run_id = run.run_id
         AND event_output.dataset_id =
             'macro.fmp.economic_calendar_incremental_events'
        JOIN fmp_economic_calendar_latest_response_cache AS cache
          ON cache.receipt_id = receipt.receipt_id
        WHERE receipt.snapshot_id = NEW.snapshot_id
          AND receipt.run_id = NEW.run_id
          AND artifact.run_id = run.run_id
          AND artifact.dataset_id = NEW.dataset_id
          AND run.dataset_id = NEW.dataset_id
          AND run.command = 'fmp.macro.us_economic_calendar_wholesale'
          AND run.status = 'running'
          AND run.semantic_identity = receipt.semantic_identity
          AND evidence_output.semantic_identity = receipt.semantic_identity
          AND event_output.semantic_identity = receipt.semantic_identity
          AND artifact.content_sha256 = receipt.receipt_manifest_sha256
          AND artifact.byte_count = receipt.receipt_manifest_byte_count
          AND artifact.captured_at = receipt.captured_at
          AND artifact.captured_precision = receipt.captured_precision
          AND artifact.normalization_version = receipt.persistence_version
          AND artifact.source_reference =
              'fmp/economic-calendar/us-wholesale-receipt.json'
          AND run.scope_json = artifact.request_scope_json
          AND run.scope_json = NEW.scope_json
          AND receipt.semantic_identity = NEW.semantic_identity
          AND receipt.row_count = NEW.row_count
          AND receipt.captured_at = NEW.captured_at
          AND receipt.captured_precision = NEW.captured_precision
          AND NEW.completeness = 'complete'
          AND NEW.validation_state = 'validated'
          AND cache.feed_id = 'fmp_us'
          AND cache.response_sha256 = receipt.response_sha256
          AND cache.semantic_identity = receipt.semantic_identity
          AND cache.source_semantic_identity = receipt.source_semantic_identity
          AND (
              SELECT count(*)
              FROM fmp_economic_calendar_raw_event_versions AS version
              WHERE version.receipt_id = receipt.receipt_id
          ) = receipt.changed_event_count
    ) THEN RAISE(ABORT, 'FMP calendar incremental snapshot lineage is incomplete') END;
END;
