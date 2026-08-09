CREATE TABLE macro_series (
    series_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_series_code TEXT NOT NULL,
    title TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency = 'monthly'),
    unit TEXT NOT NULL,
    value_representation TEXT NOT NULL,
    scale TEXT NOT NULL,
    dimensions_json TEXT NOT NULL CHECK (dimensions_json = '{}'),
    supported_modes_json TEXT NOT NULL,
    availability_basis TEXT NOT NULL CHECK (availability_basis = 'source_release'),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, provider_series_code)
) STRICT;

CREATE TABLE macro_releases (
    release_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    source_vintage_identity TEXT NOT NULL,
    vintage_at TEXT NOT NULL,
    vintage_precision TEXT NOT NULL CHECK (vintage_precision IN ('date', 'datetime')),
    source_release_order TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision IN ('date', 'datetime')),
    is_first_release INTEGER NOT NULL CHECK (is_first_release IN (0, 1)),
    first_release_evidence TEXT,
    first_seen_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (series_id, source_vintage_identity)
) STRICT;

CREATE TABLE macro_source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    request_scope_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    source_resource TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE macro_source_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    release_id TEXT NOT NULL REFERENCES macro_releases(release_id),
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (length(semantic_identity) = 64),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    validation_state TEXT NOT NULL CHECK (validation_state = 'validated'),
    warnings_json TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE macro_snapshot_scopes (
    scope_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE REFERENCES macro_source_snapshots(snapshot_id),
    scope_json TEXT NOT NULL,
    scope_digest TEXT NOT NULL CHECK (length(scope_digest) = 64),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    tombstone_authoritative INTEGER NOT NULL CHECK (tombstone_authoritative = 0)
) STRICT;

CREATE TABLE macro_observation_versions (
    version_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dimensions_json TEXT NOT NULL CHECK (dimensions_json = '{}'),
    dimensions_digest TEXT NOT NULL CHECK (length(dimensions_digest) = 64),
    release_id TEXT NOT NULL REFERENCES macro_releases(release_id),
    source_vintage_identity TEXT NOT NULL,
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    value_text TEXT,
    missing_reason TEXT,
    unit TEXT NOT NULL,
    value_representation TEXT NOT NULL,
    scale TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision IN ('date', 'datetime')),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    supersedes_version_id TEXT REFERENCES macro_observation_versions(version_id),
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots(snapshot_id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK ((value_text IS NULL) <> (missing_reason IS NULL)),
    CHECK (period_start <= period_end),
    UNIQUE (
        series_id, period_start, period_end, dimensions_digest,
        source_vintage_identity, correction_sequence
    )
) STRICT;

CREATE TABLE macro_snapshot_observation_membership (
    snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots(snapshot_id),
    version_id TEXT NOT NULL REFERENCES macro_observation_versions(version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE INDEX macro_versions_selection
ON macro_observation_versions(
    series_id, period_start, period_end, dimensions_digest,
    source_vintage_identity, correction_sequence
);

CREATE INDEX macro_releases_selection
ON macro_releases(series_id, source_release_order, is_first_release);

CREATE TRIGGER macro_versions_same_key_supersession
BEFORE INSERT ON macro_observation_versions
WHEN NEW.supersedes_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM macro_observation_versions AS previous
        WHERE previous.version_id = NEW.supersedes_version_id
          AND previous.series_id = NEW.series_id
          AND previous.period_start = NEW.period_start
          AND previous.period_end = NEW.period_end
          AND previous.dimensions_digest = NEW.dimensions_digest
          AND previous.source_vintage_identity = NEW.source_vintage_identity
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid macro supersession') END;
END;
