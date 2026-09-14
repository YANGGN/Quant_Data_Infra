"""Immutable storage for original structured drafts and separate assessments."""
from __future__ import annotations
from uuid import uuid4
import re
from ..contracts import IngestionReceipt
from ..errors import ValidationError, ConflictError, ResourceLimitError
from ..ingestion import IngestionCoordinator, WriteResult, ArtifactWrite, SnapshotWrite
from ..json_codec import dumps_strict, loads_strict
from ..stores import acquire_write_session, quiet_immutable_read_connection, stable_id
from .equibles_transcripts import utc
from .transcript_analysis import read_source, digest, _response
from .transcript_analysis_contract import _schema
from . import transcript_structured_quality as quality
from . import transcript_structured_call_terra_sol as legacy
from . import transcript_structured_call_luna_astra as luna_astra
from .transcript_structured_registry import DATASET, TABLES

VERSION = "structured_transcript_storage.v1"
MAX_OUTPUTS = 2000
MAX_ASSESSMENTS = 8000
HEX = re.compile(r"[a-f0-9]{64}\Z")

def prepare_output(*, source, request_identity, configuration, raw_response, available_at):
    if configuration not in (legacy.configuration_for(), quality.configuration_for(), luna_astra.configuration_for()):
        raise ValidationError("Unknown structured extraction configuration")
    if not isinstance(request_identity,str) or not HEX.fullmatch(request_identity):
        raise ValidationError("Invalid extraction request identity")
    output, usage = _response(raw_response, configuration["model"])
    # Preserve schema-shaped drafts even when factual or editorial checks fail.
    _schema(output, quality.brief_schema())
    if utc(available_at) < utc(source["source_available_at"]):
        raise ValidationError("Structured output predates its source")
    value = {"source":source,"request_identity":request_identity,"configuration":configuration,
             "raw_response":raw_response.decode("utf-8"),"output":output,
             "available_at":utc(available_at)}
    return {**value,"analysis_id":stable_id("structured_transcript",request_identity),
            "semantic_identity":digest(value)}

def prepare_assessment(output, *, kind, evaluator, reasoning_effort, outcome, assessment,
                       raw_evidence, available_at):
    if kind not in ("automatic","sol_review","astra_adjudication"):
        raise ValidationError("Unknown structured assessment kind")
    if not isinstance(raw_evidence,bytes) or not 1<=len(raw_evidence)<=4*1024*1024:
        raise ResourceLimitError("Assessment evidence exceeds bound")
    if not isinstance(assessment,dict) or not isinstance(outcome,str) or not outcome:
        raise ValidationError("Assessment must preserve its outcome and document")
    if utc(available_at)<output["available_at"]:
        raise ValidationError("Assessment predates original extraction")
    if kind=="automatic":
        expected=quality.assess_brief(output["output"],output["source"])
        if (evaluator!=quality.POLICY or reasoning_effort is not None or assessment!=expected
                or outcome!=expected["status"] or loads_strict(raw_evidence.decode())!=assessment):
            raise ValidationError("Automatic assessment differs from its policy")
    elif kind=="sol_review":
        review,_=_response(raw_evidence,"gpt-5.6-sol")
        if (evaluator!="gpt-5.6-sol" or reasoning_effort!="medium"
                or assessment.get("review")!=review
                or outcome!=assessment.get("package_validation",{}).get("status")):
            raise ValidationError("Sol review evidence differs")
    else:
        verdict=loads_strict(raw_evidence.decode())
        if (evaluator!="gpt-6-astra" or reasoning_effort!="medium"
                or verdict.get("effective_model")!=evaluator or verdict.get("reasoning_effort")!=reasoning_effort
                or assessment not in verdict.get("cases",[])
                or assessment.get("capture_id")!=output["source"]["capture_id"]
                or outcome!=assessment.get("original_verdict")):
            raise ValidationError("Astra adjudication evidence differs")
    value={"analysis_id":output["analysis_id"],"kind":kind,"evaluator":evaluator,
           "reasoning_effort":reasoning_effort,"outcome":outcome,"assessment":assessment,
           "raw_evidence":raw_evidence.decode("utf-8"),"available_at":utc(available_at)}
    identity=digest(value)
    return {**value,"assessment_id":stable_id("structured_assessment",identity),"semantic_identity":identity}

def validate_batch(outputs, assessments):
    if not 1<=len(outputs)<=MAX_OUTPUTS or len(assessments)>MAX_ASSESSMENTS:
        raise ResourceLimitError("Structured publication exceeds bound")
    by_id={}
    for row in outputs:
        checked=prepare_output(source=row["source"],request_identity=row["request_identity"],
            configuration=row["configuration"],raw_response=row["raw_response"].encode(),
            available_at=row["available_at"])
        if checked!=row or row["analysis_id"] in by_id:
            raise ConflictError("Structured publication duplicates or changes an output")
        by_id[row["analysis_id"]]=row
    ids=set()
    for row in assessments:
        parent=by_id.get(row["analysis_id"])
        if parent is None:raise ValidationError("Assessment has no selected parent")
        checked=prepare_assessment(parent,kind=row["kind"],evaluator=row["evaluator"],
            reasoning_effort=row["reasoning_effort"],outcome=row["outcome"],assessment=row["assessment"],
            raw_evidence=row["raw_evidence"].encode(),available_at=row["available_at"])
        if row!=checked or row["assessment_id"] in ids:
            raise ConflictError("Structured publication duplicates or changes an assessment")
        ids.add(row["assessment_id"])
    return digest({"version":VERSION,"outputs":sorted(r["semantic_identity"] for r in outputs),
                   "assessments":sorted(r["semantic_identity"] for r in assessments)})

