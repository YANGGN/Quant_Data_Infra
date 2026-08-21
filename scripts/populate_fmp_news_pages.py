#!/usr/bin/env python3
"""Fetch FMP stock-news pages 1 through 100 into the existing table."""

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

from populate_fmp_news_simple import DB, api_key


UPSERT = """
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
"""


def main() -> None:
    key = api_key()
    total_fetched = total_stored = total_skipped = 0
    with sqlite3.connect(DB) as connection:
        for page in range(1, 101):
            request = urllib.request.Request(
                f"https://financialmodelingprep.com/stable/news/stock-latest?page={page}&limit=1000",
                headers={"apikey": key, "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read(64 * 1024 * 1024).decode("utf-8"))
            if not isinstance(payload, list):
                raise RuntimeError(f"FMP returned a non-list response on page {page}")
            if not payload:
                break
            fetched_at = datetime.now(timezone.utc).isoformat()
            rows = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                symbol, title, url = item.get("symbol"), item.get("title"), item.get("url")
                if not all(isinstance(value, str) and value.strip() for value in (symbol, title, url)):
                    continue
                rows.append((
                    symbol.strip(),
                    item.get("publishedDate") if isinstance(item.get("publishedDate"), str) else None,
                    title.strip(),
                    item.get("text") if isinstance(item.get("text"), str) else "",
                    url.strip(),
                    item.get("site") if isinstance(item.get("site"), str) else "",
                    item.get("image") if isinstance(item.get("image"), str) else None,
                    fetched_at,
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                ))
            connection.executemany(UPSERT, rows)
            connection.commit()
            total_fetched += len(payload)
            total_stored += len(rows)
            total_skipped += len(payload) - len(rows)
            print(json.dumps({"page": page, "fetched": len(payload), "stored": len(rows)}), flush=True)
    print(json.dumps({"fetched": total_fetched, "stored": total_stored, "skipped": total_skipped}))


if __name__ == "__main__":
    main()
