"""Scheduled missing-only Theta EOD publication to the fixed options store."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from quant_data.options.daily import run_scheduled, _safe_error

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("daily", "weekly"), required=True)
    args = parser.parse_args(argv)
    started = datetime.now(timezone.utc)
    try:
        result = run_scheduled(ROOT, args.mode)
    except Exception as exc:
        result = {"state": "stopped", "error": _safe_error(exc)}
    # Cached period results retain their original counts; do not attribute old
    # publication to a later invocation. Observation never changes collector flow.
    try:
        finished = datetime.fromisoformat(result["finished_at"])
        if finished >= started:
            from .fetch_run_summary import record_report
            record_report(f"quant-data-theta-options-{args.mode}.timer", result)
    except Exception:
        pass
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["state"] in ("complete", "not_due") else 2


if __name__ == "__main__":
    from .fetch_run_history import run_recorded_cli
    arguments = sys.argv[1:]
    batch = (f"quant-data-theta-options-{arguments[1]}.timer"
             if len(arguments) == 2 and arguments[0] == "--mode"
             and arguments[1] in {"daily", "weekly"} else "")
    raise SystemExit(run_recorded_cli(batch, main, argv=arguments))
