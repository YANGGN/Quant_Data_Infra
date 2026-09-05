-- Retain exact Alpaca option response bytes before canonical surface writes.
-- Existing option-core migration bytes remain immutable.

CREATE TABLE option_raw_responses (
    raw_response_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider <> ''),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    media_type TEXT NOT NULL CHECK (media_type <> ''),
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    response_body BLOB NOT NULL,
    UNIQUE (provider, content_sha256),
    CHECK (length(response_body) = byte_count)
) STRICT;

CREATE TABLE option_capture_raw_responses (
    capture_id TEXT NOT NULL
        REFERENCES option_surface_captures(capture_id) DEFERRABLE INITIALLY DEFERRED,
    response_name TEXT NOT NULL CHECK (response_name <> ''),
    raw_response_id TEXT NOT NULL
        REFERENCES option_raw_responses(raw_response_id) DEFERRABLE INITIALLY DEFERRED,
    source_order INTEGER NOT NULL CHECK (source_order > 0),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    PRIMARY KEY (capture_id, response_name),
    UNIQUE (capture_id, source_order)
) STRICT;

CREATE INDEX option_capture_raw_responses_response
    ON option_capture_raw_responses(raw_response_id, capture_id);

CREATE TRIGGER option_raw_responses_immutable_update
BEFORE UPDATE ON option_raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw option responses are immutable');
END;

CREATE TRIGGER option_raw_responses_immutable_delete
BEFORE DELETE ON option_raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw option responses must not be deleted');
END;

CREATE TRIGGER option_capture_raw_responses_immutable_update
BEFORE UPDATE ON option_capture_raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw option response memberships are immutable');
END;

CREATE TRIGGER option_capture_raw_responses_immutable_delete
BEFORE DELETE ON option_capture_raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw option response memberships must not be deleted');
END;

-- Migration 0007 correctly made option facts immutable. Temporarily remove
-- only the four update guards needed for this reviewed data correction; every
-- insert/delete guard remains active, and the update guards are recreated
-- byte-for-byte below before the migration can commit.
DROP TRIGGER option_contracts_immutable_update;
DROP TRIGGER option_surface_snapshots_immutable_update;
DROP TRIGGER option_open_interest_immutable_update;
DROP TRIGGER option_close_prices_immutable_update;

-- Historical rows affected by the old classifier have no retained response
-- body, so no price is invented. They become explicit missing observations.
UPDATE option_surface_snapshots
SET surface_state = 'missing',
    missing_reason = 'source_quote_not_retained_before_raw_option_storage',
    exclusion_reason = NULL
WHERE surface_state = 'excluded'
  AND exclusion_reason = 'nonstandard_deliverable'
  AND contract_id IN (
      SELECT contract_id
      FROM option_contracts
      WHERE provider = 'alpaca'
        AND deliverable_kind = 'nonstandard'
        AND contract_multiplier = 100
        AND json_valid(nonstandard_deliverable_json) = 1
        AND json_type(nonstandard_deliverable_json, '$.deliverables') = 'array'
        AND json_array_length(
            json_extract(nonstandard_deliverable_json, '$.deliverables')
        ) = 1
        AND json_extract(nonstandard_deliverable_json, '$.size') = 100
        AND json_extract(nonstandard_deliverable_json, '$.root_symbol') =
            json_extract(
                nonstandard_deliverable_json,
                '$.deliverables[0].symbol'
            )
        AND EXISTS (
            SELECT 1
            FROM instruments AS underlying
            WHERE underlying.instrument_id =
                    option_contracts.underlying_instrument_id
              AND underlying.canonical_symbol = json_extract(
                    nonstandard_deliverable_json,
                    '$.root_symbol'
              )
        )
        AND lower(json_extract(
            nonstandard_deliverable_json,
            '$.deliverables[0].type'
        )) = 'equity'
        AND (
            (
                json_type(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].amount'
                ) = 'text'
                AND json_extract(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].amount'
                ) = '100'
            )
            OR (
                json_type(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].amount'
                ) IN ('integer', 'real')
                AND json_extract(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].amount'
                ) = 100
            )
        )
        AND (
            (
                json_type(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].allocation_percentage'
                ) = 'text'
                AND json_extract(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].allocation_percentage'
                ) = '100'
            )
            OR (
                json_type(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].allocation_percentage'
                ) IN ('integer', 'real')
                AND json_extract(
                    nonstandard_deliverable_json,
                    '$.deliverables[0].allocation_percentage'
                ) = 100
            )
        )
        AND COALESCE(json_type(
            nonstandard_deliverable_json,
            '$.deliverables[0].delayed_settlement'
        ), 'null') IN ('null', 'false')
  );

