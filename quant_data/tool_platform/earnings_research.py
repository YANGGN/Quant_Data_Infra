"""Production earnings views over immutable FMP capture memberships."""
from quant_data.company.retained_analyst_reader import read_analyst, provenance
from .retained_research_common import cutoff, number, result

def earnings_rows(context, symbols, start, end, at):
    output = []
    for symbol in symbols:
        source = read_analyst(context, symbol, "earnings", at)
        for row in source:
            if row["source_event_date"] is None or not start <= row["source_event_date"] <= end: continue
            payload = row["payload"]
            actual = number(payload.get("epsActual"))
            revenue = number(payload.get("revenueActual"))
            output.append({**provenance(row), "event_date": row["source_event_date"],
                "date_status": "reported_actual_present" if actual is not None or revenue is not None else "scheduled_unconfirmed",
                "event_time_status": "date_only" if row["event_precision"] == "date" else "provider_timestamp",
                "eps_actual": actual, "eps_estimated": number(payload.get("epsEstimated")),
                "revenue_actual": revenue, "revenue_estimated": number(payload.get("revenueEstimated")),
                "fiscal_period_end": payload.get("fiscalDateEnding") or payload.get("referencePeriodEnd"),
                "source_payload": payload})
    return sorted(output, key=lambda r:(r["event_date"],r["symbol"],r["observation_version_id"]))

def calendar(args, context, registry):
    at = cutoff(args, context)
    rows = earnings_rows(context, args["symbols"], args["start_date"], args["end_date"], at)
    present = {r["symbol"] for r in rows}
    output = [("earnings_event", row) for row in rows]
    output += [("earnings_coverage", dict(symbol=symbol, status="events_in_range" if symbol in present else "no_retained_event_in_range",
        capture_cutoff=at)) for symbol in args["symbols"]]
    return result("company.get_earnings_calendar", args, at, output, established=bool(rows))

def setup(args, context, registry):
    from .retained_research_sources import transcript_history, price_context, news_context, valuation_context
    at = cutoff(args, context)
    symbol = args["symbol"]
    events = earnings_rows(context, (symbol,), args["start_date"], args["end_date"], at)
    rows = [("earnings_event", row) for row in events]
    estimates = read_analyst(context, symbol, "analyst-estimates", at, period=args.get("period", "quarter"))
    for item in estimates:
        rows.append(("earnings_consensus_input", {**provenance(item), "source_payload": item["payload"],
            "event_period_link": "not_inferred"}))
    identities={r["instrument_id"] for r in events+estimates if r.get("instrument_id")}
    anchor=next(iter(identities)) if len(identities)==1 else None
    for kind, reader in (("prior_call", transcript_history), ("price_context", price_context),
                          ("news_context", news_context), ("valuation_context", valuation_context)):
        for record_kind,fields in reader(symbol, args, at, context, registry):
            identity=fields.get("instrument_id") or fields.get("source",{}).get("metadata",{}).get("instrument_id")
            fields["cross_source_identity_status"]=("matched" if anchor and identity==anchor else
                "mismatch_context_only" if anchor and identity else "not_established_context_only")
            rows.append((record_kind,fields))
    return result("company.get_earnings_setup", args, at, rows,
        warnings=(("event_period_link", "Consensus target periods and announcement dates remain distinct; no fiscal-period match is guessed."),))
