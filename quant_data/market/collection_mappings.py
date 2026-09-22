"""Evidence-backed provider assertions pinned to one complete collection snapshot."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import re
from pathlib import PurePosixPath
from .collection_universe import _digest, _utc, _text, _SYMBOL, EVIDENCE, MAX_BYTES, MAX_MEMBERS
from ..contracts import IngestionReceipt
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id

DATASET="market.collection.provider_mappings"
VERSION="collection_mappings.v1"
PROVIDERS=frozenset({"fmp","sec","sharadar","equibles","alpaca"})
FIELDS=frozenset({"source_symbol","status","provider_symbol","provider_subject","instrument_id","cik",
    "evidence_sha256","evidence_reference","evidence_pointer","symbol_field","subject_field","cik_field","reason","association_sha256","association_pointer"})

@dataclass(frozen=True)
class IdentityEvidence:
    body: bytes
    source_reference: str
    captured_at: str
    provider: str = "fmp"
    @property
    def sha256(self): return hashlib.sha256(self.body).hexdigest()
    @property
    def evidence_id(self):
        return stable_id("collection_identity_evidence",_digest({"provider":self.provider,"raw":self.sha256,
            "reference":self.source_reference,"captured_at":_utc(self.captured_at)}))

@dataclass(frozen=True)
class MappingBatch:
    membership_snapshot_id: str
    provider: str
    captured_at: str
    source_reference: str
    body: bytes
    evidence: tuple[IdentityEvidence,...]
    rows_json: str
    state_sha256: str
    @property
    def acquisition_identity(self):
        return _digest({"snapshot":self.membership_snapshot_id,"provider":self.provider,
            "captured_at":self.captured_at,"raw":hashlib.sha256(self.body).hexdigest()})

def _reference(value):
    _text(value,"evidence reference",1024)
    if PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or "\\" in value or ":" in value:
        raise ValidationError("Identity evidence requires a private relative reference")
    return value

def _pointer(value,pointer):
    if not isinstance(pointer,str) or (pointer and not pointer.startswith("/")):
        raise ValidationError("Identity evidence pointer is invalid")
    try:
        for part in pointer.split("/")[1:] if pointer else ():
            key=part.replace("~1","/").replace("~0","~")
            if isinstance(value,list):
                if not key.isdigit() or str(int(key))!=key: raise ValueError()
                value=value[int(key)]
            elif isinstance(value,dict): value=value[key]
            else: raise ValueError()
    except (KeyError,IndexError,ValueError,TypeError) as exc:
        raise ValidationError("Identity evidence pointer does not resolve") from exc
    return value

def identity_source_object(source,pointer,provider):
    """Read an original object or a Nasdaq TICKERS column/array source row."""
    node=_pointer(source,pointer)
    if isinstance(node,dict):
        if provider=="sharadar" and pointer.startswith("/data/"):
            from ..company.sharadar_direct import ticker_rows
            rows=ticker_rows(dumps_strict(source).encode())
            if not re.fullmatch(r"/data/(0|[1-9][0-9]*)",pointer) or rows[int(pointer.split("/")[-1])]!=node:
                raise ValidationError("Direct identity pointer differs")
        return node
    if provider!="sharadar" or not isinstance(node,list):
        raise ValidationError("Identity evidence must select a source object")
    if not re.fullmatch(r"/datatable/data/(0|[1-9][0-9]*)",pointer):
        raise ValidationError("Sharadar identity must select an original table data row")
    table=source.get("datatable") if isinstance(source,dict) else None
    columns=table.get("columns") if isinstance(table,dict) else None
    if not isinstance(columns,list) or not 1<=len(columns)<=1024 or len(columns)!=len(node):
        raise ValidationError("Sharadar identity column envelope is invalid")
    if any(not isinstance(c,dict) or set(c)!={"name","type"} or not isinstance(c["name"],str)
        or not isinstance(c["type"],str) for c in columns):
        raise ValidationError("Sharadar identity columns are invalid")
    names=[c["name"] for c in columns]
    if len(set(names))!=len(names):
        raise ValidationError("Sharadar identity columns repeat")
    result=dict(zip(names,node))
    if result.get("table")!="SF1" or not {"ticker","permaticker"}<=set(result):
        raise ValidationError("Sharadar mapping requires SF1 security-reference rows")
    return result


def _cik(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)):
        raise ValidationError("CIK evidence is invalid")
    text=str(value)
    if not 1<=len(text)<=10 or not text.isascii() or not text.isdigit() or not 1<=int(text)<=9999999999:
        raise ValidationError("CIK evidence is invalid")
    return text.zfill(10)

def unresolved_mapping(symbol,reason="No evidenced provider mapping"):
    return {field:(symbol if field=="source_symbol" else "unresolved" if field=="status" else reason if field=="reason" else None)
        for field in FIELDS}

def prepare_mapping(*,membership_snapshot_id,provider,captured_at,source_reference,body,evidence=()):
    _text(membership_snapshot_id,"membership snapshot",256)
    if provider not in PROVIDERS: raise ValidationError("Unknown collection mapping provider")
    captured_at=_utc(captured_at); _reference(source_reference)
    if not isinstance(body,bytes) or not body or len(body)>MAX_BYTES:
        raise ResourceLimitError("Mapping body exceeds its byte bound")
    raw=loads_strict(body,max_bytes=MAX_BYTES)
    if not isinstance(raw,list) or not 1<=len(raw)<=MAX_MEMBERS:
        raise ValidationError("Mapping must contain a bounded complete row list")
    proofs={};checked=[]
    if not isinstance(evidence,(tuple,list)) or len(evidence)>MAX_MEMBERS:
        raise ValidationError("Identity evidence list is invalid")
    total=len(body)
    for item in evidence:
        if not isinstance(item,IdentityEvidence) or item.provider!=provider or not isinstance(item.body,bytes) or not item.body or len(item.body)>MAX_BYTES:
            raise ValidationError("Identity evidence is invalid")
        total+=len(item.body)
        if total>64*1024*1024: raise ResourceLimitError("Mapping evidence aggregate exceeds 64 MiB")
        at=_utc(item.captured_at);_reference(item.source_reference)
        if at>captured_at: raise ValidationError("Mapping cannot see future identity evidence")
        if item.sha256 in proofs: raise ValidationError("Duplicate identity evidence")
        proofs[item.sha256]=(loads_strict(item.body,max_bytes=MAX_BYTES),item.source_reference,at)
        checked.append(IdentityEvidence(item.body,item.source_reference,at,item.provider))
    seen=set()
    for row in raw:
        if not isinstance(row,dict) or set(row)!=FIELDS: raise ValidationError("Mapping row fields are invalid")
        symbol=row["source_symbol"]
        if not isinstance(symbol,str) or not _SYMBOL.fullmatch(symbol) or symbol in seen:
            raise ValidationError("Mapping source symbols are invalid or duplicated")
        seen.add(symbol);_text(row["reason"],"mapping reason",1024)
        if row["status"] not in {"resolved","unresolved","unsupported","ambiguous"}:
            raise ValidationError("Mapping resolution status is invalid")
        for key in ("provider_symbol","provider_subject","instrument_id"):
            if row[key] is not None: _text(row[key],"mapping "+key,256)
        if row["provider_symbol"] is not None and not _SYMBOL.fullmatch(row["provider_symbol"]):
            raise ValidationError("Provider symbol is invalid")
        if row["cik"] is not None and (not isinstance(row["cik"],str) or len(row["cik"])!=10 or _cik(row["cik"])!=row["cik"]):
            raise ValidationError("Mapped CIK must be ten digits")
        digest=row["evidence_sha256"]
        if digest is not None:
            if digest not in proofs: raise ValidationError("Mapping identity evidence bytes are missing")
            source,reference,_=proofs[digest]
            if reference!=row["evidence_reference"]: raise ValidationError("Identity evidence reference differs")
            node=identity_source_object(source,row["evidence_pointer"],provider)
            for target,field in (("provider_symbol","symbol_field"),("provider_subject","subject_field"),("cik","cik_field")):
                if row[target] is not None:
                    key=row[field]
                    if not isinstance(key,str) or key not in node: raise ValidationError("Identity evidence field is missing")
                    if isinstance(node[key],bool) or not isinstance(node[key],(str,int)):
                        raise ValidationError("Identity evidence field must be a string or integer")
                    actual=_cik(node[key]) if target=="cik" else str(node[key])
                    if actual!=row[target]: raise ValidationError("Provider assertion differs from raw identity evidence")
        elif any(row[k] is not None for k in ("evidence_reference","evidence_pointer","symbol_field","subject_field","cik_field")):
            raise ValidationError("Mapping has an evidence locator without evidence")
        if row["status"]=="resolved":
            if not row["provider_subject"] or digest is None: raise ValidationError("Resolved mapping requires evidenced subject")
            if provider!="sec" and not row["provider_symbol"]: raise ValidationError("Provider symbol is required")
            if provider=="sec" and row["cik"] is None: raise ValidationError("SEC mapping requires an evidenced CIK")
        elif any(row[k] is not None for k in ("provider_symbol","provider_subject","instrument_id","cik")):
            raise ValidationError("Unresolved mappings cannot carry accepted identities")
        association=row["association_sha256"]
        if association is not None:
            if row["status"]!="resolved" or association not in proofs:
                raise ValidationError("Reviewed security association evidence is missing")
            assertion=_pointer(proofs[association][0],row["association_pointer"])
            expected={"contract":"quant_data.reviewed_security_association.v1",
                "membership_snapshot_id":membership_snapshot_id,"source_symbol":symbol,
                "provider":provider,"provider_symbol":row["provider_symbol"],"provider_subject":row["provider_subject"],
                "instrument_id":row["instrument_id"],"provider_evidence_sha256":digest,
                "provider_evidence_pointer":row["evidence_pointer"]}
            if not isinstance(assertion,dict) or set(assertion)!=set(expected)|{"reviewed_by","basis"}:
                raise ValidationError("Reviewed security association fields are invalid")
            if any(assertion[k]!=v for k,v in expected.items()):
                raise ValidationError("Reviewed security association differs from the requested mapping")
            _text(assertion["reviewed_by"],"association reviewer",256)
            _text(assertion["basis"],"security and share-class association basis",2048)
        elif row["association_pointer"] is not None:
            raise ValidationError("Association pointer has no retained evidence")
        elif row["status"]=="resolved" and row["provider_symbol"]!=symbol:
            raise ValidationError("Nonmatching source/provider symbols require a reviewed security association")
    rows_json=dumps_strict(sorted(raw,key=lambda r:r["source_symbol"]))
    used={r[key] for r in raw for key in ("evidence_sha256","association_sha256") if r[key] is not None}
    if used!=set(proofs): raise ValidationError("Mapping contains unused identity evidence")
    return MappingBatch(membership_snapshot_id,provider,captured_at,source_reference,body,
        tuple(sorted(checked,key=lambda e:e.sha256)),rows_json,_digest({"version":VERSION,"rows":[{k:r[k] for k in ("source_symbol","status","provider_symbol","provider_subject","instrument_id","cik","reason")} for r in loads_strict(rows_json)]}))

def _selected_instrument_plan(batch, row):
    """Derive one existing-format equity identity from retained FMP profile evidence."""
    from .stage10_history_importer import _InstrumentPlan
    proof=next((p for p in batch.evidence if p.sha256==row["evidence_sha256"]),None)
    if batch.provider!="fmp" or proof is None or row["status"]!="resolved":
        raise ValidationError("New instruments require resolved retained FMP profile evidence")
    value=_pointer(loads_strict(proof.body,max_bytes=MAX_BYTES),row["evidence_pointer"])
    if (not isinstance(value,dict) or value.get("symbol")!=row["provider_symbol"]
        or value.get("isEtf") is not False or value.get("isFund") is not False):
        raise ValidationError("New selected equity requires explicit FMP non-ETF/non-fund classification")
    name=value.get("companyName") or row["provider_symbol"]
    _text(name,"FMP company name",512)
    plan=_InstrumentPlan(row["provider_symbol"],"equity",name,None)
    if plan.instrument_id!=row["instrument_id"]:
        raise ConflictError("New selected instrument differs from the established identity algorithm")
    return plan


class CollectionMappingPublisher:
    def __init__(self,stores:StoreMap,registry):
        if not isinstance(stores,StoreMap) or DATASET not in {d.id for d in registry.datasets_for("market")}:
            raise ValidationError("Registered explicit mapping stores required")
        self.stores=stores;self.coordinator=IngestionCoordinator(stores,code_version=VERSION)
        self.instrument_creation_registered=any(c["id"]=="local.market.selected_instruments" for c in registry.collectors)
    def publish(self,batch,*,create_missing_instruments=False):
        if type(create_missing_instruments) is not bool:
            raise ValidationError("Selected instrument creation mode is invalid")
        if create_missing_instruments and not self.instrument_creation_registered:
            raise ValidationError("Selected instrument creation is not registered")
        if not isinstance(batch,MappingBatch): raise ValidationError("Expected prepared mapping batch")
        checked=prepare_mapping(membership_snapshot_id=batch.membership_snapshot_id,provider=batch.provider,
            captured_at=batch.captured_at,source_reference=batch.source_reference,body=batch.body,evidence=batch.evidence)
        if checked!=batch: raise ValidationError("Prepared mapping batch changed")
        rows=loads_strict(batch.rows_json)
        if create_missing_instruments and batch.provider!="fmp":
            raise ValidationError("Only FMP creates the existing market instrument identities")
        new_instruments={}
        scope={"membership_snapshot_id":batch.membership_snapshot_id,"provider":batch.provider}
        with acquire_write_session(self.stores,(StoreRole.MARKET,)) as locks:
            with quiet_immutable_read_connection(self.stores,"market") as c:
                membership=c.execute("SELECT captured_at,member_count FROM market_collection_snapshots WHERE snapshot_id=?",(batch.membership_snapshot_id,)).fetchone()
                if not membership or batch.captured_at<membership["captured_at"]:
                    raise ValidationError("Mapping membership is absent or from the future")
                symbols={r[0] for r in c.execute("SELECT source_symbol FROM market_collection_members WHERE snapshot_id=? LIMIT ?",(batch.membership_snapshot_id,MAX_MEMBERS+1))}
                if symbols!={r["source_symbol"] for r in rows} or len(rows)!=membership["member_count"]:
                    raise ValidationError("Mapping must explicitly account for every selected member")
                for row in rows:
                    if row["instrument_id"]:
                        instrument=c.execute("SELECT provider_symbol,captured_at FROM stage10_instruments WHERE instrument_id=?",(row["instrument_id"],)).fetchone()
                        if instrument is not None and _utc(instrument["captured_at"])>batch.captured_at:
                            raise ConflictError("Mapped instrument identity is from the future")
                        if not instrument and create_missing_instruments:
                            plan=_selected_instrument_plan(batch,row)
                            existing_symbol=c.execute("SELECT instrument_id FROM stage10_instruments WHERE provider='fmp' AND provider_symbol=?",
                                (row["provider_symbol"],)).fetchone()
                            if existing_symbol is not None:
                                raise ConflictError("Selected source symbol already has another market identity")
                            prior_plan=new_instruments.get(plan.instrument_id)
                            if prior_plan is not None and prior_plan!=plan:
                                raise ConflictError("Selected source profile metadata is ambiguous")
                            new_instruments[plan.instrument_id]=plan
                        elif not instrument or (batch.provider=="fmp" and instrument[0]!=row["provider_symbol"]):
                            raise ConflictError("Mapped market identity is missing or differs")
                for row in rows:
                    if batch.provider!="fmp" and row["instrument_id"]:
                        link=c.execute("SELECT m.status,m.instrument_id,m.cik FROM market_collection_provider_mappings m JOIN market_collection_mapping_snapshots s ON s.mapping_id=m.mapping_id WHERE s.membership_snapshot_id=? AND s.provider='fmp' AND m.source_symbol=? AND s.captured_at<=? ORDER BY s.version_sequence DESC LIMIT 1",
                            (batch.membership_snapshot_id,row["source_symbol"],batch.captured_at)).fetchone()
                        if (not link or link["status"]!="resolved" or link["instrument_id"]!=row["instrument_id"]
                            or (row["cik"] and link["cik"] and row["cik"]!=link["cik"])):
                            raise ConflictError("Cross-provider security requires a time-valid same-member FMP association")
                previous=c.execute("SELECT s.* FROM market_collection_mapping_heads h JOIN market_collection_mapping_snapshots s ON s.mapping_id=h.mapping_id WHERE h.membership_snapshot_id=? AND h.provider=?",(batch.membership_snapshot_id,batch.provider)).fetchone()
                replay=c.execute("SELECT semantic_identity,state_sha256 FROM market_collection_mapping_snapshots WHERE acquisition_identity=?",(batch.acquisition_identity,)).fetchone()
            if replay and replay["state_sha256"]!=batch.state_sha256: raise ConflictError("Mapping acquisition changed meaning")
            if replay or (previous and previous["state_sha256"]==batch.state_sha256):
                if new_instruments:
                    raise ConflictError("Previously mapped instruments are missing; replay cannot recreate them")
                return IngestionReceipt("unchanged","market",DATASET,(replay or previous)["semantic_identity"],None,None,None,0,())
            if previous and batch.captured_at<=previous["captured_at"]: raise ConflictError("Mapping transitions need increasing capture time")
            with quiet_immutable_read_connection(self.stores,"market") as c:
                for proof in batch.evidence:
                    retained=c.execute("SELECT evidence_sha256,provider,raw_body,source_reference,captured_at FROM market_collection_identity_evidence WHERE evidence_id=?",(proof.evidence_id,)).fetchone()
                    expected=(proof.sha256,proof.provider,proof.body,proof.source_reference,proof.captured_at)
                    if retained and tuple(retained)!=expected:
                        raise ConflictError("Retained identity evidence acquisition metadata differs")
                    earliest=c.execute("SELECT min(captured_at) FROM market_collection_identity_evidence WHERE provider=? AND evidence_sha256=?",(proof.provider,proof.sha256)).fetchone()[0]
                    if not retained and earliest is not None and proof.captured_at<earliest:
                        raise ConflictError("Earlier acquisition of known identity content is not established")
            predecessor=previous["mapping_id"] if previous else None
            semantic=_digest({"version":VERSION,"scope":scope,"state":batch.state_sha256,"predecessor":predecessor})
            mid=stable_id("collection_mapping",semantic)
            artifact_id=stable_id("collection_artifact",mid,hashlib.sha256(batch.body).hexdigest())
            def writer(c,rid):
                written=0
                for plan in new_instruments.values():
                    c.execute("INSERT INTO stage10_instruments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (plan.instrument_id,"fmp",plan.provider_symbol,"equity",plan.display_name,None,
                         "provider_native",None,plan.identity_seed_sha256,batch.captured_at,"datetime",rid))
                    written+=1
                proof_ids={}
                for proof in batch.evidence:
                    proof_ids[proof.sha256]=proof.evidence_id
                    existing=c.execute("SELECT evidence_sha256,provider,raw_body,source_reference,captured_at FROM market_collection_identity_evidence WHERE evidence_id=?",(proof.evidence_id,)).fetchone()
                    if existing:
                        if tuple(existing)!=(proof.sha256,proof.provider,proof.body,proof.source_reference,proof.captured_at):
                            raise ConflictError("Identity evidence acquisition conflicts")
                    else:
                        c.execute("INSERT INTO market_collection_identity_evidence VALUES (?,?,?,?,?,?,?,?)",
                            (proof.evidence_id,proof.sha256,proof.provider,proof.body,"application/json",proof.source_reference,proof.captured_at,rid));written+=1
                c.execute("INSERT INTO market_collection_artifacts VALUES (?,?,?,?,?,?,?,?)",(artifact_id,hashlib.sha256(batch.body).hexdigest(),"application/json",batch.body,len(batch.body),batch.source_reference,batch.captured_at,rid))
                c.execute("INSERT INTO market_collection_mapping_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(mid,batch.membership_snapshot_id,batch.provider,artifact_id,batch.acquisition_identity,semantic,batch.state_sha256,batch.captured_at,previous["version_sequence"]+1 if previous else 1,predecessor,len(rows),rid))
                for r in rows:
                    c.execute("INSERT INTO market_collection_provider_mappings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,batch.membership_snapshot_id,r["source_symbol"],r["status"],r["provider_symbol"],r["provider_subject"],r["instrument_id"],r["cik"],r["evidence_sha256"],proof_ids.get(r["evidence_sha256"]),proof_ids.get(r["association_sha256"]),r["evidence_reference"],r["evidence_pointer"],r["reason"]))
                if previous:
                    c.execute("UPDATE market_collection_mapping_heads SET mapping_id=? WHERE membership_snapshot_id=? AND provider=?",(mid,batch.membership_snapshot_id,batch.provider))
                else: c.execute("INSERT INTO market_collection_mapping_heads VALUES (?,?,?)",(batch.membership_snapshot_id,batch.provider,mid))
                artifact=ArtifactWrite(artifact_id,EVIDENCE,hashlib.sha256(batch.body).hexdigest(),"application/json",len(batch.body),batch.source_reference,scope,batch.captured_at,"datetime",VERSION)
                snapshot=SnapshotWrite(mid,DATASET,semantic,scope,"complete",len(rows),batch.captured_at,"datetime","validated",(artifact_id,),())
                return WriteResult(written+len(rows)+3,(artifact,),snapshot,())
            outputs=(EVIDENCE,DATASET,"market.stage10.instruments") if create_missing_instruments else (EVIDENCE,DATASET)
            return self.coordinator.execute(role="market",dataset_id=DATASET,output_dataset_ids=outputs,semantic_identity=semantic,
                run_id=stable_id("collection_mapping_run",mid),
                command="local.market.selected_instruments" if create_missing_instruments else "market.collection_manifest",scope=scope,
                started_at=batch.captured_at,completed_at=batch.captured_at,fetched_count=len(rows),writer=writer,held_locks=locks)
