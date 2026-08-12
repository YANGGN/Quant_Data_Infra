-- Deliberate Stage 9 forward reconstruction for one bounded live FMP slice.
-- These relations are intentionally distinct from the frozen fixture tables.

CREATE TABLE fmp_instrument_identities (
    instrument_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    provider_symbol TEXT NOT NULL CHECK (provider_symbol = 'SPY'),
    asset_type TEXT NOT NULL CHECK (asset_type = 'etf'),
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'USD'),
    identity_seed_sha256 TEXT NOT NULL CHECK (
        length(identity_seed_sha256) = 64
        AND identity_seed_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    effective_from TEXT NOT NULL CHECK (effective_from = '2026-07-01'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    UNIQUE (provider, provider_symbol, effective_from)
) STRICT;

CREATE TABLE fmp_daily_price_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'market.fmp.daily_price_evidence'
    ),
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    endpoint_path TEXT NOT NULL CHECK (
        endpoint_path = '/stable/historical-price-eod/full'
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
    row_count INTEGER NOT NULL CHECK (row_count = 22),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'fmp.daily_price.v1'
    ),
    run_id TEXT NOT NULL UNIQUE REFERENCES ingestion_runs(run_id)
) STRICT;

CREATE TABLE fmp_daily_price_versions (
    version_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL REFERENCES fmp_instrument_identities(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    price_variant TEXT NOT NULL CHECK (price_variant = 'fmp_full_eod_v1'),
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'USD'),
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
    supersedes_version_id TEXT REFERENCES fmp_daily_price_versions(version_id),
    capture_id TEXT NOT NULL REFERENCES fmp_daily_price_captures(capture_id),
    artifact_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (
        instrument_id, trade_date, provider, price_variant,
        currency_segment, correction_sequence
    )
) STRICT;

CREATE TABLE fmp_daily_prices (
    instrument_id TEXT NOT NULL REFERENCES fmp_instrument_identities(instrument_id),
    trade_date TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'fmp'),
    price_variant TEXT NOT NULL CHECK (price_variant = 'fmp_full_eod_v1'),
    currency_segment TEXT NOT NULL CHECK (currency_segment = 'USD'),
    current_version_id TEXT NOT NULL UNIQUE
        REFERENCES fmp_daily_price_versions(version_id),
    PRIMARY KEY (
        instrument_id, trade_date, provider, price_variant, currency_segment
    )
) STRICT;

CREATE INDEX fmp_daily_price_versions_selection
ON fmp_daily_price_versions(
    instrument_id, trade_date, provider, price_variant,
    currency_segment, correction_sequence
);

CREATE INDEX fmp_daily_price_captures_run
ON fmp_daily_price_captures(run_id, captured_at);

CREATE TRIGGER fmp_instrument_identities_immutable_update
BEFORE UPDATE ON fmp_instrument_identities
BEGIN
    SELECT RAISE(ABORT, 'FMP instrument identities are immutable');
END;

CREATE TRIGGER fmp_instrument_identities_immutable_delete
BEFORE DELETE ON fmp_instrument_identities
BEGIN
    SELECT RAISE(ABORT, 'FMP instrument identities are immutable');
END;

CREATE TRIGGER fmp_daily_price_captures_immutable_update
BEFORE UPDATE ON fmp_daily_price_captures
BEGIN
    SELECT RAISE(ABORT, 'FMP captures are immutable');
END;

CREATE TRIGGER fmp_daily_price_captures_immutable_delete
BEFORE DELETE ON fmp_daily_price_captures
BEGIN
    SELECT RAISE(ABORT, 'FMP captures are immutable');
END;

CREATE TRIGGER fmp_daily_price_versions_lineage_guard
BEFORE INSERT ON fmp_daily_price_versions
BEGIN
    SELECT CASE
        WHEN NEW.correction_sequence = 1
         AND NEW.supersedes_version_id IS NOT NULL
        THEN RAISE(ABORT, 'initial FMP price version must not supersede')
        WHEN NEW.correction_sequence > 1
         AND NOT EXISTS (
            SELECT 1
            FROM fmp_daily_price_versions AS previous
            WHERE previous.version_id = NEW.supersedes_version_id
              AND previous.instrument_id = NEW.instrument_id
              AND previous.trade_date = NEW.trade_date
              AND previous.provider = NEW.provider
              AND previous.price_variant = NEW.price_variant
              AND previous.currency_segment = NEW.currency_segment
              AND previous.correction_sequence = NEW.correction_sequence - 1
         )
        THEN RAISE(ABORT, 'invalid FMP price supersession')
    END;
END;

CREATE TRIGGER fmp_daily_price_versions_capture_lineage_guard
BEFORE INSERT ON fmp_daily_price_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM fmp_daily_price_captures AS capture
    WHERE capture.capture_id = NEW.capture_id
      AND capture.artifact_id = NEW.artifact_id
      AND capture.snapshot_id = NEW.snapshot_id
      AND capture.run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'FMP price capture lineage mismatch');
END;

