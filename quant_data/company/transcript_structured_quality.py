"""Tiered private structured-call quality policy; legacy source and schema contracts stay frozen."""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
import math
from . import transcript_structured_call as legacy
from . import transcript_structured_call_terra_sol as previous
from .transcript_analysis_contract import _schema, _source, question_inventory
from ..errors import Issue, ValidationError, ResourceLimitError
from ..json_codec import dumps_strict, loads_strict

EXTRACTOR, EXTRACTOR_EFFORT = previous.EXTRACTOR, previous.EXTRACTOR_EFFORT
REVIEWER, REVIEWER_EFFORT = previous.REVIEWER, previous.REVIEWER_EFFORT
BACKEND, MAX_OUTPUT_TOKENS = previous.BACKEND, previous.MAX_OUTPUT_TOKENS
REQUEST_TIMEOUT_SECONDS, COURTESY_POLICY = previous.REQUEST_TIMEOUT_SECONDS, previous.COURTESY_POLICY
VERSION = "transcript_structured_tiered_prompt.v2"
POLICY = "transcript_quality_tiers.v2"
BRIEF_VERSION = previous.BRIEF_VERSION
REVIEW_VERSION = "transcript.structured_quality_review.v2"
FORMAT_NAMES = ("transcript_structured_tiered", "transcript_structured_review_tiered")
LENGTH_POLICY = "rendered_structured_call_words_with_editorial_tolerance.v2"
brief_schema = previous.brief_schema
expected_call = previous.expected_call
render_brief = previous.render_brief
rendered_word_count = previous.rendered_word_count
word_budget = previous.word_budget
BLOCKING_CATEGORIES = ("identity", "number", "guidance", "attribution", "material_omission", "structure", "excessive_length")
CATEGORIES = BLOCKING_CATEGORIES + ("source_metadata", "source_conflict", "repetition", "length", "style")


