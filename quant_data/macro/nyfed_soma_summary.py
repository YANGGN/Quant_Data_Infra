"""NY Fed aggregate SOMA summary parsing and canonical publication."""

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
from ..ingestion import ArtifactWrite, IngestionCoordinator, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date


COLLECTOR_ID: Final = "nyfed.macro.soma_summary_history"
HANDLER: Final = "macro.nyfed_soma_summary_history"
OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.soma_evidence",
    "fixture.macro.soma_summary",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[1]
NORMALIZATION_VERSION: Final = "nyfed_soma_summary_v2"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 20_000
MAX_WINDOW_DAYS: Final = 10_000
SOURCE_REFERENCE: Final = "nyfed/markets-data-api/soma/summary"
PROVIDER: Final = "nyfed"
MEASURE: Final = "amount"
UNIT: Final = "thousands_usd"
_USD_PER_THOUSAND: Final = Decimal("1000")


@dataclass(frozen=True, slots=True)
class NyFedSomaComponentSpec:
    source_field: str
    category: str
    title: str


COMPONENT_MANIFEST: Final = (
    NyFedSomaComponentSpec("bills", "treasury_bills", "Treasury bills"),
    NyFedSomaComponentSpec(
        "notesbonds", "treasury_notes_bonds", "Treasury notes and bonds"
    ),
    NyFedSomaComponentSpec(
        "tips", "treasury_inflation_protected_securities", "Treasury TIPS"
    ),
    NyFedSomaComponentSpec(
        "tipsInflationCompensation",
        "tips_inflation_compensation",
        "TIPS inflation compensation",
    ),
    NyFedSomaComponentSpec(
        "frn", "treasury_floating_rate_notes", "Treasury floating-rate notes"
    ),
    NyFedSomaComponentSpec("agencies", "federal_agency_debt", "Federal agency debt"),
    NyFedSomaComponentSpec("mbs", "agency_mbs", "Agency mortgage-backed securities"),
    NyFedSomaComponentSpec(
        "cmbs", "agency_cmbs", "Agency commercial mortgage-backed securities"
    ),
    NyFedSomaComponentSpec("total", "total", "Total SOMA securities holdings"),
)
_EXPECTED_ROW_FIELDS: Final = {
    "asOfDate",
    *(item.source_field for item in COMPONENT_MANIFEST),
}


@dataclass(frozen=True, slots=True)
class NyFedSomaComponent:
    category: str
    value_text: str | None
    missing_reason: str | None
    source_row: int


@dataclass(frozen=True, slots=True)
class NyFedSomaRelease:
    as_of_date: str
    semantic_identity: str
    components: tuple[NyFedSomaComponent, ...]


@dataclass(frozen=True, slots=True)
class NyFedSomaCapture:
    response_bytes: bytes
    response_sha256: str
    captured_at: str
    request_start_date: str
    request_end_date: str
    provider_row_count: int
    releases: tuple[NyFedSomaRelease, ...]


@dataclass(frozen=True, slots=True)
class NyFedSomaPublishReport:
    published_releases: int
    unchanged_releases: int
    written_components: int
    first_as_of_date: str
    last_as_of_date: str


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


