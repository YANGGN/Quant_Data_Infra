"""FMP-only production classifications from already retained company profiles.

All entry points require explicit internal store capabilities. No provider,
credential, path discovery, CSV fallback or inferred historical effective date.
"""
from __future__ import annotations
import hashlib
import re
from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import acquire_write_session, quiet_immutable_read_connection, stable_id
from .classification_registry import DATASET
from .collection_mappings import identity_source_object
from .collection_universe import _utc

VERSION = "fmp_classifications.v1"
MAX_MEMBERS = 2248
UNIVERSE = "major_index_liquid"
WARNINGS = ("fmp_classification_not_official_gics", "historical_effective_date_not_established",
            "availability_is_local_publication")
IDENTITY = ("source_symbol", "provider_symbol", "instrument_id", "cik", "membership_snapshot_id", "mapping_id")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else dumps_strict(value).encode()).hexdigest()


def _label(value):
    if (not isinstance(value,str) or not 1 <= len(value) <= 200 or value != value.strip()
        or any(ord(ch)<32 for ch in value)):
        raise ValidationError("FMP sector and industry must be nonempty bounded source labels")
    return value


def _inputs(stores, *, cutoff, expected_count):
    """Caller holds both physical locks; return a complete, validated source snapshot."""
    if type(expected_count) is not int or not 1 <= expected_count <= MAX_MEMBERS:
        raise ResourceLimitError("Classification population must pin a count of 1 through 2248")
    with quiet_immutable_read_connection(stores,"market") as c:
        head=c.execute("""SELECT s.* FROM market_collection_heads h JOIN market_collection_snapshots s
            ON s.snapshot_id=h.snapshot_id WHERE h.universe_id=?""",(UNIVERSE,)).fetchone()
        if head is None or head["captured_at"] > cutoff or head["member_count"] != expected_count:
            raise ConflictError("Classification population differs from the selected universe")
        members=c.execute("""SELECT m.*, s.captured_at AS mapping_captured_at,
            i.provider AS instrument_provider, i.provider_symbol AS instrument_symbol, i.asset_type
            FROM market_collection_mapping_heads h
            JOIN market_collection_mapping_snapshots s ON s.mapping_id=h.mapping_id
            JOIN market_collection_provider_mappings m ON m.mapping_id=h.mapping_id
            LEFT JOIN stage10_instruments i ON i.instrument_id=m.instrument_id
            WHERE h.membership_snapshot_id=? AND h.provider='fmp' ORDER BY m.source_symbol""",
            (head["snapshot_id"],)).fetchall()
        if len(members)!=expected_count or len({r["instrument_id"] for r in members})!=expected_count:
            raise ConflictError("Classification identities are incomplete or repeated")
    output=[]
    with quiet_immutable_read_connection(stores,"company") as c, quiet_immutable_read_connection(stores,"market") as market:
        for member in members:
            if (member["status"]!="resolved" or member["mapping_captured_at"]>cutoff
                or member["instrument_provider"]!="fmp" or member["asset_type"]!="equity"
                or member["instrument_symbol"]!=member["provider_symbol"]):
                raise ConflictError("Classification requires an available resolved FMP equity")
            row=c.execute("""SELECT d.*, e.raw_body, e.content_sha256, e.source_pointer,
                e.provider_symbol AS provider_symbol, e.captured_at AS source_captured_at
                FROM company_short_description_versions d JOIN company_profile_evidence e ON e.evidence_id=d.evidence_id
                WHERE d.instrument_id=? AND d.available_at<=? ORDER BY d.version_sequence DESC LIMIT 1""",
                (member["instrument_id"],cutoff)).fetchone()
            if row is None or any(row[k]!=member[k] for k in IDENTITY if k not in ("mapping_id","cik")):
                raise ConflictError("Saved profile does not match the current selected identity")
            original=market.execute("""SELECT m.*,s.provider FROM market_collection_provider_mappings m
                JOIN market_collection_mapping_snapshots s ON s.mapping_id=m.mapping_id
                WHERE m.mapping_id=? AND m.source_symbol=?""",(row["mapping_id"],row["source_symbol"])).fetchone()
            if (original is None or original["provider"]!="fmp" or original["status"]!="resolved"
                or any(original[k]!=row[k] for k in IDENTITY)):
                raise ConflictError("Saved profile no longer matches its immutable source mapping")
            body=bytes(row["raw_body"])
            if digest(body)!=row["content_sha256"] or _utc(row["source_captured_at"])>cutoff:
                raise ConflictError("Saved profile bytes or source cutoff differ")
            p=identity_source_object(loads_strict(body,max_bytes=1048576),row["source_pointer"],"fmp")
            if p.get("symbol")!=member["provider_symbol"] or row["provider_symbol"]!=member["provider_symbol"]:
                raise ConflictError("Saved profile symbol differs")
            cik=p.get("cik")
            if cik is not None and str(cik).strip() not in ("","0"):
                if not str(cik).isdigit() or len(str(cik))>10 or (row["cik"] and str(cik).zfill(10)!=row["cik"]):
                    raise ConflictError("Saved profile CIK differs")
            value={k:member[k] for k in IDENTITY}
            value.update(classification_provider="fmp",sector=_label(p.get("sector")),industry=_label(p.get("industry")))
            value["content_identity"]=digest({k:v for k,v in value.items() if k not in ("mapping_id","membership_snapshot_id","cik")})
            value.update(source_cik=row["cik"],source_mapping_id=row["mapping_id"],source_description_version_id=row["version_id"],source_evidence_id=row["evidence_id"],
                source_content_sha256=row["content_sha256"],source_captured_at=_utc(row["source_captured_at"]),
                source_pointer=row["source_pointer"],source_byte_count=len(body))
            output.append(value)
    return output


