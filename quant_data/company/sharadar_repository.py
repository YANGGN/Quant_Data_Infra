"""Replay-safe publication and bounded reads of source-native Nasdaq SF1."""
from __future__ import annotations

from ..ingestion import PublicationDeferred
from dataclasses import asdict
from uuid import uuid4
import hashlib
import zlib
import time
from ..contracts import IngestionReceipt
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..ingestion import ArtifactWrite,IngestionCoordinator,SnapshotWrite,WriteResult
from ..json_codec import dumps_strict,loads_strict
from ..market.collection_bindings import PinnedCollection
from ..market.collection_pins import validate_pinned_collection
from ..market.collection_universe import _utc
from ..stores import StoreRole,acquire_write_session,quiet_immutable_read_connection,stable_id
from .sharadar_sf1 import Sf1Partition,prepare_partition,digest,DIMENSIONS,_date,CHANNEL,TABLE
from .sharadar_registry import DATASET_IDS
DATASET="company.sharadar.sf1"
VERSION="nasdaq_sf1.v1"
MAX_PARTITION_BYTES=256*1024*1024
WARNINGS=("local_capture_availability","remote_snapshot_coherence_unproven","source_definitions_unresolved")

def _scope(partition):
    params=dict(partition.pages[0].parameters)
    params.pop("qopts.cursor_id",None);params.pop("qopts.per_page",None)
    schema=partition.pages[0].schema
    if schema.channel=="sharadar_direct":
        params.pop("offset",None);params.pop("limit",None)
    return {"channel":schema.channel,"table":"fundamentals" if schema.channel=="sharadar_direct" else TABLE,"filters":params}

def _unchanged(semantic):
    return IngestionReceipt(outcome="unchanged",store="company",dataset_id=DATASET,
        semantic_identity=semantic,run_id=None,artifact_id=None,snapshot_id=None,written_count=0,warnings=WARNINGS)

