-- Retained-news metadata lookup only; immutable captures and timestamps are unchanged.
CREATE INDEX current_multi_source_captures_feed_time
ON current_multi_source_captures(feed_id, captured_at DESC, capture_id DESC);
