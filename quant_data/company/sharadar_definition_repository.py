"""Versioned source definitions from complete, metadata-pinned INDICATORS walks."""
from ..ingestion import PublicationDeferred

import hashlib,zlib,time
from uuid import uuid4
from ..contracts import IngestionReceipt
from ..errors import ConflictError,ValidationError,ResourceLimitError
from ..ingestion import ArtifactWrite,IngestionCoordinator,SnapshotWrite,WriteResult
from ..json_codec import dumps_strict,loads_strict
from ..stores import StoreRole,acquire_write_session,quiet_immutable_read_connection,stable_id
from ..market.collection_universe import _utc
from .sharadar_definitions import DefinitionSnapshot,prepare_definition_snapshot,digest
DATASET="company.sharadar.definitions"
EVIDENCE="company.sharadar.evidence"
VERSION="nasdaq_sf1_definitions.v1"
WARNINGS=("local_capture_availability","remote_snapshot_coherence_unproven")

def unchanged(semantic):
    return IngestionReceipt(outcome="unchanged",store="company",dataset_id=DATASET,semantic_identity=semantic,
        run_id=None,artifact_id=None,snapshot_id=None,written_count=0,warnings=WARNINGS)

class SharadarDefinitionPublisher:
    def __init__(self,stores,registry):
        if DATASET not in {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Sharadar definitions are not registered")
        self.stores=stores;self.coordinator=IngestionCoordinator(stores,code_version=VERSION)
    def publish(self,snapshot,*,ingested_at,deadline=None,monotonic=time.monotonic):
        def check_deadline():
            if deadline is not None and monotonic()>=deadline:
                raise PublicationDeferred("Definition publication exceeded its invocation deadline")
        check_deadline()
        if not isinstance(snapshot,DefinitionSnapshot) or prepare_definition_snapshot(snapshot.pages)!=snapshot:
            raise ValidationError("Prepared definition snapshot changed")
        check_deadline()
        pages=snapshot.pages;schema=pages[0].schema;at=pages[-1].captured_at;finish=_utc(ingested_at)
        scope={"table":"SHARADAR/INDICATORS","scope_table":"SF1"}
        if schema.channel=="sharadar_direct":scope={"channel":schema.channel,"table":"descriptions","scope_table":"fundamentals"}
        if finish<at:raise ValidationError("Definition ingestion predates its evidence")
        bodies={hashlib.sha256(schema.body).hexdigest():schema.body}
        bodies.update({hashlib.sha256(p.body).hexdigest():p.body for p in pages})
        compressed={sha:zlib.compress(body,6) for sha,body in bodies.items()}
        rows={}
        for ordinal,page in enumerate(pages):
            for row in page.rows:rows.setdefault(row.observation_id,(row,ordinal))
        check_deadline()
        with acquire_write_session(self.stores,(StoreRole.COMPANY,)) as locks:
            check_deadline()
            with quiet_immutable_read_connection(self.stores,StoreRole.COMPANY) as c:
                old=c.execute("SELECT semantic_hash FROM company_sharadar_definition_captures WHERE acquisition_id=?",(snapshot.acquisition_id,)).fetchone()
                if old:return unchanged(old[0])
                previous=c.execute("SELECT * FROM company_sharadar_definition_captures ORDER BY available_at DESC LIMIT 1").fetchone()
                if previous and previous["semantic_hash"]==snapshot.semantic_hash:return unchanged(snapshot.semantic_hash)
                if previous and previous["available_at"]>=at:raise ConflictError("Changed definitions cannot be backdated or tied")
                if previous and previous["ingested_at"]>finish:raise ConflictError("Definition ingestion time cannot regress")
                heads={}
                for oid in rows:
                    r=c.execute("SELECT * FROM company_sharadar_definition_versions WHERE observation_id=? ORDER BY version_sequence DESC LIMIT 1",(oid,)).fetchone()
                    if r:heads[oid]=dict(r)
                # A metadata key change needs explicit reconciliation; otherwise
                # a renamed key could silently create unrelated observations.
                if previous:
                    stored=c.execute("SELECT compressed_body FROM company_sharadar_artifacts WHERE content_sha256=?",(previous["metadata_sha256"],)).fetchone()
                    raw_body=zlib.decompress(stored[0]);raw=loads_strict(raw_body)
                    if "datatable" in raw:
                        from .sharadar_definitions import parse_definition_metadata
                    else:
                        from .sharadar_direct import parse_definition_metadata
                    old_schema=parse_definition_metadata(raw_body,captured_at=previous["metadata_captured_at"],source_reference="retained/sharadar/definitions")
                    old_types=dict(old_schema.columns)
                    if (old_schema.primary_key!=schema.primary_key
                        or any(old_types[k]!=dict(schema.columns)[k] for k in schema.primary_key)):
                        raise ConflictError("Definition key change requires explicit reconciliation")
            predecessor=previous["capture_id"] if previous else None
            transition=digest({"predecessor":predecessor,"state":snapshot.semantic_hash})
            capture=stable_id("sharadar_definitions",transition)
            prepared={}
            for oid,(row,_) in rows.items():
                old=heads.get(oid)
                if old and old["semantic_hash"]==row.semantic_hash:prepared[oid]=(old["version_id"],None);continue
                pred=old["version_id"] if old else None;sequence=old["version_sequence"]+1 if old else 1
                prepared[oid]=(digest({"observation":oid,"predecessor":pred,"semantic":row.semantic_hash}),(sequence,pred))
            def writer(c,rid):
                check_deadline()
                count=0
                for sha,body in bodies.items():
                    if not c.execute("SELECT 1 FROM company_sharadar_artifacts WHERE content_sha256=?",(sha,)).fetchone():
                        c.execute("INSERT INTO company_sharadar_artifacts VALUES (?,?,?,?,?)",(sha,"zlib",len(body),compressed[sha],rid));count+=1
                meta_sha=hashlib.sha256(schema.body).hexdigest()
                c.execute("INSERT INTO company_sharadar_definition_captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (capture,snapshot.acquisition_id,snapshot.semantic_hash,schema.schema_id,meta_sha,schema.captured_at,
                     at,finish,predecessor,len(pages),len(rows),rid));count+=1
                metadata_parameters={"table":"SHARADAR/INDICATORS","kind":"metadata"}
                if schema.channel=="sharadar_direct":metadata_parameters=dict(pages[0].parameters)
                entries=[(-1,meta_sha,schema.captured_at,schema.source_reference,metadata_parameters)]
                entries.extend((i,hashlib.sha256(p.body).hexdigest(),p.captured_at,p.source_reference,dict(p.parameters)) for i,p in enumerate(pages))
                artifacts=[]
                for ordinal,sha,obtained,reference,params in entries:
                    c.execute("INSERT INTO company_sharadar_definition_capture_artifacts VALUES (?,?,?,?,?,?,?)",
                        (capture,ordinal,sha,obtained,reference,dumps_strict(params),rid));count+=1
                    artifacts.append(ArtifactWrite(stable_id("definition_artifact",capture+":"+str(ordinal)),EVIDENCE,
                        sha,"application/json",len(bodies[sha]),reference,params,obtained,"datetime",schema.normalization))
                for oid,(row,ordinal) in rows.items():
                    vid,change=prepared[oid]
                    if change:
                        sequence,pred=change
                        c.execute("INSERT INTO company_sharadar_definition_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (vid,oid,row.key_json,row.indicator,row.values_json,row.semantic_hash,sequence,pred,capture,at,rid));count+=1
                    c.execute("INSERT INTO company_sharadar_definition_membership VALUES (?,?,?,?,?,?,?)",
                        (capture,oid,vid,ordinal,row.source_row_index,row.source_row_pointer,rid));count+=1
                snap=SnapshotWrite(stable_id("definition_snapshot",capture),DATASET,transition,
                    scope,"complete",len(rows),at,"datetime",
                    "validated",tuple(a.artifact_id for a in artifacts),WARNINGS)
                return WriteResult(count,tuple(artifacts),snap,(),warnings=WARNINGS)
            check_deadline()
            return IngestionCoordinator(self.stores,code_version=schema.normalization).execute(role=StoreRole.COMPANY,dataset_id=DATASET,output_dataset_ids=(EVIDENCE,"company.sharadar.definition_evidence",DATASET),
                semantic_identity=transition,run_id=stable_id("definition_run",uuid4().hex),command="company.sharadar_definitions",
                scope=scope,started_at=at,completed_at=finish,
                fetched_count=len(pages) if schema.channel=="sharadar_direct" else len(pages)+1,writer=writer,held_locks=locks)

class SharadarDefinitionRepository:
    def __init__(self,stores):self.stores=stores
    def rows(self,*,knowledge_cutoff,indicator=None,mode="snapshot_as_of",ingested_cutoff=None,limit=100,offset=0):
        at=_utc(knowledge_cutoff)
        if mode not in ("snapshot_as_of","revision_history"):raise ValidationError("Definition read mode is invalid")
        if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or not 0<=offset<=10000:
            raise ResourceLimitError("Definition read bounds are invalid")
        if indicator is not None and (not isinstance(indicator,str) or not indicator or len(indicator)>128):
            raise ValidationError("Definition indicator is invalid")
        committed=_utc(ingested_cutoff) if ingested_cutoff is not None else None
        params=[at];where="c.available_at<=?"
        if committed:where+=" AND c.ingested_at<=?";params.append(committed)
        with quiet_immutable_read_connection(self.stores,StoreRole.COMPANY) as c:
            current=c.execute("SELECT * FROM company_sharadar_definition_captures c WHERE "+where+" ORDER BY c.available_at DESC LIMIT 1",params).fetchone()
            if current is None:return {"state":"unresolved","rows":[],"availability_basis":"local_capture","capture_id":None}
            if mode=="snapshot_as_of":
                query="""SELECT v.*,m.source_row_pointer,m.page_ordinal FROM company_sharadar_definition_membership m
                    JOIN company_sharadar_definition_versions v ON v.version_id=m.version_id WHERE m.capture_id=?"""
                args=[current["capture_id"]]
            else:
                query="""SELECT v.* FROM company_sharadar_definition_versions v
                    JOIN company_sharadar_definition_captures c ON c.capture_id=v.capture_id WHERE """+where
                args=list(params)
            if indicator is not None:query+=" AND v.indicator=?";args.append(indicator)
            query+=" ORDER BY v.indicator,v.available_at,v.version_sequence LIMIT ? OFFSET ?"
            found=[dict(r) for r in c.execute(query,(*args,limit,offset))]
        for r in found:
            r["values"]=loads_strict(r.pop("values_json"));r["source_key"]=loads_strict(r.pop("source_key_json"))
        return {"state":"captured","mode":mode,"rows":found,"availability_basis":"local_capture",
            "capture_id":current["capture_id"],"schema_id":current["schema_id"],
            "snapshot_available_at":current["available_at"],"remote_snapshot_coherence":"unproven"}