class SharadarSf1Publisher:
    def __init__(self,stores,registry):
        if not set(DATASET_IDS)<={d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Sharadar SF1 datasets are not registered")
        self.stores=stores
        self.coordinator=IngestionCoordinator(stores,code_version=VERSION)

    def publish(self,partition,*,selection,ingested_at,deadline=None,monotonic=time.monotonic):
        def check_deadline():
            if deadline is not None and monotonic()>=deadline:
                raise PublicationDeferred("SF1 publication exceeded its invocation deadline")
        check_deadline()
        if not isinstance(partition,Sf1Partition):
            raise ValidationError("Sharadar publication requires a validated partition")
        checked=prepare_partition(partition.pages,max_pages=100,max_rows=100000,max_bytes=MAX_PARTITION_BYTES)
        if checked!=partition:raise ValidationError("Prepared Sharadar partition changed")
        check_deadline()
        if not partition.transport_complete:
            raise ConflictError("Incomplete SF1 cursor walk stays private; no canonical checkpoint advances")
        pages=partition.pages;schema=pages[0].schema
        at=pages[-1].captured_at;finish=_utc(ingested_at)
        if finish<at:raise ValidationError("Sharadar ingestion predates its evidence")
        if not isinstance(selection,PinnedCollection) or selection.binding.id!="sharadar_fundamentals":
            raise ValidationError("Sharadar publication requires its pinned selected universe")
        validate_pinned_collection(self.stores,selection,cutoff=pages[0].captured_at)
        scope=_scope(partition);scope_hash=digest(scope)
        symbols=scope["filters"]["ticker"].split(",")
        grouped={}
        for subject in selection.eligible:
            if subject.provider_symbol in symbols:
                grouped.setdefault(subject.provider_symbol,[]).append(subject)
        if set(grouped)!=set(symbols):
            raise ConflictError("Sharadar partition includes an unsupported or unselected provider symbol")
        associations={}
        for symbol,members in grouped.items():
            identities={(s.provider_subject,s.instrument_id,s.cik) for s in members}
            if len(identities)!=1:raise ConflictError("Sharadar source ticker has conflicting selected identity")
            subject,instrument,cik=next(iter(identities))
            associations[symbol]={"provider_subject":subject,"instrument_id":instrument,"cik":cik,
                "source_members":sorted(s.source_symbol for s in members)}
        state_hash=digest({"partition":partition.semantic_hash,"selection":selection.scope_sha256,"associations":associations})
        acquisition=digest({"partition":partition.acquisition_id,"metadata_sha":schema.content_sha256,
            "metadata_captured_at":schema.captured_at,"metadata_reference":schema.source_reference,"selection":selection.scope_sha256})
        rows={}
        for page in pages:
            for row in page.rows:rows.setdefault(row.observation_id,row)
        # Hashing, reparsing and compression occur before the physical write lock.
        bodies={schema.content_sha256:schema.metadata_body}
        bodies.update({p.content_sha256:p.body for p in pages})
        if sum(len(body) for body in bodies.values())>MAX_PARTITION_BYTES:
            raise ResourceLimitError("SF1 metadata plus data exceeds the publication byte budget")
        compressed={sha:zlib.compress(body,level=6) for sha,body in bodies.items()}
        check_deadline()
        with acquire_write_session(self.stores,(StoreRole.COMPANY,)) as locks:
            check_deadline()
            with quiet_immutable_read_connection(self.stores,StoreRole.COMPANY) as c:
                retained=c.execute("SELECT semantic_hash FROM company_sharadar_captures WHERE acquisition_id=?",(acquisition,)).fetchone()
                if retained:return _unchanged(retained[0])
                contracts={r[0] for r in c.execute("SELECT DISTINCT key_contract_id FROM company_sharadar_schema_versions")}
                if contracts and contracts!={schema.key_contract_id}:
                    raise ConflictError("SF1 source-key change requires explicit identity reconciliation")
                stored_schema=c.execute("SELECT metadata_captured_at FROM company_sharadar_schema_versions WHERE schema_id=?",
                    (schema.schema_id,)).fetchone()
                if stored_schema and (stored_schema[0]>schema.captured_at or stored_schema[0]>at):
                    raise ConflictError("SF1 schema evidence cannot be relabelled to an unestablished earlier acquisition")
                previous=c.execute("""SELECT c.* FROM company_sharadar_scope_heads h
                    JOIN company_sharadar_captures c ON c.capture_id=h.capture_id WHERE h.scope_sha256=?""",(scope_hash,)).fetchone()
                compatible_schemas={schema.schema_id}
                for retained_schema in c.execute("SELECT * FROM company_sharadar_schema_versions"):
                    if ({retained_schema["normalization_version"],schema.normalization}=={"nasdaq_sf1.v1","sharadar_direct.v1"}
                        and retained_schema["key_contract_id"]==schema.key_contract_id
                        and loads_strict(retained_schema["columns_json"])==[list(x) for x in sorted(schema.columns)]
                        and loads_strict(retained_schema["primary_key_json"])==list(schema.primary_key)
                        and loads_strict(retained_schema["filters_json"])==list(schema.filters)):
                        compatible_schemas.add(retained_schema["schema_id"])
                heads={}
                for oid in rows:
                    found=c.execute("""SELECT v.* FROM company_sharadar_sf1_heads h
                        JOIN company_sharadar_sf1_versions v ON v.version_id=h.version_id WHERE h.observation_id=?""",(oid,)).fetchone()
                    if found:heads[oid]=dict(found)
                matches=all(oid in heads and heads[oid]["row_semantic_hash"]==r.row_semantic_hash
                    and heads[oid]["schema_id"] in compatible_schemas for oid,r in rows.items())
                if previous and previous["semantic_hash"]==state_hash and matches:
                    return _unchanged(state_hash)
                if previous and previous["captured_at"]>=at:
                    raise ConflictError("Changed Sharadar scope cannot be backdated")
                if any(h["available_at"]>at or (h["available_at"]==at and
                    (h["row_semantic_hash"]!=rows[oid].row_semantic_hash or h["schema_id"] not in compatible_schemas))
                    for oid,h in heads.items()):
                    raise ConflictError("Sharadar row transition cannot be backdated or tied")
            predecessor=previous["capture_id"] if previous else None
            transition=digest({"scope":scope_hash,"predecessor":predecessor,"state":state_hash,
                "row_predecessors":sorted((oid,h["version_id"]) for oid,h in heads.items())})
            capture=stable_id("sharadar_capture",transition)
            prepared={}
            for oid,row in rows.items():
                old=heads.get(oid)
                same=old and old["row_semantic_hash"]==row.row_semantic_hash and old["schema_id"] in compatible_schemas
                if same:prepared[oid]=(old["version_id"],None);continue
                pred=old["version_id"] if old else None
                sequence=old["version_sequence"]+1 if old else 1
                vid=digest({"observation":oid,"predecessor":pred,"schema":schema.schema_id,"row":row.row_semantic_hash})
                kind="initial" if not old else "metadata_change" if old["value_hash"]==row.value_hash else "observed_value_change"
                prepared[oid]=(vid,(sequence,pred,kind))
            def writer(c,rid):
                check_deadline()
                written=0
                for sha,body in bodies.items():
                    if not c.execute("SELECT 1 FROM company_sharadar_artifacts WHERE content_sha256=?",(sha,)).fetchone():
                        c.execute("INSERT INTO company_sharadar_artifacts VALUES (?,?,?,?,?)",(sha,"zlib",len(body),compressed[sha],rid));written+=1
                if not c.execute("SELECT 1 FROM company_sharadar_schema_versions WHERE schema_id=?",(schema.schema_id,)).fetchone():
                    c.execute("INSERT INTO company_sharadar_schema_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (schema.schema_id,schema.key_contract_id,dumps_strict(sorted(schema.columns)),dumps_strict(schema.primary_key),
                         dumps_strict(schema.filters),schema.content_sha256,schema.captured_at,"unresolved",schema.normalization,rid));written+=1
                c.execute("INSERT INTO company_sharadar_captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (capture,acquisition,scope_hash,dumps_strict(scope),state_hash,schema.schema_id,at,finish,1,"unproven",predecessor,len(pages),sum(len(p.rows) for p in pages),rid));written+=1
                metadata_parameters={"table":TABLE,"kind":"metadata"}
                if schema.channel=="sharadar_direct":
                    from .sharadar_direct import DEFINITION_PARAMETERS
                    metadata_parameters=dict(DEFINITION_PARAMETERS)
                entries=[(-1,schema.content_sha256,schema.captured_at,schema.source_reference,metadata_parameters)]
                entries.extend((i,p.content_sha256,p.captured_at,p.source_reference,dict(p.parameters)) for i,p in enumerate(pages))
                artifacts=[]
                for ordinal,sha,obtained,reference,params in entries:
                    c.execute("INSERT INTO company_sharadar_capture_artifacts VALUES (?,?,?,?,?,?,?)",
                        (capture,ordinal,sha,obtained,reference,dumps_strict(params),rid));written+=1
                    artifacts.append(ArtifactWrite(stable_id("sharadar_artifact",capture+":"+str(ordinal)),DATASET_IDS[0],
                        sha,"application/json",len(bodies[sha]),reference,params,obtained,"datetime",schema.normalization))
                for symbol,a in sorted(associations.items()):
                    assertion=digest({"capture":capture,"symbol":symbol,"association":a})
                    c.execute("INSERT INTO company_sharadar_identity_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (assertion,capture,symbol,a["provider_subject"],dumps_strict(a["source_members"]),selection.membership_snapshot_id,
                         selection.mapping_id,a["instrument_id"],a["cik"],at,rid));written+=1
                for oid,row in rows.items():
                    vid,change=prepared[oid]
                    if oid not in heads:
                        if not c.execute("SELECT 1 FROM company_sharadar_sf1_observations WHERE observation_id=?",(oid,)).fetchone():
                            c.execute("INSERT INTO company_sharadar_sf1_observations VALUES (?,?,?,?,?,?)",
                                (oid,schema.key_contract_id,row.source_key_json,row.source_ticker,row.dimension,rid));written+=1
                    if change:
                        sequence,pred,kind=change
                        c.execute("INSERT INTO company_sharadar_sf1_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (vid,oid,sequence,pred,capture,schema.schema_id,row.source_datekey,row.reportperiod,row.calendardate,
                             row.source_lastupdated,at,finish,"local_capture",row.values_json,row.missingness_json,
                             row.unrecognized_fields_json,row.value_hash,row.row_semantic_hash,kind,rid));written+=1
                        if oid in heads:c.execute("UPDATE company_sharadar_sf1_heads SET version_id=? WHERE observation_id=?",(vid,oid))
                        else:c.execute("INSERT INTO company_sharadar_sf1_heads VALUES (?,?)",(oid,vid))
                        written+=1
                for ordinal,page in enumerate(pages):
                    for row in page.rows:
                        c.execute("INSERT INTO company_sharadar_sf1_membership VALUES (?,?,?,?,?,?,?)",
                            (capture,ordinal,row.source_row_index,row.source_row_pointer,row.observation_id,prepared[row.observation_id][0],rid));written+=1
                findings={code:{} for code in WARNINGS}
                unknown=sorted({name for row in rows.values() for name in loads_strict(row.unrecognized_fields_json)})
                if unknown:findings["unrecognized_fields"]={"fields":unknown}
                for code,details in findings.items():
                    c.execute("INSERT INTO company_sharadar_quality_findings VALUES (?,?,?,?)",(capture,code,dumps_strict(details),rid));written+=1
                if previous:c.execute("UPDATE company_sharadar_scope_heads SET capture_id=? WHERE scope_sha256=?",(capture,scope_hash))
                else:c.execute("INSERT INTO company_sharadar_scope_heads VALUES (?,?)",(scope_hash,capture))
                written+=1
                snapshot=SnapshotWrite(stable_id("sharadar_snapshot",capture),DATASET,transition,scope,"complete",len(rows),at,
                    "datetime","validated",tuple(a.artifact_id for a in artifacts),WARNINGS)
                return WriteResult(written,tuple(artifacts),snapshot,(),warnings=WARNINGS)
            check_deadline()
            return IngestionCoordinator(self.stores,code_version=schema.normalization).execute(role=StoreRole.COMPANY,dataset_id=DATASET,output_dataset_ids=DATASET_IDS,
                semantic_identity=transition,run_id=stable_id("sharadar_run",uuid4().hex),command="company.sharadar_sf1",
                scope=scope,started_at=at,completed_at=finish,fetched_count=len(pages)+1,writer=writer,held_locks=locks)

