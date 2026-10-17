"""Bounded FMP U.S. Treasury curve parsing and publication.

The provider boundary lives in the operations package. This module accepts
only complete, already-captured JSON responses, normalizes the frozen
twelve-tenor manifest before opening a store, and publishes through the shared
semantic no-write coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Final

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date


COLLECTOR_ID: Final = "fmp.macro.treasury_yield_curve_history"
HANDLER: Final = "macro.fmp_treasury_yield_curve_history"
OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
    "fixture.macro.treasury_yield_curves",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[-1]
NORMALIZATION_VERSION: Final = "fmp_treasury_curve_v1"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 20_000
MAX_WINDOW_DAYS: Final = 366
SOURCE_REFERENCE: Final = "fmp/stable/treasury-rates"
PROVIDER: Final = "fmp"
CURVE_VARIANT: Final = "par_yield"


@dataclass(frozen=True, slots=True)
class FmpTreasuryTenor:
    provider_field: str
    tenor: str
    slug: str

    @property
    def series_id(self) -> str:
        return f"macro.treasury.par_yield.{self.slug}"


TENOR_MANIFEST: Final = (
    FmpTreasuryTenor("month1", "1M", "1m"),
    FmpTreasuryTenor("month2", "2M", "2m"),
    FmpTreasuryTenor("month3", "3M", "3m"),
    FmpTreasuryTenor("month6", "6M", "6m"),
    FmpTreasuryTenor("year1", "1Y", "1y"),
    FmpTreasuryTenor("year2", "2Y", "2y"),
    FmpTreasuryTenor("year3", "3Y", "3y"),
    FmpTreasuryTenor("year5", "5Y", "5y"),
    FmpTreasuryTenor("year7", "7Y", "7y"),
    FmpTreasuryTenor("year10", "10Y", "10y"),
    FmpTreasuryTenor("year20", "20Y", "20y"),
    FmpTreasuryTenor("year30", "30Y", "30y"),
)


@dataclass(frozen=True, slots=True)
class FmpTreasuryCurveObservation:
    curve_date: str
    provider_field: str
    tenor: str
    series_id: str
    value_text: str | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class FmpTreasuryCurveCapture:
    response_bytes: bytes
    response_sha256: str
    semantic_identity: str
    captured_at: str
    captured_precision: str
    request_start_date: str
    request_end_date: str
    observations: tuple[FmpTreasuryCurveObservation, ...]


@dataclass(frozen=True, slots=True)
class FmpTreasuryCurvePublishReport:
    outcome: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_curves: int
    written_curve_versions: int
    written_observation_versions: int


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _normalized_decimal(value: Decimal) -> str:
    ensure_number_within_limits(value)
    normalized = value.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _value(value: object, *, field: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_null"
    if isinstance(value, bool):
        raise _fail(f"FMP Treasury field {field} must be a finite decimal or null")
    if isinstance(value, str):
        if not value or value != value.strip():
            raise _fail(f"FMP Treasury field {field} must be a finite decimal or null")
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise _fail(
                f"FMP Treasury field {field} must be a finite decimal or null"
            ) from exc
    elif isinstance(value, (int, Decimal)):
        parsed = Decimal(value)
    else:
        raise _fail(f"FMP Treasury field {field} must be a finite decimal or null")
    if not parsed.is_finite():
        raise _fail(f"FMP Treasury field {field} must be a finite decimal or null")
    return _normalized_decimal(parsed), None


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("FMP Treasury capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("FMP Treasury request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def parse_fmp_treasury_curve(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> FmpTreasuryCurveCapture:
    """Normalize one complete FMP Treasury response without opening a store."""

    if not isinstance(body, bytes) or not body:
        raise _fail("FMP Treasury response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("FMP Treasury response exceeds its byte bound")
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(raw, list):
        raise _fail("FMP Treasury response must be a JSON array")
    if len(raw) > MAX_RESPONSE_ROWS:
        raise ResourceLimitError("FMP Treasury response exceeds its row bound")

    normalized_rows: list[tuple[str, tuple[tuple[str | None, str | None], ...]]] = []
    seen_dates: set[str] = set()
    required_fields = {item.provider_field for item in TENOR_MANIFEST}
    for raw_row in raw:
        if not isinstance(raw_row, dict):
            raise _fail("Each FMP Treasury row must be an object")
        if "date" not in raw_row or not required_fields.issubset(raw_row):
            raise _fail("FMP Treasury row is missing a declared field")
        raw_date = raw_row["date"]
        if not isinstance(raw_date, str):
            raise _fail("FMP Treasury row date must be YYYY-MM-DD")
        curve_date = parse_date(raw_date, pointer="/rows/date")
        curve_date_text = curve_date.isoformat()
        if curve_date < start or curve_date > end:
            raise _fail("FMP Treasury row date is outside the requested window")
        if curve_date_text in seen_dates:
            raise _fail("FMP Treasury row dates must be unique")
        seen_dates.add(curve_date_text)
        values = tuple(
            _value(raw_row[item.provider_field], field=item.provider_field)
            for item in TENOR_MANIFEST
        )
        normalized_rows.append((curve_date_text, values))

    normalized_rows.sort(key=lambda item: item[0])
    observations: list[FmpTreasuryCurveObservation] = []
    semantic_rows: list[dict[str, object]] = []
    for date_ordinal, (curve_date, values) in enumerate(normalized_rows):
        semantic_values: dict[str, object] = {}
        for tenor_ordinal, (tenor, (value_text, missing_reason)) in enumerate(
            zip(TENOR_MANIFEST, values, strict=True),
            start=1,
        ):
            semantic_values[tenor.provider_field] = {
                "value": value_text,
                "missing_reason": missing_reason,
            }
            observations.append(
                FmpTreasuryCurveObservation(
                    curve_date=curve_date,
                    provider_field=tenor.provider_field,
                    tenor=tenor.tenor,
                    series_id=tenor.series_id,
                    value_text=value_text,
                    missing_reason=missing_reason,
                    source_row=date_ordinal * len(TENOR_MANIFEST) + tenor_ordinal,
                )
            )
        semantic_rows.append({"date": curve_date, "values": semantic_values})

    semantic_material = {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": PROVIDER,
        "resource": SOURCE_REFERENCE,
        "request_scope": {
            "from": start_text,
            "to": end_text,
            "curve_variant": CURVE_VARIANT,
        },
        "tenor_manifest": [
            {
                "provider_field": item.provider_field,
                "tenor": item.tenor,
                "series_id": item.series_id,
            }
            for item in TENOR_MANIFEST
        ],
        "normalized_12_tenor_batch": semantic_rows,
    }
    return FmpTreasuryCurveCapture(
        response_bytes=body,
        response_sha256=_sha256_bytes(body),
        semantic_identity=_sha256_text(dumps_strict(semantic_material)),
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start_text,
        request_end_date=end_text,
        observations=tuple(observations),
    )


def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _registry_binding(registry: object) -> Registry:
    if not isinstance(registry, Registry):
        raise _fail("FMP Treasury publisher requires the reviewed registry")
    collectors = [
        item for item in registry.collectors if str(item.get("id")) == COLLECTOR_ID
    ]
    datasets = {item.id: item for item in registry.datasets}
    if len(collectors) != 1:
        raise _fail("FMP Treasury registry collector binding is invalid")
    collector = collectors[0]
    if (
        collector.get("handler") != HANDLER
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != ["FMP_API_KEY"]
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail("FMP Treasury registry collector binding is invalid")
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or COLLECTOR_ID not in dataset.collector_ids
        ):
            raise _fail("FMP Treasury registry dataset binding is invalid")
    return registry


def _capture_binding(capture: object) -> FmpTreasuryCurveCapture:
    if not isinstance(capture, FmpTreasuryCurveCapture):
        raise _fail("FMP Treasury capture binding is invalid")
    reparsed = parse_fmp_treasury_curve(
        capture.response_bytes,
        captured_at=capture.captured_at,
        start_date=capture.request_start_date,
        end_date=capture.request_end_date,
    )
    if reparsed != capture:
        raise _fail("FMP Treasury capture binding is invalid")
    return capture


class FmpTreasuryCurvePublisher:
    """Publish one pre-parsed Treasury batch to the bound macro store."""

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        self._configure(
            macro_store=macro_store,
            project_root=project_root,
            registry=registry,
            canonical=_canonical,
        )

    def _configure(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        canonical: bool,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("FMP Treasury publisher paths are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail("FMP Treasury publisher target is unavailable") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail("FMP Treasury publisher target binding is invalid")
        if not canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("FMP Treasury fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("FMP Treasury fixture root is too broad")
        self._registry = _registry_binding(registry)
        self._root = root
        self._store = store
        self._stores = _store_map(root, store)
        self._coordinator = IngestionCoordinator(
            self._stores,
            code_version=NORMALIZATION_VERSION,
        )

    def publish(
        self, capture: FmpTreasuryCurveCapture
    ) -> FmpTreasuryCurvePublishReport:
        prepared = _capture_binding(capture)
        run_id = stable_id("fmp_treasury_run", prepared.semantic_identity)
        scope = {
            "provider": PROVIDER,
            "resource": SOURCE_REFERENCE,
            "from": prepared.request_start_date,
            "to": prepared.request_end_date,
            "curve_variant": CURVE_VARIANT,
            "tenors": [item.tenor for item in TENOR_MANIFEST],
            "completeness": "complete",
        }
        counts = {
            "curves": 0,
            "curve_versions": 0,
            "observation_versions": 0,
        }

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(
                connection,
                prepared,
                active_run_id,
                scope,
                counts,
            )

        receipt = self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=CANONICAL_DATASET_ID,
            output_dataset_ids=OUTPUT_DATASET_IDS,
            semantic_identity=prepared.semantic_identity,
            run_id=run_id,
            command=COLLECTOR_ID,
            scope=scope,
            started_at=prepared.captured_at,
            completed_at=prepared.captured_at,
            fetched_count=len(prepared.observations),
            writer=writer,
        )
        if receipt.outcome == "unchanged":
            outcome = "unchanged"
        elif receipt.outcome == "succeeded":
            outcome = "published"
        else:
            raise ConflictError("FMP Treasury publisher returned an invalid outcome")
        return FmpTreasuryCurvePublishReport(
            outcome=outcome,
            semantic_identity=prepared.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_curves=counts["curves"],
            written_curve_versions=counts["curve_versions"],
            written_observation_versions=counts["observation_versions"],
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        capture: FmpTreasuryCurveCapture,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            "fmp_treasury_artifact",
            capture.semantic_identity,
            capture.response_sha256,
        )
        snapshot_id = stable_id(
            "fmp_treasury_snapshot",
            CANONICAL_DATASET_ID,
            capture.semantic_identity,
        )
        for tenor in TENOR_MANIFEST:
            _ensure_series(connection, tenor, run_id)
        release_ids: dict[tuple[str, str], str] = {}
        for observation in capture.observations:
            key = (observation.series_id, observation.curve_date)
            if key not in release_ids:
                release_ids[key] = _ensure_release(
                    connection,
                    observation.series_id,
                    observation.curve_date,
                    capture.captured_at,
                    run_id,
                )

        connection.execute(
            """
            INSERT INTO macro_source_artifacts (
                artifact_id, dataset_id, sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision,
                source_resource, normalization_version, run_id
            ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, 'datetime', ?, ?, ?)
            """,
            (
                artifact_id,
                EVIDENCE_DATASET_ID,
                capture.response_sha256,
                len(capture.response_bytes),
                dumps_strict(scope),
                capture.captured_at,
                SOURCE_REFERENCE,
                NORMALIZATION_VERSION,
                run_id,
            ),
        )
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
                capture.semantic_identity,
                len(capture.observations),
                run_id,
            ),
        )
        scope_digest = _sha256_text(dumps_strict(scope))
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', 0)
            """,
            (
                stable_id("fmp_treasury_scope", snapshot_id, scope_digest),
                snapshot_id,
                dumps_strict(scope),
                scope_digest,
            ),
        )
        for tenor in TENOR_MANIFEST:
            _ensure_dimension(
                connection,
                tenor,
                capture.captured_at,
                snapshot_id,
                run_id,
            )

        for observation in capture.observations:
            release_id = release_ids[(observation.series_id, observation.curve_date)]
            version_id, appended = _append_or_reuse_observation(
                connection,
                observation,
                release_id,
                artifact_id,
                snapshot_id,
                capture.captured_at,
                run_id,
            )
            counts["observation_versions"] += int(appended)
            connection.execute(
                """
                INSERT INTO macro_snapshot_observation_membership (
                    snapshot_id, version_id, source_row
                ) VALUES (?, ?, ?)
                """,
                (snapshot_id, version_id, observation.source_row),
            )
            curve_id, inserted_curve = _ensure_curve(
                connection,
                observation.curve_date,
                snapshot_id,
                run_id,
            )
            counts["curves"] += int(inserted_curve)
            _, inserted_curve_version = _append_or_reuse_curve_version(
                connection,
                observation,
                curve_id,
                artifact_id,
                snapshot_id,
                capture.captured_at,
                run_id,
            )
            counts["curve_versions"] += int(inserted_curve_version)

        written_count = (
            counts["curves"]
            + counts["curve_versions"]
            + counts["observation_versions"]
        )
        artifact = ArtifactWrite(
            artifact_id=artifact_id,
            dataset_id=EVIDENCE_DATASET_ID,
            content_sha256=capture.response_sha256,
            media_type="application/json",
            byte_count=len(capture.response_bytes),
            source_reference=SOURCE_REFERENCE,
            request_scope=scope,
            captured_at=capture.captured_at,
            captured_precision="datetime",
            normalization_version=NORMALIZATION_VERSION,
        )
        return WriteResult(
            written_count=written_count,
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CANONICAL_DATASET_ID,
                semantic_identity=capture.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.observations),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        "fmp_treasury_quality",
                        snapshot_id,
                        "complete_12_tenor_batch",
                    ),
                    dataset_id=CANONICAL_DATASET_ID,
                    rule_id="fmp_treasury.complete_12_tenor_batch",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "date_count": len(capture.observations)
                        // len(TENOR_MANIFEST),
                        "observation_count": len(capture.observations),
                        "tenor_count": len(TENOR_MANIFEST),
                    },
                ),
            ),
        )


