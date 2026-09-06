"""Fixed, read-only presentation metadata for the Inspector's affected macro feeds.

Source dates never fall back to capture timestamps. Operational fetch receipts
are distinct from versioned canonical evidence and the public status tool.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
import sqlite3

from .errors import QuantDataError
from .stores import StoreMap
from .registry import Registry
from .inspector_retention import lifecycle_annotations, read_inspector_retention
from .operations.refresh_status import read_refresh_status

_CALENDAR = (
    "fixture.macro.economic_calendar",
    "macro.fmp.economic_calendar_incremental_evidence",
    "macro.fmp.economic_calendar_incremental_events",
)
_SOMA = ("fixture.macro.soma_summary", "fixture.macro.soma_evidence")
_PETROLEUM = (
    "macro.eia.petroleum_weekly_stock_history",
    "macro.eia.petroleum_weekly_stock_history_evidence",
)
_NIPA = ("macro.bea.nipa_history", "macro.bea.nipa_history_evidence")
_LEGACY = ("macro.fmp.economic_calendar_evidence",)


def _receipt_metadata(root: Path, source: str) -> dict[str, Any]:
    receipt = read_refresh_status(root, source=source)
    attempt = receipt.get("last_attempt", {})
    outcome = attempt.get("outcome", "unknown")
    notes = {
        "published": "Latest tracked refresh stored new or changed data.",
        "unchanged": "Latest tracked refresh succeeded; source data was unchanged.",
        "succeeded": "Latest tracked collector completed successfully; change count was not provided.",
        "failed": "Latest tracked refresh failed. Any prior successful fetch is shown separately.",
        "unknown": "Earlier fetch attempts, including unchanged polls, were not recorded.",
    }
    return {
        "latest_successful_fetch_at": receipt.get("last_successful_fetch_at"),
        "latest_attempt_at": attempt.get("completed_at"),
        "refresh_state": outcome,
        "refresh_note": notes.get(outcome, notes["unknown"]),
    }


def read_inspector_status(
    stores: StoreMap, *, operations_root: Path, observed_at: datetime | None = None,
    registry: Registry | None = None,
) -> dict[str, dict[str, Any]]:
    # Import locally so the application can supply this optional HTML overlay
    # without creating a second store-opening implementation.
    from .canonical_inspector import _immutable_store_connection

    today = (observed_at or datetime.now(timezone.utc)).date()
    result = (read_inspector_retention(stores, registry, observed_at=observed_at)
              if registry is not None else lifecycle_annotations())
    for ids, source in ((_SOMA, "nyfed_soma"), (_PETROLEUM, "eia_petroleum_weekly_stock"),
                        ((_CALENDAR[0],), "fmp_macro_calendar"),
                        (_CALENDAR[1:], "fmp_calendar_raw")):
        receipt = _receipt_metadata(operations_root, source)
        for dataset_id in ids:
            result.setdefault(dataset_id, {}).update({"lifecycle": "active", **receipt})
    for dataset_id in _CALENDAR:
        result[dataset_id].update(
            as_of_note="Event dates describe scheduled releases, not a dataset as-of date.",
        )
    for dataset_id in _SOMA:
        result[dataset_id].update(
            source_frequency="Weekly source updates",
            as_of_note="Holdings date. NY Fed normally publishes Wednesday holdings on Thursday; weekday polls may be unchanged.",
        )
    for dataset_id in _PETROLEUM:
        result[dataset_id].update(
            source_frequency="Weekly source updates",
            as_of_note="Week-ending observation date. EIA releases weekly, with holiday shifts; weekday polls may be unchanged.",
        )
    for dataset_id in _NIPA:
        result.setdefault(dataset_id, {}).update({
            "lifecycle": "historical",
            "lifecycle_note": "Retained GDP history snapshot (T10101 / T10105), without a recurring collector. Current GDP vintages are tracked separately.",
            "successor_id": "macro.official_vintages",
            "as_of_note": "Latest retained source quarter; this historical snapshot is not refreshed on a recurring schedule.",
        })
    for dataset_id in _LEGACY:
        result.setdefault(dataset_id, {}).update({
            "lifecycle": "legacy",
            "lifecycle_note": "Frozen wholesale calendar evidence. New polls use compact incremental evidence; this retained history is not refreshed.",
            "successor_id": "macro.fmp.economic_calendar_incremental_evidence",
            "as_of_note": "Historical event evidence has no single observation as-of date.",
        })
    dates: dict[str, str | None] = {}
    try:
        with _immutable_store_connection(stores.macro, expected_role="macro") as connection:
            # Fixed SQL only. No browser input or caller-selected relation is used.
            for name, sql, parameters in (
                ("soma", """SELECT snapshot.as_of_date FROM soma_snapshots snapshot
                    JOIN ingestion_runs run ON run.run_id=snapshot.run_id
                    WHERE snapshot.completeness='complete' AND run.status='succeeded'
                    ORDER BY snapshot.as_of_date DESC LIMIT 1""", ()),
                ("petroleum", """SELECT version.period
                    FROM stage11_eia_weekly_observations observation
                    JOIN stage11_eia_weekly_observation_versions version
                    ON version.version_id=observation.current_version_id
                    WHERE version.canonical_series_id=? ORDER BY version.period DESC LIMIT 1""",
                    ("macro.eia.weekly.petroleum_stock",)),
                ("nipa", """SELECT version.period
                    FROM stage11_bea_nipa_observations observation
                    JOIN stage11_bea_nipa_observation_versions version
                    ON version.version_id=observation.current_version_id
                    WHERE version.canonical_series_id IN (?, ?)
                    ORDER BY version.period DESC LIMIT 1""",
                    ("macro.gdp.real_qoq_saar_pct", "macro.gdp.nominal_billions")),
            ):
                try:
                    row = connection.execute(sql, parameters).fetchone()
                    dates[name] = str(row[0]) if row and row[0] else None
                except sqlite3.Error:
                    dates[name] = None
    except QuantDataError:
        # A busy or changed store yields no projected date, never a stale partial read.
        dates = {}
    for ids, name in ((_SOMA, "soma"), (_PETROLEUM, "petroleum"), (_NIPA, "nipa")):
        value = dates.get(name)
        for dataset_id in ids:
            result[dataset_id]["as_of_date"] = value
            if name != "nipa" and value:
                try:
                    overdue = (today - date.fromisoformat(value)).days > 14
                except ValueError:
                    overdue = False
                result[dataset_id]["source_overdue"] = overdue
                if overdue:
                    result[dataset_id]["refresh_note"] += " The source date is over 14 days old; weekly cadence alone does not explain this age."
    return result
