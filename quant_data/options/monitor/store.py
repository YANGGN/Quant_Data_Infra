"""Private derived monitor state; compact aggregates and sanitized receipts only."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from hashlib import sha256
from pathlib import Path
from quant_data.stores import StoreWriteLock
import json,os,sqlite3,stat
from .model import canonical,baseline_eligible
from .policy import NY

SCHEMA="""
CREATE TABLE metadata(version INTEGER NOT NULL, schema_sha256 TEXT NOT NULL);
CREATE TABLE attempts(session TEXT NOT NULL, symbol TEXT NOT NULL, slot TEXT NOT NULL,
    method TEXT NOT NULL, started_at TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'started',
    PRIMARY KEY(session,symbol,slot,method));
CREATE INDEX attempts_fair ON attempts(session,symbol,started_at);
CREATE TABLE observations(session TEXT NOT NULL,symbol TEXT NOT NULL,slot TEXT NOT NULL,
    captured_at TEXT NOT NULL,cadence INTEGER NOT NULL,fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL,PRIMARY KEY(session,symbol,slot));
CREATE INDEX observations_symbol ON observations(symbol,session,slot);
CREATE TABLE receipts(session TEXT NOT NULL,symbol TEXT NOT NULL,slot TEXT NOT NULL,
    method TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(session,symbol,slot,method));
CREATE TABLE alerts(id TEXT PRIMARY KEY,session TEXT NOT NULL,symbol TEXT NOT NULL,
    detected_at TEXT NOT NULL,payload TEXT NOT NULL,published INTEGER NOT NULL DEFAULT 0);
CREATE TABLE outbox(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,
    attempted_at TEXT,published INTEGER NOT NULL DEFAULT 0);
