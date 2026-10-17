-- Metadata lookups for retained-data status; no evidence or selection changes.
CREATE INDEX fmp_stock_latest_current_captures_time
ON fmp_stock_latest_current_captures(captured_at DESC, capture_id DESC);

CREATE INDEX current_multi_source_attempts_feed
ON current_multi_source_attempts(feed_id, attempt_id);
