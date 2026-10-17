"""NY Fed overnight repo-facility parsing and canonical publication."""

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


COLLECTOR_ID: Final = "nyfed.macro.repo_facility_usage_history"
HANDLER: Final = "macro.nyfed_repo_facility_usage_history"
OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[1]
NORMALIZATION_VERSION: Final = "nyfed_repo_facility_usage_v1"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 20_000
MAX_WINDOW_DAYS: Final = 5_000
SOURCE_REFERENCE: Final = "nyfed/markets-data-api/rp/results/search"
PROVIDER: Final = "nyfed"
SRF_START_DATE: Final = date(2021, 7, 29)


@dataclass(frozen=True, slots=True)
class NyFedRepoFacilitySpec:
    code: str
    slug: str
    title: str

    @property
    def series_id(self) -> str:
        return f"macro.nyfed.{self.slug}"


FACILITY_MANIFEST: Final = (
    NyFedRepoFacilitySpec(
        "ON_RRP",
        "on_rrp_accepted_amount",
        "Overnight Reverse Repo Facility Accepted Amount",
    ),
    NyFedRepoFacilitySpec(
        "SRF",
        "srf_accepted_amount",
        "Standing Repo Facility Accepted Amount",
    ),
)
_FACILITY_BY_CODE: Final = {item.code: item for item in FACILITY_MANIFEST}
_FACILITY_ORDINAL: Final = {
    item.code: ordinal for ordinal, item in enumerate(FACILITY_MANIFEST)
}


@dataclass(frozen=True, slots=True)
class NyFedRepoFacilityObservation:
    effective_date: str
    facility_code: str
    series_id: str
    accepted_usd_text: str
    operation_ids: tuple[str, ...]
    source_row: int


@dataclass(frozen=True, slots=True)
class NyFedRepoFacilitiesCapture:
    response_bytes: bytes
    response_sha256: str
    semantic_identity: str
    captured_at: str
    request_start_date: str
    request_end_date: str
    provider_operation_count: int
    selected_operation_count: int
    observations: tuple[NyFedRepoFacilityObservation, ...]


@dataclass(frozen=True, slots=True)
class NyFedRepoFacilitiesPublishReport:
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