class SharadarSf1Repository:
    """Source-aware local archive. No SQL, writable connections or paths from callers."""
    def __init__(self,stores):self.stores=stores

    def rows(self,*,ticker,dimensions,knowledge_cutoff,mode="local_capture_as_of",filing_cutoff=None,
             ingested_cutoff=None,limit=100,offset=0,provider_subject=None):
        if not isinstance(ticker,str) or not ticker or len(ticker)>32:raise ValidationError("SF1 ticker is invalid")
        dims=tuple(dimensions)
        if not dims or len(dims)!=len(set(dims)) or not set(dims)<=DIMENSIONS:raise ValidationError("SF1 dimensions are invalid")
        if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or not 0<=offset<=100000:
            raise ResourceLimitError("SF1 reader bounds are invalid")
        if mode not in ("local_capture_as_of","provider_as_reported","revision_history"):
            raise ValidationError("SF1 read mode lacks a supported availability basis")
        at=_utc(knowledge_cutoff)
        if mode=="provider_as_reported":
            if any(not d.startswith("AR") for d in dims) or filing_cutoff is None:
                raise ValidationError("Provider-as-reported requires AR dimensions and a separate filing cutoff")
            filing_at=_utc(filing_cutoff)
            # Source date precision cannot establish same-day intraday release.
            filing_date=filing_at[:10]
        elif filing_cutoff is not None:raise ValidationError("Filing cutoff applies only to provider-as-reported")
        committed=_utc(ingested_cutoff) if ingested_cutoff is not None else None
        params=[ticker,*dims,at]
        where="o.source_ticker=? AND o.dimension IN ("+",".join("?" for _ in dims)+") AND v.available_at<=?"
        if provider_subject is not None:
            if not isinstance(provider_subject,str) or not provider_subject or len(provider_subject)>256:
                raise ValidationError("SF1 provider subject is invalid")
            where+=" AND EXISTS(SELECT 1 FROM company_sharadar_identity_assertions i WHERE i.capture_id=v.capture_id AND i.source_ticker=o.source_ticker AND i.provider_subject=?)"
            params.append(provider_subject)
        if committed:where+=" AND v.ingested_at<=?";params.append(committed)
        if mode=="provider_as_reported":where+=" AND v.source_datekey<?";params.append(filing_date)
        query="""WITH eligible AS (SELECT o.source_ticker,o.dimension,o.source_key_json,v.*,s.normalization_version,
            ROW_NUMBER() OVER (PARTITION BY v.observation_id ORDER BY v.available_at DESC,v.version_sequence DESC) AS position
            FROM company_sharadar_sf1_observations o JOIN company_sharadar_sf1_versions v ON v.observation_id=o.observation_id
            JOIN company_sharadar_schema_versions s ON s.schema_id=v.schema_id
            WHERE """+where+") SELECT * FROM eligible"+(" WHERE position=1" if mode!="revision_history" else "")+"""
            ORDER BY reportperiod DESC,dimension,source_datekey,available_at,version_sequence,observation_id LIMIT ? OFFSET ?"""
        with quiet_immutable_read_connection(self.stores,StoreRole.COMPANY) as c:
            found=[dict(r) for r in c.execute(query,(*params,limit,offset))]
        output=[]
        for item in found:
            item.pop("position",None)
            item["delivery_channel"]="sharadar_direct" if item["normalization_version"]=="sharadar_direct.v1" else CHANNEL
            item["source_table"]="fundamentals" if item["delivery_channel"]=="sharadar_direct" else TABLE
            for key in ("values_json","missingness_json","unrecognized_fields_json","source_key_json"):
                item[key.removesuffix("_json")]=loads_strict(item.pop(key))
            output.append(item)
        return {"source":"sharadar/fundamentals","mode":mode,"knowledge_cutoff":at,"rows":output,
            "availability_basis":"vendor_historical_reconstruction_with_pinned_local_archive" if mode=="provider_as_reported" else "local_capture",
            "warnings":list(WARNINGS),"first_release_completeness":"unproven"}

    def raw_artifact(self,sha256):
        if not isinstance(sha256,str) or len(sha256)!=64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValidationError("SF1 artifact hash is invalid")
        with quiet_immutable_read_connection(self.stores,StoreRole.COMPANY) as c:
            value=c.execute("SELECT raw_byte_count,compressed_body FROM company_sharadar_artifacts WHERE content_sha256=?",(sha256,)).fetchone()
        if value is None:return None
        decoder=zlib.decompressobj()
        try:
            raw=decoder.decompress(value["compressed_body"],value["raw_byte_count"]+1)
        except zlib.error as exc:raise ValidationError("SF1 compressed evidence is invalid") from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(raw)!=value["raw_byte_count"] or hashlib.sha256(raw).hexdigest()!=sha256:
            raise ValidationError("SF1 original evidence hash or size differs")
        return raw
