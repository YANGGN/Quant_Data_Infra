"""Per-symbol retained coverage and workflow input gaps."""
from datetime import date,datetime
from quant_data.errors import StoreUnavailableError,ValidationError
from quant_data.market.etf_calculations import sessions
from quant_data.options.universe import TIER1_ETFS
from .transcript_access import connection
from .retained_research_common import cutoff,compose,unpack,result,instant
from .retained_research_sources import price_series
DOMAINS=("earnings","estimates","transcripts","fundamentals","news","prices","options")

def company_coverage(context,symbol,at,domains):
    output=[]
    with connection(context) as c:
        for domain,endpoint in (("earnings","earnings"),("estimates","analyst-estimates")):
            if domain not in domains:continue
            rows=c.execute("SELECT instrument_id,cik,request_period,COUNT(*) captures,MIN(captured_at) first_capture,"
                "MAX(captured_at) last_capture FROM company_fmp_analyst_captures WHERE symbol=? AND endpoint=? AND captured_at<=? "
                "GROUP BY instrument_id,cik,request_period",(symbol,endpoint,at)).fetchall()
            if not rows:output.append(dict(domain=domain,status="not_established",captures=0))
            for row in rows:output.append(dict(domain=domain,status="identity_ambiguous" if len({(r["instrument_id"],r["cik"]) for r in rows})>1 else "available",**dict(row),
                availability="capture_history_not_historical_publication"))
        if "transcripts" in domains:
            rows=c.execute("SELECT instrument_id,COUNT(*) captures,MIN(captured_at) first_capture,MAX(captured_at) last_capture,"
                "MIN(fiscal_year) first_fiscal_year,MAX(fiscal_year) last_fiscal_year "
                "FROM company_equibles_transcripts WHERE symbol=? AND captured_at<=? GROUP BY instrument_id",(symbol,at)).fetchall()
            drafts={r[0]:r[1] for r in c.execute("SELECT t.instrument_id,COUNT(DISTINCT o.capture_id) FROM company_structured_transcript_outputs o "
                "JOIN company_equibles_transcripts t ON t.capture_id=o.capture_id WHERE t.symbol=? AND t.captured_at<=? "
                "AND o.available_at<=? AND o.source_available_at<=? GROUP BY t.instrument_id",(symbol,at,at,at))}
            if not rows:output.append(dict(domain="transcripts",status="not_established",captures=0,saved_draft_calls=0))
            for row in rows:output.append(dict(domain="transcripts",status="identity_ambiguous" if len(rows)>1 else "available",**dict(row),saved_draft_calls=drafts.get(row["instrument_id"],0),
                quality="draft_count_does_not_establish_approval"))
    return output

def readiness(statuses):
    workflows={"earnings_briefing":("earnings","estimates","transcripts"),
               "implied_realized":("prices","options"),"fundamental_screen":("fundamentals",)}
    return {name:dict(status="inputs_available" if all(statuses.get(d)=="available" for d in required) else "input_gaps",
        missing_inputs=[d for d in required if statuses.get(d)!="available"]) for name,required in workflows.items()}

def invoke(args,context,registry):
    at=cutoff(args,context);rows=[]
    domains=args.get("domains",DOMAINS)
    for symbol in args["symbols"]:
        coverage=[]
        wanted=set(domains)&{"earnings","estimates","transcripts"}
        if wanted:
            try:coverage+=company_coverage(context,symbol,at,wanted)
            except StoreUnavailableError:coverage += [dict(domain=d,status="source_unavailable") for d in sorted(wanted)]
        for domain in [d for d in domains if d not in wanted]:
            item=dict(domain=domain,status="not_established")
            try:
                if domain=="prices":
                    series=price_series(symbol,args["start_date"],args["end_date"],at,context,registry)
                    dates=[o.period_end for o in series.observations]
                    try:expected=[d.isoformat() for d in sessions(date.fromisoformat(args["start_date"]),date.fromisoformat(min(args["end_date"],at[:10])))]
                    except ValidationError:expected=None
                    gaps=sorted(set(expected)-set(dates)) if expected is not None else None
                    item.update(status="available" if dates else "not_established",observation_count=len(dates),
                        first_observation=dates[0] if dates else None,last_observation=dates[-1] if dates else None,
                        latest_capture=max((o.captured_at for o in series.observations),default=None),
                        instrument_id=series.metadata["instrument_id"],price_basis=series.metadata.get("adjustment_status"),
                        missing_sessions=gaps,calendar_status="US_cash_2024_2026" if expected is not None else "not_established",
                        lineage_digest=series.lineage_digest)
                elif domain=="options":
                    if symbol not in TIER1_ETFS:item["status"]="outside_theta_etf_scope"
                    else:
                        value=compose("options.get_coverage","1.0.0",dict(symbols=[symbol],start_date=args["start_date"],end_date=args["end_date"],as_of=at),context,registry)
                        found=next((unpack(r) for r in value.records if r.record_type=="theta_coverage"),{})
                        item.update(status="available" if found.get("present_sessions",0) else "not_established",
                            coverage=found,source_store="data/options.sqlite",provider="thetadata")
                elif domain=="fundamentals":
                    value=compose("company.get_fundamentals","3.0.0",dict(symbol=symbol,dimension=args.get("dimension","ARQ"),mode="as_of",as_of=at,limit=100),context,registry)
                    found=[unpack(r) for r in value.records]
                    item.update(status="available" if found else "not_established",returned_periods=len(found),
                        has_more=value.truncation.has_more,dimension=args.get("dimension","ARQ"),
                        first_reportperiod=min((r["reportperiod"] for r in found),default=None),
                        last_reportperiod=max((r["reportperiod"] for r in found),default=None),
                        latest_capture=max((r["available_at"] for r in found),default=None),
                        provider_subject=found[0]["provider_subject"] if found else None)
                elif domain=="news":
                    value=compose("news.search","2.3.0",dict(symbols=[symbol],mode="as_of",as_of=at,
                        start_date=args["start_date"],end_date=args["end_date"],limit=500),context,registry)
                    found=[unpack(r) for r in value.records]
                    item.update(status="available" if found else "not_established",returned_articles=len(found),
                        has_more=value.truncation.has_more,next_cursor=value.truncation.next_cursor,
                        latest_capture=max((r["available_at"] for r in found),default=None),
                        coverage_scope="publication_date_window",linkage="source_symbol_tags")
            except StoreUnavailableError:item["status"]="source_unavailable"
            except ValidationError as exc:
                if not any(x in str(exc) for x in ("instrument is unavailable","outside the selected membership")):raise
                item["status"]="identity_unavailable_at_cutoff"
            coverage.append(item)
        statuses={}
        for item in coverage:
            previous=statuses.get(item["domain"])
            statuses[item["domain"]]="available" if "available" in (previous,item["status"]) else item["status"]
            stamp=item.get("latest_capture") or item.get("last_capture")
            item.update(symbol=symbol,capture_cutoff=at,
                capture_age_hours=(datetime.fromisoformat(at.replace("Z","+00:00"))-datetime.fromisoformat(instant(stamp).replace("Z","+00:00"))).total_seconds()/3600 if stamp else None)
            rows.append(("research_dataset_coverage",item))
        rows.append(("research_input_readiness",dict(symbol=symbol,workflows=readiness(statuses),
            interpretation="input_presence_not_certified_research_quality",requested_domains=domains)))
    return result("data.get_research_coverage",args,at,rows)
