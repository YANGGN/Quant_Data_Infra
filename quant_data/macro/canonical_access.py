"""Closed read-only access to the canonical macro catalog and calendar.

The restored ``macro.get_series`` v1 gateway remains fixture-bound.  This
module serves its explicit v2 successor.  Official-vintage identifiers are
resolved only through the isolated ``macro_live_vintage_*`` model; all other
identifiers use the generic macro version core.  The two histories are never
merged or used as fallbacks for one another.

The broad FMP calendar is an explicit predecessor/successor composition:
migration 0016's immutable wholesale rows are followed by migration 0018's
incremental event versions.  Availability is the retained local capture time,
not the scheduled event time.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection, stable_id
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)
from .live_vintages import (
    CPI_ALL_ITEMS_SERIES_ID,
    CPI_CORE_SERIES_ID,
    GDI_NOMINAL_SERIES_ID,
    GDI_REAL_SERIES_ID,
    GDP_NOMINAL_SERIES_ID,
    GDP_REAL_SERIES_ID,
    RTDSM_NOMINAL_OUTPUT_SERIES_ID,
    RTDSM_REAL_OUTPUT_SERIES_ID,
    TOTAL_NONFARM_PAYROLLS_SERIES_ID,
    UNEMPLOYMENT_RATE_SERIES_ID,
)


GENERIC_CANONICAL_DATASET_ID = "fixture.macro.rtdsm_employ"
GENERIC_EVIDENCE_DATASET_ID = "fixture.macro.rtdsm_employ_evidence"
GENERIC_CATALOG_DATASET_ID = "fixture.macro.stage3_catalog"
OFFICIAL_CANONICAL_DATASET_ID = "macro.official_vintages"
OFFICIAL_EVIDENCE_DATASET_ID = "macro.official_vintages_evidence"
CALENDAR_LEGACY_DATASET_ID = "macro.fmp.economic_calendar_evidence"
CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID = (
    "macro.fmp.economic_calendar_incremental_evidence"
)
CALENDAR_INCREMENTAL_EVENT_DATASET_ID = (
    "macro.fmp.economic_calendar_incremental_events"
)

MACRO_ACCESS_DATASET_IDS = (
    GENERIC_CANONICAL_DATASET_ID,
    GENERIC_EVIDENCE_DATASET_ID,
    GENERIC_CATALOG_DATASET_ID,
    OFFICIAL_CANONICAL_DATASET_ID,
    OFFICIAL_EVIDENCE_DATASET_ID,
)
CALENDAR_DATASET_IDS = (
    CALENDAR_LEGACY_DATASET_ID,
    CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
    CALENDAR_INCREMENTAL_EVENT_DATASET_ID,
)
OFFICIAL_VINTAGE_SERIES_IDS = frozenset(
    {
        GDP_REAL_SERIES_ID,
        GDP_NOMINAL_SERIES_ID,
        GDI_REAL_SERIES_ID,
        GDI_NOMINAL_SERIES_ID,
        CPI_ALL_ITEMS_SERIES_ID,
        CPI_CORE_SERIES_ID,
        TOTAL_NONFARM_PAYROLLS_SERIES_ID,
        UNEMPLOYMENT_RATE_SERIES_ID,
        RTDSM_NOMINAL_OUTPUT_SERIES_ID,
        RTDSM_REAL_OUTPUT_SERIES_ID,
    }
)

_MAX_VERSION_CANDIDATES = 200_000
_MAX_CALENDAR_CANDIDATES = 200_000
_MAX_BATCH_SERIES = 20
_FMP_CALENDAR_COMMAND = "fmp.macro.us_economic_calendar_wholesale"


def _required_text(value: object, *, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{label} is invalid")
    return value.strip()


def _optional_text(value: object, *, label: str, maximum: int = 500) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValidationError(f"{label} is invalid")
    normalized = value.strip()
    return normalized or None


def _limit(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ResourceLimitError(f"{label} must be from 1 through {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class MacroCatalogQuery:
    query: str = ""
    provider: str | None = None
    frequency: str | None = None
    limit: int = 500

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or len(self.query) > 500:
            raise ValidationError("Macro catalog query is invalid")
        object.__setattr__(
            self,
            "provider",
            _optional_text(self.provider, label="Macro provider", maximum=100),
        )
        object.__setattr__(
            self,
            "frequency",
            _optional_text(self.frequency, label="Macro frequency", maximum=100),
        )
        _limit(self.limit, label="Macro catalog limit", maximum=500)


@dataclass(frozen=True, slots=True)
class MacroSeriesRequest:
    series_id: str
    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    limit: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "series_id",
            _required_text(self.series_id, label="Macro series ID", maximum=200),
        )
        start = (
            None
            if self.start_date is None
            else parse_date(self.start_date, pointer="/start_date")
        )
        end = (
            None
            if self.end_date is None
            else parse_date(self.end_date, pointer="/end_date")
        )
        if start is not None and end is not None and end < start:
            raise ValidationError("Macro end_date cannot precede start_date")
        if self.mode not in {"latest", "as_of", "first_release"}:
            raise ValidationError("Macro selection mode is unsupported")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Macro date-only policy is unsupported") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if self.mode == "as_of":
            if not isinstance(self.as_of, str) or not self.as_of:
                raise ValidationError("Macro as_of mode requires a cutoff")
            TemporalValue.parse(self.as_of, pointer="/as_of")
        elif self.as_of is not None:
            raise ValidationError("Macro cutoff is only valid in as_of mode")
        _limit(self.limit, label="Macro series limit", maximum=10_000)

    @property
    def cutoff(self) -> TemporalValue | None:
        if self.as_of is None:
            return None
        return TemporalValue.parse(self.as_of, pointer="/as_of")

    @property
    def start_bound(self) -> str:
        return self.start_date or "0001-01-01"

    @property
    def end_bound(self) -> str:
        return self.end_date or "9999-12-31"


@dataclass(frozen=True, slots=True)
class MacroReleaseCalendarQuery:
    start_date: str | None
    end_date: str | None
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    event_name: str | None
    limit: int
    after: tuple[str, str, str] | None = None

    def __post_init__(self) -> None:
        start = (
            None
            if self.start_date is None
            else parse_date(self.start_date, pointer="/start_date")
        )
        end = (
            None
            if self.end_date is None
            else parse_date(self.end_date, pointer="/end_date")
        )
        if start is not None and end is not None and end < start:
            raise ValidationError("Calendar end_date cannot precede start_date")
        if self.mode not in {"latest", "as_of"}:
            raise ValidationError(
                "Release calendar supports latest and as_of; first_release is not a calendar mode"
            )
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Calendar date-only policy is unsupported") from exc
        object.__setattr__(self, "date_only_policy", policy)
        object.__setattr__(
            self,
            "event_name",
            _optional_text(
                self.event_name, label="Calendar event-name filter", maximum=256
            ),
        )
        if self.mode == "as_of":
            if not isinstance(self.as_of, str) or not self.as_of:
                raise ValidationError("Calendar as_of mode requires a cutoff")
            TemporalValue.parse(self.as_of, pointer="/as_of")
        elif self.as_of is not None:
            raise ValidationError("Calendar cutoff is only valid in as_of mode")
        _limit(self.limit, label="Calendar limit", maximum=10_000)
        if self.after is not None:
            if not isinstance(self.after, tuple) or len(self.after) != 3:
                raise ValidationError("Calendar after key is invalid")
            event_at, event_name, event_id = self.after
            if (
                not isinstance(event_at, str)
                or not event_at
                or len(event_at) > 100
                or not isinstance(event_name, str)
                or not event_name
                or len(event_name) > 256
                or not isinstance(event_id, str)
                or not event_id
                or len(event_id) > 200
            ):
                raise ValidationError("Calendar after key is invalid")

    @property
    def cutoff(self) -> TemporalValue | None:
        if self.as_of is None:
            return None
        return TemporalValue.parse(self.as_of, pointer="/as_of")


@dataclass(frozen=True, slots=True)
class MacroSeriesDescriptor:
    series_id: str
    provider: str
    provider_series_code: str
    title: str
    description: str | None
    category: str | None
    frequency: str
    unit: str
    value_representation: str
    scale: str
    availability_basis: str
    supported_modes: tuple[str, ...]
    storage_model: str
    active: bool
    coverage_start: str | None
    coverage_end: str | None
    observation_count: int
    version_count: int
    release_count: int
    first_release_count: int

    def to_record(self) -> dict[str, str | int | bool | None]:
        return {
            "active": self.active,
            "availability_basis": self.availability_basis,
            "category": self.category,
            "coverage_end": self.coverage_end,
            "coverage_start": self.coverage_start,
            "description": self.description,
            "first_release_count": self.first_release_count,
            "frequency": self.frequency,
            "observation_count": self.observation_count,
            "provider": self.provider,
            "provider_series_code": self.provider_series_code,
            "release_count": self.release_count,
            "scale": self.scale,
            "series_id": self.series_id,
            "storage_model": self.storage_model,
            "supported_modes_json": dumps_strict(list(self.supported_modes)),
            "title": self.title,
            "unit": self.unit,
            "value_representation": self.value_representation,
            "version_count": self.version_count,
        }


@dataclass(frozen=True, slots=True)
class MacroCatalogSelection:
    descriptors: tuple[MacroSeriesDescriptor, ...]
    total_count: int
    truncated: bool
    migration_ids: tuple[str, ...]
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class MacroCatalogDescription:
    descriptor: MacroSeriesDescriptor
    migration_ids: tuple[str, ...]
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class MacroSeriesSelection:
    descriptor: MacroSeriesDescriptor
    records: tuple[Mapping[str, str | Decimal | int | bool | None], ...]
    warnings: tuple[str, ...]
    truncated: bool
    total_selected_count: int
    first_release_candidate_group_count: int
    first_release_evidenced_group_count: int
    first_release_missing_evidence_group_count: int
    migration_ids: tuple[str, ...]
    receipt_sha256: str
    dataset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FirstReleaseCoverage:
    """Selection coverage for explicitly evidenced first releases only."""

    candidate_group_count: int
    evidenced_group_count: int
    missing_evidence_group_count: int


@dataclass(frozen=True, slots=True)
class MacroCalendarSelection:
    records: tuple[Mapping[str, str | int | None], ...]
    warnings: tuple[str, ...]
    truncated: bool
    total_selected_count: int
    migration_ids: tuple[str, ...]
    receipt_sha256: str


def _stored_modes(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        raise ValidationError("Stored macro supported modes are invalid")
    value = loads_strict(raw, max_bytes=1024)
    if (
        not isinstance(value, list)
        or not value
        or len(value) != len(set(value))
        or any(item not in {"latest", "as_of", "first_release"} for item in value)
    ):
        raise ValidationError("Stored macro supported modes are invalid")
    return tuple(str(item) for item in value)


def _decimal_or_none(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Stored macro value is invalid") from exc
    if not value.is_finite():
        raise ValidationError("Stored macro value is invalid")
    return value


def _temporal(raw: object, precision: object, *, pointer: str) -> TemporalValue:
    value = TemporalValue.parse(str(raw), pointer=pointer)
    if value.precision.value != str(precision):
        raise ValidationError("Stored temporal precision is inconsistent")
    return value


def _release_order_rank(value: object) -> tuple[int, int, str]:
    text = str(value)
    try:
        return (0, int(text), "")
    except ValueError:
        return (1, 0, text)


def _generic_rank(row: Mapping[str, Any]) -> tuple[tuple[int, int, str], int, str]:
    return (
        _release_order_rank(row["source_release_order"]),
        int(row["correction_sequence"]),
        str(row["version_id"]),
    )


def _period_bounds(period: str, frequency: str) -> tuple[str, str]:
    if frequency == "monthly" and len(period) == 7 and period[4] == "-":
        start = parse_date(period + "-01", pointer="/stored/period")
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)
        return start.isoformat(), (next_month - timedelta(days=1)).isoformat()
    if frequency == "quarterly" and len(period) == 6 and period[4] == "Q":
        try:
            year = int(period[:4])
            quarter = int(period[5])
        except ValueError as exc:
            raise ValidationError("Stored macro quarter is invalid") from exc
        if quarter not in {1, 2, 3, 4}:
            raise ValidationError("Stored macro quarter is invalid")
        month = 1 + (quarter - 1) * 3
        start = date(year, month, 1)
        next_quarter = date(year + 1, 1, 1) if quarter == 4 else date(year, month + 3, 1)
        return start.isoformat(), (next_quarter - timedelta(days=1)).isoformat()
    parsed = parse_date(period, pointer="/stored/period")
    return parsed.isoformat(), parsed.isoformat()


def _migration_receipt(connection: Any) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    rows = list(
        connection.execute(
            "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
        )
    )
    ids = tuple(str(row["migration_id"]) for row in rows)
    material = [
        {"migration_id": str(row["migration_id"]), "sha256": str(row["sha256"])}
        for row in rows
    ]
    return ids, material


def _reconcile_store_contract(
    connection: Any,
    registry: Registry,
    dataset_ids: Iterable[str],
) -> None:
    """Fail closed unless reviewed macro migrations and datasets match the store."""

    migrations = list(
        connection.execute(
            """
            SELECT migration_id, store_role, ordinal, resource, sha256,
                   reconstruction_state
            FROM schema_migrations
            ORDER BY ordinal
            """
        )
    )
    expected_migrations = tuple(
        (
            item.id,
            item.store,
            item.ordinal,
            item.resource,
            item.sha256,
            item.reconstruction_state,
        )
        for item in sorted(
            registry.migrations_for(StoreRole.MACRO.value),
            key=lambda item: item.ordinal,
        )
    )
    actual_migrations = tuple(
        (
            str(row["migration_id"]),
            str(row["store_role"]),
            int(row["ordinal"]),
            str(row["resource"]),
            str(row["sha256"]),
            str(row["reconstruction_state"]),
        )
        for row in migrations
    )
    if actual_migrations != expected_migrations:
        raise StoreUnavailableError(
            "Macro store migration ledger does not match the reviewed registry"
        )

    requested = tuple(sorted(set(dataset_ids)))
    declarations = {
        item.id: item
        for item in registry.datasets_for(StoreRole.MACRO.value)
        if item.id in requested
    }
    if set(declarations) != set(requested):
        raise StoreUnavailableError(
            "Reviewed registry lacks the required macro dataset contract"
        )
    placeholders = ", ".join("?" for _ in requested)
    dataset_rows = list(
        connection.execute(
            f"""
            SELECT dataset.dataset_id, dataset.store_role, dataset.layer,
                   dataset.schema_version, dataset.relations_json, dataset.active,
                   identity.identity_sha256, identity.registered_version
            FROM dataset_registry AS dataset
            JOIN dataset_identity_contracts AS identity
              ON identity.dataset_id=dataset.dataset_id
            WHERE dataset.dataset_id IN ({placeholders})
            ORDER BY dataset.dataset_id
            """,
            requested,
        )
    )
    expected_datasets = tuple(
        (
            declaration.id,
            declaration.store,
            declaration.layer,
            declaration.version,
            dumps_strict(list(declaration.relations)),
            int(declaration.active),
            declaration.identity_sha256,
            declaration.version,
        )
        for declaration in sorted(declarations.values(), key=lambda item: item.id)
    )
    actual_datasets = tuple(
        (
            str(row["dataset_id"]),
            str(row["store_role"]),
            str(row["layer"]),
            str(row["schema_version"]),
            str(row["relations_json"]),
            int(row["active"]),
            str(row["identity_sha256"]),
            str(row["registered_version"]),
        )
        for row in dataset_rows
    )
    if actual_datasets != expected_datasets:
        raise StoreUnavailableError(
            "Macro store dataset declarations do not match the reviewed registry"
        )


class CanonicalMacroRepository:
    """Fixed host-routed reader for canonical macro facts."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise ValidationError("Canonical macro dependencies are invalid")
        declarations = {
            item.id: item for item in registry.datasets_for(StoreRole.MACRO.value)
        }
        self._stores = store_map
        self._registry = registry
        self._dataset_declarations = declarations

    def _require_datasets(self, dataset_ids: Iterable[str]) -> None:
        required = set(dataset_ids)
        if not required.issubset(self._dataset_declarations) or any(
            self._dataset_declarations[item].store != StoreRole.MACRO.value
            for item in required
        ):
            raise ValidationError("Canonical macro datasets are not registered")

    @staticmethod
    def _generic_descriptors(connection: Any) -> list[MacroSeriesDescriptor]:
        rows = list(
            connection.execute(
                """
                WITH current_stats AS (
                    SELECT current.series_id, COUNT(*) AS observation_count,
                           MIN(current.period_start) AS observed_start,
                           MAX(current.period_end) AS observed_end
                    FROM macro_observations AS current
                    JOIN macro_observation_versions AS version
                      ON version.version_id=current.current_version_id
                    WHERE version.state='active'
                    GROUP BY current.series_id
                ),
                version_stats AS (
                    SELECT series_id, COUNT(*) AS version_count
                    FROM macro_observation_versions
                    GROUP BY series_id
                ),
                release_stats AS (
                    SELECT series_id, COUNT(*) AS release_count,
                           SUM(CASE
                               WHEN is_first_release=1
                                AND first_release_evidence IS NOT NULL
                               THEN 1 ELSE 0 END) AS first_release_count
                    FROM macro_releases
                    GROUP BY series_id
                )
                SELECT series.series_id, series.provider,
                       series.provider_series_code, series.title,
                       series.description, series.category, series.frequency,
                       series.unit, series.value_representation, series.scale,
                       series.availability_basis, series.supported_modes_json,
                       series.active, series.coverage_start, series.coverage_end,
                       COALESCE(current.observation_count, 0) AS observation_count,
                       COALESCE(version.version_count, 0) AS version_count,
                       COALESCE(release.release_count, 0) AS release_count,
                       COALESCE(release.first_release_count, 0) AS first_release_count,
                       current.observed_start, current.observed_end
                FROM macro_series AS series
                LEFT JOIN current_stats AS current
                  ON current.series_id=series.series_id
                LEFT JOIN version_stats AS version
                  ON version.series_id=series.series_id
                LEFT JOIN release_stats AS release
                  ON release.series_id=series.series_id
                ORDER BY series.series_id
                """
            )
        )
        result: list[MacroSeriesDescriptor] = []
        for row in rows:
            series_id = str(row["series_id"])
            if series_id in OFFICIAL_VINTAGE_SERIES_IDS:
                continue
            modes = _stored_modes(row["supported_modes_json"])
            result.append(
                MacroSeriesDescriptor(
                    series_id=series_id,
                    provider=str(row["provider"]),
                    provider_series_code=str(row["provider_series_code"]),
                    title=str(row["title"]),
                    description=(
                        None if row["description"] is None else str(row["description"])
                    ),
                    category=None if row["category"] is None else str(row["category"]),
                    frequency=str(row["frequency"]),
                    unit=str(row["unit"]),
                    value_representation=str(row["value_representation"]),
                    scale=str(row["scale"]),
                    availability_basis=str(row["availability_basis"]),
                    supported_modes=modes,
                    storage_model="generic_version_core",
                    active=bool(row["active"]),
                    coverage_start=(
                        str(row["coverage_start"])
                        if row["coverage_start"] is not None
                        else (
                            None
                            if row["observed_start"] is None
                            else str(row["observed_start"])
                        )
                    ),
                    coverage_end=(
                        str(row["coverage_end"])
                        if row["coverage_end"] is not None
                        else (
                            None
                            if row["observed_end"] is None
                            else str(row["observed_end"])
                        )
                    ),
                    observation_count=int(row["observation_count"]),
                    version_count=int(row["version_count"]),
                    release_count=int(row["release_count"]),
                    first_release_count=int(row["first_release_count"]),
                )
            )
        return result

    @staticmethod
    def _official_descriptors(connection: Any) -> list[MacroSeriesDescriptor]:
        rows = list(
            connection.execute(
                """
                WITH current_stats AS (
                    SELECT series_id, COUNT(*) AS observation_count,
                           MIN(period) AS observed_start,
                           MAX(period) AS observed_end
                    FROM macro_live_vintage_observations
                    GROUP BY series_id
                ),
                version_stats AS (
                    SELECT series_id, COUNT(*) AS version_count
                    FROM macro_live_vintage_observation_versions
                    GROUP BY series_id
                ),
                release_stats AS (
                    SELECT series_id, COUNT(*) AS release_count,
                           SUM(CASE
                               WHEN is_first_release=1
                                AND first_release_evidence IS NOT NULL
                               THEN 1 ELSE 0 END) AS first_release_count
                    FROM macro_live_vintage_releases
                    GROUP BY series_id
                )
                SELECT series.series_id, series.provider,
                       series.provider_series_code, series.title,
                       series.frequency, series.unit,
                       series.value_representation, series.availability_basis,
                       COALESCE(current.observation_count, 0) AS observation_count,
                       COALESCE(version.version_count, 0) AS version_count,
                       COALESCE(release.release_count, 0) AS release_count,
                       COALESCE(release.first_release_count, 0) AS first_release_count,
                       current.observed_start, current.observed_end
                FROM macro_live_vintage_series AS series
                LEFT JOIN current_stats AS current
                  ON current.series_id=series.series_id
                LEFT JOIN version_stats AS version
                  ON version.series_id=series.series_id
                LEFT JOIN release_stats AS release
                  ON release.series_id=series.series_id
                ORDER BY series.series_id
                """
            )
        )
        result: list[MacroSeriesDescriptor] = []
        for row in rows:
            frequency = str(row["frequency"])
            coverage_start = None
            coverage_end = None
            if row["observed_start"] is not None:
                coverage_start = _period_bounds(
                    str(row["observed_start"]), frequency
                )[0]
            if row["observed_end"] is not None:
                coverage_end = _period_bounds(str(row["observed_end"]), frequency)[1]
            first_release_count = int(row["first_release_count"])
            modes = (
                ("latest", "as_of", "first_release")
                if first_release_count
                else ("latest", "as_of")
            )
            result.append(
                MacroSeriesDescriptor(
                    series_id=str(row["series_id"]),
                    provider=str(row["provider"]),
                    provider_series_code=str(row["provider_series_code"]),
                    title=str(row["title"]),
                    description=None,
                    category="official_vintage",
                    frequency=frequency,
                    unit=str(row["unit"]),
                    value_representation=str(row["value_representation"]),
                    scale="1",
                    availability_basis=str(row["availability_basis"]),
                    supported_modes=modes,
                    storage_model="official_vintage",
                    active=True,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    observation_count=int(row["observation_count"]),
                    version_count=int(row["version_count"]),
                    release_count=int(row["release_count"]),
                    first_release_count=first_release_count,
                )
            )
        return result

    def _catalog(self, connection: Any) -> list[MacroSeriesDescriptor]:
        descriptors = self._generic_descriptors(connection)
        descriptors.extend(self._official_descriptors(connection))
        descriptors.sort(key=lambda item: item.series_id)
        if len({item.series_id for item in descriptors}) != len(descriptors):
            raise ValidationError("Canonical macro catalog contains duplicate series IDs")
        return descriptors

    def search_series(self, query: MacroCatalogQuery) -> MacroCatalogSelection:
        if not isinstance(query, MacroCatalogQuery):
            raise ValidationError("Macro catalog search requires a typed query")
        self._require_datasets(MACRO_ACCESS_DATASET_IDS)
        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as connection:
            _reconcile_store_contract(
                connection, self._registry, MACRO_ACCESS_DATASET_IDS
            )
            descriptors = self._catalog(connection)
            migration_ids, migrations = _migration_receipt(connection)
        needle = query.query.strip().casefold()
        selected: list[MacroSeriesDescriptor] = []
        for item in descriptors:
            if not item.active:
                continue
            if query.provider and item.provider.casefold() != query.provider.casefold():
                continue
            if query.frequency and item.frequency.casefold() != query.frequency.casefold():
                continue
            haystack = " ".join(
                value
                for value in (
                    item.series_id,
                    item.provider,
                    item.provider_series_code,
                    item.title,
                    item.description or "",
                    item.category or "",
                )
                if value
            ).casefold()
            if needle and needle not in haystack:
                continue
            selected.append(item)
        total = len(selected)
        truncated = total > query.limit
        selected = selected[: query.limit]
        receipt = {
            "filters": {
                "frequency": query.frequency,
                "provider": query.provider,
                "query": query.query,
            },
            "migrations": migrations,
            "selected_series": [item.series_id for item in selected],
            "total_count": total,
            "truncated": truncated,
        }
        return MacroCatalogSelection(
            descriptors=tuple(selected),
            total_count=total,
            truncated=truncated,
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
        )

    def describe_series(self, series_id: str) -> MacroCatalogDescription:
        self._require_datasets(MACRO_ACCESS_DATASET_IDS)
        normalized = _required_text(
            series_id, label="Macro series ID", maximum=200
        )
        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as connection:
            _reconcile_store_contract(
                connection, self._registry, MACRO_ACCESS_DATASET_IDS
            )
            descriptors = {item.series_id: item for item in self._catalog(connection)}
            migration_ids, migrations = _migration_receipt(connection)
        descriptor = descriptors.get(normalized)
        if descriptor is None:
            if normalized in OFFICIAL_VINTAGE_SERIES_IDS:
                raise ValidationError(
                    "Requested official-vintage macro series is not populated"
                )
            raise ValidationError("Requested macro series is unavailable")
        receipt = {
            "descriptor": descriptor.to_record(),
            "migrations": migrations,
            "series_id": normalized,
        }
        return MacroCatalogDescription(
            descriptor=descriptor,
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _generic_rows(connection: Any, request: MacroSeriesRequest) -> list[Any]:
        if request.mode == "latest":
            rows = list(
                connection.execute(
                    """
                    SELECT version.*, release.vintage_at,
                           release.vintage_precision,
                           release.source_release_order,
                           release.is_first_release,
                           release.first_release_evidence,
                           release.release_stage
                    FROM macro_observations AS current
                    JOIN macro_observation_versions AS version
                      ON version.version_id=current.current_version_id
                    JOIN macro_releases AS release
                      ON release.release_id=version.release_id
                    WHERE current.series_id=?
                      AND current.period_start>=? AND current.period_start<=?
                      AND version.state='active'
                    ORDER BY current.period_start, current.period_end,
                             current.dimensions_digest
                    LIMIT ?
                    """,
                    (
                        request.series_id,
                        request.start_bound,
                        request.end_bound,
                        _MAX_VERSION_CANDIDATES + 1,
                    ),
                )
            )
            if len(rows) > _MAX_VERSION_CANDIDATES:
                raise ResourceLimitError(
                    "Current macro observations exceed the read bound"
                )
            return rows
        rows = list(
            connection.execute(
                """
                SELECT version.*, release.vintage_at,
                       release.vintage_precision,
                       release.source_release_order,
                       release.is_first_release,
                       release.first_release_evidence,
                       release.release_stage
                FROM macro_observation_versions AS version
                JOIN macro_releases AS release
                  ON release.release_id=version.release_id
                WHERE version.series_id=?
                  AND version.period_start>=? AND version.period_start<=?
                ORDER BY version.period_start, version.period_end,
                         version.dimensions_digest, release.source_release_order,
                         version.correction_sequence, version.version_id
                LIMIT ?
                """,
                (
                    request.series_id,
                    request.start_bound,
                    request.end_bound,
                    _MAX_VERSION_CANDIDATES + 1,
                ),
            )
        )
        if len(rows) > _MAX_VERSION_CANDIDATES:
            raise ResourceLimitError("Macro version history exceeds the read bound")
        return rows

    @staticmethod
    def _select_generic(
        rows: Iterable[Mapping[str, Any]], request: MacroSeriesRequest
    ) -> tuple[list[Mapping[str, Any]], set[str], _FirstReleaseCoverage]:
        grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[
                (
                    str(row["period_start"]),
                    str(row["period_end"]),
                    str(row["dimensions_digest"]),
                )
            ].append(row)
        selected: list[Mapping[str, Any]] = []
        warnings: set[str] = set()
        first_release_evidenced_group_count = 0
        for candidates in grouped.values():
            if request.mode == "first_release":
                evidenced = [
                    row
                    for row in candidates
                    if int(row["is_first_release"]) == 1
                    and row["first_release_evidence"] is not None
                ]
                if evidenced:
                    selected.append(min(evidenced, key=_generic_rank))
                    first_release_evidenced_group_count += 1
                continue
            if request.mode == "as_of":
                cutoff = request.cutoff
                assert cutoff is not None
                eligible: list[Mapping[str, Any]] = []
                for row in candidates:
                    available = _temporal(
                        row["available_at"],
                        row["available_precision"],
                        pointer="/stored/available_at",
                    )
                    decision = availability_at_or_before(
                        available, cutoff, request.date_only_policy
                    )
                    if decision.included:
                        eligible.append(row)
                        warnings.update(decision.warnings)
                if eligible:
                    selected.append(max(eligible, key=_generic_rank))
                continue
            selected.append(candidates[0])
        selected.sort(
            key=lambda row: (
                str(row["period_start"]),
                str(row["period_end"]),
                str(row["dimensions_digest"]),
                str(row["version_id"]),
            )
        )
        first_release_candidate_group_count = (
            len(grouped) if request.mode == "first_release" else 0
        )
        return (
            selected,
            warnings,
            _FirstReleaseCoverage(
                candidate_group_count=first_release_candidate_group_count,
                evidenced_group_count=first_release_evidenced_group_count,
                missing_evidence_group_count=(
                    first_release_candidate_group_count
                    - first_release_evidenced_group_count
                ),
            ),
        )

    @staticmethod
    def _generic_record(
        row: Mapping[str, Any], descriptor: MacroSeriesDescriptor
    ) -> dict[str, str | Decimal | int | bool | None]:
        dimensions = loads_strict(str(row["dimensions_json"]), max_bytes=4096)
        if not isinstance(dimensions, dict) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in dimensions.items()
        ):
            raise ValidationError("Stored macro dimensions are invalid")
        return {
            "artifact_id": str(row["artifact_id"]),
            "availability_basis": descriptor.availability_basis,
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "capture_id": None,
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "correction_sequence": int(row["correction_sequence"]),
            "dimensions_json": dumps_strict(dimensions),
            "first_release_evidence": (
                None
                if row["first_release_evidence"] is None
                else str(row["first_release_evidence"])
            ),
            "is_first_release": bool(row["is_first_release"]),
            "missing_reason": (
                None if row["missing_reason"] is None else str(row["missing_reason"])
            ),
            "period_end": str(row["period_end"]),
            "period_start": str(row["period_start"]),
            "provider": descriptor.provider,
            "release_id": str(row["release_id"]),
            "release_stage": (
                None if row["release_stage"] is None else str(row["release_stage"])
            ),
            "run_id": str(row["run_id"]),
            "scale": str(row["scale"]),
            "series_id": descriptor.series_id,
            "snapshot_id": str(row["snapshot_id"]),
            "source_period": str(row["period_start"]),
            "source_release_order": str(row["source_release_order"]),
            "source_row": int(row["source_row"]),
            "source_vintage_identity": str(row["source_vintage_identity"]),
            "storage_model": descriptor.storage_model,
            "unit": str(row["unit"]),
            "value": _decimal_or_none(row["value_text"]),
            "value_representation": str(row["value_representation"]),
            "version_id": str(row["version_id"]),
            "vintage_at": str(row["vintage_at"]),
            "vintage_precision": str(row["vintage_precision"]),
        }

    @staticmethod
    def _official_rows(connection: Any, request: MacroSeriesRequest) -> list[Any]:
        if request.mode == "latest":
            rows = list(
                connection.execute(
                    """
                    SELECT version.*, release.vintage_at,
                           release.vintage_precision,
                           release.source_release_order,
                           release.availability_basis,
                           release.is_first_release,
                           release.first_release_evidence,
                           release.release_stage,
                           capture.response_sha256
                    FROM macro_live_vintage_observations AS current
                    JOIN macro_live_vintage_observation_versions AS version
                      ON version.version_id=current.current_version_id
                    JOIN macro_live_vintage_releases AS release
                      ON release.release_id=version.release_id
                    JOIN macro_live_vintage_captures AS capture
                      ON capture.capture_id=version.capture_id
                    WHERE current.series_id=?
                    ORDER BY current.period, version.version_id
                    LIMIT ?
                    """,
                    (request.series_id, _MAX_VERSION_CANDIDATES + 1),
                )
            )
        else:
            rows = list(
                connection.execute(
                    """
                    SELECT version.*, release.vintage_at,
                           release.vintage_precision,
                           release.source_release_order,
                           release.availability_basis,
                           release.is_first_release,
                           release.first_release_evidence,
                           release.release_stage,
                           capture.response_sha256
                    FROM macro_live_vintage_observation_versions AS version
                    JOIN macro_live_vintage_releases AS release
                      ON release.release_id=version.release_id
                    JOIN macro_live_vintage_captures AS capture
                      ON capture.capture_id=version.capture_id
                    WHERE version.series_id=?
                    ORDER BY version.period, release.source_release_order,
                             version.correction_sequence, version.version_id
                    LIMIT ?
                    """,
                    (request.series_id, _MAX_VERSION_CANDIDATES + 1),
                )
            )
        if len(rows) > _MAX_VERSION_CANDIDATES:
            raise ResourceLimitError("Official macro history exceeds the read bound")
        return rows

    @staticmethod
    def _official_rank(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
        """Replicate the publisher's current-pointer ordering exactly."""

        return (
            str(row["available_at"]),
            str(row["source_release_order"]),
            int(row["correction_sequence"]),
            str(row["version_id"]),
        )

    @classmethod
    def _select_official(
        cls,
        rows: Iterable[Mapping[str, Any]],
        request: MacroSeriesRequest,
        frequency: str,
    ) -> tuple[list[Mapping[str, Any]], set[str], _FirstReleaseCoverage]:
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            period_start, _ = _period_bounds(str(row["period"]), frequency)
            if period_start < request.start_bound or period_start > request.end_bound:
                continue
            grouped[str(row["period"])].append(row)
        selected: list[Mapping[str, Any]] = []
        warnings: set[str] = set()
        first_release_evidenced_group_count = 0
        for candidates in grouped.values():
            if request.mode == "latest":
                selected.append(candidates[0])
                continue
            if request.mode == "first_release":
                evidenced = [
                    row
                    for row in candidates
                    if row["is_first_release"] is not None
                    and int(row["is_first_release"]) == 1
                    and row["first_release_evidence"] is not None
                ]
                if evidenced:
                    selected.append(min(evidenced, key=cls._official_rank))
                    first_release_evidenced_group_count += 1
                continue
            cutoff = request.cutoff
            assert cutoff is not None
            eligible: list[Mapping[str, Any]] = []
            for row in candidates:
                available = _temporal(
                    row["available_at"],
                    row["available_precision"],
                    pointer="/stored/available_at",
                )
                decision = availability_at_or_before(
                    available, cutoff, request.date_only_policy
                )
                if decision.included:
                    eligible.append(row)
                    warnings.update(decision.warnings)
            if eligible:
                selected.append(max(eligible, key=cls._official_rank))
        selected.sort(key=lambda row: (str(row["period"]), str(row["version_id"])))
        first_release_candidate_group_count = (
            len(grouped) if request.mode == "first_release" else 0
        )
        return (
            selected,
            warnings,
            _FirstReleaseCoverage(
                candidate_group_count=first_release_candidate_group_count,
                evidenced_group_count=first_release_evidenced_group_count,
                missing_evidence_group_count=(
                    first_release_candidate_group_count
                    - first_release_evidenced_group_count
                ),
            ),
        )

    @staticmethod
    def _official_record(
        row: Mapping[str, Any], descriptor: MacroSeriesDescriptor
    ) -> dict[str, str | Decimal | int | bool | None]:
        period_start, period_end = _period_bounds(
            str(row["period"]), descriptor.frequency
        )
        return {
            "artifact_id": None,
            "availability_basis": str(row["availability_basis"]),
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "capture_id": str(row["capture_id"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "correction_sequence": int(row["correction_sequence"]),
            "dimensions_json": "{}",
            "first_release_evidence": (
                None
                if row["first_release_evidence"] is None
                else str(row["first_release_evidence"])
            ),
            "is_first_release": (
                None if row["is_first_release"] is None else bool(row["is_first_release"])
            ),
            "missing_reason": None,
            "period_end": period_end,
            "period_start": period_start,
            "provider": descriptor.provider,
            "release_id": str(row["release_id"]),
            "release_stage": (
                None if row["release_stage"] is None else str(row["release_stage"])
            ),
            "run_id": None,
            "scale": descriptor.scale,
            "series_id": descriptor.series_id,
            "snapshot_id": None,
            "source_period": str(row["period"]),
            "source_release_order": str(row["source_release_order"]),
            "source_row": int(row["source_row"]),
            "source_vintage_identity": str(row["source_vintage_identity"]),
            "storage_model": descriptor.storage_model,
            "unit": str(row["unit"]),
            "value": _decimal_or_none(row["value_text"]),
            "value_representation": descriptor.value_representation,
            "version_id": str(row["version_id"]),
            "vintage_at": (
                None if row["vintage_at"] is None else str(row["vintage_at"])
            ),
            "vintage_precision": (
                None
                if row["vintage_precision"] is None
                else str(row["vintage_precision"])
            ),
        }

    def _select_series_in_connection(
        self,
        connection: Any,
        request: MacroSeriesRequest,
        *,
        descriptors: Mapping[str, MacroSeriesDescriptor],
        migration_ids: tuple[str, ...],
        migrations: list[dict[str, str]],
    ) -> MacroSeriesSelection:
        """Select one typed series from an already-pinned macro snapshot."""

        descriptor = descriptors.get(request.series_id)
        if descriptor is None:
            if request.series_id in OFFICIAL_VINTAGE_SERIES_IDS:
                raise ValidationError(
                    "Requested official-vintage macro series is not populated"
                )
            raise ValidationError("Requested macro series is unavailable")
        if request.mode not in descriptor.supported_modes:
            raise ValidationError(
                "Requested macro mode is not evidenced for this series"
            )
        if request.mode == "first_release" and not descriptor.first_release_count:
            raise ValidationError(
                "Requested macro series has no explicit first-release evidence"
            )
        if descriptor.storage_model == "official_vintage":
            raw_rows = self._official_rows(connection, request)
            selected, warnings, first_release_coverage = self._select_official(
                raw_rows, request, descriptor.frequency
            )
            make_record = self._official_record
            dataset_ids = (
                OFFICIAL_CANONICAL_DATASET_ID,
                OFFICIAL_EVIDENCE_DATASET_ID,
            )
        else:
            raw_rows = self._generic_rows(connection, request)
            selected, warnings, first_release_coverage = self._select_generic(
                raw_rows, request
            )
            make_record = self._generic_record
            dataset_ids = (
                GENERIC_CANONICAL_DATASET_ID,
                GENERIC_EVIDENCE_DATASET_ID,
                GENERIC_CATALOG_DATASET_ID,
            )
        active_rows = [
            row
            for row in selected
            if descriptor.storage_model == "official_vintage"
            or str(row["state"]) == "active"
        ]
        total = len(active_rows)
        truncated = total > request.limit
        active_rows = active_rows[: request.limit]
        records = tuple(make_record(row, descriptor) for row in active_rows)
        if truncated:
            warnings.add("result_truncated")
        if request.mode == "as_of":
            warnings.add("point_in_time_selection_applied")
        if (
            request.mode == "first_release"
            and first_release_coverage.candidate_group_count
            > first_release_coverage.evidenced_group_count
        ):
            warnings.add("first_release_evidence_incomplete")
        if descriptor.storage_model == "official_vintage":
            warnings.add("nullable_source_vintage_and_capture_lineage_preserved")
        receipt = {
            "dataset_ids": list(dataset_ids),
            "descriptor": descriptor.to_record(),
            "migrations": migrations,
            "request": {
                "as_of": request.as_of,
                "date_only_policy": request.date_only_policy.value,
                "end_date": request.end_date,
                "limit": request.limit,
                "mode": request.mode,
                "series_id": request.series_id,
                "start_date": request.start_date,
            },
            "first_release_coverage": {
                "candidate_group_count": (
                    first_release_coverage.candidate_group_count
                ),
                "evidenced_group_count": (
                    first_release_coverage.evidenced_group_count
                ),
                "missing_evidence_group_count": (
                    first_release_coverage.missing_evidence_group_count
                ),
            },
            "selected_versions": [record["version_id"] for record in records],
            "total_selected_count": total,
            "truncated": truncated,
            "warnings": sorted(warnings),
        }
        return MacroSeriesSelection(
            descriptor=descriptor,
            records=records,
            warnings=tuple(sorted(warnings)),
            truncated=truncated,
            total_selected_count=total,
            first_release_candidate_group_count=(
                first_release_coverage.candidate_group_count
            ),
            first_release_evidenced_group_count=(
                first_release_coverage.evidenced_group_count
            ),
            first_release_missing_evidence_group_count=(
                first_release_coverage.missing_evidence_group_count
            ),
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
            dataset_ids=dataset_ids,
        )

    def _select_series_batch_in_connection(
        self,
        connection: Any,
        requests: tuple[MacroSeriesRequest, ...],
    ) -> tuple[MacroSeriesSelection, ...]:
        descriptors = {item.series_id: item for item in self._catalog(connection)}
        migration_ids, migrations = _migration_receipt(connection)
        return tuple(
            self._select_series_in_connection(
                connection,
                request,
                descriptors=descriptors,
                migration_ids=migration_ids,
                migrations=migrations,
            )
            for request in requests
        )

    def _get_series_batch_in_connection(
        self,
        connection: Any,
        requests: Iterable[MacroSeriesRequest],
    ) -> tuple[MacroSeriesSelection, ...]:
        """Package-private selection for a host-owned immutable transaction."""

        typed_requests = tuple(requests)
        if not typed_requests or len(typed_requests) > _MAX_BATCH_SERIES:
            raise ResourceLimitError(
                f"Macro series batch must contain 1 through {_MAX_BATCH_SERIES} requests"
            )
        if any(
            not isinstance(request, MacroSeriesRequest) for request in typed_requests
        ):
            raise ValidationError("Canonical macro series requires typed requests")
        self._require_datasets(MACRO_ACCESS_DATASET_IDS)
        _reconcile_store_contract(connection, self._registry, MACRO_ACCESS_DATASET_IDS)
        return self._select_series_batch_in_connection(connection, typed_requests)

    def get_series_batch(
        self, requests: Iterable[MacroSeriesRequest]
    ) -> tuple[MacroSeriesSelection, ...]:
        """Read a bounded group of series from one owned immutable snapshot."""

        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as opened:
            return self._get_series_batch_in_connection(opened, requests)

    def get_series(self, request: MacroSeriesRequest) -> MacroSeriesSelection:
        if not isinstance(request, MacroSeriesRequest):
            raise ValidationError("Canonical macro series requires a typed request")
        return self.get_series_batch((request,))[0]

    @staticmethod
    def _calendar_candidate_record(
        row: Mapping[str, Any], *, source_model: str
    ) -> dict[str, str | int | None]:
        country = str(row["country"])
        event_at = str(row["event_at"])
        event_name = str(row["event_name"])
        currency = None if row["currency"] is None else str(row["currency"])
        if source_model == "incremental_event_version":
            event_id = str(row["event_id"])
            event_version_id = str(row["event_version_id"])
            capture_id = str(row["receipt_id"])
            correction_sequence = int(row["correction_sequence"])
        else:
            event_id = stable_id(
                "fmp_wholesale_calendar_raw_event",
                "fmp",
                country,
                event_at,
                event_name,
                dumps_strict(currency),
            )
            capture_id = str(row["capture_id"])
            event_version_id = stable_id(
                "macro_release_event_version",
                event_id,
                capture_id,
                str(row["source_row"]),
                str(row["row_sha256"]),
            )
            correction_sequence = 0
        return {
            "actual_json": None if row["actual_json"] is None else str(row["actual_json"]),
            "availability_basis": "local_capture",
            "available_at": str(row["captured_at"]),
            "available_precision": str(row["captured_precision"]),
            "capture_id": capture_id,
            "change_json": None if row["change_json"] is None else str(row["change_json"]),
            "change_percentage_json": (
                None
                if row["change_percentage_json"] is None
                else str(row["change_percentage_json"])
            ),
            "country": country,
            "correction_sequence": correction_sequence,
            "currency": currency,
            "estimate_json": (
                None if row["estimate_json"] is None else str(row["estimate_json"])
            ),
            "event_at": event_at,
            "event_id": event_id,
            "event_name": event_name,
            "event_version_id": event_version_id,
            "impact_json": (
                None if row["impact_json"] is None else str(row["impact_json"])
            ),
            "previous_json": (
                None if row["previous_json"] is None else str(row["previous_json"])
            ),
            "provider": "fmp",
            "response_sha256": str(row["response_sha256"]),
            "row_sha256": str(row["row_sha256"]),
            "source_model": source_model,
            "source_row": int(row["source_row"]),
            "unit": None if row["unit"] is None else str(row["unit"]),
        }

    @staticmethod
    def _calendar_datetime(value: str) -> datetime:
        temporal = TemporalValue.parse(value, pointer="/stored/captured_at")
        if temporal.precision is not TemporalPrecision.DATETIME:
            raise ValidationError("Calendar capture time must be an exact datetime")
        assert isinstance(temporal.value, datetime)
        return temporal.value.astimezone(timezone.utc)

    def get_release_calendar(
        self, query: MacroReleaseCalendarQuery
    ) -> MacroCalendarSelection:
        if not isinstance(query, MacroReleaseCalendarQuery):
            raise ValidationError("Release calendar requires a typed query")
        self._require_datasets(CALENDAR_DATASET_IDS)
        start = query.start_date or "0001-01-01"
        end = query.end_date or "9999-12-31"
        with quiet_immutable_read_connection(self._stores, StoreRole.MACRO) as connection:
            _reconcile_store_contract(
                connection, self._registry, CALENDAR_DATASET_IDS
            )
            legacy = list(
                connection.execute(
                    """
                    SELECT row.*, capture.captured_at,
                           capture.captured_precision,
                           capture.response_sha256
                    FROM fmp_economic_calendar_rows AS row
                    JOIN fmp_economic_calendar_captures AS capture
                      ON capture.capture_id=row.capture_id
                    JOIN ingestion_runs AS run
                      ON run.run_id=capture.run_id
                    JOIN ingestion_artifacts AS artifact
                      ON artifact.artifact_id=capture.artifact_id
                     AND artifact.run_id=run.run_id
                    JOIN ingestion_snapshots AS snapshot
                      ON snapshot.snapshot_id=capture.snapshot_id
                     AND snapshot.run_id=run.run_id
                    WHERE SUBSTR(row.event_at, 1, 10)>=?
                      AND SUBSTR(row.event_at, 1, 10)<=?
                      AND run.dataset_id=? AND run.command=?
                      AND run.status='succeeded'
                      AND run.artifact_id=capture.artifact_id
                      AND run.snapshot_id=capture.snapshot_id
                      AND artifact.dataset_id=?
                      AND artifact.content_sha256=capture.response_sha256
                      AND snapshot.dataset_id=?
                      AND snapshot.semantic_identity=capture.semantic_identity
                      AND snapshot.completeness='complete'
                      AND snapshot.validation_state='validated'
                    ORDER BY row.event_at, capture.captured_at,
                             row.capture_id, row.source_row
                    LIMIT ?
                    """,
                    (
                        start,
                        end,
                        CALENDAR_LEGACY_DATASET_ID,
                        _FMP_CALENDAR_COMMAND,
                        CALENDAR_LEGACY_DATASET_ID,
                        CALENDAR_LEGACY_DATASET_ID,
                        _MAX_CALENDAR_CANDIDATES + 1,
                    ),
                )
            )
            incremental = list(
                connection.execute(
                    """
                    SELECT event.event_id, event.event_at, event.country,
                           event.event_name, event.currency_key,
                           version.*, receipt.response_sha256,
                           receipt.captured_precision
                    FROM fmp_economic_calendar_raw_event_versions AS version
                    JOIN fmp_economic_calendar_raw_events AS event
                      ON event.event_id=version.event_id
                    JOIN fmp_economic_calendar_fetch_receipts AS receipt
                      ON receipt.receipt_id=version.receipt_id
                    JOIN ingestion_runs AS run
                      ON run.run_id=receipt.run_id
                    JOIN ingestion_artifacts AS artifact
                      ON artifact.artifact_id=receipt.artifact_id
                     AND artifact.run_id=run.run_id
                    JOIN ingestion_snapshots AS snapshot
                      ON snapshot.snapshot_id=receipt.snapshot_id
                     AND snapshot.run_id=run.run_id
                    WHERE SUBSTR(event.event_at, 1, 10)>=?
                      AND SUBSTR(event.event_at, 1, 10)<=?
                      AND run.dataset_id=? AND run.command=?
                      AND run.status='succeeded'
                      AND run.artifact_id=receipt.artifact_id
                      AND run.snapshot_id=receipt.snapshot_id
                      AND artifact.dataset_id=?
                      AND artifact.content_sha256=receipt.receipt_manifest_sha256
                      AND snapshot.dataset_id=?
                      AND snapshot.semantic_identity=receipt.semantic_identity
                      AND snapshot.completeness='complete'
                      AND snapshot.validation_state='validated'
                    ORDER BY event.event_at, version.captured_at,
                             version.event_version_id
                    LIMIT ?
                    """,
                    (
                        start,
                        end,
                        CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
                        _FMP_CALENDAR_COMMAND,
                        CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
                        CALENDAR_INCREMENTAL_EVIDENCE_DATASET_ID,
                        _MAX_CALENDAR_CANDIDATES + 1,
                    ),
                )
            )
            migration_ids, migrations = _migration_receipt(connection)
        if (
            len(legacy) > _MAX_CALENDAR_CANDIDATES
            or len(incremental) > _MAX_CALENDAR_CANDIDATES
            or len(legacy) + len(incremental) > _MAX_CALENDAR_CANDIDATES
        ):
            raise ResourceLimitError("Calendar version history exceeds the read bound")
        candidates: list[dict[str, str | int | None]] = []
        candidates.extend(
            self._calendar_candidate_record(row, source_model="legacy_wholesale_capture")
            for row in legacy
        )
        candidates.extend(
            self._calendar_candidate_record(row, source_model="incremental_event_version")
            for row in incremental
        )
        needle = None if query.event_name is None else query.event_name.casefold()
        cutoff = query.cutoff
        warnings: set[str] = {
            "calendar_uses_local_capture_availability",
            "first_release_mode_not_applicable_to_schedule_records",
            "provider_native_json_scalars_preserved_as_text",
        }
        selected: dict[str, dict[str, str | int | None]] = {}
        for record in candidates:
            if needle and needle not in str(record["event_name"]).casefold():
                continue
            available = _temporal(
                record["available_at"],
                record["available_precision"],
                pointer="/stored/captured_at",
            )
            if cutoff is not None:
                decision = availability_at_or_before(
                    available, cutoff, query.date_only_policy
                )
                if not decision.included:
                    continue
                warnings.update(decision.warnings)
            event_id = str(record["event_id"])
            current = selected.get(event_id)
            if current is None:
                selected[event_id] = record
                continue
            rank = (
                self._calendar_datetime(str(record["available_at"])),
                1 if record["source_model"] == "incremental_event_version" else 0,
                int(record["correction_sequence"]),
                str(record["event_version_id"]),
            )
            current_rank = (
                self._calendar_datetime(str(current["available_at"])),
                1 if current["source_model"] == "incremental_event_version" else 0,
                int(current["correction_sequence"]),
                str(current["event_version_id"]),
            )
            if rank > current_rank:
                selected[event_id] = record
        ordered = sorted(
            selected.values(),
            key=lambda item: (
                str(item["event_at"]),
                str(item["event_name"]),
                str(item["event_id"]),
            ),
        )
        total = len(ordered)
        if query.after is not None:
            ordered = [
                record
                for record in ordered
                if (
                    str(record["event_at"]),
                    str(record["event_name"]),
                    str(record["event_id"]),
                )
                > query.after
            ]
        truncated = len(ordered) > query.limit
        records = tuple(ordered[: query.limit])
        if truncated:
            warnings.add("result_truncated")
        request = {
            "as_of": query.as_of,
            "date_only_policy": query.date_only_policy.value,
            "end_date": query.end_date,
            "event_name": query.event_name,
            "limit": query.limit,
            "mode": query.mode,
            "start_date": query.start_date,
        }
        if query.after is not None:
            request["after"] = list(query.after)
        receipt = {
            "dataset_ids": list(CALENDAR_DATASET_IDS),
            "migrations": migrations,
            "request": request,
            "selected_versions": [record["event_version_id"] for record in records],
            "total_selected_count": total,
            "truncated": truncated,
        }
        return MacroCalendarSelection(
            records=records,
            warnings=tuple(sorted(warnings)),
            truncated=truncated,
            total_selected_count=total,
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
        )


__all__ = (
    "CALENDAR_DATASET_IDS",
    "MACRO_ACCESS_DATASET_IDS",
    "CanonicalMacroRepository",
    "MacroCatalogDescription",
    "MacroCatalogQuery",
    "MacroReleaseCalendarQuery",
    "MacroSeriesRequest",
)
