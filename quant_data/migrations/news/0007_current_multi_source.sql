-- Forward-only current feeds. Separate from frozen fixture news and the
-- earlier FMP stock-latest-only current feed.

CREATE TABLE current_multi_source_attempts (
    attempt_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'news.current_multi_source_evidence'
    ),
    collector_id TEXT NOT NULL CHECK (collector_id = 'news.current_multi_source'),
    feed_id TEXT NOT NULL CHECK (feed_id IN (
        'fmp_press_releases', 'fmp_general', 'fed_press', 'ecb_press',
        'bea_news', 'eia_press', 'alpaca_benzinga'
    )),
    profile_id TEXT NOT NULL CHECK (length(profile_id) > 0),
    poll_slot TEXT NOT NULL,
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64
        AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    intent_recorded_at TEXT NOT NULL,
    intent_recorded_precision TEXT NOT NULL CHECK (intent_recorded_precision = 'datetime'),
    UNIQUE (collector_id, feed_id, request_scope_sha256)
) STRICT;

CREATE TABLE current_multi_source_outcomes (
    outcome_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES current_multi_source_attempts(attempt_id),
    outcome_kind TEXT NOT NULL CHECK (outcome_kind IN (
        'succeeded', 'request_failed', 'response_rejected', 'publication_failed'
    )),
    response_sha256 TEXT CHECK (response_sha256 IS NULL OR (
        length(response_sha256) = 64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    )),
    response_byte_count INTEGER CHECK (response_byte_count IS NULL OR (
        response_byte_count BETWEEN 0 AND 67108864
    )),
    http_status INTEGER,
    content_type TEXT,
    provider_row_count INTEGER,
    accepted_row_count INTEGER,
    rejected_row_count INTEGER,
    recorded_at TEXT NOT NULL,
    recorded_precision TEXT NOT NULL CHECK (recorded_precision = 'datetime'),
    CHECK (
        (provider_row_count IS NULL AND accepted_row_count IS NULL AND rejected_row_count IS NULL)
        OR (
            provider_row_count BETWEEN 0 AND 1000
            AND accepted_row_count BETWEEN 0 AND provider_row_count
            AND rejected_row_count BETWEEN 0 AND provider_row_count
            AND accepted_row_count + rejected_row_count = provider_row_count
        )
    ),
    CHECK (
        (outcome_kind = 'succeeded' AND response_sha256 IS NOT NULL
         AND response_byte_count IS NOT NULL AND http_status = 200
         AND content_type IS NOT NULL AND provider_row_count IS NOT NULL)
        OR outcome_kind <> 'succeeded'
    )
) STRICT;

CREATE TABLE current_multi_source_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'news.current_multi_source_evidence'
    ),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES current_multi_source_attempts(attempt_id),
    outcome_id TEXT NOT NULL UNIQUE REFERENCES current_multi_source_outcomes(outcome_id),
    feed_id TEXT NOT NULL CHECK (feed_id IN (
        'fmp_press_releases', 'fmp_general', 'fed_press', 'ecb_press',
        'bea_news', 'eia_press', 'alpaca_benzinga'
    )),
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64 AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64 AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (length(response_bytes) BETWEEN 1 AND 67108864),
    http_status INTEGER NOT NULL CHECK (http_status = 200),
    content_type TEXT NOT NULL,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64 AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness = 'partial'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    provider_row_count INTEGER NOT NULL CHECK (provider_row_count BETWEEN 0 AND 1000),
    accepted_row_count INTEGER NOT NULL CHECK (accepted_row_count BETWEEN 0 AND provider_row_count),
    rejected_row_count INTEGER NOT NULL CHECK (rejected_row_count BETWEEN 0 AND provider_row_count),
    normalization_version TEXT NOT NULL CHECK (normalization_version = 'news.current_multi_source.v1'),
    CHECK (accepted_row_count + rejected_row_count = provider_row_count)
) STRICT;

CREATE TABLE current_multi_source_articles (
    article_id TEXT PRIMARY KEY,
    feed_id TEXT NOT NULL CHECK (feed_id IN (
        'fmp_press_releases', 'fmp_general', 'fed_press', 'ecb_press',
        'bea_news', 'eia_press', 'alpaca_benzinga'
    )),
    provider TEXT NOT NULL CHECK (provider IN ('fmp', 'fed', 'ecb', 'bea', 'eia', 'alpaca')),
    source_item_key TEXT NOT NULL CHECK (length(source_item_key) > 0),
    created_capture_id TEXT NOT NULL REFERENCES current_multi_source_captures(capture_id),
    UNIQUE (feed_id, source_item_key)
) STRICT;

