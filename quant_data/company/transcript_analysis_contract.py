"""Transcript analysis contract validation."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any
import re
from ..schema import validate_schema
from ..errors import Issue, ValidationError

ROOT = Path(__file__).resolve().parents[2]
ROLES = {"management","analyst","operator","unknown"}
SECTIONS = {"prepared_remarks","qa","unknown"}
REVIEW_SCHEMA = {"type":"object","properties":{"verdict":{"type":"string","enum":["accepted","needs_changes","insufficient_evidence"]},"summary":{"type":"string"},"findings":{"type":"array","items":{"type":"object","properties":{"severity":{"type":"string","enum":["error","warning"]},"category":{"type":"string","enum":["guidance","analyst_focus","management_tone","coverage"]},"output_pointer":{"type":["string","null"]},"description":{"type":"string"},"evidence":{"type":"array","items":{"type":"object","properties":{"turn_id":{"type":"string"},"quote":{"type":"string"}},"required":["turn_id","quote"],"additionalProperties":False}},"proposed_correction":{"type":["string","null"]}},"required":["severity","category","output_pointer","description","evidence","proposed_correction"],"additionalProperties":False}}},"required":["verdict","summary","findings"],"additionalProperties":False}

def _bad(message,p="/"):
    raise ValidationError(message,issues=(Issue(p,"semantic",message),),code="invalid_output")

ANALYSIS_V1 = "transcript.analysis.v1"
ANALYSIS_V2 = "transcript.analysis.v2"
REVIEW_V2 = "transcript.review.v2"
ANALYSIS_V3 = "transcript.analysis.v3"
REVIEW_V3 = "transcript.review.v3"


def analysis_schema(version=ANALYSIS_V1):
    if version not in (ANALYSIS_V1, ANALYSIS_V2, ANALYSIS_V3):
        _bad("Unknown transcript analysis schema version", "/schema_version")
    try:
        return json.loads((ROOT/("docs/rebuild/TRANSCRIPT_ANALYSIS_V"+version.rsplit("v",1)[1]+".schema.json")).read_text(encoding="utf-8"))
    except (OSError,ValueError) as exc:
        raise ValidationError("Transcript analysis schema is unavailable",code="invalid_output") from exc

def review_schema(version=None):
    if version not in (None, REVIEW_V2, REVIEW_V3):
        _bad("Unknown transcript review schema version", "/schema_version")
    if version == REVIEW_V3:
        try:
            return json.loads((ROOT/"docs/rebuild/TRANSCRIPT_REVIEW_V3.schema.json").read_text(encoding="utf-8"))
        except (OSError,ValueError) as exc:
            raise ValidationError("Transcript review schema is unavailable",code="invalid_output") from exc
    result = deepcopy(REVIEW_SCHEMA)
    if version == REVIEW_V2:
        result["properties"]["schema_version"] = {"type": "string", "enum": [REVIEW_V2]}
        result["required"].append("schema_version")
        result["properties"]["findings"]["items"]["properties"]["evidence"]["items"] = deepcopy(
            analysis_schema(ANALYSIS_V2)["$defs"]["evidence"])
    return result

def _finite(v,p=""):
    if isinstance(v,float) and not math.isfinite(v): _bad("Non-finite output is not allowed",p or "/")
    if isinstance(v,dict):
        for k,x in v.items(): _finite(x,p+"/"+k)
    elif isinstance(v,list):
        for i,x in enumerate(v): _finite(x,p+"/"+str(i))

def _schema(value, schema):
    """Resolve this fixed contract's local refs/null unions for the shared validator."""
    def expand(node):
        if "$ref" in node:
            prefix = "#/$defs/"
            if not node["$ref"].startswith(prefix):
                _bad("Unsupported analysis schema reference")
            return expand(schema["$defs"][node["$ref"][len(prefix):]])
        if "anyOf" in node:
            variants = node["anyOf"]
            if len(variants) != 2 or variants[1] != {"type": "null"}:
                _bad("Unsupported analysis schema union")
            result = expand(variants[0])
            result["type"] = [result["type"], "null"]
            return result
        result = {k: deepcopy(v) for k,v in node.items() if k != "$defs"}
        if "properties" in result:
            result["properties"] = {k: expand(v) for k,v in result["properties"].items()}
        if "items" in result:
            result["items"] = expand(result["items"])
        return result
    _finite(value)
    validate_schema(value, expand(schema), code="invalid_output")


