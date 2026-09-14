-- Nasdaq Data Link SF1. Original source evidence and locally observed versions.
CREATE TABLE company_sharadar_artifacts (
 content_sha256 TEXT PRIMARY KEY CHECK(length(content_sha256)=64),
 compression TEXT NOT NULL CHECK(compression='zlib'),
 raw_byte_count INTEGER NOT NULL CHECK(raw_byte_count BETWEEN 1 AND 16777216),
 compressed_body BLOB NOT NULL CHECK(length(compressed_body) BETWEEN 1 AND 16800000),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE TABLE company_sharadar_schema_versions (
 schema_id TEXT PRIMARY KEY CHECK(length(schema_id)=64),
 key_contract_id TEXT NOT NULL CHECK(length(key_contract_id)=64),
 columns_json TEXT NOT NULL,
 primary_key_json TEXT NOT NULL,
 filters_json TEXT NOT NULL,
 metadata_sha256 TEXT NOT NULL REFERENCES company_sharadar_artifacts(content_sha256),
 metadata_captured_at TEXT NOT NULL CHECK(length(metadata_captured_at)=27),
 definition_state TEXT NOT NULL CHECK(definition_state='unresolved'),
 normalization_version TEXT NOT NULL CHECK(normalization_version='nasdaq_sf1.v1'),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE TABLE company_sharadar_captures (
 capture_id TEXT PRIMARY KEY,
 acquisition_id TEXT NOT NULL UNIQUE CHECK(length(acquisition_id)=64),
 scope_sha256 TEXT NOT NULL CHECK(length(scope_sha256)=64),
 scope_json TEXT NOT NULL,
 semantic_hash TEXT NOT NULL CHECK(length(semantic_hash)=64),
 schema_id TEXT NOT NULL REFERENCES company_sharadar_schema_versions(schema_id),
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27),
 ingested_at TEXT NOT NULL CHECK(length(ingested_at)=27 AND ingested_at>=captured_at),
 transport_complete INTEGER NOT NULL CHECK(transport_complete=1),
 coherence TEXT NOT NULL CHECK(coherence='unproven'),
 predecessor_capture_id TEXT REFERENCES company_sharadar_captures(capture_id),
 page_count INTEGER NOT NULL CHECK(page_count BETWEEN 1 AND 100),
 row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 100000),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(capture_id,scope_sha256)
) STRICT;
CREATE INDEX company_sharadar_capture_scope ON company_sharadar_captures(scope_sha256,captured_at);
CREATE TABLE company_sharadar_capture_artifacts (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN -1 AND 99),
 content_sha256 TEXT NOT NULL REFERENCES company_sharadar_artifacts(content_sha256),
 captured_at TEXT NOT NULL CHECK(length(captured_at)=27),
 source_reference TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,ordinal)
) STRICT;
CREATE TABLE company_sharadar_identity_assertions (
 assertion_id TEXT PRIMARY KEY CHECK(length(assertion_id)=64),
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 source_ticker TEXT NOT NULL,
 provider_subject TEXT NOT NULL,
 source_members_json TEXT NOT NULL,
 membership_snapshot_id TEXT NOT NULL,
 mapping_id TEXT NOT NULL,
 instrument_id TEXT,
 cik TEXT CHECK(cik IS NULL OR (length(cik)=10 AND cik NOT GLOB '*[^0-9]*')),
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(capture_id,source_ticker)
) STRICT;
CREATE INDEX company_sharadar_subject_lookup ON company_sharadar_identity_assertions(provider_subject,available_at);
CREATE TABLE company_sharadar_sf1_observations (
 observation_id TEXT PRIMARY KEY CHECK(length(observation_id)=64),
 key_contract_id TEXT NOT NULL CHECK(length(key_contract_id)=64),
 source_key_json TEXT NOT NULL,
 source_ticker TEXT NOT NULL,
 dimension TEXT NOT NULL CHECK(dimension IN ('ARQ','ARY','ART','MRQ','MRY','MRT')),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(key_contract_id,source_key_json)
) STRICT;
CREATE TABLE company_sharadar_sf1_versions (
 version_id TEXT PRIMARY KEY CHECK(length(version_id)=64),
 observation_id TEXT NOT NULL REFERENCES company_sharadar_sf1_observations(observation_id),
 version_sequence INTEGER NOT NULL CHECK(version_sequence>=1),
 predecessor_version_id TEXT REFERENCES company_sharadar_sf1_versions(version_id),
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 schema_id TEXT NOT NULL REFERENCES company_sharadar_schema_versions(schema_id),
 source_datekey TEXT NOT NULL CHECK(length(source_datekey)=10),
 reportperiod TEXT NOT NULL CHECK(length(reportperiod)=10),
 calendardate TEXT NOT NULL CHECK(length(calendardate)=10),
 source_lastupdated TEXT CHECK(source_lastupdated IS NULL OR length(source_lastupdated)=10),
 available_at TEXT NOT NULL CHECK(length(available_at)=27),
 ingested_at TEXT NOT NULL CHECK(length(ingested_at)=27 AND ingested_at>=available_at),
 availability_basis TEXT NOT NULL CHECK(availability_basis='local_capture'),
 values_json TEXT NOT NULL,
 missingness_json TEXT NOT NULL,
 unrecognized_fields_json TEXT NOT NULL,
 value_hash TEXT NOT NULL CHECK(length(value_hash)=64),
 row_semantic_hash TEXT NOT NULL CHECK(length(row_semantic_hash)=64),
 change_kind TEXT NOT NULL CHECK(change_kind IN ('initial','observed_value_change','metadata_change')),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(observation_id,version_sequence),
 UNIQUE(version_id,observation_id),
 CHECK((version_sequence=1)=(predecessor_version_id IS NULL))
) STRICT;
CREATE INDEX company_sharadar_sf1_time ON company_sharadar_sf1_versions(observation_id,available_at,version_sequence);
CREATE INDEX company_sharadar_sf1_period ON company_sharadar_sf1_versions(reportperiod,source_datekey,available_at);
CREATE TABLE company_sharadar_sf1_membership (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 page_ordinal INTEGER NOT NULL CHECK(page_ordinal BETWEEN 0 AND 99),
 source_row_index INTEGER NOT NULL CHECK(source_row_index BETWEEN 1 AND 10000),
 source_row_pointer TEXT NOT NULL CHECK(source_row_pointer='/datatable/data/'||(source_row_index-1)),
 observation_id TEXT NOT NULL,
 version_id TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,page_ordinal,source_row_index),
 FOREIGN KEY(capture_id,page_ordinal) REFERENCES company_sharadar_capture_artifacts(capture_id,ordinal),
 FOREIGN KEY(version_id,observation_id) REFERENCES company_sharadar_sf1_versions(version_id,observation_id)
) STRICT;
CREATE TABLE company_sharadar_sf1_heads (
 observation_id TEXT PRIMARY KEY REFERENCES company_sharadar_sf1_observations(observation_id),
 version_id TEXT NOT NULL UNIQUE,
 FOREIGN KEY(version_id,observation_id) REFERENCES company_sharadar_sf1_versions(version_id,observation_id)
) STRICT;
CREATE TABLE company_sharadar_scope_heads (
 scope_sha256 TEXT PRIMARY KEY,
 capture_id TEXT NOT NULL UNIQUE,
 FOREIGN KEY(capture_id,scope_sha256) REFERENCES company_sharadar_captures(capture_id,scope_sha256)
) STRICT;
CREATE TABLE company_sharadar_quality_findings (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 finding_code TEXT NOT NULL,
 details_json TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,finding_code)
) STRICT;

