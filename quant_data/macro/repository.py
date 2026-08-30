"""Read-only RTDSM macro-series selection over immutable versions."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

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


_CANONICAL_DATASET_ID = "fixture.macro.rtdsm_employ"
_MAX_CANDIDATE_ROWS = 200_000


@dataclass(frozen=True, slots=True)
class MacroSeriesQuery:
    """Validated host-independent request for one bounded macro series."""

    series_id: str
    start_date: str
    end_date: str
    vintage_mode: str
    as_of: str | TemporalValue | None
    date_only_policy: str | DateOnlyPolicy
    limit: int

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
            if cutoff.precision not in {TemporalPrecision.DATE, TemporalPrecision.DATETIME}:
                raise ValidationError("as_of must be an ISO date or aware datetime")
            object.__setattr__(self, "as_of", cutoff)
        elif self.as_of is not None:
            raise ValidationError("as_of is only valid with vintage_mode=as_of")

    @property
    def cutoff(self) -> TemporalValue | None:
        return self.as_of if isinstance(self.as_of, TemporalValue) else None


def _version_rank(row: object) -> tuple[str, int, str]:
    return (
        str(row["source_release_order"]),
        int(row["correction_sequence"]),
        str(row["version_id"]),
    )


class MacroSeriesRepository:
    """Read canonical macro facts through a host-controlled macro reader."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        declaration = registry.tool("macro.get_series")
        if tuple(declaration["datasets"]) != (_CANONICAL_DATASET_ID,):
            raise ValidationError("Macro repository is not bound to the canonical RTDSM dataset")

    def get_series(self, query: MacroSeriesQuery) -> TimeSeries:
        with quiet_immutable_read_connection(self._store_map, StoreRole.MACRO) as connection:
            series = connection.execute(
                """
                SELECT series_id, provider, title, frequency, unit,
                       value_representation, scale, availability_basis
                FROM macro_series
                WHERE series_id=?
                """,
                (query.series_id,),
            ).fetchone()
            if series is None:
                raise ValidationError("Requested macro series is unavailable")
            rows = list(
                connection.execute(
                    """
                    SELECT ov.version_id, ov.release_id, ov.source_vintage_identity,
                           ov.period_start, ov.period_end,
                           ov.dimensions_json, ov.dimensions_digest,
                           ov.correction_sequence, ov.value_text,
                           ov.missing_reason, ov.unit, ov.value_representation,
                           ov.scale, ov.available_at, ov.available_precision,
                           ov.captured_at, ov.captured_precision, ov.artifact_id,
                           ov.snapshot_id, ov.run_id, r.vintage_at,
                           r.source_release_order, r.is_first_release
                    FROM macro_observation_versions AS ov
                    JOIN macro_releases AS r ON r.release_id=ov.release_id
                    WHERE ov.series_id=?
                      AND ov.period_start>=?
                      AND ov.period_start<=?
                    ORDER BY ov.period_start, ov.period_end, ov.dimensions_json,
                             r.source_release_order, ov.correction_sequence,
                             ov.version_id
                    LIMIT ?
                    """,
                    (query.series_id, query.start_date, query.end_date, _MAX_CANDIDATE_ROWS + 1),
                )
            )
            if len(rows) > _MAX_CANDIDATE_ROWS:
                raise ResourceLimitError("Macro version history exceeds the bounded query contract")
            selected, warnings = self._select(rows, query)
            selected.sort(
                key=lambda row: (
                    row["period_start"],
                    row["period_end"],
                    row["dimensions_json"],
                    row["version_id"],
                )
            )
            truncated = len(selected) > query.limit
            if truncated:
                selected = selected[: query.limit]
                warnings.add("result_truncated")
            observations = tuple(self._observation(row) for row in selected)
            migration_rows = list(
                connection.execute(
                    """
                    SELECT migration_id, sha256 FROM schema_migrations
                    ORDER BY ordinal
                    """
                )
            )

        migration_ids = [str(row["migration_id"]) for row in migration_rows]
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
            },
            "selected_immutable_versions": [
                {
                    "version_id": str(row["version_id"]),
                    "release_id": str(row["release_id"]),
                    "source_vintage_identity": str(row["source_vintage_identity"]),
                    "period_start": str(row["period_start"]),
                    "period_end": str(row["period_end"]),
                    "dimensions_digest": str(row["dimensions_digest"]),
                    "correction_sequence": int(row["correction_sequence"]),
                    "value_text": (
                        str(row["value_text"]) if row["value_text"] is not None else None
                    ),
                    "missing_reason": (
                        str(row["missing_reason"])
                        if row["missing_reason"] is not None
                        else None
                    ),
                    "unit": str(row["unit"]),
                    "value_representation": str(row["value_representation"]),
                    "scale": str(row["scale"]),
                    "vintage_at": str(row["vintage_at"]),
                    "source_release_order": str(row["source_release_order"]),
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "captured_at": str(row["captured_at"]),
                    "captured_precision": str(row["captured_precision"]),
                    "artifact_id": str(row["artifact_id"]),
                    "snapshot_id": str(row["snapshot_id"]),
                    "run_id": str(row["run_id"]),
                }
                for row in selected
            ],
        }
        store_receipt = {
            "migration_ids": migration_ids,
            "sha256": hashlib.sha256(dumps_strict(receipt_material).encode("utf-8")).hexdigest(),
        }
        cutoff = query.cutoff
        audit = {
            "mode": query.vintage_mode,
            "cutoff": cutoff.raw if cutoff is not None else None,
            "cutoff_precision": cutoff.precision.value if cutoff is not None else None,
            "date_only_policy": query.date_only_policy.value,
            "availability_basis": str(series["availability_basis"]),
            "period_range_rule": "period_start",
            "requested_start_date": query.start_date,
            "requested_end_date": query.end_date,
            "limit": query.limit,
            "selected_count": len(observations),
        }
        return TimeSeries(
            series_id=str(series["series_id"]),
            metadata={
                "provider": str(series["provider"]),
                "frequency": str(series["frequency"]),
                "unit": str(series["unit"]),
                "value_representation": str(series["value_representation"]),
                "scale": str(series["scale"]),
            },
            observations=observations,
            warnings=tuple(sorted(warnings)),
            audit=audit,
            provenance={
                "dataset_id": _CANONICAL_DATASET_ID,
                "store_role": StoreRole.MACRO.value,
                "registry_revision": self._registry.revision,
                "store_receipt": store_receipt,
            },
            truncated=truncated,
        )

    @staticmethod
    def _select(rows: Iterable[object], query: MacroSeriesQuery) -> tuple[list[object], set[str]]:
        grouped: dict[tuple[str, str, str], list[object]] = defaultdict(list)
        warnings: set[str] = set()
        for row in rows:
            grouped[
                (
                    str(row["period_start"]),
                    str(row["period_end"]),
                    str(row["dimensions_digest"]),
                )
            ].append(row)
        selected: list[object] = []
        for candidates in grouped.values():
            if query.vintage_mode == "latest":
                selected.append(max(candidates, key=_version_rank))
                continue
            if query.vintage_mode == "first_release":
                first_release = [row for row in candidates if int(row["is_first_release"]) == 1]
                if first_release:
                    # The original correction sequence is the explicitly
                    # evidenced first release; later same-vintage corrections
                    # must not redefine it.
                    selected.append(min(first_release, key=_version_rank))
                continue
            assert query.cutoff is not None
            eligible: list[object] = []
            for row in candidates:
                availability = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
                if availability.precision.value != str(row["available_precision"]):
                    raise ValidationError("Stored macro availability precision is inconsistent")
                decision = availability_at_or_before(availability, query.cutoff, query.date_only_policy)
                if decision.included:
                    eligible.append(row)
                    warnings.update(decision.warnings)
            if eligible:
                selected.append(max(eligible, key=_version_rank))
        return selected, warnings

    @staticmethod
    def _observation(row: object) -> Observation:
        raw_value = row["value_text"]
        if raw_value is None:
            value = None
        else:
            try:
                value = Decimal(str(raw_value))
            except (InvalidOperation, ValueError) as exc:
                raise ValidationError("Stored macro value is not a valid decimal") from exc
            if not value.is_finite():
                raise ValidationError("Stored macro value is not finite")
        try:
            dimensions = loads_strict(str(row["dimensions_json"]), max_bytes=1024)
        except ValidationError as exc:
            raise ValidationError("Stored macro dimensions are invalid") from exc
        if dimensions != {}:
            raise ValidationError("Stored macro dimensions violate the Stage 1 contract")
        return Observation(
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            value=value,
            missing_reason=(str(row["missing_reason"]) if row["missing_reason"] is not None else None),
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
