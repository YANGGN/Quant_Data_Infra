"""Offline import of hash-verified retained transcript outputs; no model transport."""
from pathlib import Path
from ..company.transcript_analysis import digest, _response
from ..company.transcript_analysis_contract import _schema
from ..company import transcript_structured_call_terra_sol as original
from ..company import transcript_structured_quality as tiered
from ..company.transcript_structured_store import prepare_output, prepare_assessment, validate_batch
from ..errors import ConflictError, ValidationError
from ..json_codec import loads_strict, dumps_strict
from .equibles_transcript_backfill import read_file
from .transcript_analysis_pair_pilot import CONTRACT as PAIR_CONTRACT
from .transcript_quality_resample import retained_history_entries

def load(path, bound=32*1024*1024):
    return loads_strict(read_file(Path(path),bound).decode())

def exchange(pairs, report, *, stage, source, draft=None, parent_sha=None, profile=original):
    identity=report[stage]["request_identity"]
    request=profile.make_request(source,analysis=draft)
    config=profile.configuration_for(review=stage=="review")
    expected=digest({"contract":PAIR_CONTRACT,"configuration":config,
                     "request_sha256":digest(request),"parent_response_sha256":parent_sha})
    if identity!=expected:raise ConflictError("Retained model request identity differs")
    receipt=load(pairs/"requests"/(identity+".json"),65536)
    raw=read_file(pairs/"responses"/(identity+".json"),4*1024*1024)
    saved_request=read_file(pairs/"inputs"/(identity+".json"),4*1024*1024)
    if (saved_request!=request or receipt.get("status")!="received" or receipt.get("stage")!=stage
            or receipt.get("configuration")!=config or receipt.get("request_identity")!=identity
            or receipt.get("request_sha256")!=digest(request) or receipt.get("parent_response_sha256")!=parent_sha
            or receipt.get("response_sha256")!=digest(raw)
            or report[stage].get("raw_response_sha256")!=digest(raw)):
        raise ConflictError("Retained model evidence differs")
    output,_=_response(raw,config["model"])
    if output!=load(pairs/"outputs"/(identity+".json")):
        raise ConflictError("Saved output differs from model response")
    return receipt,raw,output

