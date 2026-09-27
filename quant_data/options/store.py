"""Registered optional options store; legacy four-store routes are not repurposed."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib, json, os, sqlite3, stat, zlib
from pathlib import Path
from quant_data.stores import StoreWriteLock
from .model import canonical_json
from .universe import TIER1_ETFS

def packed(value):
    return zlib.compress(canonical_json(value).encode(),9)

def unpacked(blob):
    return json.loads(zlib.decompress(blob))

class OptionsStore:
    def __init__(self,project_root):
        self.root=Path(project_root).resolve(strict=True)
        registry_path=self.root/"config/options_registry.json"
        self.registry=json.loads(registry_path.read_text())
        r=self.registry
        if r["contract"]!="quant_data.optional_options_store" or r["store_role"]!="options" or r["default_path"]!="data/options.sqlite":
            raise ValueError("options_registry_identity")
        if r["version"] not in ("1.0.0","1.1.0") or r["private"] is not True or r["collector"]["symbol"]!="SPY":
            raise ValueError("options_registry_contract")
        if r["journal_mode"]!="DELETE" or r["locking"]!="sha256_canonical_path_uri_v1":
            raise ValueError("options_store_coordination")
        self.path=(self.root/r["default_path"]).resolve()
        if not self.path.is_relative_to(self.root) or (self.root/r["default_path"]).is_symlink():
            raise ValueError("options_store_path")
        self.migrations=r.get("migrations",[r["migration"]])
        resources=("quant_data/migrations/options/0001_theta_compact.sql",
                   "quant_data/migrations/options/0002_tier1_etf_roots.sql")
        if not 1<=len(self.migrations)<=2 or self.migrations[0]!=r["migration"]:
            raise ValueError("options_migration_checksum_chain")
        self.migration_sql=[]
        for ordinal,migration in enumerate(self.migrations,1):
            if migration["ordinal"]!=ordinal or migration["resource"]!=resources[ordinal-1]:
                raise ValueError("options_migration_order")
            payload=(self.root/migration["resource"]).read_bytes()
            if hashlib.sha256(payload).hexdigest()!=migration["sha256"]:
                raise ValueError("options_migration_checksum")
            self.migration_sql.append(payload)
        self.sql=self.migration_sql[0]
        if self.path.exists() and self.path.stat().st_nlink!=1:
            raise ValueError("options_store_hardlink")
        for name in ("market","macro","company","news"):
            other=self.root/"data"/(name+".sqlite")
            if self.path.exists() and other.exists() and os.path.samefile(self.path,other):
                raise ValueError("options_store_alias")

    def _stamp(self):
        stamps=[]
        for suffix in ("","-wal","-shm","-journal"):
            p=Path(str(self.path)+suffix)
            if not p.exists(): stamps.append(None);continue
            s=p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:
                raise ValueError("options_store_file_identity")
            if suffix in ("-wal","-journal") and s.st_size:
                raise ValueError("options_store_not_quiet")
            stamps.append((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
        return stamps

    @contextmanager
    def read(self):
        before=self._stamp()
        if before[0] is None:raise ValueError("options_store_missing")
        fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        c=None
        try:
            s=os.fstat(fd)
            if (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)!=before[0]:
                raise ValueError("options_store_changed")
            c=sqlite3.connect(f"file:/proc/self/fd/{fd}?mode=ro&immutable=1",uri=True)
            c.row_factory=sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            self._check(c)
            yield c
            if self._stamp()!=before:raise ValueError("options_store_changed")
        finally:
            if c:c.close()
            os.close(fd)

    def _check(self,c,*,require_latest=False):
        anchor=c.execute("SELECT store_role,contract_version FROM store_metadata").fetchall()
        if [tuple(r) for r in anchor]!=[("options","1.0.0")]:raise ValueError("options_store_anchor")
        ledger=c.execute("SELECT ordinal,migration_id,resource,sha256 FROM schema_migrations ORDER BY ordinal").fetchall()
        expected_ledger=[(m["ordinal"],m["id"],m["resource"],m["sha256"]) for m in self.migrations]
        if not ledger or [tuple(r) for r in ledger]!=expected_ledger[:len(ledger)] or (require_latest and len(ledger)!=len(expected_ledger)):
            raise ValueError("options_migration_ledger")
        expected=sorted((d["id"],d["layer"],d["version"]) for d in self.registry["datasets"])
        found=sorted(tuple(r) for r in c.execute("SELECT id,layer,version FROM dataset_registry"))
        if found!=expected:raise ValueError("options_dataset_registry")

    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with StoreWriteLock(self.path):
            if self.path.exists():
                with self.read():pass
                return self._upgrade_locked()
            c=sqlite3.connect(self.path)
            try:
                c.execute("PRAGMA foreign_keys=ON")
                c.execute("PRAGMA journal_mode=DELETE")
                c.execute("PRAGMA synchronous=FULL")
                c.executescript("BEGIN IMMEDIATE;\n"+self.sql.decode())
                c.execute("INSERT INTO store_metadata VALUES ('options','1.0.0')")
                m=self.registry["migration"]
                c.execute("INSERT INTO schema_migrations VALUES (?,?,?,?,?)",
                    (m["ordinal"],m["id"],m["resource"],m["sha256"],datetime.now(timezone.utc).isoformat()))
                c.executemany("INSERT INTO dataset_registry VALUES (?,?,?)",
                    [(d["id"],d["layer"],d["version"]) for d in self.registry["datasets"]])
                self._check(c)
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:c.close()
            self._upgrade_locked()
        return True

    def _upgrade_locked(self):
        """Append migrations atomically; the caller owns the physical store lock."""
        with self.read() as source:
            applied=source.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        if applied==len(self.migrations):return False
        c=sqlite3.connect(self.path,timeout=5)
        try:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("PRAGMA synchronous=FULL")
            for index in range(applied,len(self.migrations)):
                m=self.migrations[index]
                c.executescript("BEGIN IMMEDIATE;\n"+self.migration_sql[index].decode())
                c.execute("INSERT INTO schema_migrations VALUES (?,?,?,?,?)",
                    (m["ordinal"],m["id"],m["resource"],m["sha256"],datetime.now(timezone.utc).isoformat()))
                self._check(c)
                if c.execute("PRAGMA foreign_key_check").fetchone():raise ValueError("migration_foreign_keys")
                c.commit()
            c.execute("PRAGMA foreign_keys=ON")
            self._check(c,require_latest=True)
        except BaseException:
            c.rollback();raise
        finally:c.close()
        return True

    def publish(self,capture,*,missing_only=False):
        """One atomic publication; A->B->A is a new revision, immediate replay is no-write."""
        if len(capture["details"])>300 or len(capture["summaries"])!=102:
            raise ValueError("capture_shape")
        details=capture["details"]; summaries=capture["summaries"]; leaders=capture["leaders"]
        symbol=capture["symbol"]
        if symbol not in TIER1_ETFS:raise ValueError("unsupported_option_root")
        for detail in details:
            if detail["contract_id"]!="|".join((symbol,detail["expiration"],detail["strike"],detail["right"])):
                raise ValueError("cross_symbol_contract")
        if any(not leader["contract_id"].startswith(symbol+"|") for leader in leaders):
            raise ValueError("cross_symbol_leader")
        if len(leaders)>40:raise ValueError("leader_cap")
        metadata={k:v for k,v in capture.items() if k not in ("details","summaries","leaders","receipts")}
        with StoreWriteLock(self.path):
            with self.read() as c:
                prior=c.execute("SELECT c.capture_id,c.semantic_sha256 FROM option_current p JOIN option_captures c USING(capture_id) WHERE p.symbol=? AND p.session_date=?",
                    (capture["symbol"],capture["session"])).fetchone()
                if prior and missing_only:
                    return {"capture_id":prior["capture_id"],"outcome":"existing","canonical_changes":0}
                if prior and prior["semantic_sha256"]==capture["semantic_sha256"]:
                    return {"capture_id":prior["capture_id"],"outcome":"replay","canonical_changes":0}
            c=sqlite3.connect(self.path,timeout=5)
            try:
                c.execute("PRAGMA foreign_keys=ON");c.execute("PRAGMA synchronous=FULL")
                c.execute("BEGIN IMMEDIATE");self._check(c,require_latest=True)
                cursor=c.execute("INSERT INTO option_captures(symbol,session_date,predecessor_id,semantic_sha256,captured_at,underlying_price,metadata) VALUES (?,?,?,?,?,?,?)",
                    (capture["symbol"],capture["session"],prior["capture_id"] if prior else None,
                     capture["semantic_sha256"],capture["captured_at"],capture["spot"],packed(metadata)))
                cid=cursor.lastrowid
                for row in details:
                    c.execute("INSERT OR IGNORE INTO option_contracts VALUES (?,?,?,?,?,?)",
                        (row["contract_id"],capture["symbol"],row["expiration"],row["strike"],row["right"],row["deliverable_state"]))
                    c.execute("INSERT INTO option_details VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid,row["contract_id"],row["volume"],row["oi"],row["bid"],row["ask"],row["implied_vol"],
                         canonical_json(row["reasons"]),packed(row)))
                for row in summaries:
                    c.execute("INSERT INTO option_daily_summaries VALUES (?,?,?,?,?,?)",
                        (cid,row["population"],row["dimension"],row["bucket"],row["right"],packed(row["stats"])))
                for row in leaders:
                    c.execute("INSERT INTO option_activity_leaders VALUES (?,?,?,?,?,?,?)",
                        (cid,row["population"],row["metric"],row["right"],row["rank"],row["contract_id"],packed(row)))
                for n,row in enumerate(capture["receipts"]):
                    c.execute("INSERT INTO option_source_receipts VALUES (?,?,?,?,?,?)",
                        (cid,n,row["method"],row["response_sha256"],row["response_bytes"],packed(row)))
                c.execute("INSERT INTO option_current VALUES (?,?,?) ON CONFLICT(symbol,session_date) DO UPDATE SET capture_id=excluded.capture_id",
                    (capture["symbol"],capture["session"],cid))
                if c.execute("SELECT COUNT(*) FROM option_details WHERE capture_id=?",(cid,)).fetchone()[0]!=len(details):
                    raise ValueError("publication_count")
                # Every inserted relationship is checked immediately by SQLite.
                # Full-store foreign_key_check remains at migration/health/backup.
                if c.execute("PRAGMA foreign_keys").fetchone()[0]!=1:raise ValueError("foreign_keys_disabled")
                c.commit()
            except BaseException:
                c.rollback();raise
            finally:c.close()
            # Source buffers may be dropped only after a separate committed read.
            with self.read() as c:
                row=c.execute("SELECT semantic_sha256 FROM option_captures WHERE capture_id=?",(cid,)).fetchone()
                if not row or row[0]!=capture["semantic_sha256"]:raise ValueError("publication_readback")
        return {"capture_id":cid,"outcome":"published","canonical_changes":1}

    def status(self):
        with self.read() as c:
            result=dict(c.execute("SELECT COUNT(*) sessions,MIN(session_date) first_session,MAX(session_date) last_session FROM option_current").fetchone())
            for table,label in (("option_details","selected_rows"),("option_daily_summaries","summary_rows"),
                                ("option_contracts","contracts"),("option_source_receipts","source_receipts")):
                result[label]=c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            result["integrity"]=c.execute("PRAGMA quick_check").fetchone()[0]
            result["foreign_key_violations"]=len(c.execute("PRAGMA foreign_key_check").fetchall())
            modes={"greeks_sessions":0,"prices_only_sessions":0}
            for row in c.execute("SELECT c.metadata FROM option_current p JOIN option_captures c USING(capture_id)"):
                metadata=unpacked(row[0])
                modes["greeks_sessions" if metadata["coverage"]["greeks_contracts"] else "prices_only_sessions"]+=1
            result.update(modes)
        result["database_bytes"]=self.path.stat().st_size
        result["source_replayable"]=False
        return result

    def completed_sessions(self,symbol="SPY"):
        if not self.path.exists():return set()
        with self.read() as c:return {row[0] for row in c.execute("SELECT session_date FROM option_current WHERE symbol=?",(symbol,))}

    def backup(self,target):
        target=Path(target).resolve()
        if target.exists() or target==self.path:raise ValueError("backup_target_exists")
        target.parent.mkdir(parents=True,exist_ok=True)
        with StoreWriteLock(self.path):
            with self.read() as source:
                dest=sqlite3.connect(target)
                try:
                    source.backup(dest)
                    self._check(dest)
                    if dest.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("backup_integrity")
                    if dest.execute("PRAGMA foreign_key_check").fetchone():raise ValueError("backup_foreign_keys")
                finally:dest.close()
        return {"bytes":target.stat().st_size,"integrity":"ok"}

    def restore_from_backup(self,source):
        """Restore a checked options backup into an absent host-owned options path."""
        import copy
        source=Path(source).resolve(strict=True)
        if self.path.exists():raise ValueError("restore_target_exists")
        checked=copy.copy(self);checked.path=source
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with StoreWriteLock(self.path):
            if self.path.exists():raise ValueError("restore_target_exists")
            with checked.read() as origin:
                target=sqlite3.connect(self.path)
                try:
                    origin.backup(target);self._check(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("restore_integrity")
                    if target.execute("PRAGMA foreign_key_check").fetchone():raise ValueError("restore_foreign_keys")
                finally:target.close()
        return self.status()
