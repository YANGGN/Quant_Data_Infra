"""Bounded, immutable reads of options.sqlite. No provider or legacy-store path."""
from collections import defaultdict
from dataclasses import replace
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
import zlib
from zoneinfo import ZoneInfo

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.json_codec import dumps_strict
from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.options.store import OptionsStore
from quant_data.options.universe import TIER1_ETFS
from quant_data.options.monitor.history_model import project_daily, _usable_iv, _iv_terms
from quant_data.stores import StoreWriteLock
from .forward_pe_access import _record
from .results import QueryResult, research_envelope
from .theta_contracts import TOOLS, KINDS, MAX_RECORDS, VERSION, ThetaArguments

NY = ZoneInfo("America/New_York")
DATASETS = ("options.theta.daily_research", "options.theta.selected_contracts", "options.theta.receipts")


def _instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("naive_capture_time")
    return result.astimezone(timezone.utc)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _record_value(kind, value):
    return _record(kind, {k + "_json" if isinstance(v, (dict, list, tuple)) else k:
                         _json(v) if isinstance(v, (dict, list, tuple)) else v for k, v in value.items()})


def _lineage(row):
    return {k: row[k] for k in ("symbol", "session_date", "capture_id", "captured_at", "semantic_sha256")}


class Reader:
    def __init__(self, connection, context, cutoff):
        self.c = connection
        self.context = context
        self.cutoff = cutoff
        self.decoded_bytes = 0
        self.steps = 0
        self.interrupted = None
        self.c.create_function("capture_before", 1, lambda stamp: int(_instant(stamp) <= cutoff), deterministic=True)
        self.quantum = min(1000, context.budget.max_operations)
        self.c.set_progress_handler(self._progress, self.quantum)

    def _progress(self):
        self.steps += self.quantum
        try:
            self.context.checkpoint()
            self.context.budget.require(operations=self.steps)
        except Exception as exc:
            self.interrupted = exc
            return 1
        return 0

    def decode(self, blob):
        self.context.checkpoint()
        if len(blob) > 1048576:
            raise ResourceLimitError("Saved options payload exceeds the compressed byte bound")
        inflater = zlib.decompressobj()
        raw = inflater.decompress(blob, 1048577)
        if len(raw) > 1048576 or not inflater.eof or inflater.unused_data:
            raise ResourceLimitError("Saved options payload exceeds the decoded byte bound")
        self.decoded_bytes += len(raw)
        if self.decoded_bytes > 128 * 1048576:
            raise ResourceLimitError("Options read exceeds the decoded byte budget")
        return json.loads(raw)

    def headers(self, start, end, symbols=TIER1_ETFS):
        # MAX selects the last locally published eligible revision, not the
        # current pointer, so a later correction cannot leak into a cutoff.
        end = min(end, self.cutoff.astimezone(NY).date().isoformat())
        marks = ",".join("?" for _ in symbols)
        rows = self.c.execute(
            "SELECT c.capture_id,c.symbol,c.session_date,c.semantic_sha256,c.captured_at,c.underlying_price "
            "FROM option_captures c JOIN (SELECT MAX(capture_id) AS cid FROM option_captures "
            f"WHERE symbol IN ({marks}) AND session_date BETWEEN ? AND ? AND capture_before(captured_at)=1 "
            "GROUP BY symbol,session_date) p ON c.capture_id=p.cid "
            "ORDER BY c.symbol,c.session_date LIMIT 50001", (*symbols, start, end)).fetchall()
        if len(rows) > 50000:
            raise ResourceLimitError("Options header read exceeds 50,000 captures")
        if any(r["symbol"] not in TIER1_ETFS for r in rows):
            raise ValueError("unexpected_option_root")
        return {(r["symbol"], r["session_date"]): dict(r) for r in rows}

    def inventory(self, symbols):
        marks = ",".join("?" for _ in symbols)
        rows = self.c.execute(
            "SELECT symbol,MIN(session_date),MAX(session_date),COUNT(*) FROM "
            "(SELECT symbol,session_date FROM option_captures "
            f"WHERE symbol IN ({marks}) AND session_date<=? AND capture_before(captured_at)=1 "
            "GROUP BY symbol,session_date) GROUP BY symbol",
            (*symbols, self.cutoff.astimezone(NY).date().isoformat())).fetchall()
        if sum(r[3] for r in rows) > 50000:
            raise ResourceLimitError("Options inventory exceeds 50,000 sessions")
        return {r[0]: dict(first=r[1], latest=r[2], count=r[3]) for r in rows}

    def metadata(self, row):
        return self.decode(self.c.execute("SELECT metadata FROM option_captures WHERE capture_id=?", (row["capture_id"],)).fetchone()[0])

    def parts(self, row, full=False):
        cid = row["capture_id"]
        where = "" if full else " AND population='full' AND (dimension='overall' OR (dimension='dte' AND bucket='0'))"
        summaries = [{"population": r[0], "dimension": r[1], "bucket": r[2], "right": r[3], "stats": self.decode(r[4])}
                     for r in self.c.execute("SELECT population,dimension,bucket,right,payload FROM option_daily_summaries WHERE capture_id=?" + where + " ORDER BY population,dimension,bucket,right LIMIT 103", (cid,))]
        if len(summaries) > 102:
            raise ValueError("summary_bound")
        where = "" if full else " AND reasons LIKE '%\"anchor:1\"%'"
        details = [self.decode(r[0]) for r in self.c.execute(
            "SELECT payload FROM option_details WHERE capture_id=?" + where + " ORDER BY contract_id LIMIT 301", (cid,))]
        if len(details) > 300:
            raise ValueError("selected_contract_bound")
        if not full and not any(d.get("oi") is not None for d in details):
            extra = self.c.execute("SELECT payload FROM option_details WHERE capture_id=? AND open_interest IS NOT NULL ORDER BY contract_id LIMIT 1", (cid,)).fetchone()
            if extra:
                detail = self.decode(extra[0])
                if detail["contract_id"] not in {d["contract_id"] for d in details}:
                    details.append(detail)
        return summaries, details

    def daily(self, row, parts=None):
        summaries, details = parts if parts is not None else self.parts(row)
        point = project_daily(row, summaries, details)
        cells = {(s["dimension"], s["bucket"], s["right"]): s["stats"]
                 for s in summaries if s["population"] == "full"}
        for right, prefix in (("C", "call"), ("P", "put")):
            stats = cells.get(("overall", "all", right), {})
            for key in ("volume_hhi", "oi_hhi", "volume_top5_share", "oi_top5_share",
                        "relative_spread_median", "relative_spread_p90", "valid_two_sided_quotes",
                        "contracts", "volume_missing", "oi_missing"):
                point[prefix + "_" + key] = stats.get(key)
        volume = _sum(point["call_volume"], point["put_volume"])
        zero = [cells.get(("dte", "0", side), {}) for side in ("C", "P")]
        zero_volume = _sum(*(s.get("volume") if s.get("volume_missing") == 0 else None for s in zero))
        point["total_volume"] = volume
        point["zero_dte_volume_share"] = zero_volume / volume if zero_volume is not None and volume else None
        point["put_call_oi_ratio"] = point["put_open_interest"] / point["call_open_interest"] if point["call_open_interest"] and point["put_open_interest"] is not None else None
        return point


