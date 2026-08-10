-- Deliberate Stage 3 reconstruction of the macro catalog and version core.

CREATE TABLE macro_series_stage3 (
    series_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_series_code TEXT NOT NULL,
    title TEXT NOT NULL,
    frequency TEXT NOT NULL,
    unit TEXT NOT NULL,
    value_representation TEXT NOT NULL,
    scale TEXT NOT NULL,
    dimensions_json TEXT NOT NULL DEFAULT '{}',
    supported_modes_json TEXT NOT NULL,
    availability_basis TEXT NOT NULL CHECK (
        availability_basis IN ('source_release', 'source_vintage', 'local_capture')
    ),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    description TEXT,
    category TEXT,
    provider_unit TEXT,
    scale_factor_to_base_unit TEXT,
    base_unit TEXT,
    seasonal_adjustment TEXT,
    supports_vintages INTEGER NOT NULL DEFAULT 1 CHECK (supports_vintages IN (0, 1)),
    source_notes TEXT,
    quality_warnings_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    coverage_start TEXT,
    coverage_end TEXT,
    CHECK (coverage_end IS NULL OR coverage_start IS NULL OR coverage_start <= coverage_end),
    UNIQUE (provider, provider_series_code)
) STRICT;

INSERT INTO macro_series_stage3 (
    series_id, provider, provider_series_code, title, frequency, unit,
    value_representation, scale, dimensions_json, supported_modes_json,
    availability_basis, created_run_id
)
SELECT series_id, provider, provider_series_code, title, frequency, unit,
       value_representation, scale, dimensions_json, supported_modes_json,
       availability_basis, created_run_id
FROM macro_series;

DROP TABLE macro_series;
ALTER TABLE macro_series_stage3 RENAME TO macro_series;

CREATE TABLE macro_releases_stage3 (
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
    release_stage TEXT,
    source_published_at TEXT,
    source_published_precision TEXT CHECK (
        source_published_precision IS NULL
        OR source_published_precision IN ('date', 'datetime', 'unknown')
    ),
    UNIQUE (series_id, source_vintage_identity)
) STRICT;

INSERT INTO macro_releases_stage3 (
    release_id, series_id, source_vintage_identity, vintage_at,
    vintage_precision, source_release_order, available_at, available_precision,
    is_first_release, first_release_evidence, first_seen_run_id, release_stage,
    source_published_at, source_published_precision
)
SELECT release_id, series_id, source_vintage_identity, vintage_at,
       vintage_precision, source_release_order, available_at, available_precision,
       is_first_release, first_release_evidence, first_seen_run_id, NULL, NULL, NULL
FROM macro_releases;

DROP TABLE macro_releases;
ALTER TABLE macro_releases_stage3 RENAME TO macro_releases;

CREATE INDEX macro_releases_selection
ON macro_releases(series_id, source_release_order, is_first_release);

CREATE TABLE macro_source_snapshots_stage3 (
    snapshot_id TEXT PRIMARY KEY,
    series_id TEXT REFERENCES macro_series(series_id),
    release_id TEXT REFERENCES macro_releases(release_id),
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    validation_state TEXT NOT NULL CHECK (
        validation_state IN ('validated', 'warning', 'rejected')
    ),
    warnings_json TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    CHECK (
        (series_id IS NULL AND release_id IS NULL)
        OR (series_id IS NOT NULL AND release_id IS NOT NULL)
    )
) STRICT;

INSERT INTO macro_source_snapshots_stage3 (
    snapshot_id, series_id, release_id, artifact_id, semantic_identity,
    completeness, row_count, validation_state, warnings_json, run_id,
    quality_flags_json
)
SELECT snapshot_id, series_id, release_id, artifact_id, semantic_identity,
       completeness, row_count, validation_state, warnings_json, run_id, '[]'
FROM macro_source_snapshots;

