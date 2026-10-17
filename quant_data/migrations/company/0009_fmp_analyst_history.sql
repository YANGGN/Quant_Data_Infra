-- Source-native analyst history. Availability is local capture, not target/event date.
CREATE TABLE company_fmp_analyst_captures (
 capture_id TEXT PRIMARY KEY,
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 issuer_id TEXT NOT NULL REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
 cik TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL,
 endpoint TEXT NOT NULL, request_period TEXT NOT NULL CHECK(request_period IN ('annual','quarter','all')),
 request_scope_json TEXT NOT NULL, content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 source_reference TEXT NOT NULL, captured_at TEXT NOT NULL CHECK(length(captured_at)=27 AND captured_at GLOB '????-??-??T??:??:??.??????Z'),
 raw_row_count INTEGER NOT NULL CHECK(raw_row_count>=0),
 normalized_row_count INTEGER NOT NULL CHECK(normalized_row_count>=0),
 warnings_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE TABLE company_fmp_analyst_observation_versions (
 observation_version_id TEXT PRIMARY KEY,
 issuer_id TEXT NOT NULL REFERENCES company_issuers(issuer_id) DEFERRABLE INITIALLY DEFERRED,
 cik TEXT NOT NULL, symbol TEXT NOT NULL, instrument_id TEXT NOT NULL,
 endpoint TEXT NOT NULL, request_period TEXT NOT NULL CHECK(request_period IN ('annual','quarter','all')),
 temporal_kind TEXT NOT NULL CHECK(temporal_kind IN ('forecast_period','dated_event','current_snapshot')),
 target_period_end TEXT, source_event_date TEXT, source_time_raw TEXT, event_precision TEXT,
 currency TEXT NOT NULL, estimate_basis TEXT NOT NULL, payload_json TEXT NOT NULL,
 natural_identity TEXT NOT NULL CHECK(length(natural_identity)=64),
 semantic_hash TEXT NOT NULL CHECK(length(semantic_hash)=64),
 version_sequence INTEGER NOT NULL CHECK(version_sequence>0),
 supersedes_observation_version_id TEXT REFERENCES company_fmp_analyst_observation_versions(observation_version_id) DEFERRABLE INITIALLY DEFERRED,
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27 AND captured_at GLOB '????-??-??T??:??:??.??????Z'),
 source_capture_id TEXT NOT NULL REFERENCES company_fmp_analyst_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
 source_row_index INTEGER NOT NULL CHECK(source_row_index>0), source_row_pointer TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
 UNIQUE(natural_identity,version_sequence),
 CHECK((temporal_kind='forecast_period' AND target_period_end IS NOT NULL AND source_event_date IS NULL)
    OR (temporal_kind='dated_event' AND target_period_end IS NULL AND source_event_date IS NOT NULL)
    OR (temporal_kind='current_snapshot' AND target_period_end IS NULL AND source_event_date IS NULL AND source_time_raw IS NULL AND event_precision IS NULL))
) STRICT;
CREATE TABLE company_fmp_analyst_capture_membership (
 capture_id TEXT NOT NULL REFERENCES company_fmp_analyst_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
 observation_version_id TEXT NOT NULL REFERENCES company_fmp_analyst_observation_versions(observation_version_id) DEFERRABLE INITIALLY DEFERRED,
 source_row_index INTEGER NOT NULL CHECK(source_row_index>0), source_row_pointer TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
 PRIMARY KEY(capture_id,observation_version_id), UNIQUE(capture_id,source_row_index)
) STRICT;
CREATE INDEX company_fmp_analyst_latest ON company_fmp_analyst_observation_versions(issuer_id,endpoint,natural_identity,version_sequence DESC);
CREATE INDEX company_fmp_analyst_cutoff ON company_fmp_analyst_observation_versions(cik,captured_at);
CREATE TRIGGER company_fmp_analyst_version_lineage BEFORE INSERT ON company_fmp_analyst_observation_versions
WHEN (NEW.version_sequence=1 AND (NEW.supersedes_observation_version_id IS NOT NULL OR EXISTS(
 SELECT 1 FROM company_fmp_analyst_observation_versions WHERE natural_identity=NEW.natural_identity)))
 OR (NEW.version_sequence>1 AND NOT EXISTS(
 SELECT 1 FROM company_fmp_analyst_observation_versions p
 WHERE p.observation_version_id=NEW.supersedes_observation_version_id
 AND p.natural_identity=NEW.natural_identity AND p.version_sequence=NEW.version_sequence-1
 AND p.captured_at<NEW.captured_at
 AND p.semantic_hash<>NEW.semantic_hash
 AND NOT EXISTS(SELECT 1 FROM company_fmp_analyst_observation_versions n
 WHERE n.natural_identity=p.natural_identity AND n.version_sequence>p.version_sequence)))