CREATE TRIGGER company_sharadar_version_lineage BEFORE INSERT ON company_sharadar_sf1_versions
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c WHERE c.capture_id=NEW.capture_id
 AND c.schema_id=NEW.schema_id AND c.captured_at=NEW.available_at AND c.ingested_at=NEW.ingested_at AND c.run_id=NEW.run_id)
 OR (NEW.predecessor_version_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM company_sharadar_sf1_versions v JOIN company_sharadar_sf1_heads h ON h.version_id=v.version_id
 WHERE v.version_id=NEW.predecessor_version_id AND v.observation_id=NEW.observation_id
 AND v.version_sequence+1=NEW.version_sequence AND v.available_at<NEW.available_at))
BEGIN SELECT RAISE(ABORT,'SF1 version lineage differs'); END;
CREATE TRIGGER company_sharadar_member_lineage BEFORE INSERT ON company_sharadar_sf1_membership
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c JOIN company_sharadar_sf1_versions v
 ON v.version_id=NEW.version_id WHERE c.capture_id=NEW.capture_id AND v.available_at<=c.captured_at AND c.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'SF1 membership lineage differs'); END;
CREATE TRIGGER company_sharadar_scope_lineage BEFORE INSERT ON company_sharadar_captures
WHEN NEW.predecessor_capture_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM company_sharadar_captures c JOIN company_sharadar_scope_heads h ON h.capture_id=c.capture_id
 WHERE c.capture_id=NEW.predecessor_capture_id AND c.scope_sha256=NEW.scope_sha256 AND c.captured_at<NEW.captured_at)
