"""Lossless Nasdaq Data Link SF1 parsing; no transport, store, or credentials.

Source keys come from retained table metadata. Local capture is availability;
source datekey/lastupdated values remain date-only source claims.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal,InvalidOperation
import hashlib,re
from ..errors import ConflictError,ResourceLimitError,ValidationError
from ..json_codec import loads_strict,dumps_strict,ensure_number_within_limits
from ..market.collection_universe import _utc

CHANNEL="nasdaq_data_link"
TABLE="SHARADAR/SF1"
DIMENSIONS=frozenset(("ARQ","ARY","ART","MRQ","MRY","MRT"))
MAX_BYTES=16*1024*1024
MAX_ROWS=10000
MAX_COLUMNS=1024
CORE=frozenset(("ticker","dimension","datekey","reportperiod","calendardate"))
DATE_FIELDS=frozenset(("datekey","reportperiod","calendardate","lastupdated"))
VALUE_METADATA=CORE|{"lastupdated"}

def digest(value):
    return hashlib.sha256(dumps_strict(value,max_bytes=64*1024*1024).encode()).hexdigest()

def _reference(value):
    from pathlib import PurePosixPath
    if (not isinstance(value,str) or not value or len(value)>4096 or "\\" in value
        or ":" in value or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts):
        raise ValidationError("Sharadar source reference must be a private logical path")
    return value

def _date(value):
    if not isinstance(value,str): raise ValidationError("Sharadar source date is invalid")
    try: parsed=date.fromisoformat(value)
    except ValueError as exc: raise ValidationError("Sharadar source date is invalid") from exc
    if parsed.isoformat()!=value: raise ValidationError("Sharadar source date must remain date-only")
    return value

def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,Decimal,str)):
        raise ValidationError("Sharadar numeric field has an invalid type")
    try: number=Decimal(value)
    except InvalidOperation as exc: raise ValidationError("Sharadar numeric field is invalid") from exc
    ensure_number_within_limits(number)
    result=format(number,"f")
    if "." in result: result=result.rstrip("0").rstrip(".")
    return "0" if result in ("-0","") else result

def _opaque(value):
    # Tag every node, including objects, so vendor text cannot collide with a
    # normalized numeric value or imitate the representation of another type.
    if value is None:return {"json_type":"null","value":None}
    if type(value) is bool:return {"json_type":"boolean","value":value}
    if isinstance(value,(Decimal,int)):return {"json_type":"number","value":_number(value)}
    if isinstance(value,str):return {"json_type":"string","value":value}
    if isinstance(value,list):return {"json_type":"array","value":[_opaque(v) for v in value]}
    if isinstance(value,dict):return {"json_type":"object","value":[[k,_opaque(v)] for k,v in sorted(value.items())]}
    raise ValidationError("Unsupported SF1 source JSON type")

def _cell(value,kind):
    if value is None:return None
    if kind in ("Integer","Long","BigInteger"):
        result=_number(value)
        if "." in result:raise ValidationError("Sharadar integer field contains a fraction")
        return result
    if kind in ("Double","double","Float","Number") or re.fullmatch(r"BigDecimal\([0-9]+,[0-9]+\)",kind):
        return _number(value)
    if kind=="Date":return _date(value)
    if kind in ("Datetime","DateTime","Timestamp"):
        if not isinstance(value,str): raise ValidationError("Sharadar timestamp is invalid")
        _utc(value)
        return value
    if kind in ("String","Text","text"):
        if not isinstance(value,str):raise ValidationError("Sharadar text field has an invalid type")
        return value
    if kind=="Boolean":
        if type(value) is not bool: raise ValidationError("Sharadar boolean field has an invalid type")
        return value
    return _opaque(value)

def _columns(raw):
    if not isinstance(raw,list) or not 1<=len(raw)<=MAX_COLUMNS:
        raise ResourceLimitError("Sharadar response column count is invalid")
    columns=[]
    for item in raw:
        if not isinstance(item,dict) or set(item)!={"name","type"}:
            raise ValidationError("Sharadar column declaration is invalid")
        name,kind=item["name"],item["type"]
        if (not isinstance(name,str) or not re.fullmatch("[a-z][a-z0-9_]{0,127}",name)
            or not isinstance(kind,str) or not 1<=len(kind)<=128):
            raise ValidationError("Sharadar column name or type is invalid")
        columns.append((name,kind))
    if len({n for n,_ in columns})!=len(columns):raise ConflictError("Sharadar response repeats a column")
    return tuple(columns)

@dataclass(frozen=True)
class Sf1Schema:
    columns: tuple[tuple[str,str],...]
    primary_key: tuple[str,...]
    filters: tuple[str,...]
    metadata_body: bytes
    captured_at: str
    source_reference: str
    key_contract_id: str
    schema_id: str
    channel: str = CHANNEL
    normalization: str = "nasdaq_sf1.v1"
    @property
    def content_sha256(self):return hashlib.sha256(self.metadata_body).hexdigest()

def parse_metadata(body,*,captured_at,source_reference):
    if not isinstance(body,bytes):raise ValidationError("SF1 metadata requires original bytes")
    value=loads_strict(body,max_bytes=MAX_BYTES)
    if not isinstance(value,dict) or not isinstance(value.get("datatable"),dict):
        raise ValidationError("Sharadar metadata envelope is invalid")
    raw=value["datatable"]
    if raw.get("vendor_code")!="SHARADAR" or raw.get("datatable_code")!="SF1":
        raise ValidationError("Metadata belongs to another Nasdaq table")
    columns=_columns(raw.get("columns"));types=dict(columns)
    keys=raw.get("primary_key");filters=raw.get("filters")
    if (not isinstance(keys,list) or not keys or any(not isinstance(k,str) or k not in types for k in keys)
        or len(keys)!=len(set(keys)) or not {"ticker","dimension"}<=set(keys)):
        raise ValidationError("SF1 metadata lacks an adequate explicit source key")
    if not CORE<=set(types):raise ValidationError("SF1 metadata lacks required source identity/date fields")
    if any(types[k]!="Date" for k in CORE&DATE_FIELDS) or any(types[k] not in ("String","text") for k in ("ticker","dimension")):
        raise ValidationError("SF1 identity/date metadata types differ")
    if (not isinstance(filters,list) or any(not isinstance(f,str) or f not in types for f in filters)
        or len(filters)!=len(set(filters))):
        raise ValidationError("SF1 filter metadata is invalid")
    # Key order is not row identity. The original vendor order remains in bytes.
    primary=tuple(sorted(keys));filter_names=tuple(sorted(filters))
    key_contract=digest({"channel":CHANNEL,"table":TABLE,"primary_key":[[k,types[k]] for k in primary]})
    schema_id=digest({"key_contract":key_contract,"columns":sorted(columns),"filters":filter_names,
        "normalization":"nasdaq_sf1.v1"})
    return Sf1Schema(columns,primary,filter_names,body,_utc(captured_at),_reference(source_reference),key_contract,schema_id)

@dataclass(frozen=True)
class Sf1Row:
    observation_id: str
    source_key_json: str
    source_ticker: str
    dimension: str
    source_datekey: str
    reportperiod: str
    calendardate: str
    source_lastupdated: str|None
    values_json: str
    missingness_json: str
    unrecognized_fields_json: str
    value_hash: str
    row_semantic_hash: str
    source_row_pointer: str
    source_row_index: int

@dataclass(frozen=True)
class Sf1Page:
    body: bytes
    captured_at: str
    source_reference: str
    parameters: tuple[tuple[str,str],...]
    columns: tuple[tuple[str,str],...]
    rows: tuple[Sf1Row,...]
    next_cursor: str|None
    schema: Sf1Schema
    @property
    def content_sha256(self):return hashlib.sha256(self.body).hexdigest()

def _parameters(parameters,schema):
    if not isinstance(parameters,dict) or len(parameters)>20:
        raise ValidationError("Sharadar request parameters are invalid")
    allowed={"ticker","dimension","qopts.per_page","qopts.cursor_id"}
    allowed|={f+suffix for f in DATE_FIELDS&set(schema.filters) for suffix in ("",".gt",".gte",".lt",".lte")}
    if any(k not in allowed or not isinstance(v,str) or not v or len(v)>8192 for k,v in parameters.items()):
        raise ValidationError("Sharadar request contains unsupported or secret parameters")
    tickers=parameters.get("ticker","").split(",");dimensions=parameters.get("dimension","").split(",")
    if (not 1<=len(tickers)<=100 or len(tickers)!=len(set(tickers))
        or any(not re.fullmatch("[A-Z0-9][A-Z0-9.\\^-]{0,31}",s) for s in tickers)
        or not dimensions or len(dimensions)!=len(set(dimensions)) or not set(dimensions)<=DIMENSIONS):
        raise ValidationError("SF1 request must pin finite tickers and reporting dimensions")
    if "ticker" not in schema.filters or "dimension" not in schema.filters:
        raise ValidationError("SF1 metadata does not advertise required filters")
    if "qopts.per_page" in parameters:
        value=parameters["qopts.per_page"]
        if not value.isascii() or not value.isdigit() or not 1<=int(value)<=MAX_ROWS:
            raise ResourceLimitError("SF1 page size exceeds the provider bound")
    for key,value in parameters.items():
        if key.split(".")[0] in DATE_FIELDS:
            _date(value)
    normalized=dict(parameters)
    normalized["ticker"]=",".join(sorted(tickers))
    normalized["dimension"]=",".join(sorted(dimensions))
    return tuple(sorted(normalized.items()))

def parse_page(body,*,schema,parameters,captured_at,source_reference):
    if isinstance(schema,Sf1Schema) and schema.channel=="sharadar_direct":
        from .sharadar_direct import parse_page as direct_page
        return direct_page(body,schema=schema,parameters=parameters,captured_at=captured_at,source_reference=source_reference)
    if not isinstance(body,bytes):raise ValidationError("SF1 response requires original bytes")
    if not isinstance(schema,Sf1Schema):raise ValidationError("Pinned SF1 metadata is required")
    reparsed=parse_metadata(schema.metadata_body,captured_at=schema.captured_at,source_reference=schema.source_reference)
    if reparsed!=schema:raise ValidationError("Prepared Sharadar schema changed")
    at=_utc(captured_at)
    if at<schema.captured_at:raise ConflictError("SF1 schema evidence is newer than the data acquisition")
    params=_parameters(parameters,schema);scope=dict(params)
    value=loads_strict(body,max_bytes=MAX_BYTES)
    if (not isinstance(value,dict) or set(value)!={"datatable","meta"}
        or not isinstance(value["datatable"],dict) or not isinstance(value["meta"],dict)):
        raise ValidationError("SF1 data envelope is invalid")
    raw=value["datatable"]
    if set(raw)!={"data","columns"}:raise ValidationError("SF1 table response fields differ")
    columns=_columns(raw["columns"]);types=dict(columns);metadata_types=dict(schema.columns)
    if not CORE<=set(types) or not set(schema.primary_key)<=set(types):
        raise ValidationError("SF1 response lacks source identity/date columns")
    if any(k in metadata_types and metadata_types[k]!=kind for k,kind in columns):
        raise ConflictError("SF1 source column type changed; new metadata is required")
    rows=raw["data"]
    if not isinstance(rows,list) or len(rows)>min(MAX_ROWS,int(scope.get("qopts.per_page",str(MAX_ROWS)))):
        raise ResourceLimitError("SF1 row count exceeds its requested bound")
    cursor=value["meta"].get("next_cursor_id")
    if "next_cursor_id" not in value["meta"] or (cursor is not None and (
        not isinstance(cursor,str) or not cursor or len(cursor)>8192 or any(ord(c)<32 for c in cursor))):
        raise ValidationError("SF1 next cursor is invalid or absent")
    parsed=[];seen={}
    for i,row in enumerate(rows):
        if not isinstance(row,list) or len(row)!=len(columns):raise ValidationError("SF1 row width differs")
        values={name:_cell(v,kind) for (name,kind),v in zip(columns,row)}
        if values["ticker"] not in scope["ticker"].split(",") or values["dimension"] not in scope["dimension"].split(","):
            raise ConflictError("SF1 row lies outside its requested tickers/dimensions")
        for key in CORE&DATE_FIELDS:_date(values[key])
        if values.get("lastupdated") is not None:_date(values["lastupdated"])
        for key,requested in scope.items():
            base,*operation=key.split(".")
            if base in DATE_FIELDS:
                actual=values.get(base)
                if actual is None:raise ConflictError("SF1 row cannot satisfy its requested date filter")
                op=operation[0] if operation else "eq"
                if not {"eq":actual==requested,"gt":actual>requested,"gte":actual>=requested,
                    "lt":actual<requested,"lte":actual<=requested}[op]:
                    raise ConflictError("SF1 row lies outside its requested date window")
        if any(values[k] is None for k in schema.primary_key):raise ValidationError("SF1 source key contains null")
        key_material=[[k,metadata_types[k],values[k]] for k in schema.primary_key]
        source_key_json=dumps_strict(key_material)
        observation=digest({"key_contract":schema.key_contract_id,"source_key":key_material})
        missing={"null_fields":sorted(k for k,v in values.items() if v is None),
            "absent_columns":sorted(set(metadata_types)-set(types))}
        unknown={k:{"type":types[k],"value":v} for k,v in values.items() if k not in metadata_types}
        material={"values":values,"missingness":missing,"unknown_fields":unknown,"types":sorted(columns)}
        row_hash=digest(material)
        if observation in seen and seen[observation]!=row_hash:
            raise ConflictError("SF1 response has conflicting duplicate source keys")
        seen[observation]=row_hash
        parsed.append(Sf1Row(observation,source_key_json,values["ticker"],values["dimension"],
            values["datekey"],values["reportperiod"],values["calendardate"],values.get("lastupdated"),
            dumps_strict(values),dumps_strict(missing),dumps_strict(unknown),
            digest({k:v for k,v in values.items() if k not in VALUE_METADATA}),row_hash,
            "/datatable/data/"+str(i),i+1))
    return Sf1Page(body,at,_reference(source_reference),params,columns,tuple(parsed),cursor,schema)

@dataclass(frozen=True)
class Sf1Partition:
    pages: tuple[Sf1Page,...]
    transport_complete: bool
    coherence: str
    semantic_hash: str
    acquisition_id: str

def prepare_partition(pages,*,max_pages,max_rows,max_bytes):
    pages=tuple(pages)
    if pages and isinstance(pages[0],Sf1Page) and pages[0].schema.channel=="sharadar_direct":
        from .sharadar_direct import prepare_partition as direct_partition
        return direct_partition(pages,max_pages=max_pages,max_rows=max_rows,max_bytes=max_bytes)
    if (type(max_pages) is not int or not 1<=max_pages<=1000 or not pages or len(pages)>max_pages
        or type(max_rows) is not int or not 1<=max_rows<=1000000
        or type(max_bytes) is not int or not 1<=max_bytes<=1024*1024*1024):
        raise ResourceLimitError("SF1 partition bounds are invalid")
    if any(not isinstance(p,Sf1Page) for p in pages):raise ValidationError("SF1 partition requires validated pages")
    if sum(len(p.body) for p in pages)>max_bytes or sum(len(p.rows) for p in pages)>max_rows:
        raise ResourceLimitError("SF1 partition exceeds its byte/row budget")
    prior_cursor=None;cursor_seen=set();canonical={};acquisitions=[]
    first=pages[0];base=dict(first.parameters);base.pop("qopts.cursor_id",None)
    base.pop("qopts.per_page",None)
    for i,page in enumerate(pages):
        if parse_page(page.body,schema=page.schema,parameters=dict(page.parameters),
            captured_at=page.captured_at,source_reference=page.source_reference)!=page:
            raise ValidationError("Prepared SF1 page changed")
        params=dict(page.parameters);request_cursor=params.pop("qopts.cursor_id",None);params.pop("qopts.per_page",None)
        if (params!=base or page.schema.schema_id!=first.schema.schema_id or request_cursor!=prior_cursor
            or (i and page.captured_at<pages[i-1].captured_at) or (i and prior_cursor is None)):
            raise ConflictError("SF1 cursor chain, scope, schema or capture order differs")
        if page.next_cursor is not None:
            if page.next_cursor in cursor_seen:raise ConflictError("SF1 cursor repeats")
            cursor_seen.add(page.next_cursor)
        for row in page.rows:
            if row.observation_id in canonical and canonical[row.observation_id]!=row.row_semantic_hash:
                raise ConflictError("SF1 cursor walk changed one source key")
            canonical[row.observation_id]=row.row_semantic_hash
        acquisitions.append({"body_sha256":page.content_sha256,"captured_at":page.captured_at,
            "parameters":dict(page.parameters),"source_reference":page.source_reference})
        prior_cursor=page.next_cursor
    complete=prior_cursor is None
    # Cursor termination proves transport completion, not a coherent remote snapshot.
    semantic=digest({"channel":CHANNEL,"table":TABLE,"scope":base,"schema_id":first.schema.schema_id,
        "transport_complete":complete,"coherence":"unproven","rows":sorted(canonical.items())})
    return Sf1Partition(pages,complete,"unproven",semantic,digest(acquisitions))
