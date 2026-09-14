"""Private one-capture Sol/xhigh extraction and Astra/xhigh review; at most two calls."""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from . import transcript_analysis as m
from .equibles_transcript_backfill import atomic, private_directory, read_file, job_lock
from ..company import transcript_analysis_pair as pair
from ..company import transcript_call_brief as brief
from ..company import transcript_structured_call as structured
from ..company import transcript_structured_call_terra_sol as terra_sol
from ..company import transcript_structured_quality as tiered
from ..company.transcript_analysis import read_source, _response
from ..company.transcript_analysis_contract import validate_analysis, analysis_schema, _schema, ANALYSIS_V3
from ..company.transcript_analysis_model import MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..json_codec import loads_strict, dumps_strict

ROOT = m.STATE_ROOT / "model-pair-pilots"
CONTRACT = "transcript_model_pair_private_pilot.v1"


def ready_for_private_evaluation(stores, registry):
    """Read an exact installed company prefix; do not require unrelated pending writes."""
    expected = m._migration_rows(registry)
    declaration = next(d for d in registry.datasets_for("company") if d.id == m.DATASET)
    with m.quiet_immutable_read_connection(stores, "company") as c:
        actual = m._stored_migrations(c)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        dataset = c.execute(
            "SELECT store_role,layer,schema_version,relations_json,active FROM dataset_registry WHERE dataset_id=?",
            (m.DATASET,)).fetchone()
        identity = c.execute(
            "SELECT identity_sha256 FROM dataset_identity_contracts WHERE dataset_id=?", (m.DATASET,)).fetchone()
    expected_dataset = (declaration.store, declaration.layer, declaration.schema_version,
                        dumps_strict(list(declaration.relations)), int(declaration.active))
    if (actual != expected[:len(actual)] or not any(row[0] == m.MIGRATION_ID for row in actual)
            or not set(m.TABLES) <= names or dataset is None or tuple(dataset) != expected_dataset
            or identity is None or identity[0] != declaration.identity_sha256):
        raise ValidationError("Private transcript schema or installed migration prefix differs; no model request was made")


def _error(exc):
    result = {"error_type": type(exc).__name__}
    if isinstance(exc, (ValidationError, ConflictError, ResourceLimitError)):
        result.update(reason=str(exc), issues=[issue.to_dict() for issue in exc.issues])
    return result


