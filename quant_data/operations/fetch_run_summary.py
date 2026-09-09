"""Closed, numeric summaries for private fetch-run status; no provider or store I/O."""
from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from typing import Any

_COUNT_KEYS = {"unit", "successful", "failed", "partial", "skipped", "unattempted"}
_UNITS = {"sources", "issuers", "etfs", "symbols", "steps"}
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
    if batch_id == "quant-data-sec-company-fundamentals.timer":
        return _counts("issuers", report["succeeded_cik_count"], report["failed_cik_count"],
                       skipped=report["skipped_existing_cik_count"])
    if batch_id == "quant-data-alpaca-spy-options.timer":
        completed, failed = report["completed_underlyings"], report["failed_underlyings"]
        if not isinstance(completed, list) or not isinstance(failed, list):
            raise ValueError("Invalid ETF counts")
        return _counts("etfs", len(completed), len(failed))
    if batch_id == "quant-data-market-close.timer":
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