CREATE TABLE current_multi_source_article_versions (
    article_version_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES current_multi_source_articles(article_id),
    mutable_content_sha256 TEXT NOT NULL CHECK (
        length(mutable_content_sha256) = 64 AND mutable_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    title TEXT NOT NULL CHECK (length(title) > 0),
    summary TEXT NOT NULL,
    site TEXT NOT NULL CHECK (length(site) > 0),
    source_url TEXT,
    published_date_raw TEXT,
    published_normalized_at TEXT,
    published_precision TEXT NOT NULL CHECK (
        published_precision IN ('datetime_offset', 'datetime_naive', 'date', 'unknown', 'missing')
    ),
    published_offset_status TEXT NOT NULL CHECK (
        published_offset_status IN ('known', 'unknown', 'missing')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_article_version_id TEXT REFERENCES current_multi_source_article_versions(article_version_id),
    capture_id TEXT NOT NULL REFERENCES current_multi_source_captures(capture_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (article_id, version_sequence),
    CHECK (
        (published_precision = 'datetime_offset' AND published_offset_status = 'known'
         AND published_date_raw IS NOT NULL AND published_normalized_at IS NOT NULL)
        OR (published_precision IN ('datetime_naive', 'date', 'unknown')
            AND published_offset_status = 'unknown' AND published_date_raw IS NOT NULL
            AND published_normalized_at IS NULL)
        OR (published_precision = 'missing' AND published_offset_status = 'missing'
            AND published_date_raw IS NULL AND published_normalized_at IS NULL)
    )
) STRICT;

CREATE TABLE current_multi_source_capture_articles (
    capture_id TEXT NOT NULL REFERENCES current_multi_source_captures(capture_id),
    article_version_id TEXT NOT NULL REFERENCES current_multi_source_article_versions(article_version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (capture_id, source_row)
) STRICT;

CREATE TABLE current_multi_source_article_symbols (
    article_version_id TEXT NOT NULL REFERENCES current_multi_source_article_versions(article_version_id),
    provider_symbol TEXT NOT NULL CHECK (
        length(provider_symbol) BETWEEN 1 AND 64 AND provider_symbol = upper(provider_symbol)
    ),
    PRIMARY KEY (article_version_id, provider_symbol)
) STRICT;

CREATE INDEX current_multi_source_articles_lookup
ON current_multi_source_articles(feed_id, source_item_key);
CREATE INDEX current_multi_source_article_versions_selection
ON current_multi_source_article_versions(article_id, version_sequence);
CREATE INDEX current_multi_source_article_symbols_lookup
ON current_multi_source_article_symbols(provider_symbol, article_version_id);

CREATE TRIGGER current_multi_source_capture_outcome_guard
BEFORE INSERT ON current_multi_source_captures
WHEN NOT EXISTS (
    SELECT 1 FROM current_multi_source_attempts AS attempt
    JOIN current_multi_source_outcomes AS outcome
      ON outcome.outcome_id = NEW.outcome_id AND outcome.attempt_id = attempt.attempt_id
    WHERE attempt.attempt_id = NEW.attempt_id AND attempt.feed_id = NEW.feed_id
      AND attempt.request_scope_sha256 = NEW.request_scope_sha256
      AND outcome.outcome_kind = 'succeeded' AND outcome.response_sha256 = NEW.response_sha256
      AND outcome.response_byte_count = length(NEW.response_bytes)
      AND outcome.http_status = NEW.http_status AND outcome.content_type = NEW.content_type
      AND outcome.provider_row_count = NEW.provider_row_count
      AND outcome.accepted_row_count = NEW.accepted_row_count
      AND outcome.rejected_row_count = NEW.rejected_row_count
)
BEGIN
    SELECT RAISE(ABORT, 'current multi-source capture requires matching successful outcome');
END;

CREATE TRIGGER current_multi_source_article_version_lineage_guard
BEFORE INSERT ON current_multi_source_article_versions
WHEN NOT EXISTS (
    SELECT 1 FROM current_multi_source_articles WHERE article_id = NEW.article_id
) OR NOT EXISTS (
    SELECT 1 FROM current_multi_source_captures WHERE capture_id = NEW.capture_id
) OR (NEW.version_sequence = 1 AND NEW.supersedes_article_version_id IS NOT NULL)
OR (NEW.version_sequence > 1 AND NOT EXISTS (
    SELECT 1 FROM current_multi_source_article_versions AS prior
    WHERE prior.article_version_id = NEW.supersedes_article_version_id
      AND prior.article_id = NEW.article_id AND prior.version_sequence = NEW.version_sequence - 1
))
BEGIN
    SELECT RAISE(ABORT, 'invalid current multi-source article-version lineage');
END;

CREATE TRIGGER current_multi_source_capture_membership_guard
BEFORE INSERT ON current_multi_source_capture_articles
WHEN NOT EXISTS (
    SELECT 1 FROM current_multi_source_captures WHERE capture_id = NEW.capture_id
) OR NOT EXISTS (
    SELECT 1 FROM current_multi_source_article_versions WHERE article_version_id = NEW.article_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'invalid current multi-source capture membership');
END;

CREATE TRIGGER current_multi_source_article_symbols_guard
BEFORE INSERT ON current_multi_source_article_symbols
WHEN NOT EXISTS (
    SELECT 1 FROM current_multi_source_article_versions WHERE article_version_id = NEW.article_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'invalid current multi-source article symbol');
END;

CREATE TRIGGER current_multi_source_attempts_immutable_update
BEFORE UPDATE ON current_multi_source_attempts BEGIN
    SELECT RAISE(ABORT, 'current multi-source attempts are immutable');
END;
CREATE TRIGGER current_multi_source_attempts_immutable_delete
BEFORE DELETE ON current_multi_source_attempts BEGIN
    SELECT RAISE(ABORT, 'current multi-source attempts must not be deleted');
END;
CREATE TRIGGER current_multi_source_outcomes_immutable_update
BEFORE UPDATE ON current_multi_source_outcomes BEGIN
    SELECT RAISE(ABORT, 'current multi-source outcomes are immutable');
END;
CREATE TRIGGER current_multi_source_outcomes_immutable_delete
BEFORE DELETE ON current_multi_source_outcomes BEGIN
    SELECT RAISE(ABORT, 'current multi-source outcomes must not be deleted');
END;
CREATE TRIGGER current_multi_source_captures_immutable_update
BEFORE UPDATE ON current_multi_source_captures BEGIN
    SELECT RAISE(ABORT, 'current multi-source captures are immutable');
END;
CREATE TRIGGER current_multi_source_captures_immutable_delete
BEFORE DELETE ON current_multi_source_captures BEGIN
    SELECT RAISE(ABORT, 'current multi-source captures must not be deleted');
END;
CREATE TRIGGER current_multi_source_articles_immutable_update
BEFORE UPDATE ON current_multi_source_articles BEGIN
    SELECT RAISE(ABORT, 'current multi-source articles are immutable');
END;
CREATE TRIGGER current_multi_source_articles_immutable_delete
BEFORE DELETE ON current_multi_source_articles BEGIN
    SELECT RAISE(ABORT, 'current multi-source articles must not be deleted');
END;
CREATE TRIGGER current_multi_source_article_versions_immutable_update
BEFORE UPDATE ON current_multi_source_article_versions BEGIN
    SELECT RAISE(ABORT, 'current multi-source article versions are immutable');
END;
CREATE TRIGGER current_multi_source_article_versions_immutable_delete
BEFORE DELETE ON current_multi_source_article_versions BEGIN
    SELECT RAISE(ABORT, 'current multi-source article versions must not be deleted');
END;
CREATE TRIGGER current_multi_source_capture_articles_immutable_update
BEFORE UPDATE ON current_multi_source_capture_articles BEGIN
    SELECT RAISE(ABORT, 'current multi-source capture membership is immutable');
END;
CREATE TRIGGER current_multi_source_capture_articles_immutable_delete
BEFORE DELETE ON current_multi_source_capture_articles BEGIN
    SELECT RAISE(ABORT, 'current multi-source capture membership must not be deleted');
END;
CREATE TRIGGER current_multi_source_article_symbols_immutable_update
BEFORE UPDATE ON current_multi_source_article_symbols BEGIN
    SELECT RAISE(ABORT, 'current multi-source article symbols are immutable');
END;
CREATE TRIGGER current_multi_source_article_symbols_immutable_delete
BEFORE DELETE ON current_multi_source_article_symbols BEGIN
    SELECT RAISE(ABORT, 'current multi-source article symbols must not be deleted');
END;
