"""Bounded, read-only Stage 3 macro selection.

The class in this module is intentionally internal.  It accepts no database
path or SQL, uses the registered macro store only, and returns immutable typed
contracts over the generic macro version core plus the recession chronology.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..contracts import Observation, TimeSeries
from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


_MAX_CANDIDATE_ROWS = 200_000
_GENERIC_DATASET_ID = "fixture.macro.rtdsm_employ"
_STAGE3_CATALOG_DATASET_ID = "fixture.macro.stage3_catalog"
_FAMILY_PREFIXES = (
    ("macro.gdp.", "gdp"),
    ("macro.treasury.", "treasury"),
    ("macro.soma.", "soma"),
    ("macro.eia.", "eia"),
    ("macro.calendar.", "economic_calendar"),
    ("macro.bls.", "bls"),
    ("macro.bis.", "bis"),
    ("macro.chicagofed.", "chicagofed"),
    ("macro.bea.", "bea"),
)


@dataclass(frozen=True, slots=True)
class MacroStage3SeriesQuery:
    """Validated one-series request for the non-public Stage 3 reader."""

    series_id: str
    start_date: str
    end_date: str
    vintage_mode: str
    as_of: str | TemporalValue | None = None
    date_only_policy: str | DateOnlyPolicy = DateOnlyPolicy.COMPLETED_DATE
    limit: int = 10_000

    def __post_init__(self) -> None:
        if not isinstance(self.series_id, str) or not self.series_id:
            raise ValidationError("Macro series ID is required")
        start = parse_date(self.start_date, pointer="/start_date")
        end = parse_date(self.end_date, pointer="/end_date")
        if end < start:
            raise ValidationError("Macro end_date cannot precede start_date")
        if self.vintage_mode not in {"latest", "as_of", "first_release"}:
            raise ValidationError("Unsupported macro vintage mode")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except ValueError as exc:
            raise ValidationError("Unsupported date-only policy") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 10_000:
            raise ResourceLimitError("Macro query limit must be between 1 and 10000")
        if self.vintage_mode == "as_of":
            if self.as_of is None:
                raise ValidationError("as_of is required for as_of macro queries")
            cutoff = self.as_of if isinstance(self.as_of, TemporalValue) else TemporalValue.parse(self.as_of, pointer="/as_of")
            object.__setattr__(self, "as_of", cutoff)
        elif self.as_of is not None:
            raise ValidationError("as_of is only valid with vintage_mode=as_of")

    @property
    def cutoff(self) -> TemporalValue | None:
        return self.as_of if isinstance(self.as_of, TemporalValue) else None


@dataclass(frozen=True, slots=True)
class MacroStage3RecessionQuery:
    """The bounded U.S. recession chronology deliberately exposes latest only."""

    vintage_mode: str = "latest"
    limit: int = 100

    def __post_init__(self) -> None:
        if self.vintage_mode != "latest":
            raise ValidationError("Recession chronology supports latest mode only")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 1_000:
            raise ResourceLimitError("Recession chronology limit must be between 1 and 1000")


def _family_for_series(series_id: str) -> str:
    for prefix, family in _FAMILY_PREFIXES:
        if series_id.startswith(prefix):
            return family
    return "generic"


def _release_rank(row: Mapping[str, Any]) -> tuple[int, int, str]:
    try:
        order = int(str(row["source_release_order"]))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Stored macro release order is invalid") from exc
    return (order, int(row["correction_sequence"]), str(row["version_id"]))


def _first_rank(row: Mapping[str, Any]) -> tuple[int, int, str]:
    return _release_rank(row)


class MacroStage3Repository:
    """Read Stage 3 canonical macro facts through the host-controlled store."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        dataset_ids = {item.id for item in registry.datasets_for(StoreRole.MACRO.value)}
        required = {_GENERIC_DATASET_ID, _STAGE3_CATALOG_DATASET_ID}
        if not required.issubset(dataset_ids):
            raise ValidationError("Stage 3 macro repository is not bound to the frozen catalog datasets")

    def get_series(self, query: MacroStage3SeriesQuery) -> TimeSeries:
        family = _family_for_series(query.series_id)
        self._validate_mode_policy(family, query)
        with quiet_immutable_read_connection(self._store_map, StoreRole.MACRO) as connection:
            series = connection.execute(
                """
                SELECT series_id, provider, title, frequency, unit,
                       value_representation, scale, availability_basis,
                       supported_modes_json
                FROM macro_series WHERE series_id=?
                """,
                (query.series_id,),
            ).fetchone()
            if series is None:
                raise ValidationError("Requested macro series is unavailable")
            rows = list(
                connection.execute(
                    """
                    SELECT version.version_id, version.release_id,
                           version.source_vintage_identity, version.period_start,
                           version.period_end, version.dimensions_json,
                           version.dimensions_digest, version.correction_sequence,
                           version.value_text, version.missing_reason, version.unit,
                           version.value_representation, version.scale,
                           version.available_at, version.available_precision,
                           version.captured_at, version.captured_precision,
                           version.artifact_id, version.snapshot_id, version.run_id,
                           version.state, release.vintage_at,
                           release.source_release_order, release.is_first_release,
                           release.first_release_evidence, release.release_stage
                    FROM macro_observation_versions AS version
                    JOIN macro_releases AS release ON release.release_id=version.release_id
                    WHERE version.series_id=?
                      AND version.period_start>=?
                      AND version.period_start<=?
                    ORDER BY version.period_start, version.period_end,
                             version.dimensions_json, release.source_release_order,
                             version.correction_sequence, version.version_id
                    LIMIT ?
                    """,
                    (query.series_id, query.start_date, query.end_date, _MAX_CANDIDATE_ROWS + 1),
                )
            )
            if len(rows) > _MAX_CANDIDATE_ROWS:
                raise ResourceLimitError("Macro version history exceeds the bounded query contract")
            if family == "eia" and query.vintage_mode == "first_release" and not any(
                int(row["is_first_release"]) == 1 and row["first_release_evidence"] is not None
                for row in rows
            ):
                raise ValidationError("EIA first-release mode requires explicit source evidence")
            selected, warnings = self._select(rows, query)
            selected.sort(
                key=lambda row: (
                    str(row["period_start"]),
                    str(row["period_end"]),
                    str(row["dimensions_json"]),
                    str(row["version_id"]),
                )
            )
            truncated = len(selected) > query.limit
            if truncated:
                selected = selected[: query.limit]
                warnings.add("result_truncated")
            observations = tuple(self._observation(row) for row in selected if row["state"] == "active")
            migration_rows = list(
                connection.execute("SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal")
            )
        receipt_material = {
            "migrations": [
                {"migration_id": str(row["migration_id"]), "sha256": str(row["sha256"])}
                for row in migration_rows
            ],
            "series_contract": {
                "series_id": str(series["series_id"]),
                "provider": str(series["provider"]),
                "title": str(series["title"]),
                "frequency": str(series["frequency"]),
                "unit": str(series["unit"]),
                "value_representation": str(series["value_representation"]),
                "scale": str(series["scale"]),
                "availability_basis": str(series["availability_basis"]),
                "supported_modes_json": str(series["supported_modes_json"]),
            },
            "selected_immutable_versions": [self._version_receipt(row) for row in selected],
        }
        cutoff = query.cutoff
        return TimeSeries(
            series_id=str(series["series_id"]),
            metadata={
                "provider": str(series["provider"]),
                "frequency": str(series["frequency"]),
                "unit": str(series["unit"]),
                "value_representation": str(series["value_representation"]),
                "scale": str(series["scale"]),
                "family": family,
            },
            observations=observations,
            warnings=tuple(sorted(warnings)),
            audit={
                "mode": query.vintage_mode,
                "family": family,
                "cutoff": cutoff.raw if cutoff is not None else None,
                "cutoff_precision": cutoff.precision.value if cutoff is not None else None,
                "date_only_policy": query.date_only_policy.value,
                "availability_basis": str(series["availability_basis"]),
                "period_range_rule": "period_start",
                "requested_start_date": query.start_date,
                "requested_end_date": query.end_date,
                "limit": query.limit,
                "selected_count": len(observations),
            },
            provenance={
                "dataset_id": _GENERIC_DATASET_ID,
                "catalog_dataset_id": _STAGE3_CATALOG_DATASET_ID,
                "store_role": StoreRole.MACRO.value,
                "registry_revision": self._registry.revision,
                "store_receipt": {
                    "migration_ids": [str(row["migration_id"]) for row in migration_rows],
                    "sha256": hashlib.sha256(dumps_strict(receipt_material).encode("utf-8")).hexdigest(),
                },
            },
            truncated=truncated,
        )

    def get_recession_periods(self, query: MacroStage3RecessionQuery | None = None) -> tuple[dict[str, Any], ...]:
        request = query or MacroStage3RecessionQuery()
        with quiet_immutable_read_connection(self._store_map, StoreRole.MACRO) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT recession_period_id, provider, peak_month, trough_month,
                           status, available_at, available_precision, artifact_id,
                           source_snapshot_id, run_id
                    FROM us_recession_periods
                    ORDER BY peak_month, COALESCE(trough_month, ''), recession_period_id
                    LIMIT ?
                    """,
                    (request.limit + 1,),
                )
            )
        if len(rows) > request.limit:
            raise ResourceLimitError("Recession chronology exceeds the bounded query contract")
        return tuple(
            {
                "recession_period_id": str(row["recession_period_id"]),
                "provider": str(row["provider"]),
                "peak_month": str(row["peak_month"]),
                "trough_month": str(row["trough_month"]) if row["trough_month"] is not None else None,
                "status": str(row["status"]),
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "evidence_id": str(row["artifact_id"]) if row["artifact_id"] is not None else None,
                "snapshot_id": str(row["source_snapshot_id"]) if row["source_snapshot_id"] is not None else None,
                "run_id": str(row["run_id"]),
            }
            for row in rows
        )

    @staticmethod
    def _validate_mode_policy(family: str, query: MacroStage3SeriesQuery) -> None:
        if family == "gdp":
            return
        if family in {"treasury", "soma"} and query.vintage_mode == "first_release":
            raise ValidationError("Current-state macro family does not support first_release mode")
        if family == "eia":
            return
        if query.vintage_mode == "first_release":
            raise ValidationError("First-release mode is not evidenced for this macro family")

    @staticmethod
    def _select(
        rows: Iterable[Mapping[str, Any]], query: MacroStage3SeriesQuery
    ) -> tuple[list[Mapping[str, Any]], set[str]]:
        grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row["period_start"]), str(row["period_end"]), str(row["dimensions_digest"]))].append(row)
        selected: list[Mapping[str, Any]] = []
        warnings: set[str] = set()
        for candidates in grouped.values():
            if query.vintage_mode == "latest":
                selected.append(max(candidates, key=_release_rank))
                continue
            if query.vintage_mode == "first_release":
                evidenced = [
                    row
                    for row in candidates
                    if int(row["is_first_release"]) == 1 and row["first_release_evidence"] is not None
                ]
                if evidenced:
                    selected.append(min(evidenced, key=_first_rank))
                continue
            assert query.cutoff is not None
            eligible: list[Mapping[str, Any]] = []
            for row in candidates:
                availability = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
                if availability.precision.value != str(row["available_precision"]):
                    raise ValidationError("Stored macro availability precision is inconsistent")
                decision = availability_at_or_before(availability, query.cutoff, query.date_only_policy)
                if decision.included:
                    eligible.append(row)
                    warnings.update(decision.warnings)
            if eligible:
                selected.append(max(eligible, key=_release_rank))
        return selected, warnings

    @staticmethod
    def _version_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "version_id": str(row["version_id"]),
            "release_id": str(row["release_id"]),
            "source_vintage_identity": str(row["source_vintage_identity"]),
            "period_start": str(row["period_start"]),
            "period_end": str(row["period_end"]),
            "dimensions_digest": str(row["dimensions_digest"]),
            "correction_sequence": int(row["correction_sequence"]),
            "value_text": str(row["value_text"]) if row["value_text"] is not None else None,
            "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
            "state": str(row["state"]),
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "artifact_id": str(row["artifact_id"]),
            "snapshot_id": str(row["snapshot_id"]),
            "run_id": str(row["run_id"]),
        }

    @staticmethod
    def _observation(row: Mapping[str, Any]) -> Observation:
        raw_value = row["value_text"]
        if raw_value is None:
            value = None
        else:
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError("Stored macro value is not a finite decimal") from exc
            if not value.is_finite():
                raise ValidationError("Stored macro value is not a finite decimal")
        try:
            dimensions = loads_strict(str(row["dimensions_json"]), max_bytes=1024)
        except ValidationError as exc:
            raise ValidationError("Stored macro dimensions are invalid") from exc
        if not isinstance(dimensions, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in dimensions.items()
        ):
            raise ValidationError("Stored macro dimensions are invalid")
        return Observation(
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            value=value,
            missing_reason=str(row["missing_reason"]) if row["missing_reason"] is not None else None,
            unit=str(row["unit"]),
            value_representation=str(row["value_representation"]),
            scale=str(row["scale"]),
            vintage_at=str(row["vintage_at"]),
            available_at=str(row["available_at"]),
            available_precision=str(row["available_precision"]),
            captured_at=str(row["captured_at"]),
            captured_precision=str(row["captured_precision"]),
            version_id=str(row["version_id"]),
            evidence_id=str(row["artifact_id"]),
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            dimensions=dimensions,
            quality_flags=(),
        )


__all__ = ("MacroStage3RecessionQuery", "MacroStage3Repository", "MacroStage3SeriesQuery")