def assess_brief(value, source):
    """Collect all applicable issues without modifying facts or inventing missing speaker roles."""
    findings = []
    def add(severity, category, path, message):
        finding = {"severity":severity,"category":category,"pointer":path,"message":message}
        if finding not in findings:
            findings.append(finding)
    def finish(count=None):
        return {**word_budget(source),"policy":POLICY,"word_count":count,
                "status":"blocked" if any(f["severity"]=="blocker" for f in findings) else
                         "accepted_with_flags" if findings else "accepted",
                "findings":findings}
    try:
        _schema(value,brief_schema())
        turns=_source(source["turns"])
    except ValidationError as exc:
        add("blocker","structure","",str(exc))
        return finish()
    if value["call"] != expected_call(source):
        add("blocker","identity","/call","Call identity differs from the retained source")
    def walk(node,path=""):
        if isinstance(node,dict):
            for key,item in node.items():
                if key in {"source_turn_ids","question_turn_ids","answer_turn_ids","call","schema_version"}:
                    continue
                pointer=path+"/"+key
                if isinstance(item,str):
                    maximum=25 if pointer=="/headline/message" else 45 if key=="management_answer" else 30
                    count=sum(any(c.isalnum() for c in w) for w in item.split())
                    if count>maximum:
                        severity="editorial" if count<=math.ceil(maximum*1.25) else "blocker"
                        add(severity,"length" if severity=="editorial" else "excessive_length",pointer,
                            "Text cell exceeds its concise target"+(" substantially" if severity=="blocker" else " modestly"))
                else:walk(item,pointer)
        elif isinstance(node,list):
            for i,item in enumerate(node):walk(item,path+"/"+str(i))
    walk(value)

    def cite(ids,path,role):
        if len(ids)!=len(set(ids)):add("editorial","repetition",path,"Repeated source reference")
        uncertain=False
        for identifier in ids:
            if identifier not in turns:
                add("blocker","attribution",path,"Source reference does not exist")
            elif turns[identifier]["role"]=="unknown":
                uncertain=True
                add("uncertainty","source_metadata",path,"Speaker role is unverified; do not assume it is "+role)
            elif turns[identifier]["role"]!=role:
                add("blocker","attribution",path,"Known source role contradicts the asserted "+role+" attribution")
        return uncertain

    for section in ("headline","reported_results","guidance","business_drivers","watch_items","management_tone"):
        node=value[section]
        rows=node if isinstance(node,list) else [node] if node else []
        for i,row in enumerate(rows):cite(row["source_turn_ids"],"/"+section+"/"+str(i),"management")

    # Comparison labels belong to their metric context. Repeating "year over year"
    # for different metrics is normal; only repeated substantial narrative is warned.
    narratives=defaultdict(list)
    for section,field in (("business_drivers","driver"),("watch_items","what_to_monitor")):
        for i,row in enumerate(value[section]):
            text=" ".join(row[field].casefold().split()).rstrip(".")
            if len(text.split())>=6:narratives[text].append("/"+section+"/"+str(i))
    for paths in narratives.values():
        if len(paths)>1:add("editorial","repetition",paths[0],"Substantial narrative is repeated")

    for section in ("reported_results","guidance"):
        seen={}
        for i,row in enumerate(value[section]):
            path="/"+section+"/"+str(i)
            key=(" ".join(row["metric"].casefold().split()),row["period"])
            measures={"value":row["value"]} if section=="reported_results" else {
                "current":row["current"],"previous":row["previous"],"change":row["change"]}
            if key in seen:
                if measures==seen[key]:add("editorial","repetition",path,"Repeated metric and period with identical values")
                else:add("blocker","number",path,"Conflicting values for the same metric and period")
            seen[key]=measures
            numeric_ok=True
            for name,measure in measures.items():
                if name=="change" or measure is None:continue
                try:legacy._measure(measure,path+"/"+name)
                except ValidationError as exc:
                    numeric_ok=False;add("blocker","number",path+"/"+name,str(exc))
            if section!="guidance" or not numeric_ok:continue
            old,new=row["previous"],row["current"]
            if old and old["unit"] and new["unit"] and old["unit"]!=new["unit"]:
                add("blocker","number",path,"Previous and current guidance use incompatible units")
            if row["change"] in {"withdrawn","not_provided"} and new["kind"]!="not_provided":
                add("blocker","guidance",path,"Absent or withdrawn guidance cannot contain a current forecast")
            if row["change"] in {"raised","lowered","reiterated"} and new["kind"]=="not_provided":
                add("blocker","guidance",path,"Guidance change requires a current outlook")
            if old and old["kind"]==new["kind"] and new["kind"] in {"point","range"}:
                keys=("amount",) if new["kind"]=="point" else ("low","high")
                differences=[Decimal(new[k])-Decimal(old[k]) for k in keys]
                invalid=(row["change"]=="reiterated" and (any(differences) or old["qualifier"]!=new["qualifier"])
                         or row["change"]=="raised" and (any(d<0 for d in differences) or not any(d>0 for d in differences))
                         or row["change"]=="lowered" and (any(d>0 for d in differences) or not any(d<0 for d in differences)))
                if invalid:add("blocker","guidance",path,"Guidance direction contradicts its numerical values")

    inventory=question_inventory(source["turns"],courtesy_policy=COURTESY_POLICY)
    questions={r["analyst_turn_ids"][0]:r["management_answer_turn_ids"] for r in inventory["question_blocks"]}
    if not questions and any(t["speaker_role"]=="unknown" and t["section"]=="qa" for t in source["turns"]):
        add("uncertainty","source_metadata","/analyst_focus","Q&A inventory is incomplete because speaker roles are missing")
    for i,row in enumerate(value["analyst_focus"]):
        path="/analyst_focus/"+str(i)
        qids,aids=row["question_turn_ids"],row["answer_turn_ids"]
        uncertain=cite(qids,path+"/question_turn_ids","analyst") | cite(aids,path+"/answer_turn_ids","management")
        if uncertain:
            add("uncertainty","source_metadata",path,"Question/answer attribution needs source-context review")
        else:
            if any(q not in questions for q in qids):
                add("blocker","attribution",path,"Question is not an eligible substantive exchange")
            allowed={a for q in qids for a in questions.get(q,[])}
            if not set(aids)<=allowed:add("blocker","attribution",path,"Answers do not belong to the cited question")
            if not aids and (allowed or row["answer_status"]!="not_answered" or
                             row["management_answer"]!="No management answer in this exchange."):
                add("blocker","attribution",path,"Existing management response cannot be presented as absent")
    budget=word_budget(source)
    try:count=rendered_word_count(value)
    except (TypeError,ValueError,KeyError):
        if not any(f["severity"]=="blocker" and f["category"]=="number" for f in findings):
            add("blocker","structure","","Record cannot be rendered from its structured fields")
        return finish()
    hard=min(budget["source_words"],math.ceil(budget["max_words"]*1.25))
    if count>hard:add("blocker","excessive_length","", "Document substantially exceeds the concise length bound")
    elif count>budget["max_words"]:add("editorial","length","","Document modestly exceeds its target word ceiling")
    return finish(count)


