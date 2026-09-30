"""Immutable, locked access to the fixed derived Options Monitor store."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
import sqlite3

from quant_data.contracts import TruncationV1, WarningV1
from quant_data.errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.options.monitor.policy import CADENCES, NY, load_universe
from quant_data.options.monitor.store import MonitorStore, SCHEMA_SHA
from quant_data.stores import StoreWriteLock
from .monitor_contracts import TOOLS, KINDS, MAX_RECORDS, MAX_OBSERVATIONS, MonitorArguments, parse
from .retained_research_common import instant, cutoff, digest, record
from .results import QueryResult, research_envelope

# Only documented aggregate fields cross the agent boundary. Internal fields,
# attempts, connection material, delivery state and arbitrary future fields do not.
METRICS = (
    "call_volume", "put_volume", "trade_count", "zero_dte_share", "near_term_share",
    "top5_share", "open_interest", "volume_oi", "underlying_price", "atm_iv_7",
    "atm_iv_30", "atm_iv_90", "skew", "relative_spread", "contract_count",
    "active_contract_count", "interval_volume", "interval_minutes",
    "interval_call_volume", "interval_put_volume", "relative_volume",
    "acceleration", "baseline_sessions", "put_call_ratio", "iv_change",
)
TEXT_FIELDS = ("oi_effective_date", "interval_start", "status")
SELECT = """SELECT session,symbol,slot,captured_at,cadence,fingerprint,
    CASE WHEN length(CAST(payload AS BLOB))<=65536 THEN payload END AS payload
    FROM observations """


class Reader:
    def __init__(self, connection, context):
        self.connection, self.context = connection, context
        self.steps = self.bytes = 0
        self.interrupted = None
        self.quantum = min(1000, context.budget.max_operations)
        connection.create_function("monitor_instant", 1, instant, deterministic=True)
        connection.set_progress_handler(self.progress, self.quantum)

    def progress(self):
        self.steps += self.quantum
        try:
            self.context.checkpoint()
            self.context.budget.require(operations=self.steps)
        except Exception as exc:
            self.interrupted = exc
            return 1
        return 0

    def observation(self, row, at, now):
        self.context.checkpoint()
        if row["payload"] is None:
            raise ResourceLimitError("Saved monitor observation exceeds 64 KiB")
        self.bytes += len(row["payload"].encode())
        if self.bytes > 8 * 1048576:
            raise ResourceLimitError("Monitor read exceeds the aggregate payload byte budget")
        value = loads_strict(row["payload"])
        if not isinstance(value, dict):
            raise ValueError("monitor_payload_shape")
        for key in ("symbol", "session", "captured_at"):
            if value.get(key) != row[key]:
                raise ValueError("monitor_payload_identity")
        captured = instant(row["captured_at"])
        scheduled = instant(row["slot"])
        day = date.fromisoformat(row["session"])
        if (day.isoformat() != row["session"]
            or datetime.fromisoformat(captured).astimezone(NY).date() != day
            or datetime.fromisoformat(scheduled).astimezone(NY).date() != day
            or scheduled > captured or captured > at
            or row["cadence"] not in CADENCES
            or type(value.get("cadence_minutes")) is not int
            or value["cadence_minutes"] != row["cadence"]):
            raise ValueError("monitor_payload_time_or_cadence")
        if value.get("coverage") not in ("complete", "incomplete"):
            raise ValueError("monitor_payload_coverage")
        flags = value.get("quality_flags")
        leaders = value.get("leaders")
        if (not isinstance(flags, list) or len(flags) > 100
            or any(not isinstance(f, str) or len(f) > 100 for f in flags)
            or not isinstance(leaders, list) or len(leaders) > 5):
            raise ValueError("monitor_payload_details")
        safe_leaders = []
        for leader in leaders:
            if not isinstance(leader, dict):
                raise ValueError("monitor_leader_shape")
            if (not isinstance(leader.get("expiration"), str)
                or len(leader["expiration"]) != 10 or leader.get("right") not in ("C", "P")
                or any(isinstance(leader.get(k), bool) or not isinstance(leader.get(k), (int, Decimal))
                       for k in ("strike", "volume", "share"))):
                raise ValueError("monitor_leader_fields")
            safe_leaders.append({k: leader[k] for k in ("expiration", "strike", "right", "volume", "share")})
        metrics = {}
        for key in METRICS:
            v = value.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float, Decimal))):
                raise ValueError("monitor_metric_shape")
            metrics[key] = v
        for key in TEXT_FIELDS:
            v = value.get(key)
            if v is not None and (not isinstance(v, str) or len(v) > 100):
                raise ValueError("monitor_text_shape")
            metrics[key] = v
        age = (datetime.fromisoformat(now) - datetime.fromisoformat(captured)).total_seconds()
        cutoff_age = (datetime.fromisoformat(at) - datetime.fromisoformat(captured)).total_seconds()
        return dict(metrics, symbol=row["symbol"], session=row["session"],
            captured_at=row["captured_at"], scheduled_at=row["slot"],
            cadence_minutes=row["cadence"], coverage=value["coverage"], quality_flags=flags,
            leaders=safe_leaders, age_seconds=age, age_at_cutoff_seconds=cutoff_age,
            freshness_at_cutoff="within_cadence" if cutoff_age <= row["cadence"] * 60 else "older_than_cadence",
            observation_id=digest([row["session"], row["symbol"], row["slot"]]),
            payload_sha256=hashlib.sha256(row["payload"].encode()).hexdigest(),
            contract_fingerprint=row["fingerprint"])


def invoke(name, arguments, context, registry):
    if not isinstance(arguments, MonitorArguments) or arguments.kind != KINDS[name]:
        raise ValidationError("Intraday options tools require registered typed arguments")
    # Revalidate manually constructed in-process argument objects as well.
    public = dict(arguments)
    if "symbols" in public:
        public["symbols"] = list(public["symbols"])
    args = dict(parse(arguments.kind, public))
    at, now = cutoff(args, context), instant(context.clock.instant())
    last_day = datetime.fromisoformat(at).astimezone(NY).date().isoformat()
    reader = None
    rows, observations = [], []
    try:
        roster_path = registry.project_root / "config/options_monitor_universe.json"
        if not roster_path.resolve(strict=True).is_relative_to(registry.project_root.resolve()):
            raise ValueError("monitor_universe_path")
        if roster_path.stat().st_size > 1048576:
            raise ResourceLimitError("Monitor universe exceeds its byte budget")
        universe = load_universe(roster_path)
        store = MonitorStore(registry.project_root)
        # Do not create a parent or initialize an absent source.
        if not store.path.is_file():
            raise ValueError("monitor_store_missing")
        with StoreWriteLock(store.path, timeout_seconds=1):
            with store.read() as connection:
                reader = Reader(connection, context)
                symbols = args["symbols"] if name == TOOLS[0] else (args["symbol"],)
                for symbol in symbols:
                    context.checkpoint()
                    if symbol not in universe:
                        rows.append(record("monitor_missing", dict(symbol=symbol, reason="outside_current_monitor_universe")))
                        continue
                    if name == TOOLS[0]:
                        where, params = "symbol=? AND session<=?", [symbol, last_day]
                        if "session" in args:
                            session = args["session"]
                        else:
                            # The existing (symbol,session,slot) index can find
                            # the latest eligible session without sorting all
                            # 90 retained sessions for every requested symbol.
                            latest = connection.execute(
                                "SELECT session FROM observations WHERE " + where +
                                " AND monitor_instant(captured_at)<=? "
                                "ORDER BY session DESC,slot DESC LIMIT 1", (*params, at)).fetchone()
                            session = latest["session"] if latest else last_day
                        where += " AND session=?"
                        params.append(session)
                        limit, order = 1, "DESC"
                    else:
                        where, params = "symbol=? AND session BETWEEN ? AND ?", [
                            symbol, args["start_date"], min(args["end_date"], last_day)]
                        limit, order = MAX_OBSERVATIONS + 1, "ASC"
                    selected = connection.execute(SELECT + "WHERE " + where +
                        " AND monitor_instant(captured_at)<=? ORDER BY monitor_instant(captured_at) " +
                        order + ",session " + order + ",slot " + order + " LIMIT ?", (*params, at, limit)).fetchall()
                    if len(selected) > MAX_OBSERVATIONS:
                        raise ResourceLimitError("More than 1000 saved observations; narrow the history date range")
                    if not selected:
                        rows.append(record("monitor_missing", dict(symbol=symbol, reason="no_saved_observation_at_cutoff",
                            session=args.get("session"), start_date=args.get("start_date"), end_date=args.get("end_date"))))
                    for row in selected:
                        value = reader.observation(row, at, now)
                        observations.append(value)
                        rows.append(record("monitor_observation", value))
    except sqlite3.Error as exc:
        if reader is not None and reader.interrupted is not None:
            raise reader.interrupted from exc
        raise StoreUnavailableError("The fixed Options Monitor store could not be read") from exc
    except (OSError, ValueError, TypeError, KeyError, OverflowError, ValidationError, ConflictError) as exc:
        raise StoreUnavailableError("The fixed Options Monitor source is missing, busy, or invalid; no fallback is used") from exc
    source = dict(provider="thetadata", source="options_monitor", source_contract="quant_data.options_monitor.v1",
        source_schema_sha256=SCHEMA_SHA, reader_version="1.0.0", capture_cutoff=at, read_at=now,
        selection="latest_retained_capture_per_symbol" if name == TOOLS[0] else "capture_time_ascending",
        observation_count=len(observations), missing_symbol_count=len(rows)-len(observations),
        source_high_water=max((instant(v["captured_at"]) for v in observations), default=None),
        availability_basis="local_capture_time_only", point_in_time_status="not_established",
        retention_observed_sessions=90, universe_basis="current_configured_monitor_roster",
        provider_requests=0, raw_source_replayable=False, full_chain_details=False,
        freshness_basis="elapsed_time_at_cutoff_compared_with_saved_cadence_not_market_calendar")
    rows.insert(0, record("monitor_source", source))
    context.checkpoint()
    context.budget.require(rows=len(rows), operations=reader.steps)
    envelope = research_envelope(name, dict(as_of=at, parameters=[
        dict(name="query_json", value=dumps_strict(args)),
        dict(name="source_records_sha256", value=digest([r.to_primitive() for r in rows]))]), (), (),
        unsafe_reasons=("historical_public_availability_not_established", "periodic_derived_options_aggregates"))
    contract = replace(envelope.contract, model_id="quant_data.options_monitor.reader", model_version="1.0.0",
        temporal=replace(envelope.contract.temporal, availability_basis="local_capture_time_only"),
        availability_policy="local_capture_time_only", vintage_policy="retained_capture_cutoff",
        execution_policy="host_fixed_monitor_immutable_read", sample={"output_record_count": len(rows)})
    identity = contract.to_primitive()
    identity.pop("analysis_id")
    envelope = replace(envelope, contract=replace(contract, analysis_id=digest(identity)))
    return QueryResult(tool=name, status="ok" if observations else "not_established", records=tuple(rows),
        warnings=(WarningV1("periodic_options_monitor",
            "Saved periodic aggregates, not streaming quotes or full contract chains. IV is a provider-derived proxy; OI retains its effective date. Capture cutoffs do not establish historical public availability."),
            WarningV1("monitor_retention_and_freshness",
            "History is retained for 90 observed sessions. Missing observations do not prove no trading; elapsed age is not a market-calendar health check.")),
        truncation=TruncationV1(False, MAX_RECORDS, len(rows), len(rows), False), research_contract=envelope)
