-- Stage 11 bounded BEA/EIA history reconstruction.
-- Dedicated relations keep live candidate evidence separate from Stage 3 fixtures.

CREATE TABLE stage11_bea_nipa_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id)
        CHECK (dataset_id = 'macro.bea.nipa_history_evidence'),
    provider TEXT NOT NULL CHECK (provider = 'bea'),
    endpoint_path TEXT NOT NULL CHECK (endpoint_path = '/api/data/'),
    table_name TEXT NOT NULL CHECK (table_name IN ('T10101', 'T10105')),
    series_code TEXT NOT NULL CHECK (
        (table_name = 'T10101' AND series_code = 'A191RL') OR
        (table_name = 'T10105' AND series_code = 'A191RC')
    ),
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (length(request_scope_sha256)=64 AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_sha256 TEXT NOT NULL CHECK (length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) BETWEEN 1 AND 8388608),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (length(semantic_identity)=64 AND semantic_identity NOT GLOB '*[^0-9a-f]*'),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    availability_basis TEXT NOT NULL CHECK (availability_basis = 'local_capture'),
    artifact_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 1000),
    normalization_version TEXT NOT NULL CHECK (normalization_version = 'stage11.bea.nipa.v1'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (run_id, table_name, series_code),
    UNIQUE (table_name, series_code, response_sha256)
) STRICT;

CREATE TABLE stage11_bea_nipa_observation_versions (
    version_id TEXT PRIMARY KEY,
    canonical_series_id TEXT NOT NULL CHECK (canonical_series_id IN ('macro.gdp.real_qoq_saar_pct','macro.gdp.nominal_billions')),
    table_name TEXT NOT NULL CHECK (table_name IN ('T10101','T10105')),
    series_code TEXT NOT NULL CHECK (
        (canonical_series_id='macro.gdp.real_qoq_saar_pct' AND table_name='T10101' AND series_code='A191RL') OR
        (canonical_series_id='macro.gdp.nominal_billions' AND table_name='T10105' AND series_code='A191RC')
    ),
    period TEXT NOT NULL CHECK (length(period)=6 AND substr(period,5,1)='Q' AND substr(period,6,1) IN ('1','2','3','4')),
    value_text TEXT NOT NULL,
    unit TEXT NOT NULL,
    unit_multiplier INTEGER NOT NULL CHECK (unit_multiplier BETWEEN -100 AND 100),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision='datetime'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision='datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES stage11_bea_nipa_observation_versions(version_id),
    capture_id TEXT NOT NULL REFERENCES stage11_bea_nipa_captures(capture_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (canonical_series_id, period, correction_sequence)
) STRICT;

CREATE TABLE stage11_bea_nipa_observations (
    canonical_series_id TEXT NOT NULL,
    period TEXT NOT NULL,
    current_version_id TEXT NOT NULL UNIQUE REFERENCES stage11_bea_nipa_observation_versions(version_id),
    PRIMARY KEY (canonical_series_id, period)
) STRICT;

CREATE TABLE stage11_eia_retail_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id)
        CHECK (dataset_id = 'macro.eia.electricity_retail_history_evidence'),
    provider TEXT NOT NULL CHECK (provider='eia'),
    endpoint_path TEXT NOT NULL CHECK (endpoint_path='/v2/electricity/retail-sales/data'),
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (length(request_scope_sha256)=64 AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_sha256 TEXT NOT NULL CHECK (length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) BETWEEN 1 AND 16777216),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (length(semantic_identity)=64 AND semantic_identity NOT GLOB '*[^0-9a-f]*'),
    completeness TEXT NOT NULL CHECK (completeness='complete'),
    availability_basis TEXT NOT NULL CHECK (availability_basis='local_capture'),
    state_id TEXT NOT NULL CHECK (state_id='US'),
    sector_id TEXT NOT NULL CHECK (sector_id='ALL'),
    page_offset INTEGER NOT NULL CHECK (page_offset >= 0 AND page_offset % 5000 = 0),
    page_length INTEGER NOT NULL CHECK (page_length=5000),
    page_total INTEGER NOT NULL CHECK (page_total BETWEEN 1 AND 40000),
    artifact_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision='datetime'),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 5000),
    normalization_version TEXT NOT NULL CHECK (normalization_version='stage11.eia.retail.v1'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (run_id, page_offset),
    UNIQUE (page_offset, response_sha256)
) STRICT;

