"""Daily retained-input calculations. No network, credentials, or canonical writes."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import fcntl
import hashlib
import stat
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

from ..company.forward_pe import daily_series, digest, make_windows, instant, SPLIT_ONLY, PERIOD_MATCHING_POLICY
from ..company.forward_pe_store import OVERLAY, day_row, metadata, open_current, selected_name, window_detail, read_refresh_status, open_artifact
from .forward_pe import PROJECT_ROOT, fixed_stores, subjects, attach_sharadar_links, read_inputs, session_calendar

TIMER = "quant-data-derived-refresh.timer"
STATUS = "forward_pe.refresh_status.v1"
MAX_SECONDS = 2700
MAX_CATCHUP_SESSIONS = 20
MAX_BYTES = 512 * 1024 * 1024
SCHEMA = """
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE source_inputs(instrument_id TEXT PRIMARY KEY,symbol TEXT UNIQUE NOT NULL,subject_json TEXT NOT NULL);
CREATE TABLE windows(window_id TEXT PRIMARY KEY,instrument_id TEXT NOT NULL REFERENCES source_inputs,
 symbol TEXT NOT NULL,effective_date TEXT NOT NULL,announcement TEXT NOT NULL,reported_period_end TEXT,
 forward_eps REAL,status TEXT NOT NULL,details_json TEXT NOT NULL);
CREATE TABLE daily(instrument_id TEXT NOT NULL REFERENCES source_inputs,symbol TEXT NOT NULL,
 trade_date TEXT NOT NULL,close REAL,forward_eps REAL,forward_pe_proxy REAL,status TEXT NOT NULL,
 window_id TEXT REFERENCES windows,price_version_id TEXT,
 PRIMARY KEY(instrument_id,trade_date));
CREATE INDEX daily_symbol_date ON daily(symbol,trade_date);
CREATE TABLE daily_lineage(instrument_id TEXT NOT NULL,trade_date TEXT NOT NULL,
 calculated_at TEXT NOT NULL,estimate_cutoff TEXT,price_checked_at TEXT NOT NULL,observation_kind TEXT NOT NULL,
 PRIMARY KEY(instrument_id,trade_date),FOREIGN KEY(instrument_id,trade_date) REFERENCES daily);
CREATE TABLE refresh_members(instrument_id TEXT PRIMARY KEY REFERENCES source_inputs,
 checked_at TEXT,price_checked_at TEXT,last_session TEXT,input_hash TEXT,outcome TEXT NOT NULL,
 estimate_capture TEXT,earnings_capture TEXT,issue TEXT NOT NULL);
"""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def atomic_json(path, value):
    if path.is_symlink():
        raise ValueError("Refusing a symlinked receipt")
    temp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with temp.open("x") as f:
        f.write(encoded(value)+"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


@contextmanager
def run_lock(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Export root must already exist")
    path = root / "refresh.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise ValueError("Invalid lock file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def _verify_stage(path, identity):
    current=path.lstat()
    if (not stat.S_ISREG(current.st_mode) or current.st_nlink!=1
            or (current.st_dev,current.st_ino)!=(identity.st_dev,identity.st_ino)):
        raise ValueError("Staging identity changed")


def _applicable_window(window, day, instrument):
    if not window or not window.get("ratio_allowed") or not window.get("components"):
        return window
    first=min(c["period_end"] for c in window["components"])
    # Allow the established reporting-lag bound after the next quarter ends.
    # Only new daily observations use this guard; saved history is immutable.
    if datetime.fromisoformat(day)<=datetime.fromisoformat(first)+timedelta(days=120):
        return window
    expired=dict(window,ratio_allowed=False,status="outdated_quarter_window",
                 flags=sorted(set(window["flags"]+["outdated_quarter_window"])))
    expired["window_id"]=digest({"instrument":instrument,"window":expired})
    return expired


def _store_window(out, subject, window):
    if window:
        out.execute("INSERT OR IGNORE INTO windows VALUES (?,?,?,?,?,?,?,?,?)",
            (window["window_id"],subject["instrument_id"],subject["symbol"],
             window["effective_date"],window["announcement"],window.get("reported_period_end"),
             window.get("forward_eps"),window["status"],encoded(window)))


def _repair_price(old, price, window):
    """Correct only the saved price; preserve the day's original denominator."""
    close = Decimal(str(price["close_value"]))
    if not close.is_finite() or close <= 0:
        raise ValueError("Invalid saved close")
    value = list(old)
    value[3] = float(close)
    value[8] = price["version_id"]
    if value[3] == old[3]:
        return tuple(value)
    value[5] = None
    value[6] = window["status"] if window else "no_announcement_anchor"
    if window and window["ratio_allowed"]:
        ratio = close / Decimal(window["forward_eps"])
        if not math.isfinite(float(ratio)):
            value[6] = "nonfinite_pe"
        else:
            value[5] = float(ratio)
    return tuple(value)


