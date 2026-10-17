"""Host-selected, immutable forward-P/E research selection shared by consumers."""
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError

RANGES = {"1y": "1 year", "5y": "5 years", "all": "All history"}
MAX_DAYS = 30_000


def read_forward_pe(root: Path, symbol: str, period: str, *, history_sessions: int = 0) -> dict:
    """The host supplies the export directory; no browser input selects a file."""
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}", symbol) or period not in RANGES:
        raise ValidationError("Choose a valid ticker and date range")
    from .forward_pe_store import open_current, read_refresh_status
    try:
        with open_current(root) as (selected, db, base, metadata):
            symbols = [row[0] for row in base.execute("SELECT symbol FROM source_inputs ORDER BY symbol LIMIT 2501")]
            if len(symbols)>2500:
                raise ResourceLimitError("Forward P/E ticker limit exceeded")
            result=dict(symbol=symbol,range=period,symbols=symbols,artifact=selected,
                        cutoff=metadata["cutoff"],days=[],windows={},latest_price_date=None,
                        refresh=read_refresh_status(root),baseline_cutoff=metadata.get("baseline_cutoff"))
            if symbol not in symbols:
                result["message"]="This ticker has no saved forward P/E series."
                return result
            sources=[base] if base is db else [base,db]
            lasts=[c.execute("SELECT MAX(trade_date) FROM daily WHERE symbol=?",(symbol,)).fetchone()[0] for c in sources]
            if not any(lasts):
                result["message"]="No daily observations were saved for this ticker."
                return result
            end=date.fromisoformat(max(v for v in lasts if v))
            if period=="all":
                start="0001-01-01"
            else:
                years=1 if period=="1y" else 5
                try:
                    start=end.replace(year=end.year-years).isoformat()
                except ValueError:
                    start=end.replace(year=end.year-years,day=28).isoformat()
            days={}
            history={}
            result["start_date"]=start
            result["end_date"]=end.isoformat()
            for c in sources:
                for row in c.execute("SELECT trade_date,close,forward_eps,forward_pe_proxy,status,window_id FROM daily WHERE symbol=? AND trade_date>=? ORDER BY trade_date LIMIT ?",(symbol,start,MAX_DAYS+1)):
                    days[row[0]]=list(row)
            if history_sessions:
                for c in sources:
                    for row in c.execute("SELECT trade_date,close,forward_eps,forward_pe_proxy,status,window_id FROM daily WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC LIMIT ?",(symbol,start,history_sessions)):
                        history[row[0]]=list(row)
                result["history"]=[history[k] for k in sorted(history)[-history_sessions:]]
            if len(days)>MAX_DAYS:
                raise ResourceLimitError("Forward P/E date range exceeds the display limit")
            result["days"]=[days[k] for k in sorted(days)]
            prices=[c.execute("SELECT trade_date FROM daily WHERE symbol=? AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1",(symbol,)).fetchone() for c in sources]
            result["latest_price_date"]=max((p[0] for p in prices if p),default=None)
            identities=sorted({row[5] for row in result["days"] if row[5]})
            for c in sources:
                for offset in range(0,len(identities),500):
                    batch=identities[offset:offset+500]
                    for identity,detail in c.execute("SELECT window_id,details_json FROM windows WHERE window_id IN ("+",".join("?" for _ in batch)+")",batch):
                        result["windows"][identity]=json.loads(detail)
            if base is not db:
                member=db.execute("SELECT outcome,estimate_capture,earnings_capture,issue,checked_at FROM refresh_members JOIN source_inputs USING(instrument_id) WHERE symbol=?",(symbol,)).fetchone()
                if member:
                    result["ticker_refresh"]=dict(zip(("outcome","estimate_capture","earnings_capture","issue","checked_at"),member))
            return result
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        raise StoreUnavailableError("The forward P/E snapshot is unavailable. A completed research export must be selected by the host.") from exc
