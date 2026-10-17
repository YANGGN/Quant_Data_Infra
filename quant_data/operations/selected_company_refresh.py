"""Fixed weekday refresh of the six selected company-data groups."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import sys,time
from ..credentials import read_project_credential
from ..errors import ConflictError
from ..json_codec import dumps_strict
from ..market.collection_bindings import load_bindings
from ..registry import load_registry
from ..stores import StoreMap
from .collection_provider_policy import host_allowance
from .collection_queue import _read
from .selected_company_collection import prepare, BINDINGS, MAX_CURRENT_SECONDS
from .collection_fmp import read_when_company_quiet
from .selected_company_best_effort import run_best_effort

ROOT=Path('/home/volatility/Python_Projects/Quant_Data_Infra')
ACTIVATION=Path('data/.operations/collection/company-selected/live-activation.json')
TIMER='quant-data-selected-company-refresh.timer'


def population(manifest):
    return {v['binding']['id']:{'membership_snapshot_id':v['membership_snapshot_id'],
        'mapping_id':v['mapping_id']} for v in manifest['selections']}


def require_activation(root,manifest):
    activation=_read(root/ACTIVATION)
    if (not isinstance(activation,dict) or activation.get('contract')!='quant_data.selected_company_live_activation.v1'
        or activation.get('population')!=population(manifest)
        or activation.get('cadence')!='Mon..Fri 20:00 America/New_York'
        or activation.get('backfill_audit_verified') is not True
        or activation.get('bindings')!=list(BINDINGS)):
        raise ConflictError('Selected company refresh requires its verified population activation')


def run_live():
    now=datetime.now(timezone.utc);local=now.astimezone(ZoneInfo('America/New_York'))
    if local.weekday()>4:
        return {'contract':'quant_data.selected_company_result.v1','outcome':'non_weekday','exit_code':0,
            'counts':{'unit':'steps','successful':0,'failed':0,'partial':0,'skipped':1,'unattempted':0}}
    stores=StoreMap.four_explicit(**{r:ROOT/'data'/(r+'.sqlite') for r in ('market','macro','company','news')})
    deadline=time.monotonic()+MAX_CURRENT_SECONDS
    manifest=read_when_company_quiet(lambda:prepare(stores,load_bindings(ROOT/'config/collection_bindings.json'),
        cutoff=now.isoformat(),historical=False),stores=stores,deadline=deadline,retry_sidecar_changes=True)
    require_activation(ROOT,manifest)
    from .selected_company_collection import validate_manifest
    read_when_company_quiet(lambda:validate_manifest(manifest,stores,require_active=True),stores=stores,deadline=deadline,retry_sidecar_changes=True)
    gate=host_allowance()
    if gate is None:raise ConflictError('Selected company refresh requires the shared FMP allowance')
    credential=read_project_credential(project_root=ROOT,name='FMP_API_KEY',environment={})
    return run_best_effort(root=ROOT/'data/.operations/collection/company-selected/current'/local.date().isoformat(),
        stores=stores,registry=load_registry(ROOT/'config/system_registry.json',project_root=ROOT,environment={}),manifest=manifest,gate=gate,credential=credential,
        evidence_root=ROOT/'data/.operations/collection/fmp/blobs',hard_deadline=deadline,request_cap=50000,byte_cap=4*1024**3)


def main(argv=None):
    if tuple(sys.argv[1:] if argv is None else argv):
        print(dumps_strict({'error':'invalid_arguments','exit_code':64}));return 64
    try:result=run_live()
    except Exception as error:
        print(dumps_strict({'contract':'quant_data.selected_company_result.v1','outcome':'failed',
            'error_type':type(error).__name__,'exit_code':75}));return 75
    from .fetch_run_summary import record_report
    record_report(TIMER,result)
    print(dumps_strict(result));return result['exit_code']


if __name__=='__main__':
    from .fetch_run_history import run_recorded_cli
    raise SystemExit(run_recorded_cli("quant-data-selected-company-refresh.timer",main,argv=sys.argv[1:]))
