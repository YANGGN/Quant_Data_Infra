"""Read-only capture attribution and reviewed lifecycle labels for Inspector HTML.

The public retained-status tool stays unchanged. Only registered dataset IDs,
explicit output links and the reviewed native ledgers participate here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import sqlite3
import time
from typing import Any

from .data_status import (
    DataStatusSourceKind, _anchor_for, _capture_from_row,
    _status, registry_data_status_targets,
)
from .errors import QuantDataError, ValidationError
from .registry import Registry
from .stores import StoreMap, quiet_immutable_read_connection

_OFFICIAL = frozenset({"macro.official_vintages", "macro.official_vintages_evidence"})
_OLD_NEWS = frozenset({"news.fmp.stock_latest_articles", "news.fmp.stock_latest_evidence"})


def lifecycle_annotations() -> dict[str, dict[str, Any]]:
    """Explicit reviewed contracts; a fixture-prefixed ID is not itself a lifecycle."""
    groups = (
        (("fixture.market.catalog_evidence", "fixture.market.controlled_universes"),
         "fixture", "Offline catalog contract. Current operational rosters use Stage 10 storage.", "market.stage10.universes"),
        (("fixture.market.daily_price_evidence", "fixture.market.daily_prices"),
         "fixture", "Offline price contract. Current daily prices use Stage 10 storage.", "market.stage10.daily_prices"),
        (("market.fmp.daily_price_evidence", "market.fmp.daily_prices", "market.fmp.instruments"),
         "historical", "Earlier one-symbol candidate contract; its population was separate from this operational store.", "market.stage10.daily_prices"),
        (("fixture.macro.eia_retail", "fixture.macro.eia_retail_evidence"),
         "fixture", "Offline EIA contract. Current electricity data uses the Stage 11 history.", "macro.eia.electricity_retail_history"),
        (("fixture.macro.eia_weekly", "fixture.macro.eia_weekly_evidence"),
         "fixture", "Offline weekly-energy contract. Current EIA histories use separate operational relations.", "macro.eia.petroleum_weekly_stock_history"),
        (("fixture.macro.gdp_vintages",), "fixture",
         "Offline GDP contract. Official GDP vintages are retained separately.", "macro.official_vintages"),
        (("fixture.macro.recession_periods",), "fixture",
         "Offline recession-interval contract. Current NBER monthly chronology uses generic macro series; the interval contract is distinct.", "fixture.macro.rtdsm_employ"),
        (("fixture.news.evidence", "fixture.news.items", "fixture.news.search_index"),
         "fixture", "Offline news contract. Current headline feeds are separate from this older body/tag/search model.", "news.current_multi_source_evidence"),
        (tuple(sorted(_OLD_NEWS)), "legacy",
         "Frozen original one-shot news collector. Current news uses a separate repeatable feed.", "news.fmp.stock_latest_current_evidence"),
        (("fixture.market.instrument_classifications",), "planned",
         "Sector and industry coverage has a fixture importer, but no live collector is implemented.", None),
    )
    return {id: {"lifecycle": lifecycle, "lifecycle_note": note, "successor_id": successor}
            for ids, lifecycle, note, successor in groups for id in ids}


def _unknown(note: str) -> dict[str, Any]:
    return {"retention_state": "unknown", "retention_freshness": "unknown",
            "retained_capture_at": None, "retention_note": note}


def _capture_metadata(row: sqlite3.Row | None, *, target: Any, now: datetime,
                      native: bool = False) -> dict[str, Any]:
    if row is None:
        return {"retention_state": "missing", "retention_freshness": "no_data",
                "retained_capture_at": None,
                "retention_note": "No matching successful capture was found; this alone does not prove the underlying tables are empty."}
    capture = _capture_from_row(row)
    if capture is None or capture.instant > now:
        return _unknown("Capture metadata is invalid or later than the status observation time.")
    if native and row["membership_count"] != row["row_count"]:
        return _unknown("The native capture membership does not match its recorded observation count.")
    anchor = None if native else row["anchor_dataset_id"]
    basis = "native" if native else ("direct" if row["snapshot_dataset_id"] == target.dataset_id else "shared")
    freshness = _status(capture=capture, outcome=None, freshness=target.freshness,
                        malformed=False, now=now)[0]
    note = {"native": "Data is retained in the official-vintage capture ledger.",
            "shared": "Data is retained through a successful shared ingestion run.",
            "direct": "A successful capture is recorded directly for this dataset."}[basis]
    # A successful capture does not establish the latest attempt outcome.
    # Preserve the public outcome independently instead of replacing a later failure.
    return {"retention_state": "retained", "retention_basis": basis,
            "retained_capture_at": capture.primitive["captured_at"]["value"],
            "capture_anchor_id": anchor, "retention_note": note,
            "retention_freshness": freshness}


def _shared_capture_anchors(
    connection: sqlite3.Connection, dataset_ids: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    if not dataset_ids:
        return {}
    placeholders = ",".join("?" for _ in dataset_ids)
    rows = connection.execute(f"""
        SELECT DISTINCT output.dataset_id, run.dataset_id AS anchor_dataset_id
        FROM ingestion_run_outputs output
        CROSS JOIN ingestion_runs run ON run.run_id=output.run_id
        WHERE output.dataset_id IN ({placeholders}) AND run.status='succeeded'
          AND output.dataset_id<>run.dataset_id
        """, dataset_ids).fetchall()
    anchors: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        anchors[row["dataset_id"]].append(row["anchor_dataset_id"])
    return {dataset: tuple(values) for dataset, values in anchors.items()}


def _generic_capture(
    connection: sqlite3.Connection, dataset_id: str, *,
    shared_anchors: tuple[str, ...] | None = None,
) -> sqlite3.Row | None:
    candidate = connection.execute("""
        SELECT snapshot_id, run_id FROM ingestion_snapshots
        WHERE dataset_id=?
        ORDER BY captured_at DESC, snapshot_id COLLATE BINARY DESC LIMIT 1
        """, (dataset_id,)).fetchone()
    run = None if candidate is None else connection.execute(
        "SELECT status FROM ingestion_runs WHERE run_id=?", (candidate["run_id"],)
    ).fetchone()
    # A successful newest raw capture is also the direct branch's winner.
    # Bind its primary key to avoid joining all older runs on stores without
    # the company capture-time index. Otherwise keep the original full branch.
    direct_filter = ""
    parameters = (dataset_id,)
    if run is not None and run["status"] == "succeeded":
        direct_filter = " AND snapshot.snapshot_id=?"
        parameters = (dataset_id, candidate["snapshot_id"])
    direct = connection.execute(f"""
        SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
               snapshot.row_count, snapshot.completeness,
               snapshot.dataset_id AS snapshot_dataset_id,
               run.dataset_id AS anchor_dataset_id
        FROM ingestion_snapshots snapshot
        JOIN ingestion_runs run ON run.run_id=snapshot.run_id
        WHERE snapshot.dataset_id=? AND run.status='succeeded'{direct_filter}
        ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
        LIMIT 1
        """, parameters).fetchone()
    if shared_anchors is None:
        shared_anchors = _shared_capture_anchors(connection, (dataset_id,)).get(dataset_id, ())
    winners = [] if direct is None else [direct]
    # Rare historical output links can sit behind a large, newer capture history.
    # Probe at most 65 links through the dataset index. If there are at most 64,
    # start from those links; otherwise retain the capture-time seek. This only
    # selects a query plan: neither branch truncates eligible capture history.
    sparse_outputs = False
    if shared_anchors and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='ingestion_run_outputs_dataset' AND tbl_name='ingestion_run_outputs'"
    ).fetchone() is not None:
        sparse_outputs = connection.execute("""
            SELECT COUNT(*) FROM (
                SELECT 1 FROM ingestion_run_outputs WHERE dataset_id=? LIMIT 65
            )
            """, (dataset_id,)).fetchone()[0] <= 64
    # Output membership identifies possible anchors, not valid captures. Seek
    # each anchor's newest eligible snapshot with every original shared check
    # inside LIMIT, so an ineligible newer row cannot hide an older valid one.
    for anchor in shared_anchors:
        if sparse_outputs:
            shared = connection.execute("""
                SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
                       snapshot.row_count, snapshot.completeness,
                       snapshot.dataset_id AS snapshot_dataset_id,
                       run.dataset_id AS anchor_dataset_id
                FROM ingestion_run_outputs output
                CROSS JOIN ingestion_runs run ON run.run_id=output.run_id
                CROSS JOIN ingestion_snapshots snapshot ON snapshot.snapshot_id=run.snapshot_id
                WHERE output.dataset_id=? AND run.dataset_id=? AND run.status='succeeded'
                  AND snapshot.run_id=run.run_id
                  AND snapshot.dataset_id=run.dataset_id
                  AND output.dataset_id<>run.dataset_id
                ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
                LIMIT 1
                """, (dataset_id, anchor)).fetchone()
        else:
            shared = connection.execute("""
            SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
                   snapshot.row_count, snapshot.completeness,
                   snapshot.dataset_id AS snapshot_dataset_id,
                   run.dataset_id AS anchor_dataset_id
            FROM ingestion_snapshots snapshot
            CROSS JOIN ingestion_runs run ON run.run_id=snapshot.run_id
            CROSS JOIN ingestion_run_outputs output
              ON output.run_id=run.run_id AND output.dataset_id=?
            WHERE snapshot.dataset_id=? AND run.status='succeeded'
              AND run.snapshot_id=snapshot.snapshot_id
              AND snapshot.dataset_id=run.dataset_id
              AND output.dataset_id<>run.dataset_id
            ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
            LIMIT 1
                """, (dataset_id, anchor)).fetchone()
        if shared is not None:
            winners.append(shared)
    if len(winners) < 2:
        return winners[0] if winners else None
    # Select only among branch winners, retaining SQLite's raw-value ordering
    # and binary ID tie-break even when winning metadata is malformed.
    columns = ("snapshot_id", "captured_at", "captured_precision", "row_count",
               "completeness", "snapshot_dataset_id", "anchor_dataset_id")
    branch = "SELECT " + ",".join(f"? AS {column}" for column in columns)
    return connection.execute(
        "SELECT * FROM (" + " UNION ALL ".join(branch for _ in winners)
        + ") ORDER BY captured_at DESC,snapshot_id COLLATE BINARY DESC LIMIT 1",
        tuple(row[column] for row in winners for column in columns),
    ).fetchone()


def _official_capture(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute("""
        SELECT capture.capture_id AS snapshot_id, capture.captured_at,
               capture.captured_precision, capture.observation_count AS row_count,
               'complete' AS completeness,
               (SELECT COUNT(*) FROM macro_live_vintage_capture_membership membership
                JOIN macro_live_vintage_observation_versions version
                  ON version.version_id=membership.version_id
                 AND version.series_id=membership.series_id
                WHERE membership.capture_id=capture.capture_id) AS membership_count
        FROM macro_live_vintage_captures capture
        ORDER BY capture.captured_at DESC, capture.capture_id COLLATE BINARY DESC
        LIMIT 1
        """).fetchone()


def _old_news_outcome(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("""SELECT outcome_kind, recorded_at, http_status
        FROM fmp_stock_latest_outcomes ORDER BY recorded_at DESC, outcome_id DESC LIMIT 1""").fetchone()
    if row is None:
        return {}
    kind = row["outcome_kind"]
    if kind not in {"succeeded", "request_failed", "response_rejected", "publication_failed"}:
        return {}
    labels = {"succeeded": "succeeded", "request_failed": "request failed",
              "response_rejected": "response rejected", "publication_failed": "publication failed"}
    return {"retained_outcome": kind,
            "retention_note": "Frozen collector's recorded outcome: " + labels[kind] + ". Current news is tracked separately."}


def read_inspector_retention(stores: StoreMap, registry: Registry, *,
                             observed_at: datetime | None = None) -> dict[str, dict[str, Any]]:
    now = observed_at or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("Inspector observation time requires an offset")
    now = now.astimezone(timezone.utc)
    groups: dict[Any, list[Any]] = defaultdict(list)
    for target in registry_data_status_targets(registry):
        if target.source_kind is DataStatusSourceKind.CONTROL_PLANE:
            groups[target.store].append(target)
    annotations = lifecycle_annotations()
    result: dict[str, dict[str, Any]] = {}
    for role, targets in groups.items():
        resolved: dict[str, dict[str, Any]] = {}
        try:
            with quiet_immutable_read_connection(stores, role, expected_anchor=_anchor_for(registry, role)) as connection:
                deadline = time.monotonic() + 3.0
                connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                try:
                    anchors = _shared_capture_anchors(connection, tuple(
                        target.dataset_id for target in targets if target.id not in _OFFICIAL
                    ))
                except sqlite3.Error:
                    anchors = None
                for target in targets:
                    try:
                        native = target.id in _OFFICIAL
                        row = _official_capture(connection) if native else _generic_capture(
                            connection, target.dataset_id,
                            shared_anchors=None if anchors is None else anchors.get(target.dataset_id, ()),
                        )
                        resolved[target.id] = _capture_metadata(row, target=target, now=now, native=native)
                        if target.id in _OLD_NEWS:
                            resolved[target.id].update(_old_news_outcome(connection))
                    except sqlite3.Error:
                        resolved[target.id] = _unknown("Capture records are unavailable for this dataset.")
        except QuantDataError:
            # Never retain a partial result from a store whose identity changed.
            resolved = {target.id: _unknown("The store is busy or unavailable; capture status could not be verified.") for target in targets}
        for target in targets:
            result[target.id] = {"lifecycle": "active", **resolved[target.id], **annotations.get(target.id, {})}
    return result
