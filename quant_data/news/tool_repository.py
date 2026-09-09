"""Safe, bounded current-news reads for the fixed news tool surface.

This repository deliberately reads only the two current-news relation families:
the repeatable FMP stock-latest feed and the generic fixed multi-source feeds.
It never opens a writable connection, invokes a provider, or projects retained
article bodies, response bytes, image URLs, credentials, SQL, or store paths.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import TemporalPrecision, TemporalValue
from .current_multi_source_repository import (
    CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS,
)
from .current_repository import CURRENT_NEWS_DATASET_IDS


FMP_STOCK_LATEST_SOURCE_ID = "fmp_stock_latest"
CURRENT_NEWS_TOOL_DATASET_IDS = (
    *CURRENT_NEWS_DATASET_IDS,
    *CURRENT_MULTI_SOURCE_REPOSITORY_DATASET_IDS,
)

MAX_ITEM_HISTORY_LIMIT = 500
_MAX_IDENTIFIER_LENGTH = 256
_MAX_CAPTURE_MEMBERSHIPS = 10_000
_OUTCOME_KINDS = frozenset(
    {"succeeded", "request_failed", "response_rejected", "publication_failed"}
)
_PUBLICATION_PRECISIONS = frozenset(
    {"datetime_offset", "datetime_naive", "date", "unknown", "missing"}
)
_PUBLICATION_OFFSET_STATUSES = frozenset({"known", "unknown", "missing"})


@dataclass(frozen=True, slots=True)
class _SourceSpec:
    source_id: str
    provider: str
    family: str


_SOURCE_SPECS = (
    _SourceSpec(FMP_STOCK_LATEST_SOURCE_ID, "fmp", "fmp_stock_latest"),
    _SourceSpec("fmp_press_releases", "fmp", "multi_source"),
    _SourceSpec("fmp_general", "fmp", "multi_source"),
    _SourceSpec("fed_press", "fed", "multi_source"),
    _SourceSpec("ecb_press", "ecb", "multi_source"),
    _SourceSpec("bea_news", "bea", "multi_source"),
    _SourceSpec("eia_press", "eia", "multi_source"),
    _SourceSpec("alpaca_benzinga", "alpaca", "multi_source"),
)
CURRENT_NEWS_TOOL_SOURCE_IDS = tuple(item.source_id for item in _SOURCE_SPECS)
_SOURCE_BY_ID = {item.source_id: item for item in _SOURCE_SPECS}


_FMP_STATUS_COUNTS_SQL = """
SELECT
    (SELECT COUNT(*) FROM fmp_stock_latest_current_attempts) AS attempt_count,
    (SELECT COUNT(*) FROM fmp_stock_latest_current_outcomes) AS outcome_count,
    (SELECT COUNT(*) FROM fmp_stock_latest_current_outcomes
      WHERE outcome_kind='succeeded') AS successful_outcome_count,
    (SELECT COUNT(*) FROM fmp_stock_latest_current_captures) AS capture_count,
    (SELECT COUNT(*) FROM fmp_stock_latest_current_articles) AS article_count,
    (SELECT COUNT(*) FROM fmp_stock_latest_current_article_versions)
        AS article_version_count,
    COALESCE((SELECT SUM(accepted_row_count)
              FROM fmp_stock_latest_current_outcomes), 0)
        AS accepted_row_count_total,
    COALESCE((SELECT SUM(rejected_row_count)
              FROM fmp_stock_latest_current_outcomes), 0)
        AS rejected_row_count_total
