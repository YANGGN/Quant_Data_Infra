-- Structured model drafts and independent assessments; no source or legacy mutation.
CREATE TABLE company_structured_transcript_outputs (
 analysis_id TEXT PRIMARY KEY,
 request_identity TEXT NOT NULL UNIQUE CHECK(length(request_identity)=64),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 capture_id TEXT NOT NULL REFERENCES company_equibles_transcripts(capture_id),
 input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
 source_json TEXT NOT NULL CHECK(json_valid(source_json)),
 model TEXT NOT NULL,
 reasoning_effort TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 schema_version TEXT NOT NULL,
 configuration_json TEXT NOT NULL CHECK(json_valid(configuration_json)),
 output_json TEXT NOT NULL CHECK(json_valid(output_json)),
 response_sha256 TEXT NOT NULL CHECK(length(response_sha256)=64),
 raw_response BLOB NOT NULL CHECK(length(raw_response) BETWEEN 1 AND 4194304),
 source_available_at TEXT NOT NULL,
 available_at TEXT NOT NULL CHECK(length(available_at)=27 AND available_at>=source_available_at),
 published_at TEXT NOT NULL CHECK(length(published_at)=27 AND published_at>=available_at),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE INDEX company_structured_transcript_lookup ON company_structured_transcript_outputs(capture_id,available_at,analysis_id);
CREATE TABLE company_structured_transcript_assessments (
 assessment_id TEXT PRIMARY KEY,
 analysis_id TEXT NOT NULL REFERENCES company_structured_transcript_outputs(analysis_id),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 kind TEXT NOT NULL CHECK(kind IN ('automatic','sol_review','astra_adjudication')),
 evaluator TEXT NOT NULL,
 reasoning_effort TEXT,
 outcome TEXT NOT NULL,
 assessment_json TEXT NOT NULL CHECK(json_valid(assessment_json)),
 evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
 raw_evidence BLOB NOT NULL CHECK(length(raw_evidence) BETWEEN 1 AND 4194304),
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 published_at TEXT NOT NULL CHECK(length(published_at)=27 AND published_at>=available_at),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE INDEX company_structured_assessment_lookup ON company_structured_transcript_assessments(analysis_id,available_at,assessment_id);
CREATE TRIGGER company_structured_output_source BEFORE INSERT ON company_structured_transcript_outputs
WHEN NOT EXISTS (SELECT 1 FROM company_equibles_transcripts t WHERE t.capture_id=NEW.capture_id AND t.captured_at=NEW.source_available_at)
BEGIN SELECT RAISE(ABORT,'Structured output source differs'); END;
CREATE TRIGGER company_structured_assessment_time BEFORE INSERT ON company_structured_transcript_assessments
WHEN NOT EXISTS (SELECT 1 FROM company_structured_transcript_outputs a WHERE a.analysis_id=NEW.analysis_id AND a.available_at<=NEW.available_at)
BEGIN SELECT RAISE(ABORT,'Assessment predates analysis'); END;
CREATE TRIGGER company_structured_transcript_outputs_immutable_update BEFORE UPDATE ON company_structured_transcript_outputs
BEGIN SELECT RAISE(ABORT,'Structured transcript records are immutable'); END;
CREATE TRIGGER company_structured_transcript_outputs_immutable_delete BEFORE DELETE ON company_structured_transcript_outputs
BEGIN SELECT RAISE(ABORT,'Structured transcript records are immutable'); END;
CREATE TRIGGER company_structured_transcript_outputs_running BEFORE INSERT ON company_structured_transcript_outputs
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.structured')
BEGIN SELECT RAISE(ABORT,'Structured transcript import requires running ingestion'); END;
CREATE TRIGGER company_structured_transcript_assessments_immutable_update BEFORE UPDATE ON company_structured_transcript_assessments
BEGIN SELECT RAISE(ABORT,'Structured transcript records are immutable'); END;
CREATE TRIGGER company_structured_transcript_assessments_immutable_delete BEFORE DELETE ON company_structured_transcript_assessments
BEGIN SELECT RAISE(ABORT,'Structured transcript records are immutable'); END;
CREATE TRIGGER company_structured_transcript_assessments_running BEFORE INSERT ON company_structured_transcript_assessments
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.structured')
BEGIN SELECT RAISE(ABORT,'Structured transcript import requires running ingestion'); END;
