-- Choose a feed's latest outcome without loading historical outcome payloads.
CREATE INDEX current_multi_source_outcomes_status
ON current_multi_source_outcomes(attempt_id, recorded_at DESC, outcome_id DESC);
