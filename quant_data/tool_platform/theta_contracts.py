"""Version 1 contracts for the fixed optional Theta options store."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import re

from quant_data.errors import ValidationError, ResourceLimitError
from quant_data.options.universe import TIER1_ETFS
from quant_data.schema import validate_schema

TOOLS = tuple("options." + name for name in (
    "get_coverage", "get_daily_history", "get_daily_snapshot",
    "get_volatility_profile", "screen_activity", "get_selected_contracts"))
KINDS = {name: "theta_" + name.split(".")[1] + "_v1" for name in TOOLS}
MAX_RECORDS = 1000
VERSION = "theta_agent_analytics_v1"


def schema(kind):
    name = next(n for n, k in KINDS.items() if k == kind)
    day = {"type": "string", "minLength": 10, "maxLength": 10}
    symbol = {"type": "string", "enum": list(TIER1_ETFS)}
    props = {"as_of": {"type": "string", "minLength": 20, "maxLength": 40}}
    if name.endswith(("get_coverage", "screen_activity")):
        props["symbols"] = {"type": "array", "minItems": 1, "maxItems": 15, "items": symbol}
    else:
        props["symbol"] = symbol
    required = [] if "symbols" in props else ["symbol"]
    if name.endswith(("get_coverage", "get_daily_history")):
        props.update(start_date=day, end_date=day)
        required += ["start_date", "end_date"]
    else:
        props["session"] = day
        required += ["session"]
    if name.endswith(("get_volatility_profile", "screen_activity")):
        maximum = 60 if name.endswith("screen_activity") else 252
        props.update(lookback_sessions={"type": "integer", "minimum": 2, "maximum": maximum},
                     min_observations={"type": "integer", "minimum": 2, "maximum": maximum})
    if name.endswith("screen_activity"):
        props["rank_by"] = {"type": "string", "enum": ["relative_volume", "put_call_change", "zero_dte_volume_share", "atm_iv_30_change"]}
    if name.endswith("get_selected_contracts"):
        props.update(expiration=day, right={"type": "string", "enum": ["C", "P"]},
                     strike_min={"type": "string", "minLength": 1, "maxLength": 24},
                     strike_max={"type": "string", "minLength": 1, "maxLength": 24})
    return {"type": "object", "additionalProperties": False, "properties": props, "required": required}


@dataclass(frozen=True)
class ThetaArguments(Mapping):
    kind: str
    values: tuple

    def __iter__(self):
        return iter(dict(self.values))

    def __len__(self):
        return len(self.values)

    def __getitem__(self, key):
        return dict(self.values)[key]

    @property
    def limit(self):
        return MAX_RECORDS


def parse(kind, public):
    from decimal import Decimal
    validate_schema(public, schema(kind))
    value = dict(public)
    for key in ("start_date", "end_date", "session", "expiration"):
        if key in value:
            try:
                if date.fromisoformat(value[key]).isoformat() != value[key]:
                    raise ValueError()
            except ValueError as exc:
                raise ValidationError("Use exact valid ISO calendar dates") from exc
    if "start_date" in value:
        span = (date.fromisoformat(value["end_date"]) - date.fromisoformat(value["start_date"])).days
        if span < 0:
            raise ValidationError("start_date must be no later than end_date")
        if span > 365:
            raise ResourceLimitError("Choose at most 366 inclusive calendar days per call")
    if "as_of" in value:
        try:
            if "T" not in value["as_of"]:
                raise ValueError()
            cutoff = datetime.fromisoformat(value["as_of"].replace("Z", "+00:00"))
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError()
            cutoff = cutoff.astimezone(timezone.utc)
            cutoff.astimezone(ZoneInfo("America/New_York"))
            value["as_of"] = cutoff.isoformat()
        except (ValueError, OverflowError) as exc:
            raise ValidationError("as_of must be an aware capture-time instant") from exc
    if kind in (KINDS[TOOLS[0]], KINDS[TOOLS[4]]):
        symbols = value.get("symbols", TIER1_ETFS)
        if len(set(symbols)) != len(symbols):
            raise ValidationError("Duplicate symbols are not allowed")
        value["symbols"] = tuple(sorted(symbols))
    if kind in (KINDS[TOOLS[3]], KINDS[TOOLS[4]]):
        window = value.setdefault("lookback_sessions", 20 if kind == KINDS[TOOLS[4]] else 252)
        minimum = value.setdefault("min_observations", min(20, window))
        if minimum > window:
            raise ValidationError("min_observations must not exceed lookback_sessions")
    if kind == KINDS[TOOLS[4]]:
        value.setdefault("rank_by", "relative_volume")
    for key in ("strike_min", "strike_max"):
        if key in value:
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value[key]) or Decimal(value[key]) <= 0:
                raise ValidationError("Strike bounds must be positive decimal strings")
    if "strike_min" in value and "strike_max" in value and Decimal(value["strike_min"]) > Decimal(value["strike_max"]):
        raise ValidationError("Strike bounds are reversed")
    return ThetaArguments(kind, tuple(sorted(value.items())))