"""

_MULTI_STATUS_COUNTS_SQL = """
SELECT
    (SELECT COUNT(*) FROM current_multi_source_attempts WHERE feed_id=?)
        AS attempt_count,
    (SELECT COUNT(*)
       FROM current_multi_source_outcomes AS outcome
       JOIN current_multi_source_attempts AS attempt
         ON attempt.attempt_id=outcome.attempt_id
      WHERE attempt.feed_id=?) AS outcome_count,
    (SELECT COUNT(*)
       FROM current_multi_source_outcomes AS outcome
       JOIN current_multi_source_attempts AS attempt
         ON attempt.attempt_id=outcome.attempt_id
      WHERE attempt.feed_id=? AND outcome.outcome_kind='succeeded')
        AS successful_outcome_count,
    (SELECT COUNT(*) FROM current_multi_source_captures WHERE feed_id=?)
        AS capture_count,
    (SELECT COUNT(*) FROM current_multi_source_articles WHERE feed_id=?)
        AS article_count,
    (SELECT COUNT(*)
       FROM current_multi_source_article_versions AS version
       JOIN current_multi_source_articles AS article
         ON article.article_id=version.article_id
      WHERE article.feed_id=?) AS article_version_count,
    COALESCE((SELECT SUM(outcome.accepted_row_count)
                FROM current_multi_source_outcomes AS outcome
                JOIN current_multi_source_attempts AS attempt
                  ON attempt.attempt_id=outcome.attempt_id
               WHERE attempt.feed_id=?), 0) AS accepted_row_count_total,
    COALESCE((SELECT SUM(outcome.rejected_row_count)
                FROM current_multi_source_outcomes AS outcome
                JOIN current_multi_source_attempts AS attempt
                  ON attempt.attempt_id=outcome.attempt_id
               WHERE attempt.feed_id=?), 0) AS rejected_row_count_total
"""

_FMP_LATEST_OUTCOME_SQL = """
SELECT outcome_id, outcome_kind, http_status, provider_row_count,
       accepted_row_count, rejected_row_count, recorded_at, recorded_precision
FROM fmp_stock_latest_current_outcomes
ORDER BY recorded_at DESC, outcome_id COLLATE BINARY DESC
LIMIT 1
"""

_MULTI_LATEST_OUTCOME_SQL = """
SELECT outcome.outcome_id, outcome.outcome_kind, outcome.http_status,
       outcome.provider_row_count, outcome.accepted_row_count,
       outcome.rejected_row_count, outcome.recorded_at, outcome.recorded_precision
FROM current_multi_source_outcomes AS outcome
JOIN current_multi_source_attempts AS attempt
  ON attempt.attempt_id=outcome.attempt_id
WHERE attempt.feed_id=?
ORDER BY outcome.recorded_at DESC, outcome.outcome_id COLLATE BINARY DESC
LIMIT 1
"""

_FMP_LATEST_CAPTURE_SQL = """
SELECT capture.capture_id, capture.captured_at, capture.captured_precision,
       capture.provider_row_count, capture.accepted_row_count,
       capture.rejected_row_count
FROM fmp_stock_latest_current_captures AS capture
JOIN fmp_stock_latest_current_outcomes AS outcome
  ON outcome.outcome_id=capture.outcome_id
 AND outcome.outcome_kind='succeeded'
ORDER BY capture.captured_at DESC, capture.capture_id COLLATE BINARY DESC
LIMIT 1
"""

_MULTI_LATEST_CAPTURE_SQL = """
SELECT capture.capture_id, capture.captured_at, capture.captured_precision,
       capture.provider_row_count, capture.accepted_row_count,
       capture.rejected_row_count
FROM current_multi_source_captures AS capture
JOIN current_multi_source_outcomes AS outcome
  ON outcome.outcome_id=capture.outcome_id
 AND outcome.outcome_kind='succeeded'
WHERE capture.feed_id=?
ORDER BY capture.captured_at DESC, capture.capture_id COLLATE BINARY DESC
LIMIT 1
"""

_FMP_HISTORY_COUNT_SQL = """
SELECT COUNT(*) AS version_count
FROM fmp_stock_latest_current_article_versions AS version
JOIN fmp_stock_latest_current_articles AS article
  ON article.article_id=version.article_id
WHERE article.article_id=?
"""

_MULTI_HISTORY_COUNT_SQL = """
SELECT COUNT(*) AS version_count
FROM current_multi_source_article_versions AS version
JOIN current_multi_source_articles AS article
  ON article.article_id=version.article_id
WHERE article.article_id=?
  AND article.feed_id=?
"""

_FMP_HISTORY_SQL = """
SELECT article.article_id, article.symbol,
       version.article_version_id, version.title,
       version.published_date_raw, version.published_normalized_at,
       version.published_precision, version.published_offset_status,
       version.version_sequence, version.supersedes_article_version_id,
       version.capture_id, capture.captured_at, capture.captured_precision
FROM fmp_stock_latest_current_article_versions AS version
JOIN fmp_stock_latest_current_articles AS article
  ON article.article_id=version.article_id
JOIN fmp_stock_latest_current_captures AS capture
  ON capture.capture_id=version.capture_id
