-- Deliberate Stage 4 reconstruction of corporate-action evidence and versions.

CREATE TABLE company_action_source_artifacts (
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

CREATE TABLE company_action_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    artifact_id TEXT NOT NULL
        REFERENCES company_action_source_artifacts(artifact_id)
        DEFERRABLE INITIALLY DEFERRED,
    scope_json TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    tombstone_authoritative INTEGER NOT NULL CHECK (
        tombstone_authoritative IN (0, 1)
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (tombstone_authoritative = 0 OR completeness = 'complete'),
    UNIQUE (artifact_id)
) STRICT;

CREATE TABLE company_corporate_action_versions (
    action_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    action_kind TEXT NOT NULL CHECK (
        action_kind IN ('cash_dividend', 'split')
    ),
    event_date TEXT NOT NULL,
    record_date TEXT,
    pay_date TEXT,
    declared_date TEXT,
    cash_amount REAL,
    currency TEXT,
    split_from_quantity REAL,
    split_to_quantity REAL,
    action_state TEXT NOT NULL CHECK (
        action_state IN ('active', 'tombstone')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_action_version_id TEXT
        REFERENCES company_corporate_action_versions(action_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_action_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (issuer_id, provider, provider_event_id, version_sequence),
    CHECK (
        (action_state = 'tombstone'
         AND missing_reason IS NOT NULL
         AND cash_amount IS NULL
         AND currency IS NULL
         AND split_from_quantity IS NULL
         AND split_to_quantity IS NULL)
        OR (
            action_state = 'active'
            AND missing_reason IS NULL
            AND (
                (action_kind = 'cash_dividend'
                 AND cash_amount IS NOT NULL
                 AND cash_amount >= 0
                 AND currency IS NOT NULL
                 AND split_from_quantity IS NULL
                 AND split_to_quantity IS NULL)
                OR (
                    action_kind = 'split'
                    AND cash_amount IS NULL
                    AND currency IS NULL
                    AND split_from_quantity IS NOT NULL
                    AND split_from_quantity > 0
                    AND split_to_quantity IS NOT NULL
                    AND split_to_quantity > 0
                )
            )
        )
    )
) STRICT;

CREATE TABLE company_action_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES company_action_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    action_version_id TEXT NOT NULL
        REFERENCES company_corporate_action_versions(action_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, action_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE INDEX company_action_snapshots_selection
ON company_action_snapshots(issuer_id, provider, available_at);

CREATE INDEX company_corporate_action_versions_selection
ON company_corporate_action_versions(
    issuer_id, action_kind, event_date, available_at, version_sequence
);

CREATE INDEX company_action_snapshot_membership_version
ON company_action_snapshot_membership(action_version_id, snapshot_id);

