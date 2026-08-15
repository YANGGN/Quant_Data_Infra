"""Read-only Stage 10 market-history reconciliation and private evidence.

Stage 10 is deliberately a manual, non-production FMP population profile.
This module has no transport, credential, scheduler, default path, or store
write path.  It instead proves that a *completed* explicit market cohort is
consistent with the frozen Stage 10 scope and every retained raw FMP body.

The verification deliberately distinguishes a current constituent snapshot
from price history: a price before a member was captured says nothing about
historical index membership.  No historical-membership assertion is emitted or
accepted here.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..json_codec import dumps_strict, loads_strict
from ..market.fmp_bulk_daily_prices import (
    FMP_STAGE10_PRICE_PATH,
    FmpStage10PriceCapture,
    FmpStage10PriceRow,
    FmpStage10UniverseCapture,
    parse_fmp_stage10_price_response,
    parse_fmp_stage10_universe_response,
)
from ..market.stage10_history_importer import (
    STAGE10_CURRENCY_SEGMENT,
    STAGE10_DAILY_HISTORY_COLLECTOR_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_RESOURCE,
    STAGE10_MIGRATION_SHA256,
    STAGE10_PRICE_NORMALIZATION_VERSION,
    STAGE10_PRICE_VARIANT,
    STAGE10_UNIVERSE_COLLECTOR_ID,
    STAGE10_UNIVERSE_NORMALIZATION_VERSION,
    STAGE10_UNIVERSES_DATASET_ID,
)
from ..market.stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    MajorIndex,
    Stage10MarketScope,
    UniverseSource,
)
from ..registry import Registry
from ..stores import StoreMap, StoreRole, canonical_path_uri, read_connection, stable_id
from ..temporal import TemporalPrecision, TemporalValue
from .health import all_store_health


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_ATTEMPT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
_UNIVERSE_ORDER = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
    "curated_etfs",
    "major_indexes",
)
_WATCHLIST_COUNTS = {
    "curated_etfs": 95,
    "major_indexes": 15,
}
_SOURCE_UNIVERSE_IDS = _UNIVERSE_ORDER[:3]
_UNIVERSE_OUTPUT_DATASETS = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_UNIVERSES_DATASET_ID,
)
_PRICE_OUTPUT_DATASETS = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
)
_MAX_RECEIPT_BYTES = 512 * 1024
_CANDIDATE_STATE = "private_candidate_only_no_operational_promotion"
_FAILED_SYMBOL = re.compile(r"[A-Za-z0-9.^-]{1,32}\Z")
_FAILURE_REASONS = frozenset(
    {
        "fmp_price_history_unavailable",
        "operator_authorized_prior_failure",
    }
)
_FAILURE_PROVENANCE = frozenset(
    {
        "fmp_http_200_application_json",
        "stage10_operator_authorized_seed",
    }
)
_FAILURE_REASON_PROVENANCE = {
    "fmp_price_history_unavailable": "fmp_http_200_application_json",
    "operator_authorized_prior_failure": "stage10_operator_authorized_seed",
}
_FAILURE_REQUIRED_FIELDS = frozenset({"symbol", "reason", "provenance"})
_FAILURE_OPTIONAL_FIELDS = frozenset({"response_metadata", "response_sha256"})
_FAILURE_RESPONSE_METADATA_FIELDS = frozenset(
    {"body_byte_count", "content_type", "status"}
)
_MAX_FAILURE_RESPONSE_BYTES = 16_777_216


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 {field_name} must be a lowercase SHA-256 digest")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"Stage 10 {field_name} must be boolean")
    return value


def _require_text(value: object, field_name: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValidationError(f"Stage 10 {field_name} must be bounded normalized text")
    return value


def _require_int(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Stage 10 {field_name} must be an integer in range")
    return value


def _canonical_datetime(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Stage 10 {field_name} must be an aware datetime")
    parsed = TemporalValue.parse(value, pointer=f"/{field_name}")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise ValidationError(f"Stage 10 {field_name} must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _datetime_text(value: object, field_name: str) -> str:
    """Validate an aware datetime without leaking a physical-store detail."""

    normalized = _canonical_datetime(value, field_name)
    if value != normalized:
        raise ValidationError(f"Stage 10 {field_name} must use canonical UTC text")
    return normalized


def _decimal_text(value: object, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError(f"Stage 10 {field_name} must be a finite decimal")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Stage 10 {field_name} must be a finite decimal") from exc
    if not decimal.is_finite():
        raise ValidationError(f"Stage 10 {field_name} must be a finite decimal")
    if decimal.is_zero():
        return "0"
    return format(decimal.normalize(), "f")


def _freeze_json(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Stage 10 {field_name} keys must be strings")
            frozen[key] = _freeze_json(child, field_name)
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(child, field_name) for child in value)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError(f"Stage 10 {field_name} must be strict JSON material")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _json_from_column(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, str):
        raise ValidationError(f"Stage 10 {field_name} must be strict JSON")
    try:
        parsed = loads_strict(value, max_bytes=128 * 1024)
    except (ResourceLimitError, ValidationError):
        raise ValidationError(f"Stage 10 {field_name} must be strict JSON") from None
    if not isinstance(parsed, Mapping) or not all(isinstance(key, str) for key in parsed):
        raise ValidationError(f"Stage 10 {field_name} must be a JSON object")
    return dict(parsed)


def _assert_json_column(value: object, expected: Mapping[str, object], field_name: str) -> None:
    parsed = _json_from_column(value, field_name)
    rendered = dumps_strict(dict(expected))
    if parsed != dict(expected) or value != rendered:
        raise ValidationError(f"Stage 10 {field_name} differs from its reviewed scope")


def _scope_digest(scope: Mapping[str, object]) -> str:
    return _sha256_json(dict(scope))


def _expected_source_scope(source: UniverseSource, scope: Stage10MarketScope) -> dict[str, object]:
    return {
        "provider": "fmp",
        "endpoint_path": source.endpoint_path,
        "universe_id": source.id,
        "membership_relation": source.membership_relation,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }


def _expected_watchlist_scope(universe_id: str, scope: Stage10MarketScope) -> dict[str, object]:
    return {
        "provider": "reviewed_local_manifest",
        "universe_id": universe_id,
        "membership_relation": "reviewed_instrument_watchlist",
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }


def _assert_no_russell(value: object, field_name: str) -> None:
    if isinstance(value, str) and (
        value in {"^RUT", "IWM"} or "russell" in value.casefold()
    ):
        raise ValidationError(f"Stage 10 {field_name} contains a forbidden Russell instrument")


def normalize_stage10_failed_records(
    failed_records: object,
) -> tuple[Mapping[str, object], ...]:
    """Return the one closed, immutable Stage 10 failed-symbol manifest.

    A failure record is deliberately a bounded statement about an approved
    roster symbol, not retained provider evidence.  It therefore has no URL,
    headers, response body, request parameters, path, or credential-shaped
    field.  Live FMP failures retain only the reviewed status/content-type/
    byte-count metadata and their response digest; operator-authorized seed
    records deliberately retain neither response field.
    """

    if not isinstance(failed_records, tuple) or len(failed_records) > 1_024:
        raise ValidationError("Stage 10 failed-record manifest must be a bounded tuple")
    normalized: list[Mapping[str, object]] = []
    for record in failed_records:
        if not isinstance(record, Mapping):
            raise ValidationError("Stage 10 failed-symbol record must be a closed mapping")
        keys = frozenset(record)
        if (
            not all(isinstance(key, str) for key in keys)
            or not _FAILURE_REQUIRED_FIELDS.issubset(keys)
            or not keys.issubset(_FAILURE_REQUIRED_FIELDS | _FAILURE_OPTIONAL_FIELDS)
        ):
            raise ValidationError("Stage 10 failed-symbol record has unapproved fields")
        symbol = _require_text(record.get("symbol"), "failed symbol", maximum=32)
        if _FAILED_SYMBOL.fullmatch(symbol) is None:
            raise ValidationError("Stage 10 failed symbol is invalid")
        _assert_no_russell(symbol, "failed symbol")
        reason = _require_text(record.get("reason"), "failure reason", maximum=64)
        provenance = _require_text(
            record.get("provenance"), "failure provenance", maximum=64
        )
        if (
            reason not in _FAILURE_REASONS
            or provenance not in _FAILURE_PROVENANCE
            or _FAILURE_REASON_PROVENANCE.get(reason) != provenance
        ):
            raise ValidationError("Stage 10 failed-symbol reason/provenance is invalid")

        response_metadata = record.get("response_metadata")
        response_sha256 = record.get("response_sha256")
        is_live_failure = reason == "fmp_price_history_unavailable"
        if is_live_failure:
            if not isinstance(response_metadata, Mapping) or "response_sha256" not in keys:
                raise ValidationError("Stage 10 live failed-symbol record lacks response binding")
            if (
                not all(isinstance(key, str) for key in response_metadata)
                or frozenset(response_metadata) != _FAILURE_RESPONSE_METADATA_FIELDS
                or isinstance(response_metadata.get("body_byte_count"), bool)
                or not isinstance(response_metadata.get("body_byte_count"), int)
                or not 0 <= response_metadata["body_byte_count"] <= _MAX_FAILURE_RESPONSE_BYTES
                or response_metadata.get("content_type") != "application/json"
                or response_metadata.get("status") != 200
            ):
                raise ValidationError("Stage 10 live failed-symbol response metadata is invalid")
            response_digest = _require_digest(
                response_sha256, "failed-symbol response_sha256"
            )
            material: dict[str, object] = {
                "symbol": symbol,
                "reason": reason,
                "provenance": provenance,
                "response_metadata": {
                    "body_byte_count": response_metadata["body_byte_count"],
                    "content_type": "application/json",
                    "status": 200,
                },
                "response_sha256": response_digest,
            }
        else:
            if "response_metadata" in keys or "response_sha256" in keys:
                raise ValidationError("Stage 10 seeded failed-symbol record cannot retain response data")
            material = {
                "symbol": symbol,
                "reason": reason,
                "provenance": provenance,
            }
        frozen = _freeze_json(material, "failed_records")
        if not isinstance(frozen, Mapping):
            raise ValidationError("Stage 10 failed-symbol record is invalid")
        normalized.append(frozen)

    ordered = tuple(sorted(normalized, key=lambda item: str(item["symbol"])))
    if len({str(item["symbol"]) for item in ordered}) != len(ordered):
        raise ValidationError("Stage 10 failed-symbol manifest contains duplicate symbols")
    return ordered


def stage10_failure_manifest_sha256(failed_records: object) -> str:
    """Digest the exact canonical failed-symbol manifest without side effects."""

    normalized = normalize_stage10_failed_records(failed_records)
    return _sha256_json([_thaw_json(item) for item in normalized])


def _failed_tickers_for_roster(
    failed_records: tuple[Mapping[str, object], ...],
    planned: Mapping[str, "_ExpectedInstrument"],
) -> frozenset[str]:
    roster = frozenset(planned)
    failed = frozenset(str(item["symbol"]) for item in failed_records)
    if not failed.issubset(roster):
        raise ValidationError("Stage 10 failed-symbol manifest includes an unscoped symbol")
    return failed


def _validate_scope(scope: object) -> Stage10MarketScope:
    if not isinstance(scope, Stage10MarketScope):
        raise ValidationError("Stage 10 reconciliation requires a validated immutable scope")
    manifest = scope.manifest_mapping()
    if (
        scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or _sha256_json(manifest) != REVIEWED_STAGE10_MANIFEST_SHA256
        or scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or scope.provider != "fmp"
        or scope.price_history.endpoint_path != FMP_STAGE10_PRICE_PATH
        or scope.price_history.interval != "daily"
        or scope.price_history.history_policy
        != "earliest_available_per_provider_symbol"
        or scope.price_history.price_variant != STAGE10_PRICE_VARIANT
        or scope.price_history.currency_policy != "provider_declared_no_conversion"
        or tuple(source.id for source in scope.universe_sources) != _SOURCE_UNIVERSE_IDS
        or len(scope.curated_etfs) != 95
        or len(scope.major_indexes) != 15
    ):
        raise ValidationError("Stage 10 scope is not the reviewed immutable profile")
    if any(
        source.id not in _SOURCE_UNIVERSE_IDS
        or "russell" in source.id.casefold()
        or "russell" in source.canonical_index_id.casefold()
        for source in scope.universe_sources
    ):
        raise ValidationError("Stage 10 scope includes an unapproved Russell universe")
    for item in scope.major_indexes:
        if item.provider_symbol == "^RUT" or "russell" in item.canonical_id.casefold():
            raise ValidationError("Stage 10 scope includes an unapproved Russell index")
    return scope


def _collector_by_id(registry: Registry, collector_id: str) -> Mapping[str, Any]:
    matches = [item for item in registry.collectors if item.get("id") == collector_id]
    if len(matches) != 1:
        raise ValidationError("Stage 10 collector is not registered exactly once")
    return matches[0]


def _validate_registry(registry: object) -> Registry:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.6.0"
        or registry.registry_version != "2.8.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 10 reconciliation requires registry 1.6.0/2.8.0")

    migration_matches = [item for item in registry.migrations if item.id == STAGE10_MIGRATION_ID]
    if len(migration_matches) != 1:
        raise ValidationError("Stage 10 migration is not registered exactly once")
    migration = migration_matches[0]
    if (
        migration.store != StoreRole.MARKET.value
        or migration.ordinal != 10
        or migration.resource != STAGE10_MIGRATION_RESOURCE
        or migration.sha256 != STAGE10_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
        or registry.store(StoreRole.MARKET.value).migration_order[-1] != STAGE10_MIGRATION_ID
    ):
        raise ValidationError("Stage 10 migration declaration is invalid")

    datasets = {item.id: item for item in registry.datasets}
    expected_dataset_collectors = {
        STAGE10_EVIDENCE_DATASET_ID: frozenset(
            {STAGE10_UNIVERSE_COLLECTOR_ID, STAGE10_DAILY_HISTORY_COLLECTOR_ID}
        ),
        STAGE10_INSTRUMENTS_DATASET_ID: frozenset({STAGE10_UNIVERSE_COLLECTOR_ID}),
        STAGE10_UNIVERSES_DATASET_ID: frozenset({STAGE10_UNIVERSE_COLLECTOR_ID}),
        STAGE10_DAILY_PRICES_DATASET_ID: frozenset({STAGE10_DAILY_HISTORY_COLLECTOR_ID}),
    }
    for dataset_id, collector_ids in expected_dataset_collectors.items():
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != StoreRole.MARKET.value
            or not dataset.active
            or frozenset(dataset.collector_ids) != collector_ids
            or dataset.tool_ids
            or dataset.dashboard_ids
            or dataset.export_ids
        ):
            raise ValidationError("Stage 10 dataset declaration is invalid")

    expected_collectors = (
        (
            STAGE10_UNIVERSE_COLLECTOR_ID,
            "market.stage10_fmp_universes",
            (),
            _UNIVERSE_OUTPUT_DATASETS,
            {
                "max_bytes": 4_194_304,
                "max_requests": 3,
                "max_rows": 600,
                "max_seconds": 135,
            },
        ),
        (
            STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            "market.stage10_fmp_daily_history",
            (STAGE10_INSTRUMENTS_DATASET_ID, STAGE10_UNIVERSES_DATASET_ID),
            _PRICE_OUTPUT_DATASETS,
            {
                "max_bytes": 16_777_216,
                "max_requests": 1,
                "max_rows": 30_000,
                "max_seconds": 45,
            },
        ),
    )
    for collector_id, handler, inputs, outputs, bounds in expected_collectors:
        collector = _collector_by_id(registry, collector_id)
        if (
            collector.get("handler") != handler
            or collector.get("network") is not True
            or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
            or tuple(collector.get("input_datasets", ())) != inputs
            or tuple(collector.get("output_datasets", ())) != outputs
            or collector.get("schedule_eligibility") != {"mode": "manual_only"}
            or collector.get("retry_policy")
            != {
                "backoff": "none_operator_resume",
                "honor_retry_after": False,
                "max_attempts": 1,
                "transient_classes": ["http_429", "http_500", "transport"],
            }
            or collector.get("workload_bounds") != bounds
            or collector.get("version") != "1.0.0"
        ):
            raise ValidationError("Stage 10 collector declaration is invalid")
    _require_digest(registry.source_sha256, "registry_source_sha256")
    return registry


@dataclass(frozen=True, slots=True)
class _ExpectedInstrument:
    provider_symbol: str
    asset_type: str
    display_name: str | None
    first_trade_date: str | None

    @property
    def identity_seed_sha256(self) -> str:
        return _sha256_json(
            {
                "identity_version": "stage10.fmp.instrument.v1",
                "provider": "fmp",
                "provider_symbol": self.provider_symbol,
                "asset_type": self.asset_type,
                "currency_segment": STAGE10_CURRENCY_SEGMENT,
            }
        )

    @property
    def instrument_id(self) -> str:
        return stable_id("stage10_instrument", self.identity_seed_sha256)

    def membership_mapping(self) -> dict[str, str]:
        return {
            "instrument_id": self.instrument_id,
            "provider_symbol": self.provider_symbol,
            "asset_type": self.asset_type,
        }


@dataclass(frozen=True, slots=True)
class _ExpectedUniverse:
    universe_id: str
    universe_kind: str
    publisher: str
    source_authority: str
    membership_relation: str
    members: tuple[_ExpectedInstrument, ...]
    request_scope: Mapping[str, object]
    raw_bytes: bytes
    raw_bytes_sha256: str
    endpoint_path: str | None
    capture_id: str | None
    artifact_id: str
    snapshot_id: str
    run_id: str
    semantic_identity: str
    request_scope_sha256: str
    membership_sha256: str


@dataclass(frozen=True, slots=True)
class _PriceCapture:
    capture_id: str
    instrument: _ExpectedInstrument
    raw: FmpStage10PriceCapture
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    semantic_identity: str
    artifact_id: str
    snapshot_id: str
    run_id: str
    captured_at: str


@dataclass(frozen=True, slots=True)
class _Version:
    version_id: str
    instrument_id: str
    trade_date: str
    correction_sequence: int
    supersedes_version_id: str | None
    capture_id: str
    source_row: int
    values: tuple[str, str, str, str, int]



def _merge_instrument(
    planned: dict[str, _ExpectedInstrument],
    candidate: _ExpectedInstrument,
) -> _ExpectedInstrument:
    _assert_no_russell(candidate.provider_symbol, "provider symbol")
    existing = planned.get(candidate.provider_symbol)
    if existing is None:
        planned[candidate.provider_symbol] = candidate
        return candidate
    # Display labels may legitimately differ across FMP constituent endpoints.
    # The importer binds the first encountered label while identity stays on
    # provider symbol, type, and the reviewed first-trade date.
    if (
        existing.asset_type != candidate.asset_type
        or existing.first_trade_date != candidate.first_trade_date
    ):
        raise ValidationError("Stage 10 scope gives one symbol incompatible identities")
    return existing


def _membership_sha256(members: tuple[_ExpectedInstrument, ...]) -> str:
    return _sha256_json(
        [
            item.membership_mapping()
            for item in sorted(members, key=lambda item: item.instrument_id)
        ]
    )


def _expected_universe_identity(
    *,
    request_scope: Mapping[str, object],
    membership_sha256: str,
) -> tuple[str, str, str, str, str]:
    request_scope_sha256 = _scope_digest(request_scope)
    semantic_identity = _sha256_json(
        {
            "collector_id": STAGE10_UNIVERSE_COLLECTOR_ID,
            "canonical_dataset_id": STAGE10_UNIVERSES_DATASET_ID,
            "request_scope": dict(request_scope),
            "membership_sha256": membership_sha256,
        }
    )
    return (
        request_scope_sha256,
        semantic_identity,
        stable_id("stage10_universe_run", STAGE10_UNIVERSES_DATASET_ID, semantic_identity),
        stable_id("stage10_universe_snapshot", STAGE10_UNIVERSES_DATASET_ID, semantic_identity),
        stable_id("stage10_universe_capture", STAGE10_EVIDENCE_DATASET_ID, semantic_identity),
    )


def _strict_row_bytes(value: object, field_name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValidationError(f"Stage 10 {field_name} raw evidence is missing")
    result = bytes(value)
    if not result:
        raise ValidationError(f"Stage 10 {field_name} raw evidence is missing")
    return result


def _source_capture_rows(connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, universe_id, endpoint_path,
                   scope_manifest_sha256, request_scope_json, request_scope_sha256,
                   response_sha256, response_bytes, http_status, content_type,
                   semantic_identity, completeness, artifact_id, snapshot_id,
                   captured_at, captured_precision, as_of_date, row_count,
                   normalization_version, run_id
            FROM stage10_universe_captures
            ORDER BY universe_id, capture_id
            """
        )
    )
    if len(rows) != len(_SOURCE_UNIVERSE_IDS):
        raise ValidationError("Stage 10 requires exactly three raw current-universe captures")
    result: dict[str, sqlite3.Row] = {}
    for row in rows:
        universe_id = row["universe_id"]
        if not isinstance(universe_id, str) or universe_id in result:
            raise ValidationError("Stage 10 universe capture identity is invalid")
        result[universe_id] = row
    if set(result) != set(_SOURCE_UNIVERSE_IDS):
        raise ValidationError("Stage 10 raw universe captures include an unexpected universe")
    return {universe_id: result[universe_id] for universe_id in _SOURCE_UNIVERSE_IDS}


