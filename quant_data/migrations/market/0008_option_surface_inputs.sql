-- Deliberate Stage 4 reconstruction of synchronized option-surface inputs.
-- Inputs are capture-scoped so analysis cannot silently combine capture cohorts.

CREATE INDEX option_surface_captures_inputs_ready
ON option_surface_captures(capture_id, completeness, available_at);

CREATE TABLE option_capture_underlying_quotes (
    underlying_quote_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    underlying_instrument_id TEXT NOT NULL
        REFERENCES instruments(instrument_id) DEFERRABLE INITIALLY DEFERRED,
    input_state TEXT NOT NULL CHECK (input_state IN ('present', 'missing')),
    missing_reason TEXT,
    bid_price REAL,
    ask_price REAL,
    last_price REAL,
    trade_price REAL,
    quote_at TEXT,
    quote_precision TEXT CHECK (
        quote_precision IS NULL OR quote_precision = 'datetime'
    ),
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    CHECK (
        (input_state = 'present'
         AND missing_reason IS NULL
         AND quote_at IS NOT NULL
         AND (bid_price IS NOT NULL OR ask_price IS NOT NULL
              OR last_price IS NOT NULL OR trade_price IS NOT NULL))
        OR (
            input_state = 'missing'
            AND missing_reason IS NOT NULL
            AND bid_price IS NULL
            AND ask_price IS NULL
            AND last_price IS NULL
            AND trade_price IS NULL
            AND quote_at IS NULL
        )
    )
) STRICT;