def _series_metadata(tenor: FmpTreasuryTenor) -> tuple[object, ...]:
    return (
        PROVIDER,
        tenor.provider_field,
        f"U.S. Treasury par yield {tenor.tenor}",
        "daily",
        "percent",
        "rate",
        "1",
        dumps_strict({"tenor": tenor.tenor}),
        dumps_strict(["latest", "as_of"]),
        "local_capture",
    )


def _ensure_series(
    connection: sqlite3.Connection,
    tenor: FmpTreasuryTenor,
    run_id: str,
) -> None:
    expected = _series_metadata(tenor)
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json,
               supported_modes_json, availability_basis
        FROM macro_series
        WHERE series_id=?
        """,
        (tenor.series_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO macro_series (
                series_id, provider, provider_series_code, title, frequency,
                unit, value_representation, scale, dimensions_json,
                supported_modes_json, availability_basis, created_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (tenor.series_id, *expected, run_id),
        )
    elif tuple(existing) != expected:
        raise _fail("FMP Treasury series conflicts with immutable catalog metadata")


def _source_vintage_identity(curve_date: str) -> str:
    return f"fmp:treasury:{CURVE_VARIANT}:{curve_date}"


def _ensure_release(
    connection: sqlite3.Connection,
    series_id: str,
    curve_date: str,
    captured_at: str,
    run_id: str,
) -> str:
    source_vintage_identity = _source_vintage_identity(curve_date)
    expected = (curve_date, "date", curve_date, 0, None, "current_state")
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision,
               source_release_order, is_first_release,
               first_release_evidence, release_stage
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (series_id, source_vintage_identity),
    ).fetchone()
    if existing is not None:
        if tuple(existing)[1:] != expected:
            raise _fail("FMP Treasury release conflicts with immutable metadata")
        return str(existing["release_id"])
    release_id = stable_id(
        "fmp_treasury_release",
        series_id,
        source_vintage_identity,
    )
    connection.execute(
        """
        INSERT INTO macro_releases (
            release_id, series_id, source_vintage_identity, vintage_at,
            vintage_precision, source_release_order, available_at,
            available_precision, is_first_release, first_release_evidence,
            first_seen_run_id, release_stage, source_published_at,
            source_published_precision
        ) VALUES (?, ?, ?, ?, 'date', ?, ?, 'datetime', 0, NULL, ?,
                  'current_state', NULL, 'unknown')
        """,
        (
            release_id,
            series_id,
            source_vintage_identity,
            curve_date,
            curve_date,
            captured_at,
            run_id,
        ),
    )
    return release_id


def _ensure_dimension(
    connection: sqlite3.Connection,
    tenor: FmpTreasuryTenor,
    captured_at: str,
    snapshot_id: str,
    run_id: str,
) -> None:
    existing = connection.execute(
        """
        SELECT dimension_id
        FROM macro_series_dimensions
        WHERE series_id=? AND dimension_key='tenor' AND dimension_value=?
        """,
        (tenor.series_id, tenor.tenor),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO macro_series_dimensions (
            dimension_id, series_id, dimension_key, dimension_value, ordinal,
            metadata_json, available_at, available_precision,
            source_snapshot_id, run_id
        ) VALUES (?, ?, 'tenor', ?, 0, '{}', ?, 'datetime', ?, ?)
        """,
        (
            stable_id("fmp_treasury_dimension", tenor.series_id, tenor.tenor),
            tenor.series_id,
            tenor.tenor,
            captured_at,
            snapshot_id,
            run_id,
        ),
    )