def _accepted_amount(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise _fail("NY Fed totalAmtAccepted must be a non-negative finite decimal")
    if isinstance(value, str):
        if not value or value != value.strip():
            raise _fail("NY Fed totalAmtAccepted must be a non-negative finite decimal")
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise _fail(
                "NY Fed totalAmtAccepted must be a non-negative finite decimal"
            ) from exc
    elif isinstance(value, (int, Decimal)):
        parsed = Decimal(value)
    elif isinstance(value, float):
        parsed = Decimal(str(value))
    else:
        raise _fail("NY Fed totalAmtAccepted must be a non-negative finite decimal")
    if not parsed.is_finite() or parsed < 0:
        raise _fail("NY Fed totalAmtAccepted must be a non-negative finite decimal")
    ensure_number_within_limits(parsed)
    return parsed


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
        raise _fail("NY Fed repo-facility request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def _exercise(note: object) -> bool:
    if note is None:
        return False
    if not isinstance(note, str):
        raise _fail("NY Fed repo operation note must be text or null")
    lowered = note.casefold()
    return "small value" in lowered or "operational readiness" in lowered


def parse_nyfed_repo_facilities(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> NyFedRepoFacilitiesCapture:
    """Normalize one complete repo-results response without opening a store."""

    if not isinstance(body, bytes) or not body:
        raise _fail("NY Fed repo-facility response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("NY Fed repo-facility response exceeds its byte bound")
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("repo"), dict)
        or not isinstance(raw["repo"].get("operations"), list)
    ):
        raise _fail("NY Fed repo-facility response must contain repo.operations")
    rows = raw["repo"]["operations"]
    if len(rows) > MAX_RESPONSE_ROWS:
        raise ResourceLimitError("NY Fed repo-facility response exceeds its row bound")

    grouped: dict[tuple[str, str], tuple[Decimal, list[str]]] = {}
    seen_operation_ids: set[str] = set()
    selected_operation_count = 0
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise _fail("Each NY Fed repo operation must be an object")
        operation_type = raw_row.get("operationType")
        if operation_type not in {"Repo", "Reverse Repo"}:
            continue
        if raw_row.get("auctionStatus") != "Results":
            continue
        for field in ("operationId", "operationDate", "totalAmtAccepted"):
            if field not in raw_row:
                raise _fail("NY Fed repo operation is missing a declared field")
        operation_id = raw_row["operationId"]
        raw_date = raw_row["operationDate"]
        if (
            not isinstance(operation_id, str)
            or not operation_id
            or operation_id != operation_id.strip()
        ):
            raise _fail("NY Fed repo operationId must be non-empty text")
        if operation_id in seen_operation_ids:
            raise _fail("NY Fed repo operationId values must be unique")
        seen_operation_ids.add(operation_id)
        if not isinstance(raw_date, str):
            raise _fail("NY Fed repo operationDate must be YYYY-MM-DD")
        operation_date = parse_date(raw_date, pointer="/repo/operations/operationDate")
        if operation_date < start or operation_date > end:
            raise _fail("NY Fed repo operation is outside the requested window")
        if _exercise(raw_row.get("note")):
            continue
        if operation_type == "Reverse Repo":
            facility_code = "ON_RRP"
        elif operation_date >= SRF_START_DATE:
            facility_code = "SRF"
        else:
            continue
        amount = _accepted_amount(raw_row["totalAmtAccepted"])
        key = (operation_date.isoformat(), facility_code)
        current_amount, operation_ids = grouped.get(key, (Decimal(0), []))
        grouped[key] = (current_amount + amount, [*operation_ids, operation_id])
        selected_operation_count += 1

    observations = tuple(
        NyFedRepoFacilityObservation(
            effective_date=effective_date,
            facility_code=facility_code,
            series_id=_FACILITY_BY_CODE[facility_code].series_id,
            accepted_usd_text=_normalized_decimal(amount),
            operation_ids=tuple(sorted(operation_ids)),
            source_row=source_row,
        )
        for source_row, (
            (effective_date, facility_code),
            (amount, operation_ids),
        ) in enumerate(
            sorted(
                grouped.items(),
                key=lambda item: (
                    item[0][0],
                    _FACILITY_ORDINAL[item[0][1]],
                ),
            ),
            start=1,
        )
    )
    semantic_material = {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": PROVIDER,
        "resource": SOURCE_REFERENCE,
        "request_scope": {
            "from": start_text,
            "to": end_text,
            "term": "overnight",
        },
        "facility_manifest": [
            {"code": item.code, "series_id": item.series_id}
            for item in FACILITY_MANIFEST
        ],
        "srf_start_date": SRF_START_DATE.isoformat(),
        "small_value_exercises": "excluded_when_source_note_identifies_them",
        "normalized_daily_facility_batch": [
            {
                "effective_date": item.effective_date,
                "facility_code": item.facility_code,
                "accepted_usd": item.accepted_usd_text,
                "operation_ids": list(item.operation_ids),
            }
            for item in observations
        ],
    }
    return NyFedRepoFacilitiesCapture(
        response_bytes=body,
        response_sha256=_sha256_bytes(body),
        semantic_identity=_sha256_text(dumps_strict(semantic_material)),
        captured_at=captured,
        request_start_date=start_text,
        request_end_date=end_text,
        provider_operation_count=len(rows),
        selected_operation_count=selected_operation_count,
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
        raise _fail("NY Fed repo-facility publisher requires the reviewed registry")
    collectors = [
        item for item in registry.collectors if str(item.get("id")) == COLLECTOR_ID
    ]
    datasets = {item.id: item for item in registry.datasets}
    if len(collectors) != 1:
        raise _fail("NY Fed repo-facility registry collector binding is invalid")
    collector = collectors[0]
    if (
        collector.get("handler") != HANDLER
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != []
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail("NY Fed repo-facility registry collector binding is invalid")
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or COLLECTOR_ID not in dataset.collector_ids
        ):
            raise _fail("NY Fed repo-facility registry dataset binding is invalid")
    return registry


def _capture_binding(capture: object) -> NyFedRepoFacilitiesCapture:
    if not isinstance(capture, NyFedRepoFacilitiesCapture):
        raise _fail("NY Fed repo-facility capture binding is invalid")
    reparsed = parse_nyfed_repo_facilities(
        capture.response_bytes,
        captured_at=capture.captured_at,
        start_date=capture.request_start_date,
        end_date=capture.request_end_date,
    )
    if reparsed != capture:
        raise _fail("NY Fed repo-facility capture binding is invalid")
    return capture


class NyFedRepoFacilitiesPublisher:
    """Publish one pre-parsed facility batch to the bound macro store."""

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("NY Fed repo-facility publisher paths are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail("NY Fed repo-facility publisher target is unavailable") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail("NY Fed repo-facility publisher target binding is invalid")
        if not _canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("NY Fed repo-facility fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("NY Fed repo-facility fixture root is too broad")
        self._registry = _registry_binding(registry)
        self._stores = _store_map(root, store)
        self._coordinator = IngestionCoordinator(
            self._stores,
            code_version=NORMALIZATION_VERSION,
        )

    def publish(
        self, capture: NyFedRepoFacilitiesCapture
    ) -> NyFedRepoFacilitiesPublishReport:
        prepared = _capture_binding(capture)
        run_id = stable_id("nyfed_repo_facilities_run", prepared.semantic_identity)
        scope = {
            "provider": PROVIDER,
            "resource": SOURCE_REFERENCE,
            "from": prepared.request_start_date,
            "to": prepared.request_end_date,
            "term": "overnight",
            "facility_codes": [item.code for item in FACILITY_MANIFEST],
            "small_value_exercises": "excluded_when_source_note_identifies_them",
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
            fetched_count=prepared.provider_operation_count,
            writer=writer,
        )
        if receipt.outcome == "unchanged":
            outcome = "unchanged"
        elif receipt.outcome == "succeeded":
            outcome = "published"
        else:
            raise ConflictError("NY Fed repo-facility publisher returned an invalid outcome")
        return NyFedRepoFacilitiesPublishReport(
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
        capture: NyFedRepoFacilitiesCapture,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            "nyfed_repo_facilities_artifact",
            capture.semantic_identity,
            capture.response_sha256,
        )
        snapshot_id = stable_id(
            "nyfed_repo_facilities_snapshot",
            CANONICAL_DATASET_ID,
            capture.semantic_identity,
        )
        for spec in FACILITY_MANIFEST:
            counts["series"] += int(_ensure_series(connection, spec, run_id))

        release_ids = {
            (item.series_id, item.effective_date): _ensure_release(
                connection, item, capture.captured_at, run_id
            )
            for item in capture.observations
        }
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
                stable_id("nyfed_repo_facilities_scope", snapshot_id, scope_digest),
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
                        "nyfed_repo_facilities_quality",
                        snapshot_id,
                        "supported_facilities",
                    ),
                    dataset_id=CANONICAL_DATASET_ID,
                    rule_id="nyfed_repo_facilities.supported_facilities",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "provider_operation_count": capture.provider_operation_count,
                        "selected_operation_count": capture.selected_operation_count,
                        "observation_count": len(capture.observations),
                    },
                ),
            ),
        )


