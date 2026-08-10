-- Deliberate Stage 4 reconstruction of immutable option contracts and surfaces.
-- This resource is a forward reconstruction, not recovered historical SQL.

CREATE TABLE option_contracts (
    contract_id TEXT PRIMARY KEY,
    underlying_instrument_id TEXT NOT NULL
        REFERENCES instruments(instrument_id) DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    provider_contract_id TEXT NOT NULL,
    contract_symbol TEXT NOT NULL,
    expiration_date TEXT NOT NULL,
    strike_price TEXT NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    deliverable_kind TEXT NOT NULL CHECK (
        deliverable_kind IN ('standard', 'nonstandard')
    ),
    contract_multiplier INTEGER NOT NULL CHECK (contract_multiplier > 0),
    nonstandard_deliverable_json TEXT,
    contract_status TEXT NOT NULL CHECK (
        contract_status IN ('active', 'inactive', 'expired')
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    created_run_id TEXT NOT NULL
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (provider, provider_contract_id),
    CHECK (
        (deliverable_kind = 'standard' AND nonstandard_deliverable_json IS NULL)
        OR (
            deliverable_kind = 'nonstandard'
            AND nonstandard_deliverable_json IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_surface_captures (
    capture_id TEXT PRIMARY KEY,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    underlying_instrument_id TEXT NOT NULL
        REFERENCES instruments(instrument_id) DEFERRABLE INITIALLY DEFERRED,
    requested_feed TEXT NOT NULL,
    resolved_feed TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (
        environment IN ('synthetic', 'paper', 'live')
    ),
    request_scope_json TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    requested_precision TEXT NOT NULL CHECK (requested_precision = 'datetime'),
    completed_at TEXT NOT NULL,
    completed_precision TEXT NOT NULL CHECK (completed_precision = 'datetime'),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    deliverable_policy TEXT NOT NULL CHECK (
        deliverable_policy IN ('standard_only', 'all_deliverables')
    ),
    artifact_id TEXT NOT NULL
        REFERENCES ingestion_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
    source_snapshot_id TEXT NOT NULL
        REFERENCES ingestion_snapshots(snapshot_id) DEFERRABLE INITIALLY DEFERRED,
    run_id TEXT NOT NULL UNIQUE
        REFERENCES ingestion_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (requested_feed <> ''),
    CHECK (resolved_feed <> '')
) STRICT;

CREATE TABLE option_capture_underlyings (
    capture_id TEXT PRIMARY KEY
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    underlying_instrument_id TEXT NOT NULL
        REFERENCES instruments(instrument_id) DEFERRABLE INITIALLY DEFERRED,
    underlying_state TEXT NOT NULL CHECK (
        underlying_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    observed_at TEXT,
    observed_precision TEXT CHECK (
        observed_precision IS NULL OR observed_precision IN ('date', 'datetime')
    ),
    CHECK (
        (underlying_state = 'present' AND missing_reason IS NULL)
        OR (underlying_state = 'missing' AND missing_reason IS NOT NULL)
    ),
    CHECK (
        (underlying_state = 'present' AND observed_at IS NOT NULL)
        OR (underlying_state = 'missing' AND observed_at IS NULL)
    )
) STRICT;

CREATE TABLE option_open_interest (
    open_interest_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    contract_id TEXT NOT NULL
        REFERENCES option_contracts(contract_id) DEFERRABLE INITIALLY DEFERRED,
    as_of_date TEXT NOT NULL,
    open_interest INTEGER,
    observation_state TEXT NOT NULL CHECK (
        observation_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (capture_id, contract_id, as_of_date),
    CHECK (
        (observation_state = 'present'
         AND open_interest IS NOT NULL
         AND open_interest >= 0
         AND missing_reason IS NULL)
        OR (
            observation_state = 'missing'
            AND open_interest IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_close_prices (
    close_price_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    contract_id TEXT NOT NULL
        REFERENCES option_contracts(contract_id) DEFERRABLE INITIALLY DEFERRED,
    trade_date TEXT NOT NULL,
    close_price REAL,
    observation_state TEXT NOT NULL CHECK (
        observation_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (capture_id, contract_id, trade_date),
    CHECK (
        (observation_state = 'present'
         AND close_price IS NOT NULL
         AND close_price >= 0
         AND missing_reason IS NULL)
        OR (
            observation_state = 'missing'
            AND close_price IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_surface_snapshots (
    surface_snapshot_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    contract_id TEXT NOT NULL
        REFERENCES option_contracts(contract_id) DEFERRABLE INITIALLY DEFERRED,
    surface_state TEXT NOT NULL CHECK (
        surface_state IN ('present', 'missing', 'excluded')
    ),
    missing_reason TEXT,
    exclusion_reason TEXT,
    bid_price REAL,
    ask_price REAL,
    bid_size INTEGER CHECK (bid_size IS NULL OR bid_size >= 0),
    ask_size INTEGER CHECK (ask_size IS NULL OR ask_size >= 0),
    last_price REAL,
    last_size INTEGER CHECK (last_size IS NULL OR last_size >= 0),
    volume INTEGER CHECK (volume IS NULL OR volume >= 0),
    implied_volatility REAL CHECK (
        implied_volatility IS NULL OR implied_volatility >= 0
    ),
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    rho REAL,
    quote_at TEXT,
    quote_precision TEXT CHECK (
        quote_precision IS NULL OR quote_precision = 'datetime'
    ),
    trade_at TEXT,
    trade_precision TEXT CHECK (
        trade_precision IS NULL OR trade_precision = 'datetime'
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (capture_id, contract_id),
    CHECK (
        (surface_state = 'present'
         AND missing_reason IS NULL
         AND exclusion_reason IS NULL)
        OR (
            surface_state = 'missing'
            AND missing_reason IS NOT NULL
            AND exclusion_reason IS NULL
            AND bid_price IS NULL
            AND ask_price IS NULL
            AND bid_size IS NULL
            AND ask_size IS NULL
            AND last_price IS NULL
            AND last_size IS NULL
            AND volume IS NULL
            AND implied_volatility IS NULL
            AND delta IS NULL
            AND gamma IS NULL
            AND theta IS NULL
            AND vega IS NULL
            AND rho IS NULL
            AND quote_at IS NULL
            AND trade_at IS NULL
        )
        OR (
            surface_state = 'excluded'
            AND missing_reason IS NULL
            AND exclusion_reason IS NOT NULL
            AND bid_price IS NULL
            AND ask_price IS NULL
            AND bid_size IS NULL
            AND ask_size IS NULL
            AND last_price IS NULL
            AND last_size IS NULL
            AND volume IS NULL
            AND implied_volatility IS NULL
            AND delta IS NULL
            AND gamma IS NULL
            AND theta IS NULL
            AND vega IS NULL
            AND rho IS NULL
            AND quote_at IS NULL
            AND trade_at IS NULL
        )
    )
) STRICT;

CREATE TABLE option_bars (
    bar_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    contract_id TEXT NOT NULL
        REFERENCES option_contracts(contract_id) DEFERRABLE INITIALLY DEFERRED,
    timeframe TEXT NOT NULL,
    bar_start TEXT NOT NULL,
    bar_end TEXT NOT NULL,
    bar_state TEXT NOT NULL CHECK (bar_state IN ('present', 'missing')),
    missing_reason TEXT,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    volume INTEGER CHECK (volume IS NULL OR volume >= 0),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_bar_id TEXT
        REFERENCES option_bars(bar_id) DEFERRABLE INITIALLY DEFERRED,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (contract_id, timeframe, bar_start, version_sequence),
    CHECK (bar_end >= bar_start),
    CHECK (
        (bar_state = 'present'
         AND open_price IS NOT NULL
         AND high_price IS NOT NULL
         AND low_price IS NOT NULL
         AND close_price IS NOT NULL
         AND missing_reason IS NULL
         AND low_price <= high_price
         AND low_price <= open_price
         AND low_price <= close_price
         AND high_price >= open_price
         AND high_price >= close_price)
        OR (
            bar_state = 'missing'
            AND missing_reason IS NOT NULL
            AND open_price IS NULL
            AND high_price IS NULL
            AND low_price IS NULL
            AND close_price IS NULL
            AND volume IS NULL
        )
    )
) STRICT;

CREATE INDEX option_contracts_underlying_expiry
ON option_contracts(underlying_instrument_id, expiration_date, option_type);

CREATE INDEX option_surface_captures_selection
ON option_surface_captures(
    underlying_instrument_id, resolved_feed, environment, available_at
);

CREATE INDEX option_surface_snapshots_contract
ON option_surface_snapshots(contract_id, capture_id);

CREATE INDEX option_open_interest_contract_date
ON option_open_interest(contract_id, as_of_date, available_at);

CREATE INDEX option_close_prices_contract_date
ON option_close_prices(contract_id, trade_date, available_at);

CREATE INDEX option_bars_contract_time
ON option_bars(contract_id, timeframe, bar_start, available_at);

CREATE TRIGGER option_contracts_insert_guard
BEFORE INSERT ON option_contracts
WHEN EXISTS (
    SELECT 1 FROM option_contracts
    WHERE contract_id = NEW.contract_id
       OR (provider = NEW.provider AND provider_contract_id = NEW.provider_contract_id)
)
BEGIN
    SELECT RAISE(ABORT, 'option contract identity is immutable');
END;

CREATE TRIGGER option_contracts_immutable_update
BEFORE UPDATE ON option_contracts
BEGIN
    SELECT RAISE(ABORT, 'option contracts are immutable');
END;

CREATE TRIGGER option_contracts_immutable_delete
BEFORE DELETE ON option_contracts
BEGIN
    SELECT RAISE(ABORT, 'option contracts must be retired, not deleted');
END;

CREATE TRIGGER option_surface_captures_insert_guard
BEFORE INSERT ON option_surface_captures
WHEN EXISTS (
    SELECT 1 FROM option_surface_captures
    WHERE capture_id = NEW.capture_id
       OR semantic_identity = NEW.semantic_identity
       OR run_id = NEW.run_id
)
BEGIN
    SELECT RAISE(ABORT, 'option surface capture identity is immutable');
END;

CREATE TRIGGER option_surface_captures_immutable_update
BEFORE UPDATE ON option_surface_captures
BEGIN
    SELECT RAISE(ABORT, 'option surface captures are immutable');
END;

CREATE TRIGGER option_surface_captures_immutable_delete
BEFORE DELETE ON option_surface_captures
BEGIN
    SELECT RAISE(ABORT, 'option surface captures are immutable');
END;

CREATE TRIGGER option_capture_underlyings_capture_guard
BEFORE INSERT ON option_capture_underlyings
WHEN NOT EXISTS (
    SELECT 1 FROM option_surface_captures
    WHERE capture_id = NEW.capture_id
      AND underlying_instrument_id = NEW.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'capture underlying must match its immutable capture');
END;

CREATE TRIGGER option_capture_underlyings_immutable_update
BEFORE UPDATE ON option_capture_underlyings
BEGIN
    SELECT RAISE(ABORT, 'capture underlying is immutable');
END;

CREATE TRIGGER option_capture_underlyings_immutable_delete
BEFORE DELETE ON option_capture_underlyings
BEGIN
    SELECT RAISE(ABORT, 'capture underlying must not be deleted');
END;

CREATE TRIGGER option_open_interest_capture_contract_guard
BEFORE INSERT ON option_open_interest
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_contracts AS contract
      ON contract.contract_id = NEW.contract_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.underlying_instrument_id = contract.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'open interest contract does not belong to capture underlying');
END;

CREATE TRIGGER option_open_interest_immutable_update
BEFORE UPDATE ON option_open_interest
BEGIN
    SELECT RAISE(ABORT, 'option open interest observations are immutable');
END;

CREATE TRIGGER option_open_interest_immutable_delete
BEFORE DELETE ON option_open_interest
BEGIN
    SELECT RAISE(ABORT, 'option open interest observations must not be deleted');
END;

CREATE TRIGGER option_close_prices_capture_contract_guard
BEFORE INSERT ON option_close_prices
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_contracts AS contract
      ON contract.contract_id = NEW.contract_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.underlying_instrument_id = contract.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'close price contract does not belong to capture underlying');
END;

CREATE TRIGGER option_close_prices_immutable_update
BEFORE UPDATE ON option_close_prices
BEGIN
    SELECT RAISE(ABORT, 'option close price observations are immutable');
END;

CREATE TRIGGER option_close_prices_immutable_delete
BEFORE DELETE ON option_close_prices
BEGIN
    SELECT RAISE(ABORT, 'option close price observations must not be deleted');
END;

CREATE TRIGGER option_surface_snapshots_capture_contract_guard
BEFORE INSERT ON option_surface_snapshots
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_contracts AS contract
      ON contract.contract_id = NEW.contract_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.underlying_instrument_id = contract.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'surface contract does not belong to capture underlying');
END;

CREATE TRIGGER option_surface_snapshots_deliverable_guard
BEFORE INSERT ON option_surface_snapshots
WHEN EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_contracts AS contract
      ON contract.contract_id = NEW.contract_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.deliverable_policy = 'standard_only'
      AND contract.deliverable_kind = 'nonstandard'
      AND (
          NEW.surface_state <> 'excluded'
          OR NEW.exclusion_reason <> 'nonstandard_deliverable'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'standard-only capture requires explicit nonstandard exclusion');
END;

CREATE TRIGGER option_surface_snapshots_immutable_update
BEFORE UPDATE ON option_surface_snapshots
BEGIN
    SELECT RAISE(ABORT, 'option surface rows are immutable');
END;

CREATE TRIGGER option_surface_snapshots_immutable_delete
BEFORE DELETE ON option_surface_snapshots
BEGIN
    SELECT RAISE(ABORT, 'option surface rows must not be deleted');
END;

CREATE TRIGGER option_bars_capture_contract_guard
BEFORE INSERT ON option_bars
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_contracts AS contract
      ON contract.contract_id = NEW.contract_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.underlying_instrument_id = contract.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'option bar contract does not belong to capture underlying');
END;

CREATE TRIGGER option_bars_initial_sequence_guard
BEFORE INSERT ON option_bars
WHEN NEW.supersedes_bar_id IS NULL AND NEW.version_sequence <> 1
BEGIN
    SELECT RAISE(ABORT, 'initial option bar sequence must be one');
END;

CREATE TRIGGER option_bars_supersession_guard
BEFORE INSERT ON option_bars
WHEN NEW.supersedes_bar_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM option_bars AS previous
        WHERE previous.bar_id = NEW.supersedes_bar_id
          AND previous.contract_id = NEW.contract_id
          AND previous.timeframe = NEW.timeframe
          AND previous.bar_start = NEW.bar_start
          AND previous.version_sequence + 1 = NEW.version_sequence
    ) THEN RAISE(ABORT, 'invalid option bar supersession') END;
END;

CREATE TRIGGER option_bars_immutable_update
BEFORE UPDATE ON option_bars
BEGIN
    SELECT RAISE(ABORT, 'option bars are immutable');
END;

CREATE TRIGGER option_bars_immutable_delete
BEFORE DELETE ON option_bars
BEGIN
    SELECT RAISE(ABORT, 'option bars must not be deleted');
END;


CREATE TRIGGER ingestion_runs_option_capture_success_lineage_guard
BEFORE UPDATE OF status, artifact_id, snapshot_id ON ingestion_runs
WHEN NEW.status = 'succeeded'
 AND EXISTS (
    SELECT 1 FROM option_surface_captures
    WHERE run_id = NEW.run_id
 )
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1
        FROM option_surface_captures AS capture
        LEFT JOIN option_capture_underlyings AS underlying
          ON underlying.capture_id = capture.capture_id
        WHERE capture.run_id = NEW.run_id
          AND (
              capture.artifact_id <> NEW.artifact_id
              OR capture.source_snapshot_id <> NEW.snapshot_id
              OR underlying.capture_id IS NULL
          )
    ) THEN RAISE(ABORT, 'successful option run must select its capture evidence') END;
END;


CREATE TRIGGER option_contracts_running_run_guard
BEFORE INSERT ON option_contracts
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.created_run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'option contracts require a running ingestion run');
END;

CREATE TRIGGER option_surface_captures_running_run_guard
BEFORE INSERT ON option_surface_captures
WHEN NOT EXISTS (
    SELECT 1 FROM ingestion_runs
    WHERE run_id = NEW.run_id
      AND status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'option captures require a running ingestion run');
END;


CREATE TRIGGER option_capture_underlyings_running_capture_guard
BEFORE INSERT ON option_capture_underlyings
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run
      ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'capture underlying requires a running capture run');
END;

CREATE TRIGGER option_open_interest_running_capture_guard
BEFORE INSERT ON option_open_interest
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run
      ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'open interest requires a running capture run');
END;

CREATE TRIGGER option_close_prices_running_capture_guard
BEFORE INSERT ON option_close_prices
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run
      ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'close prices require a running capture run');
END;

CREATE TRIGGER option_surface_snapshots_running_capture_guard
BEFORE INSERT ON option_surface_snapshots
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run
      ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'surface rows require a running capture run');
END;

CREATE TRIGGER option_bars_running_capture_guard
BEFORE INSERT ON option_bars
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run
      ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'option bars require a running capture run');
END;

