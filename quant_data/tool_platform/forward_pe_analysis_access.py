"""Public adapter for the shared saved forward-P/E analysis."""
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re

from quant_data.company.forward_pe_analysis import read_analysis
from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ValidationError, ResourceLimitError
from .forward_pe_access import _record
from .results import QueryResult, research_envelope

TOOL = "company.get_forward_pe_analysis"
KIND = "forward_pe_analysis_v1"
MAX_RECORDS = 10000


def schema():
    return {"type": "object", "additionalProperties": False,
            "properties": {
                "ticker": {"type": "string", "minLength": 1, "maxLength": 20},
                "range": {"type": "string", "enum": ["1y", "5y", "all"]},
                "window_sessions": {"type": "integer", "minimum": 20, "maximum": 1260},
                "min_observations": {"type": "integer", "minimum": 2, "maximum": 1260},
                "winsor_tail_pct": {"type": "string", "enum": ["0", "1", "2.5", "5"]}},
            "required": ["ticker"]}


@dataclass(frozen=True)
class AnalysisArguments(Mapping):
    ticker: str
    range: str = "5y"
    window_sessions: int = 756
    min_observations: int = 252
    winsor_tail_pct: float = 2.5

    @property
    def limit(self):
        return MAX_RECORDS

    def __iter__(self):
        return iter(("ticker", "range", "window_sessions", "min_observations", "winsor_tail_pct", "limit"))

    def __len__(self):
        return 6

    def __getitem__(self, key):
        if key not in tuple(self):
            raise KeyError(key)
        return getattr(self, key)


def parse(value):
    allowed = {"ticker", "range", "window_sessions", "min_observations", "winsor_tail_pct"}
    if not isinstance(value, Mapping) or set(value) - allowed or "ticker" not in value:
        raise ValidationError("Use ticker and the documented rolling-analysis options only")
    ticker = value["ticker"]
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^]{0,19}", ticker):
        raise ValidationError("Use an exact uppercase retained ticker")
    period = value.get("range", "5y")
    window = value.get("window_sessions", 756)
    minimum = value.get("min_observations", min(window, 252) if isinstance(window, int) else 252)
    tail = value.get("winsor_tail_pct", "2.5")
    if period not in ("1y", "5y", "all"):
        raise ValidationError("Choose 1y, 5y or all history")
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (window, minimum)) or not 20 <= window <= 1260 or not 2 <= minimum <= window:
        raise ValidationError("Use a 20–1,260 session window and a minimum of 2 through that window")
    if not isinstance(tail, str) or tail not in ("0", "1", "2.5", "5"):
        raise ValidationError("Winsorization tail must be 0, 1, 2.5 or 5 percent")
    return AnalysisArguments(ticker, period, window, minimum, float(tail))


def invoke_analysis(arguments, context, registry):
    if not isinstance(arguments, AnalysisArguments):
        raise ValidationError("Forward P/E analysis requires its typed arguments")
    context.checkpoint()
    data = read_analysis(registry.project_root / "exports" / "forward-pe", arguments.ticker,
                         arguments.range, window_sessions=arguments.window_sessions,
                         min_observations=arguments.min_observations,
                         winsor_tail_pct=arguments.winsor_tail_pct, checkpoint=context.checkpoint)
    snapshot = hashlib.sha256(data["artifact"].encode()).hexdigest()
    refresh = data.get("refresh", {})
    safe_refresh = {k: refresh[k] for k in ("state", "completed_at", "last_successful_publication") if k in refresh}
    safe_refresh["schedule"] = {"enabled": bool(refresh.get("schedule", {}).get("enabled"))}
    summary = dict(ticker=arguments.ticker, range=arguments.range, snapshot_id=snapshot,
                   snapshot_cutoff=data["cutoff"], baseline_cutoff=data.get("baseline_cutoff"),
                   start_date=data.get("start_date"), end_date=data.get("end_date"),
                   latest_price_date=data.get("latest_price_date"), message=data.get("message"),
                   daily_count=len(data["days"]), is_point_in_time=False,
                   numeric_pe_count=sum(d[3] is not None for d in data["days"]),
                   z_score_count=sum(d["z_score"] is not None for d in data["statistics"]),
                   symbols_json=json.dumps(data["symbols"]), refresh_json=json.dumps(safe_refresh),
                   ticker_refresh_json=json.dumps(data.get("ticker_refresh", {})), **data["analysis"])
    records = [_record("forward_pe_analysis_summary", summary)]
    # Columnar chunks keep long histories below the shared record/byte limits.
    daily = [dict(statistics, close=day[1], forward_eps=day[2], status=day[4], window_id=day[5])
             for day, statistics in zip(data["days"], data["statistics"], strict=True)]
    columns = sorted(daily[0]) if daily else []
    for offset in range(0, len(daily), 500):
        batch = daily[offset:offset + 500]
        records.append(_record("forward_pe_analysis_chunk", dict(
            start_date=batch[0]["trade_date"], end_date=batch[-1]["trade_date"],
            observation_count=len(batch), columns_json=json.dumps(columns),
            rows_json=json.dumps([[row[key] for key in columns] for row in batch], allow_nan=False))))
    for identity, window in sorted(data["windows"].items()):
        records.append(_record("forward_pe_window", dict(window_id=identity,
                       details_json=json.dumps(window, sort_keys=True, allow_nan=False))))
    if len(records) > MAX_RECORDS:
        raise ResourceLimitError("The complete analysis exceeds the record bound; select a shorter history")
    context.budget.require(rows=len(records), operations=len(data["days"]) * 128)
    context.checkpoint()
    warnings = [WarningV1("reconstructed_estimates_not_point_in_time", "Trailing statistics use a reconstructed research P/E proxy, not historical point-in-time consensus."),
                WarningV1("signed_pe_descriptive_only", "Negative P/E remains in the sample. EPS sign changes and near-zero EPS limit valuation interpretation of z-scores."),
                WarningV1("eps_price_basis_unverified", "Currency and EPS share/split comparability remain unverified; preserve fiscal-window flags.")]
    if data.get("ticker_refresh", {}).get("outcome") not in (None, "current"):
        warnings.append(WarningV1("stale_or_unavailable_inputs", "Retained inputs need attention; inspect ticker freshness and window flags."))
    parameters = {k: arguments[k] for k in arguments if k != "limit"}
    parameters["winsor_tail_pct"] = Decimal(str(arguments.winsor_tail_pct))
    parameters.update(snapshot_id=snapshot, model_version=data["analysis"]["model_version"])
    return QueryResult(tool=TOOL, status="ok" if data["days"] else "not_established",
                       records=tuple(records), warnings=tuple(warnings),
                       truncation=TruncationV1(False, MAX_RECORDS, len(records), len(records), False),
                       research_contract=research_envelope(TOOL, {"parameters": [{"name": k, "value": v} for k, v in sorted(parameters.items())]}, (), (),
                           point_in_time_status="not_established", unsafe_reasons=("reconstructed_historical_consensus", "descriptive_signed_pe")))
