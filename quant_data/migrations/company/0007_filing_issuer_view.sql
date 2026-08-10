-- Deliberate Stage 4 reconstruction of the recovered filing-issuer derived view.
-- The forward table rebuild renames issuer_id to first_observed_issuer_id.

CREATE TABLE company_sec_filings_stage4 (
    accession_number TEXT PRIMARY KEY,
    first_observed_issuer_id TEXT NOT NULL
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

INSERT INTO company_sec_filings_stage4 (
    accession_number, first_observed_issuer_id, form_type, filing_date,
    filing_date_precision, accepted_at, accepted_precision, report_period_start,
    report_period_end, primary_document, source_url, first_observed_snapshot_id,
    available_at, available_precision, run_id
)
SELECT accession_number, issuer_id, form_type, filing_date, filing_date_precision,
       accepted_at, accepted_precision, report_period_start, report_period_end,
       primary_document, source_url, first_observed_snapshot_id, available_at,
       available_precision, run_id
FROM company_sec_filings;

DROP TABLE company_sec_filings;
ALTER TABLE company_sec_filings_stage4 RENAME TO company_sec_filings;

CREATE INDEX idx_company_sec_filing_membership_accession
ON company_sec_filing_snapshot_membership(accession_number, snapshot_id);

CREATE VIEW company_sec_filing_issuer_membership AS
SELECT DISTINCT membership.accession_number, snapshot.issuer_id
FROM company_sec_filing_snapshot_membership AS membership
JOIN company_sec_snapshots AS snapshot
  ON snapshot.snapshot_id = membership.snapshot_id
JOIN company_sec_filings AS filing
  ON filing.accession_number = membership.accession_number
WHERE snapshot.snapshot_kind = 'submissions';

CREATE TRIGGER company_sec_filings_first_observed_guard
BEFORE INSERT ON company_sec_filings
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_snapshots
    WHERE snapshot_id = NEW.first_observed_snapshot_id
      AND snapshot_kind = 'submissions'
      AND issuer_id = NEW.first_observed_issuer_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'filing first-observed issuer must match submissions scope');
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

CREATE TRIGGER company_sec_filing_snapshot_membership_issuer_scope_guard
BEFORE INSERT ON company_sec_filing_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_snapshots
    WHERE snapshot_id = NEW.snapshot_id
      AND snapshot_kind = 'submissions'
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'filing membership must use an issuer-scoped submissions snapshot');
END;


CREATE TRIGGER company_sec_fact_versions_accession_issuer_guard
BEFORE INSERT ON company_sec_fact_versions
WHEN NOT EXISTS (
    SELECT 1 FROM company_sec_filing_issuer_membership
    WHERE accession_number = NEW.accession_number
      AND issuer_id = NEW.issuer_id
)
BEGIN
    SELECT RAISE(ABORT, 'SEC fact issuer must belong to its filing accession');
END;

CREATE TRIGGER company_guidance_versions_accession_guard
BEFORE INSERT ON company_guidance_versions
WHEN NEW.accession_number IS NOT NULL
 AND NOT EXISTS (
    SELECT 1 FROM company_sec_filing_issuer_membership
    WHERE accession_number = NEW.accession_number
      AND issuer_id = NEW.issuer_id
)
BEGIN
    SELECT RAISE(ABORT, 'guidance accession must belong to its issuer');
END;


CREATE TRIGGER company_issuer_versions_running_run_guard
BEFORE INSERT ON company_issuer_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'issuer versions require a running ingestion run');
END;

CREATE TRIGGER company_ticker_snapshot_membership_running_run_guard
BEFORE INSERT ON company_ticker_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'ticker membership requires a running ingestion run');
END;

CREATE TRIGGER company_issuer_security_link_assertions_running_run_guard
BEFORE INSERT ON company_issuer_security_link_assertions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'issuer security assertions require a running ingestion run');
END;

CREATE TRIGGER company_sec_filings_running_run_guard
BEFORE INSERT ON company_sec_filings
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'SEC filings require a running ingestion run');
END;

CREATE TRIGGER company_sec_filing_snapshot_membership_running_run_guard
BEFORE INSERT ON company_sec_filing_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'filing membership requires a running ingestion run');
END;

CREATE TRIGGER company_sec_fact_versions_running_run_guard
BEFORE INSERT ON company_sec_fact_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'SEC fact versions require a running ingestion run');
END;

CREATE TRIGGER company_sec_fact_snapshot_membership_running_run_guard
BEFORE INSERT ON company_sec_fact_snapshot_membership
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'SEC fact membership requires a running ingestion run');
END;

CREATE TRIGGER company_fundamental_observation_versions_running_run_guard
BEFORE INSERT ON company_fundamental_observation_versions
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'fundamental observations require a running ingestion run');
END;

CREATE TRIGGER ingestion_runs_company_sec_success_membership_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_sec_filings AS filing
        WHERE filing.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM company_sec_filing_snapshot_membership AS membership
              WHERE membership.accession_number = filing.accession_number
                AND membership.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful SEC run has a filing without snapshot membership') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM company_sec_fact_versions AS fact
        WHERE fact.run_id = NEW.run_id
          AND NOT EXISTS (
              SELECT 1
              FROM company_sec_fact_snapshot_membership AS membership
              WHERE membership.fact_version_id = fact.fact_version_id
                AND membership.run_id = NEW.run_id
          )
    ) THEN RAISE(ABORT, 'successful SEC run has a fact without snapshot membership') END;
END;

