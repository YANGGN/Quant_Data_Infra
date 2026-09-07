"""Retained FMP analyst observations, versioned on local capture; no HTTP."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import math
import sqlite3

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id
from .fmp_market_data import FmpCompanySubject
from .fmp_research import _capture, _date, _reference, _text, _issuer

EVIDENCE = "company.fmp.analyst_evidence"
OBSERVATIONS = "company.fmp.analyst_observations"
VERSION = "fmp_analyst_history.v1"
ENDPOINTS = frozenset(("analyst-estimates", "grades", "grades-historical",
    "grades-consensus", "price-target-consensus", "price-target-summary", "price-target", "earnings"))
CURRENT = frozenset(("grades-consensus", "price-target-consensus", "price-target-summary"))
MAX_BYTES = 1_048_576
MAX_ROWS = 5000


def _utc_capture(value):
    """Canonical UTC, fixed microseconds: lexical ordering is exact."""
    parsed = datetime.fromisoformat(_capture(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash(value):
    return hashlib.sha256(dumps_strict(value).encode()).hexdigest()


def _error(message):
    return ValidationError("FMP analyst history " + message)


def _numbers(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(child, bool):
                raise _error("boolean source values are unsupported")
            if isinstance(child, (float, int)) and (not math.isfinite(child)):
                raise _error("non-finite numeric value")
            if child is not None and ("count" in key.lower() or key.startswith("numberOfAnalyst")):
                if isinstance(child, bool) or not isinstance(child, int) or child < 0:
                    raise _error("analyst counts must be nonnegative integers")
            _numbers(child)
    elif isinstance(value, list):
        for child in value:
            _numbers(child)


@dataclass(frozen=True)
class AnalystRow:
    natural_identity: str
    semantic_hash: str
    temporal_kind: str
    target_period_end: str | None
    source_event_date: str | None
    source_time_raw: str | None
    event_precision: str | None
    currency: str
    estimate_basis: str
    payload_json: str
    source_row_index: int


@dataclass(frozen=True)
class AnalystResponse:
    endpoint: str
    request_period: str
    parameters_json: str
    subject: FmpCompanySubject
    captured_at: str
    source_reference: str
    content_sha256: str
    raw_body: bytes
    raw_row_count: int
    rows: tuple[AnalystRow, ...]
    warnings: tuple[str, ...]

    def scope(self):
        return {"endpoint": self.endpoint, "parameters": loads_strict(self.parameters_json),
                "subject": self.subject.provenance_mapping()}


def parse_analyst_response(body: bytes, *, endpoint: str, parameters: Mapping[str, str],
                          subject: FmpCompanySubject, captured_at: str,
                          source_reference: str) -> AnalystResponse:
    if endpoint not in ENDPOINTS or not isinstance(subject, FmpCompanySubject):
        raise _error("endpoint or subject is invalid")
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ResourceLimitError("Analyst body must be nonempty and within 1 MiB")
    captured_at = _utc_capture(captured_at)
    source_reference = _reference(source_reference)
    if not isinstance(parameters, Mapping):
        raise _error("parameters must be a mapping")
    params = dict(parameters)
    if set(params) - {"symbol", "limit", "period", "page"} or any(not isinstance(v, str) for v in params.values()):
        raise _error("request parameters are unsupported; credentials must be excluded")
    if params.get("symbol", "").upper() != subject.symbol.upper():
        raise _error("request symbol differs from subject")
    if "limit" in params and (not params["limit"].isdigit() or not 1 <= int(params["limit"]) <= MAX_ROWS):
        raise _error("limit is invalid")
    period = params.get("period", "all")
    if endpoint == "analyst-estimates":
        if period not in ("annual", "quarter") or "limit" not in params:
            raise _error("estimates require an explicit annual/quarter period and limit")
        if "page" in params and (not params["page"].isdigit() or not 0 <= int(params["page"]) < 10):
            raise _error("estimate page exceeds finite scope")
    elif "period" in params or "page" in params:
        raise _error("period and page apply only to estimates")
    raw = loads_strict(body, max_bytes=MAX_BYTES)
    if not isinstance(raw, list) or len(raw) > MAX_ROWS or any(not isinstance(r, dict) for r in raw):
        raise _error("response must be a bounded object array")
    if endpoint in CURRENT and len(raw) > 1:
        raise _error("current aggregate has multiple rows")
    rows, warnings, seen, ambiguous = [], set(), {}, set()
    for i, r in enumerate(raw, 1):
        if not isinstance(r.get("symbol"), str) or r["symbol"].upper() != subject.symbol.upper():
            raise _error("source row symbol differs from subject")
        _numbers(r)
        kind, target, event, rawtime, precision = "current_snapshot", None, None, None, None
        identity = {"issuer_id": subject.issuer_id, "instrument_id": subject.instrument_id,
                    "endpoint": endpoint, "period": period}
        if endpoint == "analyst-estimates":
            kind, target = "forecast_period", _date(r.get("date"), "estimate target date")
            identity["target_period_end"] = target
        elif endpoint not in CURRENT:
            kind = "dated_event"
            rawtime = _text(r.get("publishedDate" if endpoint == "price-target" else "date"), "source time", 64)
            if len(rawtime) == 10:
                event, precision = _date(rawtime, "source date"), "date"
            else:
                if endpoint != "price-target":
                    raise _error("dated source requires a date-only date")
                _capture(rawtime)
                event, precision = _date(rawtime[:10], "source date"), "datetime"
            identity["source_time_raw"] = rawtime
            if endpoint == "grades":
                for key in ("gradingCompany", "newGrade"):
                    _text(r.get(key), key, 512)
                if r.get("action") in (None, ""):
                    warnings.add("source_recommendation_action_missing")
                else:
                    _text(r["action"], "action", 512)
                identity["event_facts"] = r
            elif endpoint == "price-target":
                for key in ("newsURL",):
                    _text(r.get(key), key, 4096)
                for key in ("analystName", "analystCompany"):
                    if r.get(key) is not None and not isinstance(r[key], str):
                        raise _error(key+" must be a source string or null")
                    if not r.get(key):
                        warnings.add("source_analyst_identity_partial")
                identity["actor"] = {k: r.get(k) for k in ("newsURL", "analystName", "analystCompany")}
        currency = r.get("reportedCurrency", r.get("currency")) or "SOURCE_UNSPECIFIED"
        basis = next((r[k] for k in ("estimateBasis", "accountingBasis", "epsBasis") if r.get(k)), "SOURCE_UNSPECIFIED")
        currency, basis = _text(currency, "currency", 32), _text(basis, "basis", 128)
        if currency == "SOURCE_UNSPECIFIED":
            warnings.add("source_currency_unspecified")
        if endpoint == "analyst-estimates" and basis == "SOURCE_UNSPECIFIED":
            warnings.add("estimate_basis_unspecified")
        material = {k: v for k, v in r.items() if not (endpoint == "earnings" and k == "lastUpdated")}
        natural, semantic = _hash(identity), _hash({"identity": identity, "payload": material})
        if natural in seen:
            if seen[natural] != semantic:
                if endpoint not in ("earnings", "analyst-estimates"):
                    raise _error("conflicting duplicate logical rows")
                # Conflicting source values do not identify an authoritative value.
                # Preserve all raw rows, excluding the entire ambiguous date/period.
                ambiguous.add(natural)
                warnings.add("conflicting_earnings_dates_excluded" if endpoint == "earnings"
                             else "conflicting_estimate_periods_excluded")
                continue
            warnings.add("indistinguishable_source_rows_collapsed")
            continue
        seen[natural] = semantic
        rows.append(AnalystRow(natural, semantic, kind, target, event, rawtime, precision,
                               currency, basis, dumps_strict(r), i))
    if endpoint == "analyst-estimates" and len(raw) >= int(params["limit"]):
        warnings.add("page_limit_reached")
    return AnalystResponse(endpoint, period, dumps_strict(params), subject, captured_at,
        source_reference, hashlib.sha256(body).hexdigest(), body, len(raw),
        tuple(sorted((r for r in rows if r.natural_identity not in ambiguous),
                     key=lambda r: r.natural_identity)), tuple(sorted(warnings)))


def _insert(c, table, fields):
    # Table/column names are private constants, never caller-supplied identifiers.
    c.execute("INSERT INTO " + table + " (" + ",".join(fields) + ") VALUES (" +
              ",".join("?" for _ in fields) + ")", tuple(fields.values()))


class FmpAnalystPublisher:
    def __init__(self, store_map: StoreMap, registry: Registry):
        if not {EVIDENCE, OBSERVATIONS}.issubset({d.id for d in registry.datasets_for("company")}):
            raise _error("datasets are not registered")
        self.store_map = store_map
        self.coordinator = IngestionCoordinator(store_map, code_version=VERSION)

    def publish(self, prepared: AnalystResponse, *, request_id: str):
        if not isinstance(prepared, AnalystResponse):
            raise _error("prepared response is invalid")
        p = parse_analyst_response(prepared.raw_body, endpoint=prepared.endpoint,
            parameters=loads_strict(prepared.parameters_json), subject=prepared.subject,
            captured_at=prepared.captured_at, source_reference=prepared.source_reference)
        if p != prepared:
            raise _error("prepared bytes and normalized fields differ")
        _text(request_id, "request_id", 256)
        with acquire_write_session(self.store_map, (StoreRole.COMPANY,)) as locks:
            with quiet_immutable_read_connection(self.store_map, StoreRole.COMPANY) as c:
                _issuer(c, p.subject)
                latest = {r["natural_identity"]: dict(r) for r in c.execute(
                    "SELECT * FROM (SELECT v.*,ROW_NUMBER() OVER(PARTITION BY natural_identity "
                    "ORDER BY version_sequence DESC) q FROM company_fmp_analyst_observation_versions v "
                    "WHERE issuer_id=? AND endpoint=? AND request_period=?) WHERE q=1",
                    (p.subject.issuer_id, p.endpoint, p.request_period))}
            changed = [r for r in p.rows if r.natural_identity not in latest or
                       r.semantic_hash != latest[r.natural_identity]["semantic_hash"]]
            base = _hash({"version": VERSION, "subject": p.subject.semantic_mapping(),
                          "endpoint": p.endpoint, "period": p.request_period,
                          "rows": [(r.natural_identity, r.semantic_hash) for r in p.rows]})
            if not changed:
                return IngestionReceipt(outcome="unchanged", store="company", dataset_id=OBSERVATIONS,
                    semantic_identity=base, run_id=None, artifact_id=None, snapshot_id=None,
                    written_count=0, warnings=p.warnings)
            for row in p.rows:
                old = latest.get(row.natural_identity)
                if old is not None:
                    now = datetime.fromisoformat(p.captured_at.replace("Z", "+00:00"))
                    then = datetime.fromisoformat(old["captured_at"].replace("Z", "+00:00"))
                    if now < then or (row in changed and now == then):
                        raise ConflictError("Analyst corrections require increasing local capture")
            pid = _hash({"base": base, "predecessors": [
                (r.natural_identity, latest[r.natural_identity]["observation_version_id"])
                for r in p.rows if r.natural_identity in latest]})
            capture = stable_id("fmp_analyst_capture", pid)
            run = stable_id("fmp_analyst_run", request_id, pid)
            artifact = stable_id("fmp_analyst_artifact", pid, p.content_sha256)
            def writer(c: sqlite3.Connection, rid: str):
                _issuer(c, p.subject)
                common = dict(issuer_id=p.subject.issuer_id, cik=p.subject.cik,
                    symbol=p.subject.symbol, instrument_id=p.subject.instrument_id,
                    endpoint=p.endpoint, request_period=p.request_period)
                _insert(c, "company_fmp_analyst_captures", dict(capture_id=capture,
                    semantic_identity=pid, **common, request_scope_json=dumps_strict(p.scope()),
                    content_sha256=p.content_sha256, source_reference=p.source_reference,
                    captured_at=p.captured_at, raw_row_count=p.raw_row_count,
                    normalized_row_count=len(p.rows), warnings_json=dumps_strict(list(p.warnings)), run_id=rid))
                written = 1
                for row in p.rows:
                    old = latest.get(row.natural_identity)
                    if row in changed:
                        ident = stable_id("fmp_analyst_observation", pid, row.natural_identity)
                        _insert(c, "company_fmp_analyst_observation_versions", dict(
                            observation_version_id=ident, **common, temporal_kind=row.temporal_kind,
                            target_period_end=row.target_period_end, source_event_date=row.source_event_date,
                            source_time_raw=row.source_time_raw, event_precision=row.event_precision,
                            currency=row.currency, estimate_basis=row.estimate_basis, payload_json=row.payload_json,
                            natural_identity=row.natural_identity, semantic_hash=row.semantic_hash,
                            version_sequence=1 if old is None else old["version_sequence"]+1,
                            supersedes_observation_version_id=None if old is None else old["observation_version_id"],
                            captured_at=p.captured_at, source_capture_id=capture,
                            source_row_index=row.source_row_index, source_row_pointer="/"+str(row.source_row_index-1),
                            run_id=rid))
                        written += 1
                    else:
                        ident = old["observation_version_id"]
                    _insert(c, "company_fmp_analyst_capture_membership", dict(capture_id=capture,
                        observation_version_id=ident, source_row_index=row.source_row_index,
                        source_row_pointer="/"+str(row.source_row_index-1), run_id=rid))
                    written += 1
                art = ArtifactWrite(artifact, EVIDENCE, p.content_sha256, "application/json",
                    len(p.raw_body), p.source_reference, p.scope(), p.captured_at, "datetime", VERSION)
                snap = SnapshotWrite(stable_id("fmp_analyst_control", pid), EVIDENCE, pid, p.scope(),
                    "partial", len(p.rows), p.captured_at, "datetime", "validated", (artifact,), p.warnings)
                return WriteResult(written, (art,), snap, (), warnings=p.warnings)
            return self.coordinator.execute(role=StoreRole.COMPANY, dataset_id=OBSERVATIONS,
                output_dataset_ids=(EVIDENCE, OBSERVATIONS), semantic_identity=pid, run_id=run,
                command="company.fmp_analyst_history", scope=p.scope(), started_at=p.captured_at,
                completed_at=p.captured_at, fetched_count=p.raw_row_count, writer=writer, held_locks=locks)


def read_analyst_history(store_map: StoreMap, *, cik: str, endpoint: str | None = None,
                        as_of: str | None = None, include_versions: bool = False,
                        limit: int = 1000, offset: int = 0) -> dict:
    """Internal bounded reader; callers cannot supply SQL or a database path."""
    if not isinstance(cik, str) or len(cik) != 10 or not cik.isascii() or not cik.isdigit():
        raise _error("CIK must be ten digits")
    if endpoint is not None and endpoint not in ENDPOINTS:
        raise _error("endpoint is invalid")
    if type(include_versions) is not bool or type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 100000:
        raise _error("reader bounds are invalid")
    where, params = ["cik=?"], [cik]
    if endpoint:
        where.append("endpoint=?"); params.append(endpoint)
    if as_of is not None:
        where.append("captured_at<=?"); params.append(_utc_capture(as_of))
    query = ("SELECT * FROM (SELECT v.*, ROW_NUMBER() OVER(PARTITION BY natural_identity "
             "ORDER BY version_sequence DESC) q FROM company_fmp_analyst_observation_versions v WHERE "
             + " AND ".join(where) + ")")
    if not include_versions:
        query += " WHERE q=1"
    query += " ORDER BY COALESCE(target_period_end,source_event_date,'9999-12-31') DESC,natural_identity,version_sequence DESC LIMIT ? OFFSET ?"
    with quiet_immutable_read_connection(store_map, StoreRole.COMPANY) as c:
        records = [dict(r) for r in c.execute(query, (*params, limit+1, offset))]
    for record in records:
        record.pop("q")
    return {"records": records[:limit], "truncated": len(records)>limit,
            "next_offset": offset+limit if len(records)>limit else None,
            "availability": "local_capture_only", "as_of": as_of}
