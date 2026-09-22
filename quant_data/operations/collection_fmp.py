"""Selected FMP company work, preserving each source's authoritative tables."""
from ..ingestion import PublicationDeferred

from dataclasses import dataclass,replace
from contextlib import nullcontext
from ..stores import acquire_write_session,StoreRole
import hashlib,time
from ..errors import ConflictError,ValidationError,ResourceLimitError,StoreUnavailableError
from ..market.collection_pins import validate_pinned_collection
from ..market.collection_universe import _utc
from ..company.fmp_research import parse_research_response,FmpResearchPublisher
from ..company.fmp_analyst_history import parse_analyst_response,FmpAnalystPublisher,CURRENT
from ..company.fmp_market_data import parse_fmp_company_response,FmpCompanyMarketDataPublisher
from .collection_targets import selected_fmp_subjects
from .collection_sec import requires_active_binding
from .collection_plan import AcquisitionUnit,validate_unit,reusable,ENDPOINTS

STATEMENTS=("income-statement","balance-sheet-statement","cash-flow-statement","financial-statement-full-as-reported")
DISTINCT_REVIEW=(
    ("grades","dated recommendation events","company.fmp.analyst_observations"),
    ("grades-historical","historical recommendation distributions","company.fmp.analyst_observations"),
    ("grades-consensus","current recommendation consensus","company.fmp.analyst_observations"),
    ("price-target","dated price-target events","company.fmp.analyst_observations"),
    ("price-target-consensus","current price-target consensus","company.fmp.analyst_observations"),
    ("price-target-summary","current price-target summary","company.fmp.analyst_observations"),
    ("revenue-product-segmentation","source-labelled fiscal product segments","company.fmp.research_inputs"),
)

@dataclass(frozen=True)
class FmpWork:
    units: tuple[AcquisitionUnit,...]
    missing_issuer_ciks: tuple[str,...]

def fmp_units(stores,selection,*,mode,observation_window,cutoff,distinct_endpoints=(),require_active=False):
    subjects,missing=selected_fmp_subjects(stores,selection,cutoff=cutoff,require_active=require_active)
    key=selection.binding.id
    if key=="fmp_statements": endpoints=STATEMENTS
    elif key=="fmp_analyst_estimates":endpoints=("analyst-estimates",)
    elif key=="dividends_splits":endpoints=("dividends","splits")
    elif key=="earnings_dates":endpoints=("earnings",)
    elif key=="fmp_distinct_inputs":
        if not isinstance(distinct_endpoints,tuple) or tuple(sorted(set(distinct_endpoints)))!=distinct_endpoints:
            raise ValidationError("Distinct FMP endpoints require a unique sorted explicit selection")
        if not set(distinct_endpoints)<=ENDPOINTS[key]["fmp"]:
            raise ValidationError("Distinct FMP endpoint is outside the reviewed families")
        endpoints=distinct_endpoints
    else:raise ValidationError("Selected FMP company binding is invalid")
    members={}
    for s in selection.eligible:members.setdefault(s.provider_symbol,set()).add(s.source_symbol)
    units=[]
    for subject in subjects:
        for endpoint in endpoints:
            periods=("annual","quarter") if endpoint in (*STATEMENTS,"analyst-estimates","revenue-product-segmentation") else (None,)
            for period in periods:
                parameters={"symbol":subject.symbol}
                if endpoint not in CURRENT:parameters["limit"]="100" if endpoint in ("analyst-estimates","revenue-product-segmentation") else "1000"
                if period:parameters["period"]=period
                if endpoint=="analyst-estimates":parameters["page"]="0"
                units.append(validate_unit(AcquisitionUnit(key,"fmp",endpoint,subject.symbol,
                    tuple(sorted(parameters.items())),mode,observation_window,tuple(sorted(members[subject.symbol])),
                    selection.scope_sha256,max_response_bytes=8*1024*1024 if endpoint in STATEMENTS else 1024*1024,
                    max_rows=1000 if endpoint in STATEMENTS else 5000,timeout_seconds=30)))
    return FmpWork(tuple(units),missing)

