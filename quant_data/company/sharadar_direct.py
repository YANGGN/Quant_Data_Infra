"""Sharadar direct adapter with an explicit bridge to retained SF1 identities.

Only the in-memory canonical representation uses legacy datekey/SF1 names.
Every returned page keeps its original direct bytes and /data/N row pointers.
"""
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
import hashlib, json, re
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..json_codec import loads_strict, dumps_strict
from ..market.collection_universe import _utc
from . import sharadar_sf1 as legacy
from . import sharadar_definitions as definitions

CHANNEL = "sharadar_direct"
VERSION = "sharadar_direct.v1"
DEFINITION_VERSION = "sharadar_direct_definitions.v1"
TABLE = "fundamentals"
DEFINITION_PARAMETERS = {"tablename": TABLE, "format": "json", "limit": "1000", "sort": "indicator.asc"}
MAX_BYTES = legacy.MAX_BYTES
DIMENSIONS = legacy.DIMENSIONS

@lru_cache(maxsize=1)
def contract():
    return json.loads(Path(__file__).with_name("sharadar_direct_schema.json").read_text())

def _payload(body, maximum):
    if not isinstance(body, bytes):
        raise ValidationError("Direct response requires original bytes")
    value = loads_strict(body, max_bytes=MAX_BYTES)
    if not isinstance(value, dict) or set(value) != {"count", "data"}:
        raise ValidationError("Direct response envelope is invalid")
    rows = value["data"]
    if (type(value["count"]) is not int or not isinstance(rows, list)
            or value["count"] != len(rows) or len(rows) > maximum
            or any(not isinstance(r, dict) for r in rows)):
        raise ResourceLimitError("Direct response count or row bound is invalid")
    return rows

def _definition_rows(body):
    rows = _payload(body, 1000)
    if not rows or len(rows) == 1000:
        raise ConflictError("Direct descriptions are empty or not proven complete")
    expected = {c["name"] for c in contract()["definition_columns"]}
    known = {c["direct_name"] for c in contract()["columns"]}
    seen = set()
    for row in rows:
        if (set(row) != expected or row.get("table") != TABLE
                or any(not isinstance(v, str) for v in row.values())
                or row["indicator"] in seen):
            raise ValidationError("Direct definition fields, scope or keys differ")
        if row["isfilter"] not in ("Y", "N") or row["isprimarykey"] not in ("Y", "N"):
            raise ValidationError("Direct definition flags are invalid")
        seen.add(row["indicator"])
    if seen != known:
        raise ConflictError("Direct field inventory changed; review its typed contract")
    keys = {r["indicator"] for r in rows if r["isprimarykey"] == "Y"}
    if keys != {"ticker", "dimension", "date", "reportperiod"}:
        raise ConflictError("Direct source key changed; explicit reconciliation is required")
    filters = {r["indicator"] for r in rows if r["isfilter"] == "Y"}
    if not {"ticker", "dimension", "lastupdated"} <= filters:
        raise ConflictError("Direct source lacks the required incremental filters")
    return rows

def _canonical_definitions(rows):
    return [{**r, "table": "SF1", "indicator": "datekey" if r["indicator"] == "date" else r["indicator"]} for r in rows]

def _legacy_schema(at, reference):
    columns = [{"name": c["canonical_name"], "type": c["canonical_type"]} for c in contract()["columns"]]
    body = dumps_strict({"datatable": {"vendor_code": "SHARADAR", "datatable_code": "SF1",
        "columns": columns, "primary_key": ["ticker", "dimension", "datekey", "reportperiod"],
        "filters": ["ticker", "dimension", "datekey", "reportperiod", "calendardate", "lastupdated"]}}).encode()
    return legacy.parse_metadata(body, captured_at=at, source_reference=reference)

def parse_metadata(body, *, captured_at, source_reference):
    rows = _definition_rows(body)
    canonical = _legacy_schema(captured_at, source_reference)
    schema_id = legacy.digest({"channel": CHANNEL, "table": TABLE, "normalization": VERSION,
        "identity_bridge": canonical.key_contract_id, "typed_contract": contract(),
        "definitions": sorted(_canonical_definitions(rows), key=lambda r: r["indicator"])})
    return replace(canonical, metadata_body=body, schema_id=schema_id,
        channel=CHANNEL, normalization=VERSION)

