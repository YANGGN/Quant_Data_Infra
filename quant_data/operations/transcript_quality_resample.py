"""Review-only resampling of retained Terra outputs under the tiered quality policy."""
from __future__ import annotations
import secrets
import re
from pathlib import Path
from . import transcript_analysis as m
from .equibles_transcript_backfill import atomic,private_directory,read_file,job_lock
from .transcript_analysis_pair_pilot import TranscriptModelPairPilot,_error
from .transcript_history_batch import load,sample_ids
from ..company import transcript_structured_quality as profile
from ..company.transcript_analysis import _response
from ..errors import ConflictError,ValidationError
from ..json_codec import loads_strict

CONTRACT="transcript_tiered_review_resample.v1"


def retained_history_entries(history_root,history_plan_id,pair_root):
    """Read and hash-check the original extraction, never substitute a previous review."""
    plan=load(history_root/"plans"/(history_plan_id+".json"))
    if m.digest(plan)!=history_plan_id:raise ConflictError("History plan changed")
    run=history_root/"runs"/history_plan_id
    entries=[]
    for item in plan["entries"]:
        report=load(run/"extractions"/(item["capture_id"]+".json"))
        identity=report["extraction"]["request_identity"]
        receipt=load(pair_root/"requests"/(identity+".json"))
        raw=read_file(pair_root/"responses"/(identity+".json"),4*1024*1024)
        if receipt["status"]!="received" or m.digest(raw)!=receipt["response_sha256"] or m.digest(raw)!=report["extraction"]["raw_response_sha256"]:
            raise ConflictError("Original extraction receipt changed")
        draft,_=_response(raw,profile.EXTRACTOR)
        if draft!=load(run/"artifacts"/(item["capture_id"]+".json")):
            raise ConflictError("Saved extraction differs from the original Terra response")
        source=load(history_root/"sources"/(item["source_sha256"]+".json"))
        if m.digest(source)!=item["source_sha256"] or source["input_sha256"]!=item["input_sha256"]:
            raise ConflictError("Saved source changed")
        entries.append({"source":source,"draft":draft,"parent_response_sha256":m.digest(raw)})
    return entries,load(run/"sample.json")["capture_ids"]


