"""Closed v1 contracts for local retained transcript access."""
from __future__ import annotations
from collections.abc import Mapping, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import re
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict

TOOLS = ("company.search_transcripts", "company.get_transcript", "company.get_transcript_extraction")
KINDS = {name: name.rsplit(".", 1)[1] + "_v1" for name in TOOLS}
RAW_DATASET = "company.equibles.transcripts"
STRUCTURED_DATASET = "company.transcript.structured"
CAPTURE_PATTERN = r"equibles_transcript_[a-f0-9]{32}"
ANALYSIS_PATTERN = r"structured_transcript_[a-f0-9]{32}"
SYMBOL_PATTERN = r"[A-Z0-9][A-Z0-9.\-]{0,19}"
MAX_PAGE = 100

def instant(value):
    if not isinstance(value, str) or len(value)>64:
        raise ValidationError("Transcript cutoff must be an explicit timezone-aware timestamp")
    try:
        parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
        if parsed.tzinfo is None:raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
    except (ValueError,OverflowError) as exc:
        raise ValidationError("Transcript cutoff must be an explicit timezone-aware timestamp") from exc

def _matches(value, pattern):
    return isinstance(value,str) and re.fullmatch(pattern,value) is not None

def schema(kind):
    name=next((n for n,k in KINDS.items() if k==kind),None)
    if name is None:raise ValidationError("Unknown transcript tool")
    props={"mode":{"type":"string","enum":["latest","as_of"]},
           "as_of":{"type":"string","maxLength":64},
           "limit":{"type":"integer","minimum":1,"maximum":20 if name==TOOLS[2] else MAX_PAGE},
           "cursor":{"type":"string","minLength":1,"maxLength":2048}}
    if name==TOOLS[0]:
        props.update(ticker={"type":"string","minLength":1,"maxLength":20},
                     fiscal_year={"type":"integer","minimum":1900,"maximum":2200},
                     fiscal_quarter={"type":"integer","minimum":1,"maximum":4})
        required=[]
    else:
        props["capture_id"]={"type":"string","minLength":52,"maxLength":52}
        required=["capture_id"]
    return {"type":"object","additionalProperties":False,"properties":props,"required":required}

@dataclass(frozen=True)
class TranscriptArgumentsV1(Mapping):
    kind: str
    ticker: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    capture_id: str | None = None
    mode: str = "latest"
    as_of: str | None = None
    limit: int = 50
    cursor: str | None = None

    def __iter__(self) -> Iterator[str]:
        return iter(("ticker","fiscal_year","fiscal_quarter","capture_id","mode","as_of","limit","cursor"))
    def __len__(self):return 8
    def __getitem__(self,key):
        if key not in tuple(self):raise KeyError(key)
        return getattr(self,key)

def parse(kind, value):
    spec=schema(kind)
    if not isinstance(value,Mapping) or set(value)-set(spec["properties"]) or not set(spec["required"])<=set(value):
        raise ValidationError("Transcript arguments do not match their registered contract")
    for key,item in value.items():
        if item is None:raise ValidationError("Omit optional transcript arguments instead of passing null")
        if key in ("limit","fiscal_year","fiscal_quarter"):
            bound=spec["properties"][key]
            if type(item) is not int or not bound["minimum"]<=item<=bound["maximum"]:
                raise ValidationError("Transcript integer argument exceeds its bounds")
        elif key=="ticker" and not _matches(item,SYMBOL_PATTERN):
            raise ValidationError("Use an exact retained provider ticker")
        elif key=="capture_id" and not _matches(item,CAPTURE_PATTERN):
            raise ValidationError("Invalid transcript capture ID")
        elif key=="cursor" and (not isinstance(item,str) or not 1<=len(item)<=2048):
            raise ValidationError("Invalid transcript cursor")
    mode=value.get("mode","latest")
    if mode not in ("latest","as_of") or (mode=="as_of")!=("as_of" in value):
        raise ValidationError("as_of mode requires as_of; latest mode omits it")
    cooked=dict(value)
    if "as_of" in cooked:cooked["as_of"]=instant(cooked["as_of"])
    result=TranscriptArgumentsV1(kind,**{"limit":10 if kind==KINDS[TOOLS[2]] else 50,**cooked})
    # Validate cursor syntax, scope and anchor before opening any store.
    if result.cursor:decode_cursor(result)
    return result

def query_hash(args):
    return hashlib.sha256(dumps_strict({"kind":args.kind,**{k:args[k] for k in args if k not in ("limit","cursor")}}).encode()).hexdigest()

def encode_cursor(args,cutoff,anchor):
    data={"v":1,"query":query_hash(args),"cutoff":cutoff,"anchor":anchor}
    return base64.urlsafe_b64encode(dumps_strict(data).encode()).decode().rstrip("=")

def decode_cursor(args):
    try:
        raw=base64.b64decode(args.cursor+"="*(-len(args.cursor)%4),altchars=b"-_",validate=True)
        data=loads_strict(raw.decode("utf-8"),max_bytes=2048)
        if not isinstance(data,dict) or set(data)!={"v","query","cutoff","anchor"} or type(data["v"]) is not int or data["v"]!=1:
            raise ValueError()
        if data["query"]!=query_hash(args) or instant(data["cutoff"])!=data["cutoff"]:
            raise ValueError()
        if args.as_of and args.as_of!=data["cutoff"]:raise ValueError()
        a=data["anchor"]
        if args.kind==KINDS[TOOLS[0]]:
            if (not isinstance(a,list) or len(a)!=5 or not _matches(a[0],SYMBOL_PATTERN)
                or type(a[1]) is not int or not 1900<=a[1]<=2200
                or type(a[2]) is not int or not 1<=a[2]<=4
                or instant(a[3])!=a[3] or a[3]>data["cutoff"]
                or not _matches(a[4],CAPTURE_PATTERN)):raise ValueError()
        elif args.kind==KINDS[TOOLS[1]]:
            if type(a) is not int or not 1<=a<=10000:raise ValueError()
        else:
            if (not isinstance(a,list) or len(a)!=3 or not _matches(a[0],ANALYSIS_PATTERN)
                or instant(a[1])!=a[1] or a[1]>data["cutoff"]
                or not _matches(a[2],r"structured_assessment_[a-f0-9]{32}")):raise ValueError()
        if encode_cursor(args,data["cutoff"],a)!=args.cursor:raise ValueError()
        return data
    except (ValueError,TypeError,KeyError,UnicodeError,ValidationError) as exc:
        raise ValidationError("Transcript cursor is invalid or belongs to another query") from exc