def _stored_value_matches(stored: object, value_text: str | None) -> bool:
    if stored is None:
        return value_text is None
    if value_text is None:
        return False
    try:
        return _normalized_decimal(Decimal(str(stored))) == value_text
    except (InvalidOperation, ValueError):
        raise ConflictError("Stored FMP Treasury value is invalid")


def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    observation: FmpTreasuryCurveObservation,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
    dimensions_json = dumps_strict({"tenor": observation.tenor})
    dimensions_digest = _sha256_text(dimensions_json)
    source_vintage_identity = _source_vintage_identity(observation.curve_date)
    existing = connection.execute(
        """
        SELECT version_id, correction_sequence, value_text, missing_reason,
               available_at, state
        FROM macro_observation_versions
        WHERE series_id=? AND period_start=? AND period_end=?
          AND dimensions_digest=? AND source_vintage_identity=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (
            observation.series_id,
            observation.curve_date,
            observation.curve_date,
            dimensions_digest,
            source_vintage_identity,
        ),
    ).fetchone()
    if existing is not None and (
        _stored_value_matches(existing["value_text"], observation.value_text)
        and existing["missing_reason"] == observation.missing_reason
        and existing["state"] == "active"
    ):
        return str(existing["version_id"]), False
    if existing is not None and str(existing["available_at"]) > captured_at:
        raise _fail("FMP Treasury correction capture precedes stored evidence")
    correction_sequence = (
        1 if existing is None else int(existing["correction_sequence"]) + 1
    )
    supersedes = None if existing is None else str(existing["version_id"])
    version_material = {
        "series_id": observation.series_id,
        "curve_date": observation.curve_date,
        "tenor": observation.tenor,
        "source_vintage_identity": source_vintage_identity,
        "correction_sequence": correction_sequence,
        "value": observation.value_text,
        "missing_reason": observation.missing_reason,
        "available_at": captured_at,
    }
    version_id = stable_id(
        "fmp_treasury_observation_version",
        observation.series_id,
        observation.curve_date,
        str(correction_sequence),
        _sha256_text(dumps_strict(version_material)),
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'percent', 'rate', '1',
                  ?, 'datetime', ?, 'datetime', ?, ?, ?, ?, ?, 'active', 0, '[]')
        """,
        (
            version_id,
            observation.series_id,
            observation.curve_date,
            observation.curve_date,
            dimensions_json,
            dimensions_digest,
            release_id,
            source_vintage_identity,
            correction_sequence,
            observation.value_text,
            observation.missing_reason,
            captured_at,
            captured_at,
            supersedes,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
        ),
    )
    current = connection.execute(
        """
        SELECT current.current_version_id, version.available_at,
               version.correction_sequence
        FROM macro_observations AS current
        JOIN macro_observation_versions AS version
          ON version.version_id=current.current_version_id
        WHERE current.series_id=? AND current.period_start=?
          AND current.period_end=? AND current.dimensions_digest=?
        """,
        (
            observation.series_id,
            observation.curve_date,
            observation.curve_date,
            dimensions_digest,
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
                observation.curve_date,
                observation.curve_date,
                dimensions_digest,
                version_id,
            ),
        )
    elif (captured_at, correction_sequence) >= (
        str(current["available_at"]),
        int(current["correction_sequence"]),
    ):
        connection.execute(
            """
            UPDATE macro_observations
            SET current_version_id=?
            WHERE series_id=? AND period_start=? AND period_end=?
              AND dimensions_digest=?
            """,
            (
                version_id,
                observation.series_id,
                observation.curve_date,
                observation.curve_date,
                dimensions_digest,
            ),
        )
    return version_id, True