def _nonempty(value, pointer):
    if not isinstance(value, str) or not value.strip():
        _bad("A nonempty string is required", pointer)


def _decimal(value, pointer):
    if (not isinstance(value, str) or len(value) > 100
            or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value)):
        _bad("Expected a bounded plain decimal string", pointer)
    return Decimal(value)


def _guidance_value(claim, pointer):
    value = claim["value"]
    fields = {"point": ("point",), "range": ("low", "high"),
              "lower_bound": ("low",), "upper_bound": ("high",),
              "qualitative": (), "none": ()}[value["shape"]]
    for name in ("point", "low", "high"):
        if name in fields:
            _decimal(value[name], pointer + "/value/" + name)
        elif value[name] is not None:
            _bad("Value fields conflict with the guidance shape", pointer + "/value/" + name)
    if fields:
        if _decimal(value["scale"], pointer + "/value/scale") <= 0:
            _bad("Numeric guidance requires positive scale", pointer)
        _nonempty(value["source_value_text"], pointer + "/value/source_value_text")
        if value["qualitative_text"] is not None:
            _bad("Numeric guidance cannot also contain a qualitative value", pointer)
    elif value["scale"] is not None:
        _bad("Nonnumeric guidance has no numeric scale", pointer)
    if value["shape"] == "range" and Decimal(value["low"]) > Decimal(value["high"]):
        _bad("Guidance range is reversed", pointer)
    if value["shape"] == "qualitative":
        _nonempty(value["qualitative_text"], pointer + "/value/qualitative_text")
    elif value["qualitative_text"] is not None:
        _bad("Qualitative text conflicts with the guidance shape", pointer)
    currency = value["currency"]
    # Null is retained when the transcript does not establish a currency.
    if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
        _bad("Currency must be a three-letter uppercase code or null", pointer)
    if currency is not None and value["unit"] not in {"currency", "currency_per_share"}:
        _bad("Currency conflicts with the guidance unit", pointer)
    if claim["management_action"] in {"withdrawn", "not_provided"} and value["shape"] != "none":
        _bad("Withdrawal or refusal to guide cannot become a current forecast value", pointer)
    target = claim["target"]
    if target["fiscal_year"] is not None and not 1900 <= target["fiscal_year"] <= 2200:
        _bad("Fiscal year exceeds the supported range", pointer + "/target")
    if target["period_kind"] != "fiscal_quarter" and target["fiscal_quarter"] is not None:
        _bad("A fiscal quarter requires an explicitly fiscal-quarter target", pointer + "/target")
    if target["period_kind"] not in {"fiscal_year", "fiscal_quarter"} and target["fiscal_year"] is not None:
        _bad("Calendar or unresolved periods cannot supply inferred fiscal labels", pointer + "/target")


def _source(turns):
    if not isinstance(turns,list) or not turns or len(turns)>2000:_bad("Transcript turns are missing or exceed the supported bound","/turns")
    out={}; size=0
    for i,t in enumerate(turns):
        p="/turns/"+str(i)
        if not isinstance(t,dict):_bad("Transcript turn must be an object",p)
        tid,sid,role,section,text=t.get("turn_id"),t.get("speaker_id"),t.get("speaker_role"),t.get("section"),t.get("text")
        if not isinstance(tid,str) or not tid or tid in out:_bad("Transcript turn IDs must be unique nonempty strings",p+"/turn_id")
        if sid is not None and (not isinstance(sid,str) or not sid):_bad("Transcript speaker ID is invalid",p+"/speaker_id")
        if role not in ROLES or section not in SECTIONS:_bad("Transcript role or section is invalid",p)
        if not isinstance(text,str) or not text.strip() or len(text)>100000:_bad("Transcript text is invalid",p+"/text")
        size+=len(text)
        if size>4000000:_bad("Transcript source exceeds the supported bound","/turns")
        out[tid]={"id":tid,"speaker":sid,"role":role,"section":section,"text":text,"index":i}
    return out

