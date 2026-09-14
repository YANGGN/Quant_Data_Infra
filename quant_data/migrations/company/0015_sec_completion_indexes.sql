-- Accelerate existing SEC ingestion completion checks without changing facts or guards.
CREATE INDEX idx_company_sec_filings_run_id
    ON company_sec_filings(run_id);
CREATE INDEX idx_company_sec_fact_versions_run_id
    ON company_sec_fact_versions(run_id);
CREATE INDEX idx_company_sec_fact_membership_version_run
    ON company_sec_fact_snapshot_membership(fact_version_id, run_id);