def validate_brief(value,source):
    result=assess_brief(value,source)
    blockers=[f for f in result["findings"] if f["severity"]=="blocker"]
    if blockers:
        raise ValidationError("Structured record has substantive or hard-bound errors",
            issues=[Issue(f["pointer"],f["category"],f["message"]) for f in blockers])
    return result


def review_schema():
    result=legacy.review_schema()
    result["properties"]["schema_version"]["enum"]=[REVIEW_VERSION]
    o=legacy.previous._object;t=legacy.previous._text;a=legacy.previous._array;e=legacy.previous._enum
    finding=o({"severity":e("blocker","uncertainty","editorial"),"category":e(*CATEGORIES),
               "pointer":t(250),"description":t(500),"turn_ids":a(t(80),6,1),"corrected": {"type":"boolean"}})
    result["properties"]["draft_findings"]=a(finding,12)
    result["properties"]["source_uncertainties"]=a(o({
        "category":e("source_metadata","source_conflict"),"section":e(*legacy.SECTIONS),
        "description":t(500),"turn_ids":a(t(80),6,1)}),6)
    result["properties"]["unresolved_issues"]["items"]["properties"]["category"]=e(*BLOCKING_CATEGORIES)
    result["properties"]["unresolved_issues"]["items"]["required"].append("category")
    result["required"]+=["draft_findings","source_uncertainties"]
    return result


def validate_editorial_review(value,draft,source):
    _schema(draft,brief_schema());_schema(value,review_schema())
    turns={t["turn_id"] for t in source["turns"]}
    for finding in value["draft_findings"]:
        expected=("blocker" if finding["category"] in BLOCKING_CATEGORIES else
                  "uncertainty" if finding["category"].startswith("source_") else "editorial")
        if finding["severity"]!=expected:
            raise ValidationError("Review finding severity contradicts its category")
        pointer=finding["pointer"]
        if pointer!="/":
            node=draft
            try:
                if not pointer.startswith("/"):raise ValueError()
                for part in pointer[1:].split("/"):
                    part=part.replace("~1","/").replace("~0","~")
                    node=node[int(part)] if isinstance(node,list) else node[part]
            except (ValueError,KeyError,IndexError,TypeError):
                raise ValidationError("Review finding pointer does not resolve in the original draft") from None
    for finding in value["draft_findings"]+value["source_uncertainties"]+value["unresolved_issues"]:
        ids=finding["turn_ids"]
        if len(ids)!=len(set(ids)) or not set(ids)<=turns:
            raise ValidationError("Review findings require existing unique source references")
    unresolved=bool(value["unresolved_issues"])
    uncorrected=[f for f in value["draft_findings"] if f["severity"]=="blocker" and not f["corrected"]]
    if uncorrected and not unresolved:raise ValidationError("Uncorrected substantive findings must remain unresolved")
    final=assess_brief(value["brief"],source)
    if final["status"]=="blocked" and not unresolved:raise ValidationError("Final substantive errors cannot be approved")
    changed=value["brief"]!=draft
    if value["decision"]=="approved" and changed:raise ValidationError("Approval cannot silently revise the draft")
    if value["decision"]=="revised" and not changed:raise ValidationError("Revised decision requires an edited brief")
    if any(f["corrected"] for f in value["draft_findings"]) and not changed:
        raise ValidationError("Claimed corrections require an edited brief")
    if unresolved!=(value["decision"]=="needs_attention"):
        raise ValidationError("Only unresolved substantive issues require needs_attention")
    return {**final,"source_uncertainties":value["source_uncertainties"],
            "draft_substantive_findings":sum(f["severity"]=="blocker" for f in value["draft_findings"])}