def prepare_classifications(stores, *, as_of, expected_count=MAX_MEMBERS):
    cutoff=_utc(as_of)
    with acquire_write_session(stores,("market","company"),timeout_seconds=5):
        return _inputs(stores,cutoff=cutoff,expected_count=expected_count)


class ClassificationPublisher:
    def __init__(self,stores,registry):
        if DATASET not in {d.id for d in registry.datasets_for("market")}:
            raise ValidationError("Classification dataset is not registered")
        self.stores,self.registry=stores,registry
        self.coordinator=IngestionCoordinator(stores,code_version=VERSION)

    def publish(self, *, published_at, expected_count=MAX_MEMBERS, expected_source_sha256=None):
        published=_utc(published_at)
        with acquire_write_session(self.stores,("market","company"),timeout_seconds=5) as locks:
            rows=_inputs(self.stores,cutoff=published,expected_count=expected_count)
            if expected_source_sha256 is not None and digest(rows)!=expected_source_sha256:
                raise ConflictError("Classification source snapshot changed since preflight")
            semantic=digest([r["content_identity"] for r in rows])
            with quiet_immutable_read_connection(self.stores,"market") as c:
                ledger=[tuple(r) for r in c.execute("SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal")]
                if ledger!=[(m.id,m.sha256) for m in sorted(self.registry.migrations_for("market"),key=lambda m:m.ordinal)]:
                    raise ConflictError("Classification migration ledger differs")
                changes=[]
                for row in rows:
                    old=c.execute("SELECT * FROM market_instrument_classifications WHERE instrument_id=?",(row["instrument_id"],)).fetchone()
                    if old and old["content_identity"]==row["content_identity"]: continue
                    if old and old["available_at"]>=published:
                        raise ConflictError("Classification revision cannot be backdated or tied")
                    changes.append((row,dict(old) if old else None))
            if not changes:
                return IngestionReceipt(outcome="unchanged",store="market",dataset_id=DATASET,
                    semantic_identity=semantic,run_id=None,artifact_id=None,snapshot_id=None,written_count=0,warnings=WARNINGS)
            transition=digest([(r["content_identity"],old["version_id"] if old else None) for r,old in changes])
            scope={"universe_id":UNIVERSE,"membership_snapshot_id":rows[0]["membership_snapshot_id"],
                "classification_provider":"fmp","selected_count":len(rows),"changed_count":len(changes),
                "source_snapshot_sha256":digest(rows),"normalization_version":VERSION}
            def writer(c,run_id):
                artifacts=[]
                for row,old in changes:
                    version_id=stable_id("fmp_classification",row["content_identity"],old["version_id"] if old else "")
                    c.execute("""INSERT INTO market_classification_versions VALUES
                        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (version_id,row["content_identity"],row["instrument_id"],row["source_symbol"],row["provider_symbol"],
                         "fmp",row["sector"],row["industry"],row["membership_snapshot_id"],row["mapping_id"],
                         row["source_cik"],row["source_mapping_id"],row["source_description_version_id"],row["source_evidence_id"],row["source_content_sha256"],
                         row["source_captured_at"],published,1 if old is None else old["version_sequence"]+1,
                         old["version_id"] if old else None,run_id))
                    artifacts.append(ArtifactWrite(stable_id("classification_artifact",transition,version_id),DATASET,
                        row["source_content_sha256"],"application/json",row["source_byte_count"],
                        "company.profile_evidence/"+row["source_evidence_id"],
                        {"source_store":"company","source_evidence_id":row["source_evidence_id"],
                         "source_pointer":row["source_pointer"]},row["source_captured_at"],"datetime",VERSION))
                snapshot=SnapshotWrite(stable_id("classification_snapshot",transition),DATASET,transition,scope,
                    "complete" if len(changes)==len(rows) else "partial",len(changes),published,"datetime","validated",
                    tuple(a.artifact_id for a in artifacts),WARNINGS)
                return WriteResult(len(changes),tuple(artifacts),snapshot,(),warnings=WARNINGS)
            return self.coordinator.execute(role="market",dataset_id=DATASET,output_dataset_ids=(DATASET,),
                semantic_identity=transition,run_id=stable_id("classification_run",transition),
                command="market.classifications.from_saved_fmp",scope=scope,started_at=published,completed_at=published,
                fetched_count=0,writer=writer,held_locks=locks)


def read_classifications(stores, symbols, *, as_of):
    if (not isinstance(symbols,(list,tuple)) or not 1<=len(symbols)<=MAX_MEMBERS
        or any(not isinstance(s,str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\^-]{0,31}",s) for s in symbols)
        or len(set(symbols))!=len(symbols)):
        raise ValidationError("Classification symbols must be a bounded unique symbol list")
    cutoff=_utc(as_of)
    with acquire_write_session(stores,("market",),timeout_seconds=2):
        with quiet_immutable_read_connection(stores,"market") as c:
            result=[]
            for symbol in symbols:
                row=c.execute("""SELECT * FROM market_classification_versions WHERE source_symbol=? AND available_at<=?
                    ORDER BY available_at DESC,version_sequence DESC LIMIT 1""",(symbol,cutoff)).fetchone()
                result.append({**dict(row),"warnings":list(WARNINGS)} if row else
                    {"source_symbol":symbol,"classification_provider":"fmp","sector":None,"industry":None,
                     "missing_reason":"not_established","warnings":list(WARNINGS)})
            return result
