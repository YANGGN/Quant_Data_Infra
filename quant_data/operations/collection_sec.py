"""Issuer-deduplicated SEC acquisition and complete-pair publication.

Raw submissions and CompanyFacts responses stay in the provider ledger with
their individual acquisition times. The canonical bundle becomes available
when both responses have been captured. Historical filing-index files are
enumerated separately and are never described as filing-document downloads.
"""
from datetime import datetime
from pathlib import Path
import hashlib,re,tempfile,time
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..json_codec import loads_strict
from ..market.collection_universe import _utc
from ..company.sec_companyfacts import (parse_sec_companyfacts_bundle, SecCompanyFactsPublisher,
    sec_companyfacts_run_id, SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID)
from ..stores import quiet_immutable_read_connection
from ..ingestion import PublicationDeferred
from .collection_targets import selected_sec_issuers
from .collection_plan import AcquisitionUnit,validate_unit,digest
from .collection_queue import _response,_read,atomic
from .equibles_transcript_backfill import job_lock,private_directory

class SecSourceRejected(ValidationError):
    """Only original source shape/values failed validation, before store writes."""

    def __init__(self, reason_code):
        self.reason_code=reason_code
        super().__init__("SEC source rejected: "+reason_code)

METADATA_CONFLICT_MESSAGE = "SEC filing accession conflicts with immutable prior metadata"
METADATA_HOLD_CONTRACT = "quant_data.sec_filing_metadata_hold.v1"


def _metadata_identity(parsed):
    return {"cik": parsed.cik, "dataset_id": SEC_AAPL_COMPANYFACTS_CANONICAL_DATASET_ID,
        "semantic_identity": parsed.semantic_identity, "run_id": sec_companyfacts_run_id(parsed),
        "collector_id": parsed.collector_id, "legacy_aapl": parsed.legacy_aapl}


def _metadata_failure(stores, parsed):
    identity = _metadata_identity(parsed)
    with quiet_immutable_read_connection(stores, "company") as connection:
        row = connection.execute("SELECT * FROM ingestion_run_failures WHERE run_id=?",
            (identity["run_id"],)).fetchone()
        if row is None:
            raise ConflictError("SEC metadata hold lacks its canonical failure audit")
        failure = dict(row)
    if (failure["dataset_id"] != identity["dataset_id"]
        or failure["semantic_identity"] != identity["semantic_identity"]
        or failure["command"] != identity["collector_id"]
        or failure["error_code"] != "conflict" or failure["status"] != "failed"):
        raise ConflictError("SEC metadata hold audit identity differs")
    return failure


def read_metadata_hold(*, hold_root, stores, parsed):
    """A classified canonical-publication hold is independent of acquisition date."""
    hold_root = Path(hold_root)
    if not hold_root.is_absolute() or hold_root.resolve() != hold_root:
        raise ValidationError("SEC metadata hold root must be explicit and resolved")
    identity = _metadata_identity(parsed)
    held = _read(hold_root / (identity["run_id"] + ".json"))
    if held is None:
        return None
    if (not isinstance(held, dict) or held.get("contract") != METADATA_HOLD_CONTRACT
        or held.get("identity") != identity
        or held.get("classification") != {"type": "ConflictError", "message": METADATA_CONFLICT_MESSAGE}
        or held.get("outcome") != "blocked_canonical_conflict"
        or held.get("failure_audit") != _metadata_failure(stores, parsed)):
        raise ConflictError("SEC classified metadata hold differs from its evidence")
    source = held.get("original_source", {})
    if (not isinstance(source, dict)
        or any(not isinstance(source.get(key), str)
            or not re.fullmatch("[0-9a-f]{64}", source[key])
            for key in ("submissions_sha256", "companyfacts_sha256"))):
        raise ConflictError("SEC metadata hold source evidence is invalid")
    _utc(source.get("captured_at"))
    return held


