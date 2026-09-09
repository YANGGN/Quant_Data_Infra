"""Bounded, read-only retained-data freshness status.

This module is deliberately narrower than operational health monitoring.  It
uses only host-routed SQLite reads and reports facts that are already retained
in canonical stores: successful captures, retained outcomes, and (for an
explicit configured macro series) the latest retained reference period.  A
``current`` result therefore never asserts that a provider, credential,
scheduler, or process is presently healthy.

The public-facing adapter owns its own route and contract.  This core accepts
only a host-created ``StoreMap`` and in-code ``DataStatusTarget`` values; it
never accepts a database path, relation, SQL statement, provider request, or
credential.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .errors import QuantDataError, ValidationError
from .stores import StoreMap, StoreRole, quiet_immutable_read_connection
from .temporal import TemporalPrecision, TemporalValue, parse_date

if TYPE_CHECKING:
    from .registry import DatasetDeclaration, Registry


MAX_STATUS_RECORDS = 128
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_DURATION = re.compile(r"^P(?:0|[1-9][0-9]*)D$")
_CADENCES = frozenset(
    {"intraday", "daily", "weekly", "monthly", "event_driven", "manual"}
)
_MEASURED_FROM = frozenset(
    {"source_period", "source_published_at", "available_at", "successful_capture"}
)


class DataStatusSourceKind(StrEnum):
    """Fixed retained-state reader selected by host configuration."""

    CONTROL_PLANE = "control_plane"
    CURRENT_NEWS = "current_news"


class ReferencePeriodKind(StrEnum):
    """Fixed reference-period projections; arbitrary relations are impossible."""

    NONE = "none"
    MACRO_CURRENT_SERIES = "macro_current_series"
    STAGE11_BEA_NIPA_SERIES = "stage11_bea_nipa_series"
    STAGE11_EIA_RETAIL_SERIES = "stage11_eia_retail_series"
    STAGE11_EIA_WEEKLY_SERIES = "stage11_eia_weekly_series"


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """Optional declarative freshness fields copied from the validated registry."""

    cadence: str | None = None
    expected_lag: str | None = None
    stale_after: str | None = None
    measured_from: str | None = None

    def __post_init__(self) -> None:
        if self.cadence is not None and self.cadence not in _CADENCES:
            raise ValidationError("Data status cadence is invalid")
        for value, field in (
            (self.expected_lag, "expected lag"),
            (self.stale_after, "stale threshold"),
        ):
            if value is not None and (
                not isinstance(value, str) or _DURATION.fullmatch(value) is None
            ):
                raise ValidationError(f"Data status {field} is invalid")
        if self.measured_from is not None and self.measured_from not in _MEASURED_FROM:
            raise ValidationError("Data status freshness basis is invalid")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FreshnessPolicy":
        if not isinstance(value, Mapping):
            raise ValidationError("Data status freshness policy is invalid")
        return cls(
            cadence=_optional_text(value.get("cadence"), "cadence"),
            expected_lag=_optional_text(value.get("expected_lag"), "expected lag"),
            stale_after=_optional_text(value.get("stale_after"), "stale threshold"),
            measured_from=_optional_text(value.get("measured_from"), "freshness basis"),
        )

    def to_primitive(self) -> dict[str, str] | None:
        result = {
            key: value
            for key, value in (
                ("cadence", self.cadence),
                ("expected_lag", self.expected_lag),
                ("stale_after", self.stale_after),
                ("measured_from", self.measured_from),
            )
            if value is not None
        }
        return result or None


@dataclass(frozen=True, slots=True)
class DataStatusTarget:
    """One host-configured retained-data status record.

    ``dataset_id`` identifies a registered dataset when control-plane evidence
    is available.  ``source_id`` is used only by the fixed current-news reader.
    ``series_id`` is allowed only with an enumerated macro reference projection.
    None of these fields is constructed from browser or tool-call input.
    """

    id: str
    store: StoreRole
    dataset_id: str | None
    freshness: FreshnessPolicy | None = None
    source_kind: DataStatusSourceKind = DataStatusSourceKind.CONTROL_PLANE
    source_id: str | None = None
    reference_kind: ReferencePeriodKind = ReferencePeriodKind.NONE
    series_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "target id")
        try:
            normalized_store = StoreRole(self.store)
            normalized_source_kind = DataStatusSourceKind(self.source_kind)
            normalized_reference_kind = ReferencePeriodKind(self.reference_kind)
        except ValueError as exc:
            raise ValidationError("Data status target selector is invalid") from exc
        object.__setattr__(self, "store", normalized_store)
        object.__setattr__(self, "source_kind", normalized_source_kind)
        object.__setattr__(self, "reference_kind", normalized_reference_kind)
        if self.dataset_id is not None:
            _require_identifier(self.dataset_id, "dataset id")
        if self.source_id is not None:
            _require_identifier(self.source_id, "source id")
        if self.series_id is not None:
            _require_identifier(self.series_id, "series id")
        if self.dataset_id is None and self.source_id is None:
            raise ValidationError("Data status target requires a dataset or source")
        if normalized_source_kind is DataStatusSourceKind.CURRENT_NEWS:
            if normalized_store is not StoreRole.NEWS or self.source_id is None:
                raise ValidationError("Current-news status target is invalid")
        elif self.source_id is not None:
            raise ValidationError("Control-plane status target cannot declare a source")
        if normalized_reference_kind is ReferencePeriodKind.NONE:
            if self.series_id is not None:
                raise ValidationError("Data status series requires a reference projection")
        elif normalized_store is not StoreRole.MACRO or self.series_id is None:
            raise ValidationError("Macro reference status target is invalid")


@dataclass(frozen=True, slots=True)
class _Capture:
    primitive: Mapping[str, object]
    instant: datetime


@dataclass(frozen=True, slots=True)
class _Reference:
    primitive: Mapping[str, object]
    capture: _Capture | None


@dataclass(frozen=True, slots=True)
class _ControlPlaneState:
    capture: _Capture | None
    outcome: Mapping[str, object] | None
    malformed: bool


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Data status {field} is invalid")
    return value


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValidationError(f"Data status {field} is invalid")
    return value


def _duration_days(value: str) -> int:
    match = _DURATION.fullmatch(value)
    if match is None:  # FreshnessPolicy already rejects this; keep fail-closed.
        raise ValidationError("Data status duration is invalid")
    return int(value[1:-1])


def _timestamp(value: object, precision: object) -> tuple[dict[str, str], datetime] | None:
    if not isinstance(value, str) or not isinstance(precision, str):
        return None
    try:
        parsed = TemporalValue.parse(value, pointer="/retained_timestamp")
    except ValidationError:
        return None
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or precision != TemporalPrecision.DATETIME.value
        or not isinstance(parsed.value, datetime)
    ):
        return None
    return (
        {"value": value, "precision": TemporalPrecision.DATETIME.value},
        parsed.value.astimezone(timezone.utc),
    )


def _row_value(row: Mapping[str, object] | sqlite3.Row, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _capture_from_row(row: Mapping[str, object] | sqlite3.Row) -> _Capture | None:
    captured = _timestamp(
        _row_value(row, "captured_at"), _row_value(row, "captured_precision")
    )
    if captured is None:
        return None
    timestamp, instant = captured
    snapshot_id = _row_value(row, "snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return None
    row_count = _row_value(row, "row_count")
    completeness = _row_value(row, "completeness")
    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
        or not isinstance(completeness, str)
        or not completeness
    ):
        return None
    return _Capture(
        primitive={
            "snapshot_id": snapshot_id,
            "captured_at": timestamp,
            "row_count": row_count,
            "completeness": completeness,
        },
        instant=instant,
    )


def _outcome_from_row(row: Mapping[str, object] | sqlite3.Row) -> Mapping[str, object] | None:
    run_id = _row_value(row, "run_id")
    outcome_kind = _row_value(row, "outcome_kind")
    recorded = _timestamp(
        _row_value(row, "recorded_at"), _row_value(row, "recorded_precision")
    )
    fetched_count = _row_value(row, "fetched_count")
    written_count = _row_value(row, "written_count")
    if (
        not isinstance(run_id, str)
        or not run_id
        or outcome_kind not in {"succeeded", "failed", "rejected", "partial"}
        or recorded is None
        or isinstance(fetched_count, bool)
        or not isinstance(fetched_count, int)
        or fetched_count < 0
        or (
            written_count is not None
            and (isinstance(written_count, bool) or not isinstance(written_count, int))
        )
    ):
        return None
    return {
        "run_id": run_id,
        "outcome_kind": outcome_kind,
        "recorded_at": recorded[0],
        "fetched_count": fetched_count,
        "written_count": written_count,
    }


def _control_plane_state(
    connection: sqlite3.Connection,
    dataset_id: str | None,
) -> _ControlPlaneState:
    if dataset_id is None:
        return _ControlPlaneState(capture=None, outcome=None, malformed=False)
    capture_row = connection.execute(
        """
        SELECT snapshot.snapshot_id, snapshot.captured_at, snapshot.captured_precision,
               snapshot.row_count, snapshot.completeness
        FROM ingestion_snapshots AS snapshot
        JOIN ingestion_runs AS run ON run.run_id = snapshot.run_id
        WHERE snapshot.dataset_id = ? AND run.status = 'succeeded'
        ORDER BY snapshot.captured_at DESC, snapshot.snapshot_id COLLATE BINARY DESC
        LIMIT 1
        """,
        (dataset_id,),
    ).fetchone()
    outcome_row = connection.execute(
        """
        SELECT run_id, outcome_kind, recorded_at, recorded_precision,
               fetched_count, written_count
        FROM (
            SELECT run_id, 'succeeded' AS outcome_kind,
                   completed_at AS recorded_at, 'datetime' AS recorded_precision,
                   fetched_count, written_count
            FROM ingestion_runs
            WHERE dataset_id = ?
              AND status = 'succeeded'
              AND completed_at IS NOT NULL
            UNION ALL
            SELECT run_id, status AS outcome_kind,
                   completed_at AS recorded_at, 'datetime' AS recorded_precision,
                   fetched_count, NULL AS written_count
            FROM ingestion_run_failures
            WHERE dataset_id = ?
        )
        ORDER BY recorded_at DESC, run_id COLLATE BINARY DESC
        LIMIT 1
        """,
        (dataset_id, dataset_id),
    ).fetchone()
    if capture_row is None:
        capture = None
        malformed_capture = False
    else:
        capture = _capture_from_row(capture_row)
        malformed_capture = capture is None
    if outcome_row is None:
        outcome = None
        malformed_outcome = False
    else:
        outcome = _outcome_from_row(outcome_row)
        malformed_outcome = outcome is None
    return _ControlPlaneState(
        capture=capture,
        outcome=outcome,
        malformed=malformed_capture or malformed_outcome,
    )


def _reference_from_row(row: Mapping[str, object] | sqlite3.Row) -> _Reference | None:
    start = _row_value(row, "period_start")
    end = _row_value(row, "period_end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        start_date = parse_date(start, pointer="/latest_reference_period/period_start")
        end_date = parse_date(end, pointer="/latest_reference_period/period_end")
    except ValidationError:
        return None
    if end_date < start_date:
        return None
    capture_timestamp = _timestamp(
        _row_value(row, "captured_at"), _row_value(row, "captured_precision")
    )
    capture = (
        _Capture(
            primitive={
                "captured_at": capture_timestamp[0],
                "basis": "series_observation_capture",
            },
            instant=capture_timestamp[1],
        )
        if capture_timestamp is not None
        else None
    )
    return _Reference(
        primitive={"period_start": start, "period_end": end, "precision": "date"},
        capture=capture,
    )


def _macro_reference(
    connection: sqlite3.Connection,
    target: DataStatusTarget,
) -> _Reference | None:
    assert target.series_id is not None
    queries: Mapping[ReferencePeriodKind, str] = {
        ReferencePeriodKind.MACRO_CURRENT_SERIES: """
            SELECT observation.period_start, observation.period_end,
                   version.captured_at, version.captured_precision
            FROM macro_observations AS observation
            JOIN macro_observation_versions AS version
              ON version.version_id = observation.current_version_id
            WHERE observation.series_id = ?
            ORDER BY observation.period_end DESC, observation.period_start DESC,
                     version.version_id COLLATE BINARY DESC
            LIMIT 1
        """,
        ReferencePeriodKind.STAGE11_BEA_NIPA_SERIES: """
            SELECT observation.period AS period_start, observation.period AS period_end,
                   version.captured_at, version.captured_precision
            FROM stage11_bea_nipa_observations AS observation
            JOIN stage11_bea_nipa_observation_versions AS version
              ON version.version_id = observation.current_version_id
            WHERE observation.canonical_series_id = ?
            ORDER BY observation.period DESC, version.version_id COLLATE BINARY DESC
            LIMIT 1
        """,
        ReferencePeriodKind.STAGE11_EIA_RETAIL_SERIES: """
            SELECT observation.period || '-01' AS period_start,
                   observation.period || '-01' AS period_end,
                   version.captured_at, version.captured_precision
            FROM stage11_eia_retail_observations AS observation
            JOIN stage11_eia_retail_observation_versions AS version
              ON version.version_id = observation.current_version_id
            WHERE observation.canonical_series_id = ?
            ORDER BY observation.period DESC, version.version_id COLLATE BINARY DESC
            LIMIT 1
        """,
        ReferencePeriodKind.STAGE11_EIA_WEEKLY_SERIES: """
            SELECT observation.period AS period_start, observation.period AS period_end,
                   version.captured_at, version.captured_precision
            FROM stage11_eia_weekly_observations AS observation
            JOIN stage11_eia_weekly_observation_versions AS version
              ON version.version_id = observation.current_version_id
            WHERE observation.canonical_series_id = ?
            ORDER BY observation.period DESC, version.version_id COLLATE BINARY DESC
            LIMIT 1
        """,
    }
    statement = queries.get(target.reference_kind)
    if statement is None:
        return None
    row = connection.execute(statement, (target.series_id,)).fetchone()
    return None if row is None else _reference_from_row(row)


def _reference_state(
    connection: sqlite3.Connection,
    target: DataStatusTarget,
) -> tuple[_Reference | None, bool]:
    if target.reference_kind is ReferencePeriodKind.NONE:
        return None, False
    try:
        return _macro_reference(connection, target), False
    except sqlite3.Error:
        return None, True


def _status(
    *,
    capture: _Capture | None,
    outcome: Mapping[str, object] | None,
    freshness: FreshnessPolicy | None,
    malformed: bool,
    now: datetime,
) -> tuple[str, str, str | None]:
    """Return status, bounded reason, and computed UTC stale deadline.

    The accepted registry currently measures all active datasets from a
    successful capture.  Other declared bases remain explicitly ``unknown``
    here until a reviewed fixed projection supplies that basis; this avoids
    inventing source publication or scheduler timing.
    """

    if malformed:
        return "unknown", "retained_metadata_invalid", None
    if capture is None:
        if outcome is None:
            return "no_data", "no_retained_outcome", None
        return "no_data", "no_successful_capture", None
    if freshness is None or freshness.stale_after is None:
        return "unknown", "stale_threshold_not_declared", None
    if freshness.measured_from != "successful_capture":
        return "unknown", "freshness_basis_not_projected", None
    stale_at = capture.instant + timedelta(days=_duration_days(freshness.stale_after))
    rendered_stale_at = _render_datetime(stale_at)
    if now >= stale_at:
        return "stale", "successful_capture_exceeds_stale_threshold", rendered_stale_at
    return "current", "within_successful_capture_stale_threshold", rendered_stale_at


def _record(
    target: DataStatusTarget,
    *,
    capture: _Capture | None,
    outcome: Mapping[str, object] | None,
    reference: _Reference | None,
    malformed: bool,
    now: datetime,
) -> dict[str, object]:
    selected_capture = capture
    if selected_capture is None and reference is not None:
        selected_capture = reference.capture
    status, reason, stale_at = _status(
        capture=selected_capture,
        outcome=outcome,
        freshness=target.freshness,
        malformed=malformed,
        now=now,
    )
    return {
        "id": target.id,
        "store": target.store.value,
        "dataset_id": target.dataset_id,
        "source_id": target.source_id,
        "latest_reference_period": (
            None if reference is None else dict(reference.primitive)
        ),
        "latest_successful_capture": (
            None if selected_capture is None else dict(selected_capture.primitive)
        ),
        "latest_retained_outcome": None if outcome is None else dict(outcome),
        "freshness": None if target.freshness is None else target.freshness.to_primitive(),
        "status": status,
        "status_reason": reason,
        "stale_at": stale_at,
        "scope": "retained_data_only",
    }


def _unknown_record(target: DataStatusTarget, reason: str) -> dict[str, object]:
    return {
        "id": target.id,
        "store": target.store.value,
        "dataset_id": target.dataset_id,
        "source_id": target.source_id,
        "latest_reference_period": None,
        "latest_successful_capture": None,
        "latest_retained_outcome": None,
        "freshness": None if target.freshness is None else target.freshness.to_primitive(),
        "status": "unknown",
        "status_reason": reason,
        "stale_at": None,
        "scope": "retained_data_only",
    }


def _validated_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Data status clock must be an aware datetime")
    return value.astimezone(timezone.utc)


def _render_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _validated_targets(
    targets: Sequence[DataStatusTarget],
) -> tuple[DataStatusTarget, ...]:
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
        raise ValidationError("Data status targets are invalid")
    if len(targets) > MAX_STATUS_RECORDS:
        raise ValidationError("Data status target count exceeds the fixed bound")
    if any(not isinstance(item, DataStatusTarget) for item in targets):
        raise ValidationError("Data status target is invalid")
    ordered = tuple(sorted(targets, key=lambda item: item.id))
    if len({item.id for item in ordered}) != len(ordered):
        raise ValidationError("Data status target ids must be unique")
    return ordered


def _anchor_for(registry: "Registry | None", role: StoreRole) -> str:
    if registry is None:
        return "store_metadata"
    declaration = registry.store(role.value)
    return declaration.anchor_relation


def _current_news_records(
    targets: Sequence[DataStatusTarget],
    *,
    store_map: StoreMap,
    registry: "Registry | None",
    now: datetime,
) -> dict[str, dict[str, object]]:
    if not targets:
        return {}
    if registry is None:
        return {target.id: _unknown_record(target, "registry_required_for_source_status") for target in targets}
    # Reuse the established fixed, immutable news reader.  It only reports
    # retained outcomes/captures and deliberately has no live-provider probe.
    from .news.tool_repository import CurrentNewsToolRepository

    try:
        statuses = CurrentNewsToolRepository(store_map, registry).source_status_evidence(
            tuple(target.source_id for target in targets if target.source_id is not None)
        )
    except QuantDataError:
        return {target.id: _unknown_record(target, "retained_source_status_unavailable") for target in targets}
    by_source = {
        item.get("source_id"): item
        for item in statuses
        if isinstance(item, Mapping) and isinstance(item.get("source_id"), str)
    }
    result: dict[str, dict[str, object]] = {}
    for target in targets:
        source = by_source.get(target.source_id)
        if source is None:
            result[target.id] = _unknown_record(target, "retained_source_status_unavailable")
            continue
        raw_capture = source.get("latest_successful_capture")
        raw_outcome = source.get("latest_outcome")
        capture: _Capture | None = None
        malformed = False
        if raw_capture is not None:
            if not isinstance(raw_capture, Mapping):
                malformed = True
            else:
                parsed = _timestamp(raw_capture.get("captured_at"), "datetime")
                if parsed is None:
                    malformed = True
                else:
                    capture = _Capture(dict(raw_capture), parsed[1])
        outcome: Mapping[str, object] | None = None
        if raw_outcome is not None:
            if not isinstance(raw_outcome, Mapping):
                malformed = True
            else:
                outcome = dict(raw_outcome)
        result[target.id] = _record(
            target,
            capture=capture,
            outcome=outcome,
            reference=None,
            malformed=malformed,
            now=now,
        )
    return result


def data_status_records(
    store_map: StoreMap,
    *,
    targets: Sequence[DataStatusTarget],
    registry: "Registry | None" = None,
    now: datetime | None = None,
) -> tuple[dict[str, object], ...]:
    """Read bounded retained-data status for fixed, host-selected targets.

    A quiet immutable connection is opened at most once per non-news-source
    store role.  Store unavailability or an incompatible retained schema is
    represented as a bounded ``unknown`` record; it is not inferred to mean a
    live feed or scheduler failure.
    """

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Data status requires an explicit StoreMap")
    ordered_targets = _validated_targets(targets)
    evaluated_at = _validated_now(now)
    records: dict[str, dict[str, object]] = {}
    controls: dict[StoreRole, list[DataStatusTarget]] = defaultdict(list)
    source_targets: list[DataStatusTarget] = []
    for target in ordered_targets:
        if target.source_kind is DataStatusSourceKind.CURRENT_NEWS:
            source_targets.append(target)
        else:
            controls[target.store].append(target)

    for role, role_targets in controls.items():
        try:
            with quiet_immutable_read_connection(
                store_map,
                role,
                expected_anchor=_anchor_for(registry, role),
            ) as connection:
                for target in role_targets:
                    try:
                        control = _control_plane_state(connection, target.dataset_id)
                    except sqlite3.Error:
                        records[target.id] = _unknown_record(
                            target, "retained_control_plane_unavailable"
                        )
                        continue
                    reference, reference_error = _reference_state(connection, target)
                    records[target.id] = _record(
                        target,
                        capture=control.capture,
                        outcome=control.outcome,
                        reference=reference,
                        malformed=control.malformed or reference_error,
                        now=evaluated_at,
                    )
        except QuantDataError:
            records.update(
                {
                    target.id: _unknown_record(target, "retained_store_unavailable")
                    for target in role_targets
                }
            )

    records.update(
        _current_news_records(
            source_targets,
            store_map=store_map,
            registry=registry,
            now=evaluated_at,
        )
    )
    return tuple(records[target.id] for target in ordered_targets)


def data_status_snapshot(
    store_map: StoreMap,
    *,
    targets: Sequence[DataStatusTarget],
    registry: "Registry | None" = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return an adapter-ready retained-data status snapshot."""

    evaluated_at = _validated_now(now)
    return {
        "contract": "quant_data.data_status",
        "contract_version": "1.0.0",
        "evaluated_at": _render_datetime(evaluated_at),
        "scope": "retained_data_only",
        "records": list(
            data_status_records(
                store_map,
                targets=targets,
                registry=registry,
                now=evaluated_at,
            )
        ),
    }