CREATE TABLE macro_snapshot_scopes_stage3 (
    scope_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE REFERENCES macro_source_snapshots_stage3(snapshot_id),
    scope_json TEXT NOT NULL,
    scope_digest TEXT NOT NULL CHECK (
        length(scope_digest) = 64
        AND scope_digest NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    tombstone_authoritative INTEGER NOT NULL CHECK (
        tombstone_authoritative IN (0, 1)
    ),
    CHECK (tombstone_authoritative = 0 OR completeness = 'complete')
) STRICT;

INSERT INTO macro_snapshot_scopes_stage3 (
    scope_id, snapshot_id, scope_json, scope_digest, completeness,
    tombstone_authoritative
)
SELECT scope_id, snapshot_id, scope_json, scope_digest, completeness,
       tombstone_authoritative
FROM macro_snapshot_scopes;

CREATE TABLE macro_observation_versions_stage3 (
    version_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    dimensions_digest TEXT NOT NULL CHECK (
        length(dimensions_digest) = 64
        AND dimensions_digest NOT GLOB '*[^0-9a-f]*'
    ),
    release_id TEXT NOT NULL REFERENCES macro_releases(release_id),
    source_vintage_identity TEXT NOT NULL,
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    value_text TEXT,
    missing_reason TEXT,
    unit TEXT NOT NULL,
    value_representation TEXT NOT NULL,
    scale TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    supersedes_version_id TEXT
        REFERENCES macro_observation_versions_stage3(version_id)
        DEFERRABLE INITIALLY DEFERRED,
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots_stage3(snapshot_id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'tombstone')),
    is_preliminary INTEGER NOT NULL DEFAULT 0 CHECK (is_preliminary IN (0, 1)),
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    CHECK (period_start <= period_end),
    CHECK (
        (state = 'active' AND ((value_text IS NULL) <> (missing_reason IS NULL)))
        OR (state = 'tombstone' AND value_text IS NULL AND missing_reason IS NOT NULL)
    ),
    UNIQUE (
        series_id, period_start, period_end, dimensions_digest,
        source_vintage_identity, correction_sequence
    )
) STRICT;

INSERT INTO macro_observation_versions_stage3 (
    version_id, series_id, period_start, period_end, dimensions_json,
    dimensions_digest, release_id, source_vintage_identity, correction_sequence,
    value_text, missing_reason, unit, value_representation, scale,
    available_at, available_precision, captured_at, captured_precision,
    supersedes_version_id, artifact_id, snapshot_id, run_id, source_row,
    state, is_preliminary, quality_flags_json
)
SELECT version_id, series_id, period_start, period_end, dimensions_json,
       dimensions_digest, release_id, source_vintage_identity, correction_sequence,
       value_text, missing_reason, unit, value_representation, scale,
       available_at, available_precision, captured_at, captured_precision,
       supersedes_version_id, artifact_id, snapshot_id, run_id, source_row,
       'active', 0, '[]'
FROM macro_observation_versions;

