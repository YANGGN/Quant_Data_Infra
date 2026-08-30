"""Additive, bounded FMP full-history collection for the IWM ETF.

This module keeps the frozen Stage 10 95-ETF scope intact.  It derives one
successor curated-ETF snapshot from that reviewed scope plus the one expressly
authorized IWM addition, captures exactly one full FMP history before taking a
market write lock, and publishes the successor membership and price facts in
one replay-safe transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import http.client
from pathlib import Path
import re
import sqlite3
from typing import Final
from urllib.parse import urlencode
import weakref

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..ingestion import ArtifactWrite, IngestionCoordinator, QualityWrite, SnapshotWrite, WriteResult
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import HeldWriteLocks, StoreMap, StoreRole, canonical_path_uri, read_connection, stable_id
from .fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_HOST,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_MAX_PRICE_ROWS,
    FMP_STAGE10_PRICE_PATH,
    FMP_STAGE10_TIMEOUT_SECONDS,
    FmpStage10PriceCapture,
    FmpStage10PriceRow,
    FmpStage10Transport,
    capture_fmp_stage10_price,
    prepare_fmp_stage10_price_capture,
)
from .stage10_history_importer import (
    STAGE10_CURRENCY_SEGMENT,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_MIGRATION_ID,
    STAGE10_MIGRATION_RESOURCE,
    STAGE10_MIGRATION_SHA256,
    STAGE10_PRICE_NORMALIZATION_VERSION,
    STAGE10_PRICE_VARIANT,
    STAGE10_UNIVERSES_DATASET_ID,
)
from .stage10_scope import (
    CuratedEtf,
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    Stage10MarketScope,
)


IWM_ETF_HISTORY_COLLECTOR_ID: Final = "fmp.market.iwm_etf_daily_history"
IWM_ETF_HISTORY_HANDLER: Final = "market.fmp_iwm_etf_daily_history"
IWM_ETF_HISTORY_SCOPE_CONTRACT: Final = "quant_data.iwm_etf_history_scope"
IWM_ETF_HISTORY_SCOPE_VERSION: Final = "1.0.0"
IWM_ETF_HISTORY_NORMALIZATION_VERSION: Final = "stage10.iwm_etf_history.v1"
IWM_ETF_HISTORY_SCOPE_NORMALIZATION_VERSION: Final = "stage10.iwm_scope.v1"
IWM_ETF_HISTORY_SYMBOL: Final = "IWM"
IWM_ETF_HISTORY_UNIVERSE_ID: Final = "curated_etfs"
IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT: Final = 96
IWM_ETF_HISTORY_OUTPUT_DATASETS: Final = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_UNIVERSES_DATASET_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
)
IWM_ETF_HISTORY_INPUT_DATASETS: Final = (
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_UNIVERSES_DATASET_ID,
)
_IWM_ETF: Final = CuratedEtf(
    category="asset_class_equity_us",
    symbol=IWM_ETF_HISTORY_SYMBOL,
    themes=("us_small_cap",),
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")
_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SCOPE_MAX_BYTES: Final = 64 * 1024


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _utc_text(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"IWM ETF history {label} must be an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"IWM ETF history {label} is invalid")
    return value


def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("IWM ETF history requires explicit stores")
    return tuple((role.value, canonical_path_uri(path)) for role, path in store_map.items())


@dataclass(frozen=True, slots=True)
class IwmEtfHistoryBounds:
    max_price_response_bytes: int
    max_price_rows: int
    max_requests: int
    max_seconds_per_request: int

    def manifest_mapping(self) -> dict[str, int]:
        return {
            "max_price_response_bytes": self.max_price_response_bytes,
            "max_price_rows": self.max_price_rows,
            "max_requests": self.max_requests,
            "max_seconds_per_request": self.max_seconds_per_request,
        }


@dataclass(frozen=True, slots=True)
class IwmEtfHistoryScope:
    """The fixed additive extension, bound to one immutable Stage 10 base."""

    base_scope: Stage10MarketScope = field(repr=False)
    base_scope_manifest_sha256: str
    bounds: IwmEtfHistoryBounds
    manifest_sha256: str
    price_history: Mapping[str, object]
    target_profile_id: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "base_scope_manifest_sha256": self.base_scope_manifest_sha256,
            "bounds": self.bounds.manifest_mapping(),
            "contract": IWM_ETF_HISTORY_SCOPE_CONTRACT,
            "etf": _IWM_ETF.manifest_mapping(),
            "price_history": dict(self.price_history),
            "provider": "fmp",
            "retry_policy": {"max_attempts": 1, "mode": "none"},
            "successor_member_count": IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
            "target_profile_id": self.target_profile_id,
            "universe_id": IWM_ETF_HISTORY_UNIVERSE_ID,
            "version": IWM_ETF_HISTORY_SCOPE_VERSION,
        }

    @property
    def successor_etfs(self) -> tuple[CuratedEtf, ...]:
        return (*self.base_scope.curated_etfs, _IWM_ETF)

    @property
    def evidence_bytes(self) -> bytes:
        return dumps_strict(
            {
                "base_stage10_scope_manifest": self.base_scope.manifest_mapping(),
                "iwm_successor_scope": self.manifest_mapping(),
            }
        ).encode("utf-8")


def _validate_base_scope(base_scope: object) -> Stage10MarketScope:
    if not isinstance(base_scope, Stage10MarketScope):
        raise ValidationError("IWM ETF history requires the reviewed Stage 10 base scope")
    if (
        base_scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or _sha256_json(base_scope.manifest_mapping()) != REVIEWED_STAGE10_MANIFEST_SHA256
        or base_scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or base_scope.provider != "fmp"
        or len(base_scope.curated_etfs) != 95
        or base_scope.price_history.endpoint_path != FMP_STAGE10_PRICE_PATH
        or base_scope.price_history.interval != "daily"
        or base_scope.price_history.history_policy
        != "earliest_available_per_provider_symbol"
        or base_scope.price_history.price_variant != STAGE10_PRICE_VARIANT
        or base_scope.price_history.currency_policy != "provider_declared_no_conversion"
        or base_scope.price_history.volume_policy
        != "provider_value_nonnegative_zero_allowed"
    ):
        raise ValidationError("IWM ETF history base scope is not the frozen Stage 10 profile")
    symbols = tuple(item.symbol for item in base_scope.curated_etfs)
    if len(symbols) != len(set(symbols)) or IWM_ETF_HISTORY_SYMBOL in symbols:
        raise ValidationError("IWM ETF history base ETF membership is invalid")
    return base_scope


def _scope_mapping(base_scope: Stage10MarketScope) -> dict[str, object]:
    return {
        "base_scope_manifest_sha256": REVIEWED_STAGE10_MANIFEST_SHA256,
        "bounds": {
            "max_price_response_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
            "max_price_rows": FMP_STAGE10_MAX_PRICE_ROWS,
            "max_requests": 1,
            "max_seconds_per_request": FMP_STAGE10_TIMEOUT_SECONDS,
        },
        "contract": IWM_ETF_HISTORY_SCOPE_CONTRACT,
        "etf": _IWM_ETF.manifest_mapping(),
        "price_history": base_scope.price_history.manifest_mapping(),
        "provider": "fmp",
        "retry_policy": {"max_attempts": 1, "mode": "none"},
        "successor_member_count": IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
        "target_profile_id": STAGE10_TARGET_PROFILE_ID,
        "universe_id": IWM_ETF_HISTORY_UNIVERSE_ID,
        "version": IWM_ETF_HISTORY_SCOPE_VERSION,
    }


def load_iwm_etf_history_scope(
    path: str | Path,
    *,
    base_scope: Stage10MarketScope,
) -> IwmEtfHistoryScope:
    """Load the closed IWM extension and bind it to the reviewed base scope."""

    base = _validate_base_scope(base_scope)
    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise ValidationError("IWM ETF history scope path is invalid")
    try:
        payload = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise ValidationError("IWM ETF history scope is unavailable") from exc
    if not payload or len(payload) > _SCOPE_MAX_BYTES:
        raise ValidationError("IWM ETF history scope is outside the byte bound")
    raw = loads_strict(payload, max_bytes=_SCOPE_MAX_BYTES)
    expected = _scope_mapping(base)
    if not isinstance(raw, Mapping) or dumps_strict(raw) != dumps_strict(expected):
        raise ValidationError("IWM ETF history scope differs from the reviewed extension")
    manifest_sha256 = _sha256_json(expected)
    return IwmEtfHistoryScope(
        base_scope=base,
        base_scope_manifest_sha256=REVIEWED_STAGE10_MANIFEST_SHA256,
        bounds=IwmEtfHistoryBounds(
            max_price_response_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
            max_price_rows=FMP_STAGE10_MAX_PRICE_ROWS,
            max_requests=1,
            max_seconds_per_request=FMP_STAGE10_TIMEOUT_SECONDS,
        ),
        manifest_sha256=manifest_sha256,
        price_history=base.price_history.manifest_mapping(),
        target_profile_id=STAGE10_TARGET_PROFILE_ID,
    )


class StdlibFmpIwmEtfHistoryTransport:
    """TLS-verified stdlib transport fixed to one IWM full-history request."""

    @staticmethod
    def _validate_request(
        *,
        path: object,
        query: object,
        headers: object,
        timeout_seconds: object,
        max_bytes: object,
    ) -> tuple[dict[str, str], dict[str, str]]:
        if not isinstance(path, str) or path != FMP_STAGE10_PRICE_PATH:
            raise ValidationError("IWM ETF history transport path is invalid")
        if not isinstance(query, Mapping) or not isinstance(headers, Mapping):
            raise ValidationError("IWM ETF history transport request is invalid")
        query_values = dict(query)
        header_values = dict(headers)
        if (
            query_values != {"symbol": IWM_ETF_HISTORY_SYMBOL}
            or set(header_values) != {"apikey"}
            or not isinstance(header_values["apikey"], str)
            or _API_KEY.fullmatch(header_values["apikey"]) is None
            or timeout_seconds != FMP_STAGE10_TIMEOUT_SECONDS
            or max_bytes != FMP_STAGE10_MAX_PRICE_BYTES
        ):
            raise ValidationError("IWM ETF history transport request is outside fixed scope")
        return query_values, header_values

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> CapturedFmpStage10Response:
        query_values, header_values = self._validate_request(
            path=path,
            query=query,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        target = f"{FMP_STAGE10_PRICE_PATH}?{urlencode(query_values)}"
        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(
                FMP_STAGE10_HOST,
                timeout=FMP_STAGE10_TIMEOUT_SECONDS,
            )
            connection.request("GET", target, headers=header_values)
            response = connection.getresponse()
            declared_size = response.getheader("Content-Length")
            if declared_size is not None:
                if not isinstance(declared_size, str) or not declared_size.isdecimal():
                    raise StoreUnavailableError("FMP provider response was unavailable")
                if int(declared_size) > FMP_STAGE10_MAX_PRICE_BYTES:
                    raise ResourceLimitError("FMP response exceeds the IWM byte bound")
            body = response.read(FMP_STAGE10_MAX_PRICE_BYTES + 1)
            if len(body) > FMP_STAGE10_MAX_PRICE_BYTES:
                raise ResourceLimitError("FMP response exceeds the IWM byte bound")
            return CapturedFmpStage10Response(
                status=response.status,
                content_type=response.getheader("Content-Type") or "application/octet-stream",
                body=body,
            )
        except (ResourceLimitError, StoreUnavailableError, ValidationError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("FMP provider request failed") from exc
        finally:
            if connection is not None:
                connection.close()


def capture_iwm_etf_history(
    *,
    api_key: str,
    transport: FmpStage10Transport,
) -> FmpStage10PriceCapture:
    """Capture and parse exactly one fixed IWM FMP full-history response."""

    return capture_fmp_stage10_price(
        prepare_fmp_stage10_price_capture(IWM_ETF_HISTORY_SYMBOL),
        api_key=api_key,
        transport=transport,
    )


def _validate_registry(registry: object) -> Registry:
    if not isinstance(registry, Registry) or registry.status != "validated":
        raise ValidationError("IWM ETF history requires the validated current registry")
    migration = next(
        (item for item in registry.migrations if item.id == STAGE10_MIGRATION_ID),
        None,
    )
    if (
        migration is None
        or migration.store != StoreRole.MARKET.value
        or migration.ordinal != 10
        or migration.resource != STAGE10_MIGRATION_RESOURCE
        or migration.sha256 != STAGE10_MIGRATION_SHA256
        or migration.reconstruction_state != "fixture_validated"
    ):
        raise ValidationError("IWM ETF history requires the existing Stage 10 migration")
    datasets = {item.id: item for item in registry.datasets}
    for dataset_id in IWM_ETF_HISTORY_OUTPUT_DATASETS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != StoreRole.MARKET.value
            or not dataset.active
            or IWM_ETF_HISTORY_COLLECTOR_ID not in dataset.collector_ids
        ):
            raise ValidationError("IWM ETF history output binding is invalid")
    for dataset_id in IWM_ETF_HISTORY_INPUT_DATASETS:
        dataset = datasets.get(dataset_id)
        if dataset is None or dataset.store != StoreRole.MARKET.value or not dataset.active:
            raise ValidationError("IWM ETF history input binding is invalid")
    declarations = [
        item for item in registry.collectors
        if item.get("id") == IWM_ETF_HISTORY_COLLECTOR_ID
    ]
    if len(declarations) != 1:
        raise ValidationError("IWM ETF history collector is not registered exactly once")
    declaration = declarations[0]
    expected_semantic_identity = {
        "excludes": ["api_key", "captured_at", "http_headers", "source_row_order"],
        "includes": [
            "scope_manifest_sha256",
            "base_scope_manifest_sha256",
            "successor_membership_sha256",
            "request_scope",
            "normalization_version",
            "normalized_complete_history_sha256",
        ],
    }
    if (
        declaration.get("handler") != IWM_ETF_HISTORY_HANDLER
        or declaration.get("network") is not True
        or tuple(declaration.get("input_datasets", ())) != IWM_ETF_HISTORY_INPUT_DATASETS
        or tuple(declaration.get("output_datasets", ())) != IWM_ETF_HISTORY_OUTPUT_DATASETS
        or tuple(declaration.get("configuration_env", ())) != ("FMP_API_KEY",)
        or declaration.get("physical_locks") != "derived_from_output_store_paths"
        or declaration.get("schedule_eligibility") != {"mode": "manual_only"}
        or declaration.get("workload_bounds") != {
            "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
            "max_requests": 1,
            "max_rows": FMP_STAGE10_MAX_PRICE_ROWS,
            "max_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
        }
        or declaration.get("retry_policy") != {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        }
        or declaration.get("mutation_policy") != {
            "mode": "append_successor_universe_and_price_versions",
            "unchanged": "zero_persistent_writes",
        }
        or declaration.get("semantic_identity") != expected_semantic_identity
        or declaration.get("version") != "1.0.0"
    ):
        raise ValidationError("IWM ETF history collector declaration is invalid")
    return registry


@dataclass(frozen=True, slots=True)
class _InstrumentPlan:
    provider_symbol: str
    asset_type: str
    display_name: str | None
    first_trade_date: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_symbol, str)
            or _SYMBOL.fullmatch(self.provider_symbol) is None
            or self.asset_type != "etf"
            or self.display_name is not None
            or (
                self.first_trade_date is not None
                and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.first_trade_date)
            )
        ):
            raise ValidationError("IWM ETF history instrument plan is invalid")

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


def _successor_members(scope: IwmEtfHistoryScope) -> tuple[_InstrumentPlan, ...]:
    members = tuple(
        _InstrumentPlan(
            provider_symbol=item.symbol,
            asset_type="etf",
            display_name=None,
            first_trade_date=item.first_trade_date,
        )
        for item in scope.successor_etfs
    )
    if (
        len(members) != IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT
        or members[-1].provider_symbol != IWM_ETF_HISTORY_SYMBOL
        or len({item.provider_symbol for item in members}) != len(members)
    ):
        raise ValidationError("IWM ETF successor membership is invalid")
    return members


def _membership_sha256(members: tuple[_InstrumentPlan, ...]) -> str:
    return _sha256_json(
        [item.membership_mapping() for item in sorted(members, key=lambda item: item.instrument_id)]
    )


def _same_instrument_record(row: sqlite3.Row, plan: _InstrumentPlan) -> bool:
    return tuple(row) == (
        "fmp",
        plan.provider_symbol,
        plan.asset_type,
        plan.display_name,
        None,
        STAGE10_CURRENCY_SEGMENT,
        plan.first_trade_date,
        plan.identity_seed_sha256,
    )


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("IWM ETF history price is invalid")
    return "0" if value.is_zero() else format(value.normalize(), "f")


def _same_price_values(current: sqlite3.Row, row: FmpStage10PriceRow) -> bool:
    return (
        current["open_value"] == _decimal_text(row.open_value)
        and current["high_value"] == _decimal_text(row.high_value)
        and current["low_value"] == _decimal_text(row.low_value)
        and current["close_value"] == _decimal_text(row.close_value)
        and current["volume"] == row.volume
    )


@dataclass(frozen=True, slots=True)
class _Publication:
    scope: IwmEtfHistoryScope
    members: tuple[_InstrumentPlan, ...]
    iwm: _InstrumentPlan
    successor_membership_sha256: str
    capture: FmpStage10PriceCapture
    scope_evidence_bytes: bytes = field(repr=False)
    scope_evidence_sha256: str
    universe_request_scope: Mapping[str, object]
    price_request_scope: Mapping[str, object]
    semantic_identity: str
    captured_at: str
    run_id: str
    price_artifact_id: str
    scope_artifact_id: str
    snapshot_id: str
    capture_id: str
    scope_snapshot_id: str
    universe_snapshot_id: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    store_map: StoreMap
    store_map_identity: tuple[tuple[str, str], ...]
    publication: _Publication


@dataclass(frozen=True, slots=True)
class _StoreState:
    scope: IwmEtfHistoryScope
    members: tuple[_InstrumentPlan, ...]
    iwm: _InstrumentPlan
    successor_membership_sha256: str
    scope_snapshot_id: str
    universe_snapshot_id: str


class PreparedIwmEtfHistoryPublication:
    """Opaque collector-bound candidate; it has no raw response repr."""

    __slots__ = ("__weakref__",)


_PREPARED: "weakref.WeakKeyDictionary[PreparedIwmEtfHistoryPublication, _PreparedState]" = (
    weakref.WeakKeyDictionary()
)


@dataclass(frozen=True, slots=True)
class IwmEtfHistoryRunReport:
    """Small credential- and path-free result of the fixed IWM collection."""

    outcome: str
    base_scope_manifest_sha256: str
    scope_manifest_sha256: str
    successor_membership_sha256: str
    normalized_complete_history_sha256: str
    response_sha256: str
    price_row_count: int
    successor_member_count: int
    written_count: int

    def mapping(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "base_scope_manifest_sha256": self.base_scope_manifest_sha256,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "successor_membership_sha256": self.successor_membership_sha256,
            "normalized_complete_history_sha256": self.normalized_complete_history_sha256,
            "response_sha256": self.response_sha256,
            "price_row_count": self.price_row_count,
            "successor_member_count": self.successor_member_count,
            "written_count": self.written_count,
        }


class IwmEtfHistoryPublisher:
    """Prepare and atomically publish the one IWM successor and price capture."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        scope: IwmEtfHistoryScope,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not isinstance(scope, IwmEtfHistoryScope):
            raise ValidationError("IWM ETF history publisher dependencies are invalid")
        if not callable(clock):
            raise ValidationError("IWM ETF history publisher clock is invalid")
        self.store_map = store_map
        self.registry = _validate_registry(registry)
        self.scope = scope
        self._clock = clock
        self._coordinator = IngestionCoordinator(
            store_map,
            code_version="iwm.etf.history.1.0.0",
        )
        self._owner_token = object()

    def _new_prepared(self, publication: _Publication) -> PreparedIwmEtfHistoryPublication:
        candidate = PreparedIwmEtfHistoryPublication()
        _PREPARED[candidate] = _PreparedState(
            owner_token=self._owner_token,
            store_map=self.store_map,
            store_map_identity=_store_map_identity(self.store_map),
            publication=publication,
        )
        return candidate

    def _prepared_state(self, prepared: object) -> _PreparedState:
        if not isinstance(prepared, PreparedIwmEtfHistoryPublication):
            raise ValidationError("IWM ETF history requires a prepared publication")
        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.store_map is not self.store_map
            or state.store_map_identity != _store_map_identity(self.store_map)
        ):
            raise ValidationError("IWM ETF history prepared publication is foreign or expired")
        return state

    @staticmethod
    def _same_instrument_row(row: sqlite3.Row, plan: _InstrumentPlan) -> bool:
        return (
            row["provider"] == "fmp"
            and row["provider_symbol"] == plan.provider_symbol
            and row["asset_type"] == plan.asset_type
            and row["display_name"] == plan.display_name
            and row["exchange_code"] is None
            and row["currency_segment"] == STAGE10_CURRENCY_SEGMENT
            and row["first_trade_date"] == plan.first_trade_date
            and row["identity_seed_sha256"] == plan.identity_seed_sha256
        )

    def _validate_store_state(
        self,
        connection: sqlite3.Connection,
        publication: _Publication | _StoreState,
    ) -> None:
        base_scope_id = stable_id(
            "stage10_scope_snapshot",
            publication.scope.base_scope_manifest_sha256,
        )
        base_scope = connection.execute(
            """
            SELECT scope_snapshot_id, target_profile_id, provider
            FROM stage10_scope_snapshots
            WHERE scope_manifest_sha256=?
            """,
            (publication.scope.base_scope_manifest_sha256,),
        ).fetchall()
        if (
            len(base_scope) != 1
            or str(base_scope[0]["scope_snapshot_id"]) != base_scope_id
            or tuple(base_scope[0][1:]) != (STAGE10_TARGET_PROFILE_ID, "fmp")
        ):
            raise ValidationError("IWM ETF history requires the frozen Stage 10 base snapshot")
        universe = connection.execute(
            """
            SELECT universe_kind, publisher, source_authority
            FROM stage10_universes WHERE universe_id=?
            """,
            (IWM_ETF_HISTORY_UNIVERSE_ID,),
        ).fetchone()
        if universe is None or tuple(universe) != (
            "instrument_watchlist",
            "stage10_reviewed_scope",
            "reviewed_local_manifest",
        ):
            raise ValidationError("IWM ETF history base curated ETF universe is invalid")
        base_members = publication.members[:-1]
        base_snapshot = connection.execute(
            """
            SELECT snapshot.universe_snapshot_id, snapshot.member_count,
                   snapshot.membership_sha256, snapshot.completeness,
                   snapshot.capture_id, snapshot.membership_relation
            FROM stage10_universe_snapshots AS snapshot
            JOIN stage10_scope_snapshots AS scope
              ON scope.scope_snapshot_id=snapshot.scope_snapshot_id
            WHERE scope.scope_manifest_sha256=? AND snapshot.universe_id=?
            """,
            (
                publication.scope.base_scope_manifest_sha256,
                IWM_ETF_HISTORY_UNIVERSE_ID,
            ),
        ).fetchall()
        if (
            len(base_snapshot) != 1
            or tuple(base_snapshot[0][1:]) != (
                len(base_members),
                _membership_sha256(base_members),
                "complete",
                None,
                "reviewed_instrument_watchlist",
            )
        ):
            raise ValidationError("IWM ETF history base curated ETF snapshot is invalid")
        expected_base = {item.instrument_id: item for item in base_members}
        base_rows = tuple(
            connection.execute(
                """
                SELECT instrument.instrument_id, instrument.provider, instrument.provider_symbol,
                       instrument.asset_type, instrument.display_name, instrument.exchange_code,
                       instrument.currency_segment, instrument.first_trade_date,
                       instrument.identity_seed_sha256
                FROM stage10_universe_snapshot_members AS member
                JOIN stage10_instruments AS instrument
                  ON instrument.instrument_id=member.instrument_id
                WHERE member.universe_snapshot_id=?
                ORDER BY instrument.instrument_id
                """,
                (base_snapshot[0]["universe_snapshot_id"],),
            )
        )
        if len(base_rows) != len(expected_base):
            raise ValidationError("IWM ETF history base membership is incomplete")
        for row in base_rows:
            plan = expected_base.get(str(row["instrument_id"]))
            if plan is None or not self._same_instrument_row(row, plan):
                raise ConflictError("IWM ETF history base membership identity conflicts")
        successor_scope = connection.execute(
            """
            SELECT scope_snapshot_id, target_profile_id, provider
            FROM stage10_scope_snapshots
            WHERE scope_manifest_sha256=?
            """,
            (publication.scope.manifest_sha256,),
        ).fetchall()
        if len(successor_scope) > 1 or (
            successor_scope
            and (
                str(successor_scope[0]["scope_snapshot_id"]) != publication.scope_snapshot_id
                or tuple(successor_scope[0][1:]) != (STAGE10_TARGET_PROFILE_ID, "fmp")
            )
        ):
            raise ConflictError("IWM ETF history successor scope conflicts")
        successor_snapshot = connection.execute(
            """
            SELECT universe_snapshot_id, member_count, membership_sha256,
                   completeness, capture_id, membership_relation
            FROM stage10_universe_snapshots
            WHERE scope_snapshot_id=? AND universe_id=?
            """,
            (publication.scope_snapshot_id, IWM_ETF_HISTORY_UNIVERSE_ID),
        ).fetchall()
        if len(successor_snapshot) > 1 or (
            successor_snapshot
            and (
                str(successor_snapshot[0]["universe_snapshot_id"])
                != publication.universe_snapshot_id
                or tuple(successor_snapshot[0][1:]) != (
                    IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
                    publication.successor_membership_sha256,
                    "complete",
                    None,
                    "reviewed_instrument_watchlist",
                )
            )
        ):
            raise ConflictError("IWM ETF history successor membership conflicts")
        if bool(successor_scope) != bool(successor_snapshot):
            raise ConflictError("IWM ETF history successor state is incomplete")
        if successor_snapshot:
            expected_successor = {
                member.instrument_id: (source_row, member)
                for source_row, member in enumerate(publication.members, start=1)
            }
            successor_rows = tuple(
                connection.execute(
                    """
                    SELECT member.source_row, instrument.instrument_id, instrument.provider,
                           instrument.provider_symbol, instrument.asset_type,
                           instrument.display_name, instrument.exchange_code,
                           instrument.currency_segment, instrument.first_trade_date,
                           instrument.identity_seed_sha256
                    FROM stage10_universe_snapshot_members AS member
                    JOIN stage10_instruments AS instrument
                      ON instrument.instrument_id=member.instrument_id
                    WHERE member.universe_snapshot_id=?
                    ORDER BY member.source_row
                    """,
                    (successor_snapshot[0]["universe_snapshot_id"],),
                )
            )
            if len(successor_rows) != len(expected_successor):
                raise ConflictError("IWM ETF history successor membership is incomplete")
            for row in successor_rows:
                expected = expected_successor.get(str(row["instrument_id"]))
                if (
                    expected is None
                    or int(row["source_row"]) != expected[0]
                    or not self._same_instrument_row(row, expected[1])
                ):
                    raise ConflictError("IWM ETF history successor membership identity conflicts")
        symbol_row = connection.execute(
            """
            SELECT instrument_id FROM stage10_instruments
            WHERE provider='fmp' AND provider_symbol=?
            """,
            (IWM_ETF_HISTORY_SYMBOL,),
        ).fetchone()
        if symbol_row is not None and str(symbol_row["instrument_id"]) != publication.iwm.instrument_id:
            raise ConflictError("IWM ETF history provider symbol is bound elsewhere")
        iwm_row = connection.execute(
            """
            SELECT provider, provider_symbol, asset_type, display_name, exchange_code,
                   currency_segment, first_trade_date, identity_seed_sha256
            FROM stage10_instruments WHERE instrument_id=?
            """,
            (publication.iwm.instrument_id,),
        ).fetchone()
        if iwm_row is not None and not _same_instrument_record(iwm_row, publication.iwm):
            raise ConflictError("IWM ETF history instrument identity conflicts")

    def _store_preflight_state(self) -> _StoreState:
        members = _successor_members(self.scope)
        membership_sha256 = _membership_sha256(members)
        return _StoreState(
            scope=self.scope,
            members=members,
            iwm=members[-1],
            successor_membership_sha256=membership_sha256,
            scope_snapshot_id=stable_id(
                "stage10_scope_snapshot",
                self.scope.manifest_sha256,
            ),
            universe_snapshot_id=stable_id(
                "iwm_etf_history_universe_snapshot",
                STAGE10_UNIVERSES_DATASET_ID,
                self.scope.manifest_sha256,
                membership_sha256,
            ),
        )

    def preflight_store(self) -> None:
        """Fail before credential/network if frozen-base state is unavailable."""

        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self._validate_store_state(connection, self._store_preflight_state())

    def prepare(
        self,
        capture: FmpStage10PriceCapture,
    ) -> PreparedIwmEtfHistoryPublication:
        """Validate a fetched IWM capture before any writer lock is acquired."""

        if not isinstance(capture, FmpStage10PriceCapture) or capture.symbol != IWM_ETF_HISTORY_SYMBOL:
            raise ValidationError("IWM ETF history capture is invalid")
        captured_at = _utc_text(self._clock(), "captured_at")
        if capture.latest_date > captured_at[:10]:
            raise ValidationError("IWM ETF history cannot contain future trade dates")
        members = _successor_members(self.scope)
        iwm = members[-1]
        membership_sha256 = _membership_sha256(members)
        universe_scope: dict[str, object] = {
            "provider": "reviewed_local_manifest",
            "universe_id": IWM_ETF_HISTORY_UNIVERSE_ID,
            "membership_relation": "reviewed_instrument_watchlist",
            "base_scope_manifest_sha256": self.scope.base_scope_manifest_sha256,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
        }
        price_scope: dict[str, object] = {
            "provider": "fmp",
            "endpoint_path": FMP_STAGE10_PRICE_PATH,
            "symbol": IWM_ETF_HISTORY_SYMBOL,
            "interval": self.scope.price_history["interval"],
            "history_policy": self.scope.price_history["history_policy"],
            "price_variant": self.scope.price_history["price_variant"],
            "currency_policy": self.scope.price_history["currency_policy"],
            "volume_policy": self.scope.price_history["volume_policy"],
            "base_scope_manifest_sha256": self.scope.base_scope_manifest_sha256,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
        }
        semantic_identity = _sha256_json(
            {
                "collector_id": IWM_ETF_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "scope_manifest_sha256": self.scope.manifest_sha256,
                "base_scope_manifest_sha256": self.scope.base_scope_manifest_sha256,
                "successor_membership_sha256": membership_sha256,
                "request_scope": price_scope,
                "normalization_version": IWM_ETF_HISTORY_NORMALIZATION_VERSION,
                "normalized_complete_history_sha256": capture.semantic_sha256,
            }
        )
        _require_digest(semantic_identity, "semantic identity")
        scope_evidence = self.scope.evidence_bytes
        publication = _Publication(
            scope=self.scope,
            members=members,
            iwm=iwm,
            successor_membership_sha256=membership_sha256,
            capture=capture,
            scope_evidence_bytes=scope_evidence,
            scope_evidence_sha256=_sha256_bytes(scope_evidence),
            universe_request_scope=universe_scope,
            price_request_scope=price_scope,
            semantic_identity=semantic_identity,
            captured_at=captured_at,
            run_id=stable_id(
                "iwm_etf_history_run",
                STAGE10_DAILY_PRICES_DATASET_ID,
                semantic_identity,
            ),
            price_artifact_id=stable_id(
                "iwm_etf_history_price_artifact",
                STAGE10_EVIDENCE_DATASET_ID,
                IWM_ETF_HISTORY_SYMBOL,
                capture.raw_bytes_sha256,
                _sha256_json(price_scope),
            ),
            scope_artifact_id=stable_id(
                "iwm_etf_history_scope_artifact",
                STAGE10_EVIDENCE_DATASET_ID,
                self.scope.manifest_sha256,
            ),
            snapshot_id=stable_id(
                "iwm_etf_history_snapshot",
                STAGE10_DAILY_PRICES_DATASET_ID,
                semantic_identity,
            ),
            capture_id=stable_id(
                "iwm_etf_history_capture",
                STAGE10_EVIDENCE_DATASET_ID,
                semantic_identity,
            ),
            scope_snapshot_id=stable_id(
                "stage10_scope_snapshot",
                self.scope.manifest_sha256,
            ),
            universe_snapshot_id=stable_id(
                "iwm_etf_history_universe_snapshot",
                STAGE10_UNIVERSES_DATASET_ID,
                self.scope.manifest_sha256,
                membership_sha256,
            ),
        )
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self._validate_store_state(connection, publication)
        return self._new_prepared(publication)

    def publish(
        self,
        prepared: PreparedIwmEtfHistoryPublication,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Publish one prevalidated candidate without provider activity."""

        state = self._prepared_state(prepared)
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("IWM ETF history held locks are invalid")
            held_locks._require_target(self.store_map, StoreRole.MARKET)
        return self._publish(state.publication, held_locks=held_locks)

    def _publish(
        self,
        publication: _Publication,
        *,
        held_locks: HeldWriteLocks | None,
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("IWM ETF history coordinator run identity is invalid")
            self._validate_store_state(connection, publication)
            written = 0
            scope_row = connection.execute(
                """
                SELECT scope_snapshot_id, target_profile_id, provider
                FROM stage10_scope_snapshots
                WHERE scope_manifest_sha256=?
                """,
                (publication.scope.manifest_sha256,),
            ).fetchone()
            scope_created = scope_row is None
            if scope_row is None:
                connection.execute(
                    """
                    INSERT INTO stage10_scope_snapshots (
                        scope_snapshot_id, scope_manifest_sha256, target_profile_id,
                        provider, captured_at, captured_precision, run_id
                    ) VALUES (?, ?, ?, 'fmp', ?, 'datetime', ?)
                    """,
                    (
                        publication.scope_snapshot_id,
                        publication.scope.manifest_sha256,
                        publication.scope.target_profile_id,
                        publication.captured_at,
                        active_run_id,
                    ),
                )
                written += 1
            elif (
                str(scope_row["scope_snapshot_id"]) != publication.scope_snapshot_id
                or tuple(scope_row[1:]) != (STAGE10_TARGET_PROFILE_ID, "fmp")
            ):
                raise ConflictError("IWM ETF history successor scope conflicts")
            iwm_row = connection.execute(
                """
                SELECT provider, provider_symbol, asset_type, display_name, exchange_code,
                       currency_segment, first_trade_date, identity_seed_sha256
                FROM stage10_instruments WHERE instrument_id=?
                """,
                (publication.iwm.instrument_id,),
            ).fetchone()
            if iwm_row is None:
                collision = connection.execute(
                    """
                    SELECT instrument_id FROM stage10_instruments
                    WHERE provider='fmp' AND provider_symbol=?
                    """,
                    (IWM_ETF_HISTORY_SYMBOL,),
                ).fetchone()
                if collision is not None:
                    raise ConflictError("IWM ETF history provider symbol is bound elsewhere")
                connection.execute(
                    """
                    INSERT INTO stage10_instruments (
                        instrument_id, provider, provider_symbol, asset_type, display_name,
                        exchange_code, currency_segment, first_trade_date,
                        identity_seed_sha256, captured_at, captured_precision, run_id
                    ) VALUES (?, 'fmp', ?, 'etf', NULL, NULL, ?, NULL, ?, ?, 'datetime', ?)
                    """,
                    (
                        publication.iwm.instrument_id,
                        IWM_ETF_HISTORY_SYMBOL,
                        STAGE10_CURRENCY_SEGMENT,
                        publication.iwm.identity_seed_sha256,
                        publication.captured_at,
                        active_run_id,
                    ),
                )
                written += 1
            elif not _same_instrument_record(iwm_row, publication.iwm):
                raise ConflictError("IWM ETF history stable instrument identity conflicts")
            universe_row = connection.execute(
                """
                SELECT universe_snapshot_id, member_count, membership_sha256,
                       completeness, capture_id, membership_relation
                FROM stage10_universe_snapshots
                WHERE scope_snapshot_id=? AND universe_id=?
                """,
                (publication.scope_snapshot_id, IWM_ETF_HISTORY_UNIVERSE_ID),
            ).fetchone()
            successor_created = universe_row is None
            if universe_row is None:
                connection.execute(
                    """
                    INSERT INTO stage10_universe_snapshots (
                        universe_snapshot_id, universe_id, scope_snapshot_id, capture_id,
                        as_of_date, captured_at, captured_precision, completeness,
                        membership_relation, member_count, membership_sha256, run_id
                    ) VALUES (?, ?, ?, NULL, ?, ?, 'datetime', 'complete', ?, ?, ?, ?)
                    """,
                    (
                        publication.universe_snapshot_id,
                        IWM_ETF_HISTORY_UNIVERSE_ID,
                        publication.scope_snapshot_id,
                        publication.captured_at[:10],
                        publication.captured_at,
                        "reviewed_instrument_watchlist",
                        IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
                        publication.successor_membership_sha256,
                        active_run_id,
                    ),
                )
                written += 1
                for source_row, member in enumerate(publication.members, start=1):
                    connection.execute(
                        """
                        INSERT INTO stage10_universe_snapshot_members (
                            universe_snapshot_id, instrument_id, source_row
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            publication.universe_snapshot_id,
                            member.instrument_id,
                            source_row,
                        ),
                    )
                    written += 1
            elif (
                str(universe_row["universe_snapshot_id"]) != publication.universe_snapshot_id
                or tuple(universe_row[1:]) != (
                    IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
                    publication.successor_membership_sha256,
                    "complete",
                    None,
                    "reviewed_instrument_watchlist",
                )
            ):
                raise ConflictError("IWM ETF history successor membership conflicts")
            capture = publication.capture
            connection.execute(
                """
                INSERT INTO stage10_daily_price_captures (
                    capture_id, dataset_id, provider, instrument_id, provider_symbol,
                    endpoint_path, scope_manifest_sha256, request_scope_json,
                    request_scope_sha256, response_sha256, response_bytes, http_status,
                    content_type, semantic_identity, completeness, artifact_id,
                    snapshot_id, captured_at, captured_precision, earliest_trade_date,
                    latest_trade_date, row_count, normalization_version, run_id
                ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, 200, 'application/json',
                          ?, 'complete', ?, ?, ?, 'datetime', ?, ?, ?, ?, ?)
                """,
                (
                    publication.capture_id,
                    STAGE10_EVIDENCE_DATASET_ID,
                    publication.iwm.instrument_id,
                    IWM_ETF_HISTORY_SYMBOL,
                    FMP_STAGE10_PRICE_PATH,
                    publication.scope.manifest_sha256,
                    dumps_strict(dict(publication.price_request_scope)),
                    _sha256_json(publication.price_request_scope),
                    capture.raw_bytes_sha256,
                    capture.raw_bytes,
                    publication.semantic_identity,
                    publication.price_artifact_id,
                    publication.snapshot_id,
                    publication.captured_at,
                    capture.earliest_date,
                    capture.latest_date,
                    len(capture.rows),
                    STAGE10_PRICE_NORMALIZATION_VERSION,
                    active_run_id,
                ),
            )
            written += 1
            for row in capture.rows:
                current = connection.execute(
                    """
                    SELECT version.version_id, version.correction_sequence,
                           version.open_value, version.high_value, version.low_value,
                           version.close_value, version.volume
                    FROM stage10_daily_prices AS current
                    JOIN stage10_daily_price_versions AS version
                      ON version.version_id=current.current_version_id
                    WHERE current.instrument_id=? AND current.trade_date=?
                      AND current.provider='fmp' AND current.price_variant=?
                      AND current.currency_segment=?
                    """,
                    (
                        publication.iwm.instrument_id,
                        row.trade_date,
                        STAGE10_PRICE_VARIANT,
                        STAGE10_CURRENCY_SEGMENT,
                    ),
                ).fetchone()
                if current is not None and _same_price_values(current, row):
                    continue
                correction_sequence = (
                    1 if current is None else int(current["correction_sequence"]) + 1
                )
                supersedes = None if current is None else str(current["version_id"])
                version_id = stable_id(
                    "stage10_daily_price_version",
                    publication.iwm.instrument_id,
                    row.trade_date,
                    STAGE10_PRICE_VARIANT,
                    STAGE10_CURRENCY_SEGMENT,
                    str(correction_sequence),
                    publication.semantic_identity,
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_price_versions (
                        version_id, instrument_id, trade_date, provider, price_variant,
                        currency_segment, open_value, high_value, low_value, close_value,
                        volume, available_at, available_precision, captured_at,
                        captured_precision, correction_sequence, supersedes_version_id,
                        capture_id, artifact_id, snapshot_id, run_id, source_row
                    ) VALUES (?, ?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, ?, 'datetime', ?,
                              'datetime', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        publication.iwm.instrument_id,
                        row.trade_date,
                        STAGE10_PRICE_VARIANT,
                        STAGE10_CURRENCY_SEGMENT,
                        _decimal_text(row.open_value),
                        _decimal_text(row.high_value),
                        _decimal_text(row.low_value),
                        _decimal_text(row.close_value),
                        row.volume,
                        publication.captured_at,
                        publication.captured_at,
                        correction_sequence,
                        supersedes,
                        publication.capture_id,
                        publication.price_artifact_id,
                        publication.snapshot_id,
                        active_run_id,
                        row.source_row,
                    ),
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO stage10_daily_prices (
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, 'fmp', ?, ?, ?)
                        """,
                        (
                            publication.iwm.instrument_id,
                            row.trade_date,
                            STAGE10_PRICE_VARIANT,
                            STAGE10_CURRENCY_SEGMENT,
                            version_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE stage10_daily_prices SET current_version_id=?
                        WHERE instrument_id=? AND trade_date=? AND provider='fmp'
                          AND price_variant=? AND currency_segment=?
                        """,
                        (
                            version_id,
                            publication.iwm.instrument_id,
                            row.trade_date,
                            STAGE10_PRICE_VARIANT,
                            STAGE10_CURRENCY_SEGMENT,
                        ),
                    )
                written += 1
            price_artifact = ArtifactWrite(
                artifact_id=publication.price_artifact_id,
                dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                content_sha256=capture.raw_bytes_sha256,
                media_type="application/json",
                byte_count=len(capture.raw_bytes),
                source_reference="fmp/stable/historical-price-eod/full",
                request_scope=dict(publication.price_request_scope),
                captured_at=publication.captured_at,
                captured_precision="datetime",
                normalization_version=IWM_ETF_HISTORY_NORMALIZATION_VERSION,
            )
            artifacts: tuple[ArtifactWrite, ...] = (price_artifact,)
            if scope_created or successor_created:
                artifacts += (
                    ArtifactWrite(
                        artifact_id=publication.scope_artifact_id,
                        dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                        content_sha256=publication.scope_evidence_sha256,
                        media_type="application/json",
                        byte_count=len(publication.scope_evidence_bytes),
                        source_reference="config/iwm_etf_history_v1_scope.json",
                        request_scope=dict(publication.universe_request_scope),
                        captured_at=publication.captured_at,
                        captured_precision="datetime",
                        normalization_version=IWM_ETF_HISTORY_SCOPE_NORMALIZATION_VERSION,
                    ),
                )
            quality = (
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_quality",
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                    rule_id="fixed_scope_and_response",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    artifact_id=publication.price_artifact_id,
                    snapshot_id=publication.snapshot_id,
                    observed={"price_rows": len(capture.rows), "response_complete": True},
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_quality",
                        STAGE10_INSTRUMENTS_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_INSTRUMENTS_DATASET_ID,
                    rule_id="stable_provider_identity",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    snapshot_id=publication.snapshot_id,
                    observed={"successor_member_count": IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT},
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_quality",
                        STAGE10_UNIVERSES_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_UNIVERSES_DATASET_ID,
                    rule_id="complete_successor_membership",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "successor_member_count": IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
                        "complete": True,
                    },
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_quality",
                        STAGE10_DAILY_PRICES_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    rule_id="full_history_ohlcv_and_corrections",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "price_rows": len(capture.rows),
                        "earliest_trade_date": capture.earliest_date,
                        "latest_trade_date": capture.latest_date,
                    },
                ),
            )
            return WriteResult(
                written_count=written,
                artifacts=artifacts,
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.price_request_scope),
                    completeness="complete",
                    row_count=len(capture.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=tuple(item.artifact_id for item in artifacts),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
            output_dataset_ids=IWM_ETF_HISTORY_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=IWM_ETF_HISTORY_COLLECTOR_ID,
            scope=dict(publication.price_request_scope),
            started_at=publication.captured_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.capture.rows),
            writer=writer,
            held_locks=held_locks,
        )


