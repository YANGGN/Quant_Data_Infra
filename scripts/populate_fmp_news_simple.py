#!/usr/bin/env python3
"""Fetch the latest FMP stock news and upsert it into data/news.sqlite."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/news.sqlite"
ENV = ROOT / ".env"
URL = "https://financialmodelingprep.com/stable/news/stock-latest?page=0&limit=1000"


def api_key() -> str:
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        if line.strip().startswith("FMP_API_KEY="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value:
                return value
    raise RuntimeError("FMP_API_KEY is missing from .env")


def main() -> None:
    request = urllib.request.Request(
        URL,
        headers={"apikey": api_key(), "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read(64 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("FMP returned a non-list response")

    fetched_at = datetime.now(timezone.utc).isoformat()
    rows: list[tuple[object, ...]] = []
    skipped = 0
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        symbol = item.get("symbol")
        title = item.get("title")
        url = item.get("url")
        if not all(isinstance(value, str) and value.strip() for value in (symbol, title, url)):
            skipped += 1
            continue
        rows.append(
            (
                symbol.strip(),
                item.get("publishedDate") if isinstance(item.get("publishedDate"), str) else None,
                title.strip(),
                item.get("text") if isinstance(item.get("text"), str) else "",
                url.strip(),
                item.get("site") if isinstance(item.get("site"), str) else "",
                item.get("image") if isinstance(item.get("image"), str) else None,
                fetched_at,
                json.dumps(item, ensure_ascii=False, separators=(",", ":")),
            )
        )

    with sqlite3.connect(DB) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
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
            ) STRICT
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS fmp_news_articles_published "
            "ON fmp_news_articles(published_date DESC)"
        )
        connection.executemany(
            """
            INSERT INTO fmp_news_articles (
                symbol, published_date, title, body_text, url, site,
                image_url, fetched_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, url) DO UPDATE SET
                published_date=excluded.published_date,
                title=excluded.title,
                body_text=excluded.body_text,
                site=excluded.site,
                image_url=excluded.image_url,
                fetched_at=excluded.fetched_at,
                raw_json=excluded.raw_json
            """,
            rows,
        )
    print(json.dumps({"fetched": len(payload), "stored": len(rows), "skipped": skipped}))


if __name__ == "__main__":
    main()