CREATE TABLE macro_snapshot_observation_membership_stage3 (
    snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots_stage3(snapshot_id),
    version_id TEXT NOT NULL REFERENCES macro_observation_versions_stage3(version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

INSERT INTO macro_snapshot_observation_membership_stage3 (
    snapshot_id, version_id, source_row
)
SELECT snapshot_id, version_id, source_row
FROM macro_snapshot_observation_membership;

DROP TABLE macro_snapshot_observation_membership;
DROP TABLE macro_observation_versions;
DROP TABLE macro_snapshot_scopes;
DROP TABLE macro_source_snapshots;
ALTER TABLE macro_source_snapshots_stage3 RENAME TO macro_source_snapshots;
ALTER TABLE macro_snapshot_scopes_stage3 RENAME TO macro_snapshot_scopes;
ALTER TABLE macro_observation_versions_stage3 RENAME TO macro_observation_versions;
ALTER TABLE macro_snapshot_observation_membership_stage3
RENAME TO macro_snapshot_observation_membership;

CREATE INDEX macro_versions_selection
ON macro_observation_versions(
    series_id, period_start, period_end, dimensions_digest,
    source_vintage_identity, correction_sequence
);

CREATE TABLE macro_series_dimensions (
    dimension_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    dimension_key TEXT NOT NULL,
    dimension_value TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    metadata_json TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_snapshot_id TEXT REFERENCES macro_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (series_id, dimension_key, dimension_value)
) STRICT;

CREATE TABLE macro_observations (
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dimensions_digest TEXT NOT NULL CHECK (
        length(dimensions_digest) = 64
        AND dimensions_digest NOT GLOB '*[^0-9a-f]*'
    ),
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES macro_observation_versions(version_id)
        DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY (series_id, period_start, period_end, dimensions_digest)
) STRICT;

INSERT INTO macro_observations (
    series_id, period_start, period_end, dimensions_digest, current_version_id
)
SELECT current_version.series_id,
       current_version.period_start,
       current_version.period_end,
       current_version.dimensions_digest,
       current_version.version_id
FROM macro_observation_versions AS current_version
JOIN macro_releases AS current_release
  ON current_release.release_id = current_version.release_id
WHERE NOT EXISTS (
    SELECT 1
    FROM macro_observation_versions AS later_version
    JOIN macro_releases AS later_release
      ON later_release.release_id = later_version.release_id
    WHERE later_version.series_id = current_version.series_id
      AND later_version.period_start = current_version.period_start
      AND later_version.period_end = current_version.period_end
      AND later_version.dimensions_digest = current_version.dimensions_digest
      AND (
            later_release.available_at > current_release.available_at
            OR (
                later_release.available_at = current_release.available_at
                AND later_release.source_release_order
                    > current_release.source_release_order
            )
            OR (
                later_release.available_at = current_release.available_at
                AND later_release.source_release_order
                    = current_release.source_release_order
                AND later_version.correction_sequence
                    > current_version.correction_sequence
            )
      )
);

CREATE INDEX macro_observations_current_version
ON macro_observations(current_version_id);

CREATE TRIGGER macro_series_insert_guard
BEFORE INSERT ON macro_series
WHEN EXISTS (
    SELECT 1
    FROM macro_series
    WHERE series_id = NEW.series_id
       OR (provider = NEW.provider AND provider_series_code = NEW.provider_series_code)
)
BEGIN
    SELECT RAISE(ABORT, 'macro series identity is immutable');
END;

CREATE TRIGGER macro_series_identity_immutable_update
BEFORE UPDATE OF series_id, provider, provider_series_code, created_run_id ON macro_series
BEGIN
    SELECT RAISE(ABORT, 'macro series identity is immutable');
END;

CREATE TRIGGER macro_series_immutable_delete
BEFORE DELETE ON macro_series
BEGIN
    SELECT RAISE(ABORT, 'macro series must be retired, not deleted');
END;

CREATE TRIGGER macro_releases_insert_guard
BEFORE INSERT ON macro_releases
WHEN EXISTS (
    SELECT 1
    FROM macro_releases
    WHERE release_id = NEW.release_id
       OR (
            series_id = NEW.series_id
            AND source_vintage_identity = NEW.source_vintage_identity
       )
)
BEGIN
    SELECT RAISE(ABORT, 'macro release identity is immutable');
END;

CREATE TRIGGER macro_releases_immutable_update
BEFORE UPDATE ON macro_releases
BEGIN
    SELECT RAISE(ABORT, 'macro releases are immutable');
END;

CREATE TRIGGER macro_releases_immutable_delete
BEFORE DELETE ON macro_releases
BEGIN
    SELECT RAISE(ABORT, 'macro releases are immutable');
END;

CREATE TRIGGER macro_source_artifacts_insert_guard
BEFORE INSERT ON macro_source_artifacts
WHEN EXISTS (
    SELECT 1
    FROM macro_source_artifacts
    WHERE artifact_id = NEW.artifact_id OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro source artifact identity is immutable');
END;

CREATE TRIGGER macro_source_artifacts_immutable_update
BEFORE UPDATE ON macro_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'macro source artifacts are immutable');
END;

CREATE TRIGGER macro_source_artifacts_immutable_delete
BEFORE DELETE ON macro_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'macro source artifacts are immutable');
END;

CREATE TRIGGER macro_source_snapshots_insert_guard
BEFORE INSERT ON macro_source_snapshots
WHEN EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.snapshot_id
       OR semantic_identity = NEW.semantic_identity
       OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro source snapshot identity is immutable');
END;

CREATE TRIGGER macro_source_snapshots_lineage_guard
BEFORE INSERT ON macro_source_snapshots
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_source_artifacts
    WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro source snapshot artifact/run mismatch');
END;

CREATE TRIGGER macro_source_snapshots_release_guard
BEFORE INSERT ON macro_source_snapshots
WHEN NEW.series_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM macro_releases
    WHERE release_id = NEW.release_id AND series_id = NEW.series_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro source snapshot release/series mismatch');
END;

CREATE TRIGGER macro_source_snapshots_immutable_update
BEFORE UPDATE ON macro_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'macro source snapshots are immutable');
END;