def registry_data_status_targets(registry: "Registry") -> tuple[DataStatusTarget, ...]:
    """Build deterministic generic targets from active registry datasets.

    Current-news datasets are represented by the existing fixed source-status
    reader, one target per source, so their retained outcome is not confused
    with the generic ingestion control plane.
    """

    from .news.tool_repository import (
        CURRENT_NEWS_TOOL_DATASET_IDS,
        CURRENT_NEWS_TOOL_SOURCE_IDS,
        FMP_STOCK_LATEST_SOURCE_ID,
    )
    from .registry import Registry

    if not isinstance(registry, Registry):
        raise ValidationError("Registry data status requires a validated registry")
    active = {item.id: item for item in registry.datasets if item.active}
    current_news_ids = frozenset(CURRENT_NEWS_TOOL_DATASET_IDS)
    targets = [
        _target_from_dataset(dataset)
        for dataset in sorted(active.values(), key=lambda item: item.id)
        if dataset.id not in current_news_ids
    ]
    fmp_dataset = active.get("news.fmp.stock_latest_current_evidence")
    multi_dataset = active.get("news.current_multi_source_evidence")
    for source_id in CURRENT_NEWS_TOOL_SOURCE_IDS:
        dataset = fmp_dataset if source_id == FMP_STOCK_LATEST_SOURCE_ID else multi_dataset
        if dataset is None:
            continue
        targets.append(
            DataStatusTarget(
                id=f"news.source.{source_id}",
                store=StoreRole.NEWS,
                dataset_id=dataset.id,
                freshness=FreshnessPolicy.from_mapping(dataset.freshness),
                source_kind=DataStatusSourceKind.CURRENT_NEWS,
                source_id=source_id,
            )
        )
    return _validated_targets(targets)


def _target_from_dataset(dataset: "DatasetDeclaration") -> DataStatusTarget:
    return DataStatusTarget(
        id=dataset.id,
        store=StoreRole(dataset.store),
        dataset_id=dataset.id,
        freshness=FreshnessPolicy.from_mapping(dataset.freshness),
    )


__all__ = (
    "DataStatusSourceKind",
    "DataStatusTarget",
    "FreshnessPolicy",
    "MAX_STATUS_RECORDS",
    "ReferencePeriodKind",
    "data_status_records",
    "data_status_snapshot",
    "registry_data_status_targets",
)
