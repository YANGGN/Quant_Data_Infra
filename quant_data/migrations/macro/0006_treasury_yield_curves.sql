-- Deliberate Stage 3 reconstruction of Treasury curve corrections.

CREATE TABLE treasury_yield_curves (
    curve_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    curve_date TEXT NOT NULL,
    curve_variant TEXT NOT NULL,
    source_snapshot_id TEXT REFERENCES macro_source_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, curve_date, curve_variant)
) STRICT;

CREATE TABLE treasury_yield_curve_versions (
    curve_version_id TEXT PRIMARY KEY,
    curve_id TEXT NOT NULL REFERENCES treasury_yield_curves(curve_id),
    provider TEXT NOT NULL,
    curve_date TEXT NOT NULL,
    curve_variant TEXT NOT NULL,
    tenor TEXT NOT NULL,
    series_id TEXT NOT NULL REFERENCES macro_series(series_id),
    yield_value TEXT,
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_curve_version_id TEXT
        REFERENCES treasury_yield_curve_versions(curve_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    artifact_id TEXT NOT NULL REFERENCES macro_source_artifacts(artifact_id),
    source_snapshot_id TEXT NOT NULL REFERENCES macro_source_snapshots(snapshot_id),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'tombstone')),
    CHECK (
        (state = 'active' AND ((yield_value IS NULL) <> (missing_reason IS NULL)))
        OR (state = 'tombstone' AND yield_value IS NULL AND missing_reason IS NOT NULL)
    ),
    UNIQUE (provider, curve_date, curve_variant, tenor, correction_sequence)
) STRICT;

CREATE INDEX treasury_yield_curve_versions_selection
ON treasury_yield_curve_versions(
    provider, curve_date, curve_variant, tenor, available_at, correction_sequence
);

CREATE TRIGGER treasury_yield_curves_insert_guard
BEFORE INSERT ON treasury_yield_curves
WHEN EXISTS (
    SELECT 1
    FROM treasury_yield_curves
    WHERE curve_id = NEW.curve_id
       OR (
            provider = NEW.provider
            AND curve_date = NEW.curve_date
            AND curve_variant = NEW.curve_variant
       )
)
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve identity is immutable');
END;

CREATE TRIGGER treasury_yield_curves_lineage_guard
BEFORE INSERT ON treasury_yield_curves
WHEN NEW.source_snapshot_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve snapshot/run mismatch');
END;

CREATE TRIGGER treasury_yield_curves_immutable_update
BEFORE UPDATE ON treasury_yield_curves
BEGIN
    SELECT RAISE(ABORT, 'Treasury curves are immutable');
END;

CREATE TRIGGER treasury_yield_curves_immutable_delete
BEFORE DELETE ON treasury_yield_curves
BEGIN
    SELECT RAISE(ABORT, 'Treasury curves are immutable');
END;

CREATE TRIGGER treasury_yield_curve_versions_insert_guard
BEFORE INSERT ON treasury_yield_curve_versions
WHEN EXISTS (
    SELECT 1
    FROM treasury_yield_curve_versions
    WHERE curve_version_id = NEW.curve_version_id
       OR (
            provider = NEW.provider
            AND curve_date = NEW.curve_date
            AND curve_variant = NEW.curve_variant
            AND tenor = NEW.tenor
            AND correction_sequence = NEW.correction_sequence
       )
)
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve version is immutable');
END;

CREATE TRIGGER treasury_yield_curve_versions_initial_sequence_guard
BEFORE INSERT ON treasury_yield_curve_versions
WHEN NEW.supersedes_curve_version_id IS NULL AND NEW.correction_sequence != 1
BEGIN
    SELECT RAISE(ABORT, 'initial Treasury correction sequence must be one');
END;

CREATE TRIGGER treasury_yield_curve_versions_supersession_guard
BEFORE INSERT ON treasury_yield_curve_versions
WHEN NEW.supersedes_curve_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM treasury_yield_curve_versions AS previous
        WHERE previous.curve_version_id = NEW.supersedes_curve_version_id
          AND previous.provider = NEW.provider
          AND previous.curve_date = NEW.curve_date
          AND previous.curve_variant = NEW.curve_variant
          AND previous.tenor = NEW.tenor
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid Treasury curve supersession') END;
END;

CREATE TRIGGER treasury_yield_curve_versions_curve_guard
BEFORE INSERT ON treasury_yield_curve_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM treasury_yield_curves
    WHERE curve_id = NEW.curve_id
      AND provider = NEW.provider
      AND curve_date = NEW.curve_date
      AND curve_variant = NEW.curve_variant
)
 OR NOT EXISTS (
    SELECT 1
    FROM macro_source_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id AND run_id = NEW.run_id
)
 OR NOT EXISTS (
    SELECT 1
    FROM macro_source_artifacts
    WHERE artifact_id = NEW.artifact_id AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve provenance mismatch');
END;

CREATE TRIGGER treasury_yield_curve_versions_immutable_update
BEFORE UPDATE ON treasury_yield_curve_versions
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve versions are immutable');
END;

CREATE TRIGGER treasury_yield_curve_versions_immutable_delete
BEFORE DELETE ON treasury_yield_curve_versions
BEGIN
    SELECT RAISE(ABORT, 'Treasury curve versions are immutable');
END;
