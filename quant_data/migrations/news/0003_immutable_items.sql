-- Deliberate Stage 4 reconstruction of immutable news evidence and item versions.
-- Full-text search is derived separately in 0004.

CREATE TABLE news_source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    media_type TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    source_reference TEXT NOT NULL,
    request_scope_json TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (source_name, content_sha256, request_scope_json)
) STRICT;

CREATE TABLE news_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    source_name TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE news_snapshot_artifacts (
    snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    artifact_id TEXT NOT NULL
        REFERENCES news_source_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
    artifact_ordinal INTEGER NOT NULL CHECK (artifact_ordinal > 0),
    PRIMARY KEY (snapshot_id, artifact_id),
    UNIQUE (snapshot_id, artifact_ordinal)
) STRICT;

CREATE TABLE news_items (
    item_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    created_run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (source_name, source_item_id)
) STRICT;

CREATE TABLE news_item_versions (
    news_item_version_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL
        REFERENCES news_items(item_id) DEFERRABLE INITIALLY DEFERRED,
    content_identity TEXT NOT NULL UNIQUE CHECK (
        length(content_identity) = 64
        AND content_identity NOT GLOB '*[^0-9a-f]*'
    ),
    headline TEXT,
    body TEXT,
    summary TEXT,
    source_url TEXT,
    published_at TEXT,
    published_precision TEXT NOT NULL CHECK (
        published_precision IN ('date', 'datetime', 'unknown')
    ),
    content_state TEXT NOT NULL CHECK (
        content_state IN ('present', 'missing', 'redacted')
    ),
    content_missing_reason TEXT,
    item_state TEXT NOT NULL CHECK (
        item_state IN ('active', 'retracted')
    ),
    retraction_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_news_item_version_id TEXT
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (item_id, version_sequence),
    CHECK (
        (published_precision = 'unknown' AND published_at IS NULL)
        OR (published_precision IN ('date', 'datetime') AND published_at IS NOT NULL)
    ),
    CHECK (
        (content_state = 'present'
         AND (headline IS NOT NULL OR body IS NOT NULL OR summary IS NOT NULL)
         AND content_missing_reason IS NULL)
        OR (
            content_state IN ('missing', 'redacted')
            AND headline IS NULL
            AND body IS NULL
            AND summary IS NULL
            AND content_missing_reason IS NOT NULL
        )
    ),
    CHECK (
        (item_state = 'active' AND retraction_reason IS NULL)
        OR (item_state = 'retracted' AND retraction_reason IS NOT NULL)
    )
) STRICT;

