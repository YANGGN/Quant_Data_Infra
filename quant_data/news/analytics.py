"""Small deterministic analytics over retained public news metadata.

These helpers operate only on bounded in-memory headline records.  They do
not read article bodies or raw provider evidence, make no instrument-identity
or named-entity claims, and are descriptive rather than predictive.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from ..errors import ResourceLimitError, ValidationError


MAX_NEWS_ANALYTICS_RECORDS = 500
MAX_STORY_CLUSTER_WINDOW_HOURS = 168
MAX_ATTENTION_BASELINE_PERIODS = 365
MAX_ATTENTION_BUCKETS = 10_000
ATTENTION_CLUSTER_WINDOW_HOURS = 24

STORY_CLUSTER_METHOD_VERSION = "news.story_clusters.v1"
ENTITY_COVERAGE_METHOD_VERSION = "news.entity_coverage.v1"
ATTENTION_METRICS_METHOD_VERSION = "news.attention_metrics.v1"
EVENT_TAXONOMY_VERSION = "news.event_taxonomy.v1"
HEADLINE_SENTIMENT_LEXICON_VERSION = "news.headline_sentiment.finance_lexicon.v1"

_DECIMAL_PRECISION = 34
_ATTENTION_BUCKETS = ("hour", "day", "week")


# The list order is part of the public taxonomy: the first matching category
# wins.  Terms are matched exactly as whole words or whole normalized phrases;
# no stemming, similarity matching, or model inference is used.
_EVENT_TAXONOMY: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "earnings",
        ("earnings", "quarterly results", "financial results", "eps"),
        (),
    ),
    (
        "guidance",
        ("guidance", "outlook", "raises forecast", "cuts forecast"),
        (),
    ),
    (
        "M&A",
        (
            "merger",
            "acquisition",
            "acquire",
            "acquires",
            "acquired",
            "takeover",
            "buyout",
        ),
        (),
    ),
    (
        "corporate action",
        (
            "dividend",
            "buyback",
            "share repurchase",
            "stock split",
            "reverse split",
            "spinoff",
            "spin-off",
        ),
        (),
    ),
    (
        "regulatory/legal",
        (
            "regulatory",
            "regulator",
            "sec",
            "doj",
            "ftc",
            "investigation",
            "lawsuit",
            "settlement",
            "antitrust",
        ),
        (),
    ),
    (
        "management",
        (
            "ceo",
            "cfo",
            "chief executive",
            "chief financial officer",
            "appoints",
            "appointment",
            "resigns",
            "resignation",
            "steps down",
        ),
        (),
    ),
    (
        "product/operations",
        (
            "launch",
            "launches",
            "product",
            "production",
            "manufacturing",
            "operations",
            "facility",
            "plant",
        ),
        (),
    ),
    (
        "macro/rates",
        (
            "federal reserve",
            "interest rate",
            "rate hike",
            "rate cut",
            "inflation",
            "gdp",
            "employment",
            "monetary policy",
        ),
        ("fed_press", "ecb_press", "bea_news"),
    ),
    (
        "energy",
        ("oil", "crude", "natural gas", "energy", "opec", "electricity", "refinery"),
        ("eia_press",),
    ),
    (
        "geopolitical",
        ("war", "sanctions", "tariff", "trade war", "conflict", "invasion", "geopolitical"),
        (),
    ),
)
EVENT_TAXONOMY_CATEGORIES = tuple(item[0] for item in _EVENT_TAXONOMY) + ("unclassified",)

_POSITIVE_SENTIMENT_TERMS = (
    "beats",
    "beat",
    "raises guidance",
    "raised guidance",
    "upgrade",
    "upgraded",
    "growth",
    "profit",
    "profits",
    "strong demand",
    "approval",
)
_NEGATIVE_SENTIMENT_TERMS = (
    "misses",
    "miss",
    "cuts guidance",
    "cut guidance",
    "downgrade",
    "downgraded",
    "loss",
    "losses",
    "weak demand",
    "recall",
    "bankruptcy",
    "layoffs",
)


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")


_EVENT_TERM_PATTERNS = {
    category: tuple((term, _term_pattern(term)) for term in terms)
    for category, terms, _ in _EVENT_TAXONOMY
}
_POSITIVE_SENTIMENT_PATTERNS = tuple(
    (term, _term_pattern(term)) for term in _POSITIVE_SENTIMENT_TERMS
)
_NEGATIVE_SENTIMENT_PATTERNS = tuple(
    (term, _term_pattern(term)) for term in _NEGATIVE_SENTIMENT_TERMS
)


@dataclass(frozen=True, slots=True)
class _StoryRecord:
    article_id: str
    article_version_id: str
    available_at: datetime
    available_at_text: str
    headline: str
    source_url: str | None
    source_name: str | None
    provider: str | None
    feed_id: str | None
    symbols: tuple[str, ...]
    key_kind: str
    canonical_key: str


def _bounded_records(records: object) -> tuple[Mapping[str, object], ...]:
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValidationError("records must be a sequence of headline-record mappings")
    if len(records) > MAX_NEWS_ANALYTICS_RECORDS:
        raise ResourceLimitError(
            f"news analytics supports at most {MAX_NEWS_ANALYTICS_RECORDS} records"
        )
    result: list[Mapping[str, object]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValidationError(f"records[{index}] must be a mapping")
        result.append(record)
    return tuple(result)


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"headline record {field} must be a nonempty string")
    return value.strip()


def _optional_text(record: Mapping[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"headline record {field} must be text or null")
    normalized = value.strip()
    return normalized or None


def _summary(record: Mapping[str, object]) -> str:
    value = record.get("summary")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("headline record summary must be text or null")
    return value.strip()


def _source_ids(record: Mapping[str, object]) -> tuple[str, str]:
    return (
        _required_text(record, "article_id"),
        _required_text(record, "article_version_id"),
    )


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValidationError(
                "headline record available_at must be an aware ISO-8601 datetime"
            ) from exc
    else:
        raise ValidationError("headline record available_at must be an aware ISO-8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("headline record available_at must include an offset")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _normalised_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _canonical_url(value: str | None) -> str | None:
    """Apply only safe URL identity normalization, never fuzzy URL matching."""

    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or hostname is None or parsed.username is not None:
        return None
    host = hostname.casefold()
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def _provider_symbols(record: Mapping[str, object]) -> tuple[str, ...]:
    value = record.get("symbols")
    if value is None and "symbol" in record:
        value = record["symbol"]
    if value is None:
        return ()
    raw = (value,) if isinstance(value, str) else value
    if isinstance(raw, bytes) or not isinstance(raw, Sequence):
        raise ValidationError("headline record symbols must be a sequence of provider symbols")
    symbols: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError("headline record symbols must contain nonempty strings")
        symbols.add(unicodedata.normalize("NFKC", item).strip().upper())
    return tuple(sorted(symbols))


def _story_record(record: Mapping[str, object]) -> _StoryRecord:
    article_id, article_version_id = _source_ids(record)
    headline = _required_text(record, "headline")
    available_at = _utc_datetime(record.get("available_at"))
    source_url = _optional_text(record, "source_url")
    source_name = _optional_text(record, "source_name")
    provider = _optional_text(record, "provider")
    feed_id = _optional_text(record, "feed_id")
    canonical_url = _canonical_url(source_url)
    if canonical_url is not None:
        key_kind, canonical_key = "canonical_url", canonical_url
    else:
        key_kind, canonical_key = "normalized_headline", _normalised_text(headline)
    return _StoryRecord(
        article_id=article_id,
        article_version_id=article_version_id,
        available_at=available_at,
        available_at_text=_utc_text(available_at),
        headline=headline,
        source_url=source_url,
        source_name=source_name,
        provider=provider,
        feed_id=feed_id,
        symbols=_provider_symbols(record),
        key_kind=key_kind,
        canonical_key=canonical_key,
    )


def _story_order_key(record: _StoryRecord) -> tuple[object, ...]:
    return (
        record.available_at,
        record.key_kind,
        record.canonical_key,
        record.article_id,
        record.article_version_id,
        record.headline,
        record.source_url or "",
        record.source_name or "",
        record.provider or "",
        record.feed_id or "",
        record.symbols,
    )


def _article_order_key(record: Mapping[str, object]) -> tuple[str, str, str]:
    article_id, article_version_id = _source_ids(record)
    headline = _required_text(record, "headline")
    return article_id, article_version_id, headline


def _source_id_order_key(record: Mapping[str, object]) -> tuple[str, str]:
    return _source_ids(record)


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _stable_identifier(prefix: str, parts: Sequence[str]) -> str:
    payload = "\x1f".join((prefix, *parts)).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:24]}"


def _member_projection(record: _StoryRecord) -> dict[str, object]:
    """Return the retained metadata used by clustering; never article body/raw data."""

    return {
        "article_id": record.article_id,
        "article_version_id": record.article_version_id,
        "available_at": record.available_at_text,
        "headline": record.headline,
        "source_url": record.source_url,
        "source_name": record.source_name,
        "provider": record.provider,
        "feed_id": record.feed_id,
        "provider_symbols": record.symbols,
    }


def _source_label(record: _StoryRecord | Mapping[str, object]) -> str | None:
    if isinstance(record, _StoryRecord):
        source_name, feed_id, provider = record.source_name, record.feed_id, record.provider
    else:
        source_name = _optional_text(record, "source_name")
        feed_id = _optional_text(record, "feed_id")
        provider = _optional_text(record, "provider")
    if source_name is not None:
        return f"source_name:{_normalised_text(source_name)}"
    if feed_id is not None:
        return f"feed_id:{_normalised_text(feed_id)}"
    if provider is not None:
        return f"provider:{_normalised_text(provider)}"
    return None


def _validated_window_hours(window_hours: object) -> int:
    if isinstance(window_hours, bool) or not isinstance(window_hours, int):
        raise ValidationError("window_hours must be an integer")
    if not 1 <= window_hours <= MAX_STORY_CLUSTER_WINDOW_HOURS:
        raise ValidationError(
            f"window_hours must be between 1 and {MAX_STORY_CLUSTER_WINDOW_HOURS}"
        )
    return window_hours


def story_clusters(
    records: Sequence[Mapping[str, object]], window_hours: int
) -> tuple[dict[str, object], ...]:
    """Build exact, time-bounded *candidate* story clusters.

    A retained source URL is canonicalized first (scheme/host/default-port and
    fragment only).  If no usable URL is present, the comparison key is the
    exact normalized headline.  No token similarity or semantic matching is
    used.  Members are sorted by UTC availability then stable source IDs, so
    both member and cluster order are invariant to input ordering.
    """

    hours = _validated_window_hours(window_hours)
    parsed = sorted((_story_record(record) for record in _bounded_records(records)), key=_story_order_key)
    window = timedelta(hours=hours)
    grouped: dict[tuple[str, str], list[list[_StoryRecord]]] = defaultdict(list)
    for record in parsed:
        groups = grouped[(record.key_kind, record.canonical_key)]
        if not groups or record.available_at - groups[-1][0].available_at > window:
            groups.append([record])
        else:
            groups[-1].append(record)

    clusters: list[dict[str, object]] = []
    for (key_kind, canonical_key), groups in grouped.items():
        for members in groups:
            article_ids = _ordered_unique([item.article_id for item in members])
            version_ids = _ordered_unique([item.article_version_id for item in members])
            source_labels = tuple(
                sorted(
                    {
                        label
                        for label in (_source_label(item) for item in members)
                        if label is not None
                    }
                )
            )
            cluster_id = _stable_identifier(
                "story-candidate",
                (
                    STORY_CLUSTER_METHOD_VERSION,
                    key_kind,
                    canonical_key,
                    members[0].available_at_text,
                    *version_ids,
                ),
            )
            clusters.append(
                {
                    "method": "exact_url_or_normalized_headline_candidate",
                    "method_version": STORY_CLUSTER_METHOD_VERSION,
                    "cluster_id": cluster_id,
                    "cluster_status": "candidate",
                    "candidate": True,
                    "key_kind": key_kind,
                    "canonical_key": canonical_key,
                    "window_hours": hours,
                    "first_observed_at": members[0].available_at_text,
                    "last_observed_at": members[-1].available_at_text,
                    "member_count": len(members),
                    "source_breadth": len(source_labels),
                    "source_labels": source_labels,
                    "source_article_ids": article_ids,
                    "source_article_version_ids": version_ids,
                    "members": tuple(_member_projection(item) for item in members),
                }
            )
    return tuple(
        sorted(
            clusters,
            key=lambda item: (
                str(item["first_observed_at"]),
                str(item["key_kind"]),
                str(item["canonical_key"]),
                str(item["cluster_id"]),
            ),
        )
    )


def entity_coverage(records: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Aggregate only source-provided symbols, without resolving entities."""

    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for record in _bounded_records(records):
        _source_ids(record)
        for symbol in _provider_symbols(record):
            grouped[symbol].append(record)

    coverage: list[dict[str, object]] = []
    for symbol in sorted(grouped):
        members = sorted(grouped[symbol], key=_source_id_order_key)
        article_ids = _ordered_unique([_source_ids(item)[0] for item in members])
        version_ids = _ordered_unique([_source_ids(item)[1] for item in members])
        feed_ids = tuple(
            sorted(
                {
                    value
                    for value in (_optional_text(item, "feed_id") for item in members)
                    if value is not None
                }
            )
        )
        providers = tuple(
            sorted(
                {
                    value
                    for value in (_optional_text(item, "provider") for item in members)
                    if value is not None
                }
            )
        )
        coverage.append(
            {
                "method": "provider_symbol_coverage",
                "method_version": ENTITY_COVERAGE_METHOD_VERSION,
                "entity_type": "provider_symbol",
                "provider_symbol": symbol,
                "identity_scope": "provider_symbol_only",
                "instrument_identity_status": "not_resolved",
                "named_entity_recognition_status": "not_performed",
                "record_count": len(members),
                "article_version_count": len(version_ids),
                "feed_ids": feed_ids,
                "providers": providers,
                "source_article_ids": article_ids,
                "source_article_version_ids": version_ids,
            }
        )
    return tuple(coverage)