def run_iwm_etf_history(
    *,
    store_map: StoreMap,
    registry: Registry,
    scope: IwmEtfHistoryScope,
    transport: FmpStage10Transport,
    api_key: str,
    captured_at: datetime,
) -> IwmEtfHistoryRunReport:
    """Preflight the fixed store, then capture and publish one complete history."""

    publisher = IwmEtfHistoryPublisher(
        store_map,
        registry,
        scope,
        clock=lambda: captured_at,
    )
    publisher.preflight_store()
    capture = capture_iwm_etf_history(api_key=api_key, transport=transport)
    prepared = publisher.prepare(capture)
    receipt = publisher.publish(prepared)
    state = publisher._prepared_state(prepared).publication
    return IwmEtfHistoryRunReport(
        outcome=receipt.outcome,
        base_scope_manifest_sha256=state.scope.base_scope_manifest_sha256,
        scope_manifest_sha256=state.scope.manifest_sha256,
        successor_membership_sha256=state.successor_membership_sha256,
        normalized_complete_history_sha256=state.capture.semantic_sha256,
        response_sha256=state.capture.raw_bytes_sha256,
        price_row_count=len(state.capture.rows),
        successor_member_count=IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
        written_count=receipt.written_count,
    )


__all__ = (
    "IWM_ETF_HISTORY_COLLECTOR_ID",
    "IWM_ETF_HISTORY_HANDLER",
    "IWM_ETF_HISTORY_INPUT_DATASETS",
    "IWM_ETF_HISTORY_OUTPUT_DATASETS",
    "IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT",
    "IWM_ETF_HISTORY_SYMBOL",
    "IwmEtfHistoryPublisher",
    "IwmEtfHistoryRunReport",
    "IwmEtfHistoryScope",
    "PreparedIwmEtfHistoryPublication",
    "StdlibFmpIwmEtfHistoryTransport",
    "capture_iwm_etf_history",
    "load_iwm_etf_history_scope",
    "run_iwm_etf_history",
)