UPDATE option_open_interest
SET missing_reason = 'source_value_not_retained_before_raw_option_storage'
WHERE observation_state = 'missing'
  AND missing_reason = 'surface_excluded_nonstandard_deliverable'
  AND contract_id IN (
      SELECT surface.contract_id
      FROM option_surface_snapshots AS surface
      WHERE surface.missing_reason =
          'source_quote_not_retained_before_raw_option_storage'
  );

UPDATE option_close_prices
SET missing_reason = 'source_value_not_retained_before_raw_option_storage'
WHERE observation_state = 'missing'
  AND missing_reason = 'surface_excluded_nonstandard_deliverable'
  AND contract_id IN (
      SELECT surface.contract_id
      FROM option_surface_snapshots AS surface
      WHERE surface.missing_reason =
          'source_quote_not_retained_before_raw_option_storage'
  );

UPDATE option_contracts
SET deliverable_kind = 'standard',
    nonstandard_deliverable_json = NULL
WHERE provider = 'alpaca'
  AND deliverable_kind = 'nonstandard'
  AND contract_multiplier = 100
  AND json_valid(nonstandard_deliverable_json) = 1
  AND json_type(nonstandard_deliverable_json, '$.deliverables') = 'array'
  AND json_array_length(
      json_extract(nonstandard_deliverable_json, '$.deliverables')
  ) = 1
  AND json_extract(nonstandard_deliverable_json, '$.size') = 100
  AND json_extract(nonstandard_deliverable_json, '$.root_symbol') =
      json_extract(nonstandard_deliverable_json, '$.deliverables[0].symbol')
  AND EXISTS (
      SELECT 1
      FROM instruments AS underlying
      WHERE underlying.instrument_id = option_contracts.underlying_instrument_id
        AND underlying.canonical_symbol = json_extract(
              nonstandard_deliverable_json,
              '$.root_symbol'
        )
  )
  AND lower(json_extract(
      nonstandard_deliverable_json,
      '$.deliverables[0].type'
  )) = 'equity'
  AND (
      (
          json_type(
              nonstandard_deliverable_json,
              '$.deliverables[0].amount'
          ) = 'text'
          AND json_extract(
              nonstandard_deliverable_json,
              '$.deliverables[0].amount'
          ) = '100'
      )
      OR (
          json_type(
              nonstandard_deliverable_json,
              '$.deliverables[0].amount'
          ) IN ('integer', 'real')
          AND json_extract(
              nonstandard_deliverable_json,
              '$.deliverables[0].amount'
          ) = 100
      )
  )
  AND (
      (
          json_type(
              nonstandard_deliverable_json,
              '$.deliverables[0].allocation_percentage'
          ) = 'text'
          AND json_extract(
              nonstandard_deliverable_json,
              '$.deliverables[0].allocation_percentage'
          ) = '100'
      )
      OR (
          json_type(
              nonstandard_deliverable_json,
              '$.deliverables[0].allocation_percentage'
          ) IN ('integer', 'real')
          AND json_extract(
              nonstandard_deliverable_json,
              '$.deliverables[0].allocation_percentage'
          ) = 100
      )
  )
  AND COALESCE(json_type(
      nonstandard_deliverable_json,
      '$.deliverables[0].delayed_settlement'
  ), 'null') IN ('null', 'false');

CREATE TRIGGER option_contracts_immutable_update
BEFORE UPDATE ON option_contracts
BEGIN
    SELECT RAISE(ABORT, 'option contracts are immutable');
END;

CREATE TRIGGER option_surface_snapshots_immutable_update
BEFORE UPDATE ON option_surface_snapshots
BEGIN
    SELECT RAISE(ABORT, 'option surface rows are immutable');
END;

CREATE TRIGGER option_open_interest_immutable_update
BEFORE UPDATE ON option_open_interest
BEGIN
    SELECT RAISE(ABORT, 'option open interest observations are immutable');
END;

CREATE TRIGGER option_close_prices_immutable_update
BEFORE UPDATE ON option_close_prices
BEGIN
    SELECT RAISE(ABORT, 'option close price observations are immutable');
END;
