-- Live GDP/CPI vintage history.  These relations are deliberately isolated
-- from the recovered Stage 3 fixtures and the Stage 11 candidate relations.
-- A capture retains the exact credential-sanitized source bytes; releases and
-- versions preserve source-native date precision without manufacturing a
-- provider timestamp when only a local capture instant is available.

CREATE TABLE macro_live_vintage_captures (
    capture_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('bea', 'bls')),
    source_resource TEXT NOT NULL,
    media_type TEXT NOT NULL,
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64
        AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) BETWEEN 1 AND 67108864),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    source_published_at TEXT,
    source_published_precision TEXT CHECK (
        source_published_precision IS NULL
        OR source_published_precision IN ('date', 'datetime')
    ),
    availability_basis TEXT NOT NULL CHECK (
        availability_basis IN ('source_release', 'local_capture')
    ),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'macro.live_vintage.v1'
    ),
    observation_count INTEGER NOT NULL CHECK (observation_count > 0),
    CHECK (
        (source_published_at IS NULL AND source_published_precision IS NULL)
        OR (source_published_at IS NOT NULL AND source_published_precision IS NOT NULL)
    )
) STRICT;

CREATE TABLE macro_live_vintage_series (
    series_id TEXT PRIMARY KEY CHECK (series_id IN (
        'macro.gdp.real_qoq_saar_pct',
        'macro.gdp.nominal_billions',
        'macro.bls.cpi_u_all_items_sa',
        'macro.bls.cpi_u_core_sa'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('bea', 'bls')),
    provider_series_code TEXT NOT NULL,
    title TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('quarterly', 'monthly')),
    unit TEXT NOT NULL CHECK (unit IN ('percent', 'billions_usd', 'index')),
    value_representation TEXT NOT NULL CHECK (value_representation IN ('rate', 'level')),
    availability_basis TEXT NOT NULL CHECK (
        availability_basis IN ('source_release', 'mixed')
    ),
    created_capture_id TEXT NOT NULL REFERENCES macro_live_vintage_captures(capture_id),
    UNIQUE (provider, provider_series_code),
    CHECK (
        (series_id = 'macro.gdp.real_qoq_saar_pct'
         AND provider = 'bea' AND provider_series_code = 'A191RL'
         AND frequency = 'quarterly' AND unit = 'percent'
         AND value_representation = 'rate' AND availability_basis = 'source_release')
        OR
        (series_id = 'macro.gdp.nominal_billions'
         AND provider = 'bea' AND provider_series_code = 'A191RC'
         AND frequency = 'quarterly' AND unit = 'billions_usd'
         AND value_representation = 'level' AND availability_basis = 'source_release')
        OR
        (series_id = 'macro.bls.cpi_u_all_items_sa'
         AND provider = 'bls' AND provider_series_code = 'CUSR0000SA0'
         AND frequency = 'monthly' AND unit = 'index'
         AND value_representation = 'level' AND availability_basis = 'mixed')
        OR
        (series_id = 'macro.bls.cpi_u_core_sa'
         AND provider = 'bls' AND provider_series_code = 'CUSR0000SA0L1E'
         AND frequency = 'monthly' AND unit = 'index'
         AND value_representation = 'level' AND availability_basis = 'mixed')
    )
) STRICT;

CREATE TABLE macro_live_vintage_releases (
    release_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_live_vintage_series(series_id),
    source_vintage_identity TEXT NOT NULL,
    vintage_at TEXT,
    vintage_precision TEXT CHECK (
        vintage_precision IS NULL OR vintage_precision IN ('date', 'datetime')
    ),
    source_release_order TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    availability_basis TEXT NOT NULL CHECK (
        availability_basis IN ('source_release', 'local_capture')
    ),
    is_first_release INTEGER CHECK (is_first_release IN (0, 1)),
    first_release_evidence TEXT,
    release_stage TEXT,
    source_published_at TEXT,
    source_published_precision TEXT CHECK (
        source_published_precision IS NULL
        OR source_published_precision IN ('date', 'datetime')
    ),
    capture_id TEXT NOT NULL REFERENCES macro_live_vintage_captures(capture_id),
    UNIQUE (series_id, source_vintage_identity),
    UNIQUE (series_id, source_release_order),
    CHECK (
        (vintage_at IS NULL AND vintage_precision IS NULL)
        OR (vintage_at IS NOT NULL AND vintage_precision IS NOT NULL)
    ),
    CHECK (
        (source_published_at IS NULL AND source_published_precision IS NULL)
        OR (source_published_at IS NOT NULL AND source_published_precision IS NOT NULL)
    ),
    CHECK (is_first_release IS NOT 1 OR first_release_evidence IS NOT NULL)
) STRICT;

