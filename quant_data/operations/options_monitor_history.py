"""Finite export of existing ETF daily facts; never calls a market-data provider."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,time
from quant_data.options.monitor.history import sync_history
from quant_data.options.monitor.site import SiteClient,connection

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--project-root",required=True,type=Path)
    parser.add_argument("--batches",required=True,type=int,choices=range(1,81))
    parser.add_argument("--through-capture-id",required=True,type=int)
    args=parser.parse_args(argv)
    if args.through_capture_id<1:parser.error("positive source capture bound required")
    site=SiteClient(connection(args.project_root/"data/.operations/options-monitor/connection.json"))
    result=sync_history(args.project_root,site,datetime.now(timezone.utc),max_batches=args.batches,
        through=args.through_capture_id,deadline=time.monotonic()+1800,
        progress=lambda value:print(json.dumps(value),flush=True))
    print(json.dumps(result),flush=True)
    return 0 if result["status"]=="up_to_date" or result["last_capture_id"]>=args.through_capture_id else 2
if __name__=="__main__":raise SystemExit(main())