class TranscriptQualityResample:
    def __init__(self,root,pair_root,*,clock=m.now):
        self.root,self.pair_root,self.clock=Path(root),Path(pair_root),clock
        # _exchange works only on retained private inputs; it never opens these stores.
        self.pair=TranscriptModelPairPilot(None,None,self.pair_root,clock=clock)

    def prepare(self,entries,*,excluded_ids,count=5,seed=None):
        if type(count) is not int or not 1<=count<=5:raise ValidationError("Resample one to five reviews")
        seed=secrets.token_hex(32) if seed is None else seed
        if not isinstance(seed,str) or not re.fullmatch("[a-f0-9]{64}",seed):raise ValidationError("Invalid random seed")
        by_id={e["source"]["capture_id"]:e for e in entries}
        if len(by_id)!=len(entries):raise ValidationError("Duplicate source capture")
        candidates=sorted(set(by_id)-set(excluded_ids))
        if len(candidates)<count:raise ValidationError("Not enough fresh sample candidates")
        selected=sample_ids(candidates,seed,count)
        with job_lock(self.root):
            for name in ("plans","inputs","reports"):private_directory(self.root/name)
            rows=[]
            for capture in selected:
                entry=by_id[capture]
                if not re.fullmatch("[a-f0-9]{64}",entry["parent_response_sha256"]):
                    raise ValidationError("Original response hash is required")
                request=profile.make_request(entry["source"],analysis=entry["draft"])
                digest=m.digest(entry)
                atomic(self.root/"inputs"/(digest+".json"),entry)
                rows.append({"capture_id":capture,"input_sha256":digest,"request_sha256":m.digest(request)})
            plan={"contract":CONTRACT,"created_at":self.clock(),"configuration":profile.configuration_for(review=True),
                  "sample_seed":seed,"candidate_ids":candidates,"excluded_ids":sorted(excluded_ids),
                  "entries":rows,"max_requests":count,"extraction_calls":0,"automatic_retries":0,"publication":"private_only"}
            identifier=m.digest(plan)
            atomic(self.root/"plans"/(identifier+".json"),plan)
            return {"plan_id":identifier,**plan}

    def plan(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch("[a-f0-9]{64}",identifier):
            raise ValidationError("Invalid resample identifier")
        plan=load(self.root/"plans"/(identifier+".json"))
        if (m.digest(plan)!=identifier or plan["contract"]!=CONTRACT
                or plan["configuration"]!=profile.configuration_for(review=True)
                or not 1<=plan["max_requests"]<=5 or plan["extraction_calls"]!=0
                or plan["automatic_retries"]!=0 or plan["publication"]!="private_only"
                or len(plan["entries"])!=plan["max_requests"]
                or set(plan["candidate_ids"])&set(plan["excluded_ids"])
                or [e["capture_id"] for e in plan["entries"]]!=sample_ids(
                    plan["candidate_ids"],plan["sample_seed"],plan["max_requests"])):
            raise ConflictError("Frozen review-only resample differs")
        return plan

    def input(self,row):
        entry=load(self.root/"inputs"/(row["input_sha256"]+".json"))
        raw=profile.make_request(entry["source"],analysis=entry["draft"])
        if m.digest(entry)!=row["input_sha256"] or m.digest(raw)!=row["request_sha256"]:
            raise ConflictError("Frozen review input changed")
        return entry,raw

    def execute(self,identifier,transport_factory,*,on_progress=None):
        plan=self.plan(identifier)
        with job_lock(self.root),job_lock(self.pair_root):
            for name in ("requests","inputs","responses","outputs","reports"):private_directory(self.pair_root/name)
            for row in plan["entries"]:self.input(row)
            calls=0;records=[]
            for row in plan["entries"]:
                entry,request=self.input(row)
                target=self.root/"reports"/(identifier+"-"+row["capture_id"]+".json")
                if target.exists():
                    report=load(target)
                    if report.get("review",{}).get("raw_response_sha256"):
                        req=report["review"]["request_identity"]
                        raw=read_file(self.pair_root/"responses"/(req+".json"),4*1024*1024)
                        if m.digest(raw)!=report["review"]["raw_response_sha256"]:
                            raise ConflictError("Retained review response changed")
                else:
                    report={"capture_id":row["capture_id"],"requests_this_run":0,
                            "draft_assessment":profile.assess_brief(entry["draft"],entry["source"])}
                    try:
                        transport=transport_factory()
                        if transport.backend!=profile.BACKEND or transport._deadline_seconds()!=profile.REQUEST_TIMEOUT_SECONDS:
                            raise ValidationError("Review-only transport differs")
                        output,_=self.pair._exchange(request,stage="review",
                            parent_response_sha256=entry["parent_response_sha256"],identifier=identifier,
                            report=report,transport=transport,profile=profile)
                        checked=profile.validate_editorial_review(output,entry["draft"],entry["source"])
                        report.update(status="passed" if output["decision"]!="needs_attention" and
                            all(report["review"][k]=="passed" for k in ("runtime_validation","usage_validation"))
                            else "needs_attention",decision=output["decision"],final_assessment=checked,
                            draft_findings=output["draft_findings"],source_uncertainties=output["source_uncertainties"],
                            unresolved_issues=output["unresolved_issues"],editorial_notes=output["editorial_notes"])
                        atomic(self.root/"reports"/(identifier+"-"+row["capture_id"]+".review.json"),output)
                    except Exception as exc:
                        report.update(status="failed",**_error(exc))
                    calls+=report["requests_this_run"]
                    atomic(target,report)
                records.append(report)
                if on_progress:on_progress({"finished":len(records),"total":len(plan["entries"]),"new_calls":calls})
            result={"plan_id":identifier,"status":"completed","reviews_finished":len(records),
                    "reviews_passed":sum(r["status"]=="passed" for r in records),
                    "reviews_needing_attention":sum(r["status"]!="passed" for r in records),
                    "drafts_with_substantive_findings":sum(any(f["severity"]=="blocker" for f in r.get("draft_findings",[])) for r in records),
                    "drafts_with_only_nonblocking_findings":sum(bool(r.get("draft_findings")) and
                        not any(f["severity"]=="blocker" for f in r["draft_findings"]) for r in records),
                    "reviews_with_source_uncertainty":sum(bool(r.get("source_uncertainties")) or
                        any(f["severity"]=="uncertainty" for f in r.get("final_assessment",{}).get("findings",[])) for r in records),
                    "new_review_calls":calls,"historical_review_calls":sum(r["requests_this_run"] for r in records),
                    "extraction_calls":0,"records":records}
            atomic(self.root/"reports"/(identifier+".json"),result,replace=True)
            return result