CREATE INDEX macro_live_vintage_releases_selection
ON macro_live_vintage_releases(series_id, available_at, source_release_order);

CREATE TABLE macro_live_vintage_observation_versions (
    version_id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL REFERENCES macro_live_vintage_series(series_id),
    period TEXT NOT NULL,
    release_id TEXT NOT NULL REFERENCES macro_live_vintage_releases(release_id),
    source_vintage_identity TEXT NOT NULL,
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    value_text TEXT NOT NULL,
    value_sha256 TEXT NOT NULL CHECK (
        length(value_sha256) = 64
        AND value_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    unit TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    supersedes_version_id TEXT REFERENCES macro_live_vintage_observation_versions(version_id)
        DEFERRABLE INITIALLY DEFERRED,
    capture_id TEXT NOT NULL REFERENCES macro_live_vintage_captures(capture_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (series_id, period, release_id, correction_sequence)
) STRICT;

CREATE INDEX macro_live_vintage_versions_selection
ON macro_live_vintage_observation_versions(
    series_id, period, release_id, correction_sequence
);

CREATE TABLE macro_live_vintage_observations (
    series_id TEXT NOT NULL REFERENCES macro_live_vintage_series(series_id),
    period TEXT NOT NULL,
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES macro_live_vintage_observation_versions(version_id)
        DEFERRABLE INITIALLY DEFERRED,
    PRIMARY KEY (series_id, period)
) STRICT;

CREATE TABLE macro_live_vintage_capture_membership (
    capture_id TEXT NOT NULL REFERENCES macro_live_vintage_captures(capture_id),
    version_id TEXT NOT NULL REFERENCES macro_live_vintage_observation_versions(version_id),
    series_id TEXT NOT NULL REFERENCES macro_live_vintage_series(series_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (capture_id, version_id),
    UNIQUE (capture_id, series_id, source_row)
) STRICT;

CREATE TABLE macro_live_vintage_gdp_provenance (
    provenance_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL UNIQUE REFERENCES macro_live_vintage_releases(release_id),
    capture_id TEXT NOT NULL REFERENCES macro_live_vintage_captures(capture_id),
    reference_period TEXT NOT NULL CHECK (
        length(reference_period) = 6
        AND substr(reference_period, 5, 1) = 'Q'
        AND substr(reference_period, 6, 1) IN ('1', '2', '3', '4')
    ),
    release_stage TEXT NOT NULL,
    source_vintage_identity TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (capture_id, release_id)
) STRICT;

CREATE TRIGGER macro_live_vintage_capture_immutable_update
BEFORE UPDATE ON macro_live_vintage_captures
BEGIN
    SELECT RAISE(ABORT, 'live vintage captures are immutable');
END;

CREATE TRIGGER macro_live_vintage_capture_immutable_delete
BEFORE DELETE ON macro_live_vintage_captures
BEGIN
    SELECT RAISE(ABORT, 'live vintage captures are immutable');
END;

CREATE TRIGGER macro_live_vintage_series_immutable_update
BEFORE UPDATE ON macro_live_vintage_series
BEGIN
    SELECT RAISE(ABORT, 'live vintage series are immutable');
END;

CREATE TRIGGER macro_live_vintage_series_immutable_delete
BEFORE DELETE ON macro_live_vintage_series
BEGIN
    SELECT RAISE(ABORT, 'live vintage series are immutable');
END;

CREATE TRIGGER macro_live_vintage_release_capture_lineage
BEFORE INSERT ON macro_live_vintage_releases
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_live_vintage_series AS series
    JOIN macro_live_vintage_captures AS capture
      ON capture.capture_id = NEW.capture_id
    WHERE series.series_id = NEW.series_id
      AND series.provider = capture.provider
      AND capture.availability_basis = NEW.availability_basis
)
BEGIN
    SELECT RAISE(ABORT, 'live vintage release capture lineage is invalid');
END;

CREATE TRIGGER macro_live_vintage_release_immutable_update
BEFORE UPDATE ON macro_live_vintage_releases
BEGIN
    SELECT RAISE(ABORT, 'live vintage releases are immutable');
END;

CREATE TRIGGER macro_live_vintage_release_immutable_delete
BEFORE DELETE ON macro_live_vintage_releases
BEGIN
    SELECT RAISE(ABORT, 'live vintage releases are immutable');
END;

CREATE TRIGGER macro_live_vintage_version_lineage
BEFORE INSERT ON macro_live_vintage_observation_versions
BEGIN
    SELECT CASE WHEN
        NOT EXISTS (
            SELECT 1
            FROM macro_live_vintage_releases AS release
            JOIN macro_live_vintage_series AS series
              ON series.series_id = release.series_id
            JOIN macro_live_vintage_captures AS capture
              ON capture.capture_id = NEW.capture_id
            WHERE release.release_id = NEW.release_id
              AND release.series_id = NEW.series_id
              AND release.source_vintage_identity = NEW.source_vintage_identity
              AND release.available_at = NEW.available_at
              AND release.available_precision = NEW.available_precision
              AND series.provider = capture.provider
              AND capture.captured_at = NEW.captured_at
        )
        OR (NEW.correction_sequence = 1 AND NEW.supersedes_version_id IS NOT NULL)
        OR (
            NEW.correction_sequence > 1
            AND NOT EXISTS (
                SELECT 1
                FROM macro_live_vintage_observation_versions AS prior
                WHERE prior.version_id = NEW.supersedes_version_id
                  AND prior.series_id = NEW.series_id
                  AND prior.period = NEW.period
                  AND prior.release_id = NEW.release_id
                  AND prior.correction_sequence = NEW.correction_sequence - 1
            )
        )
    THEN RAISE(ABORT, 'live vintage version lineage is invalid') END;
END;

CREATE TRIGGER macro_live_vintage_version_immutable_update
BEFORE UPDATE ON macro_live_vintage_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'live vintage versions are immutable');
END;

CREATE TRIGGER macro_live_vintage_version_immutable_delete
BEFORE DELETE ON macro_live_vintage_observation_versions
BEGIN
    SELECT RAISE(ABORT, 'live vintage versions are immutable');
END;

CREATE TRIGGER macro_live_vintage_membership_lineage
BEFORE INSERT ON macro_live_vintage_capture_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_live_vintage_observation_versions AS version
    JOIN macro_live_vintage_captures AS capture
      ON capture.capture_id = NEW.capture_id
    WHERE version.version_id = NEW.version_id
      AND version.series_id = NEW.series_id
)
BEGIN
    SELECT RAISE(ABORT, 'live vintage capture membership lineage is invalid');
