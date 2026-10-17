"""Closed inputs and source declarations for retained-data research tools."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
import copy
import re
from quant_data.errors import ValidationError,ResourceLimitError
from quant_data.schema import validate_schema
from quant_data.options.universe import TIER1_ETFS
from .retained_research_common import instant,number,MAX_RECORDS
from .transcript_contracts import CAPTURE_PATTERN
from .transcript_research_contracts import SECTIONS
SUCCESSORS=("company.get_earnings_calendar","company.get_earnings_setup","company.get_consensus_history","company.get_estimate_revisions")
NEW_TOOLS=("options.compare_implied_realized","company.compare_transcripts","company.screen_fundamentals",
    "research.get_watchlist_changes","research.get_event_response_distribution","market.get_breadth",
    "data.get_research_coverage","data.get_collection_plan")
TOOLS=SUCCESSORS+NEW_TOOLS
VERSIONS={n:"2.0.0" if n in SUCCESSORS else "1.0.0" for n in TOOLS}
KINDS={n:"retained_"+n.replace(".","_")+"_v"+VERSIONS[n][0] for n in TOOLS}
MARKET=("market.stage10.instruments","market.stage10.daily_prices","market.stage10.source_evidence")
ANALYST=("company.fmp.analyst_evidence","company.fmp.analyst_observations")
SHARADAR=("company.sharadar.sf1","company.sharadar.evidence","market.collection.membership","market.collection.provider_mappings")
NEWS_DATASETS=("news.fmp.stock_latest_current_evidence","news.fmp.stock_latest_current_articles","news.current_multi_source_evidence","news.current_multi_source_articles")

def stores_for(name):
    if name=="data.get_collection_plan":return ()
    if name in ("company.get_earnings_calendar","company.get_consensus_history","company.get_estimate_revisions","company.compare_transcripts"):return ("company",)
    if name in ("options.compare_implied_realized","market.get_breadth"):return ("market",)
    if name=="company.screen_fundamentals":return ("market","company")
    if name=="research.get_event_response_distribution":return ("market","macro","company")
    return ("market","company","news")

def datasets_for(name):
    from .transcript_contracts import RAW_DATASET,STRUCTURED_DATASET
    transcript=(RAW_DATASET,STRUCTURED_DATASET)
    if name in ("company.get_earnings_calendar","company.get_consensus_history","company.get_estimate_revisions"):return ANALYST
    if name=="company.compare_transcripts":return transcript
    if name=="data.get_collection_plan":return ()
    if name in ("options.compare_implied_realized","market.get_breadth"):return MARKET
    if name=="company.screen_fundamentals":return tuple(dict.fromkeys(MARKET+SHARADAR))
    if name=="research.get_event_response_distribution":
        return tuple(dict.fromkeys(MARKET+ANALYST+('macro.fmp.economic_calendar_evidence', 'macro.fmp.economic_calendar_incremental_evidence', 'macro.fmp.economic_calendar_incremental_events')))
    return tuple(dict.fromkeys(MARKET+ANALYST+transcript+SHARADAR+NEWS_DATASETS))

def schema(kind):
    name=next((n for n,k in KINDS.items() if k==kind),None)
    if name is None:raise ValidationError("Unknown retained research contract")
    day={"type":"string","minLength":10,"maxLength":10}
    symbol={"type":"string","minLength":1,"maxLength":20}
    symbols={"type":"array","minItems":1,"maxItems":20,"items":symbol}
    props={"as_of":{"type":"string","minLength":20,"maxLength":40}}
    required=[]
    if name=="data.get_collection_plan":
        return {"type":"object","additionalProperties":False,"required":[],"properties":{
            "date":day,"symbols":symbols,"limit":{"type":"integer","minimum":1,"maximum":500}}}
    if name in ("company.get_earnings_calendar","company.screen_fundamentals","research.get_watchlist_changes","market.get_breadth","data.get_research_coverage"):
        props["symbols"]=copy.deepcopy(symbols);required.append("symbols")
        props["symbols"]["maxItems"]=50 if name=="market.get_breadth" else 10 if name=="research.get_watchlist_changes" else 20
    else:props["symbol"]=symbol;required.append("symbol")
    if name not in ("company.compare_transcripts","company.screen_fundamentals","research.get_watchlist_changes"):
        props.update(start_date=day,end_date=day);required+=["start_date","end_date"]
    if name in ("company.get_earnings_setup","company.get_consensus_history","company.get_estimate_revisions"):
        props["period"]={"type":"string","enum":["annual","quarter"]}
    if name in ("company.get_consensus_history","company.get_estimate_revisions","research.get_watchlist_changes"):
        props["since"]={"type":"string","minLength":20,"maxLength":40}
    if name=="company.get_consensus_history":props["include_analyst_context"]={"type":"boolean"}
    if name=="options.compare_implied_realized":
        props["symbol"]={"type":"string","enum":list(TIER1_ETFS)}
        props["horizon_days"]={"type":"integer","enum":[7,30,90]}
    if name=="company.compare_transcripts":
        props.update(before_capture_id={"type":"string","minLength":52,"maxLength":62},
            after_capture_id={"type":"string","minLength":52,"maxLength":62},
            sections={"type":"array","minItems":1,"maxItems":7,"items":{"type":"string","enum":list(SECTIONS)}})
        required+=["before_capture_id","after_capture_id"]
    if name in ("company.screen_fundamentals","research.get_watchlist_changes","data.get_research_coverage"):
        props["dimension"]={"type":"string","enum":["ARQ","ARY","ART","MRQ","MRY","MRT"]}
    if name=="company.screen_fundamentals":
        from .fundamental_screen import METRICS
        metric={"type":"string","enum":list(METRICS)}
        props.update(metrics={"type":"array","minItems":1,"maxItems":len(METRICS),"items":metric},
            rank_by=metric,ascending={"type":"boolean"},calendar_date=day,
            criteria={"type":"array","maxItems":8,"items":{"type":"object","additionalProperties":False,
                "required":["metric"],"properties":{"metric":metric,"minimum":{"type":"number"},"maximum":{"type":"number"}}}})
    if name in ("research.get_watchlist_changes","data.get_research_coverage"):
        props["domains"]={"type":"array","minItems":1,"maxItems":7,"items":{"type":"string","enum":["earnings","estimates","transcripts","fundamentals","news","prices","options"]}}
    if name=="research.get_watchlist_changes":required.append("since")
    if name=="research.get_event_response_distribution":
        props.update(event_source={"type":"string","enum":["earnings","macro"]},
            event_name={"type":"string","minLength":1,"maxLength":256},
            horizons={"type":"array","minItems":1,"maxItems":5,"items":{"type":"integer","minimum":1,"maximum":20}},
            include_options={"type":"boolean"})
    if name=="market.get_breadth":
        props.update(ma_windows={"type":"array","minItems":1,"maxItems":3,"items":{"type":"integer","minimum":2,"maximum":252}},
            high_low_window={"type":"integer","minimum":2,"maximum":252},
            leadership_window={"type":"integer","minimum":1,"maximum":252})
    return {"type":"object","additionalProperties":False,"required":required,"properties":props}

@dataclass(frozen=True)
class RetainedResearchArguments(Mapping):
    kind:str
    values:Mapping
    def __iter__(self):return iter(self.values)
    def __len__(self):return len(self.values)
    def __getitem__(self,key):return self.values[key]
    @property
    def limit(self):return MAX_RECORDS

def parse(kind,public):
    validate_schema(public,schema(kind));cooked=copy.deepcopy(dict(public))
    name=next(n for n,k in KINDS.items() if k==kind)
    if "symbol" in cooked and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}",cooked["symbol"]):
        raise ValidationError("Use exact uppercase retained symbols")
    for key in ("symbols","domains","sections","metrics","horizons","ma_windows"):
        if key in cooked:
            if len(set(cooked[key]))!=len(cooked[key]):raise ValidationError("Selections must contain no duplicates")
            if key=="symbols" and any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}",s) for s in cooked[key]):
                raise ValidationError("Use exact uppercase retained symbols")
            cooked[key]=tuple(sorted(cooked[key])) if key in ("symbols","horizons","ma_windows") else tuple(cooked[key])
    for key in ("date","start_date","end_date","calendar_date"):
        if key in cooked:
            try:
                d=date.fromisoformat(cooked[key])
                if d.isoformat()!=cooked[key] or d.year<1901 or d.year>2199:raise ValueError()
            except ValueError as exc:raise ValidationError("Use valid YYYY-MM-DD dates within 1901-2199") from exc
    if "start_date" in cooked:
        days=(date.fromisoformat(cooked["end_date"])-date.fromisoformat(cooked["start_date"])).days
        if days<0:raise ValidationError("Date range is reversed")
        maximum=3660 if name in ("company.get_consensus_history","company.get_estimate_revisions") else 365
        if days>maximum:raise ResourceLimitError("Date range exceeds this tool's calendar-day bound")
    for key in ("as_of","since"):
        if key in cooked:cooked[key]=instant(cooked[key])
    if cooked.get("since") and cooked.get("as_of") and cooked["since"]>=cooked["as_of"]:
        raise ValidationError("since must precede as_of")
    if name=="company.compare_transcripts":
        for key in ("before_capture_id","after_capture_id"):
            if not re.fullmatch(CAPTURE_PATTERN,cooked[key]):raise ValidationError("Use an exact saved transcript capture ID")
        if cooked["before_capture_id"]==cooked["after_capture_id"]:raise ValidationError("Choose two different calls")
    if name=="research.get_event_response_distribution" and (cooked.get("event_source","earnings")=="macro")!=("event_name" in cooked):
        raise ValidationError("Macro events require event_name; earnings events omit it")
    if "criteria" in cooked:
        for item in cooked["criteria"]:
            if not ("minimum" in item or "maximum" in item):raise ValidationError("Each criterion needs minimum or maximum")
            if any(number(item[k]) is None for k in ("minimum","maximum") if k in item):raise ValidationError("Criteria require finite numeric bounds")
            if "minimum" in item and "maximum" in item and number(item["minimum"])>number(item["maximum"]):
                raise ValidationError("Criterion bounds are reversed")
        cooked["criteria"]=tuple(MappingProxyType(v) for v in cooked["criteria"])
    return RetainedResearchArguments(kind,MappingProxyType(cooked))

DESCRIPTIONS=(
    "Read retained production earnings dates, actuals and expectations with source capture membership.",
    "Build a retained earnings briefing with consensus inputs, prior call drafts, news, prices and saved valuation context.",
    "Read retained consensus snapshots and analyst context with distinct fiscal targets and local capture times.",
    "Compare same-period consensus captures with currency/basis exclusions and source-version IDs.",
    "Compare selected Theta ETF IV with complete trailing-session realized volatility.",
    "Compare saved call summaries and compatible numeric guidance with original turn citations and separate quality statuses.",
    "Screen explicit Sharadar cohorts using transparent criteria and period/currency-specific ranks.",
    "Report retained watchlist changes between review cutoffs with stable change IDs and per-domain coverage.",
    "Summarize retrospective daily responses across retained earnings or macro events.",
    "Measure advances, declines, moving-average participation, highs/lows and persistent leaders for explicit tickers.",
    "Inspect per-symbol retained datasets, capture freshness, ranges and workflow input gaps.",
    "Read the saved Equibles queue, due reasons, backoff and recorded request budget without starting collection.")
