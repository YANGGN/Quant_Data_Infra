"""Read-only research projections over saved transcripts; no extraction or network."""
from datetime import date
import hashlib
import re
from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from .results import DiagnosticV1, QueryResult, fields_from_mapping
from . import transcript_access as retained
from .transcript_contracts import instant
from .transcript_research_contracts import (
    HISTORY, SEARCH, TARGET, KINDS, TURN_PATTERN, TranscriptResearchArguments,
    parse, encode_cursor, decode_cursor,
)

MAX_HISTORY_CAPTURES = 512
SEARCH_SCAN_LIMIT = 100
MAX_READ_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
EXCERPT_CHARS = 800

class ReadBudget:
    def __init__(self): self.read = 0; self.output = 0
    def require(self, size, *, output=False):
        if output:
            self.output += size
            if self.output > MAX_OUTPUT_BYTES:
                raise ResourceLimitError("Selected transcript summaries exceed 4 MiB; narrow the request")
        self.read += size
        if self.read > MAX_READ_BYTES:
            raise ResourceLimitError("Selected transcript evidence exceeds 32 MiB; narrow the request")

def _day(value):
    if not isinstance(value, str): return None
    try:
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value: return value
        # A timestamp retains the provider's date, without inventing an event time.
        if len(value) > 10 and value[10] == "T":
            from datetime import datetime
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        pass
    return None

def _page(c, source, manifest, cutoff, budget):
    if manifest["byte_count"] > retained.MAX_SELECTED_BYTES:
        raise ResourceLimitError("A selected transcript page exceeds 4 MiB")
    budget.require(manifest["byte_count"])
    body = c.execute("SELECT raw_body FROM company_equibles_transcript_pages WHERE capture_id=? AND page_index=?",
                     (source["capture_id"], manifest["page_index"])).fetchone()[0]
    if hashlib.sha256(body).hexdigest() != manifest["content_sha256"]:
        raise ValidationError("Transcript raw-page checksum differs")
    if manifest["captured_at"] > cutoff or manifest["captured_at"] > source["captured_at"]:
        raise ValidationError("Transcript page availability differs from its capture")
    from quant_data.company.equibles_transcripts import transcript_page
    parsed = transcript_page(body, symbol=source["symbol"], event={"id": source["event_id"],
        "fiscalYear": source["fiscal_year"], "fiscalQuarter": source["fiscal_quarter"]},
        offset=manifest["turn_offset"])
    if parsed["turnCount"] != manifest["turn_count"] or parsed["totalTurnCount"] != source["total_turn_count"]:
        raise ValidationError("Transcript page counts differ from its capture")
    return parsed

MANIFEST = "page_index,turn_offset,turn_count,content_sha256,captured_at,source_reference,length(raw_body) AS byte_count"

def _call_date(c, source, cutoff, budget):
    size = c.execute("SELECT length(CAST(event_json AS BLOB)) FROM company_equibles_transcripts WHERE capture_id=?",
                     (source["capture_id"],)).fetchone()[0]
    if size > 65536: raise ResourceLimitError("Transcript event metadata exceeds its bound")
    budget.require(size)
    event = loads_strict(c.execute("SELECT event_json FROM company_equibles_transcripts WHERE capture_id=?",
                                  (source["capture_id"],)).fetchone()[0])
    found = _day(event.get("callDate"))
    if found: return found, "event.callDate"
    manifest = c.execute("SELECT " + MANIFEST + " FROM company_equibles_transcript_pages WHERE capture_id=? AND page_index=0",
                         (source["capture_id"],)).fetchone()
    if manifest is None: return None, "not_established"
    value = _page(c, source, dict(manifest), cutoff, budget)
    found = _day(value.get("callDate"))
    return found, "raw_page.callDate" if found else "not_established"

def _source_turns(c, source, ids, context, cutoff, budget):
    requested = {int(t[1:]) for t in ids}
    total = source["total_turn_count"]
    if any(n > total for n in requested):
        raise ValidationError("A requested turn is outside this transcript")
    selected = {n for t in requested for n in range(max(1, t-context), min(total, t+context)+1)}
    manifests = [dict(r) for r in c.execute("SELECT " + MANIFEST +
        " FROM company_equibles_transcript_pages WHERE capture_id=? ORDER BY page_index", (source["capture_id"],))]
    result = []
    for m in manifests:
        if not any(m["turn_offset"] < n <= m["turn_offset"] + m["turn_count"] for n in selected): continue
        page = _page(c, source, m, cutoff, budget)
        for n, turn in enumerate(page["data"], m["turn_offset"] + 1):
            if n in selected:
                result.append({"capture_id": source["capture_id"], "turn_id": "t"+str(n), "turn": n,
                    "requested": n in requested, "page_index": m["page_index"],
                    "source_json_pointer": "/data/"+str(n-m["turn_offset"]-1),
                    "source_sha256": m["content_sha256"], "source_reference": m["source_reference"],
                    "source_captured_at": m["captured_at"], "text": turn["text"],
                    "raw_turn_json": dumps_strict(turn)})
    if [r["turn"] for r in result] != sorted(selected):
        raise ValidationError("Transcript turn coverage is incomplete")
    return result

