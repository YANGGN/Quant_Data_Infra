-- Deliberate Stage 3 reconstruction: catalog facts remain fixture-gated.

CREATE TABLE market_instrument_catalog_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    artifact_id TEXT NOT NULL REFERENCES ingestion_artifacts(artifact_id)
        DEFERRABLE INITIALLY DEFERRED,
    scope_json TEXT NOT NULL,
    scope_digest TEXT NOT NULL CHECK (
        length(scope_digest) = 64
        AND scope_digest NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    tombstone_authoritative INTEGER NOT NULL CHECK (
        tombstone_authoritative IN (0, 1)
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    CHECK (tombstone_authoritative = 0 OR completeness = 'complete')
) STRICT;

CREATE TABLE instruments_stage3 (
    instrument_id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('equity', 'etf', 'index')),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    canonical_symbol TEXT,
    display_name TEXT,
    exchange TEXT,
    currency TEXT,
    country TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
) STRICT;

INSERT INTO instruments_stage3 (
    instrument_id, asset_type, created_run_id, canonical_symbol, display_name,
    exchange, currency, country, first_seen_at, last_seen_at, active
)
SELECT instrument_id, asset_type, created_run_id, NULL, NULL,
       NULL, NULL, NULL, NULL, NULL, 1
FROM instruments;

DROP TABLE instruments;
ALTER TABLE instruments_stage3 RENAME TO instruments;

CREATE TABLE instrument_identifiers_stage3 (
    identifier_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_through TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    evidence_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    confirmation_state TEXT CHECK (
        confirmation_state IN ('confirmed', 'unconfirmed')
    ),
    source_snapshot_id TEXT REFERENCES market_instrument_catalog_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (valid_through IS NULL OR valid_from <= valid_through),
    UNIQUE (provider, provider_symbol, valid_from)
) STRICT;

INSERT INTO instrument_identifiers_stage3 (
    identifier_id, instrument_id, provider, provider_symbol, valid_from,
    valid_through, available_at, available_precision, evidence_id, run_id,
    confirmation_state, source_snapshot_id
)
SELECT identifier_id, instrument_id, provider, provider_symbol, valid_from,
       valid_through, available_at, available_precision, evidence_id, run_id,
       NULL, NULL
FROM instrument_identifiers;

DROP TABLE instrument_identifiers;
ALTER TABLE instrument_identifiers_stage3 RENAME TO instrument_identifiers;

CREATE INDEX instrument_identifiers_lookup
ON instrument_identifiers(provider, provider_symbol, valid_from, valid_through);

CREATE INDEX market_instrument_catalog_snapshots_run
ON market_instrument_catalog_snapshots(run_id, captured_at);

CREATE TRIGGER instruments_identity_insert_guard
BEFORE INSERT ON instruments
WHEN EXISTS (
    SELECT 1 FROM instruments WHERE instrument_id = NEW.instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'instrument identity is immutable');
END;

CREATE TRIGGER instruments_identity_immutable_update
BEFORE UPDATE OF instrument_id, asset_type, created_run_id ON instruments
BEGIN
    SELECT RAISE(ABORT, 'instrument identity is immutable');
END;

CREATE TRIGGER instruments_immutable_delete
BEFORE DELETE ON instruments
BEGIN
    SELECT RAISE(ABORT, 'instruments must be retired, not deleted');
END;

CREATE TRIGGER instrument_identifiers_insert_guard
BEFORE INSERT ON instrument_identifiers
WHEN EXISTS (
    SELECT 1
    FROM instrument_identifiers
    WHERE identifier_id = NEW.identifier_id
       OR (
            provider = NEW.provider
            AND provider_symbol = NEW.provider_symbol
            AND valid_from = NEW.valid_from
       )
)
BEGIN
    SELECT RAISE(ABORT, 'instrument identifier version is immutable');
END;

CREATE TRIGGER instrument_identifiers_identity_immutable_update
BEFORE UPDATE OF identifier_id, instrument_id, provider, provider_symbol,
                 valid_from, valid_through, available_at, available_precision,
                 evidence_id, run_id ON instrument_identifiers
BEGIN
    SELECT RAISE(ABORT, 'instrument identifier identity is immutable');
END;

CREATE TRIGGER instrument_identifiers_version_immutable_update
BEFORE UPDATE ON instrument_identifiers
WHEN OLD.source_snapshot_id IS NOT NULL OR NEW.source_snapshot_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'instrument identifier version is immutable');
END;

CREATE TRIGGER instrument_identifiers_immutable_delete
BEFORE DELETE ON instrument_identifiers
BEGIN
    SELECT RAISE(ABORT, 'instrument identifiers must be retired, not deleted');
END;

CREATE TRIGGER instrument_identifiers_snapshot_lineage
BEFORE INSERT ON instrument_identifiers
WHEN NEW.source_snapshot_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM market_instrument_catalog_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'instrument identifier snapshot/run mismatch');
END;

CREATE TRIGGER instrument_identifiers_interval_overlap_guard
BEFORE INSERT ON instrument_identifiers
WHEN EXISTS (
    SELECT 1
    FROM instrument_identifiers AS existing
    WHERE existing.provider = NEW.provider
      AND existing.provider_symbol = NEW.provider_symbol
      AND existing.valid_from <= COALESCE(NEW.valid_through, '9999-12-31')
      AND NEW.valid_from <= COALESCE(existing.valid_through, '9999-12-31')
)
BEGIN
    SELECT RAISE(ABORT, 'provider identifier interval overlaps an existing identity');
END;

CREATE TRIGGER market_instrument_catalog_snapshots_insert_guard
BEFORE INSERT ON market_instrument_catalog_snapshots
WHEN EXISTS (
    SELECT 1
    FROM market_instrument_catalog_snapshots
    WHERE snapshot_id = NEW.snapshot_id
       OR semantic_identity = NEW.semantic_identity
       OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'catalog snapshot identity is immutable');
END;

CREATE TRIGGER market_instrument_catalog_snapshots_immutable_update
BEFORE UPDATE ON market_instrument_catalog_snapshots
BEGIN
    SELECT RAISE(ABORT, 'catalog snapshots are immutable');
END;

CREATE TRIGGER market_instrument_catalog_snapshots_immutable_delete
BEFORE DELETE ON market_instrument_catalog_snapshots
BEGIN
    SELECT RAISE(ABORT, 'catalog snapshots are immutable');
END;
