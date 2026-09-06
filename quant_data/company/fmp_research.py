"""Bounded retained FMP company research inputs; no provider transport."""
from __future__ import annotations
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
import hashlib, sqlite3
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Final, Literal
from ..contracts import IngestionReceipt, TruncationV1, WarningV1
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, acquire_write_session, quiet_immutable_read_connection, stable_id
from ..temporal import TemporalPrecision, TemporalValue
from ..tool_platform.context import ToolExecutionContext
from ..tool_platform.results import DiagnosticV1, QueryResult, fields_from_mapping, records_from_mappings
from .fmp_market_data import FmpCompanySubject
FMP_COMPANY_RESEARCH_COLLECTOR_ID: Final = 'fmp.company.research_inputs'
FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID: Final = 'company.fmp.research_evidence'
FMP_COMPANY_RESEARCH_INPUTS_DATASET_ID: Final = 'company.fmp.research_inputs'
FMP_RESEARCH_NORMALIZATION_VERSION: Final = 'fmp_company_research.v1'
ENDPOINTS: Final = frozenset(('income-statement', 'balance-sheet-statement', 'cash-flow-statement', 'financial-statement-full-as-reported', 'revenue-product-segmentation', 'analyst-estimates', 'earnings'))
_PERIODS = frozenset(('annual', 'quarter', 'all'))
_MAX_BYTES = 1024 * 1024
_MAX_ROWS = 100

def _fail(s: str) -> ValidationError:
    return ValidationError('FMP research inputs ' + s)

def _text(v: object, n: str, m: int=4096) -> str:
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > m:
        raise _fail(n + ' is invalid')
    return v.strip()

def _date(v: object, n: str) -> str:
    s = _text(v, n, 10)
    try:
        d = date.fromisoformat(s)
    except ValueError as e:
        raise _fail(n + ' is not an ISO date') from e
    if d.isoformat() != s:
        raise _fail(n + ' is not an ISO date')
    return s

def _optdate(v: object, n: str) -> str | None:
    return None if v in (None, '') else _date(v, n)

def _capture(v: object) -> str:
    if not isinstance(v, str):
        raise _fail('captured_at is invalid')
    p = TemporalValue.parse(v, pointer='/captured_at')
    if p.precision is not TemporalPrecision.DATETIME:
        raise _fail('captured_at must be an offset-aware datetime')
    return p.raw

def _reference(v: object) -> str:
    s = _text(v, 'source_reference')
    p = PurePosixPath(s)
    if p.is_absolute() or '..' in p.parts or '\\' in s or (p == PurePosixPath('.')):
        raise _fail('source_reference is not a private logical path')
    return s

def _frozen_json(v: object) -> object:
    if isinstance(v, Mapping):
        return MappingProxyType({str(k): _frozen_json(x) for k, x in v.items()})
    if isinstance(v, list):
        return tuple(_frozen_json(x) for x in v)
    return v

