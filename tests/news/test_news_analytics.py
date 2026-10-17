"""Focused offline checks for retained-metadata news analytics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.news.analytics import (
    ATTENTION_METRICS_METHOD_VERSION,
    ENTITY_COVERAGE_METHOD_VERSION,
    EVENT_TAXONOMY_VERSION,
    HEADLINE_SENTIMENT_LEXICON_VERSION,
    MAX_NEWS_ANALYTICS_RECORDS,
    STORY_CLUSTER_METHOD_VERSION,
    attention_metrics,
    classify_events,
    entity_coverage,
    headline_sentiment,
    story_clusters,
)


FIXED_TIME = datetime(2026, 8, 30, 8, tzinfo=timezone.utc)


def _record(
    suffix: str,
    *,
    at: datetime = FIXED_TIME,
    headline: str = "Fixture headline",
    summary: str | None = "",
    source_url: str | None = None,
    source_name: str | None = "Fixture Wire",
    provider: str | None = "fixture",
    feed_id: str | None = "fixture_feed",
    symbols: tuple[str, ...] = (),
    body: str = "PRIVATE ARTICLE BODY MUST NOT BE READ",
) -> dict[str, object]:
    return {
        "article_id": f"article-{suffix}",
        "article_version_id": f"version-{suffix}",
        "available_at": at.isoformat().replace("+00:00", "Z"),
        "headline": headline,
        "summary": summary,
        "source_url": source_url,
        "source_name": source_name,
        "provider": provider,
        "feed_id": feed_id,
        "symbols": list(symbols),
        "body": body,
        "raw_response": {"private": True},
    }


class NewsAnalyticsTests(unittest.TestCase):
    def test_story_clusters_are_exact_candidates_and_input_order_invariant(self) -> None:
        records = (
            _record(
                "one",
                headline="Alpha announces guidance",
                source_url="HTTPS://Example.test/story#top",
                source_name="Wire A",
                symbols=("AAPL",),
            ),
            _record(
                "two",
                at=FIXED_TIME + timedelta(hours=2),
                headline="Different retained headline",
                source_url="https://example.test/story",
                source_name="Wire B",
                symbols=("AAPL",),
            ),
            _record(
                "three",
                at=FIXED_TIME + timedelta(hours=3),
                headline="  alpha   ANNOUNCES guidance ",
                source_url=None,
            ),
            _record(
                "four",
                at=FIXED_TIME + timedelta(hours=25),
                headline="Different retained headline again",
                source_url="https://example.test/story",
            ),
        )

        clusters = story_clusters(records, window_hours=24)
        self.assertEqual(clusters, story_clusters(tuple(reversed(records)), window_hours=24))
        self.assertEqual(len(clusters), 3)
        self.assertEqual(
            [item["key_kind"] for item in clusters],
            ["canonical_url", "normalized_headline", "canonical_url"],
        )
        first = clusters[0]
        self.assertEqual(first["method_version"], STORY_CLUSTER_METHOD_VERSION)
        self.assertEqual(first["cluster_status"], "candidate")
        self.assertTrue(first["candidate"])
        self.assertEqual(first["member_count"], 2)
        self.assertEqual(
            [item["article_version_id"] for item in first["members"]],
            ["version-one", "version-two"],
        )
        self.assertNotIn("PRIVATE ARTICLE BODY", repr(clusters))
        self.assertNotIn("raw_response", repr(clusters))
        self.assertEqual(clusters[1]["member_count"], 1)
        self.assertEqual(clusters[2]["member_count"], 1)

    def test_entity_coverage_reports_provider_symbols_without_identity_claims(self) -> None:
        records = (
            _record("one", symbols=("aapl", "MSFT")),
            _record("two", symbols=("AAPL",)),
            _record("three", symbols=()),
        )

        coverage = entity_coverage(tuple(reversed(records)))
        self.assertEqual([item["provider_symbol"] for item in coverage], ["AAPL", "MSFT"])
        aapl = coverage[0]
        self.assertEqual(aapl["method_version"], ENTITY_COVERAGE_METHOD_VERSION)
        self.assertEqual(aapl["entity_type"], "provider_symbol")
        self.assertEqual(aapl["identity_scope"], "provider_symbol_only")
        self.assertEqual(aapl["instrument_identity_status"], "not_resolved")
        self.assertEqual(aapl["named_entity_recognition_status"], "not_performed")
        self.assertEqual(aapl["record_count"], 2)
        self.assertEqual(aapl["source_article_version_ids"], ("version-one", "version-two"))
        dumps_strict(coverage)

        minimal = {
            "article_id": "article-minimal",
            "article_version_id": "version-minimal",
            "symbols": ["ONLY_PROVIDER_SYMBOL"],
        }
        self.assertEqual(
            entity_coverage((minimal,))[0]["provider_symbol"], "ONLY_PROVIDER_SYMBOL"
        )

    def test_attention_counts_clusters_keeps_zero_gaps_and_reports_z_score_statuses(self) -> None:
        records = (
            _record("one", source_url="https://example.test/one", source_name="Wire A"),
            _record(
                "two",
                at=FIXED_TIME + timedelta(hours=1),
                source_url="https://example.test/one",
                source_name="Wire B",
            ),
            _record(
                "three",
                at=FIXED_TIME + timedelta(days=2),
                source_url="https://example.test/three",
            ),
        )
        metrics = attention_metrics(records, bucket="day", baseline_periods=2)
        self.assertEqual([item["candidate_cluster_count"] for item in metrics], [1, 0, 1])
        self.assertEqual(metrics[0]["method_version"], ATTENTION_METRICS_METHOD_VERSION)
        self.assertEqual(metrics[0]["z_score_status"], "insufficient_history")
        self.assertEqual(metrics[1]["zero_bucket"], True)
        self.assertEqual(metrics[1]["source_breadth"], 0)
        self.assertEqual(metrics[2]["baseline_bucket_counts"], (1, 0))
        self.assertEqual(metrics[2]["z_score_status"], "established")
        self.assertIsInstance(metrics[2]["z_score"], Decimal)
        self.assertTrue(metrics[2]["z_score"].is_finite())

        zero_variance_records = (
            _record("day-one", source_url="https://example.test/day-one"),
            _record(
                "day-two",
                at=FIXED_TIME + timedelta(days=1),
                source_url="https://example.test/day-two",
            ),
            _record(
                "day-three-a",
                at=FIXED_TIME + timedelta(days=2),
                source_url="https://example.test/day-three-a",
            ),
            _record(
                "day-three-b",
                at=FIXED_TIME + timedelta(days=2, hours=1),
                source_url="https://example.test/day-three-b",
            ),
        )
        zero_variance = attention_metrics(
            zero_variance_records, bucket="day", baseline_periods=2
        )
        self.assertEqual(zero_variance[-1]["candidate_cluster_count"], 2)
        self.assertEqual(zero_variance[-1]["z_score_status"], "zero_variance")
        self.assertIsNone(zero_variance[-1]["z_score"])
        dumps_strict(metrics)

    def test_event_taxonomy_is_ordered_and_does_not_read_article_body(self) -> None:
        records = (
            _record(
                "earnings",
                headline="Company reports earnings and raises guidance",
                summary="",
            ),
            _record("macro", headline="Routine release", feed_id="fed_press", summary=""),
            _record(
                "body-only",
                headline="Routine operating update",
                summary="",
                body="The private body says acquisition and merger",
            ),
        )
        classifications = classify_events(tuple(reversed(records)))
        by_version = {item["article_version_id"]: item for item in classifications}
        earnings = by_version["version-earnings"]
        self.assertEqual(earnings["taxonomy_version"], EVENT_TAXONOMY_VERSION)
        self.assertEqual(earnings["event_category"], "earnings")
        self.assertEqual(earnings["matched_terms"], ("earnings",))
        self.assertEqual(by_version["version-macro"]["event_category"], "macro/rates")
        self.assertEqual(by_version["version-macro"]["matched_feed_ids"], ("fed_press",))
        self.assertEqual(by_version["version-body-only"]["event_category"], "unclassified")
        self.assertNotIn("private body", repr(classifications).casefold())
        dumps_strict(classifications)

    def test_headline_sentiment_uses_only_transparent_headline_summary_lexicon(self) -> None:
        records = (
            _record(
                "positive",
                headline="Company beats estimates",
                summary="Strong growth continues",
                body="private loss and layoffs",
            ),
            _record(
                "neutral",
                headline="Company beats estimates",
                summary="Company misses one target",
            ),
            _record(
                "negative",
                headline="Broker downgrade follows weak demand",
                summary="",
                body="private profit growth",
            ),
        )
        scores = headline_sentiment(tuple(reversed(records)))
        by_version = {item["article_version_id"]: item for item in scores}
        positive = by_version["version-positive"]
        self.assertEqual(positive["lexicon_version"], HEADLINE_SENTIMENT_LEXICON_VERSION)
        self.assertEqual(positive["positive_terms"], ("beats", "growth"))
        self.assertEqual(positive["normalized_score"], Decimal("1"))
        self.assertEqual(positive["sentiment_label"], "positive")
        neutral = by_version["version-neutral"]
        self.assertEqual(neutral["normalized_score"], Decimal("0"))
        self.assertEqual(neutral["sentiment_label"], "neutral")
        negative = by_version["version-negative"]
        self.assertEqual(negative["negative_terms"], ("downgrade", "weak demand"))
        self.assertEqual(negative["sentiment_label"], "negative")
        self.assertNotIn("private profit", repr(scores).casefold())
        dumps_strict(scores)

    def test_bounds_and_typed_inputs_are_enforced(self) -> None:
        record = _record("one")
        with self.assertRaises(ResourceLimitError):
            story_clusters((record,) * (MAX_NEWS_ANALYTICS_RECORDS + 1), window_hours=24)
        with self.assertRaises(ValidationError):
            story_clusters((record,), window_hours=0)
        with self.assertRaises(ValidationError):
            attention_metrics((record,), bucket="month", baseline_periods=2)
        bad_time = dict(record)
        bad_time["available_at"] = "2026-08-30"
        with self.assertRaises(ValidationError):
            story_clusters((bad_time,), window_hours=24)


if __name__ == "__main__":
    unittest.main()