WHERE article.article_id=?
ORDER BY version.version_sequence ASC, version.article_version_id COLLATE BINARY ASC
LIMIT ?
"""

_MULTI_HISTORY_SQL = """
SELECT article.article_id, article.feed_id, article.provider,
       version.article_version_id, version.title, version.summary,
       version.published_date_raw, version.published_normalized_at,
       version.published_precision, version.published_offset_status,
       version.version_sequence, version.supersedes_article_version_id,
       version.capture_id, capture.captured_at, capture.captured_precision
FROM current_multi_source_article_versions AS version
JOIN current_multi_source_articles AS article
  ON article.article_id=version.article_id
JOIN current_multi_source_captures AS capture
  ON capture.capture_id=version.capture_id
WHERE article.article_id=?
  AND article.feed_id=?
ORDER BY version.version_sequence ASC, version.article_version_id COLLATE BINARY ASC
LIMIT ?
"""

_FMP_VERSION_SQL = """
SELECT article.article_id, article.symbol,
       version.article_version_id, version.title,
       version.published_date_raw, version.published_normalized_at,
       version.published_precision, version.published_offset_status,
       version.version_sequence, version.supersedes_article_version_id,
       version.capture_id, capture.captured_at, capture.captured_precision
FROM fmp_stock_latest_current_article_versions AS version
JOIN fmp_stock_latest_current_articles AS article
  ON article.article_id=version.article_id
JOIN fmp_stock_latest_current_captures AS capture
  ON capture.capture_id=version.capture_id
WHERE version.article_version_id=?
LIMIT 2
"""

_MULTI_VERSION_SQL = """
SELECT article.article_id, article.feed_id, article.provider,
       version.article_version_id, version.title, version.summary,
       version.published_date_raw, version.published_normalized_at,
       version.published_precision, version.published_offset_status,
       version.version_sequence, version.supersedes_article_version_id,
       version.capture_id, capture.captured_at, capture.captured_precision
FROM current_multi_source_article_versions AS version
JOIN current_multi_source_articles AS article
  ON article.article_id=version.article_id
JOIN current_multi_source_captures AS capture
  ON capture.capture_id=version.capture_id
WHERE version.article_version_id=?
  AND article.feed_id=?
LIMIT 2
"""

_FMP_MEMBERSHIPS_SQL = """
SELECT membership.capture_id, membership.source_row,
       capture.captured_at, capture.captured_precision
FROM fmp_stock_latest_current_capture_articles AS membership
JOIN fmp_stock_latest_current_captures AS capture
  ON capture.capture_id=membership.capture_id
WHERE membership.article_version_id=?
ORDER BY capture.captured_at ASC, membership.capture_id COLLATE BINARY ASC,
         membership.source_row ASC
LIMIT ?
"""

_MULTI_MEMBERSHIPS_SQL = """
SELECT membership.capture_id, membership.source_row,
       capture.captured_at, capture.captured_precision
FROM current_multi_source_capture_articles AS membership
JOIN current_multi_source_captures AS capture
  ON capture.capture_id=membership.capture_id
WHERE membership.article_version_id=?
ORDER BY capture.captured_at ASC, membership.capture_id COLLATE BINARY ASC,
         membership.source_row ASC
