"""Read the host-selected forward-P/E research publication; never open canonical stores."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
import re
import sqlite3

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ValidationError, StoreUnavailableError, ResourceLimitError
from quant_data.company.forward_pe_store import open_current, metadata, window_detail
from .results import QueryResult, RecordV1, DiagnosticV1, fields_from_mapping, research_envelope

TOOL = "company.get_forward_pe"
KIND = "forward_pe_v1"
MAX_DAYS = 3661
MAX_RECORDS = 2 * MAX_DAYS + 1


def schema():
    return {"type":"object","additionalProperties":False,
        "properties":{"ticker":{"type":"string","minLength":1,"maxLength":20},
            "start_date":{"type":"string","minLength":10,"maxLength":10},
            "end_date":{"type":"string","minLength":10,"maxLength":10}},
        "required":["ticker","start_date","end_date"]}


@dataclass(frozen=True)
class ForwardPeArgumentsV1(Mapping):
    ticker: str
    start_date: str
    end_date: str

    @property
    def limit(self):
        return 2 * ((date.fromisoformat(self.end_date)-date.fromisoformat(self.start_date)).days+1)+1

    def __iter__(self):
        return iter(("ticker","start_date","end_date","limit"))
    def __len__(self):
        return 4
    def __getitem__(self,key):
        if key not in tuple(self):raise KeyError(key)
        return getattr(self,key)


def parse(value):
    if not isinstance(value,Mapping) or set(value)!={"ticker","start_date","end_date"}:
        raise ValidationError("Forward P/E requires ticker, start_date and end_date only")
    if not isinstance(value["ticker"],str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}",value["ticker"]):
        raise ValidationError("Use an exact uppercase retained ticker")
    try:
        dates=[]
        for key in ("start_date","end_date"):
            raw=value[key]
            if not isinstance(raw,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",raw):
                raise ValueError()
            dates.append(date.fromisoformat(raw))
        days=(dates[1]-dates[0]).days
        if days<0:raise ValueError()
    except (ValueError,TypeError) as exc:
        raise ValidationError("Use valid inclusive dates with start_date no later than end_date") from exc
    if days>=MAX_DAYS:
        raise ResourceLimitError("Forward P/E accepts at most 3,661 calendar days per call")
    return ForwardPeArgumentsV1(**dict(value))


def _record(kind,fields):
    return RecordV1(kind,fields_from_mapping({key:Decimal(str(value)) if isinstance(value,float) else value for key,value in fields.items()}))


def invoke_forward_pe(arguments,context,registry):
    if not isinstance(arguments,ForwardPeArgumentsV1):
        raise ValidationError("Forward P/E requires its registered typed arguments")
    args=parse({k:arguments[k] for k in ("ticker","start_date","end_date")})
    context.checkpoint()
    root=registry.project_root/"exports"/"forward-pe"
    try:
        with open_current(root) as (name,db,base,meta):
            base_meta=metadata(base)
            snapshot=hashlib.sha256(name.encode()).hexdigest()
            baseline=hashlib.sha256(meta.get("baseline_artifact",name).encode()).hexdigest()
            subject=base.execute("SELECT instrument_id FROM source_inputs WHERE symbol=?",(args.ticker,)).fetchone()
            instrument=subject[0] if subject else None
            days={}
            sources=(base,) if db is base else (base,db)
            for source in sources:
                if instrument is None:break
                for row in source.execute("SELECT trade_date,close,forward_eps,forward_pe_proxy,status,window_id,price_version_id "
                        "FROM daily WHERE instrument_id=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date LIMIT ?",
                        (instrument,args.start_date,args.end_date,MAX_DAYS+1)):
                    days[row[0]]=row
            if len(days)>MAX_DAYS:raise ResourceLimitError("Saved series exceeds its declared date bound")
            member=None
            lineage={}
            if db is not base and instrument:
                member=db.execute("SELECT outcome,estimate_capture,earnings_capture,issue,checked_at FROM refresh_members WHERE instrument_id=?",(instrument,)).fetchone()
                lineage=dict((r[0],r[1:]) for r in db.execute(
                    "SELECT trade_date,calculated_at,estimate_cutoff,price_checked_at,observation_kind FROM daily_lineage "
                    "WHERE instrument_id=? AND trade_date BETWEEN ? AND ?",(instrument,args.start_date,args.end_date)))
            windows={}
            for row in days.values():
                identity=row[5]
                if identity and identity not in windows:
                    window=window_detail(db,base,identity)
                    if window is None:raise ValueError("Incomplete publication")
                    windows[identity]=window
            refresh=meta.get("refresh",{})
            details={"ticker":args.ticker,"instrument_id":instrument,"snapshot_id":snapshot,
                "baseline_snapshot_id":baseline,"snapshot_cutoff":meta["cutoff"],"baseline_cutoff":base_meta["cutoff"],
                "start_date":args.start_date,"end_date":args.end_date,"is_point_in_time":False,
                "status":"available" if days else "ticker_not_in_snapshot" if subject is None else "no_observations_in_range",
                "daily_count":len(days),"numeric_pe_count":sum(r[3] is not None for r in days.values()),
                "negative_pe_count":sum(r[3] is not None and r[3]<0 for r in days.values()),
                "refresh_state":refresh.get("state","manual_snapshot"),
                "last_successful_publication":refresh.get("last_successful_publication"),
                "input_freshness":member[0] if member else "not_recorded",
                "estimate_capture":member[1] if member else None,"earnings_capture":member[2] if member else None,
                "input_issue":member[3] if member else None,"inputs_checked_at":member[4] if member else None}
            records=[_record("forward_pe_summary",details)]
            for day,row in sorted(days.items()):
                calculated,estimate_at,price_at,kind=lineage.get(day,(
                    base_meta["cutoff"],base_meta.get("estimate_cutoff",base_meta["cutoff"]),
                    base_meta["cutoff"],"reconstructed_baseline"))
                records.append(_record("forward_pe_day",dict(trade_date=day,close=row[1],forward_eps=row[2],
                    forward_pe=row[3],status=row[4],missing_reason=row[4] if row[3] is None else None,
                    window_id=row[5],price_version_id=row[6],calculated_at=calculated,
                    estimate_cutoff=estimate_at,price_checked_at=price_at,observation_kind=kind)))
            for identity,window in sorted(windows.items()):
                fields={key:window.get(key) for key in ("announcement","effective_date","reported_period_end",
                    "forward_eps","status","ratio_allowed","mapping","announcement_version_id","calculation_cutoff",
                    "observation_kind","estimate_capture","earnings_capture")}
                fields.update(window_id=identity,components_json=json.dumps(window.get("components",[]),sort_keys=True,allow_nan=False),
                    flags_json=json.dumps(window.get("flags",[]),sort_keys=True,allow_nan=False))
                records.append(_record("forward_pe_window",fields))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        raise StoreUnavailableError("The saved forward P/E publication is unavailable") from exc
    context.budget.require(rows=len(records),operations=len(records)*10)
    context.checkpoint()
    warnings=[WarningV1("reconstructed_estimates_not_point_in_time",
        "Historical estimates are reconstructed research proxies; daily captures do not establish consensus known at market close."),
        WarningV1("eps_price_basis_unverified","Preserve window flags: currency and EPS share/split comparability can be unverified.")]
    if member and member[0]!="current":
        warnings.append(WarningV1("stale_or_unavailable_inputs","Retained input freshness needs attention; inspect the summary and window flags."))
    return QueryResult(tool=TOOL,status="ok" if days else "not_established",records=tuple(records),
        warnings=tuple(warnings),truncation=TruncationV1(False,args.limit,len(records),len(records),False),
        diagnostics=(DiagnosticV1("saved_forward_pe_selection",
            "Reads one complete date range from the same immutable baseline and overlay used by the dashboard.",
            fields_from_mapping(dict(snapshot_id=snapshot,canonical_reads=0,provider_requests=0))),),
        research_contract=research_envelope(TOOL,{"parameters":[
            {"name":k,"value":v} for k,v in sorted(dict(ticker=args.ticker,start_date=args.start_date,
                end_date=args.end_date,snapshot_id=snapshot).items())]},(),(),
            point_in_time_status="not_established",unsafe_reasons=("reconstructed_historical_consensus","not_market_close_point_in_time")))
