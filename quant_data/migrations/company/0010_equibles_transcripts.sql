-- Original transcript bytes; availability is local capture, never source callDate.
CREATE TABLE company_equibles_transcripts (
 capture_id TEXT PRIMARY KEY,
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 symbol TEXT NOT NULL,
 instrument_id TEXT NOT NULL,
 event_id TEXT NOT NULL,
 fiscal_year INTEGER NOT NULL CHECK(fiscal_year BETWEEN 1900 AND 2200),
 fiscal_quarter INTEGER NOT NULL CHECK(fiscal_quarter BETWEEN 1 AND 4),
 event_json TEXT NOT NULL,
 total_turn_count INTEGER NOT NULL CHECK(total_turn_count BETWEEN 1 AND 10000),
 page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 50),
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27 AND captured_at GLOB '????-??-??T??:??:??.??????Z'),
 warnings_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE TABLE company_equibles_transcript_pages (
 capture_id TEXT NOT NULL REFERENCES company_equibles_transcripts(capture_id) DEFERRABLE INITIALLY DEFERRED,
 page_index INTEGER NOT NULL CHECK(page_index BETWEEN 0 AND 49),
 turn_offset INTEGER NOT NULL CHECK(turn_offset BETWEEN 0 AND 9999),
 turn_count INTEGER NOT NULL CHECK(turn_count BETWEEN 1 AND 200),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 raw_body BLOB NOT NULL CHECK(length(raw_body) BETWEEN 1 AND 8388608),
 captured_at TEXT NOT NULL,
 source_reference TEXT NOT NULL,
 headers_json TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES ingestion_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
 PRIMARY KEY(capture_id,page_index),
 UNIQUE(capture_id,turn_offset)
) STRICT;
CREATE INDEX company_equibles_transcripts_lookup ON company_equibles_transcripts(symbol,fiscal_year,fiscal_quarter,captured_at);

CREATE TRIGGER company_equibles_transcripts_running BEFORE INSERT ON company_equibles_transcripts
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'Equibles publication requires a running run'); END;
CREATE TRIGGER company_equibles_transcripts_immutable_update BEFORE UPDATE ON company_equibles_transcripts
BEGIN SELECT RAISE(ABORT,'Equibles transcript evidence is append-only'); END;
CREATE TRIGGER company_equibles_transcripts_immutable_delete BEFORE DELETE ON company_equibles_transcripts
BEGIN SELECT RAISE(ABORT,'Equibles transcript evidence is append-only'); END;

CREATE TRIGGER company_equibles_transcript_pages_running BEFORE INSERT ON company_equibles_transcript_pages
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running')
BEGIN SELECT RAISE(ABORT,'Equibles publication requires a running run'); END;
CREATE TRIGGER company_equibles_transcript_pages_immutable_update BEFORE UPDATE ON company_equibles_transcript_pages
BEGIN SELECT RAISE(ABORT,'Equibles transcript evidence is append-only'); END;
CREATE TRIGGER company_equibles_transcript_pages_immutable_delete BEFORE DELETE ON company_equibles_transcript_pages
BEGIN SELECT RAISE(ABORT,'Equibles transcript evidence is append-only'); END;
