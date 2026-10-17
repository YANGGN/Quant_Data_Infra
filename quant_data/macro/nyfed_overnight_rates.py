"""Bounded New York Fed overnight reference-rate parsing and publication.

The provider boundary lives in the operations package. This module accepts one
complete, already-captured response and publishes its headline rates, SOFR
distribution/volume fields, and SOFR index/compounded-average fields through
the existing generic macro lineage tables.
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


COLLECTOR_ID: Final = "nyfed.macro.overnight_rates_history"
HANDLER: Final = "macro.nyfed_overnight_rates_history"
OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[1]
NORMALIZATION_VERSION: Final = "nyfed_overnight_rates_v2"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 20_000
MAX_WINDOW_DAYS: Final = 5_000
SOURCE_REFERENCE: Final = "nyfed/markets-data-api/rates/all/search"
PROVIDER: Final = "nyfed"


@dataclass(frozen=True, slots=True)
class NyFedRateSpec:
    code: str
    slug: str
    title: str
    source_type: str
    source_field: str
    unit: str
    value_representation: str

    @property
    def series_id(self) -> str:
        return f"macro.nyfed.{self.slug}"


RATE_MANIFEST: Final = (
    NyFedRateSpec(
        "EFFR",
        "effr",
        "Effective Federal Funds Rate",
        "EFFR",
        "percentRate",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "OBFR",
        "obfr",
        "Overnight Bank Funding Rate",
        "OBFR",
        "percentRate",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "TGCR",
        "tgcr",
        "Tri-Party General Collateral Rate",
        "TGCR",
        "percentRate",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "BGCR",
        "bgcr",
        "Broad General Collateral Rate",
        "BGCR",
        "percentRate",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR",
        "sofr",
        "Secured Overnight Financing Rate",
        "SOFR",
        "percentRate",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_PERCENTILE_1",
        "sofr_percentile_1",
        "SOFR 1st Percentile",
        "SOFR",
        "percentPercentile1",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_PERCENTILE_25",
        "sofr_percentile_25",
        "SOFR 25th Percentile",
        "SOFR",
        "percentPercentile25",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_PERCENTILE_75",
        "sofr_percentile_75",
        "SOFR 75th Percentile",
        "SOFR",
        "percentPercentile75",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_PERCENTILE_99",
        "sofr_percentile_99",
        "SOFR 99th Percentile",
        "SOFR",
        "percentPercentile99",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_VOLUME",
        "sofr_volume",
        "SOFR Transaction Volume",
        "SOFR",
        "volumeInBillions",
        "billions_usd",
        "amount",
    ),
    NyFedRateSpec(
        "SOFR_INDEX",
        "sofr_index",
        "SOFR Index",
        "SOFRAI",
        "index",
        "index",
        "level",
    ),
    NyFedRateSpec(
        "SOFR_AVERAGE_30D",
        "sofr_average_30d",
        "SOFR 30-Day Compounded Average",
        "SOFRAI",
        "average30day",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_AVERAGE_90D",
        "sofr_average_90d",
        "SOFR 90-Day Compounded Average",
        "SOFRAI",
        "average90day",
        "percent",
        "rate",
    ),
    NyFedRateSpec(
        "SOFR_AVERAGE_180D",
        "sofr_average_180d",
        "SOFR 180-Day Compounded Average",
        "SOFRAI",
        "average180day",
        "percent",
        "rate",
    ),
)
_RATE_BY_CODE: Final = {item.code: item for item in RATE_MANIFEST}
_RATE_BY_SOURCE_TYPE: Final = {
    source_type: tuple(
        item for item in RATE_MANIFEST if item.source_type == source_type
    )
    for source_type in {item.source_type for item in RATE_MANIFEST}
}
_RATE_ORDINAL: Final = {
    item.code: ordinal for ordinal, item in enumerate(RATE_MANIFEST)
}
_HEADLINE_RATE_CODES: Final = frozenset(
    {"EFFR", "OBFR", "TGCR", "BGCR", "SOFR"}
)


@dataclass(frozen=True, slots=True)
class NyFedOvernightRateObservation:
    effective_date: str
    rate_code: str
    series_id: str
    value_text: str | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class NyFedOvernightRatesCapture:
    response_bytes: bytes
    response_sha256: str
    semantic_identity: str
    captured_at: str
    captured_precision: str
    request_start_date: str
    request_end_date: str
    observations: tuple[NyFedOvernightRateObservation, ...]


@dataclass(frozen=True, slots=True)
class NyFedOvernightRatesPublishReport:
    outcome: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_series: int
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


def _rate_value(
    value: object,
    *,
    allow_source_missing: bool = False,
) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_null"
    if allow_source_missing and value in {"NA", "N/A"}:
        return None, "source_missing"
    if isinstance(value, bool):
        raise _fail("NY Fed overnight-rate value must be a finite decimal or null")
    if isinstance(value, str):
        if not value or value != value.strip():
            raise _fail("NY Fed overnight-rate value must be a finite decimal or null")
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise _fail("NY Fed overnight-rate value must be a finite decimal or null") from exc
    elif isinstance(value, (int, Decimal)):
        parsed = Decimal(value)
    else:
        raise _fail("NY Fed overnight-rate value must be a finite decimal or null")
    if not parsed.is_finite():
        raise _fail("NY Fed overnight-rate value must be a finite decimal or null")
    return _normalized_decimal(parsed), None


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("NY Fed capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("NY Fed overnight-rate request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def parse_nyfed_overnight_rates(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> NyFedOvernightRatesCapture:
    """Normalize one complete NY Fed response without opening a store."""

    if not isinstance(body, bytes) or not body:
        raise _fail("NY Fed overnight-rate response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("NY Fed overnight-rate response exceeds its byte bound")
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(raw, dict) or not isinstance(raw.get("refRates"), list):
        raise _fail("NY Fed overnight-rate response must contain refRates")
    rows = raw["refRates"]
    if len(rows) > MAX_RESPONSE_ROWS:
        raise ResourceLimitError("NY Fed overnight-rate response exceeds its row bound")

    normalized: list[tuple[str, str, str | None, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise _fail("Each NY Fed overnight-rate row must be an object")
        raw_code = raw_row.get("type")
        if not isinstance(raw_code, str):
            raise _fail("NY Fed overnight-rate row type must be text")
        specs = _RATE_BY_SOURCE_TYPE.get(raw_code)
        if specs is None:
            continue
        if "effectiveDate" not in raw_row:
            raise _fail("NY Fed overnight-rate row is missing a declared field")
        raw_date = raw_row["effectiveDate"]
        if not isinstance(raw_date, str):
            raise _fail("NY Fed effectiveDate must be YYYY-MM-DD")
        effective_date = parse_date(raw_date, pointer="/refRates/effectiveDate")
        effective_date_text = effective_date.isoformat()
        if effective_date < start or effective_date > end:
            raise _fail("NY Fed overnight-rate row is outside the requested window")
        for spec in specs:
            source_fields = (spec.source_field,)
            if spec.code in _HEADLINE_RATE_CODES:
                source_fields += ("percent",)
            source_field = next(
                (field for field in source_fields if field in raw_row),
                None,
            )
            if source_field is None:
                if spec.code in _HEADLINE_RATE_CODES:
                    raise _fail(
                        "NY Fed overnight-rate row is missing a declared field"
                    )
                continue
            identity = (effective_date_text, spec.code)
            if identity in seen:
                raise _fail("NY Fed overnight-rate date and type pairs must be unique")
            seen.add(identity)
            raw_value = raw_row[source_field]
            try:
                value_text, missing_reason = _rate_value(
                    raw_value,
                    allow_source_missing=spec.code not in _HEADLINE_RATE_CODES,
                )
            except ValidationError as exc:
                raise _fail(
                    "NY Fed "
                    f"{source_field} on {effective_date_text} must be a finite "
                    f"decimal or null; got {raw_value!r}"
                ) from exc
            normalized.append(
                (effective_date_text, spec.code, value_text, missing_reason)
            )

    normalized.sort(key=lambda item: (item[0], _RATE_ORDINAL[item[1]]))
    observations = tuple(
        NyFedOvernightRateObservation(
            effective_date=effective_date,
            rate_code=rate_code,
            series_id=_RATE_BY_CODE[rate_code].series_id,
            value_text=value_text,
            missing_reason=missing_reason,
            source_row=source_row,
        )
        for source_row, (
            effective_date,
            rate_code,
            value_text,
            missing_reason,
        ) in enumerate(normalized, start=1)
    )
    semantic_material = {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": PROVIDER,
        "resource": SOURCE_REFERENCE,
        "request_scope": {"from": start_text, "to": end_text},
        "rate_manifest": [
            {"code": item.code, "series_id": item.series_id}
            for item in RATE_MANIFEST
        ],
        "normalized_headline_rate_batch": [
            {
                "effective_date": item.effective_date,
                "rate_code": item.rate_code,
                "value": item.value_text,
                "missing_reason": item.missing_reason,
            }
            for item in observations
        ],
    }
    return NyFedOvernightRatesCapture(
        response_bytes=body,
        response_sha256=_sha256_bytes(body),
        semantic_identity=_sha256_text(dumps_strict(semantic_material)),
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start_text,
        request_end_date=end_text,
        observations=observations,
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
        raise _fail("NY Fed overnight-rate publisher requires the reviewed registry")
    collectors = [
        item for item in registry.collectors if str(item.get("id")) == COLLECTOR_ID
    ]
    datasets = {item.id: item for item in registry.datasets}
    if len(collectors) != 1:
        raise _fail("NY Fed overnight-rate registry collector binding is invalid")
    collector = collectors[0]
    if (
        collector.get("handler") != HANDLER
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != []
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail("NY Fed overnight-rate registry collector binding is invalid")
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or COLLECTOR_ID not in dataset.collector_ids
        ):
            raise _fail("NY Fed overnight-rate registry dataset binding is invalid")
    return registry


def _capture_binding(capture: object) -> NyFedOvernightRatesCapture:
    if not isinstance(capture, NyFedOvernightRatesCapture):
        raise _fail("NY Fed overnight-rate capture binding is invalid")
    reparsed = parse_nyfed_overnight_rates(
        capture.response_bytes,
        captured_at=capture.captured_at,
        start_date=capture.request_start_date,
        end_date=capture.request_end_date,
    )
    if reparsed != capture:
        raise _fail("NY Fed overnight-rate capture binding is invalid")
    return capture


class NyFedOvernightRatesPublisher:
    """Publish one pre-parsed headline-rate batch to the bound macro store."""

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("NY Fed overnight-rate publisher paths are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail("NY Fed overnight-rate publisher target is unavailable") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail("NY Fed overnight-rate publisher target binding is invalid")
        if not _canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("NY Fed overnight-rate fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("NY Fed overnight-rate fixture root is too broad")
        self._registry = _registry_binding(registry)
        self._root = root
        self._store = store
        self._stores = _store_map(root, store)
        self._coordinator = IngestionCoordinator(
            self._stores,
            code_version=NORMALIZATION_VERSION,
        )

    def publish(
        self, capture: NyFedOvernightRatesCapture
    ) -> NyFedOvernightRatesPublishReport:
        prepared = _capture_binding(capture)
        run_id = stable_id("nyfed_overnight_rates_run", prepared.semantic_identity)
        scope = {
            "provider": PROVIDER,
            "resource": SOURCE_REFERENCE,
            "from": prepared.request_start_date,
            "to": prepared.request_end_date,
            "rate_codes": [item.code for item in RATE_MANIFEST],
            "completeness": "complete",
        }
        counts = {"series": 0, "observation_versions": 0}

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(
                connection, prepared, active_run_id, scope, counts
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
            raise ConflictError(
                "NY Fed overnight-rate publisher returned an invalid outcome"
            )
        return NyFedOvernightRatesPublishReport(
            outcome=outcome,
            semantic_identity=prepared.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_series=counts["series"],
            written_observation_versions=counts["observation_versions"],
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        capture: NyFedOvernightRatesCapture,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            "nyfed_overnight_rates_artifact",
            capture.semantic_identity,
            capture.response_sha256,
        )
        snapshot_id = stable_id(
            "nyfed_overnight_rates_snapshot",
            CANONICAL_DATASET_ID,
            capture.semantic_identity,
        )
        for spec in RATE_MANIFEST:
            counts["series"] += int(_ensure_series(connection, spec, run_id))

        release_ids: dict[tuple[str, str], str] = {}
        for observation in capture.observations:
            key = (observation.series_id, observation.effective_date)
            if key not in release_ids:
                release_ids[key] = _ensure_release(
                    connection,
                    observation,
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
        scope_json = dumps_strict(scope)
        scope_digest = _sha256_text(scope_json)
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', 0)
            """,
            (
                stable_id("nyfed_overnight_rates_scope", snapshot_id, scope_digest),
                snapshot_id,
                scope_json,
                scope_digest,
            ),
        )

        for observation in capture.observations:
            version_id, appended = _append_or_reuse_observation(
                connection,
                observation,
                release_ids[(observation.series_id, observation.effective_date)],
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
            written_count=counts["series"] + counts["observation_versions"],
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
                        "nyfed_overnight_rates_quality",
                        snapshot_id,
                        "supported_headline_rates",
                    ),
                    dataset_id=CANONICAL_DATASET_ID,
                    rule_id="nyfed_overnight_rates.supported_headline_rates",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "observation_count": len(capture.observations),
                        "rate_count": len(
                            {item.rate_code for item in capture.observations}
                        ),
                    },
                ),
            ),
        )