def _bucket_start(value: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        day = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    raise ValidationError("attention bucket is unsupported")


def _next_bucket(value: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return value + timedelta(hours=1)
    if bucket == "day":
        return value + timedelta(days=1)
    if bucket == "week":
        return value + timedelta(days=7)
    raise ValidationError("attention bucket is unsupported")


def _validated_attention_arguments(bucket: object, baseline_periods: object) -> tuple[str, int]:
    if not isinstance(bucket, str) or bucket not in _ATTENTION_BUCKETS:
        raise ValidationError("bucket must be one of: hour, day, week")
    if isinstance(baseline_periods, bool) or not isinstance(baseline_periods, int):
        raise ValidationError("baseline_periods must be an integer")
    if not 1 <= baseline_periods <= MAX_ATTENTION_BASELINE_PERIODS:
        raise ValidationError(
            f"baseline_periods must be between 1 and {MAX_ATTENTION_BASELINE_PERIODS}"
        )
    return bucket, baseline_periods


def _cluster_source_labels(cluster: Mapping[str, object]) -> tuple[str, ...]:
    members = cluster.get("members")
    if not isinstance(members, tuple):
        raise ValidationError("internal story-cluster members are invalid")
    labels: set[str] = set()
    for member in members:
        if not isinstance(member, Mapping):
            raise ValidationError("internal story-cluster member is invalid")
        label = _source_label(member)
        if label is not None:
            labels.add(label)
    return tuple(sorted(labels))


def _cluster_member_ids(cluster: Mapping[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    members = cluster.get("members")
    if not isinstance(members, tuple):
        raise ValidationError("internal story-cluster members are invalid")
    article_ids: list[str] = []
    version_ids: list[str] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise ValidationError("internal story-cluster member is invalid")
        article_id, version_id = _source_ids(member)
        article_ids.append(article_id)
        version_ids.append(version_id)
    return _ordered_unique(article_ids), _ordered_unique(version_ids)


def _attention_statistics(history: Sequence[int], current: int) -> dict[str, object]:
    if not history:
        return {
            "baseline_mean": None,
            "baseline_sample_variance": None,
            "baseline_sample_standard_deviation": None,
            "z_score": None,
            "z_score_status": "insufficient_history",
        }
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        values = tuple(Decimal(value) for value in history)
        mean = sum(values, Decimal("0")) / Decimal(len(values))
        if len(values) == 1:
            variance = Decimal("0")
        else:
            variance = sum(((value - mean) ** 2 for value in values), Decimal("0")) / Decimal(
                len(values) - 1
            )
        if variance == 0:
            return {
                "baseline_mean": mean,
                "baseline_sample_variance": variance,
                "baseline_sample_standard_deviation": Decimal("0"),
                "z_score": None,
                "z_score_status": "zero_variance",
            }
        standard_deviation = variance.sqrt()
        return {
            "baseline_mean": mean,
            "baseline_sample_variance": variance,
            "baseline_sample_standard_deviation": standard_deviation,
            "z_score": (Decimal(current) - mean) / standard_deviation,
            "z_score_status": "established",
        }


def attention_metrics(
    records: Sequence[Mapping[str, object]], bucket: str, baseline_periods: int
) -> tuple[dict[str, object], ...]:
    """Count unique candidate clusters per UTC bucket with explicit zero gaps.

    Each candidate is assigned once, to the bucket containing its first
    retained availability timestamp.  The trailing baseline contains the
    immediately prior requested number of buckets, including explicitly
    represented zero buckets.  Z-scores use Decimal sample standard deviation
    and are withheld for insufficient history or zero variance.
    """

    bucket, periods = _validated_attention_arguments(bucket, baseline_periods)
    clusters = story_clusters(records, ATTENTION_CLUSTER_WINDOW_HOURS)
    if not clusters:
        return ()
    grouped: dict[datetime, list[Mapping[str, object]]] = defaultdict(list)
    for cluster in clusters:
        first = _utc_datetime(cluster.get("first_observed_at"))
        grouped[_bucket_start(first, bucket)].append(cluster)
    starts = sorted(grouped)
    bucket_count = 1
    cursor = starts[0]
    while cursor < starts[-1]:
        cursor = _next_bucket(cursor, bucket)
        bucket_count += 1
        if bucket_count > MAX_ATTENTION_BUCKETS:
            raise ResourceLimitError("attention bucket range exceeds the supported bound")

    result: list[dict[str, object]] = []
    history_counts: list[int] = []
    cursor = starts[0]
    while cursor <= starts[-1]:
        bucket_clusters = sorted(grouped.get(cursor, ()), key=lambda item: str(item["cluster_id"]))
        cluster_ids = tuple(str(item["cluster_id"]) for item in bucket_clusters)
        source_labels = tuple(
            sorted(
                {
                    label
                    for item in bucket_clusters
                    for label in _cluster_source_labels(item)
                }
            )
        )
        article_ids = _ordered_unique(
            [
                article_id
                for item in bucket_clusters
                for article_id in _cluster_member_ids(item)[0]
            ]
        )
        version_ids = _ordered_unique(
            [
                version_id
                for item in bucket_clusters
                for version_id in _cluster_member_ids(item)[1]
            ]
        )
        count = len(bucket_clusters)
        baseline = tuple(history_counts[-periods:])
        if len(baseline) < periods:
            statistics = {
                "baseline_mean": None,
                "baseline_sample_variance": None,
                "baseline_sample_standard_deviation": None,
                "z_score": None,
                "z_score_status": "insufficient_history",
            }
        else:
            statistics = _attention_statistics(baseline, count)
        result.append(
            {
                "method": "candidate_cluster_attention",
                "method_version": ATTENTION_METRICS_METHOD_VERSION,
                "cluster_method_version": STORY_CLUSTER_METHOD_VERSION,
                "cluster_window_hours": ATTENTION_CLUSTER_WINDOW_HOURS,
                "bucket": bucket,
                "bucket_start": _utc_text(cursor),
                "zero_bucket": count == 0,
                "candidate_cluster_count": count,
                "unique_candidate_cluster_count": count,
                "candidate_cluster_ids": cluster_ids,
                "article_version_count": len(version_ids),
                "source_breadth": len(source_labels),
                "source_labels": source_labels,
                "source_article_ids": article_ids,
                "source_article_version_ids": version_ids,
                "baseline_periods": periods,
                "baseline_bucket_counts": baseline,
                "baseline_observation_count": len(baseline),
                "z_score_basis": "sample_standard_deviation",
                **statistics,
            }
        )
        history_counts.append(count)
        cursor = _next_bucket(cursor, bucket)
    return tuple(result)


def _matching_terms(
    text: str, patterns: Sequence[tuple[str, re.Pattern[str]]]
) -> tuple[str, ...]:
    return tuple(term for term, pattern in patterns if pattern.search(text) is not None)


def _classification_text(record: Mapping[str, object]) -> str:
    headline = _required_text(record, "headline")
    summary = _summary(record)
    return _normalised_text(f"{headline} {summary}")


def classify_events(records: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Assign one ordered, transparent keyword/feed taxonomy candidate per article."""

    result: list[dict[str, object]] = []
    for record in sorted(_bounded_records(records), key=_article_order_key):
        article_id, article_version_id = _source_ids(record)
        text = _classification_text(record)
        feed_id = _optional_text(record, "feed_id")
        category = "unclassified"
        matched_terms: tuple[str, ...] = ()
        matched_feed_ids: tuple[str, ...] = ()
        taxonomy_order = len(EVENT_TAXONOMY_CATEGORIES)
        for index, (candidate, _, feed_ids) in enumerate(_EVENT_TAXONOMY, start=1):
            terms = _matching_terms(text, _EVENT_TERM_PATTERNS[candidate])
            feeds = (feed_id,) if feed_id is not None and feed_id in feed_ids else ()
            if terms or feeds:
                category = candidate
                matched_terms = terms
                matched_feed_ids = feeds
                taxonomy_order = index
                break
        if matched_terms and matched_feed_ids:
            basis = "keyword_and_feed"
        elif matched_terms:
            basis = "keyword"
        elif matched_feed_ids:
            basis = "feed"
        else:
            basis = "none"
        result.append(
            {
                "method": "ordered_keyword_feed_taxonomy",
                "method_version": EVENT_TAXONOMY_VERSION,
                "taxonomy_version": EVENT_TAXONOMY_VERSION,
                "event_classification_status": "candidate",
                "event_category": category,
                "taxonomy_order": taxonomy_order,
                "classification_basis": basis,
                "matched_terms": matched_terms,
                "matched_feed_ids": matched_feed_ids,
                "source_article_ids": (article_id,),
                "source_article_version_ids": (article_version_id,),
                "article_id": article_id,
                "article_version_id": article_version_id,
            }
        )
    return tuple(result)


def headline_sentiment(records: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Score retained headline and summary text using a fixed finance lexicon."""

    result: list[dict[str, object]] = []
    for record in sorted(_bounded_records(records), key=_article_order_key):
        article_id, article_version_id = _source_ids(record)
        text = _classification_text(record)
        positive_terms = _matching_terms(text, _POSITIVE_SENTIMENT_PATTERNS)
        negative_terms = _matching_terms(text, _NEGATIVE_SENTIMENT_PATTERNS)
        denominator = len(positive_terms) + len(negative_terms)
        with localcontext() as context:
            context.prec = _DECIMAL_PRECISION
            score = (
                Decimal("0")
                if denominator == 0
                else Decimal(len(positive_terms) - len(negative_terms)) / Decimal(denominator)
            )
        label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
        result.append(
            {
                "method": "headline_summary_finance_lexicon",
                "method_version": HEADLINE_SENTIMENT_LEXICON_VERSION,
                "lexicon_version": HEADLINE_SENTIMENT_LEXICON_VERSION,
                "sentiment_status": "lexicon_descriptive",
                "text_fields": ("headline", "summary"),
                "article_id": article_id,
                "article_version_id": article_version_id,
                "source_article_ids": (article_id,),
                "source_article_version_ids": (article_version_id,),
                "positive_terms": positive_terms,
                "negative_terms": negative_terms,
                "positive_term_count": len(positive_terms),
                "negative_term_count": len(negative_terms),
                "normalized_score": score,
                "sentiment_label": label,
            }
        )
    return tuple(result)


__all__ = (
    "ATTENTION_CLUSTER_WINDOW_HOURS",
    "ATTENTION_METRICS_METHOD_VERSION",
    "ENTITY_COVERAGE_METHOD_VERSION",
    "EVENT_TAXONOMY_CATEGORIES",
    "EVENT_TAXONOMY_VERSION",
    "HEADLINE_SENTIMENT_LEXICON_VERSION",
    "MAX_NEWS_ANALYTICS_RECORDS",
    "STORY_CLUSTER_METHOD_VERSION",
    "attention_metrics",
    "classify_events",
    "entity_coverage",
    "headline_sentiment",
    "story_clusters",
)
