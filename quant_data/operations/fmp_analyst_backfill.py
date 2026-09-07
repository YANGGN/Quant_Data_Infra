"""Manual bounded FMP analyst-history evidence capture; never publishes canonical data."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import time
from typing import Final, Protocol

from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..stores import StoreMap
from .company_market_refresh import MarketCompanySecurity, load_inputs, retain_blob
from .fmp_macro_calendar_history import _StdlibTransport
from .sec_market_companyfacts_refresh import SEC_TICKERS_URL, StdlibSecMarketCompanyFactsTransport, plan_sec_market_issuers

PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
LEDGER_ROOT: Final = PROJECT_ROOT / ".local" / "fmp-analyst-history-20260907" / "universe"
BLOB_ROOT: Final = PROJECT_ROOT / "data" / ".operations" / "company-market-refresh"
MAX_EQUITIES: Final = 519
REQUEST_CAP: Final = 15_000
MAX_TOTAL_BYTES: Final = 1024 * 1024 * 1024
MAX_FMP_RESPONSE_BYTES: Final = 1024 * 1024
MAX_SEC_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_RUN_SECONDS: Final = 7200
REQUEST_TIMEOUT_SECONDS: Final = 30
MIN_REQUEST_INTERVAL_SECONDS: Final = 0.4
MAX_ROWS: Final = 5000
PAGE_LIMIT: Final = 100
MAX_PAGES: Final = 10
AAPL_MSFT: Final = frozenset(("AAPL", "MSFT"))

ENDPOINTS: Final = {
    "analyst_estimates": "https://financialmodelingprep.com/stable/analyst-estimates",
    "grades": "https://financialmodelingprep.com/stable/grades",
    "grades_historical": "https://financialmodelingprep.com/stable/grades-historical",
    "grades_consensus": "https://financialmodelingprep.com/stable/grades-consensus",
    "price_target_consensus": "https://financialmodelingprep.com/stable/price-target-consensus",
    "price_target_summary": "https://financialmodelingprep.com/stable/price-target-summary",
    "earnings": "https://financialmodelingprep.com/stable/earnings",
    "legacy_price_target": "https://financialmodelingprep.com/api/v4/price-target",
}
SOURCE_NATURE: Final = {
    "analyst_estimates": "history", "grades": "history",
    "grades_historical": "history", "grades_consensus": "current_snapshot",
    "price_target_consensus": "current_snapshot", "price_target_summary": "current_snapshot",
    "earnings": "history", "legacy_price_target": "history",
}

class FmpAnalystTransport(Protocol):
    def request(self, *, url: str, parameters: Mapping[str, str], headers: Mapping[str, str], timeout_seconds: int, max_bytes: int) -> object: ...
class SecTickerTransport(Protocol):
    def request(self, *, url: str, headers: Mapping[str, str], timeout_seconds: int, max_bytes: int) -> object: ...

@dataclass(frozen=True)
class _BoundedResponse:
    status: int
    media_type: str
    body: bytes
    redirected: bool = False

def _child_transport_request(connection: object, transport: object, keyword_arguments: Mapping[str, object]) -> None:
    try:
        response = transport.request(**keyword_arguments)  # type: ignore[attr-defined]
        payload = (int(getattr(response, "status")), str(getattr(response, "media_type")), bytes(getattr(response, "body")), bool(getattr(response, "redirected", False)))
        connection.send(("response", payload))  # type: ignore[attr-defined]
    except Exception:
        try: connection.send(("error", "transport_failure"))  # type: ignore[attr-defined]
        except Exception: pass
    finally:
        try: connection.close()  # type: ignore[attr-defined]
        except Exception: pass

class _TotalWallTransport:
    """Contain one inherited stdlib transport call within the supplied wall timeout."""
    def __init__(self, transport: object): self._transport = transport
    def request(self, **keyword_arguments: object) -> _BoundedResponse:
        timeout = keyword_arguments.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > REQUEST_TIMEOUT_SECONDS:
            raise ValidationError("Analyst capture request timeout is invalid")
        try: context = multiprocessing.get_context("fork")
        except ValueError as exc: raise StoreUnavailableError("Analyst capture total timeout is unavailable") from exc
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_child_transport_request, args=(sender, self._transport, dict(keyword_arguments)))
        process.start(); sender.close()
        try:
            if not receiver.poll(timeout):
                process.terminate(); process.join()
                raise StoreUnavailableError("Analyst capture request exceeded total timeout")
            kind, payload = receiver.recv()
            process.join()
            if kind != "response" or not isinstance(payload, tuple) or len(payload) != 4:
                raise StoreUnavailableError("Analyst capture transport failed")
            status, media_type, body, redirected = payload
            if isinstance(status, bool) or not isinstance(status, int) or not isinstance(media_type, str) or not isinstance(body, bytes) or not isinstance(redirected, bool):
                raise StoreUnavailableError("Analyst capture transport failed")
            return _BoundedResponse(status=status, media_type=media_type, body=body, redirected=redirected)
        except (EOFError, OSError) as exc:
            process.terminate(); process.join()
            raise StoreUnavailableError("Analyst capture transport failed") from exc
        finally:
            try: receiver.close()
            except OSError: pass
            if process.is_alive(): process.terminate(); process.join()

@dataclass(frozen=True)
class _Request:
    source: str
    symbol: str | None
    parameters: tuple[tuple[str, str], ...]
    url: str
    max_bytes: int
    @property
    def id(self) -> str:
        text = json.dumps({"source": self.source, "symbol": self.symbol, "parameters": self.parameters, "url": self.url}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

def _private(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise ValidationError("Analyst capture path must be resolved")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise ConflictError("Analyst capture private path conflicts")

def _blob_root(path: Path) -> None:
    if not path.is_absolute() or path.resolve() != path:
        raise ValidationError("Analyst raw-evidence path must be resolved")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if path != BLOB_ROOT:
            _private(path)
            return
        raise StoreUnavailableError("Analyst raw-evidence root is unavailable")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ConflictError("Analyst raw-evidence path conflicts")
    expected_mode = 0o755 if path == BLOB_ROOT else 0o700
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise ConflictError("Analyst raw-evidence path conflicts")

def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)

def _paths(root: Path) -> tuple[Path, Path, Path, Path]:
    _private(root)
    answer = tuple(root / x for x in ("attempts", "results", "analyses", "page_stops"))
    for path in answer: _private(path)
    _fsync_dir(root)
    return answer  # type: ignore[return-value]

def _read(path: Path) -> dict[str, object] | None:
    try: info = path.lstat()
    except FileNotFoundError: return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536:
        raise ConflictError("Analyst capture receipt conflicts")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags), "rb") as handle: raw = handle.read(65537)
    try: value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise ConflictError("Analyst capture receipt conflicts") from exc
    if not isinstance(value, dict): raise ConflictError("Analyst capture receipt conflicts")
    return value

def _write(path: Path, value: Mapping[str, object]) -> bool:
    raw = dumps_strict(dict(value)).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try: fd = os.open(path, flags, 0o600)
    except FileExistsError: return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    except Exception:
        try: path.unlink()
        except OSError: pass
        raise
    _fsync_dir(path.parent)
    return True

def _utc(utcnow: Callable[[], datetime]) -> str:
    value = utcnow()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None: raise ValidationError("Analyst capture clock is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _binding_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

def _freeze_plan(root: Path, securities: Sequence[MarketCompanySecurity], issuers: Mapping[str, str]) -> None:
    roster = [(item.symbol, item.instrument_id) for item in securities]
    issuer_bindings = sorted((cik, issuer_id) for cik, issuer_id in issuers.items())
    if any(not isinstance(cik, str) or not isinstance(issuer_id, str) for cik, issuer_id in issuer_bindings):
        raise ValidationError("Analyst capture issuer bindings are invalid")
    plan = {
        "contract": "quant_data.fmp_analyst_backfill.plan", "version": "1.0.0",
        "roster_count": len(roster), "roster_bindings_sha256": _binding_digest(roster),
        "issuer_count": len(issuer_bindings), "issuer_bindings_sha256": _binding_digest(issuer_bindings),
        "caps": {"max_equities": MAX_EQUITIES, "request_cap": REQUEST_CAP,
                 "total_bytes": MAX_TOTAL_BYTES, "fmp_response_bytes": MAX_FMP_RESPONSE_BYTES,
                 "sec_response_bytes": MAX_SEC_RESPONSE_BYTES, "run_seconds": MAX_RUN_SECONDS,
                 "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS, "minimum_request_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
                 "estimate_page_limit": PAGE_LIMIT, "estimate_max_pages": MAX_PAGES, "source_row_cap": MAX_ROWS},
    }
    path = root / "plan.json"
    existing = _read(path)
    if existing is None:
        _write(path, plan)
        existing = _read(path)
    if existing != plan:
        raise ConflictError("Analyst capture plan drift conflicts")

def _target_dates(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, list):
        raise ValueError
    values: set[str] = set()
    for row in payload:
        if not isinstance(row, Mapping) or not isinstance(row.get("date"), str):
            raise ValueError
        try:
            target = date.fromisoformat(row["date"])
        except ValueError as exc:
            raise ValueError from exc
        if target.isoformat() != row["date"]:
            raise ValueError
        values.add(row["date"])
    return tuple(sorted(values))

def _page_stop(path: Path, request: _Request, *, reason: str | None, target_dates: Sequence[str], prior_oldest: str | None) -> None:
    _write(path / (request.id + ".json"), {"contract": "quant_data.fmp_analyst_backfill.page_stop", "request_id": request.id,
        "reason": reason or "continued", "target_date_count": len(target_dates), "oldest_target_date": min(target_dates) if target_dates else None,
        "prior_oldest_target_date": prior_oldest})

def _make(source: str, symbol: str | None, parameters: Mapping[str, str]) -> _Request:
    url = SEC_TICKERS_URL if source == "sec_ticker_discovery" else ENDPOINTS[source]
    return _Request(source, symbol, tuple(sorted(parameters.items())), url, MAX_SEC_RESPONSE_BYTES if source == "sec_ticker_discovery" else MAX_FMP_RESPONSE_BYTES)

def _usage(attempts: Path, results: Path, analyses: Path) -> tuple[int, int, float, float | None]:
    request_count, size, seconds, latest = 0, 0, 0.0, None
    for path in attempts.glob("*.json"):
        receipt = _read(path); request_count += 1
        stamp = receipt.get("request_start_epoch") if receipt else None
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp): latest = stamp if latest is None else max(latest, stamp)
        consumed = receipt.get("consumed_seconds") if receipt else None
        if isinstance(consumed, (int, float)) and not isinstance(consumed, bool) and consumed >= 0: seconds = max(seconds, float(consumed))
    for path in results.glob("*.json"):
        receipt = _read(path) or {}
        item = receipt.get("body_bytes")
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0: size += item
        consumed = receipt.get("consumed_seconds")
        if isinstance(consumed, (int, float)) and not isinstance(consumed, bool) and consumed >= 0: seconds = max(seconds, float(consumed))
    for path in analyses.glob("*.json"):
        receipt = _read(path) or {}
        consumed = receipt.get("consumed_seconds")
        if isinstance(consumed, (int, float)) and not isinstance(consumed, bool) and consumed >= 0: seconds = max(seconds, float(consumed))
    return request_count, size, seconds, latest

def _response(response: object) -> tuple[int | None, str, bytes | None, bool, str | None]:
    status, media, body, redirected = getattr(response, "status", None), getattr(response, "media_type", ""), getattr(response, "body", None), getattr(response, "redirected", False)
    if isinstance(status, bool) or not isinstance(status, int) or not isinstance(media, str) or not isinstance(body, bytes) or not isinstance(redirected, bool): return None, "", None, False, "invalid_transport_response"
    return status, media, body, redirected, None

def _request(*, request: _Request, invoke: Callable[[int, int], object], attempts: Path, results: Path, blob_root: Path, key: str, count: int, used_bytes: int, used_seconds: float, last_start: float | None, monotonic: Callable[[], float], wall_clock: Callable[[], float], sleeper: Callable[[float], None], utcnow: Callable[[], datetime], execution_seconds: Callable[[], float]) -> tuple[dict[str, object] | None, int, int, float, float | None, str | None]:
    used_seconds = execution_seconds()
    result_path, attempt_path = results / (request.id + ".json"), attempts / (request.id + ".json")
    existing = _read(result_path)
    if existing is not None: return existing, count, used_bytes, used_seconds, last_start, None
    if _read(attempt_path) is not None: return None, count, used_bytes, used_seconds, last_start, "uncertain_reserved_attempt"
    if count >= REQUEST_CAP or used_bytes >= MAX_TOTAL_BYTES: return None, count, used_bytes, used_seconds, last_start, "aggregate_cap"
    if used_seconds > MAX_RUN_SECONDS: return None, count, used_bytes, used_seconds, last_start, "aggregate_deadline_overrun"
    if MAX_RUN_SECONDS - used_seconds < 1: return None, count, used_bytes, used_seconds, last_start, "aggregate_deadline"
    now = wall_clock()
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now): raise ValidationError("Analyst wall clock is invalid")
    wait = 0.0 if last_start is None else max(0.0, MIN_REQUEST_INTERVAL_SECONDS - (now - last_start))
    if wait >= MAX_RUN_SECONDS - used_seconds: return None, count, used_bytes, used_seconds, last_start, "aggregate_deadline"
    if not _write(attempt_path, {"contract":"quant_data.fmp_analyst_backfill.attempt", "request_id":request.id, "source":request.source, "symbol":request.symbol, "parameters":dict(request.parameters), "reserved_at":_utc(utcnow), "request_start_epoch":now, "consumed_seconds":used_seconds}): return None, count, used_bytes, used_seconds, last_start, "uncertain_reserved_attempt"
    if wait: sleeper(wait)
    remaining = MAX_RUN_SECONDS - execution_seconds()
    if remaining < 1: return None, count, used_bytes, execution_seconds(), last_start, "aggregate_deadline"
    started = monotonic(); timeout = min(REQUEST_TIMEOUT_SECONDS, int(math.floor(remaining)))
    try: status, media, body, redirected, error = _response(invoke(timeout, min(request.max_bytes, MAX_TOTAL_BYTES - used_bytes)))
    except Exception: status, media, body, redirected, error = None, "", None, False, "transport_failure"
    elapsed, started_epoch = max(0.0, monotonic()-started), wall_clock()
    body_size, reference, digest, retention = (len(body), None, hashlib.sha256(body).hexdigest(), None) if body is not None else (0, None, None, None)
    if body is not None:
        if body_size > request.max_bytes: error = "response_too_large"
        elif key.encode() in body: retention = "credential_bearing_response"
        elif not body: retention = "empty_response"
        else: reference = retain_blob(blob_root, body)
    receipt = {"contract":"quant_data.fmp_analyst_backfill.result", "request_id":request.id, "source":request.source, "symbol":request.symbol, "status":status, "media_type":media, "redirected":redirected, "captured_at":_utc(utcnow), "reference":reference, "body_sha256":digest, "body_bytes":body_size, "transport_error":error, "retention_error":retention, "elapsed_seconds":elapsed, "wait_seconds":wait, "consumed_seconds":execution_seconds()}
    _write(result_path, receipt)
    current_seconds = execution_seconds()
    return _read(result_path) or receipt, count+1, used_bytes+body_size, current_seconds, started_epoch, "aggregate_deadline_overrun" if current_seconds > MAX_RUN_SECONDS else None

def _analysis(request: _Request, result: Mapping[str, object], analyses: Path, blob_root: Path, execution_seconds: Callable[[], float]) -> dict[str, object]:
    path = analyses/(request.id+".json"); old = _read(path)
    if old is not None: return old
    outcome, rows, digest, target_dates = "available", 0, None, ()
    if result.get("transport_error"): outcome = str(result["transport_error"])
    elif result.get("redirected") is True: outcome = "redirected"
    elif result.get("status") != 200: outcome = "http_"+str(result.get("status"))
    elif str(result.get("media_type", "")).split(";",1)[0].strip().lower() != "application/json": outcome = "invalid_media_type"
    elif not isinstance(result.get("reference"), str): outcome = str(result.get("retention_error") or "unavailable_body")
    else:
        reference = str(result["reference"]); sha = reference.rsplit("/", 1)[-1].removesuffix(".json")
        try:
            body = (blob_root/"blobs"/(sha+".json")).read_bytes()
            payload = json.loads(body.decode("utf-8", errors="strict"))
            if hashlib.sha256(body).hexdigest() != sha: raise ValueError
            if request.source == "sec_ticker_discovery":
                if not isinstance(payload, Mapping): raise ValueError
                rows, digest = len(payload), hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",",":")).encode()).hexdigest()
            elif not isinstance(payload, list): raise ValueError
            elif len(payload) > MAX_ROWS: outcome = "row_cap_exceeded"
            else:
                rows, digest = len(payload), hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",",":")).encode()).hexdigest()
                if request.source == "analyst_estimates":
                    try: target_dates = _target_dates(payload)
                    except ValueError: outcome = "malformed_target_dates"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError): outcome = "malformed_json"
    answer = {"contract":"quant_data.fmp_analyst_backfill.analysis", "request_id":request.id, "outcome":outcome, "row_count":rows, "page_digest":digest, "target_dates":list(target_dates), "consumed_seconds":execution_seconds()}
    _write(path, answer); return _read(path) or answer

def _report(records: Sequence[Mapping[str, object]], count: int, byte_count: int, seconds: float, stop: str | None, unmatched: Sequence[str]=(), ambiguous: Sequence[str]=(), eligible: Sequence[str]=(), rejected: int=0, blocked: set[str] | None=None, missing_existing_issuer_symbols: Sequence[str]=()) -> dict[str, object]:
    failed = sum(r.get("outcome") not in {"available", "endpoint_blocked"} for r in records)
    deadline_overrun = seconds > MAX_RUN_SECONDS
    incomplete = bool(stop or failed or unmatched or ambiguous or missing_existing_issuer_symbols or deadline_overrun)
    return {"contract":"quant_data.fmp_analyst_backfill", "version":"1.0.0", "outcome":"partial" if incomplete else "succeeded", "exit_code":75 if incomplete else 0, "requests":count, "request_cap":REQUEST_CAP, "response_bytes":byte_count, "response_byte_cap":MAX_TOTAL_BYTES, "consumed_seconds":seconds, "run_second_cap":MAX_RUN_SECONDS, "aggregate_deadline_overrun":deadline_overrun, "stop_reason":stop, "eligible_symbols":list(eligible), "unmatched_symbols":list(unmatched), "ambiguous_symbols":list(ambiguous), "missing_existing_issuer_symbols":list(missing_existing_issuer_symbols), "rejected_discovery_rows":rejected, "blocked_endpoints":sorted(blocked or ()), "records":[dict(r) for r in records]}

def capture_universe(*, roster: Sequence[MarketCompanySecurity], issuers: Mapping[str,str], sec_transport: SecTickerTransport, fmp_transport: FmpAnalystTransport, fmp_key: str, sec_headers: Mapping[str,str], state_root: Path, blob_root: Path=BLOB_ROOT, monotonic: Callable[[],float]=time.monotonic, wall_clock: Callable[[],float]=time.time, sleeper: Callable[[float],None]=time.sleep, utcnow: Callable[[],datetime]=lambda: datetime.now(timezone.utc)) -> dict[str, object]:
    securities = tuple(roster)
    if not securities or len(securities)>MAX_EQUITIES or any(not isinstance(x, MarketCompanySecurity) for x in securities) or len({x.symbol for x in securities}) != len(securities): raise ValidationError("Analyst capture roster is invalid")
    if not isinstance(fmp_key,str) or not fmp_key or not isinstance(issuers,Mapping) or not isinstance(sec_headers,Mapping): raise ValidationError("Analyst capture inputs are invalid")
    _private(Path(state_root)); _blob_root(Path(blob_root)); attempts, results, analyses, page_stops = _paths(Path(state_root)); _freeze_plan(Path(state_root), securities, issuers); count, byte_count, prior_seconds, last = _usage(attempts,results,analyses); session_started = monotonic()
    def execution_seconds() -> float:
        return prior_seconds + max(0.0, monotonic() - session_started)
    seconds = execution_seconds(); records: list[dict[str,object]]=[]
    discovery_request = _make("sec_ticker_discovery",None,{})
    discovery,count,byte_count,seconds,last,stop = _request(request=discovery_request, invoke=lambda timeout, max_bytes: sec_transport.request(url=SEC_TICKERS_URL,headers=dict(sec_headers),timeout_seconds=timeout,max_bytes=max_bytes),attempts=attempts,results=results,blob_root=Path(blob_root),key=fmp_key,count=count,used_bytes=byte_count,used_seconds=seconds,last_start=last,monotonic=monotonic,wall_clock=wall_clock,sleeper=sleeper,utcnow=utcnow,execution_seconds=execution_seconds)
    if discovery is None: return _report(records,count,byte_count,execution_seconds(),stop)
    analysis = _analysis(discovery_request, discovery, analyses, Path(blob_root), execution_seconds); records.append({"request_id":discovery_request.id,"source":"sec_ticker_discovery","outcome":analysis["outcome"],"reference":discovery.get("reference")})
    if stop: return _report(records,count,byte_count,execution_seconds(),stop)
    if analysis["outcome"] != "available": return _report(records,count,byte_count,execution_seconds(),"discovery_"+str(analysis["outcome"]))
    reference=discovery.get("reference")
    if not isinstance(reference,str): raise ConflictError("Analyst discovery evidence is unavailable")
    plans,unmatched,ambiguous,rejected=plan_sec_market_issuers(symbols=tuple(x.symbol for x in securities),discovery_body=(Path(blob_root)/"blobs"/reference.rsplit("/",1)[-1]).read_bytes())
    missing_existing_issuer_symbols=tuple(sorted(s for p in plans if p.cik not in issuers for s in p.symbols if s not in AAPL_MSFT))
    eligible=sorted(s for p in plans if p.cik in issuers for s in p.symbols if s not in AAPL_MSFT); blocked:set[str]=set(); stop=None
    for symbol in eligible:
        for period in ("annual","quarter"):
            if "analyst_estimates" in blocked:
                records.append({"symbol":symbol,"source":"analyst_estimates","period":period,"nature":"history","outcome":"endpoint_blocked"})
                continue
            prior: set[str]=set(); prior_oldest: str | None = None
            for page in range(MAX_PAGES):
                request=_make("analyst_estimates",symbol,{"symbol":symbol,"period":period,"page":str(page),"limit":str(PAGE_LIMIT)})
                result,count,byte_count,seconds,last,reason=_request(request=request,invoke=lambda timeout,max_bytes,request=request: fmp_transport.request(url=request.url,parameters={**dict(request.parameters),"apikey":fmp_key},headers={"Accept":"application/json","User-Agent":"QuantDataInfra/1.0"},timeout_seconds=timeout,max_bytes=max_bytes),attempts=attempts,results=results,blob_root=Path(blob_root),key=fmp_key,count=count,used_bytes=byte_count,used_seconds=seconds,last_start=last,monotonic=monotonic,wall_clock=wall_clock,sleeper=sleeper,utcnow=utcnow,execution_seconds=execution_seconds)
                if result is None: stop=reason or "capture_stopped"; break
                parsed=_analysis(request,result,analyses,Path(blob_root),execution_seconds); outcome=str(parsed["outcome"]); row_count=int(parsed["row_count"]); digest=parsed.get("page_digest")
                target_dates = tuple(parsed.get("target_dates", ()))
                if not all(isinstance(value, str) for value in target_dates):
                    outcome = "malformed_target_dates"; target_dates = ()
                if outcome=="available" and isinstance(digest,str) and digest in prior: outcome="repeated_page"
                elif outcome=="available" and page and not set(target_dates).difference(prior): outcome="non_progressing_target_dates"
                elif outcome=="available" and page and prior_oldest is not None and (not target_dates or min(target_dates) >= prior_oldest): outcome="non_extending_oldest_target_date"
                elif outcome=="available" and page==MAX_PAGES-1 and row_count==PAGE_LIMIT: outcome="max_page_full"
                stop_reason = outcome if outcome != "available" else ("short_or_empty_page" if row_count < PAGE_LIMIT else None)
                _page_stop(page_stops, request, reason=stop_reason, target_dates=target_dates, prior_oldest=prior_oldest)
                records.append({"request_id":request.id,"symbol":symbol,"source":"analyst_estimates","period":period,"page":page,"nature":"history","outcome":outcome,"row_count":row_count,"target_dates":list(target_dates),"page_stop_reason":stop_reason,"reference":result.get("reference")})
                if reason: stop=reason; break
                if result.get("status") in {401,403,429}: stop="systemic_http_"+str(result["status"]); break
                if result.get("status")==402: blocked.add("analyst_estimates"); break
                if outcome != "available" or row_count<PAGE_LIMIT: break
                if isinstance(digest,str): prior.add(digest)
                prior.update(target_dates)
                if target_dates: prior_oldest = min(target_dates) if prior_oldest is None else min(prior_oldest, min(target_dates))
            if stop: break
        if stop: break
        for source in ("grades","grades_historical","grades_consensus","price_target_consensus","price_target_summary","earnings","legacy_price_target"):
            if source in blocked: records.append({"symbol":symbol,"source":source,"outcome":"endpoint_blocked"}); continue
            params={"symbol":symbol} if source=="legacy_price_target" else {"symbol":symbol,"limit":"1000"}; request=_make(source,symbol,params)
            result,count,byte_count,seconds,last,reason=_request(request=request,invoke=lambda timeout,max_bytes,request=request: fmp_transport.request(url=request.url,parameters={**dict(request.parameters),"apikey":fmp_key},headers={"Accept":"application/json","User-Agent":"QuantDataInfra/1.0"},timeout_seconds=timeout,max_bytes=max_bytes),attempts=attempts,results=results,blob_root=Path(blob_root),key=fmp_key,count=count,used_bytes=byte_count,used_seconds=seconds,last_start=last,monotonic=monotonic,wall_clock=wall_clock,sleeper=sleeper,utcnow=utcnow,execution_seconds=execution_seconds)
            if result is None: stop=reason or "capture_stopped"; break
            parsed=_analysis(request,result,analyses,Path(blob_root),execution_seconds); records.append({"request_id":request.id,"symbol":symbol,"source":source,"nature":SOURCE_NATURE[source],"outcome":parsed["outcome"],"row_count":parsed["row_count"],"reference":result.get("reference")})
            if reason: stop=reason; break
            if result.get("status") in {401,403,429}: stop="systemic_http_"+str(result["status"]); break
            if result.get("status")==402: blocked.add(source)
        if stop: break
    return _report(records,count,byte_count,execution_seconds(),stop,unmatched,ambiguous,eligible,rejected,blocked,missing_existing_issuer_symbols)

def run_universe_capture() -> dict[str, object]:
    if PROJECT_ROOT.resolve(strict=True) != PROJECT_ROOT: raise ValidationError("Analyst capture project binding is invalid")
    stores=StoreMap.from_mapping({x:PROJECT_ROOT/"data"/(x+".sqlite") for x in ("market","macro","company","news")}); roster,issuers=load_inputs(stores)
    if len(roster)!=MAX_EQUITIES: raise ResourceLimitError("Analyst capture retained universe changed")
    key=read_project_credential(project_root=PROJECT_ROOT,name="FMP_API_KEY",environment=os.environ); name=read_project_credential(project_root=PROJECT_ROOT,name="SEC_USER_AGENT_NAME",environment=os.environ); email=read_project_credential(project_root=PROJECT_ROOT,name="SEC_USER_AGENT_EMAIL",environment=os.environ)
    return capture_universe(roster=roster,issuers=issuers,sec_transport=_TotalWallTransport(StdlibSecMarketCompanyFactsTransport()),fmp_transport=_TotalWallTransport(_StdlibTransport()),fmp_key=key,sec_headers={"Accept":"application/json","User-Agent":name+" "+email},state_root=LEDGER_ROOT,blob_root=BLOB_ROOT)

def main(argv: Sequence[str] | None=None) -> int:
    if tuple(sys.argv[1:] if argv is None else argv)!=("--capture-universe",): print(dumps_strict({"contract":"quant_data.fmp_analyst_backfill","error":"explicit_capture_required","exit_code":64})); return 64
    try: report=run_universe_capture()
    except Exception: print(dumps_strict({"contract":"quant_data.fmp_analyst_backfill","outcome":"failed","error":"capture_failed","exit_code":75})); return 75
    print(dumps_strict(report)); return int(report["exit_code"])
if __name__=="__main__": raise SystemExit(main())
