"""Bounded SQL selection over the existing retained-news indexes.

Choose the latest cutoff-eligible version independently of ticker/date/text
filters. In particular, an old version cannot reappear just because a later
version changed its ticker associations or publication date.
"""
from functools import lru_cache
import sqlite3
from datetime import timezone

from ..errors import ResourceLimitError, StoreUnavailableError
from ..temporal import TemporalValue, availability_at_or_before


def select_news_rows(connection, query, *, multi_source, published, source_ids=(), after=None):
    prefix = "current_multi_source" if multi_source else "fmp_stock_latest_current"
    failures = []
    cutoff = query.cutoff

    def checked(function):
        def call(*args):
            try:
                return function(*args)
            except Exception as exc:
                failures.append(exc)
                raise
        return call

    @lru_cache(maxsize=4096)
    def instant(value):
        if value is None:
            return None
        return TemporalValue.parse(value).value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @lru_cache(maxsize=4096)
    def eligible(value):
        if cutoff is None:
            return 1
        return int(availability_at_or_before(
            TemporalValue.parse(value), cutoff, query.date_only_policy).included)

    @lru_cache(maxsize=8192)
    def publication_date(raw, normalized, precision, offset_status):
        return published(dict(published_date_raw=raw, published_normalized_at=normalized,
                              published_precision=precision, published_offset_status=offset_status))[-1]

    functions = {
        "news_instant": (1, instant),
        "news_eligible": (1, eligible),
        "news_publication_date": (4, publication_date),
        "news_casefold": (1, lambda value: value.casefold()),
        "news_contains": (-1, lambda *values: int(any(
            query.query.casefold() in str(value or "").casefold() for value in values))),
    }
    for name, (arity, function) in functions.items():
        connection.create_function(name, arity, checked(function), deterministic=True)

    parameters = []
    clauses = []
    if multi_source:
        fields = """article.feed_id, article.provider, article.source_item_key,
            version.summary, version.site, version.source_url"""
        if source_ids:
            clauses.append("article.feed_id IN (" + ",".join("?" for _ in source_ids) + ")")
            parameters.extend(source_ids)
        if query.symbols is not None:
            # The generic publisher admits ASCII uppercase symbols only.
            symbols = tuple(s.casefold().upper() for s in query.symbols if s.casefold().isascii())
            if symbols:
                clauses.append("""version.article_version_id IN (
                    SELECT article_version_id FROM current_multi_source_article_symbols
                    WHERE provider_symbol IN (""" + ",".join("?" for _ in symbols) + "))")
                parameters.extend(symbols)
            else:
                clauses.append("0")
    else:
        fields = "article.site, article.source_url, article.symbol"
        if query.symbols is not None:
            clauses.append("news_casefold(article.symbol) IN (" + ",".join("?" for _ in query.symbols) + ")")
            parameters.extend(s.casefold() for s in query.symbols)

    # The unique (article_id, version_sequence) index determines the winner.
    # Its cutoff and membership predicates intentionally do not inherit filters.
    clauses.append(f"""version.article_version_id = (
        SELECT newer.article_version_id
        FROM {prefix}_article_versions AS newer
        JOIN {prefix}_captures AS newer_capture ON newer_capture.capture_id=newer.capture_id
        JOIN {prefix}_capture_articles AS newer_membership
          ON newer_membership.capture_id=newer.capture_id
         AND newer_membership.source_row=newer.source_row
         AND newer_membership.article_version_id=newer.article_version_id
        WHERE newer.article_id=article.article_id
          AND news_eligible(newer_capture.captured_at)
        ORDER BY newer.version_sequence DESC LIMIT 1)""")

    date_expression = """news_publication_date(version.published_date_raw,
        version.published_normalized_at, version.published_precision, version.published_offset_status)"""
    for bound, operator in ((query.start_date, ">="), (query.end_date, "<=")):
        if bound is not None:
            clauses.append(date_expression + operator + "?")
            parameters.append(bound)
    if query.query:
        if multi_source:
            clauses.append("""news_contains(version.title, version.summary, version.site,
                version.source_url, article.provider, article.feed_id,
                (SELECT group_concat(provider_symbol, ' ') FROM (
                    SELECT provider_symbol FROM current_multi_source_article_symbols
                    WHERE article_version_id=version.article_version_id
                    ORDER BY provider_symbol COLLATE BINARY)))""")
        else:
            clauses.append("news_contains(article.symbol, article.site, version.title, article.source_url)")

    page_clause = ""
    if after is not None:
        capture = after.captured_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        tie = "(captured_key<? OR (captured_key=? AND article_id COLLATE BINARY>?))"
        if after.publication_instant is None:
            page_clause = "WHERE publication_key IS NULL AND " + tie
            parameters.extend((capture, capture, after.article_id))
        else:
            publication = after.publication_instant.astimezone(timezone.utc).isoformat(timespec="microseconds")
            page_clause = "WHERE publication_key IS NULL OR publication_key<? OR (publication_key=? AND " + tie + ")"
            parameters.extend((publication, publication, capture, capture, after.article_id))
    statement = f"""
        WITH selected AS MATERIALIZED (
            SELECT article.article_id, article.created_capture_id, {fields},
                   version.article_version_id, version.article_id AS version_article_id,
                   version.title, version.published_date_raw, version.published_normalized_at,
                   version.published_precision, version.published_offset_status,
                   version.version_sequence, version.supersedes_article_version_id,
                   version.capture_id, version.source_row, capture.captured_at,
                   membership.source_row AS membership_source_row,
                   news_instant(version.published_normalized_at) AS publication_key,
                   news_instant(capture.captured_at) AS captured_key
            FROM {prefix}_articles AS article
            JOIN {prefix}_article_versions AS version ON version.article_id=article.article_id
            JOIN {prefix}_captures AS capture ON capture.capture_id=version.capture_id
            JOIN {prefix}_capture_articles AS membership
              ON membership.capture_id=version.capture_id
             AND membership.article_version_id=version.article_version_id
             AND membership.source_row=version.source_row
            WHERE {" AND ".join(clauses)}
        )
        SELECT *, COUNT(*) OVER () AS total_selected_count FROM selected
        {page_clause}
        ORDER BY publication_key IS NOT NULL DESC, publication_key DESC,
                 captured_key DESC, article_id COLLATE BINARY
        LIMIT ?
    """
    parameters.append(query.limit)
    try:
        rows = list(connection.execute(statement, parameters))
    except sqlite3.Error as exc:
        if failures:
            raise failures[0]
        raise StoreUnavailableError("Current news selection is unavailable") from exc
    finally:
        for name, (arity, _) in functions.items():
            connection.create_function(name, arity, None)
    total = rows[0]["total_selected_count"] if rows else 0
    if total > 200_000:
        raise ResourceLimitError("Matching news selection exceeds the bounded query contract")
    return rows, total
