"""Common source-aware fundamental read service over independent source stores.

No implicit source preference, metric equivalence, unit conversion or fallback
is introduced. Callers receive each source's rows, precision and provenance.
"""
from ..errors import ConflictError,ValidationError,ResourceLimitError
from ..market.collection_pins import validate_pinned_collection
from ..market.collection_universe import _utc
from ..stores import quiet_immutable_read_connection
from ..json_codec import loads_strict
from .stage4_repository import CompanyStage4Repository,CompanyStage4Query
from .fmp_research import read_research_inputs,FmpResearchInputsQuery
from .sharadar_repository import SharadarSf1Repository
from .sharadar_sf1 import DIMENSIONS

BINDINGS={"sec":"sec_filings_companyfacts","fmp":"fmp_statements","sharadar":"sharadar_fundamentals"}
STATEMENTS=("income-statement","balance-sheet-statement","cash-flow-statement","financial-statement-full-as-reported")

def read_selected_fundamentals(context,registry,*,source_symbol,selections,knowledge_cutoff,
        sources=("sec","fmp","sharadar"),limit_per_source=100,sharadar_dimensions=tuple(sorted(DIMENSIONS)),
        sharadar_mode="local_capture_as_of",filing_cutoff=None):
    if (not isinstance(sources,tuple) or not sources or len(set(sources))!=len(sources)
        or not set(sources)<=set(BINDINGS)):
        raise ValidationError("Fundamental sources must be an explicit unique source selection")
    if type(limit_per_source) is not int or not 1<=limit_per_source<=100:
        raise ResourceLimitError("Common fundamental reader is limited to 100 rows per source")
    at=_utc(knowledge_cutoff);context.checkpoint()
    if not isinstance(selections,dict) or set(selections)!={BINDINGS[s] for s in sources}:
        raise ValidationError("Fundamental sources need their exact independent pinned mappings")
    members={};snapshot_ids=set()
    for source in sources:
        selection=selections[BINDINGS[source]]
        if selection.binding.id!=BINDINGS[source]:raise ConflictError("Fundamental source binding differs")
        validate_pinned_collection(context.store_map,selection,cutoff=at)
        snapshot_ids.add(selection.membership_snapshot_id)
        subject=next((s for s in selection.subjects if s.source_symbol==source_symbol),None)
        if subject is None:raise ValidationError("Fundamental subject is outside the selected membership")
        members[source]=subject
    if len(snapshot_ids)!=1:raise ConflictError("Fundamental sources refer to different membership snapshots")
    ciks={s.cik for s in members.values() if s.status=="resolved" and s.cik}
    instruments={s.instrument_id for s in members.values() if s.status=="resolved" and s.instrument_id}
    if len(ciks)>1 or len(instruments)>1:
        raise ConflictError("Fundamental source mappings disagree on issuer or security identity")
    outputs={}
    for source in sources:
        subject=members[source]
        out={"source":source,"provider_symbol":subject.provider_symbol,"provider_subject":subject.provider_subject,
            "cik":subject.cik,"instrument_id":subject.instrument_id,"mapping_id":selections[BINDINGS[source]].mapping_id,
            "rows":[],"truncated":False}
        outputs[source]=out
        if subject.status!="resolved":
            out.update(status=subject.status,reason=subject.reason);continue
        if source in ("sec","fmp") and not subject.cik:
            out.update(status="unresolved",reason="An evidenced issuer CIK is required");continue
        if source=="sec":
            with quiet_immutable_read_connection(context.store_map,"company") as c:
                present=c.execute("SELECT 1 FROM company_issuers WHERE cik=?",(subject.cik,)).fetchone()
            if present is None:
                out.update(status="not_established",reason="Issuer identity has not been published");continue
            rows=CompanyStage4Repository(context.store_map,registry).get_fundamentals(
                CompanyStage4Query(subject.cik,as_of=at,limit=10000))
            out.update(rows=list(rows[:limit_per_source]),truncated=len(rows)>limit_per_source,
                reporting_basis="SEC mapped filing facts",availability_basis="existing_sec_source_availability",
                date_only_policy="completed_date")
            context.budget.require(rows=min(len(rows),limit_per_source),series=1,operations=min(len(rows),limit_per_source))
        elif source=="fmp":
            result=read_research_inputs(context,FmpResearchInputsQuery(subject.cik,endpoints=STATEMENTS,
                mode="as_of",as_of=at,limit=limit_per_source),source_symbol=subject.provider_symbol)
            rows=[{f.name:f.value for f in r.fields} for r in result.records]
            out.update(rows=rows,truncated=result.truncation.applied,reporting_basis="FMP source statement rows",
                availability_basis="local_capture")
        else:
            result=SharadarSf1Repository(context.store_map).rows(ticker=subject.provider_symbol,
                dimensions=sharadar_dimensions,knowledge_cutoff=at,mode=sharadar_mode,
                filing_cutoff=filing_cutoff,limit=limit_per_source+1,provider_subject=subject.provider_subject)
            out.update(rows=result["rows"][:limit_per_source],truncated=len(result["rows"])>limit_per_source,
                reporting_basis="Sharadar AR/MR dimensions retained separately",
                availability_basis=result["availability_basis"],warnings=result["warnings"])
            context.budget.require(rows=len(out["rows"]),series=1,operations=len(out["rows"]))
        out["status"]="available" if out["rows"] else "not_established"
        context.checkpoint()
    return {"source_symbol":source_symbol,"membership_snapshot_id":next(iter(snapshot_ids)),
        "knowledge_cutoff":at,"policy":"source_separated_no_implicit_fallback",
        "metric_equivalence":"not_asserted","sources":outputs,
        "identity_link_status":"partial" if any(s.cik is None or s.instrument_id is None for s in members.values())
            else "evidenced"}