def estimate_continuation(unit,parsed,*,seen_page_hashes=(),seen_target_dates=()):
    """Return one explicit candidate child unit; perform no network work."""
    validate_unit(unit)
    if unit.collection!="fmp_analyst_estimates" or parsed.endpoint!="analyst-estimates":
        raise ValidationError("Estimate continuation requires an estimate response")
    from ..json_codec import loads_strict
    if (loads_strict(parsed.parameters_json)!=dict(unit.parameters) or parsed.subject.symbol!=unit.subject
        or hashlib.sha256(parsed.raw_body).hexdigest()!=parsed.content_sha256):
        raise ConflictError("Estimate continuation response differs from the request")
    checked=parse_analyst_response(parsed.raw_body,endpoint=parsed.endpoint,parameters=dict(unit.parameters),
        subject=parsed.subject,captured_at=parsed.captured_at,source_reference=parsed.source_reference)
    if checked!=parsed:
        raise ConflictError("Estimate continuation metadata differs from the original response")
    from datetime import date
    if (not isinstance(seen_page_hashes,tuple) or len(seen_page_hashes)>9
        or any(not isinstance(h,str) or len(h)!=64 or any(c not in "0123456789abcdef" for c in h) for h in seen_page_hashes)
        or not isinstance(seen_target_dates,tuple) or len(seen_target_dates)>900
        or len(set(seen_target_dates))!=len(seen_target_dates)):
        raise ValidationError("Estimate continuation history exceeds its bounded contract")
    try:
        if any(date.fromisoformat(d).isoformat()!=d for d in seen_target_dates): raise ValueError
    except (TypeError,ValueError):
        raise ValidationError("Estimate continuation history contains an invalid target date") from None
    if parsed.content_sha256 in seen_page_hashes and parsed.raw_row_count:
        raise ConflictError("FMP repeated an estimate page; pagination completeness is unresolved")
    p=dict(unit.parameters);page=int(p.get("page","0"));limit=int(p["limit"])
    targets={r.target_period_end for r in parsed.rows}
    if parsed.raw_row_count and not targets:
        raise ConflictError("FMP estimate page cannot establish target-date progress")
    if page:
        if not seen_target_dates:
            raise ValidationError("Estimate continuation requires prior target dates after page zero")
        if targets and not targets.difference(seen_target_dates):
            raise ConflictError("FMP estimate page has non-progressing target dates")
        if targets and min(targets)>=min(seen_target_dates):
            raise ConflictError("FMP estimate page does not extend the oldest target date")
    if parsed.raw_row_count<limit:return None,"short_page_history_unverified"
    if page>=9:return None,"page_cap_reached_incomplete"
    p["page"]=str(page+1)
    return replace(unit,parameters=tuple(sorted(p.items()))),"continuation_required"

def read_when_company_quiet(read,*,stores=None,deadline=None,monotonic=time.monotonic,sleeper=time.sleep,retry_sidecar_changes=False):
    """Wait only around a pure read, never around a publisher or HTTP call."""
    def physical():
        return tuple((str(stores.path(role).resolve(strict=True)),
            stores.path(role).stat().st_dev,stores.path(role).stat().st_ino)
            for role in (StoreRole.MARKET,StoreRole.COMPANY))
    identity=physical() if retry_sidecar_changes and stores is not None else None
    while True:
        if deadline is not None and monotonic()>=deadline:
            raise PublicationDeferred('Company identity read exceeded its invocation deadline')
        try:
            # Coordinate immutable identity reads with existing writers. No
            # writable SQLite handle, provider call or publisher is inside.
            timeout=5.0 if deadline is None else max(0.0,deadline-monotonic())
            context=nullcontext() if stores is None else acquire_write_session(
                stores,(StoreRole.MARKET,StoreRole.COMPANY),timeout_seconds=timeout)
            with context:
                if deadline is not None and monotonic()>=deadline:
                    raise PublicationDeferred('Company identity read exceeded its invocation deadline')
                result=read()
            if deadline is not None and monotonic()>=deadline:
                raise PublicationDeferred('Company identity read exceeded its invocation deadline')
            return result
        except StoreUnavailableError as error:
            sidecar=(identity is not None and str(error)=='company store path or sidecar changed during the read'
                and physical()==identity)
            if (str(error)!='company store is not quiet' and not sidecar) or deadline is None:raise
            remaining=deadline-monotonic()
            if remaining<=0:raise PublicationDeferred('Company identity read exceeded its invocation deadline') from error
            sleeper(min(0.25,remaining))