def _cite(rows,p,source,roles=None,sections=None,allowed=None,*,summaries=False):
    if not isinstance(rows,list) or not rows or len(rows)>100:_bad("Evidence is missing or exceeds the supported bound",p)
    out=[]; seen=set()
    for i,row in enumerate(rows):
        q=p+"/"+str(i)
        if not isinstance(row,dict):_bad("Evidence entry must be an object",q)
        field = "evidence_summary" if summaries else "quote"
        tid,quote=row.get("turn_id"),row.get(field)
        if not isinstance(tid,str) or tid not in source:_bad("Evidence references an unknown turn",q+"/turn_id")
        if not isinstance(quote,str) or (not quote.strip() if summaries else not quote):
            _bad("Evidence summary is invalid" if summaries else "Evidence quote is invalid",q+"/"+field)
        if (tid,quote) in seen:_bad("Evidence quote is duplicated",q)
        seen.add((tid,quote)); turn=source[tid]
        if roles and turn["role"] not in roles or sections and turn["section"] not in sections or allowed is not None and tid not in allowed:_bad("Evidence has an invalid source scope",q+"/turn_id")
        if summaries:
            out.append({"output_pointer":q,"turn_id":tid,"evidence_summary":quote,
                        "evidence_kind":"summary","char_start":None,"char_end":None,
                        "source_span":{"kind":"turn","char_start":0,"char_end":len(turn["text"])}})
            continue
        a=turn["text"].find(quote)
        if a<0:_bad("Evidence quote does not occur in its source turn",q+"/quote")
        if turn["text"].find(quote,a+1)>=0:_bad("Evidence quote is ambiguous in its source turn",q+"/quote")
        out.append({"output_pointer":q,"turn_id":tid,"quote":quote,"char_start":a,"char_end":a+len(quote)})
    return out

COURTESY_POLICY = "courtesy_only.v1"
_COURTESY_PHRASES = tuple(
    tuple(phrase.split()) for phrase in (
        "thank you very much", "thank you so much", "thanks very much",
        "thanks so much", "no further questions", "no more questions",
        "thank you", "got it", "thanks", "okay", "ok", "great", "perfect", "understood"
    )
)


COURTESY_POLICY_V2 = "courtesy_only.v2"
_COURTESY_PHRASES_V2 = _COURTESY_PHRASES + tuple(
    tuple(phrase.split()) for phrase in (
        "good morning", "good afternoon", "good evening", "wonderful",
        "congratulations on the quarter", "congrats on the quarter",
        "congratulations again", "congrats again",
        "and congratulations again", "and congrats again",
        "appreciate the color", "appreciate the caller",
    )
)


def _courtesy_only(text, *, courtesy_policy=COURTESY_POLICY):
    if courtesy_policy not in (COURTESY_POLICY, COURTESY_POLICY_V2):
        _bad("Unknown courtesy policy")
    phrases = _COURTESY_PHRASES_V2 if courtesy_policy == COURTESY_POLICY_V2 else _COURTESY_PHRASES
    # Deterministic token consumption avoids regex backtracking on long near-matches.
    words = re.sub(r"[\s.,!;:\-]+", " ", text.casefold()).strip().split()
    if not words:
        return False
    offset = 0
    while offset < len(words):
        phrase = next((phrase for phrase in phrases
                       if words[offset:offset+len(phrase)] == list(phrase)), None)
        if phrase is None:
            return False
        offset += len(phrase)
    return True


def _blocks(source, *, courtesy_policy=COURTESY_POLICY):
    if courtesy_policy not in (COURTESY_POLICY, COURTESY_POLICY_V2):
        _bad("Unknown courtesy policy")
    rows = sorted(source.values(), key=lambda x: x["index"])
    all_blocks, current = [], []
    for turn in rows:
        adjacent = (current and turn["index"] == source[current[-1]]["index"] + 1
                    and turn["speaker"] and turn["speaker"] == source[current[-1]]["speaker"])
        if turn["role"] == "analyst" and turn["section"] == "qa" and adjacent:
            current.append(turn["id"])
        else:
            if current:
                all_blocks.append(tuple(current))
            current = [turn["id"]] if turn["role"] == "analyst" and turn["section"] == "qa" else []
    if current:
        all_blocks.append(tuple(current))
    courtesies = [block for block in all_blocks
                 if _courtesy_only(" ".join(source[tid]["text"] for tid in block), courtesy_policy=courtesy_policy)]
    questions = [block for block in all_blocks if block not in courtesies]
    return rows, questions, courtesies, all_blocks


