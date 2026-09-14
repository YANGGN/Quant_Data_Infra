-- Company-owned SF1 indicator descriptions from Nasdaq INDICATORS.
CREATE TABLE company_sharadar_definition_captures (
 capture_id TEXT PRIMARY KEY,
 acquisition_id TEXT NOT NULL UNIQUE CHECK(length(acquisition_id)=64),
 semantic_hash TEXT NOT NULL CHECK(length(semantic_hash)=64),
 schema_id TEXT NOT NULL CHECK(length(schema_id)=64),
 metadata_sha256 TEXT NOT NULL REFERENCES company_sharadar_artifacts(content_sha256),
 metadata_captured_at TEXT NOT NULL CHECK(length(metadata_captured_at)=27),
 available_at TEXT NOT NULL CHECK(length(available_at)=27 AND available_at>=metadata_captured_at),
 ingested_at TEXT NOT NULL CHECK(length(ingested_at)=27 AND ingested_at>=available_at),
 predecessor_capture_id TEXT REFERENCES company_sharadar_definition_captures(capture_id),
 page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 10),
 row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 10000),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE INDEX company_sharadar_definition_time ON company_sharadar_definition_captures(available_at);
CREATE TABLE company_sharadar_definition_capture_artifacts (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_definition_captures(capture_id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN -1 AND 9),
 content_sha256 TEXT NOT NULL REFERENCES company_sharadar_artifacts(content_sha256),
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27),
 source_reference TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,ordinal)
) STRICT;
CREATE TABLE company_sharadar_definition_versions (
 version_id TEXT PRIMARY KEY,
 observation_id TEXT NOT NULL CHECK(length(observation_id)=64),
 source_key_json TEXT NOT NULL,
 indicator TEXT NOT NULL,
 values_json TEXT NOT NULL,
 semantic_hash TEXT NOT NULL CHECK(length(semantic_hash)=64),
 version_sequence INTEGER NOT NULL CHECK(version_sequence>=1),
 predecessor_version_id TEXT REFERENCES company_sharadar_definition_versions(version_id),
 capture_id TEXT NOT NULL REFERENCES company_sharadar_definition_captures(capture_id),
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(observation_id,version_sequence),
 UNIQUE(version_id,observation_id),
 CHECK((version_sequence=1)=(predecessor_version_id IS NULL))
) STRICT;
CREATE TABLE company_sharadar_definition_membership (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_definition_captures(capture_id),
 observation_id TEXT NOT NULL,
 version_id TEXT NOT NULL,
 page_ordinal INTEGER NOT NULL,
 source_row_index INTEGER NOT NULL CHECK(source_row_index BETWEEN 1 AND 10000),
 source_row_pointer TEXT NOT NULL CHECK(source_row_pointer='/datatable/data/'||(source_row_index-1)),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,observation_id),
 FOREIGN KEY(capture_id,page_ordinal) REFERENCES company_sharadar_definition_capture_artifacts(capture_id,ordinal),
 FOREIGN KEY(version_id,observation_id) REFERENCES company_sharadar_definition_versions(version_id,observation_id)
) STRICT;
CREATE TRIGGER company_sharadar_definition_capture_order BEFORE INSERT ON company_sharadar_definition_captures
WHEN EXISTS(SELECT 1 FROM company_sharadar_definition_captures WHERE available_at>=NEW.available_at OR ingested_at>NEW.ingested_at)
 OR (NEW.predecessor_capture_id IS NOT NULL AND NEW.predecessor_capture_id IS NOT
 (SELECT capture_id FROM company_sharadar_definition_captures ORDER BY available_at DESC LIMIT 1))
 OR (NEW.predecessor_capture_id IS NULL AND EXISTS(SELECT 1 FROM company_sharadar_definition_captures))
BEGIN SELECT RAISE(ABORT,'SF1 definition capture must advance its predecessor'); END;
CREATE TRIGGER company_sharadar_definition_version_order BEFORE INSERT ON company_sharadar_definition_versions
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_definition_captures c
 WHERE c.capture_id=NEW.capture_id AND c.available_at=NEW.available_at AND c.run_id=NEW.run_id)
 OR (NEW.predecessor_version_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM company_sharadar_definition_versions v WHERE v.version_id=NEW.predecessor_version_id
 AND v.observation_id=NEW.observation_id AND v.version_sequence+1=NEW.version_sequence
 AND v.available_at<NEW.available_at AND v.version_sequence=(SELECT max(x.version_sequence)
 FROM company_sharadar_definition_versions x WHERE x.observation_id=NEW.observation_id)))