class SelectedFmpPublisher:
    def __init__(self,*,stores,registry,selection,units,cutoff,resolve_retained=None,
                 deadline=None,monotonic=time.monotonic,sleeper=time.sleep,retry_sidecar_changes=False):
        self.retry_sidecar_changes=retry_sidecar_changes
        subjects,missing=read_when_company_quiet(lambda:selected_fmp_subjects(stores,selection,cutoff=cutoff,
            require_active=requires_active_binding(stores)),stores=stores,deadline=deadline,monotonic=monotonic,sleeper=sleeper,retry_sidecar_changes=retry_sidecar_changes)
        self.subjects={s.symbol:s for s in subjects}
        eligible={}
        for s in selection.eligible:eligible.setdefault(s.provider_symbol,set()).add(s.source_symbol)
        self.units={}
        for u in units:
            validate_unit(u)
            if (u.collection!=selection.binding.id or u.provider!="fmp" or u.subject not in self.subjects
                or u.selection_sha256!=selection.scope_sha256 or set(u.selected_symbols)!=eligible[u.subject]):
                raise ConflictError("FMP publication differs from the selected subject")
            if u.unit_id in self.units:raise ConflictError("FMP publication unit is duplicated")
            self.units[u.unit_id]=u
        self.stores=stores;self.selection=selection;self.cutoff=_utc(cutoff)
        self.resolve_retained=resolve_retained
        self.research=FmpResearchPublisher(stores,registry)
        self.analyst=FmpAnalystPublisher(stores,registry)
        self.actions=FmpCompanyMarketDataPublisher(stores,registry)

    def __call__(self,*,unit,retained,receipt,body,deadline=None,monotonic=time.monotonic,sleeper=time.sleep):
        def check_deadline():
            if deadline is not None and monotonic() >= deadline:
                raise PublicationDeferred("Selected FMP publication exceeded its invocation deadline")
        check_deadline()
        if self.units.get(unit.unit_id)!=unit:
            raise ConflictError("FMP response is outside its exact unit manifest")
        if retained is not None:
            if reusable(unit,(retained,),cutoff=self.cutoff) is None or self.resolve_retained is None:
                raise ConflictError("FMP reuse requires the original response")
            body=self.resolve_retained(retained)
            if not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=retained.content_sha256:
                raise ConflictError("Retained FMP bytes differ")
            if retained.published:return {"outcome":"reused","coverage":retained.coverage,"capture_id":retained.capture_id}
            captured=retained.captured_at;reference=retained.source_reference
        else:
            if (not isinstance(receipt,dict) or receipt.get("request_id")!=unit.request_id
                or receipt.get("unit_id")!=unit.unit_id or receipt.get("status")!=200
                or not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=receipt.get("content_sha256")):
                raise ConflictError("FMP response differs from its charged request")
            captured=receipt["captured_at"]
            reference="collection/fmp/blobs/"+receipt["content_sha256"]+".json"
        if len(body)>unit.max_response_bytes:
            raise ResourceLimitError("FMP retained response exceeds its exact unit byte bound")
        # Later mappings cannot be used to backdate newly published evidence.
        # selected_fmp_subjects revalidates the pinned mapping inside the same
        # coordinated read as the issuer lookup, at the original capture time.
        subjects,missing=read_when_company_quiet(lambda:selected_fmp_subjects(self.stores,self.selection,cutoff=captured),
            stores=self.stores,deadline=deadline,monotonic=monotonic,sleeper=sleeper,retry_sidecar_changes=self.retry_sidecar_changes)
        original_subject=next((s for s in subjects if s.symbol==unit.subject),None)
        if original_subject!=self.subjects[unit.subject]:
            raise ConflictError("FMP issuer identity was unavailable at response capture")
        params=dict(unit.parameters);endpoint=unit.endpoint
        if endpoint in STATEMENTS or endpoint=="revenue-product-segmentation":
            parsed=parse_research_response(body,endpoint=endpoint,parameters=params,subject=original_subject,
                captured_at=captured,source_reference=reference,history_profile=endpoint in STATEMENTS)
            publish=lambda:self.research.publish(parsed,request_id=unit.unit_id,deadline=deadline,monotonic=monotonic)
            coverage="partial_history" if parsed.completeness=="partial" else "complete_request"
        elif endpoint in ("dividends","splits"):
            if params!={"symbol":unit.subject,"limit":"1000"}:
                raise ConflictError("FMP actions require their established exact request scope")
            parsed=parse_fmp_company_response(endpoint,original_subject,body,captured,reference)
            publish=lambda:self.actions.publish(parsed,deadline=deadline,monotonic=monotonic)
            coverage="partial_history" if parsed.completeness=="partial" else "complete_request"
        else:
            parsed=parse_analyst_response(body,endpoint=endpoint,parameters=params,subject=original_subject,
                captured_at=captured,source_reference=reference)
            publish=lambda:self.analyst.publish(parsed,request_id=unit.unit_id,deadline=deadline,monotonic=monotonic)
            coverage="partial_history" if endpoint not in CURRENT else "complete_request"
        if parsed.raw_row_count>unit.max_rows:
            raise ResourceLimitError("FMP response exceeds its exact unit row bound")
        check_deadline()
        result=publish()
        if not parsed.raw_row_count:coverage="empty"
        return {"outcome":result.outcome,"capture_id":result.snapshot_id,"coverage":coverage,
            "raw_rows":parsed.raw_row_count,"written_count":result.written_count,"warnings":list(parsed.warnings)}
