-- Company-owned derived transcript analysis. Original calls remain immutable evidence.
CREATE TABLE company_transcript_analyses (
 analysis_id TEXT PRIMARY KEY,
 request_identity TEXT NOT NULL UNIQUE CHECK(length(request_identity)=64),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 capture_id TEXT NOT NULL REFERENCES company_equibles_transcripts(capture_id),
 input_sha256 TEXT NOT NULL CHECK(length(input_sha256)=64),
 configuration_json TEXT NOT NULL,
 source_available_at TEXT NOT NULL,
 available_at TEXT NOT NULL,
 model TEXT NOT NULL CHECK(model='gpt-5.6-terra'),
 reasoning_effort TEXT NOT NULL CHECK(reasoning_effort='high'),
 source_json TEXT NOT NULL,
 response_sha256 TEXT NOT NULL CHECK(length(response_sha256)=64),
 raw_response BLOB NOT NULL CHECK(length(raw_response) BETWEEN 1 AND 4194304),
 output_json TEXT NOT NULL,
 derived_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 usage_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 CHECK(length(available_at)=27 AND available_at >= source_available_at)
) STRICT;
CREATE INDEX company_transcript_analysis_lookup ON company_transcript_analyses(capture_id,available_at,analysis_id);
CREATE TABLE company_guidance_claims (
 analysis_id TEXT NOT NULL REFERENCES company_transcript_analyses(analysis_id),
 claim_local_id TEXT NOT NULL,
 metric TEXT NOT NULL,
 fiscal_year INTEGER,
 fiscal_quarter INTEGER,
 accounting_basis TEXT NOT NULL,
 measurement TEXT NOT NULL,
 shape TEXT NOT NULL,
 point_value TEXT,
 low_value TEXT,
 high_value TEXT,
 unit TEXT NOT NULL,
 currency TEXT,
 item_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(analysis_id,claim_local_id)
) STRICT;
CREATE INDEX company_guidance_claim_metric ON company_guidance_claims(metric,fiscal_year,fiscal_quarter);
CREATE TABLE company_analyst_question_blocks (
 analysis_id TEXT NOT NULL REFERENCES company_transcript_analyses(analysis_id),
 question_local_id TEXT NOT NULL,
 item_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(analysis_id,question_local_id)
) STRICT;
CREATE TABLE company_analyst_topics (
 analysis_id TEXT NOT NULL REFERENCES company_transcript_analyses(analysis_id),
 topic_local_id TEXT NOT NULL,
 topic_code TEXT NOT NULL,
 label TEXT NOT NULL,
 item_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(analysis_id,topic_local_id)
) STRICT;
CREATE TABLE company_management_tone_assessments (
 analysis_id TEXT NOT NULL REFERENCES company_transcript_analyses(analysis_id),
 assessment_key TEXT NOT NULL,
 sentiment TEXT NOT NULL,
 expressed_confidence TEXT NOT NULL,
 hedging TEXT NOT NULL,
 item_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(analysis_id,assessment_key)
) STRICT;
CREATE TABLE company_transcript_analysis_reviews (
 review_id TEXT PRIMARY KEY,
 request_identity TEXT NOT NULL UNIQUE CHECK(length(request_identity)=64),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 analysis_id TEXT NOT NULL REFERENCES company_transcript_analyses(analysis_id),
 configuration_json TEXT NOT NULL,
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 model TEXT NOT NULL CHECK(model='gpt-5.6-sol'),
 reasoning_effort TEXT NOT NULL CHECK(reasoning_effort='high'),
 verdict TEXT NOT NULL CHECK(verdict IN ('accepted','needs_changes','insufficient_evidence')),
 response_sha256 TEXT NOT NULL CHECK(length(response_sha256)=64),
 raw_response BLOB NOT NULL CHECK(length(raw_response) BETWEEN 1 AND 4194304),
 output_json TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 usage_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE INDEX company_transcript_review_lookup ON company_transcript_analysis_reviews(analysis_id,available_at,review_id);
CREATE TRIGGER company_transcript_analysis_source_guard BEFORE INSERT ON company_transcript_analyses
WHEN NOT EXISTS (SELECT 1 FROM company_equibles_transcripts t WHERE t.capture_id=NEW.capture_id AND t.captured_at=NEW.source_available_at)
BEGIN SELECT RAISE(ABORT,'Analysis source availability mismatch'); END;
CREATE TRIGGER company_transcript_review_time_guard BEFORE INSERT ON company_transcript_analysis_reviews
WHEN NOT EXISTS (SELECT 1 FROM company_transcript_analyses a WHERE a.analysis_id=NEW.analysis_id AND a.available_at<=NEW.available_at)
BEGIN SELECT RAISE(ABORT,'Review predates analysis'); END;

CREATE TRIGGER company_transcript_analyses_running BEFORE INSERT ON company_transcript_analyses
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_transcript_analyses_immutable_update BEFORE UPDATE ON company_transcript_analyses
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_transcript_analyses_immutable_delete BEFORE DELETE ON company_transcript_analyses
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_guidance_claims_running BEFORE INSERT ON company_guidance_claims
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_guidance_claims_immutable_update BEFORE UPDATE ON company_guidance_claims
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_guidance_claims_immutable_delete BEFORE DELETE ON company_guidance_claims
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_analyst_question_blocks_running BEFORE INSERT ON company_analyst_question_blocks
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_analyst_question_blocks_immutable_update BEFORE UPDATE ON company_analyst_question_blocks
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_analyst_question_blocks_immutable_delete BEFORE DELETE ON company_analyst_question_blocks
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_analyst_topics_running BEFORE INSERT ON company_analyst_topics
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_analyst_topics_immutable_update BEFORE UPDATE ON company_analyst_topics
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_analyst_topics_immutable_delete BEFORE DELETE ON company_analyst_topics
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_management_tone_assessments_running BEFORE INSERT ON company_management_tone_assessments
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_management_tone_assessments_immutable_update BEFORE UPDATE ON company_management_tone_assessments
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_management_tone_assessments_immutable_delete BEFORE DELETE ON company_management_tone_assessments
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_transcript_analysis_reviews_running BEFORE INSERT ON company_transcript_analysis_reviews
WHEN NOT EXISTS (SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.transcript.analysis')
BEGIN SELECT RAISE(ABORT,'Transcript analysis requires a running ingestion'); END;
CREATE TRIGGER company_transcript_analysis_reviews_immutable_update BEFORE UPDATE ON company_transcript_analysis_reviews
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;
CREATE TRIGGER company_transcript_analysis_reviews_immutable_delete BEFORE DELETE ON company_transcript_analysis_reviews
BEGIN SELECT RAISE(ABORT,'Transcript analysis is immutable'); END;

CREATE TRIGGER company_guidance_claims_parent_run BEFORE INSERT ON company_guidance_claims
WHEN NOT EXISTS (SELECT 1 FROM company_transcript_analyses a WHERE a.analysis_id=NEW.analysis_id AND a.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'Analysis child must share its parent ingestion'); END;

CREATE TRIGGER company_analyst_question_blocks_parent_run BEFORE INSERT ON company_analyst_question_blocks
WHEN NOT EXISTS (SELECT 1 FROM company_transcript_analyses a WHERE a.analysis_id=NEW.analysis_id AND a.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'Analysis child must share its parent ingestion'); END;

CREATE TRIGGER company_analyst_topics_parent_run BEFORE INSERT ON company_analyst_topics
WHEN NOT EXISTS (SELECT 1 FROM company_transcript_analyses a WHERE a.analysis_id=NEW.analysis_id AND a.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'Analysis child must share its parent ingestion'); END;

CREATE TRIGGER company_management_tone_assessments_parent_run BEFORE INSERT ON company_management_tone_assessments
WHEN NOT EXISTS (SELECT 1 FROM company_transcript_analyses a WHERE a.analysis_id=NEW.analysis_id AND a.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'Analysis child must share its parent ingestion'); END;