def _captures(c, args, cutoff, anchor):
    clauses = ["r.captured_at<=?", """NOT EXISTS (
        SELECT 1 FROM company_equibles_transcripts newer
        WHERE newer.symbol=r.symbol AND newer.event_id=r.event_id AND newer.captured_at<=?
        AND (newer.captured_at,newer.capture_id)>(r.captured_at,r.capture_id))"""]
    params = [cutoff, cutoff]
    tickers = (args["ticker"],) if args.kind == KINDS[HISTORY] else args.get("tickers", ())
    if tickers:
        clauses.append("r.symbol IN (" + ",".join("?" for _ in tickers) + ")")
        params.extend(tickers)
    if anchor:
        clauses.append("(r.captured_at,r.capture_id)>(?,?)"); params.extend(anchor)
    bound = MAX_HISTORY_CAPTURES if args.kind == KINDS[HISTORY] else SEARCH_SCAN_LIMIT
    rows = [dict(r) for r in c.execute("SELECT " + ",".join("r."+k for k in retained.RAW_COLUMNS.split(",")) +
        " FROM company_equibles_transcripts r WHERE " + " AND ".join(clauses) +
        " ORDER BY r.captured_at,r.capture_id LIMIT ?", (*params, bound+1))]
    if args.kind == KINDS[HISTORY] and len(rows) > bound:
        raise ResourceLimitError("Transcript history exceeds 512 calls; use capture discovery for this ticker")
    return rows[:bound], len(rows) > bound

def _strings(value):
    if isinstance(value, str): yield value
    elif isinstance(value, list):
        for item in value: yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in ("source_turn_ids", "question_turn_ids", "answer_turn_ids"):
                yield from _strings(item)

def _turn_ids(value):
    if not isinstance(value, dict): return []
    return sorted({t for key in ("source_turn_ids", "question_turn_ids", "answer_turn_ids")
                   for t in (value[key] if isinstance(value.get(key), list) else []) if isinstance(t, str)},
                  key=lambda t: (0, int(t[1:])) if re.fullmatch(TURN_PATTERN, t) else (1, t))

def _research(c, args, cutoff, anchor, context, budget):
    rows, more = _captures(c, args, cutoff, anchor)
    counters = dict(scanned_calls=0, missing_extractions=0, excluded_blocked=0,
                    unknown_call_dates=0, excluded_date_range=0, unmatched_calls=0)
    selected = []; refs = []; last = None
    for offset, source in enumerate(rows):
        context.checkpoint()
        counters["scanned_calls"] += 1
        last = [source["captured_at"], source["capture_id"]]
        day, basis = _call_date(c, source, cutoff, budget)
        if day is None: counters["unknown_call_dates"] += 1
        if ("start_date" in args or "end_date" in args) and (
            day is None or not args.get("start_date", "") <= day <= args.get("end_date", "9999-12-31")):
            counters["excluded_date_range"] += 1; continue
        analysis = retained._analysis(c, source["capture_id"], cutoff)
        if analysis is None:
            counters["missing_extractions"] += 1; continue
        status = retained._assessment_status(c, analysis, cutoff)
        if status["automatic_quality_status"] == "blocked" and not args["include_blocked"]:
            counters["excluded_blocked"] += 1; continue
        budget.require(analysis["output_bytes"], output=True)
        output = loads_strict(c.execute("SELECT output_json FROM company_structured_transcript_outputs WHERE analysis_id=?",
                                       (analysis["analysis_id"],)).fetchone()[0])
        if not isinstance(output, dict): raise ValidationError("Stored extraction is not an object")
        fields = {**source, "call_date": day, "call_date_basis": basis, **status,
            "analysis_id": analysis["analysis_id"], "extraction_available_at": analysis["available_at"],
            "source_available_at": analysis["source_available_at"], "model": analysis["model"],
            "schema_version": analysis["schema_version"], "prompt_version": analysis["prompt_version"]}
        assessments = []
        for predicate in ("kind='automatic'", "kind!='automatic'"):
            selected_assessment = c.execute(
                "SELECT assessment_id,kind,outcome,available_at,length(CAST(assessment_json AS BLOB)) AS byte_count "
                "FROM company_structured_transcript_assessments WHERE analysis_id=? AND available_at<=? AND " +
                predicate + " ORDER BY available_at DESC,assessment_id DESC LIMIT 1",
                (analysis["analysis_id"], cutoff)).fetchone()
            if selected_assessment:
                a = dict(selected_assessment)
                budget.require(a.pop("byte_count"), output=True)
                a["assessment"] = loads_strict(c.execute(
                    "SELECT assessment_json FROM company_structured_transcript_assessments WHERE assessment_id=?",
                    (a["assessment_id"],)).fetchone()[0])
                assessments.append(a)
        fields["automatic_assessments_json"] = dumps_strict([a for a in assessments if a["kind"] == "automatic"])
        fields["latest_review_json"] = dumps_strict(next((a for a in assessments if a["kind"] != "automatic"), None))
        sections = {s: output.get(s) for s in args["sections"]}
        fields["missing_sections_json"] = dumps_strict([s for s in args["sections"] if s not in output])
        if args.kind == KINDS[HISTORY]:
            fields["sections_json"] = dumps_strict(sections)
        else:
            matches = []
            needle = args["query"].casefold()
            for section, value in sections.items():
                for i, item in enumerate(value if isinstance(value, list) else [value]):
                    if needle in " ".join(_strings(item)).casefold():
                        matches.append({"section": section, "item_index": i, "item": item,
                                        "source_turn_ids": _turn_ids(item)})
            if not matches:
                counters["unmatched_calls"] += 1; continue
            ids = sorted({t for m in matches for t in m["source_turn_ids"]}, key=lambda t: (0, int(t[1:])) if re.fullmatch(TURN_PATTERN, t) else (1, t))
            valid = [t for t in ids if re.fullmatch(TURN_PATTERN, t) and int(t[1:]) <= source["total_turn_count"]]
            # Excerpts retain original text and speaker fields; full text is separately addressable.
            turns = _source_turns(c, source, valid, 0, cutoff, budget) if valid else []
            excerpts = []
            for turn in turns:
                speaker = loads_strict(turn.pop("raw_turn_json"))
                speaker.pop("text", None)
                text = turn.pop("text")
                excerpts.append({**turn, "speaker": speaker, "text_excerpt": text[:EXCERPT_CHARS],
                    "excerpt_truncated": len(text) > EXCERPT_CHARS})
            fields.update(matches_json=dumps_strict(matches), evidence_json=dumps_strict(excerpts),
                unresolved_turn_ids_json=dumps_strict([t for t in ids if t not in valid]),
                evidence_status="referenced_source_turns" if valid else "no_valid_source_references")
        selected.append(fields)
        if args.kind == KINDS[SEARCH] and len(selected) >= args.limit:
            more = more or offset < len(rows)-1; break
    total = len(selected)
    if args.kind == KINDS[HISTORY]:
        # Unknown dates remain explicit and sort after known dates, never by fiscal quarter.
        selected.sort(key=lambda f: (f["call_date"] or "", f["captured_at"], f["capture_id"]), reverse=True)
        more = len(selected) > args.limit
        selected = selected[:args.limit]
    for row in selected:
        refs.extend((retained.lineage(row["capture_id"]), retained.lineage(row["capture_id"], row["analysis_id"])))
    cursor = encode_cursor(args, cutoff, last) if args.kind == KINDS[SEARCH] and more and last else None
    kind = "transcript_history_call" if args.kind == KINDS[HISTORY] else "transcript_evidence_match"
    return [retained.record(kind, row) for row in selected], refs, cursor, more, total, counters

