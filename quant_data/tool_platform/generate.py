"""Deterministically generate the tool surface inside the evolving registry."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from quant_data.tool_platform.catalog import (
    CATALOG_ID,
    CATALOG_VERSION,
    LEGACY_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
    VERSIONED_CATALOG_ID,
    VERSIONED_CATALOG_VERSION,
    VERSIONED_COMPANY_FILING_TOOLS,
    VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
    VERSIONED_INVESTMENT_ANALYSIS_TOOLS,
    VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    VERSIONED_NEWS_TOOLS,
    VERSIONED_RESEARCH_ANALYTIC_TOOLS,
    build_additive_tool_entries,
    build_current_tool_entries,
    build_tool_entries,
    build_tool_version_policies,
    schema_catalog,
    versioned_schema_catalog,
)


REGISTRY_RESOURCE = Path("config/system_registry.json")
_REVIEWED_REGISTRY_SOURCE_SHA256 = {
    ("1.9.0", "2.37.0"): (
        "2a2b611ac6f752e6d83a81369155454b1484b8caebf4d1aaa3be60c41f0e866b"
    ),
    ("1.9.0", "2.38.0"): (
        "3d6c0f31f2c72c20e5459c4e7f2358ea273437d59af99ef017b1f3ad47b1b547"
    ),
    ("1.9.0", "2.39.0"): (
        "f7f445a3dbc991ca7b309ce306e5bc439403d929b96fb9931a22c036cb0eea85"
    ),
    ("1.9.0", "2.40.0"): (
        "72516749f56f962bea2a265ef4a917558ef6e757444ae5d679bc50d2d64e6ed7"
    ),
    ("1.9.0", "2.41.0"): (
        "835fe846c0d0bf0ce630cda0dd23588983c83f6fad663cbe51202fe00deee2a3"
    ),
    ("1.9.0", "2.42.0"): (
        "1ab956e8338b864873f25e6e41cc6a18ba9a271a1951bec0bdccf32a07b8fd2b"
    ),
    ("1.9.0", "2.43.0"): (
        "841041060550eeb41fed491c19835f77d278cbf68daaa9a7b2f754c3f9c4f0ff"
    ),
    ("1.9.0", "2.44.0"): (
        "af6545258751f7b7a7e7c68c673e18a36c65762809032db6ea540b33f249c182"
    ),
    ("1.9.0", "2.45.0"): (
        "f151db20dd26fe2123e887415736431cfe1dcda8bf8f42d83a8b47ad3a27fec2"
    ),
    ("1.9.0", "2.46.0"): (
        "b5236b88a2b320628b870fe3abe7898b76fa5fc1d527a223f87985963e38e264"
    ),
    ("1.9.0", "2.47.0"): (
        "eefa1288e8007518d466a3d4820522ae113ae6c52dc6df0e448cd3654de4a1b8"
    ),
    ("1.9.0", "2.48.0"): (
        "3709c16168e2959a946c78e99c50b540b860d5f26ccf4afc3434831b8e9d8524"
    ),
    ("1.9.0", "2.49.0"): (
        "6d34dc495de10de42765e8909e30df744f69ad72d1901259f7da67be2d2710e1"
    ),
    ("1.9.0", "2.50.0"): (
        "0adc78cbe419b18ece989c9cdd6d918ef13113fa5f573d6f5fbe045b9eca8259"
    ),
    ("1.9.0", "2.51.0"): (
        "1d8485bd1df5351d94f0b13f2264828640d40c756c6241503f40a7b7f4e46c43"
    ),
    ("1.9.0", "2.52.0"): (
        "87c74bbc26ce6101ff6136eefe9fdab0d9616f06d714d049d4c30287eda78323"
    ),
    ("1.9.0", "2.53.0"): (
        "c201524e4e4a72b5377d36390e0cc5c746d392674b598ac1499ab818b418d238"
    ),
    ("1.9.0", "2.54.0"): (
        "f40c4d2e0cad90f686bffe52116d138f3b578f33f4844245682387d398a19e01"
    ),
    ("1.9.0", "2.55.0"): (
        "cec35d5cfed9f25c40f5adfa2f2581b0de8d442a75202e687f791b3a16dc3d7f"
    ),
    ("1.9.0", "2.56.0"): (
        "9782e77c530251401e9b5e42b33115949ce8bb28753f0a0471fa2e8353dd8802"
    ),
    ("1.9.0", "2.57.0"): (
        "1d36ddd20494cfc0ae9e6172f662c5dfe04b905319c0f83b439446f44fd29b5e"
    ),
    ("1.9.0", "2.58.0"): (
        "02a4aeb34632c5ae194622b7de77ec31145135bdf399620e45af461132134d43"
    ),
    ("1.9.0", "2.59.0"): (
        "0457910706181d7f4d9bb07efbf18845dd86335999b55c5d5a8d203041d315a1"
    ),
    ("1.9.0", "2.60.0"): (
        "563c4294c47d534754014dc77a4a1b39e84749cc4d8165636bafd82feff679b0"
    ),
    ("1.9.0", "2.61.0"): (
        "0f43045c1dcc46b0f9d5ecb0aaf98aa3e9ad61a43e250afc389408602a8615ea"
    ),
    ("1.9.0", "2.62.0"): (
        "59db17edd70d8ae9aa77f1468cf7c459338e3d1dff0015b3c99853b2e1e12fd2"
    ),
    ("1.9.0", "2.63.0"): (
        "06466e9b79be5bc0fab927a81b5972059bbad34ba4a674c1c456c3eaeaf04d72"
    ),
}
CATALOG_RESOURCE = Path("quant_data/generated/tool_contract_schemas_v1.json")
VERSIONED_CATALOG_RESOURCE = Path(
    "quant_data/generated/tool_contract_schemas_v2.json"
)
_ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS = frozenset(
    {
        "stats.distribution_diagnostics",
        "stats.covariance_matrix",
        "stats.bootstrap_confidence_interval",
        "stats.principal_components",
    }
)
_ANALYTICS_FOUNDATION_VERSIONED_TOOL_IDS = frozenset(
    {
        "data.quality_audit",
        "timeseries.transform",
    }
)
_TECHNICAL_INDICATOR_VERSIONED_TOOL_IDS = frozenset(
    {"market.technical_indicators"}
)
_RESEARCH_ANALYTIC_VERSIONED_TOOL_IDS = frozenset(
    VERSIONED_RESEARCH_ANALYTIC_TOOLS
)
_COMPANY_FILING_VERSIONED_TOOL_IDS = frozenset(
    VERSIONED_COMPANY_FILING_TOOLS
)
_PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS = frozenset(
    (
        *VERSIONED_COMPANY_SHARE_COUNT_TOOLS,
        *VERSIONED_MARKET_INSTRUMENT_SEARCH_TOOLS,
    )
)
_INVESTMENT_ANALYSIS_VERSIONED_TOOL_IDS = frozenset(
    VERSIONED_INVESTMENT_ANALYSIS_TOOLS
)
_CURRENT_NEWS_VERSIONED_TOOL_IDS = frozenset(VERSIONED_NEWS_TOOLS)
_CURRENT_NEWS_MIGRATION_ID = "news:0006_fmp_stock_latest_current"
_CURRENT_NEWS_DATASET_IDS = (
    "news.fmp.stock_latest_current_evidence",
    "news.fmp.stock_latest_current_articles",
)
_CURRENT_NEWS_COLLECTOR_ID = "fmp.news.stock_latest_current"


def _add_current_news_declarations(raw: dict[str, Any]) -> None:
    """Advance exact registry 2.62 with the isolated current-news source."""

    if (
        any(item.get("id") == _CURRENT_NEWS_MIGRATION_ID for item in raw["migrations"])
        or any(item.get("id") in _CURRENT_NEWS_DATASET_IDS for item in raw["datasets"])
        or any(item.get("id") == _CURRENT_NEWS_COLLECTOR_ID for item in raw["collectors"])
    ):
        raise ValueError("Current-news registry declarations already exist")

    news_stores = [item for item in raw["stores"] if item.get("id") == "news"]
    old_migrations = [
        item
        for item in raw["migrations"]
        if item.get("id") == "news:0005_fmp_stock_latest"
    ]
    old_datasets = {
        item["id"]: item
        for item in raw["datasets"]
        if item.get("id")
        in {
            "news.fmp.stock_latest_evidence",
            "news.fmp.stock_latest_articles",
        }
    }
    old_collectors = [
        item
        for item in raw["collectors"]
        if item.get("id") == "fmp.news.stock_latest"
    ]
    if (
        len(news_stores) != 1
        or len(old_migrations) != 1
        or set(old_datasets)
        != {
            "news.fmp.stock_latest_evidence",
            "news.fmp.stock_latest_articles",
        }
        or len(old_collectors) != 1
        or news_stores[0].get("migration_order", [])[-1:]
        != ["news:0005_fmp_stock_latest"]
    ):
        raise ValueError("Reviewed news predecessor declarations drifted")

    raw["migrations"].append(
        {
            "dependencies": ["news:0005_fmp_stock_latest"],
            "id": _CURRENT_NEWS_MIGRATION_ID,
            "ordinal": 6,
            "reconstruction_state": "fixture_validated",
            "resource": "quant_data/migrations/news/0006_fmp_stock_latest_current.sql",
            "semantic_scope": (
                "Repeatable bounded FMP stock-latest current-page evidence and "
                "immutable article versions; one partial page never infers a tombstone."
            ),
            "sha256": "bf8757bcc7679d1bd57408978eed996c9f4dad9c89339a52ca8adbeedbf83d30",
            "store": "news",
        }
    )
    news_stores[0]["migration_order"].append(_CURRENT_NEWS_MIGRATION_ID)

    evidence = copy.deepcopy(
        old_datasets["news.fmp.stock_latest_evidence"]
    )
    evidence["id"] = _CURRENT_NEWS_DATASET_IDS[0]
    evidence["collector_ids"] = [_CURRENT_NEWS_COLLECTOR_ID]
    evidence["physical"]["relations"] = [
        {"kind": "table", "name": "fmp_stock_latest_current_attempts"},
        {"kind": "table", "name": "fmp_stock_latest_current_outcomes"},
        {"kind": "table", "name": "fmp_stock_latest_current_captures"},
    ]
    evidence["identity"] = {
        "stable_fields": ["collector_id", "profile_id", "poll_slot"],
        "version_fields": [
            "attempt_id",
            "outcome_id",
            "capture_id",
            "semantic_identity",
        ],
    }
    evidence["freshness"] = {
        "cadence": "manual",
        "expected_lag": "P0D",
        "health_severity": "warning",
        "if_new": True,
        "measured_from": "successful_capture",
        "stale_after": "P1D",
    }
    evidence["quality_contract"]["rules"] = [
        "one_intent_per_utc_hour",
        "one_terminal_outcome",
        "raw_response_retained_private",
        "accepted_and_rejected_row_counts",
        "partial_page_no_tombstone",
    ]
    evidence["tool_ids"] = []

    articles = copy.deepcopy(
        old_datasets["news.fmp.stock_latest_articles"]
    )
    articles["id"] = _CURRENT_NEWS_DATASET_IDS[1]
    articles["collector_ids"] = [_CURRENT_NEWS_COLLECTOR_ID]
    articles["physical"]["relations"] = [
        {"kind": "table", "name": "fmp_stock_latest_current_articles"},
        {"kind": "table", "name": "fmp_stock_latest_current_article_versions"},
        {"kind": "table", "name": "fmp_stock_latest_current_capture_articles"},
    ]
    articles["freshness"] = copy.deepcopy(evidence["freshness"])
    articles["quality_contract"]["rules"] = [
        "stable_identity_includes_symbol",
        "normalized_mutable_content_hash",
        "source_precision_preserved",
        "missing_optional_fields_are_explicit",
        "partial_page_no_tombstone",
    ]
    articles["tool_ids"] = []
    raw["datasets"].extend((evidence, articles))

    collector = copy.deepcopy(old_collectors[0])
    collector["id"] = _CURRENT_NEWS_COLLECTOR_ID
    collector["handler"] = "news.fmp_stock_latest_current"
    collector["output_datasets"] = list(_CURRENT_NEWS_DATASET_IDS)
    collector["schedule_eligibility"] = {"mode": "manual_only"}
    raw["collectors"].append(collector)


def _render(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def generated_bytes(project_root: Path) -> tuple[bytes, bytes, bytes]:
    registry_path = project_root / REGISTRY_RESOURCE
    source_bytes = registry_path.read_bytes()
    raw = json.loads(source_bytes)
    source_version = (
        raw.get("schema_version"),
        raw.get("registry_version"),
    )
    expected_source_sha256 = _REVIEWED_REGISTRY_SOURCE_SHA256.get(
        source_version
    )
    if (
        expected_source_sha256 is None
        or hashlib.sha256(source_bytes).hexdigest() != expected_source_sha256
    ):
        raise ValueError("Tool generation requires an exact reviewed registry source")
    if source_version == ("1.9.0", "2.62.0"):
        _add_current_news_declarations(raw)
    existing = {item["id"]: item for item in raw["tools"]}
    try:
        legacy = {name: existing[name] for name in LEGACY_TOOL_NAMES}
    except KeyError as exc:
        raise ValueError("Canonical registry lost a Stage 1 contract") from exc

    recovered_entries = build_tool_entries(legacy)
    entries = build_current_tool_entries(legacy)
    additive_entries = build_additive_tool_entries()
    version_policies = build_tool_version_policies()
    catalog_version = VERSIONED_CATALOG_VERSION
    if source_version <= ("1.9.0", "2.61.0"):
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in _CURRENT_NEWS_VERSIONED_TOOL_IDS
        )
        catalog_version = "2.22.0"
    if source_version <= ("1.9.0", "2.60.0"):
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if not (
                        item["tool"] == "market.technical_indicators"
                        and variant["version"] == "2.7.0"
                    )
                ],
            }
            for item in version_policies
        )
        catalog_version = "2.21.0"
    if source_version <= ("1.9.0", "2.59.0"):
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if not (
                        item["tool"] == "market.technical_indicators"
                        and variant["version"] == "2.6.0"
                    )
                ],
            }
            for item in version_policies
        )
        catalog_version = "2.20.0"
    if source_version <= ("1.9.0", "2.58.0"):
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if not (
                        item["tool"] == "market.technical_indicators"
                        and variant["version"] == "2.5.0"
                    )
                ],
            }
            for item in version_policies
        )
        catalog_version = "2.19.0"
    if source_version <= ("1.9.0", "2.57.0"):
        registry_259_variants = {
            ("market.technical_indicators", "2.4.0"),
            ("market.cross_sectional_performance", "2.1.0"),
            ("energy.get_electricity_retail_sales", "2.1.0"),
            ("energy.get_weekly_fundamentals", "2.1.0"),
            ("company.get_fundamentals", "2.1.0"),
        }
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if (item["tool"], variant["version"])
                    not in registry_259_variants
                ],
            }
            for item in version_policies
        )
        catalog_version = "2.18.0"
    if source_version <= ("1.9.0", "2.56.0"):
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if not (
                        item["tool"] == "market.technical_indicators"
                        and variant["version"] == "2.3.0"
                    )
                ],
            }
            for item in version_policies
        )
        catalog_version = "2.17.0"
    if source_version <= ("1.9.0", "2.55.0"):
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in _INVESTMENT_ANALYSIS_VERSIONED_TOOL_IDS
        )
        catalog_version = "2.16.0"
    if source_version <= ("1.9.0", "2.54.0"):
        excluded_indicator_versions = {"2.2.0"}
        if source_version <= ("1.9.0", "2.53.0"):
            excluded_indicator_versions.add("2.1.0")
        version_policies = tuple(
            {
                **item,
                "variants": [
                    variant
                    for variant in item["variants"]
                    if not (
                        item["tool"] == "market.technical_indicators"
                        and variant["version"]
                        in excluded_indicator_versions
                    )
                ],
            }
            for item in version_policies
        )
        if source_version == ("1.9.0", "2.53.0"):
            catalog_version = "2.14.0"
        elif source_version == ("1.9.0", "2.54.0"):
            catalog_version = "2.15.0"
    if source_version == ("1.9.0", "2.45.0"):
        entries = tuple(
            item
            for item in entries
            if item["id"] not in _ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS
        )
        additive_entries = tuple(
            item
            for item in additive_entries
            if item["id"] not in _ANALYTICS_FOUNDATION_NATIVE_TOOL_IDS
        )
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"]
            not in (
                _ANALYTICS_FOUNDATION_VERSIONED_TOOL_IDS
                | _TECHNICAL_INDICATOR_VERSIONED_TOOL_IDS
                | _RESEARCH_ANALYTIC_VERSIONED_TOOL_IDS
                | _COMPANY_FILING_VERSIONED_TOOL_IDS
                | _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
            )
        )
        catalog_version = "2.9.0"
    elif source_version == ("1.9.0", "2.46.0"):
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in (
                _TECHNICAL_INDICATOR_VERSIONED_TOOL_IDS
                | _RESEARCH_ANALYTIC_VERSIONED_TOOL_IDS
                | _COMPANY_FILING_VERSIONED_TOOL_IDS
                | _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
            )
        )
        catalog_version = "2.10.0"
    elif source_version in {
        ("1.9.0", "2.47.0"),
        ("1.9.0", "2.48.0"),
        ("1.9.0", "2.49.0"),
        ("1.9.0", "2.50.0"),
    }:
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"]
            not in (
                _RESEARCH_ANALYTIC_VERSIONED_TOOL_IDS
                | _COMPANY_FILING_VERSIONED_TOOL_IDS
                | _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
            )
        )
        catalog_version = "2.11.0"
    elif source_version == ("1.9.0", "2.51.0"):
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"]
            not in (
                _COMPANY_FILING_VERSIONED_TOOL_IDS
                | _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
            )
        )
        catalog_version = "2.12.0"
    elif source_version == ("1.9.0", "2.52.0"):
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"] not in _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
        )
        catalog_version = "2.13.0"
    elif source_version not in {
        ("1.9.0", "2.45.0"),
        ("1.9.0", "2.46.0"),
        ("1.9.0", "2.47.0"),
        ("1.9.0", "2.48.0"),
        ("1.9.0", "2.49.0"),
        ("1.9.0", "2.50.0"),
        ("1.9.0", "2.51.0"),
        ("1.9.0", "2.52.0"),
        ("1.9.0", "2.53.0"),
        ("1.9.0", "2.54.0"),
        ("1.9.0", "2.55.0"),
        ("1.9.0", "2.56.0"),
        ("1.9.0", "2.57.0"),
        ("1.9.0", "2.58.0"),
        ("1.9.0", "2.59.0"),
        ("1.9.0", "2.60.0"),
        ("1.9.0", "2.61.0"),
        ("1.9.0", "2.62.0"),
        ("1.9.0", "2.63.0"),
    }:
        step1_additions = {
            "macro.get_release_calendar",
            "market.get_volume_series",
        }
        macro_v2_tools = {
            "macro.search_series",
            "macro.describe_series",
            "macro.get_series",
        }
        entries = tuple(
            item for item in entries if item["id"] not in step1_additions
        )
        additive_entries = tuple(
            item
            for item in additive_entries
            if item["id"] not in step1_additions
        )
        version_policies = tuple(
            item
            for item in version_policies
            if item["tool"]
            not in (
                macro_v2_tools
                | _TECHNICAL_INDICATOR_VERSIONED_TOOL_IDS
                | _RESEARCH_ANALYTIC_VERSIONED_TOOL_IDS
                | _COMPANY_FILING_VERSIONED_TOOL_IDS
                | _PLACEHOLDER_SUCCESSOR_VERSIONED_TOOL_IDS
            )
        )
        catalog_version = "2.8.0"
    catalog_payload = schema_catalog(recovered_entries)
    catalog_bytes = _render(catalog_payload)
    catalog_sha = hashlib.sha256(catalog_bytes).hexdigest()
    versioned_catalog_payload = versioned_schema_catalog(
        version_policies,
        additive_entries,
    )
    versioned_catalog_payload["schema_version"] = catalog_version
    versioned_catalog_bytes = _render(versioned_catalog_payload)
    versioned_catalog_sha = hashlib.sha256(versioned_catalog_bytes).hexdigest()

    raw["schema_version"] = "1.9.0"
    target_registry_versions = {
        ("1.9.0", "2.37.0"): "2.39.0",
        ("1.9.0", "2.38.0"): "2.39.0",
        ("1.9.0", "2.39.0"): "2.39.0",
        ("1.9.0", "2.40.0"): "2.42.0",
        ("1.9.0", "2.41.0"): "2.42.0",
        ("1.9.0", "2.42.0"): "2.42.0",
        ("1.9.0", "2.43.0"): "2.43.0",
        ("1.9.0", "2.44.0"): "2.44.0",
        ("1.9.0", "2.45.0"): "2.46.0",
        ("1.9.0", "2.46.0"): "2.47.0",
        ("1.9.0", "2.47.0"): "2.48.0",
        ("1.9.0", "2.48.0"): "2.48.0",
        ("1.9.0", "2.49.0"): "2.49.0",
        ("1.9.0", "2.50.0"): "2.50.0",
        ("1.9.0", "2.51.0"): "2.52.0",
        ("1.9.0", "2.52.0"): "2.53.0",
        ("1.9.0", "2.53.0"): "2.54.0",
        ("1.9.0", "2.54.0"): "2.55.0",
        ("1.9.0", "2.55.0"): "2.56.0",
        ("1.9.0", "2.56.0"): "2.57.0",
        ("1.9.0", "2.57.0"): "2.58.0",
        ("1.9.0", "2.58.0"): "2.59.0",
        ("1.9.0", "2.59.0"): "2.60.0",
        ("1.9.0", "2.60.0"): "2.61.0",
        ("1.9.0", "2.61.0"): "2.62.0",
        ("1.9.0", "2.62.0"): "2.63.0",
        ("1.9.0", "2.63.0"): "2.63.0",
    }
    raw["registry_version"] = target_registry_versions[source_version]
    raw["tool_schema_catalog"] = {
        "schema_id": CATALOG_ID,
        "schema_version": CATALOG_VERSION,
        "resource": CATALOG_RESOURCE.as_posix(),
        "sha256": catalog_sha,
    }
    raw["tool_version_schema_catalog"] = {
        "schema_id": VERSIONED_CATALOG_ID,
        "schema_version": catalog_version,
        "resource": VERSIONED_CATALOG_RESOURCE.as_posix(),
        "sha256": versioned_catalog_sha,
    }
    raw["tools"] = list(entries)
    raw["tool_versions"] = list(version_policies)
    by_dataset: dict[str, list[str]] = {item["id"]: [] for item in raw["datasets"]}
    for entry in entries:
        for dataset_id in entry["datasets"]:
            if dataset_id not in by_dataset:
                raise ValueError(f"Tool {entry['id']} references unknown dataset {dataset_id}")
            by_dataset[dataset_id].append(entry["id"])
    for policy in version_policies:
        for variant in policy["variants"]:
            for dataset_id in variant["datasets"]:
                if dataset_id not in by_dataset:
                    raise ValueError(
                        f"Tool {variant['id']} references unknown dataset {dataset_id}"
                    )
                if variant["id"] not in by_dataset[dataset_id]:
                    by_dataset[dataset_id].append(variant["id"])
    for dataset in raw["datasets"]:
        dataset["tool_ids"] = by_dataset[dataset["id"]]
    raw["presentation_order"]["tools"] = [item["id"] for item in entries]
    return _render(raw), catalog_bytes, versioned_catalog_bytes


def generate(project_root: Path, *, check: bool = False) -> None:
    root = project_root.resolve(strict=True)
    registry_bytes, catalog_bytes, versioned_catalog_bytes = generated_bytes(root)
    expected = {
        root / REGISTRY_RESOURCE: registry_bytes,
        root / CATALOG_RESOURCE: catalog_bytes,
        root / VERSIONED_CATALOG_RESOURCE: versioned_catalog_bytes,
    }
    stale = [
        path.relative_to(root).as_posix()
        for path, payload in expected.items()
        if not path.exists() or path.read_bytes() != payload
    ]
    if check:
        if stale:
            raise SystemExit("Generated Stage 5 artifacts are stale: " + ", ".join(stale))
        return
    for path, payload in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-stage5")
        temporary.write_bytes(payload)
        temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    generate(arguments.project_root, check=arguments.check)


if __name__ == "__main__":
    main()
