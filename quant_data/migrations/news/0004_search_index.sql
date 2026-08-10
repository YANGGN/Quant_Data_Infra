-- Deliberate Stage 4 derived FTS5 index over immutable canonical news versions.

CREATE VIRTUAL TABLE news_item_versions_fts
USING fts5(
    news_item_version_id UNINDEXED,
    headline,
    body,
    summary,
    content = '',
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO news_item_versions_fts (
    rowid, news_item_version_id, headline, body, summary
)
SELECT rowid, news_item_version_id, headline, body, summary
FROM news_item_versions
WHERE content_state = 'present';

CREATE TRIGGER news_item_versions_fts_insert
AFTER INSERT ON news_item_versions
WHEN NEW.content_state = 'present'
BEGIN
    INSERT INTO news_item_versions_fts (
        rowid, news_item_version_id, headline, body, summary
    ) VALUES (
        NEW.rowid, NEW.news_item_version_id, NEW.headline, NEW.body, NEW.summary
    );
END;

