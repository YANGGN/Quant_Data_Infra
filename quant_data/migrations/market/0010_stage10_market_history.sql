-- Stage 10 forward-only market history reconstruction.
-- It is isolated from the frozen Stage 9 SPY relations and remains candidate-only.

CREATE TABLE stage10_instruments (
    instrument_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    provider_symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('equity', 'etf', 'index')),
    display_name TEXT,
    exchange_code TEXT,
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'provider_native'),
    first_trade_date TEXT,
    identity_seed_sha256 TEXT NOT NULL CHECK (
        length(identity_seed_sha256) = 64
        AND identity_seed_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, provider_symbol)
) STRICT;

CREATE TABLE stage10_universe_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'market.stage10.source_evidence'
    ),
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    universe_id TEXT NOT NULL CHECK (
        universe_id IN ('sp500_current', 'nasdaq100_current', 'dow30_current')
    ),
    endpoint_path TEXT NOT NULL CHECK (
        endpoint_path IN (
            '/stable/sp500-constituent',
            '/stable/nasdaq-constituent',
            '/stable/dowjones-constituent'
        )
    ),
    scope_manifest_sha256 TEXT NOT NULL CHECK (
        length(scope_manifest_sha256) = 64
        AND scope_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64
        AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64
        AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) > 0),
    http_status INTEGER NOT NULL CHECK (http_status = 200),
    content_type TEXT NOT NULL,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    artifact_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    as_of_date TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 600),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'stage10.fmp.universe.v1'
    ),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    UNIQUE (universe_id, response_sha256)
) STRICT;

CREATE TABLE stage10_scope_snapshots (
    scope_snapshot_id TEXT PRIMARY KEY,
    scope_manifest_sha256 TEXT NOT NULL UNIQUE CHECK (
        length(scope_manifest_sha256) = 64
        AND scope_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    target_profile_id TEXT NOT NULL CHECK (
        target_profile_id = 'stage10_fmp_market_history_v1'
    ),
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE stage10_universes (
    universe_id TEXT PRIMARY KEY CHECK (
        universe_id IN (
            'sp500_current', 'nasdaq100_current', 'dow30_current',
            'curated_etfs', 'major_indexes'
        )
    ),
    universe_kind TEXT NOT NULL CHECK (
        universe_kind IN ('benchmark_constituent_universe', 'instrument_watchlist')
    ),
    publisher TEXT NOT NULL,
    source_authority TEXT NOT NULL CHECK (
        source_authority IN ('commercial_provider', 'reviewed_local_manifest')
    ),
    created_run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE stage10_universe_snapshots (
    universe_snapshot_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL REFERENCES stage10_universes(universe_id),
    scope_snapshot_id TEXT NOT NULL REFERENCES stage10_scope_snapshots(scope_snapshot_id),
    capture_id TEXT REFERENCES stage10_universe_captures(capture_id),
    as_of_date TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    membership_relation TEXT NOT NULL CHECK (
        membership_relation IN (
            'provider_claimed_constituent',
            'provider_claimed_constituent_reconciled_to_official_change_notices',
            'reviewed_instrument_watchlist'
        )
    ),
    member_count INTEGER NOT NULL CHECK (member_count BETWEEN 1 AND 800),
    membership_sha256 TEXT NOT NULL CHECK (
        length(membership_sha256) = 64
        AND membership_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (universe_id, membership_sha256),
    CHECK (
        (universe_id IN ('sp500_current', 'nasdaq100_current', 'dow30_current')
         AND capture_id IS NOT NULL)
        OR
        (universe_id IN ('curated_etfs', 'major_indexes') AND capture_id IS NULL)
    )
) STRICT;

CREATE TABLE stage10_universe_snapshot_members (
    universe_snapshot_id TEXT NOT NULL
        REFERENCES stage10_universe_snapshots(universe_snapshot_id),
    instrument_id TEXT NOT NULL REFERENCES stage10_instruments(instrument_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (universe_snapshot_id, instrument_id),
    UNIQUE (universe_snapshot_id, source_row)
) STRICT;

CREATE TABLE stage10_daily_price_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'market.stage10.source_evidence'
    ),
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    instrument_id TEXT NOT NULL REFERENCES stage10_instruments(instrument_id),
    provider_symbol TEXT NOT NULL,
    endpoint_path TEXT NOT NULL CHECK (
        endpoint_path = '/stable/historical-price-eod/full'
    ),
    scope_manifest_sha256 TEXT NOT NULL CHECK (
        length(scope_manifest_sha256) = 64
        AND scope_manifest_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64
        AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64
        AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) > 0),
    http_status INTEGER NOT NULL CHECK (http_status = 200),
    content_type TEXT NOT NULL,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness = 'complete'),
    artifact_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL UNIQUE,
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    earliest_trade_date TEXT NOT NULL,
    latest_trade_date TEXT NOT NULL CHECK (latest_trade_date >= earliest_trade_date),
    row_count INTEGER NOT NULL CHECK (row_count BETWEEN 1 AND 30000),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'stage10.fmp.daily_price.v1'
    ),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id),
    UNIQUE (instrument_id, response_sha256)
) STRICT;

