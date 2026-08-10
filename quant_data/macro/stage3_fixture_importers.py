"""Offline Stage 3 macro fixture ingestion over the frozen macro store.

This module is deliberately not a public tool.  It accepts only reviewed,
manifest-verified synthetic resources, normalizes them before the shared
coordinator is called, and writes one atomic family capture per fixture.
"""

from __future__ import annotations

import calendar
import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..contracts import IngestionReceipt
from ..errors import ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date
from .stage3_normalizers import Stage3MacroFixtureCandidate, parse_stage3_macro_fixture


_GENERIC_EVIDENCE_DATASET = "fixture.macro.rtdsm_employ_evidence"
_GENERIC_CANONICAL_DATASET = "fixture.macro.rtdsm_employ"
_CATALOG_DATASET = "fixture.macro.stage3_catalog"
_NORMALIZATION_VERSION = "stage3_macro_v1"
_SHA256_HEX_LENGTH = 64
_SUPPORTED_MODES = frozenset({"latest", "as_of", "first_release"})
_LOCAL_CAPTURE_FAMILIES = frozenset({"treasury", "economic_calendar", "soma"})


@dataclass(frozen=True, slots=True)
class _FamilySpec:
    collector_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    identity_dataset_id: str
    output_dataset_ids: tuple[str, ...]


def _spec(
    collector: str,
    evidence: str,
    canonical: str,
    *extra_outputs: str,
) -> _FamilySpec:
    outputs = tuple(
        dict.fromkeys(
            (
                _GENERIC_EVIDENCE_DATASET,
                _GENERIC_CANONICAL_DATASET,
                _CATALOG_DATASET,
                evidence,
                canonical,
                *extra_outputs,
            )
        )
    )
    return _FamilySpec(
        collector_id=f"fixture.macro.{collector}",
        evidence_dataset_id=evidence,
        canonical_dataset_id=canonical,
        identity_dataset_id=_CATALOG_DATASET,
        output_dataset_ids=outputs,
    )


