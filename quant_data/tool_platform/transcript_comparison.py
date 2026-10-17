"""Compare two saved call drafts, preserving their original turn evidence."""
from collections import Counter
import re
from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.json_codec import loads_strict,dumps_strict
from . import transcript_access as retained
from .transcript_research_access import ReadBudget,_call_date,_source_turns,_turn_ids
from .transcript_research_contracts import SECTIONS,TURN_PATTERN
from .retained_research_common import cutoff,result,number

def item_key(section,item):
    if not isinstance(item,dict): return None
    if section in ("reported_results","guidance"):
        if not item.get("metric") or not item.get("period"): return None
        return (item["metric"].strip().casefold(),item["period"].strip().casefold())
    key={"business_drivers":"business","analyst_focus":"topic","watch_items":"topic"}.get(section)
    return (str(item[key]).strip().casefold(),) if key and item.get(key) else None

def numeric_change(before,after):
    if not isinstance(before,dict) or not isinstance(after,dict): return None
    a,b=before.get("current"),after.get("current")
    if not isinstance(a,dict) or not isinstance(b,dict): return None
    if (a.get("kind"),a.get("unit"),a.get("qualifier")) != (b.get("kind"),b.get("unit"),b.get("qualifier")) or not a.get("unit"):
        return None
    keys=("amount",) if a.get("kind")=="point" else ("low","high") if a.get("kind")=="range" else ()
    if not keys or any(number(a.get(k)) is None or number(b.get(k)) is None for k in keys): return None
    return dict(unit=a["unit"],kind=a["kind"],qualifier=a.get("qualifier"),
                **{k+"_change":number(b[k])-number(a[k]) for k in keys})

def compare_section(section,before,after,*,numeric_allowed=True):
    left=before if isinstance(before,list) else [] if before is None else [before]
    right=after if isinstance(after,list) else [] if after is None else [after]
    leftkeys=Counter(item_key(section,item) for item in left)
    rightkeys=Counter(item_key(section,item) for item in right)
    used=set();out=[]
    for i,a in enumerate(left):
        key=item_key(section,a)
        j=next((j for j,b in enumerate(right) if key and item_key(section,b)==key),None)
        if j is not None and leftkeys[key]==rightkeys[key]==1:
            used.add(j);b=right[j]
            state="unchanged_saved_summary" if a==b else "changed_saved_summary"
            out.append(dict(status=state,before=a,after=b,matching_basis="exact_normalized_label_and_period",
                numeric_change=numeric_change(a,b) if numeric_allowed and section=="guidance" else None))
        else:
            # Single-object sections are presented side-by-side without semantic matching.
            if section in ("headline","management_tone") and len(left)==len(right)==1:
                used.add(0)
                out.append(dict(status="unchanged_saved_summary" if a==right[0] else "changed_saved_summary",
                    before=a,after=right[0],matching_basis="same_section",numeric_change=None))
            else:
                out.append(dict(status="only_in_before_summary",before=a,after=None,matching_basis="no_unique_match",numeric_change=None))
    for j,b in enumerate(right):
        if j not in used: out.append(dict(status="only_in_after_summary",before=None,after=b,matching_basis="no_unique_match",numeric_change=None))
    return out

def invoke(args,context,registry):
    at=cutoff(args,context);budget=ReadBudget();calls=[];refs=[];rows=[]
    with retained.connection(context) as c:
        for label in ("before","after"):
            capture=args[label+"_capture_id"]
            source=retained._raw(c,capture,at)
            if source is None:
                return result("company.compare_transcripts",args,at,
                    [("transcript_comparison_status",dict(status="not_established",reason=label+"_capture_unavailable_at_cutoff"))],established=False)
            if source["symbol"]!=args["symbol"]: raise ValidationError("Transcript capture symbol differs from requested symbol")
            day,basis=_call_date(c,source,at,budget)
            analysis=retained._analysis(c,capture,at);quality=retained._assessment_status(c,analysis,at)
            output={}
            if analysis:
                budget.require(analysis["output_bytes"],output=True)
                output=loads_strict(c.execute("SELECT output_json FROM company_structured_transcript_outputs WHERE analysis_id=?",
                    (analysis["analysis_id"],)).fetchone()[0])
                if not isinstance(output,dict): raise ValidationError("Saved transcript draft is not an object")
            metadata=dict(side=label,**source,call_date=day,call_date_basis=basis,**quality,
                analysis_id=analysis["analysis_id"] if analysis else None,
                extraction_available_at=analysis["available_at"] if analysis else None,
                schema_version=analysis["schema_version"] if analysis else None)
            rows.append(("compared_call",metadata))
            ids=set()
            for section in args.get("sections",SECTIONS):
                value=output.get(section)
                for item in value if isinstance(value,list) else [value]:
                    ids.update(_turn_ids(item))
            valid=sorted(t for t in ids if re.fullmatch(TURN_PATTERN,t) and int(t[1:])<=source["total_turn_count"])
            if len(valid)>40: raise ResourceLimitError("Comparison exceeds 40 cited turns per call; select fewer sections")
            turns=_source_turns(c,source,valid,0,at,budget) if valid else []
            for turn in turns:
                text=turn.pop("text");raw=loads_strict(turn.pop("raw_turn_json"));raw.pop("text",None)
                rows.append(("comparison_evidence",dict(side=label,**turn,speaker=raw,
                    text_excerpt=text[:1200],excerpt_truncated=len(text)>1200)))
            if ids-set(valid): rows.append(("comparison_unresolved_references",dict(side=label,turn_ids=sorted(ids-set(valid)))))
            calls.append((source,analysis,quality,output,day))
            refs.append(retained.lineage(capture))
            if analysis: refs.append(retained.lineage(capture,analysis["analysis_id"]))
        if calls[0][0]["instrument_id"]!=calls[1][0]["instrument_id"]:
            raise ValidationError("Transcript comparison requires the same retained instrument identity")
        if calls[0][0]["event_id"]==calls[1][0]["event_id"]:
            raise ValidationError("Choose two different calls, not two captures of one call")
        if calls[0][4] and calls[1][4] and calls[0][4]>=calls[1][4]:
            raise ValidationError("Before call must precede after call")
    complete=all(c[1] for c in calls)
    numeric_allowed=complete and all(c[2]["automatic_quality_status"]!="blocked" for c in calls) and calls[0][1]["schema_version"]==calls[1][1]["schema_version"]
    for section in args.get("sections",SECTIONS):
        if not complete or section not in calls[0][3] or section not in calls[1][3]:
            rows.append(("transcript_section_change",dict(section=section,status="section_or_draft_unavailable",numeric_change=None)))
            continue
        changes=compare_section(section,calls[0][3].get(section),calls[1][3].get(section),numeric_allowed=numeric_allowed)
        rows.extend(("transcript_section_change",dict(section=section,**change)) for change in changes)
        if not changes: rows.append(("transcript_section_change",dict(section=section,status="both_summaries_empty",numeric_change=None)))
    return result("company.compare_transcripts",args,at,rows,lineage=refs,established=complete,
        warnings=(("selective_drafts","These are differences in saved model drafts. A topic omitted from a summary is not proof it disappeared from the call; preserve assessments and source citations."),))
