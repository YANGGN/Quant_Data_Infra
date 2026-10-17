"""Manual SPY-only options history; no scheduler, caller database path or SQL."""
from pathlib import Path
import json
from quant_data.options.job import SpyHistoryJob
ROOT=Path(__file__).resolve().parents[2]

def main():
    try:
        result=SpyHistoryJob(ROOT).run()
        print(json.dumps(result,sort_keys=True,allow_nan=False))
        return 0 if result["state"]=="completed" else 2
    except Exception as exc:
        print(json.dumps({"state":"stopped","error_type":type(exc).__name__,
                          "status_file":".local/theta-spy-20260924/status.json"}))
        return 1

if __name__=="__main__":
    raise SystemExit(main())
