-- Deliberate Stage 4 reconstruction of earnings expectations, consensus, events,
-- and guidance. Accession-to-issuer validation is added by the later 0007 view.

CREATE TABLE company_expectation_metric_definitions (
    expectation_metric_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    base_unit TEXT NOT NULL,
    value_kind TEXT NOT NULL CHECK (
        value_kind IN ('currency', 'per_share', 'percent', 'count')
    ),
    definition_version TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE company_earnings_source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
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
    UNIQUE (issuer_id, provider, content_sha256, request_scope_json)
) STRICT;

CREATE TABLE company_earnings_source_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL CHECK (
        snapshot_kind IN ('consensus', 'earnings_event', 'guidance')
    ),
    artifact_id TEXT NOT NULL
        REFERENCES company_earnings_source_artifacts(artifact_id)
        DEFERRABLE INITIALLY DEFERRED,
    scope_json TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (artifact_id, snapshot_kind)
) STRICT;

CREATE TABLE company_consensus_observation_versions (
    consensus_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    expectation_metric_id TEXT NOT NULL
        REFERENCES company_expectation_metric_definitions(expectation_metric_id)
        DEFERRABLE INITIALLY DEFERRED,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    reference_period_start TEXT,
    reference_period_end TEXT NOT NULL,
    statistic_kind TEXT NOT NULL CHECK (
        statistic_kind IN ('mean', 'median', 'high', 'low', 'count')
    ),
    value REAL,
    value_state TEXT NOT NULL CHECK (value_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_consensus_version_id TEXT
        REFERENCES company_consensus_observation_versions(consensus_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_earnings_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        issuer_id, expectation_metric_id, reference_period_end,
        statistic_kind, version_sequence
    ),
    CHECK (
        (value_state = 'present' AND value IS NOT NULL AND missing_reason IS NULL)
        OR (value_state = 'missing' AND value IS NULL AND missing_reason IS NOT NULL)
    )
) STRICT;

CREATE TABLE company_consensus_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES company_earnings_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    consensus_version_id TEXT NOT NULL
        REFERENCES company_consensus_observation_versions(consensus_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, consensus_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE TABLE company_earnings_event_versions (
    event_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    provider_event_key TEXT NOT NULL,
    event_state TEXT NOT NULL CHECK (
        event_state IN ('scheduled', 'reported', 'cancelled', 'missing')
    ),
    event_at TEXT,
    event_precision TEXT CHECK (
        event_precision IS NULL OR event_precision IN ('date', 'datetime')
    ),
    fiscal_year INTEGER,
    fiscal_period TEXT,
    reference_period_end TEXT,
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_event_version_id TEXT
        REFERENCES company_earnings_event_versions(event_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_earnings_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (issuer_id, provider, provider_event_key, version_sequence),
    CHECK (
        (event_state = 'missing'
         AND event_at IS NULL
         AND event_precision IS NULL
         AND missing_reason IS NOT NULL)
        OR (
            event_state IN ('scheduled', 'reported', 'cancelled')
            AND event_at IS NOT NULL
            AND event_precision IS NOT NULL
            AND missing_reason IS NULL
        )
    )
) STRICT;

CREATE TABLE company_earnings_event_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES company_earnings_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    event_version_id TEXT NOT NULL
        REFERENCES company_earnings_event_versions(event_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, event_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE TABLE company_guidance_versions (
    guidance_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    expectation_metric_id TEXT NOT NULL
        REFERENCES company_expectation_metric_definitions(expectation_metric_id)
        DEFERRABLE INITIALLY DEFERRED,
    accession_number TEXT
        REFERENCES company_sec_filings(accession_number) DEFERRABLE INITIALLY DEFERRED,
    source_url TEXT NOT NULL CHECK (source_url <> ''),
    target_period_start TEXT,
    target_period_end TEXT NOT NULL,
    guidance_shape TEXT NOT NULL CHECK (
        guidance_shape IN ('point', 'range', 'narrative', 'missing')
    ),
    point_value REAL,
    low_value REAL,
    high_value REAL,
    narrative TEXT,
    missing_reason TEXT,
    review_state TEXT NOT NULL CHECK (
        review_state IN ('unreviewed', 'reviewed', 'rejected')
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_guidance_version_id TEXT
        REFERENCES company_guidance_versions(guidance_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_earnings_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        issuer_id, expectation_metric_id, target_period_end, version_sequence
    ),
    CHECK (
        target_period_start IS NULL OR target_period_start <= target_period_end
    ),
    CHECK (
        (guidance_shape = 'point'
         AND point_value IS NOT NULL
         AND low_value IS NULL
         AND high_value IS NULL
         AND narrative IS NULL
         AND missing_reason IS NULL)
        OR (
            guidance_shape = 'range'
            AND point_value IS NULL
            AND low_value IS NOT NULL
            AND high_value IS NOT NULL
            AND low_value <= high_value
            AND narrative IS NULL
            AND missing_reason IS NULL
        )
        OR (
            guidance_shape = 'narrative'
            AND point_value IS NULL
            AND low_value IS NULL
            AND high_value IS NULL
            AND narrative IS NOT NULL
            AND missing_reason IS NULL
        )
        OR (
            guidance_shape = 'missing'
            AND point_value IS NULL
            AND low_value IS NULL
            AND high_value IS NULL
            AND narrative IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE INDEX company_earnings_source_snapshots_selection
ON company_earnings_source_snapshots(
    issuer_id, provider, snapshot_kind, available_at
);

CREATE INDEX company_consensus_observation_versions_selection
ON company_consensus_observation_versions(
    issuer_id, expectation_metric_id, reference_period_end, available_at,
    version_sequence
);

CREATE INDEX company_earnings_event_versions_selection
ON company_earnings_event_versions(
    issuer_id, provider, event_at, available_at, version_sequence
);

CREATE INDEX company_guidance_versions_selection
ON company_guidance_versions(
    issuer_id, expectation_metric_id, target_period_end, available_at,
    version_sequence
);


CREATE TRIGGER company_expectation_metric_definitions_immutable_update
BEFORE UPDATE ON company_expectation_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'expectation metric definitions are immutable');
END;

CREATE TRIGGER company_expectation_metric_definitions_immutable_delete
BEFORE DELETE ON company_expectation_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'expectation metric definitions must not be deleted');
END;

CREATE TRIGGER company_earnings_source_artifacts_immutable_update
BEFORE UPDATE ON company_earnings_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'earnings source artifacts are immutable');
END;

CREATE TRIGGER company_earnings_source_artifacts_immutable_delete
BEFORE DELETE ON company_earnings_source_artifacts
BEGIN
    SELECT RAISE(ABORT, 'earnings source artifacts must not be deleted');
END;

CREATE TRIGGER company_earnings_source_snapshots_artifact_lineage_guard
BEFORE INSERT ON company_earnings_source_snapshots
WHEN NOT EXISTS (
    SELECT 1
    FROM company_earnings_source_artifacts
    WHERE artifact_id = NEW.artifact_id
      AND issuer_id = NEW.issuer_id
      AND provider = NEW.provider
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'earnings snapshot artifact/run mismatch');
END;

CREATE TRIGGER company_earnings_source_snapshots_immutable_update
BEFORE UPDATE ON company_earnings_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'earnings source snapshots are immutable');
END;

CREATE TRIGGER company_earnings_source_snapshots_immutable_delete
BEFORE DELETE ON company_earnings_source_snapshots
BEGIN
    SELECT RAISE(ABORT, 'earnings source snapshots must not be deleted');
END;

CREATE TRIGGER company_consensus_observation_versions_snapshot_scope_guard
BEFORE INSERT ON company_consensus_observation_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_earnings_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND snapshot_kind = 'consensus'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'consensus observation must match snapshot scope');
END;

CREATE TRIGGER company_consensus_observation_versions_initial_guard
BEFORE INSERT ON company_consensus_observation_versions
WHEN NEW.supersedes_consensus_version_id IS NULL
 AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial consensus version sequence must be one');
END;

CREATE TRIGGER company_consensus_observation_versions_supersession_guard
BEFORE INSERT ON company_consensus_observation_versions
WHEN NEW.supersedes_consensus_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM company_consensus_observation_versions AS previous
        WHERE previous.consensus_version_id = NEW.supersedes_consensus_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.expectation_metric_id = NEW.expectation_metric_id
          AND previous.reference_period_end = NEW.reference_period_end
          AND previous.statistic_kind = NEW.statistic_kind
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid consensus supersession') END;
END;

CREATE TRIGGER company_consensus_observation_versions_immutable_update
BEFORE UPDATE ON company_consensus_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'consensus observation versions are immutable');
END;

CREATE TRIGGER company_consensus_observation_versions_immutable_delete
BEFORE DELETE ON company_consensus_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'consensus observation versions must not be deleted');
END;

CREATE TRIGGER company_consensus_snapshot_membership_scope_guard
BEFORE INSERT ON company_consensus_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM company_earnings_source_snapshots AS snapshot
    JOIN company_consensus_observation_versions AS consensus
      ON consensus.consensus_version_id = NEW.consensus_version_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.snapshot_kind = 'consensus'
      AND snapshot.issuer_id = consensus.issuer_id
      AND snapshot.run_id = NEW.run_id
      AND consensus.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'consensus membership must match snapshot scope');
