-- Deliberate forward reconstruction for repeatable current FMP stock-latest
-- polling. This remains separate from frozen fixture-news and one-shot 0005.

CREATE TABLE fmp_stock_latest_current_attempts (
    attempt_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'news.fmp.stock_latest_current_evidence'
    ),
    collector_id TEXT NOT NULL CHECK (
        collector_id = 'fmp.news.stock_latest_current'
    ),
    profile_id TEXT NOT NULL CHECK (
        profile_id = 'fmp.stock_latest.page0.limit1000.current.v1'
    ),
    poll_slot TEXT NOT NULL,
    request_scope_json TEXT NOT NULL,
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64
        AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    intent_recorded_at TEXT NOT NULL,
    intent_recorded_precision TEXT NOT NULL CHECK (
        intent_recorded_precision = 'datetime'
    ),
    UNIQUE (collector_id, profile_id, poll_slot)
) STRICT;

CREATE TABLE fmp_stock_latest_current_outcomes (
    outcome_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE
        REFERENCES fmp_stock_latest_current_attempts(attempt_id),
    outcome_kind TEXT NOT NULL CHECK (
        outcome_kind IN (
            'succeeded', 'request_failed', 'response_rejected',
            'publication_failed'
        )
    ),
    response_sha256 TEXT CHECK (
        response_sha256 IS NULL OR (
            length(response_sha256) = 64
            AND response_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    response_byte_count INTEGER CHECK (
        response_byte_count IS NULL OR (
            response_byte_count >= 0 AND response_byte_count <= 67108864
        )
    ),
    http_status INTEGER,
    content_type TEXT,
    provider_row_count INTEGER,
    accepted_row_count INTEGER,
    rejected_row_count INTEGER,
    recorded_at TEXT NOT NULL,
    recorded_precision TEXT NOT NULL CHECK (recorded_precision = 'datetime'),
    CHECK (
        (provider_row_count IS NULL
         AND accepted_row_count IS NULL
         AND rejected_row_count IS NULL)
        OR (
            provider_row_count BETWEEN 0 AND 1000
            AND accepted_row_count BETWEEN 0 AND provider_row_count
            AND rejected_row_count BETWEEN 0 AND provider_row_count
            AND accepted_row_count + rejected_row_count = provider_row_count
        )
    ),
    CHECK (
        (outcome_kind = 'succeeded'
         AND response_sha256 IS NOT NULL
         AND response_byte_count IS NOT NULL
         AND http_status = 200
         AND content_type IS NOT NULL
         AND provider_row_count IS NOT NULL)
        OR outcome_kind <> 'succeeded'
    )
) STRICT;

CREATE TABLE fmp_stock_latest_current_captures (
    capture_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES dataset_registry(dataset_id) CHECK (
        dataset_id = 'news.fmp.stock_latest_current_evidence'
    ),
    attempt_id TEXT NOT NULL UNIQUE
        REFERENCES fmp_stock_latest_current_attempts(attempt_id),
    outcome_id TEXT NOT NULL UNIQUE
        REFERENCES fmp_stock_latest_current_outcomes(outcome_id),
    request_scope_sha256 TEXT NOT NULL CHECK (
        length(request_scope_sha256) = 64
        AND request_scope_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_sha256 TEXT NOT NULL CHECK (
        length(response_sha256) = 64
        AND response_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    response_bytes BLOB NOT NULL CHECK (
        length(response_bytes) BETWEEN 1 AND 67108864
    ),
    http_status INTEGER NOT NULL CHECK (http_status = 200),
    content_type TEXT NOT NULL,
    semantic_identity TEXT NOT NULL UNIQUE CHECK (
        length(semantic_identity) = 64
        AND semantic_identity NOT GLOB '*[^0-9a-f]*'
    ),
    completeness TEXT NOT NULL CHECK (completeness = 'partial'),
    captured_at TEXT NOT NULL,
    captured_precision TEXT NOT NULL CHECK (captured_precision = 'datetime'),
    provider_row_count INTEGER NOT NULL CHECK (
        provider_row_count BETWEEN 0 AND 1000
    ),
    accepted_row_count INTEGER NOT NULL CHECK (
        accepted_row_count BETWEEN 0 AND provider_row_count
    ),
    rejected_row_count INTEGER NOT NULL CHECK (
        rejected_row_count BETWEEN 0 AND provider_row_count
    ),
    normalization_version TEXT NOT NULL CHECK (
        normalization_version = 'fmp.news.stock_latest.current.v1'
    ),
    CHECK (accepted_row_count + rejected_row_count = provider_row_count)
) STRICT;

CREATE TABLE fmp_stock_latest_current_articles (
    article_id TEXT PRIMARY KEY,
    provider_namespace TEXT NOT NULL CHECK (
        provider_namespace = 'fmp.news.stock_latest_current'
    ),
    site TEXT NOT NULL CHECK (length(site) > 0),
    source_url TEXT NOT NULL CHECK (length(source_url) > 0),
    symbol TEXT NOT NULL CHECK (length(symbol) > 0),
    created_capture_id TEXT NOT NULL
        REFERENCES fmp_stock_latest_current_captures(capture_id),
    UNIQUE (provider_namespace, site, source_url, symbol)
) STRICT;

CREATE TABLE fmp_stock_latest_current_article_versions (
    article_version_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL
        REFERENCES fmp_stock_latest_current_articles(article_id),
    mutable_content_sha256 TEXT NOT NULL CHECK (
        length(mutable_content_sha256) = 64
        AND mutable_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    title TEXT NOT NULL CHECK (length(title) > 0),
    body_text TEXT NOT NULL,
    image_url TEXT,
    published_date_raw TEXT,
    published_normalized_at TEXT,
    published_precision TEXT NOT NULL CHECK (
        published_precision IN (
            'datetime_offset', 'datetime_naive', 'date', 'unknown', 'missing'
        )
    ),
    published_offset_status TEXT NOT NULL CHECK (
        published_offset_status IN ('known', 'unknown', 'missing')
    ),
    version_sequence INTEGER NOT NULL CHECK (version_sequence > 0),
    supersedes_article_version_id TEXT
        REFERENCES fmp_stock_latest_current_article_versions(article_version_id),
    capture_id TEXT NOT NULL
        REFERENCES fmp_stock_latest_current_captures(capture_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    UNIQUE (article_id, version_sequence),
    CHECK (
        (published_precision = 'datetime_offset'
         AND published_offset_status = 'known'
         AND published_date_raw IS NOT NULL
         AND published_normalized_at IS NOT NULL)
        OR (
            published_precision IN ('datetime_naive', 'date', 'unknown')
            AND published_offset_status = 'unknown'
            AND published_date_raw IS NOT NULL
            AND published_normalized_at IS NULL
        )
        OR (
            published_precision = 'missing'
            AND published_offset_status = 'missing'
            AND published_date_raw IS NULL
            AND published_normalized_at IS NULL
        )
    )
) STRICT;

CREATE TABLE fmp_stock_latest_current_capture_articles (
    capture_id TEXT NOT NULL
        REFERENCES fmp_stock_latest_current_captures(capture_id),
    article_version_id TEXT NOT NULL
        REFERENCES fmp_stock_latest_current_article_versions(article_version_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    PRIMARY KEY (capture_id, source_row)
) STRICT;

CREATE INDEX fmp_stock_latest_current_articles_lookup
ON fmp_stock_latest_current_articles(provider_namespace, site, source_url, symbol);

CREATE INDEX fmp_stock_latest_current_article_versions_selection
ON fmp_stock_latest_current_article_versions(article_id, version_sequence);

CREATE TRIGGER fmp_stock_latest_current_capture_outcome_guard
BEFORE INSERT ON fmp_stock_latest_current_captures
WHEN NOT EXISTS (
    SELECT 1
    FROM fmp_stock_latest_current_outcomes AS outcome
    WHERE outcome.outcome_id = NEW.outcome_id
      AND outcome.attempt_id = NEW.attempt_id
      AND outcome.outcome_kind = 'succeeded'
      AND outcome.response_sha256 = NEW.response_sha256
      AND outcome.response_byte_count = length(NEW.response_bytes)
      AND outcome.http_status = NEW.http_status
      AND outcome.content_type = NEW.content_type
      AND outcome.provider_row_count = NEW.provider_row_count
      AND outcome.accepted_row_count = NEW.accepted_row_count
      AND outcome.rejected_row_count = NEW.rejected_row_count
)
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current capture requires matching successful outcome');
END;

CREATE TRIGGER fmp_stock_latest_current_article_version_lineage_guard
BEFORE INSERT ON fmp_stock_latest_current_article_versions
WHEN NOT EXISTS (
    SELECT 1 FROM fmp_stock_latest_current_articles
    WHERE article_id = NEW.article_id
) OR NOT EXISTS (
    SELECT 1 FROM fmp_stock_latest_current_captures
    WHERE capture_id = NEW.capture_id
) OR (
    NEW.version_sequence = 1
    AND NEW.supersedes_article_version_id IS NOT NULL
) OR (
    NEW.version_sequence > 1
    AND NOT EXISTS (
        SELECT 1
        FROM fmp_stock_latest_current_article_versions AS prior
        WHERE prior.article_version_id = NEW.supersedes_article_version_id
          AND prior.article_id = NEW.article_id
          AND prior.version_sequence = NEW.version_sequence - 1
    )
)
BEGIN
    SELECT RAISE(ABORT, 'invalid fmp stock latest current article-version lineage');
END;

CREATE TRIGGER fmp_stock_latest_current_capture_membership_guard
BEFORE INSERT ON fmp_stock_latest_current_capture_articles
WHEN NOT EXISTS (
    SELECT 1 FROM fmp_stock_latest_current_captures
    WHERE capture_id = NEW.capture_id
) OR NOT EXISTS (
    SELECT 1 FROM fmp_stock_latest_current_article_versions
    WHERE article_version_id = NEW.article_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'invalid fmp stock latest current capture membership');
END;

CREATE TRIGGER fmp_stock_latest_current_attempts_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_attempts
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current attempts are immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_attempts_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_attempts
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current attempts must not be deleted');
END;
CREATE TRIGGER fmp_stock_latest_current_outcomes_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_outcomes
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current outcomes are immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_outcomes_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_outcomes
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current outcomes must not be deleted');
END;
CREATE TRIGGER fmp_stock_latest_current_captures_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_captures
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current captures are immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_captures_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_captures
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current captures must not be deleted');
END;
CREATE TRIGGER fmp_stock_latest_current_articles_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_articles
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current articles are immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_articles_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_articles
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current articles must not be deleted');
END;
CREATE TRIGGER fmp_stock_latest_current_article_versions_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_article_versions
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current article versions are immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_article_versions_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_article_versions
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current article versions must not be deleted');
END;
CREATE TRIGGER fmp_stock_latest_current_capture_articles_immutable_update
BEFORE UPDATE ON fmp_stock_latest_current_capture_articles
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current capture membership is immutable');
END;
CREATE TRIGGER fmp_stock_latest_current_capture_articles_immutable_delete
BEFORE DELETE ON fmp_stock_latest_current_capture_articles
BEGIN
    SELECT RAISE(ABORT, 'fmp stock latest current capture membership must not be deleted');
END;
