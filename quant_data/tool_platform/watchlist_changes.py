"""Read-only change feed with stable source-version IDs."""
from datetime import date,timedelta
from quant_data.errors import StoreUnavailableError,ValidationError,ResourceLimitError
from quant_data.options.universe import TIER1_ETFS
from quant_data.company.retained_changes import changes
from .retained_research_common import cutoff,instant,compose,unpack,result,digest
from .retained_research_sources import price_series

DOMAINS=("earnings","estimates","transcripts","fundamentals","news","prices","options")

def change(symbol,domain,identity,available,detail,kind):
    return ("watchlist_change",dict(change_id=digest([symbol,domain,identity]),symbol=symbol,
        domain=domain,change_kind=kind,observed_at=available,source=detail))

def news_changes(symbol,since,at,context,registry):
    rows=[];cursor=None
    for page in range(4):
        public=dict(symbols=[symbol],mode="as_of",as_of=at,limit=500)
        if cursor: public["cursor"]=cursor
        value=compose("news.search","2.3.0",public,context,registry)
        for record in value.records:
            r=unpack(record)
            if r.get("available_at") and since<instant(r["available_at"])<=at:
                rows.append(change(symbol,"news",r["article_version_id"],instant(r["available_at"]),r,
                    "new_article" if r.get("version_sequence")==1 else "revised_article"))
        cursor=value.truncation.next_cursor
        if not value.truncation.has_more: return rows
        if not cursor: raise ResourceLimitError("News selection is incomplete; request other change domains separately")
    raise ResourceLimitError("News change scan exceeds 2000 articles per symbol")

def invoke(args,context,registry):
    at=cutoff(args,context);since=instant(args["since"])
    if since>=at: raise ValidationError("since must precede the effective as_of cutoff")
    rows=[]
    for symbol in args["symbols"]:
        domains=args.get("domains",DOMAINS)
        company_domains=set(domains)&{"earnings","estimates","transcripts"}
        if company_domains:
            try:
                source=changes(context,symbol,since,at,company_domains)
                for domain,r in source:
                    identity=r.get("observation_version_id") or r.get("analysis_id") or r.get("capture_id")
                    canonical_domain={"analyst-estimates":"estimates","transcript_capture":"transcripts","transcript_draft":"transcripts"}.get(domain,domain)
                    rows.append(change(symbol,canonical_domain,identity,r.get("available_at") or r["captured_at"],r,
                        domain if domain.startswith("transcript_") else "revised_value" if r.get("version_sequence",1)>1 else "new_retained_evidence"))
                rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domains=sorted(company_domains),status="complete",selection="capture_window")))
            except StoreUnavailableError:
                rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domains=sorted(company_domains),status="source_unavailable")))
        for domain in [d for d in domains if d not in company_domains]:
            try:
                if domain=="news":
                    rows.extend(news_changes(symbol,since,at,context,registry))
                elif domain=="fundamentals":
                    current=compose("company.get_fundamentals","3.0.0",
                        dict(symbol=symbol,mode="as_of",as_of=at,dimension=args.get("dimension","ARQ"),limit=100),context,registry)
                    if current.truncation.has_more: raise ResourceLimitError("Fundamental selection exceeds 100 observations")
                    for record in current.records:
                        r=unpack(record)
                        if since<instant(r["available_at"])<=at:
                            rows.append(change(symbol,domain,r["version_id"],instant(r["available_at"]),r,"retained_fundamental_version"))
                elif domain=="prices":
                    start=(date.fromisoformat(since[:10])-timedelta(days=7)).isoformat()
                    current=price_series(symbol,start,at[:10],at,context,registry)
                    for o in current.observations:
                        if o.available_at and since<instant(o.available_at)<=at:
                            rows.append(change(symbol,domain,o.version_id,instant(o.available_at),
                                dict(trade_date=o.period_end,close=o.value,instrument_id=current.metadata["instrument_id"],
                                    price_basis=current.metadata.get("adjustment_status"),capture_id=o.snapshot_id),"retained_close_version"))
                elif domain=="options":
                    if symbol not in TIER1_ETFS:
                        rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domain=domain,status="outside_theta_etf_scope")));continue
                    start=(date.fromisoformat(since[:10])-timedelta(days=7)).isoformat()
                    current=compose("options.get_daily_history","1.0.0",
                        dict(symbol=symbol,start_date=start,end_date=at[:10],as_of=at),context,registry)
                    for record in current.records:
                        if record.record_type!="theta_daily":continue
                        r=unpack(record)
                        if since<instant(r["source_captured_at"])<=at:
                            rows.append(change(symbol,domain,str(r["source_capture_id"]),instant(r["source_captured_at"]),r,"retained_theta_capture"))
                rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domain=domain,status="complete",
                    selection="latest_visible_versions_with_new_capture_time",
                    observation_window_start=start if domain in ("prices","options") else None,
                    dimension=args.get("dimension","ARQ") if domain=="fundamentals" else None)))
            except StoreUnavailableError:
                rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domain=domain,status="source_unavailable")))
            except ValidationError as exc:
                if not any(text in str(exc) for text in ("instrument is unavailable","outside the selected membership")):raise
                rows.append(("watchlist_domain_coverage",dict(symbol=symbol,domain=domain,status="identity_unavailable_at_cutoff")))
        context.checkpoint()
    rows.sort(key=lambda item:(item[1].get("observed_at",""),item[1].get("symbol",""),item[0],item[1].get("change_id","")))
    return result("research.get_watchlist_changes",args,at,rows,
        warnings=(("change_scope","Evidence changes are not trading signals. Prices and options cover the review window plus seven calendar days; fundamentals expose latest retained versions in the selected dimension, not every intermediate revision."),))