def _verify_source_capture_basics(
    row: sqlite3.Row,
    source: UniverseSource,
    scope: Stage10MarketScope,
) -> FmpStage10UniverseCapture:
    request_scope = _expected_source_scope(source, scope)
    response_bytes = _strict_row_bytes(row["response_bytes"], "universe capture")
    response_sha256 = _require_digest(row["response_sha256"], "universe response_sha256")
    if _sha256_bytes(response_bytes) != response_sha256:
        raise ValidationError("Stage 10 universe raw response digest does not match")
    try:
        capture = parse_fmp_stage10_universe_response(
            endpoint_path=source.endpoint_path,
            body=response_bytes,
        )
    except (ResourceLimitError, ValidationError):
        raise ValidationError("Stage 10 universe raw response cannot be reparsed") from None
    if (
        row["dataset_id"] != STAGE10_EVIDENCE_DATASET_ID
        or row["provider"] != "fmp"
        or row["universe_id"] != source.id
        or row["endpoint_path"] != source.endpoint_path
        or row["scope_manifest_sha256"] != scope.manifest_sha256
        or row["http_status"] != 200
        or row["content_type"] != "application/json"
        or row["completeness"] != "complete"
        or row["normalization_version"] != STAGE10_UNIVERSE_NORMALIZATION_VERSION
        or row["response_sha256"] != capture.raw_bytes_sha256
        or row["row_count"] != len(capture.constituents)
    ):
        raise ValidationError("Stage 10 universe capture metadata is invalid")
    if not (
        source.expected_members_min
        <= row["row_count"]
        <= source.expected_members_max
    ):
        raise ValidationError("Stage 10 current-universe row count is outside the reviewed bounds")
    _assert_json_column(row["request_scope_json"], request_scope, "universe request scope")
    if row["request_scope_sha256"] != _scope_digest(request_scope):
        raise ValidationError("Stage 10 universe request-scope digest is invalid")
    captured_at = _datetime_text(row["captured_at"], "universe captured_at")
    if (
        row["captured_precision"] != "datetime"
        or row["as_of_date"] != captured_at[:10]
        or not isinstance(row["capture_id"], str)
        or not isinstance(row["artifact_id"], str)
        or not isinstance(row["snapshot_id"], str)
        or not isinstance(row["run_id"], str)
    ):
        raise ValidationError("Stage 10 universe capture lineage metadata is invalid")
    symbols = tuple(item.symbol for item in capture.constituents)
    if (
        len(symbols) != len(set(symbols))
        or not set(source.required_symbols).issubset(symbols)
        or set(source.forbidden_symbols).intersection(symbols)
        or any(
            symbol in {"^RUT", "IWM"} or "russell" in symbol.casefold()
            for symbol in symbols
        )
    ):
        raise ValidationError("Stage 10 current-universe capture is outside the reviewed scope")
    return capture


