"""Composition through existing host-selected immutable domain readers."""
from datetime import date, timedelta
from quant_data.errors import StoreUnavailableError, ValidationError, ResourceLimitError
from quant_data.market.provider_close_series import ProviderClosePriceRepository
from quant_data.market.stage10_series import Stage10DailyPriceQuery
from quant_data.stores import acquire_write_session
from .retained_research_common import compose, unpack, instant

def price_series(symbol, start, end, at, context, registry, *, volume=False, instrument_id=None):
    query = Stage10DailyPriceQuery(instrument_id or symbol, "instrument_id" if instrument_id else "provider_symbol",
        start, min(end, at[:10]), "as_of", at, "completed_date", 2000)
    with acquire_write_session(context.store_map, ("market",), timeout_seconds=1):
        repository = ProviderClosePriceRepository(context.store_map, registry)
        series = repository.get_volume_series(query) if volume else repository.get_close_series(query)
    context.checkpoint()
    if series.truncated:
        raise ResourceLimitError("Price inputs were truncated; narrow the date range")
    return series

def series_source(series):
    primitive = series.to_primitive()
    return {k: primitive[k] for k in ("series_id", "metadata", "audit", "provenance", "lineage_digest", "warnings", "unit", "frequency") if k in primitive}

def missing(kind, symbol, reason):
    return [(kind, dict(symbol=symbol, status="not_established", reason=reason))]

def transcript_history(symbol, args, at, context, registry):
    try:
        value = compose("company.get_transcript_history", "1.0.0",
            dict(ticker=symbol, mode="as_of", as_of=at, limit=2), context, registry)
    except StoreUnavailableError:
        return missing("prior_call", symbol, "transcript_store_unavailable")
    rows = [("prior_call", dict(symbol=symbol, **unpack(r))) for r in value.records]
    return rows or missing("prior_call", symbol, "no_eligible_saved_draft")

def price_context(symbol, args, at, context, registry):
    end = min(args["end_date"], at[:10])
    start = (date.fromisoformat(end) - timedelta(days=60)).isoformat()
    try:
        series = price_series(symbol, start, end, at, context, registry)
    except StoreUnavailableError:
        return missing("price_context", symbol, "market_store_unavailable")
    except ValidationError as exc:
        if "instrument is unavailable" not in str(exc): raise
        return missing("price_context", symbol, "instrument_unavailable_at_cutoff")
    observations = series.observations
    if not observations: return missing("price_context", symbol, "no_retained_prices")
    return [("price_context", dict(symbol=symbol, first_date=observations[0].period_end, last_date=observations[-1].period_end,
        first_close=observations[0].value, last_close=observations[-1].value,
        return_over_observed_window=observations[-1].value/observations[0].value-1 if observations[0].value else None,
        observed_closes=len(observations), source=series_source(series)))]

def news_context(symbol, args, at, context, registry):
    end = min(args["end_date"], at[:10])
    start = (date.fromisoformat(end) - timedelta(days=14)).isoformat()
    try:
        value = compose("news.search", "2.3.0", dict(symbols=[symbol], start_date=start, end_date=end,
            mode="as_of", as_of=at, limit=10), context, registry)
    except StoreUnavailableError:
        return missing("news_context", symbol, "news_store_unavailable")
    rows = [("news_context", dict(symbol=symbol, **unpack(r))) for r in value.records]
    rows.append(("news_context_coverage", dict(symbol=symbol, has_more=value.truncation.has_more,
        linkage="retained_source_symbol_tag_not_inferred_event_causality", window_start=start, window_end=end)))
    return rows

def valuation_context(symbol, args, at, context, registry):
    end = min(args["end_date"], at[:10])
    start = (date.fromisoformat(end)-timedelta(days=7)).isoformat()
    try:
        value = compose("company.get_forward_pe", "1.0.0", dict(ticker=symbol,start_date=start,end_date=end), context, registry)
    except StoreUnavailableError:
        return missing("valuation_context", symbol, "saved_publication_unavailable")
    summary = next((unpack(r) for r in value.records if r.record_type=="forward_pe_summary"), {})
    if not summary.get("snapshot_cutoff") or instant(summary["snapshot_cutoff"]) > at:
        return missing("valuation_context", symbol, "saved_publication_postdates_cutoff")
    return [("valuation_context", dict(symbol=symbol, record_kind=r.record_type, **unpack(r)))
        for r in value.records]