def _parameters(parameters, schema=None):
    if not isinstance(parameters, dict) or (schema is not None and schema.channel != CHANNEL):
        raise ValidationError("Direct parameters require a direct schema")
    required = {"ticker", "dimension", "format", "sort", "from", "to", "limit", "offset"}
    optional = {"lastupdated.gte", "lastupdated.lte"}
    if (not required <= set(parameters) or set(parameters) - required - optional
            or any(not isinstance(v, str) or not v or len(v) > 8192 for v in parameters.values())
            or parameters["format"] != "json" or parameters["sort"] != "date.asc"
            or parameters["dimension"] not in DIMENSIONS):
        raise ValidationError("Direct request scope differs from its fixed contract")
    if len(parameters["ticker"]) > 200:
        raise ResourceLimitError("Direct ticker parameter exceeds 200 characters")
    tickers = parameters["ticker"].split(",")
    if (not 1 <= len(tickers) <= 30 or len(set(tickers)) != len(tickers)
            or tickers != sorted(tickers)
            or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\^-]{0,31}", t) for t in tickers)):
        raise ValidationError("Direct request needs canonical bounded tickers")
    for field in ("from", "to", *sorted(optional & set(parameters))):
        legacy._date(parameters[field])
    if parameters["from"] > parameters["to"]:
        raise ValidationError("Direct date window is reversed")
    if (optional & set(parameters)) not in (set(), optional):
        raise ValidationError("Direct update window must include both bounds")
    if optional <= set(parameters) and parameters["lastupdated.gte"] > parameters["lastupdated.lte"]:
        raise ValidationError("Direct update window is reversed")
    for key, lower, upper in (("limit", 1, 10000), ("offset", 0, 100000)):
        value = parameters[key]
        if not value.isascii() or not value.isdigit() or str(int(value)) != value or not lower <= int(value) <= upper:
            raise ResourceLimitError("Direct page bounds are invalid")
    return tuple(sorted(parameters.items()))

def parse_page(body, *, schema, parameters, captured_at, source_reference):
    if (not isinstance(schema, legacy.Sf1Schema) or schema.channel != CHANNEL
            or parse_metadata(schema.metadata_body, captured_at=schema.captured_at,
                              source_reference=schema.source_reference) != schema):
        raise ValidationError("Direct pinned schema changed")
    params = _parameters(parameters, schema)
    at = _utc(captured_at)
    if at < schema.captured_at:
        raise ConflictError("Direct page predates its schema")
    rows = _payload(body, int(parameters["limit"]))
    mapping = contract()["columns"]
    native_names = {c["direct_name"] for c in mapping}
    canonical_rows = []
    for row in rows:
        if set(row) != native_names:
            raise ConflictError("Direct response fields differ from the pinned inventory")
        canonical_rows.append([row[c["direct_name"]] for c in mapping])
    # Reuse the proven scalar/key canonicalizer in memory; never retain this envelope.
    columns = [{"name": c["canonical_name"], "type": c["canonical_type"]} for c in mapping]
    temporary = dumps_strict({"datatable": {"columns": columns, "data": canonical_rows},
                             "meta": {"next_cursor_id": None}}, max_bytes=MAX_BYTES).encode()
    legacy_params = {"ticker": parameters["ticker"], "dimension": parameters["dimension"],
        "qopts.per_page": parameters["limit"], "datekey.gte": parameters["from"], "datekey.lte": parameters["to"]}
    legacy_params.update({k:v for k,v in parameters.items() if k.startswith("lastupdated.")})
    converted = legacy.parse_page(temporary, schema=_legacy_schema(schema.captured_at, schema.source_reference),
        parameters=legacy_params, captured_at=at, source_reference=source_reference)
    offset = int(parameters["offset"])
    cursor = str(offset + len(rows)) if len(rows) == int(parameters["limit"]) else None
    dates = [r.source_datekey for r in converted.rows]
    if dates != sorted(dates):
        raise ConflictError("Direct rows violate their requested date order")
    return replace(converted, body=body, schema=schema, parameters=params,
        next_cursor=cursor, rows=tuple(replace(r, source_row_pointer="/data/" + str(i))
                                     for i,r in enumerate(converted.rows)))

def prepare_partition(pages, *, max_pages, max_rows, max_bytes):
    pages = tuple(pages)
    if (type(max_pages) is not int or not 1 <= max_pages <= 100
            or not pages or len(pages) > max_pages
            or type(max_rows) is not int or not 1 <= max_rows <= 100000
            or type(max_bytes) is not int or not 1 <= max_bytes <= 256*1024*1024):
        raise ResourceLimitError("Direct partition bounds are invalid")
    if any(not isinstance(p, legacy.Sf1Page) or p.schema.channel != CHANNEL for p in pages):
        raise ValidationError("Direct partition requires direct pages")
    if sum(len(p.body) for p in pages) > max_bytes or sum(len(p.rows) for p in pages) > max_rows:
        raise ResourceLimitError("Direct partition exceeds its bounds")
    first = pages[0]
    scope = dict(first.parameters)
    scope.pop("offset")
    expected_offset = "0"
    seen = {}
    previous_date = None
    acquisitions = []
    for i,page in enumerate(pages):
        if parse_page(page.body, schema=page.schema, parameters=dict(page.parameters),
                captured_at=page.captured_at, source_reference=page.source_reference) != page:
            raise ValidationError("Prepared direct page changed")
        params = dict(page.parameters)
        offset = params.pop("offset")
        if (params != scope or offset != expected_offset or page.schema != first.schema
                or (i and page.captured_at < pages[i-1].captured_at)):
            raise ConflictError("Direct pagination scope, offset or schema changed")
        for row in page.rows:
            if row.observation_id in seen:
                raise ConflictError("Direct pagination repeated a source key")
            if previous_date is not None and row.source_datekey < previous_date:
                raise ConflictError("Direct page order regressed")
            previous_date = row.source_datekey
            seen[row.observation_id] = row.row_semantic_hash
        acquisitions.append({"body_sha256":page.content_sha256,"captured_at":page.captured_at,
            "parameters":dict(page.parameters),"source_reference":page.source_reference})
        expected_offset = page.next_cursor
        if i < len(pages)-1 and expected_offset is None:
            raise ConflictError("Direct walk continued beyond its terminal page")
    complete = expected_offset is None
    scope.pop("limit")
    semantic = legacy.digest({"channel":CHANNEL,"table":TABLE,"scope":scope,"schema_id":first.schema.schema_id,
        "transport_complete":complete,"coherence":"unproven","rows":sorted(seen.items())})
    return legacy.Sf1Partition(pages, complete, "unproven", semantic, legacy.digest(acquisitions))

