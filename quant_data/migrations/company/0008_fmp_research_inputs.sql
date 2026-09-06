-- Additive FMP research evidence and source-native normalized row envelopes.
CREATE TABLE company_fmp_research_snapshots (
 snapshot_id TEXT PRIMARY KEY, semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 issuer_id TEXT NOT NULL REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
 cik TEXT NOT NULL, symbol TEXT NOT NULL, content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 captured_at TEXT NOT NULL, source_reference TEXT NOT NULL, endpoint TEXT NOT NULL,
 request_scope_json TEXT NOT NULL, raw_row_count INTEGER NOT NULL CHECK(raw_row_count>=0),
 warnings_json TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE TABLE company_fmp_research_rows (
 research_row_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES company_fmp_research_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
 issuer_id TEXT NOT NULL REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
 cik TEXT NOT NULL, symbol TEXT NOT NULL, endpoint TEXT NOT NULL,
 request_period TEXT NOT NULL CHECK(request_period IN ('annual','quarter','all')),
 fiscal_year INTEGER, fiscal_period TEXT, period_end TEXT, event_date TEXT,
 reported_currency TEXT NOT NULL, accepted_date_raw TEXT, payload_json TEXT NOT NULL,
 source_row_index INTEGER NOT NULL CHECK(source_row_index>0), source_row_pointer TEXT NOT NULL,
 natural_identity TEXT NOT NULL CHECK(length(natural_identity)=64), semantic_hash TEXT NOT NULL CHECK(length(semantic_hash)=64),
 supersedes_research_row_id TEXT REFERENCES company_fmp_research_rows(research_row_id) DEFERRABLE INITIALLY DEFERRED,
 captured_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(period_end IS NOT NULL OR event_date IS NOT NULL)
) STRICT;
CREATE INDEX company_fmp_research_rows_read ON company_fmp_research_rows(cik,endpoint,request_period,natural_identity,captured_at);
CREATE TRIGGER company_fmp_research_snapshot_running_guard BEFORE INSERT ON company_fmp_research_snapshots
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'FMP research snapshot requires a running ingestion run'); END;
CREATE TRIGGER company_fmp_research_row_running_guard BEFORE INSERT ON company_fmp_research_rows
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'FMP research row requires a running ingestion run'); END;
CREATE TRIGGER company_fmp_research_snapshot_append_only BEFORE UPDATE ON company_fmp_research_snapshots
BEGIN SELECT RAISE(ABORT,'FMP research snapshots are append-only'); END;
CREATE TRIGGER company_fmp_research_snapshot_no_delete BEFORE DELETE ON company_fmp_research_snapshots
BEGIN SELECT RAISE(ABORT,'FMP research snapshots are append-only'); END;
CREATE TRIGGER company_fmp_research_row_append_only BEFORE UPDATE ON company_fmp_research_rows
BEGIN SELECT RAISE(ABORT,'FMP research rows are append-only'); END;
CREATE TRIGGER company_fmp_research_row_no_delete BEFORE DELETE ON company_fmp_research_rows
BEGIN SELECT RAISE(ABORT,'FMP research rows are append-only'); END;
CREATE TRIGGER company_fmp_research_row_monotonic_capture BEFORE INSERT ON company_fmp_research_rows
WHEN EXISTS(SELECT 1 FROM company_fmp_research_rows WHERE natural_identity=NEW.natural_identity AND julianday(captured_at)>=julianday(NEW.captured_at))
BEGIN SELECT RAISE(ABORT,'FMP research revisions require increasing capture time'); END;