def _expected_universes(
    connection: sqlite3.Connection,
    scope: Stage10MarketScope,
) -> tuple[tuple[_ExpectedUniverse, ...], dict[str, _ExpectedInstrument], dict[str, sqlite3.Row]]:
    source_rows = _source_capture_rows(connection)
    planned: dict[str, _ExpectedInstrument] = {}
    expected: list[_ExpectedUniverse] = []

    for source in scope.universe_sources:
        row = source_rows[source.id]
        capture = _verify_source_capture_basics(row, source, scope)
        members = tuple(
            _merge_instrument(
                planned,
                _ExpectedInstrument(
                    provider_symbol=item.symbol,
                    asset_type="equity",
                    display_name=item.name,
                    first_trade_date=None,
                ),
            )
            for item in capture.constituents
        )
        membership_sha256 = _membership_sha256(members)
        request_scope = _expected_source_scope(source, scope)
        request_scope_sha256, semantic, run_id, snapshot_id, capture_id = (
            _expected_universe_identity(
                request_scope=request_scope,
                membership_sha256=membership_sha256,
            )
        )
        artifact_id = stable_id(
            "stage10_universe_artifact",
            STAGE10_EVIDENCE_DATASET_ID,
            source.id,
            capture.raw_bytes_sha256,
            request_scope_sha256,
        )
        if (
            row["capture_id"] != capture_id
            or row["artifact_id"] != artifact_id
            or row["snapshot_id"] != snapshot_id
            or row["semantic_identity"] != semantic
            or row["run_id"] != run_id
        ):
            raise ValidationError("Stage 10 universe capture stable identities are invalid")
        expected.append(
            _ExpectedUniverse(
                universe_id=source.id,
                universe_kind="benchmark_constituent_universe",
                publisher=source.publisher,
                source_authority=source.source_authority,
                membership_relation=source.membership_relation,
                members=members,
                request_scope=MappingProxyType(request_scope),
                raw_bytes=capture.raw_bytes,
                raw_bytes_sha256=capture.raw_bytes_sha256,
                endpoint_path=source.endpoint_path,
                capture_id=capture_id,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                run_id=run_id,
                semantic_identity=semantic,
                request_scope_sha256=request_scope_sha256,
                membership_sha256=membership_sha256,
            )
        )

    watchlists: tuple[tuple[str, str, tuple[_ExpectedInstrument, ...]], ...] = (
        (
            "curated_etfs",
            "stage10_reviewed_scope",
            tuple(
                _ExpectedInstrument(
                    provider_symbol=item.symbol,
                    asset_type="etf",
                    display_name=None,
                    first_trade_date=item.first_trade_date,
                )
                for item in scope.curated_etfs
            ),
        ),
        (
            "major_indexes",
            "stage10_reviewed_scope",
            tuple(
                _ExpectedInstrument(
                    provider_symbol=item.provider_symbol,
                    asset_type="index",
                    display_name=item.name,
                    first_trade_date=None,
                )
                for item in scope.major_indexes
            ),
        ),
    )
    for universe_id, publisher, members in watchlists:
        merged_members = tuple(_merge_instrument(planned, member) for member in members)
        membership_sha256 = _membership_sha256(merged_members)
        request_scope = _expected_watchlist_scope(universe_id, scope)
        request_scope_sha256, semantic, run_id, snapshot_id, _ignored_capture = (
            _expected_universe_identity(
                request_scope=request_scope,
                membership_sha256=membership_sha256,
            )
        )
        raw_bytes = dumps_strict(
            {"scope_manifest": scope.manifest_mapping(), "universe_id": universe_id}
        ).encode("utf-8")
        raw_bytes_sha256 = _sha256_bytes(raw_bytes)
        artifact_id = stable_id(
            "stage10_scope_artifact",
            STAGE10_EVIDENCE_DATASET_ID,
            universe_id,
            raw_bytes_sha256,
            request_scope_sha256,
        )
        expected.append(
            _ExpectedUniverse(
                universe_id=universe_id,
                universe_kind="instrument_watchlist",
                publisher=publisher,
                source_authority="reviewed_local_manifest",
                membership_relation="reviewed_instrument_watchlist",
                members=merged_members,
                request_scope=MappingProxyType(request_scope),
                raw_bytes=raw_bytes,
                raw_bytes_sha256=raw_bytes_sha256,
                endpoint_path=None,
                capture_id=None,
                artifact_id=artifact_id,
                snapshot_id=snapshot_id,
                run_id=run_id,
                semantic_identity=semantic,
                request_scope_sha256=request_scope_sha256,
                membership_sha256=membership_sha256,
            )
        )
    if tuple(item.universe_id for item in expected) != _UNIVERSE_ORDER:
        raise ValidationError("Stage 10 universe ordering is invalid")
    if len(planned) > scope.bounds.max_instruments:
        raise ValidationError("Stage 10 deduplicated roster exceeds the reviewed bound")
    if any(symbol in {"^RUT", "IWM"} or "russell" in symbol.casefold() for symbol in planned):
        raise ValidationError("Stage 10 deduplicated roster includes Russell")
    return tuple(expected), planned, source_rows


def _expected_price_scope(
    instrument: _ExpectedInstrument,
    scope: Stage10MarketScope,
) -> dict[str, object]:
    return {
        "provider": "fmp",
        "endpoint_path": scope.price_history.endpoint_path,
        "symbol": instrument.provider_symbol,
        "interval": scope.price_history.interval,
        "history_policy": scope.price_history.history_policy,
        "price_variant": scope.price_history.price_variant,
        "currency_policy": scope.price_history.currency_policy,
        "volume_policy": scope.price_history.volume_policy,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }


def _verify_instruments(
    connection: sqlite3.Connection,
    planned: Mapping[str, _ExpectedInstrument],
) -> tuple[dict[str, sqlite3.Row], str]:
    rows = tuple(
        connection.execute(
            """
            SELECT instrument_id, provider, provider_symbol, asset_type, display_name,
                   exchange_code, currency_segment, first_trade_date,
                   identity_seed_sha256, captured_at, captured_precision, run_id
            FROM stage10_instruments
            ORDER BY instrument_id
            """
        )
    )
    if len(rows) != len(planned):
        raise ValidationError("Stage 10 instrument roster is incomplete or unexpected")
    expected_by_id = {item.instrument_id: item for item in planned.values()}
    actual_by_id: dict[str, sqlite3.Row] = {}
    for row in rows:
        instrument_id = row["instrument_id"]
        if not isinstance(instrument_id, str) or instrument_id in actual_by_id:
            raise ValidationError("Stage 10 instrument identity is invalid")
        expected = expected_by_id.get(instrument_id)
        if expected is None:
            raise ValidationError("Stage 10 has an unexpected instrument")
        if (
            row["provider"] != "fmp"
            or row["provider_symbol"] != expected.provider_symbol
            or row["asset_type"] != expected.asset_type
            or row["display_name"] != expected.display_name
            or row["exchange_code"] is not None
            or row["currency_segment"] != STAGE10_CURRENCY_SEGMENT
            or row["first_trade_date"] != expected.first_trade_date
            or row["identity_seed_sha256"] != expected.identity_seed_sha256
            or row["captured_precision"] != "datetime"
            or not isinstance(row["run_id"], str)
            or not row["run_id"]
        ):
            raise ValidationError("Stage 10 instrument canonical identity is invalid")
        _datetime_text(row["captured_at"], "instrument captured_at")
        _assert_no_russell(expected.provider_symbol, "instrument roster")
        actual_by_id[instrument_id] = row
    if set(actual_by_id) != set(expected_by_id):
        raise ValidationError("Stage 10 instrument roster is not deduplicated exactly")
    roster_material = [
        expected_by_id[instrument_id].membership_mapping()
        for instrument_id in sorted(expected_by_id)
    ]
    return actual_by_id, _sha256_json(roster_material)


def _quality_expectations_for_universe(
    universe: _ExpectedUniverse,
) -> tuple[tuple[str, str, str, Mapping[str, object]], ...]:
    evidence_rule = (
        "response_digest_and_scope"
        if universe.capture_id is not None
        else "reviewed_scope_manifest_binding"
    )
    return (
        (
            stable_id(
                "stage10_universe_quality",
                STAGE10_EVIDENCE_DATASET_ID,
                universe.semantic_identity,
            ),
            STAGE10_EVIDENCE_DATASET_ID,
            evidence_rule,
            {"rows": len(universe.members), "complete": True},
        ),
        (
            stable_id(
                "stage10_universe_quality",
                STAGE10_INSTRUMENTS_DATASET_ID,
                universe.semantic_identity,
            ),
            STAGE10_INSTRUMENTS_DATASET_ID,
            "stable_provider_identity",
            {"members": len(universe.members)},
        ),
        (
            stable_id(
                "stage10_universe_quality",
                STAGE10_UNIVERSES_DATASET_ID,
                universe.semantic_identity,
            ),
            STAGE10_UNIVERSES_DATASET_ID,
            "complete_current_membership",
            {
                "universe_id": universe.universe_id,
                "members": len(universe.members),
                "complete": True,
            },
        ),
    )


