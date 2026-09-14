-- Accelerate existing FMP research replay and revision checks without changing facts or guards.
CREATE INDEX idx_company_fmp_research_rows_natural_capture
    ON company_fmp_research_rows(natural_identity, captured_at);
CREATE INDEX idx_company_fmp_research_rows_issuer
    ON company_fmp_research_rows(issuer_id);
