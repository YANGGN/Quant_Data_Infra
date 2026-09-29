"""Bounded normal-clock options monitor entry point."""
from __future__ import annotations
from pathlib import Path
import argparse,json
from quant_data.options.monitor.job import MonitorJob

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--project-root",type=Path,required=True,
                        help="Explicit WSL project root containing monitor config and private connection")
    args=parser.parse_args(argv)
    result=MonitorJob(args.project_root).run()
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0

if __name__=="__main__":raise SystemExit(main())
