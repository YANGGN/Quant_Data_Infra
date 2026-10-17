"""Bounded retained-source reads and immutable company-owned transcript analysis."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from uuid import uuid4

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id
from .equibles_transcripts import RawPage, validate_bundle, validate_symbol, utc
from .transcript_analysis_contract import validate_analysis, validate_review, question_inventory

DATASET = "company.transcript.analysis"
VERSION = "transcript_analysis.v1"
PARSER_VERSION = "equibles_turns.v2"
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_CAPTURE = re.compile(r"equibles_transcript_[a-f0-9]{32}\Z")
_ANALYSIS = re.compile(r"transcript_analysis_[a-f0-9]{32}\Z")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else dumps_strict(value).encode()).hexdigest()


def _identifier(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError("Transcript analysis identifier is invalid")
    return value


def _role(value, name):
    if not isinstance(value, str):
        return "operator" if name and name.strip().casefold() == "operator" else "unknown"
    normalized = re.sub(r"[^a-z]+", " ", value.casefold()).strip()
    if normalized in {"operator", "coordinator", "moderator"}:
        return "operator"
    if re.search(r"\banalyst\b", normalized):
        return "analyst"
    if normalized in {"management", "executive", "company representative", "ceo", "cfo", "coo", "cto", "cio", "ir"} or re.search(r"\b(chief|president|investor relations|vice president)\b", normalized):
        return "management"
    return "unknown"



def _speaker_id(name):
    if not isinstance(name, str) or not name.strip():
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    placeholder = (r"(?:(?:unknown|unidentified|unspecified|unnamed|anonymous)"
                   r"(?: (?:analyst|speaker|participant|executive|management|questioner))?"
                   r"|analyst|speaker|participant|questioner|n a|na)(?: [0-9]+)?")
    if re.fullmatch(placeholder, normalized):
        return None
    return "s_" + digest(name.encode())[:16]


def read_source(stores, capture_id):
    """Host-owned stores only; retain all original turn text, roles and source pointers."""
    _identifier(capture_id, _CAPTURE)
    with quiet_immutable_read_connection(stores, StoreRole.COMPANY) as c:
        found = c.execute("SELECT * FROM company_equibles_transcripts WHERE capture_id=?", (capture_id,)).fetchone()
        if found is None:
            raise ValidationError("Stored transcript capture does not exist")
        metadata = dict(found)
        sizes = c.execute("SELECT count(*),coalesce(sum(length(raw_body)),0) FROM company_equibles_transcript_pages WHERE capture_id=?", (capture_id,)).fetchone()
        if sizes[0] != metadata["page_count"] or not 1 <= sizes[1] <= MAX_SOURCE_BYTES:
            raise ResourceLimitError("Stored transcript source exceeds its complete-call bound")
        pages = [dict(row) for row in c.execute("SELECT * FROM company_equibles_transcript_pages WHERE capture_id=? ORDER BY page_index", (capture_id,))]
    if [p["page_index"] for p in pages] != list(range(len(pages))):
        raise ValidationError("Stored transcript page indexes are incomplete")
    for page in pages:
        if digest(page["raw_body"]) != page["content_sha256"]:
            raise ValidationError("Stored transcript page hash differs")
    event = loads_strict(metadata["event_json"])
    parsed = validate_bundle(metadata["symbol"], metadata["instrument_id"], event,
        tuple(RawPage(p["raw_body"], p["captured_at"], p["source_reference"], p["headers_json"]) for p in pages))
    turns = []
    for page, value in zip(pages, parsed):
        if page["turn_offset"] != value["offset"] or page["turn_count"] != value["turnCount"]:
            raise ValidationError("Stored transcript offsets differ from evidence")
        for i, row in enumerate(value["data"]):
            name = row.get("speakerName") if isinstance(row.get("speakerName"), str) else None
            role = _role(row.get("speakerRole"), name)
            turns.append({"turn_id": "t" + str(len(turns)+1),
                "speaker_id": _speaker_id(name),
                "speaker_role": role, "speaker_name": name, "source_speaker_role": row.get("speakerRole"),
                "section": "unknown", "text": row["text"], "page_index": page["page_index"],
                "json_pointer": "/data/" + str(i) + "/text", "page_sha256": page["content_sha256"]})
    if len(turns) != metadata["total_turn_count"]:
        raise ValidationError("Stored transcript turn count differs")
    # Propagate only an unambiguous role supplied for that same named speaker.
    roles = {}
    for t in turns:
        if t["speaker_id"] and t["speaker_role"] != "unknown":
            roles.setdefault(t["speaker_id"], set()).add(t["speaker_role"])
    for t in turns:
        known = roles.get(t["speaker_id"], set())
        if len(known) == 1 and t["speaker_role"] == "unknown":
            t["speaker_role"] = next(iter(known))
        elif len(known) > 1:
            t["speaker_role"] = "unknown"
    boundary = next((i for i,t in enumerate(turns) if t["speaker_role"] == "analyst" or
        (t["speaker_role"] == "operator" and re.search(r"(?:first|next) question|question.and.answer|begin.*questions", t["text"], re.I))), None)
    for i,t in enumerate(turns):
        t["section"] = ("prepared_remarks" if i < boundary else "qa") if boundary is not None else "unknown"
    source = {"capture_id": capture_id, "symbol": metadata["symbol"], "instrument_id": metadata["instrument_id"],
        "fiscal_year": metadata["fiscal_year"], "fiscal_quarter": metadata["fiscal_quarter"],
        "source_available_at": utc(metadata["captured_at"]), "parser_version": PARSER_VERSION,
        "source_page_hashes": [p["content_sha256"] for p in pages], "turns": turns,
        "warnings": ["source_call_date_unverified", "retrospective_local_capture", "section_boundary_rule_v1"]}
    if any(t["speaker_role"] == "unknown" or t["section"] == "unknown" for t in turns):
        source["warnings"].append("unresolved_speaker_role_or_section")
    if any(t["speaker_role"] in {"analyst","management"} and t["speaker_id"] is None for t in turns):
        source["warnings"].append("unresolved_speaker_identity")
    source["question_inventory"] = question_inventory(turns)
    source["input_sha256"] = digest(source)
    return source


def select_captures(stores, *, symbol, limit, offset=0):
    validate_symbol(symbol)
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValidationError("Select between one and ten transcript captures")
    if type(offset) is not int or not 0 <= offset <= 1000:
        raise ValidationError("Transcript selection offset is invalid")
    with quiet_immutable_read_connection(stores, StoreRole.COMPANY) as c:
        rows = c.execute("SELECT capture_id FROM company_equibles_transcripts WHERE symbol=? ORDER BY fiscal_year DESC,fiscal_quarter DESC,captured_at DESC,capture_id LIMIT ? OFFSET ?", (symbol, limit, offset)).fetchall()
    if not rows:
        raise ValidationError("No stored transcript captures match the symbol")
    return [row[0] for row in rows]


def _spans(resolved, source):
    result = deepcopy(resolved)
    by_id = {t["turn_id"]:t for t in source["turns"]}
    for span in result:
        turn = by_id[span["turn_id"]]
        span.update(capture_id=source["capture_id"], page_index=turn["page_index"],
                    json_pointer=turn["json_pointer"], page_sha256=turn["page_sha256"])
    return result


def _response(raw, expected_model):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RESPONSE_BYTES:
        raise ResourceLimitError("Model response exceeds its byte bound")
    value = loads_strict(raw.decode("utf-8"))
    if not isinstance(value, dict) or value.get("status") != "completed" or value.get("error") or value.get("incomplete_details"):
        raise ValidationError("Model response did not complete")
    if value.get("model") != expected_model:
        raise ValidationError("Returned model differs from requested model")
    texts = []
    for item in value.get("output", []):
        if not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"}:
            raise ValidationError("Unexpected model output item")
        if item["type"] == "reasoning":
            continue
        if item.get("role") != "assistant" or item.get("status", "completed") != "completed":
            raise ValidationError("Model message did not complete")
        for part in item.get("content", []):
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ValidationError("Model response was refused or malformed")
            texts.append(part["text"])
    if len(texts) != 1:
        raise ValidationError("Model must return one structured output")
    output = loads_strict(texts[0])
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens", "total_tokens")):
        raise ValidationError("Model usage is missing or invalid")
    if usage["input_tokens"]+usage["output_tokens"] != usage["total_tokens"]:
        raise ValidationError("Model token totals differ")
    return output, usage


class TranscriptAnalysisRepository:
    def __init__(self, stores): self.stores = stores

    def find_request(self, request_identity, *, review=False):
        if not re.fullmatch(r"[a-f0-9]{64}", request_identity):
            raise ValidationError("Analysis request identity is invalid")
        table = "company_transcript_analysis_reviews" if review else "company_transcript_analyses"
        with quiet_immutable_read_connection(self.stores, StoreRole.COMPANY) as c:
            row = c.execute("SELECT * FROM " + table + " WHERE request_identity=?", (request_identity,)).fetchone()
        return None if row is None else dict(row)

    def get(self, analysis_id, *, as_of):
        _identifier(analysis_id, _ANALYSIS)
        cutoff = utc(as_of)
        with quiet_immutable_read_connection(self.stores, StoreRole.COMPANY) as c:
            row = c.execute("SELECT * FROM company_transcript_analyses WHERE analysis_id=? AND available_at<=?", (analysis_id, cutoff)).fetchone()
            if row is None: return None
            result = dict(row)
            reviews = [dict(r) for r in c.execute("SELECT review_id,model,reasoning_effort,verdict,available_at,output_json,evidence_json FROM company_transcript_analysis_reviews WHERE analysis_id=? AND available_at<=? ORDER BY available_at,review_id", (analysis_id, cutoff))]
        configuration = loads_strict(result["configuration_json"])
        source = loads_strict(result["source_json"])
        inventory = (question_inventory(source["turns"], courtesy_policy=configuration["question_inventory_policy"])
                     if "question_inventory_policy" in configuration else source.get("question_inventory"))
        return {"analysis_id":analysis_id, "capture_id":result["capture_id"], "available_at":result["available_at"],
            "source_available_at":result["source_available_at"], "model":result["model"], "reasoning_effort":result["reasoning_effort"],
            "source_warnings":loads_strict(result["source_json"])["warnings"],
            "source_question_inventory":inventory,
            "output":loads_strict(result["output_json"]), "derived":loads_strict(result["derived_json"]),
            "evidence":loads_strict(result["evidence_json"]),
            "reviews":[{**{k:v for k,v in r.items() if not k.endswith("_json")}, "output":loads_strict(r["output_json"]), "evidence":loads_strict(r["evidence_json"])} for r in reviews],
            "interpretation":"derived_model_analysis_not_reported_fact"}


class TranscriptAnalysisPublisher:
    def __init__(self, stores, registry):
        if DATASET not in {d.id for d in registry.datasets_for("company")}:
            raise ValidationError("Transcript analysis dataset is not registered")
        self.stores = stores
        self.repository = TranscriptAnalysisRepository(stores)
        self.coordinator = IngestionCoordinator(stores, code_version=VERSION)

    def publish(self, *, source, request_identity, configuration, raw_response, started_at, completed_at, analysis_id=None):
        review = analysis_id is not None
        expected_model = "gpt-5.6-sol" if review else "gpt-5.6-terra"
        from .transcript_analysis_model import configuration_for, request_identity_for, output_schema_version
        backend = configuration.get("backend","openai_api") if isinstance(configuration,dict) else None
        prompt_version = configuration.get("prompt_version") if isinstance(configuration,dict) else None
        if configuration != configuration_for(review=review,backend=backend,prompt_version=prompt_version):
            raise ValidationError("Analysis configuration differs from the fixed contract")
        actual = read_source(self.stores, source["capture_id"])
        if source != actual:
            raise ValidationError("Analysis source differs from retained evidence")
        parent = None
        if review:
            _identifier(analysis_id, _ANALYSIS)
            with quiet_immutable_read_connection(self.stores, StoreRole.COMPANY) as c:
                found = c.execute("SELECT * FROM company_transcript_analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
            if found is None or found["capture_id"] != source["capture_id"]:
                raise ValidationError("Review parent does not match transcript")
            parent = dict(found)
        if request_identity != request_identity_for(source, review_analysis_id=analysis_id,backend=backend,prompt_version=prompt_version):
            raise ValidationError("Model request identity differs")
        start, finish = utc(started_at), utc(completed_at)
        boundary = parent["available_at"] if parent else source["source_available_at"]
        if start < boundary or finish < start:
            raise ValidationError("Analysis predates its source or parent")
        output, usage = _response(raw_response, expected_model)
        expected_schema = output_schema_version(review=review, prompt_version=prompt_version)
        if not isinstance(output, dict) or output.get("schema_version") != expected_schema:
            raise ValidationError("Output schema version differs from the pinned request")
        checked = (validate_review(output, loads_strict(parent["output_json"]), source["turns"],
                    courtesy_policy=loads_strict(parent["configuration_json"]).get("question_inventory_policy","courtesy_only.v1")) if review
                   else validate_analysis(output, source["turns"], processing_coverage="complete",
                       courtesy_policy=configuration.get("question_inventory_policy","courtesy_only.v1")))
        evidence = _spans(checked["evidence"], source)
        semantic = digest({"request_identity":request_identity,"output":output})
        identifier = stable_id("transcript_review" if review else "transcript_analysis", semantic)
        table = "company_transcript_analysis_reviews" if review else "company_transcript_analyses"
        with acquire_write_session(self.stores, (StoreRole.COMPANY,)) as locks:
            existing = self.repository.find_request(request_identity, review=review)
            if existing:
                if existing["semantic_identity"] != semantic:
                    raise ConflictError("A different response already exists for this exact extraction request")
                return IngestionReceipt(outcome="unchanged",store="company",dataset_id=DATASET,semantic_identity=semantic,
                    run_id=None,artifact_id=None,snapshot_id=None,written_count=0,warnings=("derived_model_analysis",))
            def writer(c, rid):
                if review:
                    c.execute("INSERT INTO company_transcript_analysis_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (identifier,request_identity,semantic,analysis_id,dumps_strict(configuration),finish,expected_model,"high",output["verdict"],digest(raw_response),raw_response,dumps_strict(output),dumps_strict(evidence),dumps_strict(usage),rid))
                    written = 1
                else:
                    c.execute("INSERT INTO company_transcript_analyses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (identifier,request_identity,semantic,source["capture_id"],source["input_sha256"],dumps_strict(configuration),source["source_available_at"],finish,expected_model,"high",dumps_strict(source),digest(raw_response),raw_response,dumps_strict(output),dumps_strict(checked["derived"]),dumps_strict(evidence),dumps_strict(usage),rid))
                    written = 1
                    for claim in output["guidance_claims"]:
                        v, target = claim["value"], claim["target"]
                        c.execute("INSERT INTO company_guidance_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (identifier,claim["claim_local_id"],claim["metric"],target["fiscal_year"],target["fiscal_quarter"],claim["accounting_basis"],claim["measurement"],v["shape"],v["point"],v["low"],v["high"],v["unit"],v["currency"],dumps_strict(claim),rid))
                        written += 1
                    for q in output["analyst_focus"]["question_blocks"]:
                        c.execute("INSERT INTO company_analyst_question_blocks VALUES (?,?,?,?)",(identifier,q["question_local_id"],dumps_strict(q),rid));written += 1
                    for t in output["analyst_focus"]["topics"]:
                        c.execute("INSERT INTO company_analyst_topics VALUES (?,?,?,?,?,?)",(identifier,t["topic_local_id"],t["topic_code"],t["label"],dumps_strict(t),rid));written += 1
                    tone = output["management_tone"]
                    assessments = [(k,tone[k]) for k in ("overall","prepared_remarks","qa")]+[("speaker:"+s["speaker_id"],s["assessment"]) for s in tone["by_speaker"]]
                    for key,a in assessments:
                        c.execute("INSERT INTO company_management_tone_assessments VALUES (?,?,?,?,?,?,?)",(identifier,key,a["sentiment"],a["expressed_confidence"],a["hedging"],dumps_strict(a),rid));written += 1
                artifact = ArtifactWrite(stable_id("analysis_response",identifier),DATASET,digest(raw_response),"application/json",len(raw_response),
                    "transcript-analysis/responses/"+digest(raw_response)+".json",scope,finish,"datetime",VERSION)
                snapshot = SnapshotWrite(stable_id("analysis_snapshot",identifier),DATASET,semantic,scope,"complete",written,finish,"datetime","validated",(artifact.artifact_id,),("derived_model_analysis",))
                return WriteResult(written,(artifact,),snapshot,(),warnings=("derived_model_analysis",))
            scope = {"capture_id":source["capture_id"],"request_identity":request_identity,"kind":"review" if review else "analysis","parent_analysis_id":analysis_id}
            return self.coordinator.execute(role=StoreRole.COMPANY,dataset_id=DATASET,output_dataset_ids=(DATASET,),semantic_identity=semantic,
                run_id=stable_id("analysis_run",uuid4().hex),command="company.transcript_analysis",scope=scope,started_at=start,completed_at=finish,
                fetched_count=1,writer=writer,held_locks=locks)