class StructuredTranscriptPublisher:
    def __init__(self, stores, registry):
        if DATASET not in {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Structured transcript dataset is not registered")
        self.stores,self.registry=stores,registry
        self.coordinator=IngestionCoordinator(stores,code_version=VERSION)

    def publish(self, outputs, assessments, *, published_at):
        semantic=validate_batch(outputs,assessments)
        published=utc(published_at)
        if any(r["available_at"]>published for r in outputs+assessments):
            raise ValidationError("Publication predates retained model evidence")
        with acquire_write_session(self.stores,("company",),timeout_seconds=60) as locks:
            # Re-read full immutable evidence under the physical store lock.
            for row in outputs:
                if read_source(self.stores,row["source"]["capture_id"])!=row["source"]:
                    raise ConflictError("Structured input differs from the canonical transcript")
            with quiet_immutable_read_connection(self.stores,"company") as c:
                migration=[tuple(r) for r in c.execute("SELECT migration_id,sha256 FROM schema_migrations ORDER BY ordinal")]
                expected=[(m.id,m.sha256) for m in self.registry.migrations if m.store=="company"]
                if migration!=expected:raise ConflictError("Structured publication migration ledger differs")
                new_outputs=[]
                for row in outputs:
                    found=c.execute("SELECT semantic_identity FROM company_structured_transcript_outputs WHERE request_identity=?",(row["request_identity"],)).fetchone()
                    if found and found[0]!=row["semantic_identity"]:
                        raise ConflictError("Existing extraction request has different retained content")
                    if not found:new_outputs.append(row)
                new_assessments=[]
                for row in assessments:
                    found=c.execute("SELECT semantic_identity FROM company_structured_transcript_assessments WHERE assessment_id=?",(row["assessment_id"],)).fetchone()
                    if found and found[0]!=row["semantic_identity"]:
                        raise ConflictError("Existing assessment identity differs")
                    if not found:new_assessments.append(row)
            if not new_outputs and not new_assessments:
                return IngestionReceipt(outcome="unchanged",store="company",dataset_id=DATASET,
                    semantic_identity=semantic,run_id=None,artifact_id=None,snapshot_id=None,written_count=0,
                    warnings=("derived_drafts_not_verified_facts",))
            def writer(c,rid):
                for row in new_outputs:
                    source=row["source"];config=row["configuration"];raw=row["raw_response"].encode()
                    c.execute("INSERT INTO company_structured_transcript_outputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["analysis_id"],row["request_identity"],row["semantic_identity"],source["capture_id"],
                         source["input_sha256"],dumps_strict(source),config["model"],config["reasoning_effort"],
                         config["prompt_version"],row["output"]["schema_version"],dumps_strict(config),
                         dumps_strict(row["output"]),digest(raw),raw,source["source_available_at"],
                         row["available_at"],published,rid))
                for row in new_assessments:
                    raw=row["raw_evidence"].encode()
                    c.execute("INSERT INTO company_structured_transcript_assessments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["assessment_id"],row["analysis_id"],row["semantic_identity"],row["kind"],
                         row["evaluator"],row["reasoning_effort"],row["outcome"],dumps_strict(row["assessment"]),
                         digest(raw),raw,row["available_at"],published,rid))
                written=len(new_outputs)+len(new_assessments)
                payload=dumps_strict({"version":VERSION,"outputs":sorted(r["semantic_identity"] for r in outputs),"assessments":sorted(r["semantic_identity"] for r in assessments)}).encode()
                artifact=ArtifactWrite(stable_id("structured_evidence",semantic),DATASET,digest(payload),
                    "application/json",len(payload),"structured-transcript-imports/"+digest(payload)+".json",
                    scope,published,"datetime",VERSION)
                snapshot=SnapshotWrite(stable_id("structured_snapshot",semantic),DATASET,semantic,scope,
                    "complete",written,published,"datetime","validated",(artifact.artifact_id,),
                    ("derived_drafts_not_verified_facts",))
                return WriteResult(written,(artifact,),snapshot,(),warnings=("derived_drafts_not_verified_facts",))
            scope={"analysis_ids":sorted(r["analysis_id"] for r in outputs),"assessment_count":len(assessments)}
            return self.coordinator.execute(role="company",dataset_id=DATASET,output_dataset_ids=(DATASET,),
                semantic_identity=semantic,run_id=stable_id("structured_import",uuid4().hex),
                command="company.transcript.import_structured",
                scope=scope,
                started_at=published,completed_at=published,fetched_count=0,writer=writer,held_locks=locks)

class StructuredTranscriptRepository:
    def __init__(self, stores):self.stores=stores

    def get(self, capture_id, *, as_of):
        if not isinstance(capture_id,str) or not re.fullmatch(r"equibles_transcript_[a-f0-9]{32}",capture_id):
            raise ValidationError("Invalid transcript capture")
        cutoff=utc(as_of)
        with quiet_immutable_read_connection(self.stores,"company") as c:
            row=c.execute("SELECT * FROM company_structured_transcript_outputs WHERE capture_id=? AND available_at<=? ORDER BY available_at DESC,analysis_id DESC LIMIT 1",(capture_id,cutoff)).fetchone()
            if row is None:return None
            result=dict(row)
            result["assessments"]=[dict(r) for r in c.execute("SELECT * FROM company_structured_transcript_assessments WHERE analysis_id=? AND available_at<=? ORDER BY available_at,assessment_id LIMIT 101",(row["analysis_id"],cutoff))]
            if len(result["assessments"])>100:raise ResourceLimitError("Assessment history exceeds read bound")
            result["review_status"]="reviewed" if any(r["kind"]!="automatic" for r in result["assessments"]) else "unreviewed"
            result["output"]=loads_strict(result["output_json"])
            return result
