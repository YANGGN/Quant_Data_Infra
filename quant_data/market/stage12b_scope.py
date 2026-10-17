"""Closed, fixture-only scope for the Stage 12B market increment collector.

The manifest deliberately records a synthetic test plan rather than a live
provider request.  It has no credential, host, URL, or filesystem-target
declaration.  A caller supplies both this scope and the already-validated
Stage 12A authority scope explicitly; no ambient default is consulted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
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


STAGE12B_SCOPE_CONTRACT = "quant_data.stage12b_incremental_market_v1_scope"
STAGE12B_SCOPE_VERSION = "1.0.0"
STAGE12B_TARGET_PROFILE_ID = "stage12b_incremental_market_v1_fixture"
STAGE12B_COLLECTOR_ID = "market.stage12b.fmp_daily_incremental_fixture"
STAGE12B_COLLECTOR_HANDLER = "market.stage12b_fmp_daily_incremental_fixture"
STAGE12B_PRICE_VARIANT = "fmp_full_eod_v1"
STAGE12B_NORMALIZATION_VERSION = "stage12b.fmp.daily_incremental.fixture.v1"

# Semantic SHA-256 over the strict JSON rendering of the reviewed manifest.
REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256 = (
    "91d202540e24d2f94a3adb10135dde0bb3ee0c787c801bf66e547d649a7a3dfb"
)
REVIEWED_STAGE12A_ROSTER_SHA256 = (
    "a81f62a5fe3710011e1fe6eabfe7fcd483726a23f2dc9b0a7e592b4c78327fbb"
)

REVIEWED_STAGE12A_SCOPE_FILE_SHA256 = (
    "12e9a7380ad22c9595d922c1a53320f3c52e368cb688adf1d97bf4d7c9a18bf6"
)

_MAX_SCOPE_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_ABSOLUTE_VALUE = re.compile(r"^(?:/|~(?:/|$)|[A-Za-z]:[\\/]|file:|[A-Za-z][A-Za-z0-9+.-]*://)")
_FORBIDDEN_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "header",
        "host",
        "password",
        "path",
        "root",
        "secret",
        "token",
        "uri",
        "url",
    }
)

_STAGE12A_SOURCE_BINDINGS: Mapping[str, str] = MappingProxyType(
    {
        "resume_artifact_sha256": "1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795",
        "stage10_failure_manifest_sha256": "1b0b2a64ca302b1c5c411f927d625e852fd176a0d0aeebba20ba71ba9b9ebceb",
        "stage10_registry_source_sha256": "c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9",
        "stage10_resume_contract": "quant_data.stage10_backfill_resume",
        "stage10_resume_version": "1.1.0",
        "stage10_scope_manifest_sha256": "0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd",
        "stage10_target_profile_id": "stage10_fmp_market_history_v1",
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
_EXPECTED_TOP_LEVEL = frozenset(
    {
        "closed_capabilities",
        "collector",
        "contract",
        "fixture_plan",
        "publication",
        "stage12a_binding",
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
        raise _error(pointer, "shape", "Object fields differ from the reviewed Stage 12B scope")


def _text(value: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _error(pointer, "type", "Expected bounded nonempty trimmed text")
    if _ABSOLUTE_VALUE.fullmatch(value) is not None:
        raise _error(pointer, "operational_value", "Scope cannot contain an absolute path or URL")
    return value


def _exact_text(value: object, *, pointer: str, expected: str) -> str:
    parsed = _text(value, pointer=pointer, maximum=max(256, len(expected)))
    if parsed != expected:
        raise _error(pointer, "constant", "Value differs from the reviewed Stage 12B scope")
    return parsed


def _exact_int(value: object, *, pointer: str, expected: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _error(pointer, "constant", "Integer differs from the reviewed Stage 12B scope")
    return value


def _exact_bool(value: object, *, pointer: str, expected: bool) -> bool:
    if not isinstance(value, bool) or value is not expected:
        raise _error(pointer, "constant", "Boolean differs from the reviewed Stage 12B scope")
    return value


def _reject_unsafe_keys_and_values(value: object, *, pointer: str) -> None:
    """Reject undeclared credentials and operational locator material.

    The closed parser below supplies the stronger shape guarantee.  This pass
    is intentionally recursive so a hostile extra field cannot hide a URL or
    secret-like declaration before shape validation reports it.
    """

    if isinstance(value, Mapping):
        for key, child_value in value.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Scope object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            child = _child(pointer, key)
            if normalized in _FORBIDDEN_KEYS:
                raise _error(child, "forbidden_key", "Scope cannot declare credentials or locators")
            _reject_unsafe_keys_and_values(child_value, pointer=child)
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            _reject_unsafe_keys_and_values(child_value, pointer=_child(pointer, index))
    elif isinstance(value, str) and _ABSOLUTE_VALUE.fullmatch(value) is not None:
        raise _error(pointer, "operational_value", "Scope cannot contain an absolute path or URL")


@dataclass(frozen=True, slots=True)
class Stage12BStage12ABinding:
    scope_contract: str
    scope_manifest_sha256: str
    scope_version: str
    scope_file_sha256: str
    roster_sha256: str
    source_bindings: Mapping[str, str]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "frozen_roster": {
                "asset_type_counts": dict(_ASSET_TYPE_COUNTS),
                "price_variant": STAGE12B_PRICE_VARIANT,
                "provider": "fmp",
                "symbol_count": 629,
            },
            "roster_sha256": self.roster_sha256,
            "scope_contract": self.scope_contract,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "scope_version": self.scope_version,
            "scope_file_sha256": self.scope_file_sha256,
            "source_bindings": dict(self.source_bindings),
        }


@dataclass(frozen=True, slots=True)
class Stage12BCollectorPolicy:
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
            "configuration_env": [],
            "handler": self.handler,
            "id": self.id,
            "input_datasets": list(self.input_datasets),
            "mutation_policy": {
                "mode": "append_versions_and_move_current_projection",
                "unchanged": "zero_persistent_writes",
            },
            "network": False,
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
                "excludes": ["captured_at", "source_row_order"],
                "includes": [
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
class Stage12BFixturePlan:
    max_inclusive_calendar_days: int
    min_session_dates: int
    max_session_dates: int
    row_keys: tuple[str, ...]

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "mode": "offline_synthetic_fixture_only",
            "response": {
                "encoding": "utf-8",
                "exact_row_keys": list(self.row_keys),
                "media_type": "application/json",
                "top_level": "list",
            },
            "session_dates": {
                "calendar_lookup": "forbidden",
                "caller_supplied": "required",
                "maximum": self.max_session_dates,
                "minimum": self.min_session_dates,
            },
            "symbols_per_request": 1,
            "window": {"inclusive_max_calendar_days": self.max_inclusive_calendar_days},
        }


@dataclass(frozen=True, slots=True)
class Stage12BPublicationPolicy:
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
class Stage12BIncrementalMarketScope:
    contract: str
    version: str
    target_profile_id: str
    stage12a_binding: Stage12BStage12ABinding
    collector: Stage12BCollectorPolicy
    fixture_plan: Stage12BFixturePlan
    publication: Stage12BPublicationPolicy
    closed_capabilities: tuple[str, ...]
    manifest_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "closed_capabilities": list(self.closed_capabilities),
            "collector": self.collector.manifest_mapping(),
            "contract": self.contract,
            "fixture_plan": self.fixture_plan.manifest_mapping(),
            "publication": self.publication.manifest_mapping(),
            "stage12a_binding": self.stage12a_binding.manifest_mapping(),
            "target_profile_id": self.target_profile_id,
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.manifest_mapping(), "manifest_sha256": self.manifest_sha256}


def _parse_stage12a_binding(value: object) -> Stage12BStage12ABinding:
    raw = _mapping(value, pointer="/stage12a_binding")
    _closed(
        raw,
        pointer="/stage12a_binding",
        fields=frozenset(
            {
                "frozen_roster",
                "roster_sha256",
                "scope_contract",
                "scope_manifest_sha256",
                "scope_version",
                "scope_file_sha256",
                "source_bindings",
            }
        ),
    )
    frozen = _mapping(raw["frozen_roster"], pointer="/stage12a_binding/frozen_roster")
    _closed(
        frozen,
        pointer="/stage12a_binding/frozen_roster",
        fields=frozenset({"asset_type_counts", "price_variant", "provider", "symbol_count"}),
    )
    counts = _mapping(
        frozen["asset_type_counts"],
        pointer="/stage12a_binding/frozen_roster/asset_type_counts",
    )
    _closed(
        counts,
        pointer="/stage12a_binding/frozen_roster/asset_type_counts",
        fields=frozenset(_ASSET_TYPE_COUNTS),
    )
    for asset_type, count in _ASSET_TYPE_COUNTS.items():
        _exact_int(
            counts[asset_type],
            pointer=f"/stage12a_binding/frozen_roster/asset_type_counts/{asset_type}",
            expected=count,
        )
    _exact_text(
        frozen["price_variant"],
        pointer="/stage12a_binding/frozen_roster/price_variant",
        expected=STAGE12B_PRICE_VARIANT,
    )
    _exact_text(
        frozen["provider"],
        pointer="/stage12a_binding/frozen_roster/provider",
        expected="fmp",
    )
    _exact_int(
        frozen["symbol_count"],
        pointer="/stage12a_binding/frozen_roster/symbol_count",
        expected=629,
    )
    source_bindings = _mapping(raw["source_bindings"], pointer="/stage12a_binding/source_bindings")
    _closed(
        source_bindings,
        pointer="/stage12a_binding/source_bindings",
        fields=frozenset(_STAGE12A_SOURCE_BINDINGS),
    )
    parsed_sources: dict[str, str] = {}
    for name, expected in _STAGE12A_SOURCE_BINDINGS.items():
        parsed_sources[name] = _exact_text(
            source_bindings[name],
            pointer=f"/stage12a_binding/source_bindings/{name}",
            expected=expected,
        )
    return Stage12BStage12ABinding(
        scope_contract=_exact_text(
            raw["scope_contract"],
            pointer="/stage12a_binding/scope_contract",
            expected=STAGE12_SCOPE_CONTRACT,
        ),
        scope_manifest_sha256=_exact_text(
            raw["scope_manifest_sha256"],
            pointer="/stage12a_binding/scope_manifest_sha256",
            expected=REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256,
        ),
        scope_version=_exact_text(
            raw["scope_version"],
            pointer="/stage12a_binding/scope_version",
            expected=STAGE12_SCOPE_VERSION,
        ),
        scope_file_sha256=_exact_text(
            raw["scope_file_sha256"],
            pointer="/stage12a_binding/scope_file_sha256",
            expected=REVIEWED_STAGE12A_SCOPE_FILE_SHA256,
        ),
        roster_sha256=_exact_text(
            raw["roster_sha256"],
            pointer="/stage12a_binding/roster_sha256",
            expected=REVIEWED_STAGE12A_ROSTER_SHA256,
        ),
        source_bindings=MappingProxyType(parsed_sources),
    )


def _parse_collector(value: object) -> Stage12BCollectorPolicy:
    raw = _mapping(value, pointer="/collector")
    _closed(
        raw,
        pointer="/collector",
        fields=frozenset(
            {
                "workload_bounds",
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
            }
        ),
    )
    bounds = _mapping(raw["workload_bounds"], pointer="/collector/workload_bounds")
    _closed(bounds, pointer="/collector/workload_bounds", fields=frozenset({"max_bytes", "max_requests", "max_rows", "max_seconds"}))
    max_bytes = _exact_int(bounds["max_bytes"], pointer="/collector/workload_bounds/max_bytes", expected=65_536)
    max_requests = _exact_int(bounds["max_requests"], pointer="/collector/workload_bounds/max_requests", expected=1)
    max_rows = _exact_int(bounds["max_rows"], pointer="/collector/workload_bounds/max_rows", expected=5)
    max_seconds = _exact_int(bounds["max_seconds"], pointer="/collector/workload_bounds/max_seconds", expected=30)
    configuration_env = _array(raw["configuration_env"], pointer="/collector/configuration_env")
    if configuration_env:
        raise _error("/collector/configuration_env", "closed", "Fixture collector cannot declare configuration environment")
    input_datasets = tuple(
        _text(item, pointer=f"/collector/input_datasets/{index}")
        for index, item in enumerate(_array(raw["input_datasets"], pointer="/collector/input_datasets"))
    )
    output_datasets = tuple(
        _text(item, pointer=f"/collector/output_datasets/{index}")
        for index, item in enumerate(_array(raw["output_datasets"], pointer="/collector/output_datasets"))
    )
    if input_datasets != _INPUT_DATASETS or output_datasets != _OUTPUT_DATASETS:
        raise _error("/collector", "datasets", "Collector datasets differ from the reviewed Stage 12B declaration")
    mutation = _mapping(raw["mutation_policy"], pointer="/collector/mutation_policy")
    _closed(
        mutation,
        pointer="/collector/mutation_policy",
        fields=frozenset({"mode", "unchanged"}),
    )
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
    _exact_bool(raw["network"], pointer="/collector/network", expected=False)
    _exact_text(raw["physical_locks"], pointer="/collector/physical_locks", expected="derived_from_output_store_paths")
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
        raise _error("/collector/retry_policy/transient_classes", "closed", "Fixture collector cannot execute retries")
    schedule = _mapping(raw["schedule_eligibility"], pointer="/collector/schedule_eligibility")
    _closed(schedule, pointer="/collector/schedule_eligibility", fields=frozenset({"mode"}))
    _exact_text(schedule["mode"], pointer="/collector/schedule_eligibility/mode", expected="manual_only")
    semantic = _mapping(raw["semantic_identity"], pointer="/collector/semantic_identity")
    _closed(semantic, pointer="/collector/semantic_identity", fields=frozenset({"excludes", "includes"}))
    includes = tuple(_text(item, pointer=f"/collector/semantic_identity/includes/{index}") for index, item in enumerate(_array(semantic["includes"], pointer="/collector/semantic_identity/includes")))
    excludes = tuple(_text(item, pointer=f"/collector/semantic_identity/excludes/{index}") for index, item in enumerate(_array(semantic["excludes"], pointer="/collector/semantic_identity/excludes")))
    if includes != ("request_scope", "normalization_version", "normalized_complete_batch") or excludes != ("captured_at", "source_row_order"):
        raise _error("/collector/semantic_identity", "closed", "Semantic identity declaration differs from the reviewed Stage 12B policy")
    _exact_text(raw["version"], pointer="/collector/version", expected="1.0.0")
    return Stage12BCollectorPolicy(
        id=_exact_text(raw["id"], pointer="/collector/id", expected=STAGE12B_COLLECTOR_ID),
        handler=_exact_text(raw["handler"], pointer="/collector/handler", expected=STAGE12B_COLLECTOR_HANDLER),
        max_bytes=max_bytes,
        max_requests=max_requests,
        max_rows=max_rows,
        max_seconds=max_seconds,
        input_datasets=input_datasets,
        output_datasets=output_datasets,
    )


def _parse_fixture_plan(value: object) -> Stage12BFixturePlan:
    raw = _mapping(value, pointer="/fixture_plan")
    _closed(raw, pointer="/fixture_plan", fields=frozenset({"mode", "response", "session_dates", "symbols_per_request", "window"}))
    _exact_text(raw["mode"], pointer="/fixture_plan/mode", expected="offline_synthetic_fixture_only")
    _exact_int(raw["symbols_per_request"], pointer="/fixture_plan/symbols_per_request", expected=1)
    response = _mapping(raw["response"], pointer="/fixture_plan/response")
    _closed(response, pointer="/fixture_plan/response", fields=frozenset({"encoding", "exact_row_keys", "media_type", "top_level"}))
    _exact_text(response["encoding"], pointer="/fixture_plan/response/encoding", expected="utf-8")
    _exact_text(response["media_type"], pointer="/fixture_plan/response/media_type", expected="application/json")
    _exact_text(response["top_level"], pointer="/fixture_plan/response/top_level", expected="list")
    row_keys = tuple(_text(item, pointer=f"/fixture_plan/response/exact_row_keys/{index}") for index, item in enumerate(_array(response["exact_row_keys"], pointer="/fixture_plan/response/exact_row_keys")))
    if row_keys != _ROW_KEYS:
        raise _error("/fixture_plan/response/exact_row_keys", "closed", "Fixture row keys differ from the reviewed Stage 12B payload")
    session_dates = _mapping(raw["session_dates"], pointer="/fixture_plan/session_dates")
    _closed(session_dates, pointer="/fixture_plan/session_dates", fields=frozenset({"calendar_lookup", "caller_supplied", "maximum", "minimum"}))
    _exact_text(session_dates["calendar_lookup"], pointer="/fixture_plan/session_dates/calendar_lookup", expected="forbidden")
    _exact_text(session_dates["caller_supplied"], pointer="/fixture_plan/session_dates/caller_supplied", expected="required")
    minimum = _exact_int(session_dates["minimum"], pointer="/fixture_plan/session_dates/minimum", expected=1)
    maximum = _exact_int(session_dates["maximum"], pointer="/fixture_plan/session_dates/maximum", expected=5)
    window = _mapping(raw["window"], pointer="/fixture_plan/window")
    _closed(window, pointer="/fixture_plan/window", fields=frozenset({"inclusive_max_calendar_days"}))
    max_calendar_days = _exact_int(window["inclusive_max_calendar_days"], pointer="/fixture_plan/window/inclusive_max_calendar_days", expected=7)
    return Stage12BFixturePlan(
        max_inclusive_calendar_days=max_calendar_days,
        min_session_dates=minimum,
        max_session_dates=maximum,
        row_keys=row_keys,
    )


def _parse_publication(value: object) -> Stage12BPublicationPolicy:
    raw = _mapping(value, pointer="/publication")
    _closed(raw, pointer="/publication", fields=frozenset({"availability", "currency_segment", "existing_instrument", "normalization_version", "relations"}))
    availability = _exact_text(raw["availability"], pointer="/publication/availability", expected="local_capture_only")
    currency = _exact_text(raw["currency_segment"], pointer="/publication/currency_segment", expected="provider_native")
    _exact_text(raw["existing_instrument"], pointer="/publication/existing_instrument", expected="required")
    normalization = _exact_text(raw["normalization_version"], pointer="/publication/normalization_version", expected=STAGE12B_NORMALIZATION_VERSION)
    relations = tuple(_text(item, pointer=f"/publication/relations/{index}") for index, item in enumerate(_array(raw["relations"], pointer="/publication/relations")))
    if relations != ("stage10_daily_price_captures", "stage10_daily_price_versions", "stage10_daily_prices"):
        raise _error("/publication/relations", "closed", "Publication relations differ from the reviewed fixture boundary")
    return Stage12BPublicationPolicy(
        availability=availability,
        currency_segment=currency,
        normalization_version=normalization,
        relations=relations,
    )


def _parse_scope(value: object) -> Stage12BIncrementalMarketScope:
    raw = _mapping(value, pointer="/")
    _closed(raw, pointer="/", fields=_EXPECTED_TOP_LEVEL)
    closed_capabilities = tuple(
        _text(item, pointer=f"/closed_capabilities/{index}")
        for index, item in enumerate(_array(raw["closed_capabilities"], pointer="/closed_capabilities"))
    )
    expected_closed = (
        "calendar_lookup",
        "default_database_selection",
        "environment_credentials",
        "live_transport",
        "network",
        "provider_api",
        "scheduler",
        "stage10_request_replay",
    )
    if closed_capabilities != expected_closed or len(set(closed_capabilities)) != len(closed_capabilities):
        raise _error("/closed_capabilities", "closed", "Closed capabilities differ from the reviewed Stage 12B boundary")
    scope = Stage12BIncrementalMarketScope(
        contract=_exact_text(raw["contract"], pointer="/contract", expected=STAGE12B_SCOPE_CONTRACT),
        version=_exact_text(raw["version"], pointer="/version", expected=STAGE12B_SCOPE_VERSION),
        target_profile_id=_exact_text(raw["target_profile_id"], pointer="/target_profile_id", expected=STAGE12B_TARGET_PROFILE_ID),
        stage12a_binding=_parse_stage12a_binding(raw["stage12a_binding"]),
        collector=_parse_collector(raw["collector"]),
        fixture_plan=_parse_fixture_plan(raw["fixture_plan"]),
        publication=_parse_publication(raw["publication"]),
        closed_capabilities=closed_capabilities,
        manifest_sha256="",
    )
    if dumps_strict(raw) != dumps_strict(scope.manifest_mapping()):
        raise _error("/", "canonical", "Scope cannot be normalized to the reviewed closed schema")
    digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    if digest != REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256:
        raise _error("/", "immutable", "Scope differs from the reviewed immutable Stage 12B manifest")
    return replace(scope, manifest_sha256=digest)


def load_stage12b_incremental_market_v1_scope(
    source: str | Path,
) -> Stage12BIncrementalMarketScope:
    """Load one explicit, closed Stage 12B synthetic fixture scope."""

    if isinstance(source, bool) or not isinstance(source, (str, Path)):
        raise _error("/", "source", "An explicit Stage 12B scope file is required")
    if isinstance(source, str) and not source.strip():
        raise _error("/", "source", "An explicit Stage 12B scope file is required")
    try:
        payload = Path(source).read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "source", "Stage 12B scope file is unavailable") from exc
    raw = loads_strict(payload, max_bytes=_MAX_SCOPE_BYTES)
    _reject_unsafe_keys_and_values(raw, pointer="")
    return _parse_scope(raw)


def require_stage12b_scope(scope: object) -> Stage12BIncrementalMarketScope:
    """Revalidate an in-memory scope before any exported collector accepts it."""

    if not isinstance(scope, Stage12BIncrementalMarketScope):
        raise ValidationError("Stage 12B requires a reviewed incremental scope")
    try:
        digest = hashlib.sha256(
            dumps_strict(scope.manifest_mapping()).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValidationError("Stage 12B incremental scope is invalid") from exc
    if (
        scope.manifest_sha256 != REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256
        or digest != REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256
    ):
        raise ValidationError("Stage 12B incremental scope binding is invalid")
    return scope


def require_stage12a_binding(
    scope: Stage12BIncrementalMarketScope,
    stage12a_scope: object,
    *,
    scope_source: str | Path,
) -> Stage12MarketV1Scope:
    """Bind a fixture plan to the exact Stage 12A frozen authority object."""

    scope = require_stage12b_scope(scope)
    if not isinstance(stage12a_scope, Stage12MarketV1Scope):
        raise ValidationError("Stage 12B requires an explicit reviewed Stage 12A scope")
    if isinstance(scope_source, bool) or not isinstance(scope_source, (str, Path)):
        raise ValidationError("Stage 12B requires the explicit Stage 12A scope source")
    try:
        scope_file_sha256 = hashlib.sha256(Path(scope_source).read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        raise ValidationError("Stage 12B Stage 12A scope source is unavailable") from exc
    stage12a_manifest = stage12a_scope.manifest_mapping()
    stage12a_digest = hashlib.sha256(dumps_strict(stage12a_manifest).encode("utf-8")).hexdigest()
    roster_digest = hashlib.sha256(
        dumps_strict(
            [instrument.manifest_mapping() for instrument in stage12a_scope.roster]
        ).encode("utf-8")
    ).hexdigest()
    if (
        stage12a_scope.contract != STAGE12_SCOPE_CONTRACT
        or scope_file_sha256 != scope.stage12a_binding.scope_file_sha256
        or scope_file_sha256 != REVIEWED_STAGE12A_SCOPE_FILE_SHA256
        or stage12a_scope.version != STAGE12_SCOPE_VERSION
        or stage12a_scope.manifest_sha256 != REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256
        or stage12a_digest != REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256
        or stage12a_scope.roster_sha256 != REVIEWED_STAGE12A_ROSTER_SHA256
        or roster_digest != REVIEWED_STAGE12A_ROSTER_SHA256
        or len(stage12a_scope.roster) != 629
        or stage12a_scope.baseline.provider != "fmp"
        or stage12a_scope.baseline.price_variant != STAGE12B_PRICE_VARIANT
        or stage12a_scope.source_bindings.manifest_mapping() != dict(_STAGE12A_SOURCE_BINDINGS)
    ):
        raise ValidationError("Stage 12B Stage 12A authority binding is invalid")
    counts = {
        asset_type: sum(item.asset_type == asset_type for item in stage12a_scope.roster)
        for asset_type in _ASSET_TYPE_COUNTS
    }
    if counts != dict(_ASSET_TYPE_COUNTS):
        raise ValidationError("Stage 12B Stage 12A roster classifications are invalid")
    return stage12a_scope


__all__ = (
    "REVIEWED_STAGE12A_ROSTER_SHA256",
    "REVIEWED_STAGE12B_INCREMENTAL_MARKET_V1_SCOPE_SHA256",
    "STAGE12B_COLLECTOR_HANDLER",
    "STAGE12B_COLLECTOR_ID",
    "STAGE12B_NORMALIZATION_VERSION",
    "STAGE12B_PRICE_VARIANT",
    "STAGE12B_SCOPE_CONTRACT",
    "STAGE12B_SCOPE_VERSION",
    "STAGE12B_TARGET_PROFILE_ID",
    "Stage12BCollectorPolicy",
    "Stage12BFixturePlan",
    "Stage12BIncrementalMarketScope",
    "Stage12BPublicationPolicy",
    "Stage12BStage12ABinding",
    "load_stage12b_incremental_market_v1_scope",
    "require_stage12a_binding",
    "require_stage12b_scope",
)