CREATE TABLE calendar(session TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE calendar_years(year INTEGER PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE push_attempts(alert_id TEXT NOT NULL,subscription_id TEXT NOT NULL,attempted_at TEXT NOT NULL,outcome TEXT NOT NULL DEFAULT 'started',PRIMARY KEY(alert_id,subscription_id));
"""
SCHEMA_SHA=sha256(SCHEMA.encode()).hexdigest()

class MonitorStore:
    def __init__(self,root:Path):
        self.root=Path(root).resolve(strict=True)
        parent=self.root/"data/.operations/options-monitor"
        if parent.resolve().is_relative_to(self.root) is False:
            raise ValueError("monitor_store_parent")
        self.path=parent/"monitor.sqlite"
        if self.path.exists() and (self.path.is_symlink() or self.path.stat().st_nlink!=1):
            raise ValueError("monitor_store_identity")
        for forbidden in ("data/market.sqlite","data/options.sqlite"):
            target=self.root/forbidden
            if target.exists() and self.path.exists() and os.path.samefile(target,self.path):
                raise ValueError("monitor_store_alias")
    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with StoreWriteLock(self.path):
            if self.path.exists():
                with self.read():pass
                return False
            c=sqlite3.connect(self.path)
            try:
                c.execute("PRAGMA journal_mode=DELETE");c.execute("PRAGMA synchronous=FULL")
                c.executescript(SCHEMA)
                c.execute("INSERT INTO metadata VALUES (?,?)",(1,SCHEMA_SHA));c.commit()
            finally:c.close()
            os.chmod(self.path,0o600)
        return True
    def _stamp(self):
        states=[]
        for suffix in ("","-wal","-shm","-journal"):
            p=Path(str(self.path)+suffix)
            if not p.exists():states.append(None);continue
            s=p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or (suffix in ("-wal","-journal") and s.st_size):
                raise ValueError("monitor_store_not_quiet")
            states.append((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
        return states
    @contextmanager
    def read(self):
        before=self._stamp()
        if before[0] is None:raise ValueError("monitor_store_missing")
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        c=None
        try:
            s=os.fstat(fd)
            if (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)!=before[0]:
                raise ValueError("monitor_store_changed")
            c=sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",uri=True)
            c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
            if [tuple(x) for x in c.execute("SELECT * FROM metadata")]!=[(1,SCHEMA_SHA)]:
                raise ValueError("monitor_schema_checksum")
            yield c
            if self._stamp()!=before:raise ValueError("monitor_store_changed")
        finally:
            if c is not None:c.close()
            os.close(fd)
    def write(self,statement,params=()):
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path,timeout=15)
            try:
                c.execute("BEGIN IMMEDIATE")
                cursor=c.execute(statement,params)
                changed=cursor.rowcount;c.commit();return changed
            except BaseException:c.rollback();raise
            finally:c.close()
    def attempted(self,session,symbol,slot,method):
        with self.read() as c:
            return c.execute("SELECT 1 FROM attempts WHERE session=? AND symbol=? AND slot=? AND method=?",
                (session,symbol,slot,method)).fetchone() is not None
    def reserve(self,session,symbol,slot,method,started_at):
        return self.write("INSERT OR IGNORE INTO attempts(session,symbol,slot,method,started_at) VALUES (?,?,?,?,?)",
            (session,symbol,slot,method,started_at))==1
    def finish(self,session,symbol,slot,method,outcome,receipt):
        safe={k:receipt.get(k) for k in ("method","captured_at","outcome","rows","response_bytes","response_sha256","messages","elapsed_seconds")}
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path,timeout=15)
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("UPDATE attempts SET outcome=? WHERE session=? AND symbol=? AND slot=? AND method=?",
                    (outcome,session,symbol,slot,method))
                c.execute("INSERT OR IGNORE INTO receipts VALUES (?,?,?,?,?)",
                    (session,symbol,slot,method,canonical(safe)))
                c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
    def calendar(self,session):
        with self.read() as c:
            row=c.execute("SELECT payload FROM calendar WHERE session=?",(session,)).fetchone()
            return json.loads(row[0]) if row else None
    def save_calendar(self,session,rows):
        self.write("INSERT OR IGNORE INTO calendar VALUES (?,?)",(session,canonical(rows)))
    def calendar_year(self,year):
        with self.read() as c:
            row=c.execute("SELECT payload FROM calendar_years WHERE year=?",(year,)).fetchone()
            return json.loads(row[0]) if row else None
    def save_calendar_year(self,year,rows):
        self.write("INSERT OR IGNORE INTO calendar_years VALUES (?,?)",(year,canonical(rows)))
    def reserve_push(self,alert_id,subscription_id,when):
        return self.write("INSERT OR IGNORE INTO push_attempts(alert_id,subscription_id,attempted_at) VALUES (?,?,?)",
            (alert_id,subscription_id,when))==1
    def finish_push(self,alert_id,subscription_id,outcome):
        self.write("UPDATE push_attempts SET outcome=? WHERE alert_id=? AND subscription_id=?",
            (outcome,alert_id,subscription_id))
    def top_volume_symbols(self,prior_session,limit=100):
        if prior_session is None:return set()
        with self.read() as c:
            rows=c.execute("""
                SELECT symbol FROM (
                    SELECT symbol,payload,ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY slot DESC) AS rank
                    FROM observations WHERE session=?
                ) WHERE rank=1 AND json_extract(payload,'$.call_volume') IS NOT NULL
                  AND json_extract(payload,'$.put_volume') IS NOT NULL
                ORDER BY json_extract(payload,'$.call_volume')+json_extract(payload,'$.put_volume') DESC,symbol
                LIMIT ?""",(prior_session,limit)).fetchall()
            return {row[0] for row in rows}
    def last_attempts(self,session):
        with self.read() as c:
            return {r[0]:r[1] for r in c.execute(
                "SELECT symbol,MAX(started_at) FROM attempts WHERE session=? GROUP BY symbol",(session,))}
    def prior(self,symbol,session):
        with self.read() as c:
            row=c.execute("SELECT payload,fingerprint,slot FROM observations WHERE symbol=? AND session=? ORDER BY slot DESC LIMIT 1",
                          (symbol,session)).fetchone()
            return self._unpack(row) if row else None
    def first(self,symbol,session):
        with self.read() as c:
            row=c.execute("SELECT payload,fingerprint,slot FROM observations WHERE symbol=? AND session=? AND json_extract(payload,'$.atm_iv_30') IS NOT NULL ORDER BY slot LIMIT 1",
                          (symbol,session)).fetchone()
            return self._unpack(row) if row else None
    def _unpack(self,row):
        value=json.loads(row[0]);value["_fingerprint"]=row[1];value["_slot"]=row[2];return value
    def baselines(self,symbol,cadence,minute,session):
        with self.read() as c:
            rows=c.execute("SELECT session,payload,slot FROM observations WHERE symbol=? AND cadence=? AND session<? ORDER BY session DESC,slot DESC LIMIT 3000",
                           (symbol,cadence,session)).fetchall()
        found={}
        for row in rows:
            item=json.loads(row[1])
            stamp=datetime.fromisoformat(row[2]).astimezone(NY)
            if stamp.hour*60+stamp.minute==minute and row[0] not in found and baseline_eligible(item,cadence):
                found[row[0]]=item
            if len(found)>=60:break
        return list(found.values())
    def last_alert(self,symbol):
        with self.read() as c:
            row=c.execute("SELECT detected_at FROM alerts WHERE symbol=? ORDER BY detected_at DESC LIMIT 1",(symbol,)).fetchone()
            return row[0] if row else None
    def publish(self,observation,alert=None):
        session=observation["session"];symbol=observation["symbol"];slot=observation.get("_slot",observation["captured_at"])
        payload={k:v for k,v in observation.items() if not k.startswith("_")}
        serialized=canonical(payload);digest=sha256(serialized.encode()).hexdigest()
        with StoreWriteLock(self.path):
            c=sqlite3.connect(self.path)
            try:
                c.execute("BEGIN IMMEDIATE")
                old=c.execute("SELECT payload FROM observations WHERE session=? AND symbol=? AND slot=?",(session,symbol,slot)).fetchone()
                if old:
                    if old[0]!=serialized:raise ValueError("monitor_slot_conflict")
                    c.rollback();return False
                c.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                    (session,symbol,slot,observation["captured_at"],observation["cadence_minutes"],observation["_fingerprint"],serialized))
                c.execute("INSERT INTO outbox VALUES (?,?,?,?,0)",
                    (digest,"observation",serialized,None))
                if alert:
                    a=canonical(alert)
                    c.execute("INSERT INTO alerts(id,session,symbol,detected_at,payload) VALUES (?,?,?,?,?)",
                        (alert["id"],session,symbol,alert["detected_at"],a))
                    c.execute("INSERT INTO outbox VALUES (?,?,?,?,0)",(alert["id"],"alert",a,None))
                c.commit();return True
            except BaseException:c.rollback();raise
            finally:c.close()
    def recent_alerts(self,since):
        with self.read() as c:
            return [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM alerts WHERE detected_at>=? ORDER BY detected_at,id LIMIT 100",(since,))]
    def pending(self,kind,before,limit=100):
        with self.read() as c:
            return [(r[0],json.loads(r[1])) for r in c.execute(
                "SELECT id,payload FROM outbox WHERE kind=? AND published=0 AND (attempted_at IS NULL OR attempted_at<?) ORDER BY rowid LIMIT ?",
                (kind,before,limit))]
    def _outbox_update(self,statement,values):
        if not values:return
        with StoreWriteLock(self.path,timeout_seconds=15):
            c=sqlite3.connect(self.path,timeout=15)
            try:
                c.execute("BEGIN IMMEDIATE")
                c.executemany(statement,values)
                c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
    def mark_attempt(self,ids,when):
        self._outbox_update("UPDATE outbox SET attempted_at=? WHERE id=?",[(when,x) for x in ids])
    def mark_published(self,ids):
        self._outbox_update("DELETE FROM outbox WHERE id=?",[(x,) for x in ids])
    def prune(self,current_session,keep=90):
        # Intraday observations support charts/baselines; request accounting is
        # needed for the current session only. Keep seven sessions for diagnosis.
        sessions="SELECT DISTINCT session FROM attempts UNION SELECT DISTINCT session FROM observations"
        with StoreWriteLock(self.path,timeout_seconds=15):
            with self.read() as reader:
                retained=[r[0] for r in reader.execute("SELECT session FROM ("+sessions+") WHERE session<=? ORDER BY session DESC LIMIT ?",(current_session,max(keep,7)))]
            c=sqlite3.connect(self.path,timeout=15)
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM outbox WHERE published=1")
                if len(retained)>=7:
                    for table in ("attempts","receipts"):
                        c.execute(f"DELETE FROM {table} WHERE session<?",(retained[6],))
                if len(retained)>=keep:
                    threshold=retained[keep-1]
                    for table in ("observations","alerts","calendar"):
                        c.execute(f"DELETE FROM {table} WHERE session<?",(threshold,))
                    c.execute("DELETE FROM outbox WHERE kind='observation' AND json_extract(payload,'$.session')<?",(threshold,))
                    c.execute("DELETE FROM outbox WHERE kind='alert' AND id NOT IN (SELECT id FROM alerts)")
                    c.execute("DELETE FROM push_attempts WHERE alert_id NOT IN (SELECT id FROM alerts)")
                c.commit()
            except BaseException:c.rollback();raise
            finally:c.close()