def _legacy_definition_schema(at, reference):
    body = dumps_strict({"datatable":{"vendor_code":"SHARADAR","datatable_code":"INDICATORS",
        "columns":contract()["definition_columns"],"primary_key":["table","indicator"],
        "filters":["indicator","isfilter","isprimarykey","table"]}}).encode()
    return definitions.parse_definition_metadata(body,captured_at=at,source_reference=reference)

def parse_definition_metadata(body, *, captured_at, source_reference):
    rows = _definition_rows(body)
    base = _legacy_definition_schema(captured_at, source_reference)
    return replace(base, body=body, channel=CHANNEL, normalization=DEFINITION_VERSION,
        schema_id=legacy.digest({"normalization":DEFINITION_VERSION,"channel":CHANNEL,
            "columns":base.columns,"primary_key":base.primary_key,
            "definitions":sorted(rows,key=lambda r:r["indicator"])}))

def parse_definition_page(body, *, schema, parameters, captured_at, source_reference):
    if (not isinstance(schema, definitions.DefinitionSchema) or schema.channel != CHANNEL
            or parse_definition_metadata(schema.body,captured_at=schema.captured_at,
                                         source_reference=schema.source_reference) != schema):
        raise ValidationError("Direct definition schema changed")
    if parameters != DEFINITION_PARAMETERS or body != schema.body:
        raise ConflictError("Direct definitions require their complete pinned metadata response")
    rows = _canonical_definitions(_definition_rows(body))
    columns = contract()["definition_columns"]
    temporary = dumps_strict({"datatable":{"columns":columns,
        "data":[[r[c["name"]] for c in columns] for r in rows]},"meta":{"next_cursor_id":None}}).encode()
    converted = definitions.parse_definition_page(temporary,
        schema=_legacy_definition_schema(schema.captured_at,schema.source_reference),
        parameters={"table":"SF1","qopts.per_page":"1000"},captured_at=captured_at,source_reference=source_reference)
    return replace(converted,schema=schema,body=body,parameters=tuple(sorted(parameters.items())),
        rows=tuple(replace(r,source_row_pointer="/data/"+str(i)) for i,r in enumerate(converted.rows)))

def prepare_definition_snapshot(pages):
    pages = tuple(pages)
    if len(pages) != 1:
        raise ConflictError("Direct definitions require one complete bounded response")
    p = pages[0]
    if parse_definition_page(p.body,schema=p.schema,parameters=dict(p.parameters),
            captured_at=p.captured_at,source_reference=p.source_reference) != p:
        raise ValidationError("Prepared direct definitions changed")
    return definitions.DefinitionSnapshot(pages,legacy.digest({"schema":p.schema.schema_id,
        "rows":sorted((r.observation_id,r.semantic_hash) for r in p.rows)}),
        legacy.digest({"metadata_sha256":hashlib.sha256(p.schema.body).hexdigest(),
            "metadata_captured_at":p.schema.captured_at,"metadata_reference":p.schema.source_reference,
            "body_sha256":hashlib.sha256(p.body).hexdigest(),"captured_at":p.captured_at,
            "reference":p.source_reference,"parameters":dict(p.parameters)}))

def ticker_rows(body):
    rows = _payload(body,10000)
    if len(rows) == 10000:
        raise ConflictError("Direct ticker identity response is not proven complete")
    for row in rows:
        if (row.get("table") != TABLE or not isinstance(row.get("ticker"),str)
                or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\^-]{0,31}",row["ticker"])
                or isinstance(row.get("permaticker"),bool)
                or not isinstance(row.get("permaticker"),(int,str))
                or not re.fullmatch(r"[1-9][0-9]*",str(row["permaticker"]))):
            raise ValidationError("Direct identity is outside its fundamentals scope")
    return rows