_FAMILY_SPECS: dict[str, _FamilySpec] = {
    "gdp": _spec("gdp_import", _GENERIC_EVIDENCE_DATASET, "fixture.macro.gdp_vintages"),
    "treasury": _spec(
        "treasury_import", _GENERIC_EVIDENCE_DATASET, "fixture.macro.treasury_yield_curves"
    ),
    "economic_calendar": _spec(
        "calendar_import", _GENERIC_EVIDENCE_DATASET, "fixture.macro.economic_calendar"
    ),
    "soma": _spec(
        "soma_import",
        "fixture.macro.soma_evidence",
        "fixture.macro.soma_summary",
    ),
    "eia_retail": _spec(
        "eia_retail_import",
        "fixture.macro.eia_retail_evidence",
        "fixture.macro.eia_retail",
    ),
    "eia_weekly": _spec(
        "eia_weekly_import",
        "fixture.macro.eia_weekly_evidence",
        "fixture.macro.eia_weekly",
    ),
    "recession": _spec(
        "recession_import", _GENERIC_EVIDENCE_DATASET, "fixture.macro.recession_periods"
    ),
    "bls": _spec("bls_import", _GENERIC_EVIDENCE_DATASET, _GENERIC_CANONICAL_DATASET),
    "bis": _spec("bis_import", _GENERIC_EVIDENCE_DATASET, _GENERIC_CANONICAL_DATASET),
    "chicagofed": _spec(
        "chicago_fed_import", _GENERIC_EVIDENCE_DATASET, _GENERIC_CANONICAL_DATASET
    ),
    "bea": _spec("bea_import", _GENERIC_EVIDENCE_DATASET, _GENERIC_CANONICAL_DATASET),
}


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _mapping(value: object, label: str, *, nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or (nonempty and not value):
        raise _fail(f"{label} must be a{' nonempty' if nonempty else ''} object")
    if not all(isinstance(key, str) and key for key in value):
        raise _fail(f"{label} contains an invalid object key")
    return dict(value)


def _array(value: object, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise _fail(f"{label} must be a{' nonempty' if nonempty else ''} array")
    return list(value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label} must be a nonempty string")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise _fail(f"{label} has an unsupported shape")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label} must be a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"{label} must be a finite decimal") from exc
    if not result.is_finite():
        raise _fail(f"{label} must be a finite decimal")
    return result


def _normalized_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _temporal(
    value: object,
    precision: object,
    label: str,
    *,
    nullable_unknown: bool = False,
) -> TemporalValue | None:
    if value is None:
        if nullable_unknown and precision == "unknown":
            return None
        raise _fail(f"{label} is required")
    raw = _string(value, label)
    parsed = TemporalValue.parse(raw, pointer=f"/{label}")
    if precision not in {TemporalPrecision.DATE.value, TemporalPrecision.DATETIME.value}:
        raise _fail(f"{label}_precision is invalid")
    if parsed.precision.value != precision:
        raise _fail(f"{label} precision does not match its value")
    return parsed


def _dimensions(value: object, label: str) -> tuple[dict[str, str], str, str]:
    raw = _mapping(value, label)
    dimensions: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(item, str) or not item:
            raise _fail(f"{label}/{key} must be a nonempty string")
        dimensions[key] = item
    rendered = dumps_strict(dimensions)
    return dimensions, rendered, _sha256(rendered)


@dataclass(frozen=True, slots=True)
class _Series:
    series_id: str
    provider_series_code: str
    title: str
    frequency: str
    unit: str
    value_representation: str
    scale: str
    dimensions: Mapping[str, str]
    dimensions_json: str
    dimensions_digest: str
    availability_basis: str
    supported_modes: tuple[str, ...]
    supported_modes_json: str


@dataclass(frozen=True, slots=True)
class _Release:
    source_vintage_identity: str
    vintage_at: TemporalValue
    published_at: TemporalValue | None
    available_at: TemporalValue
    source_release_order: int
    source_release_order_text: str
    release_stage: str
    is_first_release: bool
    first_release_evidence: str | None


@dataclass(frozen=True, slots=True)
class _Observation:
    series_id: str
    release_identity: str
    period_start: str
    period_end: str
    dimensions: Mapping[str, str]
    dimensions_json: str
    dimensions_digest: str
    value: Decimal | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class _RetailAuthority:
    """Validated complete-scope EIA retail evidence.

    This is constructed before the coordinator is invoked.  Both published
    snapshot metadata and omission tombstones consume the same validated
    authority rather than reinterpreting a raw fixture independently.
    """

    scope: Mapping[str, str]
    scope_digest: str
    pages: tuple[tuple[int, int], ...]
    period_start: str
    period_end: str


@dataclass(frozen=True, slots=True)
class _ParsedFixture:
    fixture: Fixture
    candidate: Stage3MacroFixtureCandidate
    spec: _FamilySpec
    normalization_version: str
    series: tuple[_Series, ...]
    releases: Mapping[str, _Release]
    observations: tuple[_Observation, ...]
    projection: Mapping[str, Any]
    retail_authority: _RetailAuthority | None


def _parse_series(value: object, family: str) -> _Series:
    raw = _mapping(value, "series", nonempty=True)
    _exact_keys(
        raw,
        {
            "series_id",
            "provider_series_code",
            "title",
            "frequency",
            "unit",
            "value_representation",
            "scale",
            "dimensions",
            "availability_basis",
            "supported_modes",
        },
        "series",
    )
    modes = _array(raw["supported_modes"], "series/supported_modes", nonempty=True)
    if (
        not all(isinstance(mode, str) and mode in _SUPPORTED_MODES for mode in modes)
        or len(modes) != len(set(modes))
    ):
        raise _fail("series/supported_modes is invalid")
    availability_basis = _string(raw["availability_basis"], "series/availability_basis")
    if availability_basis not in {"source_release", "source_vintage", "local_capture"}:
        raise _fail("series/availability_basis is invalid")
    if family in _LOCAL_CAPTURE_FAMILIES and availability_basis != "local_capture":
        raise _fail("local-capture family has an invalid availability basis")
    if family == "gdp" and tuple(modes) != ("latest", "as_of", "first_release"):
        raise _fail("GDP series must explicitly support all three vintage modes")
    if family in {"treasury", "soma"} and "first_release" in modes:
        raise _fail("current-state family cannot claim first-release support")
    dimensions, dimensions_json, dimensions_digest = _dimensions(raw["dimensions"], "series/dimensions")
    canonical_modes = dumps_strict(sorted(modes))
    return _Series(
        series_id=_string(raw["series_id"], "series/series_id"),
        provider_series_code=_string(raw["provider_series_code"], "series/provider_series_code"),
        title=_string(raw["title"], "series/title"),
        frequency=_string(raw["frequency"], "series/frequency"),
        unit=_string(raw["unit"], "series/unit"),
        value_representation=_string(raw["value_representation"], "series/value_representation"),
        scale=_string(raw["scale"], "series/scale"),
        dimensions=dimensions,
        dimensions_json=dimensions_json,
        dimensions_digest=dimensions_digest,
        availability_basis=availability_basis,
        supported_modes=tuple(sorted(modes)),
        supported_modes_json=canonical_modes,
    )


def _parse_release(value: object) -> _Release:
    raw = _mapping(value, "release", nonempty=True)
    _exact_keys(
        raw,
        {
            "source_vintage_identity",
            "vintage_at",
            "vintage_precision",
            "published_at",
            "published_precision",
            "available_at",
            "available_precision",
            "source_release_order",
            "release_stage",
            "is_first_release",
            "first_release_evidence",
        },
        "release",
    )
    vintage_at = _temporal(raw["vintage_at"], raw["vintage_precision"], "release/vintage_at")
    assert vintage_at is not None
    published_at = _temporal(
        raw["published_at"],
        raw["published_precision"],
        "release/published_at",
        nullable_unknown=True,
    )
    available_at = _temporal(raw["available_at"], raw["available_precision"], "release/available_at")
    assert available_at is not None
    order = raw["source_release_order"]
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        raise _fail("release/source_release_order must be a positive integer")
    first = raw["is_first_release"]
    if not isinstance(first, bool):
        raise _fail("release/is_first_release must be boolean")
    evidence = raw["first_release_evidence"]
    if first:
        evidence = _string(evidence, "release/first_release_evidence")
    elif evidence is not None:
        raise _fail("release/first_release_evidence is only valid for first releases")
    return _Release(
        source_vintage_identity=_string(
            raw["source_vintage_identity"], "release/source_vintage_identity"
        ),
        vintage_at=vintage_at,
        published_at=published_at,
        available_at=available_at,
        source_release_order=order,
        source_release_order_text=f"{order:020d}",
        release_stage=_string(raw["release_stage"], "release/release_stage"),
        is_first_release=first,
        first_release_evidence=evidence,
    )


def _parse_observation(value: object) -> _Observation:
    raw = _mapping(value, "observation", nonempty=True)
    _exact_keys(
        raw,
        {
            "series_id",
            "release_identity",
            "period_start",
            "period_end",
            "dimensions",
            "value",
            "missing_reason",
            "source_row",
        },
        "observation",
    )
    period_start = _string(raw["period_start"], "observation/period_start")
    period_end = _string(raw["period_end"], "observation/period_end")
    if parse_date(period_end, pointer="/observation/period_end") < parse_date(
        period_start, pointer="/observation/period_start"
    ):
        raise _fail("observation period is inverted")
    value_raw = raw["value"]
    missing_raw = raw["missing_reason"]
    if (value_raw is None) == (missing_raw is None):
        raise _fail("observation requires exactly one of value or missing_reason")
    decimal_value = _decimal(value_raw, "observation/value") if value_raw is not None else None
    missing_reason = _string(missing_raw, "observation/missing_reason") if missing_raw is not None else None
    source_row = raw["source_row"]
    if isinstance(source_row, bool) or not isinstance(source_row, int) or source_row < 1:
        raise _fail("observation/source_row must be a positive integer")
    dimensions, dimensions_json, dimensions_digest = _dimensions(raw["dimensions"], "observation/dimensions")
    return _Observation(
        series_id=_string(raw["series_id"], "observation/series_id"),
        release_identity=_string(raw["release_identity"], "observation/release_identity"),
        period_start=period_start,
        period_end=period_end,
        dimensions=dimensions,
        dimensions_json=dimensions_json,
        dimensions_digest=dimensions_digest,
        value=decimal_value,
        missing_reason=missing_reason,
        source_row=source_row,
    )


def _verify_fixture(fixture: Fixture, candidate: Stage3MacroFixtureCandidate) -> _FamilySpec:
    if (
        fixture.byte_count != len(fixture.bytes)
        or hashlib.sha256(fixture.bytes).hexdigest() != fixture.sha256
    ):
        raise _fail("Fixture bytes do not match the reviewed digest")
    spec = _FAMILY_SPECS[candidate.family]
    if fixture.store != StoreRole.MACRO.value:
        raise _fail("Stage 3 macro fixture must target the macro store")
    if fixture.ingestion_family_id != spec.collector_id:
        raise _fail("Fixture collector does not match its macro family")
    if fixture.evidence_dataset_id != spec.evidence_dataset_id:
        raise _fail("Fixture evidence dataset does not match its macro family")
    if fixture.canonical_dataset_id != spec.canonical_dataset_id:
        raise _fail("Fixture canonical dataset does not match its macro family")
    if fixture.identity_dataset_id != spec.identity_dataset_id:
        raise _fail("Fixture identity dataset does not match the Stage 3 catalog")
    if fixture.provider != candidate.provider:
        raise _fail("Fixture provider conflicts with fixture payload")
    if fixture.captured_at != candidate.captured_at.raw:
        raise _fail("Fixture captured_at conflicts with fixture payload")
    if dumps_strict(dict(fixture.request_scope)) != dumps_strict(dict(candidate.request_scope)):
        raise _fail("Fixture request_scope conflicts with fixture payload")
    if fixture.expected_semantic_identity != candidate.semantic_identity:
        raise _fail("Fixture expected semantic identity is stale")
    version = fixture.metadata.get("normalization_version")
    if version != _NORMALIZATION_VERSION:
        raise _fail("Fixture normalization version is unsupported")
    return spec


def _parse_fixture(fixture: Fixture) -> _ParsedFixture:
    candidate = parse_stage3_macro_fixture(fixture.id, fixture.bytes)
    spec = _verify_fixture(fixture, candidate)
    payload = _mapping(candidate.payload, "payload", nonempty=True)
    series = tuple(_parse_series(item, candidate.family) for item in _array(payload["series"], "payload/series"))
    if len({item.series_id for item in series}) != len(series):
        raise _fail("Fixture contains duplicate macro series")
    if len({(candidate.provider, item.provider_series_code) for item in series}) != len(series):
        raise _fail("Fixture contains duplicate provider series codes")
    release_rows = tuple(_parse_release(item) for item in _array(payload["releases"], "payload/releases"))
    releases = {item.source_vintage_identity: item for item in release_rows}
    if len(releases) != len(release_rows):
        raise _fail("Fixture contains duplicate source vintage identities")
    observations = tuple(_parse_observation(item) for item in _array(payload["observations"], "payload/observations"))
    if len({item.source_row for item in observations}) != len(observations):
        raise _fail("Fixture contains duplicate source rows")
    known_series = {item.series_id for item in series}
    for observation in observations:
        if observation.series_id not in known_series:
            raise _fail("Observation references an undeclared macro series")
        if observation.release_identity not in releases:
            raise _fail("Observation references an undeclared macro release")
    if candidate.family != "recession" and not series:
        raise _fail("Non-chronology fixture requires at least one generic macro series")
    if candidate.family != "recession" and not releases:
        raise _fail("Non-chronology fixture requires at least one source release")
    projection_keys = set(payload) - {"series", "releases", "observations"}
    projection = {key: payload[key] for key in projection_keys}
    retail_authority = (
        _validate_authoritative_retail_snapshot(candidate, projection, observations)
        if candidate.family == "eia_retail"
        else None
    )
    return _ParsedFixture(
        fixture=fixture,
        candidate=candidate,
        spec=spec,
        normalization_version=_NORMALIZATION_VERSION,
        series=series,
        releases=releases,
        observations=observations,
        projection=projection,
        retail_authority=retail_authority,
    )


def _release_id(series_id: str, release: _Release) -> str:
    return stable_id("stage3_macro_release", series_id, release.source_vintage_identity)


def _artifact_id(parsed: _ParsedFixture) -> str:
    return stable_id(
        "stage3_macro_artifact",
        _GENERIC_EVIDENCE_DATASET,
        parsed.fixture.sha256,
        _sha256(dumps_strict(dict(parsed.candidate.request_scope))),
    )


def _snapshot_id(parsed: _ParsedFixture) -> str:
    return stable_id(
        "stage3_macro_snapshot", parsed.spec.canonical_dataset_id, parsed.candidate.semantic_identity
    )


def _assert_series(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    series: _Series,
    run_id: str,
) -> None:
    expected = (
        parsed.candidate.provider,
        series.provider_series_code,
        series.title,
        series.frequency,
        series.unit,
        series.value_representation,
        series.scale,
        series.dimensions_json,
        series.supported_modes_json,
        series.availability_basis,
    )
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json, supported_modes_json,
               availability_basis
        FROM macro_series WHERE series_id=?
        """,
        (series.series_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO macro_series (
                series_id, provider, provider_series_code, title, frequency, unit,
                value_representation, scale, dimensions_json, supported_modes_json,
                availability_basis, created_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (series.series_id, *expected, run_id),
        )
    elif tuple(existing) != expected:
        raise _fail("Macro series contract conflicts with immutable catalog metadata")


def _ensure_release(
    connection: sqlite3.Connection,
    series_id: str,
    release: _Release,
    run_id: str,
) -> str:
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision, source_release_order,
               is_first_release, first_release_evidence, release_stage
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (series_id, release.source_vintage_identity),
    ).fetchone()
    expected = (
        release.vintage_at.raw,
        release.vintage_at.precision.value,
        release.source_release_order_text,
        int(release.is_first_release),
        release.first_release_evidence,
        release.release_stage,
    )
    if existing is not None:
        actual = (
            existing["vintage_at"],
            existing["vintage_precision"],
            existing["source_release_order"],
            existing["is_first_release"],
            existing["first_release_evidence"],
            existing["release_stage"],
        )
        if actual != expected:
            raise _fail("Macro release metadata conflicts with immutable source vintage identity")
        return str(existing["release_id"])
    release_id = _release_id(series_id, release)
    connection.execute(
        """
        INSERT INTO macro_releases (
            release_id, series_id, source_vintage_identity, vintage_at,
            vintage_precision, source_release_order, available_at,
            available_precision, is_first_release, first_release_evidence,
            first_seen_run_id, release_stage, source_published_at,
            source_published_precision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            release_id,
            series_id,
            release.source_vintage_identity,
            release.vintage_at.raw,
            release.vintage_at.precision.value,
            release.source_release_order_text,
            release.available_at.raw,
            release.available_at.precision.value,
            int(release.is_first_release),
            release.first_release_evidence,
            run_id,
            release.release_stage,
            release.published_at.raw if release.published_at is not None else None,
            release.published_at.precision.value if release.published_at is not None else "unknown",
        ),
    )
    return release_id


def _insert_dimensions(
    connection: sqlite3.Connection,
    series_id: str,
    dimensions: Mapping[str, str],
    release: _Release,
    snapshot_id: str,
    run_id: str,
) -> None:
    for ordinal, (key, value) in enumerate(sorted(dimensions.items())):
        existing = connection.execute(
            """
            SELECT dimension_id FROM macro_series_dimensions
            WHERE series_id=? AND dimension_key=? AND dimension_value=?
            """,
            (series_id, key, value),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO macro_series_dimensions (
                dimension_id, series_id, dimension_key, dimension_value, ordinal,
                metadata_json, available_at, available_precision, source_snapshot_id,
                run_id
            ) VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)
            """,
            (
                stable_id("stage3_macro_dimension", series_id, key, value),
                series_id,
                key,
                value,
                ordinal,
                release.available_at.raw,
                release.available_at.precision.value,
                snapshot_id,
                run_id,
            ),
        )


def _stored_value_matches(stored: object, value: Decimal | None) -> bool:
    if stored is None:
        return value is None
    if value is None:
        return False
    try:
        return _normalized_decimal(Decimal(str(stored))) == _normalized_decimal(value)
    except (InvalidOperation, ValueError):
        return False


def _current_should_move(current: sqlite3.Row, release: _Release, correction_sequence: int) -> bool:
    try:
        existing_order = int(str(current["source_release_order"]))
    except ValueError as exc:
        raise _fail("Stored macro release order is invalid") from exc
    return (release.source_release_order, correction_sequence) >= (
        existing_order,
        int(current["correction_sequence"]),
    )


def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    observation: _Observation,
    release: _Release,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
    *,
    state: str = "active",
    missing_reason: str | None = None,
) -> tuple[str, bool]:
    series = next(item for item in parsed.series if item.series_id == observation.series_id)
    effective_missing_reason = observation.missing_reason if missing_reason is None else missing_reason
    effective_value = observation.value if state == "active" else None
    existing = connection.execute(
        """
        SELECT version_id, correction_sequence, value_text, missing_reason, unit,
               value_representation, scale, available_at, available_precision,
               state, is_preliminary
        FROM macro_observation_versions
        WHERE series_id=? AND period_start=? AND period_end=?
          AND dimensions_digest=? AND source_vintage_identity=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_digest,
            release.source_vintage_identity,
        ),
    ).fetchone()
    preliminary = int(parsed.candidate.family == "gdp" and release.release_stage in {"advance", "second", "third"})
    if existing is not None and (
        _stored_value_matches(existing["value_text"], effective_value),
        existing["missing_reason"],
        existing["unit"],
        existing["value_representation"],
        existing["scale"],
        existing["available_at"],
        existing["available_precision"],
        existing["state"],
        int(existing["is_preliminary"]),
    ) == (
        True,
        effective_missing_reason,
        series.unit,
        series.value_representation,
        series.scale,
        release.available_at.raw,
        release.available_at.precision.value,
        state,
        preliminary,
    ):
        return str(existing["version_id"]), False
    correction_sequence = 1 if existing is None else int(existing["correction_sequence"]) + 1
    supersedes = None if existing is None else str(existing["version_id"])
    version_material = {
        "series_id": observation.series_id,
        "period_start": observation.period_start,
        "period_end": observation.period_end,
        "dimensions_digest": observation.dimensions_digest,
        "source_vintage_identity": release.source_vintage_identity,
        "correction_sequence": correction_sequence,
        "value": _normalized_decimal(effective_value),
        "missing_reason": effective_missing_reason,
        "state": state,
        "available_at": release.available_at.raw,
        "available_precision": release.available_at.precision.value,
    }
    version_id = stable_id(
        "stage3_macro_version",
        observation.series_id,
        observation.period_start,
        observation.period_end,
        observation.dimensions_digest,
        release.source_vintage_identity,
        str(correction_sequence),
        _sha256(dumps_strict(version_material)),
    )
    connection.execute(
        """
        INSERT INTO macro_observation_versions (
            version_id, series_id, period_start, period_end, dimensions_json,
            dimensions_digest, release_id, source_vintage_identity,
            correction_sequence, value_text, missing_reason, unit,
            value_representation, scale, available_at, available_precision,
            captured_at, captured_precision, supersedes_version_id, artifact_id,
            snapshot_id, run_id, source_row, state, is_preliminary,
            quality_flags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')
        """,
        (
            version_id,
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_json,
            observation.dimensions_digest,
            release_id,
            release.source_vintage_identity,
            correction_sequence,
            format(effective_value, "f") if effective_value is not None else None,
            effective_missing_reason,
            series.unit,
            series.value_representation,
            series.scale,
            release.available_at.raw,
            release.available_at.precision.value,
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            supersedes,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
            state,
            preliminary,
        ),
    )
    current = connection.execute(
        """
        SELECT observation.current_version_id, version.correction_sequence,
               release.source_release_order
        FROM macro_observations AS observation
        JOIN macro_observation_versions AS version
          ON version.version_id=observation.current_version_id
        JOIN macro_releases AS release ON release.release_id=version.release_id
        WHERE observation.series_id=? AND observation.period_start=?
          AND observation.period_end=? AND observation.dimensions_digest=?
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_digest,
        ),
    ).fetchone()
    if current is None:
        connection.execute(
            """
            INSERT INTO macro_observations (
                series_id, period_start, period_end, dimensions_digest,
                current_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                observation.series_id,
                observation.period_start,
                observation.period_end,
                observation.dimensions_digest,
                version_id,
            ),
        )
    elif _current_should_move(current, release, correction_sequence):
        connection.execute(
            "UPDATE macro_observations SET current_version_id=? WHERE series_id=? AND period_start=? AND period_end=? AND dimensions_digest=?",
            (
                version_id,
                observation.series_id,
                observation.period_start,
                observation.period_end,
                observation.dimensions_digest,
            ),
        )
    return version_id, True


