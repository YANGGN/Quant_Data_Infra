"""Bounded Stage 9 non-production reconciliation and promotion evidence.

This module intentionally has no provider transport, environment lookup, default
paths, or operational-store promotion logic.  It verifies one already-published
FMP SPY backfill in an explicit four-store cohort, binds that result to a clean
restored cohort, and can publish a private *candidate-only* promotion receipt.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..json_codec import dumps_strict, loads_strict
from ..market.fmp_daily_prices import normalized_fmp_daily_price_response
from ..registry import Registry
from ..stores import StoreMap, StoreRole, canonical_path_uri, read_connection
from ..temporal import TemporalPrecision, TemporalValue
from .health import all_store_health


FMP_COLLECTOR_ID = "fmp.market.daily_price_backfill"
FMP_EVIDENCE_DATASET_ID = "market.fmp.daily_price_evidence"
FMP_IDENTITY_DATASET_ID = "market.fmp.instruments"
FMP_CANONICAL_DATASET_ID = "market.fmp.daily_prices"
FMP_MIGRATION_ID = "market:0009_fmp_daily_price_backfill"
FMP_ENDPOINT_PATH = "/stable/historical-price-eod/full"
FMP_PROVIDER = "fmp"
FMP_SYMBOL = "SPY"
FMP_ASSET_TYPE = "etf"
FMP_CURRENCY_SEGMENT = "USD"
FMP_PRICE_VARIANT = "fmp_full_eod_v1"
FMP_REQUEST_START_DATE = "2026-07-01"
FMP_REQUEST_END_DATE = "2026-07-31"
FMP_EXPECTED_DATES = (
    "2026-07-01",
    "2026-07-02",
    "2026-07-06",
    "2026-07-07",
    "2026-07-08",
    "2026-07-09",
    "2026-07-10",
    "2026-07-13",
    "2026-07-14",
    "2026-07-15",
    "2026-07-16",
    "2026-07-17",
    "2026-07-20",
    "2026-07-21",
    "2026-07-22",
    "2026-07-23",
    "2026-07-24",
    "2026-07-27",
    "2026-07-28",
    "2026-07-29",
    "2026-07-30",
    "2026-07-31",
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CODE_REVISION = re.compile(r"[0-9a-f]{7,64}\Z")
_ATTEMPT_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
_MAX_RECEIPT_BYTES = 256 * 1024
_PROMOTION_STATE = "candidate_only_no_operational_promotion"

_CORRECTION_EVIDENCE_KEYS = frozenset(
    {
        "outcome",
        "capture_count",
        "version_count",
        "max_correction_sequence",
        "corrected_trade_date",
        "corrected_close_sha256",
    }
)


def _validated_correction_evidence(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _CORRECTION_EVIDENCE_KEYS:
        raise ValidationError("Stage 9 correction evidence shape is invalid")
    material = dict(value)
    if (
        material["outcome"] != "succeeded"
        or material["capture_count"] != 2
        or material["version_count"] != 23
        or material["max_correction_sequence"] != 2
        or isinstance(material["capture_count"], bool)
        or isinstance(material["version_count"], bool)
        or isinstance(material["max_correction_sequence"], bool)
        or material["corrected_trade_date"] not in FMP_EXPECTED_DATES
    ):
        raise ValidationError("Stage 9 correction evidence does not prove one isolated revision")
    _require_sha256(material["corrected_close_sha256"], "corrected_close_sha256")
    return MappingProxyType(material)



def _sha256_primitive(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _require_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be boolean")
    return value


def _require_text(value: object, field_name: str, *, maximum: int = 160) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValidationError(f"{field_name} must be a bounded normalized string")
    return value


def _render_utc_datetime(value: object, field_name: str) -> str:
    candidate = value() if callable(value) else value
    if isinstance(candidate, str):
        parsed = TemporalValue.parse(candidate, pointer=f"/{field_name}")
        if (
            parsed.precision is not TemporalPrecision.DATETIME
            or not isinstance(parsed.value, datetime)
        ):
            raise ValidationError(f"{field_name} must be an aware datetime")
        return (
            parsed.value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if (
        not isinstance(candidate, datetime)
        or candidate.tzinfo is None
        or candidate.utcoffset() is None
    ):
        raise ValidationError(f"{field_name} must be an aware datetime")
    return (
        candidate.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _freeze_json(value: object, field_name: str) -> object:
    """Detach a strict-JSON value for the immutable receipt dataclasses."""

    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{field_name} keys must be strings")
            normalized[key] = _freeze_json(child, field_name)
        return MappingProxyType(normalized)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child, field_name) for child in value)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError(f"{field_name} must be strict JSON material")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _date_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValidationError(f"{field_name} must be a date sequence")
    dates = tuple(value)
    if not dates or len(dates) != len(set(dates)):
        raise ValidationError(f"{field_name} must contain unique dates")
    for item in dates:
        if not isinstance(item, str):
            raise ValidationError(f"{field_name} must contain ISO dates")
        try:
            date.fromisoformat(item)
        except ValueError as exc:
            raise ValidationError(f"{field_name} must contain ISO dates") from exc
    return dates


def _exact_expected_dates(value: object) -> tuple[str, ...]:
    dates = _date_tuple(value, "expected_dates")
    if dates != FMP_EXPECTED_DATES:
        raise ValidationError("Stage 9 FMP reconciliation requires the reviewed 22-date SPY set")
    return dates


def _decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValidationError(f"{field_name} must be a finite decimal")
    return result


def _require_aware_timestamp(
    value: object,
    precision: object,
    field_name: str,
) -> str:
    if precision != "datetime" or not isinstance(value, str):
        raise ValidationError(f"{field_name} must retain aware datetime precision")
    parsed = TemporalValue.parse(value, pointer=f"/{field_name}")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise ValidationError(f"{field_name} must retain aware datetime precision")
    return value


def _collector(registry: Registry) -> Mapping[str, Any]:
    if not isinstance(registry, Registry):
        raise ValidationError("Stage 9 reconciliation requires a validated registry")
    matches = [
        collector
        for collector in registry.collectors
        if collector.get("id") == FMP_COLLECTOR_ID
    ]
    if len(matches) != 1:
        raise ValidationError("Stage 9 FMP collector is not registered exactly once")
    collector = matches[0]
    if (
        collector.get("handler") != "market.fmp_daily_price"
        or collector.get("network") is not True
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector.get("output_datasets", ()))
        != (
            FMP_EVIDENCE_DATASET_ID,
            FMP_IDENTITY_DATASET_ID,
            FMP_CANONICAL_DATASET_ID,
        )
        or not isinstance(collector.get("version"), str)
    ):
        raise ValidationError("Stage 9 FMP collector declaration is invalid")
    return collector


def _market_migration(registry: Registry) -> tuple[str, str]:
    matches = [item for item in registry.migrations if item.id == FMP_MIGRATION_ID]
    if len(matches) != 1:
        raise ValidationError("Stage 9 FMP migration is not registered exactly once")
    migration = matches[0]
    if migration.store != StoreRole.MARKET.value:
        raise ValidationError("Stage 9 FMP migration must belong to the market store")
    return migration.id, _require_sha256(migration.sha256, "migration_sha256")


def _validate_fmp_registry(registry: Registry) -> tuple[Mapping[str, Any], tuple[str, str]]:
    collector = _collector(registry)
    migration = _market_migration(registry)
    dataset_ids = {item.id for item in registry.datasets}
    if not {
        FMP_EVIDENCE_DATASET_ID,
        FMP_IDENTITY_DATASET_ID,
        FMP_CANONICAL_DATASET_ID,
    }.issubset(dataset_ids):
        raise ValidationError("Stage 9 FMP datasets are not completely registered")
    return collector, migration


def _scope_value(scope: Mapping[str, object], name: str) -> object:
    if name not in scope:
        raise ValidationError("FMP capture request scope is incomplete")
    return scope[name]


def _validated_request_scope(value: object) -> tuple[Mapping[str, object], str]:
    if not isinstance(value, Mapping):
        raise ValidationError("FMP capture request scope must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValidationError("FMP capture request scope is invalid")
    scope = dict(value)
    expected = {
        "symbol": FMP_SYMBOL,
        "start_date": FMP_REQUEST_START_DATE,
        "end_date": FMP_REQUEST_END_DATE,
        "price_variant": FMP_PRICE_VARIANT,
        "currency_segment": FMP_CURRENCY_SEGMENT,
    }
    allowed_names = frozenset((*expected, "provider", "endpoint_path"))
    if set(scope).difference(allowed_names):
        raise ValidationError("FMP capture request scope contains a forbidden field")
    for name, wanted in expected.items():
        if _scope_value(scope, name) != wanted:
            raise ValidationError("FMP capture request scope does not match the bounded slice")
    if "provider" in scope and scope["provider"] != FMP_PROVIDER:
        raise ValidationError("FMP capture request scope has an invalid provider")
    if "endpoint_path" in scope and scope["endpoint_path"] != FMP_ENDPOINT_PATH:
        raise ValidationError("FMP capture request scope has an invalid endpoint")
    try:
        encoded = dumps_strict(scope, max_bytes=64 * 1024)
    except (ValidationError, ResourceLimitError):
        raise
    return MappingProxyType(scope), hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_capture_row(row: Any) -> Mapping[str, object]:
    required_text = (
        "capture_id",
        "artifact_id",
        "snapshot_id",
        "run_id",
        "content_type",
        "normalization_version",
    )
    if any(not isinstance(row[name], str) or not row[name] for name in required_text):
        raise ValidationError("FMP capture evidence chain is incomplete")
    if (
        row["dataset_id"] != FMP_EVIDENCE_DATASET_ID
        or row["provider"] != FMP_PROVIDER
        or row["endpoint_path"] != FMP_ENDPOINT_PATH
        or row["http_status"] != 200
        or row["completeness"] != "complete"
        or row["row_count"] != len(FMP_EXPECTED_DATES)
    ):
        raise ValidationError("FMP capture contract is invalid")
    response = row["response_bytes"]
    if not isinstance(response, (bytes, bytearray, memoryview)) or not response:
        raise ValidationError("FMP raw response evidence is missing")
    response_bytes = bytes(response)
    response_sha256 = _require_sha256(row["response_sha256"], "response_sha256")
    if hashlib.sha256(response_bytes).hexdigest() != response_sha256:
        raise ValidationError("FMP raw response digest does not match its capture")
    try:
        normalized_response_rows = normalized_fmp_daily_price_response(response_bytes)
    except (ValidationError, ResourceLimitError):
        raise ValidationError("FMP raw response is not the reviewed complete batch") from None

    semantic_identity = _require_sha256(row["semantic_identity"], "semantic_identity")
    request_scope_sha256 = _require_sha256(
        row["request_scope_sha256"], "request_scope_sha256"
    )
    try:
        scope = loads_strict(str(row["request_scope_json"]), max_bytes=64 * 1024)
    except (ValidationError, ResourceLimitError):
        raise ValidationError("FMP capture request scope is invalid") from None
    scope, calculated_scope_sha256 = _validated_request_scope(scope)
    if calculated_scope_sha256 != request_scope_sha256:
        raise ValidationError("FMP capture request scope digest does not match")
    captured_at = _require_aware_timestamp(
        row["captured_at"], row["captured_precision"], "capture_captured_at"
    )
    return {
        "capture_id": str(row["capture_id"]),
        "artifact_id": str(row["artifact_id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "run_id": str(row["run_id"]),
        "request_scope": scope,
        "response_sha256": response_sha256,
        "semantic_identity": semantic_identity,
        "request_scope_sha256": request_scope_sha256,
        "captured_at": captured_at,
        "normalization_version": str(row["normalization_version"]),
        "normalized_response_rows": normalized_response_rows,
    }


def _capture_rows(connection: Any) -> tuple[Mapping[str, object], ...]:
    rows = tuple(
        connection.execute(
            """
            SELECT capture_id, dataset_id, provider, endpoint_path, request_scope_json,
                   request_scope_sha256, response_sha256, response_bytes, http_status,
                   content_type, semantic_identity, completeness, artifact_id, snapshot_id,
                   captured_at, captured_precision, row_count, normalization_version, run_id
            FROM fmp_daily_price_captures
            ORDER BY capture_id
            """
        )
    )
    return tuple(_validated_capture_row(row) for row in rows)


def _capture_row(connection: Any) -> Mapping[str, object]:
    captures = _capture_rows(connection)
    if len(captures) != 1:
        raise ValidationError("Stage 9 reconciliation requires exactly one FMP capture")
    return captures[0]


def _identity_row(connection: Any) -> Mapping[str, object]:
    rows = tuple(
        connection.execute(
            """
            SELECT instrument_id, provider, provider_symbol, asset_type,
                   currency_segment, identity_seed_sha256, effective_from,
                   captured_at, captured_precision, run_id
            FROM fmp_instrument_identities
            ORDER BY instrument_id
            """
        )
    )
    if len(rows) != 1:
        raise ValidationError("Stage 9 reconciliation requires exactly one FMP instrument")
    row = rows[0]
    if (
        row["provider"] != FMP_PROVIDER
        or row["provider_symbol"] != FMP_SYMBOL
        or row["asset_type"] != FMP_ASSET_TYPE
        or row["currency_segment"] != FMP_CURRENCY_SEGMENT
        or not isinstance(row["instrument_id"], str)
        or not row["instrument_id"]
        or not isinstance(row["run_id"], str)
        or not row["run_id"]
    ):
        raise ValidationError("FMP instrument identity binding is invalid")
    _require_sha256(row["identity_seed_sha256"], "identity_seed_sha256")
    try:
        date.fromisoformat(str(row["effective_from"]))
    except ValueError as exc:
        raise ValidationError("FMP instrument effective date is invalid") from exc
    captured_at = _require_aware_timestamp(
        row["captured_at"], row["captured_precision"], "identity_captured_at"
    )
    return {
        "instrument_id": str(row["instrument_id"]),
        "run_id": str(row["run_id"]),
        "identity_seed_sha256": str(row["identity_seed_sha256"]),
        "captured_at": captured_at,
    }


def _validated_version_row(
    row: Mapping[str, object],
    *,
    identity: Mapping[str, object],
    capture: Mapping[str, object],
) -> dict[str, object]:
    if (
        row["instrument_id"] != identity["instrument_id"]
        or row["provider"] != FMP_PROVIDER
        or row["price_variant"] != FMP_PRICE_VARIANT
        or row["currency_segment"] != FMP_CURRENCY_SEGMENT
        or row["capture_id"] != capture["capture_id"]
        or row["artifact_id"] != capture["artifact_id"]
        or row["snapshot_id"] != capture["snapshot_id"]
        or row["run_id"] != capture["run_id"]
    ):
        raise ValidationError("FMP price version evidence chain is inconsistent")
    for name in ("version_id", "trade_date"):
        if not isinstance(row[name], str) or not row[name]:
            raise ValidationError("FMP price version identity is invalid")
    try:
        date.fromisoformat(str(row["trade_date"]))
    except ValueError as exc:
        raise ValidationError("FMP price version date is invalid") from exc
    correction_sequence = row["correction_sequence"]
    source_row = row["source_row"]
    if (
        isinstance(correction_sequence, bool)
        or not isinstance(correction_sequence, int)
        or correction_sequence < 1
        or isinstance(source_row, bool)
        or not isinstance(source_row, int)
        or source_row < 1
    ):
        raise ValidationError("FMP price version sequence is invalid")
    open_value = _decimal(row["open_value"], "open_value")
    high_value = _decimal(row["high_value"], "high_value")
    low_value = _decimal(row["low_value"], "low_value")
    close_value = _decimal(row["close_value"], "close_value")
    volume = row["volume"]
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
        raise ValidationError("FMP price volume is invalid")
    if (
        low_value > min(open_value, close_value)
        or high_value < max(open_value, close_value)
        or low_value > high_value
    ):
        raise ValidationError("FMP price OHLC values are inconsistent")
    available_at = _require_aware_timestamp(
        row["available_at"], row["available_precision"], "available_at"
    )
    captured_at = _require_aware_timestamp(
        row["captured_at"], row["captured_precision"], "version_captured_at"
    )
    if available_at != captured_at or captured_at != capture["captured_at"]:
        raise ValidationError("FMP price availability must equal the local capture boundary")
    return {
        "version_id": str(row["version_id"]),
        "instrument_id": str(row["instrument_id"]),
        "trade_date": str(row["trade_date"]),
        "provider": FMP_PROVIDER,
        "price_variant": FMP_PRICE_VARIANT,
        "currency_segment": FMP_CURRENCY_SEGMENT,
        "open": format(open_value.normalize(), "f") if not open_value.is_zero() else "0",
        "high": format(high_value.normalize(), "f") if not high_value.is_zero() else "0",
        "low": format(low_value.normalize(), "f") if not low_value.is_zero() else "0",
        "close": format(close_value.normalize(), "f") if not close_value.is_zero() else "0",
        "volume": volume,
        "available_at": available_at,
        "captured_at": captured_at,
        "correction_sequence": correction_sequence,
        "supersedes_version_id": row["supersedes_version_id"],
        "capture_id": str(row["capture_id"]),
        "artifact_id": str(row["artifact_id"]),
        "snapshot_id": str(row["snapshot_id"]),
        "run_id": str(row["run_id"]),
        "source_row": source_row,
    }


def _reconcile_rows(
    connection: Any,
    expected_dates: tuple[str, ...],
) -> tuple[CoverageReconciliation, Mapping[str, object]]:
    capture = _capture_row(connection)
    identity = _identity_row(connection)
    if identity["run_id"] != capture["run_id"] or identity["captured_at"] != capture["captured_at"]:
        raise ValidationError("FMP instrument identity is not bound to the capture run")

    version_rows = tuple(
        connection.execute(
            """
            SELECT version_id, instrument_id, trade_date, provider, price_variant,
                   currency_segment, open_value, high_value, low_value, close_value,
                   volume, available_at, available_precision, captured_at,
                   captured_precision, correction_sequence, supersedes_version_id,
                   capture_id, artifact_id, snapshot_id, run_id, source_row
            FROM fmp_daily_price_versions
            ORDER BY trade_date, correction_sequence, version_id
            """
        )
    )
    if not version_rows:
        raise ValidationError("FMP capture has no immutable price versions")
    versions = tuple(
        _validated_version_row(row, identity=identity, capture=capture)
        for row in version_rows
    )
    versions_by_key: dict[tuple[str, str, str, str, str], list[dict[str, object]]] = {}
    for version in versions:
        key = (
            str(version["instrument_id"]),
            str(version["trade_date"]),
            str(version["provider"]),
            str(version["price_variant"]),
            str(version["currency_segment"]),
        )
        versions_by_key.setdefault(key, []).append(version)
    for history in versions_by_key.values():
        history.sort(key=lambda item: (int(item["correction_sequence"]), str(item["version_id"])))
        for index, version in enumerate(history, start=1):
            if version["correction_sequence"] != index:
                raise ValidationError("FMP correction sequence is not contiguous")
            expected_supersedes = None if index == 1 else history[index - 2]["version_id"]
            if version["supersedes_version_id"] != expected_supersedes:
                raise ValidationError("FMP correction supersession lineage is invalid")

    current_rows = tuple(
        connection.execute(
            """
            SELECT current.instrument_id, current.trade_date, current.provider,
                   current.price_variant, current.currency_segment,
                   current.current_version_id
            FROM fmp_daily_prices AS current
            ORDER BY current.trade_date, current.current_version_id
            """
        )
    )
    if len(current_rows) != len(expected_dates):
        raise ValidationError("FMP current-price row count does not match the reviewed scope")
    current_ids: set[str] = set()
    observed_dates: list[str] = []
    current_material: list[dict[str, object]] = []
    source_rows: set[int] = set()
    for current in current_rows:
        key = (
            current["instrument_id"],
            current["trade_date"],
            current["provider"],
            current["price_variant"],
            current["currency_segment"],
        )
        if (
            key[0] != identity["instrument_id"]
            or key[2] != FMP_PROVIDER
            or key[3] != FMP_PRICE_VARIANT
            or key[4] != FMP_CURRENCY_SEGMENT
            or not isinstance(key[1], str)
        ):
            raise ValidationError("FMP current-price binding is invalid")
        history = versions_by_key.get(
            (str(key[0]), str(key[1]), str(key[2]), str(key[3]), str(key[4]))
        )
        if history is None:
            raise ValidationError("FMP current price has no immutable version history")
        current_version_id = current["current_version_id"]
        if not isinstance(current_version_id, str) or current_version_id != history[-1]["version_id"]:
            raise ValidationError("FMP current price does not select the newest version")
        if current_version_id in current_ids:
            raise ValidationError("FMP current price version is selected more than once")
        current_ids.add(current_version_id)
        observed_dates.append(str(key[1]))
        selected = history[-1]
        source_rows.add(int(selected["source_row"]))
        current_material.append(
            {
                "trade_date": selected["trade_date"],
                "open": selected["open"],
                "high": selected["high"],
                "low": selected["low"],
                "close": selected["close"],
                "volume": selected["volume"],
                "available_at": selected["available_at"],
                "captured_at": selected["captured_at"],
                "correction_sequence": selected["correction_sequence"],
            }
        )
    if tuple(observed_dates) != expected_dates:
        raise ValidationError("FMP current-price dates have a gap, duplicate, or extra row")
    if len(source_rows) != len(expected_dates):
        raise ValidationError("FMP source-row evidence is not unique for the bounded scope")
    if len(versions_by_key) != len(expected_dates):
        raise ValidationError("FMP immutable version history contains an unexpected key")
    if len(current_ids) != len(expected_dates):
        raise ValidationError("FMP current-price identity is incomplete")

    coverage_material = {
        "expected_dates": list(expected_dates),
        "observed_dates": observed_dates,
    }
    row_material = sorted(current_material, key=lambda item: str(item["trade_date"]))
    current_response_rows = tuple(
        {
            "symbol": FMP_SYMBOL,
            "trade_date": item["trade_date"],
            "open": item["open"],
            "high": item["high"],
            "low": item["low"],
            "close": item["close"],
            "volume": item["volume"],
            "price_variant": FMP_PRICE_VARIANT,
            "currency_segment": FMP_CURRENCY_SEGMENT,
        }
        for item in row_material
    )
    if current_response_rows != tuple(capture["normalized_response_rows"]):
        raise ValidationError("FMP database rows do not match the captured response")
    sample_indexes = tuple(dict.fromkeys((0, len(row_material) // 2, len(row_material) - 1)))
    sample_material = [row_material[index] for index in sample_indexes]
    chain_material = {
        "capture": {
            "request_scope_sha256": capture["request_scope_sha256"],
            "response_sha256": capture["response_sha256"],
            "semantic_identity": capture["semantic_identity"],
            "normalization_version": capture["normalization_version"],
            "normalized_response_rows_sha256": _sha256_primitive(capture["normalized_response_rows"]),
        },
        "identity_seed_sha256": identity["identity_seed_sha256"],
        "versions": [
            {
                "trade_date": version["trade_date"],
                "version_id": version["version_id"],
                "correction_sequence": version["correction_sequence"],
                "supersedes_version_id": version["supersedes_version_id"],
            }
            for version in versions
        ],
    }
    reconciliation = CoverageReconciliation(
        expected_dates=expected_dates,
        observed_dates=tuple(observed_dates),
        row_count=len(current_material),
        version_count=len(versions),
        capture_count=1,
        request_scope_sha256=str(capture["request_scope_sha256"]),
        response_sha256=str(capture["response_sha256"]),
        semantic_identity=str(capture["semantic_identity"]),
        coverage_sha256=_sha256_primitive(coverage_material),
        row_sha256=_sha256_primitive(row_material),
        sample_sha256=_sha256_primitive(sample_material),
        evidence_chain_sha256=_sha256_primitive(chain_material),
        capture_boundary_verified=True,
        correction_lineage_verified=True,
        captured_response_verified=True,
    )
    return reconciliation, capture


@dataclass(frozen=True, slots=True)
class CoverageReconciliation:
    """Path-free reconciliation of the one reviewed FMP SPY backfill."""

    expected_dates: tuple[str, ...]
    observed_dates: tuple[str, ...]
    row_count: int
    version_count: int
    capture_count: int
    request_scope_sha256: str
    response_sha256: str
    semantic_identity: str
    coverage_sha256: str
    row_sha256: str
    sample_sha256: str
    evidence_chain_sha256: str
    capture_boundary_verified: bool
    correction_lineage_verified: bool
    captured_response_verified: bool

    def __post_init__(self) -> None:
        expected = _exact_expected_dates(self.expected_dates)
        observed = _date_tuple(self.observed_dates, "observed_dates")
        if observed != expected:
            raise ValidationError("Coverage reconciliation dates must match the reviewed scope")
        for field_name in (
            "request_scope_sha256",
            "response_sha256",
            "semantic_identity",
            "coverage_sha256",
            "row_sha256",
            "sample_sha256",
            "evidence_chain_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        for field_name in ("row_count", "version_count", "capture_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValidationError(f"{field_name} must be a positive integer")
        if self.row_count != len(FMP_EXPECTED_DATES) or self.capture_count != 1:
            raise ValidationError("Coverage reconciliation cardinality is invalid")
        _require_bool(self.capture_boundary_verified, "capture_boundary_verified")
        _require_bool(self.correction_lineage_verified, "correction_lineage_verified")
        _require_bool(self.captured_response_verified, "captured_response_verified")
        object.__setattr__(self, "expected_dates", expected)
        object.__setattr__(self, "observed_dates", observed)

    def to_primitive(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage9_coverage_reconciliation",
            "contract_version": "1.0.0",
            "provider": FMP_PROVIDER,
            "symbol": FMP_SYMBOL,
            "asset_type": FMP_ASSET_TYPE,
            "price_variant": FMP_PRICE_VARIANT,
            "currency_segment": FMP_CURRENCY_SEGMENT,
            "expected_dates": list(self.expected_dates),
            "observed_dates": list(self.observed_dates),
            "row_count": self.row_count,
            "version_count": self.version_count,
            "capture_count": self.capture_count,
            "request_scope_sha256": self.request_scope_sha256,
            "response_sha256": self.response_sha256,
            "semantic_identity": self.semantic_identity,
            "coverage_sha256": self.coverage_sha256,
            "row_sha256": self.row_sha256,
            "sample_sha256": self.sample_sha256,
            "evidence_chain_sha256": self.evidence_chain_sha256,
            "capture_boundary_verified": self.capture_boundary_verified,
            "correction_lineage_verified": self.correction_lineage_verified,
            "captured_response_verified": self.captured_response_verified,
        }


def reconcile_fmp_spy_market(
    store_map: StoreMap,
    registry: Registry,
    *,
    expected_dates: Sequence[str] = FMP_EXPECTED_DATES,
) -> CoverageReconciliation:
    """Read and strictly reconcile the bounded nonproduction FMP SPY cohort.

    Only the Stage 9 ``fmp_*`` relations are queried.  The function takes no
    credentials and has no write path; fingerprints prove that it remained
    observational.
    """

    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 9 reconciliation requires an explicit four-store map")
    expected = _exact_expected_dates(expected_dates)
    _validate_fmp_registry(registry)
    before = mutation_fingerprint(store_map)
    with read_connection(
        store_map,
        StoreRole.MARKET,
        expected_anchor=registry.store(StoreRole.MARKET.value).anchor_relation,
    ) as connection:
        reconciliation, _capture = _reconcile_rows(connection, expected)
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 9 reconciliation mutated an operational store")
    return reconciliation



def _response_rows_by_date(
    value: object,
) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, tuple):
        raise ValidationError("FMP correction response rows are invalid")
    rows: dict[str, Mapping[str, object]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise ValidationError("FMP correction response row is invalid")
        trade_date = row.get("trade_date")
        if not isinstance(trade_date, str) or trade_date in rows:
            raise ValidationError("FMP correction response date is invalid")
        rows[trade_date] = row
    if tuple(rows) != FMP_EXPECTED_DATES:
        raise ValidationError("FMP correction response dates are not the reviewed scope")
    return rows


def _derive_correction_evidence(
    connection: Any,
    source_coverage: CoverageReconciliation,
) -> Mapping[str, object]:
    captures = _capture_rows(connection)
    if len(captures) != 2:
        raise ValidationError("Stage 9 scratch rehearsal requires exactly two FMP captures")
    source_matches = [
        capture
        for capture in captures
        if capture["request_scope_sha256"] == source_coverage.request_scope_sha256
        and capture["response_sha256"] == source_coverage.response_sha256
        and capture["semantic_identity"] == source_coverage.semantic_identity
    ]
    if len(source_matches) != 1:
        raise ValidationError("Stage 9 scratch rehearsal does not bind its source capture")
    source_capture = source_matches[0]
    correction_capture = next(
        capture for capture in captures if capture is not source_capture
    )
    if (
        correction_capture["request_scope"] != source_capture["request_scope"]
        or correction_capture["request_scope_sha256"]
        != source_capture["request_scope_sha256"]
        or correction_capture["response_sha256"]
        == source_capture["response_sha256"]
        or correction_capture["semantic_identity"]
        == source_capture["semantic_identity"]
    ):
        raise ValidationError("Stage 9 scratch correction capture is not a bounded revision")

    source_rows = _response_rows_by_date(source_capture["normalized_response_rows"])
    correction_rows = _response_rows_by_date(
        correction_capture["normalized_response_rows"]
    )
    changed_dates = tuple(
        trade_date
        for trade_date in FMP_EXPECTED_DATES
        if source_rows[trade_date] != correction_rows[trade_date]
    )
    if len(changed_dates) != 1:
        raise ValidationError(
            "Stage 9 scratch correction must change exactly one reviewed trade date"
        )
    corrected_trade_date = changed_dates[0]

    identity = _identity_row(connection)
    if (
        identity["run_id"] != source_capture["run_id"]
        or identity["captured_at"] != source_capture["captured_at"]
    ):
        raise ValidationError("Stage 9 scratch identity is not bound to the source capture")

    rows = tuple(
        connection.execute(
            """
            SELECT version_id, instrument_id, trade_date, provider, price_variant,
                   currency_segment, open_value, high_value, low_value, close_value,
                   volume, available_at, available_precision, captured_at,
                   captured_precision, correction_sequence, supersedes_version_id,
                   capture_id, artifact_id, snapshot_id, run_id, source_row
            FROM fmp_daily_price_versions
            ORDER BY trade_date, correction_sequence, version_id
            """
        )
    )
    if len(rows) != len(FMP_EXPECTED_DATES) + 1:
        raise ValidationError("Stage 9 scratch rehearsal requires exactly 23 versions")
    captures_by_id = {
        str(capture["capture_id"]): capture for capture in captures
    }
    versions_by_date: dict[str, list[dict[str, object]]] = {}
    source_rows_by_capture: dict[str, set[int]] = {
        str(capture["capture_id"]): set() for capture in captures
    }
    for row in rows:
        capture_id = row["capture_id"]
        if not isinstance(capture_id, str) or capture_id not in captures_by_id:
            raise ValidationError("Stage 9 scratch version has an unknown capture")
        capture = captures_by_id[capture_id]
        version = _validated_version_row(row, identity=identity, capture=capture)
        if int(version["source_row"]) > len(FMP_EXPECTED_DATES):
            raise ValidationError("Stage 9 scratch version source row is outside the response")
        source_rows_by_capture[capture_id].add(int(version["source_row"]))
        expected = _response_rows_by_date(
            capture["normalized_response_rows"]
        ).get(str(version["trade_date"]))
        if expected is None or any(
            version[name] != expected[name]
            for name in ("open", "high", "low", "close", "volume")
        ):
            raise ValidationError(
                "Stage 9 scratch canonical version does not match its raw response"
            )
        versions_by_date.setdefault(str(version["trade_date"]), []).append(version)

    if tuple(sorted(versions_by_date)) != FMP_EXPECTED_DATES:
        raise ValidationError("Stage 9 scratch versions are outside the reviewed scope")
    if source_rows_by_capture[str(source_capture["capture_id"])] != set(
        range(1, len(FMP_EXPECTED_DATES) + 1)
    ):
        raise ValidationError("Stage 9 source capture does not bind all raw response rows")

    corrected_version: dict[str, object] | None = None
    for trade_date in FMP_EXPECTED_DATES:
        history = versions_by_date.get(trade_date)
        if history is None:
            raise ValidationError("Stage 9 scratch current history is incomplete")
        history.sort(
            key=lambda value: (
                int(value["correction_sequence"]), str(value["version_id"])
            )
        )
        if trade_date == corrected_trade_date:
            if (
                len(history) != 2
                or tuple(value["correction_sequence"] for value in history) != (1, 2)
                or history[0]["capture_id"] != source_capture["capture_id"]
                or history[1]["capture_id"] != correction_capture["capture_id"]
                or history[0]["supersedes_version_id"] is not None
                or history[1]["supersedes_version_id"] != history[0]["version_id"]
            ):
                raise ValidationError(
                    "Stage 9 scratch correction predecessor lineage is invalid"
                )
            corrected_version = history[1]
        elif (
            len(history) != 1
            or history[0]["correction_sequence"] != 1
            or history[0]["capture_id"] != source_capture["capture_id"]
            or history[0]["supersedes_version_id"] is not None
        ):
            raise ValidationError(
                "Stage 9 scratch rehearsal has an unexpected correction history"
            )

    if corrected_version is None:
        raise ValidationError("Stage 9 scratch correction is unavailable")
    current_rows = tuple(
        connection.execute(
            """
            SELECT instrument_id, trade_date, provider, price_variant,
                   currency_segment, current_version_id
            FROM fmp_daily_prices
            ORDER BY trade_date, current_version_id
            """
        )
    )
    if len(current_rows) != len(FMP_EXPECTED_DATES):
        raise ValidationError("Stage 9 scratch current rows are incomplete")
    for current in current_rows:
        trade_date = current["trade_date"]
        if (
            current["instrument_id"] != identity["instrument_id"]
            or current["provider"] != FMP_PROVIDER
            or current["price_variant"] != FMP_PRICE_VARIANT
            or current["currency_segment"] != FMP_CURRENCY_SEGMENT
            or not isinstance(trade_date, str)
            or trade_date not in versions_by_date
        ):
            raise ValidationError("Stage 9 scratch current row binding is invalid")
        history = versions_by_date[trade_date]
        if current["current_version_id"] != history[-1]["version_id"]:
            raise ValidationError(
                "Stage 9 scratch current row does not select its latest version"
            )

    corrected_close = corrected_version["close"]
    if (
        not isinstance(corrected_close, str)
        or corrected_close != correction_rows[corrected_trade_date]["close"]
    ):
        raise ValidationError("Stage 9 scratch corrected close is not raw-response bound")
    evidence = {
        "outcome": "succeeded",
        "capture_count": len(captures),
        "version_count": len(rows),
        "max_correction_sequence": max(
            int(version["correction_sequence"])
            for history in versions_by_date.values()
            for version in history
        ),
        "corrected_trade_date": corrected_trade_date,
        "corrected_close_sha256": hashlib.sha256(
            corrected_close.encode("utf-8")
        ).hexdigest(),
    }
    return _validated_correction_evidence(evidence)


def _derive_scratch_correction_evidence(
    rehearsal_map: StoreMap,
    registry: Registry,
    source_coverage: CoverageReconciliation,
) -> Mapping[str, object]:
    if not isinstance(rehearsal_map, StoreMap):
        raise ValidationError("Stage 9 scratch rehearsal requires explicit stores")
    before = mutation_fingerprint(rehearsal_map)
    with read_connection(
        rehearsal_map,
        StoreRole.MARKET,
        expected_anchor=registry.store(StoreRole.MARKET.value).anchor_relation,
    ) as connection:
        evidence = _derive_correction_evidence(connection, source_coverage)
    after = mutation_fingerprint(rehearsal_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 9 scratch proof reads mutated a store")
    return evidence

@dataclass(frozen=True, slots=True)
class RepopulationEvidence:
    """Immutable, path-free evidence for a source/restored Stage 9 cohort."""

    coverage: CoverageReconciliation
    registry_schema_version: str
    registry_revision: str
    registry_source_sha256: str
    migration_id: str
    migration_sha256: str
    collector_id: str
    collector_version: str
    configuration_env_names: tuple[str, ...]
    request_scope_sha256: str
    response_sha256: str
    semantic_identity: str
    source_health_sha256: str
    restored_health_sha256: str
    source_logical_manifest_sha256: str
    restored_logical_manifest_sha256: str
    source_mutation_before_sha256: str
    source_mutation_after_sha256: str
    restored_mutation_before_sha256: str
    restored_mutation_after_sha256: str
    source_restored_equal: bool
    source_reads_unchanged: bool
    restored_reads_unchanged: bool
    replay_unchanged: bool
    correction_rehearsed: bool
    correction_evidence: Mapping[str, object]
    correction_evidence_sha256: str
    backup_restore_outcome: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, CoverageReconciliation):
            raise ValidationError("Repopulation evidence requires coverage reconciliation")
        for field_name in (
            "registry_schema_version",
            "registry_revision",
            "migration_id",
            "collector_id",
            "collector_version",
            "backup_restore_outcome",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "registry_source_sha256",
            "migration_sha256",
            "request_scope_sha256",
            "response_sha256",
            "semantic_identity",
            "source_health_sha256",
            "restored_health_sha256",
            "source_logical_manifest_sha256",
            "restored_logical_manifest_sha256",
            "source_mutation_before_sha256",
            "source_mutation_after_sha256",
            "restored_mutation_before_sha256",
            "restored_mutation_after_sha256",
            "correction_evidence_sha256",
            "sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        names = tuple(self.configuration_env_names)
        if names != ("FMP_API_KEY",):
            raise ValidationError("Stage 9 evidence must bind only the FMP credential name")
        object.__setattr__(self, "configuration_env_names", names)
        for field_name in (
            "source_restored_equal",
            "source_reads_unchanged",
            "restored_reads_unchanged",
            "replay_unchanged",
            "correction_rehearsed",
        ):
            _require_bool(getattr(self, field_name), field_name)
        correction_evidence = _validated_correction_evidence(self.correction_evidence)
        object.__setattr__(self, "correction_evidence", correction_evidence)
        if self.correction_evidence_sha256 != _sha256_primitive(dict(correction_evidence)):
            raise ValidationError("Stage 9 correction evidence digest is inconsistent")
        if (
            self.request_scope_sha256 != self.coverage.request_scope_sha256
            or self.response_sha256 != self.coverage.response_sha256
            or self.semantic_identity != self.coverage.semantic_identity
        ):
            raise ValidationError("Repopulation evidence does not bind its coverage result")
        if self.sha256 != _sha256_primitive(self.material()):
            raise ValidationError("Repopulation evidence digest is inconsistent")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage9_repopulation_evidence",
            "contract_version": "1.0.0",
            "coverage": self.coverage.to_primitive(),
            "registry": {
                "schema_version": self.registry_schema_version,
                "revision": self.registry_revision,
                "source_sha256": self.registry_source_sha256,
            },
            "migration": {"id": self.migration_id, "sha256": self.migration_sha256},
            "collector": {
                "id": self.collector_id,
                "version": self.collector_version,
                "configuration_env_names": list(self.configuration_env_names),
            },
            "request_scope_sha256": self.request_scope_sha256,
            "response_sha256": self.response_sha256,
            "semantic_identity": self.semantic_identity,
            "source_health_sha256": self.source_health_sha256,
            "restored_health_sha256": self.restored_health_sha256,
            "source_logical_manifest_sha256": self.source_logical_manifest_sha256,
            "restored_logical_manifest_sha256": self.restored_logical_manifest_sha256,
            "source_mutation_before_sha256": self.source_mutation_before_sha256,
            "source_mutation_after_sha256": self.source_mutation_after_sha256,
            "restored_mutation_before_sha256": self.restored_mutation_before_sha256,
            "restored_mutation_after_sha256": self.restored_mutation_after_sha256,
            "source_restored_equal": self.source_restored_equal,
            "source_reads_unchanged": self.source_reads_unchanged,
            "restored_reads_unchanged": self.restored_reads_unchanged,
            "replay_unchanged": self.replay_unchanged,
            "correction_rehearsed": self.correction_rehearsed,
            "correction_evidence": dict(self.correction_evidence),
            "correction_evidence_sha256": self.correction_evidence_sha256,
            "backup_restore_outcome": self.backup_restore_outcome,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def _maps_are_disjoint(*store_maps: StoreMap) -> bool:
    seen: set[str] = set()
    for store_map in store_maps:
        identities = {
            canonical_path_uri(path) for _, path in store_map.items()
        }
        if seen.intersection(identities):
            return False
        seen.update(identities)
    return True


def build_repopulation_evidence(
    source_map: StoreMap,
    restored_map: StoreMap,
    registry: Registry,
    *,
    rehearsal_map: StoreMap,
    request_scope: Mapping[str, object],
    response_sha256: str,
    semantic_identity: str,
    replay_unchanged: bool,
) -> RepopulationEvidence:
    """Bind reconciliation, health, and restored-cohort proof without writing."""

    if not all(
        isinstance(store_map, StoreMap)
        for store_map in (source_map, restored_map, rehearsal_map)
    ):
        raise ValidationError("Stage 9 evidence requires explicit source, restored, and rehearsal store maps")
    if not _maps_are_disjoint(source_map, restored_map, rehearsal_map):
        raise ConflictError("Source, restored, and rehearsal store cohorts must be physically disjoint")
    collector, (migration_id, migration_sha256) = _validate_fmp_registry(registry)
    _scope, request_scope_sha256 = _validated_request_scope(request_scope)
    response_sha256 = _require_sha256(response_sha256, "response_sha256")
    semantic_identity = _require_sha256(semantic_identity, "semantic_identity")
    replay_unchanged = _require_bool(replay_unchanged, "replay_unchanged")

    source_before = mutation_fingerprint(source_map)
    restored_before = mutation_fingerprint(restored_map)
    source_health = all_store_health(source_map, registry)
    restored_health = all_store_health(restored_map, registry)
    source_coverage = reconcile_fmp_spy_market(source_map, registry)
    restored_coverage = reconcile_fmp_spy_market(restored_map, registry)
    source_manifest = logical_manifest(source_map, registry)
    restored_manifest = logical_manifest(restored_map, registry)
    source_after = mutation_fingerprint(source_map)
    restored_after = mutation_fingerprint(restored_map)

    if source_before["sha256"] != source_after["sha256"]:
        raise ValidationError("Stage 9 source evidence reads mutated a store")
    if restored_before["sha256"] != restored_after["sha256"]:
        raise ValidationError("Stage 9 restored evidence reads mutated a store")
    if source_coverage.to_primitive() != restored_coverage.to_primitive():
        raise ValidationError("Restored Stage 9 coverage differs from its source cohort")
    if source_health != restored_health:
        raise ValidationError("Restored Stage 9 health differs from its source cohort")
    if source_manifest["sha256"] != restored_manifest["sha256"]:
        raise ValidationError("Restored Stage 9 logical manifest differs from its source cohort")
    if (
        request_scope_sha256 != source_coverage.request_scope_sha256
        or response_sha256 != source_coverage.response_sha256
        or semantic_identity != source_coverage.semantic_identity
    ):
        raise ValidationError("Caller provenance does not match the reconciled FMP capture")

    rehearsal_before = mutation_fingerprint(rehearsal_map)
    correction_evidence = _derive_scratch_correction_evidence(
        rehearsal_map,
        registry,
        source_coverage,
    )
    rehearsal_after = mutation_fingerprint(rehearsal_map)
    if rehearsal_before["sha256"] != rehearsal_after["sha256"]:
        raise ValidationError("Stage 9 scratch proof reads mutated a store")
    correction_evidence_sha256 = _sha256_primitive(dict(correction_evidence))

    registry_source_sha256 = _require_sha256(
        registry.source_sha256,
        "registry_source_sha256",
    )
    material = {
        "coverage": source_coverage.to_primitive(),
        "registry_schema_version": registry.schema_version,
        "registry_revision": registry.revision,
        "registry_source_sha256": registry_source_sha256,
        "migration_id": migration_id,
        "migration_sha256": migration_sha256,
        "collector_id": FMP_COLLECTOR_ID,
        "collector_version": collector["version"],
        "configuration_env_names": ["FMP_API_KEY"],
        "request_scope_sha256": request_scope_sha256,
        "response_sha256": response_sha256,
        "semantic_identity": semantic_identity,
        "source_health_sha256": _sha256_primitive(source_health.to_primitive()),
        "restored_health_sha256": _sha256_primitive(restored_health.to_primitive()),
        "source_logical_manifest_sha256": source_manifest["sha256"],
        "restored_logical_manifest_sha256": restored_manifest["sha256"],
        "source_mutation_before_sha256": source_before["sha256"],
        "source_mutation_after_sha256": source_after["sha256"],
        "restored_mutation_before_sha256": restored_before["sha256"],
        "restored_mutation_after_sha256": restored_after["sha256"],
        "source_restored_equal": True,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "replay_unchanged": replay_unchanged,
        "correction_rehearsed": True,
        "correction_evidence": dict(correction_evidence),
        "correction_evidence_sha256": correction_evidence_sha256,
        "backup_restore_outcome": "validated_restored_cohort_equal",
    }
    evidence_sha256 = _sha256_primitive(
        {
            "contract": "quant_data.stage9_repopulation_evidence",
            "contract_version": "1.0.0",
            "coverage": source_coverage.to_primitive(),
            "registry": {
                "schema_version": material["registry_schema_version"],
                "revision": material["registry_revision"],
                "source_sha256": material["registry_source_sha256"],
            },
            "migration": {
                "id": material["migration_id"],
                "sha256": material["migration_sha256"],
            },
            "collector": {
                "id": material["collector_id"],
                "version": material["collector_version"],
                "configuration_env_names": material["configuration_env_names"],
            },
            **{
                name: value
                for name, value in material.items()
                if name
                not in {
                    "coverage",
                    "registry_schema_version",
                    "registry_revision",
                    "registry_source_sha256",
                    "migration_id",
                    "migration_sha256",
                    "collector_id",
                    "collector_version",
                    "configuration_env_names",
                }
            },
        }
    )
    return RepopulationEvidence(
        coverage=source_coverage,
        **{name: value for name, value in material.items() if name != "coverage"},
        sha256=evidence_sha256,
    )


@dataclass(frozen=True, slots=True)
class PromotionCandidateReceipt:
    """Private evidence that a cohort is a candidate, never an operational promotion."""

    attempt_id: str
    generated_at: str
    code_revision: str
    promotion_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _ATTEMPT_ID.fullmatch(self.attempt_id) is None:
            raise ValidationError("attempt_id must be a safe bounded identifier")
        _render_utc_datetime(self.generated_at, "generated_at")
        if not isinstance(self.code_revision, str) or _CODE_REVISION.fullmatch(self.code_revision) is None:
            raise ValidationError("code_revision must be a lowercase source revision")
        if self.promotion_state != _PROMOTION_STATE:
            raise ValidationError("Stage 9 receipt cannot claim an operational promotion")
        if not isinstance(self.evidence, Mapping):
            raise ValidationError("Promotion receipt requires path-free evidence")
        frozen_evidence = _freeze_json(self.evidence, "promotion_evidence")
        if not isinstance(frozen_evidence, Mapping):  # defensive narrowing for type checkers
            raise ValidationError("Promotion receipt requires path-free evidence")
        object.__setattr__(self, "evidence", frozen_evidence)
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        _require_sha256(self.receipt_sha256, "receipt_sha256")
        thawed_evidence = _thaw_json(frozen_evidence)
        if not isinstance(thawed_evidence, Mapping):
            raise ValidationError("Promotion receipt requires path-free evidence")
        if thawed_evidence.get("sha256") != self.evidence_sha256:
            raise ValidationError("Promotion receipt evidence digest is invalid")
        evidence_material = {
            name: value for name, value in thawed_evidence.items() if name != "sha256"
        }
        if _sha256_primitive(evidence_material) != self.evidence_sha256:
            raise ValidationError("Promotion receipt evidence digest is invalid")
        if self.expected_receipt_sha256() != self.receipt_sha256:
            raise ValidationError("Promotion receipt digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage9_promotion_candidate_receipt",
            "contract_version": "1.0.0",
            "attempt_id": self.attempt_id,
            "generated_at": self.generated_at,
            "code_revision": self.code_revision,
            "promotion_state": self.promotion_state,
            "evidence": _thaw_json(self.evidence),
            "evidence_sha256": self.evidence_sha256,
        }

    def expected_receipt_sha256(self) -> str:
        return _sha256_primitive(self.material())

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "receipt_sha256": self.receipt_sha256}


class PrivatePromotionCandidateState:
    """Explicit private root for receipt-last, immutable candidate evidence."""

    def __init__(self, root: str | Path) -> None:
        if isinstance(root, str) and not root.strip():
            raise ValidationError("promotion receipt root must be an explicit absolute directory")
        if not isinstance(root, (str, Path)):
            raise ValidationError("promotion receipt root must be an explicit absolute directory")
        try:
            candidate = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValidationError("promotion receipt root must be an explicit absolute directory") from exc
        if not candidate.is_absolute() or candidate == Path(candidate.anchor) or candidate == Path.home().resolve(strict=False):
            raise ValidationError("promotion receipt root is too broad")
        if candidate.exists() and (not candidate.is_dir() or candidate.is_symlink()):
            raise ValidationError("promotion receipt root is unavailable")
        self._root = candidate

    @property
    def root(self) -> Path:
        """Return the caller-owned private root without creating it."""

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

    def _directory(self) -> Path:
        root = self._root
        directory = root / "promotion-candidates"
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not root.is_dir() or root.is_symlink():
                raise OSError("root is not a private directory")
            os.chmod(root, 0o700)
            directory.mkdir(mode=0o700, exist_ok=True)
            if not directory.is_dir() or directory.is_symlink():
                raise OSError("receipt directory is unavailable")
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise OSError("Private promotion receipt directory publication failed") from exc
        return directory

    def publish(self, receipt: PromotionCandidateReceipt) -> None:
        if not isinstance(receipt, PromotionCandidateReceipt):
            raise ValidationError("publish requires a promotion-candidate receipt")
        payload = (dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RECEIPT_BYTES) + "\n").encode("utf-8")
        if not payload or len(payload) > _MAX_RECEIPT_BYTES:
            raise ResourceLimitError("Promotion-candidate receipt exceeds its bounded size")
        directory = self._directory()
        final_path = directory / f"{receipt.attempt_id}.json"
        if final_path.exists() or final_path.is_symlink():
            raise ConflictError("Private promotion-candidate receipt already exists")
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
            # link is atomic no-replace publication; no operational store path is touched.
            os.link(temporary, final_path)
            linked = True
            os.chmod(final_path, 0o600)
            self._fsync_directory(directory)
            os.unlink(temporary)
            self._fsync_directory(directory)
        except FileExistsError as exc:
            raise ConflictError("Private promotion-candidate receipt already exists") from exc
        except OSError as exc:
            raise OSError("Private promotion-candidate receipt publication failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists() or temporary.is_symlink():
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            # A linked final receipt is immutable evidence even if a later local
            # cleanup call failed.  Never remove or replace it.
            if linked and not final_path.is_file():
                raise OSError("Private promotion-candidate receipt disappeared")


def publish_promotion_candidate(
    state: PrivatePromotionCandidateState,
    evidence: RepopulationEvidence,
    *,
    code_revision: str,
    now: Callable[[], object] | object,
    attempt_id: str,
) -> PromotionCandidateReceipt:
    """Publish only a validated candidate receipt; never promote a store."""

    if not isinstance(state, PrivatePromotionCandidateState):
        raise ValidationError("Promotion candidate requires explicit private state")
    if not isinstance(evidence, RepopulationEvidence):
        raise ValidationError("Promotion candidate requires repopulation evidence")
    if not (
        evidence.source_restored_equal
        and evidence.source_reads_unchanged
        and evidence.restored_reads_unchanged
        and evidence.replay_unchanged
        and evidence.correction_rehearsed
        and evidence.backup_restore_outcome == "validated_restored_cohort_equal"
    ):
        raise ValidationError("Promotion candidate evidence has not passed its safety gates")
    if not isinstance(code_revision, str) or _CODE_REVISION.fullmatch(code_revision) is None:
        raise ValidationError("code_revision must be a lowercase source revision")
    if not isinstance(attempt_id, str) or _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValidationError("attempt_id must be a safe bounded identifier")
    generated_at = _render_utc_datetime(now, "generated_at")
    evidence_sha256 = evidence.sha256
    if evidence_sha256 != _sha256_primitive(evidence.material()):
        raise ValidationError("Repopulation evidence digest is inconsistent")
    evidence_primitive = evidence.to_primitive()
    material = {
        "contract": "quant_data.stage9_promotion_candidate_receipt",
        "contract_version": "1.0.0",
        "attempt_id": attempt_id,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "promotion_state": _PROMOTION_STATE,
        "evidence": evidence_primitive,
        "evidence_sha256": evidence_sha256,
    }
    receipt = PromotionCandidateReceipt(
        attempt_id=attempt_id,
        generated_at=generated_at,
        code_revision=code_revision,
        promotion_state=_PROMOTION_STATE,
        evidence=evidence_primitive,
        evidence_sha256=evidence_sha256,
        receipt_sha256=_sha256_primitive(material),
    )
    state.publish(receipt)
    return receipt


__all__ = (
    "CoverageReconciliation",
    "FMP_EXPECTED_DATES",
    "FMP_PRICE_VARIANT",
    "PrivatePromotionCandidateState",
    "PromotionCandidateReceipt",
    "RepopulationEvidence",
    "build_repopulation_evidence",
    "publish_promotion_candidate",
    "reconcile_fmp_spy_market",
)
