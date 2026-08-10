"""Offline Stage 4 news-fixture ingestion over immutable news versions.

This module is intentionally a narrow fixture adapter rather than a provider
client.  It accepts only manifest-verified synthetic JSON, validates the
entire batch before the shared coordinator opens a write run, and publishes
immutable evidence, a source snapshot, append-only item versions, and their
source-labelled associations in one short transaction.

The Stage 4 news registry declares local-capture history.  Consequently every
accepted canonical availability boundary is the exact offset-aware local
capture timestamp; source publication precision is retained separately and is
never invented.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..contracts import IngestionReceipt
from ..errors import ResourceLimitError, ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue


_MAX_FIXTURE_BYTES = 1_048_576
_MAX_ITEMS = 1_000
_MAX_LABELS_PER_ITEM = 1_000
_NORMALIZATION_VERSION = "stage4_news_v1"
_COLLECTOR_ID = "fixture.news.import"
_EVIDENCE_DATASET_ID = "fixture.news.evidence"
_CANONICAL_DATASET_ID = "fixture.news.items"
_SEARCH_DATASET_ID = "fixture.news.search_index"
_OUTPUT_DATASET_IDS = (
    _EVIDENCE_DATASET_ID,
    _CANONICAL_DATASET_ID,
    _SEARCH_DATASET_ID,
)
_SOURCE_KINDS = frozenset({"article", "bulletin", "alert"})
_CONTENT_STATES = frozenset({"present", "missing", "redacted"})
_ITEM_STATES = frozenset({"active", "retracted"})
_ASSOCIATION_STATES = frozenset({"present", "missing"})


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _mapping(value: object, label: str, *, nonempty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or (nonempty and not value):
        raise _fail(f"{label} must be a{' nonempty' if nonempty else ''} object")
    if not all(isinstance(key, str) and key for key in value):
        raise _fail(f"{label} has an invalid object key")
    return dict(value)


def _array(value: object, label: str, *, maximum: int, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise _fail(f"{label} must be a{' nonempty' if nonempty else ''} array")
    if len(value) > maximum:
        raise ResourceLimitError(f"{label} exceeds the reviewed fixture bound")
    return list(value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{label} must be a nonempty string")
    return value.strip()


def _nullable_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _exact_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(raw) != expected:
        raise _fail(f"{label} has an unsupported shape")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _temporal(
    value: object,
    precision: object,
    label: str,
    *,
    allow_unknown: bool = False,
) -> TemporalValue | None:
    if precision == TemporalPrecision.UNKNOWN.value:
        if allow_unknown and value is None:
            return None
        raise _fail(f"{label} may be null only with unknown precision")
    raw = _string(value, label)
    parsed = TemporalValue.parse(raw, pointer=f"/{label}")
    if precision not in {TemporalPrecision.DATE.value, TemporalPrecision.DATETIME.value}:
        raise _fail(f"{label}_precision is invalid")
    if parsed.precision.value != precision:
        raise _fail(f"{label} precision does not match its source value")
    return parsed


def _scope(value: object) -> tuple[dict[str, str], str]:
    raw = _mapping(value, "request_scope", nonempty=True)
    result: dict[str, str] = {}
    for key, item in raw.items():
        result[_string(key, f"request_scope/{key}")] = _string(
            item, f"request_scope/{key}"
        )
    rendered = dumps_strict(result)
    return result, rendered


def _confidence(value: object, label: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        raise _fail(f"{label} must be a finite decimal between zero and one")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"{label} must be a finite decimal between zero and one") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > 1:
        raise _fail(f"{label} must be a finite decimal between zero and one")
    normalized = parsed.normalize()
    text = "0" if normalized.is_zero() else format(normalized, "f")
    return float(parsed), text


@dataclass(frozen=True, slots=True)
class _Content:
    state: str
    headline: str | None
    body: str | None
    summary: str | None
    source_url: str | None
    missing_reason: str | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "headline": self.headline,
            "body": self.body,
            "summary": self.summary,
            "source_url": self.source_url,
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True, slots=True)
class _SymbolAssociation:
    state: str
    instrument_id: str | None
    provider: str | None
    provider_symbol: str | None
    missing_reason: str | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "instrument_id": self.instrument_id,
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True, slots=True)
class _TopicAssociation:
    state: str
    topic: str | None
    confidence: float | None
    confidence_text: str | None
    missing_reason: str | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "topic": self.topic,
            "confidence": self.confidence_text,
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True, slots=True)
class _TextAssociation:
    state: str
    value: str | None
    missing_reason: str | None

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "value": self.value,
            "missing_reason": self.missing_reason,
        }


@dataclass(frozen=True, slots=True)
class _NewsItem:
    source_item_id: str
    source_kind: str
    published_at: TemporalValue | None
    content: _Content
    item_state: str
    retraction_reason: str | None
    symbols: tuple[_SymbolAssociation, ...]
    topics: tuple[_TopicAssociation, ...]
    geographies: tuple[_TextAssociation, ...]
    coverage_lanes: tuple[_TextAssociation, ...]
    source_row: int

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "source_item_id": self.source_item_id,
            "source_kind": self.source_kind,
            "published_at": self.published_at.raw if self.published_at is not None else None,
            "published_precision": (
                self.published_at.precision.value
                if self.published_at is not None
                else TemporalPrecision.UNKNOWN.value
            ),
            "content": self.content.semantic_mapping(),
            "item_state": self.item_state,
            "retraction_reason": self.retraction_reason,
            "labels": {
                "symbols": [
                    item.semantic_mapping()
                    for item in sorted(
                        self.symbols,
                        key=lambda item: (
                            item.state,
                            item.instrument_id or "",
                            item.provider or "",
                            item.provider_symbol or "",
                            item.missing_reason or "",
                        ),
                    )
                ],
                "topics": [
                    item.semantic_mapping()
                    for item in sorted(
                        self.topics,
                        key=lambda item: (
                            item.state,
                            item.topic or "",
                            item.confidence_text or "",
                            item.missing_reason or "",
                        ),
                    )
                ],
                "geographies": [
                    item.semantic_mapping()
                    for item in sorted(
                        self.geographies,
                        key=lambda item: (
                            item.state,
                            item.value or "",
                            item.missing_reason or "",
                        ),
                    )
                ],
                "coverage_lanes": [
                    item.semantic_mapping()
                    for item in sorted(
                        self.coverage_lanes,
                        key=lambda item: (
                            item.state,
                            item.value or "",
                            item.missing_reason or "",
                        ),
                    )
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class _ParsedFixture:
    fixture: Fixture
    source_name: str
    captured_at: TemporalValue
    request_scope: Mapping[str, str]
    request_scope_json: str
    completeness: str
    items: tuple[_NewsItem, ...]
    semantic_identity: str


def _parse_content(value: object, *, pointer: str) -> _Content:
    raw = _mapping(value, pointer, nonempty=True)
    _exact_keys(
        raw,
        {
            "state",
            "headline",
            "body",
            "summary",
            "source_url",
            "missing_reason",
        },
        pointer,
    )
    state = _string(raw["state"], f"{pointer}/state")
    if state not in _CONTENT_STATES:
        raise _fail("News content state is unsupported")
    headline = _nullable_string(raw["headline"], f"{pointer}/headline")
    body = _nullable_string(raw["body"], f"{pointer}/body")
    summary = _nullable_string(raw["summary"], f"{pointer}/summary")
    source_url = _nullable_string(raw["source_url"], f"{pointer}/source_url")
    missing_reason = _nullable_string(raw["missing_reason"], f"{pointer}/missing_reason")
    if state == "present":
        if not any((headline, body, summary)) or missing_reason is not None:
            raise _fail("Present news content requires text and forbids a missing reason")
    elif any((headline, body, summary, source_url)) or missing_reason is None:
        raise _fail("Missing or redacted news content requires an explicit reason only")
    return _Content(
        state=state,
        headline=headline,
        body=body,
        summary=summary,
        source_url=source_url,
        missing_reason=missing_reason,
    )


def _validate_label_states(
    states: Sequence[str],
    *,
    label: str,
) -> None:
    if not states:
        raise _fail(f"{label} must report at least one explicit association state")
    missing_count = sum(state == "missing" for state in states)
    if missing_count:
        if missing_count != 1 or len(states) != 1:
            raise _fail(f"{label} cannot mix an explicit missing association with labels")


def _parse_symbols(value: object, *, pointer: str) -> tuple[_SymbolAssociation, ...]:
    result: list[_SymbolAssociation] = []
    keys_seen: set[tuple[str, str, str]] = set()
    for ordinal, value_item in enumerate(
        _array(value, pointer, maximum=_MAX_LABELS_PER_ITEM, nonempty=True), start=1
    ):
        raw = _mapping(value_item, f"{pointer}/{ordinal}", nonempty=True)
        _exact_keys(
            raw,
            {
                "state",
                "instrument_id",
                "provider",
                "provider_symbol",
                "missing_reason",
            },
            f"{pointer}/{ordinal}",
        )
        state = _string(raw["state"], f"{pointer}/{ordinal}/state")
        if state not in _ASSOCIATION_STATES:
            raise _fail("News symbol association state is unsupported")
        instrument_id = _nullable_string(raw["instrument_id"], f"{pointer}/{ordinal}/instrument_id")
        provider = _nullable_string(raw["provider"], f"{pointer}/{ordinal}/provider")
        provider_symbol = _nullable_string(
            raw["provider_symbol"], f"{pointer}/{ordinal}/provider_symbol"
        )
        missing_reason = _nullable_string(
            raw["missing_reason"], f"{pointer}/{ordinal}/missing_reason"
        )
        if state == "present":
            if (
                instrument_id is None
                or provider is None
                or provider_symbol is None
                or missing_reason is not None
            ):
                raise _fail("Present news symbols require a full stable source label")
            key = (instrument_id, provider, provider_symbol)
            if key in keys_seen:
                raise _fail("News symbol labels are ambiguous")
            keys_seen.add(key)
        elif any((instrument_id, provider, provider_symbol)) or missing_reason is None:
            raise _fail("Missing news symbols require an explicit missing reason")
        result.append(
            _SymbolAssociation(
                state=state,
                instrument_id=instrument_id,
                provider=provider,
                provider_symbol=provider_symbol,
                missing_reason=missing_reason,
            )
        )
    _validate_label_states([item.state for item in result], label="news symbols")
    return tuple(result)


def _parse_topics(value: object, *, pointer: str) -> tuple[_TopicAssociation, ...]:
    result: list[_TopicAssociation] = []
    keys_seen: set[str] = set()
    for ordinal, value_item in enumerate(
        _array(value, pointer, maximum=_MAX_LABELS_PER_ITEM, nonempty=True), start=1
    ):
        raw = _mapping(value_item, f"{pointer}/{ordinal}", nonempty=True)
        _exact_keys(
            raw,
            {"state", "topic", "confidence", "missing_reason"},
            f"{pointer}/{ordinal}",
        )
        state = _string(raw["state"], f"{pointer}/{ordinal}/state")
        if state not in _ASSOCIATION_STATES:
            raise _fail("News topic association state is unsupported")
        topic = _nullable_string(raw["topic"], f"{pointer}/{ordinal}/topic")
        confidence, confidence_text = _confidence(
            raw["confidence"], f"{pointer}/{ordinal}/confidence"
        )
        missing_reason = _nullable_string(
            raw["missing_reason"], f"{pointer}/{ordinal}/missing_reason"
        )
        if state == "present":
            if topic is None or missing_reason is not None:
                raise _fail("Present news topics require a topic and forbid a missing reason")
            if topic in keys_seen:
                raise _fail("News topic labels are ambiguous")
            keys_seen.add(topic)
        elif topic is not None or confidence is not None or missing_reason is None:
            raise _fail("Missing news topics require an explicit missing reason")
        result.append(
            _TopicAssociation(
                state=state,
                topic=topic,
                confidence=confidence,
                confidence_text=confidence_text,
                missing_reason=missing_reason,
            )
        )
    _validate_label_states([item.state for item in result], label="news topics")
    return tuple(result)


def _parse_text_labels(
    value: object,
    *,
    pointer: str,
    field: str,
) -> tuple[_TextAssociation, ...]:
    result: list[_TextAssociation] = []
    keys_seen: set[str] = set()
    for ordinal, value_item in enumerate(
        _array(value, pointer, maximum=_MAX_LABELS_PER_ITEM, nonempty=True), start=1
    ):
        raw = _mapping(value_item, f"{pointer}/{ordinal}", nonempty=True)
        _exact_keys(
            raw,
            {"state", field, "missing_reason"},
            f"{pointer}/{ordinal}",
        )
        state = _string(raw["state"], f"{pointer}/{ordinal}/state")
        if state not in _ASSOCIATION_STATES:
            raise _fail(f"News {field} association state is unsupported")
        item = _nullable_string(raw[field], f"{pointer}/{ordinal}/{field}")
        missing_reason = _nullable_string(
            raw["missing_reason"], f"{pointer}/{ordinal}/missing_reason"
        )
        if state == "present":
            if item is None or missing_reason is not None:
                raise _fail(f"Present news {field} labels require a value")
            if item in keys_seen:
                raise _fail(f"News {field} labels are ambiguous")
            keys_seen.add(item)
        elif item is not None or missing_reason is None:
            raise _fail(f"Missing news {field} labels require an explicit missing reason")
        result.append(
            _TextAssociation(
                state=state,
                value=item,
                missing_reason=missing_reason,
            )
        )
    _validate_label_states([item.state for item in result], label=f"news {field}")
    return tuple(result)


def _parse_labels(value: object, *, pointer: str) -> tuple[
    tuple[_SymbolAssociation, ...],
    tuple[_TopicAssociation, ...],
    tuple[_TextAssociation, ...],
    tuple[_TextAssociation, ...],
]:
    raw = _mapping(value, pointer, nonempty=True)
    _exact_keys(
        raw,
        {"symbols", "topics", "geographies", "coverage_lanes"},
        pointer,
    )
    return (
        _parse_symbols(raw["symbols"], pointer=f"{pointer}/symbols"),
        _parse_topics(raw["topics"], pointer=f"{pointer}/topics"),
        _parse_text_labels(
            raw["geographies"],
            pointer=f"{pointer}/geographies",
            field="geography_code",
        ),
        _parse_text_labels(
            raw["coverage_lanes"],
            pointer=f"{pointer}/coverage_lanes",
            field="coverage_lane",
        ),
    )


def _parse_item(value: object, *, source_row: int) -> _NewsItem:
    pointer = f"items/{source_row}"
    raw = _mapping(value, pointer, nonempty=True)
    _exact_keys(
        raw,
        {
            "source_item_id",
            "source_kind",
            "published_at",
            "published_precision",
            "content",
            "item_state",
            "retraction_reason",
            "labels",
        },
        pointer,
    )
    source_kind = _string(raw["source_kind"], f"{pointer}/source_kind")
    if source_kind not in _SOURCE_KINDS:
        raise _fail("News source kind is unsupported")
    published_at = _temporal(
        raw["published_at"],
        raw["published_precision"],
        f"{pointer}/published_at",
        allow_unknown=True,
    )
    content = _parse_content(raw["content"], pointer=f"{pointer}/content")
    item_state = _string(raw["item_state"], f"{pointer}/item_state")
    if item_state not in _ITEM_STATES:
        raise _fail("News item state is unsupported")
    retraction_reason = _nullable_string(
        raw["retraction_reason"], f"{pointer}/retraction_reason"
    )
    if (item_state == "active" and retraction_reason is not None) or (
        item_state == "retracted" and retraction_reason is None
    ):
        raise _fail("News retraction state and reason are inconsistent")
    symbols, topics, geographies, coverage_lanes = _parse_labels(
        raw["labels"], pointer=f"{pointer}/labels"
    )
    return _NewsItem(
        source_item_id=_string(raw["source_item_id"], f"{pointer}/source_item_id"),
        source_kind=source_kind,
        published_at=published_at,
        content=content,
        item_state=item_state,
        retraction_reason=retraction_reason,
        symbols=symbols,
        topics=topics,
        geographies=geographies,
        coverage_lanes=coverage_lanes,
        source_row=source_row,
    )


def _parse_payload(payload: bytes) -> tuple[
    str,
    TemporalValue,
    Mapping[str, str],
    str,
    str,
    tuple[_NewsItem, ...],
    str,
]:
    if len(payload) > _MAX_FIXTURE_BYTES:
        raise ResourceLimitError("Stage 4 news fixture exceeds the reviewed byte bound")
    raw = _mapping(loads_strict(payload, max_bytes=_MAX_FIXTURE_BYTES), "fixture", nonempty=True)
    _exact_keys(
        raw,
        {
            "fixture_schema",
            "source_name",
            "captured_at",
            "captured_precision",
            "request_scope",
            "completeness",
            "items",
        },
        "fixture",
    )
    if raw["fixture_schema"] != _NORMALIZATION_VERSION:
        raise _fail("Stage 4 news fixture schema is unsupported")
    source_name = _string(raw["source_name"], "source_name")
    captured_at = _temporal(
        raw["captured_at"],
        raw["captured_precision"],
        "captured_at",
    )
    assert captured_at is not None
    if captured_at.precision is not TemporalPrecision.DATETIME:
        raise _fail("News local-capture availability requires an aware datetime")
    request_scope, request_scope_json = _scope(raw["request_scope"])
    completeness = _string(raw["completeness"], "completeness")
    if completeness not in {"complete", "partial"}:
        raise _fail("News snapshot completeness is unsupported")
    raw_items = _array(raw["items"], "items", maximum=_MAX_ITEMS, nonempty=True)
    items = tuple(
        _parse_item(item, source_row=ordinal)
        for ordinal, item in enumerate(raw_items, start=1)
    )
    if len({item.source_item_id for item in items}) != len(items):
        raise _fail("News fixture contains duplicate source item identities")
    semantic_payload = {
        "source_name": source_name,
        "request_scope": dict(request_scope),
        "completeness": completeness,
        "items": [
            item.semantic_mapping()
            for item in sorted(items, key=lambda item: item.source_item_id)
        ],
    }
    semantic_identity = _sha256_text(dumps_strict(semantic_payload))
    return (
        source_name,
        captured_at,
        request_scope,
        request_scope_json,
        completeness,
        items,
        semantic_identity,
    )


def fixture_semantic_identity(payload: bytes) -> str:
    """Return the reviewed semantic identity for a syntactically valid fixture.

    This is intentionally useful to the fixture authoring test only.  Import
    still verifies its manifest pin before it can open a write run.
    """

    return _parse_payload(payload)[-1]


def _verify_fixture(
    fixture: Fixture,
    *,
    source_name: str,
    captured_at: TemporalValue,
    request_scope_json: str,
    semantic_identity: str,
) -> None:
    if (
        fixture.byte_count != len(fixture.bytes)
        or hashlib.sha256(fixture.bytes).hexdigest() != fixture.sha256
    ):
        raise _fail("Fixture bytes do not match the reviewed digest")
    if (
        fixture.store != StoreRole.NEWS.value
        or fixture.ingestion_family_id != _COLLECTOR_ID
        or fixture.evidence_dataset_id != _EVIDENCE_DATASET_ID
        or fixture.canonical_dataset_id != _CANONICAL_DATASET_ID
        or fixture.identity_dataset_id is not None
        or fixture.provider != source_name
        or fixture.captured_at != captured_at.raw
        or dumps_strict(dict(fixture.request_scope)) != request_scope_json
        or fixture.expected_semantic_identity != semantic_identity
        or fixture.metadata.get("normalization_version") != _NORMALIZATION_VERSION
        or fixture.expected_warnings
    ):
        raise _fail("Stage 4 news fixture conflicts with its reviewed manifest declaration")


def _parse_fixture(fixture: Fixture) -> _ParsedFixture:
    (
        source_name,
        captured_at,
        request_scope,
        request_scope_json,
        completeness,
        items,
        semantic_identity,
    ) = _parse_payload(fixture.bytes)
    _verify_fixture(
        fixture,
        source_name=source_name,
        captured_at=captured_at,
        request_scope_json=request_scope_json,
        semantic_identity=semantic_identity,
    )
    return _ParsedFixture(
        fixture=fixture,
        source_name=source_name,
        captured_at=captured_at,
        request_scope=request_scope,
        request_scope_json=request_scope_json,
        completeness=completeness,
        items=items,
        semantic_identity=semantic_identity,
    )


def _artifact_id(parsed: _ParsedFixture) -> str:
    return stable_id(
        "stage4_news_artifact",
        _EVIDENCE_DATASET_ID,
        parsed.fixture.sha256,
        _sha256_text(parsed.request_scope_json),
    )


def _snapshot_id(parsed: _ParsedFixture) -> str:
    return stable_id(
        "stage4_news_snapshot",
        _CANONICAL_DATASET_ID,
        parsed.semantic_identity,
    )


def _item_id(source_name: str, source_item_id: str) -> str:
    return stable_id("stage4_news_item", source_name, source_item_id)


def _content_identity(source_name: str, item: _NewsItem) -> str:
    return _sha256_text(
        dumps_strict(
            {
                "source_name": source_name,
                "item": item.semantic_mapping(),
            }
        )
    )


def _version_id(item_id: str, content_identity: str) -> str:
    return stable_id("stage4_news_item_version", item_id, content_identity)


def _association_id(
    family: str,
    version_id: str,
    ordinal: int,
    material: Mapping[str, object],
) -> str:
    return stable_id(
        f"stage4_news_{family}",
        version_id,
        str(ordinal),
        _sha256_text(dumps_strict(dict(material))),
    )


def _insert_labels(
    connection: sqlite3.Connection,
    *,
    item: _NewsItem,
    version_id: str,
    snapshot_id: str,
    run_id: str,
    available_at: TemporalValue,
) -> int:
    written = 0
    for ordinal, association in enumerate(item.symbols, start=1):
        connection.execute(
            """
            INSERT INTO news_item_symbols (
                news_item_symbol_id, news_item_version_id, instrument_id,
                provider, provider_symbol, association_state, missing_reason,
                available_at, available_precision, source_snapshot_id, run_id,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _association_id(
                    "symbol",
                    version_id,
                    ordinal,
                    association.semantic_mapping(),
                ),
                version_id,
                association.instrument_id,
                association.provider,
                association.provider_symbol,
                association.state,
                association.missing_reason,
                available_at.raw,
                available_at.precision.value,
                snapshot_id,
                run_id,
                item.source_row * 10_000 + ordinal,
            ),
        )
        written += 1
    for ordinal, association in enumerate(item.topics, start=1):
        connection.execute(
            """
            INSERT INTO news_item_topics (
                news_item_topic_id, news_item_version_id, topic, confidence,
                association_state, missing_reason, available_at,
                available_precision, source_snapshot_id, run_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _association_id(
                    "topic",
                    version_id,
                    ordinal,
                    association.semantic_mapping(),
                ),
                version_id,
                association.topic,
                association.confidence,
                association.state,
                association.missing_reason,
                available_at.raw,
                available_at.precision.value,
                snapshot_id,
                run_id,
                item.source_row * 10_000 + ordinal,
            ),
        )
        written += 1
    for ordinal, association in enumerate(item.geographies, start=1):
        connection.execute(
            """
            INSERT INTO news_item_geographies (
                news_item_geography_id, news_item_version_id, geography_code,
                association_state, missing_reason, available_at,
                available_precision, source_snapshot_id, run_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _association_id(
                    "geography",
                    version_id,
                    ordinal,
                    association.semantic_mapping(),
                ),
                version_id,
                association.value,
                association.state,
                association.missing_reason,
                available_at.raw,
                available_at.precision.value,
                snapshot_id,
                run_id,
                item.source_row * 10_000 + ordinal,
            ),
        )
        written += 1
    for ordinal, association in enumerate(item.coverage_lanes, start=1):
        connection.execute(
            """
            INSERT INTO news_item_coverage_lanes (
                news_item_coverage_lane_id, news_item_version_id, coverage_lane,
                association_state, missing_reason, available_at,
                available_precision, source_snapshot_id, run_id, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _association_id(
                    "coverage_lane",
                    version_id,
                    ordinal,
                    association.semantic_mapping(),
                ),
                version_id,
                association.value,
                association.state,
                association.missing_reason,
                available_at.raw,
                available_at.precision.value,
                snapshot_id,
                run_id,
                item.source_row * 10_000 + ordinal,
            ),
        )
        written += 1
    return written


def _write_item(
    connection: sqlite3.Connection,
    *,
    parsed: _ParsedFixture,
    item: _NewsItem,
    snapshot_id: str,
    run_id: str,
) -> int:
    item_id = _item_id(parsed.source_name, item.source_item_id)
    existing_item = connection.execute(
        """
        SELECT source_kind
        FROM news_items
        WHERE item_id=?
        """,
        (item_id,),
    ).fetchone()
    written = 0
    if existing_item is None:
        if item.item_state != "active":
            raise _fail("An unseen news item cannot begin as retracted")
        connection.execute(
            """
            INSERT INTO news_items (
                item_id, source_name, source_item_id, source_kind, created_run_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                parsed.source_name,
                item.source_item_id,
                item.source_kind,
                run_id,
            ),
        )
        written += 1
        previous = None
    else:
        if str(existing_item["source_kind"]) != item.source_kind:
            raise _fail("News source identity conflicts with immutable source kind")
        previous = connection.execute(
            """
            SELECT news_item_version_id, version_sequence, item_state
            FROM news_item_versions
            WHERE item_id=?
            ORDER BY version_sequence DESC
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if previous is None:
            raise _fail("Existing news item has no immutable version history")

    content_identity = _content_identity(parsed.source_name, item)
    duplicate = connection.execute(
        """
        SELECT item_id
        FROM news_item_versions
        WHERE content_identity=?
        """,
        (content_identity,),
    ).fetchone()
    if duplicate is not None:
        raise _fail(
            "A write-worthy news snapshot cannot repeat an existing immutable item version"
        )

    if previous is not None and (
        str(previous["item_state"]) == "retracted" and item.item_state == "retracted"
    ):
        raise _fail("A retracted news item must be restored before another retraction")
    version_sequence = 1 if previous is None else int(previous["version_sequence"]) + 1
    supersedes = None if previous is None else str(previous["news_item_version_id"])
    version_id = _version_id(item_id, content_identity)
    connection.execute(
        """
        INSERT INTO news_item_versions (
            news_item_version_id, item_id, content_identity, headline, body,
            summary, source_url, published_at, published_precision, content_state,
            content_missing_reason, item_state, retraction_reason, available_at,
            available_precision, captured_at, captured_precision, version_sequence,
            supersedes_news_item_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            item_id,
            content_identity,
            item.content.headline,
            item.content.body,
            item.content.summary,
            item.content.source_url,
            item.published_at.raw if item.published_at is not None else None,
            item.published_at.precision.value
            if item.published_at is not None
            else TemporalPrecision.UNKNOWN.value,
            item.content.state,
            item.content.missing_reason,
            item.item_state,
            item.retraction_reason,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            parsed.captured_at.raw,
            parsed.captured_at.precision.value,
            version_sequence,
            supersedes,
            snapshot_id,
            run_id,
            item.source_row,
        ),
    )
    written += 1
    connection.execute(
        """
        INSERT INTO news_item_snapshot_membership (
            snapshot_id, news_item_version_id, run_id, source_row
        ) VALUES (?, ?, ?, ?)
        """,
        (snapshot_id, version_id, run_id, item.source_row),
    )
    written += 1
    written += _insert_labels(
        connection,
        item=item,
        version_id=version_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
        available_at=parsed.captured_at,
    )
    return written