END;

CREATE TRIGGER company_consensus_snapshot_membership_immutable_update
BEFORE UPDATE ON company_consensus_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'consensus snapshot membership is immutable');
END;

CREATE TRIGGER company_consensus_snapshot_membership_immutable_delete
BEFORE DELETE ON company_consensus_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'consensus snapshot membership must not be deleted');
END;

CREATE TRIGGER company_earnings_event_versions_snapshot_scope_guard
BEFORE INSERT ON company_earnings_event_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_earnings_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND snapshot_kind = 'earnings_event'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'earnings event must match snapshot scope');
END;

CREATE TRIGGER company_earnings_event_versions_initial_guard
BEFORE INSERT ON company_earnings_event_versions
WHEN NEW.supersedes_event_version_id IS NULL
 AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial earnings event version sequence must be one');
END;

CREATE TRIGGER company_earnings_event_versions_supersession_guard
BEFORE INSERT ON company_earnings_event_versions
WHEN NEW.supersedes_event_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM company_earnings_event_versions AS previous
        WHERE previous.event_version_id = NEW.supersedes_event_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.provider = NEW.provider
          AND previous.provider_event_key = NEW.provider_event_key
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid earnings event supersession') END;
END;

CREATE TRIGGER company_earnings_event_versions_immutable_update
BEFORE UPDATE ON company_earnings_event_versions
BEGIN
    SELECT RAISE(ABORT, 'earnings event versions are immutable');
