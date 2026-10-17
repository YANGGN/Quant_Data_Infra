"""Window-aware reconciliation and private evidence for Stage 10 history extension.

The completed Stage 10 candidate remains immutable. This transport-free module
binds that base proof to the exact 629-symbol/eight-window successor ledger,
reparses retained response bytes, validates canonical lineage, and emits only
private candidate evidence.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from ..errors import ConflictError, RegistryError, ResourceLimitError, ValidationError
from ..fingerprint import logical_manifest, mutation_fingerprint
from ..json_codec import dumps_strict, loads_strict
from ..market.fmp_bulk_daily_prices import (
    FMP_STAGE10_MAX_PRICE_BYTES,
    FmpStage10PriceRow,
    parse_fmp_stage10_price_response,
)
from ..market.stage10_history_importer import (
    STAGE10_CURRENCY_SEGMENT,
    STAGE10_DAILY_HISTORY_COLLECTOR_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_SHA256,
    STAGE10_PRICE_NORMALIZATION_VERSION,
    STAGE10_PRICE_VARIANT,
)
from ..market.stage10_history_windows import (
    STAGE10_HISTORY_WINDOWS,
    parse_fmp_stage10_window_response,
    prepare_fmp_stage10_window_capture,
)
from ..market.stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    Stage10MarketScope,
)
from ..registry import Registry, stage10_registry_profile, stage11_registry_profile
from ..stores import StoreMap, StoreRole, read_connection
from .health import all_store_health
from .stage10_repopulation import Stage10MarketReconciliation
from .stage10_history_extension_transition_402 import (
    CONTENT_TYPE as TRANSITION_402_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_402_HTTP_STATUS,
    RESPONSE_BYTE_COUNT as TRANSITION_402_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_402_RESPONSE_SHA256,
    TRANSITION_TERMINAL_REASON as TRANSITION_402_TERMINAL_REASON,
)
from .stage10_history_extension_transition_empty import (
    CONTENT_TYPE as TRANSITION_EMPTY_CONTENT_TYPE,
    HTTP_STATUS as TRANSITION_EMPTY_HTTP_STATUS,
    RESPONSE_BYTE_COUNT as TRANSITION_EMPTY_RESPONSE_BYTE_COUNT,
    RESPONSE_SHA256 as TRANSITION_EMPTY_RESPONSE_SHA256,
    TRANSITION_TERMINAL_REASON as TRANSITION_EMPTY_TERMINAL_REASON,
)

from .stage10_history_extension_transition_empty_chain import (
    validate_stage10_history_extension_authorization_proof,
)

_CONTRACT_VERSION = "1.4.0"
_OPERATOR_TERMINAL_REASONS = frozenset({
    TRANSITION_402_TERMINAL_REASON, TRANSITION_EMPTY_TERMINAL_REASON,
})
_EXPECTED_ROSTER_COUNT = 629
_EXPECTED_LEDGER_COUNT = _EXPECTED_ROSTER_COUNT * len(STAGE10_HISTORY_WINDOWS)
_HISTORY_POLICY = "explicit_inclusive_five_year_windows_from_1990"
_MAX_SIDECAR_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_RECEIPT_BYTES = 4 * 1024 * 1024
_CANDIDATE_STATE = "private_candidate_only_no_operational_promotion"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Za-z0-9.^-]{1,32}\Z")
_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_REVISION = re.compile(r"[0-9a-f]{40,64}\Z")
_TERMINAL_STATUS = {
    "operator_authorized_entitlement_unavailable": 402,
    "operator_authorized_known_listed_empty": 200,
    "provider_not_found": 404,
    "provider_unsupported": 410,
    "provider_application_error": 422,
}
_COMMON_KEYS = frozenset({
    "ordinal", "symbol", "window_id", "from", "to", "query", "disposition",
    "base_completion_sha256", "base_reconciliation_sha256", "intent_sha256",
    "result_sha256",
})
_COMPLETE_KEYS = _COMMON_KEYS | {
    "response_sha256", "response_byte_count", "row_count",
    "publication_receipt_sha256", "semantic_identity",
}
_EMPTY_KEYS = _COMMON_KEYS | {"response_sha256", "response_byte_count", "row_count"}
_FAILURE_KEYS = _EMPTY_KEYS | {"terminal_reason", "http_status", "content_type"}


def _sha_json(value: object) -> str:
    return hashlib.sha256(dumps_strict(value).encode("utf-8")).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 history-extension {label} is invalid")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Stage 10 history-extension {label} is invalid")
    return value


def _freeze(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    raise ValidationError("Stage 10 history-extension evidence is not strict JSON")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _authorization_proof_for_ledger(
    ledger: Sequence[Mapping[str, object]],
    value: object,
) -> Mapping[str, object] | None:
    required = any(
        record.get("disposition") == "terminal_failure"
        and record.get("terminal_reason") in _OPERATOR_TERMINAL_REASONS
        for record in ledger
    )
    try:
        validated = validate_stage10_history_extension_authorization_proof(
            value,
            required=required,
        )
    except ValueError as exc:
        raise ValidationError(
            "Stage 10 history-extension authorization proof is invalid"
        ) from exc
    if validated is None:
        return None
    frozen = _freeze(validated)
    if not isinstance(frozen, Mapping):
        raise ValidationError(
            "Stage 10 history-extension authorization proof is invalid"
        )
    return frozen


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("Stage 10 history-extension price is not finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _row_values(row: FmpStage10PriceRow) -> tuple[str, str, str, str, int]:
    return (
        _decimal_text(row.open_value), _decimal_text(row.high_value),
        _decimal_text(row.low_value), _decimal_text(row.close_value), row.volume,
    )


def _validate_registry_scope(
    registry: object, scope: object, base: Stage10MarketReconciliation,
) -> tuple[Registry, Stage10MarketScope]:
    if not isinstance(registry, Registry) or not isinstance(scope, Stage10MarketScope):
        raise ValidationError("Stage 10 history-extension requires reviewed declarations")
    if (
        registry.schema_version != "1.6.0" or registry.registry_version != "2.8.0"
        or registry.status != "validated"
        or registry.source_sha256 != base.registry_source_sha256
        or scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or base.scope_manifest_sha256 != scope.manifest_sha256
        or base.target_profile_id != scope.target_profile_id
        or base.registry_schema_version != registry.schema_version
        or base.registry_version != registry.registry_version
        or base.migration_id != STAGE10_MIGRATION_ID
        or base.migration_sha256 != STAGE10_MIGRATION_SHA256
        or not base.stores_unchanged
    ):
        raise ValidationError("Stage 10 history-extension declaration binding is invalid")
    return registry, scope


def _validate_cohort_registry(
    cohort_registry: object,
    stage10_registry: Registry,
) -> Registry:
    """Bind four-store evidence to the current Stage 11-extended cohort."""

    if not isinstance(cohort_registry, Registry):
        raise ValidationError(
            "Stage 10 history-extension cohort registry is invalid"
        )
    try:
        cohort_registry = stage11_registry_profile(cohort_registry)
        projected = stage10_registry_profile(cohort_registry)
    except (RegistryError, ValidationError, ValueError, TypeError) as exc:
        raise ValidationError(
            "Stage 10 history-extension cohort registry projection is invalid"
        ) from exc
    if cohort_registry.status != "validated":
        raise ValidationError(
            "Stage 10 history-extension cohort registry is invalid"
        )
    if projected.source_sha256 != stage10_registry.source_sha256:
        raise ValidationError(
            "Stage 10 history-extension cohort registry does not extend the "
            "reviewed Stage 10 profile"
        )
    return cohort_registry


def _base_roster(base: Stage10MarketReconciliation) -> tuple[str, ...]:
    symbols: list[str] = []
    for item in base.price_coverage:
        symbol = item.get("provider_symbol") if isinstance(item, Mapping) else None
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise ValidationError("Stage 10 base reconciliation roster is invalid")
        symbols.append(symbol)
    symbols.extend(base.failed_tickers)
    roster = tuple(sorted(set(symbols)))
    if (
        base.roster_count != _EXPECTED_ROSTER_COUNT
        or len(symbols) != _EXPECTED_ROSTER_COUNT
        or len(roster) != _EXPECTED_ROSTER_COUNT
        or "AAPL" not in roster or {"^RUT", "IWM"}.intersection(roster)
    ):
        raise ValidationError("Stage 10 base reconciliation is not the frozen 629-symbol cohort")
    return roster


def _expected_schedule(roster: tuple[str, ...]) -> tuple[tuple[str, object], ...]:
    ordered = ("AAPL",) + tuple(symbol for symbol in roster if symbol != "AAPL")
    return tuple((symbol, window) for window in STAGE10_HISTORY_WINDOWS for symbol in ordered)


def _normalize_ledger(
    records: object, *, roster: tuple[str, ...], base_reconciliation_sha256: str,
    base_completion_sha256: str, require_complete: bool = True,
) -> tuple[Mapping[str, object], ...]:
    _digest(base_completion_sha256, "base completion digest")
    if (
        not isinstance(records, (tuple, list))
        or len(records) > _EXPECTED_LEDGER_COUNT
        or (require_complete and len(records) != _EXPECTED_LEDGER_COUNT)
    ):
        raise ValidationError("Stage 10 history-extension ledger cardinality is invalid")
    normalized: list[Mapping[str, object]] = []
    intents: set[str] = set()
    for ordinal, ((symbol, window), raw) in enumerate(zip(_expected_schedule(roster), records), 1):
        if not isinstance(raw, Mapping):
            raise ValidationError("Stage 10 history-extension ledger record is invalid")
        disposition = raw.get("disposition")
        expected_keys = (
            _COMPLETE_KEYS if disposition == "complete" else
            _EMPTY_KEYS if disposition == "complete_empty" else
            _FAILURE_KEYS if disposition == "terminal_failure" else frozenset()
        )
        if frozenset(raw) != expected_keys:
            raise ValidationError("Stage 10 history-extension ledger record shape is invalid")
        if (
            raw.get("ordinal") != ordinal or raw.get("symbol") != symbol
            or raw.get("window_id") != window.window_id
            or raw.get("from") != window.start_date or raw.get("to") != window.end_date
            or raw.get("query") != {"symbol": symbol, "from": window.start_date, "to": window.end_date}
            or raw.get("base_completion_sha256") != base_completion_sha256
            or raw.get("base_reconciliation_sha256") != base_reconciliation_sha256
        ):
            raise ValidationError("Stage 10 history-extension ledger schedule is invalid")
        intent = _digest(raw.get("intent_sha256"), "intent digest")
        _digest(raw.get("result_sha256"), "result digest")
        response_sha256 = _digest(raw.get("response_sha256"), "response digest")
        if intent in intents:
            raise ValidationError("Stage 10 history-extension intent digest is duplicated")
        intents.add(intent)
        byte_count = _integer(raw.get("response_byte_count"), "response byte count")
        if byte_count > FMP_STAGE10_MAX_PRICE_BYTES:
            raise ResourceLimitError("Stage 10 history-extension response exceeds its byte bound")
        row_count = _integer(raw.get("row_count"), "row count")
        if disposition == "complete":
            if row_count < 1:
                raise ValidationError("Stage 10 complete window has no rows")
            _digest(raw.get("publication_receipt_sha256"), "publication receipt digest")
            _digest(raw.get("semantic_identity"), "semantic identity")
        elif disposition == "complete_empty":
            if row_count != 0:
                raise ValidationError("Stage 10 empty window row count is invalid")
        else:
            reason = raw.get("terminal_reason")
            if (
                reason not in _TERMINAL_STATUS or raw.get("http_status") != _TERMINAL_STATUS[reason]
                or raw.get("content_type") != "application/json" or row_count != 0
            ):
                raise ValidationError("Stage 10 terminal window outcome is invalid")
            if reason == TRANSITION_402_TERMINAL_REASON and (
                raw.get("http_status") != TRANSITION_402_HTTP_STATUS
                or raw.get("content_type") != TRANSITION_402_CONTENT_TYPE
                or response_sha256 != TRANSITION_402_RESPONSE_SHA256
                or byte_count != TRANSITION_402_RESPONSE_BYTE_COUNT
            ):
                raise ValidationError(
                    "Stage 10 entitlement-unavailable response fingerprint is invalid"
                )
            if reason == TRANSITION_EMPTY_TERMINAL_REASON and (
                raw.get("http_status") != TRANSITION_EMPTY_HTTP_STATUS
                or raw.get("content_type") != TRANSITION_EMPTY_CONTENT_TYPE
                or response_sha256 != TRANSITION_EMPTY_RESPONSE_SHA256
                or byte_count != TRANSITION_EMPTY_RESPONSE_BYTE_COUNT
            ):
                raise ValidationError(
                    "Stage 10 known-listed empty response fingerprint is invalid"
                )
        normalized.append(MappingProxyType({str(k): _freeze(v) for k, v in raw.items()}))
    if normalized and normalized[0].get("disposition") != "complete":
        raise ValidationError("Stage 10 AAPL historical entitlement sentinel did not pass")
    return tuple(normalized)


def normalize_stage10_history_extension_ledger(
    ledger_records: object, *, base_reconciliation: Stage10MarketReconciliation,
    base_completion_sha256: str,
    authorization_proof: object = None,
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(base_reconciliation, Stage10MarketReconciliation):
        raise ValidationError("Stage 10 history-extension requires an actual base reconciliation")
    ledger = _normalize_ledger(
        ledger_records, roster=_base_roster(base_reconciliation),
        base_reconciliation_sha256=base_reconciliation.sha256,
        base_completion_sha256=base_completion_sha256,
    )
    _authorization_proof_for_ledger(ledger, authorization_proof)
    return ledger


@dataclass(frozen=True, slots=True)
class Stage10HistoryExtensionReconciliation:
    base_completion_sha256: str
    base_reconciliation_sha256: str
    authorization_proof: Mapping[str, object] | None
    scope_manifest_sha256: str
    target_profile_id: str
    registry_source_sha256: str
    roster_count: int
    planned_window_count: int
    ledger_sha256: str
    raw_sidecar_manifest_sha256: str
    complete_window_count: int
    empty_window_count: int
    terminal_failure_count: int
    failed_tickers: tuple[str, ...]
    failed_windows: tuple[Mapping[str, object], ...]
    extension_capture_count: int
    current_price_count: int
    immutable_version_count: int
    price_coverage: tuple[Mapping[str, object], ...]
    raw_sidecars_reparsed: bool
    canonical_rows_match_raw: bool
    correction_lineage_verified: bool
    stores_unchanged: bool
    sha256: str

    def __post_init__(self) -> None:
        required = any(
            item.get("terminal_reason") in _OPERATOR_TERMINAL_REASONS
            for item in self.failed_windows
            if isinstance(item, Mapping)
        )
        try:
            chain = validate_stage10_history_extension_authorization_proof(
                _thaw(self.authorization_proof), required=required,
            )
        except ValueError as exc:
            raise ValidationError(
                "Stage 10 history-extension reconciliation authorization is invalid"
            ) from exc
        object.__setattr__(self, "authorization_proof", _freeze(chain))

        for name in (
            "base_completion_sha256", "base_reconciliation_sha256", "scope_manifest_sha256",
            "registry_source_sha256", "ledger_sha256", "raw_sidecar_manifest_sha256", "sha256",
        ):
            _digest(getattr(self, name), name)
        if (
            self.target_profile_id != STAGE10_TARGET_PROFILE_ID
            or self.scope_manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
            or self.roster_count != _EXPECTED_ROSTER_COUNT
            or self.planned_window_count != _EXPECTED_LEDGER_COUNT
            or self.complete_window_count + self.empty_window_count + self.terminal_failure_count
            != self.planned_window_count
            or self.extension_capture_count != self.complete_window_count
            or tuple(sorted(set(self.failed_tickers))) != self.failed_tickers
            or len(self.failed_windows) != self.terminal_failure_count
            or len(self.price_coverage) != self.roster_count
            or not all((self.raw_sidecars_reparsed, self.canonical_rows_match_raw,
                        self.correction_lineage_verified, self.stores_unchanged))
        ):
            raise ValidationError("Stage 10 history-extension reconciliation is invalid")
        object.__setattr__(self, "failed_windows", tuple(_freeze(v) for v in self.failed_windows))
        object.__setattr__(self, "price_coverage", tuple(_freeze(v) for v in self.price_coverage))
        if self.sha256 != _sha_json(self.material()):
            raise ValidationError("Stage 10 history-extension reconciliation digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_history_extension_reconciliation",
            "contract_version": _CONTRACT_VERSION,
            "base_completion_sha256": self.base_completion_sha256,
            "base_reconciliation_sha256": self.base_reconciliation_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "authorization_proof": _thaw(self.authorization_proof),
            "target_profile_id": self.target_profile_id,
            "registry_source_sha256": self.registry_source_sha256,
            "roster_count": self.roster_count,
            "planned_window_count": self.planned_window_count,
            "ledger_sha256": self.ledger_sha256,
            "raw_sidecar_manifest_sha256": self.raw_sidecar_manifest_sha256,
            "complete_window_count": self.complete_window_count,
            "empty_window_count": self.empty_window_count,
            "terminal_failure_count": self.terminal_failure_count,
            "failed_tickers": list(self.failed_tickers),
            "failed_windows": [_thaw(v) for v in self.failed_windows],
            "extension_capture_count": self.extension_capture_count,
            "current_price_count": self.current_price_count,
            "immutable_version_count": self.immutable_version_count,
            "price_coverage": [_thaw(v) for v in self.price_coverage],
            "raw_sidecars_reparsed": self.raw_sidecars_reparsed,
            "canonical_rows_match_raw": self.canonical_rows_match_raw,
            "correction_lineage_verified": self.correction_lineage_verified,
            "stores_unchanged": self.stores_unchanged,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def _extension_scope(scope: Stage10MarketScope, record: Mapping[str, object]) -> dict[str, object]:
    return {
        "provider": "fmp",
        "endpoint_path": scope.price_history.endpoint_path,
        "symbol": record["symbol"],
        "window_id": record["window_id"],
        "start_date": record["from"],
        "end_date": record["to"],
        "from": record["from"],
        "to": record["to"],
        "interval": scope.price_history.interval,
        "history_policy": _HISTORY_POLICY,
        "price_variant": scope.price_history.price_variant,
        "currency_policy": scope.price_history.currency_policy,
        "volume_policy": scope.price_history.volume_policy,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }


def _request_scope(value: object) -> Mapping[str, object]:
    if not isinstance(value, str):
        raise ValidationError("Stage 10 capture request scope is invalid")
    parsed = loads_strict(value, max_bytes=64 * 1024)
    if not isinstance(parsed, Mapping):
        raise ValidationError("Stage 10 capture request scope is invalid")
    return parsed


def _sidecar_bytes(
    sidecars: Mapping[str, bytes], intent: str, record: Mapping[str, object],
) -> bytes:
    try:
        raw = sidecars[intent]
    except (KeyError, OSError) as exc:
        raise ConflictError("Stage 10 history-extension sidecar is missing") from exc
    if not isinstance(raw, bytes):
        raise ValidationError("Stage 10 history-extension sidecar must yield bytes")
    if len(raw) > FMP_STAGE10_MAX_PRICE_BYTES:
        raise ResourceLimitError("Stage 10 history-extension sidecar exceeds its byte bound")
    if (
        len(raw) != record["response_byte_count"]
        or hashlib.sha256(raw).hexdigest() != record["response_sha256"]
    ):
        raise ConflictError("Stage 10 history-extension sidecar digest is inconsistent")
    return raw


def _parse_capture_rows(
    symbol: str, raw: bytes, scope_value: Mapping[str, object],
) -> tuple[FmpStage10PriceRow, ...]:
    if scope_value.get("history_policy") == _HISTORY_POLICY:
        window = next((
            item for item in STAGE10_HISTORY_WINDOWS
            if item.window_id == scope_value.get("window_id")
            and item.start_date == scope_value.get("from")
            and item.end_date == scope_value.get("to")
        ), None)
        if window is None:
            raise ValidationError("Stage 10 extension capture window is invalid")
        return parse_fmp_stage10_window_response(
            prepare_fmp_stage10_window_capture(symbol, window), raw,
        )
    return parse_fmp_stage10_price_response(symbol=symbol, body=raw).rows


def _reconcile_connection(
    connection: sqlite3.Connection, *, scope: Stage10MarketScope,
    roster: tuple[str, ...], ledger: tuple[Mapping[str, object], ...],
    sidecars: Mapping[str, bytes],
) -> dict[str, object]:
    instruments = connection.execute(
        "SELECT instrument_id, provider_symbol, first_trade_date "
        "FROM stage10_instruments "
        "WHERE provider='fmp' ORDER BY provider_symbol"
    ).fetchall()
    instrument_by_symbol = {
        str(row["provider_symbol"]): str(row["instrument_id"]) for row in instruments
    }
    first_trade_by_symbol = {
        str(row["provider_symbol"]): (
            None
            if row["first_trade_date"] is None
            else str(row["first_trade_date"])
        )
        for row in instruments
    }
    if tuple(sorted(instrument_by_symbol)) != roster:
        raise ValidationError("Stage 10 extension store roster differs from its base proof")

    ledger_by_semantic = {
        str(record["semantic_identity"]): record
        for record in ledger if record["disposition"] == "complete"
    }
    complete_keys = {
        (str(record["symbol"]), str(record["window_id"]))
        for record in ledger if record["disposition"] == "complete"
    }
    capture_meta: dict[str, dict[str, object]] = {}
    extension_seen: set[tuple[str, str]] = set()
    capture_query = """
        SELECT capture_id, instrument_id, provider_symbol, endpoint_path,
               scope_manifest_sha256, request_scope_json, request_scope_sha256,
               response_sha256, length(response_bytes) AS response_byte_count,
               semantic_identity, artifact_id, snapshot_id, captured_at,
               earliest_trade_date, latest_trade_date, row_count,
               normalization_version, run_id
        FROM stage10_daily_price_captures
        ORDER BY provider_symbol, captured_at, capture_id
    """
    for row in connection.execute(capture_query):
        request_scope = _request_scope(row["request_scope_json"])
        meta = {str(key): row[key] for key in row.keys()}
        meta["request_scope"] = request_scope
        capture_meta[str(row["capture_id"])] = meta
        if request_scope.get("history_policy") != _HISTORY_POLICY:
            continue
        record = ledger_by_semantic.get(str(row["semantic_identity"]))
        if record is None:
            raise ValidationError("Stage 10 store has an unplanned extension capture")
        key = (str(record["symbol"]), str(record["window_id"]))
        if key in extension_seen:
            raise ValidationError("Stage 10 store has duplicate extension captures")
        extension_seen.add(key)
        expected_scope = _extension_scope(scope, record)
        if (
            request_scope != expected_scope
            or row["provider_symbol"] != record["symbol"]
            or row["instrument_id"] != instrument_by_symbol[record["symbol"]]
            or row["endpoint_path"] != scope.price_history.endpoint_path
            or row["scope_manifest_sha256"] != scope.manifest_sha256
            or row["request_scope_sha256"] != _sha_json(expected_scope)
            or row["response_sha256"] != record["response_sha256"]
            or row["response_byte_count"] != record["response_byte_count"]
            or row["row_count"] != record["row_count"]
            or row["normalization_version"] != STAGE10_PRICE_NORMALIZATION_VERSION
        ):
            raise ValidationError("Stage 10 extension capture metadata is inconsistent")
    if extension_seen != complete_keys:
        raise ValidationError("Stage 10 extension capture partition is incomplete")

    try:
        sidecar_keys = set(sidecars.keys())
    except (OSError, TypeError) as exc:
        raise ConflictError("Stage 10 history-extension sidecar manifest is unreadable") from exc
    expected_intents = {str(record["intent_sha256"]) for record in ledger}
    if sidecar_keys != expected_intents:
        raise ConflictError("Stage 10 history-extension sidecar manifest is incomplete")

    complete_rows: dict[tuple[str, str], tuple[FmpStage10PriceRow, ...]] = {}
    total_bytes = 0
    sidecar_material: list[dict[str, object]] = []
    for record in ledger:
        intent = str(record["intent_sha256"])
        raw = _sidecar_bytes(sidecars, intent, record)
        total_bytes += len(raw)
        if total_bytes > _MAX_SIDECAR_TOTAL_BYTES:
            raise ResourceLimitError("Stage 10 history-extension sidecars exceed the shared bound")
        sidecar_material.append({
            "intent_sha256": intent,
            "response_sha256": record["response_sha256"],
            "response_byte_count": record["response_byte_count"],
        })
        window = next(item for item in STAGE10_HISTORY_WINDOWS if item.window_id == record["window_id"])
        if record["disposition"] == "complete":
            parsed = parse_fmp_stage10_window_response(
                prepare_fmp_stage10_window_capture(str(record["symbol"]), window), raw,
            )
            if len(parsed) != record["row_count"]:
                raise ValidationError("Stage 10 extension sidecar row count is inconsistent")
            capture_semantic_sha256 = _sha_json({
                "provider": "fmp",
                "endpoint_path": scope.price_history.endpoint_path,
                "query": _thaw(record["query"]),
                "disposition": "complete",
                "normalized_complete_batch": [row.semantic_mapping() for row in parsed],
            })
            expected_semantic_identity = _sha_json({
                "collector_id": STAGE10_DAILY_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "request_scope": _extension_scope(scope, record),
                "normalized_window_history_sha256": capture_semantic_sha256,
            })
            if record["semantic_identity"] != expected_semantic_identity:
                raise ValidationError(
                    "Stage 10 extension semantic identity is inconsistent"
                )
            retained = connection.execute(
                """
                SELECT capture.response_bytes, capture.run_id,
                       capture.artifact_id, capture.snapshot_id,
                       run.dataset_id, run.semantic_identity AS run_semantic_identity,
                       run.status, run.artifact_id AS run_artifact_id,
                       run.snapshot_id AS run_snapshot_id,
                       run.written_count, run.warnings_json
                FROM stage10_daily_price_captures AS capture
                JOIN ingestion_runs AS run ON run.run_id=capture.run_id
                WHERE capture.semantic_identity=?
                """,
                (record["semantic_identity"],),
            ).fetchone()
            if retained is None or bytes(retained["response_bytes"]) != raw:
                raise ValidationError("Stage 10 extension raw evidence differs from its sidecar")
            try:
                warnings = loads_strict(str(retained["warnings_json"]), max_bytes=64 * 1024)
            except (ResourceLimitError, ValidationError) as exc:
                raise ValidationError(
                    "Stage 10 extension publication receipt is invalid"
                ) from exc
            if (
                warnings != []
                or retained["dataset_id"] != STAGE10_DAILY_PRICES_DATASET_ID
                or retained["run_semantic_identity"] != expected_semantic_identity
                or retained["status"] != "succeeded"
                or retained["run_artifact_id"] != retained["artifact_id"]
                or retained["run_snapshot_id"] != retained["snapshot_id"]
            ):
                raise ValidationError(
                    "Stage 10 extension publication receipt is invalid"
                )
            publication_receipt = {
                "outcome": "succeeded",
                "store": "market",
                "dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "semantic_identity": expected_semantic_identity,
                "run_id": retained["run_id"],
                "artifact_id": retained["artifact_id"],
                "snapshot_id": retained["snapshot_id"],
                "written_count": retained["written_count"],
                "warnings": [],
            }
            if _sha_json(publication_receipt) != record["publication_receipt_sha256"]:
                raise ValidationError(
                    "Stage 10 extension publication receipt digest is inconsistent"
                )
            complete_rows[(str(record["symbol"]), str(record["window_id"]))] = parsed
        elif record["disposition"] == "complete_empty":
            parsed = parse_fmp_stage10_window_response(
                prepare_fmp_stage10_window_capture(str(record["symbol"]), window), raw,
            )
            if parsed:
                raise ValidationError("Stage 10 empty-window sidecar is not empty")
        elif record["terminal_reason"] == TRANSITION_EMPTY_TERMINAL_REASON:
            parsed = parse_fmp_stage10_window_response(
                prepare_fmp_stage10_window_capture(str(record["symbol"]), window), raw,
            )
            if parsed:
                raise ValidationError(
                    "Stage 10 known-listed empty sidecar is not empty"
                )

    ledger_by_key = {
        (str(record["symbol"]), str(record["window_id"])): record for record in ledger
    }
    current_count = 0
    version_count = 0
    coverage: list[dict[str, object]] = []
    for symbol in roster:
        instrument_id = instrument_by_symbol[symbol]
        parsed_by_capture: dict[str, dict[int, FmpStage10PriceRow]] = {}
        rows = connection.execute(
            """
            SELECT capture_id, response_bytes, response_sha256, request_scope_json, row_count
            FROM stage10_daily_price_captures WHERE instrument_id=?
            ORDER BY captured_at, capture_id
            """, (instrument_id,),
        )
        for capture in rows:
            raw = bytes(capture["response_bytes"])
            if hashlib.sha256(raw).hexdigest() != capture["response_sha256"]:
                raise ValidationError("Stage 10 retained response digest is invalid")
            parsed = _parse_capture_rows(symbol, raw, _request_scope(capture["request_scope_json"]))
            if len(parsed) != capture["row_count"]:
                raise ValidationError("Stage 10 retained capture row count is invalid")
            parsed_by_capture[str(capture["capture_id"])] = {
                row.source_row: row for row in parsed
            }

        versions_by_date: dict[str, list[sqlite3.Row]] = {}
        version_rows = connection.execute(
            """
            SELECT version_id, trade_date, open_value, high_value, low_value,
                   close_value, volume, available_at, captured_at,
                   correction_sequence, supersedes_version_id, capture_id,
                   artifact_id, snapshot_id, run_id, source_row
            FROM stage10_daily_price_versions
            WHERE instrument_id=? AND provider='fmp' AND price_variant=? AND currency_segment=?
            ORDER BY trade_date, correction_sequence
            """, (instrument_id, STAGE10_PRICE_VARIANT, STAGE10_CURRENCY_SEGMENT),
        )
        for version in version_rows:
            capture_id = str(version["capture_id"])
            source = parsed_by_capture.get(capture_id, {}).get(int(version["source_row"]))
            meta = capture_meta.get(capture_id)
            if (
                source is None or meta is None or source.trade_date != version["trade_date"]
                or _row_values(source) != (
                    version["open_value"], version["high_value"], version["low_value"],
                    version["close_value"], version["volume"],
                )
                or version["artifact_id"] != meta["artifact_id"]
                or version["snapshot_id"] != meta["snapshot_id"]
                or version["run_id"] != meta["run_id"]
                or version["available_at"] != meta["captured_at"]
                or version["captured_at"] != meta["captured_at"]
            ):
                raise ValidationError("Stage 10 canonical version differs from retained raw evidence")
            versions_by_date.setdefault(str(version["trade_date"]), []).append(version)
            version_count += 1

        current = {
            str(row["trade_date"]): str(row["current_version_id"])
            for row in connection.execute(
                """
                SELECT trade_date, current_version_id FROM stage10_daily_prices
                WHERE instrument_id=? AND provider='fmp' AND price_variant=? AND currency_segment=?
                """, (instrument_id, STAGE10_PRICE_VARIANT, STAGE10_CURRENCY_SEGMENT),
            )
        }
        if set(current) != set(versions_by_date):
            raise ValidationError("Stage 10 current-date projection is incomplete")
        for trade_date, chain in versions_by_date.items():
            previous: str | None = None
            for sequence, version in enumerate(chain, 1):
                if version["correction_sequence"] != sequence or version["supersedes_version_id"] != previous:
                    raise ValidationError("Stage 10 correction lineage is invalid")
                previous = str(version["version_id"])
            if current[trade_date] != previous:
                raise ValidationError("Stage 10 current pointer is not the latest correction")

        for window in STAGE10_HISTORY_WINDOWS:
            record = ledger_by_key.get((symbol, window.window_id))
            if record is None:
                continue
            observed = {
                trade_date
                for trade_date in current
                if window.start_date <= trade_date <= window.end_date
            }
            known_on_or_before_end = any(
                trade_date <= window.end_date for trade_date in current
            ) or (
                first_trade_by_symbol[symbol] is not None
                and first_trade_by_symbol[symbol] <= window.end_date
            )
            final_window_without_any_boundary = (
                window == STAGE10_HISTORY_WINDOWS[-1]
                and not current
                and first_trade_by_symbol[symbol] is None
            )
            if record["disposition"] == "complete":
                parsed = complete_rows[(symbol, window.window_id)]
                if observed != {row.trade_date for row in parsed}:
                    raise ValidationError("Stage 10 complete window differs from current canonical dates")
                for row in parsed:
                    selected = versions_by_date[row.trade_date][-1]
                    if _row_values(row) != (
                        selected["open_value"], selected["high_value"], selected["low_value"],
                        selected["close_value"], selected["volume"],
                    ):
                        raise ValidationError("Stage 10 complete window differs from current canonical values")
            elif (
                record["disposition"] == "complete_empty"
                and (known_on_or_before_end or final_window_without_any_boundary)
            ):
                raise ValidationError(
                    "Stage 10 empty window is not proven pre-listing"
                )
            elif (
                record["disposition"] == "terminal_failure"
                and record["terminal_reason"] == TRANSITION_EMPTY_TERMINAL_REASON
                and not known_on_or_before_end
            ):
                raise ValidationError(
                    "Stage 10 known-listed empty failure lacks listing evidence"
                )
        current_count += len(current)
        coverage.append({
            "provider_symbol": symbol, "row_count": len(current),
            "earliest_trade_date": min(current) if current else None,
            "latest_trade_date": max(current) if current else None,
        })

    failures = [record for record in ledger if record["disposition"] == "terminal_failure"]
    return {
        "ledger_sha256": _sha_json([_thaw(record) for record in ledger]),
        "raw_sidecar_manifest_sha256": _sha_json(sidecar_material),
        "complete_window_count": sum(record["disposition"] == "complete" for record in ledger),
        "empty_window_count": sum(record["disposition"] == "complete_empty" for record in ledger),
        "terminal_failure_count": len(failures),
        "failed_tickers": sorted({str(record["symbol"]) for record in failures}),
        "failed_windows": [{
            "symbol": record["symbol"], "window_id": record["window_id"],
            "from": record["from"], "to": record["to"],
            "terminal_reason": record["terminal_reason"], "http_status": record["http_status"],
        } for record in failures],
        "extension_capture_count": len(extension_seen),
        "current_price_count": current_count,
        "immutable_version_count": version_count,
        "price_coverage": coverage,
    }


def validate_stage10_history_extension_prefix(
    store_map: StoreMap, registry: Registry, scope: Stage10MarketScope, *,
    base_reconciliation: Stage10MarketReconciliation,
    base_completion_sha256: str,
    ledger_records: Sequence[Mapping[str, object]],
    sidecar_raw_by_intent: Mapping[str, bytes],
    authorization_proof: object = None,
) -> dict[str, object]:
    """Validate one exact completed schedule prefix before another live request.

    The caller must first re-read and authenticate the immutable intent/result
    journal.  This query-only gate then binds that prefix to retained raw bytes,
    ingestion receipts, canonical versions, and latest current pointers.
    """
    if not isinstance(store_map, StoreMap) or not isinstance(
        base_reconciliation, Stage10MarketReconciliation
    ):
        raise ValidationError("Stage 10 history-extension requires explicit stores and base proof")
    registry, scope = _validate_registry_scope(registry, scope, base_reconciliation)
    roster = _base_roster(base_reconciliation)
    ledger = _normalize_ledger(
        ledger_records, roster=roster,
        base_reconciliation_sha256=base_reconciliation.sha256,
        base_completion_sha256=base_completion_sha256,
        require_complete=False,
    )
    verified_chain = _authorization_proof_for_ledger(ledger, authorization_proof)
    before = mutation_fingerprint(store_map)
    with read_connection(store_map, StoreRole.MARKET) as connection:
        values = _reconcile_connection(
            connection, scope=scope, roster=roster, ledger=ledger,
            sidecars=sidecar_raw_by_intent,
        )
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 10 history-extension prefix validation mutated a store")
    return {
        "base_completion_sha256": base_completion_sha256,
        "base_reconciliation_sha256": base_reconciliation.sha256,
        "authorization_proof": _thaw(verified_chain),
        "closed_request_count": len(ledger),
        "ledger_sha256": values["ledger_sha256"],
        "raw_sidecar_manifest_sha256": values["raw_sidecar_manifest_sha256"],
        "mutation_sha256": before["sha256"],
    }


def reconcile_stage10_history_extension(
    store_map: StoreMap, registry: Registry, scope: Stage10MarketScope, *,
    base_reconciliation: Stage10MarketReconciliation,
    base_completion_sha256: str,
    ledger_records: Sequence[Mapping[str, object]],
    sidecar_raw_by_intent: Mapping[str, bytes],
    authorization_proof: object = None,
) -> Stage10HistoryExtensionReconciliation:
    """Recompute the complete successor market proof without a write."""
    if not isinstance(store_map, StoreMap) or not isinstance(base_reconciliation, Stage10MarketReconciliation):
        raise ValidationError("Stage 10 history-extension requires explicit stores and base proof")
    registry, scope = _validate_registry_scope(registry, scope, base_reconciliation)
    roster = _base_roster(base_reconciliation)
    ledger = _normalize_ledger(
        ledger_records, roster=roster,
        base_reconciliation_sha256=base_reconciliation.sha256,
        base_completion_sha256=base_completion_sha256,
    )
    verified_chain = _authorization_proof_for_ledger(ledger, authorization_proof)
    before = mutation_fingerprint(store_map)
    with read_connection(store_map, StoreRole.MARKET) as connection:
        values = _reconcile_connection(
            connection, scope=scope, roster=roster, ledger=ledger,
            sidecars=sidecar_raw_by_intent,
        )
    after = mutation_fingerprint(store_map)
    if before["sha256"] != after["sha256"]:
        raise ValidationError("Stage 10 history-extension reconciliation mutated a store")
    material_values = {
        "base_completion_sha256": base_completion_sha256,
        "base_reconciliation_sha256": base_reconciliation.sha256,
        "authorization_proof": verified_chain,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
        "registry_source_sha256": registry.source_sha256,
        "roster_count": len(roster),
        "planned_window_count": len(ledger),
        "ledger_sha256": values["ledger_sha256"],
        "raw_sidecar_manifest_sha256": values["raw_sidecar_manifest_sha256"],
        "complete_window_count": values["complete_window_count"],
        "empty_window_count": values["empty_window_count"],
        "terminal_failure_count": values["terminal_failure_count"],
        "failed_tickers": tuple(values["failed_tickers"]),
        "failed_windows": tuple(values["failed_windows"]),
        "extension_capture_count": values["extension_capture_count"],
        "current_price_count": values["current_price_count"],
        "immutable_version_count": values["immutable_version_count"],
        "price_coverage": tuple(values["price_coverage"]),
        "raw_sidecars_reparsed": True,
        "canonical_rows_match_raw": True,
        "correction_lineage_verified": True,
        "stores_unchanged": True,
    }
    digest_view = {
        "contract": "quant_data.stage10_history_extension_reconciliation",
        "contract_version": _CONTRACT_VERSION,
        **{key: (list(value) if key == "failed_tickers" else
                 [_thaw(item) for item in value] if key in {"failed_windows", "price_coverage"}
                 else _thaw(value) if key == "authorization_proof"
                 else value) for key, value in material_values.items()},
    }
    return Stage10HistoryExtensionReconciliation(
        **material_values, sha256=_sha_json(digest_view),
    )


def _maps_disjoint(first: StoreMap, second: StoreMap) -> bool:
    first_paths = [path.resolve(strict=True) for _, path in first.items()]
    second_paths = [path.resolve(strict=True) for _, path in second.items()]
    return all(not os.path.samefile(left, right) for left in first_paths for right in second_paths)


@dataclass(frozen=True, slots=True)
class Stage10HistoryExtensionEvidence:
    reconciliation: Stage10HistoryExtensionReconciliation
    cohort_registry_schema_version: str
    authorization_proof: Mapping[str, object] | None
    cohort_registry_version: str
    cohort_registry_source_sha256: str
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
    source_restored_equal: bool
    source_reads_unchanged: bool
    restored_reads_unchanged: bool
    raw_reparsed_equal: bool
    backup_restore_outcome: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.reconciliation, Stage10HistoryExtensionReconciliation):
            raise ValidationError("Stage 10 extension evidence reconciliation is invalid")
        if _thaw(self.authorization_proof) != _thaw(
            self.reconciliation.authorization_proof
        ):
            raise ValidationError(
                "Stage 10 extension evidence authorization chain is invalid"
            )
        object.__setattr__(
            self, "authorization_proof", _freeze(self.authorization_proof)
        )

        if (
            self.cohort_registry_schema_version != "1.7.0"
            or self.cohort_registry_version != "2.9.0"
        ):
            raise ValidationError(
                "Stage 10 extension evidence cohort registry is invalid"
            )
        for name in (
            "cohort_registry_source_sha256",
            "source_health_sha256", "restored_health_sha256",
            "source_logical_manifest_sha256", "restored_logical_manifest_sha256",
            "source_mutation_before_sha256", "source_mutation_after_sha256",
            "restored_mutation_before_sha256", "restored_mutation_after_sha256",
            "source_reconciliation_sha256", "restored_reconciliation_sha256", "sha256",
        ):
            _digest(getattr(self, name), name)
        if (
            not all((self.source_restored_equal, self.source_reads_unchanged,
                     self.restored_reads_unchanged, self.raw_reparsed_equal))
            or self.backup_restore_outcome != "validated_restored_cohort_equal"
            or self.source_reconciliation_sha256 != self.reconciliation.sha256
            or self.restored_reconciliation_sha256 != self.reconciliation.sha256
            or self.sha256 != _sha_json(self.material())
        ):
            raise ValidationError("Stage 10 extension evidence safety gates are incomplete")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_history_extension_evidence",
            "contract_version": _CONTRACT_VERSION,
            "reconciliation": self.reconciliation.to_primitive(),
            "cohort_registry_schema_version": self.cohort_registry_schema_version,
            "cohort_registry_version": self.cohort_registry_version,
            "authorization_proof": _thaw(self.authorization_proof),
            "cohort_registry_source_sha256": self.cohort_registry_source_sha256,
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
            "source_restored_equal": self.source_restored_equal,
            "source_reads_unchanged": self.source_reads_unchanged,
            "restored_reads_unchanged": self.restored_reads_unchanged,
            "raw_reparsed_equal": self.raw_reparsed_equal,
            "backup_restore_outcome": self.backup_restore_outcome,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "sha256": self.sha256}


def build_stage10_history_extension_evidence(
    source_map: StoreMap, restored_map: StoreMap, registry: Registry,
    scope: Stage10MarketScope, *, base_reconciliation: Stage10MarketReconciliation,
    base_completion_sha256: str, ledger_records: Sequence[Mapping[str, object]],
    sidecar_raw_by_intent: Mapping[str, bytes], cohort_registry: Registry,
    authorization_proof: object = None,
) -> Stage10HistoryExtensionEvidence:
    if (
        not isinstance(source_map, StoreMap) or not isinstance(restored_map, StoreMap)
        or not _maps_disjoint(source_map, restored_map)
    ):
        raise ValidationError("Stage 10 extension evidence cohort binding is invalid")
    _validate_registry_scope(registry, scope, base_reconciliation)
    cohort_registry = _validate_cohort_registry(cohort_registry, registry)
    source_before = mutation_fingerprint(source_map)
    restored_before = mutation_fingerprint(restored_map)
    source_health = all_store_health(source_map, cohort_registry)
    restored_health = all_store_health(restored_map, cohort_registry)
    kwargs = {
        "base_reconciliation": base_reconciliation,
        "base_completion_sha256": base_completion_sha256,
        "ledger_records": ledger_records,
        "sidecar_raw_by_intent": sidecar_raw_by_intent,
        "authorization_proof": authorization_proof,
    }
    source = reconcile_stage10_history_extension(source_map, registry, scope, **kwargs)
    restored = reconcile_stage10_history_extension(restored_map, registry, scope, **kwargs)
    source_manifest = logical_manifest(source_map, cohort_registry)
    restored_manifest = logical_manifest(restored_map, cohort_registry)
    source_after = mutation_fingerprint(source_map)
    restored_after = mutation_fingerprint(restored_map)
    if (
        source_before["sha256"] != source_after["sha256"]
        or restored_before["sha256"] != restored_after["sha256"]
        or source_health.to_primitive() != restored_health.to_primitive()
        or source_manifest["sha256"] != restored_manifest["sha256"]
        or source.to_primitive() != restored.to_primitive()
    ):
        raise ValidationError("Stage 10 extension source/restored evidence differs")
    values = {
        "reconciliation": source,
        "authorization_proof": source.authorization_proof,
        "cohort_registry_schema_version": cohort_registry.schema_version,
        "cohort_registry_version": cohort_registry.registry_version,
        "cohort_registry_source_sha256": cohort_registry.source_sha256,
        "source_health_sha256": _sha_json(source_health.to_primitive()),
        "restored_health_sha256": _sha_json(restored_health.to_primitive()),
        "source_logical_manifest_sha256": source_manifest["sha256"],
        "restored_logical_manifest_sha256": restored_manifest["sha256"],
        "source_mutation_before_sha256": source_before["sha256"],
        "source_mutation_after_sha256": source_after["sha256"],
        "restored_mutation_before_sha256": restored_before["sha256"],
        "restored_mutation_after_sha256": restored_after["sha256"],
        "source_reconciliation_sha256": source.sha256,
        "restored_reconciliation_sha256": restored.sha256,
        "source_restored_equal": True,
        "source_reads_unchanged": True,
        "restored_reads_unchanged": True,
        "raw_reparsed_equal": True,
        "backup_restore_outcome": "validated_restored_cohort_equal",
    }
    material = {
        "contract": "quant_data.stage10_history_extension_evidence",
        "contract_version": _CONTRACT_VERSION,
        "reconciliation": source.to_primitive(),
        "authorization_proof": _thaw(source.authorization_proof),
        **{
            key: value
            for key, value in values.items()
            if key not in {"reconciliation", "authorization_proof"}
        },
    }
    return Stage10HistoryExtensionEvidence(**values, sha256=_sha_json(material))


def _utc(value: Callable[[], object] | object) -> str:
    candidate = value() if callable(value) else value
    if not isinstance(candidate, datetime) or candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValidationError("Stage 10 extension generated_at must be timezone-aware")
    return candidate.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Stage10HistoryExtensionCandidateReceipt:
    attempt_id: str
    generated_at: str
    code_revision: str
    candidate_state: str
    evidence: Mapping[str, object]
    evidence_sha256: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.attempt_id, str) or _ATTEMPT.fullmatch(self.attempt_id) is None
            or not isinstance(self.code_revision, str) or _REVISION.fullmatch(self.code_revision) is None
            or self.candidate_state != _CANDIDATE_STATE
        ):
            raise ValidationError("Stage 10 extension candidate receipt identity is invalid")
        _digest(self.evidence_sha256, "evidence digest")
        _digest(self.receipt_sha256, "receipt digest")
        frozen = _freeze(self.evidence)
        if not isinstance(frozen, Mapping):
            raise ValidationError("Stage 10 extension candidate evidence is invalid")
        object.__setattr__(self, "evidence", frozen)
        primitive = _thaw(frozen)
        if (
            not isinstance(primitive, Mapping)
            or primitive.get("sha256") != self.evidence_sha256
        ):
            raise ValidationError("Stage 10 extension candidate evidence digest is invalid")
        reconciliation = primitive.get("reconciliation")
        proof_value = primitive.get("authorization_proof")
        failed_windows = (
            reconciliation.get("failed_windows")
            if isinstance(reconciliation, Mapping)
            else None
        )
        required = isinstance(failed_windows, list) and any(
            isinstance(item, Mapping)
            and item.get("terminal_reason") in _OPERATOR_TERMINAL_REASONS
            for item in failed_windows
        )
        try:
            proof = validate_stage10_history_extension_authorization_proof(
                proof_value,
                required=required,
            )
        except ValueError as exc:
            raise ValidationError(
                "Stage 10 extension candidate authorization proof is invalid"
            ) from exc
        if (
            not isinstance(reconciliation, Mapping)
            or reconciliation.get("authorization_proof") != proof
            or primitive.get("authorization_proof") != proof
        ):
            raise ValidationError(
                "Stage 10 extension candidate authorization proof is invalid"
            )
        if proof is not None:
            successor = proof.get("successor_transition")
            if (
                not isinstance(successor, Mapping)
                or successor.get("new_execution_revision") != self.code_revision
            ):
                raise ValidationError(
                    "Stage 10 extension candidate authorization revision differs"
                )
        if self.receipt_sha256 != _sha_json(self.material()):
            raise ValidationError("Stage 10 extension candidate receipt digest is invalid")

    def material(self) -> dict[str, object]:
        return {
            "contract": "quant_data.stage10_history_extension_candidate_receipt",
            "contract_version": _CONTRACT_VERSION,
            "attempt_id": self.attempt_id,
            "generated_at": self.generated_at,
            "code_revision": self.code_revision,
            "candidate_state": self.candidate_state,
            "evidence": _thaw(self.evidence),
            "evidence_sha256": self.evidence_sha256,
        }

    def to_primitive(self) -> dict[str, object]:
        return {**self.material(), "receipt_sha256": self.receipt_sha256}


class PrivateStage10HistoryExtensionCandidateState:
    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser().resolve(strict=False)
        if not candidate.is_absolute() or candidate == Path(candidate.anchor) or candidate == Path.home():
            raise ValidationError("Stage 10 extension candidate root is too broad")
        self._root = candidate

    @staticmethod
    def _fsync(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def publish(
        self, evidence: Stage10HistoryExtensionEvidence, *, code_revision: str,
        now: Callable[[], object] | object, attempt_id: str,
    ) -> Stage10HistoryExtensionCandidateReceipt:
        if not isinstance(evidence, Stage10HistoryExtensionEvidence):
            raise ValidationError("Stage 10 extension candidate requires validated evidence")
        generated = _utc(now)
        material = {
            "contract": "quant_data.stage10_history_extension_candidate_receipt",
            "contract_version": _CONTRACT_VERSION,
            "attempt_id": attempt_id,
            "generated_at": generated,
            "code_revision": code_revision,
            "candidate_state": _CANDIDATE_STATE,
            "evidence": evidence.to_primitive(),
            "evidence_sha256": evidence.sha256,
        }
        receipt = Stage10HistoryExtensionCandidateReceipt(
            attempt_id=attempt_id, generated_at=generated, code_revision=code_revision,
            candidate_state=_CANDIDATE_STATE, evidence=evidence.to_primitive(),
            evidence_sha256=evidence.sha256, receipt_sha256=_sha_json(material),
        )
        directory = self._root / "candidate-receipts"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ConflictError("Stage 10 extension candidate directory is invalid")
        os.chmod(self._root, 0o700)
        os.chmod(directory, 0o700)
        path = directory / f"{attempt_id}.json"
        if path.exists() or path.is_symlink():
            raise ConflictError("Stage 10 extension candidate receipt already exists")
        payload = (dumps_strict(receipt.to_primitive(), max_bytes=_MAX_RECEIPT_BYTES) + "\n").encode("utf-8")
        temporary = directory / f".{attempt_id}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        linked = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
            linked = True
            os.chmod(path, 0o600)
            self._fsync(directory)
            temporary.unlink()
            self._fsync(directory)
        finally:
            if temporary.exists():
                temporary.unlink()
            if linked and not path.is_file():
                raise OSError("Stage 10 extension candidate receipt disappeared")
        return receipt


__all__ = (
    "PrivateStage10HistoryExtensionCandidateState",
    "Stage10HistoryExtensionCandidateReceipt",
    "Stage10HistoryExtensionEvidence",
    "Stage10HistoryExtensionReconciliation",
    "build_stage10_history_extension_evidence",
    "normalize_stage10_history_extension_ledger",
    "reconcile_stage10_history_extension",
    "validate_stage10_history_extension_prefix",
)
