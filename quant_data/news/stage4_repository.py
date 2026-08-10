"""Bounded, read-only Stage 4 news queries over the news store.

The repository is deliberately internal: callers provide a host-owned
StoreMap and a reviewed registry, never a database path, connection, or SQL
fragment. It selects immutable item versions with the shared mixed-precision
availability predicate, then applies the active/retracted state after
selection so a later retraction correctly hides prior content.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Hashable, Mapping, Sequence

from ..errors import ResourceLimitError, ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection
from ..temporal import DateOnlyPolicy, TemporalValue, availability_at_or_before


_MAX_CANDIDATE_ROWS = 200_000
_MAX_QUERY_LIMIT = 1_000
_MAX_SEARCH_TERMS = 8
_MAX_SEARCH_TERM_LENGTH = 64
_SEARCH_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_REQUIRED_DATASETS = frozenset(
    {
        "fixture.news.evidence",
        "fixture.news.items",
        "fixture.news.search_index",
    }
)


@dataclass(frozen=True, slots=True)
class NewsStage4Query:
    """A bounded latest or point-in-time canonical news selection."""

    source_name: str | None = None
    source_item_id: str | None = None
    as_of: str | TemporalValue | None = None
    date_only_policy: str | DateOnlyPolicy = DateOnlyPolicy.COMPLETED_DATE
    limit: int = 100
    include_retracted: bool = False

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_name, "source_name"),
            (self.source_item_id, "source_item_id"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValidationError(f"News query {label} must be a nonempty string")
        if self.source_name is not None:
            object.__setattr__(self, "source_name", self.source_name.strip())
        if self.source_item_id is not None:
            object.__setattr__(self, "source_item_id", self.source_item_id.strip())
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Unsupported date-only policy") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= _MAX_QUERY_LIMIT
        ):
            raise ResourceLimitError("News query limit must be between 1 and 1000")
        if not isinstance(self.include_retracted, bool):
            raise ValidationError("News query include_retracted must be boolean")
        if self.as_of is not None:
            cutoff = (
                self.as_of
                if isinstance(self.as_of, TemporalValue)
                else TemporalValue.parse(self.as_of, pointer="/as_of")
            )
            object.__setattr__(self, "as_of", cutoff)

    @property
    def cutoff(self) -> TemporalValue | None:
        return self.as_of if isinstance(self.as_of, TemporalValue) else None


def _availability(row: Mapping[str, Any]) -> TemporalValue:
    value = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
    if value.precision.value != str(row["available_precision"]):
        raise ValidationError("Stored news availability precision is inconsistent")
    return value


def _bounded(rows: Sequence[Mapping[str, Any]], *, message: str) -> list[Mapping[str, Any]]:
    if len(rows) > _MAX_CANDIDATE_ROWS:
        raise ResourceLimitError(message)
    return list(rows)


def _query_rows(
    connection: Any,
    query: NewsStage4Query,
) -> list[Mapping[str, Any]]:
    clauses: list[str] = []
    parameters: list[object] = []
    if query.source_name is not None:
        clauses.append("item.source_name=?")
        parameters.append(query.source_name)
    if query.source_item_id is not None:
        clauses.append("item.source_item_id=?")
        parameters.append(query.source_item_id)
    where = " AND ".join(clauses) if clauses else "1=1"
    parameters.append(_MAX_CANDIDATE_ROWS + 1)
    return _bounded(
        list(
            connection.execute(
                f"""
                SELECT version.rowid AS version_rowid,
                       version.news_item_version_id, version.item_id,
                       item.source_name, item.source_item_id, item.source_kind,
                       version.content_identity, version.headline, version.body,
                       version.summary, version.source_url, version.published_at,
                       version.published_precision, version.content_state,
                       version.content_missing_reason, version.item_state,
                       version.retraction_reason, version.available_at,
                       version.available_precision, version.captured_at,
                       version.captured_precision, version.version_sequence,
                       version.supersedes_news_item_version_id,
                       version.source_snapshot_id, version.run_id, version.source_row
                FROM news_item_versions AS version
                JOIN news_items AS item ON item.item_id=version.item_id
                WHERE {where}
                ORDER BY item.source_name, item.source_item_id, version.version_sequence
                LIMIT ?
                """,
                tuple(parameters),
            )
        ),
        message="News version history exceeds the bounded query contract",
    )


def _select_versions(
    rows: Sequence[Mapping[str, Any]],
    *,
    query: NewsStage4Query,
) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    grouped: dict[Hashable, list[Mapping[str, Any]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in rows:
        if query.cutoff is not None:
            decision = availability_at_or_before(
                _availability(row),
                query.cutoff,
                query.date_only_policy,
            )
            warnings.update(decision.warnings)
            if not decision.included:
                continue
        grouped[str(row["item_id"])].append(row)
    selected = [
        max(
            candidates,
            key=lambda row: (
                int(row["version_sequence"]),
                str(row["available_at"]),
                str(row["news_item_version_id"]),
            ),
        )
        for candidates in grouped.values()
    ]
    if not query.include_retracted:
        selected = [
            row
            for row in selected
            if str(row["item_state"]) == "active"
        ]
    selected.sort(
        key=lambda row: (
            str(row["source_name"]),
            str(row["source_item_id"]),
            int(row["version_sequence"]),
        )
    )
    if len(selected) > query.limit:
        raise ResourceLimitError("News query result exceeds the requested limit")
    return selected, tuple(sorted(warnings))


def _label_rows(
    connection: Any,
    *,
    relation: str,
    columns: str,
    version_id: str,
) -> list[Mapping[str, Any]]:
    return _bounded(
        list(
            connection.execute(
                f"""
                SELECT {columns}
                FROM {relation}
                WHERE news_item_version_id=?
                ORDER BY source_row
                LIMIT ?
                """,
                (version_id, _MAX_CANDIDATE_ROWS + 1),
            )
        ),
        message="News label history exceeds the bounded query contract",
    )


def _labels(connection: Any, version_id: str) -> dict[str, tuple[dict[str, Any], ...]]:
    symbols = _label_rows(
        connection,
        relation="news_item_symbols",
        columns="""
            news_item_symbol_id, instrument_id, provider, provider_symbol,
            association_state, missing_reason, available_at, available_precision,
            source_snapshot_id, run_id, source_row
        """,
        version_id=version_id,
    )
    topics = _label_rows(
        connection,
        relation="news_item_topics",
        columns="""
            news_item_topic_id, topic, confidence, association_state, missing_reason,
            available_at, available_precision, source_snapshot_id, run_id, source_row
        """,
        version_id=version_id,
    )
    geographies = _label_rows(
        connection,
        relation="news_item_geographies",
        columns="""
            news_item_geography_id, geography_code, association_state,
            missing_reason, available_at, available_precision, source_snapshot_id,
            run_id, source_row
        """,
        version_id=version_id,
    )
    coverage_lanes = _label_rows(
        connection,
        relation="news_item_coverage_lanes",
        columns="""
            news_item_coverage_lane_id, coverage_lane, association_state,
            missing_reason, available_at, available_precision, source_snapshot_id,
            run_id, source_row
        """,
        version_id=version_id,
    )
    return {
        "symbols": tuple(
            {
                "news_item_symbol_id": str(row["news_item_symbol_id"]),
                "instrument_id": str(row["instrument_id"]) if row["instrument_id"] is not None else None,
                "provider": str(row["provider"]) if row["provider"] is not None else None,
                "provider_symbol": str(row["provider_symbol"]) if row["provider_symbol"] is not None else None,
                "association_state": str(row["association_state"]),
                "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "run_id": str(row["run_id"]),
                "source_row": int(row["source_row"]),
            }
            for row in symbols
        ),
        "topics": tuple(
            {
                "news_item_topic_id": str(row["news_item_topic_id"]),
                "topic": str(row["topic"]) if row["topic"] is not None else None,
                "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
                "association_state": str(row["association_state"]),
                "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "run_id": str(row["run_id"]),
                "source_row": int(row["source_row"]),
            }
            for row in topics
        ),
        "geographies": tuple(
            {
                "news_item_geography_id": str(row["news_item_geography_id"]),
                "geography_code": str(row["geography_code"]) if row["geography_code"] is not None else None,
                "association_state": str(row["association_state"]),
                "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "run_id": str(row["run_id"]),
                "source_row": int(row["source_row"]),
            }
            for row in geographies
        ),
        "coverage_lanes": tuple(
            {
                "news_item_coverage_lane_id": str(row["news_item_coverage_lane_id"]),
                "coverage_lane": str(row["coverage_lane"]) if row["coverage_lane"] is not None else None,
                "association_state": str(row["association_state"]),
                "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "run_id": str(row["run_id"]),
                "source_row": int(row["source_row"]),
            }
            for row in coverage_lanes
        ),
    }


def _render_item(
    connection: Any,
    row: Mapping[str, Any],
    *,
    warnings: tuple[str, ...],
) -> dict[str, Any]:
    published_precision = str(row["published_precision"])
    if published_precision == "unknown" and row["published_at"] is not None:
        raise ValidationError("Stored unknown news publication has a value")
    if published_precision != "unknown" and row["published_at"] is None:
        raise ValidationError("Stored precise news publication is missing its value")
    version_id = str(row["news_item_version_id"])
    return {
        "news_item_version_id": version_id,
        "item_id": str(row["item_id"]),
        "source_name": str(row["source_name"]),
        "source_item_id": str(row["source_item_id"]),
        "source_kind": str(row["source_kind"]),
        "content_identity": str(row["content_identity"]),
        "headline": str(row["headline"]) if row["headline"] is not None else None,
        "body": str(row["body"]) if row["body"] is not None else None,
        "summary": str(row["summary"]) if row["summary"] is not None else None,
        "source_url": str(row["source_url"]) if row["source_url"] is not None else None,
        "published_at": str(row["published_at"]) if row["published_at"] is not None else None,
        "published_precision": published_precision,
        "content_state": str(row["content_state"]),
        "content_missing_reason": (
            str(row["content_missing_reason"])
            if row["content_missing_reason"] is not None
            else None
        ),
        "item_state": str(row["item_state"]),
        "retraction_reason": (
            str(row["retraction_reason"])
            if row["retraction_reason"] is not None
            else None
        ),
        "available_at": str(row["available_at"]),
        "available_precision": str(row["available_precision"]),
        "captured_at": str(row["captured_at"]),
        "captured_precision": str(row["captured_precision"]),
        "version_sequence": int(row["version_sequence"]),
        "supersedes_news_item_version_id": (
            str(row["supersedes_news_item_version_id"])
            if row["supersedes_news_item_version_id"] is not None
            else None
        ),
        "source_snapshot_id": str(row["source_snapshot_id"]),
        "run_id": str(row["run_id"]),
        "source_row": int(row["source_row"]),
        "labels": _labels(connection, version_id),
        "warnings": warnings,
    }


def _search_expression(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("News search text must contain one or more safe terms")
    terms = value.split()
    if len(terms) > _MAX_SEARCH_TERMS:
        raise ResourceLimitError("News search has too many terms")
    if any(
        len(term) > _MAX_SEARCH_TERM_LENGTH
        or _SEARCH_TERM.fullmatch(term) is None
        for term in terms
    ):
        raise ValidationError("News search terms may contain only letters, digits, hyphen, and underscore")
    return " AND ".join('"' + term + '"' for term in terms)


class NewsStage4Repository:
    """Read registered Stage 4 news facts without mutating the news store."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        dataset_ids = {
            item.id for item in registry.datasets_for(StoreRole.NEWS.value)
        }
        if not _REQUIRED_DATASETS.issubset(dataset_ids):
            raise ValidationError("Stage 4 news repository is not bound to the frozen catalog datasets")

    @staticmethod
    def _selection(
        connection: Any,
        query: NewsStage4Query,
    ) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
        return _select_versions(_query_rows(connection, query), query=query)

    def get_items(self, query: NewsStage4Query) -> tuple[dict[str, Any], ...]:
        with read_connection(self._store_map, StoreRole.NEWS) as connection:
            selected, warnings = self._selection(connection, query)
            return tuple(
                _render_item(connection, row, warnings=warnings)
                for row in selected
            )

    def search(
        self,
        query: NewsStage4Query,
        text: str,
    ) -> tuple[dict[str, Any], ...]:
        """Run bounded, syntax-restricted FTS and reapply canonical selection.

        The FTS index is a derived candidate accelerator only. A result is
        returned only when its immutable version is the selected latest/as-of
        canonical version and remains active under the caller's policy.
        """

        expression = _search_expression(text)
        with read_connection(self._store_map, StoreRole.NEWS) as connection:
            selected, warnings = self._selection(connection, query)
            eligible = {
                str(row["news_item_version_id"]): row for row in selected
            }
            if not eligible:
                return ()
            matches = _bounded(
                list(
                    connection.execute(
                        """
                        SELECT version.news_item_version_id
                        FROM news_item_versions_fts
                        JOIN news_item_versions AS version
                          ON version.rowid=news_item_versions_fts.rowid
                        WHERE news_item_versions_fts MATCH ?
                        ORDER BY bm25(news_item_versions_fts),
                                 version.news_item_version_id
                        LIMIT ?
                        """,
                        (expression, _MAX_CANDIDATE_ROWS + 1),
                    )
                ),
                message="News full-text candidate set exceeds the bounded query contract",
            )
            ordered = [
                eligible[str(row["news_item_version_id"])]
                for row in matches
                if str(row["news_item_version_id"]) in eligible
            ]
            if len(ordered) > query.limit:
                raise ResourceLimitError("News search result exceeds the requested limit")
            return tuple(
                _render_item(connection, row, warnings=warnings)
                for row in ordered
            )
