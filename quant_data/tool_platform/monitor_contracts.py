"""Bounded public contracts for saved intraday Options Monitor observations."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.options.monitor.policy import SYMBOL, NY
from quant_data.schema import validate_schema
from .retained_research_common import instant

TOOLS = ("options.get_intraday_snapshot", "options.get_intraday_history")
KINDS = {name: "monitor_" + name.split(".")[1] + "_v1" for name in TOOLS}
MAX_OBSERVATIONS = 1000
MAX_RECORDS = MAX_OBSERVATIONS + 2


def schema(kind):
    symbol = {"type": "string", "minLength": 1, "maxLength": 12}
    day = {"type": "string", "minLength": 10, "maxLength": 10}
    properties = {"as_of": {"type": "string", "minLength": 20, "maxLength": 40}}
    if kind == KINDS[TOOLS[0]]:
        properties.update(symbols={"type": "array", "minItems": 1, "maxItems": 50, "items": symbol}, session=day)
        required = ["symbols"]
    elif kind == KINDS[TOOLS[1]]:
        properties.update(symbol=symbol, start_date=day, end_date=day)
        required = ["symbol", "start_date", "end_date"]
    else:
        raise ValidationError("Unknown intraday options contract")
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}


@dataclass(frozen=True)
class MonitorArguments(Mapping):
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
    validate_schema(public, schema(kind))
    value = dict(public)
    symbols = value.get("symbols", [value.get("symbol")])
    if any(not SYMBOL.fullmatch(s) for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValidationError("Use unique exact uppercase monitor symbols")
    if "symbols" in value:
        value["symbols"] = tuple(sorted(symbols))
    for key in ("session", "start_date", "end_date"):
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
        if span > 6:
            raise ResourceLimitError("Choose at most seven inclusive calendar days; split longer history into separate calls")
    if "as_of" in value:
        value["as_of"] = instant(value["as_of"])
        try:
            datetime.fromisoformat(value["as_of"]).astimezone(NY)
        except (ValueError, OverflowError) as exc:
            raise ValidationError("as_of is outside the supported monitor calendar") from exc
    return MonitorArguments(kind, tuple(sorted(value.items())))
