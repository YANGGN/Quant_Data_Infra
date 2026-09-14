"""Finite saved-history extraction with the established Terra/Sol profile and sampled review."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import secrets
import threading

from . import transcript_analysis as m
from .equibles_transcript_backfill import atomic, private_directory, read_file, job_lock
from .transcript_analysis_pair_pilot import TranscriptModelPairPilot, ROOT as PAIR_ROOT, ready_for_private_evaluation, _error
from ..company import transcript_structured_call_terra_sol as profile
from ..company.transcript_analysis import read_source
from ..company.transcript_analysis_contract import _schema
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import loads_strict
from ..stores import acquire_write_session

ROOT = m.STATE_ROOT / "history-batches"
CONTRACT = "transcript_history_sample_batch.v1"
MAX_CAPTURES = 2000


def load(path, bound=32 * 1024 * 1024):
    return loads_strict(read_file(path, bound).decode())


def sample_ids(items, seed, count):
    """Random hash ordering; seed and selection are persisted, never regenerated on resume."""
    return sorted(items, key=lambda c: m.digest({"seed": seed, "capture_id": c}))[:count]


def select_history(connection, ticker_count):
    if type(ticker_count) is not int or not 1 <= ticker_count <= 10:
        raise ValidationError("Select one to ten saved tickers")
    symbols = [r[0] for r in connection.execute(
        "SELECT DISTINCT symbol FROM company_equibles_transcripts ORDER BY symbol LIMIT ?", (ticker_count,))]
    rows = []
    for symbol in symbols:
        values = connection.execute("""
            SELECT capture_id,symbol,fiscal_year,fiscal_quarter,captured_at FROM (
              SELECT capture_id,symbol,fiscal_year,fiscal_quarter,captured_at,
                row_number() OVER (PARTITION BY fiscal_year,fiscal_quarter
                  ORDER BY captured_at DESC,capture_id DESC) AS rank
              FROM company_equibles_transcripts WHERE symbol=?
            ) WHERE rank=1 ORDER BY fiscal_year,fiscal_quarter""", (symbol,)).fetchall()
        rows.extend(dict(r) for r in values)
    if len(symbols) != ticker_count or not rows or len(rows) > MAX_CAPTURES:
        raise ValidationError("Saved history does not fit the finite selection")
    return symbols, rows


class TranscriptHistoryBatch:
    def __init__(self, stores, registry, root, pair_root, *, clock=m.now):
        self.stores, self.registry = stores, registry
        self.root, self.pair_root, self.clock = Path(root), Path(pair_root), clock
        self.pair = TranscriptModelPairPilot(stores, registry, self.pair_root, clock=clock)

    def prepare(self, *, ticker_count=10, sample_count=5, concurrency=3, seed=None):
        if type(sample_count) is not int or not 1 <= sample_count <= 5:
            raise ValidationError("Use one to five random sample reviews")
        if type(concurrency) is not int or not 1 <= concurrency <= 3:
            raise ValidationError("Use one to three bounded model workers")
        seed = secrets.token_hex(32) if seed is None else seed
        if not isinstance(seed, str) or not re.fullmatch("[a-f0-9]{64}", seed):
            raise ValidationError("Sampling seed differs")
        with job_lock(self.root):
            for name in ("plans", "sources", "runs"):
                private_directory(self.root/name)
            with acquire_write_session(self.stores, ("company",), timeout_seconds=60):
                ready_for_private_evaluation(self.stores, self.registry)
                with m.quiet_immutable_read_connection(self.stores, "company") as c:
                    symbols, rows = select_history(c, ticker_count)
                entries = []
                for row in rows:
                    item = dict(row)
                    try:
                        source = read_source(self.stores, row["capture_id"])
                        source_hash = m.digest(source)
                        atomic(self.root/"sources"/(source_hash+".json"), source)
                        item.update(source_sha256=source_hash, input_sha256=source["input_sha256"],
                                    source_page_hashes=source["source_page_hashes"],
                                    source_words=profile.word_budget(source)["source_words"],
                                    request_sha256=m.digest(profile.make_request(source)), ready=True)
                    except (ValidationError, ResourceLimitError) as exc:
                        item.update(ready=False, **_error(exc))
                    entries.append(item)
            plan = {"contract": CONTRACT, "created_at": self.clock(), "symbols": symbols,
                    "selection": "latest_saved_capture_per_source_labelled_quarter",
                    "entries": entries, "sample_seed": seed, "sample_count": sample_count,
                    "sampling": "uniform_without_replacement_from_schema_shaped_extractions",
                    "concurrency": concurrency, "extractor_configuration": profile.configuration_for(),
                    "reviewer_configuration": profile.configuration_for(review=True),
                    "max_extraction_calls": sum(e["ready"] for e in entries),
                    "max_review_calls": min(sample_count, sum(e["ready"] for e in entries)),
                    "publication": "private_only", "automatic_retries": 0}
            identifier = m.digest(plan)
            atomic(self.root/"plans"/(identifier+".json"), plan)
            return {"plan_id": identifier, **plan}

    def plan(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch("[a-f0-9]{64}", identifier):
            raise ValidationError("Invalid batch identifier")
        plan = load(self.root/"plans"/(identifier+".json"))
        if (m.digest(plan) != identifier or plan["contract"] != CONTRACT
                or plan["extractor_configuration"] != profile.configuration_for()
                or plan["reviewer_configuration"] != profile.configuration_for(review=True)
                or plan["publication"] != "private_only" or plan["automatic_retries"] != 0
                or not 1 <= plan["concurrency"] <= 3 or not 1 <= plan["sample_count"] <= 5
                or plan["max_extraction_calls"] != sum(e["ready"] for e in plan["entries"])
                or plan["max_review_calls"] != min(plan["sample_count"], plan["max_extraction_calls"])
                or len({e["capture_id"] for e in plan["entries"]}) != len(plan["entries"])):
            raise ConflictError("Frozen batch scope changed")
        return plan

    def source(self, entry):
        source = load(self.root/"sources"/(entry["source_sha256"]+".json"))
        if (m.digest(source) != entry["source_sha256"] or source["input_sha256"] != entry["input_sha256"]
                or source["capture_id"] != entry["capture_id"] or source["symbol"] != entry["symbol"]
                or m.digest(profile.make_request(source)) != entry["request_sha256"]):
            raise ConflictError("Frozen batch source or request changed")
        return source

    def aggregate(self, identifier):
        plan = self.plan(identifier)
        directory = self.root/"runs"/identifier
        records = [load(p) for p in sorted((directory/"extractions").glob("*.json"))]
        reviews = [load(p) for p in sorted((directory/"reviews").glob("*.json"))]
        valid = [r for r in records if r["status"] == "passed"]
        schema_shaped = [r for r in records if r.get("schema_shaped")]
        decisions = Counter(r.get("review", {}).get("verdict", "failed") for r in reviews)
        usable_reviews = [r for r in reviews if r["status"] == "passed"]
        calls = sum(r["requests_this_run"] for r in records+reviews)
        by_ticker = []
        for symbol in plan["symbols"]:
            chosen = [r for r in records if r["symbol"] == symbol]
            by_ticker.append({"symbol":symbol,"planned":sum(e["symbol"]==symbol for e in plan["entries"]),
                              "finished":len(chosen),"passed":sum(r["status"]=="passed" for r in chosen)})
        return {"plan_id":identifier,"at":self.clock(),"total_transcripts":len(plan["entries"]),
                "extractions_finished":len(records),"automated_passed":len(valid),
                "schema_shaped":len(schema_shaped),"extractions_needing_attention":len(records)-len(valid),
                "sample_reviews_finished":len(reviews),"sample_reviews_passed":len(usable_reviews),
                "sample_decisions":dict(decisions),
                "sample_unresolved_issues":sum(r.get("review",{}).get("unresolved_issue_count",0) for r in reviews),
                "mean_extracted_words":round(sum(r["extraction"]["length"]["word_count"] for r in valid)/len(valid),1) if valid else None,
                "model_calls":calls,"max_model_calls":plan["max_extraction_calls"]+plan["max_review_calls"],
                "by_ticker":by_ticker,"canonical_published":False}

    def execute(self, identifier, transport_factory, *, on_progress=None):
        plan = self.plan(identifier)
        directory = self.root/"runs"/identifier
        with job_lock(self.root), job_lock(self.pair_root):
            private_directory(directory)
            for name in ("extractions","reviews","artifacts"):
                private_directory(directory/name)
            for name in ("requests","inputs","responses","outputs","reports"):
                private_directory(self.pair_root/name)
            # Validate all frozen eligible inputs before any network activity.
            for entry in plan["entries"]:
                if entry["ready"]:
                    self.source(entry)
            stopped = threading.Event()
            gate = threading.Lock()
            failures = 0

            def one(entry, *, review=False):
                nonlocal failures
                capture = entry["capture_id"]
                kind = "reviews" if review else "extractions"
                target = directory/kind/(capture+".json")
                if target.exists():
                    return load(target)
                if stopped.is_set():
                    return None
                report = {"capture_id":capture,"symbol":entry["symbol"],"requests_this_run":0}
                stage = "review" if review else "extraction"
                if not entry["ready"]:
                    report.update(status="preflight_failed", **{k:entry[k] for k in ("error_type","reason","issues") if k in entry})
                    atomic(target,report)
                    return report
                source = self.source(entry)
                output_path = directory/"artifacts"/(capture+(".review.json" if review else ".json"))
                try:
                    parent = None
                    draft = None
                    if review:
                        extracted = load(directory/"extractions"/(capture+".json"))
                        parent = extracted["extraction"]["raw_response_sha256"]
                        draft = load(directory/"artifacts"/(capture+".json"))
                    request = profile.make_request(source, analysis=draft)
                    transport = transport_factory()
                    if (transport.backend != profile.BACKEND
                            or transport._deadline_seconds() != profile.REQUEST_TIMEOUT_SECONDS):
                        raise ValidationError("Batch transport differs from fixed profile")
                    output, _ = self.pair._exchange(request, stage=stage, parent_response_sha256=parent,
                        identifier=identifier, report=report, transport=transport, profile=profile)
                    atomic(output_path,output)
                    if review:
                        checked = profile.validate_editorial_review(output,draft,source)
                        report["review"].update(length=checked, verdict=output["decision"],
                            unresolved_issue_count=len(output["unresolved_issues"]),
                            editorial_notes=output["editorial_notes"])
                        usable = output["decision"] in {"approved","revised"}
                    else:
                        _schema(output,profile.brief_schema())
                        report["schema_shaped"] = True
                        checked = profile.validate_brief(output,source)
                        report["extraction"]["length"] = checked
                        usable = True
                        atomic(directory/"artifacts"/(capture+".md"),profile.render_brief(output).encode())
                    report[stage]["validation"] = "passed"
                    passed = (usable and report[stage]["runtime_validation"] == "passed"
                              and report[stage]["usage_validation"] == "passed")
                    report["status"] = "passed" if passed else "needs_attention"
                except Exception as exc:
                    report.update(status="failed", **_error(exc))
                    report["transport_failed"] = report.get(stage,{}).get("raw_response_sha256") is None
                atomic(target,report)
                with gate:
                    if report.get("transport_failed"):
                        failures += 1
                    elif report.get("schema_shaped") or report["status"] == "passed":
                        failures = 0
                    if failures >= 3:
                        stopped.set()
                return report

            def run(entries, *, review=False):
                nonlocal failures
                with ThreadPoolExecutor(max_workers=plan["concurrency"]) as pool:
                    futures = [pool.submit(one,e,review=review) for e in entries]
                    for future in as_completed(futures):
                        record = future.result()
                        if record is None:
                            continue
                        progress = self.aggregate(identifier)
                        progress["status"] = "stopped_after_transport_failures" if stopped.is_set() else "running"
                        atomic(directory/"progress.json",progress,replace=True)
                        if on_progress:
                            on_progress(progress)

            run(plan["entries"])
            selection_path = directory/"sample.json"
            if not stopped.is_set():
                candidates = [e["capture_id"] for e in plan["entries"]
                              if load(directory/"extractions"/(e["capture_id"]+".json")).get("schema_shaped")]
                expected = {"seed":plan["sample_seed"],"eligible_count":len(candidates),
                            "capture_ids":sample_ids(candidates,plan["sample_seed"],plan["sample_count"]),
                            "method":plan["sampling"]}
                if selection_path.exists():
                    selection = load(selection_path)
                    if selection != expected:
                        raise ConflictError("Frozen review sample differs")
                else:
                    selection = expected
                    atomic(selection_path,selection)
                selected = set(selection["capture_ids"])
                run([e for e in plan["entries"] if e["capture_id"] in selected],review=True)
            final = self.aggregate(identifier)
            final["status"] = "stopped_after_transport_failures" if stopped.is_set() else "completed"
            final["sampling_note"] = "Five random schema-shaped outputs at most; failed/unreadable extractions are reported separately. Review edits are not an accuracy percentage."
            atomic(directory/"result.json",final,replace=True)
            return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","execute","status"))
    parser.add_argument("--plan-id")
    args=parser.parse_args(argv)
    registry=m.load_registry(m.PROJECT_ROOT/"config/system_registry.json",project_root=m.PROJECT_ROOT,environment={})
    job=TranscriptHistoryBatch(m.fixed_stores(),registry,ROOT,PAIR_ROOT)
    if args.action == "prepare":
        result=job.prepare()
    elif args.action == "status":
        result=job.aggregate(args.plan_id)
    else:
        from ..company.transcript_analysis_codex import CodexTranscriptTransport
        result=job.execute(args.plan_id,lambda:CodexTranscriptTransport(
            evidence_root=m.STATE_ROOT/"codex",timeout_seconds=profile.REQUEST_TIMEOUT_SECONDS),
            on_progress=lambda value:print(m.dumps_strict(value),flush=True))
    print(m.dumps_strict(result),flush=True)
    return 0 if result.get("status","completed") == "completed" else 75


if __name__=="__main__":
    raise SystemExit(main())
