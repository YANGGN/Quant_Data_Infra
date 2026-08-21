-- Add the two reviewed BLS employment series and their one reviewed archival
-- provider alias.  Stage 0013 remains immutable; these rebuilds preserve its
-- rows while extending only the two constrained CHECK domains.

-- SQLite validates trigger bodies while renaming rebuilt tables, so detach
-- every trigger that references either rebuilt relation first.  Each is
-- recreated below with its original guard (or the reviewed alias extension).
DROP TRIGGER IF EXISTS macro_live_vintage_capture_immutable_update;
DROP TRIGGER IF EXISTS macro_live_vintage_capture_immutable_delete;
DROP TRIGGER IF EXISTS macro_live_vintage_series_immutable_update;
DROP TRIGGER IF EXISTS macro_live_vintage_series_immutable_delete;
DROP TRIGGER IF EXISTS macro_live_vintage_release_capture_lineage;
DROP TRIGGER IF EXISTS macro_live_vintage_version_lineage;
DROP TRIGGER IF EXISTS macro_live_vintage_membership_lineage;
DROP TRIGGER IF EXISTS macro_live_vintage_gdp_provenance_lineage;

CREATE TABLE macro_live_vintage_captures_0014 (
    capture_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('bea', 'bls', 'philadelphia_fed')),
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

INSERT INTO macro_live_vintage_captures_0014 (
    capture_id, provider, source_resource, media_type, response_sha256,
    response_bytes, semantic_identity, captured_at, captured_precision,
    source_published_at, source_published_precision, availability_basis,
    normalization_version, observation_count
)
SELECT
    capture_id, provider, source_resource, media_type, response_sha256,
    response_bytes, semantic_identity, captured_at, captured_precision,
    source_published_at, source_published_precision, availability_basis,
    normalization_version, observation_count
FROM macro_live_vintage_captures;

DROP TABLE macro_live_vintage_captures;
ALTER TABLE macro_live_vintage_captures_0014 RENAME TO macro_live_vintage_captures;

CREATE TABLE macro_live_vintage_series_0014 (
    series_id TEXT PRIMARY KEY CHECK (series_id IN (
        'macro.gdp.real_qoq_saar_pct',
        'macro.gdp.nominal_billions',
        'macro.bls.cpi_u_all_items_sa',
        'macro.bls.cpi_u_core_sa',
        'macro.bls.total_nonfarm_payrolls_sa',
        'macro.bls.unemployment_rate_sa'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('bea', 'bls')),
    provider_series_code TEXT NOT NULL,
    title TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('quarterly', 'monthly')),
    unit TEXT NOT NULL CHECK (unit IN ('percent', 'billions_usd', 'index', 'thousands_persons')),
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
        OR
        (series_id = 'macro.bls.total_nonfarm_payrolls_sa'
         AND provider = 'bls' AND provider_series_code = 'CES0000000001'
         AND frequency = 'monthly' AND unit = 'thousands_persons'
         AND value_representation = 'level' AND availability_basis = 'mixed')
        OR
        (series_id = 'macro.bls.unemployment_rate_sa'
         AND provider = 'bls' AND provider_series_code = 'LNS14000000'
         AND frequency = 'monthly' AND unit = 'percent'
         AND value_representation = 'rate' AND availability_basis = 'mixed')
    )
) STRICT;

INSERT INTO macro_live_vintage_series_0014 (
    series_id, provider, provider_series_code, title, frequency, unit,
    value_representation, availability_basis, created_capture_id
)
SELECT
    series_id, provider, provider_series_code, title, frequency, unit,
    value_representation, availability_basis, created_capture_id
FROM macro_live_vintage_series;

DROP TABLE macro_live_vintage_series;
ALTER TABLE macro_live_vintage_series_0014 RENAME TO macro_live_vintage_series;

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
      AND capture.availability_basis = NEW.availability_basis
      AND (
          series.provider = capture.provider
          OR (
              series.provider = 'bls'
              AND capture.provider = 'philadelphia_fed'
              AND series.series_id IN (
                  'macro.bls.total_nonfarm_payrolls_sa',
                  'macro.bls.unemployment_rate_sa'
              )
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'live vintage release capture lineage is invalid');
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
              AND capture.captured_at = NEW.captured_at
              AND (
                  series.provider = capture.provider
                  OR (
                      series.provider = 'bls'
                      AND capture.provider = 'philadelphia_fed'
                      AND series.series_id IN (
                          'macro.bls.total_nonfarm_payrolls_sa',
                          'macro.bls.unemployment_rate_sa'
                      )
                  )
              )
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

CREATE TRIGGER macro_live_vintage_membership_lineage
BEFORE INSERT ON macro_live_vintage_capture_membership
WHEN NOT EXISTS (
    SELECT 1
    FROM macro_live_vintage_observation_versions AS version
    JOIN macro_live_vintage_series AS series
      ON series.series_id = version.series_id
    JOIN macro_live_vintage_captures AS capture
      ON capture.capture_id = NEW.capture_id
    WHERE version.version_id = NEW.version_id
      AND version.series_id = NEW.series_id
      AND (
          capture.provider = series.provider
          OR (
              capture.provider = 'philadelphia_fed'
              AND series.provider = 'bls'
              AND series.series_id IN (
                  'macro.bls.total_nonfarm_payrolls_sa',
                  'macro.bls.unemployment_rate_sa'
              )
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'live vintage capture membership lineage is invalid');
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