BEGIN SELECT RAISE(ABORT,'SF1 capture lineage differs'); END;
CREATE TRIGGER company_sharadar_sf1_heads_advance BEFORE UPDATE ON company_sharadar_sf1_heads
WHEN NEW.observation_id<>OLD.observation_id OR NOT EXISTS(
 SELECT 1 FROM company_sharadar_sf1_versions v WHERE v.version_id=NEW.version_id
 AND v.predecessor_version_id=OLD.version_id AND v.observation_id=OLD.observation_id)
BEGIN SELECT RAISE(ABORT,'SF1 head must advance to its direct successor'); END;
CREATE TRIGGER company_sharadar_scope_heads_advance BEFORE UPDATE ON company_sharadar_scope_heads
WHEN NEW.scope_sha256<>OLD.scope_sha256 OR NOT EXISTS(
 SELECT 1 FROM company_sharadar_captures c WHERE c.capture_id=NEW.capture_id AND c.predecessor_capture_id=OLD.capture_id
 AND c.scope_sha256=OLD.scope_sha256)
BEGIN SELECT RAISE(ABORT,'SF1 scope head must advance to its direct successor'); END;

CREATE TRIGGER company_sharadar_artifacts_no_replace BEFORE INSERT ON company_sharadar_artifacts
WHEN EXISTS(SELECT 1 FROM company_sharadar_artifacts t WHERE t.content_sha256=NEW.content_sha256)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_artifacts_no_delete BEFORE DELETE ON company_sharadar_artifacts
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_artifacts_no_update BEFORE UPDATE ON company_sharadar_artifacts
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_artifacts_running BEFORE INSERT ON company_sharadar_artifacts
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_schema_versions_no_replace BEFORE INSERT ON company_sharadar_schema_versions
WHEN EXISTS(SELECT 1 FROM company_sharadar_schema_versions t WHERE t.schema_id=NEW.schema_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_schema_versions_no_delete BEFORE DELETE ON company_sharadar_schema_versions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_schema_versions_no_update BEFORE UPDATE ON company_sharadar_schema_versions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_schema_versions_running BEFORE INSERT ON company_sharadar_schema_versions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_captures_no_replace BEFORE INSERT ON company_sharadar_captures
WHEN EXISTS(SELECT 1 FROM company_sharadar_captures t WHERE t.capture_id=NEW.capture_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_captures_no_delete BEFORE DELETE ON company_sharadar_captures
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_captures_no_update BEFORE UPDATE ON company_sharadar_captures
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_captures_running BEFORE INSERT ON company_sharadar_captures
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_capture_artifacts_no_replace BEFORE INSERT ON company_sharadar_capture_artifacts
WHEN EXISTS(SELECT 1 FROM company_sharadar_capture_artifacts t WHERE t.capture_id=NEW.capture_id AND t.ordinal=NEW.ordinal)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_capture_artifacts_no_delete BEFORE DELETE ON company_sharadar_capture_artifacts
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_capture_artifacts_no_update BEFORE UPDATE ON company_sharadar_capture_artifacts
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_capture_artifacts_running BEFORE INSERT ON company_sharadar_capture_artifacts
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_identity_assertions_no_replace BEFORE INSERT ON company_sharadar_identity_assertions
WHEN EXISTS(SELECT 1 FROM company_sharadar_identity_assertions t WHERE t.assertion_id=NEW.assertion_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_identity_assertions_no_delete BEFORE DELETE ON company_sharadar_identity_assertions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_identity_assertions_no_update BEFORE UPDATE ON company_sharadar_identity_assertions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_identity_assertions_running BEFORE INSERT ON company_sharadar_identity_assertions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_sf1_observations_no_replace BEFORE INSERT ON company_sharadar_sf1_observations
WHEN EXISTS(SELECT 1 FROM company_sharadar_sf1_observations t WHERE t.observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_sf1_observations_no_delete BEFORE DELETE ON company_sharadar_sf1_observations
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_observations_no_update BEFORE UPDATE ON company_sharadar_sf1_observations
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_observations_running BEFORE INSERT ON company_sharadar_sf1_observations
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_sf1_versions_no_replace BEFORE INSERT ON company_sharadar_sf1_versions
WHEN EXISTS(SELECT 1 FROM company_sharadar_sf1_versions t WHERE t.version_id=NEW.version_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_sf1_versions_no_delete BEFORE DELETE ON company_sharadar_sf1_versions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_versions_no_update BEFORE UPDATE ON company_sharadar_sf1_versions
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_versions_running BEFORE INSERT ON company_sharadar_sf1_versions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_sf1_membership_no_replace BEFORE INSERT ON company_sharadar_sf1_membership
WHEN EXISTS(SELECT 1 FROM company_sharadar_sf1_membership t WHERE t.capture_id=NEW.capture_id AND t.page_ordinal=NEW.page_ordinal AND t.source_row_index=NEW.source_row_index)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_sf1_membership_no_delete BEFORE DELETE ON company_sharadar_sf1_membership
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_membership_no_update BEFORE UPDATE ON company_sharadar_sf1_membership
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_sf1_membership_running BEFORE INSERT ON company_sharadar_sf1_membership
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_sf1_heads_no_replace BEFORE INSERT ON company_sharadar_sf1_heads
WHEN EXISTS(SELECT 1 FROM company_sharadar_sf1_heads t WHERE t.observation_id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_sf1_heads_no_delete BEFORE DELETE ON company_sharadar_sf1_heads
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;

CREATE TRIGGER company_sharadar_scope_heads_no_replace BEFORE INSERT ON company_sharadar_scope_heads
WHEN EXISTS(SELECT 1 FROM company_sharadar_scope_heads t WHERE t.scope_sha256=NEW.scope_sha256)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_scope_heads_no_delete BEFORE DELETE ON company_sharadar_scope_heads
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;

CREATE TRIGGER company_sharadar_quality_findings_no_replace BEFORE INSERT ON company_sharadar_quality_findings
WHEN EXISTS(SELECT 1 FROM company_sharadar_quality_findings t WHERE t.capture_id=NEW.capture_id AND t.finding_code=NEW.finding_code)
BEGIN SELECT RAISE(ABORT,'Sharadar replacement is forbidden'); END;
CREATE TRIGGER company_sharadar_quality_findings_no_delete BEFORE DELETE ON company_sharadar_quality_findings
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_quality_findings_no_update BEFORE UPDATE ON company_sharadar_quality_findings
BEGIN SELECT RAISE(ABORT,'Sharadar evidence is append-only'); END;
CREATE TRIGGER company_sharadar_quality_findings_running BEFORE INSERT ON company_sharadar_quality_findings
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs WHERE run_id=NEW.run_id AND status='running' AND dataset_id='company.sharadar.sf1')
BEGIN SELECT RAISE(ABORT,'Sharadar publication requires its running ingestion'); END;

CREATE TRIGGER company_sharadar_page_lineage BEFORE INSERT ON company_sharadar_capture_artifacts
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c WHERE c.capture_id=NEW.capture_id
 AND c.run_id=NEW.run_id AND NEW.captured_at<=c.captured_at
 AND (NEW.ordinal=-1 OR NEW.ordinal<c.page_count))
BEGIN SELECT RAISE(ABORT,'SF1 page lineage differs'); END;
CREATE TRIGGER company_sharadar_identity_lineage BEFORE INSERT ON company_sharadar_identity_assertions
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c WHERE c.capture_id=NEW.capture_id
 AND c.run_id=NEW.run_id AND c.captured_at=NEW.available_at)
BEGIN SELECT RAISE(ABORT,'SF1 identity assertion lineage differs'); END;
CREATE TRIGGER company_sharadar_heads_initial BEFORE INSERT ON company_sharadar_sf1_heads
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_sf1_versions v JOIN ingestion_runs r ON r.run_id=v.run_id
 WHERE v.version_id=NEW.version_id AND v.observation_id=NEW.observation_id
 AND v.version_sequence=1 AND r.status='running')
BEGIN SELECT RAISE(ABORT,'SF1 initial head requires its running initial version'); END;
CREATE TRIGGER company_sharadar_heads_running BEFORE UPDATE ON company_sharadar_sf1_heads
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_sf1_versions v JOIN ingestion_runs r ON r.run_id=v.run_id
 WHERE v.version_id=NEW.version_id AND r.status='running')
BEGIN SELECT RAISE(ABORT,'SF1 head advancement requires its running version'); END;

CREATE TRIGGER company_sharadar_scope_seal_insert BEFORE INSERT ON company_sharadar_scope_heads
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c JOIN ingestion_runs r ON r.run_id=c.run_id
 WHERE c.capture_id=NEW.capture_id AND c.scope_sha256=NEW.scope_sha256 AND r.status='running'
 AND (SELECT COUNT(*) FROM company_sharadar_capture_artifacts a WHERE a.capture_id=c.capture_id)=c.page_count+1
 AND (SELECT COUNT(*) FROM company_sharadar_sf1_membership m WHERE m.capture_id=c.capture_id)=c.row_count)
