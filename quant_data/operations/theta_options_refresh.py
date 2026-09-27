"""Scheduled missing-only Theta EOD publication to the fixed options store."""
import argparse
import json
from pathlib import Path
from quant_data.options.daily import run_scheduled, _safe_error

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "weekly"), required=True)
    args = parser.parse_args(argv)
    try:
        result = run_scheduled(ROOT, args.mode)
    except Exception as exc:
        result = {"state": "stopped", "error": _safe_error(exc)}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["state"] in ("complete", "not_due") else 2


if __name__ == "__main__":
    raise SystemExit(main())