CREATE TABLE stage11_eia_retail_observation_versions (
    version_id TEXT PRIMARY KEY,
    canonical_series_id TEXT NOT NULL CHECK (canonical_series_id IN (
        'macro.eia.electricity.retail_sales','macro.eia.electricity.retail_revenue',
        'macro.eia.electricity.retail_price','macro.eia.electricity.retail_customers'
    )),
    metric TEXT NOT NULL CHECK (
        (canonical_series_id='macro.eia.electricity.retail_sales' AND metric='sales') OR
        (canonical_series_id='macro.eia.electricity.retail_revenue' AND metric='revenue') OR
        (canonical_series_id='macro.eia.electricity.retail_price' AND metric='price') OR
        (canonical_series_id='macro.eia.electricity.retail_customers' AND metric='customers')
    ),
    period TEXT NOT NULL CHECK (length(period)=7 AND substr(period,5,1)='-'),
    state_id TEXT NOT NULL CHECK (state_id='US'),
    sector_id TEXT NOT NULL CHECK (sector_id='ALL'),
    value_text TEXT NOT NULL,
    unit TEXT NOT NULL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision='datetime'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision='datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES stage11_eia_retail_observation_versions(version_id),
    capture_id TEXT NOT NULL REFERENCES stage11_eia_retail_captures(capture_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (canonical_series_id, period, state_id, sector_id, correction_sequence)
) STRICT;

CREATE TABLE stage11_eia_retail_observations (
    canonical_series_id TEXT NOT NULL,
    period TEXT NOT NULL,
    state_id TEXT NOT NULL,
    sector_id TEXT NOT NULL,
    current_version_id TEXT NOT NULL UNIQUE REFERENCES stage11_eia_retail_observation_versions(version_id),
    PRIMARY KEY (canonical_series_id, period, state_id, sector_id)
) STRICT;

CREATE TABLE stage11_eia_weekly_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id)
        CHECK (dataset_id='macro.eia.petroleum_weekly_stock_history_evidence'),
    provider TEXT NOT NULL CHECK (provider='eia'),
    endpoint_path TEXT NOT NULL CHECK (endpoint_path='/v2/seriesid/PET.WCESTUS1.W'),
    provider_series_id TEXT NOT NULL CHECK (provider_series_id='PET.WCESTUS1.W'),
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (length(request_scope_sha256)=64 AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_sha256 TEXT NOT NULL CHECK (length(response_sha256)=64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) BETWEEN 1 AND 16777216),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (length(semantic_identity)=64 AND semantic_identity NOT GLOB '*[^0-9a-f]*'),
    completeness TEXT NOT NULL CHECK (completeness='complete'),
    availability_basis TEXT NOT NULL CHECK (availability_basis='local_capture'),
    artifact_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision='datetime'),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 5000),
    normalization_version TEXT NOT NULL CHECK (normalization_version='stage11.eia.weekly.v1'),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    UNIQUE (provider_series_id, response_sha256)
) STRICT;