def record_metadata_hold(*, hold_root, stores, parsed, error):
    """Retain only an exact observed metadata rejection after canonical rollback/audit."""
    if type(error) is not ConflictError or str(error) != METADATA_CONFLICT_MESSAGE:
        raise error
    hold_root = Path(hold_root)
    private_directory(hold_root)
    with job_lock(hold_root):
        prior = read_metadata_hold(hold_root=hold_root, stores=stores, parsed=parsed)
        if prior is not None:
            return prior
        held = {"contract": METADATA_HOLD_CONTRACT, "identity": _metadata_identity(parsed),
            "classification": {"type": "ConflictError", "message": METADATA_CONFLICT_MESSAGE},
            "outcome": "blocked_canonical_conflict",
            "original_source": {"submissions_sha256": parsed.submissions_sha256,
                "companyfacts_sha256": parsed.companyfacts_sha256, "captured_at": parsed.captured_at},
            "failure_audit": _metadata_failure(stores, parsed)}
        atomic(hold_root / (held["identity"]["run_id"] + ".json"), held)
        return held


def requires_active_binding(stores):
    temporary=Path(tempfile.gettempdir()).resolve(strict=True)
    return not all(p.is_absolute() and p.resolve()==p and p!=temporary and p.is_relative_to(temporary)
        for _,p in stores.items())

def sec_units(stores,selection,*,mode,observation_window,cutoff,require_active=False):
    targets=selected_sec_issuers(stores,selection,cutoff=cutoff,require_active=require_active)
    return tuple(validate_unit(AcquisitionUnit("sec_filings_companyfacts","sec",endpoint,t.cik,
        (("cik",t.cik),),mode,observation_window,t.source_members,selection.scope_sha256,
        max_response_bytes=16*1024*1024 if t.cik=="0000320193" else 64*1024*1024,
        max_rows=10000 if t.cik=="0000320193" else 50000,timeout_seconds=120))
        for t in targets for endpoint in ("submissions","companyfacts"))

def submissions_history_files(body,*,cik):
    if not re.fullmatch("[0-9]{10}",cik):
        raise ValidationError("SEC history needs an exact CIK")
    value=loads_strict(body,max_bytes=64*1024*1024)
    if not isinstance(value,dict) or str(value.get("cik","")).zfill(10)!=cik:
        raise ConflictError("SEC history index differs from the selected issuer")
    files=value.get("filings",{}).get("files",[])
    if not isinstance(files,list) or len(files)>100:
        raise ResourceLimitError("SEC historical index list exceeds its bound")
    names=[]
    for row in files:
        name=row.get("name") if isinstance(row,dict) else None
        if not isinstance(name,str) or not re.fullmatch("CIK"+cik+"-submissions-[0-9]{3}\\.json",name):
            raise ValidationError("SEC historical filename is outside its issuer")
        if name in names:raise ConflictError("SEC historical index file is duplicated")
        names.append(name)
    return tuple(names)

