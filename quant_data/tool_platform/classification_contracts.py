"""Bounded inputs for the sole canonical FMP sector/industry mapping."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from quant_data.errors import ValidationError
from quant_data.schema import validate_schema
from .retained_research_common import instant

TOOL = "market.get_classifications"
KIND = "market_classifications_v1"
MAX_TICKERS = 2248
MAX_RECORDS = MAX_TICKERS + 1


def schema():
    return {"type": "object", "additionalProperties": False, "required": [], "properties": {
        "tickers": {"type": "array", "minItems": 1, "maxItems": MAX_TICKERS,
                    "items": {"type": "string", "minLength": 1, "maxLength": 32}},
        "sectors": {"type": "array", "minItems": 1, "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 200}},
        "industries": {"type": "array", "minItems": 1, "maxItems": 200,
                       "items": {"type": "string", "minLength": 1, "maxLength": 200}},
        "as_of": {"type": "string", "minLength": 20, "maxLength": 40},
    }}


@dataclass(frozen=True)
class ClassificationArguments(Mapping):
    values: tuple

    def __iter__(self): return iter(dict(self.values))
    def __len__(self): return len(self.values)
    def __getitem__(self, key): return dict(self.values)[key]

    @property
    def limit(self): return MAX_RECORDS


def parse(public):
    validate_schema(public, schema())
    values = dict(public)
    for key in ("tickers", "sectors", "industries"):
        if key not in values: continue
        items = values[key]
        if len(items) != len(set(items)):
            raise ValidationError("Classification selectors must contain unique values")
        if key == "tickers":
            if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\^-]{0,31}", v) for v in items):
                raise ValidationError("Use exact uppercase selected-universe ticker spellings")
        elif any(v != v.strip() or any(ord(ch) < 32 for ch in v) for v in items):
            raise ValidationError("Use exact FMP labels without surrounding whitespace or control characters")
        values[key] = tuple(sorted(items))
    if "as_of" in values: values["as_of"] = instant(values["as_of"])
    return ClassificationArguments(tuple(sorted(values.items())))
