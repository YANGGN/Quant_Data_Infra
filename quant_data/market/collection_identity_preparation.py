"""Prepare complete mappings from retained provider identity evidence; zero GETs."""
from dataclasses import dataclass
import json
from ..errors import ConflictError,ValidationError,ResourceLimitError
from ..json_codec import loads_strict
from ..stores import quiet_immutable_read_connection
from .collection_mappings import IdentityEvidence,identity_source_object,unresolved_mapping,prepare_mapping,_cik
from .collection_universe import _utc

@dataclass(frozen=True)
class IdentityLocator:
    evidence_sha256: str
    pointer: str
    symbol_field: str
    subject_field: str
    cik_field: str | None = None

def identity_locators(evidence):
    """Enumerate documented response envelopes; aliases require reviewed input."""
    if not isinstance(evidence,IdentityEvidence):raise ValidationError("Original identity evidence is required")
    value=loads_strict(evidence.body,max_bytes=8*1024*1024)
    sha=evidence.sha256
    if evidence.provider=="sharadar":
        if isinstance(value,dict) and set(value)=={"count","data"}:
            from ..company.sharadar_direct import ticker_rows
            rows=ticker_rows(evidence.body)
            return tuple(IdentityLocator(sha,f"/data/{i}","ticker","permaticker") for i in range(len(rows)))
        table=value.get("datatable") if isinstance(value,dict) else None
        data=table.get("data") if isinstance(table,dict) else None
        if not isinstance(data,list) or len(data)>10000:raise ValidationError("TICKERS data envelope is invalid")
        result=[]
        for i in range(len(data)):
            pointer=f"/datatable/data/{i}"
            row=identity_source_object(value,pointer,"sharadar")
            result.append(IdentityLocator(sha,pointer,"ticker","permaticker"))
        return tuple(result)
    if evidence.provider=="fmp":
        if not isinstance(value,list) or len(value)>5000:raise ValidationError("FMP profile evidence must be a bounded array")
        return tuple(IdentityLocator(sha,f"/{i}","symbol","symbol","cik") for i in range(len(value)))
    if evidence.provider=="sec":
        if not isinstance(value,dict) or len(value)>100000:raise ValidationError("SEC ticker evidence must be an object")
        return tuple(IdentityLocator(sha,"/"+str(k).replace("~","~0").replace("/","~1"),"ticker","cik_str","cik_str")
            for k,row in value.items() if isinstance(row,dict) and {"ticker","cik_str"}<=set(row))
    raise ValidationError("This provider requires explicit original identity locators")

def prepare_evidenced_mappings(stores,*,membership_snapshot_id,provider,evidence,locators,captured_at,source_reference):
    """Resolve exact symbols only; keep ambiguous/unsupported/unresolved members."""
    at=_utc(captured_at)
    if not isinstance(evidence,tuple) or not isinstance(locators,tuple) or len(locators)>100000:
        raise ResourceLimitError("Identity preparation exceeds its finite evidence bound")
    if any(not isinstance(e,IdentityEvidence) or e.provider!=provider or not isinstance(e.body,bytes)
        or not e.body or len(e.body)>8*1024*1024 for e in evidence):
        raise ValidationError("Identity preparation requires bounded original provider evidence")
    if sum(len(e.body) for e in evidence)>64*1024*1024:
        raise ResourceLimitError("Identity preparation evidence exceeds 64 MiB")
    if any(_utc(e.captured_at)>at for e in evidence):
        raise ConflictError("Future identity evidence cannot influence an earlier mapping")
    proofs={e.sha256:e for e in evidence}
    if len(proofs)!=len(evidence) or any(e.provider!=provider for e in evidence):
        raise ConflictError("Identity preparation has duplicate or mixed provider evidence")
    parsed={sha:loads_strict(e.body,max_bytes=8*1024*1024) for sha,e in proofs.items()}
    with quiet_immutable_read_connection(stores,"market") as c:
        snapshot=c.execute("SELECT captured_at,member_count FROM market_collection_snapshots WHERE snapshot_id=?",(membership_snapshot_id,)).fetchone()
        if snapshot is None or _utc(snapshot["captured_at"])>at:
            raise ConflictError("Identity preparation needs an available membership snapshot")
        symbols=tuple(r[0] for r in c.execute("SELECT source_symbol FROM market_collection_members WHERE snapshot_id=? ORDER BY source_symbol",(membership_snapshot_id,)))
        if len(symbols)!=snapshot["member_count"]:raise ConflictError("Identity membership snapshot is incomplete")
        existing={r["provider_symbol"]:dict(r) for r in c.execute(
            "SELECT instrument_id,provider_symbol,asset_type,captured_at FROM stage10_instruments WHERE provider='fmp'")}
    candidates={}
    for locator in locators:
        if not isinstance(locator,IdentityLocator) or locator.evidence_sha256 not in proofs:
            raise ValidationError("Identity locator lacks its original evidence")
        row=identity_source_object(parsed[locator.evidence_sha256],locator.pointer,provider)
        symbol=row.get(locator.symbol_field);subject=row.get(locator.subject_field)
        if not isinstance(symbol,str) or symbol not in symbols:continue
        if isinstance(subject,bool) or not isinstance(subject,(str,int)) or not str(subject):
            raise ValidationError("Identity source subject is invalid")
        cik=None if locator.cik_field is None else _cik(row.get(locator.cik_field))
        if provider=="sec":subject=cik
        candidates.setdefault(symbol,[]).append((str(subject),cik,locator,row))
    output=[];used=set()
    for symbol in symbols:
        target=unresolved_mapping(symbol)
        rows=candidates.get(symbol,[])
        if len({(subject,cik) for subject,cik,_,_ in rows})>1:
            target.update(status="ambiguous",reason="Provider evidence contains competing exact-symbol identities")
        elif rows:
            subject,cik,locator,row=rows[0];instrument=None
            if provider=="fmp":
                old=existing.get(symbol)
                if old and (old["asset_type"]!="equity" or _utc(old["captured_at"])>at):
                    target.update(status="unsupported",reason="Existing identity is not an available selected equity")
                    output.append(target);continue
                if old:instrument=old["instrument_id"]
                else:
                    if row.get("isEtf") is not False or row.get("isFund") is not False:
                        target.update(reason="FMP profile does not explicitly establish an equity")
                        output.append(target);continue
                    from .stage10_history_importer import _InstrumentPlan
                    instrument=_InstrumentPlan(symbol,"equity",row.get("companyName") or symbol,None).instrument_id
            proof=proofs[locator.evidence_sha256]
            target.update(status="resolved",provider_symbol=symbol,provider_subject=subject,instrument_id=instrument,cik=cik,
                evidence_sha256=proof.sha256,evidence_reference=proof.source_reference,evidence_pointer=locator.pointer,
                symbol_field=locator.symbol_field,subject_field=locator.subject_field,cik_field=locator.cik_field,
                reason="Exact source symbol established by retained provider identity evidence")
            # SEC subjects are padded CIKs; use the source's original integer representation in subject_field.
            if provider=="sec":target["provider_subject"]=str(row[locator.subject_field])
            used.add(proof.sha256)
        output.append(target)
    return prepare_mapping(membership_snapshot_id=membership_snapshot_id,provider=provider,captured_at=at,
        source_reference=source_reference,body=json.dumps(output,sort_keys=True,separators=(",",":")).encode(),
        evidence=tuple(proofs[sha] for sha in sorted(used)))
