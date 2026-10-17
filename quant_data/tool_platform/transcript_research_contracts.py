"""Bounded transcript research contracts; existing transcript v1 stays frozen."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
import base64
import hashlib
import re
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from .transcript_contracts import CAPTURE_PATTERN, SYMBOL_PATTERN, instant

HISTORY = "company.get_transcript_history"
SEARCH = "company.search_transcript_evidence"
TARGET = "company.get_transcript"
NEW_TOOLS = (HISTORY, SEARCH)
KINDS = {HISTORY: "transcript_history_v1", SEARCH: "transcript_evidence_v1", TARGET: "transcript_turns_v2"}
SECTIONS = ("headline", "reported_results", "guidance", "business_drivers",
            "analyst_focus", "watch_items", "management_tone")
TURN_PATTERN = r"t(?:[1-9][0-9]{0,3}|10000)"

def schema(kind):
    if kind not in KINDS.values():
        raise ValidationError("Unknown transcript research contract")
    props = {"mode": {"type": "string", "enum": ["latest", "as_of"]},
             "as_of": {"type": "string", "maxLength": 64}}
    if kind == KINDS[TARGET]:
        props.update(capture_id={"type": "string", "minLength": 52, "maxLength": 62},
            turn_ids={"type": "array", "minItems": 1, "maxItems": 20,
                      "items": {"type": "string", "minLength": 2, "maxLength": 6}},
            context_turns={"type": "integer", "minimum": 0, "maximum": 2})
        required = ["capture_id", "turn_ids"]
    else:
        props.update(sections={"type": "array", "minItems": 1, "maxItems": len(SECTIONS),
                               "items": {"type": "string", "enum": list(SECTIONS)}},
            include_blocked={"type": "boolean"},
            start_date={"type": "string", "minLength": 10, "maxLength": 10},
            end_date={"type": "string", "minLength": 10, "maxLength": 10},
            limit={"type": "integer", "minimum": 1, "maximum": 20})
        if kind == KINDS[HISTORY]:
            props["ticker"] = {"type": "string", "minLength": 1, "maxLength": 20}
            required = ["ticker"]
        else:
            props.update(query={"type": "string", "minLength": 1, "maxLength": 200},
                tickers={"type": "array", "minItems": 1, "maxItems": 20,
                         "items": {"type": "string", "minLength": 1, "maxLength": 20}},
                cursor={"type": "string", "minLength": 1, "maxLength": 2048})
            required = ["query"]
    return {"type": "object", "additionalProperties": False, "properties": props, "required": required}

@dataclass(frozen=True)
class TranscriptResearchArguments(Mapping):
    kind: str
    values: Mapping
    def __iter__(self): return iter(self.values)
    def __len__(self): return len(self.values)
    def __getitem__(self, key): return self.values[key]
    @property
    def limit(self): return self.get("limit", 100)

def parse(kind, value):
    from quant_data.schema import validate_schema
    validate_schema(value, schema(kind))
    cooked = dict(value)
    for key in ("ticker", "capture_id"):
        if key in cooked and not re.fullmatch(SYMBOL_PATTERN if key == "ticker" else CAPTURE_PATTERN, cooked[key]):
            raise ValidationError("Invalid transcript identifier")
    for key in ("turn_ids", "tickers", "sections"):
        if key in cooked:
            items = cooked[key]
            if len(set(items)) != len(items):
                raise ValidationError("Transcript selections must be unique")
            pattern = TURN_PATTERN if key == "turn_ids" else SYMBOL_PATTERN if key == "tickers" else None
            if pattern and any(not re.fullmatch(pattern, item) for item in items):
                raise ValidationError("Invalid transcript selection")
            cooked[key] = tuple(sorted(items)) if key == "tickers" else tuple(items)
    for key in ("start_date", "end_date"):
        if key in cooked:
            try:
                if date.fromisoformat(cooked[key]).isoformat() != cooked[key]: raise ValueError()
            except ValueError as exc:
                raise ValidationError("Use an exact YYYY-MM-DD call date") from exc
    if cooked.get("start_date", "") > cooked.get("end_date", "9999-12-31"):
        raise ValidationError("Call date range is reversed")
    mode = cooked.setdefault("mode", "latest")
    if (mode == "as_of") != ("as_of" in cooked):
        raise ValidationError("as_of mode requires as_of; latest mode omits it")
    if "as_of" in cooked: cooked["as_of"] = instant(cooked["as_of"])
    if kind == KINDS[TARGET]:
        cooked.setdefault("context_turns", 0)
    else:
        cooked.setdefault("sections", SECTIONS)
        cooked.setdefault("include_blocked", False)
        cooked.setdefault("limit", 8 if kind == KINDS[HISTORY] else 10)
    if "query" in cooked:
        cooked["query"] = cooked["query"].strip()
        if not cooked["query"]: raise ValidationError("Search query cannot be blank")
    result = TranscriptResearchArguments(kind, MappingProxyType(cooked))
    if result.get("cursor"): decode_cursor(result)
    return result

def query_hash(args):
    return hashlib.sha256(dumps_strict({"kind": args.kind,
        **{k: v for k, v in args.items() if k not in ("limit", "cursor")}}).encode()).hexdigest()

def encode_cursor(args, cutoff, anchor):
    data = {"v": 1, "query": query_hash(args), "cutoff": cutoff, "anchor": anchor}
    return base64.urlsafe_b64encode(dumps_strict(data).encode()).decode().rstrip("=")

def decode_cursor(args):
    try:
        raw = base64.b64decode(args["cursor"] + "=" * (-len(args["cursor"]) % 4), altchars=b"-_", validate=True)
        data = loads_strict(raw.decode(), max_bytes=2048)
        a = data["anchor"]
        if (set(data) != {"v", "query", "cutoff", "anchor"} or type(data["v"]) is not int or data["v"] != 1
            or args.kind != KINDS[SEARCH] or data["query"] != query_hash(args)
            or instant(data["cutoff"]) != data["cutoff"]
            or (args.get("as_of") and args["as_of"] != data["cutoff"])
            or not isinstance(a, list) or len(a) != 2 or instant(a[0]) != a[0]
            or a[0] > data["cutoff"] or not re.fullmatch(CAPTURE_PATTERN, a[1])
            or encode_cursor(args, data["cutoff"], a) != args["cursor"]):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, ValidationError) as exc:
        raise ValidationError("Transcript research cursor is invalid or belongs to another query") from exc