CREATE TRIGGER macro_source_snapshots_immutable_delete
BEFORE DELETE ON macro_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'macro source snapshots are immutable');
END;

CREATE TRIGGER macro_snapshot_scopes_insert_guard
BEFORE INSERT ON macro_snapshot_scopes
WHEN EXISTS (
    SELECT 1
    FROM macro_snapshot_scopes
    WHERE scope_id = NEW.scope_id OR snapshot_id = NEW.snapshot_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot scope is immutable');
END;

CREATE TRIGGER macro_snapshot_scopes_completeness_guard
BEFORE INSERT ON macro_snapshot_scopes
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.snapshot_id AND completeness = NEW.completeness
)
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot scope completeness mismatch');
END;

CREATE TRIGGER macro_snapshot_scopes_immutable_update
BEFORE UPDATE ON macro_snapshot_scopes
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot scopes are immutable');
END;

CREATE TRIGGER macro_snapshot_scopes_immutable_delete
BEFORE DELETE ON macro_snapshot_scopes
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot scopes are immutable');
END;

CREATE TRIGGER macro_observation_versions_insert_guard
BEFORE INSERT ON macro_observation_versions
WHEN EXISTS (
    SELECT 1
    FROM macro_observation_versions
    WHERE version_id = NEW.version_id
       OR (
            series_id = NEW.series_id
            AND period_start = NEW.period_start
            AND period_end = NEW.period_end
            AND dimensions_digest = NEW.dimensions_digest
            AND source_vintage_identity = NEW.source_vintage_identity
            AND correction_sequence = NEW.correction_sequence
       )
)
BEGIN
    SELECT RAISE(ABORT, 'macro observation version is immutable');
END;

CREATE TRIGGER macro_observation_versions_initial_sequence_guard
BEFORE INSERT ON macro_observation_versions
WHEN NEW.supersedes_version_id IS NULL AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial macro correction sequence must be one');
END;

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

CREATE TRIGGER macro_observation_versions_lineage_guard
BEFORE INSERT ON macro_observation_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots AS snapshot
    JOIN macro_releases AS release ON release.release_id = NEW.release_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.run_id = NEW.run_id
      AND release.series_id = NEW.series_id
      AND release.source_vintage_identity = NEW.source_vintage_identity
      AND (
            snapshot.series_id IS NULL
            OR (
                snapshot.series_id = NEW.series_id
                AND snapshot.release_id = NEW.release_id
            )
      )
)
 OR NOT EXISTS (
    SELECT 1
    FROM macro_source_artifacts
    WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro observation provenance mismatch');
END;

CREATE TRIGGER macro_observation_versions_tombstone_scope_guard
BEFORE INSERT ON macro_observation_versions
WHEN NEW.state = 'tombstone'
 AND NOT EXISTS (
    SELECT 1
    FROM macro_snapshot_scopes
    WHERE snapshot_id = NEW.snapshot_id
      AND completeness = 'complete'
      AND tombstone_authoritative = 1
)
BEGIN
    SELECT RAISE(ABORT, 'macro tombstone requires complete authoritative scope');
END;

CREATE TRIGGER macro_observation_versions_immutable_update
BEFORE UPDATE ON macro_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'macro observation versions are immutable');
END;

