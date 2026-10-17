"""Read-only v2 access to retained Alpaca option-surface cohorts.

This module intentionally sits beside the fixture-only Stage 4 reader rather
than extending it.  The live Alpaca grid uses the same canonical relations,
but its retained cohorts are ``paper``/``alpaca_indicative`` and its fixed
15-ETF universe has different identity and capture rules.  Each operation
therefore chooses one coherent capture cohort at a time; it never joins option
rows, quote inputs, or dates across captures.

The public adapter accepts only typed/mapping arguments supplied by the host.
It never accepts a database path, connection, SQL, provider endpoint, or
credential.  The repository opens the market store solely through the
established immutable reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
import sqlite3
from typing import Any, Final

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.market.alpaca_options import (
    ALPACA_ENVIRONMENT,
    ALPACA_ETF_OPTIONS_DTE_TARGETS,
    ALPACA_ETF_OPTIONS_UNIVERSE,
    ALPACA_PROVIDER,
    ALPACA_REQUESTED_FEED,
    ALPACA_RESOLVED_FEED,
    ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
    ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
)
from quant_data.registry import Registry
from quant_data.stores import StoreMap, StoreRole, quiet_immutable_read_connection
from quant_data.temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)

from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    Scalar,
    fields_from_mapping,
    records_from_mappings,
)


OPTIONS_V2_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "options.search_captures",
        "options.search_contracts",
        "options.get_surface_snapshot",
    }
)
OPTIONS_V2_OPERATION_VERSION: Final = "2.0.0"

# Public preflight charges cover the complete worst-case retained workload,
# independently of the caller's output limit.  They count bounded candidate
# and target-membership evaluations rather than Python instructions:
#
# * capture search: 10k captures + 10 target-DTE memberships per capture;
# * contract search: the capture workload plus 20k surface candidates; and
# * surface snapshot: capture filters/selections, 5k surfaces, 20k
#   observations per table, and conservative bounded headroom.
OPTIONS_V2_OPERATION_CHARGES: Final[Mapping[str, int]] = {
    "options.search_captures": 110_000,
    "options.search_contracts": 130_000,
    "options.get_surface_snapshot": 80_000,
}

_MAX_CAPTURE_RESULTS: Final = 500
_MAX_CONTRACT_RESULTS: Final = 2_000
_MAX_SURFACE_RESULTS: Final = 5_000
_MAX_CAPTURE_CANDIDATES: Final = 10_000
_MAX_CONTRACT_CANDIDATES: Final = 20_000
_MAX_SCOPE_BYTES: Final = 16_384
_MAX_TEXT_LENGTH: Final = 256
_OPTION_TYPES: Final = frozenset({"call", "put"})
_SURFACE_STATES: Final = frozenset({"present", "missing", "excluded"})
_REQUIRED_DATASETS: Final = frozenset(
    {
        "fixture.market.instruments",
        ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
        ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
    }
)
_UNDERLYING_ORDER: Final = {
    symbol: index for index, symbol in enumerate(ALPACA_ETF_OPTIONS_UNIVERSE)
}


def _invalid(message: str) -> ValidationError:
    return ValidationError(f"Options v2 {message}")


def _bounded_text(value: object, *, label: str, maximum: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _invalid(f"{label} is invalid")
    return value


def _optional_text(value: object, *, label: str, maximum: int = _MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, label=label, maximum=maximum)


def _date(value: object, *, label: str) -> str:
    text = _bounded_text(value, label=label, maximum=10)
    try:
        parsed = parse_date(text, pointer=f"/{label}")
    except ValidationError as exc:
        raise _invalid(f"{label} is invalid") from exc
    if parsed.isoformat() != text:
        raise _invalid(f"{label} is invalid")
    return text


def _policy(value: object) -> DateOnlyPolicy:
    try:
        return DateOnlyPolicy(value)
    except (TypeError, ValueError) as exc:
        raise _invalid("date_only_policy is invalid") from exc


def _cutoff(
    *, mode: object, as_of: object, date_only_policy: object
) -> tuple[str, TemporalValue | None, DateOnlyPolicy]:
    if mode not in {"latest", "as_of"}:
        raise _invalid("mode is invalid")
    policy = _policy(date_only_policy)
    if mode == "latest":
        if as_of is not None:
            raise _invalid("latest mode does not accept as_of")
        return "latest", None, policy
    if not isinstance(as_of, str) or not as_of:
        raise _invalid("as_of mode requires a cutoff")
    try:
        parsed = TemporalValue.parse(as_of, pointer="/as_of")
    except ValidationError as exc:
        raise _invalid("as_of is invalid") from exc
    if parsed.precision not in {TemporalPrecision.DATE, TemporalPrecision.DATETIME}:
        raise _invalid("as_of is invalid")
    return "as_of", parsed, policy


def _limit(value: object, *, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise _invalid(f"{label} must be between 1 and {maximum}")
    return value


def _argument_mapping(
    arguments: object,
    *,
    allowed: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise _invalid("arguments must be a mapping")
    unknown = set(arguments).difference(allowed)
    if unknown:
        raise _invalid("arguments contain unsupported fields")
    if not all(isinstance(key, str) for key in arguments):
        raise _invalid("argument keys are invalid")
    return arguments


def _underlying_symbol(value: object, *, label: str) -> str:
    symbol = _bounded_text(value, label=label, maximum=16)
    if symbol not in _UNDERLYING_ORDER:
        raise _invalid(f"{label} is outside the fixed ETF universe")
    return symbol


def _underlying_symbols(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)) or len(value) > len(ALPACA_ETF_OPTIONS_UNIVERSE):
        raise _invalid("underlying_symbols is invalid")
    symbols = tuple(
        _underlying_symbol(item, label="underlying_symbols") for item in value
    )
    if len(symbols) != len(set(symbols)):
        raise _invalid("underlying_symbols must be distinct")
    return symbols


def _option_type(value: object) -> str | None:
    if value is None:
        return None
    result = _bounded_text(value, label="option_type", maximum=8)
    if result not in _OPTION_TYPES:
        raise _invalid("option_type is invalid")
    return result


def _surface_state(value: object) -> str | None:
    if value is None:
        return None
    result = _bounded_text(value, label="surface_state", maximum=16)
    if result not in _SURFACE_STATES:
        raise _invalid("surface_state is invalid")
    return result


def _target_dte(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALPACA_ETF_OPTIONS_DTE_TARGETS:
        raise _invalid("target_dte is invalid")
    return value


def _cursor(value: object) -> None:
    if value is not None:
        raise _invalid("cursor pagination is not implemented for options v2")


@dataclass(frozen=True, slots=True)
class OptionsCaptureSearchQuery:
    underlying_symbols: tuple[str, ...]
    mode: str
    cutoff: TemporalValue | None
    date_only_policy: DateOnlyPolicy
    limit: int


@dataclass(frozen=True, slots=True)
class OptionsContractSearchQuery:
    underlying_symbol: str
    query: str
    expiration_date: str | None
    option_type: str | None
    mode: str
    cutoff: TemporalValue | None
    date_only_policy: DateOnlyPolicy
    limit: int


@dataclass(frozen=True, slots=True)
class OptionsSurfaceSnapshotQuery:
    underlying_symbol: str
    capture_id: str | None
    target_dte: int | None
    expiration_date: str | None
    option_type: str | None
    surface_state: str | None
    mode: str
    cutoff: TemporalValue | None
    date_only_policy: DateOnlyPolicy
    limit: int


def parse_capture_search_query(arguments: object) -> OptionsCaptureSearchQuery:
    """Validate the bounded current/as-of capture-search request."""

    material = _argument_mapping(
        arguments,
        allowed=frozenset(
            {
                "underlying_symbols",
                "mode",
                "as_of",
                "date_only_policy",
                "cursor",
                "limit",
            }
        ),
    )
    _cursor(material.get("cursor"))
    mode, cutoff, policy = _cutoff(
        mode=material.get("mode", "latest"),
        as_of=material.get("as_of"),
        date_only_policy=material.get("date_only_policy", DateOnlyPolicy.COMPLETED_DATE.value),
    )
    return OptionsCaptureSearchQuery(
        underlying_symbols=_underlying_symbols(material.get("underlying_symbols", ())),
        mode=mode,
        cutoff=cutoff,
        date_only_policy=policy,
        limit=_limit(
            material.get("limit", _MAX_CAPTURE_RESULTS),
            maximum=_MAX_CAPTURE_RESULTS,
            label="capture-search limit",
        ),
    )


def parse_contract_search_query(arguments: object) -> OptionsContractSearchQuery:
    """Validate the bounded current/as-of standard-contract search request."""

    material = _argument_mapping(
        arguments,
        allowed=frozenset(
            {
                "underlying_symbol",
                "query",
                "expiration_date",
                "option_type",
                "mode",
                "as_of",
                "date_only_policy",
                "limit",
            }
        ),
    )
    query = material.get("query", "")
    if not isinstance(query, str) or len(query) > _MAX_TEXT_LENGTH:
        raise _invalid("query is invalid")
    mode, cutoff, policy = _cutoff(
        mode=material.get("mode", "latest"),
        as_of=material.get("as_of"),
        date_only_policy=material.get("date_only_policy", DateOnlyPolicy.COMPLETED_DATE.value),
    )
    expiration = material.get("expiration_date")
    return OptionsContractSearchQuery(
        underlying_symbol=_underlying_symbol(
            material.get("underlying_symbol"), label="underlying_symbol"
        ),
        query=query.strip(),
        expiration_date=None if expiration is None else _date(expiration, label="expiration_date"),
        option_type=_option_type(material.get("option_type")),
        mode=mode,
        cutoff=cutoff,
        date_only_policy=policy,
        limit=_limit(
            material.get("limit", _MAX_CONTRACT_RESULTS),
            maximum=_MAX_CONTRACT_RESULTS,
            label="contract-search limit",
        ),
    )


def parse_surface_snapshot_query(arguments: object) -> OptionsSurfaceSnapshotQuery:
    """Validate a bounded request for exactly one coherent surface capture."""

    material = _argument_mapping(
        arguments,
        allowed=frozenset(
            {
                "underlying_symbol",
                "capture_id",
                "target_dte",
                "expiration_date",
                "option_type",
                "surface_state",
                "mode",
                "as_of",
                "date_only_policy",
                "limit",
            }
        ),
    )
    mode, cutoff, policy = _cutoff(
        mode=material.get("mode", "latest"),
        as_of=material.get("as_of"),
        date_only_policy=material.get("date_only_policy", DateOnlyPolicy.COMPLETED_DATE.value),
    )
    capture_id = _optional_text(material.get("capture_id"), label="capture_id")
    expiration = material.get("expiration_date")
    return OptionsSurfaceSnapshotQuery(
        underlying_symbol=_underlying_symbol(
            material.get("underlying_symbol"), label="underlying_symbol"
        ),
        capture_id=capture_id,
        target_dte=_target_dte(material.get("target_dte")),
        expiration_date=None if expiration is None else _date(expiration, label="expiration_date"),
        option_type=_option_type(material.get("option_type")),
        surface_state=_surface_state(material.get("surface_state")),
        mode=mode,
        cutoff=cutoff,
        date_only_policy=policy,
        limit=_limit(
            material.get("limit", _MAX_SURFACE_RESULTS),
            maximum=_MAX_SURFACE_RESULTS,
            label="surface limit",
        ),
    )


@dataclass(frozen=True, slots=True)
class _CaptureScope:
    underlying_symbol: str
    session_date: str
    selected_expiration: str
    target_dtes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Capture:
    capture_id: str
    semantic_identity: str
    underlying_instrument_id: str
    underlying_symbol: str
    requested_feed: str
    resolved_feed: str
    environment: str
    requested_at: str
    completed_at: str
    available_at: str
    available_precision: str
    completeness: str
    deliverable_policy: str
    artifact_id: str
    source_snapshot_id: str
    scope: _CaptureScope


@dataclass(frozen=True, slots=True)
class OptionsSelection:
    """One bounded, deterministic retained-options selection."""

    records: tuple[Mapping[str, Scalar], ...]
    captures: tuple[_Capture, ...]
    total_selected_count: int
    truncated: bool
    warnings: tuple[str, ...]


def _scope_from_row(row: Mapping[str, Any]) -> _CaptureScope:
    try:
        raw_scope = loads_strict(str(row["request_scope_json"]), max_bytes=_MAX_SCOPE_BYTES)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ConflictError("Option capture scope is not valid retained JSON") from exc
    if not isinstance(raw_scope, Mapping) or not all(isinstance(key, str) for key in raw_scope):
        raise ConflictError("Option capture scope is not a retained object")
    symbol = raw_scope.get("underlying_symbol")
    if not isinstance(symbol, str) or symbol not in _UNDERLYING_ORDER:
        raise ConflictError("Option capture scope has an unsupported underlying")
    session = raw_scope.get("session_date")
    expiration = raw_scope.get("selected_expiration")
    try:
        session_date = _date(session, label="stored_session_date")
        selected_expiration = _date(expiration, label="stored_selected_expiration")
    except ValidationError as exc:
        raise ConflictError("Option capture scope has an invalid date") from exc
    if raw_scope.get("requested_feed") != ALPACA_REQUESTED_FEED:
        raise ConflictError("Option capture scope has an unexpected requested feed")
    if raw_scope.get("resolved_feed") != ALPACA_RESOLVED_FEED:
        raise ConflictError("Option capture scope has an unexpected resolved feed")
    if raw_scope.get("environment") != ALPACA_ENVIRONMENT:
        raise ConflictError("Option capture scope has an unexpected environment")
    if raw_scope.get("deliverable_policy") != "standard_only":
        raise ConflictError("Option capture scope does not enforce standard deliverables")
    has_single_target = "target_dte" in raw_scope
    has_target_set = "target_dtes" in raw_scope
    if has_single_target == has_target_set:
        raise ConflictError("Option capture scope has ambiguous target-DTE coverage")
    raw_targets = (
        (raw_scope["target_dte"],)
        if has_single_target
        else raw_scope["target_dtes"]
    )
    if not isinstance(raw_targets, (tuple, list)) or not raw_targets:
        raise ConflictError("Option capture scope target-DTE coverage is invalid")
    targets = tuple(raw_targets)
    if (
        any(isinstance(target, bool) or not isinstance(target, int) for target in targets)
        or any(target not in ALPACA_ETF_OPTIONS_DTE_TARGETS for target in targets)
        or tuple(sorted(set(targets))) != targets
    ):
        raise ConflictError("Option capture scope target-DTE coverage is invalid")
    return _CaptureScope(
        underlying_symbol=symbol,
        session_date=session_date,
        selected_expiration=selected_expiration,
        target_dtes=targets,
    )


def _capture_from_row(row: Mapping[str, Any]) -> _Capture:
    scope = _scope_from_row(row)
    underlying_symbol = row["canonical_symbol"]
    if underlying_symbol != scope.underlying_symbol:
        raise ConflictError("Option capture scope does not match its canonical underlying")
    fixed = {
        "requested_feed": ALPACA_REQUESTED_FEED,
        "resolved_feed": ALPACA_RESOLVED_FEED,
        "environment": ALPACA_ENVIRONMENT,
        "deliverable_policy": "standard_only",
    }
    for name, expected in fixed.items():
        if row[name] != expected:
            raise ConflictError("Option capture violates the fixed Alpaca cohort boundary")
    completed_precision = row["completed_precision"]
    if completed_precision != "datetime":
        raise ConflictError("Option capture completion precision is invalid")
    _stored_temporal(row, value_field="completed_at", precision_field="completed_precision")
    _stored_temporal(row, value_field="available_at", precision_field="available_precision")
    return _Capture(
        capture_id=_stored_text(row, "capture_id"),
        semantic_identity=_stored_text(row, "semantic_identity"),
        underlying_instrument_id=_stored_text(row, "underlying_instrument_id"),
        underlying_symbol=scope.underlying_symbol,
        requested_feed=ALPACA_REQUESTED_FEED,
        resolved_feed=ALPACA_RESOLVED_FEED,
        environment=ALPACA_ENVIRONMENT,
        requested_at=_stored_text(row, "requested_at"),
        completed_at=_stored_text(row, "completed_at"),
        available_at=_stored_text(row, "available_at"),
        available_precision=_stored_text(row, "available_precision"),
        completeness=_stored_text(row, "completeness"),
        deliverable_policy="standard_only",
        artifact_id=_stored_text(row, "artifact_id"),
        source_snapshot_id=_stored_text(row, "source_snapshot_id"),
        scope=scope,
    )


def _stored_text(row: Mapping[str, Any], name: str) -> str:
    value = row[name]
    if not isinstance(value, str) or not value:
        raise ConflictError(f"Retained option {name} is invalid")
    return value


def _stored_temporal(
    row: Mapping[str, Any], *, value_field: str, precision_field: str
) -> TemporalValue:
    value = _stored_text(row, value_field)
    precision = _stored_text(row, precision_field)
    try:
        parsed = TemporalValue.parse(value, pointer=f"/stored/{value_field}")
    except ValidationError as exc:
        raise ConflictError("Retained option temporal value is invalid") from exc
    if parsed.precision.value != precision:
        raise ConflictError("Retained option temporal precision is inconsistent")
    return parsed


def _instant(value: str) -> datetime:
    try:
        parsed = TemporalValue.parse(value, pointer="/stored/completed_at")
    except ValidationError as exc:
        raise ConflictError("Retained option completion timestamp is invalid") from exc
    if parsed.precision is not TemporalPrecision.DATETIME or not isinstance(parsed.value, datetime):
        raise ConflictError("Retained option completion timestamp is invalid")
    return parsed.value.astimezone(timezone.utc)


def _available(
    row: Mapping[str, Any],
    *,
    cutoff: TemporalValue | None,
    date_only_policy: DateOnlyPolicy,
    warnings: set[str],
) -> bool:
    if cutoff is None:
        return True
    decision = availability_at_or_before(
        _stored_temporal(row, value_field="available_at", precision_field="available_precision"),
        cutoff,
        date_only_policy,
    )
    warnings.update(decision.warnings)
    return decision.included


def _scalar(value: object) -> Scalar:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ConflictError("Retained option numeric value is non-finite")
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ConflictError("Retained option numeric value is non-finite")
        return Decimal(str(value))
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ConflictError("Retained option value is not a public scalar")


def _decimal_sort(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ConflictError(f"Retained option {label} is invalid")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConflictError(f"Retained option {label} is invalid") from exc
    if not decimal.is_finite():
        raise ConflictError(f"Retained option {label} is invalid")
    return decimal


def _capture_record(capture: _Capture) -> dict[str, Scalar]:
    return {
        "artifact_id": capture.artifact_id,
        "available_at": capture.available_at,
        "available_precision": capture.available_precision,
        "capture_id": capture.capture_id,
        "completed_at": capture.completed_at,
        "completeness": capture.completeness,
        "deliverable_policy": capture.deliverable_policy,
        "environment": capture.environment,
        "requested_at": capture.requested_at,
        "requested_feed": capture.requested_feed,
        "resolved_feed": capture.resolved_feed,
        "selected_expiration": capture.scope.selected_expiration,
        "semantic_identity": capture.semantic_identity,
        "session_date": capture.scope.session_date,
        "source_snapshot_id": capture.source_snapshot_id,
        "target_dtes": dumps_strict(list(capture.scope.target_dtes)),
        "underlying_instrument_id": capture.underlying_instrument_id,
        "underlying_symbol": capture.underlying_symbol,
    }


def _deduplicated_lineage(captures: Sequence[_Capture]) -> tuple[LineageRef, ...]:
    result: list[LineageRef] = []
    for capture in sorted(captures, key=lambda item: item.capture_id):
        result.append(
            LineageRef(
                dataset_id=ALPACA_SPY_OPTIONS_EVIDENCE_DATASET_ID,
                store_role=StoreRole.MARKET.value,
                semantic_id=capture.capture_id,
                evidence_id=capture.artifact_id,
                snapshot_id=capture.source_snapshot_id,
            )
        )
        result.append(
            LineageRef(
                dataset_id=ALPACA_SPY_OPTIONS_CANONICAL_DATASET_ID,
                store_role=StoreRole.MARKET.value,
                semantic_id=capture.source_snapshot_id,
                evidence_id=capture.artifact_id,
                snapshot_id=capture.source_snapshot_id,
                canonical_version_id=capture.source_snapshot_id,
            )
        )
    return tuple(result)


class OptionsV2Repository:
    """Host-routed immutable access to the fixed Alpaca paper/indicative grid."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(registry, Registry):
            raise _invalid("repository dependencies are invalid")
        registered = {
            dataset.id for dataset in registry.datasets_for(StoreRole.MARKET.value)
        }
        if not _REQUIRED_DATASETS.issubset(registered):
            raise _invalid("repository is not bound to the canonical option datasets")
        self._store_map = store_map

    def _capture_candidates(
        self,
        connection: sqlite3.Connection,
        *,
        symbols: tuple[str, ...],
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
        selected_expiration: str | None = None,
    ) -> list[_Capture]:
        rows = list(
            connection.execute(
                """
                SELECT capture.*, instrument.canonical_symbol
                FROM option_surface_captures AS capture
                JOIN instruments AS instrument
                  ON instrument.instrument_id = capture.underlying_instrument_id
                WHERE capture.requested_feed=?
                  AND capture.resolved_feed=?
                  AND capture.environment=?
                  AND capture.deliverable_policy='standard_only'
                ORDER BY capture.completed_at DESC, capture.capture_id DESC
                LIMIT ?
                """,
                (
                    ALPACA_REQUESTED_FEED,
                    ALPACA_RESOLVED_FEED,
                    ALPACA_ENVIRONMENT,
                    _MAX_CAPTURE_CANDIDATES + 1,
                ),
            )
        )
        if len(rows) > _MAX_CAPTURE_CANDIDATES:
            raise ResourceLimitError(
                "Retained option capture history exceeds the supported candidate bound"
            )
        allowed_symbols = set(symbols) if symbols else set(ALPACA_ETF_OPTIONS_UNIVERSE)
        result: list[_Capture] = []
        for row in rows:
            canonical_symbol = row["canonical_symbol"]
            if canonical_symbol not in allowed_symbols:
                continue
            capture = _capture_from_row(row)
            if capture.underlying_symbol not in allowed_symbols:
                continue
            if selected_expiration is not None and capture.scope.selected_expiration != selected_expiration:
                continue
            if not _available(
                row,
                cutoff=cutoff,
                date_only_policy=date_only_policy,
                warnings=warnings,
            ):
                continue
            result.append(capture)
        return result

    @staticmethod
    def _latest_by_target(captures: Sequence[_Capture]) -> tuple[_Capture, ...]:
        selected: dict[tuple[str, int], _Capture] = {}
        for capture in captures:
            for target_dte in capture.scope.target_dtes:
                key = (capture.underlying_symbol, target_dte)
                previous = selected.get(key)
                if previous is None or (
                    _instant(capture.completed_at),
                    capture.capture_id,
                ) > (
                    _instant(previous.completed_at),
                    previous.capture_id,
                ):
                    selected[key] = capture
        unique = {capture.capture_id: capture for capture in selected.values()}
        return tuple(
            sorted(
                unique.values(),
                key=lambda capture: (
                    _UNDERLYING_ORDER[capture.underlying_symbol],
                    min(capture.scope.target_dtes),
                    capture.scope.selected_expiration,
                    capture.capture_id,
                ),
            )
        )

    def search_captures(
        self, query: OptionsCaptureSearchQuery
    ) -> OptionsSelection:
        """Select the latest eligible capture for each fixed target-DTE cohort."""

        warnings: set[str] = set()
        with quiet_immutable_read_connection(
            self._store_map, StoreRole.MARKET
        ) as connection:
            candidates = self._capture_candidates(
                connection,
                symbols=query.underlying_symbols,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            )
        captures = self._latest_by_target(candidates)
        total = len(captures)
        returned = captures[: query.limit]
        return OptionsSelection(
            records=tuple(_capture_record(capture) for capture in returned),
            captures=returned,
            total_selected_count=total,
            truncated=total > query.limit,
            warnings=tuple(sorted(warnings)),
        )

    @staticmethod
    def _capture_ids(captures: Sequence[_Capture]) -> tuple[str, ...]:
        return tuple(capture.capture_id for capture in captures)

    @staticmethod
    def _surface_rows_for_captures(
        connection: sqlite3.Connection,
        captures: Sequence[_Capture],
    ) -> list[sqlite3.Row]:
        capture_ids = OptionsV2Repository._capture_ids(captures)
        if not capture_ids:
            return []
        placeholders = ",".join("?" for _ in capture_ids)
        rows = list(
            connection.execute(
                f"""
                SELECT surface.*, contract.provider, contract.provider_contract_id,
                       contract.contract_symbol, contract.expiration_date,
                       contract.strike_price, contract.option_type,
                       contract.deliverable_kind, contract.contract_multiplier,
                       contract.contract_status,
                       contract.available_at AS contract_available_at,
                       contract.available_precision AS contract_available_precision
                FROM option_surface_snapshots AS surface
                JOIN option_contracts AS contract
                  ON contract.contract_id = surface.contract_id
                WHERE surface.capture_id IN ({placeholders})
                  AND contract.provider=?
                ORDER BY surface.capture_id, contract.option_type,
                         contract.strike_price, contract.provider_contract_id,
                         surface.surface_snapshot_id
                LIMIT ?
                """,
                (*capture_ids, ALPACA_PROVIDER, _MAX_CONTRACT_CANDIDATES + 1),
            )
        )
        if len(rows) > _MAX_CONTRACT_CANDIDATES:
            raise ResourceLimitError(
                "Retained option-contract history exceeds the supported candidate bound"
            )
        return rows

    @staticmethod
    def _row_capture(
        capture_by_id: Mapping[str, _Capture], row: Mapping[str, Any]
    ) -> _Capture:
        capture_id = _stored_text(row, "capture_id")
        capture = capture_by_id.get(capture_id)
        if capture is None:
            raise ConflictError("Option surface row is outside its selected capture cohort")
        if row["provider"] != ALPACA_PROVIDER or row["deliverable_kind"] not in {
            "standard",
            "nonstandard",
        }:
            raise ConflictError("Option surface row violates the fixed deliverable boundary")
        if row["contract_status"] not in {"active", "inactive", "expired"}:
            raise ConflictError("Retained option contract status is invalid")
        if row["expiration_date"] != capture.scope.selected_expiration:
            raise ConflictError(
                "Option contract expiration is outside its selected capture scope"
            )
        return capture

    @staticmethod
    def _row_available(
        row: Mapping[str, Any],
        *,
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
    ) -> bool:
        contract_available = {
            "available_at": row["contract_available_at"],
            "available_precision": row["contract_available_precision"],
        }
        return _available(
            row,
            cutoff=cutoff,
            date_only_policy=date_only_policy,
            warnings=warnings,
        ) and _available(
            contract_available,
            cutoff=cutoff,
            date_only_policy=date_only_policy,
            warnings=warnings,
        )

    @staticmethod
    def _contract_record(capture: _Capture, row: Mapping[str, Any]) -> dict[str, Scalar]:
        return {
            "available_at": _scalar(row["available_at"]),
            "available_precision": _scalar(row["available_precision"]),
            "capture_id": capture.capture_id,
            "completed_at": capture.completed_at,
            "contract_available_at": _scalar(row["contract_available_at"]),
            "contract_available_precision": _scalar(row["contract_available_precision"]),
            "contract_id": _scalar(row["contract_id"]),
            "deliverable_kind": _scalar(row["deliverable_kind"]),
            "contract_multiplier": _scalar(row["contract_multiplier"]),
            "contract_status": _scalar(row["contract_status"]),
            "contract_symbol": _scalar(row["contract_symbol"]),
            "environment": capture.environment,
            "expiration_date": _scalar(row["expiration_date"]),
            "option_type": _scalar(row["option_type"]),
            "provider": _scalar(row["provider"]),
            "provider_contract_id": _scalar(row["provider_contract_id"]),
            "resolved_feed": capture.resolved_feed,
            "selected_expiration": capture.scope.selected_expiration,
            "semantic_identity": capture.semantic_identity,
            "strike_price": _scalar(row["strike_price"]),
            "surface_exclusion_reason": _scalar(row["exclusion_reason"]),
            "surface_missing_reason": _scalar(row["missing_reason"]),
            "surface_snapshot_id": _scalar(row["surface_snapshot_id"]),
            "surface_state": _scalar(row["surface_state"]),
            "target_dtes": dumps_strict(list(capture.scope.target_dtes)),
            "underlying_symbol": capture.underlying_symbol,
        }

    def search_contracts(
        self, query: OptionsContractSearchQuery
    ) -> OptionsSelection:
        """Search retained contracts from the latest eligible capture per target DTE.

        Standard contracts are usable retained members.  A nonstandard contract
        is still returned when present so its persisted ``excluded`` or
        ``missing`` state and reason remain visible instead of being silently
        dropped.
        """

        warnings: set[str] = set()
        with quiet_immutable_read_connection(
            self._store_map, StoreRole.MARKET
        ) as connection:
            candidates = self._capture_candidates(
                connection,
                symbols=(query.underlying_symbol,),
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
                selected_expiration=query.expiration_date,
            )
            captures = self._latest_by_target(candidates)
            rows = self._surface_rows_for_captures(connection, captures)
        capture_by_id = {capture.capture_id: capture for capture in captures}
        needle = query.query.casefold()
        selected: list[tuple[_Capture, sqlite3.Row]] = []
        for row in rows:
            capture = self._row_capture(capture_by_id, row)
            if not self._row_available(
                row,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            ):
                continue
            if query.option_type is not None and row["option_type"] != query.option_type:
                continue
            if query.expiration_date is not None and row["expiration_date"] != query.expiration_date:
                continue
            haystack = "\n".join(
                (str(row["contract_symbol"]), str(row["provider_contract_id"]))
            ).casefold()
            if needle and needle not in haystack:
                continue
            selected.append((capture, row))
        selected.sort(
            key=lambda item: (
                _UNDERLYING_ORDER[item[0].underlying_symbol],
                item[0].scope.selected_expiration,
                str(item[1]["option_type"]),
                _decimal_sort(item[1]["strike_price"], label="strike_price"),
                str(item[1]["provider_contract_id"]),
                str(item[1]["surface_snapshot_id"]),
            )
        )
        total = len(selected)
        returned = selected[: query.limit]
        return OptionsSelection(
            records=tuple(
                self._contract_record(capture, row) for capture, row in returned
            ),
            captures=tuple(
                capture
                for capture_id, capture in sorted(
                    {
                        capture.capture_id: capture for capture, _ in returned
                    }.items()
                )
            ),
            total_selected_count=total,
            truncated=total > query.limit,
            warnings=tuple(sorted(warnings)),
        )

    @staticmethod
    def _required_one(
        connection: sqlite3.Connection,
        *,
        table: str,
        capture_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            f"SELECT * FROM {table} WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if row is None:
            raise ConflictError(f"Option capture is missing its retained {table} record")
        return row

    @staticmethod
    def _require_component_available(
        row: Mapping[str, Any],
        *,
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
        label: str,
    ) -> None:
        if not _available(
            row,
            cutoff=cutoff,
            date_only_policy=date_only_policy,
            warnings=warnings,
        ):
            raise ConflictError(
                f"Selected option capture {label} is unavailable at the requested cutoff"
            )

    def _surface_components(
        self,
        connection: sqlite3.Connection,
        *,
        capture: _Capture,
        expiration_date: str,
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
    ) -> dict[str, sqlite3.Row]:
        underlying = self._required_one(
            connection,
            table="option_capture_underlyings",
            capture_id=capture.capture_id,
        )
        quote = self._required_one(
            connection,
            table="option_capture_underlying_quotes",
            capture_id=capture.capture_id,
        )
        curve = self._required_one(
            connection,
            table="option_capture_rate_curves",
            capture_id=capture.capture_id,
        )
        dividends = self._required_one(
            connection,
            table="option_capture_dividend_sets",
            capture_id=capture.capture_id,
        )
        expiry = connection.execute(
            """
            SELECT * FROM option_capture_expiry_inputs
            WHERE capture_id=? AND expiration_date=?
            """,
            (capture.capture_id, expiration_date),
        ).fetchone()
        if expiry is None:
            raise ConflictError("Option capture is missing its selected-expiry input record")
        for label, row in (
            ("underlying quote", quote),
            ("rate curve", curve),
            ("dividend set", dividends),
            ("expiry input", expiry),
        ):
            self._require_component_available(
                row,
                cutoff=cutoff,
                date_only_policy=date_only_policy,
                warnings=warnings,
                label=label,
            )
        if underlying["underlying_instrument_id"] != capture.underlying_instrument_id:
            raise ConflictError("Option capture underlying record is incoherent")
        return {
            "underlying": underlying,
            "quote": quote,
            "curve": curve,
            "dividends": dividends,
            "expiry": expiry,
        }

    @staticmethod
    def _latest_observations_for_capture(
        connection: sqlite3.Connection,
        *,
        capture_id: str,
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
    ) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row]]:
        selections: list[dict[str, sqlite3.Row]] = []
        for table, order_fields in (
            (
                "option_open_interest",
                "as_of_date DESC, source_row DESC, open_interest_id DESC",
            ),
            (
                "option_close_prices",
                "trade_date DESC, source_row DESC, close_price_id DESC",
            ),
        ):
            count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE capture_id=?",
                    (capture_id,),
                ).fetchone()[0]
            )
            if count > _MAX_CONTRACT_CANDIDATES:
                raise ResourceLimitError(
                    "Retained option observations exceed the supported candidate bound"
                )
            rows = connection.execute(
                f"""
                SELECT * FROM {table}
                WHERE capture_id=?
                ORDER BY contract_id, {order_fields}
                """,
                (capture_id,),
            ).fetchall()
            selected: dict[str, sqlite3.Row] = {}
            for row in rows:
                contract_id = _stored_text(row, "contract_id")
                if contract_id in selected or not _available(
                    row,
                    cutoff=cutoff,
                    date_only_policy=date_only_policy,
                    warnings=warnings,
                ):
                    continue
                selected[contract_id] = row
            selections.append(selected)
        return selections[0], selections[1]

    @classmethod
    def _surface_rows_for_capture(
        cls,
        connection: sqlite3.Connection,
        capture: _Capture,
        *,
        cutoff: TemporalValue | None,
        date_only_policy: DateOnlyPolicy,
        warnings: set[str],
    ) -> list[Mapping[str, Any]]:
        surface_rows = list(
            connection.execute(
                """
                SELECT surface.*, contract.provider, contract.provider_contract_id,
                       contract.contract_symbol, contract.expiration_date,
                       contract.strike_price, contract.option_type,
                       contract.deliverable_kind, contract.contract_multiplier,
                       contract.contract_status,
                       contract.available_at AS contract_available_at,
                       contract.available_precision AS contract_available_precision
                FROM option_surface_snapshots AS surface
                JOIN option_contracts AS contract
                  ON contract.contract_id = surface.contract_id
                WHERE surface.capture_id=?
                  AND contract.provider=?
                ORDER BY contract.option_type, contract.strike_price,
                         contract.provider_contract_id, surface.surface_snapshot_id
                LIMIT ?
                """,
                (
                    capture.capture_id,
                    ALPACA_PROVIDER,
                    _MAX_SURFACE_RESULTS + 1,
                ),
            )
        )
        if len(surface_rows) > _MAX_SURFACE_RESULTS:
            raise ResourceLimitError(
                "Selected option capture exceeds the supported surface bound"
            )
        interests, closes = cls._latest_observations_for_capture(
            connection,
            capture_id=capture.capture_id,
            cutoff=cutoff,
            date_only_policy=date_only_policy,
            warnings=warnings,
        )
        rows: list[Mapping[str, Any]] = []
        for surface in surface_rows:
            contract_id = _stored_text(surface, "contract_id")
            interest = interests.get(contract_id)
            close = closes.get(contract_id)
            row = dict(surface)
            row.update(
                {
                    "open_interest_id": None if interest is None else interest["open_interest_id"],
                    "open_interest_as_of_date": None if interest is None else interest["as_of_date"],
                    "open_interest": None if interest is None else interest["open_interest"],
                    "open_interest_state": None if interest is None else interest["observation_state"],
                    "open_interest_missing_reason": None if interest is None else interest["missing_reason"],
                    "open_interest_available_at": None if interest is None else interest["available_at"],
                    "open_interest_available_precision": None if interest is None else interest["available_precision"],
                    "close_price_id": None if close is None else close["close_price_id"],
                    "close_trade_date": None if close is None else close["trade_date"],
                    "close_price": None if close is None else close["close_price"],
                    "close_state": None if close is None else close["observation_state"],
                    "close_missing_reason": None if close is None else close["missing_reason"],
                    "close_available_at": None if close is None else close["available_at"],
                    "close_available_precision": None if close is None else close["available_precision"],
                }
            )
            rows.append(row)
        return rows

    @staticmethod
    def _component_value(
        row: Mapping[str, Any], name: str
    ) -> Scalar:
        return _scalar(row[name])

    def _surface_record(
        self,
        *,
        capture: _Capture,
        row: Mapping[str, Any],
        components: Mapping[str, sqlite3.Row],
    ) -> dict[str, Scalar]:
        underlying = components["underlying"]
        quote = components["quote"]
        curve = components["curve"]
        dividends = components["dividends"]
        expiry = components["expiry"]
        if row["open_interest_id"] is None or row["close_price_id"] is None:
            raise ConflictError("Option surface is missing a retained observation record")
        return {
            "ask_price": self._component_value(row, "ask_price"),
            "ask_size": self._component_value(row, "ask_size"),
            "available_at": self._component_value(row, "available_at"),
            "available_precision": self._component_value(row, "available_precision"),
            "bid_price": self._component_value(row, "bid_price"),
            "bid_size": self._component_value(row, "bid_size"),
            "capture_available_at": capture.available_at,
            "capture_available_precision": capture.available_precision,
            "capture_id": capture.capture_id,
            "close_available_at": self._component_value(row, "close_available_at"),
            "close_available_precision": self._component_value(row, "close_available_precision"),
            "close_missing_reason": self._component_value(row, "close_missing_reason"),
            "close_price": self._component_value(row, "close_price"),
            "close_price_id": self._component_value(row, "close_price_id"),
            "close_state": self._component_value(row, "close_state"),
            "close_trade_date": self._component_value(row, "close_trade_date"),
            "completed_at": capture.completed_at,
            "contract_id": self._component_value(row, "contract_id"),
            "deliverable_kind": self._component_value(row, "deliverable_kind"),
            "contract_multiplier": self._component_value(row, "contract_multiplier"),
            "contract_status": self._component_value(row, "contract_status"),
            "contract_symbol": self._component_value(row, "contract_symbol"),
            "delta": self._component_value(row, "delta"),
            "deliverable_policy": capture.deliverable_policy,
            "dividend_available_at": self._component_value(dividends, "available_at"),
            "dividend_available_precision": self._component_value(dividends, "available_precision"),
            "dividend_missing_reason": self._component_value(dividends, "missing_reason"),
            "dividend_source_name": self._component_value(dividends, "source_name"),
            "dividend_state": self._component_value(dividends, "input_state"),
            "dividend_yield": self._component_value(expiry, "dividend_yield"),
            "environment": capture.environment,
            "expiry_available_at": self._component_value(expiry, "available_at"),
            "expiry_available_precision": self._component_value(expiry, "available_precision"),
            "expiry_input_id": self._component_value(expiry, "expiry_input_id"),
            "expiry_missing_reason": self._component_value(expiry, "missing_reason"),
            "expiry_state": self._component_value(expiry, "input_state"),
            "expiration_date": self._component_value(row, "expiration_date"),
            "forward_price": self._component_value(expiry, "forward_price"),
            "gamma": self._component_value(row, "gamma"),
            "implied_volatility": self._component_value(row, "implied_volatility"),
            "last_price": self._component_value(row, "last_price"),
            "last_size": self._component_value(row, "last_size"),
            "open_interest": self._component_value(row, "open_interest"),
            "open_interest_as_of_date": self._component_value(row, "open_interest_as_of_date"),
            "open_interest_available_at": self._component_value(row, "open_interest_available_at"),
            "open_interest_available_precision": self._component_value(row, "open_interest_available_precision"),
            "open_interest_id": self._component_value(row, "open_interest_id"),
            "open_interest_missing_reason": self._component_value(row, "open_interest_missing_reason"),
            "open_interest_state": self._component_value(row, "open_interest_state"),
            "option_type": self._component_value(row, "option_type"),
            "provider": self._component_value(row, "provider"),
            "provider_contract_id": self._component_value(row, "provider_contract_id"),
            "quote_at": self._component_value(row, "quote_at"),
            "quote_precision": self._component_value(row, "quote_precision"),
            "rate_curve_available_at": self._component_value(curve, "available_at"),
            "rate_curve_available_precision": self._component_value(curve, "available_precision"),
            "rate_curve_date": self._component_value(curve, "curve_date"),
            "rate_curve_missing_reason": self._component_value(curve, "missing_reason"),
            "rate_curve_source_name": self._component_value(curve, "source_name"),
            "rate_curve_state": self._component_value(curve, "input_state"),
            "requested_feed": capture.requested_feed,
            "resolved_feed": capture.resolved_feed,
            "rho": self._component_value(row, "rho"),
            "risk_free_rate": self._component_value(expiry, "risk_free_rate"),
            "selected_expiration": capture.scope.selected_expiration,
            "semantic_identity": capture.semantic_identity,
            "spot_price": self._component_value(expiry, "spot_price"),
            "strike_price": self._component_value(row, "strike_price"),
            "surface_exclusion_reason": self._component_value(row, "exclusion_reason"),
            "surface_missing_reason": self._component_value(row, "missing_reason"),
            "surface_snapshot_id": self._component_value(row, "surface_snapshot_id"),
            "surface_state": self._component_value(row, "surface_state"),
            "target_dtes": dumps_strict(list(capture.scope.target_dtes)),
            "theta": self._component_value(row, "theta"),
            "trade_at": self._component_value(row, "trade_at"),
            "trade_precision": self._component_value(row, "trade_precision"),
            "underlying_missing_reason": self._component_value(underlying, "missing_reason"),
            "underlying_observed_at": self._component_value(underlying, "observed_at"),
            "underlying_observed_precision": self._component_value(underlying, "observed_precision"),
            "underlying_quote_ask_price": self._component_value(quote, "ask_price"),
            "underlying_quote_available_at": self._component_value(quote, "available_at"),
            "underlying_quote_available_precision": self._component_value(quote, "available_precision"),
            "underlying_quote_bid_price": self._component_value(quote, "bid_price"),
            "underlying_quote_last_price": self._component_value(quote, "last_price"),
            "underlying_quote_missing_reason": self._component_value(quote, "missing_reason"),
            "underlying_quote_state": self._component_value(quote, "input_state"),
            "underlying_quote_trade_price": self._component_value(quote, "trade_price"),
            "underlying_state": self._component_value(underlying, "underlying_state"),
            "underlying_symbol": capture.underlying_symbol,
            "vega": self._component_value(row, "vega"),
            "volume": self._component_value(row, "volume"),
        }

    def get_surface_snapshot(
        self, query: OptionsSurfaceSnapshotQuery
    ) -> OptionsSelection:
        """Return rows from one eligible capture only, never a blended surface."""

        warnings: set[str] = set()
        with quiet_immutable_read_connection(
            self._store_map, StoreRole.MARKET
        ) as connection:
            candidates = self._capture_candidates(
                connection,
                symbols=(query.underlying_symbol,),
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
                selected_expiration=query.expiration_date,
            )
            if query.target_dte is not None:
                candidates = [
                    capture
                    for capture in candidates
                    if query.target_dte in capture.scope.target_dtes
                ]
            if query.capture_id is not None:
                candidates = [
                    capture
                    for capture in candidates
                    if capture.capture_id == query.capture_id
                ]
                if not candidates:
                    raise ConflictError(
                        "Requested option capture is not in the selected fixed cohort"
                    )
            if not candidates:
                return OptionsSelection((), (), 0, False, tuple(sorted(warnings)))
            selected_capture = max(
                candidates,
                key=lambda capture: (_instant(capture.completed_at), capture.capture_id),
            )
            rows = self._surface_rows_for_capture(
                connection,
                selected_capture,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            )
            components = self._surface_components(
                connection,
                capture=selected_capture,
                expiration_date=selected_capture.scope.selected_expiration,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            )
        selected: list[Mapping[str, Any]] = []
        for row in rows:
            self._row_capture({selected_capture.capture_id: selected_capture}, row)
            if not self._row_available(
                row,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            ):
                continue
            open_interest_available = {
                "available_at": row["open_interest_available_at"],
                "available_precision": row["open_interest_available_precision"],
            }
            close_available = {
                "available_at": row["close_available_at"],
                "available_precision": row["close_available_precision"],
            }
            if row["open_interest_id"] is None or row["close_price_id"] is None:
                raise ConflictError("Option surface is missing a retained observation record")
            if not _available(
                open_interest_available,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            ) or not _available(
                close_available,
                cutoff=query.cutoff,
                date_only_policy=query.date_only_policy,
                warnings=warnings,
            ):
                continue
            if query.option_type is not None and row["option_type"] != query.option_type:
                continue
            if query.surface_state is not None and row["surface_state"] != query.surface_state:
                continue
            selected.append(row)
        selected.sort(
            key=lambda row: (
                str(row["option_type"]),
                _decimal_sort(row["strike_price"], label="strike_price"),
                str(row["provider_contract_id"]),
                str(row["surface_snapshot_id"]),
            )
        )
        total = len(selected)
        returned = selected[: query.limit]
        return OptionsSelection(
            records=tuple(
                self._surface_record(
                    capture=selected_capture,
                    row=row,
                    components=components,
                )
                for row in returned
            ),
            captures=(selected_capture,),
            total_selected_count=total,
            truncated=total > query.limit,
            warnings=tuple(sorted(warnings)),
        )


