"""Bounded, immutable reads of the repeatable FMP current-news feed.

This gateway is deliberately separate from the frozen Stage 4 fixture-news
reader. It reads only the forward, FMP stock-latest current-feed relations
through the quiet immutable reader and projects headline metadata; it never
returns body text or retained response bytes.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..errors import ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


CURRENT_NEWS_EVIDENCE_DATASET_ID = "news.fmp.stock_latest_current_evidence"
CURRENT_NEWS_ARTICLES_DATASET_ID = "news.fmp.stock_latest_current_articles"
CURRENT_NEWS_DATASET_IDS = (
    CURRENT_NEWS_EVIDENCE_DATASET_ID,
    CURRENT_NEWS_ARTICLES_DATASET_ID,
)
_CURRENT_ARTICLES_TABLE = "fmp_stock_latest_current_articles"
_MAX_CANDIDATE_ROWS = 200_000
_MAX_QUERY_LENGTH = 500
_MAX_SYMBOLS = 50
_MAX_SYMBOL_LENGTH = 64
_MAX_LIMIT = 500
_PUBLISHED_PRECISIONS = frozenset(
    {
        "date",
        "datetime_naive",
        "datetime_offset",
        "missing",
        "unknown",
    }
)
_PUBLISHED_OFFSET_STATUSES = frozenset({"known", "missing", "unknown"})


def _text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Current-news {label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValidationError(f"Current-news {label} is invalid")
    return normalized


def _optional_date(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("Current-news date bounds must be calendar dates")
    return parse_date(value, pointer=pointer).isoformat()


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResourceLimitError("Current-news limit must be an integer")
    if not 1 <= value <= _MAX_LIMIT:
        raise ResourceLimitError(
            f"Current-news limit must be from 1 through {_MAX_LIMIT}"
        )
    return value


def _symbols(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("Current-news symbols must be an array of symbols")
    if not value or len(value) > _MAX_SYMBOLS:
        raise ResourceLimitError(
            f"Current-news symbols must contain 1 through {_MAX_SYMBOLS} values"
        )
    normalized = tuple(
        _text(item, label="symbol", maximum=_MAX_SYMBOL_LENGTH) for item in value
    )
    if len({item.casefold() for item in normalized}) != len(normalized):
        raise ValidationError("Current-news symbols must be distinct")
    return normalized


@dataclass(frozen=True, slots=True)
class CurrentNewsQuery:
    """Validated fixed-shape selection of retained current FMP headlines."""

    query: str = ""
    symbols: tuple[str, ...] | list[str] | None = None
    mode: str = "latest"
    as_of: str | None = None
    date_only_policy: DateOnlyPolicy | str = DateOnlyPolicy.COMPLETED_DATE
    start_date: str | None = None
    end_date: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or len(self.query) > _MAX_QUERY_LENGTH:
            raise ValidationError(
                "Current-news query must be text no longer than 500 characters"
            )
        if not isinstance(self.mode, str) or self.mode not in {"latest", "as_of"}:
            raise ValidationError("Current-news mode is unsupported")
        if self.mode == "as_of":
            if not isinstance(self.as_of, str) or not self.as_of:
                raise ValidationError("Current-news as_of mode requires a cutoff")
            TemporalValue.parse(self.as_of, pointer="/as_of")
        elif self.as_of is not None:
            raise ValidationError("Current-news latest mode does not accept a cutoff")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Current-news date-only policy is unsupported") from exc
        start = _optional_date(self.start_date, pointer="/start_date")
        end = _optional_date(self.end_date, pointer="/end_date")
        if start is not None and end is not None and end < start:
            raise ValidationError("Current-news end_date cannot precede start_date")
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "symbols", _symbols(self.symbols))
        object.__setattr__(self, "date_only_policy", policy)
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "limit", _limit(self.limit))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "CurrentNewsQuery":
        if not isinstance(value, Mapping):
            raise ValidationError("Current-news arguments must be a mapping")
        allowed = {
            "query",
            "symbols",
            "mode",
            "as_of",
            "date_only_policy",
            "start_date",
            "end_date",
            "limit",
        }
        if set(value) - allowed:
            raise ValidationError("Current-news arguments contain an unsupported field")
        return cls(
            query=value.get("query", ""),
            symbols=value.get("symbols"),
            mode=value.get("mode", "latest"),
            as_of=value.get("as_of"),
            date_only_policy=value.get(
                "date_only_policy", DateOnlyPolicy.COMPLETED_DATE
            ),
            start_date=value.get("start_date"),
            end_date=value.get("end_date"),
            limit=value.get("limit", 100),
        )

    @property
    def cutoff(self) -> TemporalValue | None:
        if self.as_of is None:
            return None
        return TemporalValue.parse(self.as_of, pointer="/as_of")

    def receipt_mapping(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "date_only_policy": self.date_only_policy.value,
            "end_date": self.end_date,
            "limit": self.limit,
            "mode": self.mode,
            "query": self.query,
            "start_date": self.start_date,
            "symbols": list(self.symbols) if self.symbols is not None else None,
        }


@dataclass(frozen=True, slots=True)
class CurrentNewsSelection:
    """A deterministic, bounded current-news selection and its receipt."""

    records: tuple[dict[str, object], ...]
    total_selected_count: int
    truncated: bool
    migration_ids: tuple[str, ...]
    receipt_sha256: str
    warnings: tuple[str, ...] = ()
    dataset_ids: tuple[str, str] = CURRENT_NEWS_DATASET_IDS


def _migration_receipt(connection: Any) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    rows = list(
        connection.execute(
            "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
        )
    )
    return (
        tuple(str(row["migration_id"]) for row in rows),
        [
            {
                "migration_id": str(row["migration_id"]),
                "sha256": str(row["sha256"]),
            }
            for row in rows
        ],
    )


def _capture_temporal(row: Mapping[str, object]) -> TemporalValue:
    value = TemporalValue.parse(
        str(row["captured_at"]), pointer="/stored/captured_at"
    )
    if value.precision is not TemporalPrecision.DATETIME:
        raise ValidationError(
            "Current-news capture availability must be an aware datetime"
        )
    return value


def _published(
    row: Mapping[str, object],
) -> tuple[str | None, str | None, str, str, datetime | None, str | None]:
    raw = (
        None
        if row["published_date_raw"] is None
        else str(row["published_date_raw"])
    )
    normalized = (
        None
        if row["published_normalized_at"] is None
        else str(row["published_normalized_at"])
    )
    precision = str(row["published_precision"])
    offset_status = str(row["published_offset_status"])
    if precision not in _PUBLISHED_PRECISIONS:
        raise ValidationError("Stored current-news publication precision is invalid")
    if offset_status not in _PUBLISHED_OFFSET_STATUSES:
        raise ValidationError(
            "Stored current-news publication offset status is invalid"
        )
    if precision == "datetime_offset":
        if raw is None or normalized is None or offset_status != "known":
            raise ValidationError("Stored offset publication metadata is inconsistent")
        normalized_temporal = TemporalValue.parse(
            normalized, pointer="/stored/published_normalized_at"
        )
        if normalized_temporal.precision is not TemporalPrecision.DATETIME:
            raise ValidationError(
                "Stored normalized current-news publication is invalid"
            )
        assert isinstance(normalized_temporal.value, datetime)
        publication_instant = normalized_temporal.value.astimezone(timezone.utc)
    else:
        if normalized is not None:
            raise ValidationError(
                "Stored imprecise current-news publication has a normalized timestamp"
            )
        if precision == "missing":
            if raw is not None or offset_status not in {"missing", "unknown"}:
                raise ValidationError(
                    "Stored missing current-news publication is inconsistent"
                )
        elif raw is None or offset_status != "unknown":
            raise ValidationError("Stored current-news publication metadata is inconsistent")
        publication_instant = None
    calendar_date: str | None = None
    if raw is not None:
        try:
            calendar_date = parse_date(
                raw[:10], pointer="/stored/published_date"
            ).isoformat()
        except ValidationError:
            if precision in {"date", "datetime_offset", "datetime_naive"}:
                raise ValidationError(
                    "Stored current-news publication date is invalid"
                ) from None
    return raw, normalized, precision, offset_status, publication_instant, calendar_date


_KEYSET_MISSING = object()


@dataclass(frozen=True, slots=True)
class CurrentNewsKeysetAnchor:
    """Strict continuation key for the stable current-news newest-first order."""

    publication_instant: datetime | None
    captured_at: datetime
    article_id: str

    def __post_init__(self) -> None:
        publication = self.publication_instant
        if publication is not None:
            publication = self._utc_datetime(
                publication,
                label="publication instant",
            )
        captured = self._utc_datetime(self.captured_at, label="capture availability")
        if not isinstance(self.article_id, str) or not self.article_id:
            raise ValidationError("Current-news keyset article identity is invalid")
        object.__setattr__(self, "publication_instant", publication)
        object.__setattr__(self, "captured_at", captured)

    @staticmethod
    def _utc_datetime(value: object, *, label: str) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError(f"Current-news keyset {label} is invalid")
        return value.astimezone(timezone.utc)

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> "CurrentNewsKeysetAnchor":
        """Build an anchor from a safe repository record or its internal form."""

        if not isinstance(record, Mapping):
            raise ValidationError("Current-news keyset record is invalid")

        captured = record.get("_captured_instant", _KEYSET_MISSING)
        if captured is _KEYSET_MISSING:
            available_at = record.get("available_at", _KEYSET_MISSING)
            if not isinstance(available_at, str):
                raise ValidationError("Current-news keyset availability is invalid")
            temporal = TemporalValue.parse(available_at, pointer="/available_at")
            if temporal.precision is not TemporalPrecision.DATETIME:
                raise ValidationError("Current-news keyset availability is invalid")
            assert isinstance(temporal.value, datetime)
            captured = temporal.value

        publication = record.get("_publication_instant", _KEYSET_MISSING)
        if publication is _KEYSET_MISSING:
            publication = record.get("publication_instant", _KEYSET_MISSING)
        if publication is _KEYSET_MISSING:
            precision = record.get("published_precision", _KEYSET_MISSING)
            normalized = record.get("published_normalized_at", _KEYSET_MISSING)
            if not isinstance(precision, str) or precision not in _PUBLISHED_PRECISIONS:
                raise ValidationError("Current-news keyset publication is invalid")
            if precision == "datetime_offset":
                if not isinstance(normalized, str):
                    raise ValidationError("Current-news keyset publication is invalid")
                temporal = TemporalValue.parse(
                    normalized,
                    pointer="/published_normalized_at",
                )
                if temporal.precision is not TemporalPrecision.DATETIME:
                    raise ValidationError("Current-news keyset publication is invalid")
                assert isinstance(temporal.value, datetime)
                publication = temporal.value
            else:
                if normalized is not None:
                    raise ValidationError("Current-news keyset publication is invalid")
                publication = None

        return cls(
            publication_instant=publication,
            captured_at=captured,
            article_id=record.get("article_id"),
        )

    def receipt_mapping(self) -> dict[str, str | None]:
        return {
            "article_id": self.article_id,
            "captured_at": self.captured_at.isoformat().replace("+00:00", "Z"),
            "publication_instant": (
                None
                if self.publication_instant is None
                else self.publication_instant.isoformat().replace("+00:00", "Z")
            ),
        }


def record_is_after_current_news_anchor(
    record: Mapping[str, object],
    after: CurrentNewsKeysetAnchor,
) -> bool:
    """Return whether one record follows ``after`` in current-news sort order."""

    if not isinstance(after, CurrentNewsKeysetAnchor):
        raise ValidationError("Current-news keyset anchor is invalid")
    candidate = CurrentNewsKeysetAnchor.from_record(record)
    if candidate.publication_instant is None:
        if after.publication_instant is not None:
            return True
    elif after.publication_instant is None:
        return False
    elif candidate.publication_instant != after.publication_instant:
        return candidate.publication_instant < after.publication_instant
    if candidate.captured_at != after.captured_at:
        return candidate.captured_at < after.captured_at
    return candidate.article_id > after.article_id


def _required_row_text(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Stored current-news {name} is invalid")
    return value


def _query_rows(connection: Any) -> list[Mapping[str, object]]:
    try:
        rows = list(
            connection.execute(
                """
                SELECT article.article_id, article.site, article.source_url,
                       article.symbol, article.created_capture_id,
                       version.article_version_id,
                       version.article_id AS version_article_id,
                       version.title, version.published_date_raw,
                       version.published_normalized_at, version.published_precision,
                       version.published_offset_status, version.version_sequence,
                       version.supersedes_article_version_id, version.capture_id,
                       version.source_row, capture.captured_at,
                       membership.source_row AS membership_source_row
                FROM fmp_stock_latest_current_article_versions AS version
                JOIN fmp_stock_latest_current_articles AS article
                  ON article.article_id=version.article_id
                JOIN fmp_stock_latest_current_captures AS capture
                  ON capture.capture_id=version.capture_id
                JOIN fmp_stock_latest_current_capture_articles AS membership
                  ON membership.capture_id=version.capture_id
                 AND membership.article_version_id=version.article_version_id
                 AND membership.source_row=version.source_row
                ORDER BY article.article_id, version.version_sequence,
                         version.article_version_id
                LIMIT ?
                """,
                (_MAX_CANDIDATE_ROWS + 1,),
            )
        )
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Current FMP news store is unavailable") from exc
    if len(rows) > _MAX_CANDIDATE_ROWS:
        raise ResourceLimitError(
            "Current-news version history exceeds the bounded query contract"
        )
    return rows


def _version_rank(row: Mapping[str, object]) -> tuple[int, datetime, str]:
    sequence = row["version_sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValidationError("Stored current-news version sequence is invalid")
    captured = _capture_temporal(row)
    assert isinstance(captured.value, datetime)
    return sequence, captured.value.astimezone(timezone.utc), _required_row_text(
        row, "article_version_id"
    )


def _record(row: Mapping[str, object]) -> dict[str, object]:
    article_id = _required_row_text(row, "article_id")
    if row["version_article_id"] != article_id:
        raise ValidationError(
            "Stored current-news article/version identity is inconsistent"
        )
    source_row = row["source_row"]
    if (
        isinstance(source_row, bool)
        or not isinstance(source_row, int)
        or source_row < 1
        or row["membership_source_row"] != source_row
    ):
        raise ValidationError("Stored current-news capture membership is inconsistent")
    raw, normalized, precision, offset_status, publication_instant, calendar_date = (
        _published(row)
    )
    captured = _capture_temporal(row)
    sequence, _, article_version_id = _version_rank(row)
    return {
        "article_id": article_id,
        "article_version_id": article_version_id,
        "available_at": str(row["captured_at"]),
        "capture_id": _required_row_text(row, "capture_id"),
        "headline": _required_row_text(row, "title"),
        "published_date_raw": raw,
        "published_normalized_at": normalized,
        "published_offset_status": offset_status,
        "published_precision": precision,
        "provider_calendar_date": calendar_date,
        "publication_instant": publication_instant,
        "source_name": _required_row_text(row, "site"),
        "source_row": source_row,
        "source_url": _required_row_text(row, "source_url"),
        "symbol": _required_row_text(row, "symbol"),
        "version_sequence": sequence,
        "_captured_instant": captured.value.astimezone(timezone.utc),
    }


def _matches(record: Mapping[str, object], query: CurrentNewsQuery) -> bool:
    if query.symbols is not None:
        symbols = {symbol.casefold() for symbol in query.symbols}
        if str(record["symbol"]).casefold() not in symbols:
            return False
    if query.query:
        needle = query.query.casefold()
        if not any(
            needle in str(record[name]).casefold()
            for name in ("symbol", "source_name", "headline", "source_url")
        ):
            return False
    provider_date = record["provider_calendar_date"]
    if query.start_date is not None and (
        provider_date is None or str(provider_date) < query.start_date
    ):
        return False
    if query.end_date is not None and (
        provider_date is None or str(provider_date) > query.end_date
    ):
        return False
    return True


def _sort(records: list[dict[str, object]]) -> None:
    records.sort(key=lambda row: str(row["article_id"]))
    records.sort(key=lambda row: row["_captured_instant"], reverse=True)
    records.sort(
        key=lambda row: (
            row["publication_instant"] is not None,
            row["publication_instant"]
            if row["publication_instant"] is not None
            else datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )


class CurrentNewsRepository:
    """Read registered, repeatable FMP current-news relations without writes."""

    def __init__(self, store_map: StoreMap, registry: Registry | None = None) -> None:
        if not isinstance(store_map, StoreMap):
            raise ValidationError(
                "Current-news reader requires an explicit store map"
            )
        if registry is not None and not isinstance(registry, Registry):
            raise ValidationError("Current-news reader registry is invalid")
        self._store_map = store_map
        self._registry = registry

    def _require_datasets(self) -> None:
        if self._registry is None:
            return
        datasets = {
            item.id: item
            for item in self._registry.datasets_for(StoreRole.NEWS.value)
        }
        if any(
            dataset_id not in datasets
            or datasets[dataset_id].store != StoreRole.NEWS.value
            for dataset_id in CURRENT_NEWS_DATASET_IDS
        ):
            raise ValidationError("Current FMP news datasets are not registered")

    def search(
        self,
        query: CurrentNewsQuery,
        *,
        after: CurrentNewsKeysetAnchor | None = None,
    ) -> CurrentNewsSelection:
        if not isinstance(query, CurrentNewsQuery):
            raise ValidationError("Current-news selection requires a typed query")
        if after is not None and not isinstance(after, CurrentNewsKeysetAnchor):
            raise ValidationError("Current-news selection anchor is invalid")
        self._require_datasets()
        with quiet_immutable_read_connection(
            self._store_map,
            StoreRole.NEWS,
            expected_anchor=_CURRENT_ARTICLES_TABLE,
        ) as connection:
            rows = _query_rows(connection)
            migration_ids, migrations = _migration_receipt(connection)
        cutoff = query.cutoff
        warnings: set[str] = set()
        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for row in rows:
            if cutoff is not None:
                decision = availability_at_or_before(
                    _capture_temporal(row), cutoff, query.date_only_policy
                )
                warnings.update(decision.warnings)
                if not decision.included:
                    continue
            grouped[_required_row_text(row, "article_id")].append(row)
        selected = [
            _record(max(rows_for_article, key=_version_rank))
            for rows_for_article in grouped.values()
        ]
        filtered = [record for record in selected if _matches(record, query)]
        _sort(filtered)
        if after is not None:
            filtered = [
                record
                for record in filtered
                if record_is_after_current_news_anchor(record, after)
            ]
        total = len(filtered)
        rendered: list[dict[str, object]] = []
        for record in filtered[: query.limit]:
            rendered.append(
                {
                    name: value
                    for name, value in record.items()
                    if name
                    not in {
                        "provider_calendar_date",
                        "publication_instant",
                        "_captured_instant",
                    }
                }
            )
        receipt = {
            "dataset_ids": list(CURRENT_NEWS_DATASET_IDS),
            "migrations": migrations,
            "request": query.receipt_mapping(),
            "selected_article_versions": [
                str(record["article_version_id"]) for record in rendered
            ],
            "total_selected_count": total,
            "truncated": total > query.limit,
            "warnings": sorted(warnings),
        }
        if after is not None:
            receipt["after"] = after.receipt_mapping()
        return CurrentNewsSelection(
            records=tuple(rendered),
            total_selected_count=total,
            truncated=total > query.limit,
            migration_ids=migration_ids,
            receipt_sha256=hashlib.sha256(
                dumps_strict(receipt).encode("utf-8")
            ).hexdigest(),
            warnings=tuple(sorted(warnings)),
        )


__all__ = (
    "CURRENT_NEWS_ARTICLES_DATASET_ID",
    "CURRENT_NEWS_DATASET_IDS",
    "CURRENT_NEWS_EVIDENCE_DATASET_ID",
    "CurrentNewsKeysetAnchor",
    "CurrentNewsQuery",
    "CurrentNewsRepository",
    "CurrentNewsSelection",
    "record_is_after_current_news_anchor",
)
