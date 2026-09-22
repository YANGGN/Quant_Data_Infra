"""Finite host-owned collection manifests, independent of execution and credentials.

HTTP identity is independent of membership versions. A new universe therefore
cannot turn an already retained response into a new acquisition. Incremental
observations explicitly name a new observation window.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import date
import hashlib
import re
from collections import Counter
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..market.collection_universe import _utc
from ..market.collection_bindings import PinnedCollection, BINDING_IDS

MAX_UNITS = 200000
MAX_SELECTED = 5000
PROVIDERS = frozenset(("fmp", "sec", "sharadar", "equibles", "alpaca", "public_news"))
MODES = frozenset(("historical_backfill", "incremental", "correction"))
# This is a host dispatch allowlist, never a user-selected URL.
ENDPOINTS = {
 "daily_prices": {"fmp": frozenset(("historical-price-eod/full",))},
 "sec_filings_companyfacts": {"sec": frozenset(("submissions", "submissions-history", "companyfacts"))},
 "dividends_splits": {"fmp": frozenset(("dividends", "splits"))},
 "earnings_dates": {"fmp": frozenset(("earnings",))},
 "fmp_statements": {"fmp": frozenset(("income-statement", "balance-sheet-statement", "cash-flow-statement", "financial-statement-full-as-reported"))},
 "fmp_analyst_estimates": {"fmp": frozenset(("analyst-estimates",))},
 "fmp_distinct_inputs": {"fmp": frozenset(("grades", "grades-historical", "grades-consensus", "price-target-consensus", "price-target-summary", "price-target", "revenue-product-segmentation"))},
 "sharadar_fundamentals": {"sharadar": frozenset(("SHARADAR/SF1","SHARADAR/SF1/metadata","SHARADAR/INDICATORS","SHARADAR/INDICATORS/metadata","SHARADAR_DIRECT/fundamentals","SHARADAR_DIRECT/descriptions"))},
 "equibles_transcripts": {"equibles": frozenset(("earnings-call-catalogue", "earnings-call-transcript"))},
 "news": {"fmp": frozenset(("news/stock-latest", "news/press-releases-latest", "news/general-latest")),
          "alpaca": frozenset(("news",)),
          "public_news": frozenset(("fed_press", "ecb_press", "bea_news", "eia_press", "finviz", "financial_juice"))},
}
PARAMETERS = frozenset(("symbol", "symbols", "ticker", "dimension", "period", "limit", "page",
    "from", "to", "start", "end", "cik", "table", "file", "event_id", "offset", "qopts.per_page", "qopts.cursor_id",
    "calendardate.gte", "calendardate.lte", "lastupdated.gte", "lastupdated.lte", "page_token", "sort", "include_content", "format", "tablename"))

def digest(value):
    return hashlib.sha256(dumps_strict(value).encode()).hexdigest()

def text_value(value, name, limit=256):
    if not isinstance(value, str) or not value or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValidationError("Collection "+name+" is invalid")
    return value

def positive(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ResourceLimitError("Collection "+name+" exceeds its bound")
    return value

@dataclass(frozen=True)
class AcquisitionUnit:
    collection: str
    provider: str
    endpoint: str
    subject: str
    parameters: tuple[tuple[str,str], ...]
    mode: str
    observation_window: str
    selected_symbols: tuple[str,...]
    selection_sha256: str
    max_requests: int = 1
    max_response_bytes: int = 8*1024*1024
    max_rows: int = 10000
    timeout_seconds: int = 30

    def request_material(self):
        return {"provider":self.provider, "endpoint":self.endpoint, "subject":self.subject,
                "parameters":dict(self.parameters)}
    @property
    def request_id(self):
        return digest(self.request_material())
    @property
    def unit_id(self):
        return digest({"request":self.request_id,"observation_window":
            "historical_backfill" if self.mode=="historical_backfill" else _utc(self.observation_window)})
    @property
    def owner(self):
        return "market" if self.collection=="daily_prices" else "news" if self.collection=="news" else "company"

def validate_unit(unit):
    if not isinstance(unit, AcquisitionUnit):
        raise ValidationError("A typed collection unit is required")
    if unit.endpoint not in ENDPOINTS.get(unit.collection,{}).get(unit.provider,()):
        raise ValidationError("Collection endpoint is outside its binding")
    if unit.mode not in MODES:
        raise ValidationError("Collection acquisition mode is invalid")
    text_value(unit.subject,"provider subject",4096)
    text_value(unit.observation_window,"observation window")
    if unit.mode!="historical_backfill":
        _utc(unit.observation_window)
    if not re.fullmatch("[a-f0-9]{64}",unit.selection_sha256):
        raise ValidationError("Collection selection hash is invalid")
    if not isinstance(unit.parameters,tuple) or len(unit.parameters)>20:
        raise ValidationError("Collection parameters are invalid")
    seen={}
    for pair in unit.parameters:
        if not isinstance(pair,tuple) or len(pair)!=2:
            raise ValidationError("Collection parameter pair is invalid")
        k,v=pair
        if k not in PARAMETERS or k in seen:
            raise ValidationError("Collection parameter is unknown or duplicated")
        seen[k]=text_value(v,"parameter",8192)
        if k in ("page","limit","offset","qopts.per_page"):
            if not v.isascii() or not v.isdigit():
                raise ValidationError("Collection numeric parameter is invalid")
    if tuple(sorted(seen.items()))!=unit.parameters:
        raise ValidationError("Collection parameters must be canonical")
    if (not isinstance(unit.selected_symbols,tuple) or len(unit.selected_symbols)>MAX_SELECTED
        or tuple(sorted(set(unit.selected_symbols)))!=unit.selected_symbols
        or any(not re.fullmatch("[A-Z0-9][A-Z0-9.\\^-]{0,31}",s) for s in unit.selected_symbols)):
        raise ValidationError("Collection selected symbols are invalid")

    if unit.collection=="news":
        if unit.provider=="alpaca":
            if seen.get("symbols")!=unit.subject or len(unit.subject.split(","))>50:
                raise ValidationError("Alpaca news requires its exact bounded provider symbols")
        elif unit.subject!="shared_feed":
            raise ValidationError("Global news requests use one shared feed subject")
    elif unit.provider=="fmp":
        if seen.get("symbol")!=unit.subject or "," in unit.subject:
            raise ValidationError("FMP request symbol differs from its provider subject")
    elif unit.provider=="sec":
        if not re.fullmatch("[0-9]{10}",unit.subject) or seen.get("cik")!=unit.subject:
            raise ValidationError("SEC request requires its exact CIK")
        if unit.endpoint=="submissions-history":
            if not re.fullmatch("CIK"+unit.subject+"-submissions-[0-9]{3}\\.json",seen.get("file","")):
                raise ValidationError("SEC historical submissions filename differs")
    elif unit.provider=="equibles" and seen.get("symbol")!=unit.subject:
        raise ValidationError("Equibles request symbol differs")
    elif unit.provider=="sharadar" and unit.endpoint=="SHARADAR_DIRECT/descriptions":
        from ..company.sharadar_direct import DEFINITION_PARAMETERS
        if unit.subject!="fundamentals" or unit.selected_symbols or seen!=DEFINITION_PARAMETERS:
            raise ValidationError("Direct descriptions require their exact bounded fundamentals scope")
    elif unit.provider=="sharadar" and unit.endpoint=="SHARADAR_DIRECT/fundamentals":
        from ..company.sharadar_direct import _parameters
        _parameters(seen)
        if int(seen["limit"])>unit.max_rows:
            raise ResourceLimitError("Direct page size exceeds its unit row cap")
    elif unit.provider=="sharadar" and unit.endpoint.endswith("/metadata"):
        if unit.subject!=unit.endpoint.removesuffix("/metadata") or seen or unit.selected_symbols:
            raise ValidationError("Nasdaq metadata uses one unfiltered table request")
    elif unit.provider=="sharadar" and unit.endpoint=="SHARADAR/INDICATORS":
        if (unit.subject!="SHARADAR/INDICATORS:SF1" or unit.selected_symbols or seen.get("table")!="SF1"
            or set(seen)-{"table","qopts.per_page","qopts.cursor_id"}
            or ("qopts.per_page" in seen and not 1<=int(seen["qopts.per_page"])<=10000)):
            raise ValidationError("Sharadar definitions require a bounded SF1-only table request")
    elif unit.provider=="sharadar":
        if seen.get("dimension") not in ("ARQ","ARY","ART","MRQ","MRY","MRT") or not seen.get("ticker"):
            raise ValidationError("SF1 request needs an explicit reporting dimension and tickers")
        if seen.get("qopts.per_page") is not None and not 1<=int(seen["qopts.per_page"])<=10000:
            raise ValidationError("SF1 page size exceeds its contract")
    for first,last in (("from","to"),("start","end"),("calendardate.gte","calendardate.lte"),("lastupdated.gte","lastupdated.lte")):
        for key in (first,last):
            if key in seen:
                try: parsed=date.fromisoformat(seen[key])
                except ValueError as exc: raise ValidationError("Collection date filter is invalid") from exc
                if parsed.isoformat()!=seen[key]: raise ValidationError("Collection date filter is invalid")
        if first in seen and last in seen and seen[first]>seen[last]:
            raise ValidationError("Collection date window is reversed")
    positive(unit.max_requests,"partition request cap",1000)
    positive(unit.max_response_bytes,"response byte cap",64*1024*1024)
    positive(unit.max_rows,"row cap",100000)
    positive(unit.timeout_seconds,"request timeout",120)
    return unit

@dataclass(frozen=True)
class ProviderBudget:
    provider: str
    max_requests: int
    max_total_bytes: int
    max_run_seconds: int
    min_interval_milliseconds: int
    daily_ceiling: int | None = None

def validate_budget(b):
    if not isinstance(b,ProviderBudget) or b.provider not in PROVIDERS:
        raise ValidationError("Collection provider budget is invalid")
    positive(b.max_requests,"invocation request cap",1000)
    positive(b.max_total_bytes,"invocation byte cap",1024*1024*1024)
    positive(b.max_run_seconds,"invocation duration",7200)
    positive(b.min_interval_milliseconds,"request spacing",60000)
    if b.provider=="equibles":
        if (b.daily_ceiling!=100000 or b.min_interval_milliseconds<1000
            or b.max_total_bytes>400*1024*1024 or b.max_run_seconds>3600):
            raise ValidationError("Equibles paid-plan bounds differ")
    elif b.daily_ceiling is not None:
        positive(b.daily_ceiling,"daily ceiling",1000000)
    return b

@dataclass(frozen=True)
class RetainedResponse:
    request_id: str
    captured_at: str
    content_sha256: str
    source_reference: str
    transport_complete: bool
    coverage: str
    raw_verified: bool
    published: bool
    capture_id: str
    observation_window: str | None = None

def reusable(unit, retained, *, cutoff):
    """Only exact requests and verified evidence suppress acquisition.

    Partial histories may still contain a complete single HTTP response. Reuse
    those bytes for pagination or parsing; never relabel them complete history.
    """
    validate_unit(unit)
    at=_utc(cutoff)
    candidates=[]
    for item in retained:
        if (not isinstance(item,RetainedResponse) or item.request_id!=unit.request_id):
            continue
        if item.coverage not in ("complete_request","partial_history","complete_partition","unknown"):
            raise ValidationError("Retained collection coverage is invalid")
        if not re.fullmatch("[a-f0-9]{64}",item.content_sha256):
            raise ValidationError("Retained collection content hash is invalid")
        captured=_utc(item.captured_at)
        if captured>at or not item.raw_verified or not item.transport_complete:
            continue
        if unit.mode!="historical_backfill" and item.observation_window!=unit.observation_window:
            continue
        candidates.append(item)
    return max(candidates,key=lambda x:(_utc(x.captured_at),x.capture_id),default=None)

@dataclass(frozen=True)
class CollectionPlan:
    created_at: str
    units: tuple[AcquisitionUnit,...]
    budgets: tuple[ProviderBudget,...]
    reuse: tuple[tuple[str,RetainedResponse],...]
    identity_gaps: tuple[tuple[str,str,str],...]
    omitted_collections: tuple[str,...]
    blocked_units: tuple[tuple[str,str],...] = ()

    def material(self):
        return {"contract":"quant_data.collection_plan.v1","created_at":self.created_at,
            "units":[asdict(u) for u in self.units],"budgets":[asdict(b) for b in self.budgets],
            "reuse":[[key,asdict(value)] for key,value in self.reuse],
            "identity_gaps":[list(x) for x in self.identity_gaps],"omitted_collections":list(self.omitted_collections),"blocked_units":[list(x) for x in self.blocked_units]}
    @property
    def plan_id(self): return digest(self.material())
    def report(self):
        reused={key for key,_ in self.reuse}
        blocked={key for key,_ in self.blocked_units}
        counts=Counter()
        requests=Counter()
        for unit in self.units:
            counts[unit.provider]+=1
            if unit.unit_id not in reused and unit.unit_id not in blocked:
                requests[unit.provider]+=unit.max_requests
        return {"plan_id":self.plan_id,"unit_count":len(self.units),"identity_gaps":len(self.identity_gaps),
            "retained_request_reuses":len(reused),"blocked_units":len(blocked),"maximum_new_requests_by_provider":dict(sorted(requests.items())),
            "units_by_provider":dict(sorted(counts.items())),"omitted_collections":list(self.omitted_collections),
            "live_authorization":False,"whole_history_complete":False}

def build_plan(*,selections,units,budgets,retained=(),created_at):
    at=_utc(created_at)
    selections=tuple(selections);units=tuple(units);budgets=tuple(budgets);retained=tuple(retained)
    if len(units)>MAX_UNITS:
        raise ResourceLimitError("Collection plan has too many units")
    if any(not isinstance(s,PinnedCollection) for s in selections):
        raise ValidationError("Collection plan requires pinned selections")
    bound={s.binding.id:s for s in selections}
    if len(bound)!=len(selections):
        raise ValidationError("Collection selections are duplicated")
    provider_budgets={b.provider:validate_budget(b) for b in budgets}
    if len(provider_budgets)!=len(budgets):
        raise ValidationError("Collection provider budgets are duplicated")
    by_id={}
    for u in units:
        validate_unit(u)
        s=bound.get(u.collection)
        if s is None or u.selection_sha256!=s.scope_sha256:
            raise ConflictError("Collection unit selection changed")
        if u.provider not in provider_budgets:
            raise ValidationError("Collection unit lacks a provider budget")
        eligible={x.source_symbol for x in s.eligible}
        if not set(u.selected_symbols)<=eligible:
            raise ValidationError("Collection unit includes an unresolved or unselected member")
        if not u.selected_symbols and u.collection=="daily_prices":
            if u.subject not in {x.provider_symbol for x in s.retained}:
                raise ValidationError("Collection price subject is outside retained ETF/index selections")
        if u.provider!=s.binding.provider and u.provider!="public_news":
            raise ValidationError("Collection requires a separately pinned provider mapping")
        if not u.selected_symbols and u.collection not in ("daily_prices","news") and not (u.provider=="sharadar" and u.endpoint in ("SHARADAR/SF1/metadata","SHARADAR/INDICATORS","SHARADAR/INDICATORS/metadata","SHARADAR_DIRECT/fundamentals","SHARADAR_DIRECT/descriptions")):
            raise ValidationError("Collection unit has no selected members")
        # Subject equivalence is proven by pinned mapping, not symbol similarity.
        relevant=[x for x in s.eligible if x.source_symbol in u.selected_symbols]
        if u.provider==s.binding.provider and u.collection!="news":
            expected={x.cik if u.provider=="sec" else x.provider_subject if u.provider=="sharadar"
                else x.provider_symbol for x in relevant}
            if relevant and (None in expected or set(u.subject.split(","))!=expected):
                raise ConflictError("Collection provider subject differs from pinned mappings")
            if u.provider=="sharadar" and u.endpoint in ("SHARADAR/SF1","SHARADAR_DIRECT/fundamentals") and set(dict(u.parameters)["ticker"].split(","))!={x.provider_symbol for x in relevant}:
                raise ConflictError("SF1 tickers differ from the pinned provider symbols")
        if u.max_response_bytes>provider_budgets[u.provider].max_total_bytes:
            raise ResourceLimitError("One response exceeds its invocation byte budget")
        prior=by_id.get(u.unit_id)
        if prior is not None and prior!=u:
            raise ConflictError("Duplicate collection request has conflicting scope")
        by_id[u.unit_id]=u
    ordered=tuple(sorted(by_id.values(),key=lambda u:(u.provider,u.collection,u.subject,u.endpoint,u.parameters,u.unit_id)))
    reuse=tuple((u.unit_id,r) for u in ordered if (r:=reusable(u,retained,cutoff=at)) is not None)
    gaps=tuple(sorted((s.binding.id,x.source_symbol,x.reason) for s in selections for x in s.gaps))
    reused_ids={key for key,_ in reuse}
    blocked=tuple((u.unit_id,"retained_request_requires_original_evidence")
        for u in ordered if u.unit_id not in reused_ids and any(
            r.request_id==u.request_id and r.published and _utc(r.captured_at)<=at
            and (u.mode=="historical_backfill" or r.observation_window==u.observation_window)
            for r in retained))
    return CollectionPlan(at,ordered,tuple(sorted(budgets,key=lambda b:b.provider)),reuse,gaps,
        tuple(sorted(BINDING_IDS-set(bound))),blocked)
