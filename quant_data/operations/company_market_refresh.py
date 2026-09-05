"""Fixed company actions and annual-estimates refresh for retained market equities.

One SEC ticker discovery resolves the existing roster to existing CIK identities.
Each FMP response is bounded, retained privately, and published independently.
No provider work occurs under a database lock, and no retry or catch-up is made.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Callable, Mapping, Sequence

from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, quiet_immutable_read_connection
from .fmp_macro_calendar_history import _StdlibTransport
from .sec_market_companyfacts_refresh import (
    SEC_TICKERS_URL, StdlibSecMarketCompanyFactsTransport,
    _credential_component, _validate_response as validate_sec_response,
    plan_sec_market_issuers,
)

PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
STATE_RELATIVE = Path("data/.operations/company-market-refresh")
MAX_EQUITIES = 700
REQUEST_CAP = 1 + 3 * MAX_EQUITIES
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DISCOVERY_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_RUN_SECONDS = 1800
MIN_REQUEST_INTERVAL_SECONDS = 0.4
SOURCES = ("dividends", "splits", "analyst_estimates")
ENDPOINTS = {
    "dividends": "https://financialmodelingprep.com/stable/dividends",
    "splits": "https://financialmodelingprep.com/stable/splits",
    "analyst_estimates": "https://financialmodelingprep.com/stable/analyst-estimates",
}


@dataclass(frozen=True)
class MarketCompanySecurity:
    symbol: str
    instrument_id: str


def load_inputs(stores: StoreMap) -> tuple[tuple[MarketCompanySecurity, ...], dict[str, str]]:
    with quiet_immutable_read_connection(stores, "market") as connection:
        rows = connection.execute(
            "SELECT provider_symbol, instrument_id FROM stage10_instruments "
            "WHERE provider='fmp' AND asset_type='equity' ORDER BY provider_symbol LIMIT ?",
            (MAX_EQUITIES + 1,),
        ).fetchall()
    if not rows or len(rows) > MAX_EQUITIES:
        raise ResourceLimitError("Company market roster exceeds its bound")
    roster = tuple(MarketCompanySecurity(str(r[0]), str(r[1])) for r in rows)
    if len({s.symbol for s in roster}) != len(roster):
        raise ConflictError("Company market symbols are ambiguous")
    with quiet_immutable_read_connection(stores, "company") as connection:
        rows = connection.execute(
            "SELECT cik,issuer_id FROM company_issuers ORDER BY cik LIMIT 10001"
        ).fetchall()
    if len(rows) > 10000:
        raise ResourceLimitError("Company issuer roster exceeds its bound")
    return roster, {str(r[0]): str(r[1]) for r in rows}


def retain_blob(state_root: Path, body: bytes) -> str:
    """Publish immutable private response bytes before a canonical publication."""
    if not isinstance(body, bytes) or not body or len(body) > MAX_DISCOVERY_BYTES:
        raise ResourceLimitError("Company response blob exceeds its bound")
    state_root = Path(state_root)
    if not state_root.is_absolute():
        raise ValidationError("Company state root must be explicit")
    if state_root.resolve() != state_root:
        raise ValidationError("Company state path must not contain aliases")
    directory = state_root / "blobs"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValidationError("Company blob directory is invalid")
    digest = hashlib.sha256(body).hexdigest()
    target = directory / (digest + ".json")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError:
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ConflictError("Company blob identity is invalid")
        with target.open("rb") as handle:
            existing = handle.read(MAX_DISCOVERY_BYTES + 1)
        if existing != body:
            raise ConflictError("Company immutable blob differs")
    else:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    return "company-market-refresh/blobs/" + digest + ".json"


def _error_code(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "invalid_response"
    if isinstance(error, ConflictError):
        return "conflict"
    if isinstance(error, ResourceLimitError):
        return "resource_limit"
    if isinstance(error, StoreUnavailableError):
        return "unavailable"
    return "internal_failure"


def run_company_market_refresh(
    *, roster: Sequence[MarketCompanySecurity], issuers: Mapping[str, str],
    discovery_body: bytes, fetch: Callable[..., object], publisher: object,
    state_root: Path, started: float, monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    """Run a supplied, already bounded discovery through isolated FMP sources."""
    from ..company.fmp_market_data import FmpCompanySubject, parse_fmp_company_response
    if not isinstance(started, (int, float)) or isinstance(started, bool) or not math.isfinite(started):
        raise ValidationError("Company refresh clock is invalid")
    if not isinstance(discovery_body, bytes) or len(discovery_body) > MAX_DISCOVERY_BYTES:
        raise ResourceLimitError("Company discovery exceeds its bound")
    securities = tuple(roster)
    if any(not isinstance(item, MarketCompanySecurity) for item in securities):
        raise ValidationError("Company roster is invalid")
    plans, unmatched, ambiguous, rejected = plan_sec_market_issuers(
        symbols=tuple(item.symbol for item in securities), discovery_body=discovery_body
    )
    by_symbol = {item.symbol: item for item in securities}
    evidence = hashlib.sha256(discovery_body).hexdigest()
    discovery_reference = retain_blob(state_root, discovery_body)
    missing_issuers = tuple(p.cik for p in plans if p.cik not in issuers)
    subjects = [
        FmpCompanySubject(
            issuer_id=issuers[plan.cik], cik=plan.cik, symbol=symbol,
            instrument_id=by_symbol[symbol].instrument_id,
            identity_evidence_sha256=evidence,
        )
        for plan in plans if plan.cik in issuers for symbol in plan.symbols
    ]
    subjects.sort(key=lambda item: (item.symbol != "AAPL", item.symbol))
    deadline = started + MAX_RUN_SECONDS
    results: list[dict[str, object]] = []
    requests = 1
    response_bytes = len(discovery_body)
    last_request: float | None = None
    exhausted = False
    for subject in subjects:
        for source in SOURCES:
            now = monotonic()
            if last_request is not None:
                delay = max(0.0, MIN_REQUEST_INTERVAL_SECONDS - (now - last_request))
                if now + delay >= deadline:
                    exhausted = True
                    break
                if delay:
                    sleeper(delay)
            now = monotonic()
            if now >= deadline or requests >= REQUEST_CAP or response_bytes >= MAX_TOTAL_BYTES:
                exhausted = True
                break
            parameters = {"symbol": subject.symbol}
            if source == "analyst_estimates":
                parameters.update(period="annual", page="0", limit="10")
            requests += 1
            last_request = now
            try:
                response = fetch(
                    source=source, parameters=parameters,
                    timeout_seconds=min(30, max(1, math.ceil(deadline - now))),
                    max_bytes=min(MAX_RESPONSE_BYTES, MAX_TOTAL_BYTES - response_bytes),
                )
                body = response.body
                if not isinstance(body, bytes) or not body or len(body) > MAX_RESPONSE_BYTES:
                    raise ValidationError("Company response body is invalid")
                response_bytes += len(body)
                if response_bytes > MAX_TOTAL_BYTES or monotonic() >= deadline:
                    raise ResourceLimitError("Company response exceeds aggregate bounds")
                if response.redirected or response.status != 200:
                    raise StoreUnavailableError("Company response status is unavailable")
                if response.media_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise ValidationError("Company response media type is invalid")
                captured = utcnow()
                if captured.tzinfo is None or captured.utcoffset() is None:
                    raise ValidationError("Company capture time requires an offset")
                source_reference = retain_blob(state_root, body)
                parsed = parse_fmp_company_response(
                    source=source, subject=subject, body=body,
                    captured_at=captured.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    source_reference=source_reference,
                )
                receipt = publisher.publish(parsed)
                status = str(receipt.outcome)
                if status not in {"succeeded", "unchanged"}:
                    raise ConflictError("Company publication did not complete")
                results.append({
                    "symbol": subject.symbol, "source": source,
                    "outcome": "partial" if parsed.completeness == "partial" else status,
                    "publication_outcome": status, "completeness": parsed.completeness,
                    "warnings": list(parsed.warnings),
                    "written_count": receipt.written_count,
                })
            except Exception as error:
                results.append({
                    "symbol": subject.symbol, "source": source,
                    "outcome": "failed", "error": _error_code(error),
                })
        if exhausted:
            break
    failures = sum(r["outcome"] == "failed" for r in results)
    partial = sum(r["outcome"] == "partial" for r in results)
    incomplete = bool(failures or partial or exhausted or ambiguous or missing_issuers or unmatched)
    report = {
        "contract": "quant_data.company_market_refresh", "version": "1.0.0",
        "outcome": "partial" if incomplete else "succeeded", "exit_code": 75 if incomplete else 0,
        "planned_symbols": len(securities), "matched_symbols": len(subjects),
        "unmatched_symbols": list(unmatched), "ambiguous_symbols": list(ambiguous),
        "missing_issuer_count": len(missing_issuers), "rejected_discovery_rows": rejected,
        "discovery_reference": discovery_reference, "request_cap": REQUEST_CAP,
        "requests": requests, "response_bytes": response_bytes,
        "attempted_steps": len(results), "failed_steps": failures, "partial_steps": partial,
        "deadline_or_cap_exhausted": exhausted, "steps": results,
    }
    return report


def refresh_company_market_live() -> dict[str, object]:
    from ..company.fmp_market_data import FmpCompanyMarketDataPublisher
    if PROJECT_ROOT.resolve(strict=True) != PROJECT_ROOT:
        raise ValidationError("Company project binding is invalid")
    stores = StoreMap.from_mapping({r: PROJECT_ROOT / "data" / (r + ".sqlite")
                                  for r in ("market", "macro", "company", "news")})
    for role in ("market", "company"):
        path = stores.path(role)
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValidationError("Company store binding is invalid")
    registry = load_registry(PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
                             project_root=PROJECT_ROOT, environment={})
    roster, issuers = load_inputs(stores)
    publisher = FmpCompanyMarketDataPublisher(stores, registry)
    key = read_project_credential(project_root=PROJECT_ROOT, name="FMP_API_KEY", environment=os.environ)
    name = _credential_component(read_project_credential(
        project_root=PROJECT_ROOT, name="SEC_USER_AGENT_NAME", environment=os.environ), kind="name")
    email = _credential_component(read_project_credential(
        project_root=PROJECT_ROOT, name="SEC_USER_AGENT_EMAIL", environment=os.environ), kind="email")
    started = time.monotonic()
    discovery = StdlibSecMarketCompanyFactsTransport().request(
        url=SEC_TICKERS_URL, headers={"Accept": "application/json", "User-Agent": name + " " + email},
        timeout_seconds=30, max_bytes=MAX_DISCOVERY_BYTES,
    )
    discovery = validate_sec_response(discovery, max_bytes=MAX_DISCOVERY_BYTES)
    transport = _StdlibTransport()
    def fetch(*, source: str, parameters: Mapping[str, str], timeout_seconds: int, max_bytes: int):
        response = transport.request(
            url=ENDPOINTS[source], parameters={**parameters, "apikey": key},
            headers={"Accept": "application/json", "User-Agent": "QuantDataInfra/1.0"},
            timeout_seconds=timeout_seconds, max_bytes=max_bytes,
        )
        if key.encode() in response.body:
            raise ValidationError("Company provider returned credential-bearing content")
        return response
    return run_company_market_refresh(
        roster=roster, issuers=issuers, discovery_body=discovery.body, fetch=fetch,
        publisher=publisher, state_root=PROJECT_ROOT / STATE_RELATIVE, started=started,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(dumps_strict({"error": "invalid_arguments", "exit_code": 64}))
        return 64
    try:
        report = refresh_company_market_live()
    except Exception as error:
        print(dumps_strict({"contract": "quant_data.company_market_refresh",
                           "outcome": "failed", "error": _error_code(error), "exit_code": 75}))
        return 75
    print(dumps_strict(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
