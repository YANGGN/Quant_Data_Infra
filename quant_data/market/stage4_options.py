"""Offline Stage 4 option-capture ingestion and bounded read services.

The module is deliberately fixture-only.  It accepts no database path, does no
network work, and keeps all capture-cohort validation ahead of the shared
coordinator's write transaction.
"""

from __future__ import annotations


import hashlib
import math
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from ..contracts import IngestionReceipt
from ..errors import ConflictError, Issue, ResourceLimitError, ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, quiet_immutable_read_connection, read_connection, stable_id
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


_COLLECTOR_ID = "fixture.market.options_import"
_EVIDENCE_DATASET_ID = "fixture.market.option_capture_evidence"
_CANONICAL_DATASET_ID = "fixture.market.options"
_IDENTITY_DATASET_ID = "fixture.market.instruments"
_MAX_FIXTURE_BYTES = 1_048_576
_MAX_CONTRACTS = 1_000
_MAX_SURFACE_ROWS = 1_000
_MAX_BARS = 10_000
_MAX_RATE_POINTS = 1_000
_MAX_DIVIDEND_CASHFLOWS = 1_000
_MAX_EXPIRY_INPUTS = 1_000
_MAX_READ_LIMIT = 1_000
_MAX_CAPTURE_CANDIDATES = 10_000
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807

# Option fixtures resolve only reviewed opaque market identities. A ticker is
# never used as a durable option-underlying key.
_REVIEWED_UNDERLYING_BINDINGS: Mapping[str, str] = {
    "fixture.market.instrument.spy": "fixture.market.instrument.spy.v1",
    "fixture.market.index.gspc": "fixture.market.instrument.gspc.v1",
}
_REQUIRED_DATASETS = frozenset(
    {
        _IDENTITY_DATASET_ID,
        _EVIDENCE_DATASET_ID,
        _CANONICAL_DATASET_ID,
    }
)


@dataclass(frozen=True, slots=True)
class ParsedOptionsFixture:
    """Fully normalized candidate, safe to pass to the coordinator."""

    fixture: Fixture
    capture: Mapping[str, Any]
    contracts: tuple[Mapping[str, Any], ...]
    surface_rows: tuple[Mapping[str, Any], ...]
    inputs: Mapping[str, Any]
    scope: Mapping[str, Any]
    captured_at: TemporalValue
    normalization_version: str
    semantic_identity: str

    @property
    def fetched_count(self) -> int:
        rate_points = len(self.inputs["rate_curve"]["points"])
        cashflows = len(self.inputs["dividend_set"]["cashflows"])
        bars = sum(len(row["bars"]) for row in self.surface_rows)
        return (
            1
            + len(self.contracts)
            + len(self.surface_rows)
            + bars
            + rate_points
            + cashflows
            + len(self.inputs["expiry_inputs"])
        )


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


class _PreparedOptionsFixture:
    """Opaque option candidate produced only by one importer instance."""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("Prepared option fixtures are created by prepare_fixture")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("Prepared option fixtures cannot be subclassed")


@dataclass(frozen=True, slots=True)
class _PreparedOptionsFixtureState:
    owner_token: object
    store_map: StoreMap
    fixture_manifest: FixtureManifest
    fixture_id: str
    fixture_sha256: str
    fixture_byte_count: int
    collector_id: str
    evidence_dataset_id: str
    canonical_dataset_id: str
    identity_dataset_id: str | None
    store: str
    fixture: Fixture
    parsed: ParsedOptionsFixture
    semantic_identity: str
    run_id: str


_PREPARED_OPTIONS_STATES: weakref.WeakKeyDictionary[
    _PreparedOptionsFixture, _PreparedOptionsFixtureState
] = weakref.WeakKeyDictionary()


def _new_prepared_options_fixture(
    state: _PreparedOptionsFixtureState,
) -> _PreparedOptionsFixture:
    prepared = object.__new__(_PreparedOptionsFixture)
    _PREPARED_OPTIONS_STATES[prepared] = state
    return prepared


