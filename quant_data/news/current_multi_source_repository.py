"""Read-only projection of the generic current multi-source news relations."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import (
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)
from .current_multi_source import CURRENT_MULTI_SOURCE_FEED_IDS
from .current_repository import CurrentNewsQuery


CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID = "news.current_multi_source_evidence"
CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID = "news.current_multi_source_articles"
CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS = (
    CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID,
    CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID,
)
_CURRENT_ARTICLES_TABLE = "current_multi_source_articles"
_MAX_CANDIDATE_ROWS = 200_000


@dataclass(frozen=True, slots=True)
class CurrentMultiSourceNewsSelection:
    records: tuple[dict[str, object], ...]
    total_selected_count: int
    truncated: bool
    migration_ids: tuple[str, ...]
    receipt_sha256: str
    warnings: tuple[str, ...] = ()
    dataset_ids: tuple[str, str] = CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS


def _migration_receipt(connection: Any) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    rows = list(
        connection.execute(
            "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
        )
    )
    return (
        tuple(str(row["migration_id"]) for row in rows),
        [
            {"migration_id": str(row["migration_id"]), "sha256": str(row["sha256"])}
            for row in rows
        ],
    )


def _captured(row: Mapping[str, object]) -> TemporalValue:
    value = TemporalValue.parse(str(row["captured_at"]), pointer="/stored/captured_at")
    if value.precision is not TemporalPrecision.DATETIME:
        raise ValidationError("Stored multi-source news availability is invalid")
    return value


def _published(
    row: Mapping[str, object],
) -> tuple[str | None, str | None, str, str, datetime | None, str | None]:
    raw = None if row["published_date_raw"] is None else str(row["published_date_raw"])
    normalized = (
        None if row["published_normalized_at"] is None else str(row["published_normalized_at"])
    )
    precision = str(row["published_precision"])
    offset_status = str(row["published_offset_status"])
    allowed_precisions = {
        "date",
        "datetime_naive",
        "datetime_offset",
        "missing",
        "unknown",
    }
    if precision not in allowed_precisions or offset_status not in {"known", "unknown", "missing"}:
        raise ValidationError("Stored multi-source news publication metadata is invalid")
    instant: datetime | None = None
    if precision == "datetime_offset":
        if raw is None or normalized is None or offset_status != "known":
            raise ValidationError("Stored multi-source offset publication is inconsistent")
        temporal = TemporalValue.parse(normalized, pointer="/stored/published_normalized_at")
        if temporal.precision is not TemporalPrecision.DATETIME:
            raise ValidationError("Stored multi-source publication is invalid")
        assert isinstance(temporal.value, datetime)
        instant = temporal.value.astimezone(timezone.utc)
    elif precision == "missing":
        if raw is not None or normalized is not None or offset_status != "missing":
            raise ValidationError("Stored multi-source missing publication is inconsistent")
    elif raw is None or normalized is not None or offset_status != "unknown":
        raise ValidationError("Stored multi-source publication is inconsistent")
    calendar_date: str | None = None
    if raw is not None:
        try:
            calendar_date = parse_date(raw[:10], pointer="/stored/published_date").isoformat()
        except ValidationError:
            if precision == "datetime_offset":
                try:
                    source_datetime = parsedate_to_datetime(raw)
                except (TypeError, ValueError, IndexError):
                    source_datetime = None
                if (
                    source_datetime is None
                    or source_datetime.tzinfo is None
                    or source_datetime.utcoffset() is None
                ):
                    raise ValidationError(
                        "Stored multi-source publication date is invalid"
                    ) from None
                calendar_date = source_datetime.date().isoformat()
            elif precision in {"date", "datetime_naive"}:
                raise ValidationError(
                    "Stored multi-source publication date is invalid"
                ) from None
    return raw, normalized, precision, offset_status, instant, calendar_date


def _text(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Stored multi-source news {name} is invalid")
    return value


def _query_rows(connection: Any) -> list[Mapping[str, object]]:
    try:
        rows = list(
            connection.execute(
                """
                SELECT article.article_id, article.feed_id, article.provider,
                       article.source_item_key, article.created_capture_id,
                       version.article_version_id, version.article_id AS version_article_id,
                       version.title, version.summary, version.site, version.source_url,
                       version.published_date_raw, version.published_normalized_at,
                       version.published_precision, version.published_offset_status,
                       version.version_sequence, version.supersedes_article_version_id,
                       version.capture_id, version.source_row, capture.captured_at,
                       membership.source_row AS membership_source_row
                FROM current_multi_source_article_versions AS version
                JOIN current_multi_source_articles AS article
                  ON article.article_id=version.article_id
                JOIN current_multi_source_captures AS capture
                  ON capture.capture_id=version.capture_id
                JOIN current_multi_source_capture_articles AS membership
                  ON membership.capture_id=version.capture_id
                 AND membership.article_version_id=version.article_version_id
                 AND membership.source_row=version.source_row
                ORDER BY article.article_id, version.version_sequence, version.article_version_id
                LIMIT ?
                """,
                (_MAX_CANDIDATE_ROWS + 1,),
            )
        )
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Current multi-source news store is unavailable") from exc
    if len(rows) > _MAX_CANDIDATE_ROWS:
        raise ResourceLimitError("Multi-source news version history exceeds the query bound")
    return rows


def _symbols(connection: Any, article_version_id: str) -> tuple[str, ...]:
    try:
        rows = list(
            connection.execute(
                """
                SELECT provider_symbol
                FROM current_multi_source_article_symbols
                WHERE article_version_id=?
                ORDER BY provider_symbol COLLATE BINARY
                """,
                (article_version_id,),
            )
        )
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Current multi-source news symbols are unavailable") from exc
    symbols = tuple(_text(row, "provider_symbol") for row in rows)
    if len(symbols) != len(set(symbols)):
        raise ValidationError("Stored multi-source article symbols are ambiguous")
    return symbols


def _rank(row: Mapping[str, object]) -> tuple[int, datetime, str]:
    sequence = row["version_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValidationError("Stored multi-source version sequence is invalid")
    captured = _captured(row)
    assert isinstance(captured.value, datetime)
    return sequence, captured.value.astimezone(timezone.utc), _text(row, "article_version_id")


def _record(connection: Any, row: Mapping[str, object]) -> dict[str, object]:
    article_id = _text(row, "article_id")
    if row["version_article_id"] != article_id:
        raise ValidationError("Stored multi-source article/version identity is inconsistent")
    source_row = row["source_row"]
    if (
        isinstance(source_row, bool)
        or not isinstance(source_row, int)
        or source_row < 1
        or row["membership_source_row"] != source_row
    ):
        raise ValidationError("Stored multi-source capture membership is inconsistent")
    raw, normalized, precision, offset_status, instant, calendar_date = _published(row)
    captured = _captured(row)
    sequence, _, version_id = _rank(row)
    symbols = _symbols(connection, version_id)
    return {
        "article_id": article_id,
        "article_version_id": version_id,
        "available_at": str(row["captured_at"]),
        "capture_id": _text(row, "capture_id"),
        "feed_id": _text(row, "feed_id"),
        "headline": _text(row, "title"),
        "provider": _text(row, "provider"),
        "published_date_raw": raw,
        "published_normalized_at": normalized,
        "published_offset_status": offset_status,
        "published_precision": precision,
        "source_name": _text(row, "site"),
        "source_row": source_row,
        "source_url": None if row["source_url"] is None else str(row["source_url"]),
        "summary": _text(row, "summary") if row["summary"] else "",
        "symbol": symbols[0] if len(symbols) == 1 else None,
        "symbols": list(symbols),
        "version_sequence": sequence,
        "_captured_instant": captured.value.astimezone(timezone.utc),
        "_provider_calendar_date": calendar_date,
        "_publication_instant": instant,
    }


def _matches(record: Mapping[str, object], query: CurrentNewsQuery) -> bool:
    if query.symbols is not None:
        wanted = {item.casefold() for item in query.symbols}
        if not any(item.casefold() in wanted for item in record["symbols"]):
            return False
    if query.query:
        needle = query.query.casefold()
        searchable = (
            record["headline"],
            record["summary"],
            record["source_name"],
            record["source_url"] or "",
            record["provider"],
            record["feed_id"],
            " ".join(record["symbols"]),
        )
        if not any(needle in str(value).casefold() for value in searchable):
            return False
    provider_date = record["_provider_calendar_date"]
    if query.start_date is not None and (
        provider_date is None or str(provider_date) < query.start_date
    ):
        return False
    if query.end_date is not None and (
        provider_date is None or str(provider_date) > query.end_date
    ):
        return False
    return True


def _source_ids(source_ids: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(source_ids, tuple):
        raise ValidationError("Current multi-source source_ids must be a tuple")
    if any(
        not isinstance(item, str) or item not in CURRENT_MULTI_SOURCE_FEED_IDS
        for item in source_ids
    ):
        raise ValidationError("Current multi-source source_ids contain an unsupported feed")
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("Current multi-source source_ids must be unique")
    return source_ids


def _sort(records: list[dict[str, object]]) -> None:
    records.sort(key=lambda row: str(row["article_id"]))
    records.sort(key=lambda row: row["_captured_instant"], reverse=True)
    records.sort(
        key=lambda row: (
            row["_publication_instant"] is not None,
            row["_publication_instant"]
            if row["_publication_instant"] is not None
            else datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )


class CurrentMultiSourceNewsRepository:
    """Read registered generic current-news headlines without bodies or raw bytes."""

    def __init__(self, store_map: StoreMap, registry: Registry | None = None) -> None:
        if not isinstance(store_map, StoreMap):
            raise ValidationError("Multi-source news reader requires an explicit store map")
        if registry is not None and not isinstance(registry, Registry):
            raise ValidationError("Multi-source news reader registry is invalid")
        self._store_map = store_map
        self._registry = registry

    def _require_datasets(self) -> None:
        if self._registry is None:
            return
        datasets = {
            item.id: item for item in self._registry.datasets_for(StoreRole.NEWS.value)
        }
        for dataset_id in CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS:
            dataset = datasets.get(dataset_id)
            if dataset is None or dataset.store != StoreRole.NEWS.value or not dataset.active:
                raise ValidationError("Current multi-source news datasets are not registered")

    def search(
        self,
        query: CurrentNewsQuery,
        *,
        source_ids: tuple[str, ...] = (),
    ) -> CurrentMultiSourceNewsSelection:
        if not isinstance(query, CurrentNewsQuery):
            raise ValidationError("Multi-source news selection requires a typed current-news query")
        source_ids = _source_ids(source_ids)
        self._require_datasets()
        with quiet_immutable_read_connection(
            self._store_map, StoreRole.NEWS, expected_anchor=_CURRENT_ARTICLES_TABLE
        ) as connection:
            rows = _query_rows(connection)
            migration_ids, migrations = _migration_receipt(connection)
            cutoff = query.cutoff
            warnings: set[str] = set()
            grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
            for row in rows:
                if cutoff is not None:
                    decision = availability_at_or_before(
                        _captured(row), cutoff, query.date_only_policy
                    )
                    warnings.update(decision.warnings)
                    if not decision.included:
                        continue
                grouped[_text(row, "article_id")].append(row)
            selected = [
                _record(connection, max(candidates, key=_rank))
                for candidates in grouped.values()
            ]
        filtered = [
            record for record in selected
            if (not source_ids or str(record["feed_id"]) in source_ids)
            and _matches(record, query)
        ]
        _sort(filtered)
        rendered = [
            {
                key: value
                for key, value in record.items()
                if not key.startswith("_")
            }
            for record in filtered[: query.limit]
        ]
        receipt = {
            "dataset_ids": list(CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS),
            "migrations": migrations,
            "request": query.receipt_mapping(),
            "source_ids": list(source_ids),
            "selected_article_versions": [
                str(record["article_version_id"]) for record in rendered
            ],
            "total_selected_count": len(filtered),
            "truncated": len(filtered) > query.limit,
            "warnings": sorted(warnings),
        }
        return CurrentMultiSourceNewsSelection(
            records=tuple(rendered),
            total_selected_count=len(filtered),
            truncated=len(filtered) > query.limit,
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
            warnings=tuple(sorted(warnings)),
        )


__all__ = (
    "CURRENT_MULTI_SOURCE_REPOSITORY_ARTICLES_DATASET_ID",
    "CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS",
    "CURRENT_MULTI_SOURCE_REPOSITORY_EVIDENCE_DATASET_ID",
    "CurrentMultiSourceNewsRepository",
    "CurrentMultiSourceNewsSelection",
)