def invoke(name, arguments, context):
    if not isinstance(arguments, TranscriptResearchArguments) or arguments.kind != KINDS.get(name):
        raise ValidationError("Transcript research requires its registered typed arguments")
    public = {k: list(v) if isinstance(v, tuple) else v for k, v in arguments.items()}
    args = parse(arguments.kind, public)
    cursor = decode_cursor(args) if args.get("cursor") else None
    cutoff = cursor["cutoff"] if cursor else args.get("as_of") or instant(context.clock.instant())
    if cutoff > instant(context.clock.instant()): raise ValidationError("Transcript cutoff is in the future")
    budget = ReadBudget()
    with retained.connection(context) as c:
        if name == TARGET:
            source = retained._raw(c, args["capture_id"], cutoff)
            turns = _source_turns(c, source, args["turn_ids"], args["context_turns"], cutoff, budget) if source else []
            records = [retained.record("transcript_turn", t) for t in turns]
            refs = [retained.lineage(args["capture_id"])] if source else []
            next_cursor = None; more = False; total = len(records); counters = {}
        else:
            records, refs, next_cursor, more, total, counters = _research(
                c, args, cutoff, cursor["anchor"] if cursor else None, context, budget)
    context.budget.require(rows=len(records), operations=max(1, len(records)))
    context.checkpoint()
    return QueryResult(tool=name, status="ok" if records else "not_established", records=tuple(records),
        lineage=tuple(refs),
        warnings=(WarningV1("local_capture_cutoff", "Availability is local capture and extraction completion, not historical public availability."),
                  WarningV1("selective_model_summary", "Structured summaries are selective model drafts. Omitted topics do not establish absence; source references do not establish factual correctness.")),
        diagnostics=(DiagnosticV1("transcript_research_selection", "Read retained data without fetching or extracting.",
            fields_from_mapping({"cutoff": cutoff, "mode": args["mode"], "next_cursor": next_cursor,
                "returned_page_items": len(records), "selection_complete": not more,
                "search_scope": "structured_summary_literal_case_insensitive" if name == SEARCH else None,
                "ordering": "actual_call_date_desc_unknown_last" if name == HISTORY else "capture_time_then_id" if name == SEARCH else "source_turn_number",
                "read_bytes": budget.read, **counters})),),
        truncation=TruncationV1(more, args.limit, len(records), total if name != SEARCH else None, more, next_cursor))