def _verify_control_lineage(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    dataset_id: str,
    semantic_identity: str,
    command: str,
    request_scope: Mapping[str, object],
    output_dataset_ids: tuple[str, ...],
    artifact_id: str,
    content_sha256: str,
    byte_count: int,
    source_reference: str,
    captured_at: str,
    normalization_version: str,
    snapshot_id: str,
    row_count: int,
    quality_expectations: tuple[tuple[str, str, str, Mapping[str, object]], ...],
) -> None:
    run = connection.execute(
        """
        SELECT dataset_id, semantic_identity, command, scope_json, status,
               started_at, completed_at, fetched_count, artifact_id, snapshot_id,
               warnings_json
        FROM ingestion_runs WHERE run_id=?
        """,
        (run_id,),
    ).fetchone()
    if run is None:
        raise ValidationError("Stage 10 canonical run lineage is missing")
    if (
        run["dataset_id"] != dataset_id
        or run["semantic_identity"] != semantic_identity
        or run["command"] != command
        or run["status"] != "succeeded"
        or run["artifact_id"] != artifact_id
        or run["snapshot_id"] != snapshot_id
        or run["fetched_count"] != row_count
        or run["warnings_json"] != "[]"
        or _datetime_text(run["started_at"], "run started_at") != captured_at
        or _datetime_text(run["completed_at"], "run completed_at") != captured_at
    ):
        raise ValidationError("Stage 10 canonical run lineage is invalid")
    _assert_json_column(run["scope_json"], request_scope, "run scope")

    outputs = tuple(
        row["dataset_id"]
        for row in connection.execute(
            "SELECT dataset_id FROM ingestion_run_outputs WHERE run_id=? ORDER BY dataset_id",
            (run_id,),
        )
    )
    if outputs != tuple(sorted(output_dataset_ids)):
        raise ValidationError("Stage 10 canonical run outputs are invalid")

    artifact = connection.execute(
        """
        SELECT run_id, dataset_id, content_sha256, media_type, byte_count,
               source_reference, request_scope_json, captured_at,
               captured_precision, normalization_version
        FROM ingestion_artifacts WHERE artifact_id=?
        """,
        (artifact_id,),
    ).fetchone()
    if artifact is None or (
        artifact["run_id"] != run_id
        or artifact["dataset_id"] != STAGE10_EVIDENCE_DATASET_ID
        or artifact["content_sha256"] != content_sha256
        or artifact["media_type"] != "application/json"
        or artifact["byte_count"] != byte_count
        or artifact["source_reference"] != source_reference
        or artifact["captured_at"] != captured_at
        or artifact["captured_precision"] != "datetime"
        or artifact["normalization_version"] != normalization_version
    ):
        raise ValidationError("Stage 10 immutable artifact lineage is invalid")
    _assert_json_column(artifact["request_scope_json"], request_scope, "artifact scope")

    snapshot = connection.execute(
        """
        SELECT run_id, dataset_id, semantic_identity, scope_json, completeness,
               row_count, captured_at, captured_precision, validation_state,
               warnings_json
        FROM ingestion_snapshots WHERE snapshot_id=?
        """,
        (snapshot_id,),
    ).fetchone()
    if snapshot is None or (
        snapshot["run_id"] != run_id
        or snapshot["dataset_id"] != dataset_id
        or snapshot["semantic_identity"] != semantic_identity
        or snapshot["completeness"] != "complete"
        or snapshot["row_count"] != row_count
        or snapshot["captured_at"] != captured_at
        or snapshot["captured_precision"] != "datetime"
        or snapshot["validation_state"] != "validated"
        or snapshot["warnings_json"] != "[]"
    ):
        raise ValidationError("Stage 10 immutable snapshot lineage is invalid")
    _assert_json_column(snapshot["scope_json"], request_scope, "snapshot scope")
    snapshot_artifacts = tuple(
        connection.execute(
            """
            SELECT artifact_id, artifact_ordinal
            FROM ingestion_snapshot_artifacts
            WHERE snapshot_id=?
            ORDER BY artifact_ordinal
            """,
            (snapshot_id,),
        )
    )
    if tuple((row["artifact_id"], row["artifact_ordinal"]) for row in snapshot_artifacts) != (
        (artifact_id, 1),
    ):
        raise ValidationError("Stage 10 snapshot artifact lineage is invalid")

    quality_rows = tuple(
        connection.execute(
            """
            SELECT quality_result_id, dataset_id, rule_id, rule_version, severity,
                   outcome, subject_kind, subject_id, artifact_id, snapshot_id,
                   observed_json, created_at
            FROM data_quality_results
            WHERE run_id=?
            ORDER BY quality_result_id
            """,
            (run_id,),
        )
    )
    expected_quality = {
        quality_id: (dataset, rule, dict(observed))
        for quality_id, dataset, rule, observed in quality_expectations
    }
    if len(quality_rows) != len(expected_quality):
        raise ValidationError("Stage 10 quality evidence is incomplete or unexpected")
    for row in quality_rows:
        expected = expected_quality.get(row["quality_result_id"])
        if expected is None:
            raise ValidationError("Stage 10 quality evidence is unexpected")
        dataset, rule, observed = expected
        expected_artifact_id = (
            artifact_id if dataset == STAGE10_EVIDENCE_DATASET_ID else None
        )
        if (
            row["dataset_id"] != dataset
            or row["rule_id"] != rule
            or row["rule_version"] != "1.0.0"
            or row["severity"] != "critical"
            or row["outcome"] != "passed"
            or row["subject_kind"] != "snapshot"
            or row["subject_id"] != snapshot_id
            or row["artifact_id"] != expected_artifact_id
            or row["snapshot_id"] != snapshot_id
            or row["observed_json"] != dumps_strict(observed)
            or _datetime_text(row["created_at"], "quality created_at") != captured_at
        ):
            raise ValidationError("Stage 10 quality evidence is invalid")


def _verify_universes(
    connection: sqlite3.Connection,
    *,
    scope: Stage10MarketScope,
    expected: tuple[_ExpectedUniverse, ...],
    source_rows: Mapping[str, sqlite3.Row],
    instrument_rows: Mapping[str, sqlite3.Row],
) -> tuple[dict[str, object], ...]:
    scope_rows = tuple(
        connection.execute(
            """
            SELECT scope_snapshot_id, scope_manifest_sha256, target_profile_id,
                   provider, captured_at, captured_precision, run_id
            FROM stage10_scope_snapshots
            ORDER BY scope_snapshot_id
            """
        )
    )
    expected_scope_snapshot_id = stable_id(
        "stage10_scope_snapshot", scope.manifest_sha256
    )
    if len(scope_rows) != 1:
        raise ValidationError("Stage 10 requires exactly one current scope snapshot")
    scope_row = scope_rows[0]
    if (
        scope_row["scope_snapshot_id"] != expected_scope_snapshot_id
        or scope_row["scope_manifest_sha256"] != scope.manifest_sha256
        or scope_row["target_profile_id"] != scope.target_profile_id
        or scope_row["provider"] != "fmp"
        or scope_row["captured_precision"] != "datetime"
    ):
        raise ValidationError("Stage 10 scope snapshot is invalid")
    scope_captured_at = _datetime_text(scope_row["captured_at"], "scope captured_at")

    universe_rows = tuple(
        connection.execute(
            """
            SELECT universe_id, universe_kind, publisher, source_authority,
                   created_run_id
            FROM stage10_universes
            ORDER BY universe_id
            """
        )
    )
    if len(universe_rows) != len(expected):
        raise ValidationError("Stage 10 universe declarations are incomplete or unexpected")
    actual_universes = {row["universe_id"]: row for row in universe_rows}
    if set(actual_universes) != set(_UNIVERSE_ORDER):
        raise ValidationError("Stage 10 universe declarations include an unexpected universe")

    snapshot_rows = tuple(
        connection.execute(
            """
            SELECT universe_snapshot_id, universe_id, scope_snapshot_id, capture_id,
                   as_of_date, captured_at, captured_precision, completeness,
                   membership_relation, member_count, membership_sha256, run_id
            FROM stage10_universe_snapshots
            ORDER BY universe_id, universe_snapshot_id
            """
        )
    )
    if len(snapshot_rows) != len(expected):
        raise ValidationError("Stage 10 has a historical or incomplete membership snapshot")
    snapshots = {row["universe_id"]: row for row in snapshot_rows}
    if len(snapshots) != len(expected) or set(snapshots) != set(_UNIVERSE_ORDER):
        raise ValidationError("Stage 10 membership snapshots are not exactly current")

    expected_runs = {item.run_id for item in expected}
    snapshot_capture_times: dict[str, str] = {}
    output: list[dict[str, object]] = []
    for universe in expected:
        declaration = actual_universes[universe.universe_id]
        if (
            declaration["universe_kind"] != universe.universe_kind
            or declaration["publisher"] != universe.publisher
            or declaration["source_authority"] != universe.source_authority
            or declaration["created_run_id"] != universe.run_id
        ):
            raise ValidationError("Stage 10 universe declaration differs from scope")
        snapshot = snapshots[universe.universe_id]
        captured_at = _datetime_text(snapshot["captured_at"], "universe snapshot captured_at")
        if (
            snapshot["universe_snapshot_id"] != universe.snapshot_id
            or snapshot["scope_snapshot_id"] != expected_scope_snapshot_id
            or snapshot["capture_id"] != universe.capture_id
            or snapshot["as_of_date"] != captured_at[:10]
            or snapshot["captured_precision"] != "datetime"
            or snapshot["completeness"] != "complete"
            or snapshot["membership_relation"] != universe.membership_relation
            or snapshot["member_count"]
            != (
                len(universe.members)
                if universe.universe_id in _SOURCE_UNIVERSE_IDS
                else _WATCHLIST_COUNTS[universe.universe_id]
            )
            or snapshot["membership_sha256"] != universe.membership_sha256
            or snapshot["run_id"] != universe.run_id
        ):
            raise ValidationError("Stage 10 current membership snapshot is invalid")
        if universe.capture_id is not None:
            source_row = source_rows[universe.universe_id]
            if source_row["captured_at"] != captured_at:
                raise ValidationError("Stage 10 current membership capture is time-inconsistent")
        members = tuple(
            connection.execute(
                """
                SELECT source_row, instrument_id
                FROM stage10_universe_snapshot_members
                WHERE universe_snapshot_id=?
                ORDER BY source_row
                """,
                (universe.snapshot_id,),
            )
        )
        expected_members = tuple(
            (index, member.instrument_id)
            for index, member in enumerate(universe.members, start=1)
        )
        if tuple((row["source_row"], row["instrument_id"]) for row in members) != expected_members:
            raise ValidationError("Stage 10 membership rows do not match the raw or reviewed scope")
        snapshot_capture_times[universe.run_id] = captured_at
        source_reference = (
            "config/stage10_market_scope.json"
            if universe.endpoint_path is None
            else "fmp" + universe.endpoint_path
        )
        _verify_control_lineage(
            connection,
            run_id=universe.run_id,
            dataset_id=STAGE10_UNIVERSES_DATASET_ID,
            semantic_identity=universe.semantic_identity,
            command=STAGE10_UNIVERSE_COLLECTOR_ID,
            request_scope=universe.request_scope,
            output_dataset_ids=_UNIVERSE_OUTPUT_DATASETS,
            artifact_id=universe.artifact_id,
            content_sha256=universe.raw_bytes_sha256,
            byte_count=len(universe.raw_bytes),
            source_reference=source_reference,
            captured_at=captured_at,
            normalization_version=STAGE10_UNIVERSE_NORMALIZATION_VERSION,
            snapshot_id=universe.snapshot_id,
            row_count=len(universe.members),
            quality_expectations=_quality_expectations_for_universe(universe),
        )
        output.append(
            {
                "universe_id": universe.universe_id,
                "member_count": len(universe.members),
                "membership_sha256": universe.membership_sha256,
                "capture_count": 1 if universe.capture_id is not None else 0,
                "raw_response_sha256": universe.raw_bytes_sha256,
                "semantic_identity": universe.semantic_identity,
                "snapshot_id": universe.snapshot_id,
            }
        )

    if (
        scope_row["run_id"] not in expected_runs
        or scope_captured_at != snapshot_capture_times.get(scope_row["run_id"])
    ):
        raise ValidationError("Stage 10 scope snapshot is not bound to a current universe run")
    for instrument_id, row in instrument_rows.items():
        if (
            row["run_id"] not in expected_runs
            or row["captured_at"] != snapshot_capture_times.get(row["run_id"])
        ):
            raise ValidationError("Stage 10 instrument is not bound to a universe snapshot")
        if not isinstance(instrument_id, str):
            raise ValidationError("Stage 10 instrument identity is invalid")
    return tuple(output)