def load_saved_batch(project_root, *, history_plan_id, tiered_root, astra_root):
    """Trusted host paths only. Freeze exact originals; retain failed reviews as failed."""
    project=Path(project_root);tiered_root=Path(tiered_root);astra_root=Path(astra_root)
    state=project/"data/.operations/transcript-analysis"
    history=state/"history-batches";pairs=state/"model-pair-pilots"
    plan=load(history/"plans"/(history_plan_id+".json"))
    if digest(plan)!=history_plan_id or plan["extractor_configuration"]!=original.configuration_for():
        raise ConflictError("Original batch plan differs")
    entries,previous_sample=retained_history_entries(history,history_plan_id,pairs)
    if {e["source"]["capture_id"]:digest(e) for e in entries}!=load(tiered_root/"retained-input-hashes.json"):
        raise ConflictError("Original retained population changed")
    run=history/"runs"/history_plan_id
    assessments=load(tiered_root/"population-assessments.json")
    assessed_at=load(tiered_root/"preflight.json")["at"]
    outputs=[];evaluations=[];by_capture={}
    for entry in entries:
        source=entry["source"];capture=source["capture_id"]
        report=load(run/"extractions"/(capture+".json"))
        receipt,raw,draft=exchange(pairs,report,stage="extraction",source=source)
        if draft!=entry["draft"]:raise ConflictError("Original Terra draft changed")
        row=prepare_output(source=source,request_identity=receipt["request_identity"],
            configuration=receipt["configuration"],raw_response=raw,available_at=receipt["completed_at"])
        outputs.append(row);by_capture[capture]=row
        local=assessments[capture]
        evaluations.append(prepare_assessment(row,kind="automatic",evaluator=tiered.POLICY,
            reasoning_effort=None,outcome=local["status"],assessment=local,
            raw_evidence=dumps_strict(local).encode(),available_at=assessed_at))
    def append_review(report,profile):
        capture=report["capture_id"];parent=by_capture[capture]
        receipt,raw,review=exchange(pairs,report,stage="review",source=parent["source"],
            draft=parent["output"],parent_sha=digest(parent["raw_response"].encode()),profile=profile)
        _schema(review,profile.review_schema())
        evaluation={"review":review,"package_validation":report,"configuration":receipt["configuration"],
                    "request_identity":receipt["request_identity"],"parent_response_sha256":receipt["parent_response_sha256"]}
        evaluations.append(prepare_assessment(parent,kind="sol_review",evaluator="gpt-5.6-sol",
            reasoning_effort="medium",outcome=report["status"],assessment=evaluation,
            raw_evidence=raw,available_at=receipt["completed_at"]))
    for capture in previous_sample:append_review(load(run/"reviews"/(capture+".json")),original)
    fresh=load(tiered_root/"result.json")
    frozen=load(tiered_root/"prepared-plan.json")
    check={k:v for k,v in frozen.items() if k!="plan_id"}
    if digest(check)!=frozen["plan_id"] or fresh["plan_id"]!=frozen["plan_id"]:
        raise ConflictError("Fresh review plan differs")
    if {r["capture_id"] for r in fresh["records"]}!={r["capture_id"] for r in frozen["entries"]}:
        raise ConflictError("Fresh review membership differs")
    for report in fresh["records"]:append_review(report,tiered)
    astra_bytes=read_file(astra_root/"astra-verdict.json",4*1024*1024)
    astra=loads_strict(astra_bytes.decode());audit=load(astra_root/"final-audit.json")
    if digest(astra_bytes)!=audit["verdict_hashes"]["astra-verdict.json"]:
        raise ConflictError("Astra verdict hash differs")
    manifest=load(astra_root/"input-manifest.json")
    if manifest["parent_sol_plan_id"]!=fresh["plan_id"]:
        raise ConflictError("Astra source plan differs")
    if len(astra["cases"])!=5 or {r["capture_id"] for r in astra["cases"]}!={r["capture_id"] for r in fresh["records"]}:
        raise ConflictError("Astra adjudication membership differs")
    for case in astra["cases"]:
        parent=by_capture[case["capture_id"]]
        evaluations.append(prepare_assessment(parent,kind="astra_adjudication",evaluator="gpt-6-astra",
            reasoning_effort="medium",outcome=case["original_verdict"],assessment=case,
            raw_evidence=astra_bytes,available_at=audit["at"]))
    identity=validate_batch(outputs,evaluations)
    return {"contract":"retained_structured_transcript_import.v1","history_plan_id":history_plan_id,
            "semantic_identity":identity,"outputs":outputs,"assessments":evaluations}


def freeze_batch(root, batch):
    """Shard the bounded batch so no JSON document exceeds the codec limit."""
    from .equibles_transcript_backfill import atomic, private_directory
    root=Path(root)
    private_directory(root)
    manifest={k:v for k,v in batch.items() if k not in ("outputs","assessments")}
    for kind in ("outputs","assessments"):
        private_directory(root/kind);manifest[kind]=[]
        for row in batch[kind]:
            key=digest(row)
            atomic(root/kind/(key+".json"),row)
            manifest[kind].append(key)
    atomic(root/"manifest.json",manifest)
    return manifest

def thaw_batch(root):
    from ..company.transcript_structured_store import HEX
    root=Path(root);manifest=load(root/"manifest.json")
    batch={k:v for k,v in manifest.items() if k not in ("outputs","assessments")}
    for kind,maximum in (("outputs",2000),("assessments",8000)):
        keys=manifest[kind]
        if not isinstance(keys,list) or len(keys)>maximum:
            raise ValidationError("Frozen import exceeds bound")
        batch[kind]=[]
        for key in keys:
            if not isinstance(key,str) or not HEX.fullmatch(key):
                raise ValidationError("Invalid frozen import record hash")
            row=load(root/kind/(key+".json"))
            if digest(row)!=key:raise ConflictError("Frozen import record changed")
            batch[kind].append(row)
    if validate_batch(batch["outputs"],batch["assessments"])!=batch["semantic_identity"]:
        raise ConflictError("Frozen import identity differs")
    return batch
