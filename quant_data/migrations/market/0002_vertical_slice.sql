CREATE TABLE instruments (
    instrument_id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('etf', 'index')),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE instrument_identifiers (
    identifier_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_through TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision IN ('date', 'datetime')),
    evidence_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, provider_symbol, valid_from)
) STRICT;

CREATE TABLE market_price_ingestion_requests (
    request_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id),
    provider TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    scope_digest TEXT NOT NULL CHECK (length(scope_digest) = 64),
    semantic_identity TEXT NOT NULL UNIQUE CHECK (length(semantic_identity) = 64),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    artifact_id TEXT NOT NULL UNIQUE,
    artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    source_resource TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    normalization_version TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE prices_daily_versions (
    version_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    price_variant TEXT NOT NULL,
    currency_segment TEXT NOT NULL,
    open_value TEXT NOT NULL,
    high_value TEXT NOT NULL,
    low_value TEXT NOT NULL,
    close_value TEXT NOT NULL,
    volume INTEGER NOT NULL CHECK (volume >= 0),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision IN ('date', 'datetime')),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES prices_daily_versions(version_id),
    request_id TEXT NOT NULL REFERENCES market_price_ingestion_requests(request_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        instrument_id, trade_date, provider, price_variant,
        currency_segment, correction_sequence
    )
) STRICT;

CREATE TABLE prices_daily (
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    price_variant TEXT NOT NULL,
    currency_segment TEXT NOT NULL,
    current_version_id TEXT NOT NULL UNIQUE REFERENCES prices_daily_versions(version_id),
    PRIMARY KEY (
        instrument_id, trade_date, provider, price_variant, currency_segment
    )
) STRICT;

CREATE INDEX instrument_identifiers_lookup
ON instrument_identifiers(provider, provider_symbol, valid_from, valid_through);

CREATE INDEX prices_daily_versions_selection
ON prices_daily_versions(
    instrument_id, trade_date, provider, price_variant,
    currency_segment, correction_sequence
);

CREATE TRIGGER prices_daily_versions_same_key_supersession
BEFORE INSERT ON prices_daily_versions
WHEN NEW.supersedes_version_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM prices_daily_versions AS previous
        WHERE previous.version_id = NEW.supersedes_version_id
          AND previous.instrument_id = NEW.instrument_id
          AND previous.trade_date = NEW.trade_date
          AND previous.provider = NEW.provider
          AND previous.price_variant = NEW.price_variant
          AND previous.currency_segment = NEW.currency_segment
          AND previous.correction_sequence + 1 = NEW.correction_sequence
    ) THEN RAISE(ABORT, 'invalid price supersession') END;
END;

CREATE TRIGGER prices_daily_current_matches_key_insert
BEFORE INSERT ON prices_daily
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM prices_daily_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
    ) THEN RAISE(ABORT, 'invalid current price version') END;
END;

CREATE TRIGGER prices_daily_current_matches_key_update
BEFORE UPDATE OF current_version_id ON prices_daily
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM prices_daily_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
    ) THEN RAISE(ABORT, 'invalid current price version') END;
END;