END;

CREATE TRIGGER macro_live_vintage_membership_immutable_update
BEFORE UPDATE ON macro_live_vintage_capture_membership
BEGIN
    SELECT RAISE(ABORT, 'live vintage capture membership is immutable');
END;

CREATE TRIGGER macro_live_vintage_membership_immutable_delete
BEFORE DELETE ON macro_live_vintage_capture_membership
BEGIN
    SELECT RAISE(ABORT, 'live vintage capture membership is immutable');
END;

CREATE TRIGGER macro_live_vintage_gdp_provenance_lineage
BEFORE INSERT ON macro_live_vintage_gdp_provenance
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_live_vintage_releases AS release
    JOIN macro_live_vintage_series AS series
      ON series.series_id = release.series_id
    WHERE release.release_id = NEW.release_id
      AND release.capture_id = NEW.capture_id
      AND release.source_vintage_identity = NEW.source_vintage_identity
      AND series.series_id IN (
          'macro.gdp.real_qoq_saar_pct',
          'macro.gdp.nominal_billions'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'live GDP provenance lineage is invalid');
END;

CREATE TRIGGER macro_live_vintage_gdp_provenance_immutable_update
BEFORE UPDATE ON macro_live_vintage_gdp_provenance
BEGIN
    SELECT RAISE(ABORT, 'live GDP provenance is immutable');
