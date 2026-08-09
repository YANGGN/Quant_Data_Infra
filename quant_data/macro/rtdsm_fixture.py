"""Controlled RTDSM-style EMPLOY fixture parsing and immutable ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..contracts import IngestionReceipt
from ..errors import Issue, ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import IngestionCoordinator, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date
from .models import (
    MacroFixtureCandidate,
    MacroObservationCandidate,
    MacroReleaseCandidate,
    MacroSeriesCandidate,
)


_EXPECTED_FIELDS = (
    "series_id",
    "provider",
    "provider_series_code",
    "title",
    "frequency",
    "unit",
    "value_representation",
    "scale",
    "dimensions_json",
    "source_vintage_identity",
    "vintage_at",
    "vintage_precision",
    "source_release_order",
    "available_at",
    "available_precision",
    "is_first_release",
    "period_start",
    "period_end",
    "value",
    "missing_reason",
)
_SERIES_ID = "fixture:philadelphia_fed_rtdsm:EMPLOY"
_SUPPORTED_MODES_JSON = dumps_strict(["latest", "as_of", "first_release"])


def _issue(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_string(value: object, *, pointer: str, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise _issue(pointer, "required", message)
    return value


def _parse_exact_temporal(
    raw: object,
    precision: object,
    *,
    pointer: str,
) -> TemporalValue:
    value = TemporalValue.parse(_require_string(raw, pointer=pointer, message="Temporal value is required"), pointer=pointer)
    if precision not in (TemporalPrecision.DATE.value, TemporalPrecision.DATETIME.value):
        raise _issue(f"{pointer}_precision", "enum", "Temporal precision must be date or datetime")
    if value.precision.value != precision:
        raise _issue(f"{pointer}_precision", "precision", "Declared precision does not match temporal value")
    return value


def _parse_dimensions(raw: object, *, pointer: str) -> tuple[str, str]:
    if not isinstance(raw, str):
        raise _issue(pointer, "type", "Dimensions must be strict JSON text")
    try:
        dimensions = loads_strict(raw, max_bytes=1024)
    except ValidationError as exc:
        raise _issue(pointer, "json", "Dimensions must be strict JSON") from exc
    if dimensions != {}:
        raise _issue(pointer, "dimensions", "Stage 1 RTDSM dimensions must be the explicit empty object")
    canonical = dumps_strict(dimensions)
    return canonical, _sha256(canonical)


def _parse_decimal(raw: object, *, pointer: str) -> Decimal | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise _issue(pointer, "type", "Macro value must be a decimal string or empty")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise _issue(pointer, "decimal", "Macro value must be finite") from exc
    if not value.is_finite():
        raise _issue(pointer, "finite", "Macro value must be finite")
    return value


def _semantic_decimal(value: Decimal | None) -> str | None:
    """Return a formatting-independent finite numeric identity.

    Evidence retains its original bytes, while semantic replay treats decimal
    spellings such as ``100.0`` and ``100.00`` as the same numeric fact.
    """

    if value is None:
        return None
    normalized = value.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _parse_bool(raw: object, *, pointer: str) -> bool:
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise _issue(pointer, "boolean", "Expected lowercase true or false")


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    series: MacroSeriesCandidate
    release: MacroReleaseCandidate
    observation: MacroObservationCandidate


def parse_rtdsm_fixture(fixture: Fixture) -> MacroFixtureCandidate:
    """Validate one manifest-verified RTDSM fixture before any write path.

    The parser deliberately does not rely on the fixture filename or source
    order for semantics.  Its output is immutable enough to be safely passed
    to the shared no-write coordinator after all validation has completed.
    """

    if (
        fixture.byte_count != len(fixture.bytes)
        or hashlib.sha256(fixture.bytes).hexdigest() != fixture.sha256
    ):
        raise _issue("/resource", "digest", "Fixture bytes do not match the reviewed digest")
    if fixture.store != StoreRole.MACRO.value:
        raise _issue("/store", "store", "RTDSM fixture must target the macro store")
    if fixture.canonical_dataset_id != "fixture.macro.rtdsm_employ":
        raise _issue("/canonical_dataset_id", "dataset", "Unexpected RTDSM canonical dataset")
    if fixture.evidence_dataset_id != "fixture.macro.rtdsm_employ_evidence":
        raise _issue("/evidence_dataset_id", "dataset", "Unexpected RTDSM evidence dataset")
    if fixture.provider != "fixture_philadelphia_fed":
        raise _issue("/provider", "provider", "Unexpected RTDSM fixture provider")
    if fixture.identity_dataset_id is not None:
        raise _issue("/identity_dataset_id", "dataset", "RTDSM fixture has no separate identity dataset")

    try:
        text = fixture.bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _issue("/resource", "utf8", "Fixture resource is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), restkey="__extra__")
    if tuple(reader.fieldnames or ()) != _EXPECTED_FIELDS:
        raise _issue("/header", "schema", "RTDSM fixture header does not match the reviewed schema")

    scope = dict(fixture.request_scope)
    if set(scope) != {"series_id", "source_vintage_identity", "completeness"}:
        raise _issue("/request_scope", "schema", "RTDSM fixture scope is invalid")
    if scope["series_id"] != _SERIES_ID or scope["completeness"] != "complete":
        raise _issue("/request_scope", "scope", "RTDSM fixture scope must be complete EMPLOY coverage")
    source_vintage_identity = _require_string(
        scope["source_vintage_identity"],
        pointer="/request_scope/source_vintage_identity",
        message="Source vintage identity is required",
    )
    captured_at = TemporalValue.parse(fixture.captured_at, pointer="/captured_at")
    if captured_at.precision is not TemporalPrecision.DATETIME:
        raise _issue("/captured_at", "precision", "Fixture capture must be an aware datetime")
    metadata = dict(fixture.metadata)
    normalization_version = _require_string(
        metadata.get("normalization_version"),
        pointer="/metadata/normalization_version",
        message="Normalization version is required",
    )
    expected_rows = metadata.get("expected_rows")
    expected_missing = metadata.get("expected_missing")
    if (
        isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or isinstance(expected_missing, bool)
        or not isinstance(expected_missing, int)
    ):
        raise _issue("/metadata", "type", "Expected fixture row and missing counts must be integers")

    rows: list[_ParsedRow] = []
    duplicate_keys: set[tuple[str, str, str, str]] = set()
    for source_row, row in enumerate(reader, start=2):
        if row.get("__extra__") is not None or any(row.get(field) is None for field in _EXPECTED_FIELDS):
            raise _issue(f"/rows/{source_row}", "schema", "RTDSM fixture row has missing or extra fields")
        assert row is not None
        prefix = f"/rows/{source_row}"
        dimensions_json, dimensions_digest = _parse_dimensions(
            row["dimensions_json"], pointer=f"{prefix}/dimensions_json"
        )
        series_id = _require_string(row["series_id"], pointer=f"{prefix}/series_id", message="Series ID is required")
        if series_id != _SERIES_ID or series_id != scope["series_id"]:
            raise _issue(f"{prefix}/series_id", "identity", "RTDSM row series ID conflicts with scope")
        provider = _require_string(row["provider"], pointer=f"{prefix}/provider", message="Provider is required")
        if provider != fixture.provider:
            raise _issue(f"{prefix}/provider", "provider", "Row provider conflicts with fixture provider")
        frequency = _require_string(row["frequency"], pointer=f"{prefix}/frequency", message="Frequency is required")
        if frequency != "monthly":
            raise _issue(f"{prefix}/frequency", "frequency", "RTDSM fixture frequency must be monthly")
        series = MacroSeriesCandidate(
            series_id=series_id,
            provider=provider,
            provider_series_code=_require_string(row["provider_series_code"], pointer=f"{prefix}/provider_series_code", message="Provider series code is required"),
            title=_require_string(row["title"], pointer=f"{prefix}/title", message="Series title is required"),
            frequency=frequency,
            unit=_require_string(row["unit"], pointer=f"{prefix}/unit", message="Unit is required"),
            value_representation=_require_string(row["value_representation"], pointer=f"{prefix}/value_representation", message="Value representation is required"),
            scale=_require_string(row["scale"], pointer=f"{prefix}/scale", message="Scale is required"),
            dimensions_json=dimensions_json,
            dimensions_digest=dimensions_digest,
        )
        row_vintage_identity = _require_string(
            row["source_vintage_identity"],
            pointer=f"{prefix}/source_vintage_identity",
            message="Source vintage identity is required",
        )
        if row_vintage_identity != source_vintage_identity:
            raise _issue(f"{prefix}/source_vintage_identity", "identity", "Row vintage conflicts with scope")
        release = MacroReleaseCandidate(
            source_vintage_identity=row_vintage_identity,
            vintage_at=_parse_exact_temporal(
                row["vintage_at"], row["vintage_precision"], pointer=f"{prefix}/vintage_at"
            ),
            source_release_order=_require_string(
                row["source_release_order"],
                pointer=f"{prefix}/source_release_order",
                message="Source release order is required",
            ),
            available_at=_parse_exact_temporal(
                row["available_at"], row["available_precision"], pointer=f"{prefix}/available_at"
            ),
            is_first_release=_parse_bool(row["is_first_release"], pointer=f"{prefix}/is_first_release"),
            first_release_evidence=(
                f"fixture_source_vintage:{row_vintage_identity}"
                if row["is_first_release"] == "true"
                else None
            ),
        )
        period_start = parse_date(row["period_start"], pointer=f"{prefix}/period_start").isoformat()
        period_end = parse_date(row["period_end"], pointer=f"{prefix}/period_end").isoformat()
        if period_start > period_end:
            raise _issue(f"{prefix}/period_end", "period_order", "Period end cannot precede period start")
        value = _parse_decimal(row["value"], pointer=f"{prefix}/value")
        missing_reason = row["missing_reason"] or None
        if (value is None) == (missing_reason is None):
            raise _issue(
                prefix,
                "missingness",
                "Macro observation must contain exactly one of value or missing_reason",
            )
        observation = MacroObservationCandidate(
            period_start=period_start,
            period_end=period_end,
            dimensions_json=dimensions_json,
            dimensions_digest=dimensions_digest,
            value=value,
            missing_reason=missing_reason,
            source_row=source_row,
        )
        duplicate_key = (
            series.series_id,
            observation.period_start,
            observation.period_end,
            release.source_vintage_identity,
        )
        if duplicate_key in duplicate_keys:
            raise _issue(prefix, "duplicate", "Duplicate source-vintage logical observation")
        duplicate_keys.add(duplicate_key)
        rows.append(_ParsedRow(series=series, release=release, observation=observation))

    if not rows:
        raise _issue("/rows", "min_items", "RTDSM fixture must contain observations")
    if len(rows) != expected_rows:
        raise _issue("/metadata/expected_rows", "count", "Fixture row count does not match reviewed metadata")
    if sum(item.observation.value is None for item in rows) != expected_missing:
        raise _issue("/metadata/expected_missing", "count", "Fixture missing count does not match reviewed metadata")
    baseline_series = rows[0].series
    baseline_release = rows[0].release
    for item in rows[1:]:
        if item.series != baseline_series:
            raise _issue("/rows", "series_contract", "Unit, scale, dimensions, or metadata conflicts within one series")
        if item.release != baseline_release:
            raise _issue("/rows", "release_contract", "Release metadata conflicts within one vintage")

    observations = tuple(
        item.observation
        for item in sorted(
            rows,
            key=lambda item: (
                item.observation.period_start,
                item.observation.period_end,
                item.observation.dimensions_json,
                item.observation.source_row,
            ),
        )
    )
    scope_digest = _sha256(dumps_strict(scope))
    semantic_rows = [
        {
            "period_start": observation.period_start,
            "period_end": observation.period_end,
            "dimensions": observation.dimensions_json,
            "value": _semantic_decimal(observation.value),
            "missing_reason": observation.missing_reason,
        }
        for observation in observations
    ]
    semantic_material = {
        "canonical_dataset_id": fixture.canonical_dataset_id,
        "evidence_dataset_id": fixture.evidence_dataset_id,
        "scope": scope,
        "completeness": scope["completeness"],
        "normalization_version": normalization_version,
        "series": {
            "series_id": baseline_series.series_id,
            "provider": baseline_series.provider,
            "provider_series_code": baseline_series.provider_series_code,
            "title": baseline_series.title,
            "frequency": baseline_series.frequency,
            "unit": baseline_series.unit,
            "value_representation": baseline_series.value_representation,
            "scale": baseline_series.scale,
            "dimensions_json": baseline_series.dimensions_json,
            "supported_modes_json": _SUPPORTED_MODES_JSON,
            "availability_basis": "source_release",
        },
        "release": {
            "source_vintage_identity": baseline_release.source_vintage_identity,
            "vintage_at": baseline_release.vintage_at.raw,
            "vintage_precision": baseline_release.vintage_at.precision.value,
            "source_release_order": baseline_release.source_release_order,
            "available_at": baseline_release.available_at.raw,
            "available_precision": baseline_release.available_at.precision.value,
            "is_first_release": baseline_release.is_first_release,
            "first_release_evidence": baseline_release.first_release_evidence,
        },
        "rows": semantic_rows,
    }
    semantic_identity = _sha256(dumps_strict(semantic_material))
    return MacroFixtureCandidate(
        fixture_id=fixture.id,
        ingestion_family_id=fixture.ingestion_family_id,
        evidence_dataset_id=fixture.evidence_dataset_id,
        canonical_dataset_id=fixture.canonical_dataset_id,
        provider=fixture.provider,
        artifact_sha256=fixture.sha256,
        artifact_byte_count=fixture.byte_count,
        artifact_resource=fixture.resource_name,
        captured_at=captured_at,
        normalization_version=normalization_version,
        request_scope=scope,
        scope_digest=scope_digest,
        semantic_identity=semantic_identity,
        series=baseline_series,
        release=baseline_release,
        observations=observations,
    )


def _assert_series_contract(connection: sqlite3.Connection, candidate: MacroFixtureCandidate, run_id: str) -> None:
    series = candidate.series
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json, supported_modes_json,
               availability_basis
        FROM macro_series WHERE series_id=?
        """,
        (series.series_id,),
    ).fetchone()
    expected = (
        series.provider,
        series.provider_series_code,
        series.title,
        series.frequency,
        series.unit,
        series.value_representation,
        series.scale,
        series.dimensions_json,
        _SUPPORTED_MODES_JSON,
        "source_release",
    )
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
        return
    if tuple(existing) != expected:
        raise ValidationError("Macro series contract conflicts with existing canonical metadata")


