"""Read-only daily options projection to the existing private Site cache."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import time
from quant_data.options.store import OptionsStore,unpacked
from quant_data.options.universe import TIER1_ETFS
from quant_data.stores import StoreWriteLock
from .history_model import DERIVATION_VERSION,project_daily
from .policy import NY

BATCH_SIZE=500

def completed_day(now):
    local=now.astimezone(NY)
    return local.date() if local.hour>=18 else local.date()-timedelta(days=1)

def read_batch(root,after,now,*,through=None):
    """Freeze at most 500 current captures; release the source lock before HTTP."""
    if type(after) is not int or after<0:raise ValueError("daily_cursor")
    store=OptionsStore(Path(root));cutoff=completed_day(now).isoformat()
    with StoreWriteLock(store.path,timeout_seconds=10):
        with store.read() as c:
            high=c.execute("SELECT COALESCE(MAX(capture_id),0) FROM option_captures").fetchone()[0]
            if high<after:raise ValueError("daily_source_high_water_regressed")
            ceiling=high if through is None else min(high,through)
            rows=c.execute("SELECT c.capture_id,c.symbol,c.session_date,c.semantic_sha256,c.captured_at,c.underlying_price FROM option_current p JOIN option_captures c USING(capture_id) WHERE c.capture_id>? AND c.capture_id<=? ORDER BY c.capture_id LIMIT ?",(after,ceiling,BATCH_SIZE)).fetchall()
            ready=[]
            for row in rows:
                if row["symbol"] not in TIER1_ETFS:raise ValueError("daily_source_symbol")
                # Do not advance past a not-yet-completed source session.
                if row["session_date"]>cutoff:break
                ready.append(dict(row))
            if not ready:return [],{"source_high_water":high,"source_cutoff":cutoff,"waiting_for_close":bool(rows)}
            ids=[x["capture_id"] for x in ready];marks=",".join("?" for _ in ids)
            summaries=defaultdict(list);details=defaultdict(list)
            for row in c.execute(f"SELECT capture_id,population,dimension,bucket,right,payload FROM option_daily_summaries WHERE capture_id IN ({marks}) AND population='full' AND dimension='overall' AND bucket='all'",ids):
                summaries[row[0]].append({"population":row[1],"dimension":row[2],"bucket":row[3],"right":row[4],"stats":unpacked(row[5])})
            # Read only stable ATM anchors. Fetch one OI date separately only
            # when all anchors lack it; avoid a correlated scan for every detail.
            query=f"SELECT capture_id,payload FROM option_details WHERE capture_id IN ({marks}) AND reasons LIKE ?"
            for row in c.execute(query,ids+['%"anchor:1"%']):details[row[0]].append(unpacked(row[1]))
            for cid in ids:
                if not any(row.get("oi") is not None for row in details[cid]):
                    row=c.execute("SELECT payload FROM option_details WHERE capture_id=? AND open_interest IS NOT NULL ORDER BY contract_id LIMIT 1",(cid,)).fetchone()
                    if row:
                        extra=unpacked(row[0])
                        if extra["contract_id"] not in {x["contract_id"] for x in details[cid]}:details[cid].append(extra)
    points=[project_daily(x,summaries[x["capture_id"]],details[x["capture_id"]]) for x in ready]
    return points,{"source_high_water":high,"source_cutoff":cutoff,"waiting_for_close":False}

def sync_history(root,site,now,*,max_batches=1,through=None,deadline=None,progress=None):
    if type(max_batches) is not int or not 1<=max_batches<=80:raise ValueError("daily_batch_cap")
    state=site.daily_state()
    if state.get("derivation_version")!=DERIVATION_VERSION:raise ValueError("daily_derivation_version")
    cursor=state.get("last_capture_id")
    if type(cursor) is not int or cursor<0:raise ValueError("daily_cursor")
    total=0;batches=0;status="batch_limit"
    for _ in range(max_batches):
        if deadline is not None and time.monotonic()>deadline-20:status="deadline";break
        points,metadata=read_batch(root,cursor,now,through=through)
        if not points:status="waiting_for_close" if metadata["waiting_for_close"] else "up_to_date";break
        result=site.daily_publish({"schema_version":1,"derivation_version":DERIVATION_VERSION,
            "expected_capture_id":cursor,"source_cutoff":metadata["source_cutoff"],"points":points})
        expected=points[-1]["source_capture_id"]
        if result.get("last_capture_id")!=expected:raise ValueError("daily_acknowledgment_cursor")
        cursor=expected;total+=len(points);batches+=1
        if progress:progress({"batches":batches,"points":total,"last_capture_id":cursor})
    return {"status":status,"points":total,"batches":batches,"last_capture_id":cursor,"provider_requests":0}
