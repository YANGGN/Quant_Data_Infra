-- Immutable source manifests and observed collection membership transitions.
-- Existing fixture-era and Stage10 universe/identity relations remain unchanged.
CREATE TABLE market_collection_universes (
 universe_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 source_namespace TEXT NOT NULL,
 created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE TABLE market_collection_artifacts (
 artifact_id TEXT PRIMARY KEY REFERENCES ingestion_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 media_type TEXT NOT NULL,
 raw_body BLOB NOT NULL,
 byte_count INTEGER NOT NULL CHECK(byte_count>0 AND byte_count=length(raw_body)),
 source_reference TEXT NOT NULL,
 captured_at TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE TABLE market_collection_snapshots (
 snapshot_id TEXT PRIMARY KEY,
 universe_id TEXT NOT NULL REFERENCES market_collection_universes(universe_id),
 artifact_id TEXT NOT NULL REFERENCES market_collection_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
 acquisition_identity TEXT NOT NULL UNIQUE CHECK(length(acquisition_identity)=64),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 state_sha256 TEXT NOT NULL CHECK(length(state_sha256)=64),
 membership_sha256 TEXT NOT NULL CHECK(length(membership_sha256)=64),
 source_label_date TEXT,
 captured_at TEXT NOT NULL,
 version_sequence INTEGER NOT NULL CHECK(version_sequence>0),
 predecessor_snapshot_id TEXT UNIQUE REFERENCES market_collection_snapshots(snapshot_id),
 member_count INTEGER NOT NULL CHECK(member_count BETWEEN 1 AND 5000),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(universe_id,version_sequence),
 CHECK((version_sequence=1 AND predecessor_snapshot_id IS NULL) OR (version_sequence>1 AND predecessor_snapshot_id IS NOT NULL))
) STRICT;
CREATE TABLE market_collection_members (
 snapshot_id TEXT NOT NULL REFERENCES market_collection_snapshots(snapshot_id),
 source_symbol TEXT NOT NULL,
 source_row INTEGER NOT NULL CHECK(source_row>=2),
 display_name TEXT NOT NULL,
 source_payload_json TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,source_symbol),
 UNIQUE(snapshot_id,source_row)
) STRICT;
CREATE TABLE market_collection_heads (
 universe_id TEXT PRIMARY KEY REFERENCES market_collection_universes(universe_id),
 snapshot_id TEXT NOT NULL UNIQUE REFERENCES market_collection_snapshots(snapshot_id)
) STRICT;
CREATE INDEX market_collection_snapshots_cutoff ON market_collection_snapshots(universe_id,captured_at,version_sequence);
CREATE TRIGGER market_collection_snapshot_predecessor BEFORE INSERT ON market_collection_snapshots
WHEN NEW.predecessor_snapshot_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM market_collection_snapshots p JOIN market_collection_heads h ON h.snapshot_id=p.snapshot_id
 WHERE p.snapshot_id=NEW.predecessor_snapshot_id AND p.universe_id=NEW.universe_id
 AND p.version_sequence+1=NEW.version_sequence AND p.captured_at<NEW.captured_at
)
BEGIN SELECT RAISE(ABORT,'Collection predecessor must be the current earlier snapshot'); END;
CREATE TRIGGER market_collection_head_insert BEFORE INSERT ON market_collection_heads
WHEN NOT EXISTS(SELECT 1 FROM market_collection_snapshots s WHERE s.snapshot_id=NEW.snapshot_id AND s.universe_id=NEW.universe_id)
BEGIN SELECT RAISE(ABORT,'Collection head scope mismatch'); END;
CREATE TRIGGER market_collection_head_update BEFORE UPDATE ON market_collection_heads
WHEN NEW.universe_id<>OLD.universe_id OR NOT EXISTS(
 SELECT 1 FROM market_collection_snapshots s WHERE s.snapshot_id=NEW.snapshot_id
 AND s.universe_id=NEW.universe_id AND s.predecessor_snapshot_id=OLD.snapshot_id)
BEGIN SELECT RAISE(ABORT,'Collection head must advance one transition'); END;
CREATE TRIGGER market_collection_head_delete BEFORE DELETE ON market_collection_heads
BEGIN SELECT RAISE(ABORT,'Collection heads cannot be deleted'); END;
CREATE TRIGGER market_collection_universes_update BEFORE UPDATE ON market_collection_universes
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_universes_delete BEFORE DELETE ON market_collection_universes
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_artifacts_update BEFORE UPDATE ON market_collection_artifacts
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_artifacts_delete BEFORE DELETE ON market_collection_artifacts
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_snapshots_update BEFORE UPDATE ON market_collection_snapshots
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_snapshots_delete BEFORE DELETE ON market_collection_snapshots
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_members_update BEFORE UPDATE ON market_collection_members
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;
CREATE TRIGGER market_collection_members_delete BEFORE DELETE ON market_collection_members
BEGIN SELECT RAISE(ABORT,'Collection evidence and membership are immutable'); END;