CREATE TABLE stage11_eia_weekly_observation_versions (
    version_id TEXT PRIMARY KEY,
    canonical_series_id TEXT NOT NULL CHECK (canonical_series_id='macro.eia.weekly.petroleum_stock'),
    provider_series_id TEXT NOT NULL CHECK (provider_series_id='PET.WCESTUS1.W'),
    period TEXT NOT NULL CHECK (length(period)=10 AND substr(period,5,1)='-' AND substr(period,8,1)='-'),
    value_text TEXT NOT NULL,
    unit TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision='datetime'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision='datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES stage11_eia_weekly_observation_versions(version_id),
    capture_id TEXT NOT NULL REFERENCES stage11_eia_weekly_captures(capture_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (canonical_series_id, period, correction_sequence)
) STRICT;

CREATE TABLE stage11_eia_weekly_observations (
    canonical_series_id TEXT NOT NULL,
    period TEXT NOT NULL,
    current_version_id TEXT NOT NULL UNIQUE REFERENCES stage11_eia_weekly_observation_versions(version_id),
    PRIMARY KEY (canonical_series_id, period)
) STRICT;

CREATE INDEX stage11_bea_nipa_versions_selection ON stage11_bea_nipa_observation_versions(canonical_series_id,period,correction_sequence);
CREATE INDEX stage11_eia_retail_versions_selection ON stage11_eia_retail_observation_versions(canonical_series_id,period,state_id,sector_id,correction_sequence);
CREATE INDEX stage11_eia_weekly_versions_selection ON stage11_eia_weekly_observation_versions(canonical_series_id,period,correction_sequence);

CREATE TRIGGER stage11_bea_capture_immutable_update BEFORE UPDATE ON stage11_bea_nipa_captures BEGIN SELECT RAISE(ABORT,'stage11 bea capture is immutable'); END;
CREATE TRIGGER stage11_bea_capture_immutable_delete BEFORE DELETE ON stage11_bea_nipa_captures BEGIN SELECT RAISE(ABORT,'stage11 bea capture is immutable'); END;
CREATE TRIGGER stage11_bea_version_immutable_update BEFORE UPDATE ON stage11_bea_nipa_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 bea version is immutable'); END;
CREATE TRIGGER stage11_bea_version_immutable_delete BEFORE DELETE ON stage11_bea_nipa_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 bea version is immutable'); END;
CREATE TRIGGER stage11_eia_retail_capture_immutable_update BEFORE UPDATE ON stage11_eia_retail_captures BEGIN SELECT RAISE(ABORT,'stage11 eia retail capture is immutable'); END;
CREATE TRIGGER stage11_eia_retail_capture_immutable_delete BEFORE DELETE ON stage11_eia_retail_captures BEGIN SELECT RAISE(ABORT,'stage11 eia retail capture is immutable'); END;
CREATE TRIGGER stage11_eia_retail_version_immutable_update BEFORE UPDATE ON stage11_eia_retail_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 eia retail version is immutable'); END;
CREATE TRIGGER stage11_eia_retail_version_immutable_delete BEFORE DELETE ON stage11_eia_retail_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 eia retail version is immutable'); END;
CREATE TRIGGER stage11_eia_weekly_capture_immutable_update BEFORE UPDATE ON stage11_eia_weekly_captures BEGIN SELECT RAISE(ABORT,'stage11 eia weekly capture is immutable'); END;
CREATE TRIGGER stage11_eia_weekly_capture_immutable_delete BEFORE DELETE ON stage11_eia_weekly_captures BEGIN SELECT RAISE(ABORT,'stage11 eia weekly capture is immutable'); END;
CREATE TRIGGER stage11_eia_weekly_version_immutable_update BEFORE UPDATE ON stage11_eia_weekly_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 eia weekly version is immutable'); END;
CREATE TRIGGER stage11_eia_weekly_version_immutable_delete BEFORE DELETE ON stage11_eia_weekly_observation_versions BEGIN SELECT RAISE(ABORT,'stage11 eia weekly version is immutable'); END;

CREATE TRIGGER stage11_bea_version_lineage BEFORE INSERT ON stage11_bea_nipa_observation_versions BEGIN
 SELECT CASE WHEN (NEW.correction_sequence=1 AND NEW.supersedes_version_id IS NOT NULL) OR
 (NEW.correction_sequence>1 AND NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_observation_versions p WHERE p.version_id=NEW.supersedes_version_id AND p.canonical_series_id=NEW.canonical_series_id AND p.period=NEW.period AND p.correction_sequence=NEW.correction_sequence-1)) OR
 NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_captures c WHERE c.capture_id=NEW.capture_id AND c.table_name=NEW.table_name AND c.series_code=NEW.series_code AND c.artifact_id=NEW.artifact_id AND c.snapshot_id=NEW.snapshot_id AND c.run_id=NEW.run_id)
 THEN RAISE(ABORT,'invalid stage11 bea correction lineage') END; END;
