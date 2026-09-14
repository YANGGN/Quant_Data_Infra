-- Sharadar direct evidence: preserve established identities, original rows and prior migration bytes.

DROP TRIGGER company_sharadar_member_lineage;

DROP TRIGGER company_sharadar_schema_versions_no_replace;

DROP TRIGGER company_sharadar_schema_versions_no_delete;

DROP TRIGGER company_sharadar_schema_versions_no_update;

DROP TRIGGER company_sharadar_schema_versions_running;

DROP TRIGGER company_sharadar_sf1_membership_no_replace;

DROP TRIGGER company_sharadar_sf1_membership_no_delete;

DROP TRIGGER company_sharadar_sf1_membership_no_update;

DROP TRIGGER company_sharadar_sf1_membership_running;

DROP TRIGGER company_sharadar_scope_seal_insert;

DROP TRIGGER company_sharadar_scope_seal_update;

DROP TRIGGER company_sharadar_key_contract;

DROP TRIGGER company_sharadar_schema_availability;

DROP TRIGGER company_sharadar_metadata_acquisition;

DROP TRIGGER company_sharadar_definition_member_order;

DROP TRIGGER company_sharadar_definition_membership_append_only;

DROP TRIGGER company_sharadar_definition_membership_no_delete;

DROP TRIGGER company_sharadar_definition_membership_running;

DROP TRIGGER company_sharadar_definition_membership_no_replace;

CREATE TABLE company_sharadar_schema_versions_upgrade (
 schema_id TEXT PRIMARY KEY CHECK(length(schema_id)=64),
 key_contract_id TEXT NOT NULL CHECK(length(key_contract_id)=64),
 columns_json TEXT NOT NULL,
 primary_key_json TEXT NOT NULL,
 filters_json TEXT NOT NULL,
 metadata_sha256 TEXT NOT NULL REFERENCES company_sharadar_artifacts(content_sha256),
 metadata_captured_at TEXT NOT NULL CHECK(length(metadata_captured_at)=27),
 definition_state TEXT NOT NULL CHECK(definition_state='unresolved'),
 normalization_version TEXT NOT NULL CHECK(normalization_version IN ('nasdaq_sf1.v1','sharadar_direct.v1')),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

INSERT INTO company_sharadar_schema_versions_upgrade (schema_id,key_contract_id,columns_json,primary_key_json,filters_json,metadata_sha256,metadata_captured_at,definition_state,normalization_version,run_id) SELECT schema_id,key_contract_id,columns_json,primary_key_json,filters_json,metadata_sha256,metadata_captured_at,definition_state,normalization_version,run_id FROM company_sharadar_schema_versions;

DROP TABLE company_sharadar_schema_versions;

ALTER TABLE company_sharadar_schema_versions_upgrade RENAME TO company_sharadar_schema_versions;

CREATE TABLE company_sharadar_sf1_membership_upgrade (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_captures(capture_id),
 page_ordinal INTEGER NOT NULL CHECK(page_ordinal BETWEEN 0 AND 99),
 source_row_index INTEGER NOT NULL CHECK(source_row_index BETWEEN 1 AND 10000),
 source_row_pointer TEXT NOT NULL CHECK(source_row_pointer IN ('/datatable/data/'||(source_row_index-1),'/data/'||(source_row_index-1))),
 observation_id TEXT NOT NULL,
 version_id TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,page_ordinal,source_row_index),
 FOREIGN KEY(capture_id,page_ordinal) REFERENCES company_sharadar_capture_artifacts(capture_id,ordinal),
 FOREIGN KEY(version_id,observation_id) REFERENCES company_sharadar_sf1_versions(version_id,observation_id)
) STRICT;

INSERT INTO company_sharadar_sf1_membership_upgrade (capture_id,page_ordinal,source_row_index,source_row_pointer,observation_id,version_id,run_id) SELECT capture_id,page_ordinal,source_row_index,source_row_pointer,observation_id,version_id,run_id FROM company_sharadar_sf1_membership;

DROP TABLE company_sharadar_sf1_membership;

ALTER TABLE company_sharadar_sf1_membership_upgrade RENAME TO company_sharadar_sf1_membership;

CREATE TABLE company_sharadar_definition_membership_upgrade (
 capture_id TEXT NOT NULL REFERENCES company_sharadar_definition_captures(capture_id),
 observation_id TEXT NOT NULL,
 version_id TEXT NOT NULL,
 page_ordinal INTEGER NOT NULL,
 source_row_index INTEGER NOT NULL CHECK(source_row_index BETWEEN 1 AND 10000),
 source_row_pointer TEXT NOT NULL CHECK(source_row_pointer IN ('/datatable/data/'||(source_row_index-1),'/data/'||(source_row_index-1))),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 PRIMARY KEY(capture_id,observation_id),
 FOREIGN KEY(capture_id,page_ordinal) REFERENCES company_sharadar_definition_capture_artifacts(capture_id,ordinal),
 FOREIGN KEY(version_id,observation_id) REFERENCES company_sharadar_definition_versions(version_id,observation_id)
) STRICT;

INSERT INTO company_sharadar_definition_membership_upgrade (capture_id,observation_id,version_id,page_ordinal,source_row_index,source_row_pointer,run_id) SELECT capture_id,observation_id,version_id,page_ordinal,source_row_index,source_row_pointer,run_id FROM company_sharadar_definition_membership;

DROP TABLE company_sharadar_definition_membership;

ALTER TABLE company_sharadar_definition_membership_upgrade RENAME TO company_sharadar_definition_membership;

CREATE TRIGGER company_sharadar_member_lineage BEFORE INSERT ON company_sharadar_sf1_membership
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_captures c JOIN company_sharadar_sf1_versions v
 ON v.version_id=NEW.version_id WHERE c.capture_id=NEW.capture_id AND v.available_at<=c.captured_at AND c.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'SF1 membership lineage differs'); END;

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

CREATE TRIGGER company_sharadar_definition_member_order BEFORE INSERT ON company_sharadar_definition_membership
WHEN NOT EXISTS(SELECT 1 FROM company_sharadar_definition_captures c JOIN company_sharadar_definition_versions v
 ON v.version_id=NEW.version_id WHERE c.capture_id=NEW.capture_id AND v.available_at<=c.available_at AND c.run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'SF1 definition membership lineage differs'); END;

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

CREATE TRIGGER company_sharadar_direct_member_origin BEFORE INSERT ON company_sharadar_sf1_membership
WHEN NEW.source_row_pointer != (
 SELECT CASE s.normalization_version WHEN 'sharadar_direct.v1' THEN '/data/' ELSE '/datatable/data/' END || (NEW.source_row_index-1)
 FROM company_sharadar_captures c JOIN company_sharadar_schema_versions s ON s.schema_id=c.schema_id
 WHERE c.capture_id=NEW.capture_id)
BEGIN SELECT RAISE(ABORT,'Sharadar membership pointer differs from acquisition channel'); END;

CREATE TRIGGER company_sharadar_direct_definition_origin BEFORE INSERT ON company_sharadar_definition_membership
WHEN NEW.source_row_pointer != (
 SELECT CASE WHEN json_extract(a.parameters_json,'$.tablename')='fundamentals' AND json_extract(a.parameters_json,'$.format')='json'
 THEN '/data/' ELSE '/datatable/data/' END || (NEW.source_row_index-1)
 FROM company_sharadar_definition_capture_artifacts a WHERE a.capture_id=NEW.capture_id AND a.ordinal=NEW.page_ordinal)
BEGIN SELECT RAISE(ABORT,'Sharadar definition pointer differs from acquisition channel'); END;