def _selection_result(
    *,
    name: str,
    query: OptionsCaptureSearchQuery | OptionsContractSearchQuery | OptionsSurfaceSnapshotQuery,
    selection: OptionsSelection,
    record_type: str,
    diagnostic_code: str,
    diagnostic_message: str,
) -> QueryResult:
    cutoff = query.cutoff
    return QueryResult(
        tool=name,
        status="ok",
        records=records_from_mappings(record_type, selection.records),
        diagnostics=(
            DiagnosticV1(
                code=diagnostic_code,
                message=diagnostic_message,
                metrics=fields_from_mapping(
                    {
                        "availability_basis": "local_capture",
                        "cutoff": None if cutoff is None else cutoff.raw,
                        "cutoff_precision": None if cutoff is None else cutoff.precision.value,
                        "date_only_policy": query.date_only_policy.value,
                        "mode": query.mode,
                        "returned_count": len(selection.records),
                        "selected_capture_count": len(selection.captures),
                        "total_selected_count": selection.total_selected_count,
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(code=warning, message=f"Option selection warning: {warning}.")
            for warning in selection.warnings
        ),
        lineage=_deduplicated_lineage(selection.captures),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=query.limit,
            returned_count=len(selection.records),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
        ),
    )


def invoke_options_v2(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Invoke one of the three canonical retained-options v2 read adapters."""

    if name not in OPTIONS_V2_TOOL_NAMES:
        raise LookupError("Options v2 operation is not registered")
    if not isinstance(context, ToolExecutionContext):
        raise _invalid("execution context is invalid")
    if context.tool_version != OPTIONS_V2_OPERATION_VERSION:
        raise LookupError("Options v2 operation requires the explicit v2 version")
    context.checkpoint()
    repository = OptionsV2Repository(context.store_map, registry)
    if name == "options.search_captures":
        query = parse_capture_search_query(arguments)
        context.budget.require(
            rows=query.limit,
            operations=OPTIONS_V2_OPERATION_CHARGES[name],
        )
        selection = repository.search_captures(query)
        result = _selection_result(
            name=name,
            query=query,
            selection=selection,
            record_type="alpaca_option_capture_v2",
            diagnostic_code="alpaca_option_capture_selection",
            diagnostic_message=(
                "Retained paper/indicative Alpaca capture cohorts under the fixed "
                "standard-only collection policy "
                "were selected without provider access."
            ),
        )
    elif name == "options.search_contracts":
        query = parse_contract_search_query(arguments)
        context.budget.require(
            rows=query.limit,
            operations=OPTIONS_V2_OPERATION_CHARGES[name],
        )
        selection = repository.search_contracts(query)
        result = _selection_result(
            name=name,
            query=query,
            selection=selection,
            record_type="alpaca_option_contract_v2",
            diagnostic_code="alpaca_option_contract_selection",
            diagnostic_message=(
                "Retained contracts, including explicit nonstandard exclusions, were selected from their "
                "own latest eligible Alpaca capture cohorts without provider access."
            ),
        )
    else:
        query = parse_surface_snapshot_query(arguments)
        context.budget.require(
            rows=query.limit,
            operations=OPTIONS_V2_OPERATION_CHARGES[name],
        )
        selection = repository.get_surface_snapshot(query)
        result = _selection_result(
            name=name,
            query=query,
            selection=selection,
            record_type="alpaca_option_surface_snapshot_v2",
            diagnostic_code="alpaca_option_surface_snapshot_selection",
            diagnostic_message=(
                "One retained paper/indicative capture cohort under the fixed standard-only "
                "collection policy "
                "was selected without blending rows or synchronized inputs across captures."
            ),
        )
    context.checkpoint()
    return result


__all__ = (
    "OPTIONS_V2_OPERATION_CHARGES",
    "OPTIONS_V2_OPERATION_VERSION",
    "OPTIONS_V2_TOOL_NAMES",
    "OptionsCaptureSearchQuery",
    "OptionsContractSearchQuery",
    "OptionsSelection",
    "OptionsSurfaceSnapshotQuery",
    "OptionsV2Repository",
    "invoke_options_v2",
    "parse_capture_search_query",
    "parse_contract_search_query",
    "parse_surface_snapshot_query",
)
