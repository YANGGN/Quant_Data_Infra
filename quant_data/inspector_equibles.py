"""Bounded read-only Equibles checkpoint and retained-transcript projections."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from .errors import QuantDataError, ResourceLimitError, ValidationError
from .json_codec import loads_strict

DATASET = "company.equibles.transcripts"
_PROGRESS_BYTES = 16384
_TRANSCRIPT_BYTES = 8 * 1024 * 1024
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid count")
    return value


def read_equibles_progress(root: Path | None, *, observed_at: datetime):
    """Only the host supplies the root; fixtures omit it to disable file access."""
    unavailable = {"available": False, "reason": "Equibles progress is not recorded or is unavailable."}
    if root is None:
        return unavailable
    directory = descriptor = None
    try:
        root = Path(root)
        if not root.is_absolute() or root.resolve() != root:
            return unavailable
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        directory = os.open(root, flags | os.O_DIRECTORY)
        descriptor = os.open("status.json", flags | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= _PROGRESS_BYTES
                or before.st_mtime > observed_at.timestamp()):
            return unavailable
        raw = os.read(descriptor, _PROGRESS_BYTES + 1)
        after = os.fstat(descriptor)
        if any(getattr(before,k) != getattr(after,k) for k in ("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")) or len(raw) != before.st_size:
            return unavailable
        value = loads_strict(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("contract") != "quant_data.equibles_transcript_backfill.v1":
            return unavailable
        universe = _integer(value.get("universe"), 1, 519)
        completed = _integer(value.get("completed_tickers"), 0, universe)
        transcripts = _integer(value.get("transcripts"), 0, 519000)
        requests = _integer(value.get("requests_this_run"), 0, 100)
        ticker = value.get("current_ticker")
        if ticker is not None and (not isinstance(ticker, str) or not _SYMBOL.fullmatch(ticker)):
            return unavailable
        outcome = value.get("outcome")
        if outcome not in {"complete", "blocked", "daily_quota", "provider_quota", "day_or_runtime_limit", "run_resource_limit"}:
            return unavailable
        if (completed == universe) != (outcome == "complete") or (completed == universe) != (ticker is None):
            return unavailable
        quota = value.get("quota")
        used = remaining = reset = None
        if quota is not None:
            if not isinstance(quota, dict):
                return unavailable
            used = _integer(quota.get("attempted"), 0, 100)
            remaining = _integer(quota.get("remaining"), 0, 100)
            if quota.get("reset") is not None:
                reset = datetime.fromtimestamp(_integer(quota["reset"], 0, 4102444800), timezone.utc).isoformat().replace("+00:00", "Z")
        return {"available": True, "outcome": ("quota_deferred" if outcome in {"daily_quota", "provider_quota"}
            else "bounded" if outcome in {"day_or_runtime_limit", "run_resource_limit"} else outcome),
            "universe": universe, "completed_tickers": completed, "transcripts": transcripts,
            "current_ticker": ticker, "requests_this_run": requests,
            "quota_used": used, "quota_remaining": remaining, "quota_reset_at": reset,
            "recorded_at": datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
            "reason": "Backfill paused; operator review is required." if outcome == "blocked" else None}
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError, QuantDataError):
        return unavailable
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def transcript_rows(connection, *, symbol, year, quarter, capture_id, direction, page, limit):
    """Fixed SQL on the Inspector's established immutable company connection."""
    where, parameters = [], []
    for column, value in (("symbol", symbol), ("fiscal_year", year), ("fiscal_quarter", quarter)):
        if value:
            where.append(column + "=?")
            parameters.append(value)
    base = " FROM company_equibles_transcripts" + (" WHERE " + " AND ".join(where) if where else "")
    columns = ("symbol", "fiscal_year", "fiscal_quarter", "total_turn_count", "page_count", "captured_at", "capture_id")
    if not capture_id:
        total = connection.execute("SELECT COUNT(*)" + base, parameters).fetchone()[0]
        rows = [dict(row) for row in connection.execute("SELECT " + ",".join(columns) + base
            + " ORDER BY fiscal_year " + direction.upper() + ",fiscal_quarter " + direction.upper()
            + ",symbol,captured_at DESC,capture_id LIMIT ? OFFSET ?", (*parameters, limit, (page - 1) * limit))]
        return columns, rows, total, None
    metadata = connection.execute("SELECT " + ",".join(columns) + " FROM company_equibles_transcripts WHERE capture_id=?", (capture_id,)).fetchone()
    columns = ("turn", "speaker_name", "speaker_role", "start_seconds", "end_seconds", "text")
    if metadata is None:
        return columns, [], 0, None
    if any(value and metadata[column] != value for column,value in (("symbol",symbol),("fiscal_year",year),("fiscal_quarter",quarter))):
        return columns, [], 0, None
    total = metadata["total_turn_count"]
    # Speaker turns always retain their source order; direction applies to the catalog.
    start, end = (page - 1) * limit, min(page * limit, total)
    pages = connection.execute("""SELECT page_index,turn_offset,turn_count,length(raw_body) AS bytes
        FROM company_equibles_transcript_pages WHERE capture_id=?
        AND turn_offset < ? AND turn_offset + turn_count > ? ORDER BY page_index""", (capture_id,end,start)).fetchall()
    if sum(row["bytes"] for row in pages) > _TRANSCRIPT_BYTES:
        raise ResourceLimitError("Transcript selection exceeds its byte limit; reduce the row limit")
    rows = []
    for item in pages:
        stored = connection.execute("SELECT raw_body,content_sha256 FROM company_equibles_transcript_pages WHERE capture_id=? AND page_index=?", (capture_id,item["page_index"])).fetchone()
        if hashlib.sha256(stored["raw_body"]).hexdigest() != stored["content_sha256"]:
            raise ValidationError("Stored transcript content is inconsistent")
        value = loads_strict(stored["raw_body"].decode("utf-8"))
        if not isinstance(value,dict) or not isinstance(value.get("data"),list) or len(value["data"]) != item["turn_count"]:
            raise ValidationError("Stored transcript turns are inconsistent")
        for index, turn in enumerate(value["data"], item["turn_offset"]):
            if start <= index < end:
                if not isinstance(turn,dict) or not isinstance(turn.get("text"),str):
                    raise ValidationError("Stored transcript text is invalid")
                rows.append({"turn":index+1,"speaker_name":turn.get("speakerName"),
                    "speaker_role":turn.get("speakerRole"),"start_seconds":turn.get("startSeconds"),
                    "end_seconds":turn.get("endSeconds"),"text":turn["text"]})
    if len(rows) != max(0,end-start):
        raise ValidationError("Stored transcript selection is incomplete")
    return columns, rows, total, dict(metadata)