def _answers(block, source, rows, all_blocks):
    end = source[block[-1]]["index"]
    boundary = min((source[b[0]]["index"] for b in all_blocks
                    if source[b[0]]["index"] > end), default=len(source))
    return [turn["id"] for turn in rows if end < turn["index"] < boundary
            and turn["role"] == "management" and turn["section"] == "qa"]


def question_inventory(turns, *, courtesy_policy=COURTESY_POLICY):
    """Trusted input hints; excluded courtesy text remains in the transcript."""
    source = _source(turns)
    rows, questions, courtesies, all_blocks = _blocks(source, courtesy_policy=courtesy_policy)
    return {
        "courtesy_policy_version": courtesy_policy,
        "question_blocks": [{"analyst_turn_ids": list(block),
                             "management_answer_turn_ids": _answers(block, source, rows, all_blocks)}
                            for block in questions],
        "excluded_courtesy_blocks": [list(block) for block in courtesies],
    }


def _assessment(a,p,source,section=None,speaker=None,*,summaries=False):
    matches=[x for x in source.values() if x["role"]=="management" and (section is None or x["section"]==section) and (speaker is None or x["speaker"]==speaker)]
    insuff=all(a[x]=="insufficient_evidence" for x in ("sentiment","expressed_confidence","hedging"))
    if not matches:
        if not insuff or a["assessment_support"]!="insufficient" or a["summary"] is not None or a["supporting_evidence"] or a["counterevidence"]:_bad("Missing management section requires insufficient evidence",p)
        return []
    if insuff and a["assessment_support"] != "insufficient":
        _bad("Insufficient tone labels require insufficient assessment support",p)
    if not insuff:
        _nonempty(a["summary"],p+"/summary")
    if not insuff and (a["assessment_support"]=="insufficient" or not a["supporting_evidence"]):_bad("Tone labels require management evidence" if summaries else "Tone labels require management quotations",p)
    return (_cite(a["supporting_evidence"],p+"/supporting_evidence",source,{"management"},{section} if section else None,{x["id"] for x in matches},summaries=summaries) if a["supporting_evidence"] else [])+(_cite(a["counterevidence"],p+"/counterevidence",source,{"management"},{section} if section else None,{x["id"] for x in matches},summaries=summaries) if a["counterevidence"] else [])