CREATE TRIGGER market_collection_universes_replace BEFORE INSERT ON market_collection_universes
WHEN EXISTS(SELECT 1 FROM market_collection_universes WHERE universe_id=NEW.universe_id)
BEGIN SELECT RAISE(ABORT,'Collection replacement is forbidden'); END;

CREATE TRIGGER market_collection_artifacts_replace BEFORE INSERT ON market_collection_artifacts
WHEN EXISTS(SELECT 1 FROM market_collection_artifacts WHERE artifact_id=NEW.artifact_id)
BEGIN SELECT RAISE(ABORT,'Collection replacement is forbidden'); END;

CREATE TRIGGER market_collection_snapshots_replace BEFORE INSERT ON market_collection_snapshots
WHEN EXISTS(SELECT 1 FROM market_collection_snapshots WHERE snapshot_id=NEW.snapshot_id OR acquisition_identity=NEW.acquisition_identity OR semantic_identity=NEW.semantic_identity OR (universe_id=NEW.universe_id AND version_sequence=NEW.version_sequence) OR predecessor_snapshot_id=NEW.predecessor_snapshot_id)
BEGIN SELECT RAISE(ABORT,'Collection replacement is forbidden'); END;

CREATE TRIGGER market_collection_members_replace BEFORE INSERT ON market_collection_members
WHEN EXISTS(SELECT 1 FROM market_collection_members WHERE snapshot_id=NEW.snapshot_id AND (source_symbol=NEW.source_symbol OR source_row=NEW.source_row))
BEGIN SELECT RAISE(ABORT,'Collection replacement is forbidden'); END;

CREATE TRIGGER market_collection_heads_replace BEFORE INSERT ON market_collection_heads
WHEN EXISTS(SELECT 1 FROM market_collection_heads WHERE universe_id=NEW.universe_id OR snapshot_id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT,'Collection replacement is forbidden'); END;

CREATE TRIGGER market_collection_member_sealed BEFORE INSERT ON market_collection_members
WHEN EXISTS(SELECT 1 FROM market_collection_heads WHERE snapshot_id=NEW.snapshot_id)
 OR EXISTS(SELECT 1 FROM market_collection_snapshots WHERE predecessor_snapshot_id=NEW.snapshot_id)
BEGIN SELECT RAISE(ABORT,'Published collection membership is sealed'); END;
CREATE TRIGGER market_collection_head_complete_insert BEFORE INSERT ON market_collection_heads
WHEN NOT EXISTS(SELECT 1 FROM market_collection_snapshots s WHERE s.snapshot_id=NEW.snapshot_id
 AND s.member_count=(SELECT count(*) FROM market_collection_members m WHERE m.snapshot_id=s.snapshot_id))
BEGIN SELECT RAISE(ABORT,'Collection membership must be complete'); END;
CREATE TRIGGER market_collection_head_complete_update BEFORE UPDATE ON market_collection_heads
WHEN NOT EXISTS(SELECT 1 FROM market_collection_snapshots s WHERE s.snapshot_id=NEW.snapshot_id
 AND s.member_count=(SELECT count(*) FROM market_collection_members m WHERE m.snapshot_id=s.snapshot_id))
BEGIN SELECT RAISE(ABORT,'Collection membership must be complete'); END;

