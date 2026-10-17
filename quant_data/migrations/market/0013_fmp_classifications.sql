-- FMP is the sole production classification authority; source captures remain in company.
CREATE TABLE market_classification_versions (
 version_id TEXT PRIMARY KEY,
 content_identity TEXT NOT NULL CHECK(length(content_identity)=64),
 instrument_id TEXT NOT NULL REFERENCES stage10_instruments(instrument_id),
 source_symbol TEXT NOT NULL,
 provider_symbol TEXT NOT NULL,
 classification_provider TEXT NOT NULL CHECK(classification_provider='fmp'),
 sector TEXT NOT NULL CHECK(length(trim(sector)) BETWEEN 1 AND 200),
 industry TEXT NOT NULL CHECK(length(trim(industry)) BETWEEN 1 AND 200),
 membership_snapshot_id TEXT NOT NULL REFERENCES market_collection_snapshots(snapshot_id),
 mapping_id TEXT NOT NULL,
 source_cik TEXT,
 source_mapping_id TEXT NOT NULL,
 source_description_version_id TEXT NOT NULL,
 source_evidence_id TEXT NOT NULL,
 source_content_sha256 TEXT NOT NULL CHECK(length(source_content_sha256)=64),
 source_captured_at TEXT NOT NULL CHECK(length(source_captured_at)=27),
 available_at TEXT NOT NULL CHECK(length(available_at)=27 AND available_at>=source_captured_at),
 version_sequence INTEGER NOT NULL CHECK(version_sequence>0),
 predecessor_version_id TEXT UNIQUE REFERENCES market_classification_versions(version_id),
 run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
 FOREIGN KEY(mapping_id,source_symbol) REFERENCES market_collection_provider_mappings(mapping_id,source_symbol),
 FOREIGN KEY(source_mapping_id,source_symbol) REFERENCES market_collection_provider_mappings(mapping_id,source_symbol),
 UNIQUE(instrument_id,version_sequence),
 CHECK((version_sequence=1 AND predecessor_version_id IS NULL) OR
       (version_sequence>1 AND predecessor_version_id IS NOT NULL))
) STRICT;
CREATE INDEX market_classification_symbol_cutoff ON market_classification_versions(source_symbol,available_at,version_sequence);
CREATE VIEW market_instrument_classifications AS
 SELECT v.* FROM market_classification_versions v
 WHERE NOT EXISTS (SELECT 1 FROM market_classification_versions n
                   WHERE n.instrument_id=v.instrument_id AND n.version_sequence>v.version_sequence);
CREATE TRIGGER market_classification_immutable_update BEFORE UPDATE ON market_classification_versions
BEGIN SELECT RAISE(ABORT,'Classification versions are immutable'); END;
CREATE TRIGGER market_classification_immutable_delete BEFORE DELETE ON market_classification_versions
BEGIN SELECT RAISE(ABORT,'Classification versions are immutable'); END;
CREATE TRIGGER market_classification_no_replace BEFORE INSERT ON market_classification_versions
WHEN EXISTS(SELECT 1 FROM market_classification_versions WHERE version_id=NEW.version_id
 OR (instrument_id=NEW.instrument_id AND version_sequence=NEW.version_sequence))
BEGIN SELECT RAISE(ABORT,'Classification replacement is forbidden'); END;
CREATE TRIGGER market_classification_identity BEFORE INSERT ON market_classification_versions
WHEN NOT EXISTS(SELECT 1 FROM market_collection_provider_mappings m
 JOIN market_collection_mapping_snapshots s ON s.mapping_id=m.mapping_id
 JOIN stage10_instruments i ON i.instrument_id=m.instrument_id
 WHERE m.mapping_id=NEW.mapping_id AND m.source_symbol=NEW.source_symbol
 AND m.membership_snapshot_id=NEW.membership_snapshot_id AND s.provider='fmp'
 AND m.status='resolved' AND m.instrument_id=NEW.instrument_id
 AND m.provider_symbol=NEW.provider_symbol AND i.provider='fmp'
 AND i.provider_symbol=NEW.provider_symbol AND i.asset_type='equity')
OR NOT EXISTS(SELECT 1 FROM market_collection_provider_mappings m
 JOIN market_collection_mapping_snapshots s ON s.mapping_id=m.mapping_id
 WHERE m.mapping_id=NEW.source_mapping_id AND m.source_symbol=NEW.source_symbol
 AND s.provider='fmp' AND m.status='resolved' AND m.instrument_id=NEW.instrument_id
 AND m.provider_symbol=NEW.provider_symbol AND m.cik IS NEW.source_cik)
BEGIN SELECT RAISE(ABORT,'Classification requires the exact FMP equity identity'); END;
CREATE TRIGGER market_classification_predecessor BEFORE INSERT ON market_classification_versions
WHEN NEW.version_sequence>1 AND NOT EXISTS(SELECT 1 FROM market_instrument_classifications p
 WHERE p.version_id=NEW.predecessor_version_id AND p.instrument_id=NEW.instrument_id
 AND p.version_sequence+1=NEW.version_sequence AND p.available_at<NEW.available_at)
BEGIN SELECT RAISE(ABORT,'Classification predecessor or publication time differs'); END;
CREATE TRIGGER market_classification_running BEFORE INSERT ON market_classification_versions
WHEN NOT EXISTS(SELECT 1 FROM ingestion_runs r WHERE r.run_id=NEW.run_id
 AND r.dataset_id='market.instrument_classifications' AND r.status='running')
BEGIN SELECT RAISE(ABORT,'Classification requires its running ingestion'); END;