CREATE TRIGGER macro_observation_versions_immutable_delete
BEFORE DELETE ON macro_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'macro observation versions are immutable');
END;

CREATE TRIGGER macro_snapshot_observation_membership_insert_guard
BEFORE INSERT ON macro_snapshot_observation_membership
WHEN EXISTS (
    SELECT 1
    FROM macro_snapshot_observation_membership
    WHERE (snapshot_id = NEW.snapshot_id AND version_id = NEW.version_id)
       OR (snapshot_id = NEW.snapshot_id AND source_row = NEW.source_row)
)
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot membership is immutable');
END;

CREATE TRIGGER macro_snapshot_observation_membership_immutable_update
BEFORE UPDATE ON macro_snapshot_observation_membership
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot membership is immutable');
END;

CREATE TRIGGER macro_snapshot_observation_membership_immutable_delete
BEFORE DELETE ON macro_snapshot_observation_membership
BEGIN
    SELECT RAISE(ABORT, 'macro snapshot membership is immutable');
END;

CREATE TRIGGER macro_series_dimensions_insert_guard
BEFORE INSERT ON macro_series_dimensions
WHEN EXISTS (
    SELECT 1
    FROM macro_series_dimensions
    WHERE dimension_id = NEW.dimension_id
       OR (
            series_id = NEW.series_id
            AND dimension_key = NEW.dimension_key
            AND dimension_value = NEW.dimension_value
       )
)
BEGIN
    SELECT RAISE(ABORT, 'macro series dimension is immutable');
END;

CREATE TRIGGER macro_series_dimensions_snapshot_lineage_guard
BEFORE INSERT ON macro_series_dimensions
WHEN NEW.source_snapshot_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND run_id = NEW.run_id
      AND (series_id IS NULL OR series_id = NEW.series_id)
)
BEGIN
    SELECT RAISE(ABORT, 'macro series dimension snapshot/run mismatch');
END;

CREATE TRIGGER macro_series_dimensions_immutable_update
BEFORE UPDATE ON macro_series_dimensions
BEGIN
    SELECT RAISE(ABORT, 'macro series dimensions are immutable');
END;

CREATE TRIGGER macro_series_dimensions_immutable_delete
BEFORE DELETE ON macro_series_dimensions
BEGIN
    SELECT RAISE(ABORT, 'macro series dimensions are immutable');
END;

CREATE TRIGGER macro_observations_insert_guard
BEFORE INSERT ON macro_observations
WHEN EXISTS (
    SELECT 1
    FROM macro_observations
    WHERE (
            series_id = NEW.series_id
            AND period_start = NEW.period_start
            AND period_end = NEW.period_end
            AND dimensions_digest = NEW.dimensions_digest
       )
       OR current_version_id = NEW.current_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'macro observation current pointer is immutable');
END;

CREATE TRIGGER macro_observations_pointer_insert_guard
BEFORE INSERT ON macro_observations
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM macro_observation_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.series_id = NEW.series_id
          AND version.period_start = NEW.period_start
          AND version.period_end = NEW.period_end
          AND version.dimensions_digest = NEW.dimensions_digest
    ) THEN RAISE(ABORT, 'invalid current macro observation version') END;
END;

CREATE TRIGGER macro_observations_identity_immutable_update
BEFORE UPDATE OF series_id, period_start, period_end, dimensions_digest
ON macro_observations
BEGIN
    SELECT RAISE(ABORT, 'macro observation current identity is immutable');
END;

CREATE TRIGGER macro_observations_pointer_update_guard
BEFORE UPDATE OF current_version_id ON macro_observations
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM macro_observation_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.series_id = NEW.series_id
          AND version.period_start = NEW.period_start
          AND version.period_end = NEW.period_end
          AND version.dimensions_digest = NEW.dimensions_digest
    ) THEN RAISE(ABORT, 'invalid current macro observation version') END;
END;

CREATE TRIGGER macro_observations_immutable_delete
BEFORE DELETE ON macro_observations
BEGIN
    SELECT RAISE(ABORT, 'macro observation current pointers are immutable');
END;
