"""Finite selected-universe dividends/splits; immutable evidence and one publisher."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib, math, time, re
from ..company.fmp_market_data import parse_fmp_company_response, FmpCompanyMarketDataPublisher, _domain_snapshot_id, _control_snapshot_id
from ..errors import ConflictError, ValidationError, ResourceLimitError
from ..ingestion import PublicationDeferred
from ..json_codec import dumps_strict, loads_strict
from ..market.collection_bindings import pin_binding
from ..stores import quiet_immutable_read_connection, acquire_write_session, StoreRole
from .collection_fmp import fmp_units, read_when_company_quiet
from .collection_targets import selected_fmp_subjects, validate_selection
from .collection_inventory import read_original
from .collection_plan import validate_unit
from .collection_parallel_prices import _acquire_batch
from .collection_queue import atomic, _read, _response, BoundedForwardClock
from .equibles_transcript_backfill import private_directory, job_lock
from .selected_company_collection import selection_from_dict, unit_from_dict

LIVE_ROOT = Path('/home/volatility/Python_Projects/Quant_Data_Infra')
BINDING = 'dividends_splits'
ENDPOINTS = ('dividends', 'splits')
MAX_REQUESTS = 4500
MAX_BYTES = 1024**3
MAX_SECONDS = 4 * 3600
CURRENT_BYTES = 512 * 1024**2
CURRENT_SECONDS = 2 * 3600
ACTIVATION = Path('data/.operations/collection/actions-selected/live-activation.json')
AUDIT = ACTIVATION.with_name('canonical-audit.json')
COMPLETION = ACTIVATION.with_name('history-completion.json')
SOURCE_GAPS = ACTIVATION.with_name('source-gaps.json')


def key(unit):
    return (unit.subject, unit.endpoint)


def population(selection):
    return {'binding': selection.binding.id, 'membership_snapshot_id': selection.membership_snapshot_id,
            'mapping_id': selection.mapping_id}


def retained_actions(stores, subjects, *, cutoff, source_root):
    """Preserve previously populated scopes; presence does not prove unlimited history."""
    from ..market.collection_universe import _utc
    ready = {s.symbol: s for s in subjects}
    retained = {}
    with quiet_immutable_read_connection(stores, 'company') as c:
        deadline = time.monotonic() + 30
        c.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        rows = c.execute("SELECT s.snapshot_id,s.captured_at,s.scope_json,a.content_sha256,a.source_reference "
                         "FROM company_action_snapshots s JOIN company_action_source_artifacts a "
                         "ON a.artifact_id=s.artifact_id WHERE s.provider='fmp'").fetchall()
    if len(rows) > 100000:
        raise ResourceLimitError('Action inventory exceeds its bound')
    total = 0
    for row in rows:
        if _utc(row['captured_at']) > _utc(cutoff):
            continue
        scope = loads_strict(row['scope_json'])
        subject = scope.get('subject', {}); symbol = subject.get('symbol'); endpoint = scope.get('source')
        if symbol not in ready or endpoint not in ENDPOINTS:
            continue
        target = ready[symbol]
        if (subject.get('issuer_id'), subject.get('cik'), subject.get('instrument_id')) != (target.issuer_id, target.cik, target.instrument_id):
            raise ConflictError('Retained action identity differs from the selected security')
        old = retained.get((symbol, endpoint))
        if old and _utc(old['captured_at']) >= _utc(row['captured_at']):
            continue
        body = read_original(source_root, row['source_reference'], row['content_sha256'], max_bytes=1024**2)
        if body is None:
            raise ConflictError('Retained action bytes are missing; completed scopes cannot be refetched silently')
        total += len(body)
        if total > 256 * 1024**2:
            raise ResourceLimitError('Retained action inventory exceeds its byte bound')
        retained[(symbol, endpoint)] = {'symbol': symbol, 'endpoint': endpoint,
            'capture_id': row['snapshot_id'], 'captured_at': row['captured_at'],
            'content_sha256': row['content_sha256'], 'source_reference': row['source_reference'],
            'raw_rows': scope.get('response', {}).get('raw_row_count'),
            'coverage': 'retained_population_history_not_reasserted'}
    return retained


def prepare(stores, binding, *, cutoff, historical, source_root):
    selection = pin_binding(stores, binding, cutoff=cutoff)
    subjects, _ = selected_fmp_subjects(stores, selection, cutoff=cutoff)
    ready = {s.symbol for s in subjects}
    existing = retained_actions(stores, subjects, cutoff=cutoff, source_root=source_root) if historical else {}
    work = fmp_units(stores, selection, mode='historical_backfill' if historical else 'incremental',
                     observation_window=cutoff, cutoff=cutoff)
    units = [u for u in work.units if key(u) not in existing]
    units.sort(key=lambda u: (u.endpoint, u.subject))
    first = {}
    for unit in units:
        first.setdefault(unit.endpoint, unit)
    pilot = list(first.values()); ids = {u.unit_id for u in pilot}
    ordered = pilot + [u for u in units if u.unit_id not in ids]
    if len(work.units) > MAX_REQUESTS:
        raise ResourceLimitError('Action population exceeds its finite request ceiling')
    return {'contract': 'quant_data.selected_actions_plan.v1', 'created_at': cutoff,
        'historical': historical, 'selection': asdict(selection), 'units': [asdict(u) for u in ordered],
        'retained': [existing[k] for k in sorted(existing)], 'planned_scopes': len(work.units),
        'ready_symbols': sorted(ready), 'identity_gaps': [
            {'symbol': s.source_symbol, 'reason': s.reason if s.status != 'resolved' else 'canonical_issuer_missing'}
            for s in selection.subjects if s.status != 'resolved' or s.provider_symbol not in ready],
        'pilot_units': len(pilot), 'maximum_requests': len(ordered), 'maximum_bytes': MAX_BYTES if historical else CURRENT_BYTES,
        'maximum_seconds': MAX_SECONDS if historical else CURRENT_SECONDS, 'retries': 0}


def validate_manifest(manifest, stores, *, require_active):
    if (manifest.get('contract') != 'quant_data.selected_actions_plan.v1'
        or type(manifest.get('historical')) is not bool or manifest.get('retries') != 0):
        raise ValidationError('Selected action manifest is invalid')
    for field, bound in [('maximum_requests', MAX_REQUESTS), ('maximum_bytes', MAX_BYTES if manifest['historical'] else CURRENT_BYTES),
                         ('maximum_seconds', MAX_SECONDS if manifest['historical'] else CURRENT_SECONDS)]:
        if type(manifest.get(field)) is not int or not 0 <= manifest[field] <= bound:
            raise ResourceLimitError('Selected action allocation exceeds its bound')
    selection = selection_from_dict(manifest['selection'])
    if require_active:
        validate_live_authority(stores, manifest, cutoff=manifest['created_at'])
    else:
        validate_selection(stores, selection, cutoff=manifest['created_at'], collection=BINDING)
    subjects, _ = selected_fmp_subjects(stores, selection, cutoff=manifest['created_at'])
    expected = fmp_units(stores, selection, mode='historical_backfill' if manifest['historical'] else 'incremental',
                         observation_window=manifest['created_at'], cutoff=manifest['created_at']).units
    expected_units = {u.unit_id: u for u in expected}; expected_keys = {key(u) for u in expected}
    units = tuple(unit_from_dict(v) for v in manifest['units'])
    skipped = [(v['symbol'], v['endpoint']) for v in manifest['retained']]
    if (len({u.unit_id for u in units}) != len(units) or any(expected_units.get(u.unit_id) != u for u in units)
        or len(set(skipped)) != len(skipped) or set(skipped) & {key(u) for u in units}
        or set(skipped) | {key(u) for u in units} != expected_keys
        or manifest['planned_scopes'] != len(expected) or manifest['maximum_requests'] != len(units)
        or manifest['ready_symbols'] != sorted(s.symbol for s in subjects)
        or (not manifest['historical'] and skipped)):
        raise ConflictError('Action manifest lost, repeated or changed a selected scope')
    pilot = manifest['pilot_units']
    if type(pilot) is not int or pilot != len({u.endpoint for u in units}) or {u.endpoint for u in units[:pilot]} != {u.endpoint for u in units}:
        raise ConflictError('Action endpoint pilot is incomplete')
    if skipped:
        with quiet_immutable_read_connection(stores, 'company') as c:
            for entry in manifest['retained']:
                row = c.execute('SELECT s.scope_json,a.content_sha256,a.source_reference FROM company_action_snapshots s '
                    'JOIN company_action_source_artifacts a ON a.artifact_id=s.artifact_id WHERE s.snapshot_id=?',
                    (entry['capture_id'],)).fetchone()
                scope = loads_strict(row['scope_json']) if row else {}
                if (not row or (scope.get('subject', {}).get('symbol'), scope.get('source')) != (entry['symbol'], entry['endpoint'])
                    or row['content_sha256'] != entry['content_sha256'] or row['source_reference'] != entry['source_reference']):
                    raise ConflictError('Retained action population is not supported by its canonical evidence')
    return selection, units, {s.symbol: s for s in subjects}


def acquire_actions_batch(**kwargs):
    units = kwargs['units']; modes = {u.mode for u in units}
    if not isinstance(units, tuple) or not units or len(modes) != 1 or not modes <= {'historical_backfill', 'incremental'}:
        raise ValidationError('Actions require one explicit bounded mode')
    for u in units:
        validate_unit(u)
        if u.collection != BINDING or u.provider != 'fmp' or u.endpoint not in ENDPOINTS or dict(u.parameters) != {'symbol': u.subject, 'limit': '1000'}:
            raise ValidationError('Action acquisition is outside its two fixed endpoints')
    return _acquire_batch(**kwargs, priority='backfill' if modes == {'historical_backfill'} else 'maintenance')


def publish_response(*, stores, publisher, selection, subject, unit, receipt, body, evidence_root, deadline,
                     monotonic=time.monotonic):
    if (receipt.get('unit_id') != unit.unit_id or receipt.get('request_id') != unit.request_id
        or hashlib.sha256(body).hexdigest() != receipt.get('content_sha256') or len(body) > unit.max_response_bytes):
        raise ConflictError('Action original response differs from its charged request')
    if receipt['status'] != 200:
        return {'outcome': 'provider_unavailable', 'status': receipt['status']}
    current, _ = read_when_company_quiet(lambda: selected_fmp_subjects(stores, selection, cutoff=receipt['captured_at']),
                                         stores=stores, deadline=deadline, monotonic=monotonic)
    if next((s for s in current if s.symbol == unit.subject), None) != subject:
        raise ConflictError('Action issuer identity was unavailable at capture time')
    reference = 'collection/fmp/blobs/' + receipt['content_sha256'] + '.json'
    try:
        parsed = parse_fmp_company_response(unit.endpoint, subject, body, receipt['captured_at'], reference)
    except (ValidationError, ResourceLimitError) as error:
        return {'outcome': 'source_rejected', 'error_type': type(error).__name__, 'reason': str(error)}
    if parsed.raw_row_count > unit.max_rows:
        raise ResourceLimitError('Action response exceeds its declared row bound')
    private_directory(evidence_root)
    atomic(evidence_root / (receipt['content_sha256'] + '.json'), body)
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise PublicationDeferred('Action publication deadline exhausted')
    # Parse and network work have finished. Reuse the existing publisher's held-lock interface.
    with acquire_write_session(stores, (StoreRole.COMPANY,), timeout_seconds=min(60, remaining)) as locks:
        result = publisher.publish(parsed, held_locks=locks, deadline=deadline, monotonic=monotonic)
    return {'outcome': result.outcome, 'capture_id': _domain_snapshot_id(parsed),
            'publication_snapshot_id': _control_snapshot_id(parsed), 'raw_rows': parsed.raw_row_count,
            'written_count': result.written_count, 'coverage': 'empty' if not parsed.raw_row_count else
            'partial_history' if parsed.completeness == 'partial' or parsed.raw_row_count >= 1000 else 'complete_request',
            'warnings': list(parsed.warnings)}


def run(*, root, stores, registry, manifest, gate, credential, evidence_root, require_active=True,
        acquire=acquire_actions_batch, hard_deadline=None, clock=lambda: datetime.now(timezone.utc),
        monotonic=time.monotonic, sleeper=time.sleep):
    started = monotonic(); deadline = started + manifest['maximum_seconds']
    if hard_deadline is not None:
        if type(hard_deadline) not in (int, float) or not math.isfinite(hard_deadline):
            raise ValidationError('Action hard deadline is invalid')
        deadline = min(deadline, hard_deadline)
    selection, units, subjects = read_when_company_quiet(lambda: validate_manifest(manifest, stores, require_active=require_active),
        stores=stores, deadline=deadline, monotonic=monotonic, sleeper=sleeper)
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError('Action operation root must be resolved')
    private_directory(root)
    with job_lock(root):
        if (root / 'started.json').exists():
            raise ConflictError('Action invocation already started; use its retained checkpoint, never refetch silently')
        atomic(root / 'manifest.json', manifest)
        now = BoundedForwardClock(clock, deadline=deadline, monotonic=monotonic, sleeper=sleeper)
        atomic(root / 'started.json', {'at': now().isoformat(), 'deadline_monotonic': deadline,
             'manifest_sha256': hashlib.sha256(dumps_strict(manifest, max_bytes=16*1024**2).encode()).hexdigest(), 'retries': 0})
        for name in ('batches', 'outcomes'):
            private_directory(root / name)
        pending = list(units); completed = {}; attempted_ids = set(); requests = received = batches = 0; reason = None
        publisher = FmpCompanyMarketDataPublisher(stores, registry)
        pilot_ids = {u.unit_id for u in units[:manifest['pilot_units']]}
        try:
            while pending:
                if monotonic() + 31 >= deadline:
                    reason = 'invocation_deadline'; break
                candidates = [u for u in pending if u.unit_id in pilot_ids - set(completed)] or pending
                batch = tuple(candidates[:min(64, manifest['maximum_requests'] - requests)])
                if not batch or manifest['maximum_bytes'] - received < max(u.max_response_bytes for u in batch):
                    reason = 'aggregate_budget'; break
                read_when_company_quiet(lambda: validate_live_authority(stores, manifest, cutoff=now().isoformat())
                    if require_active else validate_selection(stores, selection, cutoff=now().isoformat(), collection=BINDING),
                    stores=stores, deadline=deadline, monotonic=monotonic, sleeper=sleeper)
                path = root / 'batches' / str(batches).zfill(5)
                report = acquire(root=path, gate=gate, units=batch, credential=credential, deadline=deadline,
                    max_total_bytes=manifest['maximum_bytes'] - received, monotonic=monotonic, sleeper=sleeper, clock=now)
                requests += report['requests']; received += report['received_bytes']; batches += 1
                attempted = {p.stem for p in (path / 'attempts').glob('*.json')}; attempted_ids.update(attempted)
                if len(attempted) != report['requests']:
                    raise ConflictError('Action attempt accounting differs')
                for u in batch:
                    cached = _response(path, u.unit_id, u)
                    if cached is None:
                        continue
                    receipt, body = cached
                    result = publish_response(stores=stores, publisher=publisher, selection=selection, subject=subjects[u.subject],
                        unit=u, receipt=receipt, body=body, evidence_root=evidence_root, deadline=deadline, monotonic=monotonic)
                    atomic(root / 'outcomes' / (u.unit_id + '.json'), {'unit': asdict(u),
                        'response_sha256': receipt['content_sha256'], 'captured_at': receipt['captured_at'], 'result': result})
                    completed[u.unit_id] = result
                pending = [u for u in pending if u.unit_id not in completed]
                atomic(root / 'progress.json', {'at': now().isoformat(), 'requests': requests, 'received_bytes': received,
                    'processed_scopes': len(completed), 'pending_scopes': len(pending),
                    'outcomes': dict(Counter(r['outcome'] for r in completed.values()))}, replace=True)
                atomic(root / 'checkpoint.json', {'pending': [u.unit_id for u in pending], 'completed': list(completed),
                    'requests': requests, 'received_bytes': received, 'deadline_monotonic': deadline}, replace=True)
                if any(completed[i]['outcome'] in ('source_rejected', 'provider_unavailable') for i in pilot_ids & set(completed)):
                    reason = 'endpoint_preflight_failed'; break
                if report['uncertain_requests'] or attempted - set(completed):
                    reason = 'uncertain_requests'; break
                if report['outcome'] not in ('complete', 'batch_boundary', 'provider_failure'):
                    reason = report['outcome']; break
                if report['requests'] == 0:
                    reason = 'no_acquisition_progress'; break
        except Exception as error:
            reason = type(error).__name__
            atomic(root / 'failure.json', {'at': clock().isoformat(), 'error_type': type(error).__name__,
                'message': str(error), 'provider_retries': 0})
        # Reconcile physical attempts even when acquisition or publication raised.
        attempted_ids = {p.stem for p in (root / 'batches').glob('*/attempts/*.json')}
        if not attempted_ids <= {u.unit_id for u in units}:
            raise ConflictError('Action checkpoint contains an unrelated attempt')
        requests = len(attempted_ids)
        received = sum(_read(p)['byte_count'] for p in (root / 'batches').glob('*/responses/*.json'))
        failed = sum(r['outcome'] in ('source_rejected', 'provider_unavailable') for r in completed.values())
        partial = sum(r.get('coverage') == 'partial_history' for r in completed.values())
        successful = len(completed) - failed - partial
        unresolved = attempted_ids - set(completed)
        result = {'contract': 'quant_data.selected_actions_result.v1', 'at': clock().isoformat(),
            'outcome': 'stopped' if reason else 'processed_with_gaps' if failed or partial or manifest['identity_gaps'] else 'complete',
            'stop_reason': reason, 'requests': requests, 'received_bytes': received,
            'processed_scopes': len(completed), 'pending_scopes': len(units) - len(completed),
            'retained_scopes': len(manifest['retained']), 'ready_symbols': len(manifest['ready_symbols']),
            'identity_gaps': manifest['identity_gaps'], 'source_failures': failed,
            'counts': {'unit': 'steps', 'successful': successful, 'failed': failed, 'partial': partial + len(unresolved),
                'skipped': len(manifest['retained']), 'unattempted': sum(u.unit_id not in attempted_ids for u in units)},
            'retries': 0, 'unlimited_history_asserted': False,
            'exit_code': 75 if reason or failed or partial or manifest['identity_gaps'] else 0}
        atomic(root / 'result.json', result)
        return result


def require_activation(root, manifest):
    activation = _read(root / ACTIVATION)
    audit = _read(root / AUDIT)
    completion = _read(root / COMPLETION)
    if isinstance(activation, dict) and activation.get('contract') == 'quant_data.selected_actions_activation.v2':
        return require_gap_activation(root, manifest, activation, audit, completion)
    if (not isinstance(activation, dict) or activation.get('contract') != 'quant_data.selected_actions_activation.v1'
        or activation.get('population') != population(selection_from_dict(manifest['selection']))
        or activation.get('ready_symbols') != manifest['ready_symbols']
        or activation.get('endpoints') != list(ENDPOINTS) or activation.get('cadence') != 'Mon..Fri 19:00 America/New_York'
        or activation.get('backfill_audit_verified') is not True):
        raise ConflictError('Expanded actions require audited history for their exact population')
    if (not isinstance(audit, dict) or not isinstance(completion, dict)
        or activation.get('audit_sha256') != hashlib.sha256((root / AUDIT).read_bytes()).hexdigest()
        or activation.get('completion_sha256') != hashlib.sha256((root / COMPLETION).read_bytes()).hexdigest()
        or completion.get('contract') != 'quant_data.selected_actions_result.v1'
        or completion.get('stop_reason') is not None or completion.get('pending_scopes') != 0
        or completion.get('source_failures') != 0 or completion.get('counts', {}).get('partial') != 0
        or audit.get('population') != activation['population'] or audit.get('ready_symbols') != manifest['ready_symbols']
        or audit.get('verified_scopes') != manifest['planned_scopes'] or audit.get('foreign_key_violations') != 0
        or audit.get('original_bytes_verified') is not True):
        raise ConflictError('Expanded actions require matching completed and audited source evidence')


def require_gap_activation(root, manifest, activation, audit, completion):
    """Permit monitoring after an exact audited partition, without claiming rejected history."""
    ledger = _read(root / SOURCE_GAPS)
    if not isinstance(audit, dict) or not isinstance(completion, dict) or not isinstance(ledger, dict):
        raise ConflictError('Actions monitoring requires its complete evidence partition')
    for field, path in (('audit_sha256', AUDIT), ('completion_sha256', COMPLETION), ('source_gaps_sha256', SOURCE_GAPS)):
        if activation.get(field) != hashlib.sha256((root / path).read_bytes()).hexdigest():
            raise ConflictError('Actions monitoring evidence link differs')
    if (activation.get('population') != population(selection_from_dict(manifest['selection']))
        or activation.get('ready_symbols') != manifest['ready_symbols']
        or activation.get('endpoints') != list(ENDPOINTS) or activation.get('cadence') != 'Mon..Fri 19:00 America/New_York'
        or activation.get('history_status') != 'assessed_with_source_gaps' or activation.get('monitoring_enabled') is not True
        or audit.get('contract') != 'quant_data.selected_actions_audit.v2'
        or audit.get('population') != activation['population'] or audit.get('ready_symbols') != manifest['ready_symbols']
        or audit.get('original_bytes_verified') is not True or audit.get('source_gap_evidence_verified') is not True
        or audit.get('foreign_key_violations') != 0 or ledger.get('contract') != 'quant_data.selected_action_source_gaps.v1'
        or not isinstance(ledger.get('gaps'), list) or not ledger['gaps'] or not isinstance(audit.get('scopes'), list)):
        raise ConflictError('Actions monitoring has incomplete assessed history')
    gaps = ledger['gaps']; canonical = audit['scopes']
    expected = {(symbol, endpoint) for symbol in manifest['ready_symbols'] for endpoint in ENDPOINTS}
    canonical_keys = [(v.get('symbol'), v.get('endpoint')) for v in canonical]
    gap_keys = [(v.get('symbol'), v.get('endpoint')) for v in gaps]
    if (len(set(canonical_keys)) != len(canonical_keys) or len(set(gap_keys)) != len(gap_keys)
        or set(canonical_keys) & set(gap_keys) or set(canonical_keys) | set(gap_keys) != expected
        or audit.get('verified_scopes') != len(canonical) or audit.get('assessed_scopes') != len(expected)
        or len(expected) != manifest['planned_scopes'] or audit.get('source_rejected_scopes') != len(gaps)):
        raise ConflictError('Actions monitoring lost or duplicated a selected scope')
    for v in gaps:
        if (v.get('endpoint') != 'dividends' or v.get('status') != 200 or v.get('error_type') != 'ValidationError'
            or v.get('classification') != 'ambiguous_duplicate_dividend_event' or v.get('canonical_unpublished') is not True
            or type(v.get('raw_rows')) is not int or not 2 <= v['raw_rows'] < 1000
            or type(v.get('byte_count')) is not int or not 0 < v['byte_count'] <= 1024**2
            or not isinstance(v.get('reason'), str) or not re.fullmatch(
                r'FMP company market data /dividends/[0-9]+ duplicates an FMP dividend event', v['reason'])
            or not all(isinstance(v.get(k), str) and v[k] for k in ('unit_id', 'request_id', 'captured_at', 'content_sha256'))):
            raise ConflictError('Actions monitoring cannot admit this rejected source response')
    counts = completion.get('counts', {})
    if (completion.get('contract') != 'quant_data.selected_actions_result.v1' or completion.get('exit_code') != 75
        or completion.get('stop_reason') is not None or completion.get('pending_scopes') != 0
        or completion.get('source_failures') != len(gaps) or counts.get('failed') != len(gaps)
        or counts.get('partial') != 0 or counts.get('unattempted') != 0
        or completion.get('requests') != completion.get('processed_scopes')
        or completion.get('requests', -1) + completion.get('retained_scopes', -1) != len(expected)
        or counts.get('successful', -1) + len(gaps) != completion.get('requests')
        or len({v['unit_id'] for v in gaps}) != len(gaps) or len({v['request_id'] for v in gaps}) != len(gaps)):
        raise ConflictError('Actions monitoring has unresolved acquisition or publication work')


def validate_live_authority(stores, manifest, *, cutoff):
    """Only this clock-driven actions lane has receipt authority; shared modes remain unchanged."""
    from ..market.collection_bindings import load_bindings
    selection = selection_from_dict(manifest['selection'])
    trusted = load_bindings(LIVE_ROOT / 'config/collection_bindings.json')[BINDING]
    if selection.binding != trusted:
        raise ConflictError('Selected actions differ from their trusted host binding')
    require_activation(LIVE_ROOT, manifest)
    validate_selection(stores, selection, cutoff=cutoff, collection=BINDING)


def run_live(*, project_root, stores, registry):
    from ..credentials import read_project_credential
    from ..market.collection_bindings import load_bindings
    from .collection_provider_policy import host_allowance
    from zoneinfo import ZoneInfo
    if project_root != LIVE_ROOT:
        raise ConflictError('Selected actions require the fixed host root')
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    if local.weekday() > 4:
        raise ConflictError('Selected action refresh is weekday-only')
    deadline = time.monotonic() + CURRENT_SECONDS
    binding = load_bindings(project_root / 'config/collection_bindings.json')[BINDING]
    manifest = read_when_company_quiet(lambda: prepare(stores, binding, cutoff=now.isoformat(), historical=False,
        source_root=project_root / 'data/.operations'), stores=stores, deadline=deadline)
    require_activation(project_root, manifest)
    queue = project_root / 'data/.operations/collection/actions-selected/current' / local.date().isoformat()
    old = _read(queue / 'result.json')
    if old is not None:
        return old
    gate = host_allowance()
    if gate is None:
        raise ConflictError('Selected actions require the existing shared FMP allowance')
    credential = read_project_credential(project_root=project_root, name='FMP_API_KEY', environment={})
    return run(root=queue, stores=stores, registry=registry, manifest=manifest, gate=gate, credential=credential,
        evidence_root=project_root / 'data/.operations/collection/fmp/blobs', hard_deadline=deadline)
