"""Closed, numeric summaries for private fetch-run status; no provider or store I/O."""
from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from typing import Any

_COUNT_KEYS = {"unit", "successful", "failed", "partial", "skipped", "unattempted"}
_UNITS = {"sources", "issuers", "etfs", "symbols", "steps", "symbol_sessions"}
_CAPTURE: ContextVar[tuple[str, list[dict[str, Any]]] | None] = ContextVar("fetch_summary", default=None)


def validate_counts(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _COUNT_KEYS or value["unit"] not in _UNITS:
        raise ValueError("Invalid fetch counts")
    if any(type(value[key]) is not int or not 0 <= value[key] <= 1_000_000
           for key in _COUNT_KEYS - {"unit"}):
        raise ValueError("Invalid fetch count")
    if sum(value[key] for key in _COUNT_KEYS - {"unit"}) > 1_000_000:
        raise ValueError("Fetch total exceeds its bound")
    return dict(value)


def _counts(unit: str, successful: int, failed: int, *, partial: int = 0,
            skipped: int = 0, unattempted: int = 0) -> dict[str, Any]:
    return validate_counts(dict(unit=unit, successful=successful, failed=failed,
                               partial=partial, skipped=skipped, unattempted=unattempted))


def summarize_report(batch_id: str, report: object) -> dict[str, Any]:
    """Select numeric fields only; never retain symbols, URLs or exception text."""
    if not isinstance(report, dict):
        raise ValueError("Invalid fetch report")
    if batch_id in {"quant-data-theta-options-daily.timer", "quant-data-theta-options-weekly.timer"}:
        mode = "daily" if batch_id.endswith("-daily.timer") else "weekly"
        maximum = 15 if mode == "daily" else 75
        if report.get("contract") != "theta_daily_and_weekly_v1" or report.get("mode") != mode:
            raise ValueError("Invalid Theta report identity")
        state = report.get("state")
        expected, missing = report.get("expected_sessions"), report.get("initially_missing")
        published, preserved = report.get("published"), report.get("preserved")
        if (state not in {"complete", "complete_with_gaps", "stopped"}
                or any(type(value) is not int or not 0 <= value <= maximum
                       for value in (expected, missing, published, preserved))
                or missing > expected or published + preserved > missing):
            raise ValueError("Theta work counts are unavailable")
        gaps = report.get("unresolved") if state != "stopped" else report.get("gaps")
        if not isinstance(gaps, list) or len(gaps) > missing - published - preserved:
            raise ValueError("Invalid Theta gaps")
        if state != "stopped":
            if (state == "complete") != (len(gaps) == 0):
                raise ValueError("Contradictory Theta completion")
            # A concurrent publisher can fill an initially missing session.
            return _counts("symbol_sessions", published, len(gaps),
                           skipped=expected - published - len(gaps))
        return _counts("symbol_sessions", published, len(gaps),
                       skipped=expected - missing + preserved,
                       unattempted=missing - published - preserved - len(gaps))
    if batch_id == "quant-data-sharadar-selected-refresh.timer":
        details = report["details"]
        completed = details["completed_partitions"]
        failed = int(details["http_status"] is not None)
        return _counts("steps", completed, failed,
            unattempted=max(0, details["total_partitions"] - completed - failed))
    if batch_id == "quant-data-derived-refresh.timer":
        outcomes=report["outcomes"]
        return _counts("symbols",outcomes.get("current",0),outcomes.get("waiting_inputs",0),
                       partial=outcomes.get("stale_inputs",0)+outcomes.get("catchup_pending",0))
    if batch_id in {"quant-data-macro-vintages.timer", "quant-data-employment-vintages.timer",
                    "quant-data-fmp-macro-calendar.timer"}:
        published, unchanged = report["published"], report["unchanged"]
        if type(published) is not int or type(unchanged) is not int or min(published, unchanged) < 0:
            raise ValueError("Invalid publication counts")
        successful = published + unchanged
        if batch_id == "quant-data-fmp-macro-calendar.timer":
            outcomes = [report["employment_outcome"], report["wholesale_outcome"]]
            if any(outcome not in {"published", "unchanged"} for outcome in outcomes):
                raise ValueError("Invalid calendar outcomes")
            successful += len(outcomes)
        return _counts("steps" if batch_id == "quant-data-fmp-macro-calendar.timer" else "sources",
                       successful, 0)
    if batch_id == "quant-data-company-market-refresh.timer" and report.get("contract") == "quant_data.company_market_refresh.v2":
        return validate_counts(report.get("counts"))
    if batch_id in ("quant-data-selected-company-refresh.timer", "quant-data-equibles-refresh.timer"):
        return validate_counts(report.get("counts"))
    if batch_id == "quant-data-sec-company-fundamentals.timer":
        return _counts("issuers", report["succeeded_cik_count"], report["failed_cik_count"],
                       skipped=report["skipped_existing_cik_count"],
                       partial=report.get("partial_cik_count", 0),
                       unattempted=report.get("unattempted_cik_count", 0))
    if batch_id == "quant-data-alpaca-spy-options.timer":
        completed, failed = report["completed_underlyings"], report["failed_underlyings"]
        if not isinstance(completed, list) or not isinstance(failed, list):
            raise ValueError("Invalid ETF counts")
        return _counts("etfs", len(completed), len(failed))
    if batch_id == "quant-data-weekly-price-repair.timer":
        return validate_counts(report.get("symbol_counts"))
    if batch_id == "quant-data-market-close.timer":
        if report.get("contract") == "quant_data.selected_price_refresh.v1":
            counts = validate_counts(report.get("symbol_counts"))
            planned = report.get("planned_requests")
            if (counts["unit"] != "symbols" or type(planned) is not int or not 1 <= planned <= 6600
                or sum(value for key,value in counts.items() if key != "unit") != planned):
                raise ValueError("Invalid selected daily-price counts")
            return counts
        results = report["results"]
        if not isinstance(results, list) or len(results) > 800:
            raise ValueError("Invalid market results")
        counts = Counter(item["outcome"] for item in results)
        if set(counts) - {"published", "failed_response", "terminal_noncoverage", "no_market_session"}:
            raise ValueError("Invalid market outcomes")
        return _counts("symbols", counts["published"], counts["failed_response"],
                       skipped=counts["terminal_noncoverage"] + counts["no_market_session"])
    if batch_id in {"quant-data-macro-current-refresh.timer", "quant-data-current-news-refresh.timer",
                    "quant-data-company-market-refresh.timer"}:
        steps = report["steps"]
        if not isinstance(steps, list) or len(steps) > 3000:
            raise ValueError("Invalid source steps")
        counts = Counter(item["outcome"] for item in steps)
        if set(counts) - {"succeeded", "published", "unchanged", "failed", "unavailable", "partial", "skipped"}:
            raise ValueError("Invalid source outcomes")
        return _counts("steps" if "company-market" in batch_id else "sources",
                       counts["succeeded"] + counts["published"] + counts["unchanged"],
                       counts["failed"] + counts["unavailable"], partial=counts["partial"],
                       skipped=counts["skipped"])
    raise ValueError("Unsupported summary batch")


def record_report(batch_id: str, report: object) -> None:
    """Best-effort observer scoped to run_recorded_cli; never change collector flow."""
    capture = _CAPTURE.get()
    if capture is None or capture[0] != batch_id:
        return
    try:
        summary = summarize_report(batch_id, report)
    except Exception:
        return
    capture[1][:] = [summary]
