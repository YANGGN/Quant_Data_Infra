"""Bounded immutable transcript readers for the local public tool boundary."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.stores import acquire_write_session, quiet_immutable_read_connection
from .results import DiagnosticV1, QueryResult, RecordV1, fields_from_mapping
from .transcript_contracts import (
    TOOLS, KINDS, RAW_DATASET, STRUCTURED_DATASET, TranscriptArgumentsV1,
    instant, encode_cursor, decode_cursor, parse, schema,
)

MAX_SELECTED_BYTES = 4 * 1024 * 1024
RAW_COLUMNS = "capture_id,symbol,instrument_id,event_id,fiscal_year,fiscal_quarter,total_turn_count,page_count,captured_at,run_id"

def record(kind, values):
    return RecordV1(kind, fields_from_mapping(values))

def lineage(capture, analysis=None):
    return LineageRef(dataset_id=STRUCTURED_DATASET if analysis else RAW_DATASET,
        store_role="company",semantic_id=analysis or capture,evidence_id=capture,
        canonical_version_id=analysis or capture)

@contextmanager
def connection(context):
    context.checkpoint()
    # Coordinate with the same physical lock used by the existing publishers.
    with acquire_write_session(context.store_map,("company",),timeout_seconds=1):
        with quiet_immutable_read_connection(context.store_map,"company") as c:
            interrupted=[]
            def checkpoint():
                try:context.checkpoint();return 0
                except Exception as exc:interrupted.append(exc);return 1
            c.set_progress_handler(checkpoint,1000)
            try:
                yield c
            finally:
                c.set_progress_handler(None,0)
                if interrupted:raise interrupted[0]
    context.checkpoint()

def _raw(c,capture,cutoff):
    row=c.execute("SELECT "+RAW_COLUMNS+" FROM company_equibles_transcripts WHERE capture_id=? AND captured_at<=?",(capture,cutoff)).fetchone()
    return None if row is None else dict(row)

def _analysis(c,capture,cutoff,chosen=None):
    where="capture_id=? AND available_at<=? AND source_available_at<=?"
    params=[capture,cutoff,cutoff]
    if chosen is not None:
        where+=" AND analysis_id=?";params.append(chosen)
    row=c.execute("SELECT analysis_id,request_identity,capture_id,input_sha256,model,reasoning_effort,prompt_version,schema_version,response_sha256,source_available_at,available_at,published_at,run_id,length(CAST(output_json AS BLOB)) AS output_bytes FROM company_structured_transcript_outputs WHERE "+where+" ORDER BY available_at DESC,analysis_id DESC LIMIT 1",params).fetchone()
    return None if row is None else dict(row)

def _assessment_status(c,analysis,cutoff):
    if analysis is None:return {"automatic_quality_status":"unassessed","review_status":"unreviewed","latest_review_outcome":None}
    params=(analysis["analysis_id"],cutoff)
    automatic=c.execute("SELECT outcome FROM company_structured_transcript_assessments WHERE analysis_id=? AND available_at<=? AND kind='automatic' ORDER BY available_at DESC,assessment_id DESC LIMIT 1",params).fetchone()
    review=c.execute("SELECT outcome FROM company_structured_transcript_assessments WHERE analysis_id=? AND available_at<=? AND kind!='automatic' ORDER BY available_at DESC,assessment_id DESC LIMIT 1",params).fetchone()
    return {"automatic_quality_status":automatic[0] if automatic else "unassessed",
        "review_status":"review_recorded" if review else "unreviewed","latest_review_outcome":review[0] if review else None}

def _search(c,args,cutoff,anchor):
    clauses=["captured_at<=?"];params=[cutoff]
    for column,key in (("symbol","ticker"),("fiscal_year","fiscal_year"),("fiscal_quarter","fiscal_quarter")):
        if args[key] is not None:clauses.append(column+"=?");params.append(args[key])
    if anchor:
        clauses.append("(symbol,fiscal_year,fiscal_quarter,captured_at,capture_id)>(?,?,?,?,?)")
        params.extend(anchor)
    rows=[dict(r) for r in c.execute("SELECT "+RAW_COLUMNS+" FROM company_equibles_transcripts WHERE "+
        " AND ".join(clauses)+" ORDER BY symbol,fiscal_year,fiscal_quarter,captured_at,capture_id LIMIT ?",(*params,args.limit+1))]
    more=len(rows)>args.limit;rows=rows[:args.limit]
    records=[];refs=[]
    for row in rows:
        analysis=_analysis(c,row["capture_id"],cutoff)
        fields={**row,"extraction_available":analysis is not None,
            "analysis_id":analysis["analysis_id"] if analysis else None,
            "extraction_available_at":analysis["available_at"] if analysis else None,
            **_assessment_status(c,analysis,cutoff)}
        records.append(record("transcript_capture",fields))
        refs.append(lineage(row["capture_id"]))
        if analysis:refs.append(lineage(row["capture_id"],analysis["analysis_id"]))
    cursor=encode_cursor(args,cutoff,[rows[-1][k] for k in ("symbol","fiscal_year","fiscal_quarter","captured_at","capture_id")]) if more else None
    return records,refs,cursor,len(rows),None

def _turns(c,args,cutoff,anchor):
    source=_raw(c,args.capture_id,cutoff)
    if source is None:return [],[],None,0,0
    start=anchor or 0;total=source["total_turn_count"]
    if start>=total and anchor is not None:raise ValidationError("Transcript cursor is outside this capture")
    end=min(start+args.limit,total)
    manifests=[dict(r) for r in c.execute(
        "SELECT page_index,turn_offset,turn_count,content_sha256,captured_at,source_reference,artifact_id,run_id,length(raw_body) AS byte_count FROM company_equibles_transcript_pages WHERE capture_id=? AND turn_offset<? AND turn_offset+turn_count>? ORDER BY page_index",
        (args.capture_id,end,start))]
    if sum(p["byte_count"] for p in manifests)>MAX_SELECTED_BYTES:
        raise ResourceLimitError("Selected transcript pages exceed the read byte bound")
    event_bytes=c.execute("SELECT length(CAST(event_json AS BLOB)) FROM company_equibles_transcripts WHERE capture_id=?",(args.capture_id,)).fetchone()[0]
    if event_bytes>65536:raise ResourceLimitError("Transcript event metadata exceeds its bound")
    event=c.execute("SELECT event_json FROM company_equibles_transcripts WHERE capture_id=?",(args.capture_id,)).fetchone()[0]
    records=[record("transcript_metadata",{**source,"event_json":event,"first_turn":start+1,
        "returned_turns":end-start,"source_text_policy":"original_provider_text"})]
    turns=[]
    from quant_data.company.equibles_transcripts import transcript_page
    for manifest in manifests:
        body=c.execute("SELECT raw_body FROM company_equibles_transcript_pages WHERE capture_id=? AND page_index=?",(args.capture_id,manifest["page_index"])).fetchone()[0]
        if hashlib.sha256(body).hexdigest()!=manifest["content_sha256"]:
            raise ValidationError("Transcript raw-page checksum differs")
        if manifest["captured_at"]>cutoff or manifest["captured_at"]>source["captured_at"]:
            raise ValidationError("Transcript page availability differs from its capture")
        value=transcript_page(body,symbol=source["symbol"],event={"id":source["event_id"],
            "fiscalYear":source["fiscal_year"],"fiscalQuarter":source["fiscal_quarter"]},offset=manifest["turn_offset"])
        if value["turnCount"]!=manifest["turn_count"] or value["totalTurnCount"]!=total:
            raise ValidationError("Transcript page counts differ from its capture")
        for index,turn in enumerate(value["data"],manifest["turn_offset"]):
            if start<=index<end:
                turns.append(index)
                records.append(record("transcript_turn",{"capture_id":args.capture_id,"turn":index+1,
                    "turn_id":"t"+str(index+1),"page_index":manifest["page_index"],
                    "source_json_pointer":"/data/"+str(index-manifest["turn_offset"]),
                    "source_sha256":manifest["content_sha256"],"source_reference":manifest["source_reference"],
                    "source_captured_at":manifest["captured_at"],"text":turn["text"],
                    "raw_turn_json":dumps_strict(turn)}))
    if turns!=list(range(start,end)):raise ValidationError("Transcript turn coverage is incomplete")
    cursor=encode_cursor(args,cutoff,end) if end<total else None
    return records,[lineage(args.capture_id)],cursor,end-start,total

def _extraction(c,args,cutoff,anchor):
    source=_raw(c,args.capture_id,cutoff)
    if source is None:return [],[],None,0,None
    analysis=_analysis(c,args.capture_id,cutoff,anchor[0] if anchor else None)
    if analysis is None:
        return [record("transcript_extraction_status",{"capture_id":args.capture_id,
            "extraction_available":False,"reason":"no_extraction_available_at_cutoff"})],[
            lineage(args.capture_id)],None,0,None
    where="analysis_id=? AND available_at<=?";params=[analysis["analysis_id"],cutoff]
    if anchor:
        where+=" AND (available_at,assessment_id)>(?,?)";params.extend(anchor[1:])
    rows=[dict(r) for r in c.execute(
        "SELECT assessment_id,analysis_id,kind,evaluator,reasoning_effort,outcome,evidence_sha256,available_at,published_at,run_id,length(CAST(assessment_json AS BLOB)) AS assessment_bytes FROM company_structured_transcript_assessments WHERE "+
        where+" ORDER BY available_at,assessment_id LIMIT ?",(*params,args.limit+1))]
    more=len(rows)>args.limit;rows=rows[:args.limit]
    if analysis["output_bytes"]+sum(r["assessment_bytes"] for r in rows)>MAX_SELECTED_BYTES:
        raise ResourceLimitError("Structured output and assessments exceed the read byte bound")
    output=c.execute("SELECT output_json FROM company_structured_transcript_outputs WHERE analysis_id=?",(analysis["analysis_id"],)).fetchone()[0]
    if not isinstance(loads_strict(output),dict):raise ValidationError("Stored extraction is not an object")
    fields={k:v for k,v in analysis.items() if k!="output_bytes"}
    fields.update(symbol=source["symbol"],fiscal_year=source["fiscal_year"],fiscal_quarter=source["fiscal_quarter"],
        extraction_available=True,structured_json=output,**_assessment_status(c,analysis,cutoff))
    records=[record("transcript_extraction",fields)]
    refs=[lineage(args.capture_id),lineage(args.capture_id,analysis["analysis_id"])]
    for row in rows:
        assessment=c.execute("SELECT assessment_json FROM company_structured_transcript_assessments WHERE assessment_id=?",(row["assessment_id"],)).fetchone()[0]
        loads_strict(assessment)
        records.append(record("transcript_assessment",{**{k:v for k,v in row.items() if k!="assessment_bytes"},"assessment_json":assessment}))
        refs.append(LineageRef(dataset_id=STRUCTURED_DATASET,store_role="company",semantic_id=row["assessment_id"],
            evidence_id=args.capture_id,canonical_version_id=analysis["analysis_id"]))
    cursor=encode_cursor(args,cutoff,[analysis["analysis_id"],rows[-1]["available_at"],rows[-1]["assessment_id"]]) if more else None
    return records,refs,cursor,len(rows),None

def invoke_transcript(name,arguments,context):
    if name not in TOOLS or not isinstance(arguments,TranscriptArgumentsV1) or arguments.kind!=KINDS[name]:
        raise ValidationError("Transcript tool requires its registered typed arguments")
    # Recheck direct in-process construction as well as dispatcher-decoded input.
    allowed=schema(arguments.kind)["properties"]
    public={k:arguments[k] for k in arguments if k in allowed and arguments[k] is not None}
    arguments=parse(arguments.kind,public)
    context.checkpoint()
    cursor=decode_cursor(arguments) if arguments.cursor else None
    cutoff=cursor["cutoff"] if cursor else arguments.as_of or instant(context.clock.instant())
    if cutoff>instant(context.clock.instant()):raise ValidationError("Transcript cutoff is in the future")
    anchor=cursor["anchor"] if cursor else None
    with connection(context) as c:
        records,refs,next_cursor,count,total={TOOLS[0]:_search,TOOLS[1]:_turns,TOOLS[2]:_extraction}[name](c,arguments,cutoff,anchor)
    context.budget.require(rows=len(records),operations=max(1,len(records)))
    context.checkpoint()
    warnings=[WarningV1("source_dates_unverified","Fiscal labels and call dates are provider supplied; availability is retained local capture time.")]
    if name!=TOOLS[1]:
        warnings.append(WarningV1("model_drafts_require_assessment","Stored extractions are original model drafts; preserve automatic flags and separate review outcomes."))
    established=bool(records) and records[0].record_type!="transcript_extraction_status"
    return QueryResult(tool=name,status="ok" if established else "not_established",records=tuple(records),
        lineage=tuple(refs),warnings=tuple(warnings),
        diagnostics=(DiagnosticV1("transcript_selection","Read retained transcript data without fetching or extracting.",
            fields_from_mapping({"cutoff":cutoff,"mode":arguments.mode,"next_cursor":next_cursor,
                "page_unit":"captures" if name==TOOLS[0] else "speaker_turns" if name==TOOLS[1] else "assessments",
                "returned_page_items":count,"availability_basis":"raw_capture_and_model_assessment_completion",
                "historical_publication_time":"not_established"})),),
        truncation=TruncationV1(bool(next_cursor),arguments.limit,count,total,bool(next_cursor),next_cursor))