class MacroStage3FixtureImporter:
    """Publish one complete reviewed Stage 3 macro capture atomically."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map, code_version="stage3.0.0")

    def import_fixture(self, fixture_id: str) -> IngestionReceipt:
        fixture = self._fixture_manifest.get(fixture_id)
        parsed = _parse_fixture(fixture)
        run_id = stable_id(
            "stage3_macro_run", parsed.spec.canonical_dataset_id, parsed.candidate.semantic_identity
        )
        scope = {
            "fixture_id": parsed.fixture.id,
            "family": parsed.candidate.family,
            "collector_id": parsed.spec.collector_id,
            "request_scope": dict(parsed.candidate.request_scope),
            "completeness": "complete",
        }

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(connection, parsed, active_run_id)

        return self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=parsed.spec.canonical_dataset_id,
            output_dataset_ids=parsed.spec.output_dataset_ids,
            semantic_identity=parsed.candidate.semantic_identity,
            run_id=run_id,
            command=parsed.spec.collector_id,
            scope=scope,
            started_at=parsed.candidate.captured_at.raw,
            completed_at=parsed.candidate.captured_at.raw,
            fetched_count=len(parsed.observations),
            writer=writer,
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        parsed: _ParsedFixture,
        run_id: str,
    ) -> WriteResult:
        artifact_id = _artifact_id(parsed)
        snapshot_id = _snapshot_id(parsed)
        release_ids: dict[tuple[str, str], str] = {}
        for series in parsed.series:
            _assert_series(connection, parsed, series, run_id)
            for release in parsed.releases.values():
                release_ids[(series.series_id, release.source_vintage_identity)] = _ensure_release(
                    connection, series.series_id, release, run_id
                )
        connection.execute(
            """
            INSERT INTO macro_source_artifacts (
                artifact_id, dataset_id, sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision,
                source_resource, normalization_version, run_id
            ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                _GENERIC_EVIDENCE_DATASET,
                parsed.fixture.sha256,
                parsed.fixture.byte_count,
                dumps_strict(dict(parsed.candidate.request_scope)),
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                parsed.fixture.resource_name,
                parsed.normalization_version,
                run_id,
            ),
        )
        tombstone_authoritative = int(parsed.retail_authority is not None)
        connection.execute(
            """
            INSERT INTO macro_source_snapshots (
                snapshot_id, series_id, release_id, artifact_id,
                semantic_identity, completeness, row_count, validation_state,
                warnings_json, run_id, quality_flags_json
            ) VALUES (?, NULL, NULL, ?, ?, 'complete', ?, 'validated', '[]', ?, '[]')
            """,
            (
                snapshot_id,
                artifact_id,
                parsed.candidate.semantic_identity,
                len(parsed.observations),
                run_id,
            ),
        )
        scope_digest = _sha256(dumps_strict(dict(parsed.candidate.request_scope)))
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', ?)
            """,
            (
                stable_id("stage3_macro_scope", snapshot_id, scope_digest),
                snapshot_id,
                dumps_strict(dict(parsed.candidate.request_scope)),
                scope_digest,
                tombstone_authoritative,
            ),
        )
        for series in parsed.series:
            related = next(iter(parsed.releases.values()))
            _insert_dimensions(connection, series.series_id, series.dimensions, related, snapshot_id, run_id)
        appended = 0
        observed_keys: set[tuple[str, str, str, str]] = set()
        for observation in parsed.observations:
            release = parsed.releases[observation.release_identity]
            _insert_dimensions(
                connection,
                observation.series_id,
                observation.dimensions,
                release,
                snapshot_id,
                run_id,
            )
            version_id, wrote = _append_or_reuse_observation(
                connection,
                parsed,
                observation,
                release,
                release_ids[(observation.series_id, release.source_vintage_identity)],
                artifact_id,
                snapshot_id,
                run_id,
            )
            appended += int(wrote)
            observed_keys.add(
                (
                    observation.series_id,
                    observation.period_start,
                    observation.period_end,
                    observation.dimensions_digest,
                )
            )
            connection.execute(
                """
                INSERT INTO macro_snapshot_observation_membership (
                    snapshot_id, version_id, source_row
                ) VALUES (?, ?, ?)
                """,
                (snapshot_id, version_id, observation.source_row),
            )
        appended += _write_retail_tombstones(
            connection,
            parsed,
            release_ids,
            artifact_id,
            snapshot_id,
            run_id,
            observed_keys,
        )
        family_artifacts, family_written = _write_family_projection(
            connection,
            parsed,
            artifact_id,
            snapshot_id,
            release_ids,
            run_id,
        )
        artifacts = (
            ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=_GENERIC_EVIDENCE_DATASET,
                content_sha256=parsed.fixture.sha256,
                media_type="application/json",
                byte_count=parsed.fixture.byte_count,
                source_reference=parsed.fixture.resource_name,
                request_scope=dict(parsed.candidate.request_scope),
                captured_at=parsed.candidate.captured_at.raw,
                captured_precision=parsed.candidate.captured_at.precision.value,
                normalization_version=parsed.normalization_version,
            ),
            *family_artifacts,
        )
        return WriteResult(
            written_count=appended + family_written,
            artifacts=artifacts,
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=parsed.spec.canonical_dataset_id,
                semantic_identity=parsed.candidate.semantic_identity,
                scope=dict(parsed.candidate.request_scope),
                completeness="complete",
                row_count=len(parsed.observations),
                captured_at=parsed.candidate.captured_at.raw,
                captured_precision=parsed.candidate.captured_at.precision.value,
                validation_state="validated",
                artifact_ids=tuple(item.artifact_id for item in artifacts),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage3_macro_quality",
                        run_id,
                        parsed.spec.canonical_dataset_id,
                    ),
                    dataset_id=parsed.spec.canonical_dataset_id,
                    rule_id="fixture.batch_contract",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "family": parsed.candidate.family,
                        "fetched_count": len(parsed.observations),
                        "written_count": appended + family_written,
                        "completeness": "complete",
                    },
                ),
            ),
        )


def _retail_period_bounds(period: object) -> tuple[str, str]:
    if not isinstance(period, str) or len(period) != 7 or period[4] != "-":
        raise _fail("EIA retail scope period must be YYYY-MM")
    try:
        year, month = (int(part) for part in period.split("-"))
        return (
            f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        )
    except (ValueError, IndexError) as exc:
        raise _fail("EIA retail scope period must be a valid month") from exc


def _validate_authoritative_retail_snapshot(
    candidate: Stage3MacroFixtureCandidate,
    projection: Mapping[str, Any],
    observations: Sequence[_Observation],
) -> _RetailAuthority:
    """Validate complete EIA retail evidence before a coordinator can write.

    The fixture contract already carries the requested area/month, the
    authoritative provider projection scope, page-level row counts, and the
    normalized observation area/month.  Require them to reconcile exactly;
    otherwise an apparent omission could be a truncated or mismatched capture.
    """

    raw = _mapping(projection.get("retail_snapshot"), "retail_snapshot", nonempty=True)
    _exact_keys(raw, {"scope", "complete", "tombstone_authoritative", "pages"}, "retail_snapshot")
    if raw["complete"] is not True or raw["tombstone_authoritative"] is not True:
        raise _fail("Complete EIA retail fixture must explicitly authorize tombstones")

    request_scope = _mapping(candidate.request_scope, "EIA retail request scope", nonempty=True)
    if request_scope.get("complete") is not True or request_scope.get("tombstone_authoritative") is not True:
        raise _fail("EIA retail request scope must explicitly authorize tombstones")
    request_area = _string(request_scope.get("scope"), "EIA retail request scope/scope")
    request_period = _string(request_scope.get("period"), "EIA retail request scope/period")
    period_start, period_end = _retail_period_bounds(request_period)

    projection_scope = _string(raw["scope"], "retail_snapshot/scope")
    if projection_scope != f"{request_area}:{request_period}":
        raise _fail("EIA retail projection scope must match the requested scope and period")

    page_pairs: list[tuple[int, int]] = []
    page_numbers: set[int] = set()
    for page in _array(raw["pages"], "retail_snapshot/pages", nonempty=True):
        row = _mapping(page, "retail_snapshot/page", nonempty=True)
        _exact_keys(row, {"page", "row_count"}, "retail_snapshot/page")
        page_number, row_count = row["page"], row["row_count"]
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 0
        ):
            raise _fail("EIA retail page metadata is invalid")
        if page_number in page_numbers:
            raise _fail("EIA retail authoritative pages must be unique")
        page_numbers.add(page_number)
        page_pairs.append((page_number, row_count))
    if sorted(page_numbers) != list(range(1, len(page_pairs) + 1)):
        raise _fail("EIA retail authoritative pages must be contiguous from page 1")

    in_scope = tuple(
        observation
        for observation in observations
        if (
            observation.period_start == period_start
            and observation.period_end == period_end
            and observation.dimensions.get("area") == request_area
        )
    )
    if len(in_scope) != len(observations):
        raise _fail("EIA retail authoritative fixture contains an observation outside its requested scope")
    if sum(row_count for _, row_count in page_pairs) != len(in_scope):
        raise _fail("EIA retail authoritative page row counts do not match parsed observations")

    scope = {"scope": projection_scope}
    return _RetailAuthority(
        scope=scope,
        scope_digest=_sha256(dumps_strict(scope)),
        pages=tuple(page_pairs),
        period_start=period_start,
        period_end=period_end,
    )


def _write_retail_tombstones(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    release_ids: Mapping[tuple[str, str], str],
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
    observed_keys: set[tuple[str, str, str, str]],
) -> int:
    authority = parsed.retail_authority
    if authority is None:
        return 0
    if not parsed.releases:
        raise _fail("EIA retail tombstone capture requires a release")
    release = max(parsed.releases.values(), key=lambda item: item.source_release_order)
    rows = list(
        connection.execute(
            """
            SELECT current.series_id, current.period_start, current.period_end,
                   current.dimensions_digest, version.dimensions_json,
                   version.source_row
            FROM macro_observations AS current
            JOIN macro_observation_versions AS version
              ON version.version_id=current.current_version_id
            WHERE current.period_start=? AND current.period_end=?
              AND version.state='active'
              AND current.series_id IN ({})
            ORDER BY current.series_id, current.dimensions_digest
            """.format(",".join("?" for _ in parsed.series)),
            (
                authority.period_start,
                authority.period_end,
                *(series.series_id for series in parsed.series),
            ),
        )
    )
    used_rows = {item.source_row for item in parsed.observations}
    next_row = max(used_rows, default=0) + 1
    written = 0
    for row in rows:
        key = (
            str(row["series_id"]),
            str(row["period_start"]),
            str(row["period_end"]),
            str(row["dimensions_digest"]),
        )
        if key in observed_keys:
            continue
        dimensions = _mapping(
            loads_strict(str(row["dimensions_json"]), max_bytes=1024),
            "stored retail dimensions",
        )
        cast_dimensions = {str(key): _string(value, "stored retail dimension") for key, value in dimensions.items()}
        synthetic = _Observation(
            series_id=key[0],
            release_identity=release.source_vintage_identity,
            period_start=key[1],
            period_end=key[2],
            dimensions=cast_dimensions,
            dimensions_json=dumps_strict(cast_dimensions),
            dimensions_digest=key[3],
            value=None,
            missing_reason="source_omitted_from_complete_authoritative_snapshot",
            source_row=next_row,
        )
        next_row += 1
        version_id, appended = _append_or_reuse_observation(
            connection,
            parsed,
            synthetic,
            release,
            release_ids[(synthetic.series_id, release.source_vintage_identity)],
            artifact_id,
            snapshot_id,
            run_id,
            state="tombstone",
            missing_reason=synthetic.missing_reason,
        )
        written += int(appended)
        connection.execute(
            """
            INSERT INTO macro_snapshot_observation_membership (
                snapshot_id, version_id, source_row
            ) VALUES (?, ?, ?)
            """,
            (snapshot_id, version_id, synthetic.source_row),
        )
    return written


def _write_family_projection(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    artifact_id: str,
    snapshot_id: str,
    release_ids: Mapping[tuple[str, str], str],
    run_id: str,
) -> tuple[tuple[ArtifactWrite, ...], int]:
    family = parsed.candidate.family
    if family == "gdp":
        return (), _write_gdp_vintages(connection, parsed, artifact_id, snapshot_id, run_id)
    if family == "treasury":
        return (), _write_treasury_curves(connection, parsed, artifact_id, snapshot_id, run_id)
    if family == "economic_calendar":
        return (), _write_calendar_events(connection, parsed, artifact_id, snapshot_id, run_id)
    if family == "soma":
        return _write_soma_summary(connection, parsed, run_id)
    if family == "eia_retail":
        return _write_eia_retail(connection, parsed, run_id)
    if family == "eia_weekly":
        return _write_eia_weekly(connection, parsed, run_id)
    if family == "recession":
        return (), _write_recession_periods(connection, parsed, artifact_id, snapshot_id, run_id)
    return (), 0


def _write_gdp_vintages(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    rows = _array(parsed.projection["gdp_vintages"], "gdp_vintages", nonempty=True)
    written = 0
    for raw in rows:
        item = _mapping(raw, "gdp_vintage", nonempty=True)
        _exact_keys(item, {"release_identity", "release_stage", "source"}, "gdp_vintage")
        identity = _string(item["release_identity"], "gdp_vintage/release_identity")
        release = parsed.releases.get(identity)
        if release is None or release.release_stage != _string(item["release_stage"], "gdp_vintage/release_stage"):
            raise _fail("GDP vintage must bind a declared release stage")
        source = _string(item["source"], "gdp_vintage/source")
        existing = connection.execute(
            "SELECT vintage_id FROM gdp_vintages WHERE source=? AND source_vintage_identity=?",
            (source, identity),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO gdp_vintages (
                vintage_id, source, source_vintage_identity, release_stage,
                vintage_at, vintage_precision, source_published_at,
                source_published_precision, available_at, available_precision,
                artifact_id, source_snapshot_id, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("gdp_vintage", source, identity),
                source,
                identity,
                release.release_stage,
                release.vintage_at.raw,
                release.vintage_at.precision.value,
                release.published_at.raw if release.published_at is not None else None,
                release.published_at.precision.value if release.published_at is not None else "unknown",
                release.available_at.raw,
                release.available_at.precision.value,
                artifact_id,
                snapshot_id,
                run_id,
            ),
        )
        written += 1
    return written


def _write_treasury_curves(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    rows = _array(parsed.projection["curves"], "curves", nonempty=True)
    observations = {item.series_id: item for item in parsed.observations}
    written = 0
    for raw in rows:
        item = _mapping(raw, "curve", nonempty=True)
        _exact_keys(item, {"curve_date", "curve_variant", "tenor", "series_id"}, "curve")
        series_id = _string(item["series_id"], "curve/series_id")
        observation = observations.get(series_id)
        if observation is None:
            raise _fail("Treasury curve must bind one fixture observation")
        curve_date = _string(item["curve_date"], "curve/curve_date")
        if curve_date != observation.period_start or observation.period_start != observation.period_end:
            raise _fail("Treasury curve date must match its daily observation")
        curve_variant = _string(item["curve_variant"], "curve/curve_variant")
        tenor = _string(item["tenor"], "curve/tenor")
        curve_id = stable_id("treasury_curve", parsed.candidate.provider, curve_date, curve_variant)
        existing_curve = connection.execute(
            "SELECT curve_id FROM treasury_yield_curves WHERE provider=? AND curve_date=? AND curve_variant=?",
            (parsed.candidate.provider, curve_date, curve_variant),
        ).fetchone()
        if existing_curve is None:
            connection.execute(
                """
                INSERT INTO treasury_yield_curves (
                    curve_id, provider, curve_date, curve_variant,
                    source_snapshot_id, run_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (curve_id, parsed.candidate.provider, curve_date, curve_variant, snapshot_id, run_id),
            )
            written += 1
        else:
            curve_id = str(existing_curve["curve_id"])
        current = connection.execute(
            """
            SELECT curve_version_id, correction_sequence, yield_value,
                   missing_reason, state
            FROM treasury_yield_curve_versions
            WHERE provider=? AND curve_date=? AND curve_variant=? AND tenor=?
            ORDER BY correction_sequence DESC LIMIT 1
            """,
            (parsed.candidate.provider, curve_date, curve_variant, tenor),
        ).fetchone()
        if current is not None and (
            _stored_value_matches(current["yield_value"], observation.value)
            and current["missing_reason"] == observation.missing_reason
            and current["state"] == "active"
        ):
            continue
        sequence = 1 if current is None else int(current["correction_sequence"]) + 1
        previous = None if current is None else str(current["curve_version_id"])
        version_id = stable_id(
            "treasury_curve_version",
            parsed.candidate.provider,
            curve_date,
            curve_variant,
            tenor,
            str(sequence),
            _normalized_decimal(observation.value) or observation.missing_reason or "",
        )
        connection.execute(
            """
            INSERT INTO treasury_yield_curve_versions (
                curve_version_id, curve_id, provider, curve_date, curve_variant,
                tenor, series_id, yield_value, missing_reason, available_at,
                available_precision, captured_at, captured_precision,
                correction_sequence, supersedes_curve_version_id, artifact_id,
                source_snapshot_id, run_id, source_row, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                version_id,
                curve_id,
                parsed.candidate.provider,
                curve_date,
                curve_variant,
                tenor,
                series_id,
                format(observation.value, "f") if observation.value is not None else None,
                observation.missing_reason,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                sequence,
                previous,
                artifact_id,
                snapshot_id,
                run_id,
                observation.source_row,
            ),
        )
        written += 1
    return written


def _write_calendar_events(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    rows = _array(parsed.projection["calendar_events"], "calendar_events", nonempty=True)
    only_series = parsed.series[0].series_id if len(parsed.series) == 1 else None
    written = 0
    for source_row, raw in enumerate(rows, start=1):
        item = _mapping(raw, "calendar_event", nonempty=True)
        required = {"provider_event_id", "event_at", "country", "name", "actual", "consensus"}
        if set(item) != required and set(item) != required | {"previous", "unit"}:
            raise _fail("calendar_event has an unsupported shape")
        provider_event_id = _string(item["provider_event_id"], "calendar_event/provider_event_id")
        event_at = TemporalValue.parse(_string(item["event_at"], "calendar_event/event_at"), pointer="/calendar_event/event_at")
        country = _string(item["country"], "calendar_event/country")
        name = _string(item["name"], "calendar_event/name")
        event_id = stable_id("calendar_event", parsed.candidate.provider, provider_event_id)
        existing = connection.execute(
            "SELECT event_id FROM economic_calendar WHERE provider=? AND provider_event_id=?",
            (parsed.candidate.provider, provider_event_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO economic_calendar (
                    event_id, provider, provider_event_id, event_at, event_precision,
                    country, name, series_id, created_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    parsed.candidate.provider,
                    provider_event_id,
                    event_at.raw,
                    event_at.precision.value,
                    country,
                    name,
                    only_series,
                    run_id,
                ),
            )
            written += 1
        else:
            event_id = str(existing["event_id"])
        actual = _decimal(item["actual"], "calendar_event/actual") if item["actual"] is not None else None
        consensus = _decimal(item["consensus"], "calendar_event/consensus") if item["consensus"] is not None else None
        previous = _decimal(item["previous"], "calendar_event/previous") if item.get("previous") is not None else None
        unit = _string(item["unit"], "calendar_event/unit") if item.get("unit") is not None else None
        current = connection.execute(
            """
            SELECT event_version_id, correction_sequence, actual_value,
                   consensus_value, previous_value, unit, state
            FROM economic_calendar_event_versions
            WHERE event_id=? ORDER BY correction_sequence DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if current is not None and (
            _stored_value_matches(current["actual_value"], actual)
            and _stored_value_matches(current["consensus_value"], consensus)
            and _stored_value_matches(current["previous_value"], previous)
            and current["unit"] == unit
            and current["state"] == "active"
        ):
            continue
        sequence = 1 if current is None else int(current["correction_sequence"]) + 1
        previous_id = None if current is None else str(current["event_version_id"])
        version_id = stable_id("calendar_event_version", event_id, str(sequence), parsed.candidate.semantic_identity)
        connection.execute(
            """
            INSERT INTO economic_calendar_event_versions (
                event_version_id, event_id, actual_value, consensus_value,
                previous_value, unit, available_at, available_precision,
                captured_at, captured_precision, correction_sequence,
                supersedes_event_version_id, artifact_id, source_snapshot_id,
                run_id, source_row, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                version_id,
                event_id,
                format(actual, "f") if actual is not None else None,
                format(consensus, "f") if consensus is not None else None,
                format(previous, "f") if previous is not None else None,
                unit,
                event_at.raw,
                event_at.precision.value,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                sequence,
                previous_id,
                artifact_id,
                snapshot_id,
                run_id,
                source_row,
            ),
        )
        written += 1
    return written


def _write_soma_summary(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    run_id: str,
) -> tuple[tuple[ArtifactWrite, ...], int]:
    raw = _mapping(parsed.projection["latest_summary_release"], "latest_summary_release", nonempty=True)
    required = {"release_id", "as_of_date", "components"}
    if not required.issubset(raw):
        raise _fail("SOMA summary release is incomplete")
    as_of_date = _string(raw["as_of_date"], "latest_summary_release/as_of_date")
    parse_date(as_of_date, pointer="/latest_summary_release/as_of_date")
    components = _array(raw["components"], "latest_summary_release/components", nonempty=True)
    soma_artifact_id = stable_id("soma_artifact", parsed.fixture.sha256, parsed.candidate.semantic_identity)
    soma_snapshot_id = stable_id("soma_snapshot", parsed.candidate.semantic_identity)
    connection.execute(
        """
        INSERT INTO soma_source_artifacts (
            artifact_id, source_name, content_sha256, media_type, byte_count,
            request_scope_json, captured_at, captured_precision, run_id
        ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, ?)
        """,
        (
            soma_artifact_id,
            parsed.candidate.provider,
            parsed.fixture.sha256,
            parsed.fixture.byte_count,
            dumps_strict(dict(parsed.candidate.request_scope)),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO soma_snapshots (
            snapshot_id, semantic_identity, as_of_date, scope_json, completeness,
            row_count, captured_at, captured_precision, source_published_at,
            source_published_precision, run_id
        ) VALUES (?, ?, ?, ?, 'complete', ?, ?, ?, NULL, 'unknown', ?)
        """,
        (
            soma_snapshot_id,
            parsed.candidate.semantic_identity,
            as_of_date,
            dumps_strict(dict(parsed.candidate.request_scope)),
            len(components),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        "INSERT INTO soma_snapshot_artifacts (snapshot_id, artifact_id, artifact_ordinal) VALUES (?, ?, 1)",
        (soma_snapshot_id, soma_artifact_id),
    )
    seen: set[tuple[str, str]] = set()
    for source_row, raw_component in enumerate(components, start=1):
        component = _mapping(raw_component, "soma_component", nonempty=True)
        _exact_keys(component, {"category", "metric", "unit", "value"}, "soma_component")
        category = _string(component["category"], "soma_component/category")
        measure = _string(component["metric"], "soma_component/metric")
        unit = _string(component["unit"], "soma_component/unit")
        if (category, measure) in seen:
            raise _fail("SOMA summary component is duplicated")
        seen.add((category, measure))
        value = _decimal(component["value"], "soma_component/value") if component["value"] is not None else None
        missing = None if value is not None else "source_missing"
        connection.execute(
            """
            INSERT INTO soma_summary_components (
                component_id, snapshot_id, as_of_date, category, measure,
                value_text, missing_reason, unit, available_at,
                available_precision, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("soma_component", soma_snapshot_id, category, measure),
                soma_snapshot_id,
                as_of_date,
                category,
                measure,
                format(value, "f") if value is not None else None,
                missing,
                unit,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                source_row,
            ),
        )
    artifact = ArtifactWrite(
        artifact_id=soma_artifact_id,
        dataset_id="fixture.macro.soma_evidence",
        content_sha256=parsed.fixture.sha256,
        media_type="application/json",
        byte_count=parsed.fixture.byte_count,
        source_reference=parsed.fixture.resource_name,
        request_scope=dict(parsed.candidate.request_scope),
        captured_at=parsed.candidate.captured_at.raw,
        captured_precision=parsed.candidate.captured_at.precision.value,
        normalization_version=parsed.normalization_version,
    )
    return (artifact,), len(components) + 2


def _retail_projection_scope(
    parsed: _ParsedFixture,
) -> tuple[Mapping[str, str], str, tuple[tuple[int, int], ...]]:
    authority = parsed.retail_authority
    if authority is None:
        raise _fail("EIA retail projection requires validated authoritative evidence")
    return authority.scope, authority.scope_digest, authority.pages


def _retail_projection_artifact_id(parsed: _ParsedFixture) -> str:
    return stable_id("eia_retail_artifact", parsed.fixture.sha256, parsed.candidate.semantic_identity)


def _append_retail_projection_version(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    observation: _Observation,
    artifact_id: str,
    source_snapshot_id: str,
    snapshot_version_id: str,
    run_id: str,
    *,
    state: str = "active",
    missing_reason: str | None = None,
) -> tuple[str, bool]:
    series = next(item for item in parsed.series if item.series_id == observation.series_id)
    effective_value = observation.value if state == "active" else None
    effective_missing = observation.missing_reason if missing_reason is None else missing_reason
    existing = connection.execute(
        """
        SELECT retail_sales_version_id, correction_sequence, value_text,
               missing_reason, state
        FROM eia_electricity_retail_sales_versions
        WHERE series_id=? AND period_start=? AND period_end=? AND dimensions_digest=?
        ORDER BY correction_sequence DESC LIMIT 1
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_digest,
        ),
    ).fetchone()
    if existing is not None and (
        _stored_value_matches(existing["value_text"], effective_value)
        and existing["missing_reason"] == effective_missing
        and existing["state"] == state
    ):
        return str(existing["retail_sales_version_id"]), False
    sequence = 1 if existing is None else int(existing["correction_sequence"]) + 1
    previous = None if existing is None else str(existing["retail_sales_version_id"])
    version_id = stable_id(
        "eia_retail_version",
        observation.series_id,
        observation.period_start,
        observation.period_end,
        observation.dimensions_digest,
        str(sequence),
        _normalized_decimal(effective_value) or effective_missing or "",
    )
    connection.execute(
        """
        INSERT INTO eia_electricity_retail_sales_versions (
            retail_sales_version_id, series_id, period_start, period_end,
            dimensions_json, dimensions_digest, value_text, missing_reason,
            unit, value_representation, scale, available_at, available_precision,
            captured_at, captured_precision, correction_sequence,
            supersedes_retail_sales_version_id, artifact_id, source_snapshot_id,
            snapshot_version_id, run_id, source_row, state, quality_flags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')
        """,
        (
            version_id,
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_json,
            observation.dimensions_digest,
            format(effective_value, "f") if effective_value is not None else None,
            effective_missing,
            series.unit,
            series.value_representation,
            series.scale,
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            sequence,
            previous,
            artifact_id,
            source_snapshot_id,
            snapshot_version_id,
            run_id,
            observation.source_row,
            state,
        ),
    )
    current = connection.execute(
        """
        SELECT current_version_id FROM eia_electricity_retail_sales
        WHERE series_id=? AND period_start=? AND period_end=? AND dimensions_digest=?
        """,
        (
            observation.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_digest,
        ),
    ).fetchone()
    if current is None:
        connection.execute(
            """
            INSERT INTO eia_electricity_retail_sales (
                series_id, period_start, period_end, dimensions_digest,
                current_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                observation.series_id,
                observation.period_start,
                observation.period_end,
                observation.dimensions_digest,
                version_id,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE eia_electricity_retail_sales SET current_version_id=?
            WHERE series_id=? AND period_start=? AND period_end=? AND dimensions_digest=?
            """,
            (
                version_id,
                observation.series_id,
                observation.period_start,
                observation.period_end,
                observation.dimensions_digest,
            ),
        )
    return version_id, True


def _write_eia_retail(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    run_id: str,
) -> tuple[tuple[ArtifactWrite, ...], int]:
    authority = parsed.retail_authority
    if authority is None:
        raise _fail("EIA retail projection requires validated authoritative evidence")
    scope, scope_digest, pages = _retail_projection_scope(parsed)
    tombstone_authoritative = int(authority is not None)
    artifact_id = _retail_projection_artifact_id(parsed)
    source_snapshot_id = stable_id("eia_retail_snapshot", parsed.candidate.semantic_identity)
    previous_snapshot = connection.execute(
        """
        SELECT snapshot_version_id, version_sequence
        FROM eia_electricity_snapshot_versions
        WHERE scope_digest=? ORDER BY version_sequence DESC LIMIT 1
        """,
        (scope_digest,),
    ).fetchone()
    sequence = 1 if previous_snapshot is None else int(previous_snapshot["version_sequence"]) + 1
    snapshot_version_id = stable_id(
        "eia_retail_snapshot_version", scope_digest, str(sequence), parsed.candidate.semantic_identity
    )
    snapshot_state = "tombstone" if not parsed.observations else "active"
    connection.execute(
        """
        INSERT INTO eia_electricity_source_artifacts (
            artifact_id, provider, content_sha256, media_type, byte_count,
            request_scope_json, captured_at, captured_precision, run_id
        ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            parsed.candidate.provider,
            parsed.fixture.sha256,
            parsed.fixture.byte_count,
            dumps_strict(dict(parsed.candidate.request_scope)),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO eia_electricity_source_snapshots (
            source_snapshot_id, provider, semantic_identity, scope_json,
            scope_digest, completeness, tombstone_authoritative, row_count,
            validation_state, warnings_json, captured_at, captured_precision,
            run_id
        ) VALUES (?, ?, ?, ?, ?, 'complete', ?, ?, 'validated', '[]', ?, ?, ?)
        """,
        (
            source_snapshot_id,
            parsed.candidate.provider,
            parsed.candidate.semantic_identity,
            dumps_strict(scope),
            scope_digest,
            tombstone_authoritative,
            len(parsed.observations),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO eia_electricity_snapshot_artifacts (
            source_snapshot_id, artifact_id, artifact_ordinal
        ) VALUES (?, ?, 1)
        """,
        (source_snapshot_id, artifact_id),
    )
    for page_number, row_count in pages:
        connection.execute(
            """
            INSERT INTO eia_electricity_ingestion_pages (
                page_id, source_snapshot_id, artifact_id, page_number, row_count,
                content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("eia_retail_page", source_snapshot_id, str(page_number)),
                source_snapshot_id,
                artifact_id,
                page_number,
                row_count,
                parsed.fixture.sha256,
            ),
        )
    connection.execute(
        """
        INSERT INTO eia_electricity_snapshot_versions (
            snapshot_version_id, source_snapshot_id, scope_digest, available_at,
            available_precision, captured_at, captured_precision, version_sequence,
            supersedes_snapshot_version_id, state, run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_version_id,
            source_snapshot_id,
            scope_digest,
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            sequence,
            None if previous_snapshot is None else str(previous_snapshot["snapshot_version_id"]),
            snapshot_state,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO eia_electricity_snapshot_scopes (
            scope_id, snapshot_version_id, scope_json, scope_digest, completeness,
            tombstone_authoritative
        ) VALUES (?, ?, ?, ?, 'complete', ?)
        """,
        (
            stable_id("eia_retail_scope", snapshot_version_id, scope_digest),
            snapshot_version_id,
            dumps_strict(scope),
            scope_digest,
            tombstone_authoritative,
        ),
    )
    written = 2 + len(pages)
    observed = {
        (item.series_id, item.period_start, item.period_end, item.dimensions_digest)
        for item in parsed.observations
    }
    for observation in parsed.observations:
        version_id, appended = _append_retail_projection_version(
            connection,
            parsed,
            observation,
            artifact_id,
            source_snapshot_id,
            snapshot_version_id,
            run_id,
        )
        written += int(appended)
        connection.execute(
            """
            INSERT INTO eia_electricity_snapshot_membership (
                snapshot_version_id, retail_sales_version_id, source_row
            ) VALUES (?, ?, ?)
            """,
            (snapshot_version_id, version_id, observation.source_row),
        )
    old_rows = list(
        connection.execute(
            """
            SELECT current.series_id, current.period_start, current.period_end,
                   current.dimensions_digest, version.dimensions_json
            FROM eia_electricity_retail_sales AS current
            JOIN eia_electricity_retail_sales_versions AS version
              ON version.retail_sales_version_id=current.current_version_id
            WHERE current.period_start=? AND current.period_end=?
              AND version.state='active' AND current.series_id IN ({})
            ORDER BY current.series_id, current.dimensions_digest
            """.format(",".join("?" for _ in parsed.series)),
            (
                authority.period_start,
                authority.period_end,
                *(series.series_id for series in parsed.series),
            ),
        )
    )
    next_row = max((item.source_row for item in parsed.observations), default=0) + 1
    for row in old_rows:
        key = (
            str(row["series_id"]),
            str(row["period_start"]),
            str(row["period_end"]),
            str(row["dimensions_digest"]),
        )
        if key in observed:
            continue
        dimensions = _mapping(loads_strict(str(row["dimensions_json"]), max_bytes=1024), "stored retail dimensions")
        mapped_dimensions = {str(key): _string(value, "stored retail dimension") for key, value in dimensions.items()}
        synthetic = _Observation(
            series_id=key[0],
            release_identity="",
            period_start=key[1],
            period_end=key[2],
            dimensions=mapped_dimensions,
            dimensions_json=dumps_strict(mapped_dimensions),
            dimensions_digest=key[3],
            value=None,
            missing_reason="source_omitted_from_complete_authoritative_snapshot",
            source_row=next_row,
        )
        next_row += 1
        version_id, appended = _append_retail_projection_version(
            connection,
            parsed,
            synthetic,
            artifact_id,
            source_snapshot_id,
            snapshot_version_id,
            run_id,
            state="tombstone",
            missing_reason=synthetic.missing_reason,
        )
        written += int(appended)
        connection.execute(
            """
            INSERT INTO eia_electricity_snapshot_membership (
                snapshot_version_id, retail_sales_version_id, source_row
            ) VALUES (?, ?, ?)
            """,
            (snapshot_version_id, version_id, synthetic.source_row),
        )
    artifact = ArtifactWrite(
        artifact_id=artifact_id,
        dataset_id="fixture.macro.eia_retail_evidence",
        content_sha256=parsed.fixture.sha256,
        media_type="application/json",
        byte_count=parsed.fixture.byte_count,
        source_reference=parsed.fixture.resource_name,
        request_scope=dict(parsed.candidate.request_scope),
        captured_at=parsed.candidate.captured_at.raw,
        captured_precision=parsed.candidate.captured_at.precision.value,
        normalization_version=parsed.normalization_version,
    )
    return (artifact,), written


def _write_eia_weekly(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    run_id: str,
) -> tuple[tuple[ArtifactWrite, ...], int]:
    rows = _array(parsed.projection["weekly_rows"], "weekly_rows", nonempty=True)
    content_by_provider_period: dict[tuple[str, str], str] = {}
    for raw in rows:
        item = _mapping(raw, "weekly_row", nonempty=True)
        _exact_keys(item, {"series_id", "period", "content_sha256"}, "weekly_row")
        provider_series = _string(item["series_id"], "weekly_row/series_id")
        period = _string(item["period"], "weekly_row/period")
        parse_date(period, pointer="/weekly_row/period")
        digest = _string(item["content_sha256"], "weekly_row/content_sha256")
        if len(digest) != _SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in digest):
            raise _fail("EIA weekly content digest is invalid")
        key = (provider_series, period)
        if key in content_by_provider_period:
            raise _fail("EIA weekly content identity is duplicated")
        content_by_provider_period[key] = digest
    scope = dict(parsed.candidate.request_scope)
    scope_digest = _sha256(dumps_strict(scope))
    artifact_id = stable_id("eia_weekly_artifact", parsed.fixture.sha256, parsed.candidate.semantic_identity)
    source_snapshot_id = stable_id("eia_weekly_snapshot", parsed.candidate.semantic_identity)
    connection.execute(
        """
        INSERT INTO eia_weekly_source_artifacts (
            artifact_id, provider, content_sha256, media_type, byte_count,
            request_scope_json, captured_at, captured_precision, run_id
        ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            parsed.candidate.provider,
            parsed.fixture.sha256,
            parsed.fixture.byte_count,
            dumps_strict(scope),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO eia_weekly_source_snapshots (
            source_snapshot_id, provider, semantic_identity, scope_json,
            scope_digest, completeness, tombstone_authoritative, row_count,
            validation_state, warnings_json, captured_at, captured_precision,
            run_id
        ) VALUES (?, ?, ?, ?, ?, 'complete', 0, ?, 'validated', '[]', ?, ?, ?)
        """,
        (
            source_snapshot_id,
            parsed.candidate.provider,
            parsed.candidate.semantic_identity,
            dumps_strict(scope),
            scope_digest,
            len(rows),
            parsed.candidate.captured_at.raw,
            parsed.candidate.captured_at.precision.value,
            run_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO eia_weekly_snapshot_artifacts (
            source_snapshot_id, artifact_id, artifact_ordinal
        ) VALUES (?, ?, 1)
        """,
        (source_snapshot_id, artifact_id),
    )
    provider_codes = {item.series_id: item.provider_series_code for item in parsed.series}
    written = 2
    for observation in parsed.observations:
        content_sha = content_by_provider_period.get(
            (provider_codes[observation.series_id], observation.period_start)
        )
        if content_sha is None:
            raise _fail("EIA weekly observation lacks its provider content identity")
        existing = connection.execute(
            """
            SELECT fundamental_version_id, correction_sequence, value_text,
                   missing_reason, content_sha256, state
            FROM eia_weekly_fundamental_versions
            WHERE series_id=? AND period=? AND dimensions_digest=?
            ORDER BY correction_sequence DESC LIMIT 1
            """,
            (observation.series_id, observation.period_start, observation.dimensions_digest),
        ).fetchone()
        if existing is not None and (
            existing["content_sha256"] == content_sha
            or (
                _stored_value_matches(existing["value_text"], observation.value)
                and existing["missing_reason"] == observation.missing_reason
                and existing["state"] == "active"
            )
        ):
            # The frozen schema makes source_snapshot_id part of the immutable
            # version, so an unchanged content identity is intentionally not
            # re-parented merely because the requested scope grew.
            continue
        sequence = 1 if existing is None else int(existing["correction_sequence"]) + 1
        previous = None if existing is None else str(existing["fundamental_version_id"])
        series = next(item for item in parsed.series if item.series_id == observation.series_id)
        version_id = stable_id(
            "eia_weekly_version",
            observation.series_id,
            observation.period_start,
            observation.dimensions_digest,
            str(sequence),
            content_sha,
        )
        connection.execute(
            """
            INSERT INTO eia_weekly_fundamental_versions (
                fundamental_version_id, series_id, period, dimensions_json,
                dimensions_digest, content_sha256, value_text, missing_reason,
                unit, value_representation, scale, available_at,
                available_precision, captured_at, captured_precision,
                correction_sequence, supersedes_fundamental_version_id,
                artifact_id, source_snapshot_id, run_id, source_row, state,
                quality_flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', '[]')
            """,
            (
                version_id,
                observation.series_id,
                observation.period_start,
                observation.dimensions_json,
                observation.dimensions_digest,
                content_sha,
                format(observation.value, "f") if observation.value is not None else None,
                observation.missing_reason,
                series.unit,
                series.value_representation,
                series.scale,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                parsed.candidate.captured_at.raw,
                parsed.candidate.captured_at.precision.value,
                sequence,
                previous,
                artifact_id,
                source_snapshot_id,
                run_id,
                observation.source_row,
            ),
        )
        connection.execute(
            """
            INSERT INTO eia_weekly_snapshot_versions (
                source_snapshot_id, fundamental_version_id, source_row
            ) VALUES (?, ?, ?)
            """,
            (source_snapshot_id, version_id, observation.source_row),
        )
        written += 1
    artifact = ArtifactWrite(
        artifact_id=artifact_id,
        dataset_id="fixture.macro.eia_weekly_evidence",
        content_sha256=parsed.fixture.sha256,
        media_type="application/json",
        byte_count=parsed.fixture.byte_count,
        source_reference=parsed.fixture.resource_name,
        request_scope=scope,
        captured_at=parsed.candidate.captured_at.raw,
        captured_precision=parsed.candidate.captured_at.precision.value,
        normalization_version=parsed.normalization_version,
    )
    return (artifact,), written


def _write_recession_periods(
    connection: sqlite3.Connection,
    parsed: _ParsedFixture,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    rows = _array(parsed.projection["recession_periods"], "recession_periods", nonempty=True)
    written = 0
    for raw in rows:
        item = _mapping(raw, "recession_period", nonempty=True)
        _exact_keys(
            item,
            {"peak_month", "trough_month", "status", "available_at", "available_precision"},
            "recession_period",
        )
        peak = _string(item["peak_month"], "recession_period/peak_month")
        trough = _string(item["trough_month"], "recession_period/trough_month") if item["trough_month"] is not None else None
        parse_date(peak, pointer="/recession_period/peak_month")
        if trough is not None and parse_date(trough, pointer="/recession_period/trough_month") < parse_date(peak, pointer="/recession_period/peak_month"):
            raise _fail("Recession period is inverted")
        status = _string(item["status"], "recession_period/status")
        if status not in {"completed", "ongoing"} or (status == "completed" and trough is None):
            raise _fail("Recession period status is invalid")
        available = _temporal(item["available_at"], item["available_precision"], "recession_period/available_at")
        assert available is not None
        existing = connection.execute(
            """
            SELECT recession_period_id FROM us_recession_periods
            WHERE provider=? AND peak_month=? AND COALESCE(trough_month, '')=COALESCE(?, '')
            """,
            (parsed.candidate.provider, peak, trough),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO us_recession_periods (
                recession_period_id, provider, peak_month, trough_month, status,
                available_at, available_precision, artifact_id, source_snapshot_id,
                run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("us_recession_period", parsed.candidate.provider, peak, trough or ""),
                parsed.candidate.provider,
                peak,
                trough,
                status,
                available.raw,
                available.precision.value,
                artifact_id,
                snapshot_id,
                run_id,
            ),
        )
        written += 1
    return written


__all__ = ("MacroStage3FixtureImporter",)
