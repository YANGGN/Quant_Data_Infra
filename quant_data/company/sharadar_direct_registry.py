"""Direct delivery declarations; retained Nasdaq collectors describe historical replay only."""
import copy, hashlib, json
from pathlib import Path
VERSION="2.82.0"
PREDECESSOR_SHA256="6809d73406863d5b4b2e04f4c8eed3b2fac4ac021afa4c66536141cafbad2115"
MIGRATION_ID="company:0017_sharadar_direct"
MIGRATION_RESOURCE="quant_data/migrations/company/0017_sharadar_direct.sql"
COLLECTOR_IDS=("sharadar.company.direct_sf1","sharadar.company.direct_definitions")

def declarations(raw):
    result=[]
    for old,new,handler in zip(("nasdaq.company.sharadar_sf1","nasdaq.company.sharadar_definitions"),
                               COLLECTOR_IDS,("company.sharadar_direct_sf1","company.sharadar_direct_definitions")):
        c=copy.deepcopy(next(c for c in raw["collectors"] if c["id"]==old))
        c.update(id=new,handler=handler,configuration_env=["SHARADAR_DIRECT_API"])
        c["retry_policy"]["max_attempts"]=1
        if new==COLLECTOR_IDS[1]:
            c["workload_bounds"]={"max_requests":1,"max_rows":1000,"max_bytes":16777216,"max_seconds":30}
        result.append(c)
    return result

def add_declarations(raw,project_root):
    rendered=(json.dumps(raw,ensure_ascii=True,indent=1,sort_keys=True)+"\n").encode()
    if raw["registry_version"]!="2.81.0" or hashlib.sha256(rendered).hexdigest()!=PREDECESSOR_SHA256:
        raise ValueError("Sharadar direct requires exact registry 2.81")
    for collector in declarations(raw):
        raw["collectors"].append(collector)
        for dataset in raw["datasets"]:
            if dataset["id"] in collector["output_datasets"]:dataset["collector_ids"].append(collector["id"])
    resource=Path(project_root)/MIGRATION_RESOURCE
    if not resource.exists():resource=Path(__file__).resolve().parents[2]/MIGRATION_RESOURCE
    raw["migrations"].insert(next(i for i,m in enumerate(raw["migrations"]) if m["store"]=="news"),{
        "id":MIGRATION_ID,"store":"company","ordinal":17,"dependencies":["company:0016_fmp_research_lookup_indexes"],
        "resource":MIGRATION_RESOURCE,"sha256":hashlib.sha256(resource.read_bytes()).hexdigest(),
        "reconstruction_state":"fixture_validated",
        "semantic_scope":"Accept native Sharadar direct evidence and pointers through a reviewed date-to-datekey identity bridge; preserve Nasdaq rows, keys, history and prior migration bytes; equal cross-channel rows reuse established versions and corrections append."})
    next(s for s in raw["stores"] if s["id"]=="company")["migration_order"].append(MIGRATION_ID)
    raw["registry_version"]=VERSION