BEGIN SELECT RAISE(ABORT,'SF1 complete scope requires all pages and row memberships'); END;

CREATE TRIGGER company_sharadar_scope_seal_update BEFORE UPDATE ON company_sharadar_scope_heads
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c JOIN ingestion_runs r ON r.run_id=c.run_id
 WHERE c.capture_id=NEW.capture_id AND c.scope_sha256=NEW.scope_sha256 AND r.status='running'
 AND (SELECT COUNT(*) FROM company_sharadar_capture_artifacts a WHERE a.capture_id=c.capture_id)=c.page_count+1
 AND (SELECT COUNT(*) FROM company_sharadar_sf1_membership m WHERE m.capture_id=c.capture_id)=c.row_count)
BEGIN SELECT RAISE(ABORT,'SF1 complete scope requires all pages and row memberships'); END;

CREATE TRIGGER company_sharadar_key_contract BEFORE INSERT ON company_sharadar_schema_versions
WHEN EXISTS(SELECT 1 FROM company_sharadar_schema_versions s WHERE s.key_contract_id<>NEW.key_contract_id)
BEGIN SELECT RAISE(ABORT,'SF1 key change requires explicit identity reconciliation'); END;
CREATE TRIGGER company_sharadar_schema_availability BEFORE INSERT ON company_sharadar_captures
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_schema_versions s WHERE s.schema_id=NEW.schema_id
 AND s.metadata_captured_at<=NEW.captured_at)
BEGIN SELECT RAISE(ABORT,'SF1 capture cannot see future schema evidence'); END;
CREATE TRIGGER company_sharadar_metadata_acquisition BEFORE INSERT ON company_sharadar_capture_artifacts
WHEN NEW.ordinal=-1 AND NOT EXISTS(SELECT 1 FROM company_sharadar_captures c
 JOIN company_sharadar_schema_versions s ON s.schema_id=c.schema_id
 WHERE c.capture_id=NEW.capture_id AND s.metadata_captured_at<=NEW.captured_at)
BEGIN SELECT RAISE(ABORT,'SF1 metadata acquisition cannot predate established schema evidence'); END;