CREATE TABLE news_item_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    news_item_version_id TEXT NOT NULL
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, news_item_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE TABLE news_item_symbols (
    news_item_symbol_id TEXT PRIMARY KEY,
    news_item_version_id TEXT NOT NULL
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    instrument_id TEXT,
    provider TEXT,
    provider_symbol TEXT,
    association_state TEXT NOT NULL CHECK (
        association_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK (
        (association_state = 'present'
         AND instrument_id IS NOT NULL
         AND provider IS NOT NULL
         AND provider_symbol IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            association_state = 'missing'
            AND instrument_id IS NULL
            AND provider IS NULL
            AND provider_symbol IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE news_item_topics (
    news_item_topic_id TEXT PRIMARY KEY,
    news_item_version_id TEXT NOT NULL
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    topic TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    association_state TEXT NOT NULL CHECK (
        association_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK (
        (association_state = 'present' AND topic IS NOT NULL AND missing_reason IS NULL)
        OR (
            association_state = 'missing'
            AND topic IS NULL
            AND confidence IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE news_item_geographies (
    news_item_geography_id TEXT PRIMARY KEY,
    news_item_version_id TEXT NOT NULL
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    geography_code TEXT,
    association_state TEXT NOT NULL CHECK (
        association_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK (
        (association_state = 'present'
         AND geography_code IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            association_state = 'missing'
            AND geography_code IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE news_item_coverage_lanes (
    news_item_coverage_lane_id TEXT PRIMARY KEY,
    news_item_version_id TEXT NOT NULL
        REFERENCES news_item_versions(news_item_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    coverage_lane TEXT,
    association_state TEXT NOT NULL CHECK (
        association_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_snapshot_id TEXT NOT NULL
        REFERENCES news_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK (
        (association_state = 'present'
         AND coverage_lane IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            association_state = 'missing'
            AND coverage_lane IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE INDEX news_snapshots_selection
ON news_snapshots(source_name, available_at, captured_at);

CREATE INDEX news_item_versions_selection
ON news_item_versions(item_id, available_at, version_sequence);

CREATE INDEX news_item_versions_availability
ON news_item_versions(available_at, published_at, item_state);

CREATE INDEX news_item_symbols_instrument
ON news_item_symbols(instrument_id, available_at);

CREATE INDEX news_item_topics_topic
ON news_item_topics(topic, available_at);

CREATE INDEX news_item_geographies_code
ON news_item_geographies(geography_code, available_at);

CREATE INDEX news_item_coverage_lanes_lane
ON news_item_coverage_lanes(coverage_lane, available_at);


CREATE TRIGGER news_source_artifacts_immutable_update
BEFORE UPDATE ON news_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'news source artifacts are immutable');
END;

CREATE TRIGGER news_source_artifacts_immutable_delete
BEFORE DELETE ON news_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'news source artifacts must not be deleted');
END;

CREATE TRIGGER news_snapshots_immutable_update
BEFORE UPDATE ON news_snapshots
BEGIN
    SELECT RAISE(ABORT, 'news snapshots are immutable');
END;

CREATE TRIGGER news_snapshots_immutable_delete
BEFORE DELETE ON news_snapshots
BEGIN
    SELECT RAISE(ABORT, 'news snapshots must not be deleted');
END;

CREATE TRIGGER news_snapshot_artifacts_lineage_guard
BEFORE INSERT ON news_snapshot_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM news_snapshots AS snapshot
    JOIN news_source_artifacts AS artifact
      ON artifact.artifact_id = NEW.artifact_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.run_id = artifact.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'news snapshot artifact/run mismatch');
END;

CREATE TRIGGER news_snapshot_artifacts_immutable_update
BEFORE UPDATE ON news_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'news snapshot artifact membership is immutable');
END;

CREATE TRIGGER news_snapshot_artifacts_immutable_delete
BEFORE DELETE ON news_snapshot_artifacts
BEGIN
    SELECT RAISE(ABORT, 'news snapshot artifact membership must not be deleted');
END;

CREATE TRIGGER news_items_immutable_update
BEFORE UPDATE ON news_items
BEGIN
    SELECT RAISE(ABORT, 'news item identity is immutable');
END;

CREATE TRIGGER news_items_immutable_delete
BEFORE DELETE ON news_items
BEGIN
    SELECT RAISE(ABORT, 'news items must not be deleted');
END;

CREATE TRIGGER news_item_versions_snapshot_scope_guard
BEFORE INSERT ON news_item_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM news_items AS item
    JOIN news_snapshots AS snapshot
      ON snapshot.snapshot_id = NEW.source_snapshot_id
    WHERE item.item_id = NEW.item_id
      AND item.source_name = snapshot.source_name
      AND snapshot.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'news item version must match source snapshot scope');
END;

CREATE TRIGGER news_item_versions_initial_guard
BEFORE INSERT ON news_item_versions
WHEN NEW.supersedes_news_item_version_id IS NULL
 AND (NEW.version_sequence <> 1 OR NEW.item_state <> 'active')
BEGIN
    SELECT RAISE(ABORT, 'initial news item version must be active sequence one');
END;

CREATE TRIGGER news_item_versions_supersession_guard
BEFORE INSERT ON news_item_versions
WHEN NEW.supersedes_news_item_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM news_item_versions AS previous
        WHERE previous.news_item_version_id = NEW.supersedes_news_item_version_id
          AND previous.item_id = NEW.item_id
          AND previous.version_sequence + 1 = NEW.version_sequence
          AND (
              (previous.item_state = 'active'
               AND NEW.item_state IN ('active', 'retracted'))
              OR (
                  previous.item_state = 'retracted'
                  AND NEW.item_state = 'active'
              )
          )
    ) THEN RAISE(ABORT, 'invalid news item version supersession') END;
END;

CREATE TRIGGER news_item_versions_immutable_update
BEFORE UPDATE ON news_item_versions
BEGIN
    SELECT RAISE(ABORT, 'news item versions are immutable');
END;

CREATE TRIGGER news_item_versions_immutable_delete
BEFORE DELETE ON news_item_versions
BEGIN
    SELECT RAISE(ABORT, 'news item versions must not be deleted');
END;

CREATE TRIGGER news_item_snapshot_membership_scope_guard
BEFORE INSERT ON news_item_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM news_snapshots AS snapshot
    JOIN news_item_versions AS version
      ON version.news_item_version_id = NEW.news_item_version_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.source_name = (
          SELECT source_name FROM news_items WHERE item_id = version.item_id
      )
      AND snapshot.run_id = NEW.run_id
      AND version.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'news item membership must match source snapshot scope');
END;

CREATE TRIGGER news_item_snapshot_membership_immutable_update
BEFORE UPDATE ON news_item_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'news item snapshot membership is immutable');
END;

CREATE TRIGGER news_item_snapshot_membership_immutable_delete
BEFORE DELETE ON news_item_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'news item snapshot membership must not be deleted');
END;


CREATE TRIGGER news_source_artifacts_running_run_guard
BEFORE INSERT ON news_source_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news artifacts require a running ingestion run');
END;

CREATE TRIGGER news_snapshots_running_run_guard
BEFORE INSERT ON news_snapshots
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news snapshots require a running ingestion run');
END;

CREATE TRIGGER news_items_running_run_guard
BEFORE INSERT ON news_items
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.created_run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news items require a running ingestion run');
END;

CREATE TRIGGER ingestion_runs_news_success_domain_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM news_source_artifacts
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM news_source_artifacts AS artifact
        WHERE artifact.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM news_snapshot_artifacts AS membership
              JOIN news_snapshots AS snapshot
                ON snapshot.snapshot_id = membership.snapshot_id
              WHERE membership.artifact_id = artifact.artifact_id
                AND snapshot.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful news run has an artifact without a domain snapshot') END;
END;

CREATE TRIGGER news_item_snapshot_membership_running_run_guard
BEFORE INSERT ON news_item_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news item membership requires a running ingestion run');
END;

CREATE TRIGGER news_item_symbols_scope_guard
BEFORE INSERT ON news_item_symbols
WHEN NOT EXISTS (
    SELECT 1
    FROM news_item_versions AS version
    JOIN news_snapshots AS snapshot
      ON snapshot.snapshot_id = NEW.source_snapshot_id
    JOIN ingestion_runs AS run
      ON run.run_id = NEW.run_id
    WHERE version.news_item_version_id = NEW.news_item_version_id
      AND version.source_snapshot_id = NEW.source_snapshot_id
      AND version.run_id = NEW.run_id
      AND snapshot.run_id = NEW.run_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news symbol association must match a running item version snapshot');
END;

CREATE TRIGGER news_item_symbols_immutable_update
BEFORE UPDATE ON news_item_symbols
BEGIN
    SELECT RAISE(ABORT, 'news symbol association is immutable');
END;

CREATE TRIGGER news_item_symbols_immutable_delete
BEFORE DELETE ON news_item_symbols
BEGIN
    SELECT RAISE(ABORT, 'news symbol association must not be deleted');
END;

CREATE TRIGGER news_item_topics_scope_guard
BEFORE INSERT ON news_item_topics
WHEN NOT EXISTS (
    SELECT 1
    FROM news_item_versions AS version
    JOIN news_snapshots AS snapshot
      ON snapshot.snapshot_id = NEW.source_snapshot_id
    JOIN ingestion_runs AS run
      ON run.run_id = NEW.run_id
    WHERE version.news_item_version_id = NEW.news_item_version_id
      AND version.source_snapshot_id = NEW.source_snapshot_id
      AND version.run_id = NEW.run_id
      AND snapshot.run_id = NEW.run_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news topic association must match a running item version snapshot');
END;

CREATE TRIGGER news_item_topics_immutable_update
BEFORE UPDATE ON news_item_topics
BEGIN
    SELECT RAISE(ABORT, 'news topic association is immutable');
END;

CREATE TRIGGER news_item_topics_immutable_delete
BEFORE DELETE ON news_item_topics
BEGIN
    SELECT RAISE(ABORT, 'news topic association must not be deleted');
END;

CREATE TRIGGER news_item_geographies_scope_guard
BEFORE INSERT ON news_item_geographies
WHEN NOT EXISTS (
    SELECT 1
    FROM news_item_versions AS version
    JOIN news_snapshots AS snapshot
      ON snapshot.snapshot_id = NEW.source_snapshot_id
    JOIN ingestion_runs AS run
      ON run.run_id = NEW.run_id
    WHERE version.news_item_version_id = NEW.news_item_version_id
      AND version.source_snapshot_id = NEW.source_snapshot_id
      AND version.run_id = NEW.run_id
      AND snapshot.run_id = NEW.run_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news geography association must match a running item version snapshot');
END;

CREATE TRIGGER news_item_geographies_immutable_update
BEFORE UPDATE ON news_item_geographies
BEGIN
    SELECT RAISE(ABORT, 'news geography association is immutable');
END;

CREATE TRIGGER news_item_geographies_immutable_delete
BEFORE DELETE ON news_item_geographies
BEGIN
    SELECT RAISE(ABORT, 'news geography association must not be deleted');
END;

CREATE TRIGGER news_item_coverage_lanes_scope_guard
BEFORE INSERT ON news_item_coverage_lanes
WHEN NOT EXISTS (
    SELECT 1
    FROM news_item_versions AS version
    JOIN news_snapshots AS snapshot
      ON snapshot.snapshot_id = NEW.source_snapshot_id
    JOIN ingestion_runs AS run
      ON run.run_id = NEW.run_id
    WHERE version.news_item_version_id = NEW.news_item_version_id
      AND version.source_snapshot_id = NEW.source_snapshot_id
      AND version.run_id = NEW.run_id
      AND snapshot.run_id = NEW.run_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news coverage association must match a running item version snapshot');
END;

CREATE TRIGGER news_item_coverage_lanes_immutable_update
BEFORE UPDATE ON news_item_coverage_lanes
BEGIN
    SELECT RAISE(ABORT, 'news coverage association is immutable');
END;

CREATE TRIGGER news_item_coverage_lanes_immutable_delete
BEFORE DELETE ON news_item_coverage_lanes
BEGIN
    SELECT RAISE(ABORT, 'news coverage association must not be deleted');
END;


CREATE TRIGGER news_snapshot_artifacts_running_run_guard
BEFORE INSERT ON news_snapshot_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM news_snapshots AS snapshot
    JOIN ingestion_runs AS run
      ON run.run_id = snapshot.run_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news snapshot artifacts require a running ingestion run');
END;

CREATE TRIGGER news_item_versions_running_run_guard
BEFORE INSERT ON news_item_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'news item versions require a running ingestion run');
END;