def _nonempty(value: object, *, pointer: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(pointer, "required", "Expected a nonempty string")
    return value.strip()


def _mapping(value: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error(pointer, "type", "Expected an object with string keys")
    return value


def _array(value: object, *, pointer: str, maximum: int) -> Sequence[Any]:
    if not isinstance(value, list):
        raise _error(pointer, "type", "Expected an array")
    if len(value) > maximum:
        raise ResourceLimitError(f"{pointer} exceeds the supported fixture bound")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    pointer: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise _error(pointer, "shape", "Object fields do not match the reviewed fixture shape")


def _date(value: object, *, pointer: str) -> str:
    return parse_date(_nonempty(value, pointer=pointer), pointer=pointer).isoformat()


def _temporal(
    value: object,
    precision: object,
    *,
    pointer: str,
    datetime_only: bool = False,
    nullable: bool = False,
) -> tuple[str | None, str | None, TemporalValue | None]:
    if value is None:
        if nullable and precision is None:
            return None, None, None
        raise _error(pointer, "required", "Temporal value and precision must be supplied together")
    parsed = TemporalValue.parse(_nonempty(value, pointer=pointer), pointer=pointer)
    declared = _nonempty(precision, pointer=pointer.rsplit("/", 1)[0] + "/precision")
    if declared not in {TemporalPrecision.DATE.value, TemporalPrecision.DATETIME.value}:
        raise _error(
            pointer.rsplit("/", 1)[0] + "/precision",
            "enum",
            "Expected date or datetime precision",
        )
    if parsed.precision.value != declared:
        raise _error(
            pointer.rsplit("/", 1)[0] + "/precision",
            "precision",
            "Temporal precision must match the source representation",
        )
    if datetime_only and parsed.precision is not TemporalPrecision.DATETIME:
        raise _error(pointer, "precision", "Expected an offset-aware datetime")
    assert parsed.raw is not None
    return parsed.raw, parsed.precision.value, parsed


def _instant(value: TemporalValue, *, pointer: str) -> datetime:
    if value.precision is not TemporalPrecision.DATETIME or not isinstance(value.value, datetime):
        raise _error(pointer, "precision", "Expected an offset-aware datetime")
    return value.value.astimezone(timezone.utc)


def _decimal(value: object, *, pointer: str, minimum: Decimal | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise _error(pointer, "type", "Expected a finite decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise _error(pointer, "format", "Expected a finite decimal string") from exc
    if not parsed.is_finite():
        raise _error(pointer, "finite", "Expected a finite decimal string")
    if minimum is not None and parsed < minimum:
        raise _error(pointer, "minimum", f"Expected a value greater than or equal to {minimum}")
    normalized = parsed.normalize()
    if normalized.is_zero():
        return "0"
    return format(normalized, "f")


def _optional_decimal(
    value: object,
    *,
    pointer: str,
    minimum: Decimal | None = None,
) -> str | None:
    if value is None:
        return None
    return _decimal(value, pointer=pointer, minimum=minimum)


def _integer(value: object, *, pointer: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(pointer, "type", "Expected an integer")
    if value < minimum:
        raise _error(pointer, "minimum", f"Expected an integer greater than or equal to {minimum}")
    if value > _MAX_SQLITE_INTEGER:
        raise _error(pointer, "maximum", "Integer exceeds SQLite capacity")
    return value


def _nullable_text(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    return _nonempty(value, pointer=pointer)


def _require_context(
    value: Mapping[str, Any],
    *,
    capture: Mapping[str, Any],
    pointer: str,
) -> tuple[str, str]:
    feed = _nonempty(value["feed"], pointer=f"{pointer}/feed")
    environment = _nonempty(value["environment"], pointer=f"{pointer}/environment")
    if feed != capture["resolved_feed"]:
        raise _error(
            f"{pointer}/feed",
            "capture_cohort",
            "Every option row must use the capture's resolved feed",
        )
    if environment != capture["environment"]:
        raise _error(
            f"{pointer}/environment",
            "capture_cohort",
            "Every option row must use the capture's environment",
        )
    return feed, environment


def _ensure_available_by_capture(
    raw: str,
    precision: str,
    *,
    capture: Mapping[str, Any],
    pointer: str,
) -> None:
    available = TemporalValue.parse(raw, pointer=pointer)
    if available.precision.value != precision:
        raise _error(pointer, "precision", "Availability precision is inconsistent")
    capture_available = TemporalValue.parse(
        str(capture["available_at"]), pointer="/capture/available_at"
    )
    decision = availability_at_or_before(
        available,
        capture_available,
        DateOnlyPolicy.CALENDAR_DATE_INCLUSIVE,
    )
    if not decision.included:
        raise _error(
            pointer,
            "availability",
            "Capture member availability cannot be later than the capture availability",
        )


def _parse_capture(
    raw: object,
    *,
    fixture_captured: TemporalValue,
) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/capture")
    _exact_keys(
        value,
        pointer="/capture",
        required={
            "underlying_key",
            "requested_feed",
            "resolved_feed",
            "environment",
            "requested_at",
            "requested_precision",
            "completed_at",
            "completed_precision",
            "available_at",
            "available_precision",
            "completeness",
            "deliverable_policy",
        },
    )
    underlying_key = _nonempty(value["underlying_key"], pointer="/capture/underlying_key")
    if underlying_key not in _REVIEWED_UNDERLYING_BINDINGS:
        raise _error(
            "/capture/underlying_key",
            "identity_binding",
            "Option fixtures require a reviewed opaque underlying identity",
        )
    requested_feed = _nonempty(value["requested_feed"], pointer="/capture/requested_feed")
    resolved_feed = _nonempty(value["resolved_feed"], pointer="/capture/resolved_feed")
    environment = _nonempty(value["environment"], pointer="/capture/environment")
    if environment != "synthetic":
        raise _error(
            "/capture/environment",
            "offline_only",
            "Stage 4 fixtures may use only the synthetic environment",
        )
    requested_at, requested_precision, requested = _temporal(
        value["requested_at"],
        value["requested_precision"],
        pointer="/capture/requested_at",
        datetime_only=True,
    )
    completed_at, completed_precision, completed = _temporal(
        value["completed_at"],
        value["completed_precision"],
        pointer="/capture/completed_at",
        datetime_only=True,
    )
    available_at, available_precision, _ = _temporal(
        value["available_at"],
        value["available_precision"],
        pointer="/capture/available_at",
    )
    assert requested_at is not None and requested_precision is not None and requested is not None
    assert completed_at is not None and completed_precision is not None and completed is not None
    assert available_at is not None and available_precision is not None
    if _instant(completed, pointer="/capture/completed_at") < _instant(
        requested, pointer="/capture/requested_at"
    ):
        raise _error("/capture/completed_at", "range", "Capture completed before it was requested")
    if _instant(fixture_captured, pointer="/captured_at") < _instant(
        completed, pointer="/capture/completed_at"
    ):
        raise _error(
            "/captured_at",
            "range",
            "Fixture capture time cannot precede option capture completion",
        )
    completeness = _nonempty(value["completeness"], pointer="/capture/completeness")
    if completeness not in {"complete", "partial"}:
        raise _error("/capture/completeness", "enum", "Expected complete or partial")
    deliverable_policy = _nonempty(
        value["deliverable_policy"], pointer="/capture/deliverable_policy"
    )
    if deliverable_policy not in {"standard_only", "all_deliverables"}:
        raise _error(
            "/capture/deliverable_policy",
            "enum",
            "Expected standard_only or all_deliverables",
        )
    return {
        "underlying_key": underlying_key,
        "requested_feed": requested_feed,
        "resolved_feed": resolved_feed,
        "environment": environment,
        "requested_at": requested_at,
        "requested_precision": requested_precision,
        "completed_at": completed_at,
        "completed_precision": completed_precision,
        "available_at": available_at,
        "available_precision": available_precision,
        "completeness": completeness,
        "deliverable_policy": deliverable_policy,
    }


def _parse_contracts(raw: object, *, capture: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = _array(raw, pointer="/contracts", maximum=_MAX_CONTRACTS)
    if not rows:
        raise _error("/contracts", "minimum", "An option capture requires at least one contract")
    parsed: list[Mapping[str, Any]] = []
    seen_provider_ids: set[tuple[str, str]] = set()
    seen_contract_ids: set[str] = set()
    for index, item in enumerate(rows):
        pointer = f"/contracts/{index}"
        value = _mapping(item, pointer=pointer)
        _exact_keys(
            value,
            pointer=pointer,
            required={
                "provider",
                "provider_contract_id",
                "contract_symbol",
                "expiration_date",
                "strike_price",
                "option_type",
                "deliverable_kind",
                "contract_multiplier",
                "nonstandard_deliverable",
                "contract_status",
                "available_at",
                "available_precision",
                "feed",
                "environment",
            },
        )
        _require_context(value, capture=capture, pointer=pointer)
        provider = _nonempty(value["provider"], pointer=f"{pointer}/provider")
        provider_contract_id = _nonempty(
            value["provider_contract_id"], pointer=f"{pointer}/provider_contract_id"
        )
        contract_symbol = _nonempty(value["contract_symbol"], pointer=f"{pointer}/contract_symbol")
        expiration_date = _date(value["expiration_date"], pointer=f"{pointer}/expiration_date")
        strike_price = _decimal(
            value["strike_price"],
            pointer=f"{pointer}/strike_price",
            minimum=Decimal("0.0000000001"),
        )
        option_type = _nonempty(value["option_type"], pointer=f"{pointer}/option_type")
        if option_type not in {"call", "put"}:
            raise _error(f"{pointer}/option_type", "enum", "Expected call or put")
        deliverable_kind = _nonempty(
            value["deliverable_kind"], pointer=f"{pointer}/deliverable_kind"
        )
        if deliverable_kind not in {"standard", "nonstandard"}:
            raise _error(
                f"{pointer}/deliverable_kind",
                "enum",
                "Expected standard or nonstandard",
            )
        multiplier = _integer(
            value["contract_multiplier"], pointer=f"{pointer}/contract_multiplier", minimum=1
        )
        nonstandard = value["nonstandard_deliverable"]
        if deliverable_kind == "standard":
            if nonstandard is not None:
                raise _error(
                    f"{pointer}/nonstandard_deliverable",
                    "deliverable",
                    "Standard contracts cannot carry a nonstandard deliverable",
                )
            nonstandard_json = None
        else:
            detail = _mapping(nonstandard, pointer=f"{pointer}/nonstandard_deliverable")
            if not detail:
                raise _error(
                    f"{pointer}/nonstandard_deliverable",
                    "minimum",
                    "Nonstandard contracts require a nonempty deliverable description",
                )
            nonstandard_json = dumps_strict(dict(detail))
        contract_status = _nonempty(value["contract_status"], pointer=f"{pointer}/contract_status")
        if contract_status not in {"active", "inactive", "expired"}:
            raise _error(
                f"{pointer}/contract_status",
                "enum",
                "Expected active, inactive, or expired",
            )
        available_at, available_precision, _ = _temporal(
            value["available_at"], value["available_precision"], pointer=f"{pointer}/available_at"
        )
        assert available_at is not None and available_precision is not None
        _ensure_available_by_capture(
            available_at,
            available_precision,
            capture=capture,
            pointer=f"{pointer}/available_at",
        )
        natural_key = (provider, provider_contract_id)
        if natural_key in seen_provider_ids or provider_contract_id in seen_contract_ids:
            raise _error(
                f"{pointer}/provider_contract_id",
                "unique",
                "Contract provider identity must occur exactly once in one fixture",
            )
        seen_provider_ids.add(natural_key)
        seen_contract_ids.add(provider_contract_id)
        parsed.append(
            {
                "provider": provider,
                "provider_contract_id": provider_contract_id,
                "contract_symbol": contract_symbol,
                "expiration_date": expiration_date,
                "strike_price": strike_price,
                "option_type": option_type,
                "deliverable_kind": deliverable_kind,
                "contract_multiplier": multiplier,
                "nonstandard_deliverable_json": nonstandard_json,
                "contract_status": contract_status,
                "available_at": available_at,
                "available_precision": available_precision,
            }
        )
    return tuple(
        sorted(parsed, key=lambda row: (str(row["provider"]), str(row["provider_contract_id"])))
    )


def _parse_quote(raw: object, *, pointer: str) -> Mapping[str, Any]:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "bid_price",
            "ask_price",
            "bid_size",
            "ask_size",
            "last_price",
            "last_size",
            "volume",
            "implied_volatility",
            "delta",
            "gamma",
            "theta",
            "vega",
            "rho",
            "quote_at",
            "quote_precision",
            "trade_at",
            "trade_precision",
        },
    )
    quote_at, quote_precision, _ = _temporal(
        value["quote_at"],
        value["quote_precision"],
        pointer=f"{pointer}/quote_at",
        datetime_only=True,
        nullable=True,
    )
    trade_at, trade_precision, _ = _temporal(
        value["trade_at"],
        value["trade_precision"],
        pointer=f"{pointer}/trade_at",
        datetime_only=True,
        nullable=True,
    )
    return {
        "bid_price": _optional_decimal(
            value["bid_price"], pointer=f"{pointer}/bid_price", minimum=Decimal("0")
        ),
        "ask_price": _optional_decimal(
            value["ask_price"], pointer=f"{pointer}/ask_price", minimum=Decimal("0")
        ),
        "bid_size": None
        if value["bid_size"] is None
        else _integer(value["bid_size"], pointer=f"{pointer}/bid_size"),
        "ask_size": None
        if value["ask_size"] is None
        else _integer(value["ask_size"], pointer=f"{pointer}/ask_size"),
        "last_price": _optional_decimal(
            value["last_price"], pointer=f"{pointer}/last_price", minimum=Decimal("0")
        ),
        "last_size": None
        if value["last_size"] is None
        else _integer(value["last_size"], pointer=f"{pointer}/last_size"),
        "volume": None
        if value["volume"] is None
        else _integer(value["volume"], pointer=f"{pointer}/volume"),
        "implied_volatility": _optional_decimal(
            value["implied_volatility"],
            pointer=f"{pointer}/implied_volatility",
            minimum=Decimal("0"),
        ),
        "delta": _optional_decimal(value["delta"], pointer=f"{pointer}/delta"),
        "gamma": _optional_decimal(value["gamma"], pointer=f"{pointer}/gamma"),
        "theta": _optional_decimal(value["theta"], pointer=f"{pointer}/theta"),
        "vega": _optional_decimal(value["vega"], pointer=f"{pointer}/vega"),
        "rho": _optional_decimal(value["rho"], pointer=f"{pointer}/rho"),
        "quote_at": quote_at,
        "quote_precision": quote_precision,
        "trade_at": trade_at,
        "trade_precision": trade_precision,
    }


def _quote_empty(quote: Mapping[str, Any]) -> bool:
    return all(value is None for value in quote.values())


def _parse_open_interest(
    raw: object, *, pointer: str, capture: Mapping[str, Any]
) -> Mapping[str, Any]:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "as_of_date",
            "state",
            "value",
            "missing_reason",
            "available_at",
            "available_precision",
        },
    )
    state = _nonempty(value["state"], pointer=f"{pointer}/state")
    if state not in {"present", "missing"}:
        raise _error(f"{pointer}/state", "enum", "Expected present or missing")
    number = None if value["value"] is None else _integer(value["value"], pointer=f"{pointer}/value")
    missing_reason = _nullable_text(value["missing_reason"], pointer=f"{pointer}/missing_reason")
    if (state == "present" and (number is None or missing_reason is not None)) or (
        state == "missing" and (number is not None or missing_reason is None)
    ):
        raise _error(
            pointer,
            "missingness",
            "Open interest must carry either a value or an explicit missing reason",
        )
    available_at, available_precision, _ = _temporal(
        value["available_at"], value["available_precision"], pointer=f"{pointer}/available_at"
    )
    assert available_at is not None and available_precision is not None
    _ensure_available_by_capture(
        available_at, available_precision, capture=capture, pointer=f"{pointer}/available_at"
    )
    return {
        "as_of_date": _date(value["as_of_date"], pointer=f"{pointer}/as_of_date"),
        "state": state,
        "value": number,
        "missing_reason": missing_reason,
        "available_at": available_at,
        "available_precision": available_precision,
    }


def _parse_close_price(
    raw: object, *, pointer: str, capture: Mapping[str, Any]
) -> Mapping[str, Any]:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "trade_date",
            "state",
            "value",
            "missing_reason",
            "available_at",
            "available_precision",
        },
    )
    state = _nonempty(value["state"], pointer=f"{pointer}/state")
    if state not in {"present", "missing"}:
        raise _error(f"{pointer}/state", "enum", "Expected present or missing")
    number = _optional_decimal(
        value["value"], pointer=f"{pointer}/value", minimum=Decimal("0")
    )
    missing_reason = _nullable_text(value["missing_reason"], pointer=f"{pointer}/missing_reason")
    if (state == "present" and (number is None or missing_reason is not None)) or (
        state == "missing" and (number is not None or missing_reason is None)
    ):
        raise _error(
            pointer,
            "missingness",
            "Close price must carry either a value or an explicit missing reason",
        )
    available_at, available_precision, _ = _temporal(
        value["available_at"], value["available_precision"], pointer=f"{pointer}/available_at"
    )
    assert available_at is not None and available_precision is not None
    _ensure_available_by_capture(
        available_at, available_precision, capture=capture, pointer=f"{pointer}/available_at"
    )
    return {
        "trade_date": _date(value["trade_date"], pointer=f"{pointer}/trade_date"),
        "state": state,
        "value": number,
        "missing_reason": missing_reason,
        "available_at": available_at,
        "available_precision": available_precision,
    }


def _parse_bars(
    raw: object, *, pointer: str, capture: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    rows = _array(raw, pointer=pointer, maximum=_MAX_BARS)
    parsed: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(rows):
        row_pointer = f"{pointer}/{index}"
        value = _mapping(item, pointer=row_pointer)
        _exact_keys(
            value,
            pointer=row_pointer,
            required={
                "timeframe",
                "bar_start",
                "bar_end",
                "state",
                "missing_reason",
                "open_price",
                "high_price",
                "low_price",
                "close_price",
                "volume",
                "available_at",
                "available_precision",
            },
        )
        state = _nonempty(value["state"], pointer=f"{row_pointer}/state")
        if state not in {"present", "missing"}:
            raise _error(f"{row_pointer}/state", "enum", "Expected present or missing")
        open_price = _optional_decimal(
            value["open_price"], pointer=f"{row_pointer}/open_price", minimum=Decimal("0")
        )
        high_price = _optional_decimal(
            value["high_price"], pointer=f"{row_pointer}/high_price", minimum=Decimal("0")
        )
        low_price = _optional_decimal(
            value["low_price"], pointer=f"{row_pointer}/low_price", minimum=Decimal("0")
        )
        close_price = _optional_decimal(
            value["close_price"], pointer=f"{row_pointer}/close_price", minimum=Decimal("0")
        )
        volume = (
            None
            if value["volume"] is None
            else _integer(value["volume"], pointer=f"{row_pointer}/volume")
        )
        missing_reason = _nullable_text(
            value["missing_reason"], pointer=f"{row_pointer}/missing_reason"
        )
        if state == "present":
            if (
                None in {open_price, high_price, low_price, close_price}
                or missing_reason is not None
            ):
                raise _error(
                    row_pointer,
                    "missingness",
                    "Present bars require OHLC values and no missing reason",
                )
            assert open_price is not None and high_price is not None
            assert low_price is not None and close_price is not None
            if Decimal(low_price) > min(Decimal(open_price), Decimal(close_price)) or Decimal(
                high_price
            ) < max(Decimal(open_price), Decimal(close_price)):
                raise _error(row_pointer, "ohlc", "Option bar OHLC values are inconsistent")
        elif (
            any(item is not None for item in (open_price, high_price, low_price, close_price, volume))
            or missing_reason is None
        ):
            raise _error(
                row_pointer,
                "missingness",
                "Missing bars require an explicit reason and no values",
            )
        bar_start, _, start_value = _temporal(
            value["bar_start"], "datetime", pointer=f"{row_pointer}/bar_start", datetime_only=True
        )
        bar_end, _, end_value = _temporal(
            value["bar_end"], "datetime", pointer=f"{row_pointer}/bar_end", datetime_only=True
        )
        assert bar_start is not None and start_value is not None
        assert bar_end is not None and end_value is not None
        if _instant(end_value, pointer=f"{row_pointer}/bar_end") < _instant(
            start_value, pointer=f"{row_pointer}/bar_start"
        ):
            raise _error(f"{row_pointer}/bar_end", "range", "Option bar ends before it starts")
        available_at, available_precision, _ = _temporal(
            value["available_at"], value["available_precision"], pointer=f"{row_pointer}/available_at"
        )
        assert available_at is not None and available_precision is not None
        _ensure_available_by_capture(
            available_at, available_precision, capture=capture, pointer=f"{row_pointer}/available_at"
        )
        timeframe = _nonempty(value["timeframe"], pointer=f"{row_pointer}/timeframe")
        natural_key = (timeframe, bar_start)
        if natural_key in seen:
            raise _error(row_pointer, "unique", "A fixture cannot duplicate an option bar key")
        seen.add(natural_key)
        parsed.append(
            {
                "timeframe": timeframe,
                "bar_start": bar_start,
                "bar_end": bar_end,
                "state": state,
                "missing_reason": missing_reason,
                "open_price": open_price,
                "high_price": high_price,
                "low_price": low_price,
                "close_price": close_price,
                "volume": volume,
                "available_at": available_at,
                "available_precision": available_precision,
            }
        )
    return tuple(sorted(parsed, key=lambda row: (str(row["timeframe"]), str(row["bar_start"]))))


def _parse_surface_rows(
    raw: object,
    *,
    capture: Mapping[str, Any],
    contracts: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    rows = _array(raw, pointer="/surface", maximum=_MAX_SURFACE_ROWS)
    if len(rows) != len(contracts):
        raise _error(
            "/surface",
            "coverage",
            "Every declared contract must have one explicit surface or exclusion row",
        )
    by_provider_contract = {str(contract["provider_contract_id"]): contract for contract in contracts}
    parsed: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        pointer = f"/surface/{index}"
        value = _mapping(item, pointer=pointer)
        _exact_keys(
            value,
            pointer=pointer,
            required={
                "provider_contract_id",
                "surface_state",
                "missing_reason",
                "exclusion_reason",
                "quote",
                "available_at",
                "available_precision",
                "feed",
                "environment",
                "open_interest",
                "close_price",
                "bars",
            },
        )
        _require_context(value, capture=capture, pointer=pointer)
        contract_key = _nonempty(
            value["provider_contract_id"], pointer=f"{pointer}/provider_contract_id"
        )
        contract = by_provider_contract.get(contract_key)
        if contract is None:
            raise _error(
                f"{pointer}/provider_contract_id",
                "contract_reference",
                "Surface rows must reference a declared contract in the same capture",
            )
        if contract_key in seen:
            raise _error(
                f"{pointer}/provider_contract_id",
                "unique",
                "A capture cannot contain duplicate surface contract rows",
            )
        seen.add(contract_key)
        state = _nonempty(value["surface_state"], pointer=f"{pointer}/surface_state")
        if state not in {"present", "missing", "excluded"}:
            raise _error(
                f"{pointer}/surface_state",
                "enum",
                "Expected present, missing, or excluded",
            )
        missing_reason = _nullable_text(value["missing_reason"], pointer=f"{pointer}/missing_reason")
        exclusion_reason = _nullable_text(
            value["exclusion_reason"], pointer=f"{pointer}/exclusion_reason"
        )
        quote = _parse_quote(value["quote"], pointer=f"{pointer}/quote")
        available_at, available_precision, _ = _temporal(
            value["available_at"], value["available_precision"], pointer=f"{pointer}/available_at"
        )
        assert available_at is not None and available_precision is not None
        _ensure_available_by_capture(
            available_at, available_precision, capture=capture, pointer=f"{pointer}/available_at"
        )
        if state == "present":
            if missing_reason is not None or exclusion_reason is not None:
                raise _error(
                    pointer,
                    "missingness",
                    "Present surface rows cannot carry missing or exclusion reasons",
                )
            if quote["quote_at"] is None or all(
                quote[field] is None for field in ("bid_price", "ask_price", "last_price")
            ):
                raise _error(
                    f"{pointer}/quote",
                    "quote",
                    "Present surface rows require a quote timestamp and price",
                )
        elif state == "missing":
            if missing_reason is None or exclusion_reason is not None or not _quote_empty(quote):
                raise _error(
                    pointer,
                    "missingness",
                    "Missing surface rows require only an explicit missing reason",
                )
        elif exclusion_reason is None or missing_reason is not None or not _quote_empty(quote):
            raise _error(
                pointer,
                "exclusion",
                "Excluded surface rows require only an explicit exclusion reason",
            )
        nonstandard = str(contract["deliverable_kind"]) == "nonstandard"
        if capture["deliverable_policy"] == "standard_only" and nonstandard:
            if state != "excluded" or exclusion_reason != "nonstandard_deliverable":
                raise _error(
                    pointer,
                    "deliverable_policy",
                    "Standard-only captures require explicit nonstandard exclusion",
                )
        elif state == "excluded":
            raise _error(
                pointer,
                "deliverable_policy",
                "Only a standard-only nonstandard contract may be excluded",
            )
        open_interest = _parse_open_interest(
            value["open_interest"], pointer=f"{pointer}/open_interest", capture=capture
        )
        close_price = _parse_close_price(
            value["close_price"], pointer=f"{pointer}/close_price", capture=capture
        )
        bars = _parse_bars(value["bars"], pointer=f"{pointer}/bars", capture=capture)
        if state != "present" and (
            open_interest["state"] != "missing"
            or close_price["state"] != "missing"
            or bars
        ):
            raise _error(
                pointer,
                "missingness",
                "Non-present surfaces require explicit missing observations and no bars",
            )
        parsed.append(
            {
                "provider_contract_id": contract_key,
                "surface_state": state,
                "missing_reason": missing_reason,
                "exclusion_reason": exclusion_reason,
                "quote": quote,
                "available_at": available_at,
                "available_precision": available_precision,
                "open_interest": open_interest,
                "close_price": close_price,
                "bars": bars,
            }
        )
    if seen != set(by_provider_contract):
        raise _error(
            "/surface",
            "coverage",
            "Every declared contract must have one explicit surface or exclusion row",
        )
    return tuple(sorted(parsed, key=lambda row: str(row["provider_contract_id"])))


def _parse_underlying(raw: object, *, capture: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/inputs/underlying")
    _exact_keys(
        value,
        pointer="/inputs/underlying",
        required={
            "state",
            "missing_reason",
            "observed_at",
            "observed_precision",
            "feed",
            "environment",
        },
    )
    _require_context(value, capture=capture, pointer="/inputs/underlying")
    state = _nonempty(value["state"], pointer="/inputs/underlying/state")
    if state not in {"present", "missing"}:
        raise _error("/inputs/underlying/state", "enum", "Expected present or missing")
    observed_at, observed_precision, _ = _temporal(
        value["observed_at"],
        value["observed_precision"],
        pointer="/inputs/underlying/observed_at",
        datetime_only=True,
        nullable=True,
    )
    missing_reason = _nullable_text(
        value["missing_reason"], pointer="/inputs/underlying/missing_reason"
    )
    if (state == "present" and (observed_at is None or missing_reason is not None)) or (
        state == "missing" and (observed_at is not None or missing_reason is None)
    ):
        raise _error(
            "/inputs/underlying",
            "missingness",
            "Underlying state must have either an observation or an explicit reason",
        )
    return {
        "state": state,
        "missing_reason": missing_reason,
        "observed_at": observed_at,
        "observed_precision": observed_precision,
    }


def _parse_underlying_quote(
    raw: object, *, capture: Mapping[str, Any]
) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/inputs/underlying_quote")
    _exact_keys(
        value,
        pointer="/inputs/underlying_quote",
        required={
            "state",
            "missing_reason",
            "bid_price",
            "ask_price",
            "last_price",
            "trade_price",
            "quote_at",
            "quote_precision",
            "available_at",
            "available_precision",
            "feed",
            "environment",
        },
    )
    _require_context(value, capture=capture, pointer="/inputs/underlying_quote")
    state = _nonempty(value["state"], pointer="/inputs/underlying_quote/state")
    if state not in {"present", "missing"}:
        raise _error("/inputs/underlying_quote/state", "enum", "Expected present or missing")
    quote_at, quote_precision, _ = _temporal(
        value["quote_at"],
        value["quote_precision"],
        pointer="/inputs/underlying_quote/quote_at",
        datetime_only=True,
        nullable=True,
    )
    bid_price = _optional_decimal(
        value["bid_price"], pointer="/inputs/underlying_quote/bid_price", minimum=Decimal("0")
    )
    ask_price = _optional_decimal(
        value["ask_price"], pointer="/inputs/underlying_quote/ask_price", minimum=Decimal("0")
    )
    last_price = _optional_decimal(
        value["last_price"], pointer="/inputs/underlying_quote/last_price", minimum=Decimal("0")
    )
    trade_price = _optional_decimal(
        value["trade_price"], pointer="/inputs/underlying_quote/trade_price", minimum=Decimal("0")
    )
    missing_reason = _nullable_text(
        value["missing_reason"], pointer="/inputs/underlying_quote/missing_reason"
    )
    if state == "present":
        if quote_at is None or missing_reason is not None or all(
            number is None for number in (bid_price, ask_price, last_price, trade_price)
        ):
            raise _error(
                "/inputs/underlying_quote",
                "missingness",
                "Present underlying quote requires a timestamp, a price, and no missing reason",
            )
    elif (
        quote_at is not None
        or missing_reason is None
        or any(number is not None for number in (bid_price, ask_price, last_price, trade_price))
    ):
        raise _error(
            "/inputs/underlying_quote",
            "missingness",
            "Missing underlying quote requires an explicit reason and no values",
        )
    available_at, available_precision, _ = _temporal(
        value["available_at"],
        value["available_precision"],
        pointer="/inputs/underlying_quote/available_at",
    )
    assert available_at is not None and available_precision is not None
    _ensure_available_by_capture(
        available_at,
        available_precision,
        capture=capture,
        pointer="/inputs/underlying_quote/available_at",
    )
    return {
        "state": state,
        "missing_reason": missing_reason,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "last_price": last_price,
        "trade_price": trade_price,
        "quote_at": quote_at,
        "quote_precision": quote_precision,
        "available_at": available_at,
        "available_precision": available_precision,
    }


def _parse_rate_curve(raw: object, *, capture: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/inputs/rate_curve")
    _exact_keys(
        value,
        pointer="/inputs/rate_curve",
        required={
            "state",
            "missing_reason",
            "curve_date",
            "source_name",
            "available_at",
            "available_precision",
            "feed",
            "environment",
            "points",
        },
    )
    _require_context(value, capture=capture, pointer="/inputs/rate_curve")
    state = _nonempty(value["state"], pointer="/inputs/rate_curve/state")
    if state not in {"present", "missing"}:
        raise _error("/inputs/rate_curve/state", "enum", "Expected present or missing")
    curve_date = None if value["curve_date"] is None else _date(
        value["curve_date"], pointer="/inputs/rate_curve/curve_date"
    )
    source_name = _nullable_text(value["source_name"], pointer="/inputs/rate_curve/source_name")
    missing_reason = _nullable_text(value["missing_reason"], pointer="/inputs/rate_curve/missing_reason")
    points_raw = _array(value["points"], pointer="/inputs/rate_curve/points", maximum=_MAX_RATE_POINTS)
    parsed_points: list[Mapping[str, Any]] = []
    seen_tenors: set[int] = set()
    for index, item in enumerate(points_raw):
        pointer = f"/inputs/rate_curve/points/{index}"
        row = _mapping(item, pointer=pointer)
        _exact_keys(
            row,
            pointer=pointer,
            required={"tenor_days", "state", "zero_rate", "missing_reason"},
        )
        point_state = _nonempty(row["state"], pointer=f"{pointer}/state")
        if point_state not in {"present", "missing"}:
            raise _error(f"{pointer}/state", "enum", "Expected present or missing")
        tenor_days = _integer(row["tenor_days"], pointer=f"{pointer}/tenor_days", minimum=1)
        zero_rate = _optional_decimal(row["zero_rate"], pointer=f"{pointer}/zero_rate")
        point_missing = _nullable_text(row["missing_reason"], pointer=f"{pointer}/missing_reason")
        if (point_state == "present" and (zero_rate is None or point_missing is not None)) or (
            point_state == "missing" and (zero_rate is not None or point_missing is None)
        ):
            raise _error(pointer, "missingness", "Rate points require a value or explicit missing reason")
        if tenor_days in seen_tenors:
            raise _error(f"{pointer}/tenor_days", "unique", "Rate tenor appears more than once")
        seen_tenors.add(tenor_days)
        parsed_points.append(
            {
                "tenor_days": tenor_days,
                "state": point_state,
                "zero_rate": zero_rate,
                "missing_reason": point_missing,
            }
        )
    if state == "present":
        if curve_date is None or source_name is None or missing_reason is not None or not parsed_points:
            raise _error(
                "/inputs/rate_curve",
                "missingness",
                "Present rate curves require date, source, points, and no missing reason",
            )
    elif curve_date is not None or source_name is not None or missing_reason is None or parsed_points:
        raise _error(
            "/inputs/rate_curve",
            "missingness",
            "Missing rate curves require an explicit reason and no points",
        )
    available_at, available_precision, _ = _temporal(
        value["available_at"], value["available_precision"], pointer="/inputs/rate_curve/available_at"
    )
    assert available_at is not None and available_precision is not None
    _ensure_available_by_capture(
        available_at, available_precision, capture=capture, pointer="/inputs/rate_curve/available_at"
    )
    return {
        "state": state,
        "missing_reason": missing_reason,
        "curve_date": curve_date,
        "source_name": source_name,
        "available_at": available_at,
        "available_precision": available_precision,
        "points": tuple(sorted(parsed_points, key=lambda row: int(row["tenor_days"]))),
    }


def _parse_dividend_set(raw: object, *, capture: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/inputs/dividend_set")
    _exact_keys(
        value,
        pointer="/inputs/dividend_set",
        required={
            "state",
            "missing_reason",
            "source_name",
            "available_at",
            "available_precision",
            "feed",
            "environment",
            "cashflows",
        },
    )
    _require_context(value, capture=capture, pointer="/inputs/dividend_set")
    state = _nonempty(value["state"], pointer="/inputs/dividend_set/state")
    if state not in {"present", "missing"}:
        raise _error("/inputs/dividend_set/state", "enum", "Expected present or missing")
    source_name = _nullable_text(value["source_name"], pointer="/inputs/dividend_set/source_name")
    missing_reason = _nullable_text(value["missing_reason"], pointer="/inputs/dividend_set/missing_reason")
    cashflows_raw = _array(
        value["cashflows"],
        pointer="/inputs/dividend_set/cashflows",
        maximum=_MAX_DIVIDEND_CASHFLOWS,
    )
    parsed_cashflows: list[Mapping[str, Any]] = []
    seen_dates: set[str] = set()
    for index, item in enumerate(cashflows_raw):
        pointer = f"/inputs/dividend_set/cashflows/{index}"
        row = _mapping(item, pointer=pointer)
        _exact_keys(
            row,
            pointer=pointer,
            required={"ex_date", "pay_date", "state", "cash_amount", "missing_reason"},
        )
        cashflow_state = _nonempty(row["state"], pointer=f"{pointer}/state")
        if cashflow_state not in {"present", "missing"}:
            raise _error(f"{pointer}/state", "enum", "Expected present or missing")
        ex_date = _date(row["ex_date"], pointer=f"{pointer}/ex_date")
        pay_date = None if row["pay_date"] is None else _date(row["pay_date"], pointer=f"{pointer}/pay_date")
        if pay_date is not None and pay_date < ex_date:
            raise _error(f"{pointer}/pay_date", "range", "Dividend pay date precedes ex date")
        amount = _optional_decimal(
            row["cash_amount"], pointer=f"{pointer}/cash_amount", minimum=Decimal("0")
        )
        cash_missing = _nullable_text(row["missing_reason"], pointer=f"{pointer}/missing_reason")
        if (cashflow_state == "present" and (amount is None or cash_missing is not None)) or (
            cashflow_state == "missing" and (amount is not None or cash_missing is None)
        ):
            raise _error(pointer, "missingness", "Dividend cashflows require a value or missing reason")
        if ex_date in seen_dates:
            raise _error(f"{pointer}/ex_date", "unique", "Dividend ex-date appears more than once")
        seen_dates.add(ex_date)
        parsed_cashflows.append(
            {
                "ex_date": ex_date,
                "pay_date": pay_date,
                "state": cashflow_state,
                "cash_amount": amount,
                "missing_reason": cash_missing,
            }
        )
    if state == "present":
        if source_name is None or missing_reason is not None:
            raise _error(
                "/inputs/dividend_set",
                "missingness",
                "Present dividend sets require a source and no missing reason",
            )
    elif source_name is not None or missing_reason is None or parsed_cashflows:
        raise _error(
            "/inputs/dividend_set",
            "missingness",
            "Missing dividend sets require an explicit reason and no cashflows",
        )
    available_at, available_precision, _ = _temporal(
        value["available_at"],
        value["available_precision"],
        pointer="/inputs/dividend_set/available_at",
    )
    assert available_at is not None and available_precision is not None
    _ensure_available_by_capture(
        available_at, available_precision, capture=capture, pointer="/inputs/dividend_set/available_at"
    )
    return {
        "state": state,
        "missing_reason": missing_reason,
        "source_name": source_name,
        "available_at": available_at,
        "available_precision": available_precision,
        "cashflows": tuple(sorted(parsed_cashflows, key=lambda row: str(row["ex_date"]))),
    }


def _parse_expiry_inputs(
    raw: object,
    *,
    capture: Mapping[str, Any],
    contracts: tuple[Mapping[str, Any], ...],
    underlying: Mapping[str, Any],
    underlying_quote: Mapping[str, Any] | None,
    rate_curve: Mapping[str, Any],
    dividend_set: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    rows = _array(raw, pointer="/inputs/expiry_inputs", maximum=_MAX_EXPIRY_INPUTS)
    expected_expirations = {str(contract["expiration_date"]) for contract in contracts}
    if len(rows) != len(expected_expirations):
        raise _error(
            "/inputs/expiry_inputs",
            "coverage",
            "Every capture expiration requires one synchronized input state",
        )
    dependencies_present = (
        underlying["state"] == "present"
        and underlying_quote is not None
        and underlying_quote["state"] == "present"
        and rate_curve["state"] == "present"
        and dividend_set["state"] == "present"
    )
    spot_reference: str | None = None
    if underlying_quote is not None:
        spot_reference = (
            underlying_quote["last_price"]
            if underlying_quote["last_price"] is not None
            else underlying_quote["trade_price"]
        )
    rate_values = {
        str(point["zero_rate"])
        for point in rate_curve["points"]
        if point["state"] == "present" and point["zero_rate"] is not None
    }
    parsed: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        pointer = f"/inputs/expiry_inputs/{index}"
        value = _mapping(item, pointer=pointer)
        _exact_keys(
            value,
            pointer=pointer,
            required={
                "expiration_date",
                "state",
                "missing_reason",
                "spot_price",
                "risk_free_rate",
                "dividend_yield",
                "forward_price",
                "available_at",
                "available_precision",
                "feed",
                "environment",
            },
        )
        _require_context(value, capture=capture, pointer=pointer)
        expiration_date = _date(value["expiration_date"], pointer=f"{pointer}/expiration_date")
        if expiration_date not in expected_expirations:
            raise _error(
                f"{pointer}/expiration_date",
                "coverage",
                "Expiry input does not belong to a declared capture contract",
            )
        if expiration_date in seen:
            raise _error(
                f"{pointer}/expiration_date",
                "unique",
                "Capture expiry input appears more than once",
            )
        seen.add(expiration_date)
        state = _nonempty(value["state"], pointer=f"{pointer}/state")
        if state not in {"present", "missing"}:
            raise _error(f"{pointer}/state", "enum", "Expected present or missing")
        missing_reason = _nullable_text(value["missing_reason"], pointer=f"{pointer}/missing_reason")
        spot_price = _optional_decimal(
            value["spot_price"], pointer=f"{pointer}/spot_price", minimum=Decimal("0.0000000001")
        )
        risk_free_rate = _optional_decimal(
            value["risk_free_rate"], pointer=f"{pointer}/risk_free_rate"
        )
        dividend_yield = _optional_decimal(
            value["dividend_yield"], pointer=f"{pointer}/dividend_yield", minimum=Decimal("0")
        )
        forward_price = _optional_decimal(
            value["forward_price"], pointer=f"{pointer}/forward_price", minimum=Decimal("0.0000000001")
        )
        available_at, available_precision, _ = _temporal(
            value["available_at"],
            value["available_precision"],
            pointer=f"{pointer}/available_at",
        )
        assert available_at is not None and available_precision is not None
        _ensure_available_by_capture(
            available_at, available_precision, capture=capture, pointer=f"{pointer}/available_at"
        )
        if dependencies_present:
            if (
                state != "present"
                or missing_reason is not None
                or None in {spot_price, risk_free_rate, dividend_yield, forward_price}
            ):
                raise _error(
                    pointer,
                    "synchronization",
                    "Present synchronized inputs require all capture-scoped components",
                )
            if spot_reference is None or spot_price != spot_reference:
                raise _error(
                    f"{pointer}/spot_price",
                    "synchronization",
                    "Expiry spot must use the capture's last or trade underlying price",
                )
            if risk_free_rate not in rate_values:
                raise _error(
                    f"{pointer}/risk_free_rate",
                    "synchronization",
                    "Expiry rate must be an explicit present point from the same capture curve",
                )
        elif (
            state != "missing"
            or missing_reason is None
            or any(item is not None for item in (spot_price, risk_free_rate, dividend_yield, forward_price))
        ):
            raise _error(
                pointer,
                "synchronization",
                "Incoherent inputs must be represented as an explicit missing expiry state",
            )
        parsed.append(
            {
                "expiration_date": expiration_date,
                "state": state,
                "missing_reason": missing_reason,
                "spot_price": spot_price,
                "risk_free_rate": risk_free_rate,
                "dividend_yield": dividend_yield,
                "forward_price": forward_price,
                "available_at": available_at,
                "available_precision": available_precision,
            }
        )
    if seen != expected_expirations:
        raise _error(
            "/inputs/expiry_inputs",
            "coverage",
            "Every capture expiration requires one synchronized input state",
        )
    return tuple(sorted(parsed, key=lambda row: str(row["expiration_date"])))


def _parse_inputs(
    raw: object,
    *,
    capture: Mapping[str, Any],
    contracts: tuple[Mapping[str, Any], ...],
) -> Mapping[str, Any]:
    value = _mapping(raw, pointer="/inputs")
    _exact_keys(
        value,
        pointer="/inputs",
        required={
            "underlying",
            "underlying_quote",
            "rate_curve",
            "dividend_set",
            "expiry_inputs",
        },
    )
    underlying = _parse_underlying(value["underlying"], capture=capture)
    if underlying["state"] == "present":
        underlying_quote = _parse_underlying_quote(value["underlying_quote"], capture=capture)
    else:
        if value["underlying_quote"] is not None:
            raise _error(
                "/inputs/underlying_quote",
                "synchronization",
                "A missing capture underlying cannot carry a detached underlying quote",
            )
        underlying_quote = None
    rate_curve = _parse_rate_curve(value["rate_curve"], capture=capture)
    dividend_set = _parse_dividend_set(value["dividend_set"], capture=capture)
    expiry_inputs = _parse_expiry_inputs(
        value["expiry_inputs"],
        capture=capture,
        contracts=contracts,
        underlying=underlying,
        underlying_quote=underlying_quote,
        rate_curve=rate_curve,
        dividend_set=dividend_set,
    )
    return {
        "underlying": underlying,
        "underlying_quote": underlying_quote,
        "rate_curve": rate_curve,
        "dividend_set": dividend_set,
        "expiry_inputs": expiry_inputs,
    }


def _fixture_scope(fixture: Fixture, *, capture: Mapping[str, Any]) -> Mapping[str, Any]:
    scope = _mapping(fixture.request_scope, pointer="/request_scope")
    _exact_keys(
        scope,
        pointer="/request_scope",
        required={
            "underlying_key",
            "requested_feed",
            "resolved_feed",
            "environment",
            "deliverable_policy",
            "completeness",
        },
    )
    normalized = {
        "underlying_key": _nonempty(scope["underlying_key"], pointer="/request_scope/underlying_key"),
        "requested_feed": _nonempty(scope["requested_feed"], pointer="/request_scope/requested_feed"),
        "resolved_feed": _nonempty(scope["resolved_feed"], pointer="/request_scope/resolved_feed"),
        "environment": _nonempty(scope["environment"], pointer="/request_scope/environment"),
        "deliverable_policy": _nonempty(
            scope["deliverable_policy"], pointer="/request_scope/deliverable_policy"
        ),
        "completeness": _nonempty(scope["completeness"], pointer="/request_scope/completeness"),
    }
    for field, item in normalized.items():
        if item != capture[field]:
            raise _error(
                f"/request_scope/{field}",
                "capture_scope",
                "Fixture request scope must exactly match the immutable capture scope",
            )
    return normalized


def _normalization_version(
    fixture: Fixture,
    *,
    contract_count: int,
    surface_count: int,
    expiry_count: int,
) -> str:
    metadata = _mapping(fixture.metadata, pointer="/metadata")
    _exact_keys(
        metadata,
        pointer="/metadata",
        required={
            "normalization_version",
            "expected_contracts",
            "expected_surface_rows",
            "expected_expiry_inputs",
            "expected_outcome",
            "expected_error_rule",
        },
    )
    normalization_version = _nonempty(
        metadata["normalization_version"], pointer="/metadata/normalization_version"
    )
    if _integer(metadata["expected_contracts"], pointer="/metadata/expected_contracts") != contract_count:
        raise _error("/metadata/expected_contracts", "count", "Reviewed contract count is inconsistent")
    if _integer(
        metadata["expected_surface_rows"], pointer="/metadata/expected_surface_rows"
    ) != surface_count:
        raise _error(
            "/metadata/expected_surface_rows",
            "count",
            "Reviewed surface-row count is inconsistent",
        )
    if _integer(
        metadata["expected_expiry_inputs"], pointer="/metadata/expected_expiry_inputs"
    ) != expiry_count:
        raise _error(
            "/metadata/expected_expiry_inputs",
            "count",
            "Reviewed expiry-input count is inconsistent",
        )
    outcome = _nonempty(metadata["expected_outcome"], pointer="/metadata/expected_outcome")
    if outcome not in {"succeeded", "rejected"}:
        raise _error("/metadata/expected_outcome", "enum", "Expected succeeded or rejected")
    expected_rule = metadata["expected_error_rule"]
    if outcome == "succeeded" and expected_rule is not None:
        raise _error(
            "/metadata/expected_error_rule",
            "shape",
            "Successful fixtures cannot declare an expected error rule",
        )
    if outcome == "rejected" and not isinstance(expected_rule, str):
        raise _error(
            "/metadata/expected_error_rule",
            "required",
            "Rejected fixtures must declare an expected error rule",
        )
    return normalization_version


def _semantic_identity(
    fixture: Fixture,
    *,
    capture: Mapping[str, Any],
    contracts: tuple[Mapping[str, Any], ...],
    surface_rows: tuple[Mapping[str, Any], ...],
    inputs: Mapping[str, Any],
    scope: Mapping[str, Any],
    normalization_version: str,
) -> str:
    material = {
        "ingestion_family_id": fixture.ingestion_family_id,
        "evidence_dataset_id": fixture.evidence_dataset_id,
        "canonical_dataset_id": fixture.canonical_dataset_id,
        "provider": fixture.provider,
        "scope": dict(scope),
        "normalization_version": normalization_version,
        "capture": dict(capture),
        "contracts": [dict(row) for row in contracts],
        "surface": [dict(row) for row in surface_rows],
        "inputs": dict(inputs),
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def parse_stage4_options_fixture(fixture: Fixture) -> ParsedOptionsFixture:
    """Parse one reviewed synthetic option fixture without opening a store."""

    if (
        fixture.fixture_schema_version != "1.0.0"
        or fixture.ingestion_family_id != _COLLECTOR_ID
        or fixture.evidence_dataset_id != _EVIDENCE_DATASET_ID
        or fixture.canonical_dataset_id != _CANONICAL_DATASET_ID
        or fixture.identity_dataset_id != _IDENTITY_DATASET_ID
        or fixture.store != StoreRole.MARKET.value
        or not fixture.test_fixture
        or fixture.promotable
    ):
        raise ValidationError("Option fixture does not match the frozen Stage 4 contract")
    if len(fixture.bytes) > _MAX_FIXTURE_BYTES:
        raise ResourceLimitError("Option fixture exceeds the supported byte bound")
    if (
        hashlib.sha256(fixture.bytes).hexdigest() != fixture.sha256
        or len(fixture.bytes) != fixture.byte_count
    ):
        raise ValidationError("Option fixture bytes do not match its reviewed manifest pin")
    captured = TemporalValue.parse(fixture.captured_at, pointer="/captured_at")
    if captured.precision is not TemporalPrecision.DATETIME:
        raise _error("/captured_at", "precision", "Fixture capture must be an offset-aware datetime")
    payload = _mapping(loads_strict(fixture.bytes, max_bytes=_MAX_FIXTURE_BYTES), pointer="/")
    _exact_keys(
        payload,
        pointer="/",
        required={"capture", "contracts", "surface", "inputs"},
    )
    capture = _parse_capture(payload["capture"], fixture_captured=captured)
    if fixture.provider != capture["resolved_feed"]:
        raise _error(
            "/capture/resolved_feed",
            "provider",
            "Fixture provider must be the immutable capture's resolved feed",
        )
    scope = _fixture_scope(fixture, capture=capture)
    contracts = _parse_contracts(payload["contracts"], capture=capture)
    surface_rows = _parse_surface_rows(payload["surface"], capture=capture, contracts=contracts)
    inputs = _parse_inputs(payload["inputs"], capture=capture, contracts=contracts)
    normalization_version = _normalization_version(
        fixture,
        contract_count=len(contracts),
        surface_count=len(surface_rows),
        expiry_count=len(inputs["expiry_inputs"]),
    )
    if fixture.metadata["expected_outcome"] != "succeeded":
        raise _error(
            "/metadata/expected_outcome",
            "rejection_fixture",
            "Reviewed rejection fixtures are never publishable",
        )
    semantic_identity = _semantic_identity(
        fixture,
        capture=capture,
        contracts=contracts,
        surface_rows=surface_rows,
        inputs=inputs,
        scope=scope,
        normalization_version=normalization_version,
    )
    return ParsedOptionsFixture(
        fixture=fixture,
        capture=capture,
        contracts=contracts,
        surface_rows=surface_rows,
        inputs=inputs,
        scope=scope,
        captured_at=captured,
        normalization_version=normalization_version,
        semantic_identity=semantic_identity,
    )


def _real(value: str | None, *, pointer: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise _error(pointer, "finite", "Value cannot be represented safely in SQLite")
    return result


def _underlying_id(capture: Mapping[str, Any]) -> str:
    return stable_id(
        "instrument",
        _IDENTITY_DATASET_ID,
        _REVIEWED_UNDERLYING_BINDINGS[str(capture["underlying_key"])],
    )


def _preflight_underlying(store_map: StoreMap, parsed: ParsedOptionsFixture) -> str:
    instrument_id = _underlying_id(parsed.capture)
    with read_connection(store_map, StoreRole.MARKET) as connection:
        row = connection.execute(
            "SELECT instrument_id FROM instruments WHERE instrument_id=?",
            (instrument_id,),
        ).fetchone()
    if row is None:
        raise ValidationError(
            "The reviewed option underlying is unavailable; import its identity fixture first"
        )
    return instrument_id


def _contract_id(contract: Mapping[str, Any]) -> str:
    return stable_id(
        "option_contract",
        str(contract["provider"]),
        str(contract["provider_contract_id"]),
    )


def _ensure_contract(
    connection: sqlite3.Connection,
    *,
    contract: Mapping[str, Any],
    underlying_instrument_id: str,
    run_id: str,
) -> tuple[str, int]:
    contract_id = _contract_id(contract)
    existing = connection.execute(
        """
        SELECT contract_id, underlying_instrument_id, provider, provider_contract_id,
               contract_symbol, expiration_date, strike_price, option_type,
               deliverable_kind, contract_multiplier, nonstandard_deliverable_json,
               contract_status, available_at, available_precision
        FROM option_contracts
        WHERE provider=? AND provider_contract_id=?
        """,
        (contract["provider"], contract["provider_contract_id"]),
    ).fetchone()
    expected = (
        contract_id,
        underlying_instrument_id,
        str(contract["provider"]),
        str(contract["provider_contract_id"]),
        str(contract["contract_symbol"]),
        str(contract["expiration_date"]),
        str(contract["strike_price"]),
        str(contract["option_type"]),
        str(contract["deliverable_kind"]),
        int(contract["contract_multiplier"]),
        contract["nonstandard_deliverable_json"],
        str(contract["contract_status"]),
        str(contract["available_at"]),
        str(contract["available_precision"]),
    )
    if existing is not None:
        actual = tuple(existing[field] for field in existing.keys())
        if actual != expected:
            raise ConflictError("Immutable option contract conflicts with reviewed fixture identity")
        return contract_id, 0
    connection.execute(
        """
        INSERT INTO option_contracts (
            contract_id, underlying_instrument_id, provider, provider_contract_id,
            contract_symbol, expiration_date, strike_price, option_type,
            deliverable_kind, contract_multiplier, nonstandard_deliverable_json,
            contract_status, available_at, available_precision, created_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*expected, run_id),
    )
    return contract_id, 1


def _write_capture_inputs(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedOptionsFixture,
    capture_id: str,
    underlying_instrument_id: str,
) -> tuple[int, Mapping[str, str | None]]:
    inputs = parsed.inputs
    underlying = inputs["underlying"]
    connection.execute(
        """
        INSERT INTO option_capture_underlyings (
            capture_id, underlying_instrument_id, underlying_state, missing_reason,
            observed_at, observed_precision
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            capture_id,
            underlying_instrument_id,
            underlying["state"],
            underlying["missing_reason"],
            underlying["observed_at"],
            underlying["observed_precision"],
        ),
    )
    count = 1
    quote_id: str | None = None
    quote = inputs["underlying_quote"]
    if quote is not None:
        quote_id = stable_id("option_underlying_quote", capture_id)
        connection.execute(
            """
            INSERT INTO option_capture_underlying_quotes (
                underlying_quote_id, capture_id, underlying_instrument_id, input_state,
                missing_reason, bid_price, ask_price, last_price, trade_price,
                quote_at, quote_precision, available_at, available_precision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                quote_id,
                capture_id,
                underlying_instrument_id,
                quote["state"],
                quote["missing_reason"],
                _real(quote["bid_price"], pointer="/inputs/underlying_quote/bid_price"),
                _real(quote["ask_price"], pointer="/inputs/underlying_quote/ask_price"),
                _real(quote["last_price"], pointer="/inputs/underlying_quote/last_price"),
                _real(quote["trade_price"], pointer="/inputs/underlying_quote/trade_price"),
                quote["quote_at"],
                quote["quote_precision"],
                quote["available_at"],
                quote["available_precision"],
            ),
        )
        count += 1
    rate_curve = inputs["rate_curve"]
    rate_curve_id = stable_id("option_rate_curve", capture_id)
    connection.execute(
        """
        INSERT INTO option_capture_rate_curves (
            rate_curve_id, capture_id, curve_date, source_name, input_state,
            missing_reason, available_at, available_precision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rate_curve_id,
            capture_id,
            rate_curve["curve_date"],
            rate_curve["source_name"],
            rate_curve["state"],
            rate_curve["missing_reason"],
            rate_curve["available_at"],
            rate_curve["available_precision"],
        ),
    )
    count += 1
    for source_row, point in enumerate(rate_curve["points"], start=1):
        connection.execute(
            """
            INSERT INTO option_capture_rate_curve_points (
                rate_curve_point_id, rate_curve_id, tenor_days, zero_rate,
                point_state, missing_reason, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("option_rate_point", rate_curve_id, str(point["tenor_days"])),
                rate_curve_id,
                point["tenor_days"],
                _real(point["zero_rate"], pointer="/inputs/rate_curve/points/zero_rate"),
                point["state"],
                point["missing_reason"],
                source_row,
            ),
        )
        count += 1
    dividend_set = inputs["dividend_set"]
    dividend_set_id = stable_id("option_dividend_set", capture_id)
    connection.execute(
        """
        INSERT INTO option_capture_dividend_sets (
            dividend_set_id, capture_id, underlying_instrument_id, source_name,
            input_state, missing_reason, available_at, available_precision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dividend_set_id,
            capture_id,
            underlying_instrument_id,
            dividend_set["source_name"],
            dividend_set["state"],
            dividend_set["missing_reason"],
            dividend_set["available_at"],
            dividend_set["available_precision"],
        ),
    )
    count += 1
    for source_row, cashflow in enumerate(dividend_set["cashflows"], start=1):
        connection.execute(
            """
            INSERT INTO option_capture_dividend_cashflows (
                dividend_cashflow_id, dividend_set_id, ex_date, pay_date,
                cash_amount, cashflow_state, missing_reason, source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("option_dividend_cashflow", dividend_set_id, str(cashflow["ex_date"])),
                dividend_set_id,
                cashflow["ex_date"],
                cashflow["pay_date"],
                _real(cashflow["cash_amount"], pointer="/inputs/dividend_set/cashflows/cash_amount"),
                cashflow["state"],
                cashflow["missing_reason"],
                source_row,
            ),
        )
        count += 1
    for expiry in inputs["expiry_inputs"]:
        expiry_id = stable_id("option_expiry_input", capture_id, str(expiry["expiration_date"]))
        is_present = expiry["state"] == "present"
        connection.execute(
            """
            INSERT INTO option_capture_expiry_inputs (
                expiry_input_id, capture_id, expiration_date, underlying_quote_id,
                rate_curve_id, dividend_set_id, input_state, missing_reason,
                spot_price, risk_free_rate, dividend_yield, forward_price,
                available_at, available_precision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expiry_id,
                capture_id,
                expiry["expiration_date"],
                quote_id if is_present else None,
                rate_curve_id if is_present else None,
                dividend_set_id if is_present else None,
                expiry["state"],
                expiry["missing_reason"],
                _real(expiry["spot_price"], pointer="/inputs/expiry_inputs/spot_price"),
                _real(expiry["risk_free_rate"], pointer="/inputs/expiry_inputs/risk_free_rate"),
                _real(expiry["dividend_yield"], pointer="/inputs/expiry_inputs/dividend_yield"),
                _real(expiry["forward_price"], pointer="/inputs/expiry_inputs/forward_price"),
                expiry["available_at"],
                expiry["available_precision"],
            ),
        )
        count += 1
    return count, {
        "underlying_quote_id": quote_id,
        "rate_curve_id": rate_curve_id,
        "dividend_set_id": dividend_set_id,
    }


def _write_surface_rows(
    connection: sqlite3.Connection,
    *,
    parsed: ParsedOptionsFixture,
    capture_id: str,
    contract_ids: Mapping[str, str],
) -> int:
    count = 0
    for source_row, surface in enumerate(parsed.surface_rows, start=1):
        contract_id = contract_ids[str(surface["provider_contract_id"])]
        quote = surface["quote"]
        surface_snapshot_id = stable_id("option_surface_snapshot", capture_id, contract_id)
        connection.execute(
            """
            INSERT INTO option_surface_snapshots (
                surface_snapshot_id, capture_id, contract_id, surface_state,
                missing_reason, exclusion_reason, bid_price, ask_price, bid_size,
                ask_size, last_price, last_size, volume, implied_volatility,
                delta, gamma, theta, vega, rho, quote_at, quote_precision,
                trade_at, trade_precision, available_at, available_precision,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                surface_snapshot_id,
                capture_id,
                contract_id,
                surface["surface_state"],
                surface["missing_reason"],
                surface["exclusion_reason"],
                _real(quote["bid_price"], pointer="/surface/quote/bid_price"),
                _real(quote["ask_price"], pointer="/surface/quote/ask_price"),
                quote["bid_size"],
                quote["ask_size"],
                _real(quote["last_price"], pointer="/surface/quote/last_price"),
                quote["last_size"],
                quote["volume"],
                _real(quote["implied_volatility"], pointer="/surface/quote/implied_volatility"),
                _real(quote["delta"], pointer="/surface/quote/delta"),
                _real(quote["gamma"], pointer="/surface/quote/gamma"),
                _real(quote["theta"], pointer="/surface/quote/theta"),
                _real(quote["vega"], pointer="/surface/quote/vega"),
                _real(quote["rho"], pointer="/surface/quote/rho"),
                quote["quote_at"],
                quote["quote_precision"],
                quote["trade_at"],
                quote["trade_precision"],
                surface["available_at"],
                surface["available_precision"],
                source_row,
            ),
        )
        count += 1
        open_interest = surface["open_interest"]
        connection.execute(
            """
            INSERT INTO option_open_interest (
                open_interest_id, capture_id, contract_id, as_of_date, open_interest,
                observation_state, missing_reason, available_at, available_precision,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "option_open_interest",
                    capture_id,
                    contract_id,
                    str(open_interest["as_of_date"]),
                ),
                capture_id,
                contract_id,
                open_interest["as_of_date"],
                open_interest["value"],
                open_interest["state"],
                open_interest["missing_reason"],
                open_interest["available_at"],
                open_interest["available_precision"],
                source_row,
            ),
        )
        count += 1
        close_price = surface["close_price"]
        connection.execute(
            """
            INSERT INTO option_close_prices (
                close_price_id, capture_id, contract_id, trade_date, close_price,
                observation_state, missing_reason, available_at, available_precision,
                source_row
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id(
                    "option_close_price",
                    capture_id,
                    contract_id,
                    str(close_price["trade_date"]),
                ),
                capture_id,
                contract_id,
                close_price["trade_date"],
                _real(close_price["value"], pointer="/surface/close_price/value"),
                close_price["state"],
                close_price["missing_reason"],
                close_price["available_at"],
                close_price["available_precision"],
                source_row,
            ),
        )
        count += 1
        for bar_source_row, bar in enumerate(surface["bars"], start=1):
            previous = connection.execute(
                """
                SELECT bar_id, version_sequence
                FROM option_bars
                WHERE contract_id=? AND timeframe=? AND bar_start=?
                ORDER BY version_sequence DESC, bar_id DESC
                LIMIT 1
                """,
                (contract_id, bar["timeframe"], bar["bar_start"]),
            ).fetchone()
            sequence = 1 if previous is None else int(previous["version_sequence"]) + 1
            supersedes = None if previous is None else str(previous["bar_id"])
            bar_id = stable_id(
                "option_bar",
                capture_id,
                contract_id,
                str(bar["timeframe"]),
                str(bar["bar_start"]),
                str(sequence),
            )
            connection.execute(
                """
                INSERT INTO option_bars (
                    bar_id, capture_id, contract_id, timeframe, bar_start, bar_end,
                    bar_state, missing_reason, open_price, high_price, low_price,
                    close_price, volume, available_at, available_precision,
                    version_sequence, supersedes_bar_id, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bar_id,
                    capture_id,
                    contract_id,
                    bar["timeframe"],
                    bar["bar_start"],
                    bar["bar_end"],
                    bar["state"],
                    bar["missing_reason"],
                    _real(bar["open_price"], pointer="/surface/bars/open_price"),
                    _real(bar["high_price"], pointer="/surface/bars/high_price"),
                    _real(bar["low_price"], pointer="/surface/bars/low_price"),
                    _real(bar["close_price"], pointer="/surface/bars/close_price"),
                    bar["volume"],
                    bar["available_at"],
                    bar["available_precision"],
                    sequence,
                    supersedes,
                    bar_source_row,
                ),
            )
            count += 1
    return count


class Stage4OptionsFixtureImporter:
    """Publish one coherent reviewed option capture through the shared coordinator."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self._store_map = store_map
        self._fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map, code_version="stage4.0.0")
        self._prepared_owner_token = object()

    def prepare_fixture(self, fixture_id: str) -> _PreparedOptionsFixture:
        """Validate and stage one reviewed option fixture without opening a store."""

        fixture = self._fixture_manifest.get(fixture_id)
        parsed = parse_stage4_options_fixture(fixture)
        if parsed.semantic_identity != fixture.expected_semantic_identity:
            raise ValidationError(
                "Option fixture semantic identity does not match its reviewed manifest pin"
            )
        run_id = stable_id(
            "stage4_option_capture_run",
            fixture.canonical_dataset_id,
            parsed.semantic_identity,
        )
        return _new_prepared_options_fixture(
            _PreparedOptionsFixtureState(
                owner_token=self._prepared_owner_token,
                store_map=self._store_map,
                fixture_manifest=self._fixture_manifest,
                fixture_id=fixture.id,
                fixture_sha256=fixture.sha256,
                fixture_byte_count=fixture.byte_count,
                collector_id=fixture.ingestion_family_id,
                evidence_dataset_id=fixture.evidence_dataset_id,
                canonical_dataset_id=fixture.canonical_dataset_id,
                identity_dataset_id=fixture.identity_dataset_id,
                store=fixture.store,
                fixture=fixture,
                parsed=parsed,
                semantic_identity=parsed.semantic_identity,
                run_id=run_id,
            )
        )

    def _prepared_state(
        self,
        prepared: _PreparedOptionsFixture,
    ) -> _PreparedOptionsFixtureState:
        if not isinstance(prepared, _PreparedOptionsFixture):
            raise ValidationError("Prepared option fixture has an invalid type")
        state = _PREPARED_OPTIONS_STATES.get(prepared)
        if (
            state is None
            or state.owner_token is not self._prepared_owner_token
            or state.store_map is not self._store_map
            or state.fixture_manifest is not self._fixture_manifest
            or state.fixture.id != state.fixture_id
            or state.fixture.sha256 != state.fixture_sha256
            or state.fixture.byte_count != state.fixture_byte_count
            or state.fixture.ingestion_family_id != state.collector_id
            or state.fixture.evidence_dataset_id != state.evidence_dataset_id
            or state.fixture.canonical_dataset_id != state.canonical_dataset_id
            or state.fixture.identity_dataset_id != state.identity_dataset_id
            or state.fixture.store != state.store
            or state.store != StoreRole.MARKET.value
            or state.parsed.fixture is not state.fixture
            or state.parsed.semantic_identity != state.semantic_identity
            or state.semantic_identity != state.fixture.expected_semantic_identity
        ):
            raise ValidationError("Prepared option fixture is not valid for this importer")
        return state

    def publish_prepared(
        self,
        prepared: _PreparedOptionsFixture,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Publish a prior validated option candidate without parsing or fetching."""

        state = self._prepared_state(prepared)
        fixture = state.fixture
        parsed = state.parsed
        run_id = state.run_id
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self._store_map, StoreRole.MARKET)
        underlying_instrument_id = _preflight_underlying(self._store_map, parsed)

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            artifact_id = stable_id(
                "stage4_option_capture_artifact",
                fixture.evidence_dataset_id,
                fixture.sha256,
                parsed.semantic_identity,
            )
            snapshot_id = stable_id(
                "stage4_option_capture_snapshot",
                fixture.canonical_dataset_id,
                parsed.semantic_identity,
            )
            capture_id = stable_id(
                "option_capture",
                fixture.evidence_dataset_id,
                parsed.semantic_identity,
            )
            contract_ids: dict[str, str] = {}
            written = 0
            for contract in parsed.contracts:
                contract_id, inserted = _ensure_contract(
                    connection,
                    contract=contract,
                    underlying_instrument_id=underlying_instrument_id,
                    run_id=active_run_id,
                )
                contract_ids[str(contract["provider_contract_id"])] = contract_id
                written += inserted
            connection.execute(
                """
                INSERT INTO option_surface_captures (
                    capture_id, semantic_identity, underlying_instrument_id,
                    requested_feed, resolved_feed, environment, request_scope_json,
                    requested_at, requested_precision, completed_at, completed_precision,
                    available_at, available_precision, completeness, deliverable_policy,
                    artifact_id, source_snapshot_id, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    parsed.semantic_identity,
                    underlying_instrument_id,
                    parsed.capture["requested_feed"],
                    parsed.capture["resolved_feed"],
                    parsed.capture["environment"],
                    dumps_strict(dict(parsed.scope)),
                    parsed.capture["requested_at"],
                    parsed.capture["requested_precision"],
                    parsed.capture["completed_at"],
                    parsed.capture["completed_precision"],
                    parsed.capture["available_at"],
                    parsed.capture["available_precision"],
                    parsed.capture["completeness"],
                    parsed.capture["deliverable_policy"],
                    artifact_id,
                    snapshot_id,
                    active_run_id,
                ),
            )
            written += 1
            input_written, _ = _write_capture_inputs(
                connection,
                parsed=parsed,
                capture_id=capture_id,
                underlying_instrument_id=underlying_instrument_id,
            )
            written += input_written
            written += _write_surface_rows(
                connection,
                parsed=parsed,
                capture_id=capture_id,
                contract_ids=contract_ids,
            )
            artifact = ArtifactWrite(
                artifact_id=artifact_id,
                dataset_id=fixture.evidence_dataset_id,
                content_sha256=fixture.sha256,
                media_type="application/json",
                byte_count=fixture.byte_count,
                source_reference=fixture.resource_name,
                request_scope=dict(parsed.scope),
                captured_at=parsed.captured_at.raw or fixture.captured_at,
                captured_precision=parsed.captured_at.precision.value,
                normalization_version=parsed.normalization_version,
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=snapshot_id,
                    dataset_id=fixture.canonical_dataset_id,
                    semantic_identity=parsed.semantic_identity,
                    scope=dict(parsed.scope),
                    completeness=str(parsed.capture["completeness"]),
                    row_count=parsed.fetched_count,
                    captured_at=parsed.captured_at.raw or fixture.captured_at,
                    captured_precision=parsed.captured_at.precision.value,
                    validation_state="validated",
                    artifact_ids=(artifact_id,),
                    warnings=fixture.expected_warnings,
                ),
                quality_results=(
                    QualityWrite(
                        quality_result_id=stable_id(
                            "stage4_option_capture_quality",
                            active_run_id,
                            fixture.canonical_dataset_id,
                        ),
                        dataset_id=fixture.canonical_dataset_id,
                        rule_id="single_capture_cohort",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=snapshot_id,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        observed={
                            "contracts": len(parsed.contracts),
                            "surface_rows": len(parsed.surface_rows),
                            "expiry_inputs": len(parsed.inputs["expiry_inputs"]),
                            "written_count": written,
                        },
                    ),
                ),
                warnings=fixture.expected_warnings,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=fixture.canonical_dataset_id,
            output_dataset_ids=(
                fixture.evidence_dataset_id,
                fixture.canonical_dataset_id,
            ),
            semantic_identity=parsed.semantic_identity,
            run_id=run_id,
            command=fixture.ingestion_family_id,
            scope={
                "fixture_id": fixture.id,
                "request_scope": dict(parsed.scope),
                "resolved_feed": parsed.capture["resolved_feed"],
                "environment": parsed.capture["environment"],
            },
            started_at=parsed.captured_at.raw or fixture.captured_at,
            completed_at=parsed.captured_at.raw or fixture.captured_at,
            fetched_count=parsed.fetched_count,
            writer=writer,
            held_locks=held_locks,
        )

    def import_fixture(
        self,
        fixture_id: str,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        return self.publish_prepared(
            self.prepare_fixture(fixture_id),
            held_locks=held_locks,
        )


@dataclass(frozen=True, slots=True)
class OptionsStage4Query:
    """One bounded request for a single coherent latest or as-of capture."""

    underlying_instrument_id: str
    resolved_feed: str
    environment: str = "synthetic"
    mode: str = "latest"
    as_of: str | TemporalValue | None = None
    date_only_policy: str | DateOnlyPolicy = DateOnlyPolicy.COMPLETED_DATE
    limit: int = 1_000

    def __post_init__(self) -> None:
        if not isinstance(self.underlying_instrument_id, str) or not self.underlying_instrument_id:
            raise ValidationError("Option query requires a stable underlying instrument identity")
        if not isinstance(self.resolved_feed, str) or not self.resolved_feed:
            raise ValidationError("Option query requires a resolved feed")
        if self.environment != "synthetic":
            raise ValidationError("Stage 4 option reads support only the synthetic environment")
        if self.mode not in {"latest", "as_of"}:
            raise ValidationError("Option query mode must be latest or as_of")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Unsupported date-only policy") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= _MAX_READ_LIMIT:
            raise ResourceLimitError("Option query limit must be between 1 and 1000")
        if self.mode == "latest":
            if self.as_of is not None:
                raise ValidationError("Latest option queries cannot provide an as_of cutoff")
            return
        if self.as_of is None:
            raise ValidationError("As-of option queries require an explicit cutoff")
        cutoff = self.as_of if isinstance(self.as_of, TemporalValue) else TemporalValue.parse(
            self.as_of, pointer="/as_of"
        )
        if cutoff.precision not in {TemporalPrecision.DATE, TemporalPrecision.DATETIME}:
            raise ValidationError("Option as_of must be an exact date or offset-aware datetime")
        object.__setattr__(self, "as_of", cutoff)

    @property
    def cutoff(self) -> TemporalValue | None:
        return self.as_of if isinstance(self.as_of, TemporalValue) else None


def _stored_temporal(row: Mapping[str, Any], *, value_field: str, precision_field: str) -> TemporalValue:
    temporal = TemporalValue.parse(str(row[value_field]), pointer=f"/stored/{value_field}")
    if temporal.precision.value != str(row[precision_field]):
        raise ValidationError("Stored option temporal precision is inconsistent")
    return temporal


def _eligible(
    row: Mapping[str, Any],
    *,
    query: OptionsStage4Query,
    warnings: set[str],
) -> bool:
    if query.cutoff is None:
        return True
    decision = availability_at_or_before(
        _stored_temporal(row, value_field="available_at", precision_field="available_precision"),
        query.cutoff,
        query.date_only_policy,
    )
    warnings.update(decision.warnings)
    return decision.included


def _bounded_rows(rows: list[sqlite3.Row], *, maximum: int, message: str) -> list[sqlite3.Row]:
    if len(rows) > maximum:
        raise ResourceLimitError(message)
    return rows


class OptionsStage4Repository:
    """Host-routed, read-only selection of one immutable option capture cohort."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        datasets = {dataset.id for dataset in registry.datasets_for(StoreRole.MARKET.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise ValidationError("Stage 4 options repository is not bound to frozen registry datasets")

    @staticmethod
    def _select_capture(
        connection: sqlite3.Connection,
        query: OptionsStage4Query,
        warnings: set[str],
    ) -> sqlite3.Row:
        candidates = _bounded_rows(
            list(
                connection.execute(
                    """
                    SELECT *
                    FROM option_surface_captures
                    WHERE underlying_instrument_id=? AND resolved_feed=? AND environment=?
                    ORDER BY completed_at DESC, capture_id DESC
                    LIMIT ?
                    """,
                    (
                        query.underlying_instrument_id,
                        query.resolved_feed,
                        query.environment,
                        _MAX_CAPTURE_CANDIDATES + 1,
                    ),
                )
            ),
            maximum=_MAX_CAPTURE_CANDIDATES,
            message="Option capture history exceeds the supported candidate bound",
        )
        if query.cutoff is not None:
            candidates = [
                row for row in candidates if _eligible(row, query=query, warnings=warnings)
            ]
        if not candidates:
            raise ValidationError("No option capture is available for the requested cohort and cutoff")
        return max(
            candidates,
            key=lambda row: (
                _instant(
                    TemporalValue.parse(str(row["completed_at"]), pointer="/stored/completed_at"),
                    pointer="/stored/completed_at",
                ),
                str(row["capture_id"]),
            ),
        )

    @staticmethod
    def _underlying(connection: sqlite3.Connection, capture_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT underlying_state, missing_reason, observed_at, observed_precision
            FROM option_capture_underlyings
            WHERE capture_id=?
            """,
            (capture_id,),
        ).fetchone()
        if row is None:
            raise ConflictError("Option capture is missing its immutable underlying record")
        return {
            "state": str(row["underlying_state"]),
            "missing_reason": None if row["missing_reason"] is None else str(row["missing_reason"]),
            "observed_at": None if row["observed_at"] is None else str(row["observed_at"]),
            "observed_precision": (
                None if row["observed_precision"] is None else str(row["observed_precision"])
            ),
        }

    @staticmethod
    def _inputs(
        connection: sqlite3.Connection,
        capture_id: str,
        query: OptionsStage4Query,
        warnings: set[str],
    ) -> dict[str, Any]:
        quote_row = connection.execute(
            """
            SELECT * FROM option_capture_underlying_quotes WHERE capture_id=?
            """,
            (capture_id,),
        ).fetchone()
        quote: dict[str, Any] | None = None
        if quote_row is not None and _eligible(quote_row, query=query, warnings=warnings):
            quote = {
                "underlying_quote_id": str(quote_row["underlying_quote_id"]),
                "state": str(quote_row["input_state"]),
                "missing_reason": (
                    None if quote_row["missing_reason"] is None else str(quote_row["missing_reason"])
                ),
                "bid_price": quote_row["bid_price"],
                "ask_price": quote_row["ask_price"],
                "last_price": quote_row["last_price"],
                "trade_price": quote_row["trade_price"],
                "quote_at": None if quote_row["quote_at"] is None else str(quote_row["quote_at"]),
                "quote_precision": (
                    None if quote_row["quote_precision"] is None else str(quote_row["quote_precision"])
                ),
                "available_at": str(quote_row["available_at"]),
                "available_precision": str(quote_row["available_precision"]),
            }
        curve_row = connection.execute(
            "SELECT * FROM option_capture_rate_curves WHERE capture_id=?",
            (capture_id,),
        ).fetchone()
        curve: dict[str, Any] | None = None
        if curve_row is not None and _eligible(curve_row, query=query, warnings=warnings):
            points = _bounded_rows(
                list(
                    connection.execute(
                        """
                        SELECT * FROM option_capture_rate_curve_points
                        WHERE rate_curve_id=?
                        ORDER BY tenor_days, rate_curve_point_id
                        LIMIT ?
                        """,
                        (curve_row["rate_curve_id"], _MAX_RATE_POINTS + 1),
                    )
                ),
                maximum=_MAX_RATE_POINTS,
                message="Option rate curve exceeds the supported point bound",
            )
            curve = {
                "rate_curve_id": str(curve_row["rate_curve_id"]),
                "state": str(curve_row["input_state"]),
                "missing_reason": (
                    None if curve_row["missing_reason"] is None else str(curve_row["missing_reason"])
                ),
                "curve_date": None if curve_row["curve_date"] is None else str(curve_row["curve_date"]),
                "source_name": None if curve_row["source_name"] is None else str(curve_row["source_name"]),
                "available_at": str(curve_row["available_at"]),
                "available_precision": str(curve_row["available_precision"]),
                "points": [
                    {
                        "tenor_days": int(point["tenor_days"]),
                        "state": str(point["point_state"]),
                        "zero_rate": point["zero_rate"],
                        "missing_reason": (
                            None if point["missing_reason"] is None else str(point["missing_reason"])
                        ),
                    }
                    for point in points
                ],
            }
        dividend_row = connection.execute(
            "SELECT * FROM option_capture_dividend_sets WHERE capture_id=?",
            (capture_id,),
        ).fetchone()
        dividend: dict[str, Any] | None = None
        if dividend_row is not None and _eligible(dividend_row, query=query, warnings=warnings):
            cashflows = _bounded_rows(
                list(
                    connection.execute(
                        """
                        SELECT * FROM option_capture_dividend_cashflows
                        WHERE dividend_set_id=?
                        ORDER BY ex_date, dividend_cashflow_id
                        LIMIT ?
                        """,
                        (dividend_row["dividend_set_id"], _MAX_DIVIDEND_CASHFLOWS + 1),
                    )
                ),
                maximum=_MAX_DIVIDEND_CASHFLOWS,
                message="Option dividend input exceeds the supported cashflow bound",
            )
            dividend = {
                "dividend_set_id": str(dividend_row["dividend_set_id"]),
                "state": str(dividend_row["input_state"]),
                "missing_reason": (
                    None
                    if dividend_row["missing_reason"] is None
                    else str(dividend_row["missing_reason"])
                ),
                "source_name": (
                    None if dividend_row["source_name"] is None else str(dividend_row["source_name"])
                ),
                "available_at": str(dividend_row["available_at"]),
                "available_precision": str(dividend_row["available_precision"]),
                "cashflows": [
                    {
                        "ex_date": str(cashflow["ex_date"]),
                        "pay_date": None if cashflow["pay_date"] is None else str(cashflow["pay_date"]),
                        "state": str(cashflow["cashflow_state"]),
                        "cash_amount": cashflow["cash_amount"],
                        "missing_reason": (
                            None if cashflow["missing_reason"] is None else str(cashflow["missing_reason"])
                        ),
                    }
                    for cashflow in cashflows
                ],
            }
        expiry_rows = _bounded_rows(
            list(
                connection.execute(
                    """
                    SELECT * FROM option_capture_expiry_inputs
                    WHERE capture_id=?
                    ORDER BY expiration_date, expiry_input_id
                    LIMIT ?
                    """,
                    (capture_id, _MAX_EXPIRY_INPUTS + 1),
                )
            ),
            maximum=_MAX_EXPIRY_INPUTS,
            message="Option expiry inputs exceed the supported bound",
        )
        expiry_inputs = [
            {
                "expiry_input_id": str(row["expiry_input_id"]),
                "expiration_date": str(row["expiration_date"]),
                "state": str(row["input_state"]),
                "missing_reason": None if row["missing_reason"] is None else str(row["missing_reason"]),
                "spot_price": row["spot_price"],
                "risk_free_rate": row["risk_free_rate"],
                "dividend_yield": row["dividend_yield"],
                "forward_price": row["forward_price"],
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
            }
            for row in expiry_rows
            if _eligible(row, query=query, warnings=warnings)
        ]
        return {
            "underlying_quote": quote,
            "rate_curve": curve,
            "dividend_set": dividend,
            "expiry_inputs": expiry_inputs,
        }


    @staticmethod
    def _surface_rows(
        connection: sqlite3.Connection,
        capture_id: str,
        query: OptionsStage4Query,
        warnings: set[str],
    ) -> list[dict[str, Any]]:
        rows = _bounded_rows(
            list(
                connection.execute(
                    """
                    SELECT surface.*,
                           contract.provider, contract.provider_contract_id,
                           contract.contract_symbol, contract.expiration_date,
                           contract.strike_price, contract.option_type,
                           contract.deliverable_kind, contract.contract_multiplier,
                           contract.nonstandard_deliverable_json, contract.contract_status,
                           contract.available_at AS contract_available_at,
                           contract.available_precision AS contract_available_precision
                    FROM option_surface_snapshots AS surface
                    JOIN option_contracts AS contract ON contract.contract_id=surface.contract_id
                    WHERE surface.capture_id=?
                    ORDER BY contract.provider, contract.provider_contract_id, surface.surface_snapshot_id
                    LIMIT ?
                    """,
                    (capture_id, query.limit + 1),
                )
            ),
            maximum=query.limit,
            message="Option surface exceeds the requested result limit",
        )
        selected: list[dict[str, Any]] = []
        for row in rows:
            contract_availability = {
                "available_at": row["contract_available_at"],
                "available_precision": row["contract_available_precision"],
            }
            if not _eligible(row, query=query, warnings=warnings) or not _eligible(
                contract_availability, query=query, warnings=warnings
            ):
                continue
            open_interest_row = connection.execute(
                "SELECT * FROM option_open_interest WHERE capture_id=? AND contract_id=?",
                (capture_id, row["contract_id"]),
            ).fetchone()
            close_price_row = connection.execute(
                "SELECT * FROM option_close_prices WHERE capture_id=? AND contract_id=?",
                (capture_id, row["contract_id"]),
            ).fetchone()
            bars = _bounded_rows(
                list(
                    connection.execute(
                        """
                        SELECT * FROM option_bars
                        WHERE capture_id=? AND contract_id=?
                        ORDER BY timeframe, bar_start, version_sequence, bar_id
                        LIMIT ?
                        """,
                        (capture_id, row["contract_id"], _MAX_BARS + 1),
                    )
                ),
                maximum=_MAX_BARS,
                message="Option bar history exceeds the supported bound",
            )
            nonstandard = (
                None
                if row["nonstandard_deliverable_json"] is None
                else loads_strict(str(row["nonstandard_deliverable_json"]))
            )
            selected.append(
                {
                    "surface_snapshot_id": str(row["surface_snapshot_id"]),
                    "contract": {
                        "contract_id": str(row["contract_id"]),
                        "provider": str(row["provider"]),
                        "provider_contract_id": str(row["provider_contract_id"]),
                        "contract_symbol": str(row["contract_symbol"]),
                        "expiration_date": str(row["expiration_date"]),
                        "strike_price": str(row["strike_price"]),
                        "option_type": str(row["option_type"]),
                        "deliverable_kind": str(row["deliverable_kind"]),
                        "contract_multiplier": int(row["contract_multiplier"]),
                        "nonstandard_deliverable": nonstandard,
                        "contract_status": str(row["contract_status"]),
                        "available_at": str(row["contract_available_at"]),
                        "available_precision": str(row["contract_available_precision"]),
                    },
                    "state": str(row["surface_state"]),
                    "missing_reason": (
                        None if row["missing_reason"] is None else str(row["missing_reason"])
                    ),
                    "exclusion_reason": (
                        None if row["exclusion_reason"] is None else str(row["exclusion_reason"])
                    ),
                    "quote": {
                        "bid_price": row["bid_price"],
                        "ask_price": row["ask_price"],
                        "bid_size": row["bid_size"],
                        "ask_size": row["ask_size"],
                        "last_price": row["last_price"],
                        "last_size": row["last_size"],
                        "volume": row["volume"],
                        "implied_volatility": row["implied_volatility"],
                        "delta": row["delta"],
                        "gamma": row["gamma"],
                        "theta": row["theta"],
                        "vega": row["vega"],
                        "rho": row["rho"],
                        "quote_at": None if row["quote_at"] is None else str(row["quote_at"]),
                        "quote_precision": (
                            None if row["quote_precision"] is None else str(row["quote_precision"])
                        ),
                        "trade_at": None if row["trade_at"] is None else str(row["trade_at"]),
                        "trade_precision": (
                            None if row["trade_precision"] is None else str(row["trade_precision"])
                        ),
                    },
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "open_interest": (
                        None
                        if open_interest_row is None
                        or not _eligible(open_interest_row, query=query, warnings=warnings)
                        else {
                            "open_interest_id": str(open_interest_row["open_interest_id"]),
                            "as_of_date": str(open_interest_row["as_of_date"]),
                            "state": str(open_interest_row["observation_state"]),
                            "value": open_interest_row["open_interest"],
                            "missing_reason": (
                                None
                                if open_interest_row["missing_reason"] is None
                                else str(open_interest_row["missing_reason"])
                            ),
                            "available_at": str(open_interest_row["available_at"]),
                            "available_precision": str(open_interest_row["available_precision"]),
                        }
                    ),
                    "close_price": (
                        None
                        if close_price_row is None
                        or not _eligible(close_price_row, query=query, warnings=warnings)
                        else {
                            "close_price_id": str(close_price_row["close_price_id"]),
                            "trade_date": str(close_price_row["trade_date"]),
                            "state": str(close_price_row["observation_state"]),
                            "value": close_price_row["close_price"],
                            "missing_reason": (
                                None
                                if close_price_row["missing_reason"] is None
                                else str(close_price_row["missing_reason"])
                            ),
                            "available_at": str(close_price_row["available_at"]),
                            "available_precision": str(close_price_row["available_precision"]),
                        }
                    ),
                    "bars": [
                        {
                            "bar_id": str(bar["bar_id"]),
                            "timeframe": str(bar["timeframe"]),
                            "bar_start": str(bar["bar_start"]),
                            "bar_end": str(bar["bar_end"]),
                            "state": str(bar["bar_state"]),
                            "missing_reason": (
                                None if bar["missing_reason"] is None else str(bar["missing_reason"])
                            ),
                            "open_price": bar["open_price"],
                            "high_price": bar["high_price"],
                            "low_price": bar["low_price"],
                            "close_price": bar["close_price"],
                            "volume": bar["volume"],
                            "available_at": str(bar["available_at"]),
                            "available_precision": str(bar["available_precision"]),
                            "version_sequence": int(bar["version_sequence"]),
                            "supersedes_bar_id": (
                                None if bar["supersedes_bar_id"] is None else str(bar["supersedes_bar_id"])
                            ),
                        }
                        for bar in bars
                        if _eligible(bar, query=query, warnings=warnings)
                    ],
                }
            )
        return selected

    def get_capture(self, query: OptionsStage4Query) -> dict[str, Any]:
        """Return a finite, read-only snapshot from one capture cohort only."""

        warnings: set[str] = set()
        with quiet_immutable_read_connection(self._store_map, StoreRole.MARKET) as connection:
            capture = self._select_capture(connection, query, warnings)
            capture_id = str(capture["capture_id"])
            surface_rows = self._surface_rows(connection, capture_id, query, warnings)
            result = {
                "contract": "quant_data.stage4_option_capture",
                "contract_version": "1.0.0",
                "capture": {
                    "capture_id": capture_id,
                    "semantic_identity": str(capture["semantic_identity"]),
                    "underlying_instrument_id": str(capture["underlying_instrument_id"]),
                    "requested_feed": str(capture["requested_feed"]),
                    "resolved_feed": str(capture["resolved_feed"]),
                    "environment": str(capture["environment"]),
                    "request_scope": loads_strict(str(capture["request_scope_json"])),
                    "requested_at": str(capture["requested_at"]),
                    "completed_at": str(capture["completed_at"]),
                    "available_at": str(capture["available_at"]),
                    "available_precision": str(capture["available_precision"]),
                    "completeness": str(capture["completeness"]),
                    "deliverable_policy": str(capture["deliverable_policy"]),
                    "artifact_id": str(capture["artifact_id"]),
                    "source_snapshot_id": str(capture["source_snapshot_id"]),
                },
                "as_of": None if query.cutoff is None else query.cutoff.raw,
                "date_only_policy": query.date_only_policy.value,
                "underlying": self._underlying(connection, capture_id),
                "inputs": self._inputs(connection, capture_id, query, warnings),
                "surface": surface_rows,
                "warnings": sorted(warnings),
            }
        result["lineage_digest"] = hashlib.sha256(dumps_strict(result).encode("utf-8")).hexdigest()
        return result

    def get_surface(self, query: OptionsStage4Query) -> dict[str, Any]:
        return self.get_capture(query)


__all__ = (
    "OptionsStage4Query",
    "OptionsStage4Repository",
    "ParsedOptionsFixture",
    "Stage4OptionsFixtureImporter",
    "parse_stage4_options_fixture",
)
