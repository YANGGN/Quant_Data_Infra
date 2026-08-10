-- Deliberate Stage 3 reconstruction of effective-dated classifications.

CREATE TABLE instrument_classifications (
    classification_version_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    sector TEXT,
    industry TEXT,
    classification_provider TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_through TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES instrument_classifications(classification_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL REFERENCES market_instrument_catalog_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    CHECK (effective_through IS NULL OR effective_from <= effective_through)
) STRICT;

CREATE UNIQUE INDEX instrument_classifications_natural_identity
ON instrument_classifications(
    instrument_id,
    classification_provider,
    effective_from,
    COALESCE(effective_through, ''),
    correction_sequence
);

CREATE INDEX instrument_classifications_selection
ON instrument_classifications(
    instrument_id,
    classification_provider,
    effective_from,
    effective_through,
    available_at,
    correction_sequence
);

CREATE TRIGGER instrument_classifications_insert_guard
BEFORE INSERT ON instrument_classifications
WHEN EXISTS (
    SELECT 1
    FROM instrument_classifications
    WHERE classification_version_id = NEW.classification_version_id
       OR (
            instrument_id = NEW.instrument_id
            AND classification_provider = NEW.classification_provider
            AND effective_from = NEW.effective_from
            AND COALESCE(effective_through, '') = COALESCE(NEW.effective_through, '')
            AND correction_sequence = NEW.correction_sequence
       )
)
BEGIN
    SELECT RAISE(ABORT, 'instrument classification version is immutable');
END;

CREATE TRIGGER instrument_classifications_supersession_guard
BEFORE INSERT ON instrument_classifications
WHEN NEW.supersedes_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM instrument_classifications AS previous
        WHERE previous.classification_version_id = NEW.supersedes_version_id
          AND previous.instrument_id = NEW.instrument_id
          AND previous.classification_provider = NEW.classification_provider
          AND previous.effective_from = NEW.effective_from
          AND COALESCE(previous.effective_through, '') = COALESCE(NEW.effective_through, '')
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid classification supersession') END;
END;

CREATE TRIGGER instrument_classifications_initial_sequence_guard
BEFORE INSERT ON instrument_classifications
WHEN NEW.supersedes_version_id IS NULL
 AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial classification sequence must be one');
END;

CREATE TRIGGER instrument_classifications_snapshot_lineage_guard
BEFORE INSERT ON instrument_classifications
WHEN NOT EXISTS (
    SELECT 1
    FROM market_instrument_catalog_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'classification snapshot/run mismatch');
END;

CREATE TRIGGER instrument_classifications_immutable_update
BEFORE UPDATE ON instrument_classifications
BEGIN
    SELECT RAISE(ABORT, 'instrument classifications are immutable');
END;

CREATE TRIGGER instrument_classifications_immutable_delete
BEFORE DELETE ON instrument_classifications
BEGIN
    SELECT RAISE(ABORT, 'instrument classifications are immutable');
END;
