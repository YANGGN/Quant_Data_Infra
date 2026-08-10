-- Deliberate Stage 4 reconstruction of SEC evidence, issuer identity, filings,
-- raw CompanyFacts, and normalized fundamentals. It is not recovered SQL parity.

CREATE TABLE company_sec_artifacts (
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

CREATE TABLE company_issuers (
    issuer_id TEXT PRIMARY KEY,
    cik TEXT NOT NULL UNIQUE CHECK (
        length(cik) = 10 AND cik NOT GLOB '*[^0-9]*'
    ),
    created_run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE company_sec_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    snapshot_kind TEXT NOT NULL CHECK (
        snapshot_kind IN ('submissions', 'companyfacts')
    ),
    artifact_id TEXT NOT NULL
        REFERENCES company_sec_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
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

CREATE TABLE company_issuer_versions (
    issuer_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    legal_name TEXT,
    entity_type TEXT,
    name_state TEXT NOT NULL CHECK (name_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_issuer_version_id TEXT
        REFERENCES company_issuer_versions(issuer_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (issuer_id, version_sequence),
    CHECK (
        (name_state = 'present' AND legal_name IS NOT NULL AND missing_reason IS NULL)
        OR (name_state = 'missing' AND legal_name IS NULL AND missing_reason IS NOT NULL)
    )
) STRICT;

CREATE TABLE company_ticker_snapshot_membership (
    ticker_membership_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    security_identifier TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_through TEXT,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (snapshot_id, source_row),
    CHECK (valid_through IS NULL OR valid_from <= valid_through)
) STRICT;

CREATE TABLE company_issuer_security_link_assertions (
    link_assertion_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    security_identifier TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_through TEXT,
    assertion_state TEXT NOT NULL CHECK (
        assertion_state IN ('active', 'retracted')
    ),
    confidence TEXT NOT NULL CHECK (
        confidence IN ('confirmed', 'unconfirmed')
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    assertion_sequence INTEGER NOT NULL CHECK (assertion_sequence > 0),
    supersedes_link_assertion_id TEXT
        REFERENCES company_issuer_security_link_assertions(link_assertion_id)
        DEFERRABLE INITIALLY DEFERRED,
    ticker_membership_id TEXT NOT NULL
        REFERENCES company_ticker_snapshot_membership(ticker_membership_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (valid_through IS NULL OR valid_from <= valid_through)
) STRICT;

CREATE UNIQUE INDEX company_issuer_security_link_assertions_natural
ON company_issuer_security_link_assertions(
    issuer_id, provider, provider_symbol, security_identifier, valid_from,
    COALESCE(valid_through, ''), assertion_sequence
);

CREATE TABLE company_sec_filings (
    accession_number TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    filing_date_precision TEXT NOT NULL CHECK (filing_date_precision = 'date'),
    accepted_at TEXT NOT NULL,
    accepted_precision TEXT NOT NULL CHECK (
        accepted_precision IN ('date', 'datetime')
    ),
    report_period_start TEXT,
    report_period_end TEXT,
    primary_document TEXT,
    source_url TEXT,
    first_observed_snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        report_period_start IS NULL
        OR report_period_end IS NULL
        OR report_period_start <= report_period_end
    )
) STRICT;

CREATE TABLE company_sec_filing_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    accession_number TEXT NOT NULL
        REFERENCES company_sec_filings(accession_number) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, accession_number),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE TABLE company_sec_fact_versions (
    fact_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    unit TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_period TEXT,
    reference_period_start TEXT,
    reference_period_end TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    accession_number TEXT NOT NULL
        REFERENCES company_sec_filings(accession_number) DEFERRABLE INITIALLY DEFERRED,
    value_text TEXT,
    value_state TEXT NOT NULL CHECK (value_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_fact_version_id TEXT
        REFERENCES company_sec_fact_versions(fact_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        issuer_id, taxonomy, concept, unit, reference_period_end,
        accession_number, version_sequence
    ),
    CHECK (
        (value_state = 'present' AND value_text IS NOT NULL AND missing_reason IS NULL)
        OR (value_state = 'missing' AND value_text IS NULL AND missing_reason IS NOT NULL)
    )
) STRICT;

CREATE TABLE company_sec_fact_snapshot_membership (
    snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    fact_version_id TEXT NOT NULL
        REFERENCES company_sec_fact_versions(fact_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, fact_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE TABLE company_metric_definitions (
    metric_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    base_unit TEXT NOT NULL,
    share_semantics TEXT NOT NULL CHECK (
        share_semantics IN ('instant', 'weighted_average', 'not_share')
    ),
    definition_version TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE company_metric_mappings (
    mapping_id TEXT PRIMARY KEY,
    metric_id TEXT NOT NULL
        REFERENCES company_metric_definitions(metric_id)
        DEFERRABLE INITIALLY DEFERRED,
    mapping_version TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    unit TEXT NOT NULL,
    mapping_state TEXT NOT NULL CHECK (
        mapping_state IN ('active', 'retired')
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    UNIQUE (metric_id, mapping_version, taxonomy, concept, unit)
) STRICT;

CREATE TABLE company_fundamental_observation_versions (
    fundamental_version_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL
        REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
    metric_id TEXT NOT NULL
        REFERENCES company_metric_definitions(metric_id)
        DEFERRABLE INITIALLY DEFERRED,
    mapping_id TEXT NOT NULL
        REFERENCES company_metric_mappings(mapping_id)
        DEFERRABLE INITIALLY DEFERRED,
    share_semantics TEXT NOT NULL CHECK (
        share_semantics IN ('instant', 'weighted_average', 'not_share')
    ),
    fiscal_year INTEGER,
    fiscal_period TEXT,
    reference_period_start TEXT,
    reference_period_end TEXT NOT NULL,
    accession_number TEXT NOT NULL
        REFERENCES company_sec_filings(accession_number) DEFERRABLE INITIALLY DEFERRED,
    source_fact_version_id TEXT NOT NULL
        REFERENCES company_sec_fact_versions(fact_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    value_text TEXT,
    value_state TEXT NOT NULL CHECK (value_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_fundamental_version_id TEXT
        REFERENCES company_fundamental_observation_versions(fundamental_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES company_sec_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (issuer_id, metric_id, reference_period_end, version_sequence),
    CHECK (
        (value_state = 'present' AND value_text IS NOT NULL AND missing_reason IS NULL)
        OR (value_state = 'missing' AND value_text IS NULL AND missing_reason IS NOT NULL)
    )
) STRICT;

CREATE INDEX company_sec_snapshots_issuer_kind
ON company_sec_snapshots(issuer_id, snapshot_kind, available_at);

CREATE INDEX company_ticker_snapshot_membership_symbol
ON company_ticker_snapshot_membership(provider, provider_symbol, valid_from);

CREATE INDEX company_sec_filings_issuer_available
ON company_sec_filings(issuer_id, available_at, filing_date);

CREATE INDEX company_sec_fact_versions_selection
ON company_sec_fact_versions(
    issuer_id, taxonomy, concept, unit, reference_period_end, available_at,
    version_sequence
);

CREATE INDEX company_fundamental_observation_versions_selection
ON company_fundamental_observation_versions(
    issuer_id, metric_id, reference_period_end, available_at, version_sequence
);


CREATE TRIGGER company_sec_artifacts_immutable_update
BEFORE UPDATE ON company_sec_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SEC artifacts are immutable');
END;

CREATE TRIGGER company_sec_artifacts_immutable_delete
BEFORE DELETE ON company_sec_artifacts
BEGIN
    SELECT RAISE(ABORT, 'SEC artifacts must not be deleted');
END;

CREATE TRIGGER company_issuers_immutable_update
BEFORE UPDATE ON company_issuers
BEGIN
    SELECT RAISE(ABORT, 'CIK issuer identity is immutable');
END;

CREATE TRIGGER company_issuers_immutable_delete
BEFORE DELETE ON company_issuers
BEGIN
    SELECT RAISE(ABORT, 'issuers must not be deleted');
END;

CREATE TRIGGER company_sec_snapshots_artifact_lineage_guard
BEFORE INSERT ON company_sec_snapshots
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_artifacts
    WHERE artifact_id = NEW.artifact_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'SEC snapshot artifact/run mismatch');
END;

CREATE TRIGGER company_sec_snapshots_immutable_update
BEFORE UPDATE ON company_sec_snapshots
BEGIN
    SELECT RAISE(ABORT, 'SEC snapshots are immutable');
END;

CREATE TRIGGER company_sec_snapshots_immutable_delete
BEFORE DELETE ON company_sec_snapshots
BEGIN
    SELECT RAISE(ABORT, 'SEC snapshots must not be deleted');
END;

CREATE TRIGGER company_issuer_versions_initial_guard
BEFORE INSERT ON company_issuer_versions
WHEN NEW.supersedes_issuer_version_id IS NULL AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial issuer version sequence must be one');
END;

CREATE TRIGGER company_issuer_versions_supersession_guard
BEFORE INSERT ON company_issuer_versions
WHEN NEW.supersedes_issuer_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM company_issuer_versions AS previous
        WHERE previous.issuer_version_id = NEW.supersedes_issuer_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid issuer version supersession') END;
END;

CREATE TRIGGER company_issuer_versions_snapshot_scope_guard
BEFORE INSERT ON company_issuer_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'issuer version snapshot scope mismatch');
END;

CREATE TRIGGER company_issuer_versions_immutable_update
BEFORE UPDATE ON company_issuer_versions
BEGIN
    SELECT RAISE(ABORT, 'issuer versions are immutable');
END;

CREATE TRIGGER company_issuer_versions_immutable_delete
BEFORE DELETE ON company_issuer_versions
BEGIN
    SELECT RAISE(ABORT, 'issuer versions must not be deleted');
END;

CREATE TRIGGER company_ticker_snapshot_membership_scope_guard
BEFORE INSERT ON company_ticker_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_snapshots
    WHERE snapshot_id = NEW.snapshot_id
      AND issuer_id = NEW.issuer_id
      AND snapshot_kind = 'submissions'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'ticker membership must match a submissions snapshot scope');
END;

CREATE TRIGGER company_ticker_snapshot_membership_immutable_update
BEFORE UPDATE ON company_ticker_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'ticker snapshot membership is immutable');
END;

CREATE TRIGGER company_ticker_snapshot_membership_immutable_delete
BEFORE DELETE ON company_ticker_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'ticker snapshot membership must not be deleted');
END;

CREATE TRIGGER company_issuer_security_link_assertions_membership_guard
BEFORE INSERT ON company_issuer_security_link_assertions
WHEN NOT EXISTS (
    SELECT 1 FROM company_ticker_snapshot_membership
    WHERE ticker_membership_id = NEW.ticker_membership_id
      AND issuer_id = NEW.issuer_id
      AND provider = NEW.provider
      AND provider_symbol = NEW.provider_symbol
      AND security_identifier = NEW.security_identifier
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'issuer security assertion must match ticker evidence');
END;

CREATE TRIGGER company_issuer_security_link_assertions_initial_guard
BEFORE INSERT ON company_issuer_security_link_assertions
WHEN NEW.supersedes_link_assertion_id IS NULL AND NEW.assertion_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial issuer security assertion sequence must be one');
END;

CREATE TRIGGER company_issuer_security_link_assertions_supersession_guard
BEFORE INSERT ON company_issuer_security_link_assertions
WHEN NEW.supersedes_link_assertion_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM company_issuer_security_link_assertions AS previous
        WHERE previous.link_assertion_id = NEW.supersedes_link_assertion_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.provider = NEW.provider
          AND previous.provider_symbol = NEW.provider_symbol
          AND previous.security_identifier = NEW.security_identifier
          AND previous.valid_from = NEW.valid_from
          AND COALESCE(previous.valid_through, '') = COALESCE(NEW.valid_through, '')
          AND previous.assertion_sequence + 1 = NEW.assertion_sequence
    ) THEN RAISE(ABORT, 'invalid issuer security assertion supersession') END;
END;

CREATE TRIGGER company_issuer_security_link_assertions_immutable_update
BEFORE UPDATE ON company_issuer_security_link_assertions
BEGIN
    SELECT RAISE(ABORT, 'issuer security assertions are immutable');
END;

CREATE TRIGGER company_issuer_security_link_assertions_immutable_delete
BEFORE DELETE ON company_issuer_security_link_assertions
BEGIN
    SELECT RAISE(ABORT, 'issuer security assertions must not be deleted');
END;

CREATE TRIGGER company_sec_filings_immutable_update
BEFORE UPDATE ON company_sec_filings
BEGIN
    SELECT RAISE(ABORT, 'SEC filings are immutable');
END;

CREATE TRIGGER company_sec_filings_immutable_delete
BEFORE DELETE ON company_sec_filings
BEGIN
    SELECT RAISE(ABORT, 'SEC filings must not be deleted');
END;

CREATE TRIGGER company_sec_filing_snapshot_membership_immutable_update
BEFORE UPDATE ON company_sec_filing_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'filing snapshot membership is immutable');
END;

CREATE TRIGGER company_sec_filing_snapshot_membership_immutable_delete
BEFORE DELETE ON company_sec_filing_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'filing snapshot membership must not be deleted');
END;


CREATE TRIGGER company_sec_fact_versions_initial_guard
BEFORE INSERT ON company_sec_fact_versions
WHEN NEW.supersedes_fact_version_id IS NULL AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial SEC fact version sequence must be one');
END;

CREATE TRIGGER company_sec_fact_versions_supersession_guard
BEFORE INSERT ON company_sec_fact_versions
WHEN NEW.supersedes_fact_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM company_sec_fact_versions AS previous
        WHERE previous.fact_version_id = NEW.supersedes_fact_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.taxonomy = NEW.taxonomy
          AND previous.concept = NEW.concept
          AND previous.unit = NEW.unit
          AND previous.reference_period_end = NEW.reference_period_end
          AND previous.accession_number = NEW.accession_number
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid SEC fact supersession') END;
END;

CREATE TRIGGER company_sec_fact_versions_snapshot_scope_guard
BEFORE INSERT ON company_sec_fact_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND issuer_id = NEW.issuer_id
      AND snapshot_kind = 'companyfacts'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'SEC fact snapshot scope mismatch');
END;

CREATE TRIGGER company_sec_fact_versions_immutable_update
BEFORE UPDATE ON company_sec_fact_versions
BEGIN
    SELECT RAISE(ABORT, 'SEC fact versions are immutable');
END;

CREATE TRIGGER company_sec_fact_versions_immutable_delete
BEFORE DELETE ON company_sec_fact_versions
BEGIN
    SELECT RAISE(ABORT, 'SEC fact versions must not be deleted');
END;

CREATE TRIGGER company_sec_fact_snapshot_membership_scope_guard
BEFORE INSERT ON company_sec_fact_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM company_sec_snapshots AS snapshot
    JOIN company_sec_fact_versions AS fact
      ON fact.fact_version_id = NEW.fact_version_id
    WHERE snapshot.snapshot_id = NEW.snapshot_id
      AND snapshot.snapshot_kind = 'companyfacts'
      AND snapshot.issuer_id = fact.issuer_id
      AND snapshot.run_id = NEW.run_id
      AND fact.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'SEC fact membership must match companyfacts snapshot scope');
END;

CREATE TRIGGER company_sec_fact_snapshot_membership_immutable_update
BEFORE UPDATE ON company_sec_fact_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'SEC fact snapshot membership is immutable');
END;

CREATE TRIGGER company_sec_fact_snapshot_membership_immutable_delete
BEFORE DELETE ON company_sec_fact_snapshot_membership
BEGIN
    SELECT RAISE(ABORT, 'SEC fact snapshot membership must not be deleted');
END;

CREATE TRIGGER company_metric_definitions_immutable_update
BEFORE UPDATE ON company_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'metric definitions are immutable');
END;