CREATE TABLE option_capture_rate_curves (
    rate_curve_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    curve_date TEXT,
    source_name TEXT,
    input_state TEXT NOT NULL CHECK (input_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    CHECK (
        (input_state = 'present'
         AND curve_date IS NOT NULL
         AND source_name IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            input_state = 'missing'
            AND curve_date IS NULL
            AND source_name IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_capture_rate_curve_points (
    rate_curve_point_id TEXT PRIMARY KEY,
    rate_curve_id TEXT NOT NULL
        REFERENCES option_capture_rate_curves(rate_curve_id)
        DEFERRABLE INITIALLY DEFERRED,
    tenor_days INTEGER NOT NULL CHECK (tenor_days > 0),
    zero_rate REAL,
    point_state TEXT NOT NULL CHECK (point_state IN ('present', 'missing')),
    missing_reason TEXT,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (rate_curve_id, tenor_days),
    CHECK (
        (point_state = 'present'
         AND zero_rate IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            point_state = 'missing'
            AND zero_rate IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_capture_dividend_sets (
    dividend_set_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL UNIQUE
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    underlying_instrument_id TEXT NOT NULL
        REFERENCES instruments(instrument_id) DEFERRABLE INITIALLY DEFERRED,
    source_name TEXT,
    input_state TEXT NOT NULL CHECK (input_state IN ('present', 'missing')),
    missing_reason TEXT,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    CHECK (
        (input_state = 'present'
         AND source_name IS NOT NULL
         AND missing_reason IS NULL)
        OR (
            input_state = 'missing'
            AND source_name IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_capture_dividend_cashflows (
    dividend_cashflow_id TEXT PRIMARY KEY,
    dividend_set_id TEXT NOT NULL
        REFERENCES option_capture_dividend_sets(dividend_set_id)
        DEFERRABLE INITIALLY DEFERRED,
    ex_date TEXT NOT NULL,
    pay_date TEXT,
    cash_amount REAL,
    cashflow_state TEXT NOT NULL CHECK (
        cashflow_state IN ('present', 'missing')
    ),
    missing_reason TEXT,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (dividend_set_id, ex_date),
    CHECK (
        (cashflow_state = 'present'
         AND cash_amount IS NOT NULL
         AND cash_amount >= 0
         AND missing_reason IS NULL)
        OR (
            cashflow_state = 'missing'
            AND cash_amount IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE option_capture_expiry_inputs (
    expiry_input_id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    expiration_date TEXT NOT NULL,
    underlying_quote_id TEXT
        REFERENCES option_capture_underlying_quotes(underlying_quote_id)
        DEFERRABLE INITIALLY DEFERRED,
    rate_curve_id TEXT
        REFERENCES option_capture_rate_curves(rate_curve_id)
        DEFERRABLE INITIALLY DEFERRED,
    dividend_set_id TEXT
        REFERENCES option_capture_dividend_sets(dividend_set_id)
        DEFERRABLE INITIALLY DEFERRED,
    input_state TEXT NOT NULL CHECK (input_state IN ('present', 'missing')),
    missing_reason TEXT,
    spot_price REAL,
    risk_free_rate REAL,
    dividend_yield REAL,
    forward_price REAL,
    available_at TEXT NOT NULL,
    available_precision TEXT NOT NULL CHECK (
        available_precision IN ('date', 'datetime')
    ),
    UNIQUE (capture_id, expiration_date),
    CHECK (
        (input_state = 'present'
         AND underlying_quote_id IS NOT NULL
         AND rate_curve_id IS NOT NULL
         AND dividend_set_id IS NOT NULL
         AND spot_price IS NOT NULL
         AND spot_price > 0
         AND risk_free_rate IS NOT NULL
         AND dividend_yield IS NOT NULL
         AND dividend_yield >= 0
         AND forward_price IS NOT NULL
         AND forward_price > 0
         AND missing_reason IS NULL)
        OR (
            input_state = 'missing'
            AND underlying_quote_id IS NULL
            AND rate_curve_id IS NULL
            AND dividend_set_id IS NULL
            AND spot_price IS NULL
            AND risk_free_rate IS NULL
            AND dividend_yield IS NULL
            AND forward_price IS NULL
            AND missing_reason IS NOT NULL
        )
    )
) STRICT;

CREATE INDEX option_capture_rate_curve_points_selection
ON option_capture_rate_curve_points(rate_curve_id, tenor_days);

CREATE INDEX option_capture_dividend_cashflows_selection
ON option_capture_dividend_cashflows(dividend_set_id, ex_date);

CREATE INDEX option_capture_expiry_inputs_selection
ON option_capture_expiry_inputs(capture_id, expiration_date, input_state);

CREATE TRIGGER option_capture_underlying_quotes_capture_guard
BEFORE INSERT ON option_capture_underlying_quotes
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN option_capture_underlyings AS underlying
      ON underlying.capture_id = capture.capture_id
    WHERE capture.capture_id = NEW.capture_id
      AND capture.underlying_instrument_id = NEW.underlying_instrument_id
      AND underlying.underlying_instrument_id = NEW.underlying_instrument_id
      AND underlying.underlying_state = 'present'
)
BEGIN
    SELECT RAISE(ABORT, 'underlying quote must match a present capture underlying');
END;

CREATE TRIGGER option_capture_underlying_quotes_immutable_update
BEFORE UPDATE ON option_capture_underlying_quotes
BEGIN
    SELECT RAISE(ABORT, 'capture underlying quotes are immutable');
END;

CREATE TRIGGER option_capture_underlying_quotes_immutable_delete
BEFORE DELETE ON option_capture_underlying_quotes
BEGIN
    SELECT RAISE(ABORT, 'capture underlying quotes must not be deleted');
END;

CREATE TRIGGER option_capture_rate_curves_capture_guard
BEFORE INSERT ON option_capture_rate_curves
WHEN NOT EXISTS (
    SELECT 1 FROM option_surface_captures
    WHERE capture_id = NEW.capture_id
)
BEGIN
    SELECT RAISE(ABORT, 'rate curve must belong to a capture');
END;

CREATE TRIGGER option_capture_rate_curves_immutable_update
BEFORE UPDATE ON option_capture_rate_curves
BEGIN
    SELECT RAISE(ABORT, 'capture rate curves are immutable');
END;

CREATE TRIGGER option_capture_rate_curves_immutable_delete
BEFORE DELETE ON option_capture_rate_curves
BEGIN
    SELECT RAISE(ABORT, 'capture rate curves must not be deleted');
END;

CREATE TRIGGER option_capture_rate_curve_points_curve_guard
BEFORE INSERT ON option_capture_rate_curve_points
WHEN NOT EXISTS (
    SELECT 1 FROM option_capture_rate_curves
    WHERE rate_curve_id = NEW.rate_curve_id
      AND input_state = 'present'
)
BEGIN
    SELECT RAISE(ABORT, 'rate points require a present capture rate curve');
END;

CREATE TRIGGER option_capture_rate_curve_points_immutable_update
BEFORE UPDATE ON option_capture_rate_curve_points
BEGIN
    SELECT RAISE(ABORT, 'capture rate curve points are immutable');
END;

CREATE TRIGGER option_capture_rate_curve_points_immutable_delete
BEFORE DELETE ON option_capture_rate_curve_points
BEGIN
    SELECT RAISE(ABORT, 'capture rate curve points must not be deleted');
END;

CREATE TRIGGER option_capture_dividend_sets_capture_guard
BEFORE INSERT ON option_capture_dividend_sets
WHEN NOT EXISTS (
    SELECT 1 FROM option_surface_captures
    WHERE capture_id = NEW.capture_id
      AND underlying_instrument_id = NEW.underlying_instrument_id
)
BEGIN
    SELECT RAISE(ABORT, 'dividend set must match its capture underlying');
END;

CREATE TRIGGER option_capture_dividend_sets_immutable_update
BEFORE UPDATE ON option_capture_dividend_sets
BEGIN
    SELECT RAISE(ABORT, 'capture dividend sets are immutable');
END;

CREATE TRIGGER option_capture_dividend_sets_immutable_delete
BEFORE DELETE ON option_capture_dividend_sets
BEGIN
    SELECT RAISE(ABORT, 'capture dividend sets must not be deleted');
END;

CREATE TRIGGER option_capture_dividend_cashflows_set_guard
BEFORE INSERT ON option_capture_dividend_cashflows
WHEN NOT EXISTS (
    SELECT 1 FROM option_capture_dividend_sets
    WHERE dividend_set_id = NEW.dividend_set_id
      AND input_state = 'present'
)
BEGIN
    SELECT RAISE(ABORT, 'dividend cashflows require a present dividend set');
END;

CREATE TRIGGER option_capture_dividend_cashflows_immutable_update
BEFORE UPDATE ON option_capture_dividend_cashflows
BEGIN
    SELECT RAISE(ABORT, 'capture dividend cashflows are immutable');
END;

CREATE TRIGGER option_capture_dividend_cashflows_immutable_delete
BEFORE DELETE ON option_capture_dividend_cashflows
BEGIN
    SELECT RAISE(ABORT, 'capture dividend cashflows must not be deleted');
END;

CREATE TRIGGER option_capture_expiry_inputs_synchronization_guard
BEFORE INSERT ON option_capture_expiry_inputs
WHEN NEW.input_state = 'present'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM option_capture_underlying_quotes
        WHERE underlying_quote_id = NEW.underlying_quote_id
          AND capture_id = NEW.capture_id
          AND input_state = 'present'
    ) THEN RAISE(ABORT, 'expiry input quote belongs to another capture') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM option_capture_rate_curves
        WHERE rate_curve_id = NEW.rate_curve_id
          AND capture_id = NEW.capture_id
          AND input_state = 'present'
    ) THEN RAISE(ABORT, 'expiry input rate curve belongs to another capture') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM option_capture_dividend_sets
        WHERE dividend_set_id = NEW.dividend_set_id
          AND capture_id = NEW.capture_id
          AND input_state = 'present'
    ) THEN RAISE(ABORT, 'expiry input dividend set belongs to another capture') END;
END;

CREATE TRIGGER option_capture_expiry_inputs_immutable_update
BEFORE UPDATE ON option_capture_expiry_inputs
BEGIN
    SELECT RAISE(ABORT, 'capture expiry inputs are immutable');
END;

CREATE TRIGGER option_capture_expiry_inputs_immutable_delete
BEFORE DELETE ON option_capture_expiry_inputs
BEGIN
    SELECT RAISE(ABORT, 'capture expiry inputs must not be deleted');
END;


CREATE TRIGGER option_capture_underlying_quotes_running_capture_guard
BEFORE INSERT ON option_capture_underlying_quotes
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'underlying quote requires a running capture run');
END;

CREATE TRIGGER option_capture_rate_curves_running_capture_guard
BEFORE INSERT ON option_capture_rate_curves
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'rate curve requires a running capture run');
END;

CREATE TRIGGER option_capture_rate_curve_points_running_capture_guard
BEFORE INSERT ON option_capture_rate_curve_points
WHEN NOT EXISTS (
    SELECT 1
    FROM option_capture_rate_curves AS curve
    JOIN option_surface_captures AS capture ON capture.capture_id = curve.capture_id
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE curve.rate_curve_id = NEW.rate_curve_id AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'rate points require a running capture run');
END;

CREATE TRIGGER option_capture_dividend_sets_running_capture_guard
BEFORE INSERT ON option_capture_dividend_sets
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'dividend set requires a running capture run');
END;

CREATE TRIGGER option_capture_dividend_cashflows_running_capture_guard
BEFORE INSERT ON option_capture_dividend_cashflows
WHEN NOT EXISTS (
    SELECT 1
    FROM option_capture_dividend_sets AS dividend_set
    JOIN option_surface_captures AS capture
      ON capture.capture_id = dividend_set.capture_id
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE dividend_set.dividend_set_id = NEW.dividend_set_id
      AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'dividend cashflows require a running capture run');
END;

CREATE TRIGGER option_capture_expiry_inputs_running_capture_guard
BEFORE INSERT ON option_capture_expiry_inputs
WHEN NOT EXISTS (
    SELECT 1
    FROM option_surface_captures AS capture
    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
    WHERE capture.capture_id = NEW.capture_id AND run.status = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'expiry inputs require a running capture run');
END;