class NewsStage4FixtureImporter:
    """Publish one manifest-pinned offline news capture atomically."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map, code_version="stage4.0.0")

    def import_fixture(self, fixture_id: str) -> IngestionReceipt:
        fixture = self._fixture_manifest.get(fixture_id)
        parsed = _parse_fixture(fixture)
        run_id = stable_id(
            "stage4_news_run",
            _CANONICAL_DATASET_ID,
            parsed.semantic_identity,
        )
        scope = {
            "fixture_id": parsed.fixture.id,
            "collector_id": _COLLECTOR_ID,
            "source_name": parsed.source_name,
            "request_scope": dict(parsed.request_scope),
            "completeness": parsed.completeness,
        }

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(connection, parsed, active_run_id)

        return self._coordinator.execute(
            role=StoreRole.NEWS,
            dataset_id=_CANONICAL_DATASET_ID,
            output_dataset_ids=_OUTPUT_DATASET_IDS,
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=_COLLECTOR_ID,
            scope=scope,
            started_at=parsed.captured_at.raw,
            completed_at=parsed.captured_at.raw,
            fetched_count=len(parsed.items),
            writer=writer,
        )

    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        parsed: _ParsedFixture,
        run_id: str,
    ) -> WriteResult:
        artifact_id = _artifact_id(parsed)
        snapshot_id = _snapshot_id(parsed)
        connection.execute(
            """
            INSERT INTO news_source_artifacts (
                artifact_id, source_name, content_sha256, media_type, byte_count,
                source_reference, request_scope_json, captured_at,
                captured_precision, available_at, available_precision, run_id
            ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                parsed.source_name,
                parsed.fixture.sha256,
                parsed.fixture.byte_count,
                parsed.fixture.resource_name,
                parsed.request_scope_json,
                parsed.captured_at.raw,
                parsed.captured_at.precision.value,
                parsed.captured_at.raw,
                parsed.captured_at.precision.value,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO news_snapshots (
                snapshot_id, semantic_identity, source_name, scope_json,
                completeness, captured_at, captured_precision, available_at,
                available_precision, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                parsed.semantic_identity,
                parsed.source_name,
                parsed.request_scope_json,
                parsed.completeness,
                parsed.captured_at.raw,
                parsed.captured_at.precision.value,
                parsed.captured_at.raw,
                parsed.captured_at.precision.value,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO news_snapshot_artifacts (
                snapshot_id, artifact_id, artifact_ordinal
            ) VALUES (?, ?, 1)
            """,
            (snapshot_id, artifact_id),
        )
        written = 3
        for item in parsed.items:
            written += _write_item(
                connection,
                parsed=parsed,
                item=item,
                snapshot_id=snapshot_id,
                run_id=run_id,
            )
        artifact = ArtifactWrite(
            artifact_id=artifact_id,
            dataset_id=_EVIDENCE_DATASET_ID,
            content_sha256=parsed.fixture.sha256,
            media_type="application/json",
            byte_count=parsed.fixture.byte_count,
            source_reference=parsed.fixture.resource_name,
            request_scope=dict(parsed.request_scope),
            captured_at=parsed.captured_at.raw,
            captured_precision=parsed.captured_at.precision.value,
            normalization_version=_NORMALIZATION_VERSION,
        )
        return WriteResult(
            written_count=written,
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=_CANONICAL_DATASET_ID,
                semantic_identity=parsed.semantic_identity,
                scope=dict(parsed.request_scope),
                completeness=parsed.completeness,
                row_count=len(parsed.items),
                captured_at=parsed.captured_at.raw,
                captured_precision=parsed.captured_at.precision.value,
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage4_news_quality",
                        run_id,
                        _CANONICAL_DATASET_ID,
                    ),
                    dataset_id=_CANONICAL_DATASET_ID,
                    rule_id="fixture.news_batch_contract",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "source_name": parsed.source_name,
                        "fetched_count": len(parsed.items),
                        "written_count": written,
                        "completeness": parsed.completeness,
                        "availability_basis": "local_capture",
                    },
                ),
            ),
        )