def _component_value(value: object) -> tuple[str | None, str | None]:
    if value is None or value == "":
        return None, "source_missing"
    if isinstance(value, bool):
        raise _fail("NY Fed SOMA component must be a non-negative finite decimal")
    if isinstance(value, str):
        if value != value.strip():
            raise _fail("NY Fed SOMA component must be a non-negative finite decimal")
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise _fail(
                "NY Fed SOMA component must be a non-negative finite decimal"
            ) from exc
    elif isinstance(value, (int, Decimal)):
        parsed = Decimal(value)
    elif isinstance(value, float):
        parsed = Decimal(str(value))
    else:
        raise _fail("NY Fed SOMA component must be a non-negative finite decimal")
    if not parsed.is_finite() or parsed < 0:
        raise _fail("NY Fed SOMA component must be a non-negative finite decimal")
    # The NY Fed JSON API reports dollars while the canonical SOMA contract
    # stores and exposes thousands of U.S. dollars.
    return _normalized_decimal(parsed / _USD_PER_THOUSAND), None


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("NY Fed SOMA capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("NY Fed SOMA request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def parse_nyfed_soma_summary(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> NyFedSomaCapture:
    """Normalize one complete aggregate-only SOMA summary response offline."""

    if not isinstance(body, bytes) or not body:
        raise _fail("NY Fed SOMA response must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("NY Fed SOMA response exceeds its byte bound")
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(raw, dict) or set(raw) != {"soma"}:
        raise _fail("NY Fed SOMA response must contain only soma summary data")
    soma = raw["soma"]
    if (
        not isinstance(soma, dict)
        or set(soma) != {"summary"}
        or not isinstance(soma["summary"], list)
    ):
        raise _fail("NY Fed SOMA response must contain soma.summary")
    rows = soma["summary"]
    if len(rows) > MAX_RESPONSE_ROWS:
        raise ResourceLimitError("NY Fed SOMA response exceeds its row bound")

    seen_dates: set[str] = set()
    releases: list[NyFedSomaRelease] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _EXPECTED_ROW_FIELDS:
            raise _fail("NY Fed SOMA row is not an aggregate summary")
        raw_date = row["asOfDate"]
        if not isinstance(raw_date, str):
            raise _fail("NY Fed SOMA asOfDate must be YYYY-MM-DD")
        as_of = parse_date(raw_date, pointer="/soma/summary/asOfDate")
        as_of_text = as_of.isoformat()
        if as_of_text in seen_dates:
            raise _fail("NY Fed SOMA asOfDate values must be unique")
        seen_dates.add(as_of_text)
        if as_of < start or as_of > end:
            continue
        components = tuple(
            NyFedSomaComponent(
                category=spec.category,
                value_text=value,
                missing_reason=missing,
                source_row=source_row,
            )
            for source_row, spec in enumerate(COMPONENT_MANIFEST, start=1)
            for value, missing in (_component_value(row[spec.source_field]),)
        )
        if components[-1].value_text is None:
            raise _fail("NY Fed SOMA total must be present")
        semantic_material = {
            "normalization_version": NORMALIZATION_VERSION,
            "provider": PROVIDER,
            "resource": SOURCE_REFERENCE,
            "request_scope": {
                "as_of_date": as_of_text,
                "summary_only": True,
            },
            "normalized_summary_components": [
                {
                    "category": item.category,
                    "measure": MEASURE,
                    "unit": UNIT,
                    "value": item.value_text,
                    "missing_reason": item.missing_reason,
                }
                for item in components
            ],
        }
        releases.append(
            NyFedSomaRelease(
                as_of_date=as_of_text,
                semantic_identity=_sha256_text(dumps_strict(semantic_material)),
                components=components,
            )
        )
    releases.sort(key=lambda item: item.as_of_date)
    if not releases:
        raise _fail("NY Fed SOMA response has no rows in the requested window")
    return NyFedSomaCapture(
        response_bytes=body,
        response_sha256=_sha256_bytes(body),
        captured_at=captured,
        request_start_date=start_text,
        request_end_date=end_text,
        provider_row_count=len(rows),
        releases=tuple(releases),
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
        raise _fail("NY Fed SOMA publisher requires the reviewed registry")
    collectors = [
        item for item in registry.collectors if str(item.get("id")) == COLLECTOR_ID
    ]
    datasets = {item.id: item for item in registry.datasets}
    if len(collectors) != 1:
        raise _fail("NY Fed SOMA registry collector binding is invalid")
    collector = collectors[0]
    if (
        collector.get("handler") != HANDLER
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != []
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail("NY Fed SOMA registry collector binding is invalid")
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or COLLECTOR_ID not in dataset.collector_ids
        ):
            raise _fail("NY Fed SOMA registry dataset binding is invalid")
    return registry


def _capture_binding(capture: object) -> NyFedSomaCapture:
    if not isinstance(capture, NyFedSomaCapture):
        raise _fail("NY Fed SOMA capture binding is invalid")
    reparsed = parse_nyfed_soma_summary(
        capture.response_bytes,
        captured_at=capture.captured_at,
        start_date=capture.request_start_date,
        end_date=capture.request_end_date,
    )
    if reparsed != capture:
        raise _fail("NY Fed SOMA capture binding is invalid")
    return capture


class NyFedSomaPublisher:
    """Publish a pre-parsed weekly SOMA summary batch to the macro store."""

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("NY Fed SOMA publisher paths are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail("NY Fed SOMA publisher target is unavailable") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail("NY Fed SOMA publisher target binding is invalid")
        if not _canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("NY Fed SOMA fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("NY Fed SOMA fixture root is too broad")
        self._registry = _registry_binding(registry)
        self._stores = _store_map(root, store)
        self._coordinator = IngestionCoordinator(
            self._stores,
            code_version=NORMALIZATION_VERSION,
        )

    def publish(self, capture: NyFedSomaCapture) -> NyFedSomaPublishReport:
        prepared = _capture_binding(capture)
        published = 0
        unchanged = 0
        written_components = 0
        for release in prepared.releases:
            outcome, written = self._publish_release(prepared, release)
            published += int(outcome == "published")
            unchanged += int(outcome == "unchanged")
            written_components += written
        return NyFedSomaPublishReport(
            published_releases=published,
            unchanged_releases=unchanged,
            written_components=written_components,
            first_as_of_date=prepared.releases[0].as_of_date,
            last_as_of_date=prepared.releases[-1].as_of_date,
        )

    def _publish_release(
        self,
        capture: NyFedSomaCapture,
        release: NyFedSomaRelease,
    ) -> tuple[str, int]:
        run_id = stable_id("nyfed_soma_run", release.semantic_identity)
        scope = {
            "provider": PROVIDER,
            "resource": SOURCE_REFERENCE,
            "as_of_date": release.as_of_date,
            "summary_only": True,
            "completeness": "complete",
        }
        counts = {"components": 0}

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(
                connection,
                capture,
                release,
                active_run_id,
                scope,
                counts,
            )

        receipt = self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=CANONICAL_DATASET_ID,
            output_dataset_ids=OUTPUT_DATASET_IDS,
            semantic_identity=release.semantic_identity,
            run_id=run_id,
            command=COLLECTOR_ID,
            scope=scope,
            started_at=capture.captured_at,
            completed_at=capture.captured_at,
            fetched_count=len(release.components),
            writer=writer,
        )
        if receipt.outcome == "unchanged":
            return "unchanged", 0
        if receipt.outcome == "succeeded":
            return "published", counts["components"]
        raise ConflictError("NY Fed SOMA publisher returned an invalid outcome")

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        capture: NyFedSomaCapture,
        release: NyFedSomaRelease,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            "nyfed_soma_artifact",
            release.semantic_identity,
            capture.response_sha256,
        )
        snapshot_id = stable_id(
            "nyfed_soma_snapshot",
            CANONICAL_DATASET_ID,
            release.semantic_identity,
        )
        connection.execute(
            """
            INSERT INTO soma_source_artifacts (
                artifact_id, source_name, content_sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision, run_id
            ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, 'datetime', ?)
            """,
            (
                artifact_id,
                PROVIDER,
                capture.response_sha256,
                len(capture.response_bytes),
                dumps_strict(scope),
                capture.captured_at,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO soma_snapshots (
                snapshot_id, semantic_identity, as_of_date, scope_json,
                completeness, row_count, captured_at, captured_precision,
                source_published_at, source_published_precision, run_id
            ) VALUES (?, ?, ?, ?, 'complete', ?, ?, 'datetime', NULL, 'unknown', ?)
            """,
            (
                snapshot_id,
                release.semantic_identity,
                release.as_of_date,
                dumps_strict(scope),
                len(release.components),
                capture.captured_at,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO soma_snapshot_artifacts (
                snapshot_id, artifact_id, artifact_ordinal
            ) VALUES (?, ?, 1)
            """,
            (snapshot_id, artifact_id),
        )
        for component in release.components:
            connection.execute(
                """
                INSERT INTO soma_summary_components (
                    component_id, snapshot_id, as_of_date, category, measure,
                    value_text, missing_reason, unit, available_at,
                    available_precision, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?)
                """,
                (
                    stable_id(
                        "nyfed_soma_component",
                        snapshot_id,
                        component.category,
                        MEASURE,
                    ),
                    snapshot_id,
                    release.as_of_date,
                    component.category,
                    MEASURE,
                    component.value_text,
                    component.missing_reason,
                    UNIT,
                    capture.captured_at,
                    component.source_row,
                ),
            )
            counts["components"] += 1

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
            written_count=len(release.components) + 2,
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CANONICAL_DATASET_ID,
                semantic_identity=release.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(release.components),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(),
        )


__all__ = (
    "CANONICAL_DATASET_ID",
    "COLLECTOR_ID",
    "COMPONENT_MANIFEST",
    "HANDLER",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_ROWS",
    "MAX_WINDOW_DAYS",
    "NyFedSomaCapture",
    "NyFedSomaPublishReport",
    "NyFedSomaPublisher",
    "OUTPUT_DATASET_IDS",
    "parse_nyfed_soma_summary",
)
