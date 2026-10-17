"""Read-only presentation of derived publications and retained source checks.

Canonical capture timestamps describe changes, not successful unchanged checks.
This projection preserves original run receipts and never recalculates a ratio.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .company.forward_pe_store import NAME, open_artifact, metadata, selected_name, read_refresh_status
from .json_codec import dumps_strict, loads_strict
from .errors import QuantDataError
import sqlite3

SUCCESS = {'succeeded', 'unchanged', 'reused'}
FRESHNESS_ISSUES = {
    'Older or unchecked estimates inputs; source cutoffs retained.',
    'Older or unchecked earnings inputs; source cutoffs retained.',
    'Older or unchecked estimates, earnings inputs; source cutoffs retained.',
}


def _instant(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Missing timezone')
    return stamp


def _stamp(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Invalid evidence file')
    st = path.stat()
    if st.st_nlink != 1:
        raise ValueError('Invalid evidence links')
    return st.st_ino, st.st_size, st.st_mtime_ns


def _json(path, limit=64*1024*1024):
    before = _stamp(path)
    if before[1] > limit:
        raise ValueError('Evidence exceeds bound')
    value = loads_strict(path.read_bytes(), max_bytes=limit)
    if _stamp(path) != before:
        raise ValueError('Evidence changed while reading')
    return value


@lru_cache(maxsize=4)
def _collection_index(directory, fingerprint):
    """Validate a finished collection and index only its relevant successful checks."""
    directory = Path(directory)
    manifest = _json(directory/'manifest.json')
    checkpoint = _json(directory/'checkpoint.json')
    result = _json(directory/'result.json', 1024*1024)
    digest = hashlib.sha256(dumps_strict(manifest, max_bytes=64*1024*1024).encode()).hexdigest()
    if (checkpoint.get('manifest_sha256') != digest or result.get('pending_units') != 0
            or result.get('outcome') not in ('complete', 'processed', 'processed_with_gaps', 'succeeded')
            or checkpoint.get('pending_first') or checkpoint.get('pending_retry')):
        raise ValueError('Collection is not a validated terminal checkpoint')
    selections = {s['binding']['id']: s for s in manifest['selections']}
    if len(checkpoint['units']) > 80000:
        raise ValueError('Collection exceeds bound')
    members = {key: {s['provider_symbol']: s for s in value['subjects']
                    if s['status'] == 'resolved' and s.get('instrument_id') and s.get('cik')}
               for key, value in selections.items()}
    indexed = {}
    blocked = set()
    from .operations.selected_company_collection import unit_from_dict
    for key, raw in checkpoint['units'].items():
        endpoint = raw.get('endpoint')
        params = dict(raw.get('parameters', []))
        if endpoint != 'earnings' and not (endpoint == 'analyst-estimates' and params.get('period') == 'quarter'):
            continue
        unit = unit_from_dict(raw)
        selection = selections.get(unit.collection)
        member = members.get(unit.collection, {}).get(unit.subject)
        outcome = checkpoint['completed'].get(key, {})
        if selection is None or member is None:
            continue
        target = (member['instrument_id'], unit.subject, endpoint)
        if (unit.unit_id != key or unit.selection_sha256 != selection['scope_sha256']
                or unit.provider != 'fmp' or params.get('symbol') != unit.subject
                or outcome.get('outcome') not in SUCCESS):
            blocked.add(target)
            continue
        ordinal = checkpoint.get('retry_counts', {}).get(key, 0)
        proof = _json(directory/'outcomes'/(key+'-'+str(ordinal)+'.json'), 128*1024)
        if (proof.get('unit') != raw or proof.get('result') != outcome
                or not re.fullmatch('[0-9a-f]{64}', str(proof.get('response_sha256', '')))
                or not proof.get('captured_at') or type(outcome.get('raw_rows')) is not int
                or outcome['raw_rows'] <= 0):
            blocked.add(target)
            continue
        captured = _instant(proof['captured_at'])
        if not _instant(manifest['created_at']) <= captured <= _instant(result['at']):
            blocked.add(target)
            continue
        prior = indexed.get(target)
        changed_at = (prior or {}).get('changed_at')
        if outcome['outcome'] == 'succeeded' and type(outcome.get('written_count')) is int and outcome['written_count'] > 0:
            if changed_at is None or captured > _instant(changed_at):
                changed_at = proof['captured_at']
        if prior is None or captured > _instant(prior['at']):
            indexed[target] = {'at': proof['captured_at'], 'outcome': outcome['outcome']}
        if changed_at is not None:
            indexed[target]['changed_at'] = changed_at
    if tuple(_stamp(directory/p) for p in ('manifest.json','checkpoint.json','result.json')) != fingerprint:
        raise ValueError('Collection changed while indexing')
    return {k:v for k,v in indexed.items() if k not in blocked}, result['at']


def source_checks(project_root, cutoff):
    """Only consider completed retained work available at the requested cutoff."""
    at = _instant(cutoff)
    root = Path(project_root)/'data/.operations/collection/company-selected/current'
    day = at.astimezone(ZoneInfo('America/New_York')).date()
    for offset in range(7):
        directory = root/(day-timedelta(days=offset)).isoformat()
        if not directory.exists():
            continue
        if directory.is_symlink() or directory.resolve() != directory:
            return {}
        started = _json(directory/'started.json', 4096)
        if _instant(started['at']) > at:
            continue
        # A newer unfinished attempt is not silently replaced by an older success.
        if not (directory/'result.json').exists():
            return {}
        result = _json(directory/'result.json', 1024*1024)
        if _instant(result['at']) > at:
            return {}
        fingerprints = tuple(_stamp(directory/p) for p in ('manifest.json','checkpoint.json','result.json'))
        return _collection_index(str(directory), fingerprints)[0]
    return {}


@lru_cache(maxsize=8)
def _members(root, name, stamp):
    with open_artifact(Path(root), name, seconds=10) as db:
        meta = metadata(db)
        # Parse both receipt copies identically: binary floats otherwise disagree
        # with identical strict JSON decimals, including elapsed_seconds.
        raw = db.execute("SELECT value_json FROM metadata WHERE key='refresh'").fetchone()
        meta['refresh'] = loads_strict(raw[0], max_bytes=1024*1024) if raw else None
        rows = [dict(zip(('instrument_id','symbol','outcome','issue','estimate_capture','earnings_capture'), r))
                for r in db.execute('SELECT s.instrument_id,s.symbol,r.outcome,r.issue,r.estimate_capture,r.earnings_capture FROM source_inputs s JOIN refresh_members r USING(instrument_id) LIMIT 2501')]
    if len(rows) > 2500 or _stamp(Path(root)/name) != stamp:
        raise ValueError('Invalid derived member cohort')
    return meta, rows


def _input_state(row, checks, session, cutoff):
    states = []
    unchanged = False
    for endpoint, field in (('analyst-estimates','estimate_capture'), ('earnings','earnings_capture')):
        captured = _instant(row[field]) if row[field] else None
        check = checks.get((row['instrument_id'], row['symbol'], endpoint))
        checked = _instant(check['at']) if check and check.get('outcome') in SUCCESS else None
        if checked is not None and checked > cutoff:
            checked = None
        changed = _instant(check['changed_at']) if checked is not None and check.get('changed_at') else None
        if captured is None or captured > cutoff:
            states.append('unconfirmed')
        elif changed is not None and captured < changed <= checked:
            states.append('stale')
        elif checked is not None and checked.date().isoformat() >= session and (
                check['outcome'] == 'unchanged' or captured >= checked):
            states.append('current')
            unchanged |= check['outcome'] == 'unchanged' and captured < checked
        elif captured.date().isoformat() >= session:
            states.append('current')
        else:
            states.append('unconfirmed')
    state = 'stale' if 'stale' in states else 'unconfirmed' if 'unconfirmed' in states else 'current'
    return state, unchanged if state == 'current' else False


def _projection(root, name, *, detailed=True):
    receipt = _json(root/(name+'.receipt.json'), 1024*1024)
    counts = receipt['outcomes']
    if (receipt.get('artifact') != name or receipt.get('state') not in ('complete','complete_with_gaps')
            or any(type(v) is not int or v < 0 for v in counts.values())
            or set(counts)-{'current','stale_inputs','waiting_inputs','catchup_pending'}
            or sum(counts.values()) != receipt['symbols'] or not 1 <= receipt['symbols'] <= 2500):
        raise ValueError('Invalid publication receipt')
    view = dict(calculated=counts.get('current',0)+counts.get('stale_inputs',0),
                current_inputs=counts.get('current',0), freshness_warnings=counts.get('stale_inputs',0),
                waiting_inputs=counts.get('waiting_inputs',0), catchup_pending=counts.get('catchup_pending',0),
                stale_inputs=0, unconfirmed_inputs=counts.get('stale_inputs',0), input_warnings=0,
                unchanged_checks=0, recorded_freshness_warnings=counts.get('stale_inputs',0),
                evidence='publication_receipt', artifact=name, target_session=receipt['target_session'])
    tickers = {}
    if not detailed or not (root/name).exists():
        return receipt, view, tickers
    meta, rows = _members(str(root), name, _stamp(root/name))
    if meta.get('refresh') != receipt or Counter(r['outcome'] for r in rows) != Counter(counts):
        raise ValueError('Publication/member evidence differs')
    try:
        checks = source_checks(root.parent.parent, meta['cutoff'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, QuantDataError, sqlite3.Error):
        checks = {}
    for key in ('current_inputs', 'stale_inputs', 'unconfirmed_inputs', 'input_warnings'):
        view[key] = 0
    for row in rows:
        state = row['outcome']
        unchanged = False
        if state == 'current' or (state == 'stale_inputs' and row['issue'] in FRESHNESS_ISSUES):
            state, unchanged = _input_state(row, checks, receipt['target_session'], _instant(meta['cutoff']))
            view[{'current':'current_inputs', 'stale':'stale_inputs', 'unconfirmed':'unconfirmed_inputs'}[state]] += 1
            view['unchanged_checks'] += int(unchanged)
        elif state == 'stale_inputs':
            state = 'input_warning'
            view['input_warnings'] += 1
        note = ('Recent source checks confirmed the retained values are unchanged.' if unchanged else {
            'current': 'Inputs are current for the checked session.',
            'stale': 'A newer changed input was published before this calculation cutoff but is not reflected in its recorded input capture.',
            'unconfirmed': 'Recent source checks are not established. An older capture alone does not mean the information is stale.',
            'input_warning': row['issue'],
            'waiting_inputs': 'Calculation is waiting for readable inputs.',
            'catchup_pending': 'Calculation catch-up is pending.',
        }[state])
        tickers[row['symbol']] = {'state':state,'unchanged':unchanged,'note':note}
    view['freshness_warnings'] = view['stale_inputs'] + view['unconfirmed_inputs'] + view['input_warnings']
    if checks:
        view['evidence'] = 'validated_collection_receipts'
    return receipt, view, tickers


def _name(root, started_at):
    if started_at is None:
        return selected_name(root)
    at = _instant(started_at).astimezone(timezone.utc)
    prefix = at.strftime('%Y%m%dT%H%M')
    candidates = [p.name.removesuffix('.receipt.json') for p in root.glob(prefix+'*.sqlite.receipt.json')]
    candidates = [n for n in candidates if NAME.fullmatch(n) and abs((datetime.strptime(n[:22],'%Y%m%dT%H%M%S%fZ').replace(tzinfo=at.tzinfo)-at).total_seconds()) <= 1]
    return candidates[0] if len(candidates)==1 else None


def presentation(root, *, started_at=None, finished_at=None, cutoff=None, detailed=True):
    try:
        root = Path(root)
        if root.is_symlink():
            return {}
        root = root.resolve(strict=True)
        name = _name(root, started_at)
        if name is None:
            return {}
        receipt, view, _ = _projection(root, name, detailed=detailed)
        if finished_at is not None and _instant(receipt['completed_at']) > _instant(finished_at):
            return {}
        if cutoff is not None and _instant(receipt['completed_at']) > _instant(cutoff):
            return {}
        return view
    except (OSError, ValueError, KeyError, TypeError, AttributeError, QuantDataError, sqlite3.Error):
        return {}


def ticker_presentation(root, symbol, cutoff=None, *, artifact=None):
    try:
        root = Path(root)
        if root.is_symlink():
            return {}
        root = root.resolve(strict=True)
        name = selected_name(root) if artifact is None else artifact
        if not isinstance(name,str) or not NAME.fullmatch(name):
            return {}
        receipt, _, tickers = _projection(root, name)
        if cutoff is not None and _instant(receipt['completed_at']) > _instant(cutoff):
            return {}
        return tickers.get(symbol, {})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, QuantDataError, sqlite3.Error):
        return {}


def read_status(root):
    result = read_refresh_status(root)
    view = presentation(root)
    if view and view.get('artifact') == result.get('artifact'):
        result = dict(result, input_freshness=view)
    return result


def ticker_snapshot_presentation(root, symbol, snapshot_id):
    """UI-only annotation pinned to the public reader's immutable snapshot hash."""
    if not isinstance(snapshot_id, str) or not re.fullmatch('[0-9a-f]{64}', snapshot_id):
        return {}
    try:
        names = [p.name.removesuffix('.receipt.json') for p in Path(root).glob('*.sqlite.receipt.json')]
        names = [name for name in names if NAME.fullmatch(name) and hashlib.sha256(name.encode()).hexdigest() == snapshot_id]
        return ticker_presentation(root, symbol, artifact=names[0]) if len(names) == 1 else {}
    except (OSError, ValueError):
        return {}