BEGIN SELECT RAISE(ABORT,'Analyst version lineage or capture order is invalid'); END;
CREATE TRIGGER company_fmp_analyst_version_capture BEFORE INSERT ON company_fmp_analyst_observation_versions
WHEN NOT EXISTS(SELECT 1 FROM company_fmp_analyst_captures c
 WHERE c.capture_id=NEW.source_capture_id AND c.issuer_id=NEW.issuer_id
 AND c.cik=NEW.cik AND c.symbol=NEW.symbol AND c.instrument_id=NEW.instrument_id
 AND c.endpoint=NEW.endpoint AND c.request_period=NEW.request_period
 AND c.captured_at=NEW.captured_at AND c.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'Analyst version capture scope is invalid'); END;
CREATE TRIGGER company_fmp_analyst_membership_scope BEFORE INSERT ON company_fmp_analyst_capture_membership
WHEN NOT EXISTS(SELECT 1 FROM company_fmp_analyst_captures c
 JOIN company_fmp_analyst_observation_versions v ON v.observation_version_id=NEW.observation_version_id
 WHERE c.capture_id=NEW.capture_id AND c.run_id=NEW.run_id
 AND c.issuer_id=v.issuer_id AND c.cik=v.cik AND c.symbol=v.symbol
 AND c.instrument_id=v.instrument_id AND c.endpoint=v.endpoint AND c.request_period=v.request_period
 AND v.captured_at<=c.captured_at)
BEGIN SELECT RAISE(ABORT,'Analyst membership scope is invalid'); END;

CREATE TRIGGER company_fmp_analyst_captures_running BEFORE INSERT ON company_fmp_analyst_captures
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'Analyst publication requires a running run'); END;
CREATE TRIGGER company_fmp_analyst_captures_immutable_update BEFORE UPDATE ON company_fmp_analyst_captures
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;
CREATE TRIGGER company_fmp_analyst_captures_immutable_delete BEFORE DELETE ON company_fmp_analyst_captures
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;

CREATE TRIGGER company_fmp_analyst_observation_versions_running BEFORE INSERT ON company_fmp_analyst_observation_versions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'Analyst publication requires a running run'); END;
CREATE TRIGGER company_fmp_analyst_observation_versions_immutable_update BEFORE UPDATE ON company_fmp_analyst_observation_versions
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;
CREATE TRIGGER company_fmp_analyst_observation_versions_immutable_delete BEFORE DELETE ON company_fmp_analyst_observation_versions
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;

CREATE TRIGGER company_fmp_analyst_capture_membership_running BEFORE INSERT ON company_fmp_analyst_capture_membership
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'Analyst publication requires a running run'); END;
CREATE TRIGGER company_fmp_analyst_capture_membership_immutable_update BEFORE UPDATE ON company_fmp_analyst_capture_membership
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;
CREATE TRIGGER company_fmp_analyst_capture_membership_immutable_delete BEFORE DELETE ON company_fmp_analyst_capture_membership
BEGIN SELECT RAISE(ABORT,'Analyst evidence is append-only'); END;
