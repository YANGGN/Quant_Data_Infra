"""Read-only, path-free progress inspection for the Stage 10 extension."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


TARGET = Path("/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1")
LIVE = TARGET / "private/candidate/stage10-history-extension-v1/live"


def main() -> None:
    files = sorted(path for path in LIVE.rglob("*") if path.is_file())
    if not files:
        print(json.dumps({"state": "absent"}, sort_keys=True))
        return
    assert all(not path.is_symlink() and path.lstat().st_nlink == 1 for path in files)
    result_paths = sorted((LIVE / "results").glob("*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]
    completed = [record for record in records if record["disposition"] == "complete"]
    empty = [record for record in records if record["disposition"] == "complete_empty"]
    failed = [record for record in records if record["disposition"] == "terminal_failure"]
    output: dict[str, object] = {
        "closed_request_count": len(records),
        "complete_count": len(completed),
        "complete_empty_count": len(empty),
        "terminal_failure_count": len(failed),
        "failed_windows": [
            {
                "symbol": record["symbol"],
                "window_id": record["window_id"],
                "status": record["http_status"],
                "reason": record["terminal_reason"],
            }
            for record in failed
        ],
    }
    if records:
        last = max(records, key=lambda record: int(record["ordinal"]))
        output["last_closed"] = {
            "ordinal": last["ordinal"],
            "symbol": last["symbol"],
            "window_id": last["window_id"],
            "disposition": last["disposition"],
        }
    if completed:
        sentinel = next(
            (
                record
                for record in completed
                if record["symbol"] == "AAPL"
                and record["from"] == "1990-01-01"
                and record["to"] == "1994-12-31"
            ),
            None,
        )
        if sentinel is not None:
            connection = sqlite3.connect(
                f"file:{TARGET / 'stores/market.sqlite'}?mode=ro", uri=True
            )
            try:
                connection.execute("PRAGMA query_only = ON")
                first_date, last_date, row_count = connection.execute(
                    """
                    SELECT MIN(v.date), MAX(v.date), COUNT(*)
                    FROM stage10_daily_price_versions AS v
                    JOIN stage10_instruments AS i
                      ON i.instrument_id = v.instrument_id
                    JOIN stage10_daily_price_captures AS capture
                      ON capture.capture_id = v.capture_id
                    WHERE i.provider_symbol = 'AAPL'
                      AND json_extract(capture.request_scope_json, '$.from') = '1990-01-01'
                      AND json_extract(capture.request_scope_json, '$.to') = '1994-12-31'
                    """
                ).fetchone()
            finally:
                connection.close()
            assert row_count == sentinel["row_count"]
            assert "1990-01-01" <= first_date <= last_date <= "1994-12-31"
            output["sentinel"] = {
                "binding": "verified",
                "symbol": "AAPL",
                "window": "1990-01-01..1994-12-31",
                "first_date": first_date,
                "last_date": last_date,
                "row_count": row_count,
            }
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