BEGIN SELECT RAISE(ABORT,'SF1 definition version lineage differs'); END;
CREATE TRIGGER company_sharadar_definition_member_order BEFORE INSERT ON company_sharadar_definition_membership
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_definition_captures c JOIN company_sharadar_definition_versions v
 ON v.version_id=NEW.version_id WHERE c.capture_id=NEW.capture_id AND v.available_at<=c.available_at AND c.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'SF1 definition membership lineage differs'); END;

CREATE TRIGGER company_sharadar_definition_captures_append_only BEFORE UPDATE ON company_sharadar_definition_captures
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_captures_no_delete BEFORE DELETE ON company_sharadar_definition_captures
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_captures_running BEFORE INSERT ON company_sharadar_definition_captures
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.definitions')
BEGIN SELECT RAISE(ABORT,'SF1 definition requires its running ingestion'); END;
CREATE TRIGGER company_sharadar_definition_captures_no_replace BEFORE INSERT ON company_sharadar_definition_captures
WHEN EXISTS(SELECT 1 FROM company_sharadar_definition_captures WHERE capture_id=NEW.capture_id OR acquisition_id=NEW.acquisition_id)
BEGIN SELECT RAISE(ABORT,'SF1 definition replacement is forbidden'); END;

CREATE TRIGGER company_sharadar_definition_capture_artifacts_append_only BEFORE UPDATE ON company_sharadar_definition_capture_artifacts
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_capture_artifacts_no_delete BEFORE DELETE ON company_sharadar_definition_capture_artifacts
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_capture_artifacts_running BEFORE INSERT ON company_sharadar_definition_capture_artifacts
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.definitions')
BEGIN SELECT RAISE(ABORT,'SF1 definition requires its running ingestion'); END;
CREATE TRIGGER company_sharadar_definition_capture_artifacts_no_replace BEFORE INSERT ON company_sharadar_definition_capture_artifacts
WHEN EXISTS(SELECT 1 FROM company_sharadar_definition_capture_artifacts WHERE capture_id=NEW.capture_id AND ordinal=NEW.ordinal)
BEGIN SELECT RAISE(ABORT,'SF1 definition replacement is forbidden'); END;

CREATE TRIGGER company_sharadar_definition_versions_append_only BEFORE UPDATE ON company_sharadar_definition_versions
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_versions_no_delete BEFORE DELETE ON company_sharadar_definition_versions
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_versions_running BEFORE INSERT ON company_sharadar_definition_versions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.definitions')
BEGIN SELECT RAISE(ABORT,'SF1 definition requires its running ingestion'); END;
CREATE TRIGGER company_sharadar_definition_versions_no_replace BEFORE INSERT ON company_sharadar_definition_versions
WHEN EXISTS(SELECT 1 FROM company_sharadar_definition_versions WHERE version_id=NEW.version_id OR (observation_id=NEW.observation_id AND version_sequence=NEW.version_sequence))
BEGIN SELECT RAISE(ABORT,'SF1 definition replacement is forbidden'); END;

CREATE TRIGGER company_sharadar_definition_membership_append_only BEFORE UPDATE ON company_sharadar_definition_membership
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_membership_no_delete BEFORE DELETE ON company_sharadar_definition_membership
BEGIN SELECT RAISE(ABORT,'SF1 definitions are append-only'); END;
CREATE TRIGGER company_sharadar_definition_membership_running BEFORE INSERT ON company_sharadar_definition_membership
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.definitions')
BEGIN SELECT RAISE(ABORT,'SF1 definition requires its running ingestion'); END;
CREATE TRIGGER company_sharadar_definition_membership_no_replace BEFORE INSERT ON company_sharadar_definition_membership
WHEN EXISTS(SELECT 1 FROM company_sharadar_definition_membership WHERE capture_id=NEW.capture_id AND observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT,'SF1 definition replacement is forbidden'); END;


-- Permit the new definition publisher to reuse the immutable Nasdaq blob table.
-- Every other existing SF1 guard remains unchanged.
DROP TRIGGER company_sharadar_artifacts_running;
CREATE TRIGGER company_sharadar_artifacts_running BEFORE INSERT ON company_sharadar_artifacts
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running'
 AND dataset_id IN ('company.sharadar.sf1','company.sharadar.definitions'))
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_definition_artifact_lineage BEFORE INSERT ON company_sharadar_definition_capture_artifacts
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_definition_captures c WHERE c.capture_id=NEW.capture_id
 AND c.run_id=NEW.run_id AND NEW.captured_at BETWEEN c.metadata_captured_at AND c.available_at
 AND ((NEW.ordinal=-1 AND NEW.content_sha256=c.metadata_sha256 AND NEW.captured_at=c.metadata_captured_at)
 OR (NEW.ordinal>=0 AND NEW.ordinal<c.page_count)))
BEGIN SELECT RAISE(ABORT,'SF1 definition artifact lineage differs'); END;
