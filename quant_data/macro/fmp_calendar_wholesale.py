"""Compact raw evidence for the FMP U.S. economic calendar.

Future material batches retain one immutable receipt, only new or changed raw
calendar-event versions, and a single replaceable response cache for local
normalization replay.  The older wholesale-capture tables remain readable for
historic replay only.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, QualityWrite, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection, stable_id


LEGACY_WHOLESALE_DATASET_ID: Final = "macro.fmp.economic_calendar_evidence"
WHOLESALE_DATASET_ID: Final = "macro.fmp.economic_calendar_incremental_evidence"
WHOLESALE_EVENT_DATASET_ID: Final = "macro.fmp.economic_calendar_incremental_events"
WHOLESALE_COLLECTOR_ID: Final = "fmp.macro.us_economic_calendar_wholesale"
NORMALIZATION_VERSION: Final = "fmp_us_calendar_wholesale_evidence_v1"
PERSISTENCE_CODE_VERSION: Final = "fmp_us_calendar_incremental_events_v2"
SOURCE_RESOURCE: Final = "fmp_stable_economic_calendar"
SOURCE_REFERENCE: Final = "fmp/economic-calendar/us-wholesale-receipt.json"
_FEED_ID: Final = "fmp_us"
_INCREMENTAL_RELATIONS: Final = frozenset(
    {
        "fmp_economic_calendar_fetch_receipts",
        "fmp_economic_calendar_raw_events",
        "fmp_economic_calendar_raw_event_versions",
        "fmp_economic_calendar_latest_response_cache",
    }
)
_INCREMENTAL_MIGRATION: Final = (
    "macro:0018_fmp_calendar_incremental_events",
    "macro",
    18,
    "quant_data/migrations/macro/0018_fmp_calendar_incremental_events.sql",
    "078cfd62e7cd7a314e6b828b19414023f60fa81c760c89a31b3398f1b7e41a3e",
    "fixture_validated",
)
MAX_RESPONSE_BYTES: Final = 1_048_576
MAX_SOURCE_ROWS: Final = 2_000


@dataclass(frozen=True, slots=True)
class FmpWholesaleCalendarRow:
    """One canonicalized provider object, retained in its original order."""

    source_row: int
    row_sha256: str
    raw_row_json: str
    event_at: str
    country: str
    event_name: str
    currency: str | None
    unit: str | None
    previous_json: str | None
    estimate_json: str | None
    actual_json: str | None
    change_json: str | None
    impact_json: str | None
    change_percentage_json: str | None


@dataclass(frozen=True, slots=True)
class FmpWholesaleCalendarCapture:
    """A parsed response suitable for raw evidence publication or replay."""

    response_bytes: bytes
    response_sha256: str
    semantic_identity: str
    captured_at: str
    captured_precision: str
    request_start_date: str
    request_end_date: str
    rows: tuple[FmpWholesaleCalendarRow, ...]


@dataclass(frozen=True, slots=True)
class FmpWholesalePublishReport:
    outcome: str
    semantic_identity: str
    capture_id: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_captures: int
    written_rows: int


@dataclass(frozen=True, slots=True)
class _EventRow:
    event_id: str
    currency_key: str
    row: FmpWholesaleCalendarRow


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _utc_datetime(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise _fail(f"FMP wholesale calendar {label} is invalid")
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
    except ValueError as exc:
        raise _fail(f"FMP wholesale calendar {label} is invalid") from exc
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _validated_window(start_date: object, end_date: object) -> tuple[str, str]:
    if (
        not isinstance(start_date, str)
        or not isinstance(end_date, str)
        or len(start_date) != 10
        or len(end_date) != 10
    ):
        raise _fail("FMP wholesale calendar request window is invalid")
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise _fail("FMP wholesale calendar request window is invalid") from exc
    if start > end:
        raise _fail("FMP wholesale calendar request window is invalid")
    return start_date, end_date


def _required_text(item: dict[str, object], key: str, *, maximum: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _fail(f"FMP wholesale calendar row {key} is invalid")
    return value


def _optional_text(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _optional_json(item: dict[str, object], key: str) -> str | None:
    if key not in item:
        return None
    return dumps_strict(item[key])


def _row_from_object(
    item: object,
    *,
    source_row: int,
) -> FmpWholesaleCalendarRow:
    if not isinstance(item, dict):
        raise _fail("FMP wholesale calendar row must be an object")
    event_at = _required_text(item, "date", maximum=128)
    country = _required_text(item, "country", maximum=64)
    event_name = _required_text(item, "event", maximum=512)
    raw_row_json = dumps_strict(item, max_bytes=MAX_RESPONSE_BYTES)
    return FmpWholesaleCalendarRow(
        source_row=source_row,
        row_sha256=hashlib.sha256(raw_row_json.encode("utf-8")).hexdigest(),
        raw_row_json=raw_row_json,
        event_at=event_at,
        country=country,
        event_name=event_name,
        currency=_optional_text(item, "currency"),
        unit=_optional_text(item, "unit"),
        previous_json=_optional_json(item, "previous"),
        estimate_json=_optional_json(item, "estimate"),
        actual_json=_optional_json(item, "actual"),
        change_json=_optional_json(item, "change"),
        impact_json=_optional_json(item, "impact"),
        change_percentage_json=_optional_json(item, "changePercentage"),
    )


def parse_fmp_us_calendar_wholesale(
    body: bytes,
    *,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> FmpWholesaleCalendarCapture:
    """Validate and retain a bounded FMP response without target filtering."""

    if not isinstance(body, bytes):
        raise _fail("FMP wholesale calendar response body is invalid")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("FMP wholesale calendar response exceeds its byte bound")
    start, end = _validated_window(start_date, end_date)
    captured = _utc_datetime(captured_at, label="capture time")
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(raw, list):
        raise _fail("FMP wholesale calendar response must be an array")
    if len(raw) > MAX_SOURCE_ROWS:
        raise ResourceLimitError("FMP wholesale calendar response exceeds its row bound")
    rows = tuple(
        _row_from_object(item, source_row=source_row)
        for source_row, item in enumerate(raw, start=1)
    )
    semantic_material = {
        "normalization_version": NORMALIZATION_VERSION,
        "request_scope": {"country": "US", "from": start, "to": end},
        "normalized_complete_batch": sorted(row.row_sha256 for row in rows),
    }
    return FmpWholesaleCalendarCapture(
        response_bytes=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        semantic_identity=hashlib.sha256(
            dumps_strict(semantic_material).encode("utf-8")
        ).hexdigest(),
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start,
        request_end_date=end,
        rows=rows,
    )


def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _currency_key(row: FmpWholesaleCalendarRow) -> str:
    value = dumps_strict(row.currency)
    if len(value) > 128:
        raise _fail("FMP wholesale calendar event currency key is invalid")
    return value


def _event_rows(capture: FmpWholesaleCalendarCapture) -> tuple[_EventRow, ...]:
    unique: dict[str, _EventRow] = {}
    for row in capture.rows:
        currency_key = _currency_key(row)
        event_id = stable_id(
            "fmp_wholesale_calendar_raw_event",
            "fmp",
            row.country,
            row.event_at,
            row.event_name,
            currency_key,
        )
        existing = unique.get(event_id)
        if existing is None:
            unique[event_id] = _EventRow(event_id, currency_key, row)
        elif existing.row.row_sha256 != row.row_sha256:
            raise ConflictError(
                "FMP wholesale calendar response contains conflicting event identities"
            )
    return tuple(unique.values())


def _receipt_manifest(
    capture: FmpWholesaleCalendarCapture,
    *,
    changed_event_count: int,
) -> tuple[bytes, str]:
    payload = dumps_strict(
        {
            "artifact_kind": "fmp_us_calendar_receipt_manifest",
            "changed_event_count": changed_event_count,
            "normalization_version": NORMALIZATION_VERSION,
            "persistence_version": PERSISTENCE_CODE_VERSION,
            "request_scope": {
                "country": "US",
                "from": capture.request_start_date,
                "to": capture.request_end_date,
            },
            "response_sha256": capture.response_sha256,
            "row_count": len(capture.rows),
            "source_resource": SOURCE_RESOURCE,
            "source_semantic_identity": capture.semantic_identity,
        }
    ).encode("utf-8")
    if not 1 <= len(payload) <= 8_192:
        raise ConflictError("FMP wholesale calendar receipt manifest is invalid")
    return payload, hashlib.sha256(payload).hexdigest()


def _transition_identity(
    capture: FmpWholesaleCalendarCapture,
    *,
    predecessor_receipt_id: str | None,
) -> str:
    return hashlib.sha256(
        dumps_strict(
            {
                "persistence_version": PERSISTENCE_CODE_VERSION,
                "predecessor_receipt_id": predecessor_receipt_id,
                "source_semantic_identity": capture.semantic_identity,
            }
        ).encode("utf-8")
    ).hexdigest()


def _registry_binding(registry: object) -> None:
    if not isinstance(registry, Registry):
        raise _fail("FMP wholesale calendar publisher requires the reviewed registry")
    datasets = {item.id: item for item in registry.datasets}
    collectors = {str(item["id"]): item for item in registry.collectors}
    legacy = datasets.get(LEGACY_WHOLESALE_DATASET_ID)
    evidence = datasets.get(WHOLESALE_DATASET_ID)
    events = datasets.get(WHOLESALE_EVENT_DATASET_ID)
    collector = collectors.get(WHOLESALE_COLLECTOR_ID)
    if (
        legacy is None
        or evidence is None
        or events is None
        or collector is None
        or legacy.store != "macro"
        or legacy.layer != "evidence"
        or set(legacy.relations)
        != {"fmp_economic_calendar_captures", "fmp_economic_calendar_rows"}
        or tuple(legacy.collector_ids) != ()
        or evidence.store != "macro"
        or evidence.layer != "evidence"
        or set(evidence.relations)
        != {
            "fmp_economic_calendar_fetch_receipts",
            "fmp_economic_calendar_latest_response_cache",
        }
        or tuple(evidence.collector_ids) != (WHOLESALE_COLLECTOR_ID,)
        or events.store != "macro"
        or events.layer != "canonical"
        or set(events.relations)
        != {
            "fmp_economic_calendar_raw_events",
            "fmp_economic_calendar_raw_event_versions",
        }
        or tuple(events.collector_ids) != (WHOLESALE_COLLECTOR_ID,)
        or collector.get("output_datasets")
        != [WHOLESALE_DATASET_ID, WHOLESALE_EVENT_DATASET_ID]
    ):
        raise _fail("FMP wholesale calendar registry binding is invalid")


def _legacy_rows(rows: tuple[sqlite3.Row, ...]) -> tuple[FmpWholesaleCalendarRow, ...]:
    return tuple(
        FmpWholesaleCalendarRow(
            source_row=int(row["source_row"]),
            row_sha256=str(row["row_sha256"]),
            raw_row_json=str(row["raw_row_json"]),
            event_at=str(row["event_at"]),
            country=str(row["country"]),
            event_name=str(row["event_name"]),
            currency=(None if row["currency"] is None else str(row["currency"])),
            unit=None if row["unit"] is None else str(row["unit"]),
            previous_json=(
                None if row["previous_json"] is None else str(row["previous_json"])
            ),
            estimate_json=(
                None if row["estimate_json"] is None else str(row["estimate_json"])
            ),
            actual_json=(
                None if row["actual_json"] is None else str(row["actual_json"])
            ),
            change_json=(
                None if row["change_json"] is None else str(row["change_json"])
            ),
            impact_json=(
                None if row["impact_json"] is None else str(row["impact_json"])
            ),
            change_percentage_json=(
                None
                if row["change_percentage_json"] is None
                else str(row["change_percentage_json"])
            ),
        )
        for row in rows
    )


class FmpWholesaleCalendarPublisher:
    """Publish compact FMP calendar evidence to an explicit macro store."""

    _canonical_root = Path(__file__).resolve().parents[2]

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
    ) -> None:
        self._configure(
            macro_store=macro_store,
            project_root=project_root,
            registry=registry,
            canonical=False,
        )

    @classmethod
    def for_canonical(cls, *, registry: object) -> "FmpWholesaleCalendarPublisher":
        instance = object.__new__(cls)
        root = cls._canonical_root.resolve(strict=True)
        instance._configure(
            macro_store=root / "data" / "macro.sqlite",
            project_root=root,
            registry=registry,
            canonical=True,
        )
        return instance

    def _configure(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        canonical: bool,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("FMP wholesale calendar publisher paths are invalid")
        root = project_root.resolve(strict=True)
        store = macro_store.resolve(strict=True)
        if not root.is_dir() or not store.is_file() or store.is_symlink():
            raise _fail("FMP wholesale calendar publisher target is unavailable")
        try:
            store.relative_to(root)
        except ValueError as exc:
            raise _fail(
                "FMP wholesale calendar publisher target is outside the project"
            ) from exc
        if canonical:
            if (
                root != self._canonical_root.resolve(strict=True)
                or store != root / "data" / "macro.sqlite"
            ):
                raise _fail("FMP wholesale calendar canonical target binding is invalid")
        else:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail(
                    "FMP wholesale calendar fixture root must be temporary"
                ) from exc
            if root == temporary:
                raise _fail("FMP wholesale calendar fixture root is too broad")
        _registry_binding(registry)
        self._root = root
        self._store = store
        self._registry = registry
        self._stores = _store_map(root, store)
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            relations = {
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_schema
                    WHERE type='table'
                      AND name IN (?, ?, ?, ?)
                    """,
                    tuple(sorted(_INCREMENTAL_RELATIONS)),
                ).fetchall()
            }
            migration = connection.execute(
                """
                SELECT migration_id, store_role, ordinal, resource, sha256,
                       reconstruction_state
                FROM schema_migrations
                WHERE migration_id=?
                """,
                (_INCREMENTAL_MIGRATION[0],),
            ).fetchone()
        migration_contract = (
            None
            if migration is None
            else tuple(
                migration[field]
                for field in (
                    "migration_id",
                    "store_role",
                    "ordinal",
                    "resource",
                    "sha256",
                    "reconstruction_state",
                )
            )
        )
        if (
            relations != _INCREMENTAL_RELATIONS
            or migration_contract != _INCREMENTAL_MIGRATION
        ):
            raise ConflictError(
                "FMP wholesale calendar migration 0018 is not applied"
            )

    def _current_cache_identity(self) -> tuple[str, str, str] | None:
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            row = connection.execute(
                """
                SELECT cache.semantic_identity, cache.source_semantic_identity,
                       cache.receipt_id
                FROM fmp_economic_calendar_latest_response_cache AS cache
                JOIN fmp_economic_calendar_fetch_receipts AS receipt
                  ON receipt.receipt_id = cache.receipt_id
                JOIN ingestion_runs AS run ON run.run_id = receipt.run_id
                WHERE cache.feed_id=?
                  AND run.dataset_id=?
                  AND run.command=?
                  AND run.status='succeeded'
                """,
                (_FEED_ID, WHOLESALE_DATASET_ID, WHOLESALE_COLLECTOR_ID),
            ).fetchone()
        if row is None:
            return None
        return (
            str(row["semantic_identity"]),
            str(row["source_semantic_identity"]),
            str(row["receipt_id"]),
        )

    def _publication_identity(
        self,
        capture: FmpWholesaleCalendarCapture,
    ) -> str:
        current = self._current_cache_identity()
        if current is not None and current[1] == capture.semantic_identity:
            return current[0]
        return _transition_identity(
            capture,
            predecessor_receipt_id=None if current is None else current[2],
        )

    def completed_windows(self) -> tuple[tuple[str, str], ...]:
        """Return windows with a successful legacy or compact receipt."""

        with read_connection(self._stores, StoreRole.MACRO) as connection:
            rows = connection.execute(
                """
                SELECT request_start_date, request_end_date
                FROM (
                    SELECT capture.request_start_date, capture.request_end_date
                    FROM fmp_economic_calendar_captures AS capture
                    JOIN ingestion_runs AS run ON run.run_id = capture.run_id
                    WHERE run.dataset_id=? AND run.command=? AND run.status='succeeded'
                    UNION
                    SELECT receipt.request_start_date, receipt.request_end_date
                    FROM fmp_economic_calendar_fetch_receipts AS receipt
                    JOIN ingestion_runs AS run ON run.run_id = receipt.run_id
                    WHERE run.dataset_id=? AND run.command=? AND run.status='succeeded'
                )
                ORDER BY request_start_date, request_end_date
                """,
                (
                    LEGACY_WHOLESALE_DATASET_ID,
                    WHOLESALE_COLLECTOR_ID,
                    WHOLESALE_DATASET_ID,
                    WHOLESALE_COLLECTOR_ID,
                ),
            ).fetchall()
        result: list[tuple[str, str]] = []
        for row in rows:
            start, end = _validated_window(
                str(row["request_start_date"]), str(row["request_end_date"])
            )
            result.append((start, end))
        return tuple(result)

    def _load_cached_window(
        self,
        start: str,
        end: str,
    ) -> FmpWholesaleCalendarCapture | None:
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            cached = connection.execute(
                """
                SELECT cache.response_bytes, cache.response_sha256,
                       cache.semantic_identity, cache.source_semantic_identity,
                       cache.captured_at, cache.captured_precision,
                       cache.request_start_date, cache.request_end_date,
                       cache.normalization_version, cache.persistence_version,
                       cache.row_count, cache.receipt_id,
                       receipt.response_sha256 AS receipt_response_sha256,
                       receipt.semantic_identity AS receipt_semantic_identity,
                       receipt.source_semantic_identity AS receipt_source_semantic_identity,
                       receipt.captured_at AS receipt_captured_at,
                       receipt.row_count AS receipt_row_count
                FROM fmp_economic_calendar_latest_response_cache AS cache
                JOIN fmp_economic_calendar_fetch_receipts AS receipt
                  ON receipt.receipt_id = cache.receipt_id
                JOIN ingestion_runs AS run ON run.run_id = receipt.run_id
                WHERE cache.feed_id=?
                  AND cache.request_country='US'
                  AND cache.request_start_date=?
                  AND cache.request_end_date=?
                  AND run.dataset_id=?
                  AND run.command=?
                  AND run.status='succeeded'
                """,
                (
                    _FEED_ID,
                    start,
                    end,
                    WHOLESALE_DATASET_ID,
                    WHOLESALE_COLLECTOR_ID,
                ),
            ).fetchone()
        if cached is None:
            return None
        expected = parse_fmp_us_calendar_wholesale(
            bytes(cached["response_bytes"]),
            captured_at=str(cached["captured_at"]),
            start_date=str(cached["request_start_date"]),
            end_date=str(cached["request_end_date"]),
        )
        if (
            str(cached["response_sha256"]) != expected.response_sha256
            or str(cached["source_semantic_identity"]) != expected.semantic_identity
            or str(cached["captured_precision"]) != expected.captured_precision
            or str(cached["normalization_version"]) != NORMALIZATION_VERSION
            or str(cached["persistence_version"]) != PERSISTENCE_CODE_VERSION
            or int(cached["row_count"]) != len(expected.rows)
            or str(cached["receipt_response_sha256"]) != expected.response_sha256
            or str(cached["receipt_semantic_identity"])
            != str(cached["semantic_identity"])
            or str(cached["receipt_source_semantic_identity"])
            != expected.semantic_identity
            or str(cached["receipt_captured_at"]) != expected.captured_at
            or int(cached["receipt_row_count"]) != len(expected.rows)
        ):
            raise ConflictError("FMP wholesale calendar latest cache drifted")
        return expected

    def _load_legacy_window(
        self,
        start: str,
        end: str,
    ) -> FmpWholesaleCalendarCapture | None:
        with read_connection(self._stores, StoreRole.MACRO) as connection:
            capture = connection.execute(
                """
                SELECT capture.capture_id, capture.response_bytes,
                       capture.response_sha256, capture.semantic_identity,
                       capture.captured_at, capture.captured_precision,
                       capture.request_start_date, capture.request_end_date,
                       capture.row_count, capture.normalization_version
                FROM fmp_economic_calendar_captures AS capture
                JOIN ingestion_runs AS run ON run.run_id = capture.run_id
                WHERE capture.request_country='US'
                  AND capture.request_start_date=?
                  AND capture.request_end_date=?
                  AND run.dataset_id=?
                  AND run.command=?
                  AND run.status='succeeded'
                ORDER BY capture.captured_at DESC, capture.capture_id DESC
                LIMIT 1
                """,
                (start, end, LEGACY_WHOLESALE_DATASET_ID, WHOLESALE_COLLECTOR_ID),
            ).fetchone()
            if capture is None:
                return None
            rows = tuple(
                connection.execute(
                    """
                    SELECT source_row, row_sha256, raw_row_json, event_at, country,
                           event_name, currency, unit, previous_json, estimate_json,
                           actual_json, change_json, impact_json, change_percentage_json
                    FROM fmp_economic_calendar_rows
                    WHERE capture_id=?
                    ORDER BY source_row
                    """,
                    (str(capture["capture_id"]),),
                ).fetchall()
            )
        expected = parse_fmp_us_calendar_wholesale(
            bytes(capture["response_bytes"]),
            captured_at=str(capture["captured_at"]),
            start_date=str(capture["request_start_date"]),
            end_date=str(capture["request_end_date"]),
        )
        if (
            str(capture["response_sha256"]) != expected.response_sha256
            or str(capture["semantic_identity"]) != expected.semantic_identity
            or str(capture["captured_precision"]) != expected.captured_precision
            or str(capture["normalization_version"]) != NORMALIZATION_VERSION
            or int(capture["row_count"]) != len(expected.rows)
            or _legacy_rows(rows) != expected.rows
        ):
            raise ConflictError("FMP wholesale calendar persisted capture drifted")
        return expected

    def load_latest_window(
        self,
        start_date: str,
        end_date: str,
    ) -> FmpWholesaleCalendarCapture | None:
        """Load a matching compact cache response, else a legacy capture."""

        start, end = _validated_window(start_date, end_date)
        cached = self._load_cached_window(start, end)
        return cached if cached is not None else self._load_legacy_window(start, end)


    def publish(self, capture: FmpWholesaleCalendarCapture) -> FmpWholesalePublishReport:
        if not isinstance(capture, FmpWholesaleCalendarCapture):
            raise _fail("FMP wholesale calendar capture is invalid")
        expected_capture = parse_fmp_us_calendar_wholesale(
            capture.response_bytes,
            captured_at=capture.captured_at,
            start_date=capture.request_start_date,
            end_date=capture.request_end_date,
        )
        if capture != expected_capture:
            raise _fail("FMP wholesale calendar capture binding is invalid")
        event_rows = _event_rows(capture)
        publication_identity = self._publication_identity(capture)
        receipt_id = stable_id(
            "fmp_wholesale_calendar_receipt", publication_identity
        )
        run_id = stable_id(
            "fmp_wholesale_calendar_incremental_run", publication_identity
        )
        artifact_id = stable_id(
            "fmp_wholesale_calendar_incremental_artifact", publication_identity
        )
        snapshot_id = stable_id(
            "fmp_wholesale_calendar_incremental_snapshot", publication_identity
        )
        scope = {
            "artifact_kind": "fmp_us_calendar_receipt_manifest",
            "country": "US",
            "from": capture.request_start_date,
            "normalization_version": NORMALIZATION_VERSION,
            "persistence_version": PERSISTENCE_CODE_VERSION,
            "source_resource": SOURCE_RESOURCE,
            "to": capture.request_end_date,
        }

        def writer(connection: sqlite3.Connection, actual_run_id: str) -> WriteResult:
            if actual_run_id != run_id:
                raise ConflictError("FMP wholesale calendar run identity changed")
            changed: list[tuple[_EventRow, sqlite3.Row | None]] = []
            for event in event_rows:
                latest = connection.execute(
                    """
                    SELECT event_version_id, row_sha256, correction_sequence
                    FROM fmp_economic_calendar_raw_event_versions
                    WHERE event_id=?
                    ORDER BY correction_sequence DESC
                    LIMIT 1
                    """,
                    (event.event_id,),
                ).fetchone()
                if latest is None or str(latest["row_sha256"]) != event.row.row_sha256:
                    changed.append((event, latest))
            manifest_bytes, manifest_sha256 = _receipt_manifest(
                capture,
                changed_event_count=len(changed),
            )
            connection.execute(
                """
                INSERT INTO fmp_economic_calendar_fetch_receipts (
                    receipt_id, provider, source_resource, request_country,
                    request_start_date, request_end_date, response_sha256,
                    semantic_identity, source_semantic_identity,
                    receipt_manifest_sha256, receipt_manifest_byte_count,
                    captured_at, captured_precision, normalization_version,
                    persistence_version, row_count, changed_event_count, run_id,
                    artifact_id, snapshot_id
                ) VALUES (?, 'fmp', ?, 'US', ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    SOURCE_RESOURCE,
                    capture.request_start_date,
                    capture.request_end_date,
                    capture.response_sha256,
                    publication_identity,
                    capture.semantic_identity,
                    manifest_sha256,
                    len(manifest_bytes),
                    capture.captured_at,
                    NORMALIZATION_VERSION,
                    PERSISTENCE_CODE_VERSION,
                    len(capture.rows),
                    len(changed),
                    run_id,
                    artifact_id,
                    snapshot_id,
                ),
            )
            for event in event_rows:
                row = event.row
                connection.execute(
                    """
                    INSERT INTO fmp_economic_calendar_raw_events (
                        event_id, provider, country, event_at, event_name,
                        currency_key, created_receipt_id
                    ) VALUES (?, 'fmp', ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event.event_id,
                        row.country,
                        row.event_at,
                        row.event_name,
                        event.currency_key,
                        receipt_id,
                    ),
                )
            for event, previous in changed:
                row = event.row
                correction_sequence = (
                    1 if previous is None else int(previous["correction_sequence"]) + 1
                )
                supersedes = (
                    None if previous is None else str(previous["event_version_id"])
                )
                event_version_id = stable_id(
                    "fmp_wholesale_calendar_raw_event_version",
                    event.event_id,
                    str(correction_sequence),
                )
                connection.execute(
                    """
                    INSERT INTO fmp_economic_calendar_raw_event_versions (
                        event_version_id, event_id, receipt_id, source_row,
                        row_sha256, raw_row_json, currency, unit, previous_json,
                        estimate_json, actual_json, change_json, impact_json,
                        change_percentage_json, captured_at, captured_precision,
                        correction_sequence, supersedes_event_version_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?)
                    """,
                    (
                        event_version_id,
                        event.event_id,
                        receipt_id,
                        row.source_row,
                        row.row_sha256,
                        row.raw_row_json,
                        row.currency,
                        row.unit,
                        row.previous_json,
                        row.estimate_json,
                        row.actual_json,
                        row.change_json,
                        row.impact_json,
                        row.change_percentage_json,
                        capture.captured_at,
                        correction_sequence,
                        supersedes,
                    ),
                )
            connection.execute(
                """
                INSERT INTO fmp_economic_calendar_latest_response_cache (
                    feed_id, provider, source_resource, request_country,
                    request_start_date, request_end_date, response_sha256,
                    response_bytes, semantic_identity, source_semantic_identity,
                    captured_at, captured_precision, normalization_version,
                    persistence_version, row_count, receipt_id
                ) VALUES (?, 'fmp', ?, 'US', ?, ?, ?, ?, ?, ?, ?, 'datetime', ?, ?, ?, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    provider=excluded.provider,
                    source_resource=excluded.source_resource,
                    request_country=excluded.request_country,
                    request_start_date=excluded.request_start_date,
                    request_end_date=excluded.request_end_date,
                    response_sha256=excluded.response_sha256,
                    response_bytes=excluded.response_bytes,
                    semantic_identity=excluded.semantic_identity,
                    source_semantic_identity=excluded.source_semantic_identity,
                    captured_at=excluded.captured_at,
                    captured_precision=excluded.captured_precision,
                    normalization_version=excluded.normalization_version,
                    persistence_version=excluded.persistence_version,
                    row_count=excluded.row_count,
                    receipt_id=excluded.receipt_id
                """,
                (
                    _FEED_ID,
                    SOURCE_RESOURCE,
                    capture.request_start_date,
                    capture.request_end_date,
                    capture.response_sha256,
                    capture.response_bytes,
                    publication_identity,
                    capture.semantic_identity,
                    capture.captured_at,
                    NORMALIZATION_VERSION,
                    PERSISTENCE_CODE_VERSION,
                    len(capture.rows),
                    receipt_id,
                ),
            )
            artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=WHOLESALE_DATASET_ID,
                content_sha256=manifest_sha256,
                media_type="application/json",
                byte_count=len(manifest_bytes),
                source_reference=SOURCE_REFERENCE,
                request_scope=scope,
                captured_at=capture.captured_at,
                captured_precision="datetime",
                normalization_version=PERSISTENCE_CODE_VERSION,
            )
            snapshot = SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=WHOLESALE_DATASET_ID,
                semantic_identity=publication_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.rows),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            )
            quality = QualityWrite(
                quality_result_id=stable_id(
                    "fmp_wholesale_calendar_incremental_quality",
                    publication_identity,
                ),
                dataset_id=WHOLESALE_DATASET_ID,
                rule_id="compact_calendar_event_delta",
                rule_version="1.0.0",
                severity="informational",
                outcome="passed",
                subject_kind="snapshot",
                subject_id=snapshot_id,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                observed={
                    "changed_event_count": len(changed),
                    "receipt_id": receipt_id,
                    "response_sha256": capture.response_sha256,
                    "source_semantic_identity": capture.semantic_identity,
                },
            )
            return WriteResult(
                written_count=1 + len(changed),
                artifacts=(artifact,),
                snapshot=snapshot,
                quality_results=(quality,),
            )

        receipt = IngestionCoordinator(
            self._stores, code_version=PERSISTENCE_CODE_VERSION
        ).execute(
            role=StoreRole.MACRO,
            dataset_id=WHOLESALE_DATASET_ID,
            output_dataset_ids=(WHOLESALE_DATASET_ID, WHOLESALE_EVENT_DATASET_ID),
            semantic_identity=publication_identity,
            run_id=run_id,
            command=WHOLESALE_COLLECTOR_ID,
            scope=scope,
            started_at=capture.captured_at,
            completed_at=capture.captured_at,
            fetched_count=len(capture.rows),
            writer=writer,
        )
        return FmpWholesalePublishReport(
            outcome="unchanged" if receipt.outcome == "unchanged" else "published",
            semantic_identity=capture.semantic_identity,
            capture_id=receipt_id,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_captures=0 if receipt.outcome == "unchanged" else 1,
            written_rows=0 if receipt.outcome == "unchanged" else receipt.written_count - 1,
        )
