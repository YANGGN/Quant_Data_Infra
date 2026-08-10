-- Deliberate Stage 3 reconstruction of EIA electricity-retail provenance.
-- `eia_electricity_retail_sales` is a new current-pointer relation, not a
-- recovered physical-table claim.

CREATE TABLE eia_electricity_source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
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

CREATE TABLE eia_electricity_source_snapshots (
    source_snapshot_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    scope_json TEXT NOT NULL,
    scope_digest TEXT NOT NULL CHECK (
        length(scope_digest) = 64
        AND scope_digest NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    tombstone_authoritative INTEGER NOT NULL CHECK (
        tombstone_authoritative IN (0, 1)
    ),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    validation_state TEXT NOT NULL CHECK (
        validation_state IN ('validated', 'warning', 'rejected')
    ),
    warnings_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    CHECK (tombstone_authoritative = 0 OR completeness = 'complete')
) STRICT;

CREATE TABLE eia_electricity_ingestion_pages (
    page_id TEXT PRIMARY KEY,
    source_snapshot_id TEXT NOT NULL
        REFERENCES eia_electricity_source_snapshots(source_snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES eia_electricity_source_artifacts(artifact_id),
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    UNIQUE (source_snapshot_id, page_number)
) STRICT;

CREATE TABLE eia_electricity_snapshot_artifacts (
    source_snapshot_id TEXT NOT NULL
        REFERENCES eia_electricity_source_snapshots(source_snapshot_id),
    artifact_id TEXT NOT NULL REFERENCES eia_electricity_source_artifacts(artifact_id),
    artifact_ordinal INTEGER NOT NULL CHECK (artifact_ordinal > 0),
    PRIMARY KEY (source_snapshot_id, artifact_id),
    UNIQUE (source_snapshot_id, artifact_ordinal)
) STRICT;

CREATE TABLE eia_electricity_snapshot_versions (
    snapshot_version_id TEXT PRIMARY KEY,
    source_snapshot_id TEXT NOT NULL UNIQUE
        REFERENCES eia_electricity_source_snapshots(source_snapshot_id),
    scope_digest TEXT NOT NULL CHECK (
        length(scope_digest) = 64
        AND scope_digest NOT GLOB '*[^0-9a-f]*'
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_snapshot_version_id TEXT
        REFERENCES eia_electricity_snapshot_versions(snapshot_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'tombstone')),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (scope_digest, version_sequence)
) STRICT;

CREATE TABLE eia_electricity_snapshot_scopes (
    scope_id TEXT PRIMARY KEY,
    snapshot_version_id TEXT NOT NULL UNIQUE
        REFERENCES eia_electricity_snapshot_versions(snapshot_version_id),
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

CREATE TABLE eia_electricity_retail_sales_versions (
    retail_sales_version_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    dimensions_digest TEXT NOT NULL CHECK (
        length(dimensions_digest) = 64
        AND dimensions_digest NOT GLOB '*[^0-9a-f]*'
    ),
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
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_retail_sales_version_id TEXT
        REFERENCES eia_electricity_retail_sales_versions(retail_sales_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    artifact_id TEXT NOT NULL REFERENCES eia_electricity_source_artifacts(artifact_id),
    source_snapshot_id TEXT NOT NULL
        REFERENCES eia_electricity_source_snapshots(source_snapshot_id),
    snapshot_version_id TEXT NOT NULL
        REFERENCES eia_electricity_snapshot_versions(snapshot_version_id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'tombstone')),
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    CHECK (period_start <= period_end),
    CHECK (
        (state = 'active' AND ((value_text IS NULL) <> (missing_reason IS NULL)))
        OR (state = 'tombstone' AND value_text IS NULL AND missing_reason IS NOT NULL)
    ),
    UNIQUE (
        series_id, period_start, period_end, dimensions_digest, correction_sequence
    )
) STRICT;

CREATE TABLE eia_electricity_snapshot_membership (
    snapshot_version_id TEXT NOT NULL
        REFERENCES eia_electricity_snapshot_versions(snapshot_version_id),
    retail_sales_version_id TEXT NOT NULL
        REFERENCES eia_electricity_retail_sales_versions(retail_sales_version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_version_id, retail_sales_version_id),
    UNIQUE (snapshot_version_id, source_row)
) STRICT;

CREATE TABLE eia_electricity_retail_sales (
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dimensions_digest TEXT NOT NULL CHECK (
        length(dimensions_digest) = 64
        AND dimensions_digest NOT GLOB '*[^0-9a-f]*'
    ),
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES eia_electricity_retail_sales_versions(retail_sales_version_id),
    PRIMARY KEY (series_id, period_start, period_end, dimensions_digest)
) STRICT;

CREATE INDEX eia_electricity_retail_sales_versions_selection
ON eia_electricity_retail_sales_versions(
    series_id, period_start, period_end, dimensions_digest,
    available_at, correction_sequence
);

CREATE INDEX eia_electricity_snapshot_membership_version
ON eia_electricity_snapshot_membership(retail_sales_version_id, snapshot_version_id);

CREATE TRIGGER eia_electricity_source_artifacts_insert_guard
BEFORE INSERT ON eia_electricity_source_artifacts
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_source_artifacts
    WHERE artifact_id = NEW.artifact_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source artifact identity is immutable');
END;

CREATE TRIGGER eia_electricity_source_artifacts_immutable_update
BEFORE UPDATE ON eia_electricity_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source artifacts are immutable');
END;

CREATE TRIGGER eia_electricity_source_artifacts_immutable_delete
BEFORE DELETE ON eia_electricity_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source artifacts are immutable');
END;

CREATE TRIGGER eia_electricity_source_snapshots_insert_guard
BEFORE INSERT ON eia_electricity_source_snapshots
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_source_snapshots
    WHERE source_snapshot_id = NEW.source_snapshot_id
       OR semantic_identity = NEW.semantic_identity
       OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source snapshot identity is immutable');
END;

CREATE TRIGGER eia_electricity_source_snapshots_immutable_update
BEFORE UPDATE ON eia_electricity_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source snapshots are immutable');
END;

CREATE TRIGGER eia_electricity_source_snapshots_immutable_delete
BEFORE DELETE ON eia_electricity_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity source snapshots are immutable');
END;

CREATE TRIGGER eia_electricity_ingestion_pages_insert_guard
BEFORE INSERT ON eia_electricity_ingestion_pages
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_ingestion_pages
    WHERE page_id = NEW.page_id
       OR (source_snapshot_id = NEW.source_snapshot_id AND page_number = NEW.page_number)
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity ingestion page is immutable');
END;

CREATE TRIGGER eia_electricity_ingestion_pages_lineage_guard
BEFORE INSERT ON eia_electricity_ingestion_pages
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_source_snapshots AS snapshot
    JOIN eia_electricity_source_artifacts AS artifact ON artifact.artifact_id = NEW.artifact_id
    WHERE snapshot.source_snapshot_id = NEW.source_snapshot_id
      AND snapshot.run_id = artifact.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity page provenance mismatch');
END;

CREATE TRIGGER eia_electricity_ingestion_pages_immutable_update
BEFORE UPDATE ON eia_electricity_ingestion_pages
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity ingestion pages are immutable');
END;

CREATE TRIGGER eia_electricity_ingestion_pages_immutable_delete
BEFORE DELETE ON eia_electricity_ingestion_pages
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity ingestion pages are immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_artifacts_insert_guard
BEFORE INSERT ON eia_electricity_snapshot_artifacts
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_artifacts
    WHERE (source_snapshot_id = NEW.source_snapshot_id AND artifact_id = NEW.artifact_id)
       OR (source_snapshot_id = NEW.source_snapshot_id
           AND artifact_ordinal = NEW.artifact_ordinal)
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot artifact membership is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_artifacts_lineage_guard
BEFORE INSERT ON eia_electricity_snapshot_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_source_snapshots AS snapshot
    JOIN eia_electricity_source_artifacts AS artifact ON artifact.artifact_id = NEW.artifact_id
    WHERE snapshot.source_snapshot_id = NEW.source_snapshot_id
      AND snapshot.run_id = artifact.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot artifact run mismatch');
END;

CREATE TRIGGER eia_electricity_snapshot_artifacts_immutable_update
BEFORE UPDATE ON eia_electricity_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot artifact membership is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_artifacts_immutable_delete
BEFORE DELETE ON eia_electricity_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot artifact membership is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_insert_guard
BEFORE INSERT ON eia_electricity_snapshot_versions
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_versions
    WHERE snapshot_version_id = NEW.snapshot_version_id
       OR source_snapshot_id = NEW.source_snapshot_id
       OR (scope_digest = NEW.scope_digest AND version_sequence = NEW.version_sequence)
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot version is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_initial_sequence_guard
BEFORE INSERT ON eia_electricity_snapshot_versions
WHEN NEW.supersedes_snapshot_version_id IS NULL AND NEW.version_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial EIA electricity snapshot sequence must be one');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_supersession_guard
BEFORE INSERT ON eia_electricity_snapshot_versions
WHEN NEW.supersedes_snapshot_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM eia_electricity_snapshot_versions AS previous
        WHERE previous.snapshot_version_id = NEW.supersedes_snapshot_version_id
          AND previous.scope_digest = NEW.scope_digest
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid EIA electricity snapshot supersession') END;
END;

CREATE TRIGGER eia_electricity_snapshot_versions_lineage_guard
BEFORE INSERT ON eia_electricity_snapshot_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_source_snapshots
    WHERE source_snapshot_id = NEW.source_snapshot_id
      AND scope_digest = NEW.scope_digest
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot version provenance mismatch');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_tombstone_scope_guard
BEFORE INSERT ON eia_electricity_snapshot_versions
WHEN NEW.state = 'tombstone'
 AND NOT EXISTS (
    SELECT 1
    FROM eia_electricity_source_snapshots
    WHERE source_snapshot_id = NEW.source_snapshot_id
      AND completeness = 'complete'
      AND tombstone_authoritative = 1
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity tombstone requires complete authoritative scope');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_immutable_update
BEFORE UPDATE ON eia_electricity_snapshot_versions
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot versions are immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_versions_immutable_delete
BEFORE DELETE ON eia_electricity_snapshot_versions
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot versions are immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_scopes_insert_guard
BEFORE INSERT ON eia_electricity_snapshot_scopes
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_scopes
    WHERE scope_id = NEW.scope_id OR snapshot_version_id = NEW.snapshot_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot scope is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_scopes_lineage_guard
BEFORE INSERT ON eia_electricity_snapshot_scopes
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_versions AS version
    JOIN eia_electricity_source_snapshots AS snapshot
      ON snapshot.source_snapshot_id = version.source_snapshot_id
    WHERE version.snapshot_version_id = NEW.snapshot_version_id
      AND version.scope_digest = NEW.scope_digest
      AND snapshot.completeness = NEW.completeness
      AND snapshot.tombstone_authoritative = NEW.tombstone_authoritative
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot scope provenance mismatch');
END;

CREATE TRIGGER eia_electricity_snapshot_scopes_immutable_update
BEFORE UPDATE ON eia_electricity_snapshot_scopes
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot scopes are immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_scopes_immutable_delete
BEFORE DELETE ON eia_electricity_snapshot_scopes
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot scopes are immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_insert_guard
BEFORE INSERT ON eia_electricity_retail_sales_versions
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_retail_sales_versions
    WHERE retail_sales_version_id = NEW.retail_sales_version_id
       OR (
            series_id = NEW.series_id
            AND period_start = NEW.period_start
            AND period_end = NEW.period_end
            AND dimensions_digest = NEW.dimensions_digest
            AND correction_sequence = NEW.correction_sequence
       )
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail version is immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_initial_sequence_guard
BEFORE INSERT ON eia_electricity_retail_sales_versions
WHEN NEW.supersedes_retail_sales_version_id IS NULL AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial EIA electricity correction sequence must be one');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_supersession_guard
BEFORE INSERT ON eia_electricity_retail_sales_versions
WHEN NEW.supersedes_retail_sales_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM eia_electricity_retail_sales_versions AS previous
        WHERE previous.retail_sales_version_id = NEW.supersedes_retail_sales_version_id
          AND previous.series_id = NEW.series_id
          AND previous.period_start = NEW.period_start
          AND previous.period_end = NEW.period_end
          AND previous.dimensions_digest = NEW.dimensions_digest
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid EIA electricity retail supersession') END;
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_lineage_guard
BEFORE INSERT ON eia_electricity_retail_sales_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_versions AS version
    JOIN eia_electricity_source_snapshots AS snapshot
      ON snapshot.source_snapshot_id = version.source_snapshot_id
    WHERE version.snapshot_version_id = NEW.snapshot_version_id
      AND version.source_snapshot_id = NEW.source_snapshot_id
      AND snapshot.run_id = NEW.run_id
)
 OR NOT EXISTS (
    SELECT 1
    FROM eia_electricity_source_artifacts
    WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail provenance mismatch');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_tombstone_scope_guard
BEFORE INSERT ON eia_electricity_retail_sales_versions
WHEN NEW.state = 'tombstone'
 AND NOT EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_scopes AS scope
    WHERE scope.snapshot_version_id = NEW.snapshot_version_id
      AND scope.completeness = 'complete'
      AND scope.tombstone_authoritative = 1
)
BEGIN
    SELECT RAISE(ABORT, 'EIA retail tombstone requires complete authoritative scope');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_immutable_update
BEFORE UPDATE ON eia_electricity_retail_sales_versions
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail versions are immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_versions_immutable_delete
BEFORE DELETE ON eia_electricity_retail_sales_versions
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail versions are immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_membership_insert_guard
BEFORE INSERT ON eia_electricity_snapshot_membership
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_snapshot_membership
    WHERE (
            snapshot_version_id = NEW.snapshot_version_id
            AND retail_sales_version_id = NEW.retail_sales_version_id
       )
       OR (snapshot_version_id = NEW.snapshot_version_id AND source_row = NEW.source_row)
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot membership is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_membership_lineage_guard
BEFORE INSERT ON eia_electricity_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM eia_electricity_retail_sales_versions
    WHERE retail_sales_version_id = NEW.retail_sales_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot membership version is absent');
END;

CREATE TRIGGER eia_electricity_snapshot_membership_immutable_update
BEFORE UPDATE ON eia_electricity_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot membership is immutable');
END;

CREATE TRIGGER eia_electricity_snapshot_membership_immutable_delete
BEFORE DELETE ON eia_electricity_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity snapshot membership is immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_insert_guard
BEFORE INSERT ON eia_electricity_retail_sales
WHEN EXISTS (
    SELECT 1
    FROM eia_electricity_retail_sales
    WHERE (
            series_id = NEW.series_id
            AND period_start = NEW.period_start
            AND period_end = NEW.period_end
            AND dimensions_digest = NEW.dimensions_digest
       )
       OR current_version_id = NEW.current_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail current pointer is immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_pointer_insert_guard
BEFORE INSERT ON eia_electricity_retail_sales
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM eia_electricity_retail_sales_versions AS version
        WHERE version.retail_sales_version_id = NEW.current_version_id
          AND version.series_id = NEW.series_id
          AND version.period_start = NEW.period_start
          AND version.period_end = NEW.period_end
          AND version.dimensions_digest = NEW.dimensions_digest
    ) THEN RAISE(ABORT, 'invalid EIA electricity retail current version') END;
END;

CREATE TRIGGER eia_electricity_retail_sales_identity_immutable_update
BEFORE UPDATE OF series_id, period_start, period_end, dimensions_digest
ON eia_electricity_retail_sales
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail current identity is immutable');
END;

CREATE TRIGGER eia_electricity_retail_sales_pointer_update_guard
BEFORE UPDATE OF current_version_id ON eia_electricity_retail_sales
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM eia_electricity_retail_sales_versions AS version
        WHERE version.retail_sales_version_id = NEW.current_version_id
          AND version.series_id = NEW.series_id
          AND version.period_start = NEW.period_start
          AND version.period_end = NEW.period_end
          AND version.dimensions_digest = NEW.dimensions_digest
    ) THEN RAISE(ABORT, 'invalid EIA electricity retail current version') END;
END;

CREATE TRIGGER eia_electricity_retail_sales_immutable_delete
BEFORE DELETE ON eia_electricity_retail_sales
BEGIN
    SELECT RAISE(ABORT, 'EIA electricity retail current pointers are immutable');
END;
