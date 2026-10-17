"""One finite XLB multi-day experiment. Never alters the production baseline."""
from __future__ import annotations
import argparse,fcntl,json,os,time
from datetime import date,datetime,timezone
from pathlib import Path
from statistics import median
from hashlib import sha256
from .baseline import aggregate_expiration,expirations
from .backfill import backfill_window
from .baseline_store import BaselineStore
from .model import canonical,contract,nonnegative,timestamp
from .policy import NY
from .transport import MonitorThetaTransport
from quant_data.options.transport import Budget,ProviderFailure

SYMBOL="XLB"
SESSIONS=("2026-08-14","2026-08-17")
MAX_REQUESTS=40
MAX_BYTES=128*1024**2
MAX_SECONDS=600
OUTPUT=".local/options-monitor-batch-probe-20260930"


def aggregate_vectorized(rows,symbol,session,expiration,close_minute):
    """Same validation as the scalar oracle; NumPy performs integer reduction.

    Include conversion/validation in timing. Object integers preserve sums that
    could overflow int64; no floating-point weighted histogram is used.
    """
    import numpy as np
    if not rows:raise ValueError("historical_expiration_empty")
    if close_minute not in (780,960):raise ValueError("historical_close")
    seen=set();indices=[];sides=[];volumes=[]
    for row in rows:
        key=contract(row,symbol)
        if key[0]!=expiration:raise ValueError("historical_expiration_scope")
        stamp=timestamp(row.get("timestamp"))
        if stamp is None:raise ValueError("historical_timestamp")
        local=stamp.astimezone(NY);minute=local.hour*60+local.minute
        if local.date().isoformat()!=session or local.second or local.microsecond or minute%5:
            raise ValueError("historical_bar_alignment")
        if minute==close_minute:continue
        if not 570<=minute<close_minute:raise ValueError("historical_bar_outside_session")
        identity=(*key,minute)
        if identity in seen:raise ValueError("historical_duplicate_bar")
        seen.add(identity)
        volume=nonnegative(row.get("volume"))
        if volume is None or int(volume)!=volume:raise ValueError("historical_volume")
        indices.append((minute-570)//5);sides.append(0 if key[2]=="C" else 1);volumes.append(int(volume))
    if not seen:raise ValueError("historical_no_regular_bars")
    dtype=object if max(volumes)*len(volumes)>np.iinfo(np.int64).max else np.int64
    totals=np.zeros(((close_minute-570)//5,2),dtype=dtype)
    np.add.at(totals,(np.asarray(indices),np.asarray(sides)),np.asarray(volumes,dtype=dtype))
    return [[570+5*i,*values] for i,values in enumerate(totals.tolist())]


def split_sessions(rows,sessions):
    groups={session:[] for session in sessions}
    for row in rows:
        stamp=timestamp(row.get("timestamp"))
        if stamp is None:raise ValueError("historical_timestamp")
        session=stamp.astimezone(NY).date().isoformat()
        if session not in groups:raise ValueError("historical_unrequested_session")
        groups[session].append(row)
    if any(not group for group in groups.values()):raise ValueError("historical_missing_session")
    return groups


def compare_engines(rows,symbol,sessions,expiration,repeats=3):
    timings={};results={}
    for name,reducer in (("scalar",aggregate_expiration),("numpy",aggregate_vectorized)):
        elapsed=[]
        for _ in range(repeats):
            start=time.perf_counter();groups=split_sessions(rows,sessions)
            values={session:reducer(group,symbol,session,expiration,960) for session,group in groups.items()}
            elapsed.append(time.perf_counter()-start)
        timings[name]=round(median(elapsed),6);results[name]=values
    if results["scalar"]!=results["numpy"]:raise ValueError("historical_vectorization_mismatch")
    return results["scalar"],{"rows":len(rows),"scalar_seconds":timings["scalar"],
        "numpy_seconds":timings["numpy"],"exact_match":True,
        "digest":sha256(canonical(results["scalar"]).encode()).hexdigest()}


class ProbeTransport(MonitorThetaTransport):
    def request(self,method,**params):
        if params.get("symbol")!=SYMBOL:raise ValueError("probe_symbol_scope")
        if method=="option_list_contracts":
            if set(params)!={"symbol","date","request_type"} or params["request_type"]!="trade" or params["date"] not in tuple(map(date.fromisoformat,SESSIONS)):
                raise ValueError("probe_catalog_scope")
        elif method=="option_history_ohlc":
            fixed={"symbol","expiration","interval","strike","right","start_time","end_time"}
            if set(params)==fixed|{"date"}:dates=[params["date"]]
            elif set(params)==fixed|{"start_date","end_date"}:
                dates=[params["start_date"],params["end_date"]]
                if dates!=list(map(date.fromisoformat,SESSIONS)):raise ValueError("probe_date_scope")
            else:raise ValueError("probe_request_scope")
            if any(type(d) is not date or d.isoformat() not in SESSIONS for d in dates):raise ValueError("probe_date_scope")
            if type(params["expiration"]) is not date or params["expiration"]<max(dates):raise ValueError("probe_expiry_scope")
            if params["interval"]!="5m" or params["strike"]!="*" or params["right"]!="both" or params["start_time"]!="09:30:00" or params["end_time"]!="16:00:00":
                raise ValueError("probe_full_chain_scope")
        else:raise ValueError("probe_endpoint_scope")
        return self._request(method,**params)


class Probe:
    def __init__(self,root,output,*,factory=ProbeTransport,clock=lambda:datetime.now(timezone.utc)):
        self.root=root;self.output=output;self.factory=factory;self.clock=clock
        self.budget=Budget(MAX_REQUESTS,MAX_BYTES,MAX_SECONDS)
        self.transport=None;self.admissions=set()
        # Exclusive creation makes a second invocation fail before login/network.
        self.ledger=(output/"live-admissions.jsonl").open("x",encoding="utf-8")
        try:
            self.log({"kind":"plan","symbol":SYMBOL,"sessions":SESSIONS,"max_requests":MAX_REQUESTS,"max_bytes":MAX_BYTES,"max_seconds":MAX_SECONDS,
                "daily_controls":"nearest and farthest common expiry, once per date (at most four)"})
            # Persist both ledger creation and the newly created output name
            # before authentication or any data request can begin.
            for directory in (output,output.parent):
                fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
        except BaseException:
            self.ledger.close();raise
    def log(self,value):
        self.ledger.write(canonical(value)+"\n");self.ledger.flush();os.fsync(self.ledger.fileno())
    def close(self):
        if self.transport:self.transport.close()
        self.ledger.close()
    def request(self,method,**params):
        self.budget.check()
        if not backfill_window(self.clock()):raise RuntimeError("probe_reserved_collection_window")
        identity=canonical({"method":method,"params":{k:v.isoformat() if type(v) is date else v for k,v in params.items()}})
        if identity in self.admissions:raise ValueError("probe_repeat_forbidden")
        self.admissions.add(identity);self.log({"kind":"started","identity":identity,"at":self.clock().isoformat()})
        receipt={}
        try:
            if self.transport is None:self.transport=self.factory(self.root,self.budget,symbols=(SYMBOL,))
            rows,receipt=self.transport.request(method,**params)
            self.log({"kind":"finished","identity":identity,"receipt":receipt})
            return rows,receipt
        except BaseException as exc:
            if isinstance(exc,ProviderFailure):receipt=exc.receipt
            self.log({"kind":"failed","identity":identity,"error":type(exc).__name__,"receipt":receipt or {"response_bytes":MAX_BYTES}})
            raise
    def run(self):
        catalogs={};expiry_by_session={};seen={s:set() for s in SESSIONS}
        for session in SESSIONS:
            rows,_=self.request("option_list_contracts",symbol=SYMBOL,date=date.fromisoformat(session),request_type="trade")
            catalogs[session]={contract(row,SYMBOL) for row in rows}
            expiry_by_session[session]=expirations(rows,SYMBOL,session)
            if not catalogs[session]:raise ValueError("probe_empty_catalog")
        all_expiries=sorted(set().union(*map(set,expiry_by_session.values())))
        common=sorted(set.intersection(*map(set,expiry_by_session.values())))
        controls=set((common[0],common[-1])) if common else set()
        if len(all_expiries)+len(SESSIONS)+len(controls)*2>MAX_REQUESTS:raise ValueError("probe_expiry_count_exceeds_cap")
        summary=[];totals={s:[[m,0,0] for m in range(570,960,5)] for s in SESSIONS}
        for expiry in all_expiries:
            sessions=[s for s in SESSIONS if expiry in expiry_by_session[s]]
            dates={"start_date":date.fromisoformat(sessions[0]),"end_date":date.fromisoformat(sessions[-1])} if len(sessions)>1 else {"date":date.fromisoformat(sessions[0])}
            rows,receipt=self.request("option_history_ohlc",symbol=SYMBOL,expiration=date.fromisoformat(expiry),
                interval="5m",strike="*",right="both",start_time="09:30:00",end_time="16:00:00",**dates)
            aggregates,timing=compare_engines(rows,SYMBOL,sessions,expiry)
            groups=split_sessions(rows,sessions)
            for session,group in groups.items():
                seen[session].update(contract(row,SYMBOL) for row in group)
                for total,values in zip(totals[session],aggregates[session]):
                    total[1]+=values[1];total[2]+=values[2]
            daily_controls=[]
            if expiry in controls:
                for session in sessions:
                    daily_rows,daily_receipt=self.request("option_history_ohlc",symbol=SYMBOL,expiration=date.fromisoformat(expiry),
                        date=date.fromisoformat(session),interval="5m",strike="*",right="both",start_time="09:30:00",end_time="16:00:00")
                    daily_bins=aggregate_expiration(daily_rows,SYMBOL,session,expiry,960)
                    control={"session":session,"rows":len(daily_rows),"exact_match":daily_bins==aggregates[session],
                        "request_seconds":daily_receipt.get("elapsed_seconds"),"response_bytes":daily_receipt.get("response_bytes"),
                        "digest":sha256(canonical(daily_bins).encode()).hexdigest()}
                    self.log({"kind":"daily_control","expiry":expiry,**control})
                    if not control["exact_match"]:raise ValueError("probe_multiday_daily_mismatch")
                    daily_controls.append(control);del daily_rows
            summary.append({"expiry":expiry,"sessions":sessions,"daily_controls":daily_controls,"response_bytes":receipt.get("response_bytes"),"request_seconds":receipt.get("elapsed_seconds"),**timing})
            self.log({"kind":"comparison",**summary[-1]})
            print(canonical(summary[-1]),flush=True)
            del rows,groups
        coverage={s:{"catalog_contracts":len(catalogs[s]),"returned_contracts":len(seen[s]),"missing_contracts":len(catalogs[s]-seen[s]),
            "expected_expiries":expiry_by_session[s],"observed_expiries":sorted({x[0] for x in seen[s]})} for s in SESSIONS}
        result={"symbol":SYMBOL,"sessions":SESSIONS,"coverage":coverage,"requests":summary,"aggregates":totals,
            "complete_catalog_coverage":all(not (catalogs[s]-seen[s]) for s in SESSIONS),"exact_engine_match":True,**self.budget.snapshot()}
        (self.output/"live-result.json").write_text(json.dumps(result,indent=2)+"\n")
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",type=Path,required=True);parser.add_argument("--execute",action="store_true",required=True)
    args=parser.parse_args();root=args.root.resolve();output=root/OUTPUT
    ProbeTransport.preflight()
    # Preload NumPy outside measured sections; report aggregation conversion too.
    import numpy
    store=BaselineStore(root)
    with (store.path.parent/"baseline.lock").open("a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        output.mkdir(parents=True,exist_ok=True)
        probe=Probe(root,output)
        try:probe.run()
        except BaseException as exc:
            (output/"live-error.json").write_text(json.dumps({"error":type(exc).__name__,"detail":str(exc),**probe.budget.snapshot()},indent=2)+"\n")
            raise
        finally:probe.close()

if __name__=="__main__":main()
