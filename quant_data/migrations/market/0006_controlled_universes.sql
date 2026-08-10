-- Deliberate Stage 3 reconstruction of controlled universe membership history.

CREATE TABLE market_universes (
    universe_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE market_universe_membership_versions (
    membership_version_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL REFERENCES market_universes(universe_id),
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    effective_from TEXT NOT NULL,
    effective_through TEXT CHECK (
        effective_through IS NULL OR effective_through >= effective_from
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    state TEXT NOT NULL CHECK (state IN ('active', 'tombstone')),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_membership_version_id TEXT
        REFERENCES market_universe_membership_versions(membership_version_id)
        DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES market_instrument_catalog_snapshots(snapshot_id)
        DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (universe_id, instrument_id, version_sequence)
) STRICT;

CREATE TABLE market_universe_memberships (
    universe_id TEXT NOT NULL REFERENCES market_universes(universe_id),
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES market_universe_membership_versions(membership_version_id),
    PRIMARY KEY (universe_id, instrument_id)
) STRICT;

CREATE TABLE market_universe_snapshot_memberships (
    snapshot_id TEXT NOT NULL
        REFERENCES market_instrument_catalog_snapshots(snapshot_id),
    membership_version_id TEXT NOT NULL
        REFERENCES market_universe_membership_versions(membership_version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (snapshot_id, membership_version_id),
    UNIQUE (snapshot_id, source_row)
) STRICT;

CREATE INDEX market_universe_membership_versions_selection
ON market_universe_membership_versions(
    universe_id,
    instrument_id,
    available_at,
    version_sequence
);

CREATE INDEX market_universe_snapshot_memberships_version
ON market_universe_snapshot_memberships(membership_version_id, snapshot_id);

CREATE TRIGGER market_universes_insert_guard
BEFORE INSERT ON market_universes
WHEN EXISTS (
    SELECT 1 FROM market_universes WHERE universe_id = NEW.universe_id
)
BEGIN
    SELECT RAISE(ABORT, 'market universe identity is immutable');
END;

CREATE TRIGGER market_universes_identity_immutable_update
BEFORE UPDATE OF universe_id, created_run_id ON market_universes
BEGIN
    SELECT RAISE(ABORT, 'market universe identity is immutable');
END;

CREATE TRIGGER market_universes_immutable_delete
BEFORE DELETE ON market_universes
BEGIN
    SELECT RAISE(ABORT, 'market universes must be retired, not deleted');
END;

CREATE TRIGGER market_universe_membership_versions_insert_guard
BEFORE INSERT ON market_universe_membership_versions
WHEN EXISTS (
    SELECT 1
    FROM market_universe_membership_versions
    WHERE membership_version_id = NEW.membership_version_id
       OR (
            universe_id = NEW.universe_id
            AND instrument_id = NEW.instrument_id
            AND version_sequence = NEW.version_sequence
       )
)
BEGIN
    SELECT RAISE(ABORT, 'universe membership version is immutable');
END;

CREATE TRIGGER market_universe_membership_versions_initial_guard
BEFORE INSERT ON market_universe_membership_versions
WHEN NEW.supersedes_membership_version_id IS NULL
 AND (NEW.version_sequence != 1 OR NEW.state != 'active')
BEGIN
    SELECT RAISE(ABORT, 'initial universe membership must be active sequence one');
END;

CREATE TRIGGER market_universe_membership_versions_supersession_guard
BEFORE INSERT ON market_universe_membership_versions
WHEN NEW.supersedes_membership_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM market_universe_membership_versions AS previous
        WHERE previous.membership_version_id = NEW.supersedes_membership_version_id
          AND previous.universe_id = NEW.universe_id
          AND previous.instrument_id = NEW.instrument_id
          AND previous.version_sequence + 1 = NEW.version_sequence
          AND (
                (previous.state = 'active' AND NEW.state = 'tombstone')
                OR (previous.state = 'tombstone' AND NEW.state = 'active')
          )
    ) THEN RAISE(ABORT, 'invalid universe membership supersession') END;
END;

CREATE TRIGGER market_universe_membership_versions_tombstone_scope_guard
BEFORE INSERT ON market_universe_membership_versions
WHEN NEW.state = 'tombstone'
 AND NOT EXISTS (
    SELECT 1
    FROM market_instrument_catalog_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND completeness = 'complete'
      AND tombstone_authoritative = 1
)
BEGIN
    SELECT RAISE(ABORT, 'universe tombstone requires complete authoritative catalog');
END;

CREATE TRIGGER market_universe_membership_versions_snapshot_lineage_guard
BEFORE INSERT ON market_universe_membership_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM market_instrument_catalog_snapshots
    WHERE snapshot_id = NEW.source_snapshot_id
      AND run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'universe membership snapshot/run mismatch');
END;

CREATE TRIGGER market_universe_membership_versions_immutable_update
BEFORE UPDATE ON market_universe_membership_versions
BEGIN
    SELECT RAISE(ABORT, 'universe membership versions are immutable');
END;

CREATE TRIGGER market_universe_membership_versions_immutable_delete
BEFORE DELETE ON market_universe_membership_versions
BEGIN
    SELECT RAISE(ABORT, 'universe membership versions are immutable');
END;

CREATE TRIGGER market_universe_memberships_insert_guard
BEFORE INSERT ON market_universe_memberships
WHEN EXISTS (
    SELECT 1
    FROM market_universe_memberships
    WHERE (universe_id = NEW.universe_id AND instrument_id = NEW.instrument_id)
       OR current_version_id = NEW.current_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'universe membership current pointer is immutable');
END;

CREATE TRIGGER market_universe_memberships_pointer_insert_guard
BEFORE INSERT ON market_universe_memberships
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM market_universe_membership_versions AS version
        WHERE version.membership_version_id = NEW.current_version_id
          AND version.universe_id = NEW.universe_id
          AND version.instrument_id = NEW.instrument_id
          AND NOT EXISTS (
                SELECT 1
                FROM market_universe_membership_versions AS later
                WHERE later.universe_id = version.universe_id
                  AND later.instrument_id = version.instrument_id
                  AND later.version_sequence > version.version_sequence
          )
    ) THEN RAISE(ABORT, 'invalid current universe membership version') END;
