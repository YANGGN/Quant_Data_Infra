"""Fixed, read-only access to the populated Stage 11 EIA energy histories.

The Stage 3 fixture imports and the Stage 11 retained EIA histories have
different ownership and lineage.  This module deliberately exposes only the
Stage 11 U.S. retail-electricity and petroleum-stock facts.  It does not
derive balances, trends, or synthetic period endpoints.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..errors import ResourceLimitError, ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)
from .canonical_access import _reconcile_store_contract
from .stage11_eia import (
    EIA_RETAIL_CANONICAL_SERIES_BY_METRIC,
    EIA_WEEKLY_CANONICAL_SERIES_ID,
)


RETAIL_DATASET_IDS = (
    "macro.eia.electricity_retail_history_evidence",
    "macro.eia.electricity_retail_history",
)
WEEKLY_DATASET_IDS = (
    "macro.eia.petroleum_weekly_stock_history_evidence",
    "macro.eia.petroleum_weekly_stock_history",
)
RETAIL_METRICS = tuple(EIA_RETAIL_CANONICAL_SERIES_BY_METRIC)
_MAX_CANDIDATE_ROWS = 200_000


def _limit(value: object, *, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ResourceLimitError(f"{label} must be from 1 through {maximum}")
    return value


def _optional_date(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Energy period bound is invalid")
    parse_date(value, pointer=pointer)
    return value


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Stored energy value is invalid") from exc
    if not parsed.is_finite():
        raise ValidationError("Stored energy value is invalid")
    return parsed


def _temporal(
    *,
    mode: object,
    as_of: object,
    date_only_policy: object,
) -> tuple[str, str | None, DateOnlyPolicy]:
    if mode not in {"latest", "as_of"}:
        raise ValidationError("Energy selection mode is unsupported")
    if mode == "as_of":
        if not isinstance(as_of, str) or not as_of:
            raise ValidationError("Energy as_of mode requires a cutoff")
        TemporalValue.parse(as_of, pointer="/as_of")
    elif as_of is not None:
        raise ValidationError("Energy latest mode does not accept a cutoff")
    try:
        policy = DateOnlyPolicy(date_only_policy)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Energy date-only policy is unsupported") from exc
    return str(mode), as_of if isinstance(as_of, str) else None, policy


@dataclass(frozen=True, slots=True)
class EnergyRetailQuery:
    """Select one populated U.S./ALL retail-electricity metric."""

    metric: str
    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    limit: int

    def __post_init__(self) -> None:
        if self.metric not in EIA_RETAIL_CANONICAL_SERIES_BY_METRIC:
            raise ValidationError("Energy retail metric is unsupported")
        start = _optional_date(self.start_date, pointer="/start_date")
        end = _optional_date(self.end_date, pointer="/end_date")
        if start is not None and end is not None and end < start:
            raise ValidationError("Energy end_date cannot precede start_date")
        mode, as_of, policy = _temporal(
            mode=self.mode,
            as_of=self.as_of,
            date_only_policy=self.date_only_policy,
        )
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "date_only_policy", policy)
        _limit(self.limit, maximum=1_000, label="Energy retail limit")

    @property
    def cutoff(self) -> TemporalValue | None:
        return (
            None
            if self.as_of is None
            else TemporalValue.parse(self.as_of, pointer="/as_of")
        )


@dataclass(frozen=True, slots=True)
class EnergyWeeklyQuery:
    """Select the populated EIA weekly petroleum-stock history."""

    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    limit: int

    def __post_init__(self) -> None:
        start = _optional_date(self.start_date, pointer="/start_date")
        end = _optional_date(self.end_date, pointer="/end_date")
        if start is not None and end is not None and end < start:
            raise ValidationError("Energy end_date cannot precede start_date")
        mode, as_of, policy = _temporal(
            mode=self.mode,
            as_of=self.as_of,
            date_only_policy=self.date_only_policy,
        )
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "date_only_policy", policy)
        _limit(self.limit, maximum=5_000, label="Energy weekly limit")

    @property
    def cutoff(self) -> TemporalValue | None:
        return (
            None
            if self.as_of is None
            else TemporalValue.parse(self.as_of, pointer="/as_of")
        )


@dataclass(frozen=True, slots=True)
class EnergySelection:
    """One bounded, source-native set of retained EIA observations."""

    records: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    total_selected_count: int
    truncated: bool
    dataset_ids: tuple[str, ...]


def _availability(row: Mapping[str, object]) -> TemporalValue:
    value = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
    if value.precision.value != str(row["available_precision"]):
        raise ValidationError("Stored energy availability precision is inconsistent")
    if value.precision is not TemporalPrecision.DATETIME:
        raise ValidationError("Stage 11 energy availability must be an exact datetime")
    return value


def _bounded(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    result = list(rows)
    if len(result) > _MAX_CANDIDATE_ROWS:
        raise ResourceLimitError("Energy version history exceeds the bounded query contract")
    return result


def _selected_versions(
    rows: Iterable[Mapping[str, object]],
    *,
    mode: str,
    cutoff: TemporalValue | None,
    date_only_policy: DateOnlyPolicy,
) -> tuple[list[Mapping[str, object]], tuple[str, ...]]:
    if mode == "latest":
        return list(rows), ()
    assert cutoff is not None
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in rows:
        decision = availability_at_or_before(
            _availability(row), cutoff, date_only_policy
        )
        warnings.update(decision.warnings)
        if decision.included:
            grouped[str(row["period"])].append(row)
    selected = [
        max(
            candidates,
            key=lambda item: (
                int(item["correction_sequence"]),
                str(item["version_id"]),
            ),
        )
        for candidates in grouped.values()
    ]
    return selected, tuple(sorted(warnings))


def _retail_record(row: Mapping[str, object]) -> dict[str, Any]:
    if str(row["state_id"]) != "US" or str(row["sector_id"]) != "ALL":
        raise ValidationError("Stored energy retail scope is inconsistent")
    return {
        "artifact_id": str(row["artifact_id"]),
        "available_at": str(row["available_at"]),
        "available_precision": str(row["available_precision"]),
        "canonical_series_id": str(row["canonical_series_id"]),
        "capture_id": str(row["capture_id"]),
        "captured_at": str(row["captured_at"]),
        "captured_precision": str(row["captured_precision"]),
        "correction_sequence": int(row["correction_sequence"]),
        "metric": str(row["metric"]),
        "period": str(row["period"]),
        "run_id": str(row["run_id"]),
        "sector_id": str(row["sector_id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "source_row": int(row["source_row"]),
        "state_id": str(row["state_id"]),
        "unit": str(row["unit"]),
        "value": _decimal(row["value_text"]),
        "version_id": str(row["version_id"]),
    }


def _weekly_record(row: Mapping[str, object]) -> dict[str, Any]:
    if str(row["canonical_series_id"]) != EIA_WEEKLY_CANONICAL_SERIES_ID:
        raise ValidationError("Stored energy weekly scope is inconsistent")
    return {
        "artifact_id": str(row["artifact_id"]),
        "available_at": str(row["available_at"]),
        "available_precision": str(row["available_precision"]),
        "canonical_series_id": str(row["canonical_series_id"]),
        "capture_id": str(row["capture_id"]),
        "captured_at": str(row["captured_at"]),
        "captured_precision": str(row["captured_precision"]),
        "correction_sequence": int(row["correction_sequence"]),
        "period": str(row["period"]),
        "provider_series_id": str(row["provider_series_id"]),
        "run_id": str(row["run_id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "source_row": int(row["source_row"]),
        "unit": str(row["unit"]),
        "value": _decimal(row["value_text"]),
        "version_id": str(row["version_id"]),
    }


class EnergyCanonicalRepository:
    """Closed host-routed reader for populated Stage 11 EIA histories."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Energy canonical dependencies are invalid")
        self._stores = store_map
        self._registry = registry
        self._datasets = {
            item.id: item for item in registry.datasets_for(StoreRole.MACRO.value)
        }

    def _require_datasets(self, dataset_ids: tuple[str, ...]) -> None:
        if any(
            dataset_id not in self._datasets
            or self._datasets[dataset_id].store != StoreRole.MACRO.value
            for dataset_id in dataset_ids
        ):
            raise ValidationError("Stage 11 energy datasets are not registered")

    @staticmethod
    def _retail_rows(connection: Any, query: EnergyRetailQuery) -> list[Mapping[str, object]]:
        clauses = [
            "version.canonical_series_id=?",
            "version.metric=?",
            "version.state_id='US'",
            "version.sector_id='ALL'",
        ]
        parameters: list[object] = [
            EIA_RETAIL_CANONICAL_SERIES_BY_METRIC[query.metric],
            query.metric,
        ]
        if query.start_date is not None:
            clauses.append("date(version.period || '-01')>=?")
            parameters.append(query.start_date)
        if query.end_date is not None:
            clauses.append("date(version.period || '-01')<=?")
            parameters.append(query.end_date)
        source = (
            "stage11_eia_retail_observations AS current "
            "JOIN stage11_eia_retail_observation_versions AS version "
            "ON version.version_id=current.current_version_id"
            if query.mode == "latest"
            else "stage11_eia_retail_observation_versions AS version"
        )
        rows = connection.execute(
            """
            SELECT version.version_id, version.canonical_series_id, version.metric,
                   version.period, version.state_id, version.sector_id,
                   version.value_text, version.unit, version.available_at,
                   version.available_precision, version.captured_at,
                   version.captured_precision, version.correction_sequence,
                   version.capture_id, version.artifact_id, version.snapshot_id,
                   version.run_id, version.source_row
            FROM """
            + source
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY version.period, version.correction_sequence, version.version_id",
            tuple(parameters),
        )
        return _bounded(rows)

    @staticmethod
    def _weekly_rows(connection: Any, query: EnergyWeeklyQuery) -> list[Mapping[str, object]]:
        clauses = ["version.canonical_series_id=?"]
        parameters: list[object] = [EIA_WEEKLY_CANONICAL_SERIES_ID]
        if query.start_date is not None:
            clauses.append("version.period>=?")
            parameters.append(query.start_date)
        if query.end_date is not None:
            clauses.append("version.period<=?")
            parameters.append(query.end_date)
        source = (
            "stage11_eia_weekly_observations AS current "
            "JOIN stage11_eia_weekly_observation_versions AS version "
            "ON version.version_id=current.current_version_id"
            if query.mode == "latest"
            else "stage11_eia_weekly_observation_versions AS version"
        )
        rows = connection.execute(
            """
            SELECT version.version_id, version.canonical_series_id,
                   version.provider_series_id, version.period, version.value_text,
                   version.unit, version.available_at, version.available_precision,
                   version.captured_at, version.captured_precision,
                   version.correction_sequence, version.capture_id,
                   version.artifact_id, version.snapshot_id, version.run_id,
                   version.source_row
            FROM """
            + source
            + " WHERE "
            + " AND ".join(clauses)
            + " ORDER BY version.period, version.correction_sequence, version.version_id",
            tuple(parameters),
        )
        return _bounded(rows)

    def get_retail(self, query: EnergyRetailQuery) -> EnergySelection:
        if not isinstance(query, EnergyRetailQuery):
            raise ValidationError("Energy retail access requires a typed query")
        self._require_datasets(RETAIL_DATASET_IDS)
        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as connection:
            _reconcile_store_contract(connection, self._registry, RETAIL_DATASET_IDS)
            selected, warnings = _selected_versions(
                self._retail_rows(connection, query),
                mode=query.mode,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
            )
        selected.sort(
            key=lambda row: (
                str(row["period"]),
                int(row["correction_sequence"]),
                str(row["version_id"]),
            )
        )
        total = len(selected)
        return EnergySelection(
            records=tuple(_retail_record(row) for row in selected[: query.limit]),
            warnings=warnings,
            total_selected_count=total,
            truncated=total > query.limit,
            dataset_ids=RETAIL_DATASET_IDS,
        )

    def get_weekly(self, query: EnergyWeeklyQuery) -> EnergySelection:
        if not isinstance(query, EnergyWeeklyQuery):
            raise ValidationError("Energy weekly access requires a typed query")
        self._require_datasets(WEEKLY_DATASET_IDS)
        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as connection:
            _reconcile_store_contract(connection, self._registry, WEEKLY_DATASET_IDS)
            selected, warnings = _selected_versions(
                self._weekly_rows(connection, query),
                mode=query.mode,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
            )
        selected.sort(
            key=lambda row: (
                str(row["period"]),
                int(row["correction_sequence"]),
                str(row["version_id"]),
            )
        )
        total = len(selected)
        return EnergySelection(
            records=tuple(_weekly_record(row) for row in selected[: query.limit]),
            warnings=warnings,
            total_selected_count=total,
            truncated=total > query.limit,
            dataset_ids=WEEKLY_DATASET_IDS,
        )


__all__ = (
    "EnergyCanonicalRepository",
    "EnergyRetailQuery",
    "EnergySelection",
    "EnergyWeeklyQuery",
    "RETAIL_DATASET_IDS",
    "RETAIL_METRICS",
    "WEEKLY_DATASET_IDS",
)