END;

CREATE TRIGGER company_earnings_event_versions_immutable_delete
BEFORE DELETE ON company_earnings_event_versions
BEGIN
    SELECT RAISE(ABORT, 'earnings event versions must not be deleted');
END;

CREATE TRIGGER company_earnings_event_snapshot_membership_scope_guard
BEFORE INSERT ON company_earnings_event_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM company_earnings_source_snapshots AS snapshot
    JOIN company_earnings_event_versions AS event
      ON event.event_version_id = NEW.event_version_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.snapshot_kind = 'earnings_event'
      AND snapshot.issuer_id = event.issuer_id
      AND snapshot.run_id = NEW.run_id
      AND event.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'earnings event membership must match snapshot scope');
END;

CREATE TRIGGER company_earnings_event_snapshot_membership_immutable_update
BEFORE UPDATE ON company_earnings_event_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'earnings event snapshot membership is immutable');
END;

CREATE TRIGGER company_earnings_event_snapshot_membership_immutable_delete
BEFORE DELETE ON company_earnings_event_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'earnings event snapshot membership must not be deleted');
END;

CREATE TRIGGER company_guidance_versions_snapshot_scope_guard
BEFORE INSERT ON company_guidance_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_earnings_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND snapshot_kind = 'guidance'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'guidance must match snapshot scope');
END;

CREATE TRIGGER company_guidance_versions_initial_guard
BEFORE INSERT ON company_guidance_versions
WHEN NEW.supersedes_guidance_version_id IS NULL
 AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial guidance version sequence must be one');
END;

CREATE TRIGGER company_guidance_versions_supersession_guard
BEFORE INSERT ON company_guidance_versions
WHEN NEW.supersedes_guidance_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM company_guidance_versions AS previous
        WHERE previous.guidance_version_id = NEW.supersedes_guidance_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.expectation_metric_id = NEW.expectation_metric_id
          AND previous.target_period_end = NEW.target_period_end
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid guidance supersession') END;
END;

CREATE TRIGGER company_guidance_versions_immutable_update
BEFORE UPDATE ON company_guidance_versions
BEGIN
    SELECT RAISE(ABORT, 'guidance versions are immutable');
END;

CREATE TRIGGER company_guidance_versions_immutable_delete
BEFORE DELETE ON company_guidance_versions
BEGIN
    SELECT RAISE(ABORT, 'guidance versions must not be deleted');
END;


CREATE TRIGGER ingestion_runs_company_earnings_success_domain_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM company_earnings_source_artifacts
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_earnings_source_artifacts AS artifact
        WHERE artifact.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM company_earnings_source_snapshots AS snapshot
              WHERE snapshot.artifact_id = artifact.artifact_id
                AND snapshot.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful earnings run has an artifact without a domain snapshot') END;
END;


CREATE TRIGGER company_earnings_source_artifacts_running_run_guard
BEFORE INSERT ON company_earnings_source_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'earnings artifacts require a running ingestion run');
END;

CREATE TRIGGER company_earnings_source_snapshots_running_run_guard
BEFORE INSERT ON company_earnings_source_snapshots
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'earnings snapshots require a running ingestion run');
END;


CREATE TRIGGER company_consensus_observation_versions_running_run_guard
BEFORE INSERT ON company_consensus_observation_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'consensus versions require a running ingestion run');
END;

CREATE TRIGGER company_consensus_snapshot_membership_running_run_guard
BEFORE INSERT ON company_consensus_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'consensus membership requires a running ingestion run');
END;

CREATE TRIGGER company_earnings_event_versions_running_run_guard
BEFORE INSERT ON company_earnings_event_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'earnings event versions require a running ingestion run');
END;

CREATE TRIGGER company_earnings_event_snapshot_membership_running_run_guard
BEFORE INSERT ON company_earnings_event_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'earnings event membership requires a running ingestion run');
END;

CREATE TRIGGER company_guidance_versions_running_run_guard
BEFORE INSERT ON company_guidance_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'guidance versions require a running ingestion run');
END;

CREATE TRIGGER ingestion_runs_company_earnings_success_membership_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_consensus_observation_versions AS consensus
        WHERE consensus.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1 FROM company_consensus_snapshot_membership AS membership
              WHERE membership.consensus_version_id = consensus.consensus_version_id
                AND membership.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful earnings run has consensus without snapshot membership') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_earnings_event_versions AS event
        WHERE event.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1 FROM company_earnings_event_snapshot_membership AS membership
              WHERE membership.event_version_id = event.event_version_id
                AND membership.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful earnings run has event without snapshot membership') END;
END;