COMMON_PROMPT=legacy.COMMON_PROMPT.replace(
    "Merge repetition across sections.","Avoid redundant narrative; comparison labels may repeat across distinct metrics.").replace(
    "The brief_budget is a hard rendered word ceiling including labels and source IDs; aim near target_words,",
    "The brief_budget is the normal concise target including labels and source IDs; aim near target_words,").replace(
    "All source_turn_ids cite management turns.",
    "Source IDs must exist. Cite management statements; known analyst/operator statements cannot become company forecasts. Unknown role labels are uncertainty, not proof of error or permission to assume management.").replace(
    "Use TOP-LEVEL question_inventory (courtesy_only.v2), not embedded historical hints. question_turn_ids use first\nanalyst turns of substantive blocks; answer_turn_ids belong to those exact exchanges. Exclude courtesy-only blocks.",
    "Use top-level question_inventory as reliable where roles are known, not as proof Q&A is absent when speaker labels are missing. Cite the actual full-text question and response in order, flag uncertain attribution, and exclude courtesy exchanges. Do not invent missing speakers.")
COMMON_PROMPT+="""
Apply three quality tiers. Block substantive errors: incorrect numbers, wrong company/call, unsupported guidance,
known attribution errors, and omissions that materially change a selected claim's meaning. Do not require every detail.
Flag source uncertainty separately: missing speaker metadata or conflicting source statements. Preserve uncertainty;
do not silently choose one side, invent certainty, or erase otherwise useful content because of incomplete labels.
Treat repeated narrative and modest word overruns up to 25% as editorial warnings. Shared comparison labels
across different financial metrics are normal. Gross overruns, malformed values, and hard request bounds still fail.
"""
EXTRACTION_PROMPT=COMMON_PROMPT+"\nReturn only the structured brief. The local assessment retains uncertainty and editorial flags.\n"
REVIEW_PROMPT=COMMON_PROMPT+"""
Independently compare the COMPLETE saved source with the ORIGINAL Terra draft. Return the corrected brief.
List draft_findings by severity/category, each with a JSON pointer into the original draft (use / for the whole
document), source turn IDs, and whether this review corrected it. Incorrect figures and unsupported forecasts
are blockers even when you successfully correct them; do not downgrade them to style or source uncertainty.
Do not manufacture findings or corrections to fill arrays. Editorial preferences alone are not substantive errors.
Record remaining source_metadata/source_conflict in source_uncertainties; these do not require needs_attention.
Unresolved_issues contains only substantive or hard-bound problems remaining in the final brief, with category.
Use approved if the draft is unchanged and has no remaining blockers, revised if you edited it and resolved
the blockers, or needs_attention if substantive problems remain. Missing speaker metadata alone is not a blocker.
Local assessment is a set of mechanical signals, not a factual verdict. Verify figures, scope, period, sign,
qualifications and attribution against the full text. Preserve source contradictions as concise qualifications.
Editorial notes are optional. There is no additional extraction call or automatic retry.
"""


def request_contract(*,review=False):
    return (REVIEW_PROMPT,review_schema()) if review else (EXTRACTION_PROMPT,brief_schema())


def configuration_for(*,review=False):
    prompt,schema=request_contract(review=review)
    return {**previous.configuration_for(review=review),"prompt_version":VERSION,
            "quality_policy":POLICY,"prompt_sha256":legacy.legacy._digest(prompt),
            "schema_sha256":legacy.legacy._digest(schema),"document_length_policy":LENGTH_POLICY}


def make_request(source,*,analysis=None):
    # Reuse the historical transport envelope, not its source-role acceptance rules.
    request=loads_strict(previous.make_request(source,analysis=analysis).decode())
    review=analysis is not None
    prompt,schema=request_contract(review=review)
    request["instructions"]=prompt
    request["text"]["format"].update(name=FORMAT_NAMES[int(review)],schema=schema)
    supplied=loads_strict(request["input"][0]["content"][0]["text"])
    if review:supplied["draft_automated_assessment"]=assess_brief(analysis,source)
    request["input"][0]["content"][0]["text"]=dumps_strict(supplied)
    raw=dumps_strict(request).encode()
    if len(raw)+1024>legacy.legacy.MAX_REQUEST_BYTES:
        raise ResourceLimitError("Complete tiered request exceeds bound; no text was truncated")
    return raw