def _verify_price_capture(
    row: sqlite3.Row,
    *,
    instrument: _ExpectedInstrument,
    scope: Stage10MarketScope,
) -> _PriceCapture:
    request_scope = _expected_price_scope(instrument, scope)
    response_bytes = _strict_row_bytes(row["response_bytes"], "price capture")
    response_sha256 = _require_digest(row["response_sha256"], "price response_sha256")
    if _sha256_bytes(response_bytes) != response_sha256:
        raise ValidationError("Stage 10 price raw response digest does not match")
    try:
        capture = parse_fmp_stage10_price_response(
            symbol=instrument.provider_symbol,
            body=response_bytes,
        )
    except (ResourceLimitError, ValidationError):
        raise ValidationError("Stage 10 price raw response cannot be reparsed") from None
    if (
        row["dataset_id"] != STAGE10_EVIDENCE_DATASET_ID
        or row["provider"] != "fmp"
        or row["instrument_id"] != instrument.instrument_id
        or row["provider_symbol"] != instrument.provider_symbol
        or row["endpoint_path"] != FMP_STAGE10_PRICE_PATH
        or row["scope_manifest_sha256"] != scope.manifest_sha256
        or row["http_status"] != 200
        or row["content_type"] != "application/json"
        or row["completeness"] != "complete"
        or row["normalization_version"] != STAGE10_PRICE_NORMALIZATION_VERSION
        or row["response_sha256"] != capture.raw_bytes_sha256
        or row["earliest_trade_date"] != capture.earliest_date
        or row["latest_trade_date"] != capture.latest_date
        or row["row_count"] != len(capture.rows)
    ):
        raise ValidationError("Stage 10 price-capture metadata is invalid")
    _assert_json_column(row["request_scope_json"], request_scope, "price request scope")
    request_scope_sha256 = _scope_digest(request_scope)
    if row["request_scope_sha256"] != request_scope_sha256:
        raise ValidationError("Stage 10 price request-scope digest is invalid")
    captured_at = _datetime_text(row["captured_at"], "price captured_at")
    if (
        row["captured_precision"] != "datetime"
        or not isinstance(row["capture_id"], str)
        or not isinstance(row["artifact_id"], str)
        or not isinstance(row["snapshot_id"], str)
        or not isinstance(row["run_id"], str)
    ):
        raise ValidationError("Stage 10 price-capture lineage metadata is invalid")
    semantic_identity = _sha256_json(
        {
            "collector_id": STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
            "request_scope": request_scope,
            "normalized_complete_history_sha256": capture.semantic_sha256,
        }
    )
    run_id = stable_id(
        "stage10_daily_price_run", STAGE10_DAILY_PRICES_DATASET_ID, semantic_identity
    )
    artifact_id = stable_id(
        "stage10_daily_price_artifact",
        STAGE10_EVIDENCE_DATASET_ID,
        instrument.provider_symbol,
        capture.raw_bytes_sha256,
        request_scope_sha256,
    )
    snapshot_id = stable_id(
        "stage10_daily_price_snapshot",
        STAGE10_DAILY_PRICES_DATASET_ID,
        semantic_identity,
    )
    capture_id = stable_id(
        "stage10_daily_price_capture",
        STAGE10_EVIDENCE_DATASET_ID,
        semantic_identity,
    )
    if (
        row["semantic_identity"] != semantic_identity
        or row["run_id"] != run_id
        or row["artifact_id"] != artifact_id
        or row["snapshot_id"] != snapshot_id
        or row["capture_id"] != capture_id
    ):
        raise ValidationError("Stage 10 price-capture stable identities are invalid")
    return _PriceCapture(
        capture_id=capture_id,
        instrument=instrument,
        raw=capture,
        request_scope=MappingProxyType(request_scope),
        request_scope_sha256=request_scope_sha256,
        semantic_identity=semantic_identity,
        artifact_id=artifact_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
        captured_at=captured_at,
    )


def _price_quality_expectations(
    capture: _PriceCapture,
) -> tuple[tuple[str, str, str, Mapping[str, object]], ...]:
    return (
        (
            stable_id(
                "stage10_price_quality",
                STAGE10_EVIDENCE_DATASET_ID,
                capture.semantic_identity,
            ),
            STAGE10_EVIDENCE_DATASET_ID,
            "response_digest_and_scope",
            {"rows": len(capture.raw.rows), "complete": True},
        ),
        (
            stable_id(
                "stage10_price_quality",
                STAGE10_DAILY_PRICES_DATASET_ID,
                capture.semantic_identity,
            ),
            STAGE10_DAILY_PRICES_DATASET_ID,
            "full_history_ohlcv_and_corrections",
            {
                "rows": len(capture.raw.rows),
                "earliest_trade_date": capture.raw.earliest_date,
                "latest_trade_date": capture.raw.latest_date,
            },
        ),
    )


def _version_from_row(
    row: sqlite3.Row,
    *,
    captures: Mapping[str, _PriceCapture],
) -> _Version:
    capture_id = row["capture_id"]
    if not isinstance(capture_id, str):
        raise ValidationError("Stage 10 price version has no capture")
    capture = captures.get(capture_id)
    if capture is None:
        raise ValidationError("Stage 10 price version references an unexpected capture")
    if (
        row["instrument_id"] != capture.instrument.instrument_id
        or row["provider"] != "fmp"
        or row["price_variant"] != STAGE10_PRICE_VARIANT
        or row["currency_segment"] != STAGE10_CURRENCY_SEGMENT
        or row["artifact_id"] != capture.artifact_id
        or row["snapshot_id"] != capture.snapshot_id
        or row["run_id"] != capture.run_id
        or row["available_precision"] != "datetime"
        or row["captured_precision"] != "datetime"
        or _datetime_text(row["available_at"], "price available_at") != capture.captured_at
        or _datetime_text(row["captured_at"], "price version captured_at") != capture.captured_at
    ):
        raise ValidationError("Stage 10 price-version lineage is invalid")
    version_id = row["version_id"]
    trade_date = row["trade_date"]
    correction_sequence = _require_int(
        row["correction_sequence"], "price correction sequence", minimum=1
    )
    source_row = _require_int(row["source_row"], "price source row", minimum=1)
    if not isinstance(version_id, str) or not isinstance(trade_date, str):
        raise ValidationError("Stage 10 price-version identity is invalid")
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError("Stage 10 price-version trade date is invalid") from exc
    supersedes = row["supersedes_version_id"]
    if supersedes is not None and not isinstance(supersedes, str):
        raise ValidationError("Stage 10 price-version supersession is invalid")
    values = (
        _decimal_text(row["open_value"], "open value"),
        _decimal_text(row["high_value"], "high value"),
        _decimal_text(row["low_value"], "low value"),
        _decimal_text(row["close_value"], "close value"),
        _require_int(row["volume"], "volume", minimum=0),
    )
    open_value, high_value, low_value, close_value = (
        Decimal(values[0]),
        Decimal(values[1]),
        Decimal(values[2]),
        Decimal(values[3]),
    )
    if (
        min(open_value, high_value, low_value, close_value) <= 0
        or low_value > min(open_value, close_value)
        or high_value < max(open_value, close_value)
        or low_value > high_value
    ):
        raise ValidationError("Stage 10 canonical OHLC values are inconsistent")
    return _Version(
        version_id=version_id,
        instrument_id=capture.instrument.instrument_id,
        trade_date=trade_date,
        correction_sequence=correction_sequence,
        supersedes_version_id=supersedes,
        capture_id=capture_id,
        source_row=source_row,
        values=values,
    )


def _assert_failed_tickers_have_no_price_facts(
    connection: sqlite3.Connection,
    *,
    planned: Mapping[str, _ExpectedInstrument],
    failed_tickers: frozenset[str],
) -> None:
    """A declared failure authorizes omission, never partial price facts."""

    for symbol in sorted(failed_tickers):
        instrument_id = planned[symbol].instrument_id
        capture_count = connection.execute(
            "SELECT count(*) FROM stage10_daily_price_captures WHERE instrument_id=?",
            (instrument_id,),
        ).fetchone()[0]
        version_count = connection.execute(
            "SELECT count(*) FROM stage10_daily_price_versions WHERE instrument_id=?",
            (instrument_id,),
        ).fetchone()[0]
        current_count = connection.execute(
            "SELECT count(*) FROM stage10_daily_prices WHERE instrument_id=?",
            (instrument_id,),
        ).fetchone()[0]
        if capture_count or version_count or current_count:
            raise ValidationError("Stage 10 failed symbol has retained price facts")


