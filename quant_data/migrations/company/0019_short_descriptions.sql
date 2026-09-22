-- Source profiles and extractive business descriptions have separate ownership.
CREATE TABLE company_profile_evidence (
 evidence_id TEXT PRIMARY KEY,
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 raw_body BLOB NOT NULL CHECK(length(raw_body) BETWEEN 1 AND 1048576),
 source_reference TEXT NOT NULL,
 source_pointer TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('retained_fmp_identity','fmp_profile')),
 source_evidence_id TEXT,
 provider_symbol TEXT NOT NULL,
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE TABLE company_short_description_versions (
 version_id TEXT PRIMARY KEY,
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 content_identity TEXT NOT NULL CHECK(length(content_identity)=64),
 source_symbol TEXT NOT NULL,
 instrument_id TEXT NOT NULL,
 cik TEXT,
 membership_snapshot_id TEXT NOT NULL,
 mapping_id TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES company_profile_evidence(evidence_id),
 full_description TEXT,
 short_description TEXT NOT NULL CHECK(length(short_description) BETWEEN 1 AND 400),
 method TEXT NOT NULL CHECK(method IN ('opening_sentence_excerpt.v1','industry_template.v1')),
 excerpt_truncated INTEGER NOT NULL CHECK(excerpt_truncated IN (0,1)),
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 version_sequence INTEGER NOT NULL CHECK(version_sequence>0),
 predecessor_version_id TEXT UNIQUE REFERENCES company_short_description_versions(version_id),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(instrument_id,version_sequence),
 CHECK((version_sequence=1 AND predecessor_version_id IS NULL) OR
       (version_sequence>1 AND predecessor_version_id IS NOT NULL))
) STRICT;
CREATE INDEX company_short_description_lookup
 ON company_short_description_versions(source_symbol,available_at,version_sequence);
CREATE VIEW company_short_descriptions AS
SELECT d.* FROM company_short_description_versions d
WHERE NOT EXISTS (SELECT 1 FROM company_short_description_versions newer
                  WHERE newer.instrument_id=d.instrument_id AND newer.version_sequence>d.version_sequence);
CREATE TRIGGER company_short_description_time BEFORE INSERT ON company_short_description_versions
WHEN NOT EXISTS (SELECT 1 FROM company_profile_evidence e
                 WHERE e.evidence_id=NEW.evidence_id AND e.captured_at<=NEW.available_at)
BEGIN SELECT RAISE(ABORT,'Description predates profile evidence'); END;
CREATE TRIGGER company_short_description_predecessor BEFORE INSERT ON company_short_description_versions
WHEN NEW.version_sequence>1 AND NOT EXISTS (
 SELECT 1 FROM company_short_description_versions d WHERE d.version_id=NEW.predecessor_version_id
 AND d.instrument_id=NEW.instrument_id AND d.version_sequence=NEW.version_sequence-1
 AND d.available_at<NEW.available_at)
BEGIN SELECT RAISE(ABORT,'Description predecessor differs'); END;
CREATE TRIGGER company_profile_evidence_immutable_update BEFORE UPDATE ON company_profile_evidence
BEGIN SELECT RAISE(ABORT,'Profile evidence is immutable'); END;
CREATE TRIGGER company_profile_evidence_immutable_delete BEFORE DELETE ON company_profile_evidence
BEGIN SELECT RAISE(ABORT,'Profile evidence is immutable'); END;
CREATE TRIGGER company_short_description_immutable_update BEFORE UPDATE ON company_short_description_versions
BEGIN SELECT RAISE(ABORT,'Description versions are immutable'); END;
CREATE TRIGGER company_short_description_immutable_delete BEFORE DELETE ON company_short_description_versions
BEGIN SELECT RAISE(ABORT,'Description versions are immutable'); END;
CREATE TRIGGER company_profile_evidence_running BEFORE INSERT ON company_profile_evidence
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id
 AND status='running' AND dataset_id='company.short_descriptions')
BEGIN SELECT RAISE(ABORT,'Profile import requires running ingestion'); END;
CREATE TRIGGER company_short_description_running BEFORE INSERT ON company_short_description_versions
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id
 AND status='running' AND dataset_id='company.short_descriptions')
BEGIN SELECT RAISE(ABORT,'Description import requires running ingestion'); END;
