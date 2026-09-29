"""Selected Theta IV versus complete trailing cash-session log-return volatility."""
from datetime import date,timedelta
from decimal import Decimal,localcontext
from quant_data.errors import ValidationError
from quant_data.market.etf_calculations import sessions, CALENDAR_ID
from .retained_research_common import cutoff,compose,unpack,result,number
from .retained_research_sources import price_series,series_source

def trailing_volatility(prices, session, horizon):
    end=date.fromisoformat(session); lower=end-timedelta(days=horizon)
    try:
        days=sessions(lower-timedelta(days=10),end)
    except ValidationError:
        return dict(realized_volatility=None,reason="calendar_outside_supported_2024_2026",
                    return_count=0,missing_sessions=[],window_start=lower.isoformat())
    endpoints=[d for d in days if d>lower]
    preceding=[d for d in days if d<=lower]
    needed=([preceding[-1]] if preceding else [])+endpoints
    missing=[d.isoformat() for d in needed if number(prices.get(d.isoformat())) is None]
    base=dict(return_count=len(endpoints),missing_sessions=missing,window_start=lower.isoformat(),
              first_close_date=needed[0].isoformat() if needed else None,calendar_id=CALENDAR_ID)
    if not endpoints or endpoints[-1]!=end:
        return dict(base,realized_volatility=None,reason="missing_session_endpoint")
    if missing or len(needed)<3 or not preceding:
        return dict(base,realized_volatility=None,reason="missing_daily_session" if missing else "insufficient_history")
    values=[number(prices[d.isoformat()]) for d in needed]
    if any(v<=0 for v in values):
        return dict(base,realized_volatility=None,reason="nonpositive_close")
    with localcontext() as ctx:
        ctx.prec=34
        returns=[(b/a).ln() for a,b in zip(values,values[1:])]
        mean=sum(returns)/len(returns)
        variance=sum((r-mean)**2 for r in returns)/(len(returns)-1)
        rv=(variance*252).sqrt()
    return dict(base,realized_volatility=rv,reason="zero_variance" if rv==0 else None)

def invoke(args,context,registry):
    at=cutoff(args,context)
    theta=compose("options.get_daily_history","1.0.0",
        dict(symbol=args["symbol"],start_date=args["start_date"],end_date=args["end_date"],as_of=at),context,registry)
    horizon=args.get("horizon_days",30)
    start=(date.fromisoformat(args["start_date"])-timedelta(days=horizon+10)).isoformat()
    series=price_series(args["symbol"],start,args["end_date"],at,context,registry)
    if series.metadata["provider_symbol"]!=args["symbol"] or series.metadata["asset_type"]!="etf":
        raise ValidationError("Theta root must match the retained ETF identity")
    prices={o.period_end:o.value for o in series.observations}
    rows=[("comparison_price_source",series_source(series))]
    rows += [("comparison_theta_source",unpack(r)) for r in theta.records if r.record_type=="theta_source"]
    history=[]
    for record in theta.records:
        if record.record_type!="theta_daily": continue
        row=unpack(record)
        rv=trailing_volatility(prices,row["session"],horizon)
        if series.metadata.get("adjustment_status")!="split_adjusted_excluding_distributions":
            rv.update(realized_volatility=None,reason="price_basis_not_established")
        iv=number(row.get("atm_iv_"+str(horizon))); realized=rv["realized_volatility"]
        gap=iv-realized if iv is not None and realized is not None else None
        fields=dict(symbol=args["symbol"],session=row["session"],horizon_calendar_days=horizon,
            implied_volatility=iv,**rv,iv_minus_rv=gap,
            iv_to_rv=iv/realized if iv is not None and realized else None,
            annualized_variance_gap=iv**2-realized**2 if gap is not None else None,
            comparison_status="available" if gap is not None else "not_established",
            iv_missing_reason="missing_selected_anchor_iv" if iv is None else None,
            ratio_reason="zero_realized_volatility" if realized==0 else rv["reason"],
            option_capture_id=row.get("source_capture_id"),option_captured_at=row.get("source_captured_at"),option_semantic_sha256=row.get("source_semantic_sha256"),
            option_quality_flags_json=row.get("quality_flags_json"),
            method="sample_log_return_std_ddof1_sqrt252",volatility_units="annualized_decimal",
            interpretation="trailing_iv_rv_comparison_not_forward_variance_risk_premium",
            prior_comparable_count=len(history),
            spread_percentile_vs_prior=None if gap is None or not history else Decimal(100)*sum(x<=gap for x in history)/len(history))
        if gap is not None: history.append(gap)
        rows.append(("implied_realized_comparison",fields))
    return result("options.compare_implied_realized",args,at,rows,established=any(k=="implied_realized_comparison" for k,_ in rows),
        warnings=(("volatility_proxy","IV uses selected Theta anchors. Trailing price volatility excludes dividends; the variance gap is not an estimated forward risk premium."),))