def _verify_prices(
    connection: sqlite3.Connection,
    *,
    scope: Stage10MarketScope,
    planned: Mapping[str, _ExpectedInstrument],
    failed_records: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object], ...]:
    failed_ticker_set = _failed_tickers_for_roster(failed_records, planned)
    expected_by_id = {item.instrument_id: item for item in planned.values()}
    successful_by_id = {
        instrument_id: instrument
        for instrument_id, instrument in expected_by_id.items()
        if instrument.provider_symbol not in failed_ticker_set
    }
    _assert_failed_tickers_have_no_price_facts(
        connection,
        planned=planned,
        failed_tickers=failed_ticker_set,
    )
    capture_rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, instrument_id, provider_symbol,
                   endpoint_path, scope_manifest_sha256, request_scope_json,
                   request_scope_sha256, response_sha256, response_bytes, http_status,
                   content_type, semantic_identity, completeness, artifact_id,
                   snapshot_id, captured_at, captured_precision, earliest_trade_date,
                   latest_trade_date, row_count, normalization_version, run_id
            FROM stage10_daily_price_captures
            ORDER BY provider_symbol, capture_id
            """
        )
    )
    if not capture_rows and successful_by_id:
        raise ValidationError("Stage 10 has no full-history price captures")
    captures: dict[str, _PriceCapture] = {}
    captures_by_instrument: dict[str, list[_PriceCapture]] = {}
    for row in capture_rows:
        instrument = expected_by_id.get(row["instrument_id"])
        if instrument is None:
            raise ValidationError("Stage 10 price capture references an unscoped instrument")
        if instrument.provider_symbol in failed_ticker_set:
            raise ValidationError("Stage 10 failed symbol has a price capture")
        capture = _verify_price_capture(row, instrument=instrument, scope=scope)
        if capture.capture_id in captures:
            raise ValidationError("Stage 10 price capture identity is duplicated")
        captures[capture.capture_id] = capture
        captures_by_instrument.setdefault(instrument.instrument_id, []).append(capture)
        _verify_control_lineage(
            connection,
            run_id=capture.run_id,
            dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
            semantic_identity=capture.semantic_identity,
            command=STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            request_scope=capture.request_scope,
            output_dataset_ids=_PRICE_OUTPUT_DATASETS,
            artifact_id=capture.artifact_id,
            content_sha256=capture.raw.raw_bytes_sha256,
            byte_count=len(capture.raw.raw_bytes),
            source_reference="fmp/stable/historical-price-eod/full",
            captured_at=capture.captured_at,
            normalization_version=STAGE10_PRICE_NORMALIZATION_VERSION,
            snapshot_id=capture.snapshot_id,
            row_count=len(capture.raw.rows),
            quality_expectations=_price_quality_expectations(capture),
        )

    if set(captures_by_instrument) != set(successful_by_id):
        raise ValidationError("Every nonfailed Stage 10 scoped symbol must have a complete history")
    for captures_for_instrument in captures_by_instrument.values():
        if not captures_for_instrument:
            raise ValidationError("Stage 10 scoped instrument lacks complete history")

    version_rows = tuple(
        connection.execute(
            """
            SELECT version_id, instrument_id, trade_date, provider, price_variant,
                   currency_segment, open_value, high_value, low_value, close_value,
                   volume, available_at, available_precision, captured_at,
                   captured_precision, correction_sequence, supersedes_version_id,
                   capture_id, artifact_id, snapshot_id, run_id, source_row
            FROM stage10_daily_price_versions
            ORDER BY instrument_id, trade_date, correction_sequence, version_id
            """
        )
    )
    if not version_rows and successful_by_id:
        raise ValidationError("Stage 10 has no immutable price versions")
    versions = tuple(_version_from_row(row, captures=captures) for row in version_rows)
    histories: dict[tuple[str, str], list[_Version]] = {}
    for version in versions:
        histories.setdefault((version.instrument_id, version.trade_date), []).append(version)
    for history in histories.values():
        history.sort(key=lambda item: (item.correction_sequence, item.version_id))
        for index, version in enumerate(history, start=1):
            if version.correction_sequence != index:
                raise ValidationError("Stage 10 correction sequence is not contiguous")
            predecessor = None if index == 1 else history[index - 2].version_id
            if version.supersedes_version_id != predecessor:
                raise ValidationError("Stage 10 correction supersession lineage is invalid")

    current_rows = tuple(
        connection.execute(
            """
            SELECT instrument_id, trade_date, provider, price_variant,
                   currency_segment, current_version_id
            FROM stage10_daily_prices
            ORDER BY instrument_id, trade_date
            """
        )
    )
    if not current_rows and successful_by_id:
        raise ValidationError("Stage 10 has no current price rows")
    current_keys: set[tuple[str, str]] = set()
    current_ids: set[str] = set()
    observed_current: dict[tuple[str, str], _Version] = {}
    for row in current_rows:
        instrument_id = row["instrument_id"]
        trade_date = row["trade_date"]
        if (
            not isinstance(instrument_id, str)
            or not isinstance(trade_date, str)
            or instrument_id not in successful_by_id
            or row["provider"] != "fmp"
            or row["price_variant"] != STAGE10_PRICE_VARIANT
            or row["currency_segment"] != STAGE10_CURRENCY_SEGMENT
            or not isinstance(row["current_version_id"], str)
        ):
            raise ValidationError("Stage 10 current-price identity is invalid")
        key = (instrument_id, trade_date)
        history = histories.get(key)
        if history is None:
            raise ValidationError("Stage 10 current price lacks immutable history")
        latest = history[-1]
        if row["current_version_id"] != latest.version_id:
            raise ValidationError("Stage 10 current price does not select latest correction")
        if key in current_keys or latest.version_id in current_ids:
            raise ValidationError("Stage 10 current price is duplicated")
        current_keys.add(key)
        current_ids.add(latest.version_id)
        observed_current[key] = latest

    coverage: list[dict[str, object]] = []
    for instrument_id, instrument in sorted(successful_by_id.items()):
        raw_rows_by_capture: dict[str, dict[str, FmpStage10PriceRow]] = {}
        for capture in captures_by_instrument[instrument_id]:
            raw_rows_by_capture[capture.capture_id] = {
                row.trade_date: row for row in capture.raw.rows
            }
        for capture in captures_by_instrument[instrument_id]:
            raw_rows = raw_rows_by_capture[capture.capture_id]
            expected_source_rows = {row.source_row for row in capture.raw.rows}
            bound_versions = [
                version
                for history_key, history in histories.items()
                if history_key[0] == instrument_id
                for version in history
                if version.capture_id == capture.capture_id
            ]
            bound_source_rows = {version.source_row for version in bound_versions}
            if not bound_source_rows.issubset(expected_source_rows):
                raise ValidationError("Stage 10 canonical version source row is outside raw history")
            for version in bound_versions:
                raw_row = raw_rows.get(version.trade_date)
                if raw_row is None or version.source_row != raw_row.source_row:
                    raise ValidationError("Stage 10 canonical version is not raw-row bound")
                expected_values = (
                    _decimal_text(raw_row.open_value, "raw open"),
                    _decimal_text(raw_row.high_value, "raw high"),
                    _decimal_text(raw_row.low_value, "raw low"),
                    _decimal_text(raw_row.close_value, "raw close"),
                    raw_row.volume,
                )
                if version.values != expected_values:
                    raise ValidationError("Stage 10 canonical OHLCV differs from raw response")

        current_dates = {
            trade_date
            for candidate_instrument, trade_date in current_keys
            if candidate_instrument == instrument_id
        }
        all_dates = {
            trade_date
            for candidate_instrument, trade_date in histories
            if candidate_instrument == instrument_id
        }
        if current_dates != all_dates:
            raise ValidationError("Stage 10 current price coverage is incomplete")
        if not current_dates:
            raise ValidationError("Stage 10 scoped symbol has no current price rows")
        if not current_dates.issubset(
            {
                trade_date
                for capture in captures_by_instrument[instrument_id]
                for trade_date in raw_rows_by_capture[capture.capture_id]
            }
        ):
            raise ValidationError("Stage 10 current date is not present in a raw response")
        latest_capture = max(
            captures_by_instrument[instrument_id],
            key=lambda item: (item.captured_at, item.capture_id),
        )
        expected_latest_dates = tuple(row.trade_date for row in latest_capture.raw.rows)
        if tuple(sorted(current_dates)) != expected_latest_dates:
            raise ValidationError("Stage 10 current rows do not match the latest complete provider history")
        for raw_row in latest_capture.raw.rows:
            selected = observed_current.get((instrument_id, raw_row.trade_date))
            if selected is None:
                raise ValidationError("Stage 10 current raw history row is missing")
            expected_values = (
                _decimal_text(raw_row.open_value, "latest raw open"),
                _decimal_text(raw_row.high_value, "latest raw high"),
                _decimal_text(raw_row.low_value, "latest raw low"),
                _decimal_text(raw_row.close_value, "latest raw close"),
                raw_row.volume,
            )
            if selected.values != expected_values:
                raise ValidationError("Stage 10 current OHLCV does not match latest raw history")
        coverage.append(
            {
                "provider_symbol": instrument.provider_symbol,
                "instrument_id": instrument_id,
                "capture_count": len(captures_by_instrument[instrument_id]),
                "current_row_count": len(current_dates),
                "version_count": sum(
                    len(history)
                    for (candidate_instrument, _), history in histories.items()
                    if candidate_instrument == instrument_id
                ),
                "earliest_trade_date": latest_capture.raw.earliest_date,
                "latest_trade_date": latest_capture.raw.latest_date,
                "latest_response_sha256": latest_capture.raw.raw_bytes_sha256,
                "latest_semantic_identity": latest_capture.semantic_identity,
            }
        )
    if len(coverage) != len(successful_by_id):
        raise ValidationError("Stage 10 full-history coverage is incomplete")
    return tuple(coverage)



@dataclass(frozen=True, slots=True)
class Stage10MarketReconciliation:
    """Path-free, read-only reconciliation of a complete Stage 10 market cohort."""

    scope_manifest_sha256: str
    target_profile_id: str
    registry_schema_version: str
    registry_version: str
    registry_source_sha256: str
    migration_id: str
    migration_sha256: str
    universe_summaries: tuple[Mapping[str, object], ...]
    roster_count: int
    roster_sha256: str
    failed_records: tuple[Mapping[str, object], ...]
    failed_tickers: tuple[str, ...]
    failed_symbol_count: int
    successful_symbol_count: int
    failure_manifest_sha256: str
    price_coverage: tuple[Mapping[str, object], ...]
    universe_capture_count: int
    price_capture_count: int
    current_price_count: int
    immutable_version_count: int
    historical_membership_inferred: bool
    russell_excluded: bool
    raw_captures_reparsed: bool
    canonical_rows_match_raw: bool
    correction_lineage_verified: bool
    stores_unchanged: bool
    sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "scope_manifest_sha256",
            "registry_source_sha256",
            "migration_sha256",
            "roster_sha256",
            "failure_manifest_sha256",
            "sha256",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "target_profile_id",
            "registry_schema_version",
            "registry_version",
            "migration_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        if (
            self.scope_manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
            or self.target_profile_id != STAGE10_TARGET_PROFILE_ID
            or self.registry_schema_version != "1.6.0"
            or self.registry_version != "2.8.0"
            or self.migration_id != STAGE10_MIGRATION_ID
            or self.migration_sha256 != STAGE10_MIGRATION_SHA256
        ):
            raise ValidationError("Stage 10 reconciliation contract binding is invalid")
        if (
            not isinstance(self.universe_summaries, tuple)
            or len(self.universe_summaries) != len(_UNIVERSE_ORDER)
            or tuple(
                item.get("universe_id") if isinstance(item, Mapping) else None
                for item in self.universe_summaries
            )
            != _UNIVERSE_ORDER
        ):
            raise ValidationError("Stage 10 reconciliation requires five ordered universes")
        frozen_universes = tuple(
            _freeze_json(item, "universe_summaries") for item in self.universe_summaries
        )
        normalized_failed_records = normalize_stage10_failed_records(self.failed_records)
        frozen_coverage = tuple(
            _freeze_json(item, "price_coverage") for item in self.price_coverage
        )
        if any(not isinstance(item, Mapping) for item in frozen_universes) or any(
            not isinstance(item, Mapping) for item in frozen_coverage
        ):
            raise ValidationError("Stage 10 reconciliation material is invalid")
        object.__setattr__(self, "universe_summaries", frozen_universes)
        object.__setattr__(self, "failed_records", normalized_failed_records)
        object.__setattr__(self, "price_coverage", frozen_coverage)
        _require_int(self.roster_count, "roster_count", minimum=1)
        _require_int(self.failed_symbol_count, "failed_symbol_count", minimum=0)
        _require_int(self.successful_symbol_count, "successful_symbol_count", minimum=0)
        _require_int(self.universe_capture_count, "universe_capture_count", minimum=1)
        _require_int(self.price_capture_count, "price_capture_count", minimum=0)
        _require_int(self.current_price_count, "current_price_count", minimum=0)
        _require_int(self.immutable_version_count, "immutable_version_count", minimum=0)
        if (
            self.universe_capture_count != 3
            or self.failed_symbol_count != len(self.failed_records)
            or self.successful_symbol_count != len(self.price_coverage)
            or self.roster_count
            != self.successful_symbol_count + self.failed_symbol_count
            or self.failure_manifest_sha256
            != stage10_failure_manifest_sha256(self.failed_records)
            or self.price_capture_count < self.successful_symbol_count
            or self.current_price_count < self.successful_symbol_count
            or self.immutable_version_count < self.current_price_count
        ):
            raise ValidationError("Stage 10 reconciliation cardinality is invalid")
        failure_tickers = tuple(str(item["symbol"]) for item in self.failed_records)
        if (
            not isinstance(self.failed_tickers, tuple)
            or any(not isinstance(symbol, str) for symbol in self.failed_tickers)
            or self.failed_tickers != failure_tickers
        ):
            raise ValidationError("Stage 10 failed-ticker projection is invalid")
        coverage_symbols: list[str] = []
        for item in self.price_coverage:
            if not isinstance(item.get("provider_symbol"), str):
                raise ValidationError("Stage 10 price coverage symbol is invalid")
            coverage_symbols.append(item["provider_symbol"])
        if (
            len(set(coverage_symbols)) != len(coverage_symbols)
            or set(failure_tickers).intersection(coverage_symbols)
        ):
            raise ValidationError("Stage 10 roster partition is invalid")
        for field_name in (
            "historical_membership_inferred",
            "russell_excluded",
            "raw_captures_reparsed",
            "canonical_rows_match_raw",
            "correction_lineage_verified",
            "stores_unchanged",
        ):
            _require_bool(getattr(self, field_name), field_name)
        if self.historical_membership_inferred:
            raise ValidationError("Stage 10 cannot claim historical membership")
        if not all(
            (
                self.russell_excluded,
                self.raw_captures_reparsed,
                self.canonical_rows_match_raw,
                self.correction_lineage_verified,
                self.stores_unchanged,
            )
        ):
            raise ValidationError("Stage 10 reconciliation safety proof is incomplete")
        if self.sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 10 reconciliation digest is inconsistent")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_market_reconciliation",
            "contract_version": "1.1.0",
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "target_profile_id": self.target_profile_id,
            "registry": {
                "schema_version": self.registry_schema_version,
                "registry_version": self.registry_version,
                "source_sha256": self.registry_source_sha256,
            },
            "migration": {
                "id": self.migration_id,
                "sha256": self.migration_sha256,
            },
            "universe_summaries": [
                _thaw_json(item) for item in self.universe_summaries
            ],
            "roster_count": self.roster_count,
            "roster_sha256": self.roster_sha256,
            "failed_records": [_thaw_json(item) for item in self.failed_records],
            "failed_tickers": list(self.failed_tickers),
            "failed_symbol_count": self.failed_symbol_count,
            "successful_symbol_count": self.successful_symbol_count,
            "failure_manifest_sha256": self.failure_manifest_sha256,
            "price_coverage": [_thaw_json(item) for item in self.price_coverage],
            "universe_capture_count": self.universe_capture_count,
            "price_capture_count": self.price_capture_count,
            "current_price_count": self.current_price_count,
            "immutable_version_count": self.immutable_version_count,
            "historical_membership_inferred": self.historical_membership_inferred,
            "russell_excluded": self.russell_excluded,
            "raw_captures_reparsed": self.raw_captures_reparsed,
            "canonical_rows_match_raw": self.canonical_rows_match_raw,
            "correction_lineage_verified": self.correction_lineage_verified,
            "stores_unchanged": self.stores_unchanged,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def _reconcile_market_connection(
    connection: sqlite3.Connection,
    registry: Registry,
    scope: Stage10MarketScope,
    *,
    failed_records: tuple[Mapping[str, object], ...],
) -> Stage10MarketReconciliation:
    expected, planned, source_rows = _expected_universes(connection, scope)
    failed_ticker_set = _failed_tickers_for_roster(failed_records, planned)
    instrument_rows, roster_sha256 = _verify_instruments(connection, planned)
    universe_summaries = _verify_universes(
        connection,
        scope=scope,
        expected=expected,
        source_rows=source_rows,
        instrument_rows=instrument_rows,
    )
    price_coverage = _verify_prices(
        connection,
        scope=scope,
        planned=planned,
        failed_records=failed_records,
    )
    price_capture_count = connection.execute(
        "SELECT count(*) FROM stage10_daily_price_captures"
    ).fetchone()[0]
    current_price_count = connection.execute(
        "SELECT count(*) FROM stage10_daily_prices"
    ).fetchone()[0]
    immutable_version_count = connection.execute(
        "SELECT count(*) FROM stage10_daily_price_versions"
    ).fetchone()[0]
    material = {
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "registry_schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "registry_source_sha256": registry.source_sha256,
        "migration_id": STAGE10_MIGRATION_ID,
        "migration_sha256": STAGE10_MIGRATION_SHA256,
        "universe_summaries": universe_summaries,
        "roster_count": len(planned),
        "roster_sha256": roster_sha256,
        "failed_records": tuple(_thaw_json(item) for item in failed_records),
        "failed_tickers": tuple(sorted(failed_ticker_set)),
        "failed_symbol_count": len(failed_records),
        "successful_symbol_count": len(planned) - len(failed_ticker_set),
        "failure_manifest_sha256": stage10_failure_manifest_sha256(failed_records),
        "price_coverage": price_coverage,
        "universe_capture_count": len(source_rows),
        "price_capture_count": price_capture_count,
        "current_price_count": current_price_count,
        "immutable_version_count": immutable_version_count,
        "historical_membership_inferred": False,
        "russell_excluded": True,
        "raw_captures_reparsed": True,
        "canonical_rows_match_raw": True,
        "correction_lineage_verified": True,
        "stores_unchanged": True,
    }
    return Stage10MarketReconciliation(
        **material,
        sha256=_sha256_json(
            {
                "contract": "quant_data.stage10_market_reconciliation",
                "contract_version": "1.1.0",
                "scope_manifest_sha256": material["scope_manifest_sha256"],
                "target_profile_id": material["target_profile_id"],
                "registry": {
                    "schema_version": material["registry_schema_version"],
                    "registry_version": material["registry_version"],
                    "source_sha256": material["registry_source_sha256"],
                },
                "migration": {
                    "id": material["migration_id"],
                    "sha256": material["migration_sha256"],
                },
                **{
                    name: value
                    for name, value in material.items()
                    if name
                    not in {
                        "scope_manifest_sha256",
                        "target_profile_id",
                        "registry_schema_version",
                        "registry_version",
                        "registry_source_sha256",
                        "migration_id",
                        "migration_sha256",
                    }
                },
            }
        ),
    )


def reconcile_stage10_market(
    store_map: StoreMap,
    registry: Registry,
    scope: Stage10MarketScope,
    *,
    failed_records: tuple[Mapping[str, object], ...] = (),
) -> Stage10MarketReconciliation:
    """Reconcile one explicit Stage 10 cohort without mutating stores.

    ``failed_records`` is the closed, canonical omission manifest.  Every
    failed symbol must be in the current reviewed roster and have no price
    capture, immutable version, or current fact; every other roster symbol
    must retain its complete raw-to-canonical history.
    """

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 10 reconciliation requires explicit four-store paths")
    validated_registry = _validate_registry(registry)
    validated_scope = _validate_scope(scope)
    normalized_failed_records = normalize_stage10_failed_records(failed_records)
    before = mutation_fingerprint(store_map)
    with read_connection(
        store_map,
        StoreRole.MARKET,
        expected_anchor=validated_registry.store(StoreRole.MARKET.value).anchor_relation,
    ) as connection:
        result = _reconcile_market_connection(
            connection,
            validated_registry,
            validated_scope,
            failed_records=normalized_failed_records,
        )
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 10 reconciliation mutated a store")
    return result


def _maps_are_disjoint(*store_maps: StoreMap) -> bool:
    """Require every private cohort to own four different physical stores."""

    seen: set[str] = set()
    for store_map in store_maps:
        identities = {canonical_path_uri(path) for _, path in store_map.items()}
        if seen.intersection(identities):
            return False
        seen.update(identities)
    return True


@dataclass(frozen=True, slots=True)
class Stage10RepopulationEvidence:
    """Immutable, path-free proof that a restored Stage 10 cohort is exact.

    The evidence is intentionally only an assertion about a private candidate.
    It neither selects a production destination nor permits a store promotion.
    """

    reconciliation: Stage10MarketReconciliation
    registry_schema_version: str
    registry_version: str
    registry_source_sha256: str
    source_health_sha256: str
    restored_health_sha256: str
    source_logical_manifest_sha256: str
    restored_logical_manifest_sha256: str
    source_mutation_before_sha256: str
    source_mutation_after_sha256: str
    restored_mutation_before_sha256: str
    restored_mutation_after_sha256: str
    source_reconciliation_sha256: str
    restored_reconciliation_sha256: str
    source_failure_manifest_sha256: str
    restored_failure_manifest_sha256: str
    source_restored_equal: bool
    source_reads_unchanged: bool
    restored_reads_unchanged: bool
    raw_reparsed_equal: bool
    complete_symbol_coverage: bool
    historical_membership_inferred: bool
    russell_excluded: bool
    replay_unchanged: bool
    backup_restore_outcome: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.reconciliation, Stage10MarketReconciliation):
            raise ValidationError("Stage 10 evidence requires market reconciliation")
        for field_name in (
            "registry_schema_version",
            "registry_version",
            "backup_restore_outcome",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "registry_source_sha256",
            "source_health_sha256",
            "restored_health_sha256",
            "source_logical_manifest_sha256",
            "restored_logical_manifest_sha256",
            "source_mutation_before_sha256",
            "source_mutation_after_sha256",
            "restored_mutation_before_sha256",
            "restored_mutation_after_sha256",
            "source_reconciliation_sha256",
            "restored_reconciliation_sha256",
            "source_failure_manifest_sha256",
            "restored_failure_manifest_sha256",
            "sha256",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if (
            self.registry_schema_version != "1.6.0"
            or self.registry_version != "2.8.0"
            or self.registry_source_sha256 != self.reconciliation.registry_source_sha256
            or self.source_reconciliation_sha256 != self.reconciliation.sha256
            or self.source_failure_manifest_sha256
            != self.reconciliation.failure_manifest_sha256
            or self.restored_failure_manifest_sha256
            != self.reconciliation.failure_manifest_sha256
            or self.backup_restore_outcome != "validated_restored_cohort_equal"
        ):
            raise ValidationError("Stage 10 evidence contract binding is invalid")
        for field_name in (
            "source_restored_equal",
            "source_reads_unchanged",
            "restored_reads_unchanged",
            "raw_reparsed_equal",
            "complete_symbol_coverage",
            "historical_membership_inferred",
            "russell_excluded",
            "replay_unchanged",
        ):
            _require_bool(getattr(self, field_name), field_name)
        if (
            not self.source_restored_equal
            or not self.source_reads_unchanged
            or not self.restored_reads_unchanged
            or not self.raw_reparsed_equal
            or not self.complete_symbol_coverage
            or self.historical_membership_inferred
            or not self.russell_excluded
        ):
            raise ValidationError("Stage 10 evidence safety proof is incomplete")
        if self.complete_symbol_coverage != (
            self.reconciliation.roster_count
            == self.reconciliation.successful_symbol_count
            + self.reconciliation.failed_symbol_count
            and self.reconciliation.successful_symbol_count
            == len(self.reconciliation.price_coverage)
        ):
            raise ValidationError("Stage 10 evidence symbol partition is invalid")
        if self.sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 10 repopulation evidence digest is inconsistent")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_repopulation_evidence",
            "contract_version": "1.1.0",
            "reconciliation": self.reconciliation.to_primitive(),
            "registry": {
                "schema_version": self.registry_schema_version,
                "registry_version": self.registry_version,
                "source_sha256": self.registry_source_sha256,
            },
            "source_health_sha256": self.source_health_sha256,
            "restored_health_sha256": self.restored_health_sha256,
            "source_logical_manifest_sha256": self.source_logical_manifest_sha256,
            "restored_logical_manifest_sha256": self.restored_logical_manifest_sha256,
            "source_mutation_before_sha256": self.source_mutation_before_sha256,
            "source_mutation_after_sha256": self.source_mutation_after_sha256,
            "restored_mutation_before_sha256": self.restored_mutation_before_sha256,
            "restored_mutation_after_sha256": self.restored_mutation_after_sha256,
            "source_reconciliation_sha256": self.source_reconciliation_sha256,
            "restored_reconciliation_sha256": self.restored_reconciliation_sha256,
            "source_failure_manifest_sha256": self.source_failure_manifest_sha256,
            "restored_failure_manifest_sha256": self.restored_failure_manifest_sha256,
            "source_restored_equal": self.source_restored_equal,
            "source_reads_unchanged": self.source_reads_unchanged,
            "restored_reads_unchanged": self.restored_reads_unchanged,
            "raw_reparsed_equal": self.raw_reparsed_equal,
            "complete_symbol_coverage": self.complete_symbol_coverage,
            "historical_membership_inferred": self.historical_membership_inferred,
            "russell_excluded": self.russell_excluded,
            "replay_unchanged": self.replay_unchanged,
            "backup_restore_outcome": self.backup_restore_outcome,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def build_stage10_repopulation_evidence(
    source_map: StoreMap,
    restored_map: StoreMap,
    registry: Registry,
    scope: Stage10MarketScope,
    *,
    replay_unchanged: bool,
    failed_records: tuple[Mapping[str, object], ...] = (),
) -> Stage10RepopulationEvidence:
    """Bind a source and restored Stage 10 cohort without mutating either.

    This deliberately recomputes every market invariant from retained raw
    captures in *both* cohorts.  The all-store health and logical manifests
    make the backup/restore claim cover the complete four-store map, while the
    Stage 10 reconciliations prove the market-specific scope and lineage.
    """

    if not isinstance(source_map, StoreMap) or not isinstance(restored_map, StoreMap):
        raise ValidationError("Stage 10 evidence requires explicit source and restored store maps")
    if not _maps_are_disjoint(source_map, restored_map):
        raise ConflictError("Stage 10 source and restored cohorts must be physically disjoint")
    validated_registry = _validate_registry(registry)
    validated_scope = _validate_scope(scope)
    replay_unchanged = _require_bool(replay_unchanged, "replay_unchanged")
    normalized_failed_records = normalize_stage10_failed_records(failed_records)

    source_before = mutation_fingerprint(source_map)
    restored_before = mutation_fingerprint(restored_map)
    source_health = all_store_health(source_map, validated_registry)
    restored_health = all_store_health(restored_map, validated_registry)
    source_reconciliation = reconcile_stage10_market(
        source_map,
        validated_registry,
        validated_scope,
        failed_records=normalized_failed_records,
    )
    restored_reconciliation = reconcile_stage10_market(
        restored_map,
        validated_registry,
        validated_scope,
        failed_records=normalized_failed_records,
    )
    source_manifest = logical_manifest(source_map, validated_registry)
    restored_manifest = logical_manifest(restored_map, validated_registry)
    source_after = mutation_fingerprint(source_map)
    restored_after = mutation_fingerprint(restored_map)

    if source_before["sha256"] != source_after["sha256"]:
        raise ValidationError("Stage 10 source evidence reads mutated a store")
    if restored_before["sha256"] != restored_after["sha256"]:
        raise ValidationError("Stage 10 restored evidence reads mutated a store")
    if source_health.to_primitive() != restored_health.to_primitive():
        raise ValidationError("Stage 10 restored health differs from its source cohort")
    if source_manifest["sha256"] != restored_manifest["sha256"]:
        raise ValidationError("Stage 10 restored logical manifest differs from its source cohort")
    if source_reconciliation.to_primitive() != restored_reconciliation.to_primitive():
        raise ValidationError("Stage 10 restored raw reconciliation differs from source")

    material = {
        "reconciliation": source_reconciliation.to_primitive(),
        "registry_schema_version": validated_registry.schema_version,
        "registry_version": validated_registry.registry_version,
        "registry_source_sha256": validated_registry.source_sha256,
        "source_health_sha256": _sha256_json(source_health.to_primitive()),
        "restored_health_sha256": _sha256_json(restored_health.to_primitive()),
        "source_logical_manifest_sha256": source_manifest["sha256"],
        "restored_logical_manifest_sha256": restored_manifest["sha256"],
        "source_mutation_before_sha256": source_before["sha256"],
        "source_mutation_after_sha256": source_after["sha256"],
        "restored_mutation_before_sha256": restored_before["sha256"],
        "restored_mutation_after_sha256": restored_after["sha256"],
        "source_reconciliation_sha256": source_reconciliation.sha256,
        "restored_reconciliation_sha256": restored_reconciliation.sha256,
        "source_failure_manifest_sha256": source_reconciliation.failure_manifest_sha256,
        "restored_failure_manifest_sha256": restored_reconciliation.failure_manifest_sha256,
        "source_restored_equal": True,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "raw_reparsed_equal": True,
        "complete_symbol_coverage": (
            source_reconciliation.roster_count
            == source_reconciliation.successful_symbol_count
            + source_reconciliation.failed_symbol_count
            and source_reconciliation.successful_symbol_count
            == len(source_reconciliation.price_coverage)
        ),
        "historical_membership_inferred": False,
        "russell_excluded": source_reconciliation.russell_excluded,
        "replay_unchanged": replay_unchanged,
        "backup_restore_outcome": "validated_restored_cohort_equal",
    }
    return Stage10RepopulationEvidence(
        reconciliation=source_reconciliation,
        **{name: value for name, value in material.items() if name != "reconciliation"},
        sha256=_sha256_json(
            {
                "contract": "quant_data.stage10_repopulation_evidence",
                "contract_version": "1.1.0",
                "reconciliation": material["reconciliation"],
                "registry": {
                    "schema_version": material["registry_schema_version"],
                    "registry_version": material["registry_version"],
                    "source_sha256": material["registry_source_sha256"],
                },
                **{
                    name: value
                    for name, value in material.items()
                    if name
                    not in {
                        "reconciliation",
                        "registry_schema_version",
                        "registry_version",
                        "registry_source_sha256",
                    }
                },
            }
        ),
    )


def _render_generated_at(value: Callable[[], object] | object) -> str:
    candidate = value() if callable(value) else value
    if isinstance(candidate, datetime):
        if candidate.tzinfo is None or candidate.utcoffset() is None:
            raise ValidationError("Stage 10 generated_at must be timezone-aware")
        return (
            candidate.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    return _datetime_text(candidate, "generated_at")


@dataclass(frozen=True, slots=True)
class Stage10CandidateReceipt:
    """Private Stage 10 candidate receipt, never a promotion instruction."""

    attempt_id: str
    generated_at: str
    code_revision: str
    candidate_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise ValidationError("Stage 10 attempt_id must be a safe bounded identifier")
        _datetime_text(self.generated_at, "generated_at")
        if not isinstance(self.code_revision, str) or _CODE_REVISION.fullmatch(self.code_revision) is None:
            raise ValidationError("Stage 10 code_revision must be a lowercase source revision")
        if self.candidate_state != _CANDIDATE_STATE:
            raise ValidationError("Stage 10 receipt cannot claim an operational promotion")
        if not isinstance(self.evidence, Mapping):
            raise ValidationError("Stage 10 candidate receipt requires path-free evidence")
        frozen = _freeze_json(self.evidence, "candidate_evidence")
        if not isinstance(frozen, Mapping):
            raise ValidationError("Stage 10 candidate receipt requires path-free evidence")
        object.__setattr__(self, "evidence", frozen)
        _require_digest(self.evidence_sha256, "evidence_sha256")
        _require_digest(self.receipt_sha256, "receipt_sha256")
        evidence = _thaw_json(frozen)
        if not isinstance(evidence, Mapping):
            raise ValidationError("Stage 10 candidate receipt requires path-free evidence")
        material = {name: value for name, value in evidence.items() if name != "sha256"}
        if evidence.get("sha256") != self.evidence_sha256 or _sha256_json(material) != self.evidence_sha256:
            raise ValidationError("Stage 10 candidate receipt evidence digest is invalid")
        if self.receipt_sha256 != _sha256_json(self.material()):
            raise ValidationError("Stage 10 candidate receipt digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": self.attempt_id,
            "generated_at": self.generated_at,
            "code_revision": self.code_revision,
            "candidate_state": self.candidate_state,
            "evidence": _thaw_json(self.evidence),
            "evidence_sha256": self.evidence_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "receipt_sha256": self.receipt_sha256}


class PrivateStage10CandidateState:
    """Receipt-last, append-only private candidate state.

    The only persistent output is a path-free JSON receipt beneath the explicit
    caller-owned private root.  No current pointer, store mutation, promotion,
    export, scheduler, or public surface is available from this class.
    """

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)) or (isinstance(root, str) and not root.strip()):
            raise ValidationError("Stage 10 candidate root must be an explicit absolute directory")
        try:
            candidate = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError("Stage 10 candidate root must be an explicit absolute directory") from exc
        if (
            not candidate.is_absolute()
            or candidate == Path(candidate.anchor)
            or candidate == Path.home().resolve(strict=False)
            or (candidate.exists() and (not candidate.is_dir() or candidate.is_symlink()))
        ):
            raise ValidationError("Stage 10 candidate root is unavailable or too broad")
        self._root = candidate

    @property
    def root(self) -> Path:
        """The caller-owned private root; accessing it has no side effect."""

        return self._root

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _receipt_directory(self) -> Path:
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self._root.is_dir() or self._root.is_symlink():
                raise OSError("candidate root is unavailable")
            os.chmod(self._root, 0o700)
            directory = self._root / "promotion-candidates"
            directory.mkdir(mode=0o700, exist_ok=True)
            if not directory.is_dir() or directory.is_symlink():
                raise OSError("candidate directory is unavailable")
            os.chmod(directory, 0o700)
            self._fsync_directory(self._root)
        except OSError as exc:
            raise OSError("Stage 10 private candidate directory publication failed") from exc
        return directory

    def _publish_receipt(self, receipt: Stage10CandidateReceipt) -> None:
        payload = (dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RECEIPT_BYTES) + "\n").encode("utf-8")
        if not payload or len(payload) > _MAX_RECEIPT_BYTES:
            raise ResourceLimitError("Stage 10 candidate receipt exceeds its bounded size")
        directory = self._receipt_directory()
        final_path = directory / f"{receipt.attempt_id}.json"
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Stage 10 private candidate receipt already exists")
        temporary = directory / f".{receipt.attempt_id}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        linked = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # hard-link publication is atomic and cannot overwrite another receipt.
            os.link(temporary, final_path)
            linked = True
            os.chmod(final_path, 0o600)
            self._fsync_directory(directory)
            os.unlink(temporary)
            self._fsync_directory(directory)
        except FileExistsError as exc:
            raise ConflictError("Stage 10 private candidate receipt already exists") from exc
        except OSError as exc:
            raise OSError("Stage 10 private candidate receipt publication failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists() or temporary.is_symlink():
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            # Never remove an already linked receipt: it is immutable evidence.
            if linked and not final_path.is_file():
                raise OSError("Stage 10 private candidate receipt disappeared")

    def publish(
        self,
        evidence: Stage10RepopulationEvidence,
        *,
        code_revision: str,
        now: Callable[[], object] | object,
        attempt_id: str,
    ) -> Stage10CandidateReceipt:
        """Publish one append-only candidate receipt after every safety gate."""

        if not isinstance(evidence, Stage10RepopulationEvidence):
            raise ValidationError("Stage 10 candidate requires repopulation evidence")
        if not (
            evidence.source_restored_equal
            and evidence.source_reads_unchanged
            and evidence.restored_reads_unchanged
            and evidence.raw_reparsed_equal
            and evidence.complete_symbol_coverage
            and not evidence.historical_membership_inferred
            and evidence.russell_excluded
            and evidence.replay_unchanged
            and evidence.backup_restore_outcome == "validated_restored_cohort_equal"
        ):
            raise ValidationError("Stage 10 candidate evidence has not passed its safety gates")
        if not isinstance(code_revision, str) or _CODE_REVISION.fullmatch(code_revision) is None:
            raise ValidationError("Stage 10 code_revision must be a lowercase source revision")
        if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
            raise ValidationError("Stage 10 attempt_id must be a safe bounded identifier")
        generated_at = _render_generated_at(now)
        evidence_primitive = evidence.to_primitive()
        if evidence.sha256 != _sha256_json(evidence.material()):
            raise ValidationError("Stage 10 repopulation evidence digest is inconsistent")
        material = {
            "contract": "quant_data.stage10_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": attempt_id,
            "generated_at": generated_at,
            "code_revision": code_revision,
            "candidate_state": _CANDIDATE_STATE,
            "evidence": evidence_primitive,
            "evidence_sha256": evidence.sha256,
        }
        receipt = Stage10CandidateReceipt(
            attempt_id=attempt_id,
            generated_at=generated_at,
            code_revision=code_revision,
            candidate_state=_CANDIDATE_STATE,
            evidence=evidence_primitive,
            evidence_sha256=evidence.sha256,
            receipt_sha256=_sha256_json(material),
        )
        self._publish_receipt(receipt)
        return receipt


__all__ = (
    "PrivateStage10CandidateState",
    "Stage10CandidateReceipt",
    "Stage10MarketReconciliation",
    "Stage10RepopulationEvidence",
    "build_stage10_repopulation_evidence",
    "reconcile_stage10_market",
    "normalize_stage10_failed_records",
    "stage10_failure_manifest_sha256",
)
