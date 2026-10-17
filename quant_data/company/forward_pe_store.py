"""Fixed-root immutable forward-P/E snapshots and compact daily overlays."""
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timezone

NAME = re.compile(r"\d{8}T\d{12}Z\.sqlite")
OVERLAY = "forward_pe.daily_overlay.v1"


def selected_name(root):
    pointer = root / "current.json"
    if root.is_symlink() or pointer.is_symlink() or pointer.stat().st_size > 2048:
        raise ValueError("Invalid export pointer")
    name = json.loads(pointer.read_text())["artifact"]
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid artifact name")
    return name


@contextmanager
def open_artifact(root, name, *, seconds=5):
    if root.is_symlink() or not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid export path")
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("Invalid completed artifact")
    if any(Path(str(path)+s).exists() for s in ("-wal", "-journal")):
        raise ValueError("Export has an active journal")
    db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro&immutable=1", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic()+seconds
        db.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
        yield db
    finally:
        db.close()


def metadata(db):
    result = {}
    size = 0
    for key, value in db.execute("SELECT key,value_json FROM metadata"):
        size += len(value)
        if size > 16*1024*1024:
            raise ValueError("Metadata exceeds limit")
        result[key] = json.loads(value)
    if result.get("contract") != "announcement_forward_pe.v1" or result.get("is_point_in_time") is not False:
        raise ValueError("Unsupported research artifact")
    return result


@contextmanager
def open_current(root, *, seconds=5):
    name = selected_name(root)
    with ExitStack() as stack:
        db = stack.enter_context(open_artifact(root, name, seconds=seconds))
        meta = metadata(db)
        base = db
        if meta.get("overlay_contract"):
            if meta["overlay_contract"] != OVERLAY or meta.get("baseline_artifact") == name:
                raise ValueError("Invalid overlay")
            base = stack.enter_context(open_artifact(root, meta["baseline_artifact"], seconds=seconds))
            if metadata(base).get("overlay_contract"):
                raise ValueError("Nested overlays are unsupported")
        yield name, db, base, meta


def day_row(db, base, instrument, day):
    sql = "SELECT instrument_id,symbol,trade_date,close,forward_eps,forward_pe_proxy,status,window_id,price_version_id FROM daily WHERE instrument_id=? AND trade_date=?"
    row = db.execute(sql, (instrument, day)).fetchone()
    return row or (base.execute(sql, (instrument, day)).fetchone() if base is not db else None)


def window_detail(db, base, identity):
    if identity is None:
        return None
    row = db.execute("SELECT details_json FROM windows WHERE window_id=?", (identity,)).fetchone()
    if row is None and base is not db:
        row = base.execute("SELECT details_json FROM windows WHERE window_id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def read_refresh_status(root):
    """Read bounded host-owned receipts; never probe services or operational stores."""
    try:
        path = root / "refresh-status.json"
        if root.is_symlink() or path.is_symlink() or path.stat().st_size > 1024*1024:
            return {}
        value = json.loads(path.read_text())
        if value.get("contract") != "forward_pe.refresh_status.v1":
            return {}
        # If telemetry failed after commit, embedded publication evidence wins.
        with open_current(root) as (name,db,base,meta):
            committed=meta.get("refresh",{})
            if (committed and value.get("artifact")!=name
                    and meta["cutoff"]>=str(value.get("started_at") or value.get("completed_at") or "")):
                value=dict(committed)
        schedule=root/"refresh-schedule.json"
        if schedule.exists() and not schedule.is_symlink() and schedule.stat().st_size<=4096:
            value["schedule"]=json.loads(schedule.read_text())
        if value.get("state")=="running" and value.get("started_at"):
            started=datetime.fromisoformat(value["started_at"].replace("Z","+00:00"))
            if (datetime.now(timezone.utc)-started).total_seconds()>2800:
                value["state"]="interrupted"
        return value
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return {}