def publish_sec_pairs(*,root,stores,registry,selection,units,cutoff,deadline=None,monotonic=time.monotonic,hold_root=None):
    """Publish only fully retained issuer pairs; issue zero provider requests."""
    targets=selected_sec_issuers(stores,selection,cutoff=cutoff,
        require_active=requires_active_binding(stores))
    by_cik={t.cik:t for t in targets}
    expected={}
    for u in units:
        validate_unit(u)
        target=by_cik.get(u.subject)
        if (target is None or u.collection!="sec_filings_companyfacts" or u.provider!="sec"
            or u.endpoint not in ("submissions","companyfacts")
            or u.parameters!=(("cik",u.subject),) or u.selected_symbols!=target.source_members
            or u.selection_sha256!=selection.scope_sha256):
            raise ConflictError("SEC publication unit differs from its pinned issuer")
        key=(u.subject,u.endpoint)
        if key in expected:raise ConflictError("SEC publication has duplicate issuer endpoints")
        expected[key]=u
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValidationError("SEC retained ledger root must be explicit and resolved")
    hold_root = root / "sec-metadata-holds" if hold_root is None else Path(hold_root)
    if not hold_root.is_absolute() or hold_root.resolve() != hold_root:
        raise ValidationError("SEC metadata hold root must be explicit and resolved")
    outcomes=[]
    with job_lock(root):
        private_directory(root/"sec-bundles")
        private_directory(root/"sec-publications")
        for cik in sorted({u.subject for u in units}):
            if deadline is not None and monotonic()>=deadline:
                outcomes.append({"cik":cik,"outcome":"publication_deadline"});break
            pair=[]
            for endpoint in ("submissions","companyfacts"):
                u=expected.get((cik,endpoint))
                pair.append(None if u is None else _response(root,u.unit_id,u))
            if any(p is None or p[0]["status"]!=200 for p in pair):
                outcomes.append({"cik":cik,"outcome":"incomplete_pair"});continue
            if len(pair[0][1])+len(pair[1][1])>(16 if cik=="0000320193" else 64)*1024*1024:
                raise SecSourceRejected("ResourceLimitError")
            times=[_utc(p[0]["captured_at"]) for p in pair]
            if max(times)>_utc(cutoff):
                raise ConflictError("SEC response is beyond the publication cutoff")
            modes={(expected[(cik,e)].mode,expected[(cik,e)].observation_window) for e in ("submissions","companyfacts")}
            if len(modes)!=1:raise ConflictError("SEC responses belong to different observation windows")
            target=by_cik[cik]
            manifest={"contract":"quant_data.selected_sec_pair.v1","cik":cik,
                "selection_sha256":selection.scope_sha256,"source_members":list(target.source_members),
                "responses":[{"endpoint":e,"request_id":p[0]["request_id"],
                    "unit_id":p[0]["unit_id"],"content_sha256":p[0]["content_sha256"],
                    "captured_at":_utc(p[0]["captured_at"])} for e,p in zip(("submissions","companyfacts"),pair)],
                "bundle_available_at":max(times)}
            # Acquisition identity excludes membership; associations are retained separately.
            key=digest({"cik":cik,"responses":manifest["responses"]})
            atomic(root/"sec-bundles"/(digest(manifest)+".json"),manifest)
            prior=_read(root/"sec-publications"/(key+".json"))
            if prior is not None:
                outcomes.append({"cik":cik,"outcome":"reused","receipt":prior});continue
            try:
                source=loads_strict(pair[0][1],max_bytes=64*1024*1024)
                if not isinstance(source,dict):
                    raise ValidationError("SEC submissions must be an object")
                tickers=source.get("tickers",[])
                if not isinstance(tickers,list) or any(not isinstance(t,str) for t in tickers):
                    raise ValidationError("SEC submissions tickers must be a string list")
                if tickers and not set(tickers).intersection(target.provider_symbols):
                    raise ConflictError("SEC submissions symbols differ from the selected issuer")
                # Pure parsing preserves original bytes and the AAPL compatibility identity.
                parsed=parse_sec_companyfacts_bundle(cik=cik,
                    submissions_body=pair[0][1],companyfacts_body=pair[1][1],
                    captured_at=datetime.fromisoformat(max(times).replace("Z","+00:00")))
            except (ConflictError,ResourceLimitError,ValidationError) as error:
                raise SecSourceRejected(type(error).__name__) from None
            if deadline is not None and monotonic()>=deadline:
                outcomes.append({"cik":cik,"outcome":"publication_deadline"});break
            held = read_metadata_hold(hold_root=hold_root, stores=stores, parsed=parsed)
            if held is not None:
                outcomes.append({"cik": cik, "outcome": "blocked_canonical_conflict",
                    "run_id": held["identity"]["run_id"], "held": True})
                continue
            try:
                result=SecCompanyFactsPublisher(stores,registry,legacy_aapl=parsed.legacy_aapl).publish(
                    parsed,deadline=deadline,monotonic_clock=monotonic)
            except PublicationDeferred:
                outcomes.append({"cik":cik,"outcome":"publication_deadline"});break
            except ConflictError as error:
                held = record_metadata_hold(hold_root=hold_root, stores=stores, parsed=parsed, error=error)
                outcomes.append({"cik": cik, "outcome": "blocked_canonical_conflict",
                    "run_id": held["identity"]["run_id"], "held": False})
                continue
            receipt={"cik":cik,"outcome":result.outcome,"run_id":result.run_id,
                "bundle_sha256":key,"bundle_available_at":max(times),
                "coverage":"recent_submissions_and_supported_companyfacts"}
            atomic(root/"sec-publications"/(key+".json"),receipt)
            outcomes.append(receipt)
    return tuple(outcomes)