-- Provider assertions are full, independently versioned mapping snapshots.
CREATE TABLE market_collection_mapping_snapshots (
 mapping_id TEXT PRIMARY KEY,
 membership_snapshot_id TEXT NOT NULL REFERENCES market_collection_snapshots(snapshot_id),
 provider TEXT NOT NULL CHECK(provider IN ('fmp','sec','sharadar','equibles','alpaca')),
 artifact_id TEXT NOT NULL REFERENCES market_collection_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
 acquisition_identity TEXT NOT NULL UNIQUE CHECK(length(acquisition_identity)=64),
 semantic_identity TEXT NOT NULL UNIQUE CHECK(length(semantic_identity)=64),
 state_sha256 TEXT NOT NULL CHECK(length(state_sha256)=64),
 captured_at TEXT NOT NULL,
 version_sequence INTEGER NOT NULL CHECK(version_sequence>0),
 predecessor_mapping_id TEXT UNIQUE REFERENCES market_collection_mapping_snapshots(mapping_id),
 member_count INTEGER NOT NULL CHECK(member_count BETWEEN 1 AND 5000),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 UNIQUE(membership_snapshot_id,provider,version_sequence),
 CHECK((version_sequence=1 AND predecessor_mapping_id IS NULL) OR (version_sequence>1 AND predecessor_mapping_id IS NOT NULL))
) STRICT;
CREATE TABLE market_collection_provider_mappings (
 mapping_id TEXT NOT NULL REFERENCES market_collection_mapping_snapshots(mapping_id),
 membership_snapshot_id TEXT NOT NULL,
 source_symbol TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('resolved','unresolved','unsupported','ambiguous')),
 provider_symbol TEXT,
 provider_subject TEXT,
 instrument_id TEXT REFERENCES stage10_instruments(instrument_id),
 cik TEXT CHECK(cik IS NULL OR (length(cik)=10 AND cik NOT GLOB '*[^0-9]*')),
 evidence_sha256 TEXT CHECK(evidence_sha256 IS NULL OR length(evidence_sha256)=64),
 evidence_id TEXT REFERENCES market_collection_identity_evidence(evidence_id),
 association_evidence_id TEXT REFERENCES market_collection_identity_evidence(evidence_id),
 evidence_reference TEXT,
 evidence_pointer TEXT,
 reason TEXT NOT NULL,
 PRIMARY KEY(mapping_id,source_symbol),
 FOREIGN KEY(membership_snapshot_id,source_symbol) REFERENCES market_collection_members(snapshot_id,source_symbol),
 CHECK((status='resolved' AND provider_subject IS NOT NULL AND evidence_sha256 IS NOT NULL) OR status<>'resolved')
) STRICT;
CREATE TABLE market_collection_identity_evidence (
 evidence_id TEXT PRIMARY KEY,
 evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
 provider TEXT NOT NULL CHECK(provider IN ('fmp','sec','sharadar','equibles','alpaca')),
 raw_body BLOB NOT NULL CHECK(length(raw_body)>0),
 media_type TEXT NOT NULL CHECK(media_type='application/json'),
 source_reference TEXT NOT NULL,
 captured_at TEXT NOT NULL,
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;
CREATE INDEX market_collection_identity_origin_time ON market_collection_identity_evidence(provider,evidence_sha256,captured_at);
CREATE TABLE market_collection_mapping_heads (
 membership_snapshot_id TEXT NOT NULL REFERENCES market_collection_snapshots(snapshot_id),
 provider TEXT NOT NULL,
 mapping_id TEXT NOT NULL UNIQUE REFERENCES market_collection_mapping_snapshots(mapping_id),
 PRIMARY KEY(membership_snapshot_id,provider)
) STRICT;
CREATE INDEX market_collection_mapping_cutoff ON market_collection_mapping_snapshots(membership_snapshot_id,provider,captured_at);
CREATE TRIGGER market_collection_mapping_predecessor BEFORE INSERT ON market_collection_mapping_snapshots
WHEN NEW.predecessor_mapping_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM market_collection_mapping_snapshots p JOIN market_collection_mapping_heads h ON h.mapping_id=p.mapping_id
 WHERE p.mapping_id=NEW.predecessor_mapping_id AND p.membership_snapshot_id=NEW.membership_snapshot_id
 AND p.provider=NEW.provider AND p.version_sequence+1=NEW.version_sequence AND p.captured_at<NEW.captured_at)
BEGIN SELECT RAISE(ABORT,'Mapping predecessor must be the current earlier snapshot'); END;
CREATE TRIGGER market_collection_mapping_head_insert BEFORE INSERT ON market_collection_mapping_heads
WHEN NOT EXISTS(SELECT 1 FROM market_collection_mapping_snapshots s WHERE s.mapping_id=NEW.mapping_id
 AND s.membership_snapshot_id=NEW.membership_snapshot_id AND s.provider=NEW.provider
 AND s.member_count=(SELECT count(*) FROM market_collection_provider_mappings m WHERE m.mapping_id=s.mapping_id))
BEGIN SELECT RAISE(ABORT,'Mapping head must reference complete matching scope'); END;
CREATE TRIGGER market_collection_mapping_head_update BEFORE UPDATE ON market_collection_mapping_heads
WHEN NEW.membership_snapshot_id<>OLD.membership_snapshot_id OR NEW.provider<>OLD.provider OR NOT EXISTS(
 SELECT 1 FROM market_collection_mapping_snapshots s WHERE s.mapping_id=NEW.mapping_id
 AND s.membership_snapshot_id=NEW.membership_snapshot_id AND s.provider=NEW.provider AND s.predecessor_mapping_id=OLD.mapping_id
 AND s.member_count=(SELECT count(*) FROM market_collection_provider_mappings m WHERE m.mapping_id=s.mapping_id))