END;

CREATE TRIGGER market_universe_memberships_identity_immutable_update
BEFORE UPDATE OF universe_id, instrument_id ON market_universe_memberships
BEGIN
    SELECT RAISE(ABORT, 'universe membership current identity is immutable');
END;

CREATE TRIGGER market_universe_memberships_pointer_update_guard
BEFORE UPDATE OF current_version_id ON market_universe_memberships
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM market_universe_membership_versions AS version
        WHERE version.membership_version_id = NEW.current_version_id
          AND version.universe_id = NEW.universe_id
          AND version.instrument_id = NEW.instrument_id
          AND NOT EXISTS (
                SELECT 1
                FROM market_universe_membership_versions AS later
                WHERE later.universe_id = version.universe_id
                  AND later.instrument_id = version.instrument_id
                  AND later.version_sequence > version.version_sequence
          )
    ) THEN RAISE(ABORT, 'invalid current universe membership version') END;
END;

CREATE TRIGGER market_universe_memberships_immutable_delete
BEFORE DELETE ON market_universe_memberships
BEGIN
    SELECT RAISE(ABORT, 'universe membership current pointers are immutable');
END;

CREATE TRIGGER market_universe_snapshot_memberships_insert_guard
BEFORE INSERT ON market_universe_snapshot_memberships
WHEN EXISTS (
    SELECT 1
    FROM market_universe_snapshot_memberships
    WHERE (snapshot_id = NEW.snapshot_id
           AND membership_version_id = NEW.membership_version_id)
       OR (snapshot_id = NEW.snapshot_id AND source_row = NEW.source_row)
)
BEGIN
    SELECT RAISE(ABORT, 'universe snapshot membership is immutable');
END;

CREATE TRIGGER market_universe_snapshot_memberships_immutable_update
BEFORE UPDATE ON market_universe_snapshot_memberships
BEGIN
    SELECT RAISE(ABORT, 'universe snapshot membership is immutable');
END;

CREATE TRIGGER market_universe_snapshot_memberships_immutable_delete
BEFORE DELETE ON market_universe_snapshot_memberships
BEGIN
    SELECT RAISE(ABORT, 'universe snapshot membership is immutable');
END;