LIMIT ?
"""

_MULTI_SYMBOLS_SQL = """
SELECT provider_symbol
FROM current_multi_source_article_symbols
WHERE article_version_id=?
ORDER BY provider_symbol COLLATE BINARY ASC
LIMIT ?
"""


def _identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Current-news {label} must be text")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_IDENTIFIER_LENGTH
        or "\x00" in normalized
    ):
        raise ValidationError(f"Current-news {label} is invalid")
    return normalized


def _history_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResourceLimitError("Current-news history limit must be an integer")
    if not 1 <= value <= MAX_ITEM_HISTORY_LIMIT:
        raise ResourceLimitError(
            "Current-news history limit must be from 1 through 500"
        )
    return value


def _selected_sources(source_ids: object) -> tuple[_SourceSpec, ...]:
    if not isinstance(source_ids, tuple):
        raise ValidationError("Current-news source_ids must be a tuple")
    if len(source_ids) > len(_SOURCE_SPECS):
        raise ResourceLimitError("Current-news source_ids exceeds the fixed source bound")
    if not source_ids:
        return _SOURCE_SPECS
    normalized: set[str] = set()
    for source_id in source_ids:
        if not isinstance(source_id, str) or source_id not in _SOURCE_BY_ID:
            raise ValidationError("Current-news source_ids contain an unsupported source")
        if source_id in normalized:
            raise ValidationError("Current-news source_ids must be distinct")
        normalized.add(source_id)
    return tuple(item for item in _SOURCE_SPECS if item.source_id in normalized)


def _rows(
    connection: Any,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> list[Mapping[str, object]]:
    try:
        return list(connection.execute(statement, parameters))
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Current news store is unavailable") from exc


def _one(
    connection: Any,
    statement: str,
    parameters: tuple[object, ...] = (),
) -> Mapping[str, object] | None:
    result = _rows(connection, statement, parameters)
    if not result:
        return None
    return result[0]


def _text(
    row: Mapping[str, object],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = row[name]
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _optional_text(row: Mapping[str, object], name: str) -> str | None:
    value = row[name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _count(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _optional_count(row: Mapping[str, object], name: str) -> int | None:
    value = row[name]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _timestamp(
    row: Mapping[str, object],
    name: str,
    precision_name: str,
) -> str:
    if _text(row, precision_name) != "datetime":
        raise ValidationError(f"Stored current-news {precision_name} is invalid")
    value = _text(row, name)
    parsed = TemporalValue.parse(value, pointer=f"/stored/{name}")
    if parsed.precision is not TemporalPrecision.DATETIME:
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _publication(row: Mapping[str, object]) -> tuple[str | None, str | None, str, str]:
    raw = _optional_text(row, "published_date_raw")
    normalized = _optional_text(row, "published_normalized_at")
    precision = _text(row, "published_precision")
    offset_status = _text(row, "published_offset_status")
    if precision not in _PUBLICATION_PRECISIONS:
        raise ValidationError("Stored current-news publication precision is invalid")
    if offset_status not in _PUBLICATION_OFFSET_STATUSES:
        raise ValidationError("Stored current-news publication offset status is invalid")
    if precision == "datetime_offset":
        if raw is None or normalized is None or offset_status != "known":
            raise ValidationError("Stored current-news publication metadata is invalid")
        normalized_value = TemporalValue.parse(
            normalized, pointer="/stored/published_normalized_at"
        )
        if normalized_value.precision is not TemporalPrecision.DATETIME:
            raise ValidationError("Stored current-news publication metadata is invalid")
    elif precision == "missing":
        if raw is not None or normalized is not None or offset_status != "missing":
            raise ValidationError("Stored current-news publication metadata is invalid")
    elif raw is None or normalized is not None or offset_status != "unknown":
        raise ValidationError("Stored current-news publication metadata is invalid")
    return raw, normalized, precision, offset_status


def _row_counts(row: Mapping[str, object]) -> dict[str, int | None]:
    provider = _optional_count(row, "provider_row_count")
    accepted = _optional_count(row, "accepted_row_count")
    rejected = _optional_count(row, "rejected_row_count")
    if provider is None:
        if accepted is not None or rejected is not None:
            raise ValidationError("Stored current-news outcome counts are invalid")
    elif accepted is None or rejected is None or accepted + rejected != provider:
        raise ValidationError("Stored current-news outcome counts are invalid")
    return {
        "provider_row_count": provider,
        "accepted_row_count": accepted,
        "rejected_row_count": rejected,
    }


def _latest_outcome(row: Mapping[str, object] | None) -> dict[str, object] | None:
    if row is None:
        return None
    outcome_kind = _text(row, "outcome_kind")
    if outcome_kind not in _OUTCOME_KINDS:
        raise ValidationError("Stored current-news outcome kind is invalid")
    http_status = row["http_status"]
    if http_status is not None and (
        isinstance(http_status, bool) or not isinstance(http_status, int)
    ):
        raise ValidationError("Stored current-news outcome HTTP status is invalid")
    return {
        "outcome_id": _text(row, "outcome_id"),
        "outcome_kind": outcome_kind,
        "recorded_at": _timestamp(row, "recorded_at", "recorded_precision"),
        "http_status": http_status,
        **_row_counts(row),
    }


def _latest_capture(row: Mapping[str, object] | None) -> dict[str, object] | None:
    if row is None:
        return None
    counts = _row_counts(row)
    if counts["provider_row_count"] is None:
        raise ValidationError("Stored current-news capture counts are invalid")
    return {
        "capture_id": _text(row, "capture_id"),
        "captured_at": _timestamp(row, "captured_at", "captured_precision"),
        **counts,
    }


def _source_counts(
    connection: Any,
    source: _SourceSpec,
) -> Mapping[str, object]:
    if source.family == "fmp_stock_latest":
        result = _one(connection, _FMP_STATUS_COUNTS_SQL)
    else:
        result = _one(
            connection,
            _MULTI_STATUS_COUNTS_SQL,
            (source.source_id,) * 8,
        )
    if result is None:
        raise StoreUnavailableError("Current news store is unavailable")
    return result


def _source_latest_outcome(
    connection: Any,
    source: _SourceSpec,
) -> Mapping[str, object] | None:
    if source.family == "fmp_stock_latest":
        return _one(connection, _FMP_LATEST_OUTCOME_SQL)
    return _one(connection, _MULTI_LATEST_OUTCOME_SQL, (source.source_id,))


def _source_latest_capture(
    connection: Any,
    source: _SourceSpec,
) -> Mapping[str, object] | None:
    if source.family == "fmp_stock_latest":
        return _one(connection, _FMP_LATEST_CAPTURE_SQL)
    return _one(connection, _MULTI_LATEST_CAPTURE_SQL, (source.source_id,))


def _status_record(
    connection: Any,
    source: _SourceSpec,
) -> dict[str, object]:
    counts = _source_counts(connection, source)
    outcome = _latest_outcome(_source_latest_outcome(connection, source))
    capture = _latest_capture(_source_latest_capture(connection, source))
    return {
        "source_id": source.source_id,
        "feed_id": source.source_id,
        "provider": source.provider,
        "status": "no_retained_outcome" if outcome is None else outcome["outcome_kind"],
        "no_retained_outcome": outcome is None,
        "attempt_count": _count(counts, "attempt_count"),
        "outcome_count": _count(counts, "outcome_count"),
        "successful_outcome_count": _count(counts, "successful_outcome_count"),
        "capture_count": _count(counts, "capture_count"),
        "article_count": _count(counts, "article_count"),
        "article_version_count": _count(counts, "article_version_count"),
        "accepted_row_count_total": _count(counts, "accepted_row_count_total"),
        "rejected_row_count_total": _count(counts, "rejected_row_count_total"),
        "latest_outcome": outcome,
        "latest_successful_capture": capture,
    }


def _history_count(
    connection: Any,
    source: _SourceSpec,
    article_id: str,
) -> int:
    statement = (
        _FMP_HISTORY_COUNT_SQL
        if source.family == "fmp_stock_latest"
        else _MULTI_HISTORY_COUNT_SQL
    )
    parameters = (
        (article_id,)
        if source.family == "fmp_stock_latest"
        else (article_id, source.source_id)
    )
    row = _one(connection, statement, parameters)
    if row is None:
        raise StoreUnavailableError("Current news store is unavailable")
    return _count(row, "version_count")


def _history_rows(
    connection: Any,
    source: _SourceSpec,
    article_id: str,
    limit: int,
) -> list[Mapping[str, object]]:
    statement = (
        _FMP_HISTORY_SQL
        if source.family == "fmp_stock_latest"
        else _MULTI_HISTORY_SQL
    )
    parameters = (
        (article_id, limit)
        if source.family == "fmp_stock_latest"
        else (article_id, source.source_id, limit)
    )
    return _rows(connection, statement, parameters)


def _version_rows(
    connection: Any,
    source: _SourceSpec,
    article_version_id: str,
) -> list[Mapping[str, object]]:
    statement = (
        _FMP_VERSION_SQL
        if source.family == "fmp_stock_latest"
        else _MULTI_VERSION_SQL
    )
    parameters = (
        (article_version_id,)
        if source.family == "fmp_stock_latest"
        else (article_version_id, source.source_id)
    )
    rows = _rows(connection, statement, parameters)
    if len(rows) > 1:
        raise ConflictError("Current-news article version identity is ambiguous")
    return rows


def _symbols(
    connection: Any,
    source: _SourceSpec,
    row: Mapping[str, object],
) -> tuple[str, ...]:
    if source.family == "fmp_stock_latest":
        return (_text(row, "symbol"),)
    version_id = _text(row, "article_version_id")
    symbol_rows = _rows(
        connection,
        _MULTI_SYMBOLS_SQL,
        (version_id, _MAX_CAPTURE_MEMBERSHIPS + 1),
    )
    if len(symbol_rows) > _MAX_CAPTURE_MEMBERSHIPS:
        raise ResourceLimitError("Current-news symbols exceed the bounded history contract")
    symbols = tuple(_text(item, "provider_symbol") for item in symbol_rows)
    if len(symbols) != len(set(symbols)):
        raise ConflictError("Current-news article symbols are ambiguous")
    return symbols


def _capture_memberships(
    connection: Any,
    source: _SourceSpec,
    article_version_id: str,
    *,
    remaining: int,
) -> tuple[tuple[str, ...], tuple[dict[str, object], ...]]:
    if remaining < 1:
        raise ResourceLimitError("Current-news capture memberships exceed the bounded history contract")
    statement = (
        _FMP_MEMBERSHIPS_SQL
        if source.family == "fmp_stock_latest"
        else _MULTI_MEMBERSHIPS_SQL
    )
    rows = _rows(connection, statement, (article_version_id, remaining + 1))
    if len(rows) > remaining:
        raise ResourceLimitError("Current-news capture memberships exceed the bounded history contract")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        capture_id = _text(row, "capture_id")
        if capture_id in seen:
            raise ConflictError("Current-news capture membership is ambiguous")
        source_row = row["source_row"]
        if isinstance(source_row, bool) or not isinstance(source_row, int) or source_row < 1:
            raise ValidationError("Stored current-news capture membership is invalid")
        seen.add(capture_id)
        records.append(
            {
                "capture_id": capture_id,
                "captured_at": _timestamp(row, "captured_at", "captured_precision"),
                "source_row": source_row,
            }
        )
    return tuple(item["capture_id"] for item in records), tuple(records)


def _safe_version(
    connection: Any,
    source: _SourceSpec,
    row: Mapping[str, object],
    *,
    remaining_memberships: int,
) -> tuple[dict[str, object], int]:
    article_id = _text(row, "article_id")
    version_id = _text(row, "article_version_id")
    if source.family == "multi_source":
        if _text(row, "feed_id") != source.source_id:
            raise ValidationError("Stored current-news feed identity is invalid")
        if _text(row, "provider") != source.provider:
            raise ValidationError("Stored current-news provider identity is invalid")
        summary = _text(row, "summary", allow_empty=True)
    else:
        summary = ""
    sequence = row["version_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValidationError("Stored current-news version sequence is invalid")
    raw, normalized, precision, offset_status = _publication(row)
    capture_id = _text(row, "capture_id")
    membership_ids, memberships = _capture_memberships(
        connection,
        source,
        version_id,
        remaining=remaining_memberships,
    )
    if capture_id not in membership_ids:
        raise ValidationError("Stored current-news version lacks capture membership")
    return (
        {
            "source_id": source.source_id,
            "feed_id": source.source_id,
            "provider": source.provider,
            "article_id": article_id,
            "article_version_id": version_id,
            "headline": _text(row, "title"),
            "summary": summary,
            "symbols": _symbols(connection, source, row),
            "capture_id": capture_id,
            "available_at": _timestamp(row, "captured_at", "captured_precision"),
            "captured_at": _timestamp(row, "captured_at", "captured_precision"),
            "capture_membership_ids": membership_ids,
            "capture_memberships": memberships,
            "published_date_raw": raw,
            "published_normalized_at": normalized,
            "published_precision": precision,
            "published_offset_status": offset_status,
            "version_sequence": sequence,
            "supersedes_article_version_id": _optional_text(
                row, "supersedes_article_version_id"
            ),
        },
        len(membership_ids),
    )


class CurrentNewsToolRepository:
    """Read fixed current-news tool data through the quiet immutable gateway."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap):
            raise ValidationError("Current-news tool reader requires an explicit store map")
        if not isinstance(registry, Registry):
            raise ValidationError("Current-news tool reader requires a registry")
        self._store_map = store_map
        self._registry = registry
        self._require_datasets()

    def _require_datasets(self) -> None:
        datasets = {
            dataset.id: dataset
            for dataset in self._registry.datasets_for(StoreRole.NEWS.value)
        }
        for dataset_id in CURRENT_NEWS_TOOL_DATASET_IDS:
            dataset = datasets.get(dataset_id)
            if (
                dataset is None
                or dataset.store != StoreRole.NEWS.value
                or not dataset.active
            ):
                raise ValidationError("Current-news tool datasets are not registered")

    def source_status(
        self,
        source_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], ...]:
        """Return deterministic retained-outcome status for fixed current feeds."""

        sources = _selected_sources(source_ids)
        with quiet_immutable_read_connection(
            self._store_map,
            StoreRole.NEWS,
            expected_anchor="fmp_stock_latest_current_attempts",
        ) as connection:
            return tuple(_status_record(connection, source) for source in sources)

    def source_status_evidence(
        self,
        source_ids: tuple[str, ...] = (),
    ) -> tuple[dict[str, object], ...]:
        """Read latest retained evidence without scanning lifetime article totals.

        Data Status consumes only these fields. The full source-status tool
        keeps its counts and existing response contract.
        """

        sources = _selected_sources(source_ids)
        with quiet_immutable_read_connection(
            self._store_map,
            StoreRole.NEWS,
            expected_anchor="fmp_stock_latest_current_attempts",
        ) as connection:
            return tuple(
                {
                    "source_id": source.source_id,
                    "latest_outcome": _latest_outcome(
                        _source_latest_outcome(connection, source)
                    ),
                    "latest_successful_capture": _latest_capture(
                        _source_latest_capture(connection, source)
                    ),
                }
                for source in sources
            )

    def item_history(
        self,
        article_id: str,
        limit: int,
    ) -> dict[str, object] | None:
        """Return the bounded immutable version chain for one retained article."""

        normalized_article_id = _identifier(article_id, label="article_id")
        bounded_limit = _history_limit(limit)
        with quiet_immutable_read_connection(
            self._store_map,
            StoreRole.NEWS,
            expected_anchor="fmp_stock_latest_current_article_versions",
        ) as connection:
            matches = [
                (source, _history_count(connection, source, normalized_article_id))
                for source in _SOURCE_SPECS
            ]
            populated = [(source, count) for source, count in matches if count]
            if not populated:
                return None
            if len(populated) != 1:
                raise ConflictError("Current-news article identity is ambiguous")
            source, total_count = populated[0]
            rows = _history_rows(
                connection,
                source,
                normalized_article_id,
                bounded_limit,
            )
            if len(rows) != min(total_count, bounded_limit):
                raise ValidationError("Stored current-news version history is inconsistent")
            membership_budget = _MAX_CAPTURE_MEMBERSHIPS
            versions: list[dict[str, object]] = []
            for row in rows:
                version, used = _safe_version(
                    connection,
                    source,
                    row,
                    remaining_memberships=membership_budget,
                )
                if version["article_id"] != normalized_article_id:
                    raise ValidationError("Stored current-news article identity is invalid")
                membership_budget -= used
                versions.append(version)
        return {
            "article_id": normalized_article_id,
            "source_id": source.source_id,
            "feed_id": source.source_id,
            "provider": source.provider,
            "versions": tuple(versions),
            "total_version_count": total_count,
            "truncated": total_count > bounded_limit,
        }

    def article_version(
        self,
        article_version_id: str,
    ) -> dict[str, object] | None:
        """Return one safe immutable version for bounded event-impact analysis."""

        normalized_version_id = _identifier(
            article_version_id,
            label="article_version_id",
        )
        with quiet_immutable_read_connection(
            self._store_map,
            StoreRole.NEWS,
            expected_anchor="fmp_stock_latest_current_article_versions",
        ) as connection:
            matches: list[tuple[_SourceSpec, Mapping[str, object]]] = []
            for source in _SOURCE_SPECS:
                rows = _version_rows(connection, source, normalized_version_id)
                if rows:
                    matches.append((source, rows[0]))
            if not matches:
                return None
            if len(matches) != 1:
                raise ConflictError("Current-news article version identity is ambiguous")
            source, row = matches[0]
            version, _ = _safe_version(
                connection,
                source,
                row,
                remaining_memberships=_MAX_CAPTURE_MEMBERSHIPS,
            )
            return version


__all__ = (
    "CURRENT_NEWS_TOOL_DATASET_IDS",
    "CURRENT_NEWS_TOOL_SOURCE_IDS",
    "FMP_STOCK_LATEST_SOURCE_ID",
    "MAX_ITEM_HISTORY_LIMIT",
    "CurrentNewsToolRepository",
)
