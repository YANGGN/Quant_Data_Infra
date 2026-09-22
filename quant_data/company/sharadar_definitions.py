"""Nasdaq INDICATORS evidence for SF1 definitions, independent of fact values."""
from dataclasses import dataclass
from ..errors import ValidationError,ConflictError,ResourceLimitError
from ..json_codec import loads_strict,dumps_strict
from ..market.collection_universe import _utc
from .sharadar_sf1 import _columns,_cell,_reference,digest,MAX_BYTES

@dataclass(frozen=True)
class DefinitionSchema:
    columns: tuple
    primary_key: tuple
    filters: tuple
    body: bytes
    captured_at: str
    source_reference: str
    schema_id: str
    channel: str = "nasdaq_data_link"
    normalization: str = "nasdaq_sf1_definitions.v1"

def parse_definition_metadata(body,*,captured_at,source_reference):
    if not isinstance(body,bytes):raise ValidationError("INDICATORS metadata requires original bytes")
    raw=loads_strict(body,max_bytes=MAX_BYTES)
    table=raw.get("datatable") if isinstance(raw,dict) else None
    if not isinstance(table,dict) or table.get("vendor_code")!="SHARADAR" or table.get("datatable_code")!="INDICATORS":
        raise ValidationError("Definition metadata belongs to another Nasdaq table")
    columns=_columns(table.get("columns"));types=dict(columns);keys=table.get("primary_key");filters=table.get("filters")
    if (not isinstance(keys,list) or not keys or any(not isinstance(k,str) for k in keys) or len(set(keys))!=len(keys) or not {"table","indicator"}<=set(keys)
        or any(k not in types for k in keys) or types.get("table") not in ("String","text") or types.get("indicator") not in ("String","text")
        or not isinstance(filters,list) or any(not isinstance(f,str) for f in filters) or len(set(filters))!=len(filters) or "table" not in filters
        or any(f not in types for f in filters)):
        raise ValidationError("INDICATORS lacks its explicit metadata keys or SF1 table filter")
    material={"channel":"nasdaq_data_link","table":"SHARADAR/INDICATORS","scope_table":"SF1",
        "columns":sorted(columns),"primary_key":sorted(keys),"filters":sorted(filters),"normalization":"nasdaq_sf1_definitions.v1"}
    return DefinitionSchema(columns,tuple(sorted(keys)),tuple(sorted(filters)),body,_utc(captured_at),
        _reference(source_reference),digest(material))

@dataclass(frozen=True)
class DefinitionRow:
    observation_id: str
    key_json: str
    indicator: str
    values_json: str
    semantic_hash: str
    source_row_pointer: str
    source_row_index: int

@dataclass(frozen=True)
class DefinitionPage:
    schema: DefinitionSchema
    body: bytes
    captured_at: str
    source_reference: str
    parameters: tuple
    rows: tuple[DefinitionRow,...]
    next_cursor: str | None