def validate_analysis(output:Any,turns:Any,*,processing_coverage="complete",courtesy_policy=COURTESY_POLICY)->dict:
    if processing_coverage not in {"complete","partial"}:_bad("Processing coverage must be complete or partial","/processing_coverage")
    source=_source(turns)
    try:
        if len(json.dumps(output,allow_nan=False))>2000000:_bad("Output exceeds the supported bound")
    except (TypeError,ValueError):_bad("Output is not strict JSON")
    version = output.get("schema_version") if isinstance(output,dict) else None
    _schema(output,analysis_schema(version)); evidence=[]; ids=set()
    summaries = version in (ANALYSIS_V2, ANALYSIS_V3)
    for i,g in enumerate(output["guidance_claims"]):
        p="/guidance_claims/"+str(i)
        if not isinstance(g["claim_local_id"],str) or not g["claim_local_id"] or g["claim_local_id"] in ids:_bad("Guidance local IDs must be unique",p)
        ids.add(g["claim_local_id"])
        _nonempty(g["metric"],p+"/metric")
        _nonempty(g["metric_text"],p+"/metric_text")
        _guidance_value(g,p)
        evidence+=_cite(g["evidence"],p+"/evidence",source,{"management"},summaries=summaries)
    rows,blocks,courtesies,all_blocks=_blocks(source, courtesy_policy=courtesy_policy); focus=output["analyst_focus"]
    if blocks and focus["qa_observed"]!="present":_bad("Analyst Q&A is present in source","/analyst_focus/qa_observed")
    if not blocks:
        expected = "uncertain" if any(x["section"] in {"qa","unknown"} for x in source.values()) else "absent"
        if focus["qa_observed"] != expected:
            _bad("Q&A availability must preserve unknown sections or roles","/analyst_focus/qa_observed")
    found={}; used=set()
    for i,b in enumerate(focus["question_blocks"]):
        p="/analyst_focus/question_blocks/"+str(i); key=tuple(b["analyst_turn_ids"])
        _nonempty(b["question_local_id"],p+"/question_local_id")
        _nonempty(b["summary"],p+"/summary")
        if key not in blocks or key in used or b["question_local_id"] in found:_bad("Question block is duplicate, split, or invalid",p)
        used.add(key);found[b["question_local_id"]]=b;evidence+=_cite(b["evidence"],p+"/evidence",source,{"analyst"},{"qa"},set(key),summaries=summaries)
        expected_answers = _answers(key,source,rows,all_blocks)
        if b["management_answer_turn_ids"] != expected_answers:
            _bad("Question answer links must match the complete source exchange",p+"/management_answer_turn_ids")
    if set(blocks)!=used:_bad("Source analyst question blocks were omitted or split","/analyst_focus/question_blocks")
    covered=set(); ranked=[]; topic_ids=set()
    for i,t in enumerate(focus["topics"]):
        p="/analyst_focus/topics/"+str(i); keys=t["question_local_ids"]
        _nonempty(t["topic_local_id"],p+"/topic_local_id")
        _nonempty(t["label"],p+"/label")
        _nonempty(t["summary"],p+"/summary")
        if t["topic_local_id"] in topic_ids:
            _bad("Topic IDs must be unique",p)
        topic_ids.add(t["topic_local_id"])
        if not keys or len(keys)!=len(set(keys)) or any(k not in found for k in keys):_bad("Topic references are invalid",p)
        covered.update(keys); qids=set().union(*(set(found[k]["analyst_turn_ids"]) for k in keys)); aids=set().union(*(set(found[k]["management_answer_turn_ids"]) for k in keys))
        evidence+=_cite(t["question_evidence"],p+"/question_evidence",source,{"analyst"},{"qa"},qids,summaries=summaries)
        if t["response_status"] in {"answered","partially_answered","mixed"}:
            _nonempty(t["management_response_summary"],p+"/management_response_summary")
            if not t["answer_evidence"]:
                _bad("A response assessment requires answer evidence",p)
        if t["management_response_summary"] is not None and not t["answer_evidence"]:
            _bad("A management response summary requires answer evidence",p)
        if t["answer_evidence"]:
            evidence+=_cite(t["answer_evidence"],p+"/answer_evidence",source,{"management"},{"qa"},aids,summaries=summaries)
        ranked.append((t,keys,min(source[found[k]["analyst_turn_ids"][0]]["index"] for k in keys)))
    if found and covered!=set(found):_bad("Every observed analyst question block must belong to a topic","/analyst_focus/topics")
    tone=output["management_tone"];evidence+=_assessment(tone["overall"],"/management_tone/overall",source,summaries=summaries)+_assessment(tone["prepared_remarks"],"/management_tone/prepared_remarks",source,"prepared_remarks",summaries=summaries)+_assessment(tone["qa"],"/management_tone/qa",source,"qa",summaries=summaries)
    speaker_ids=set()
    for i,entry in enumerate(tone["by_speaker"]):
        p="/management_tone/by_speaker/"+str(i)
        speaker=entry["speaker_id"]
        if speaker in speaker_ids or not any(x["speaker"]==speaker and x["role"]=="management" for x in source.values()):
            _bad("Speaker tone requires a unique resolved management speaker",p)
        speaker_ids.add(speaker)
        evidence+=_assessment(entry["assessment"],p+"/assessment",source,speaker=speaker,summaries=summaries)
    rows=[]
    for t,keys,pos in ranked:
        people={source[found[k]["analyst_turn_ids"][0]]["speaker"] for k in keys if source[found[k]["analyst_turn_ids"][0]]["speaker"]}
        rows.append({"topic_local_id":t["topic_local_id"],"question_block_count":len(keys),"distinct_identified_analyst_count":len(people),"unresolved_analyst_question_block_count":sum(source[found[k]["analyst_turn_ids"][0]]["speaker"] is None for k in keys),"question_block_share":str(Decimal(len(keys))/Decimal(len(found))),"_p":pos})
    rows.sort(key=lambda x:(-x["question_block_count"],-x["distinct_identified_analyst_count"],x["_p"])); prev=None
    for i,x in enumerate(rows,1):
        key=x["question_block_count"],x["distinct_identified_analyst_count"];x["rank"]=i if key!=prev else rows[i-2]["rank"];prev=key;del x["_p"]
    people={source[b["analyst_turn_ids"][0]]["speaker"] for b in found.values() if source[b["analyst_turn_ids"][0]]["speaker"]}
    result = {"model_output":deepcopy(output),"derived":{"ranking_policy_version":"question_frequency.v1","courtesy_policy_version":courtesy_policy,
        "excluded_courtesy_block_count":len(courtesies),"processing_coverage":processing_coverage,"total_question_block_count":len(found),"distinct_identified_analyst_count":len(people),"unresolved_analyst_question_block_count":sum(source[b["analyst_turn_ids"][0]]["speaker"] is None for b in found.values()),"ranked_topics":rows},"evidence":evidence}

    if version == ANALYSIS_V3:
        from .transcript_analysis_coverage import validate_coverage
        result["derived"]["guidance_coverage"] = validate_coverage(output,source)
    return result

