"""Immutable selected-universe inputs for existing domain collectors.

Every identity is rechecked against the pinned market snapshot. Prices and news
retain their independent ETF/index snapshots; SEC work is grouped by exact CIK.
"""
from dataclasses import dataclass
from pathlib import Path
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..market.collection_bindings import PinnedCollection,load_bindings
from ..market.collection_pins import validate_pinned_collection
from ..market.collection_universe import _utc
from ..stores import quiet_immutable_read_connection

HOST_BINDINGS_PATH=Path(__file__).resolve().parents[2]/"config/collection_bindings.json"

MAX_MARKET_TARGETS=6600 # 5000 selected source members plus two existing 800-member scopes.

@dataclass(frozen=True)
class IssuerTarget:
    cik: str
    source_members: tuple[str,...]
    provider_symbols: tuple[str,...]

def validate_selection(stores,selection,*,cutoff,collection,require_active=False):
    if not isinstance(selection,PinnedCollection) or selection.binding.id!=collection:
        raise ValidationError("Collector selection belongs to another binding")
    if require_active:
        trusted=load_bindings(HOST_BINDINGS_PATH)[collection]
        if trusted.mode!="active" or selection.binding!=trusted:
            raise ConflictError("Collector selection does not match its activated host binding")
    validate_pinned_collection(stores,selection,cutoff=cutoff)
    return selection

def selected_market_rows(stores,selection,*,cutoff,require_active=False):
    if not isinstance(selection,PinnedCollection) or selection.binding.id not in ("daily_prices","news"):
        raise ValidationError("Market target selection requires the price or news binding")
    validate_selection(stores,selection,cutoff=cutoff,collection=selection.binding.id,require_active=require_active)
    requested={}
    for s in selection.eligible:
        identity=(s.instrument_id,s.provider_symbol,"equity")
        if identity[0] is None or identity[1] is None:raise ValidationError("Selected market identity is incomplete")
        old=requested.get(identity[0])
        if old is not None and old!=identity:raise ConflictError("Selected market instrument has conflicting symbols")
        requested[identity[0]]=identity
    for s in selection.retained:
        identity=(s.instrument_id,s.provider_symbol,s.asset_type)
        old=requested.get(identity[0])
        if old is not None and old!=identity:raise ConflictError("Retained market scope conflicts with selected equities")
        requested[identity[0]]=identity
    if len(requested)>MAX_MARKET_TARGETS:raise ResourceLimitError("Selected market roster exceeds its bound")
    rows=[]
    with quiet_immutable_read_connection(stores,"market") as c:
        for instrument,symbol,asset in sorted(requested.values(),key=lambda r:(r[1],r[0])):
            row=c.execute("""SELECT instrument_id,provider_symbol,asset_type,currency_segment,captured_at
                FROM stage10_instruments WHERE instrument_id=? AND provider='fmp'""",(instrument,)).fetchone()
            if row is None or (row["provider_symbol"],row["asset_type"],row["currency_segment"])!=(symbol,asset,"provider_native"):
                raise ConflictError("Selected market identity differs from its owning instrument record")
            if _utc(row["captured_at"])>_utc(cutoff):raise ConflictError("Selected market instrument is from the future")
            rows.append({"instrument_id":instrument,"provider_symbol":symbol,"asset_type":asset})
    if len({r["provider_symbol"] for r in rows})!=len(rows):
        raise ConflictError("Selected market symbols identify multiple instruments")
    return tuple(rows)

def selected_sec_issuers(stores,selection,*,cutoff,require_active=False):
    validate_selection(stores,selection,cutoff=cutoff,collection="sec_filings_companyfacts",require_active=require_active)
    groups={}
    for s in selection.eligible:
        if not s.cik:raise ValidationError("Selected SEC issuer lacks its CIK")
        members,symbols=groups.setdefault(s.cik,(set(),set()))
        members.add(s.source_symbol)
        if s.provider_symbol:symbols.add(s.provider_symbol)
    return tuple(IssuerTarget(cik,tuple(sorted(members)),tuple(sorted(symbols)))
        for cik,(members,symbols) in sorted(groups.items()))

def selected_fmp_subjects(stores,selection,*,cutoff,require_active=False):
    if not isinstance(selection,PinnedCollection) or selection.binding.id not in (
        "fmp_statements","fmp_analyst_estimates","fmp_distinct_inputs","dividends_splits","earnings_dates"):
        raise ValidationError("Company target selection requires its FMP binding")
    validate_selection(stores,selection,cutoff=cutoff,collection=selection.binding.id,require_active=require_active)
    from ..company.fmp_market_data import FmpCompanySubject
    subjects={};missing=set()
    # The immutable mapping record supplies the original evidence hash. A scope
    # hash is not substituted for the provider identity evidence.
    with quiet_immutable_read_connection(stores,"market") as c:
        evidence={r["source_symbol"]:r["evidence_sha256"] for r in c.execute(
            "SELECT source_symbol,evidence_sha256 FROM market_collection_provider_mappings WHERE mapping_id=?",(selection.mapping_id,))}
    with quiet_immutable_read_connection(stores,"company") as c:
        for s in selection.eligible:
            issuer=c.execute("""SELECT i.issuer_id,r.started_at FROM company_issuers i
                JOIN ingestion_runs r ON r.run_id=i.created_run_id WHERE i.cik=?""",(s.cik,)).fetchone()
            if issuer is None or _utc(issuer["started_at"])>_utc(cutoff):missing.add(s.cik);continue
            subject=FmpCompanySubject(issuer_id=issuer[0],cik=s.cik,symbol=s.provider_symbol,
                instrument_id=s.instrument_id,identity_evidence_sha256=evidence[s.source_symbol])
            old=subjects.get(subject.symbol)
            if old is not None and old!=subject:raise ConflictError("Selected FMP subject association is ambiguous")
            subjects[subject.symbol]=subject
    return tuple(subjects[s] for s in sorted(subjects)),tuple(sorted(missing))