CREATE TRIGGER company_metric_definitions_immutable_delete
BEFORE DELETE ON company_metric_definitions
BEGIN
    SELECT RAISE(ABORT, 'metric definitions must not be deleted');
END;

CREATE TRIGGER company_metric_mappings_immutable_update
BEFORE UPDATE ON company_metric_mappings
BEGIN
    SELECT RAISE(ABORT, 'metric mappings are immutable');
END;

CREATE TRIGGER company_metric_mappings_immutable_delete
BEFORE DELETE ON company_metric_mappings
BEGIN
    SELECT RAISE(ABORT, 'metric mappings must not be deleted');
END;

CREATE TRIGGER company_fundamental_observation_versions_mapping_guard
BEFORE INSERT ON company_fundamental_observation_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM company_metric_mappings AS mapping
    JOIN company_metric_definitions AS metric
      ON metric.metric_id = mapping.metric_id
    JOIN company_sec_fact_versions AS fact
      ON fact.fact_version_id = NEW.source_fact_version_id
    WHERE mapping.mapping_id = NEW.mapping_id
      AND mapping.metric_id = NEW.metric_id
      AND metric.share_semantics = NEW.share_semantics
      AND fact.issuer_id = NEW.issuer_id
      AND fact.accession_number = NEW.accession_number
      AND fact.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'fundamental must retain mapping, share, fact, and accession lineage');
