-- Adopt the pre-registry FMP table as inactive private legacy evidence.
-- Existing rows and schema are preserved; current collectors never write it.

CREATE TABLE IF NOT EXISTS fmp_news_articles (
    symbol TEXT NOT NULL,
    published_date TEXT,
    title TEXT NOT NULL,
    body_text TEXT NOT NULL,
    url TEXT NOT NULL,
    site TEXT NOT NULL,
    image_url TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (symbol, url)
) STRICT;