class TranscriptModelPairPilot:
    def __init__(self, stores, registry, root, *, clock=m.now):
        self.stores, self.registry, self.root, self.clock = stores, registry, Path(root), clock

    def prepare(self, *, capture_id, output_format="structured"):
        if output_format not in {"structured", "structured_terra_sol", "structured_tiered", "call_brief", "detailed"}:
            raise ValidationError("Unknown private transcript output format")
        profile = {"structured": structured, "structured_terra_sol": terra_sol, "structured_tiered": tiered,
                   "call_brief": brief, "detailed": pair}[output_format]
        ready_for_private_evaluation(self.stores, self.registry)
        source = read_source(self.stores, capture_id)
        plan = {
            "contract": CONTRACT, "created_at": self.clock(),
            "capture_id": capture_id, "symbol": source["symbol"], "input_sha256": source["input_sha256"],
            "configuration": profile.configuration_for(), "review_configuration": profile.configuration_for(review=True),
            "backend": profile.BACKEND, "publication": "private_only", "max_requests": 2,
            "review_rejected_draft": True, "request_sha256": m.digest(profile.make_request(source)),
        }
        identifier = m.digest(plan)
        with job_lock(self.root):
            private_directory(self.root / "plans")
            atomic(self.root / "plans" / (identifier + ".json"), plan)
        return {"plan_id": identifier, **plan, "model_requests_made": 0}

    @staticmethod
    def _profile(plan):
        version = plan.get("configuration", {}).get("prompt_version")
        if version == tiered.VERSION:
            return tiered
        if version == terra_sol.VERSION:
            return terra_sol
        if version == structured.VERSION:
            return structured
        if version == brief.VERSION:
            return brief
        if version == pair.VERSION:
            return pair
        raise ValidationError("Unknown private transcript profile")

    def _plan(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValidationError("Invalid paired pilot identifier")
        plan = loads_strict(read_file(self.root / "plans" / (identifier + ".json"), 65536).decode())
        if not isinstance(plan, dict) or not isinstance(plan.get("configuration"), dict):
            raise ValidationError("Invalid paired plan shape")
        profile = self._profile(plan)
        keys = {"contract", "created_at", "capture_id", "symbol", "input_sha256",
                "configuration", "review_configuration", "backend", "publication",
                "max_requests", "review_rejected_draft", "request_sha256"}
        if (not isinstance(plan, dict) or set(plan) != keys or m.digest(plan) != identifier
                or plan["contract"] != CONTRACT or plan["configuration"] != profile.configuration_for()
                or plan["review_configuration"] != profile.configuration_for(review=True)
                or plan["backend"] != profile.BACKEND or plan["publication"] != "private_only"
                or type(plan["max_requests"]) is not int or plan["max_requests"] != 2
                or plan["review_rejected_draft"] is not True):
            raise ValidationError("Paired pilot scope or configuration differs")
        return plan

    def _exchange(self, raw_request, *, stage, parent_response_sha256, identifier, report, transport, profile=pair):
        config = profile.configuration_for(review=stage == "review")
        req = m.digest({"contract": CONTRACT, "configuration": config,
                        "request_sha256": m.digest(raw_request), "parent_response_sha256": parent_response_sha256})
        receipt_path = self.root / "requests" / (req + ".json")
        response_path = self.root / "responses" / (req + ".json")
        receipt = loads_strict(read_file(receipt_path, 65536).decode()) if receipt_path.exists() else None
        item = {"request_identity": req, "model": config["model"], "reasoning_effort": config["reasoning_effort"]}
        report[stage] = item
        if receipt is not None:
            if (receipt.get("request_identity") != req or receipt.get("request_sha256") != m.digest(raw_request)
                    or receipt.get("parent_response_sha256") != parent_response_sha256
                    or receipt.get("status") != "received" or not response_path.exists()):
                raise ConflictError("Prior paired attempt differs, failed or is uncertain; no automatic retry")
            raw = read_file(response_path, MAX_RESPONSE_BYTES)
            if receipt.get("response_sha256") != m.digest(raw):
                raise ConflictError("Retained paired response changed")
        else:
            transport.prepare_credentials()
            atomic(self.root / "inputs" / (req + ".json"), raw_request)
            receipt = {"request_identity": req, "request_sha256": m.digest(raw_request),
                       "parent_response_sha256": parent_response_sha256, "stage": stage,
                       "plan_id": identifier, "backend": profile.BACKEND, "configuration": config,
                       "status": "pending", "started_at": self.clock(), "publication": "private_only"}
            atomic(receipt_path, receipt)
            report["requests_this_run"] += 1
            try:
                raw = transport.request(raw_request)
                if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RESPONSE_BYTES:
                    raise ResourceLimitError("Paired response exceeds its byte bound")
                atomic(response_path, raw)
                receipt.update(status="received", completed_at=self.clock(), response_sha256=m.digest(raw))
                atomic(receipt_path, receipt, replace=True)
            except BaseException:
                receipt.update(status="failed_or_uncertain", completed_at=self.clock())
                atomic(receipt_path, receipt, replace=True)
                raise
        item["raw_response_sha256"] = m.digest(raw)
        output, usage = _response(raw, config["model"])
        item["usage"] = usage
        if profile is not pair:
            evidence = loads_strict(raw.decode()).get("transport_evidence", {})
            fallback = (evidence.get("model_metadata_status") == "fallback"
                        or evidence.get("effective_reasoning_effort_verified") is False)
            item["runtime_validation"] = "failed" if fallback else "passed"
            if fallback:
                item["runtime_warning"] = "Native runtime reported fallback model metadata; effective effort is unverified"
        if usage["input_tokens"] > MAX_REQUEST_BYTES:
            raise ResourceLimitError("Reported paired input usage exceeds its token bound")
        # Subscription output limits are validated after completion. A retained,
        # schema-shaped over-limit draft may still receive the one planned private
        # review, but neither that draft nor the overall pair can pass the limit.
        item["usage_validation"] = "passed" if usage["output_tokens"] <= config["max_output_tokens"] else "failed"
        if item["usage_validation"] == "failed":
            item["usage_error"] = "Reported output usage exceeds the pinned post-completion token limit"
        atomic(self.root / "outputs" / (req + ".json"), output, replace=True)
        return output, m.digest(raw)

    def execute(self, identifier, transport):
        ready_for_private_evaluation(self.stores, self.registry)
        with job_lock(self.root):
            plan = self._plan(identifier)
            profile = self._profile(plan)
            if (getattr(transport, "backend", None) != profile.BACKEND
                    or not callable(getattr(transport, "_deadline_seconds", None))
                    or transport._deadline_seconds() != profile.REQUEST_TIMEOUT_SECONDS):
                raise ValidationError("Paired transport differs from its backend or deadline")
            source = read_source(self.stores, plan["capture_id"])
            raw_request = profile.make_request(source)
            if (source["input_sha256"] != plan["input_sha256"] or source["symbol"] != plan["symbol"]
                    or m.digest(raw_request) != plan["request_sha256"]):
                raise ConflictError("Paired pilot source or request changed")
            for name in ("requests", "inputs", "responses", "outputs", "reports"):
                private_directory(self.root / name)
            report = {"plan_id": identifier, "publication": "private_only",
                      "canonical_published": False, "requests_this_run": 0, "max_requests": 2}
            try:
                draft, parent = self._exchange(raw_request, stage="extraction",
                    parent_response_sha256=None, identifier=identifier, report=report, transport=transport, profile=profile)
                if profile is not pair:
                    passed = self._finish_brief(source, draft, parent, identifier, report, transport, profile=profile)
                else:
                    # Review well-formed drafts even when semantic validation rejects them.
                    _schema(draft, analysis_schema(ANALYSIS_V3))
                    try:
                        checked = validate_analysis(draft, source["turns"], processing_coverage="complete",
                                                    courtesy_policy=pair.COURTESY_POLICY)
                        report["extraction"].update(validation="passed", derived=checked["derived"],
                                                    guidance_claim_count=len(draft["guidance_claims"]))
                    except ValidationError as exc:
                        report["extraction"].update(validation="failed", **_error(exc))
                    review, _ = self._exchange(pair.make_request(source, analysis=draft), stage="review",
                        parent_response_sha256=parent, identifier=identifier, report=report, transport=transport, profile=profile)
                    try:
                        pair.validate_draft_review(review, draft, source["turns"])
                        report["review"].update(validation="passed", verdict=review["verdict"],
                                                 finding_count=len(review["findings"]))
                    except ValidationError as exc:
                        report["review"].update(validation="failed", **_error(exc))
                    passed = (report["extraction"]["validation"] == "passed"
                              and report["extraction"]["usage_validation"] == "passed"
                              and report["review"]["validation"] == "passed"
                              and report["review"]["usage_validation"] == "passed"
                              and report["review"]["verdict"] == "accepted")
                report["outcome"] = "validated_private" if passed else "needs_changes"
            except Exception as exc:
                report.update(outcome="blocked", **_error(exc))
                atomic(self.root / "reports" / (identifier + ".json"), report, replace=True)
                raise
            atomic(self.root / "reports" / (identifier + ".json"), report, replace=True)
            return report

    def _finish_brief(self, source, draft, parent, identifier, report, transport, *, profile):
        _schema(draft, profile.brief_schema())
        try:
            checked = profile.validate_brief(draft, source)
            report["extraction"].update(validation="passed", length=checked)
        except ValidationError as exc:
            report["extraction"].update(validation="failed", **_error(exc))
        review, _ = self._exchange(profile.make_request(source, analysis=draft), stage="review",
            parent_response_sha256=parent, identifier=identifier, report=report,
            transport=transport, profile=profile)
        try:
            checked = profile.validate_editorial_review(review, draft, source)
            report["review"].update(validation="passed", verdict=review["decision"], length=checked,
                                    unresolved_issue_count=len(review["unresolved_issues"]))
        except ValidationError as exc:
            report["review"].update(validation="failed", **_error(exc))
            return False
        passed = (review["decision"] in {"approved", "revised"}
                  and all(report[stage]["usage_validation"] == "passed"
                          and report[stage]["runtime_validation"] == "passed"
                          for stage in ("extraction", "review")))
        # The corrected candidate remains inspectable even when review or runtime
        # quality gates need attention. Its status is explicit in the audit file.
        directory = self.root / "artifacts" / identifier
        private_directory(self.root / "artifacts")
        private_directory(directory)
        for name, value in (
            ("draft.json", draft), ("final-candidate.json", review["brief"]),
            ("call-brief.md", profile.render_brief(review["brief"]).encode()),
            ("audit.json", {"capture_id": source["capture_id"], "symbol": source["symbol"],
                           "fiscal_year": source["fiscal_year"], "fiscal_quarter": source["fiscal_quarter"],
                           "input_sha256": source["input_sha256"], "source_page_hashes": source["source_page_hashes"],
                           "source_warnings": source["warnings"], "profile": profile.VERSION,
                           "status": "validated_private" if passed else "needs_attention",
                           "decision": review["decision"], "length": checked,
                           "extraction_checks": report["extraction"], "review_checks": report["review"],
                           "editorial_notes": review["editorial_notes"],
                           "unresolved_issues": review["unresolved_issues"]})):
            atomic(directory / name, value, replace=True)
        report["brief_artifact"] = str(directory / "call-brief.md")
        report["brief_audit"] = str(directory / "audit.json")
        return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare", help="Freeze one stored capture without model calls")
    prepare.add_argument("--capture-id", required=True)
    prepare.add_argument("--output-format", choices=("structured", "structured_terra_sol", "structured_tiered", "call_brief", "detailed"), default="structured")
    execute = sub.add_parser("execute", help="Run or replay the approved private pair")
    execute.add_argument("--plan-id", required=True)
    args = parser.parse_args(argv)
    registry = m.load_registry(m.PROJECT_ROOT / "config/system_registry.json",
                               project_root=m.PROJECT_ROOT, environment={})
    job = TranscriptModelPairPilot(m.fixed_stores(), registry, ROOT)
    try:
        if args.action == "prepare":
            result = job.prepare(capture_id=args.capture_id, output_format=args.output_format)
        else:
            from ..company.transcript_analysis_codex import CodexTranscriptTransport
            job._plan(args.plan_id)
            transport = CodexTranscriptTransport(evidence_root=m.STATE_ROOT / "codex",
                                                 timeout_seconds=pair.REQUEST_TIMEOUT_SECONDS)
            result = job.execute(args.plan_id, transport)
        print(dumps_strict(result))
        return 0 if result.get("outcome", "validated_private") == "validated_private" else 75
    except Exception as exc:
        print(dumps_strict({"error": "paired_transcript_pilot_failed", **_error(exc)}))
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