END;

CREATE TRIGGER company_fundamental_observation_versions_initial_guard
BEFORE INSERT ON company_fundamental_observation_versions
WHEN NEW.supersedes_fundamental_version_id IS NULL
 AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial fundamental version sequence must be one');
END;

CREATE TRIGGER company_fundamental_observation_versions_supersession_guard
BEFORE INSERT ON company_fundamental_observation_versions
WHEN NEW.supersedes_fundamental_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM company_fundamental_observation_versions AS previous
        WHERE previous.fundamental_version_id = NEW.supersedes_fundamental_version_id
          AND previous.issuer_id = NEW.issuer_id
          AND previous.metric_id = NEW.metric_id
          AND previous.reference_period_end = NEW.reference_period_end
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid fundamental supersession') END;
END;

CREATE TRIGGER company_fundamental_observation_versions_immutable_update
BEFORE UPDATE ON company_fundamental_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'fundamental observation versions are immutable');
END;

CREATE TRIGGER company_fundamental_observation_versions_immutable_delete
BEFORE DELETE ON company_fundamental_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'fundamental observation versions must not be deleted');
END;


CREATE TRIGGER ingestion_runs_company_sec_success_domain_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM company_sec_artifacts
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_sec_artifacts AS artifact
        WHERE artifact.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM company_sec_snapshots AS snapshot
              WHERE snapshot.artifact_id = artifact.artifact_id
                AND snapshot.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful SEC run has an artifact without a domain snapshot') END;
END;


CREATE TRIGGER company_sec_artifacts_running_run_guard
BEFORE INSERT ON company_sec_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'SEC artifacts require a running ingestion run');
END;

CREATE TRIGGER company_issuers_running_run_guard
BEFORE INSERT ON company_issuers
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.created_run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'issuer identities require a running ingestion run');
END;

CREATE TRIGGER company_sec_snapshots_running_run_guard
BEFORE INSERT ON company_sec_snapshots
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'SEC snapshots require a running ingestion run');
END;