CREATE TABLE stage10_daily_price_versions (
    version_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES stage10_instruments(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    price_variant TEXT NOT NULL CHECK (price_variant = 'fmp_full_eod_v1'),
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'provider_native'),
    open_value TEXT NOT NULL,
    high_value TEXT NOT NULL,
    low_value TEXT NOT NULL,
    close_value TEXT NOT NULL,
    volume INTEGER NOT NULL CHECK (volume >= 0),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (available_precision = 'datetime'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    correction_sequence INTEGER NOT NULL CHECK (correction_sequence > 0),
    supersedes_version_id TEXT REFERENCES stage10_daily_price_versions(version_id),
    capture_id TEXT NOT NULL REFERENCES stage10_daily_price_captures(capture_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        instrument_id, trade_date, provider, price_variant,
        currency_segment, correction_sequence
    )
) STRICT;

CREATE TABLE stage10_daily_prices (
    instrument_id TEXT NOT NULL REFERENCES stage10_instruments(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    price_variant TEXT NOT NULL CHECK (price_variant = 'fmp_full_eod_v1'),
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'provider_native'),
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES stage10_daily_price_versions(version_id),
    PRIMARY KEY (
        instrument_id, trade_date, provider, price_variant, currency_segment
    )
) STRICT;

CREATE INDEX stage10_universe_snapshot_members_instrument
ON stage10_universe_snapshot_members(instrument_id, universe_snapshot_id);

CREATE INDEX stage10_daily_price_captures_scope
ON stage10_daily_price_captures(scope_manifest_sha256, instrument_id, captured_at);

CREATE INDEX stage10_daily_price_versions_selection
ON stage10_daily_price_versions(
    instrument_id, trade_date, provider, price_variant,
    currency_segment, correction_sequence
);

CREATE TRIGGER stage10_instruments_immutable_update
BEFORE UPDATE ON stage10_instruments
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 instrument identities are immutable');
END;

CREATE TRIGGER stage10_instruments_immutable_delete
BEFORE DELETE ON stage10_instruments
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 instrument identities are immutable');
END;

CREATE TRIGGER stage10_universe_captures_immutable_update
BEFORE UPDATE ON stage10_universe_captures
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe captures are immutable');
END;

CREATE TRIGGER stage10_universe_captures_immutable_delete
BEFORE DELETE ON stage10_universe_captures
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe captures are immutable');
END;

CREATE TRIGGER stage10_scope_snapshots_immutable_update
BEFORE UPDATE ON stage10_scope_snapshots
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 scope snapshots are immutable');
END;

CREATE TRIGGER stage10_scope_snapshots_immutable_delete
BEFORE DELETE ON stage10_scope_snapshots
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 scope snapshots are immutable');
END;

CREATE TRIGGER stage10_universes_immutable_update
BEFORE UPDATE ON stage10_universes
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universes are immutable');
END;

CREATE TRIGGER stage10_universes_immutable_delete
BEFORE DELETE ON stage10_universes
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universes are immutable');
END;

CREATE TRIGGER stage10_universe_snapshots_capture_guard
BEFORE INSERT ON stage10_universe_snapshots
WHEN NEW.capture_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM stage10_universe_captures AS capture
        WHERE capture.capture_id = NEW.capture_id
          AND capture.universe_id = NEW.universe_id
          AND capture.scope_manifest_sha256 = (
              SELECT scope_manifest_sha256 FROM stage10_scope_snapshots
              WHERE scope_snapshot_id = NEW.scope_snapshot_id
          )
          AND capture.run_id = NEW.run_id
    ) THEN RAISE(ABORT, 'Stage 10 universe capture lineage mismatch') END;
END;

CREATE TRIGGER stage10_universe_snapshots_immutable_update
BEFORE UPDATE ON stage10_universe_snapshots
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe snapshots are immutable');
END;

CREATE TRIGGER stage10_universe_snapshots_immutable_delete
BEFORE DELETE ON stage10_universe_snapshots
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe snapshots are immutable');
END;