END;

CREATE TRIGGER macro_live_vintage_gdp_provenance_immutable_delete
BEFORE DELETE ON macro_live_vintage_gdp_provenance
BEGIN
    SELECT RAISE(ABORT, 'live GDP provenance is immutable');
END;

CREATE TRIGGER macro_live_vintage_current_insert
BEFORE INSERT ON macro_live_vintage_observations
BEGIN
    SELECT CASE WHEN
        NOT EXISTS (
            SELECT 1
            FROM macro_live_vintage_observation_versions AS current_version
            WHERE current_version.version_id = NEW.current_version_id
              AND current_version.series_id = NEW.series_id
              AND current_version.period = NEW.period
        )
        OR EXISTS (
            SELECT 1
            FROM macro_live_vintage_observation_versions AS current_version
            JOIN macro_live_vintage_releases AS current_release
              ON current_release.release_id = current_version.release_id
            JOIN macro_live_vintage_observation_versions AS later_version
              ON later_version.series_id = current_version.series_id
             AND later_version.period = current_version.period
            JOIN macro_live_vintage_releases AS later_release
              ON later_release.release_id = later_version.release_id
            WHERE current_version.version_id = NEW.current_version_id
              AND (
                  later_release.available_at > current_release.available_at
                  OR (
                      later_release.available_at = current_release.available_at
                      AND later_release.source_release_order > current_release.source_release_order
                  )
                  OR (
                      later_release.release_id = current_release.release_id
                      AND later_version.correction_sequence > current_version.correction_sequence
                  )
              )
        )
    THEN RAISE(ABORT, 'live vintage current pointer is invalid') END;
END;

CREATE TRIGGER macro_live_vintage_current_update
BEFORE UPDATE OF current_version_id ON macro_live_vintage_observations
BEGIN
    SELECT CASE WHEN
        NOT EXISTS (
            SELECT 1
            FROM macro_live_vintage_observation_versions AS current_version
            WHERE current_version.version_id = NEW.current_version_id
              AND current_version.series_id = NEW.series_id
              AND current_version.period = NEW.period
        )
        OR EXISTS (
            SELECT 1
            FROM macro_live_vintage_observation_versions AS current_version
            JOIN macro_live_vintage_releases AS current_release
              ON current_release.release_id = current_version.release_id
            JOIN macro_live_vintage_observation_versions AS later_version
              ON later_version.series_id = current_version.series_id
             AND later_version.period = current_version.period
            JOIN macro_live_vintage_releases AS later_release
              ON later_release.release_id = later_version.release_id
            WHERE current_version.version_id = NEW.current_version_id
              AND (
                  later_release.available_at > current_release.available_at
                  OR (
                      later_release.available_at = current_release.available_at
                      AND later_release.source_release_order > current_release.source_release_order
                  )
                  OR (
                      later_release.release_id = current_release.release_id
                      AND later_version.correction_sequence > current_version.correction_sequence
                  )
              )
        )
    THEN RAISE(ABORT, 'live vintage current pointer is invalid') END;
END;

CREATE TRIGGER macro_live_vintage_current_key_immutable
BEFORE UPDATE OF series_id, period ON macro_live_vintage_observations
BEGIN
    SELECT RAISE(ABORT, 'live vintage current key is immutable');
END;