CREATE TRIGGER stage11_eia_retail_version_lineage BEFORE INSERT ON stage11_eia_retail_observation_versions BEGIN
 SELECT CASE WHEN (NEW.correction_sequence=1 AND NEW.supersedes_version_id IS NOT NULL) OR
 (NEW.correction_sequence>1 AND NOT EXISTS (SELECT 1 FROM stage11_eia_retail_observation_versions p WHERE p.version_id=NEW.supersedes_version_id AND p.canonical_series_id=NEW.canonical_series_id AND p.period=NEW.period AND p.state_id=NEW.state_id AND p.sector_id=NEW.sector_id AND p.correction_sequence=NEW.correction_sequence-1)) OR
 NOT EXISTS (SELECT 1 FROM stage11_eia_retail_captures c WHERE c.capture_id=NEW.capture_id AND c.state_id=NEW.state_id AND c.sector_id=NEW.sector_id AND c.artifact_id=NEW.artifact_id AND c.snapshot_id=NEW.snapshot_id AND c.run_id=NEW.run_id)
 THEN RAISE(ABORT,'invalid stage11 eia retail correction lineage') END; END;
CREATE TRIGGER stage11_eia_weekly_version_lineage BEFORE INSERT ON stage11_eia_weekly_observation_versions BEGIN
 SELECT CASE WHEN (NEW.correction_sequence=1 AND NEW.supersedes_version_id IS NOT NULL) OR
 (NEW.correction_sequence>1 AND NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_observation_versions p WHERE p.version_id=NEW.supersedes_version_id AND p.canonical_series_id=NEW.canonical_series_id AND p.period=NEW.period AND p.correction_sequence=NEW.correction_sequence-1)) OR
 NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_captures c WHERE c.capture_id=NEW.capture_id AND c.provider_series_id=NEW.provider_series_id AND c.artifact_id=NEW.artifact_id AND c.snapshot_id=NEW.snapshot_id AND c.run_id=NEW.run_id)
 THEN RAISE(ABORT,'invalid stage11 eia weekly correction lineage') END; END;

CREATE TRIGGER stage11_bea_current_insert BEFORE INSERT ON stage11_bea_nipa_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 bea current') END; END;
CREATE TRIGGER stage11_bea_current_update BEFORE UPDATE OF current_version_id ON stage11_bea_nipa_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND NOT EXISTS (SELECT 1 FROM stage11_bea_nipa_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 bea current') END; END;
CREATE TRIGGER stage11_bea_current_key_immutable BEFORE UPDATE OF canonical_series_id,period ON stage11_bea_nipa_observations BEGIN SELECT RAISE(ABORT,'stage11 bea current key is immutable'); END;
CREATE TRIGGER stage11_eia_retail_current_insert BEFORE INSERT ON stage11_eia_retail_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_eia_retail_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND v.state_id=NEW.state_id AND v.sector_id=NEW.sector_id AND NOT EXISTS (SELECT 1 FROM stage11_eia_retail_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.state_id=v.state_id AND later.sector_id=v.sector_id AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 eia retail current') END; END;
CREATE TRIGGER stage11_eia_retail_current_update BEFORE UPDATE OF current_version_id ON stage11_eia_retail_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_eia_retail_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND v.state_id=NEW.state_id AND v.sector_id=NEW.sector_id AND NOT EXISTS (SELECT 1 FROM stage11_eia_retail_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.state_id=v.state_id AND later.sector_id=v.sector_id AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 eia retail current') END; END;
CREATE TRIGGER stage11_eia_retail_current_key_immutable BEFORE UPDATE OF canonical_series_id,period,state_id,sector_id ON stage11_eia_retail_observations BEGIN SELECT RAISE(ABORT,'stage11 eia retail current key is immutable'); END;
CREATE TRIGGER stage11_eia_weekly_current_insert BEFORE INSERT ON stage11_eia_weekly_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 eia weekly current') END; END;
CREATE TRIGGER stage11_eia_weekly_current_update BEFORE UPDATE OF current_version_id ON stage11_eia_weekly_observations BEGIN
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_observation_versions v WHERE v.version_id=NEW.current_version_id AND v.canonical_series_id=NEW.canonical_series_id AND v.period=NEW.period AND NOT EXISTS (SELECT 1 FROM stage11_eia_weekly_observation_versions later WHERE later.canonical_series_id=v.canonical_series_id AND later.period=v.period AND later.correction_sequence>v.correction_sequence)) THEN RAISE(ABORT,'invalid stage11 eia weekly current') END; END;
CREATE TRIGGER stage11_eia_weekly_current_key_immutable BEFORE UPDATE OF canonical_series_id,period ON stage11_eia_weekly_observations BEGIN SELECT RAISE(ABORT,'stage11 eia weekly current key is immutable'); END;
