"""Dated guidance drafts and consecutive-call changes with original evidence."""
import re
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import loads_strict
from . import transcript_access as retained
from .transcript_research_access import ReadBudget, _captures, _call_date, _source_turns, _turn_ids
from .transcript_research_contracts import HISTORY, KINDS, parse, TURN_PATTERN
from .transcript_comparison import compare_section
from .retained_research_common import cutoff, result
from .research_extensions_contracts import GUIDANCE

def invoke(args, context, registry):
    args = dict(args)
    at = cutoff(args, context)
    history = parse(KINDS[HISTORY], {**dict(args), "sections": ["guidance"]})
    budget = ReadBudget()
    rows, refs, selected = [], [], []
    with retained.connection(context) as c:
        captures, _ = _captures(c, history, at, None)
        for source in captures:
            context.checkpoint()
            day, basis = _call_date(c, source, at, budget)
            if ("start_date" in args or "end_date" in args) and (
                day is None or not args.get("start_date", "") <= day <= args.get("end_date", "9999-12-31")):
                continue
            selected.append(dict(source, call_date=day, call_date_basis=basis))
        total = len(selected)
        # Select most recent known calls; unknown dates remain explicit.
        selected.sort(key=lambda s: (s["call_date"] or "", s["captured_at"], s["capture_id"]), reverse=True)
        selected = selected[:args["limit"]]
        selected.sort(key=lambda s: (s["call_date"] is None, s["call_date"] or "", s["captured_at"], s["capture_id"]))
        calls = []
        for source in selected:
            context.checkpoint()
            capture = source["capture_id"]
            analysis = retained._analysis(c, capture, at)
            quality = retained._assessment_status(c, analysis, at)
            meta = dict(source, **quality, analysis_id=analysis["analysis_id"] if analysis else None,
                extraction_available_at=analysis["available_at"] if analysis else None,
                source_available_at=analysis["source_available_at"] if analysis else None,
                schema_version=analysis["schema_version"] if analysis else None)
            guidance = None
            if analysis:
                if quality["automatic_quality_status"] == "blocked" and not args["include_blocked"]:
                    meta["guidance_status"] = "blocked_draft_excluded"
                else:
                    budget.require(analysis["output_bytes"], output=True)
                    output = loads_strict(c.execute("SELECT output_json FROM company_structured_transcript_outputs WHERE analysis_id=?",
                        (analysis["analysis_id"],)).fetchone()[0])
                    if not isinstance(output, dict): raise ValidationError("Saved transcript draft is not an object")
                    guidance = output.get("guidance")
                    if guidance is not None and not isinstance(guidance, list):
                        raise ValidationError("Saved guidance section must be a list")
                    meta["guidance_status"] = "section_unavailable" if guidance is None else "empty_saved_section" if not guidance else "saved_draft"
            else:
                meta["guidance_status"] = "no_draft_at_cutoff"
            rows.append(("guidance_call", meta))
            refs.append(retained.lineage(capture))
            if analysis: refs.append(retained.lineage(capture, analysis["analysis_id"]))
            ids = {t for item in guidance or [] for t in _turn_ids(item)}
            valid = sorted(t for t in ids if re.fullmatch(TURN_PATTERN, t) and int(t[1:]) <= source["total_turn_count"])
            if len(valid) > 40: raise ResourceLimitError("Guidance timeline exceeds 40 cited turns per call")
            for index, item in enumerate(guidance or []):
                rows.append(("guidance_item", dict(capture_id=capture, analysis_id=meta["analysis_id"],
                    call_date=source["call_date"], item_index=index, guidance=item)))
            for turn in _source_turns(c, source, valid, 0, at, budget) if valid else []:
                text = turn.pop("text")
                speaker = loads_strict(turn.pop("raw_turn_json")); speaker.pop("text", None)
                rows.append(("guidance_evidence", dict(turn, speaker=speaker,
                    text_excerpt=text[:1200], excerpt_truncated=len(text) > 1200)))
            meta["unresolved_citations"] = bool(ids - set(valid))
            if ids - set(valid):
                rows.append(("guidance_unresolved_references", dict(capture_id=capture, turn_ids=sorted(ids-set(valid)))))
            calls.append((meta, guidance))
    for (before, left), (after, right) in zip(calls, calls[1:]):
        comparable = (before["call_date"] is not None and after["call_date"] is not None
            and before["call_date"] < after["call_date"] and before["instrument_id"] == after["instrument_id"]
            and before["schema_version"] == after["schema_version"]
            and all(m["automatic_quality_status"] in ("accepted", "accepted_with_flags")
                and m["latest_review_outcome"] in (None, "accepted", "accepted_with_flags", "approved", "pass")
                and not m.get("unresolved_citations", False) for m in (before, after)))
        pair = dict(before_capture_id=before["capture_id"], after_capture_id=after["capture_id"],
            before_call_date=before["call_date"], after_call_date=after["call_date"])
        if left is None or right is None:
            changes = [dict(status="section_or_draft_unavailable", numeric_change=None)]
        elif not comparable:
            changes = [dict(status="comparison_not_established", numeric_change=None)]
        else:
            changes = compare_section("guidance", left, right) or [dict(status="both_summaries_empty", numeric_change=None)]
        rows.extend(("guidance_change", dict(pair, **change)) for change in changes)
    rows.insert(0, ("guidance_timeline", dict(ticker=args["ticker"], as_of=at, selected_calls=len(calls),
        eligible_calls=total, omitted_older_calls=max(0, total-len(calls)), order="actual_call_date_ascending_unknown_dates_last",
        selection="latest_capture_per_event_then_most_recent_calls", provider_requests=0, model_requests=0)))
    return result(GUIDANCE, args, at, rows, lineage=refs,
        established=any(g is not None for _, g in calls),
        warnings=(("saved_guidance_drafts", "Items are saved model drafts with separate quality and review status. Missing or omitted guidance is not evidence of withdrawal. Numeric changes require unique matching metric and period, compatible units, qualifier and schema, assessed nonblocked drafts, one identity and known increasing call dates."),))