def _ensure_release(
    connection: sqlite3.Connection,
    candidate: MacroFixtureCandidate,
    run_id: str,
) -> str:
    release = candidate.release
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision, source_release_order,
               available_at, available_precision, is_first_release,
               first_release_evidence
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (candidate.series.series_id, release.source_vintage_identity),
    ).fetchone()
    immutable_expected = (
        release.vintage_at.raw,
        release.vintage_at.precision.value,
        release.source_release_order,
        int(release.is_first_release),
        release.first_release_evidence,
    )
    if existing is not None:
        immutable_actual = (
            existing["vintage_at"],
            existing["vintage_precision"],
            existing["source_release_order"],
            existing["is_first_release"],
            existing["first_release_evidence"],
        )
        if immutable_actual != immutable_expected:
            raise ValidationError("Source-vintage metadata conflicts with an immutable macro release")
        return str(existing["release_id"])
    release_id = stable_id(
        "macro_release",
        candidate.series.series_id,
        release.source_vintage_identity,
    )
    connection.execute(
        """
        INSERT INTO macro_releases (
            release_id, series_id, source_vintage_identity, vintage_at,
            vintage_precision, source_release_order, available_at,
            available_precision, is_first_release, first_release_evidence,
            first_seen_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            release_id,
            candidate.series.series_id,
            release.source_vintage_identity,
            release.vintage_at.raw,
            release.vintage_at.precision.value,
            release.source_release_order,
            release.available_at.raw,
            release.available_at.precision.value,
            int(release.is_first_release),
            release.first_release_evidence,
            run_id,
        ),
    )
    return release_id


def _version_payload_matches(
    row: sqlite3.Row,
    candidate: MacroFixtureCandidate,
    observation: MacroObservationCandidate,
) -> bool:
    stored_value = row["value_text"]
    if stored_value is None:
        values_match = observation.value is None
    elif observation.value is None:
        values_match = False
    else:
        try:
            values_match = _semantic_decimal(Decimal(str(stored_value))) == _semantic_decimal(observation.value)
        except (InvalidOperation, ValueError):
            values_match = False
    return (
        values_match,
        row["missing_reason"],
        row["unit"],
        row["value_representation"],
        row["scale"],
        row["available_at"],
        row["available_precision"],
    ) == (
        True,
        observation.missing_reason,
        candidate.series.unit,
        candidate.series.value_representation,
        candidate.series.scale,
        candidate.release.available_at.raw,
        candidate.release.available_at.precision.value,
    )


def _append_or_reuse_version(
    connection: sqlite3.Connection,
    candidate: MacroFixtureCandidate,
    *,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
    observation: MacroObservationCandidate,
) -> tuple[str, bool]:
    existing = connection.execute(
        """
        SELECT version_id, correction_sequence, value_text, missing_reason,
               unit, value_representation, scale, available_at, available_precision
        FROM macro_observation_versions
        WHERE series_id=? AND period_start=? AND period_end=?
          AND dimensions_digest=? AND source_vintage_identity=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (
            candidate.series.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_digest,
            candidate.release.source_vintage_identity,
        ),
    ).fetchone()
    if existing is not None and _version_payload_matches(existing, candidate, observation):
        return str(existing["version_id"]), False
    correction_sequence = 1 if existing is None else int(existing["correction_sequence"]) + 1
    supersedes_version_id = None if existing is None else str(existing["version_id"])
    payload_material = {
        "series_id": candidate.series.series_id,
        "period_start": observation.period_start,
        "period_end": observation.period_end,
        "dimensions_digest": observation.dimensions_digest,
        "source_vintage_identity": candidate.release.source_vintage_identity,
        "correction_sequence": correction_sequence,
        "value": observation.value,
        "missing_reason": observation.missing_reason,
        "unit": candidate.series.unit,
        "value_representation": candidate.series.value_representation,
        "scale": candidate.series.scale,
        "available_at": candidate.release.available_at.raw,
        "available_precision": candidate.release.available_at.precision.value,
    }
    version_id = stable_id(
        "macro_version",
        candidate.series.series_id,
        observation.period_start,
        observation.period_end,
        observation.dimensions_digest,
        candidate.release.source_vintage_identity,
        str(correction_sequence),
        _sha256(dumps_strict(payload_material)),
    )
    connection.execute(
        """
        INSERT INTO macro_observation_versions (
            version_id, series_id, period_start, period_end, dimensions_json,
            dimensions_digest, release_id, source_vintage_identity,
            correction_sequence, value_text, missing_reason, unit,
            value_representation, scale, available_at, available_precision,
            captured_at, captured_precision, supersedes_version_id, artifact_id,
            snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            candidate.series.series_id,
            observation.period_start,
            observation.period_end,
            observation.dimensions_json,
            observation.dimensions_digest,
            release_id,
            candidate.release.source_vintage_identity,
            correction_sequence,
            format(observation.value, "f") if observation.value is not None else None,
            observation.missing_reason,
            candidate.series.unit,
            candidate.series.value_representation,
            candidate.series.scale,
            candidate.release.available_at.raw,
            candidate.release.available_at.precision.value,
            candidate.captured_at.raw,
            candidate.captured_at.precision.value,
            supersedes_version_id,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
        ),
    )
    return version_id, True


class MacroFixtureImporter:
    """Import verified synthetic RTDSM fixtures through the shared coordinator."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map)

    def import_fixture(self, fixture_id: str) -> IngestionReceipt:
        fixture = self._fixture_manifest.get(fixture_id)
        candidate = parse_rtdsm_fixture(fixture)
        run_id = stable_id(
            "macro_run",
            candidate.canonical_dataset_id,
            candidate.semantic_identity,
        )
        scope = {
            "fixture_id": candidate.fixture_id,
            "ingestion_family_id": candidate.ingestion_family_id,
            "request_scope": dict(candidate.request_scope),
            "completeness": candidate.request_scope["completeness"],
        }

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(connection, candidate, active_run_id)

        return self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=candidate.canonical_dataset_id,
            output_dataset_ids=(candidate.evidence_dataset_id, candidate.canonical_dataset_id),
            semantic_identity=candidate.semantic_identity,
            run_id=run_id,
            command=candidate.ingestion_family_id,
            scope=scope,
            started_at=candidate.captured_at.raw or "",
            completed_at=candidate.captured_at.raw or "",
            fetched_count=len(candidate.observations),
            writer=writer,
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        candidate: MacroFixtureCandidate,
        run_id: str,
    ) -> WriteResult:
        _assert_series_contract(connection, candidate, run_id)
        release_id = _ensure_release(connection, candidate, run_id)
        artifact_id = stable_id(
            "macro_artifact",
            candidate.evidence_dataset_id,
            candidate.artifact_sha256,
            candidate.scope_digest,
        )
        snapshot_id = stable_id(
            "macro_snapshot",
            candidate.canonical_dataset_id,
            candidate.semantic_identity,
        )
        scope_id = stable_id("macro_scope", snapshot_id, candidate.scope_digest)
        connection.execute(
            """
            INSERT INTO macro_source_artifacts (
                artifact_id, dataset_id, sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision,
                source_resource, normalization_version, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                candidate.evidence_dataset_id,
                candidate.artifact_sha256,
                "text/csv",
                candidate.artifact_byte_count,
                dumps_strict(dict(candidate.request_scope)),
                candidate.captured_at.raw,
                candidate.captured_at.precision.value,
                candidate.artifact_resource,
                candidate.normalization_version,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO macro_source_snapshots (
                snapshot_id, series_id, release_id, artifact_id,
                semantic_identity, completeness, row_count, validation_state,
                warnings_json, run_id
            ) VALUES (?, ?, ?, ?, ?, 'complete', ?, 'validated', '[]', ?)
            """,
            (
                snapshot_id,
                candidate.series.series_id,
                release_id,
                artifact_id,
                candidate.semantic_identity,
                len(candidate.observations),
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', 0)
            """,
            (
                scope_id,
                snapshot_id,
                dumps_strict(dict(candidate.request_scope)),
                candidate.scope_digest,
            ),
        )
        appended_count = 0
        for observation in candidate.observations:
            version_id, appended = _append_or_reuse_version(
                connection,
                candidate,
                release_id=release_id,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                run_id=run_id,
                observation=observation,
            )
            appended_count += int(appended)
            connection.execute(
                """
                INSERT INTO macro_snapshot_observation_membership (
                    snapshot_id, version_id, source_row
                ) VALUES (?, ?, ?)
                """,
                (snapshot_id, version_id, observation.source_row),
            )
        return WriteResult(
            written_count=appended_count,
            artifact_id=artifact_id,
            snapshot_id=snapshot_id,
        )