def _plain_json(v: object) -> object:
    if isinstance(v, Mapping):
        return {str(k): _plain_json(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return [_plain_json(x) for x in v]
    return v

def _explicit_estimate_basis(raw: Mapping[str, Any]) -> str | None:
    for key in ('estimateBasis', 'accountingBasis', 'epsBasis'):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None

def _estimate_basis_from_payload_json(payload_json: object) -> str:
    payload = loads_strict(str(payload_json))
    if isinstance(payload, Mapping):
        return _explicit_estimate_basis(payload) or 'SOURCE_UNSPECIFIED'
    return 'SOURCE_UNSPECIFIED'

def _params(v: Mapping[str, str], subject: FmpCompanySubject, endpoint: str) -> Mapping[str, str]:
    if not isinstance(v, Mapping):
        raise _fail('parameters are invalid')
    d = {}
    for k, x in v.items():
        k = _text(k, 'parameter name', 64).lower()
        if k in {'apikey', 'api_key', 'token', 'authorization'}:
            continue
        if k not in {'symbol', 'period', 'limit', 'page'}:
            raise _fail('parameter is unsupported')
        d[k] = _text(x, 'parameter ' + k, 256)
    if d.get('symbol', '').upper() != subject.symbol.upper() or 'limit' not in d:
        raise _fail('request scope is invalid')
    if 'page' in d and (endpoint != 'analyst-estimates' or d['page'] != '0'):
        raise _fail('request page is unsupported')
    if not d['limit'].isdigit() or not 1 <= int(d['limit']) <= 100:
        raise _fail('request limit is invalid')
    return MappingProxyType(dict(sorted(d.items())))

def _nested(row: Mapping[str, Any]) -> str | None:
    out = []

    def walk(x: object) -> None:
        if isinstance(x, Mapping):
            for k, v in x.items():
                if k.lower() in {'documentperiodenddate', 'document_period_end_date'} and v not in (None, ''):
                    out.append(_date(v, 'document period end date'))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(row)
    if len(set(out)) > 1:
        raise _fail('full-as-reported row has conflicting document period end dates')
    return out[0] if out else None

@dataclass(frozen=True, slots=True)
class ResearchRow:
    endpoint: str
    request_period: str
    symbol: str
    issuer_id: str
    cik: str
    fiscal_year: int | None
    fiscal_period: str | None
    period_end: str | None
    event_date: str | None
    currency: str
    accepted_date_raw: str | None
    payload: Mapping[str, Any]
    source_row_index: int
    source_row_pointer: str
    natural_identity: str
    semantic_hash: str

    def material(self) -> dict[str, object]:
        return {'endpoint': self.endpoint, 'request_period': self.request_period, 'issuer_id': self.issuer_id, 'cik': self.cik, 'fiscal_year': self.fiscal_year, 'fiscal_period': self.fiscal_period, 'period_end': self.period_end, 'event_date': self.event_date, 'currency': self.currency, 'accepted_date_raw': self.accepted_date_raw, 'payload': _plain_json(self.payload), 'natural_identity': self.natural_identity}

@dataclass(frozen=True, slots=True)
class ParsedResearchResponse:
    endpoint: str
    parameters: Mapping[str, str]
    subject: FmpCompanySubject
    captured_at: str
    source_reference: str
    content_sha256: str
    byte_count: int
    raw_row_count: int
    rows: tuple[ResearchRow, ...]
    semantic_identity: str
    completeness: Literal['complete', 'partial']
    warnings: tuple[str, ...]
    raw_body: bytes

    def request_scope(self) -> dict[str, object]:
        return {'endpoint': self.endpoint, 'parameters': dict(self.parameters), 'subject': self.subject.semantic_mapping()}

def _norm(raw: Mapping[str, Any], i: int, endpoint: str, period: str, subject: FmpCompanySubject) -> tuple[ResearchRow, tuple[str, ...]]:
    symbol = raw.get('symbol', subject.symbol)
    if not isinstance(symbol, str) or symbol.upper() != subject.symbol.upper():
        raise _fail('row %d symbol conflicts with subject' % i)
    fy = raw.get('calendarYear', raw.get('fiscalYear'))
    fy = None if fy in (None, '') else int(fy) if isinstance(fy, (int, str)) and (not isinstance(fy, bool)) and str(fy).isdigit() else (_ for _ in ()).throw(_fail('fiscal year is invalid'))
    fp = raw.get('period', raw.get('fiscalPeriod'))
    fp = None if fp in (None, '') else _text(fp, 'fiscal period', 32)
    top = _optdate(raw.get('date', raw.get('periodEndDate')), 'period end')
    doc = _nested(raw) if endpoint == 'financial-statement-full-as-reported' else None
    event = _optdate(raw.get('reportDate', raw.get('eventDate')), 'event date')
    warnings = []
    end = doc or top
    if doc and top and (doc != top):
        warnings.append('fmp_full_as_reported_document_period_end_overrides_top_date')
    if endpoint == 'earnings':
        end = event = _optdate(raw.get('date', raw.get('reportDate')), 'earnings date')
    if end is None and event is None:
        raise _fail('row %d lacks period end or event date' % i)
    cur = raw.get('reportedCurrency', raw.get('currency'))
    cur = 'SOURCE_UNSPECIFIED' if cur in (None, '') else _text(cur, 'currency', 32)
    if cur == 'SOURCE_UNSPECIFIED':
        warnings.append('fmp_source_currency_unspecified')
    if endpoint == 'analyst-estimates' and _explicit_estimate_basis(raw) is None:
        warnings.append('fmp_analyst_estimate_basis_unspecified')
    accepted = raw.get('acceptedDate')
    if accepted is not None and (not isinstance(accepted, str)):
        raise _fail('acceptedDate is invalid')
    accepted = accepted.strip() if isinstance(accepted, str) and accepted.strip() else None
    if accepted and (len(accepted) <= 10 or ('Z' not in accepted and '+' not in accepted[10:])):
        warnings.append('fmp_accepted_date_timezone_unspecified')
    ident = {'endpoint': endpoint, 'request_period': period, 'issuer_id': subject.issuer_id, 'fiscal_year': fy, 'fiscal_period': fp, 'period_end': end, 'event_date': event}
    natural = hashlib.sha256(dumps_strict(ident).encode()).hexdigest()
    semantic = hashlib.sha256(dumps_strict({**ident, 'currency': cur, 'accepted_date_raw': accepted, 'payload': _plain_json(_frozen_json(raw))}).encode()).hexdigest()
    return (ResearchRow(endpoint, period, subject.symbol, subject.issuer_id, subject.cik, fy, fp, end, event, cur, accepted, _frozen_json(raw), i, '/%d' % (i - 1), natural, semantic), tuple(warnings))

def _fingerprint(endpoint: str, params: Mapping[str, str], subject: FmpCompanySubject, rows: tuple[ResearchRow, ...]) -> str:
    material = sorted((r.material() for r in rows), key=lambda x: (str(x['natural_identity']), dumps_strict(x)))
    return hashlib.sha256(dumps_strict({'version': FMP_RESEARCH_NORMALIZATION_VERSION, 'endpoint': endpoint, 'parameters': dict(params), 'subject': subject.semantic_mapping(), 'rows': material}).encode()).hexdigest()

def parse_research_response(body: bytes, *, endpoint: str, parameters: Mapping[str, str], subject: FmpCompanySubject, captured_at: str, source_reference: str) -> ParsedResearchResponse:
    if endpoint not in ENDPOINTS or not isinstance(subject, FmpCompanySubject):
        raise _fail('endpoint or subject is invalid')
    if not isinstance(body, bytes) or not body:
        raise _fail('body must be nonempty bytes')
    if len(body) > _MAX_BYTES:
        raise ResourceLimitError('FMP research response exceeds byte bound')
    p = _params(parameters, subject, endpoint)
    period = p.get('period', 'all')
    limit = int(p['limit'])
    if period not in _PERIODS or (endpoint in {'income-statement', 'balance-sheet-statement', 'cash-flow-statement', 'financial-statement-full-as-reported'} and period == 'all'):
        raise _fail('request period is invalid')
    value = loads_strict(body, max_bytes=_MAX_BYTES)
    if not isinstance(value, list) or len(value) > _MAX_ROWS or any((not isinstance(x, Mapping) for x in value)):
        raise _fail('response must be a bounded JSON object array')
    allrows = []
    warnings = []
    seen = set()
    for i, row in enumerate(value, 1):
        n, w = _norm(row, i, endpoint, period, subject)
        if n.natural_identity in seen:
            raise _fail('response has duplicate logical rows')
        seen.add(n.natural_identity)
        allrows.append(n)
        warnings.extend(w)
    rows = sorted(allrows, key=lambda r: (r.period_end or r.event_date or '', -r.source_row_index), reverse=True)
    if endpoint != 'earnings':
        rows = rows[:limit]
    if endpoint == 'revenue-product-segmentation' and len(value) > len(rows):
        warnings.append('fmp_segment_response_retained_raw_all_normalized_requested_limit')
    if endpoint == 'analyst-estimates' and len(value) == limit:
        warnings.append('fmp_analyst_estimates_may_be_truncated_at_limit')
    rows = tuple(rows)
    semantic = _fingerprint(endpoint, p, subject, rows)
    return ParsedResearchResponse(endpoint, p, subject, _capture(captured_at), _reference(source_reference), hashlib.sha256(body).hexdigest(), len(body), len(value), rows, semantic, 'partial' if any(('truncated' in x or 'segment' in x for x in warnings)) else 'complete', tuple(sorted(set(warnings)),), body)

def _validate_prepared(prepared: ParsedResearchResponse) -> None:
    """Re-derive every persisted row field before a write is eligible to start."""
    if not isinstance(prepared, ParsedResearchResponse):
        raise _fail('prepared response is invalid')
    if prepared.endpoint not in ENDPOINTS or not isinstance(prepared.subject, FmpCompanySubject):
        raise _fail('prepared response is invalid')
    params = _params(prepared.parameters, prepared.subject, prepared.endpoint)
    period = params.get('period', 'all')
    if dict(params) != dict(prepared.parameters) or period not in _PERIODS:
        raise _fail('prepared response is invalid')
    if prepared.endpoint in {'income-statement', 'balance-sheet-statement', 'cash-flow-statement', 'financial-statement-full-as-reported'} and period == 'all':
        raise _fail('prepared response is invalid')
    if isinstance(prepared.raw_row_count, bool) or not isinstance(prepared.raw_row_count, int) or not 0 <= prepared.raw_row_count <= _MAX_ROWS:
        raise _fail('prepared response is invalid')
    if isinstance(prepared.byte_count, bool) or not isinstance(prepared.byte_count, int) or not 1 <= prepared.byte_count <= _MAX_BYTES:
        raise _fail('prepared response is invalid')
    if not isinstance(prepared.content_sha256, str) or len(prepared.content_sha256) != 64 or any(ch not in '0123456789abcdef' for ch in prepared.content_sha256):
        raise _fail('prepared response is invalid')
    _capture(prepared.captured_at)
    _reference(prepared.source_reference)
    if not isinstance(prepared.raw_body, bytes):
        raise _fail('prepared response is invalid')
    reparsed = parse_research_response(
        prepared.raw_body,
        endpoint=prepared.endpoint,
        parameters=params,
        subject=prepared.subject,
        captured_at=prepared.captured_at,
        source_reference=prepared.source_reference,
    )
    if (
        reparsed.content_sha256,
        reparsed.byte_count,
        reparsed.raw_row_count,
        reparsed.rows,
        reparsed.semantic_identity,
        reparsed.completeness,
        reparsed.warnings,
    ) != (
        prepared.content_sha256,
        prepared.byte_count,
        prepared.raw_row_count,
        prepared.rows,
        prepared.semantic_identity,
        prepared.completeness,
        prepared.warnings,
    ):
        raise _fail('prepared response is invalid')
    if not isinstance(prepared.rows, tuple) or len(prepared.rows) > prepared.raw_row_count:
        raise _fail('prepared response is invalid')
    normalized = []
    row_warnings = []
    natural = set()
    for row in prepared.rows:
        if not isinstance(row, ResearchRow):
            raise _fail('prepared response is invalid')
        expected, warnings = _norm(_plain_json(row.payload), row.source_row_index, prepared.endpoint, period, prepared.subject)
        if expected != row or expected.natural_identity in natural:
            raise _fail('prepared response is invalid')
        natural.add(expected.natural_identity)
        normalized.append(expected)
        row_warnings.extend(warnings)
    selected = sorted(normalized, key=lambda row: (row.period_end or row.event_date or '', -row.source_row_index), reverse=True)
    if prepared.endpoint != 'earnings':
        selected = selected[:int(params['limit'])]
    if tuple(selected) != prepared.rows:
        raise _fail('prepared response is invalid')
    if not isinstance(prepared.warnings, tuple) or tuple(sorted(set(prepared.warnings))) != prepared.warnings or any(not isinstance(warning, str) for warning in prepared.warnings):
        raise _fail('prepared response is invalid')
    if not set(row_warnings).issubset(prepared.warnings):
        raise _fail('prepared response is invalid')
    if prepared.endpoint == 'analyst-estimates' and prepared.raw_row_count == int(params['limit']) and 'fmp_analyst_estimates_may_be_truncated_at_limit' not in prepared.warnings:
        raise _fail('prepared response is invalid')
    if prepared.endpoint == 'revenue-product-segmentation' and prepared.raw_row_count > len(prepared.rows) and 'fmp_segment_response_retained_raw_all_normalized_requested_limit' not in prepared.warnings:
        raise _fail('prepared response is invalid')
    expected_completeness = 'partial' if any(('truncated' in warning or 'segment' in warning for warning in prepared.warnings)) else 'complete'
    if prepared.completeness != expected_completeness or _fingerprint(prepared.endpoint, params, prepared.subject, tuple(normalized)) != prepared.semantic_identity:
        raise _fail('prepared response is invalid')

def _issuer(c: sqlite3.Connection, s: FmpCompanySubject) -> None:
    r = c.execute('SELECT issuer_id,cik FROM company_issuers WHERE issuer_id=? OR cik=?', (s.issuer_id, s.cik)).fetchone()
    if r is None or str(r['issuer_id']) != s.issuer_id or str(r['cik']) != s.cik:
        raise ConflictError('FMP research subject issuer/CIK is not an existing exact match')

def _pubid(store: StoreMap, p: ParsedResearchResponse) -> str:
    """Resolve exact replay or a correction identity while the company lock is held."""
    wanted = {row.natural_identity: row.semantic_hash for row in p.rows}
    if not wanted:
        return p.semantic_identity
    with quiet_immutable_read_connection(store, StoreRole.COMPANY) as connection:
        latest_rows = connection.execute(
            'SELECT natural_identity,semantic_hash,research_row_id,publication_identity FROM ('
            'SELECT r.natural_identity,r.semantic_hash,r.research_row_id,'
            's.semantic_identity publication_identity,'
            'ROW_NUMBER() OVER(PARTITION BY r.natural_identity '
            'ORDER BY julianday(r.captured_at) DESC,r.research_row_id DESC) q '
            'FROM company_fmp_research_rows r '
            'JOIN company_fmp_research_snapshots s ON s.snapshot_id=r.snapshot_id '
            'WHERE r.issuer_id=?) WHERE q=1',
            (p.subject.issuer_id,),
        ).fetchall()
    current = {
        str(row['natural_identity']): {
            'semantic_hash': str(row['semantic_hash']),
            'research_row_id': str(row['research_row_id']),
            'publication_identity': str(row['publication_identity']),
        }
        for row in latest_rows
        if str(row['natural_identity']) in wanted
    }
    if {natural: value['semantic_hash'] for natural, value in current.items()} == wanted:
        identities = {value['publication_identity'] for value in current.values()}
        if len(identities) == 1:
            return next(iter(identities))
    if not current:
        return p.semantic_identity
    predecessors = tuple(
        {
            'natural_identity': natural,
            'semantic_hash': value['semantic_hash'],
            'research_row_id': value['research_row_id'],
            'publication_identity': value['publication_identity'],
        }
        for natural, value in sorted(current.items())
    )
    return hashlib.sha256(
        dumps_strict({'base': p.semantic_identity, 'predecessors': predecessors}).encode()
    ).hexdigest()

class FmpResearchPublisher:

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise _fail('publisher dependencies are invalid')
        if not {FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID, FMP_COMPANY_RESEARCH_INPUTS_DATASET_ID}.issubset({x.id for x in registry.datasets_for('company')}):
            raise _fail('research input datasets are not registered')
        self.store_map = store_map
        self.coordinator = IngestionCoordinator(store_map, code_version='fmp_company_research.1.0.0')

    def publish(self, prepared: ParsedResearchResponse, *, request_id: str, held_locks: HeldWriteLocks | None=None) -> IngestionReceipt:
        _validate_prepared(prepared)
        if not isinstance(request_id, str) or not request_id:
            raise _fail('request_id is invalid')
        if held_locks is None:
            with acquire_write_session(self.store_map, (StoreRole.COMPANY,)) as acquired_locks:
                return self.publish(prepared, request_id=request_id, held_locks=acquired_locks)
        held_locks._require_target(self.store_map, StoreRole.COMPANY)
        pid = _pubid(self.store_map, prepared)
        run = stable_id('fmp_research_run', request_id, pid)
        snap = stable_id('fmp_research_snapshot', pid)
        art = stable_id('fmp_research_artifact', pid, prepared.content_sha256)

        def writer(c: sqlite3.Connection, rid: str) -> WriteResult:
            _issuer(c, prepared.subject)
            c.execute('INSERT INTO company_fmp_research_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (snap, pid, prepared.subject.issuer_id, prepared.subject.cik, prepared.subject.symbol, prepared.content_sha256, prepared.captured_at, prepared.source_reference, prepared.endpoint, dumps_strict(prepared.request_scope()), prepared.raw_row_count, dumps_strict(list(prepared.warnings)), rid))
            written = 1
            for row in prepared.rows:
                prior = c.execute('SELECT research_row_id,captured_at FROM company_fmp_research_rows WHERE natural_identity=? ORDER BY julianday(captured_at) DESC,research_row_id DESC LIMIT 1', (row.natural_identity,)).fetchone()
                if prior is not None and c.execute('SELECT julianday(?)<=julianday(?)', (prepared.captured_at, prior['captured_at'])).fetchone()[0]:
                    raise ConflictError('FMP research revisions require increasing capture time')
                ident = stable_id('fmp_research_row', snap, row.natural_identity, row.semantic_hash)
                c.execute('INSERT INTO company_fmp_research_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (ident, snap, row.issuer_id, row.cik, row.symbol, row.endpoint, row.request_period, row.fiscal_year, row.fiscal_period, row.period_end, row.event_date, row.currency, row.accepted_date_raw, dumps_strict(_plain_json(row.payload)), row.source_row_index, row.source_row_pointer, row.natural_identity, row.semantic_hash, None if prior is None else str(prior['research_row_id']), prepared.captured_at, rid))
                written += 1
            a = ArtifactWrite(art, FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID, prepared.content_sha256, 'application/json', prepared.byte_count, prepared.source_reference, prepared.request_scope(), prepared.captured_at, 'datetime', FMP_RESEARCH_NORMALIZATION_VERSION)
            return WriteResult(written, (a,), SnapshotWrite(stable_id('fmp_research_control', pid), FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID, pid, prepared.request_scope(), prepared.completeness, len(prepared.rows), prepared.captured_at, 'datetime', 'validated', (art,), prepared.warnings), (), warnings=prepared.warnings)
        return self.coordinator.execute(role=StoreRole.COMPANY, dataset_id=FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID, output_dataset_ids=(FMP_COMPANY_RESEARCH_EVIDENCE_DATASET_ID, FMP_COMPANY_RESEARCH_INPUTS_DATASET_ID), semantic_identity=pid, run_id=run, command=FMP_COMPANY_RESEARCH_COLLECTOR_ID, scope=prepared.request_scope(), started_at=prepared.captured_at, completed_at=prepared.captured_at, fetched_count=prepared.raw_row_count, writer=writer, held_locks=held_locks)

@dataclass(frozen=True, slots=True)
class FmpResearchInputsQuery(Mapping[str, object]):
    cik: str
    endpoints: tuple[str, ...] = ()
    period: Literal['annual', 'quarter', 'all'] = 'all'
    mode: Literal['latest', 'as_of'] = 'latest'
    as_of: str | None = None
    limit: int = 30

    def __post_init__(self) -> None:
        cik = _text(self.cik, 'cik', 10)
        if len(cik) != 10 or not cik.isdigit() or not isinstance(self.period, str) or self.period not in _PERIODS:
            raise _fail('query is invalid')
        if not isinstance(self.mode, str) or self.mode not in {'latest', 'as_of'} or (self.mode == 'as_of') != (self.as_of is not None):
            raise _fail('query is invalid')
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or (not 1 <= self.limit <= 100):
            raise _fail('query is invalid')
        if isinstance(self.endpoints, (str, bytes)) or not isinstance(self.endpoints, (list, tuple)):
            raise _fail('query is invalid')
        endpoints = tuple(self.endpoints)
        if len(endpoints) > 7 or any((not isinstance(endpoint, str) or endpoint not in ENDPOINTS for endpoint in endpoints)) or (len(set(endpoints)) != len(endpoints)):
            raise _fail('query is invalid')
        if self.as_of is not None:
            _capture(self.as_of)
        object.__setattr__(self, 'cik', cik)
        object.__setattr__(self, 'endpoints', endpoints)

    def __getitem__(self, k: str) -> object:
        if k not in {'cik', 'endpoints', 'period', 'mode', 'as_of', 'limit'}:
            raise KeyError(k)
        return getattr(self, k)

    def __iter__(self) -> Iterator[str]:
        return iter(('cik', 'endpoints', 'period', 'mode', 'as_of', 'limit'))

    def __len__(self) -> int:
        return 6

def parse_research_inputs_arguments(a: object) -> FmpResearchInputsQuery:
    if not isinstance(a, Mapping) or set(a) - {'cik', 'endpoints', 'period', 'mode', 'as_of', 'limit'} or 'cik' not in a:
        raise _fail('arguments are invalid')
    e = a.get('endpoints', ())
    if isinstance(e, (str, bytes)) or not isinstance(e, (list, tuple)):
        raise _fail('endpoints must be array')
    return FmpResearchInputsQuery(a['cik'], tuple(e), a.get('period', 'all'), a.get('mode', 'latest'), a.get('as_of'), a.get('limit', 30))

def research_inputs_schema() -> dict[str, object]:
    return {'type': 'object', 'additionalProperties': False, 'required': ['cik'], 'properties': {'cik': {'type': 'string', 'minLength': 10, 'maxLength': 10}, 'endpoints': {'type': 'array', 'maxItems': 7, 'items': {'type': 'string', 'enum': sorted(ENDPOINTS)}}, 'period': {'type': 'string', 'enum': ['annual', 'quarter', 'all']}, 'mode': {'type': 'string', 'enum': ['latest', 'as_of']}, 'as_of': {'type': 'string', 'maxLength': 64}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}}

def read_research_inputs(context: ToolExecutionContext, query: FmpResearchInputsQuery) -> QueryResult:
    context.checkpoint()
    cutoff = query.as_of or _capture(context.clock.instant())
    where = ['r.cik=?', 'julianday(r.captured_at)<=julianday(?)']
    args = [query.cik, cutoff]
    if query.endpoints:
        where.append('r.endpoint IN (' + ','.join(('?' for _ in query.endpoints)) + ')')
        args += list(query.endpoints)
    if query.period != 'all':
        where.append('r.request_period=?')
        args.append(query.period)
    with quiet_immutable_read_connection(context.store_map, StoreRole.COMPANY) as c:
        rows = c.execute('SELECT * FROM (SELECT r.*,s.source_reference,s.content_sha256,s.warnings_json,ROW_NUMBER() OVER(PARTITION BY r.natural_identity ORDER BY julianday(r.captured_at) DESC,r.research_row_id DESC) q FROM company_fmp_research_rows r JOIN company_fmp_research_snapshots s ON s.snapshot_id=r.snapshot_id WHERE ' + ' AND '.join(where) + ') WHERE q=1 ORDER BY COALESCE(period_end,event_date) DESC,endpoint,julianday(captured_at) DESC,research_row_id LIMIT ?', (*args, query.limit + 1)).fetchall()
    more = len(rows) > query.limit
    rows = rows[:query.limit]
    context.budget.require(rows=len(rows), series=1, operations=len(rows))
    context.checkpoint()
    warnings = sorted({w for r in rows for w in loads_strict(str(r['warnings_json']))})
    records = records_from_mappings('fmp_research_input', tuple(({'endpoint': r['endpoint'], 'period': r['request_period'], 'period_end': r['period_end'], 'fiscal_year': r['fiscal_year'], 'fiscal_period': r['fiscal_period'], 'currency': r['reported_currency'], 'estimate_basis': _estimate_basis_from_payload_json(r['payload_json']) if r['endpoint'] == 'analyst-estimates' else None, 'payload_json': r['payload_json'], 'captured_at': r['captured_at'], 'source_reference': r['source_reference'], 'contenthash': r['content_sha256'], 'source_row_pointer': r['source_row_pointer'], 'temporal_basis': 'local_capture'} for r in rows)))
    return QueryResult(tool='company.get_research_inputs', status='ok' if records else 'not_established', records=records, warnings=tuple((WarningV1(w, w.replace('_', ' ')) for w in warnings)), diagnostics=(DiagnosticV1('fmp_research_input_selection', 'Returned retained FMP source rows using local capture time.', fields_from_mapping({'cutoff': cutoff, 'returned_count': len(records), 'temporal_basis': 'local_capture'})),), truncation=TruncationV1(more, query.limit, len(records), None if more else len(records), more))
__all__ = ['ENDPOINTS', 'FmpResearchPublisher', 'FmpResearchInputsQuery', 'ParsedResearchResponse', 'parse_research_response', 'parse_research_inputs_arguments', 'read_research_inputs', 'research_inputs_schema']
