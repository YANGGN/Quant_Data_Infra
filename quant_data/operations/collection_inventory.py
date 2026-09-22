"""Bounded immutable inventories of retained collection evidence.

Metadata presence, original raw-byte availability, and historical completeness
are separate results. No credential, network, default database or provider
request is accessed here. Paths are resolved only under an explicit host root.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
import stat
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import loads_strict
from ..stores import quiet_immutable_read_connection
from ..market.collection_universe import _utc
from .collection_plan import AcquisitionUnit, RetainedResponse, digest, validate_unit

MAX_CAPTURE_ROWS=2000
MAX_INVENTORY_ROWS=200000
MAX_BODY=64*1024*1024
MAX_RAW_TOTAL=1024*1024*1024

@dataclass(frozen=True)
class CoverageEntry:
    collection: str
    subject: str
    endpoint: str
    capture_id: str
    captured_at: str
    raw_state: str
    coverage: str
    source_reference: str
    row_count: int | None
    request_id: str | None

@dataclass(frozen=True)
class CoverageInventory:
    cutoff: str
    entries: tuple[CoverageEntry,...]
    retained: tuple[RetainedResponse,...]
    unchecked: tuple[tuple[str,str,str],...]

def read_original(root,reference,expected_sha,*,max_bytes=MAX_BODY):
    """Verify one declared private logical source reference without scanning."""
    root=Path(root)
    relative=PurePosixPath(reference)
    if (not root.is_absolute() or root.resolve()!=root or not isinstance(reference,str)
        or relative.is_absolute() or ".." in relative.parts or "\\" in reference or ":" in reference):
        raise ValidationError("Collection evidence reference is invalid")
    path=root.joinpath(*relative.parts)
    if not path.is_relative_to(root) or path.resolve()!=path:
        raise ValidationError("Collection evidence path escapes its source root")
    try: info=path.lstat()
    except FileNotFoundError: return None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>max_bytes):
        raise ConflictError("Collection evidence file is invalid")
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW),"rb") as handle:
        body=handle.read(max_bytes+1)
    if len(body)>max_bytes or hashlib.sha256(body).hexdigest()!=expected_sha:
        raise ConflictError("Collection retained evidence hash differs")
    return body

def _rows(c,sql,parameters):
    rows=c.execute(sql,(*parameters,MAX_CAPTURE_ROWS+1)).fetchall()
    if len(rows)>MAX_CAPTURE_ROWS:
        raise ResourceLimitError("Collection capture inventory exceeds its per-subject bound")
    return rows

def _request_id(provider,endpoint,subject,parameters):
    if not isinstance(parameters,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in parameters.items()):
        return None
    # Never copy secret values into an inventory or request hash.
    if any(k.lower() in ("apikey","api_key","authorization","token") for k in parameters):
        return None
    return digest({"provider":provider,"endpoint":endpoint,"subject":subject,"parameters":parameters})

def _scope(row):
    value=loads_strict(row["request_scope_json"],max_bytes=1024*1024)
    if not isinstance(value,dict): raise ConflictError("Retained request scope is invalid")
    return value

def inventory_units(stores,*,units,cutoff,source_root=None,raw_total_limit=MAX_RAW_TOTAL):
    at=_utc(cutoff)
    units=tuple(units)
    if len(units)>MAX_INVENTORY_ROWS:
        raise ResourceLimitError("Collection inventory unit count exceeds bound")
    if isinstance(raw_total_limit,bool) or not isinstance(raw_total_limit,int) or not 1<=raw_total_limit<=MAX_RAW_TOTAL:
        raise ResourceLimitError("Collection raw inventory budget is invalid")
    for unit in units: validate_unit(unit)
    entries=[];retained=[];unchecked=[];seen=set();raw_used=0;verified={}
    def append_entry(entry):
        if len(entries)>=MAX_INVENTORY_ROWS:
            raise ResourceLimitError("Collection inventory exceeds total bound")
        entries.append(entry)
    def add(unit,row,*,parameters=None,body=None,coverage="partial_history",transport_complete=True):
        nonlocal raw_used
        captured=_utc(row["captured_at"])
        if captured>at: return
        key=(unit.collection,row["capture_id"])
        if key in seen: return
        seen.add(key)
        if len(seen)>MAX_INVENTORY_ROWS:
            raise ResourceLimitError("Collection inventory exceeds total bound")
        sha=row["content_sha256"];ref=row["source_reference"]
        proof_key=(sha,ref)
        state="not_checked"
        if body is not None:
            if len(body)>MAX_BODY or raw_used+len(body)>raw_total_limit:
                raise ResourceLimitError("Collection raw verification budget exceeded")
            raw_used+=len(body)
            if hashlib.sha256(body).hexdigest()!=sha:
                raise ConflictError("Canonical collection raw bytes differ")
            state="verified"
        elif proof_key in verified:
            state=verified[proof_key]
        elif source_root is not None:
            remaining=raw_total_limit-raw_used
            if remaining<=0: raise ResourceLimitError("Collection raw verification budget exceeded")
            raw=read_original(source_root,ref,sha,max_bytes=min(MAX_BODY,remaining))
            state="missing" if raw is None else "verified"
            if raw is not None: raw_used+=len(raw)
            verified[proof_key]=state
        request_id=_request_id(unit.provider,unit.endpoint,unit.subject,parameters)
        append_entry(CoverageEntry(unit.collection,unit.subject,unit.endpoint,row["capture_id"],captured,
            state,coverage,ref,row["row_count"],request_id))
        if request_id:
            retained.append(RetainedResponse(request_id,captured,sha,ref,transport_complete,coverage,
                state=="verified",True,row["capture_id"]))
    # Open one immutable owning store at a time, never a cross-store transaction.
    for owner in ("market","company","news"):
        own=[u for u in units if u.owner==owner]
        if not own: continue
        with quiet_immutable_read_connection(stores,owner) as c:
            c.create_function("collection_capture_utc",1,_utc,deterministic=True)
            checked=set()
            for u in own:
                # One metadata scan serves all requested pages/periods for a subject.
                scan=(u.collection,u.endpoint,u.subject)
                if scan in checked: continue
                checked.add(scan)
                if u.collection in ("fmp_statements","fmp_analyst_estimates","fmp_distinct_inputs","earnings_dates"):
                    research=u.collection=="fmp_statements" or u.endpoint=="revenue-product-segmentation"
                    table="company_fmp_research_snapshots" if research else "company_fmp_analyst_captures"
                    identifier="snapshot_id" if research else "capture_id"
                    rows=_rows(c,"SELECT "+identifier+" AS capture_id,captured_at,content_sha256,source_reference,"
                        "request_scope_json,raw_row_count AS row_count FROM "+table+
                        " WHERE symbol=? AND endpoint=? AND collection_capture_utc(captured_at)<=? ORDER BY captured_at LIMIT ?",
                        (u.subject,u.endpoint,at))
                    for r in rows:
                        scope=_scope(r)
                        subject=scope.get("subject",{})
                        if subject.get("symbol")!=u.subject: raise ConflictError("FMP retained subject differs")
                        add(u,r,parameters=scope.get("parameters"))
                elif u.collection=="dividends_splits":
                    rows=_rows(c,"SELECT s.snapshot_id AS capture_id,s.captured_at,a.content_sha256,a.source_reference,"
                        "a.request_scope_json,NULL AS row_count FROM company_action_snapshots s "
                        "JOIN company_action_source_artifacts a ON a.artifact_id=s.artifact_id "
                        "JOIN company_issuers i ON i.issuer_id=s.issuer_id "
                        "WHERE s.provider='fmp' AND i.cik=? AND collection_capture_utc(s.captured_at)<=? "
                        "ORDER BY s.captured_at LIMIT ?",(dict(u.parameters).get("cik",""),at))
                    # Unit HTTP parameters should not include CIK for FMP. Derive
                    # subject captures using source-scope JSON when no CIK is supplied.
                    if not rows:
                        rows=_rows(c,"SELECT s.snapshot_id AS capture_id,s.captured_at,a.content_sha256,a.source_reference,"
                            "a.request_scope_json,NULL AS row_count FROM company_action_snapshots s "
                            "JOIN company_action_source_artifacts a ON a.artifact_id=s.artifact_id "
                            "WHERE s.provider='fmp' AND json_extract(a.request_scope_json,'$.subject.symbol')=? "
                            "AND json_extract(a.request_scope_json,'$.source')=? AND collection_capture_utc(s.captured_at)<=? "
                            "ORDER BY s.captured_at LIMIT ?",(u.subject,u.endpoint,at))
                    for r in rows:
                        scope=_scope(r)
                        if scope.get("source")==u.endpoint and scope.get("subject",{}).get("symbol")==u.subject:
                            add(u,r,parameters=scope.get("request"))
                elif u.collection=="sec_filings_companyfacts":
                    if u.endpoint=="submissions-history":
                        unchecked.append((u.collection,u.subject,"older_submission_files_require_explicit_retained_receipts"));continue
                    rows=_rows(c,"SELECT s.snapshot_id AS capture_id,s.captured_at,a.content_sha256,a.source_reference,"
                        "a.request_scope_json,NULL AS row_count FROM company_sec_snapshots s "
                        "JOIN company_sec_artifacts a ON a.artifact_id=s.artifact_id "
                        "JOIN company_issuers i ON i.issuer_id=s.issuer_id "
                        "WHERE i.cik=? AND s.snapshot_kind=? AND collection_capture_utc(s.captured_at)<=? "
                        "ORDER BY s.captured_at LIMIT ?",(u.subject,u.endpoint,at))
                    for r in rows:
                        scope=_scope(r)
                        if scope.get("cik")!=u.subject or scope.get("endpoint")!=u.endpoint:
                            raise ConflictError("SEC retained subject differs")
                        add(u,r,parameters={"cik":u.subject})
                elif u.collection=="sharadar_fundamentals":
                    definitions=u.endpoint.startswith("SHARADAR/INDICATORS")
                    metadata=u.endpoint.endswith("/metadata")
                    capture_table="company_sharadar_definition_captures" if definitions else "company_sharadar_captures"
                    artifact_table="company_sharadar_definition_capture_artifacts" if definitions else "company_sharadar_capture_artifacts"
                    capture_time="available_at" if definitions else "captured_at"
                    fields=("a.capture_id||':'||a.ordinal AS capture_id,a.capture_id AS source_capture_id,"
                        "a.ordinal,a.captured_at,a.content_sha256,a.source_reference,a.parameters_json,s.row_count AS row_count")
                    source=" FROM "+artifact_table+" a JOIN "+capture_table+" s ON s.capture_id=a.capture_id "
                    where=" WHERE s."+capture_time+"<=? AND "+("a.ordinal=-1" if metadata else "a.ordinal>=0")
                    args=(at,)
                    if not metadata and not definitions:
                        # Filter the exact original permaticker batch before the
                        # per-subject cap; unrelated selected members cannot consume it.
                        where+=(" AND (SELECT group_concat(provider_subject,',') FROM "
                            "(SELECT DISTINCT provider_subject FROM company_sharadar_identity_assertions i "
                            "WHERE i.capture_id=a.capture_id ORDER BY provider_subject))=?")
                        args+=(u.subject,)
                    if metadata:
                        # One metadata acquisition may be associated with every
                        # subject/dimension capture. Count that acquisition once.
                        query=("SELECT * FROM (SELECT "+fields+",ROW_NUMBER() OVER(PARTITION BY "
                            "a.content_sha256,a.captured_at,a.source_reference,a.parameters_json ORDER BY a.capture_id) AS q"
                            +source+where+") WHERE q=1 ORDER BY captured_at,source_capture_id,ordinal LIMIT ?")
                    else:
                        query="SELECT "+fields+source+where+" ORDER BY a.captured_at,a.capture_id,a.ordinal LIMIT ?"
                    records=_rows(c,query,args)
                    for r in records:
                        parameters={} if metadata else loads_strict(r["parameters_json"])
                        if not metadata and not definitions:
                            tickers=parameters.get("ticker","").split(",")
                            identities=c.execute("SELECT source_ticker,provider_subject FROM company_sharadar_identity_assertions "
                                "WHERE capture_id=? ORDER BY source_ticker LIMIT 101",(r["source_capture_id"],)).fetchall()
                            if (len(identities)>100 or {x["source_ticker"] for x in identities}!=set(tickers)
                                or ",".join(sorted({x["provider_subject"] for x in identities}))!=u.subject):
                                continue
                        blob=c.execute("SELECT raw_byte_count,compressed_body FROM company_sharadar_artifacts WHERE content_sha256=?",
                            (r["content_sha256"],)).fetchone()
                        if blob is None:raise ConflictError("Sharadar retained artifact is missing")
                        size=blob["raw_byte_count"]
                        if size>16*1024*1024 or raw_used+size>raw_total_limit:
                            raise ResourceLimitError("Sharadar raw verification exceeds its bound")
                        import zlib
                        decoder=zlib.decompressobj()
                        try:body=decoder.decompress(blob["compressed_body"],size+1)
                        except zlib.error:raise ConflictError("Sharadar compressed evidence is invalid") from None
                        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data or len(body)!=size:
                            raise ConflictError("Sharadar retained bytes differ")
                        add(u,r,parameters=parameters,body=body,coverage="complete_request")
                elif u.collection=="daily_prices":
                    rows=_rows(c,"SELECT capture_id,captured_at,response_sha256 AS content_sha256,"
                        "'stage10/captures/'||capture_id AS source_reference,request_scope_json,row_count,"
                        "length(response_bytes) AS raw_bytes FROM stage10_daily_price_captures "
                        "WHERE provider_symbol=? AND collection_capture_utc(captured_at)<=? ORDER BY captured_at LIMIT ?",
                        (u.subject,at))
                    for r in rows:
                        scope=_scope(r);parameters=scope.get("parameters")
                        if parameters is None and scope.get("symbol")==u.subject:
                            parameters={"symbol":u.subject}
                            if "from" in scope and "to" in scope:
                                parameters.update({"from":scope["from"],"to":scope["to"]})
                        if parameters is None and all(k in scope for k in ("symbol","start_date","end_date")):
                            parameters={"symbol":scope["symbol"],"from":scope["start_date"],"to":scope["end_date"]}
                        if r["raw_bytes"]>MAX_BODY or raw_used+r["raw_bytes"]>raw_total_limit:
                            raise ResourceLimitError("Collection raw verification budget exceeded")
                        body=c.execute("SELECT response_bytes FROM stage10_daily_price_captures WHERE capture_id=?",(r["capture_id"],)).fetchone()[0]
                        add(u,r,parameters=parameters,body=body,coverage="complete_partition")
                elif u.collection=="equibles_transcripts":
                    rows=_rows(c,"SELECT capture_id,captured_at,event_id,fiscal_year,fiscal_quarter,total_turn_count,page_count "
                        "FROM company_equibles_transcripts WHERE symbol=? AND collection_capture_utc(captured_at)<=? "
                        "ORDER BY captured_at LIMIT ?",(u.subject,at))
                    for r in rows:
                        if _utc(r["captured_at"])>at: continue
                        # Atomic complete calls are known; catalogue termination and
                        # private partial pages belong to the existing checkpoint.
                        key=(u.collection,r["capture_id"])
                        if key in seen: continue
                        seen.add(key)
                        pages=c.execute("SELECT page_index,content_sha256,source_reference,length(raw_body) AS byte_count "
                            "FROM company_equibles_transcript_pages WHERE capture_id=? ORDER BY page_index LIMIT 51",
                            (r["capture_id"],)).fetchall()
                        if len(pages)!=r["page_count"]:
                            raise ConflictError("Retained transcript page bundle is incomplete")
                        for p in pages:
                            if raw_used+p["byte_count"]>raw_total_limit:
                                raise ResourceLimitError("Collection raw verification budget exceeded")
                            raw_used+=p["byte_count"]
                            body=c.execute("SELECT raw_body FROM company_equibles_transcript_pages WHERE capture_id=? AND page_index=?",
                                (r["capture_id"],p["page_index"])).fetchone()[0]
                            if hashlib.sha256(body).hexdigest()!=p["content_sha256"]:
                                raise ConflictError("Retained transcript page hash differs")
                        append_entry(CoverageEntry(u.collection,u.subject,"call:"+r["event_id"],r["capture_id"],
                            _utc(r["captured_at"]),"verified","complete_partition","company_equibles_transcript_pages",
                            r["total_turn_count"],None))
                    unchecked.append((u.collection,u.subject,"catalogue_and_partial_progress_use_existing_checkpoint"))
                elif u.collection=="news":
                    # News matching coverage is source/window-specific. No global
                    # feed capture proves complete ticker-specific news history.
                    symbols=u.selected_symbols or tuple(u.subject.split(","))
                    if not symbols: continue
                    rows=_rows(c,"SELECT DISTINCT v.capture_id,cap.captured_at FROM current_multi_source_article_symbols s "
                        "JOIN current_multi_source_article_versions v ON v.article_version_id=s.article_version_id "
                        "JOIN current_multi_source_captures cap ON cap.capture_id=v.capture_id "
                        "WHERE s.provider_symbol IN ("+",".join("?" for _ in symbols)+") "
                        "AND collection_capture_utc(cap.captured_at)<=? ORDER BY cap.captured_at LIMIT ?",(*symbols,at))
                    for r in rows:
                        if _utc(r["captured_at"])>at: continue
                        append_entry(CoverageEntry(u.collection,u.subject,u.endpoint,r["capture_id"],
                            _utc(r["captured_at"]),"not_checked","partial_history","current_multi_source_article_symbols",None,None))
                    unchecked.append((u.collection,u.subject,"feed_window_and_pagination_receipts_required"))
                else:
                    unchecked.append((u.collection,u.subject,"source_adapter_not_yet_available"))
    return CoverageInventory(at,tuple(entries),tuple(retained),tuple(sorted(set(unchecked))))