def _sum(a, b):
    return a + b if a is not None and b is not None else None


def _gap_info(headers, symbols, start, end):
    sessions = sorted({day for _, day in headers if start <= day <= end})
    output = {}
    for symbol in symbols:
        present = {day for s, day in headers if s == symbol and start <= day <= end}
        output[symbol] = [day for day in sessions if day not in present]
    weekdays = []
    first = date.fromisoformat(start)
    for offset in range((date.fromisoformat(end)-first).days+1):
        day = first + timedelta(days=offset)
        if day.weekday() < 5 and day.isoformat() not in sessions:
            weekdays.append(day.isoformat())
    return sessions, output, weekdays


def _baseline(headers, symbol, session, window, reader):
    dates = sorted({day for _, day in headers if day < session})[-window:]
    selected = [headers[(symbol, day)] for day in dates if (symbol, day) in headers]
    points = [reader.daily(row) for row in selected]
    return dates, selected, points


def _volatility(reader, row, headers, args, parts):
    current = reader.daily(row, parts)
    dates, base, points = _baseline(headers, row["symbol"], row["session_date"], args["lookback_sessions"], reader)
    values = [p["atm_iv_30"] for p in points if p["atm_iv_30"] is not None]
    iv = current["atm_iv_30"]
    adequate = len(values) >= args["min_observations"]
    iv_range = max(values) - min(values) if values else 0
    result = dict(current,
        baseline_sessions=len(dates), baseline_captures=len(base), valid_iv_observations=len(values),
        lookback_sessions=args["lookback_sessions"], min_observations=args["min_observations"],
        baseline_start=dates[0] if dates else None, baseline_end=dates[-1] if dates else None,
        missing_baseline_sessions=[d for d in dates if (row["symbol"], d) not in headers],
        iv_rank_30=100 * (iv - min(values)) / iv_range if adequate and iv is not None and iv_range else None,
        iv_percentile_30=100 * sum(v <= iv for v in values) / len(values) if adequate and iv is not None else None,
        rank_reason="insufficient_baseline" if not adequate else "missing_current_iv" if iv is None else "constant_baseline" if not iv_range else None,
        term_slope_30_minus_7=iv-current["atm_iv_7"] if iv is not None and current["atm_iv_7"] is not None else None,
        term_slope_90_minus_30=current["atm_iv_90"]-iv if iv is not None and current["atm_iv_90"] is not None else None)
    _, details = parts
    terms, _, _ = _iv_terms(details, row["symbol"], date.fromisoformat(row["session_date"]),
                            Decimal(row["underlying_price"]), _instant(row["captured_at"]))
    interpolation = {}
    for target in (7, 30, 90):
        lower = [d for d in terms if d <= target]
        upper = [d for d in terms if d >= target]
        low, high = max(lower, default=None), min(upper, default=None)
        interpolation[str(target)] = dict(lower_dte=low, upper_dte=high,
            lower_iv=terms.get(low), upper_iv=terms.get(high),
            method="exact" if low == high == target else "total_variance_interpolation" if low is not None and high is not None else "missing_bracket")
    result["interpolation"] = interpolation
    result["atm_anchor_contracts"] = [dict(contract_id=d["contract_id"], expiration=d["expiration"],
        strike=d["strike"], right=d["right"], moneyness=float(Decimal(d["strike"])/Decimal(row["underlying_price"])))
        for d in details if "anchor:1" in d.get("reasons", [])]
    # Same-expiry 95%-put minus 105%-call; explicitly a moneyness proxy.
    pairs = defaultdict(dict)
    spot = Decimal(row["underlying_price"])
    session = date.fromisoformat(row["session_date"])
    for detail in details:
        right = detail["right"]
        target = Decimal(".95") if right == "P" else Decimal("1.05")
        money = Decimal(detail["strike"]) / spot
        dte = (date.fromisoformat(detail["expiration"]) - session).days
        if abs(money-target) > Decimal(".025") or not 1 <= dte <= 60:
            continue
        # Reuse the saved quote/underlying quality gate without requiring ATM.
        candidate = dict(detail, reasons=["anchor:1"])
        iv_value = _usable_iv(candidate, row["symbol"], session, spot, _instant(row["captured_at"]))
        if iv_value is None:
            continue
        key = (abs(money-target), Decimal(detail["strike"]))
        if right not in pairs[dte] or key < pairs[dte][right][0]:
            pairs[dte][right] = (key, iv_value, detail["contract_id"], float(money))
    eligible = [d for d, sides in pairs.items() if set(sides) == {"C", "P"}]
    chosen = min(eligible, key=lambda d: (abs(d-30), d)) if eligible else None
    result["skew_method"] = "same_expiry_95pct_put_minus_105pct_call_max_moneyness_distance_0.025_dte_1_to_60"
    result["skew_dte"] = chosen
    result["skew_95p_minus_105c"] = pairs[chosen]["P"][1] - pairs[chosen]["C"][1] if chosen else None
    result["skew_anchors"] = {r: dict(contract_id=v[2], iv=v[1], moneyness=v[3]) for r, v in pairs[chosen].items()} if chosen else {}
    result["baseline_lineage"] = [_lineage(r) for r in base]
    return result