CREATE TRIGGER stage10_universe_snapshot_members_immutable_update
BEFORE UPDATE ON stage10_universe_snapshot_members
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe membership is immutable');
END;

CREATE TRIGGER stage10_universe_snapshot_members_immutable_delete
BEFORE DELETE ON stage10_universe_snapshot_members
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 universe membership is immutable');
END;

CREATE TRIGGER stage10_daily_price_captures_symbol_guard
BEFORE INSERT ON stage10_daily_price_captures
WHEN NOT EXISTS (
    SELECT 1 FROM stage10_instruments AS instrument
    WHERE instrument.instrument_id = NEW.instrument_id
      AND instrument.provider_symbol = NEW.provider_symbol
)
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price capture symbol mismatch');
END;

CREATE TRIGGER stage10_daily_price_captures_immutable_update
BEFORE UPDATE ON stage10_daily_price_captures
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price captures are immutable');
END;

CREATE TRIGGER stage10_daily_price_captures_immutable_delete
BEFORE DELETE ON stage10_daily_price_captures
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price captures are immutable');
END;

CREATE TRIGGER stage10_daily_price_versions_lineage_guard
BEFORE INSERT ON stage10_daily_price_versions
BEGIN
    SELECT CASE
        WHEN NEW.correction_sequence = 1
         AND NEW.supersedes_version_id IS NOT NULL
        THEN RAISE(ABORT, 'initial Stage 10 price version must not supersede')
        WHEN NEW.correction_sequence > 1
         AND NOT EXISTS (
            SELECT 1 FROM stage10_daily_price_versions AS previous
            WHERE previous.version_id = NEW.supersedes_version_id
              AND previous.instrument_id = NEW.instrument_id
              AND previous.trade_date = NEW.trade_date
              AND previous.provider = NEW.provider
              AND previous.price_variant = NEW.price_variant
              AND previous.currency_segment = NEW.currency_segment
              AND previous.correction_sequence = NEW.correction_sequence - 1
         )
        THEN RAISE(ABORT, 'invalid Stage 10 price supersession')
    END;
END;

CREATE TRIGGER stage10_daily_price_versions_capture_guard
BEFORE INSERT ON stage10_daily_price_versions
WHEN NOT EXISTS (
    SELECT 1 FROM stage10_daily_price_captures AS capture
    WHERE capture.capture_id = NEW.capture_id
      AND capture.instrument_id = NEW.instrument_id
      AND capture.artifact_id = NEW.artifact_id
      AND capture.snapshot_id = NEW.snapshot_id
      AND capture.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price capture lineage mismatch');
END;

CREATE TRIGGER stage10_daily_price_versions_immutable_update
BEFORE UPDATE ON stage10_daily_price_versions
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price versions are immutable');
END;

CREATE TRIGGER stage10_daily_price_versions_immutable_delete
BEFORE DELETE ON stage10_daily_price_versions
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price versions are immutable');
END;

CREATE TRIGGER stage10_daily_prices_pointer_insert_guard
BEFORE INSERT ON stage10_daily_prices
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM stage10_daily_price_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
          AND NOT EXISTS (
              SELECT 1 FROM stage10_daily_price_versions AS later
              WHERE later.instrument_id = version.instrument_id
                AND later.trade_date = version.trade_date
                AND later.provider = version.provider
                AND later.price_variant = version.price_variant
                AND later.currency_segment = version.currency_segment
                AND later.correction_sequence > version.correction_sequence
          )
    ) THEN RAISE(ABORT, 'invalid current Stage 10 price version') END;
END;

CREATE TRIGGER stage10_daily_prices_identity_immutable_update
BEFORE UPDATE OF instrument_id, trade_date, provider, price_variant,
                 currency_segment ON stage10_daily_prices
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price current identity is immutable');
END;

CREATE TRIGGER stage10_daily_prices_pointer_update_guard
BEFORE UPDATE OF current_version_id ON stage10_daily_prices
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM stage10_daily_price_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
          AND NOT EXISTS (
              SELECT 1 FROM stage10_daily_price_versions AS later
              WHERE later.instrument_id = version.instrument_id
                AND later.trade_date = version.trade_date
                AND later.provider = version.provider
                AND later.price_variant = version.price_variant
                AND later.currency_segment = version.currency_segment
                AND later.correction_sequence > version.correction_sequence
          )
    ) THEN RAISE(ABORT, 'invalid current Stage 10 price version') END;
END;

CREATE TRIGGER stage10_daily_prices_immutable_delete
BEFORE DELETE ON stage10_daily_prices
BEGIN
    SELECT RAISE(ABORT, 'Stage 10 price current pointers are immutable');
END;