def retain_serving_copies(root, current, previous, baseline):
    """Remove only redundant copies created by this runner; keep baseline and rollback."""
    protected={current,previous,baseline}
    removed=[]
    candidates=sorted(root.glob("*.sqlite"))
    if len(candidates)>500:
        return removed
    for path in candidates:
        if path.name in protected or not (root/(path.name+".receipt.json")).is_file():
            continue
        try:
            with open_artifact(root,path.name) as db:
                meta=metadata(db)
            if (meta.get("created_by")!="quant_data.operations.derived_refresh"
                    or meta.get("overlay_contract")!=OVERLAY or meta.get("baseline_artifact")!=baseline):
                continue
            # Fixed-root, generated basename; symlinks and hardlinks rejected by open_artifact.
            if selected_name(root)!=current:
                break
            path.unlink()
            removed.append(path.name)
        except (OSError,ValueError,sqlite3.Error):
            continue
    return removed


def _terminal_status(root, prior, *, state, now, **fields):
    value = {k:v for k,v in prior.items() if k not in ("state","started_at","completed_at","issue","counts")}
    value.update(contract=STATUS,state=state,completed_at=now,**fields)
    atomic_json(root/"refresh-status.json", value)
    return value


def refresh(root, *, now, selected, sessions, read, max_seconds=MAX_SECONDS, repair_symbols=None):
    """Internal explicit-root/injected-reader API; offline tests never default live."""
    if now.tzinfo is None:
        raise ValueError("An aware clock is required")
    now = now.astimezone(timezone.utc)
    cutoff = now.isoformat(timespec="microseconds").replace("+00:00","Z")
    completed = [s for s in sessions if instant(s[1]) <= now]
    if not completed:
        raise ValueError("No completed trading sessions")
    end = completed[-1][0]
    if len(selected)>2500 or len({s["instrument_id"] for s in selected})!=len(selected):
        raise ValueError("Invalid bounded universe")
    if repair_symbols is not None:
        repair_symbols = frozenset(repair_symbols)
        if not repair_symbols or len(repair_symbols) > 207 or repair_symbols != {s["symbol"] for s in selected}:
            raise ValueError("Review repair requires an exact nonempty cohort of at most 207 symbols")
    began = time.monotonic()
    deadline = began + max_seconds
    previous_status = read_refresh_status(root)
    with run_lock(root):
        atomic_json(root/"refresh-status.json", dict(previous_status,contract=STATUS,state="running",
                    started_at=cutoff,completed_at=None,issue=None))
        try:
            with open_current(root, seconds=max_seconds) as (parent, current, base, oldmeta):
                baseline = oldmeta.get("baseline_artifact",parent)
                base_meta = metadata(base)
                roster = dict(base.execute("SELECT instrument_id,symbol FROM source_inputs"))
                if not 1<=len(roster)<=2500:
                    raise ValueError("Invalid saved universe")
                if repair_symbols is not None and (not repair_symbols <= set(roster.values()) or end != oldmeta["end"]):
                    raise ValueError("Review repair is limited to the latest saved completed session")
                candidates = {s["instrument_id"]:s for s in selected}
                name = now.strftime("%Y%m%dT%H%M%S%fZ")+".sqlite"
                output = root/name
                stage = root/name.replace(".sqlite",".partial.sqlite")
                if output.exists() or output.is_symlink():
                    raise FileExistsError("Run timestamp already exists")
                descriptor=os.open(stage,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(descriptor,"wb") as target:
                    stage_identity=os.fstat(target.fileno())
                    if not stat.S_ISREG(stage_identity.st_mode) or stage_identity.st_nlink!=1:
                        raise ValueError("Invalid staging file")
                    if oldmeta.get("overlay_contract"):
                        with (root/parent).open("rb") as source:
                            shutil.copyfileobj(source,target)
                    target.flush();os.fsync(target.fileno())
                _verify_stage(stage,stage_identity)
                with sqlite3.connect(stage) as out:
                    out.execute("PRAGMA foreign_keys=ON")
                    out.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
                    if not oldmeta.get("overlay_contract"):
                        out.executescript(SCHEMA)
                        for instrument,symbol in roster.items():
                            out.execute("INSERT INTO source_inputs VALUES (?,?,?)",
                                (instrument,symbol,encoded(dict(instrument_id=instrument,symbol=symbol))))
                        out.commit()
                    statuses = Counter(oldmeta["statuses"])
                    summary = {s["symbol"]:dict(s,statuses=dict(s["statuses"])) for s in oldmeta["summary"]}
                    changed = appended = repaired = period_repairs = 0
                    outcomes = Counter()
                    for index,(instrument,symbol) in enumerate(sorted(roster.items(),key=lambda r:r[1]),1):
                        if repair_symbols is not None and symbol not in repair_symbols:
                            continue
                        member = out.execute("SELECT * FROM refresh_members WHERE instrument_id=?",(instrument,)).fetchone()
                        last = out.execute("SELECT MAX(trade_date) FROM daily WHERE instrument_id=?",(instrument,)).fetchone()[0]
                        base_last = base.execute("SELECT MAX(trade_date) FROM daily WHERE instrument_id=?",(instrument,)).fetchone()[0]
                        last = max(x for x in (last,base_last) if x)
                        price_checked = member[2] if member else base_meta["cutoff"]
                        issue = ""
                        outcome = "current"
                        fingerprint = member[4] if member else None
                        estimate_capture = member[6] if member else None
                        earnings_capture = member[7] if member else None
                        subject = candidates.get(instrument)
                        out.execute("SAVEPOINT ticker")
                        local_changes=[]
                        try:
                            if time.monotonic()>deadline-5:
                                raise TimeoutError("Run time bound reached")
                            if subject is None or subject["symbol"]!=symbol:
                                raise ValueError("Saved universe identity is not established")
                            start = max(base_meta["start"],(datetime.fromisoformat(last)-timedelta(days=10)).date().isoformat())
                            inputs = read(subject,cutoff,start,end,price_checked,base_meta["start"])
                            if inputs["instrument_id"]!=instrument or inputs["symbol"]!=symbol or inputs["cutoff"]!=cutoff:
                                raise ValueError("Source reader identity/cutoff mismatch")
                            freshness = inputs.get("freshness",{})
                            estimate_capture = freshness.get("analyst-estimates")
                            earnings_capture = freshness.get("earnings")
                            fingerprint = digest({k:inputs.get(k, []) for k in ("estimates","earnings","statements","transcripts","price_basis","flags","reviewed_periods")})
                            stale = [label for label,stamp in (("estimates",estimate_capture),("earnings",earnings_capture))
                                     if not stamp or instant(stamp).date().isoformat()<end]
                            if stale:
                                outcome="stale_inputs"; issue="Older or unchecked "+", ".join(stale)+" inputs; source cutoffs retained."
                            new_sessions=[] if repair_symbols is not None else [s for s in completed if s[0]>last][:MAX_CATCHUP_SESSIONS]
                            windows=[]
                            if new_sessions or repair_symbols is not None:
                                windows=make_windows(inputs,sessions,allow_unverified_basis=bool(base_meta["allow_unverified_basis"]))
                                for w in windows:
                                    w["calculation_cutoff"]=cutoff
                                    w["observation_kind"]="daily_capture"
                                    w["estimate_capture"]=estimate_capture
                                    w["earnings_capture"]=earnings_capture
                                    w["flags"]=sorted(set(w["flags"]+["daily_capture_not_market_close"]))
                                    for label in stale:
                                        w["flags"].append("stale_"+label+"_input")
                                    w["window_id"]=digest({"instrument":instrument,"window":w})
                                if repair_symbols is not None:
                                    old=day_row(out,base,instrument,end)
                                    if old is None and (len(completed)<2 or last!=completed[-2][0]):
                                        raise ValueError("Review repair can fill only one missing latest-session observation")
                                    old_window=window_detail(out,base,old[7]) if old else None
                                    applicable=[w for w in windows if w["effective_date"]<=end]
                                    window=applicable[-1] if applicable else None
                                    review_evidence=(window or {}).get("mapping_evidence",{})
                                    if not review_evidence.get("review_sha256"):
                                        raise ValueError("Latest event has no applicable reviewed period")
                                    if (old_window or {}).get("mapping_evidence",{}).get("review_sha256") != review_evidence["review_sha256"]:
                                        window["observation_kind"]="reviewed_period_correction"
                                        window["flags"]=sorted(set(window["flags"]+["reviewed_period_correction"]))
                                        window["window_id"]=digest({"instrument":instrument,"window":window})
                                        window=_applicable_window(window,end,instrument)
                                        # Preserve the published close and its version. This operation only repairs the denominator.
                                        saved_prices=inputs["prices"] if old is None else ([] if old[3] is None else [dict(trade_date=end,close_value=old[3],
                                            version_id=old[8],captured_at=cutoff)])
                                        repaired_inputs=dict(inputs,start=end,end=end,prices=saved_prices)
                                        row=next(daily_series(repaired_inputs,[(end,completed[-1][1])],[window]))
                                        record=(instrument,symbol,end,float(row["close"]) if row["close"] is not None else None,
                                                float(row["forward_eps"]) if row["forward_eps"] is not None else None,
                                                float(row["forward_pe_proxy"]) if row["forward_pe_proxy"] is not None else None,
                                                row["status"],row["window_id"],row["price_version_id"] if old is None else old[8])
                                        _store_window(out,subject,window)
                                        out.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?,?,?)",record)
                                        prior_lineage=out.execute("SELECT price_checked_at FROM daily_lineage WHERE instrument_id=? AND trade_date=?",(instrument,end)).fetchone()
                                        out.execute("INSERT OR REPLACE INTO daily_lineage VALUES (?,?,?,?,?,?)",
                                            (instrument,end,cutoff,cutoff,prior_lineage[0] if prior_lineage else (base_meta["cutoff"] if old else cutoff),"reviewed_period_correction"))
                                        local_changes.append((old,record,"period" if old else "new_period"))
                                        last=end
                            if new_sessions:
                                fresh_inputs=dict(inputs,start=new_sessions[0][0],end=new_sessions[-1][0])
                                by_window={w["window_id"]:w for w in windows}
                                for row in daily_series(fresh_inputs,new_sessions,windows):
                                    window=by_window.get(row["window_id"])
                                    window=_applicable_window(window,row["trade_date"],instrument)
                                    if window is not None and window["window_id"]!=row["window_id"]:
                                        row=dict(row,window_id=window["window_id"],forward_pe_proxy=None,status=window["status"])
                                        by_window[window["window_id"]]=window
                                        outcome="stale_inputs";issue="Saved quarter window is outdated; new ratios are unavailable."
                                    record=(instrument,symbol,row["trade_date"],
                                      float(row["close"]) if row["close"] is not None else None,
                                      float(row["forward_eps"]) if row["forward_eps"] is not None else None,
                                      float(row["forward_pe_proxy"]) if row["forward_pe_proxy"] is not None else None,
                                      row["status"],row["window_id"],row["price_version_id"])
                                    _store_window(out,subject,by_window.get(row["window_id"]))
                                    out.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?,?,?)",record)
                                    out.execute("INSERT INTO daily_lineage VALUES (?,?,?,?,?,?)",
                                        (instrument,row["trade_date"],cutoff,cutoff,cutoff,"daily_capture"))
                                    local_changes.append((None,record,"new"))
                                last=new_sessions[-1][0]
                            if repair_symbols is not None:
                                pass  # The explicit review repair never scans or rewrites historical prices.
                            elif inputs["price_basis"]==SPLIT_ONLY:
                                saved_versions=dict(base.execute("SELECT trade_date,price_version_id FROM daily WHERE instrument_id=?",(instrument,)))
                                saved_versions.update(out.execute("SELECT trade_date,price_version_id FROM daily WHERE instrument_id=?",(instrument,)))
                                for price in inputs["prices"]:
                                    day=price["trade_date"]
                                    if saved_versions.get(day)==price["version_id"]:
                                        continue
                                    old=day_row(out,base,instrument,day)
                                    if old is None or old[8]==price["version_id"]:
                                        continue
                                    w=window_detail(out,base,old[7])
                                    record=_repair_price(old,price,w)
                                    _store_window(out,subject,w)
                                    out.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?,?,?,?)",record)
                                    lineage=out.execute("SELECT calculated_at,estimate_cutoff,observation_kind FROM daily_lineage WHERE instrument_id=? AND trade_date=?",(instrument,day)).fetchone()
                                    calculated,estimate_at,kind=lineage or (base_meta["cutoff"],base_meta.get("estimate_cutoff",base_meta["cutoff"]),"reconstructed_baseline_price_repair")
                                    out.execute("INSERT OR REPLACE INTO daily_lineage VALUES (?,?,?,?,?,?)",
                                        (instrument,day,calculated,estimate_at,cutoff,kind))
                                    local_changes.append((old,record,"repair"))
                                price_checked=cutoff
                            else:
                                outcome="stale_inputs";issue="Saved price basis could not be verified; historical prices were preserved."
                            if last<end:
                                outcome="catchup_pending";issue="More than 20 missed sessions; remaining sessions await the next run."
                            out.execute("RELEASE ticker")
                        except Exception as exc:
                            # Each source cohort is independent. Global bounds still stop publication.
                            out.execute("ROLLBACK TO ticker");out.execute("RELEASE ticker")
                            local_changes=[]
                            last=max(x for x in (base_last, out.execute("SELECT MAX(trade_date) FROM daily WHERE instrument_id=?",(instrument,)).fetchone()[0]) if x)
                            price_checked=member[2] if member else base_meta["cutoff"]
                            outcome="waiting_inputs";issue=type(exc).__name__+": source inputs unavailable; prior values preserved."
                        for old,record,kind in local_changes:
                            if old:
                                statuses[old[6]]-=1;summary[symbol]["statuses"][old[6]]-=1
                                summary[symbol]["numeric_pe"]-=int(old[5] is not None)
                            else:
                                summary[symbol]["rows"]+=1
                            statuses[record[6]]+=1
                            summary[symbol]["statuses"][record[6]]=summary[symbol]["statuses"].get(record[6],0)+1
                            summary[symbol]["numeric_pe"]+=int(record[5] is not None)
                            changed+=1;appended+=kind in ("new","new_period");repaired+=kind=="repair";period_repairs+=kind in ("period","new_period")
                        out.execute("INSERT OR REPLACE INTO refresh_members VALUES (?,?,?,?,?,?,?,?,?)",
                            (instrument,cutoff,price_checked,last,fingerprint,outcome,estimate_capture,earnings_capture,issue))
                        base_windows={r[0] for r in base.execute("SELECT window_id FROM windows WHERE instrument_id=?",(instrument,))}
                        extra_windows={r[0] for r in out.execute("SELECT window_id FROM windows WHERE instrument_id=?",(instrument,))}-base_windows
                        summary[symbol]["windows"]=len(base_windows)+len(extra_windows)
                        outcomes[outcome]+=1
                        out.commit()
                        if stage.stat().st_size>MAX_BYTES:
                            raise ValueError("Compact snapshot exceeds 512 MiB")
                        if time.monotonic()>deadline:
                            raise TimeoutError("Run deadline reached")
                        if index%100==0 or index==len(roster):
                            print(encoded(dict(state="refreshing",completed=index,total=len(roster),appended=appended,repaired=repaired)),flush=True)
                    if outcomes["waiting_inputs"]==len(repair_symbols or roster):
                        raise ValueError("No source cohort was readable; current snapshot preserved")
                    statuses={k:v for k,v in statuses.items() if v}
                    result=dict(contract=STATUS,state="complete_with_gaps" if outcomes["waiting_inputs"] or outcomes["stale_inputs"] or outcomes["catchup_pending"] else "complete",
                        completed_at=(now+timedelta(seconds=time.monotonic()-began)).isoformat().replace("+00:00","Z"),artifact=name,baseline_artifact=baseline,target_session=end,
                        symbols=len(repair_symbols or roster),changed_rows=changed,appended_rows=appended,repaired_prices=repaired,
                        repaired_periods=period_repairs,operation="reviewed_period_repair" if repair_symbols is not None else "daily_refresh",
                        outcomes=dict(outcomes),numeric_pe=sum(s["numeric_pe"] for s in summary.values()),
                        daily_rows=sum(s["rows"] for s in summary.values()),statuses=statuses,
                        provider_requests=0,canonical_writes=0,elapsed_seconds=round(time.monotonic()-began,3))
                    result.update(last_successful_publication=result["completed_at"],previous_artifact=parent)
                    code_root=Path(__file__).resolve().parents[2]
                    provenance={str(path.relative_to(code_root)):hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in (Path(__file__),code_root/"quant_data/operations/forward_pe.py",
                                     code_root/"quant_data/company/forward_pe.py",code_root/"quant_data/company/forward_pe_reviews.py",
                                     code_root/"config/forward_pe_reviewed_periods.json")}
                    extension=[list(session) for session in sessions if session[0]>base_meta["end"]]
                    meta=dict(implementation_sha256=provenance,
                        calendar_extension=extension,calendar_extension_sha256=digest(extension),
                        price_detection_policy="bounded_current_version_scan.v1",
                        window_validity_policy="first_forward_period_end_plus_120_days.v1",
                        contract=base_meta["contract"],is_point_in_time=False,cutoff=cutoff,
                        start=base_meta["start"],end=end,overlay_contract=OVERLAY,baseline_artifact=baseline,
                        created_by="quant_data.operations.derived_refresh",baseline_cutoff=base_meta["cutoff"],allow_unverified_basis=base_meta["allow_unverified_basis"],
                        statuses=statuses,summary=list(summary.values()),refresh=result,
                        ratio_policy=base_meta.get("ratio_policy"),period_matching_policy=PERIOD_MATCHING_POLICY)
                    out.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)",[(k,encoded(v)) for k,v in meta.items()])
                    out.commit()
                    if out.execute("PRAGMA quick_check").fetchone()[0]!="ok" or out.execute("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("Derived snapshot integrity failed")
                    bad=out.execute("SELECT 1 FROM daily WHERE forward_pe_proxy IS NOT NULL AND "
                        "(close IS NULL OR forward_eps IS NULL OR forward_eps=0 OR "
                        "ABS(forward_pe_proxy-close/forward_eps)>1e-9*MAX(1,ABS(forward_pe_proxy))) LIMIT 1").fetchone()
                    if bad:
                        raise ValueError("Derived ratio arithmetic failed")
                    if selected_name(root)!=parent:
                        raise ValueError("Serving pointer changed during refresh")
                _verify_stage(stage,stage_identity)
                os.link(stage,output,follow_symlinks=False);stage.unlink()
                # Completion evidence is durable before the serving commit.
                atomic_json(root/(name+".receipt.json"),result)
                atomic_json(root/"current.json",dict(artifact=name))
                # The pointer is the commit boundary. Telemetry cannot undo it.
                try:
                    atomic_json(root/"refresh-status.json",result)
                    if repair_symbols is None:
                        retain_serving_copies(root,name,parent,baseline)
                except OSError:
                    result["telemetry_issue"]="Publication committed; status receipt update unavailable."
                return result
        except BaseException as exc:
            _terminal_status(root,previous_status,state="failed",now=cutoff,issue=type(exc).__name__+": refresh failed; last published data retained.")
            raise


def completion_exit_code(result):
    """The shared activity recorder requires nonzero for partial work."""
    return 0 if result["state"]=="complete" else 2


def main(argv=None):
    import sys
    if tuple(sys.argv[1:] if argv is None else argv):
        raise ValueError("The scheduled runner accepts no arguments")
    root=PROJECT_ROOT/"exports/forward-pe"
    now=datetime.now(timezone.utc)
    cutoff=now.isoformat(timespec="microseconds").replace("+00:00","Z")
    try:
        stores=fixed_stores()
        with open_current(root,seconds=30) as (_,db,base,meta):
            wanted=dict(base.execute("SELECT symbol,instrument_id FROM source_inputs"))
        selected=[s for s in subjects(stores,tuple(wanted),cutoff,strict=False) if wanted.get(s["symbol"])==s["instrument_id"]]
        selected=attach_sharadar_links(stores,selected,cutoff)
        sessions=session_calendar((now+timedelta(days=7)).date().isoformat())
        result=refresh(root,now=now,selected=selected,sessions=sessions,
            read=lambda subject,at,start,end,since,history:read_inputs(stores,subject,at,start,end,changed_since=since,history_start=history))
        from .fetch_run_summary import record_report
        try:
            record_report(TIMER,result)
        except OSError:
            result["telemetry_issue"]="Publication committed; run-summary update unavailable."
        print(encoded(result),flush=True)
        return completion_exit_code(result)
    except Exception as exc:
        try:
            with run_lock(root):
                _terminal_status(root,read_refresh_status(root),state="failed",now=cutoff,
                    issue=type(exc).__name__+": source preparation or refresh failed; prior publication retained.")
        except (OSError,ValueError):
            pass
        print(encoded(dict(state="failed",error_type=type(exc).__name__,provider_requests=0)),flush=True)
        return 75


if __name__=="__main__":
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli("quant-data-derived-refresh.timer",main,argv=sys.argv[1:]))