def invoke(name, arguments, context, registry):
    if not isinstance(arguments, ThetaArguments) or arguments.kind != KINDS[name]:
        raise ValidationError("Theta tools require their registered typed arguments")
    args = dict(arguments)
    now = _instant(context.clock.instant())
    cutoff = _instant(args["as_of"]) if "as_of" in args else now
    if cutoff > now:
        raise ValidationError("as_of cannot be later than the host clock")
    context.checkpoint()
    reader = None
    try:
        store = OptionsStore(registry.project_root)
        with StoreWriteLock(store.path, timeout_seconds=1):
            with store.read() as connection:
                store._check(connection, require_latest=True)
                reader = Reader(connection, context, cutoff)
                records, count, high = _read(name, args, reader, store)
    except sqlite3.OperationalError as exc:
        if reader is not None and reader.interrupted is not None:
            raise reader.interrupted from exc
        raise StoreUnavailableError("The fixed Theta options store could not be read") from exc
    except (OSError, ValueError, OverflowError, KeyError, zlib.error) as exc:
        raise StoreUnavailableError("The fixed Theta options store is missing, busy, or invalid; no fallback is used") from exc
    source = dict(lineage_contract="options.theta.source", lineage_version="1.0.0",
        provider="thetadata", store_role="options", source_store="data/options.sqlite",
        datasets=[dict(id=d, version="1.0.0") for d in DATASETS],
        capture_cutoff=cutoff.isoformat(), source_high_water=high, derivation_version=VERSION,
        scope="tier1_etf_daily_compact", point_in_time_status="not_established",
        availability_basis="local_capture_time_only", gap_basis="sessions_observed_in_saved_etf_universe",
        selection_policy="latest_published_revision_at_or_before_capture_cutoff",
        is_full_chain=False, provider_requests=0)
    records.insert(0, _record_value("theta_source", source))
    if len(records) > MAX_RECORDS:
        raise ResourceLimitError("Options result exceeds the record cap")
    context.budget.require(rows=len(records), operations=reader.steps)
    return QueryResult(tool=name, status="ok" if count else "not_established", records=tuple(records),
        warnings=(WarningV1("theta_compact_history", "Theta daily history uses compact retained contracts and later capture times; historical point-in-time availability and verified deliverables are not established."),),
        truncation=TruncationV1(False, MAX_RECORDS, len(records), len(records), False),
        research_contract=_research(name, args, records, cutoff.isoformat()))


