"""One-call private Sol/high extraction pilot; no canonical publication or review."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import transcript_analysis as m
from .equibles_transcript_backfill import atomic, private_directory, read_file, job_lock
from ..company.transcript_analysis import read_source, _response
from ..company.transcript_analysis_contract import validate_analysis
from ..company.transcript_analysis_model import (
    configuration_for, make_request, request_identity_for, REVIEWER,
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, output_schema_version, PROMPT_VERSION,
)
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..json_codec import loads_strict, dumps_strict

ROOT = m.STATE_ROOT / "extractor-pilots"
CONTRACT = "transcript_extraction_private_pilot.v1"


class TranscriptExtractionPilot:
    def __init__(self, stores, registry, root, *, clock=m.now):
        self.stores, self.registry, self.root, self.clock = stores, registry, Path(root), clock

    @staticmethod
    def configuration(prompt_version=PROMPT_VERSION):
        return configuration_for(backend="codex_subscription", extractor_model=REVIEWER, prompt_version=prompt_version)

    def prepare(self, *, capture_id):
        m.ready(self.stores, self.registry)
        source = read_source(self.stores, capture_id)
        request = make_request(source, extractor_model=REVIEWER)
        plan = {"contract":CONTRACT, "created_at":self.clock(), "capture_id":capture_id,
            "symbol":source["symbol"], "input_sha256":source["input_sha256"],
            "configuration":self.configuration(), "backend":"codex_subscription",
            "publication":"private_only", "max_requests":1, "request_sha256":m.digest(request)}
        identifier = m.digest(plan)
        with job_lock(self.root):
            private_directory(self.root/"plans")
            atomic(self.root/"plans"/(identifier+".json"), plan)
        return {"plan_id":identifier, **plan, "model_requests_made":0}

    def _plan(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValidationError("Invalid private pilot identifier")
        plan = loads_strict(read_file(self.root/"plans"/(identifier+".json"), 65536).decode())
        keys = {"contract","created_at","capture_id","symbol","input_sha256","configuration",
                "backend","publication","max_requests","request_sha256"}
        if (not isinstance(plan, dict) or set(plan)!=keys or m.digest(plan)!=identifier
                or plan["contract"]!=CONTRACT or not isinstance(plan["configuration"],dict)
                or plan["configuration"]!=self.configuration(plan["configuration"].get("prompt_version"))
                or plan["backend"]!="codex_subscription" or plan["publication"]!="private_only"
                or type(plan["max_requests"]) is not int or plan["max_requests"]!=1):
            raise ValidationError("Private pilot scope or configuration differs")
        return plan

    def execute(self, identifier, transport):
        m.ready(self.stores, self.registry)
        with job_lock(self.root):
            plan = self._plan(identifier)
            if (getattr(transport,"backend",None)!="codex_subscription"
                    or not callable(getattr(transport,"_deadline_seconds",None))
                    or transport._deadline_seconds()!=plan["configuration"]["request_timeout_seconds"]):
                raise ValidationError("Private pilot transport differs from its backend or deadline")
            source = read_source(self.stores, plan["capture_id"])
            if source["input_sha256"]!=plan["input_sha256"] or source["symbol"]!=plan["symbol"]:
                raise ConflictError("Private pilot source changed")
            version = plan["configuration"]["prompt_version"]
            raw_request = make_request(source, extractor_model=REVIEWER, prompt_version=version)
            if m.digest(raw_request)!=plan["request_sha256"]:
                raise ConflictError("Private pilot request changed")
            req = request_identity_for(source, backend="codex_subscription", extractor_model=REVIEWER, prompt_version=version)
            for name in ("requests","inputs","responses","reports"):
                private_directory(self.root/name)
            receipt_path = self.root/"requests"/(req+".json")
            response_path = self.root/"responses"/(req+".json")
            receipt = loads_strict(read_file(receipt_path,65536).decode()) if receipt_path.exists() else None
            report = {"plan_id":identifier,"request_identity":req,"model":REVIEWER,"reasoning_effort":"high",
                      "publication":"private_only","canonical_published":False,"requests_this_run":0}
            try:
                if receipt is not None:
                    if (receipt.get("request_identity")!=req or receipt.get("request_sha256")!=m.digest(raw_request)
                            or receipt.get("status")!="received" or not response_path.exists()):
                        raise ConflictError("Prior pilot attempt differs, failed or is uncertain; no automatic retry")
                    raw = read_file(response_path, MAX_RESPONSE_BYTES)
                    if receipt.get("response_sha256")!=m.digest(raw):
                        raise ConflictError("Retained pilot response changed")
                else:
                    transport.prepare_credentials()
                    atomic(self.root/"inputs"/(req+".json"), raw_request)
                    receipt = {"request_identity":req,"request_sha256":m.digest(raw_request),
                        "plan_id":identifier,"backend":"codex_subscription","model":REVIEWER,
                        "status":"pending","started_at":self.clock(),"publication":"private_only"}
                    atomic(receipt_path, receipt)
                    report["requests_this_run"] = 1
                    try:
                        raw = transport.request(raw_request)
                        if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_RESPONSE_BYTES:
                            raise ResourceLimitError("Private pilot response exceeds its byte bound")
                        atomic(response_path, raw)
                        receipt.update(status="received",completed_at=self.clock(),response_sha256=m.digest(raw))
                        atomic(receipt_path, receipt, replace=True)
                    except Exception:
                        receipt.update(status="failed_or_uncertain",completed_at=self.clock())
                        atomic(receipt_path, receipt, replace=True)
                        raise
                output, usage = _response(raw, REVIEWER)
                report["usage"] = usage
                if usage["input_tokens"]>MAX_REQUEST_BYTES or usage["output_tokens"]>plan["configuration"]["max_output_tokens"]:
                    raise ResourceLimitError("Reported pilot usage exceeds the existing token bound")
                if not isinstance(output,dict) or output.get("schema_version")!=output_schema_version(
                        prompt_version=plan["configuration"]["prompt_version"]):
                    raise ValidationError("Output schema version differs from the pinned pilot request")
                checked = validate_analysis(output, source["turns"], processing_coverage="complete",
                    courtesy_policy=plan["configuration"].get("question_inventory_policy","courtesy_only.v1"))
                # Only private metadata is written: no publisher, coordinator or database writer.
                report.update(outcome="validated_private",derived=checked["derived"],
                              guidance_claim_count=len(output["guidance_claims"]))
            except Exception as exc:
                report.update(outcome="validation_failed" if isinstance(exc,ValidationError) else "blocked",
                              error_type=type(exc).__name__)
                if isinstance(exc, (ValidationError,ResourceLimitError,ConflictError)):
                    report["reason"] = str(exc)
                    report["issues"] = [issue.to_dict() for issue in exc.issues]
                atomic(self.root/"reports"/(identifier+".json"),report,replace=True)
                if report["outcome"]!="validation_failed":
                    raise
                return report
            atomic(self.root/"reports"/(identifier+".json"),report,replace=True)
            return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    prepare=sub.add_parser("prepare",help="Freeze one stored capture; no credentials or model calls")
    prepare.add_argument("--capture-id",required=True)
    execute=sub.add_parser("execute",help="Run or replay one approved private Sol extraction")
    execute.add_argument("--plan-id",required=True)
    args=parser.parse_args(argv)
    registry=m.load_registry(m.PROJECT_ROOT/"config/system_registry.json",
                             project_root=m.PROJECT_ROOT,environment={})
    job=TranscriptExtractionPilot(m.fixed_stores(),registry,ROOT)
    try:
        if args.action=="prepare":
            result=job.prepare(capture_id=args.capture_id)
        else:
            from ..company.transcript_analysis_codex import CodexTranscriptTransport
            job._plan(args.plan_id)
            transport=CodexTranscriptTransport(evidence_root=m.STATE_ROOT/"codex",timeout_seconds=1200)
            result=job.execute(args.plan_id,transport)
        print(dumps_strict(result))
        return 0 if result.get("outcome","validated_private")=="validated_private" else 75
    except Exception as exc:
        print(dumps_strict({"error":"private_transcript_pilot_failed","error_type":type(exc).__name__}))
        return 75


if __name__=="__main__":
    raise SystemExit(main())