CREATE TRIGGER fmp_daily_price_versions_immutable_update
BEFORE UPDATE ON fmp_daily_price_versions
BEGIN
    SELECT RAISE(ABORT, 'FMP daily price versions are immutable');
END;

CREATE TRIGGER fmp_daily_price_versions_immutable_delete
BEFORE DELETE ON fmp_daily_price_versions
BEGIN
    SELECT RAISE(ABORT, 'FMP daily price versions are immutable');
END;

CREATE TRIGGER fmp_daily_prices_current_matches_key_insert
BEFORE INSERT ON fmp_daily_prices
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fmp_daily_price_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
          AND NOT EXISTS (
                SELECT 1
                FROM fmp_daily_price_versions AS later
                WHERE later.instrument_id = version.instrument_id
                  AND later.trade_date = version.trade_date
                  AND later.provider = version.provider
                  AND later.price_variant = version.price_variant
                  AND later.currency_segment = version.currency_segment
                  AND later.correction_sequence > version.correction_sequence
          )
          AND NOT EXISTS (
                SELECT 1
                FROM fmp_daily_price_versions AS lineage
                WHERE lineage.instrument_id = version.instrument_id
                  AND lineage.trade_date = version.trade_date
                  AND lineage.provider = version.provider
                  AND lineage.price_variant = version.price_variant
                  AND lineage.currency_segment = version.currency_segment
                  AND lineage.correction_sequence <= version.correction_sequence
                  AND (
                      lineage.correction_sequence < 1
                      OR (
                          lineage.correction_sequence = 1
                          AND lineage.supersedes_version_id IS NOT NULL
                      )
                      OR (
                          lineage.correction_sequence > 1
                          AND NOT EXISTS (
                              SELECT 1
                              FROM fmp_daily_price_versions AS previous
                              WHERE previous.version_id = lineage.supersedes_version_id
                                AND previous.instrument_id = lineage.instrument_id
                                AND previous.trade_date = lineage.trade_date
                                AND previous.provider = lineage.provider
                                AND previous.price_variant = lineage.price_variant
                                AND previous.currency_segment = lineage.currency_segment
                                AND previous.correction_sequence = lineage.correction_sequence - 1
                          )
                      )
                  )
          )
    ) THEN RAISE(ABORT, 'invalid current FMP price version') END;
END;

CREATE TRIGGER fmp_daily_prices_identity_immutable_update
BEFORE UPDATE OF instrument_id, trade_date, provider, price_variant,
                 currency_segment ON fmp_daily_prices
BEGIN
    SELECT RAISE(ABORT, 'FMP daily price current identity is immutable');
END;

CREATE TRIGGER fmp_daily_prices_current_matches_key_update
BEFORE UPDATE OF current_version_id ON fmp_daily_prices
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM fmp_daily_price_versions AS version
        WHERE version.version_id = NEW.current_version_id
          AND version.instrument_id = NEW.instrument_id
          AND version.trade_date = NEW.trade_date
          AND version.provider = NEW.provider
          AND version.price_variant = NEW.price_variant
          AND version.currency_segment = NEW.currency_segment
          AND NOT EXISTS (
                SELECT 1
                FROM fmp_daily_price_versions AS later
                WHERE later.instrument_id = version.instrument_id
                  AND later.trade_date = version.trade_date
                  AND later.provider = version.provider
                  AND later.price_variant = version.price_variant
                  AND later.currency_segment = version.currency_segment
                  AND later.correction_sequence > version.correction_sequence
          )
          AND NOT EXISTS (
                SELECT 1
                FROM fmp_daily_price_versions AS lineage
                WHERE lineage.instrument_id = version.instrument_id
                  AND lineage.trade_date = version.trade_date
                  AND lineage.provider = version.provider
                  AND lineage.price_variant = version.price_variant
                  AND lineage.currency_segment = version.currency_segment
                  AND lineage.correction_sequence <= version.correction_sequence
                  AND (
                      lineage.correction_sequence < 1
                      OR (
                          lineage.correction_sequence = 1
                          AND lineage.supersedes_version_id IS NOT NULL
                      )
                      OR (
                          lineage.correction_sequence > 1
                          AND NOT EXISTS (
                              SELECT 1
                              FROM fmp_daily_price_versions AS previous
                              WHERE previous.version_id = lineage.supersedes_version_id
                                AND previous.instrument_id = lineage.instrument_id
                                AND previous.trade_date = lineage.trade_date
                                AND previous.provider = lineage.provider
                                AND previous.price_variant = lineage.price_variant
                                AND previous.currency_segment = lineage.currency_segment
                                AND previous.correction_sequence = lineage.correction_sequence - 1
                          )
                      )
                  )
          )
    ) THEN RAISE(ABORT, 'invalid current FMP price version') END;
END;

CREATE TRIGGER fmp_daily_prices_immutable_delete
BEFORE DELETE ON fmp_daily_prices
BEGIN
    SELECT RAISE(ABORT, 'FMP daily price current pointers are immutable');
END;