def _research(name, args, records, cutoff):
    parameters = {"query_json": _json(args), "capture_cutoff": cutoff, "derivation_version": VERSION,
                  "source_records_sha256": hashlib.sha256(dumps_strict(
                      [record.to_primitive() for record in records]).encode()).hexdigest()}
    material = dict(args, as_of=cutoff, parameters=[{"name": k, "value": v} for k, v in sorted(parameters.items())])
    envelope = research_envelope(name, material, (), (),
        unsafe_reasons=("historical_source_release_time_unverified", "selected_contract_iv_proxy"))
    temporal = replace(envelope.contract.temporal, availability_basis="local_capture_time_only",
                       date_only_policy="calendar_date_inclusive")
    contract = replace(envelope.contract, temporal=temporal, model_id="quant_data.theta.daily_research", model_version="1.0.0",
        vintage_policy="last_published_eligible_capture", availability_policy="local_capture_time_only",
        execution_policy="host_fixed_optional_options_immutable_read",
        sample={"output_record_count": len(records)})
    identity = contract.to_primitive()
    identity.pop("analysis_id")
    contract = replace(contract, analysis_id=hashlib.sha256(dumps_strict(identity).encode()).hexdigest())
    return replace(envelope, contract=contract)


def _read(name, args, reader, store):
    c = reader.c
    high = c.execute("SELECT COALESCE(MAX(capture_id),0) FROM option_captures WHERE capture_before(captured_at)=1").fetchone()[0]
    if name == TOOLS[0]:
        inventory = reader.inventory(args["symbols"])
        headers = reader.headers(args["start_date"], args["end_date"])
        sessions, gaps, weekdays = _gap_info(headers, args["symbols"], args["start_date"], args["end_date"])
        records = []
        for symbol in args["symbols"]:
            item = inventory.get(symbol)
            latest = reader.headers(item["latest"], item["latest"], (symbol,)).get((symbol, item["latest"])) if item else None
            point = reader.daily(latest) if latest else {}
            records.append(_record_value("theta_coverage", dict(symbol=symbol,
                status="available" if item else "no_saved_captures", first_session=item["first"] if item else None,
                latest_session=latest["session_date"] if latest else None, total_sessions=item["count"] if item else 0,
                requested_start=args["start_date"], requested_end=args["end_date"],
                observed_universe_sessions=len(sessions), missing_observed_sessions=gaps[symbol],
                unobserved_weekdays_calendar_unverified=weekdays,
                present_sessions=sum((symbol, d) in headers for d in sessions),
                freshness_calendar_days=(reader.cutoff.astimezone(NY).date()-date.fromisoformat(latest["session_date"])).days if latest else None,
                latest_capture=_lineage(latest) if latest else None,
                latest_quality=point,
                latest_capture_coverage=reader.metadata(latest).get("coverage") if latest else None,
                collection_status="not_queried_read_only_data_evidence")))
        return records, len(inventory), high
    if name == TOOLS[1]:
        headers = reader.headers(args["start_date"], args["end_date"])
        _, gaps, weekdays = _gap_info(headers, [args["symbol"]], args["start_date"], args["end_date"])
        rows = [r for (s, _), r in headers.items() if s == args["symbol"]]
        records = [_record_value("theta_history_coverage", dict(symbol=args["symbol"], start_date=args["start_date"],
            end_date=args["end_date"], capture_count=len(rows), missing_observed_sessions=gaps[args["symbol"]],
            unobserved_weekdays_calendar_unverified=weekdays))]
        records += [_record_value("theta_daily", reader.daily(row)) for row in rows]
        return records, len(rows), high
    session = args["session"]
    lookback = args.get("lookback_sessions", 0)
    start = date.fromordinal(max(1, date.fromisoformat(session).toordinal()-lookback*3-20)).isoformat() if lookback else session
    headers = reader.headers(start, session)
    if name == TOOLS[4]:
        records = []
        for symbol in args["symbols"]:
            row = headers.get((symbol, session))
            if row is None:
                records.append(dict(symbol=symbol, session=session, status="missing_session", rank=None))
                continue
            current = reader.daily(row)
            dates, base, points = _baseline(headers, symbol, session, lookback, reader)
            volumes = [p["total_volume"] for p in points if p["total_volume"] is not None]
            prior = next((p for p in points if dates and p["session"] == dates[-1]), {})
            mean = sum(volumes)/len(volumes) if len(volumes) >= args["min_observations"] else None
            current.update(status="available", rank=None, baseline_sessions=len(dates),
                baseline_captures=len(base), valid_volume_observations=len(volumes),
                lookback_sessions=lookback, min_observations=args["min_observations"],
                relative_volume=current["total_volume"]/mean if mean and current["total_volume"] is not None else None,
                baseline_mean_volume=mean, previous_session=dates[-1] if dates else None,
                put_call_change=_difference(current.get("put_call_ratio"), prior.get("put_call_ratio")),
                atm_iv_30_change=_difference(current.get("atm_iv_30"), prior.get("atm_iv_30")),
                missing_baseline_sessions=[d for d in dates if (symbol, d) not in headers],
                baseline_lineage=[_lineage(r) for r in base])
            records.append(current)
        key = args["rank_by"]
        records.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0), r["symbol"]))
        for rank, row in enumerate(records, 1):
            row["rank"] = rank if row.get(key) is not None else None
            row["rank_by"] = key
        return [_record_value("theta_activity", r) for r in records], sum(r["status"] == "available" for r in records), high
    row = headers.get((args["symbol"], session))
    if row is None:
        return [_record_value("theta_missing", dict(symbol=args["symbol"], session=session, reason="no_saved_capture_at_cutoff"))], 0, high
    summaries, details = reader.parts(row, full=True)
    metadata = reader.metadata(row)
    base = dict(_lineage(row), underlying_price=row["underlying_price"],
        coverage=metadata.get("coverage"), selection=metadata.get("selection"), policy=metadata.get("policy"),
        availability_state=metadata.get("availability_state"))
    if name == TOOLS[3]:
        return [_record_value("theta_volatility", _volatility(reader, row, headers, args, (summaries, details)))], 1, high
    if name == TOOLS[2]:
        leaders = [reader.decode(r[0]) for r in c.execute("SELECT payload FROM option_activity_leaders WHERE capture_id=? ORDER BY population,metric,right,rank LIMIT 41", (row["capture_id"],))]
        if len(leaders) > 40:
            raise ValueError("leader_bound")
        records = [_record_value("theta_capture", base), _record_value("theta_daily", reader.daily(row, (summaries, details)))]
        records += [_record_value("theta_summary_cell", dict(capture_id=row["capture_id"], **s)) for s in summaries]
        records += [_record_value("theta_activity_leader", dict(capture_id=row["capture_id"], **leader)) for leader in leaders]
        return records, 1, high
    selected = [d for d in details
                if ("expiration" not in args or d["expiration"] == args["expiration"])
                and ("right" not in args or d["right"] == args["right"])
                and ("strike_min" not in args or Decimal(d["strike"]) >= Decimal(args["strike_min"]))
                and ("strike_max" not in args or Decimal(d["strike"]) <= Decimal(args["strike_max"]))]
    selected.sort(key=lambda d: (d["expiration"], Decimal(d["strike"]), d["right"]))
    base.update(retained_count=len(details), matched_count=len(selected), filters={k: v for k, v in args.items() if k in ("expiration", "right", "strike_min", "strike_max")})
    return [_record_value("theta_capture", base)] + [_record_value("theta_selected_contract",
        dict(capture_id=row["capture_id"], **d)) for d in selected], 1, high


def _difference(current, previous):
    return current-previous if current is not None and previous is not None else None