def validate_review(review:Any,analysis_output:Any,turns:Any,*,courtesy_policy=COURTESY_POLICY)->dict:
    output=validate_analysis(analysis_output.get("model_output",analysis_output) if isinstance(analysis_output,dict) else analysis_output,turns,courtesy_policy=courtesy_policy)["model_output"]
    return _validate_review_output(review,output,turns)


def _validate_review_output(review,output,turns):
    """Validate review evidence against a draft; canonical callers validate its parent first."""
    source=_source(turns)
    version = review.get("schema_version") if isinstance(review,dict) else None
    _schema(review,review_schema(version))
    summaries = version in (REVIEW_V2, REVIEW_V3)
    _nonempty(review["summary"],"/summary")
    errors=[x for x in review["findings"] if x["severity"]=="error"]
    if review["verdict"]=="accepted" and errors:_bad("Accepted review cannot contain error findings","/verdict")
    if review["verdict"]=="needs_changes" and not errors:_bad("Needs-changes review requires an error finding","/verdict")
    if review["verdict"]=="insufficient_evidence" and not any(f["category"]=="coverage" for f in review["findings"]):
        _bad("Insufficient-evidence review requires a coverage finding","/verdict")
    evidence=[]
    for i,f in enumerate(review["findings"]):
        p="/findings/"+str(i); point=f["output_pointer"]
        if not isinstance(f["description"],str) or not f["description"].strip():_bad("Review finding description is invalid",p)
        if point is not None:
            if not isinstance(point,str) or not point.startswith("/"):_bad("Review output pointer is invalid",p)
            cur=output
            for key in point[1:].split("/"):
                if re.search(r"~(?![01])",key):
                    _bad("Review pointer has an invalid escape",p)
                key=key.replace("~1","/").replace("~0","~")
                if isinstance(cur,dict) and key in cur:cur=cur[key]
                elif isinstance(cur,list) and re.fullmatch(r"0|[1-9][0-9]*",key) and int(key)<len(cur):cur=cur[int(key)]
                else:_bad("Review output pointer does not resolve",p)
        if version == REVIEW_V3:
            from .transcript_analysis_coverage import validate_finding
            validate_finding(f,output,p)
        roles={"guidance":{"management"},"analyst_focus":{"analyst","management"},"management_tone":{"management"},"coverage":ROLES}[f["category"]]
        evidence+=_cite(f["evidence"],p+"/evidence",source,roles,{"qa"} if f["category"]=="analyst_focus" else None,summaries=summaries)
    return {"model_output":deepcopy(review),"derived":{},"evidence":evidence}
