"""Read-only SPY close references through the versioned public price tool."""
import gzip,hashlib,json,subprocess
from pathlib import Path

def prepare_references(root,work,*,symbol="SPY",first_year=2016):
    root,work=Path(root),Path(work)
    work.mkdir(parents=True,exist_ok=True)
    index={}
    for year in range(first_year,2027):
        artifact=work/f"{symbol.lower()}-price-reference-{year}.json.gz"
        if artifact.exists():
            with gzip.open(artifact,"rt") as f:result=json.load(f)
        else:
            request={"api_version":"1.0","tool":"market.get_price_series","tool_version":"2.0.0",
                "arguments":{"ticker":symbol,"mode":"latest","as_of":None,
                "date_only_policy":"completed_date","start_date":f"{year}-01-01",
                "end_date":min(f"{year}-12-31","2026-09-23"),"limit":1000}}
            proc=subprocess.run([str(root/"bin/quant-data-tools"),"call"],
                input=json.dumps(request),capture_output=True,text=True,timeout=60,cwd=root)
            if proc.returncode:raise RuntimeError("local_etf_price_reader_failed")
            result=json.loads(proc.stdout)
            if result["receipt"]["outcome"]!="succeeded" or result["receipt"]["truncation"]["applied"]:
                raise RuntimeError("local_etf_price_reference_incomplete")
            with gzip.open(artifact,"wt") as f:json.dump(result,f,allow_nan=False)
        digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
        for series in result["result"]["series"]:
            if series["audit"]["observation_field"]!="close":continue
            if not any(obs["missing_reason"] is None and obs["value"] is not None for obs in series["observations"]):continue
            metadata=series["metadata"]
            if metadata.get("provider_symbol")!=symbol or metadata.get("adjustment_status")!="split_adjusted_excluding_distributions":
                raise RuntimeError("local_etf_price_basis")
            for obs in series["observations"]:
                if obs["missing_reason"] is not None or obs["value"] is None:continue
                index[obs["period_start"]]={"value":str(obs["value"]),"observation":obs,
                    "metadata":metadata,"artifact":artifact.name,"artifact_sha256":digest,
                    "tool":"market.get_price_series","tool_version":"2.0.0",
                    "registry_revision":result["receipt"]["registry_revision"]}
    (work/"spot-references.json").write_text(json.dumps(index,sort_keys=True))
    return index


def prepare_spy_references(root,work):
    return prepare_references(root,work,symbol="SPY")
