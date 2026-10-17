"""Closed authority scope for the bounded live Stage 12C market gap slice.

The manifest admits exactly two new daily sessions for the frozen Stage 12A
roster.  It intentionally describes no host, credential value, request
transport, scheduler, or caller-selectable database path.  The one retained
source literal is an immutable identity binding only; this module never opens
that source.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import Issue, ValidationError
from ..json_codec import dumps_strict, loads_strict
from .stage12_scope import (
    REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256,
    STAGE12_SCOPE_CONTRACT,
    STAGE12_SCOPE_VERSION,
    Stage12MarketV1Scope,
)
from .stage12b_scope import (
    REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256,
    STAGE12B_COLLECTOR_ID,
    STAGE12B_NORMALIZATION_VERSION,
    STAGE12B_SCOPE_CONTRACT,
    STAGE12B_SCOPE_VERSION,
    Stage12BIncrementalMarketScope,
    require_stage12b_scope,
)


STAGE12C_SCOPE_CONTRACT = "quant_data.stage12c_market_gap_v1_scope"
STAGE12C_SCOPE_VERSION = "1.0.0"
STAGE12C_TARGET_PROFILE_ID = "stage12c_market_gap_v1_project_default"
STAGE12C_COLLECTOR_ID = "market.stage12c.fmp_daily_incremental_manual"
STAGE12C_COLLECTOR_HANDLER = "market.stage12c_fmp_daily_incremental_manual"
STAGE12C_PRICE_VARIANT = "fmp_full_eod_v1"
STAGE12C_NORMALIZATION_VERSION = "stage12c.fmp.daily_incremental.live.v1"

# The semantic digest covers the strictly normalized JSON manifest.  The raw
# digest additionally makes whitespace/source-byte drift an admission failure.
REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256 = (
    "2c11fd5bfe99160e7949db3a87f2e3b1616a829b975424df40ec7f4fb16d5392"
)
REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256 = (
    "24d8448c124cedb5deb745b50943291d955f05e7a95ac1d9747a7d51b6760226"
)
REVIEWED_STAGE12A_SCOPE_FILE_SHA256 = (
    "12e9a7380ad22c9595d922c1a53320f3c52e368cb688adf1d97bf4d7c9a18bf6"
)
REVIEWED_STAGE12A_ROSTER_SHA256 = (
    "a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb"
)
REVIEWED_STAGE12B_SCOPE_FILE_SHA256 = (
    "671d0b62cbcc82c92eaf59fe129e4dad12f2f327995f24b81ffbb206d7645392"
)

_MAX_SCOPE_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^(?:[A-Z][A-Z0-9.-]{0,14}|\^[A-Z][A-Z0-9]{0,14})$")
_ABSOLUTE_OR_URL = re.compile(
    r"^(?:/|~(?:/|$)|[A-Za-z]:[\\/]|file:|[A-Za-z][A-Za-z0-9+.-]*://)"
)
_FORBIDDEN_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "header",
        "host",
        "password",
        "secret",
        "token",
        "uri",
        "url",
    }
)
_ASSET_TYPE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"equity": 519, "etf": 95, "index": 15}
)
_ROW_KEYS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "change",
    "changePercent",
    "vwap",
)
_INPUT_DATASETS = ("market.stage10.instruments", "market.stage10.daily_prices")
_OUTPUT_DATASETS = ("market.stage10.source_evidence", "market.stage10.daily_prices")
_EXPECTED_SESSIONS = ("2026-08-13", "2026-08-14")
_RETAINED_SOURCE_LITERAL = (
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1/stores/market.sqlite"
)
_RETAINED_SOURCE_SHA256 = "b0ee0a02cc74e603320d0fa4f7a68a64339f8ed834229bdc480efa0351cc6f3c"
_EXPECTED_CLOSED_CAPABILITIES = (
    "additional_dates",
    "additional_symbols",
    "default_database_selection",
    "migration",
    "public_consumer",
    "scheduler",
    "second_full_database_copy",
    "source_mutation",
    "stage10_request_replay",
    "target_replacement",
)
_EXPECTED_TOP_LEVEL = frozenset(
    {
        "authority_bindings",
        "closed_capabilities",
        "collector",
        "contract",
        "publication",
        "request_plan",
        "response",
        "source_target",
        "target_profile_id",
        "version",
    }
)


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _child(pointer: str, part: str | int) -> str:
    return f"{pointer}/{part}" if pointer else f"/{part}"


def _mapping(value: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error(pointer, "type", "Expected an object with string keys")
    return value


def _array(value: object, *, pointer: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(pointer, "type", "Expected an array")
    return value


def _closed(value: Mapping[str, Any], *, pointer: str, fields: frozenset[str]) -> None:
    if set(value) != fields:
        raise _error(pointer, "shape", "Object fields differ from the reviewed Stage 12C scope")


def _text(value: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _error(pointer, "type", "Expected bounded nonempty trimmed text")
    return value


def _exact_text(value: object, *, pointer: str, expected: str) -> str:
    parsed = _text(value, pointer=pointer, maximum=max(256, len(expected)))
    if parsed != expected:
        raise _error(pointer, "constant", "Value differs from the reviewed Stage 12C scope")
    return parsed


def _exact_int(value: object, *, pointer: str, expected: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _error(pointer, "constant", "Integer differs from the reviewed Stage 12C scope")
    return value


def _exact_bool(value: object, *, pointer: str, expected: bool) -> bool:
    if not isinstance(value, bool) or value is not expected:
        raise _error(pointer, "constant", "Boolean differs from the reviewed Stage 12C scope")
    return value


def _date_text(value: object, *, pointer: str, expected: str | None = None) -> str:
    parsed = _text(value, pointer=pointer, maximum=10)
    if len(parsed) != 10 or parsed[4] != "-" or parsed[7] != "-":
        raise _error(pointer, "date", "Expected an ISO calendar date")
    try:
        date.fromisoformat(parsed)
    except ValueError as exc:
        raise _error(pointer, "date", "Expected an ISO calendar date") from exc
    if expected is not None and parsed != expected:
        raise _error(pointer, "constant", "Date differs from the reviewed Stage 12C scope")
    return parsed


def _relative_path(value: object, *, pointer: str, expected: str) -> str:
    parsed = _exact_text(value, pointer=pointer, expected=expected)
    path = Path(parsed)
    if path.is_absolute() or ".." in path.parts or "\\" in parsed or not path.parts:
        raise _error(pointer, "path", "Path must be the reviewed project-relative declaration")
    return parsed


def _sha256_text(value: object, *, pointer: str, expected: str) -> str:
    parsed = _exact_text(value, pointer=pointer, expected=expected)
    if _SHA256.fullmatch(parsed) is None:
        raise _error(pointer, "sha256", "Expected a lowercase SHA-256 digest")
    return parsed


def _reject_unsafe_keys_and_values(value: object, *, pointer: str) -> None:
    """Reject secret-like declarations before the closed parser handles shape."""

    if isinstance(value, Mapping):
        for key, child_value in value.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Scope object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            child = _child(pointer, key)
            if normalized in _FORBIDDEN_KEYS:
                raise _error(child, "forbidden_key", "Scope cannot declare credentials or transport locators")
            _reject_unsafe_keys_and_values(child_value, pointer=child)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            _reject_unsafe_keys_and_values(child_value, pointer=_child(pointer, index))
    elif isinstance(value, str) and _ABSOLUTE_OR_URL.fullmatch(value) is not None:
        # The immutable retained-source literal is the sole reviewed exception.
        if value != _RETAINED_SOURCE_LITERAL:
            raise _error(pointer, "operational_value", "Scope cannot contain an absolute path or URL")


@dataclass(frozen=True, slots=True)
class Stage12CStage12ABinding:
    scope_contract: str
    scope_file_sha256: str
    scope_manifest_sha256: str
    scope_version: str
    roster_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "frozen_roster": {
                "asset_type_counts": dict(_ASSET_TYPE_COUNTS),
                "price_variant": STAGE12C_PRICE_VARIANT,
                "provider": "fmp",
                "symbol_count": 629,
            },
            "roster_sha256": self.roster_sha256,
            "scope_contract": self.scope_contract,
            "scope_file_sha256": self.scope_file_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "scope_version": self.scope_version,
        }


@dataclass(frozen=True, slots=True)
class Stage12CStage12BBinding:
    collector_id: str
    normalization_version: str
    scope_contract: str
    scope_file_sha256: str
    scope_manifest_sha256: str
    scope_version: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "collector_id": self.collector_id,
            "normalization_version": self.normalization_version,
            "scope_contract": self.scope_contract,
            "scope_file_sha256": self.scope_file_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "scope_version": self.scope_version,
        }


@dataclass(frozen=True, slots=True)
class Stage12CAuthorityBindings:
    stage12a: Stage12CStage12ABinding
    stage12b: Stage12CStage12BBinding

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "stage12a": self.stage12a.manifest_mapping(),
            "stage12b": self.stage12b.manifest_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Stage12CCollectorPolicy:
    id: str
    handler: str
    max_bytes: int
    max_requests: int
    max_rows: int
    max_seconds: int
    input_datasets: tuple[str, ...]
    output_datasets: tuple[str, ...]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "configuration_env": ["FMP_API_KEY"],
            "handler": self.handler,
            "id": self.id,
            "input_datasets": list(self.input_datasets),
            "mutation_policy": {
                "mode": "append_versions_and_move_current_projection",
                "unchanged": "zero_persistent_writes",
            },
            "network": True,
            "output_datasets": list(self.output_datasets),
            "physical_locks": "derived_from_output_store_paths",
            "retry_policy": {
                "backoff": "none",
                "honor_retry_after": False,
                "max_attempts": 1,
                "transient_classes": [],
            },
            "schedule_eligibility": {"mode": "manual_only"},
            "semantic_identity": {
                "excludes": [
                    "api_key",
                    "captured_at",
                    "http_headers",
                    "source_row_order",
                ],
                "includes": [
                    "scope_manifest_sha256",
                    "request_scope",
                    "normalization_version",
                    "normalized_complete_batch",
                ],
            },
            "version": "1.0.0",
            "workload_bounds": {
                "max_bytes": self.max_bytes,
                "max_requests": self.max_requests,
                "max_rows": self.max_rows,
                "max_seconds": self.max_seconds,
            },
        }


@dataclass(frozen=True, slots=True)
class Stage12CRequestPlan:
    endpoint_path: str
    from_date: str
    to_date: str
    session_dates: tuple[str, ...]
    sentinel_symbol: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "endpoint_path": self.endpoint_path,
            "expected_session_dates": list(self.session_dates),
            "from": self.from_date,
            "logical_unit_count": 629,
            "query_parameters": ["symbol", "from", "to"],
            "remaining_symbol_order": "stage12a_frozen_roster_lexicographic_excluding_sentinel",
            "sentinel_symbol": self.sentinel_symbol,
            "symbols_per_request": 1,
            "to": self.to_date,
        }


@dataclass(frozen=True, slots=True)
class Stage12CResponsePolicy:
    row_keys: tuple[str, ...]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "complete_row_count": 2,
            "encoding": "utf-8",
            "exact_row_keys": list(self.row_keys),
            "media_type": "application/json",
            "top_level": "list",
        }


@dataclass(frozen=True, slots=True)
class Stage12CPublicationPolicy:
    availability: str
    currency_segment: str
    normalization_version: str
    relations: tuple[str, ...]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "availability": self.availability,
            "currency_segment": self.currency_segment,
            "existing_instrument": "required",
            "normalization_version": self.normalization_version,
            "relations": list(self.relations),
        }


@dataclass(frozen=True, slots=True)
class Stage12CSourceTarget:
    project_store_path: str
    private_state_root: str
    retained_source_initial_sha256: str
    retained_source_literal: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "project_store_path": self.project_store_path,
            "private_state_root": self.private_state_root,
            "retained_source_initial_sha256": self.retained_source_initial_sha256,
            "retained_source_literal": self.retained_source_literal,
            "same_baseline_required": True,
        }


@dataclass(frozen=True, slots=True)
class Stage12CMarketGapV1Scope:
    authority_bindings: Stage12CAuthorityBindings
    closed_capabilities: tuple[str, ...]
    collector: Stage12CCollectorPolicy
    contract: str
    publication: Stage12CPublicationPolicy
    request_plan: Stage12CRequestPlan
    response: Stage12CResponsePolicy
    source_target: Stage12CSourceTarget
    target_profile_id: str
    version: str
    manifest_sha256: str
    source_file_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "authority_bindings": self.authority_bindings.manifest_mapping(),
            "closed_capabilities": list(self.closed_capabilities),
            "collector": self.collector.manifest_mapping(),
            "contract": self.contract,
            "publication": self.publication.manifest_mapping(),
            "request_plan": self.request_plan.manifest_mapping(),
            "response": self.response.manifest_mapping(),
            "source_target": self.source_target.manifest_mapping(),
            "target_profile_id": self.target_profile_id,
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.manifest_mapping(),
            "manifest_sha256": self.manifest_sha256,
            "source_file_sha256": self.source_file_sha256,
        }


def _parse_stage12a_binding(value: object) -> Stage12CStage12ABinding:
    raw = _mapping(value, pointer="/authority_bindings/stage12a")
    _closed(
        raw,
        pointer="/authority_bindings/stage12a",
        fields=frozenset(
            {
                "frozen_roster",
                "roster_sha256",
                "scope_contract",
                "scope_file_sha256",
                "scope_manifest_sha256",
                "scope_version",
            }
        ),
    )
    frozen = _mapping(raw["frozen_roster"], pointer="/authority_bindings/stage12a/frozen_roster")
    _closed(
        frozen,
        pointer="/authority_bindings/stage12a/frozen_roster",
        fields=frozenset({"asset_type_counts", "price_variant", "provider", "symbol_count"}),
    )
    counts = _mapping(
        frozen["asset_type_counts"],
        pointer="/authority_bindings/stage12a/frozen_roster/asset_type_counts",
    )
    _closed(
        counts,
        pointer="/authority_bindings/stage12a/frozen_roster/asset_type_counts",
        fields=frozenset(_ASSET_TYPE_COUNTS),
    )
    for asset_type, expected in _ASSET_TYPE_COUNTS.items():
        _exact_int(
            counts[asset_type],
            pointer=f"/authority_bindings/stage12a/frozen_roster/asset_type_counts/{asset_type}",
            expected=expected,
        )
    _exact_text(
        frozen["price_variant"],
        pointer="/authority_bindings/stage12a/frozen_roster/price_variant",
        expected=STAGE12C_PRICE_VARIANT,
    )
    _exact_text(
        frozen["provider"],
        pointer="/authority_bindings/stage12a/frozen_roster/provider",
        expected="fmp",
    )
    _exact_int(
        frozen["symbol_count"],
        pointer="/authority_bindings/stage12a/frozen_roster/symbol_count",
        expected=629,
    )
    return Stage12CStage12ABinding(
        scope_contract=_exact_text(
            raw["scope_contract"],
            pointer="/authority_bindings/stage12a/scope_contract",
            expected=STAGE12_SCOPE_CONTRACT,
        ),
        scope_file_sha256=_sha256_text(
            raw["scope_file_sha256"],
            pointer="/authority_bindings/stage12a/scope_file_sha256",
            expected=REVIEWED_STAGE12A_SCOPE_FILE_SHA256,
        ),
        scope_manifest_sha256=_sha256_text(
            raw["scope_manifest_sha256"],
            pointer="/authority_bindings/stage12a/scope_manifest_sha256",
            expected=REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256,
        ),
        scope_version=_exact_text(
            raw["scope_version"],
            pointer="/authority_bindings/stage12a/scope_version",
            expected=STAGE12_SCOPE_VERSION,
        ),
        roster_sha256=_sha256_text(
            raw["roster_sha256"],
            pointer="/authority_bindings/stage12a/roster_sha256",
            expected=REVIEWED_STAGE12A_ROSTER_SHA256,
        ),
    )


def _parse_stage12b_binding(value: object) -> Stage12CStage12BBinding:
    raw = _mapping(value, pointer="/authority_bindings/stage12b")
    _closed(
        raw,
        pointer="/authority_bindings/stage12b",
        fields=frozenset(
            {
                "collector_id",
                "normalization_version",
                "scope_contract",
                "scope_file_sha256",
                "scope_manifest_sha256",
                "scope_version",
            }
        ),
    )
    return Stage12CStage12BBinding(
        collector_id=_exact_text(
            raw["collector_id"],
            pointer="/authority_bindings/stage12b/collector_id",
            expected=STAGE12B_COLLECTOR_ID,
        ),
        normalization_version=_exact_text(
            raw["normalization_version"],
            pointer="/authority_bindings/stage12b/normalization_version",
            expected=STAGE12B_NORMALIZATION_VERSION,
        ),
        scope_contract=_exact_text(
            raw["scope_contract"],
            pointer="/authority_bindings/stage12b/scope_contract",
            expected=STAGE12B_SCOPE_CONTRACT,
        ),
        scope_file_sha256=_sha256_text(
            raw["scope_file_sha256"],
            pointer="/authority_bindings/stage12b/scope_file_sha256",
            expected=REVIEWED_STAGE12B_SCOPE_FILE_SHA256,
        ),
        scope_manifest_sha256=_sha256_text(
            raw["scope_manifest_sha256"],
            pointer="/authority_bindings/stage12b/scope_manifest_sha256",
            expected=REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256,
        ),
        scope_version=_exact_text(
            raw["scope_version"],
            pointer="/authority_bindings/stage12b/scope_version",
            expected=STAGE12B_SCOPE_VERSION,
        ),
    )


def _parse_collector(value: object) -> Stage12CCollectorPolicy:
    raw = _mapping(value, pointer="/collector")
    _closed(
        raw,
        pointer="/collector",
        fields=frozenset(
            {
                "configuration_env",
                "handler",
                "id",
                "input_datasets",
                "mutation_policy",
                "network",
                "output_datasets",
                "physical_locks",
                "retry_policy",
                "schedule_eligibility",
                "semantic_identity",
                "version",
                "workload_bounds",
            }
        ),
    )
    bounds = _mapping(raw["workload_bounds"], pointer="/collector/workload_bounds")
    _closed(
        bounds,
        pointer="/collector/workload_bounds",
        fields=frozenset({"max_bytes", "max_requests", "max_rows", "max_seconds"}),
    )
    max_bytes = _exact_int(bounds["max_bytes"], pointer="/collector/workload_bounds/max_bytes", expected=65_536)
    max_requests = _exact_int(bounds["max_requests"], pointer="/collector/workload_bounds/max_requests", expected=1)
    max_rows = _exact_int(bounds["max_rows"], pointer="/collector/workload_bounds/max_rows", expected=2)
    max_seconds = _exact_int(bounds["max_seconds"], pointer="/collector/workload_bounds/max_seconds", expected=45)
    configuration_env = tuple(
        _text(item, pointer=f"/collector/configuration_env/{index}")
        for index, item in enumerate(_array(raw["configuration_env"], pointer="/collector/configuration_env"))
    )
    if configuration_env != ("FMP_API_KEY",):
        raise _error("/collector/configuration_env", "closed", "Stage 12C declares exactly FMP_API_KEY")
    input_datasets = tuple(
        _text(item, pointer=f"/collector/input_datasets/{index}")
        for index, item in enumerate(_array(raw["input_datasets"], pointer="/collector/input_datasets"))
    )
    output_datasets = tuple(
        _text(item, pointer=f"/collector/output_datasets/{index}")
        for index, item in enumerate(_array(raw["output_datasets"], pointer="/collector/output_datasets"))
    )
    if input_datasets != _INPUT_DATASETS or output_datasets != _OUTPUT_DATASETS:
        raise _error("/collector", "datasets", "Collector datasets differ from the reviewed Stage 12C declaration")
    mutation = _mapping(raw["mutation_policy"], pointer="/collector/mutation_policy")
    _closed(mutation, pointer="/collector/mutation_policy", fields=frozenset({"mode", "unchanged"}))
    _exact_text(
        mutation["mode"],
        pointer="/collector/mutation_policy/mode",
        expected="append_versions_and_move_current_projection",
    )
    _exact_text(
        mutation["unchanged"],
        pointer="/collector/mutation_policy/unchanged",
        expected="zero_persistent_writes",
    )
    _exact_bool(raw["network"], pointer="/collector/network", expected=True)
    _exact_text(
        raw["physical_locks"],
        pointer="/collector/physical_locks",
        expected="derived_from_output_store_paths",
    )
    retry = _mapping(raw["retry_policy"], pointer="/collector/retry_policy")
    _closed(
        retry,
        pointer="/collector/retry_policy",
        fields=frozenset({"backoff", "honor_retry_after", "max_attempts", "transient_classes"}),
    )
    _exact_text(retry["backoff"], pointer="/collector/retry_policy/backoff", expected="none")
    _exact_bool(retry["honor_retry_after"], pointer="/collector/retry_policy/honor_retry_after", expected=False)
    _exact_int(retry["max_attempts"], pointer="/collector/retry_policy/max_attempts", expected=1)
    if _array(retry["transient_classes"], pointer="/collector/retry_policy/transient_classes"):
        raise _error("/collector/retry_policy/transient_classes", "closed", "Stage 12C cannot execute retries")
    schedule = _mapping(raw["schedule_eligibility"], pointer="/collector/schedule_eligibility")
    _closed(schedule, pointer="/collector/schedule_eligibility", fields=frozenset({"mode"}))
    _exact_text(schedule["mode"], pointer="/collector/schedule_eligibility/mode", expected="manual_only")
    semantic = _mapping(raw["semantic_identity"], pointer="/collector/semantic_identity")
    _closed(semantic, pointer="/collector/semantic_identity", fields=frozenset({"excludes", "includes"}))
    includes = tuple(
        _text(item, pointer=f"/collector/semantic_identity/includes/{index}")
        for index, item in enumerate(_array(semantic["includes"], pointer="/collector/semantic_identity/includes"))
    )
    excludes = tuple(
        _text(item, pointer=f"/collector/semantic_identity/excludes/{index}")
        for index, item in enumerate(_array(semantic["excludes"], pointer="/collector/semantic_identity/excludes"))
    )
    if includes != (
        "scope_manifest_sha256",
        "request_scope",
        "normalization_version",
        "normalized_complete_batch",
    ) or excludes != (
        "api_key",
        "captured_at",
        "http_headers",
        "source_row_order",
    ):
        raise _error("/collector/semantic_identity", "closed", "Semantic identity declaration differs from the reviewed Stage 12C policy")
    _exact_text(raw["version"], pointer="/collector/version", expected="1.0.0")
    return Stage12CCollectorPolicy(
        id=_exact_text(raw["id"], pointer="/collector/id", expected=STAGE12C_COLLECTOR_ID),
        handler=_exact_text(raw["handler"], pointer="/collector/handler", expected=STAGE12C_COLLECTOR_HANDLER),
        max_bytes=max_bytes,
        max_requests=max_requests,
        max_rows=max_rows,
        max_seconds=max_seconds,
        input_datasets=input_datasets,
        output_datasets=output_datasets,
    )


def _parse_request_plan(value: object) -> Stage12CRequestPlan:
    raw = _mapping(value, pointer="/request_plan")
    _closed(
        raw,
        pointer="/request_plan",
        fields=frozenset(
            {
                "endpoint_path",
                "expected_session_dates",
                "from",
                "logical_unit_count",
                "query_parameters",
                "remaining_symbol_order",
                "sentinel_symbol",
                "symbols_per_request",
                "to",
            }
        ),
    )
    endpoint = _exact_text(
        raw["endpoint_path"],
        pointer="/request_plan/endpoint_path",
        expected="/stable/historical-price-eod/full",
    )
    sessions = tuple(
        _date_text(item, pointer=f"/request_plan/expected_session_dates/{index}")
        for index, item in enumerate(_array(raw["expected_session_dates"], pointer="/request_plan/expected_session_dates"))
    )
    if sessions != _EXPECTED_SESSIONS:
        raise _error("/request_plan/expected_session_dates", "closed", "Stage 12C sessions differ from the reviewed two-session scope")
    from_date = _date_text(raw["from"], pointer="/request_plan/from", expected=_EXPECTED_SESSIONS[0])
    to_date = _date_text(raw["to"], pointer="/request_plan/to", expected=_EXPECTED_SESSIONS[-1])
    if date.fromisoformat(to_date) < date.fromisoformat(from_date):
        raise _error("/request_plan", "window", "Stage 12C window is not ordered")
    _exact_int(raw["logical_unit_count"], pointer="/request_plan/logical_unit_count", expected=629)
    query = tuple(
        _text(item, pointer=f"/request_plan/query_parameters/{index}")
        for index, item in enumerate(_array(raw["query_parameters"], pointer="/request_plan/query_parameters"))
    )
    if query != ("symbol", "from", "to"):
        raise _error("/request_plan/query_parameters", "closed", "Stage 12C permits only the exact reviewed query parameters")
    _exact_text(
        raw["remaining_symbol_order"],
        pointer="/request_plan/remaining_symbol_order",
        expected="stage12a_frozen_roster_lexicographic_excluding_sentinel",
    )
    sentinel = _exact_text(raw["sentinel_symbol"], pointer="/request_plan/sentinel_symbol", expected="AAPL")
    if _SYMBOL.fullmatch(sentinel) is None:
        raise _error("/request_plan/sentinel_symbol", "symbol", "Sentinel symbol is invalid")
    _exact_int(raw["symbols_per_request"], pointer="/request_plan/symbols_per_request", expected=1)
    return Stage12CRequestPlan(
        endpoint_path=endpoint,
        from_date=from_date,
        to_date=to_date,
        session_dates=sessions,
        sentinel_symbol=sentinel,
    )


def _parse_response(value: object) -> Stage12CResponsePolicy:
    raw = _mapping(value, pointer="/response")
    _closed(
        raw,
        pointer="/response",
        fields=frozenset({"complete_row_count", "encoding", "exact_row_keys", "media_type", "top_level"}),
    )
    _exact_int(raw["complete_row_count"], pointer="/response/complete_row_count", expected=2)
    _exact_text(raw["encoding"], pointer="/response/encoding", expected="utf-8")
    _exact_text(raw["media_type"], pointer="/response/media_type", expected="application/json")
    _exact_text(raw["top_level"], pointer="/response/top_level", expected="list")
    row_keys = tuple(
        _text(item, pointer=f"/response/exact_row_keys/{index}")
        for index, item in enumerate(_array(raw["exact_row_keys"], pointer="/response/exact_row_keys"))
    )
    if row_keys != _ROW_KEYS:
        raise _error("/response/exact_row_keys", "closed", "Response row keys differ from the reviewed Stage 12C payload")
    return Stage12CResponsePolicy(row_keys=row_keys)


def _parse_publication(value: object) -> Stage12CPublicationPolicy:
    raw = _mapping(value, pointer="/publication")
    _closed(
        raw,
        pointer="/publication",
        fields=frozenset({"availability", "currency_segment", "existing_instrument", "normalization_version", "relations"}),
    )
    availability = _exact_text(raw["availability"], pointer="/publication/availability", expected="local_capture_only")
    currency_segment = _exact_text(raw["currency_segment"], pointer="/publication/currency_segment", expected="provider_native")
    _exact_text(raw["existing_instrument"], pointer="/publication/existing_instrument", expected="required")
    normalization = _exact_text(
        raw["normalization_version"],
        pointer="/publication/normalization_version",
        expected=STAGE12C_NORMALIZATION_VERSION,
    )
    relations = tuple(
        _text(item, pointer=f"/publication/relations/{index}")
        for index, item in enumerate(_array(raw["relations"], pointer="/publication/relations"))
    )
    if relations != (
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    ):
        raise _error("/publication/relations", "closed", "Publication relations differ from the reviewed Stage 12C boundary")
    return Stage12CPublicationPolicy(
        availability=availability,
        currency_segment=currency_segment,
        normalization_version=normalization,
        relations=relations,
    )


def _parse_source_target(value: object) -> Stage12CSourceTarget:
    raw = _mapping(value, pointer="/source_target")
    _closed(
        raw,
        pointer="/source_target",
        fields=frozenset(
            {
                "project_store_path",
                "private_state_root",
                "retained_source_initial_sha256",
                "retained_source_literal",
                "same_baseline_required",
            }
        ),
    )
    return Stage12CSourceTarget(
        project_store_path=_relative_path(
            raw["project_store_path"],
            pointer="/source_target/project_store_path",
            expected="data/market.sqlite",
        ),
        private_state_root=_relative_path(
            raw["private_state_root"],
            pointer="/source_target/private_state_root",
            expected="data/.stage12/market-v1/stage12c-20260813-20260814",
        ),
        retained_source_initial_sha256=_sha256_text(
            raw["retained_source_initial_sha256"],
            pointer="/source_target/retained_source_initial_sha256",
            expected=_RETAINED_SOURCE_SHA256,
        ),
        retained_source_literal=_exact_text(
            raw["retained_source_literal"],
            pointer="/source_target/retained_source_literal",
            expected=_RETAINED_SOURCE_LITERAL,
        ),
    )


def _parse_scope(value: object, *, source_file_sha256: str) -> Stage12CMarketGapV1Scope:
    raw = _mapping(value, pointer="/")
    _closed(raw, pointer="/", fields=_EXPECTED_TOP_LEVEL)
    closed_capabilities = tuple(
        _text(item, pointer=f"/closed_capabilities/{index}")
        for index, item in enumerate(_array(raw["closed_capabilities"], pointer="/closed_capabilities"))
    )
    if closed_capabilities != _EXPECTED_CLOSED_CAPABILITIES or len(set(closed_capabilities)) != len(closed_capabilities):
        raise _error("/closed_capabilities", "closed", "Closed capabilities differ from the reviewed Stage 12C boundary")
    bindings_raw = _mapping(raw["authority_bindings"], pointer="/authority_bindings")
    _closed(bindings_raw, pointer="/authority_bindings", fields=frozenset({"stage12a", "stage12b"}))
    scope = Stage12CMarketGapV1Scope(
        authority_bindings=Stage12CAuthorityBindings(
            stage12a=_parse_stage12a_binding(bindings_raw["stage12a"]),
            stage12b=_parse_stage12b_binding(bindings_raw["stage12b"]),
        ),
        closed_capabilities=closed_capabilities,
        collector=_parse_collector(raw["collector"]),
        contract=_exact_text(raw["contract"], pointer="/contract", expected=STAGE12C_SCOPE_CONTRACT),
        publication=_parse_publication(raw["publication"]),
        request_plan=_parse_request_plan(raw["request_plan"]),
        response=_parse_response(raw["response"]),
        source_target=_parse_source_target(raw["source_target"]),
        target_profile_id=_exact_text(
            raw["target_profile_id"],
            pointer="/target_profile_id",
            expected=STAGE12C_TARGET_PROFILE_ID,
        ),
        version=_exact_text(raw["version"], pointer="/version", expected=STAGE12C_SCOPE_VERSION),
        manifest_sha256="",
        source_file_sha256=source_file_sha256,
    )
    if dumps_strict(raw) != dumps_strict(scope.manifest_mapping()):
        raise _error("/", "canonical", "Scope cannot be normalized to the reviewed closed schema")
    digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    if digest != REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256:
        raise _error("/", "immutable", "Scope differs from the reviewed immutable Stage 12C manifest")
    return replace(scope, manifest_sha256=digest)


def load_stage12c_market_gap_v1_scope(source: str | Path) -> Stage12CMarketGapV1Scope:
    """Load the one exact Stage 12C two-session Market v1 scope."""

    if isinstance(source, bool) or not isinstance(source, (str, Path)):
        raise _error("/", "source", "An explicit Stage 12C scope file is required")
    if isinstance(source, str) and not source.strip():
        raise _error("/", "source", "An explicit Stage 12C scope file is required")
    try:
        payload = Path(source).read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "source", "Stage 12C scope file is unavailable") from exc
    source_digest = hashlib.sha256(payload).hexdigest()
    if source_digest != REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256:
        raise _error("/", "source_bytes", "Stage 12C scope source bytes differ from the reviewed manifest")
    raw = loads_strict(payload, max_bytes=_MAX_SCOPE_BYTES)
    _reject_unsafe_keys_and_values(raw, pointer="")
    return _parse_scope(raw, source_file_sha256=source_digest)


def require_stage12c_scope(scope: object) -> Stage12CMarketGapV1Scope:
    """Revalidate a supplied in-memory Stage 12C scope before use."""

    if not isinstance(scope, Stage12CMarketGapV1Scope):
        raise ValidationError("Stage 12C requires a reviewed market-gap scope")
    try:
        digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Stage 12C market-gap scope is invalid") from exc
    if (
        scope.manifest_sha256 != REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256
        or scope.source_file_sha256 != REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256
        or digest != REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256
    ):
        raise ValidationError("Stage 12C market-gap scope binding is invalid")
    return scope


def require_stage12c_bindings(
    scope: object,
    stage12a_scope: object,
    stage12b_scope: object,
) -> tuple[Stage12CMarketGapV1Scope, Stage12MarketV1Scope, Stage12BIncrementalMarketScope]:
    """Bind Stage 12C to the exact prior authority and fixture contracts."""

    validated_scope = require_stage12c_scope(scope)
    if not isinstance(stage12a_scope, Stage12MarketV1Scope):
        raise ValidationError("Stage 12C requires an explicit reviewed Stage 12A scope")
    if not isinstance(stage12b_scope, Stage12BIncrementalMarketScope):
        raise ValidationError("Stage 12C requires an explicit reviewed Stage 12B scope")
    try:
        validated_stage12b = require_stage12b_scope(stage12b_scope)
        stage12a_digest = hashlib.sha256(
            dumps_strict(stage12a_scope.manifest_mapping()).encode("utf-8")
        ).hexdigest()
        roster_digest = hashlib.sha256(
            dumps_strict(
                [item.manifest_mapping() for item in stage12a_scope.roster]
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Stage 12C prior-scope binding is invalid") from exc
    stage12a_binding = validated_scope.authority_bindings.stage12a
    stage12b_binding = validated_scope.authority_bindings.stage12b
    if (
        stage12a_scope.contract != STAGE12_SCOPE_CONTRACT
        or stage12a_scope.version != STAGE12_SCOPE_VERSION
        or stage12a_scope.manifest_sha256 != REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256
        or stage12a_digest != REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256
        or stage12a_scope.roster_sha256 != REVIEWED_STAGE12A_ROSTER_SHA256
        or roster_digest != REVIEWED_STAGE12A_ROSTER_SHA256
        or len(stage12a_scope.roster) != 629
        or stage12a_scope.baseline.provider != "fmp"
        or stage12a_scope.baseline.price_variant != STAGE12C_PRICE_VARIANT
        or stage12a_binding.scope_contract != STAGE12_SCOPE_CONTRACT
        or stage12a_binding.scope_file_sha256 != REVIEWED_STAGE12A_SCOPE_FILE_SHA256
        or stage12a_binding.scope_manifest_sha256 != stage12a_digest
        or stage12a_binding.scope_version != STAGE12_SCOPE_VERSION
        or stage12a_binding.roster_sha256 != roster_digest
        or validated_stage12b.contract != STAGE12B_SCOPE_CONTRACT
        or validated_stage12b.version != STAGE12B_SCOPE_VERSION
        or validated_stage12b.manifest_sha256 != REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256
        or validated_stage12b.collector.id != STAGE12B_COLLECTOR_ID
        or validated_stage12b.publication.normalization_version != STAGE12B_NORMALIZATION_VERSION
        or stage12b_binding.collector_id != STAGE12B_COLLECTOR_ID
        or stage12b_binding.normalization_version != STAGE12B_NORMALIZATION_VERSION
        or stage12b_binding.scope_contract != STAGE12B_SCOPE_CONTRACT
        or stage12b_binding.scope_file_sha256 != REVIEWED_STAGE12B_SCOPE_FILE_SHA256
        or stage12b_binding.scope_manifest_sha256 != validated_stage12b.manifest_sha256
        or stage12b_binding.scope_version != STAGE12B_SCOPE_VERSION
    ):
        raise ValidationError("Stage 12C Stage 12A/12B authority binding is invalid")
    counts = {
        asset_type: sum(item.asset_type == asset_type for item in stage12a_scope.roster)
        for asset_type in _ASSET_TYPE_COUNTS
    }
    if counts != dict(_ASSET_TYPE_COUNTS):
        raise ValidationError("Stage 12C frozen roster classifications are invalid")
    return validated_scope, stage12a_scope, validated_stage12b


def stage12c_ordered_symbols(
    scope: object,
    stage12a_scope: object,
    stage12b_scope: object,
) -> tuple[str, ...]:
    """Return the one reviewed request order: AAPL then sorted remaining roster."""

    validated_scope, validated_stage12a, _ = require_stage12c_bindings(
        scope, stage12a_scope, stage12b_scope
    )
    symbols = tuple(item.symbol for item in validated_stage12a.roster)
    sentinel = validated_scope.request_plan.sentinel_symbol
    if sentinel not in symbols:
        raise ValidationError("Stage 12C sentinel is absent from the frozen roster")
    remaining = tuple(symbol for symbol in symbols if symbol != sentinel)
    ordered = (sentinel, *remaining)
    if (
        len(ordered) != 629
        or len(set(ordered)) != 629
        or ordered[0] != "AAPL"
        or ordered[1:] != tuple(sorted(remaining))
    ):
        raise ValidationError("Stage 12C reviewed request order is invalid")
    return ordered


__all__ = (
    "REVIEWED_STAGE12A_ROSTER_SHA256",
    "REVIEWED_STAGE12A_SCOPE_FILE_SHA256",
    "REVIEWED_STAGE12B_SCOPE_FILE_SHA256",
    "REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_FILE_SHA256",
    "REVIEWED_STAGE12C_MARKET_GAP_V1_SCOPE_SHA256",
    "STAGE12C_COLLECTOR_HANDLER",
    "STAGE12C_COLLECTOR_ID",
    "STAGE12C_NORMALIZATION_VERSION",
    "STAGE12C_PRICE_VARIANT",
    "STAGE12C_SCOPE_CONTRACT",
    "STAGE12C_SCOPE_VERSION",
    "STAGE12C_TARGET_PROFILE_ID",
    "Stage12CAuthorityBindings",
    "Stage12CCollectorPolicy",
    "Stage12CMarketGapV1Scope",
    "Stage12CPublicationPolicy",
    "Stage12CRequestPlan",
    "Stage12CResponsePolicy",
    "Stage12CSourceTarget",
    "Stage12CStage12ABinding",
    "Stage12CStage12BBinding",
    "load_stage12c_market_gap_v1_scope",
    "require_stage12c_bindings",
    "require_stage12c_scope",
    "stage12c_ordered_symbols",
)
