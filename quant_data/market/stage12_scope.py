"""Strict immutable loader for the offline Stage 12A Market v1 scope.

The manifest freezes only retained Stage 10 coverage facts and the next
authorized decision boundary.  It deliberately contains no credentials,
provider endpoint, filesystem target, or operational instruction.
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


STAGE12_SCOPE_CONTRACT = "quant_data.stage12_market_v1_scope"
STAGE12_SCOPE_VERSION = "1.0.0"
STAGE12_TARGET_PROFILE_ID = "stage12_market_v1_authority_coverage"

# This SHA-256 is calculated over the closed manifest rendered by
# ``dumps_strict``.  It pins every authority and coverage claim regardless of
# whitespace or JSON object-key order in the source file.
REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256 = (
    "8266f431519913879797a14ea0baa781b6c7e0dfe78365a5ff0107e386a0ad29"
)

_MAX_SCOPE_BYTES = 64 * 1024
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
_PHASE_ID = re.compile(r"^12[B-E]$")
_SYMBOL = re.compile(r"^(?:[A-Z][A-Z0-9.-]{0,14}|\^[A-Z][A-Z0-9]{0,14})$")
_SENSITIVE_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "apikey",
    "api_key",
    "header",
    "environment",
    "endpoint",
    "host",
    "query",
    "url",
    "uri",
    "path",
    "root",
    "filename",
    "filepath",
)

_EXPECTED_BASELINE = {
    "coverage_end_date": "2026-08-12",
    "frozen_symbol_count": 629,
    "history_policy": "earliest_provider_returned_per_symbol",
    "price_data": "fmp_daily_provider_native_ohlcv",
    "price_variant": "fmp_full_eod_v1",
    "provider": "fmp",
    "roster_policy": "frozen_stage10_629_symbol_roster",
}
_EXPECTED_BASE = {"completed_symbols": 621, "retained_failures": 8}
_EXPECTED_EXTENSION = {
    "authorized_terminal_outcomes": 58,
    "completed_windows": 3_970,
    "planned_windows": 5_032,
    "successful_empty_windows": 1_004,
    "terminal_outcome_tickers": 9,
}
_EXPECTED_PROJECTION = {
    "current_rows": 4_235_893,
    "missing_current_references": 0,
    "version_rows": 4_236_635,
}
_EXPECTED_STORE_DEFAULTS = {
    "company": "data/company_data.sqlite",
    "macro": "data/macro_data.sqlite",
    "market": "data/market.sqlite",
    "news": "data/news_data.sqlite",
}
_EXPECTED_EXCLUDED_CLAIMS = (
    "historical_constituent_membership",
    "common_start_date",
    "adjusted_price_or_corporate_action_inference",
    "full_us_or_delisted_coverage",
    "currency_conversion",
    "incremental_refresh",
    "promotion",
    "scheduling",
)
_EXPECTED_SUCCESSOR_PHASES = (
    ("12B", "incremental_market_collector"),
    ("12C", "gap_only_new_scope_population"),
    ("12D", "audited_project_local_operationalization_repeated_manual_evidence"),
    ("12E", "separate_scheduler_proposal"),
)
_EXPECTED_PHASE_STATUS = "separate_authorization_required"
_EXPECTED_ASSET_TYPE_COUNTS = {"equity": 519, "etf": 95, "index": 15}
_FORBIDDEN_ROSTER_SYMBOLS = frozenset({"IWM", "^RUT"})
_EXPECTED_SOURCE_BINDINGS = {
    "resume_artifact_sha256": "1c0a9f941d829170e4085ef133df6343dadf32d975168ce43aa3724749553795",
    "stage10_failure_manifest_sha256": "1b0b2a64ca302b1c5c411f927d625e852fd176a0d0aeebba20ba71ba9b9ebceb",
    "stage10_registry_source_sha256": "c64aceefcc9a37cc0669398ea4d5817d997a5d82167941a204c2b77fffce77f9",
    "stage10_resume_contract": "quant_data.stage10_backfill_resume",
    "stage10_resume_version": "1.1.0",
    "stage10_scope_manifest_sha256": "0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd",
    "stage10_target_profile_id": "stage10_fmp_market_history_v1",
}


@dataclass(frozen=True, slots=True)
class Stage12MarketBaseline:
    coverage_end_date: str
    frozen_symbol_count: int
    history_policy: str
    price_data: str
    price_variant: str
    provider: str
    roster_policy: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "coverage_end_date": self.coverage_end_date,
            "frozen_symbol_count": self.frozen_symbol_count,
            "history_policy": self.history_policy,
            "price_data": self.price_data,
            "price_variant": self.price_variant,
            "provider": self.provider,
            "roster_policy": self.roster_policy,
        }


@dataclass(frozen=True, slots=True)
class Stage12RetainedBase:
    completed_symbols: int
    retained_failures: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "completed_symbols": self.completed_symbols,
            "retained_failures": self.retained_failures,
        }


@dataclass(frozen=True, slots=True)
class Stage12RetainedExtension:
    authorized_terminal_outcomes: int
    completed_windows: int
    planned_windows: int
    successful_empty_windows: int
    terminal_outcome_tickers: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "authorized_terminal_outcomes": self.authorized_terminal_outcomes,
            "completed_windows": self.completed_windows,
            "planned_windows": self.planned_windows,
            "successful_empty_windows": self.successful_empty_windows,
            "terminal_outcome_tickers": self.terminal_outcome_tickers,
        }


@dataclass(frozen=True, slots=True)
class Stage12RetainedProjection:
    current_rows: int
    missing_current_references: int
    version_rows: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "current_rows": self.current_rows,
            "missing_current_references": self.missing_current_references,
            "version_rows": self.version_rows,
        }


@dataclass(frozen=True, slots=True)
class Stage12RetainedStage10:
    base: Stage12RetainedBase
    extension: Stage12RetainedExtension
    projection: Stage12RetainedProjection

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "base": self.base.manifest_mapping(),
            "extension": self.extension.manifest_mapping(),
            "projection": self.projection.manifest_mapping(),
        }


@dataclass(frozen=True, slots=True)
class Stage12SuccessorPhase:
    id: str
    scope: str
    status: str

    def manifest_mapping(self) -> dict[str, str]:
        return {"id": self.id, "scope": self.scope, "status": self.status}


@dataclass(frozen=True, slots=True)
class Stage12RosterInstrument:
    symbol: str
    asset_type: str

    def manifest_mapping(self) -> dict[str, str]:
        return {"symbol": self.symbol, "asset_type": self.asset_type}


@dataclass(frozen=True, slots=True)
class Stage12SourceBindings:
    resume_artifact_sha256: str
    stage10_failure_manifest_sha256: str
    stage10_registry_source_sha256: str
    stage10_resume_contract: str
    stage10_resume_version: str
    stage10_scope_manifest_sha256: str
    stage10_target_profile_id: str

    def manifest_mapping(self) -> dict[str, str]:
        return {
            "resume_artifact_sha256": self.resume_artifact_sha256,
            "stage10_failure_manifest_sha256": self.stage10_failure_manifest_sha256,
            "stage10_registry_source_sha256": self.stage10_registry_source_sha256,
            "stage10_resume_contract": self.stage10_resume_contract,
            "stage10_resume_version": self.stage10_resume_version,
            "stage10_scope_manifest_sha256": self.stage10_scope_manifest_sha256,
            "stage10_target_profile_id": self.stage10_target_profile_id,
        }


@dataclass(frozen=True, slots=True)
class Stage12MarketV1Scope:
    baseline: Stage12MarketBaseline
    contract: str
    excluded_claims: tuple[str, ...]
    retained_stage10: Stage12RetainedStage10
    roster: tuple[Stage12RosterInstrument, ...]
    roster_sha256: str
    source_bindings: Stage12SourceBindings
    store_defaults: Mapping[str, str]
    successor_phases: tuple[Stage12SuccessorPhase, ...]
    target_profile_id: str
    version: str
    manifest_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.manifest_mapping(),
            "contract": self.contract,
            "excluded_claims": list(self.excluded_claims),
            "retained_stage10": self.retained_stage10.manifest_mapping(),
            "roster": [instrument.manifest_mapping() for instrument in self.roster],
            "source_bindings": self.source_bindings.manifest_mapping(),
            "store_defaults": dict(self.store_defaults),
            "successor_phases": [phase.manifest_mapping() for phase in self.successor_phases],
            "target_profile_id": self.target_profile_id,
            "version": self.version,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.manifest_mapping(),
            "roster_sha256": self.roster_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer or "/", rule, message),))


def _child(pointer: str, part: str | int) -> str:
    return f"{pointer}/{part}" if pointer else f"/{part}"


def _mapping(raw: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or not all(isinstance(key, str) for key in raw):
        raise _error(pointer, "type", "Expected an object with string keys")
    return raw


def _array(raw: object, *, pointer: str) -> list[Any]:
    if not isinstance(raw, list):
        raise _error(pointer, "type", "Expected an array")
    return raw


def _closed_object(raw: Mapping[str, Any], *, pointer: str, fields: frozenset[str]) -> None:
    if set(raw) != fields:
        raise _error(pointer, "shape", "Object fields do not match the reviewed Stage 12A scope")


def _text(raw: object, *, pointer: str, maximum: int = 256) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or raw != raw.strip()
        or len(raw) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise _error(pointer, "type", "Expected bounded nonempty trimmed text")
    return raw


def _exact_text(raw: object, *, pointer: str, expected: str) -> str:
    value = _text(raw, pointer=pointer, maximum=max(256, len(expected)))
    if value != expected:
        raise _error(pointer, "constant", "Value differs from the reviewed Stage 12A scope")
    return value


def _exact_int(raw: object, *, pointer: str, expected: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw != expected:
        raise _error(pointer, "constant", "Integer differs from the reviewed Stage 12A scope")
    return raw


def _token(raw: object, *, pointer: str, expected: str) -> str:
    value = _exact_text(raw, pointer=pointer, expected=expected)
    if _TOKEN.fullmatch(value) is None:
        raise _error(pointer, "format", "Reviewed identifier is invalid")
    return value


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _reject_sensitive_or_operational_keys(raw: object, *, pointer: str) -> None:
    """Reject credentials and unreviewed operational declarations recursively."""

    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if not isinstance(key, str):
                raise _error(pointer, "type", "Object keys must be strings")
            child = _child(pointer, key)
            normalized = _normalized_key(key)
            if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
                raise _error(child, "forbidden_key", "Scope cannot declare secrets or operational targets")
            _reject_sensitive_or_operational_keys(value, pointer=child)
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            _reject_sensitive_or_operational_keys(value, pointer=_child(pointer, index))


def _parse_exact_mapping(
    raw: object,
    *,
    pointer: str,
    expected: Mapping[str, str | int],
) -> dict[str, str | int]:
    value = _mapping(raw, pointer=pointer)
    _closed_object(value, pointer=pointer, fields=frozenset(expected))
    parsed: dict[str, str | int] = {}
    for name, exact in expected.items():
        child = _child(pointer, name)
        if isinstance(exact, int):
            parsed[name] = _exact_int(value[name], pointer=child, expected=exact)
        else:
            parsed[name] = _exact_text(value[name], pointer=child, expected=exact)
    return parsed


def _parse_baseline(raw: object) -> Stage12MarketBaseline:
    values = _parse_exact_mapping(raw, pointer="/baseline", expected=_EXPECTED_BASELINE)
    return Stage12MarketBaseline(
        coverage_end_date=str(values["coverage_end_date"]),
        frozen_symbol_count=int(values["frozen_symbol_count"]),
        history_policy=str(values["history_policy"]),
        price_data=str(values["price_data"]),
        price_variant=str(values["price_variant"]),
        provider=_token(values["provider"], pointer="/baseline/provider", expected="fmp"),
        roster_policy=str(values["roster_policy"]),
    )


def _parse_retained_stage10(raw: object) -> Stage12RetainedStage10:
    value = _mapping(raw, pointer="/retained_stage10")
    _closed_object(value, pointer="/retained_stage10", fields=frozenset({"base", "extension", "projection"}))
    base = _parse_exact_mapping(value["base"], pointer="/retained_stage10/base", expected=_EXPECTED_BASE)
    extension = _parse_exact_mapping(value["extension"], pointer="/retained_stage10/extension", expected=_EXPECTED_EXTENSION)
    projection = _parse_exact_mapping(value["projection"], pointer="/retained_stage10/projection", expected=_EXPECTED_PROJECTION)
    result = Stage12RetainedStage10(
        base=Stage12RetainedBase(
            completed_symbols=int(base["completed_symbols"]),
            retained_failures=int(base["retained_failures"]),
        ),
        extension=Stage12RetainedExtension(
            authorized_terminal_outcomes=int(extension["authorized_terminal_outcomes"]),
            completed_windows=int(extension["completed_windows"]),
            planned_windows=int(extension["planned_windows"]),
            successful_empty_windows=int(extension["successful_empty_windows"]),
            terminal_outcome_tickers=int(extension["terminal_outcome_tickers"]),
        ),
        projection=Stage12RetainedProjection(
            current_rows=int(projection["current_rows"]),
            missing_current_references=int(projection["missing_current_references"]),
            version_rows=int(projection["version_rows"]),
        ),
    )
    if result.base.completed_symbols + result.base.retained_failures != _EXPECTED_BASELINE["frozen_symbol_count"]:
        raise _error("/retained_stage10/base", "reconcile", "Retained base outcomes do not close the frozen roster")
    if (
        result.extension.completed_windows
        + result.extension.successful_empty_windows
        + result.extension.authorized_terminal_outcomes
        != result.extension.planned_windows
    ):
        raise _error("/retained_stage10/extension", "reconcile", "Retained extension outcomes do not close planned windows")
    if result.projection.version_rows < result.projection.current_rows:
        raise _error("/retained_stage10/projection", "reconcile", "Version rows cannot be fewer than current rows")
    return result


def _parse_roster(raw: object) -> tuple[Stage12RosterInstrument, ...]:
    values = _array(raw, pointer="/roster")
    expected_count = sum(_EXPECTED_ASSET_TYPE_COUNTS.values())
    if len(values) != expected_count:
        raise _error("/roster", "count", "Stage 12A roster must contain exactly 629 instruments")
    roster: list[Stage12RosterInstrument] = []
    for index, entry in enumerate(values):
        pointer = _child("/roster", index)
        value = _mapping(entry, pointer=pointer)
        _closed_object(value, pointer=pointer, fields=frozenset({"asset_type", "symbol"}))
        symbol = _text(value["symbol"], pointer=_child(pointer, "symbol"), maximum=16)
        if _SYMBOL.fullmatch(symbol) is None:
            raise _error(_child(pointer, "symbol"), "format", "Roster symbol is invalid")
        asset_type = _text(value["asset_type"], pointer=_child(pointer, "asset_type"), maximum=16)
        if asset_type not in _EXPECTED_ASSET_TYPE_COUNTS:
            raise _error(_child(pointer, "asset_type"), "asset_type", "Roster asset type is not reviewed")
        roster.append(Stage12RosterInstrument(symbol=symbol, asset_type=asset_type))
    symbols = tuple(item.symbol for item in roster)
    if len(set(symbols)) != len(symbols) or symbols != tuple(sorted(symbols)):
        raise _error("/roster", "order", "Roster symbols must be unique and sorted")
    if set(symbols).intersection(_FORBIDDEN_ROSTER_SYMBOLS):
        raise _error("/roster", "excluded_symbol", "Roster contains an explicitly excluded Stage 10 symbol")
    counts = {
        asset_type: sum(item.asset_type == asset_type for item in roster)
        for asset_type in _EXPECTED_ASSET_TYPE_COUNTS
    }
    if counts != _EXPECTED_ASSET_TYPE_COUNTS:
        raise _error("/roster", "coverage", "Roster asset-type counts differ from the reviewed frozen coverage")
    return tuple(roster)


def _parse_source_bindings(raw: object) -> Stage12SourceBindings:
    values = _parse_exact_mapping(
        raw,
        pointer="/source_bindings",
        expected=_EXPECTED_SOURCE_BINDINGS,
    )
    return Stage12SourceBindings(
        resume_artifact_sha256=str(values["resume_artifact_sha256"]),
        stage10_failure_manifest_sha256=str(values["stage10_failure_manifest_sha256"]),
        stage10_registry_source_sha256=str(values["stage10_registry_source_sha256"]),
        stage10_resume_contract=str(values["stage10_resume_contract"]),
        stage10_resume_version=str(values["stage10_resume_version"]),
        stage10_scope_manifest_sha256=str(values["stage10_scope_manifest_sha256"]),
        stage10_target_profile_id=str(values["stage10_target_profile_id"]),
    )


def _parse_store_defaults(raw: object) -> Mapping[str, str]:
    values = _parse_exact_mapping(raw, pointer="/store_defaults", expected=_EXPECTED_STORE_DEFAULTS)
    defaults = {name: str(values[name]) for name in _EXPECTED_STORE_DEFAULTS}
    if any(value.startswith("/") or ".." in Path(value).parts or "\\" in value for value in defaults.values()):
        raise _error("/store_defaults", "path", "Store defaults must be reviewed project-relative paths")
    return MappingProxyType(defaults)


def _parse_excluded_claims(raw: object) -> tuple[str, ...]:
    values = _array(raw, pointer="/excluded_claims")
    parsed = tuple(_text(value, pointer=_child("/excluded_claims", index)) for index, value in enumerate(values))
    if parsed != _EXPECTED_EXCLUDED_CLAIMS or len(set(parsed)) != len(parsed):
        raise _error("/excluded_claims", "immutable", "Excluded claims differ from the reviewed Stage 12A boundary")
    return parsed


def _parse_successor_phases(raw: object) -> tuple[Stage12SuccessorPhase, ...]:
    values = _array(raw, pointer="/successor_phases")
    if len(values) != len(_EXPECTED_SUCCESSOR_PHASES):
        raise _error("/successor_phases", "count", "Stage 12A requires exactly four successor phases")
    phases: list[Stage12SuccessorPhase] = []
    for index, (expected_id, expected_scope) in enumerate(_EXPECTED_SUCCESSOR_PHASES):
        pointer = _child("/successor_phases", index)
        item = _mapping(values[index], pointer=pointer)
        _closed_object(item, pointer=pointer, fields=frozenset({"id", "scope", "status"}))
        phase_id = _exact_text(item["id"], pointer=_child(pointer, "id"), expected=expected_id)
        if _PHASE_ID.fullmatch(phase_id) is None:
            raise _error(_child(pointer, "id"), "format", "Successor phase identity is invalid")
        phases.append(
            Stage12SuccessorPhase(
                id=phase_id,
                scope=_token(item["scope"], pointer=_child(pointer, "scope"), expected=expected_scope),
                status=_token(item["status"], pointer=_child(pointer, "status"), expected=_EXPECTED_PHASE_STATUS),
            )
        )
    if tuple((phase.id, phase.scope) for phase in phases) != _EXPECTED_SUCCESSOR_PHASES:
        raise _error("/successor_phases", "immutable", "Successor phases differ from the reviewed order")
    return tuple(phases)


def _parse_scope(raw: object) -> Stage12MarketV1Scope:
    value = _mapping(raw, pointer="/")
    _closed_object(
        value,
        pointer="/",
        fields=frozenset(
            {
                "baseline",
                "contract",
                "excluded_claims",
                "retained_stage10",
                "roster",
                "source_bindings",
                "store_defaults",
                "successor_phases",
                "target_profile_id",
                "version",
            }
        ),
    )
    roster = _parse_roster(value["roster"])
    scope = Stage12MarketV1Scope(
        baseline=_parse_baseline(value["baseline"]),
        contract=_exact_text(value["contract"], pointer="/contract", expected=STAGE12_SCOPE_CONTRACT),
        excluded_claims=_parse_excluded_claims(value["excluded_claims"]),
        retained_stage10=_parse_retained_stage10(value["retained_stage10"]),
        roster=roster,
        roster_sha256="",
        source_bindings=_parse_source_bindings(value["source_bindings"]),
        store_defaults=_parse_store_defaults(value["store_defaults"]),
        successor_phases=_parse_successor_phases(value["successor_phases"]),
        target_profile_id=_token(value["target_profile_id"], pointer="/target_profile_id", expected=STAGE12_TARGET_PROFILE_ID),
        version=_exact_text(value["version"], pointer="/version", expected=STAGE12_SCOPE_VERSION),
        manifest_sha256="",
    )
    if len(scope.roster) != scope.baseline.frozen_symbol_count:
        raise _error("/roster", "coverage", "Roster count does not match the frozen baseline")
    if dumps_strict(value) != dumps_strict(scope.manifest_mapping()):
        raise _error("/", "canonical", "Scope cannot be normalized to the reviewed closed schema")
    roster_sha256 = hashlib.sha256(
        dumps_strict([instrument.manifest_mapping() for instrument in scope.roster]).encode("utf-8")
    ).hexdigest()
    digest = hashlib.sha256(dumps_strict(scope.manifest_mapping()).encode("utf-8")).hexdigest()
    if digest != REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256:
        raise _error("/", "immutable", "Scope differs from the reviewed immutable Stage 12A manifest")
    return replace(scope, roster_sha256=roster_sha256, manifest_sha256=digest)


def load_stage12_market_v1_scope(path: str | Path) -> Stage12MarketV1Scope:
    """Load one explicit, closed-schema Stage 12A Market v1 scope file."""

    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise _error("/", "path", "An explicit Stage 12A scope path is required")
    if isinstance(path, str) and not path.strip():
        raise _error("/", "path", "An explicit Stage 12A scope path is required")
    try:
        payload = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise _error("/", "path", "Stage 12A scope file is unavailable") from exc
    raw = loads_strict(payload, max_bytes=_MAX_SCOPE_BYTES)
    _reject_sensitive_or_operational_keys(raw, pointer="")
    return _parse_scope(raw)


__all__ = (
    "REVIEWED_STAGE12_MARKET_V1_SCOPE_SHA256",
    "STAGE12_SCOPE_CONTRACT",
    "STAGE12_SCOPE_VERSION",
    "STAGE12_TARGET_PROFILE_ID",
    "Stage12MarketBaseline",
    "Stage12MarketV1Scope",
    "Stage12RetainedBase",
    "Stage12RetainedExtension",
    "Stage12RetainedProjection",
    "Stage12RetainedStage10",
    "Stage12RosterInstrument",
    "Stage12SourceBindings",
    "Stage12SuccessorPhase",
    "load_stage12_market_v1_scope",
)