BEGIN SELECT RAISE(ABORT,'Mapping head must advance complete scope one transition'); END;
CREATE TRIGGER market_collection_mapping_head_delete BEFORE DELETE ON market_collection_mapping_heads
BEGIN SELECT RAISE(ABORT,'Mapping heads cannot be deleted'); END;
CREATE TRIGGER market_collection_mapping_member_insert BEFORE INSERT ON market_collection_provider_mappings
WHEN EXISTS(SELECT 1 FROM market_collection_mapping_heads WHERE mapping_id=NEW.mapping_id)
 OR EXISTS(SELECT 1 FROM market_collection_mapping_snapshots WHERE predecessor_mapping_id=NEW.mapping_id)
 OR NOT EXISTS(SELECT 1 FROM market_collection_mapping_snapshots WHERE mapping_id=NEW.mapping_id AND membership_snapshot_id=NEW.membership_snapshot_id)
 OR (NEW.evidence_sha256 IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM market_collection_identity_evidence e JOIN market_collection_mapping_snapshots s ON s.mapping_id=NEW.mapping_id
 WHERE e.evidence_id=NEW.evidence_id AND e.evidence_sha256=NEW.evidence_sha256 AND e.source_reference=NEW.evidence_reference
 AND e.provider=s.provider AND e.captured_at<=s.captured_at))
 OR (NEW.association_evidence_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM market_collection_identity_evidence e JOIN market_collection_mapping_snapshots s ON s.mapping_id=NEW.mapping_id
 WHERE e.evidence_id=NEW.association_evidence_id AND e.provider=s.provider AND e.captured_at<=s.captured_at))
BEGIN SELECT RAISE(ABORT,'Mapping row has invalid evidence, scope or sealed membership'); END;

CREATE TRIGGER market_collection_mapping_snapshots_replace BEFORE INSERT ON market_collection_mapping_snapshots
WHEN EXISTS(SELECT 1 FROM market_collection_mapping_snapshots WHERE mapping_id=NEW.mapping_id OR acquisition_identity=NEW.acquisition_identity OR semantic_identity=NEW.semantic_identity OR (membership_snapshot_id=NEW.membership_snapshot_id AND provider=NEW.provider AND version_sequence=NEW.version_sequence) OR predecessor_mapping_id=NEW.predecessor_mapping_id)
BEGIN SELECT RAISE(ABORT,'Mapping replacement is forbidden'); END;
CREATE TRIGGER market_collection_mapping_snapshots_update BEFORE UPDATE ON market_collection_mapping_snapshots
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;
CREATE TRIGGER market_collection_mapping_snapshots_delete BEFORE DELETE ON market_collection_mapping_snapshots
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;

CREATE TRIGGER market_collection_provider_mappings_replace BEFORE INSERT ON market_collection_provider_mappings
WHEN EXISTS(SELECT 1 FROM market_collection_provider_mappings WHERE mapping_id=NEW.mapping_id AND source_symbol=NEW.source_symbol)
BEGIN SELECT RAISE(ABORT,'Mapping replacement is forbidden'); END;
CREATE TRIGGER market_collection_provider_mappings_update BEFORE UPDATE ON market_collection_provider_mappings
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;
CREATE TRIGGER market_collection_provider_mappings_delete BEFORE DELETE ON market_collection_provider_mappings
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;

CREATE TRIGGER market_collection_identity_evidence_replace BEFORE INSERT ON market_collection_identity_evidence
WHEN EXISTS(SELECT 1 FROM market_collection_identity_evidence WHERE evidence_id=NEW.evidence_id)
BEGIN SELECT RAISE(ABORT,'Mapping replacement is forbidden'); END;
CREATE TRIGGER market_collection_identity_evidence_update BEFORE UPDATE ON market_collection_identity_evidence
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;
CREATE TRIGGER market_collection_identity_evidence_delete BEFORE DELETE ON market_collection_identity_evidence
BEGIN SELECT RAISE(ABORT,'Mapping evidence and assertions are immutable'); END;

CREATE TRIGGER market_collection_mapping_heads_replace BEFORE INSERT ON market_collection_mapping_heads
WHEN EXISTS(SELECT 1 FROM market_collection_mapping_heads WHERE mapping_id=NEW.mapping_id OR (membership_snapshot_id=NEW.membership_snapshot_id AND provider=NEW.provider))
BEGIN SELECT RAISE(ABORT,'Mapping replacement is forbidden'); END;