def _ensure_curve(
    connection: sqlite3.Connection,
    curve_date: str,
    snapshot_id: str,
    run_id: str,
) -> tuple[str, bool]:
    existing = connection.execute(
        """
        SELECT curve_id
        FROM treasury_yield_curves
        WHERE provider=? AND curve_date=? AND curve_variant=?
        """,
        (PROVIDER, curve_date, CURVE_VARIANT),
    ).fetchone()
    if existing is not None:
        return str(existing["curve_id"]), False
    curve_id = stable_id(
        "fmp_treasury_curve",
        PROVIDER,
        curve_date,
        CURVE_VARIANT,
    )
    connection.execute(
        """
        INSERT INTO treasury_yield_curves (
            curve_id, provider, curve_date, curve_variant,
            source_snapshot_id, run_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (curve_id, PROVIDER, curve_date, CURVE_VARIANT, snapshot_id, run_id),
    )
    return curve_id, True


def _append_or_reuse_curve_version(
    connection: sqlite3.Connection,
    observation: FmpTreasuryCurveObservation,
    curve_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
    existing = connection.execute(
        """
        SELECT curve_version_id, correction_sequence, yield_value,
               missing_reason, available_at, state
        FROM treasury_yield_curve_versions
        WHERE provider=? AND curve_date=? AND curve_variant=? AND tenor=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (PROVIDER, observation.curve_date, CURVE_VARIANT, observation.tenor),
    ).fetchone()
    if existing is not None and (
        _stored_value_matches(existing["yield_value"], observation.value_text)
        and existing["missing_reason"] == observation.missing_reason
        and existing["state"] == "active"
    ):
        return str(existing["curve_version_id"]), False
    if existing is not None and str(existing["available_at"]) > captured_at:
        raise _fail("FMP Treasury correction capture precedes stored evidence")
    correction_sequence = (
        1 if existing is None else int(existing["correction_sequence"]) + 1
    )
    supersedes = None if existing is None else str(existing["curve_version_id"])
    version_id = stable_id(
        "fmp_treasury_curve_version",
        PROVIDER,
        observation.curve_date,
        CURVE_VARIANT,
        observation.tenor,
        str(correction_sequence),
        observation.value_text or observation.missing_reason or "",
        captured_at,
    )
    connection.execute(
        """
        INSERT INTO treasury_yield_curve_versions (
            curve_version_id, curve_id, provider, curve_date, curve_variant,
            tenor, series_id, yield_value, missing_reason, available_at,
            available_precision, captured_at, captured_precision,
            correction_sequence, supersedes_curve_version_id, artifact_id,
            source_snapshot_id, run_id, source_row, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, 'datetime',
                  ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            version_id,
            curve_id,
            PROVIDER,
            observation.curve_date,
            CURVE_VARIANT,
            observation.tenor,
            observation.series_id,
            observation.value_text,
            observation.missing_reason,
            captured_at,
            captured_at,
            correction_sequence,
            supersedes,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
        ),
    )
    return version_id, True


__all__ = (
    "CANONICAL_DATASET_ID",
    "COLLECTOR_ID",
    "CURVE_VARIANT",
    "FmpTreasuryCurveCapture",
    "FmpTreasuryCurveObservation",
    "FmpTreasuryCurvePublishReport",
    "FmpTreasuryCurvePublisher",
    "FmpTreasuryTenor",
    "HANDLER",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "NORMALIZATION_VERSION",
    "OUTPUT_DATASET_IDS",
    "TENOR_MANIFEST",
    "parse_fmp_treasury_curve",
)
