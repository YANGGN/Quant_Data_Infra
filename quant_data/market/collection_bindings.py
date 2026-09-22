"""Host-owned collector bindings and immutable run selections.

Prepared bindings can be inspected offline. Runtime adoption requires a deliberate
mode transition; neither membership nor identity insertion enables a collector.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .collection_universe import read_collection, _digest, _utc, MAX_MEMBERS
from .collection_mappings import PROVIDERS
from ..errors import ConflictError, ValidationError, StoreUnavailableError, ResourceLimitError
from ..json_codec import loads_strict
from ..stores import quiet_immutable_read_connection

BINDING_IDS=frozenset({"sharadar_fundamentals","daily_prices","sec_filings_companyfacts","dividends_splits",
    "earnings_dates","fmp_statements","fmp_analyst_estimates","fmp_distinct_inputs","news","equibles_transcripts"})
EXPECTED_PROVIDERS={key:("sharadar" if key=="sharadar_fundamentals" else "sec" if key=="sec_filings_companyfacts"
    else "equibles" if key=="equibles_transcripts" else "fmp") for key in BINDING_IDS}
REQUIRED_FIELDS={"sharadar_fundamentals":("provider_subject","provider_symbol"),
    "daily_prices":("provider_symbol","instrument_id"),"sec_filings_companyfacts":("cik",),
    "dividends_splits":("provider_symbol","instrument_id","cik"),
    "earnings_dates":("provider_symbol","instrument_id","cik"),"fmp_statements":("provider_symbol","instrument_id","cik"),
    "fmp_analyst_estimates":("provider_symbol","instrument_id","cik"),"fmp_distinct_inputs":("provider_symbol","instrument_id","cik"),
    "news":("provider_symbol","instrument_id"),"equibles_transcripts":("provider_symbol","instrument_id")}

@dataclass(frozen=True)
class CollectionBinding:
    id: str
    provider: str
    universe_id: str
    mode: str
    retain_universes: tuple[str,...]
    required_identity_fields: tuple[str,...]
    config_sha256: str

@dataclass(frozen=True)
class BoundSubject:
    source_symbol: str
    provider_symbol: str|None
    provider_subject: str|None
    instrument_id: str|None
    cik: str|None
    status: str
    reason: str

@dataclass(frozen=True)
class RetainedInstrument:
    instrument_id: str
    provider_symbol: str
    asset_type: str
    universe_snapshot_id: str

@dataclass(frozen=True)
class PinnedCollection:
    binding: CollectionBinding
    membership_snapshot_id: str
    mapping_id: str
    subjects: tuple[BoundSubject,...]
    retained: tuple[RetainedInstrument,...]
    scope_sha256: str
    @property
    def eligible(self): return tuple(s for s in self.subjects if s.status=="resolved")
    @property
    def gaps(self): return tuple(s for s in self.subjects if s.status!="resolved")
    @property
    def ciks(self): return tuple(sorted({s.cik for s in self.eligible if s.cik}))

def load_bindings(path:Path):
    return parse_bindings(Path(path).read_bytes())

def parse_bindings(body):
    if not isinstance(body,bytes):raise ValidationError("Collection binding bytes are invalid")
    if len(body)>65536: raise ResourceLimitError("Collection binding configuration exceeds bound")
    raw=loads_strict(body,max_bytes=65536)
    base={"contract","version","max_selected_members","initial_manifest","bindings"}
    if (not isinstance(raw,dict) or raw.get("contract")!="quant_data.collection_bindings"
        or raw.get("max_selected_members")!=MAX_MEMBERS
        or not ((raw.get("version")=="1.0.0" and set(raw)==base)
            or (raw.get("version")=="1.1.0" and set(raw)==base|{"provider_allowances"}))):
        raise ValidationError("Collection binding configuration is invalid")
    if raw["version"]=="1.1.0":
        allowances=raw["provider_allowances"]
        if not isinstance(allowances,dict) or set(allowances)!={"fmp"}:
            raise ValidationError("Collection account allowance matrix is invalid")
        entry=allowances["fmp"]
        if not isinstance(entry,dict) or set(entry)!={"mode","policy"} or entry["mode"] not in ("prepared","active"):
            raise ValidationError("Collection FMP allowance declaration is invalid")
        if entry["mode"]=="prepared" and entry["policy"] is not None:
            raise ValidationError("Prepared FMP allowance cannot assert an unverified account policy")
        if entry["mode"]=="active":
            from ..operations.collection_provider_policy import FmpAllowance
            if not isinstance(entry["policy"],dict):
                raise ValidationError("Active FMP allowance needs a reviewed policy")
            try:FmpAllowance(**entry["policy"]).validate()
            except TypeError:raise ValidationError("Active FMP allowance policy is invalid") from None
    items=raw["bindings"]
    if not isinstance(items,list) or len(items)!=len(BINDING_IDS): raise ValidationError("Collection binding matrix is incomplete")
    result={}
    for item in items:
        if not isinstance(item,dict) or set(item)!={"id","provider","universe_id","mode","retain_universes","required_identity_fields"}:
            raise ValidationError("Collection binding fields are invalid")
        key=item["id"]
        if key not in BINDING_IDS or key in result: raise ValidationError("Unknown or duplicate collection binding")
        retained=["curated_etfs","major_indexes"] if key in {"daily_prices","news"} else []
        if (item["provider"]!=EXPECTED_PROVIDERS[key] or item["universe_id"]!="major_index_liquid"
            or item["mode"] not in {"prepared","active"} or item["retain_universes"]!=retained
            or item["required_identity_fields"]!=list(REQUIRED_FIELDS[key])):
            raise ValidationError("Collection binding scope differs from accepted matrix")
        result[key]=CollectionBinding(key,item["provider"],item["universe_id"],item["mode"],
            tuple(retained),tuple(item["required_identity_fields"]),_digest(raw))
    initial=raw["initial_manifest"]
    if initial!={"path":"Major Index Liquid_2026-09-08.csv","sha256":"9393c72160eae41fc3af10a16af4ecc5ab2ab8f8bb7a5d4130162fa9e4aff15a","member_count":2248}:
        raise ValidationError("Initial collection manifest differs")
    return result

def pin_binding(stores,binding,*,cutoff=None):
    if not isinstance(binding,CollectionBinding) or binding.id not in BINDING_IDS:
        raise ValidationError("A registered collection binding is required")
    if (binding.provider!=EXPECTED_PROVIDERS[binding.id] or binding.required_identity_fields!=REQUIRED_FIELDS[binding.id]
        or binding.retain_universes!=(("curated_etfs","major_indexes") if binding.id in {"daily_prices","news"} else ())
        or binding.mode not in {"active","prepared"} or binding.universe_id!="major_index_liquid"):
        raise ValidationError("Collection binding scope changed")
    at=_utc(cutoff) if cutoff is not None else None
    selection=read_collection(stores,binding.universe_id,cutoff=at)
    if selection is None: raise StoreUnavailableError("Collection membership has not been published")
    retained=[]
    with quiet_immutable_read_connection(stores,"market") as c:
        query="SELECT * FROM market_collection_mapping_snapshots WHERE membership_snapshot_id=? AND provider=?"
        params=[selection.snapshot_id,binding.provider]
        if at is not None: query+=" AND captured_at<=?";params.append(at)
        row=c.execute(query+" ORDER BY version_sequence DESC LIMIT 1",params).fetchone()
        if row is None: raise StoreUnavailableError("Collection provider mapping has not been published")
        rows=c.execute("SELECT * FROM market_collection_provider_mappings WHERE mapping_id=? ORDER BY source_symbol LIMIT ?",(row["mapping_id"],MAX_MEMBERS+1)).fetchall()
        if len(rows)!=row["member_count"] or {r["source_symbol"] for r in rows}!={m.symbol for m in selection.members}:
            raise ConflictError("Bound provider mapping is incomplete")
        subjects=[]
        for r in rows:
            missing=[key for key in binding.required_identity_fields if r[key] is None]
            status="identity_incomplete" if r["status"]=="resolved" and missing else r["status"]
            reason="Missing "+", ".join(missing) if status=="identity_incomplete" else r["reason"]
            subjects.append(BoundSubject(r["source_symbol"],r["provider_symbol"],r["provider_subject"],
                r["instrument_id"],r["cik"],status,reason))
        for universe_id in binding.retain_universes:
            query="SELECT * FROM stage10_universe_snapshots WHERE universe_id=?";params=[universe_id]
            if at is not None: query+=" AND captured_at<=?";params.append(at)
            snapshot=c.execute(query+" ORDER BY captured_at DESC,universe_snapshot_id DESC LIMIT 1",params).fetchone()
            if snapshot is None: raise StoreUnavailableError("Independent ETF/index snapshot is missing")
            members=c.execute("SELECT i.instrument_id,i.provider_symbol,i.asset_type FROM stage10_universe_snapshot_members m JOIN stage10_instruments i ON i.instrument_id=m.instrument_id WHERE m.universe_snapshot_id=? ORDER BY i.instrument_id LIMIT 801",(snapshot["universe_snapshot_id"],)).fetchall()
            if len(members)!=snapshot["member_count"] or len(members)>800:
                raise ConflictError("Independent ETF/index snapshot is incomplete")
            expected="etf" if universe_id=="curated_etfs" else "index"
            if any(r["asset_type"]!=expected for r in members): raise ConflictError("Independent asset scope differs")
            retained.extend(RetainedInstrument(r["instrument_id"],r["provider_symbol"],r["asset_type"],snapshot["universe_snapshot_id"]) for r in members)
    scope={"config":binding.config_sha256,"binding":binding.id,"membership":selection.snapshot_id,"mapping":row["mapping_id"],
        "retained_snapshots":sorted({r.universe_snapshot_id for r in retained})}
    return PinnedCollection(binding,selection.snapshot_id,row["mapping_id"],tuple(subjects),tuple(retained),_digest(scope))