def parse_definition_page(body,*,schema,parameters,captured_at,source_reference):
    if isinstance(schema,DefinitionSchema) and schema.channel=="sharadar_direct":
        from .sharadar_direct import parse_definition_page as direct_page
        return direct_page(body,schema=schema,parameters=parameters,captured_at=captured_at,source_reference=source_reference)
    if not isinstance(schema,DefinitionSchema) or parse_definition_metadata(schema.body,
        captured_at=schema.captured_at,source_reference=schema.source_reference)!=schema:
        raise ValidationError("Definition metadata changed")
    if (not isinstance(parameters,dict) or parameters.get("table")!="SF1"
        or set(parameters)-{"table","qopts.per_page","qopts.cursor_id"}):
        raise ValidationError("Definitions must use the disjoint SF1 table scope")
    maximum=parameters.get("qopts.per_page","10000")
    if not isinstance(maximum,str) or not maximum.isascii() or not maximum.isdigit() or not 1<=int(maximum)<=10000:
        raise ResourceLimitError("Definition page size exceeds its bound")
    if "qopts.cursor_id" in parameters and (not isinstance(parameters["qopts.cursor_id"],str)
        or not parameters["qopts.cursor_id"] or len(parameters["qopts.cursor_id"])>8192
        or any(ord(c)<32 for c in parameters["qopts.cursor_id"])):
        raise ValidationError("Definition request cursor is invalid")
    at=_utc(captured_at)
    if at<schema.captured_at:raise ConflictError("Definition page predates its metadata")
    if not isinstance(body,bytes):raise ValidationError("Definition page requires original bytes")
    raw=loads_strict(body,max_bytes=MAX_BYTES)
    if not isinstance(raw,dict) or not isinstance(raw.get("datatable"),dict) or not isinstance(raw.get("meta"),dict):
        raise ValidationError("Definition page envelope is invalid")
    data=raw["datatable"];columns=_columns(data.get("columns"))
    if dict(columns)!=dict(schema.columns):raise ConflictError("Definition column metadata changed")
    rows=data.get("data")
    if not isinstance(rows,list) or len(rows)>int(maximum):raise ResourceLimitError("Definition row count exceeds its bound")
    cursor=raw["meta"].get("next_cursor_id")
    if "next_cursor_id" not in raw["meta"] or (cursor is not None and (not isinstance(cursor,str) or not cursor or len(cursor)>8192 or any(ord(c)<32 for c in cursor))):
        raise ValidationError("Definition response cursor is invalid")
    parsed=[];seen={}
    for index,row in enumerate(rows):
        if not isinstance(row,list) or len(row)!=len(columns):raise ValidationError("Definition row width differs")
        values={name:_cell(v,kind) for (name,kind),v in zip(columns,row)}
        if values.get("table")!="SF1" or not isinstance(values.get("indicator"),str) or not values["indicator"] or len(values["indicator"])>128:
            raise ConflictError("Definition row belongs outside the SF1 indicator scope")
        if any(values[k] is None for k in schema.primary_key):raise ValidationError("Definition key is null")
        key=[[k,dict(columns)[k],values[k]] for k in schema.primary_key]
        key_json=dumps_strict(key);oid=digest({"channel":"nasdaq_data_link","table":"SHARADAR/INDICATORS","key":key})
        semantic=digest({"values":values,"types":sorted(columns)})
        if oid in seen and seen[oid]!=semantic:raise ConflictError("Definitions repeat a conflicting source key")
        seen[oid]=semantic
        parsed.append(DefinitionRow(oid,key_json,values["indicator"],dumps_strict(values),semantic,
            "/datatable/data/"+str(index),index+1))
    return DefinitionPage(schema,body,at,_reference(source_reference),tuple(sorted(parameters.items())),tuple(parsed),cursor)

@dataclass(frozen=True)
class DefinitionSnapshot:
    pages: tuple[DefinitionPage,...]
    semantic_hash: str
    acquisition_id: str

def prepare_definition_snapshot(pages):
    import hashlib
    pages=tuple(pages)
    if pages and isinstance(pages[0],DefinitionPage) and pages[0].schema.channel=="sharadar_direct":
        from .sharadar_direct import prepare_definition_snapshot as direct_snapshot
        return direct_snapshot(pages)
    if not pages or len(pages)>10 or any(not isinstance(p,DefinitionPage) for p in pages):
        raise ResourceLimitError("Definitions need one to ten validated pages")
    if sum(len(p.body) for p in pages)+len(pages[0].schema.body)>32*1024*1024 or sum(len(p.rows) for p in pages)>10000:
        raise ResourceLimitError("Definitions exceed 32 MiB or 10000 rows")
    cursor=None;seen=set();rows={}
    for i,p in enumerate(pages):
        if parse_definition_page(p.body,schema=p.schema,parameters=dict(p.parameters),captured_at=p.captured_at,source_reference=p.source_reference)!=p:
            raise ValidationError("Prepared definition evidence changed")
        if (dict(p.parameters).get("qopts.cursor_id")!=cursor or p.schema.schema_id!=pages[0].schema.schema_id
            or p.schema!=pages[0].schema
            or {k:v for k,v in p.parameters if k!="qopts.cursor_id"}!={k:v for k,v in pages[0].parameters if k!="qopts.cursor_id"}
            or (i and (cursor is None or p.captured_at<pages[i-1].captured_at))):
            raise ConflictError("Definition cursor chain or metadata differs")
        if p.next_cursor is not None and p.next_cursor in seen:raise ConflictError("Definition cursor repeats")
        seen.add(p.next_cursor);cursor=p.next_cursor
        for row in p.rows:
            if row.observation_id in rows and rows[row.observation_id]!=row.semantic_hash:
                raise ConflictError("Definition changed within the cursor walk")
            rows[row.observation_id]=row.semantic_hash
    if cursor is not None:raise ConflictError("Incomplete definitions stay private")
    return DefinitionSnapshot(pages,digest({"schema":pages[0].schema.schema_id,"rows":sorted(rows.items())}),
        digest({"metadata_sha256":hashlib.sha256(pages[0].schema.body).hexdigest(),
            "metadata_captured_at":pages[0].schema.captured_at,"metadata_reference":pages[0].schema.source_reference,
            "pages":[{"sha256":hashlib.sha256(p.body).hexdigest(),"captured_at":p.captured_at,
                "reference":p.source_reference,"parameters":dict(p.parameters)} for p in pages]}))