def _series_metadata(spec: NyFedRepoFacilitySpec) -> tuple[object, ...]:
    return (
        PROVIDER,
        spec.code,
        spec.title,
        "daily",
        "usd",
        "amount",
        "1",
        dumps_strict({}),
        dumps_strict(["latest", "as_of"]),
        "local_capture",
    )


def _ensure_series(
    connection: sqlite3.Connection,
    spec: NyFedRepoFacilitySpec,
    run_id: str,
) -> bool:
    expected = _series_metadata(spec)
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json,
               supported_modes_json, availability_basis
        FROM macro_series WHERE series_id=?
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
        raise _fail("NY Fed facility series conflicts with immutable catalog metadata")
    return False


def _source_vintage_identity(observation: NyFedRepoFacilityObservation) -> str:
    return (
        f"nyfed:repo-facility:{observation.facility_code}:"
        f"{observation.effective_date}"
    )


def _ensure_release(
    connection: sqlite3.Connection,
    observation: NyFedRepoFacilityObservation,
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
            raise _fail("NY Fed facility release conflicts with immutable metadata")
        return str(existing["release_id"])
    release_id = stable_id(
        "nyfed_repo_facility_release",
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


def _stored_value_matches(stored: object, value_text: str) -> bool:
    if stored is None:
        return False
    try:
        return _normalized_decimal(Decimal(str(stored))) == value_text
    except (InvalidOperation, ValueError) as exc:
        raise ConflictError("Stored NY Fed facility amount is invalid") from exc


def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    observation: NyFedRepoFacilityObservation,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
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
        _stored_value_matches(
            existing["value_text"],
            observation.accepted_usd_text,
        )
        and existing["missing_reason"] is None
        and existing["state"] == "active"
    ):
        return str(existing["version_id"]), False
    if existing is not None and str(existing["available_at"]) > captured_at:
        raise _fail("NY Fed facility correction capture precedes stored evidence")
    correction_sequence = (
        1 if existing is None else int(existing["correction_sequence"]) + 1
    )
    supersedes = None if existing is None else str(existing["version_id"])
    version_material = {
        "series_id": observation.series_id,
        "effective_date": observation.effective_date,
        "facility_code": observation.facility_code,
        "source_vintage_identity": source_vintage_identity,
        "correction_sequence": correction_sequence,
        "accepted_usd": observation.accepted_usd_text,
        "available_at": captured_at,
    }
    version_id = stable_id(
        "nyfed_repo_facility_observation_version",
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'usd', 'amount', '1',
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
            observation.accepted_usd_text,
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
    "FACILITY_MANIFEST",
    "HANDLER",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "MAX_WINDOW_DAYS",
    "NORMALIZATION_VERSION",
    "NyFedRepoFacilitiesCapture",
    "NyFedRepoFacilitiesPublishReport",
    "NyFedRepoFacilitiesPublisher",
    "NyFedRepoFacilityObservation",
    "NyFedRepoFacilitySpec",
    "OUTPUT_DATASET_IDS",
    "PROVIDER",
    "SOURCE_REFERENCE",
    "SRF_START_DATE",
    "parse_nyfed_repo_facilities",
)