def _series_metadata(spec: NyFedRateSpec) -> tuple[object, ...]:
    return (
        PROVIDER,
        spec.code,
        spec.title,
        "daily",
        spec.unit,
        spec.value_representation,
        "1",
        dumps_strict({}),
        dumps_strict(["latest", "as_of"]),
        "local_capture",
    )


def _ensure_series(
    connection: sqlite3.Connection,
    spec: NyFedRateSpec,
    run_id: str,
) -> bool:
    expected = _series_metadata(spec)
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json,
               supported_modes_json, availability_basis
        FROM macro_series
        WHERE series_id=?
        """,
        (spec.series_id,),
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
            (spec.series_id, *expected, run_id),
        )
        return True
    if tuple(existing) != expected:
        raise _fail("NY Fed rate series conflicts with immutable catalog metadata")
    return False


def _source_vintage_identity(observation: NyFedOvernightRateObservation) -> str:
    return (
        f"nyfed:overnight-rate:{observation.rate_code}:"
        f"{observation.effective_date}"
    )


def _ensure_release(
    connection: sqlite3.Connection,
    observation: NyFedOvernightRateObservation,
    captured_at: str,
    run_id: str,
) -> str:
    source_vintage_identity = _source_vintage_identity(observation)
    expected = (
        observation.effective_date,
        "date",
        observation.effective_date,
        0,
        None,
        "current_state",
    )
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision,
               source_release_order, is_first_release,
               first_release_evidence, release_stage
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (observation.series_id, source_vintage_identity),
    ).fetchone()
    if existing is not None:
        if tuple(existing)[1:] != expected:
            raise _fail("NY Fed rate release conflicts with immutable metadata")
        return str(existing["release_id"])
    release_id = stable_id(
        "nyfed_overnight_rate_release",
        observation.series_id,
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
            observation.series_id,
            source_vintage_identity,
            observation.effective_date,
            observation.effective_date,
            captured_at,
            run_id,
        ),
    )
    return release_id


def _stored_value_matches(stored: object, value_text: str | None) -> bool:
    if stored is None:
        return value_text is None
    if value_text is None:
        return False
    try:
        return _normalized_decimal(Decimal(str(stored))) == value_text
    except (InvalidOperation, ValueError) as exc:
        raise ConflictError("Stored NY Fed overnight-rate value is invalid") from exc


def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    observation: NyFedOvernightRateObservation,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
    spec = _RATE_BY_CODE[observation.rate_code]
    dimensions_json = dumps_strict({})
    dimensions_digest = _sha256_text(dimensions_json)
    source_vintage_identity = _source_vintage_identity(observation)
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
            observation.effective_date,
            observation.effective_date,
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
        raise _fail("NY Fed correction capture precedes stored evidence")
    correction_sequence = (
        1 if existing is None else int(existing["correction_sequence"]) + 1
    )
    supersedes = None if existing is None else str(existing["version_id"])
    version_material = {
        "series_id": observation.series_id,
        "effective_date": observation.effective_date,
        "rate_code": observation.rate_code,
        "source_vintage_identity": source_vintage_identity,
        "correction_sequence": correction_sequence,
        "value": observation.value_text,
        "missing_reason": observation.missing_reason,
        "available_at": captured_at,
    }
    version_id = stable_id(
        "nyfed_overnight_rate_observation_version",
        observation.series_id,
        observation.effective_date,
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1',
                  ?, 'datetime', ?, 'datetime', ?, ?, ?, ?, ?, 'active', 0, '[]')
        """,
        (
            version_id,
            observation.series_id,
            observation.effective_date,
            observation.effective_date,
            dimensions_json,
            dimensions_digest,
            release_id,
            source_vintage_identity,
            correction_sequence,
            observation.value_text,
            observation.missing_reason,
            spec.unit,
            spec.value_representation,
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
            observation.effective_date,
            observation.effective_date,
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
                observation.effective_date,
                observation.effective_date,
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
                observation.effective_date,
                observation.effective_date,
                dimensions_digest,
            ),
        )
    return version_id, True


__all__ = (
    "CANONICAL_DATASET_ID",
    "COLLECTOR_ID",
    "HANDLER",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "MAX_WINDOW_DAYS",
    "NORMALIZATION_VERSION",
    "NyFedOvernightRateObservation",
    "NyFedOvernightRatesCapture",
    "NyFedOvernightRatesPublishReport",
    "NyFedOvernightRatesPublisher",
    "NyFedRateSpec",
    "OUTPUT_DATASET_IDS",
    "PROVIDER",
    "RATE_MANIFEST",
    "SOURCE_REFERENCE",
    "parse_nyfed_overnight_rates",
)
