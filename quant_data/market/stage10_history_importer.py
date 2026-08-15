"""Offline-safe publication for the bounded Stage 10 market-history profile.

The FMP transport and parsers live in :mod:`fmp_bulk_daily_prices`; this
module deliberately accepts only their already-validated capture objects.  A
candidate is prepared before a write lock is acquired, is opaque and bound to
one importer/store map, and is then published through ``IngestionCoordinator``
in one short market-store transaction.

Stage 10 is a forward reconstruction.  It records one current membership
snapshot for each approved constituent universe plus the reviewed ETF/index
watchlists.  It does not model historical membership, Russell universes, a
scheduler, public reads, paths, credentials, or provider transport.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping

from ..contracts import IngestionReceipt
from ..errors import ConflictError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import (
    HeldWriteLocks,
    StoreMap,
    StoreRole,
    canonical_path_uri,
    read_connection,
    stable_id,
)
from .fmp_bulk_daily_prices import (
    FMP_STAGE10_PRICE_PATH,
    FmpStage10PriceCapture,
    FmpStage10PriceRow,
    FmpStage10UniverseCapture,
)
from .stage10_scope import (
    REVIEWED_STAGE10_MANIFEST_SHA256,
    STAGE10_TARGET_PROFILE_ID,
    CuratedEtf,
    MajorIndex,
    Stage10MarketScope,
    UniverseSource,
)


STAGE10_UNIVERSE_COLLECTOR_ID = "fmp.market.stage10_universe_capture"
STAGE10_DAILY_HISTORY_COLLECTOR_ID = "fmp.market.stage10_daily_history"
STAGE10_EVIDENCE_DATASET_ID = "market.stage10.source_evidence"
STAGE10_INSTRUMENTS_DATASET_ID = "market.stage10.instruments"
STAGE10_UNIVERSES_DATASET_ID = "market.stage10.universes"
STAGE10_DAILY_PRICES_DATASET_ID = "market.stage10.daily_prices"
STAGE10_MIGRATION_ID = "market:0010_stage10_market_history"
STAGE10_MIGRATION_RESOURCE = "quant_data/migrations/market/0010_stage10_market_history.sql"
STAGE10_MIGRATION_SHA256 = (
    "a7703655f6fe8089431eacdc78600582d6f5582589c0c38b531a2b93c7dc041a"
)
STAGE10_UNIVERSE_NORMALIZATION_VERSION = "stage10.fmp.universe.v1"
STAGE10_PRICE_NORMALIZATION_VERSION = "stage10.fmp.daily_price.v1"
STAGE10_PRICE_VARIANT = "fmp_full_eod_v1"
STAGE10_CURRENCY_SEGMENT = "provider_native"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_SYMBOL = re.compile(r"^[A-Za-z0-9.^-]{1,32}$")
_UNIVERSE_IDS = (
    "sp500_current",
    "nasdaq100_current",
    "dow30_current",
    "curated_etfs",
    "major_indexes",
)
_UNIVERSE_OUTPUT_DATASETS = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_INSTRUMENTS_DATASET_ID,
    STAGE10_UNIVERSES_DATASET_ID,
)
_PRICE_OUTPUT_DATASETS = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
)
_ASSET_TYPES = frozenset({"equity", "etf", "index"})


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256(dumps_strict(value).encode("utf-8"))


def _utc_text(value: object, field_name: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be an aware datetime")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("Stage 10 importer requires explicit stores")
    return tuple(
        (role.value, canonical_path_uri(path)) for role, path in store_map.items()
    )


def _require_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"Stage 10 {field_name} is invalid")
    return value


def _scope_manifest_bytes(scope: Stage10MarketScope) -> bytes:
    return dumps_strict(scope.manifest_mapping()).encode("utf-8")


def _validate_scope(scope: object) -> Stage10MarketScope:
    if not isinstance(scope, Stage10MarketScope):
        raise ValidationError("Stage 10 importer requires a reviewed scope")
    manifest = scope.manifest_mapping()
    digest = _sha256_json(manifest)
    if (
        scope.manifest_sha256 != REVIEWED_STAGE10_MANIFEST_SHA256
        or digest != REVIEWED_STAGE10_MANIFEST_SHA256
        or scope.target_profile_id != STAGE10_TARGET_PROFILE_ID
        or scope.provider != "fmp"
        or scope.price_history.endpoint_path != FMP_STAGE10_PRICE_PATH
        or scope.price_history.price_variant != STAGE10_PRICE_VARIANT
        or tuple(source.id for source in scope.universe_sources)
        != _UNIVERSE_IDS[:3]
        or len(scope.curated_etfs) != 95
        or len(scope.major_indexes) != 15
    ):
        raise ValidationError("Stage 10 scope is not the reviewed immutable profile")
    if any(
        "russell" in source.id.casefold()
        or "russell" in source.canonical_index_id.casefold()
        for source in scope.universe_sources
    ) or any(
        "russell" in item.canonical_id.casefold() or item.provider_symbol == "^RUT"
        for item in scope.major_indexes
    ):
        raise ValidationError("Russell instruments are outside the approved Stage 10 scope")
    return scope


def _validate_registry(registry: object) -> Registry:
    if (
        not isinstance(registry, Registry)
        or registry.schema_version != "1.6.0"
        or registry.registry_version != "2.8.0"
        or registry.status != "validated"
    ):
        raise ValidationError("Stage 10 importer requires the reviewed 1.6.0/2.8.0 registry")

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
        raise ValidationError("Stage 10 migration declaration is invalid")

    datasets = {item.id: item for item in registry.datasets}
    if len(datasets) != len(registry.datasets):
        raise ValidationError("Stage 10 registry contains duplicate datasets")
    expected_collectors = {
        STAGE10_EVIDENCE_DATASET_ID: frozenset(
            {STAGE10_UNIVERSE_COLLECTOR_ID, STAGE10_DAILY_HISTORY_COLLECTOR_ID}
        ),
        STAGE10_INSTRUMENTS_DATASET_ID: frozenset({STAGE10_UNIVERSE_COLLECTOR_ID}),
        STAGE10_UNIVERSES_DATASET_ID: frozenset({STAGE10_UNIVERSE_COLLECTOR_ID}),
        STAGE10_DAILY_PRICES_DATASET_ID: frozenset(
            {STAGE10_DAILY_HISTORY_COLLECTOR_ID}
        ),
    }
    for dataset_id, collector_ids in expected_collectors.items():
        declaration = datasets.get(dataset_id)
        if (
            declaration is None
            or declaration.store != StoreRole.MARKET.value
            or not declaration.active
            or frozenset(declaration.collector_ids) != collector_ids
            or declaration.tool_ids
            or declaration.dashboard_ids
            or declaration.export_ids
        ):
            raise ValidationError("Stage 10 dataset declaration is invalid")

    collectors = {str(item.get("id")): item for item in registry.collectors}
    expected = (
        (
            STAGE10_UNIVERSE_COLLECTOR_ID,
            "market.stage10_fmp_universes",
            (),
            _UNIVERSE_OUTPUT_DATASETS,
        ),
        (
            STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            "market.stage10_fmp_daily_history",
            (STAGE10_INSTRUMENTS_DATASET_ID, STAGE10_UNIVERSES_DATASET_ID),
            _PRICE_OUTPUT_DATASETS,
        ),
    )
    for collector_id, handler, inputs, outputs in expected:
        collector = collectors.get(collector_id)
        if (
            collector is None
            or collector.get("handler") != handler
            or collector.get("network") is not True
            or tuple(collector.get("input_datasets", ())) != inputs
            or tuple(collector.get("output_datasets", ())) != outputs
            or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
            or collector.get("schedule_eligibility") != {"mode": "manual_only"}
        ):
            raise ValidationError("Stage 10 collector declaration is invalid")
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
            or _PROVIDER_SYMBOL.fullmatch(self.provider_symbol) is None
            or self.asset_type not in _ASSET_TYPES
            or (
                self.display_name is not None
                and (not isinstance(self.display_name, str) or not self.display_name.strip())
            )
            or (
                self.first_trade_date is not None
                and (
                    not isinstance(self.first_trade_date, str)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.first_trade_date)
                )
            )
        ):
            raise ValidationError("Stage 10 instrument plan is invalid")

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
class _UniversePublication:
    universe_id: str
    universe_kind: str
    publisher: str
    source_authority: str
    membership_relation: str
    members: tuple[_InstrumentPlan, ...]
    membership_sha256: str
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    semantic_identity: str
    raw_bytes: bytes
    raw_bytes_sha256: str
    endpoint_path: str | None
    captured_at: str
    started_at: str
    run_id: str
    artifact_id: str
    snapshot_id: str
    capture_id: str | None


@dataclass(frozen=True, slots=True)
class _PricePublication:
    instrument: _InstrumentPlan
    capture: FmpStage10PriceCapture
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    semantic_identity: str
    captured_at: str
    started_at: str
    run_id: str
    artifact_id: str
    snapshot_id: str
    capture_id: str


@dataclass(frozen=True, slots=True)
class _PreparedState:
    owner_token: object
    store_map: StoreMap
    registry: Registry
    scope: Stage10MarketScope
    store_map_identity: tuple[tuple[str, str], ...]
    universe: _UniversePublication | None
    price: _PricePublication | None


class PreparedStage10Publication:
    """Opaque, importer-bound Stage 10 candidate with no raw-byte repr."""

    __slots__ = ("__weakref__",)


_PREPARED: "weakref.WeakKeyDictionary[PreparedStage10Publication, _PreparedState]" = (
    weakref.WeakKeyDictionary()
)


def _universe_membership_sha256(members: tuple[_InstrumentPlan, ...]) -> str:
    return _sha256_json(
        [item.membership_mapping() for item in sorted(members, key=lambda item: item.instrument_id)]
    )


def _source_reference(endpoint_path: str | None) -> str:
    if endpoint_path is None:
        return "config/stage10_market_scope.json"
    return "fmp" + endpoint_path


def _merge_instrument(
    planned: dict[str, _InstrumentPlan],
    candidate: _InstrumentPlan,
) -> _InstrumentPlan:
    existing = planned.get(candidate.provider_symbol)
    if existing is None:
        planned[candidate.provider_symbol] = candidate
        return candidate
    if (
        existing.asset_type != candidate.asset_type
        or existing.first_trade_date != candidate.first_trade_date
    ):
        raise ValidationError("Stage 10 scope assigns incompatible identities to one symbol")
    return existing


def _source_universe_plan(
    source: UniverseSource,
    capture: FmpStage10UniverseCapture,
    planned: dict[str, _InstrumentPlan],
    scope: Stage10MarketScope,
    *,
    captured_at: str,
) -> _UniversePublication:
    if capture.endpoint_path != source.endpoint_path:
        raise ValidationError("Stage 10 universe capture endpoint does not match scope")
    symbols = tuple(row.symbol for row in capture.constituents)
    if (
        not source.expected_members_min <= len(symbols) <= source.expected_members_max
        or not set(source.required_symbols).issubset(symbols)
        or set(source.forbidden_symbols) & set(symbols)
        or any(symbol in {"^RUT", "IWM"} for symbol in symbols)
    ):
        raise ValidationError("Stage 10 current universe capture is outside the reviewed scope")
    members = tuple(
        _merge_instrument(
            planned,
            _InstrumentPlan(
                provider_symbol=row.symbol,
                asset_type="equity",
                display_name=row.name,
                first_trade_date=None,
            ),
        )
        for row in capture.constituents
    )
    membership_sha256 = _universe_membership_sha256(members)
    request_scope: dict[str, object] = {
        "provider": "fmp",
        "endpoint_path": source.endpoint_path,
        "universe_id": source.id,
        "membership_relation": source.membership_relation,
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }
    request_scope_sha256 = _sha256_json(request_scope)
    semantic_identity = _sha256_json(
        {
            "collector_id": STAGE10_UNIVERSE_COLLECTOR_ID,
            "canonical_dataset_id": STAGE10_UNIVERSES_DATASET_ID,
            "request_scope": request_scope,
            "membership_sha256": membership_sha256,
        }
    )
    _require_digest(semantic_identity, "universe semantic identity")
    run_id = stable_id(
        "stage10_universe_run", STAGE10_UNIVERSES_DATASET_ID, semantic_identity
    )
    return _UniversePublication(
        universe_id=source.id,
        universe_kind="benchmark_constituent_universe",
        publisher=source.publisher,
        source_authority=source.source_authority,
        membership_relation=source.membership_relation,
        members=members,
        membership_sha256=membership_sha256,
        request_scope=request_scope,
        request_scope_sha256=request_scope_sha256,
        semantic_identity=semantic_identity,
        raw_bytes=capture.raw_bytes,
        raw_bytes_sha256=capture.raw_bytes_sha256,
        endpoint_path=capture.endpoint_path,
        captured_at=captured_at,
        started_at=captured_at,
        run_id=run_id,
        artifact_id=stable_id(
            "stage10_universe_artifact",
            STAGE10_EVIDENCE_DATASET_ID,
            source.id,
            capture.raw_bytes_sha256,
            request_scope_sha256,
        ),
        snapshot_id=stable_id(
            "stage10_universe_snapshot", STAGE10_UNIVERSES_DATASET_ID, semantic_identity
        ),
        capture_id=stable_id(
            "stage10_universe_capture", STAGE10_EVIDENCE_DATASET_ID, semantic_identity
        ),
    )


def _watchlist_universe_plan(
    *,
    universe_id: str,
    publisher: str,
    members: tuple[_InstrumentPlan, ...],
    planned: dict[str, _InstrumentPlan],
    scope: Stage10MarketScope,
    captured_at: str,
) -> _UniversePublication:
    if universe_id not in {"curated_etfs", "major_indexes"}:
        raise ValidationError("Stage 10 reviewed watchlist is invalid")
    merged_members = tuple(_merge_instrument(planned, item) for item in members)
    membership_sha256 = _universe_membership_sha256(merged_members)
    request_scope: dict[str, object] = {
        "provider": "reviewed_local_manifest",
        "universe_id": universe_id,
        "membership_relation": "reviewed_instrument_watchlist",
        "scope_manifest_sha256": scope.manifest_sha256,
        "target_profile_id": scope.target_profile_id,
    }
    request_scope_sha256 = _sha256_json(request_scope)
    semantic_identity = _sha256_json(
        {
            "collector_id": STAGE10_UNIVERSE_COLLECTOR_ID,
            "canonical_dataset_id": STAGE10_UNIVERSES_DATASET_ID,
            "request_scope": request_scope,
            "membership_sha256": membership_sha256,
        }
    )
    raw_bytes = dumps_strict(
        {
            "scope_manifest": scope.manifest_mapping(),
            "universe_id": universe_id,
        }
    ).encode("utf-8")
    raw_bytes_sha256 = _sha256(raw_bytes)
    return _UniversePublication(
        universe_id=universe_id,
        universe_kind="instrument_watchlist",
        publisher=publisher,
        source_authority="reviewed_local_manifest",
        membership_relation="reviewed_instrument_watchlist",
        members=merged_members,
        membership_sha256=membership_sha256,
        request_scope=request_scope,
        request_scope_sha256=request_scope_sha256,
        semantic_identity=semantic_identity,
        raw_bytes=raw_bytes,
        raw_bytes_sha256=raw_bytes_sha256,
        endpoint_path=None,
        captured_at=captured_at,
        started_at=captured_at,
        run_id=stable_id(
            "stage10_universe_run", STAGE10_UNIVERSES_DATASET_ID, semantic_identity
        ),
        artifact_id=stable_id(
            "stage10_scope_artifact",
            STAGE10_EVIDENCE_DATASET_ID,
            universe_id,
            raw_bytes_sha256,
            request_scope_sha256,
        ),
        snapshot_id=stable_id(
            "stage10_universe_snapshot", STAGE10_UNIVERSES_DATASET_ID, semantic_identity
        ),
        capture_id=None,
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


def _current_version(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    trade_date: str,
) -> sqlite3.Row | None:
    return connection.execute(
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
        (instrument_id, trade_date, STAGE10_PRICE_VARIANT, STAGE10_CURRENCY_SEGMENT),
    ).fetchone()


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("Stage 10 price value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _same_price_values(current: sqlite3.Row, row: FmpStage10PriceRow) -> bool:
    return (
        current["open_value"] == _decimal_text(row.open_value)
        and current["high_value"] == _decimal_text(row.high_value)
        and current["low_value"] == _decimal_text(row.low_value)
        and current["close_value"] == _decimal_text(row.close_value)
        and current["volume"] == row.volume
    )


class Stage10HistoryImporter:
    """Publish bounded Stage 10 universe and full-history price candidates.

    The importer has no transport or credential parameter.  Callers capture
    and parse FMP responses first, then pass those immutable capture objects to
    the preparation methods.  Publishing a prepared candidate cannot fetch or
    parse provider data.
    """

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        scope: Stage10MarketScope,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(store_map, StoreMap) or not callable(clock):
            raise ValidationError("Stage 10 importer requires explicit stores and a clock")
        self.store_map = store_map
        self.registry = _validate_registry(registry)
        self.scope = _validate_scope(scope)
        self._clock = clock
        self._coordinator = IngestionCoordinator(store_map, code_version="stage10.0.0")
        self._owner_token = object()

    def _new_prepared(
        self,
        *,
        universe: _UniversePublication | None = None,
        price: _PricePublication | None = None,
    ) -> PreparedStage10Publication:
        if (universe is None) == (price is None):
            raise ValidationError("Stage 10 prepared candidate kind is invalid")
        candidate = PreparedStage10Publication()
        _PREPARED[candidate] = _PreparedState(
            owner_token=self._owner_token,
            store_map=self.store_map,
            registry=self.registry,
            scope=self.scope,
            store_map_identity=_store_map_identity(self.store_map),
            universe=universe,
            price=price,
        )
        return candidate

    def _prepared_state(self, prepared: object) -> _PreparedState:
        if not isinstance(prepared, PreparedStage10Publication):
            raise ValidationError("Stage 10 publication requires a prepared candidate")
        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner_token is not self._owner_token
            or state.store_map is not self.store_map
            or state.registry is not self.registry
            or state.scope is not self.scope
            or state.store_map_identity != _store_map_identity(self.store_map)
            or (state.universe is None) == (state.price is None)
        ):
            raise ValidationError("Stage 10 prepared candidate is foreign or expired")
        return state

    def prepare_universe_publications(
        self,
        captures: Mapping[str, FmpStage10UniverseCapture],
    ) -> tuple[PreparedStage10Publication, ...]:
        """Prepare all five current/curated universe candidates without I/O.

        ``captures`` must contain exactly the three reviewed FMP current
        constituent payloads keyed by their scope universe IDs.  The returned
        candidates are ordered S&P 500, Nasdaq-100, Dow 30, curated ETFs, and
        major indexes.  Nothing is written until each candidate is separately
        handed to :meth:`publish_prepared`.
        """

        if not isinstance(captures, Mapping) or set(captures) != set(_UNIVERSE_IDS[:3]):
            raise ValidationError("Stage 10 requires exactly three reviewed universe captures")
        if not all(isinstance(key, str) for key in captures):
            raise ValidationError("Stage 10 universe capture identifiers are invalid")
        if not all(isinstance(value, FmpStage10UniverseCapture) for value in captures.values()):
            raise ValidationError("Stage 10 universe captures must already be parsed")
        captured_at = _utc_text(self._clock(), "captured_at")
        planned: dict[str, _InstrumentPlan] = {}
        publications: list[_UniversePublication] = []
        for source in self.scope.universe_sources:
            capture = captures[source.id]
            publications.append(
                _source_universe_plan(
                    source,
                    capture,
                    planned,
                    self.scope,
                    captured_at=captured_at,
                )
            )

        curated_members = tuple(
            _InstrumentPlan(
                provider_symbol=item.symbol,
                asset_type="etf",
                display_name=None,
                first_trade_date=item.first_trade_date,
            )
            for item in self.scope.curated_etfs
        )
        publications.append(
            _watchlist_universe_plan(
                universe_id="curated_etfs",
                publisher="stage10_reviewed_scope",
                members=curated_members,
                planned=planned,
                scope=self.scope,
                captured_at=captured_at,
            )
        )
        index_members = tuple(
            _InstrumentPlan(
                provider_symbol=item.provider_symbol,
                asset_type="index",
                display_name=item.name,
                first_trade_date=None,
            )
            for item in self.scope.major_indexes
        )
        publications.append(
            _watchlist_universe_plan(
                universe_id="major_indexes",
                publisher="stage10_reviewed_scope",
                members=index_members,
                planned=planned,
                scope=self.scope,
                captured_at=captured_at,
            )
        )
        if (
            len(planned) > self.scope.bounds.max_instruments
            or bool({"^RUT", "IWM"} & planned.keys())
            or any("russell" in symbol.casefold() for symbol in planned)
        ):
            raise ValidationError("Stage 10 instrument roster is outside the reviewed scope")
        return tuple(self._new_prepared(universe=item) for item in publications)

    def _published_instrument(self, provider_symbol: str) -> _InstrumentPlan:
        if provider_symbol in {"^RUT", "IWM"}:
            raise ValidationError("Russell instruments are outside the approved Stage 10 scope")
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            row = connection.execute(
                """
                SELECT provider, provider_symbol, asset_type, display_name,
                       exchange_code, currency_segment, first_trade_date,
                       identity_seed_sha256
                FROM stage10_instruments
                WHERE provider='fmp' AND provider_symbol=?
                """,
                (provider_symbol,),
            ).fetchone()
        if row is None:
            raise ValidationError("Stage 10 price history requires a published scoped instrument")
        plan = _InstrumentPlan(
            provider_symbol=str(row["provider_symbol"]),
            asset_type=str(row["asset_type"]),
            display_name=None if row["display_name"] is None else str(row["display_name"]),
            first_trade_date=(
                None if row["first_trade_date"] is None else str(row["first_trade_date"])
            ),
        )
        if (
            plan.provider_symbol != provider_symbol
            or plan.asset_type not in _ASSET_TYPES
            or row["provider"] != "fmp"
            or row["exchange_code"] is not None
            or row["currency_segment"] != STAGE10_CURRENCY_SEGMENT
            or row["identity_seed_sha256"] != plan.identity_seed_sha256
        ):
            raise ConflictError("Published Stage 10 instrument identity is invalid")
        return plan

    def prepare_price_capture(
        self,
        capture: FmpStage10PriceCapture,
    ) -> PreparedStage10Publication:
        """Prepare one full provider-history price capture without writing.

        The instrument must already exist in a published Stage 10 universe.
        This preflight is read-only, binds the candidate to that identity, and
        prevents a price request from creating an unreviewed instrument.
        """

        if not isinstance(capture, FmpStage10PriceCapture):
            raise ValidationError("Stage 10 price capture must already be parsed")
        if capture.symbol in {"^RUT", "IWM"}:
            raise ValidationError("Russell instruments are outside the approved Stage 10 scope")
        instrument = self._published_instrument(capture.symbol)
        captured_at = _utc_text(self._clock(), "captured_at")
        if capture.latest_date > captured_at[:10]:
            raise ValidationError("Stage 10 price history extends beyond capture time")
        if (
            instrument.first_trade_date is not None
            and capture.earliest_date < instrument.first_trade_date
        ):
            raise ValidationError("Stage 10 price history predates the instrument")
        request_scope: dict[str, object] = {
            "provider": "fmp",
            "endpoint_path": self.scope.price_history.endpoint_path,
            "symbol": capture.symbol,
            "interval": self.scope.price_history.interval,
            "history_policy": self.scope.price_history.history_policy,
            "price_variant": self.scope.price_history.price_variant,
            "currency_policy": self.scope.price_history.currency_policy,
            "volume_policy": self.scope.price_history.volume_policy,
            "scope_manifest_sha256": self.scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
        }
        request_scope_sha256 = _sha256_json(request_scope)
        semantic_identity = _sha256_json(
            {
                "collector_id": STAGE10_DAILY_HISTORY_COLLECTOR_ID,
                "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "request_scope": request_scope,
                "normalized_complete_history_sha256": capture.semantic_sha256,
            }
        )
        _require_digest(semantic_identity, "price semantic identity")
        return self._new_prepared(
            price=_PricePublication(
                instrument=instrument,
                capture=capture,
                request_scope=request_scope,
                request_scope_sha256=request_scope_sha256,
                semantic_identity=semantic_identity,
                captured_at=captured_at,
                started_at=captured_at,
                run_id=stable_id(
                    "stage10_daily_price_run",
                    STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity,
                ),
                artifact_id=stable_id(
                    "stage10_daily_price_artifact",
                    STAGE10_EVIDENCE_DATASET_ID,
                    capture.symbol,
                    capture.raw_bytes_sha256,
                    request_scope_sha256,
                ),
                snapshot_id=stable_id(
                    "stage10_daily_price_snapshot",
                    STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity,
                ),
                capture_id=stable_id(
                    "stage10_daily_price_capture",
                    STAGE10_EVIDENCE_DATASET_ID,
                    semantic_identity,
                ),
            )
        )

    def publish_prepared(
        self,
        prepared: PreparedStage10Publication,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        """Atomically publish one prior candidate with no provider activity."""

        state = self._prepared_state(prepared)
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("Held write-lock capability is invalid")
            held_locks._require_target(self.store_map, StoreRole.MARKET)
        if state.universe is not None:
            return self._publish_universe(state.universe, held_locks=held_locks)
        assert state.price is not None
        return self._publish_price(state.price, held_locks=held_locks)

    def _ensure_scope_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        publication: _UniversePublication,
        run_id: str,
    ) -> int:
        scope_snapshot_id = stable_id(
            "stage10_scope_snapshot", self.scope.manifest_sha256
        )
        existing = connection.execute(
            """
            SELECT scope_manifest_sha256, target_profile_id, provider
            FROM stage10_scope_snapshots WHERE scope_snapshot_id=?
            """,
            (scope_snapshot_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != (
                self.scope.manifest_sha256,
                self.scope.target_profile_id,
                "fmp",
            ):
                raise ConflictError("Stage 10 scope snapshot conflicts with the reviewed scope")
            return 0
        connection.execute(
            """
            INSERT INTO stage10_scope_snapshots (
                scope_snapshot_id, scope_manifest_sha256, target_profile_id,
                provider, captured_at, captured_precision, run_id
            ) VALUES (?, ?, ?, 'fmp', ?, 'datetime', ?)
            """,
            (
                scope_snapshot_id,
                self.scope.manifest_sha256,
                self.scope.target_profile_id,
                publication.captured_at,
                run_id,
            ),
        )
        return 1

    @staticmethod
    def _ensure_universe(
        connection: sqlite3.Connection,
        *,
        publication: _UniversePublication,
        run_id: str,
    ) -> int:
        existing = connection.execute(
            """
            SELECT universe_kind, publisher, source_authority
            FROM stage10_universes WHERE universe_id=?
            """,
            (publication.universe_id,),
        ).fetchone()
        expected = (
            publication.universe_kind,
            publication.publisher,
            publication.source_authority,
        )
        if existing is not None:
            if tuple(existing) != expected:
                raise ConflictError("Stage 10 universe declaration conflicts with the reviewed scope")
            return 0
        connection.execute(
            """
            INSERT INTO stage10_universes (
                universe_id, universe_kind, publisher, source_authority, created_run_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (publication.universe_id, *expected, run_id),
        )
        return 1

    @staticmethod
    def _ensure_instrument(
        connection: sqlite3.Connection,
        *,
        plan: _InstrumentPlan,
        captured_at: str,
        run_id: str,
    ) -> int:
        existing = connection.execute(
            """
            SELECT provider, provider_symbol, asset_type, display_name,
                   exchange_code, currency_segment, first_trade_date,
                   identity_seed_sha256
            FROM stage10_instruments WHERE instrument_id=?
            """,
            (plan.instrument_id,),
        ).fetchone()
        if existing is not None:
            if not _same_instrument_record(existing, plan):
                raise ConflictError("Stage 10 stable instrument identity conflicts with the store")
            return 0
        colliding = connection.execute(
            """
            SELECT instrument_id FROM stage10_instruments
            WHERE provider='fmp' AND provider_symbol=?
            """,
            (plan.provider_symbol,),
        ).fetchone()
        if colliding is not None:
            raise ConflictError("Stage 10 provider symbol is bound to another identity")
        connection.execute(
            """
            INSERT INTO stage10_instruments (
                instrument_id, provider, provider_symbol, asset_type, display_name,
                exchange_code, currency_segment, first_trade_date,
                identity_seed_sha256, captured_at, captured_precision, run_id
            ) VALUES (?, 'fmp', ?, ?, ?, NULL, ?, ?, ?, ?, 'datetime', ?)
            """,
            (
                plan.instrument_id,
                plan.provider_symbol,
                plan.asset_type,
                plan.display_name,
                STAGE10_CURRENCY_SEGMENT,
                plan.first_trade_date,
                plan.identity_seed_sha256,
                captured_at,
                run_id,
            ),
        )
        return 1

    def _publish_universe(
        self,
        publication: _UniversePublication,
        *,
        held_locks: HeldWriteLocks | None,
    ) -> IngestionReceipt:
        scope_snapshot_id = stable_id(
            "stage10_scope_snapshot", self.scope.manifest_sha256
        )

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            written = self._ensure_scope_snapshot(
                connection,
                publication=publication,
                run_id=active_run_id,
            )
            written += self._ensure_universe(
                connection,
                publication=publication,
                run_id=active_run_id,
            )
            for plan in publication.members:
                written += self._ensure_instrument(
                    connection,
                    plan=plan,
                    captured_at=publication.captured_at,
                    run_id=active_run_id,
                )
            if publication.capture_id is not None:
                assert publication.endpoint_path is not None
                connection.execute(
                    """
                    INSERT INTO stage10_universe_captures (
                        capture_id, dataset_id, provider, universe_id, endpoint_path,
                        scope_manifest_sha256, request_scope_json, request_scope_sha256,
                        response_sha256, response_bytes, http_status, content_type,
                        semantic_identity, completeness, artifact_id, snapshot_id,
                        captured_at, captured_precision, as_of_date, row_count,
                        normalization_version, run_id
                    ) VALUES (?, ?, 'fmp', ?, ?, ?, ?, ?, ?, ?, 200, 'application/json',
                              ?, 'complete', ?, ?, ?, 'datetime', ?, ?, ?, ?)
                    """,
                    (
                        publication.capture_id,
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.universe_id,
                        publication.endpoint_path,
                        self.scope.manifest_sha256,
                        dumps_strict(dict(publication.request_scope)),
                        publication.request_scope_sha256,
                        publication.raw_bytes_sha256,
                        publication.raw_bytes,
                        publication.semantic_identity,
                        publication.artifact_id,
                        publication.snapshot_id,
                        publication.captured_at,
                        publication.captured_at[:10],
                        len(publication.members),
                        STAGE10_UNIVERSE_NORMALIZATION_VERSION,
                        active_run_id,
                    ),
                )
                written += 1
            connection.execute(
                """
                INSERT INTO stage10_universe_snapshots (
                    universe_snapshot_id, universe_id, scope_snapshot_id, capture_id,
                    as_of_date, captured_at, captured_precision, completeness,
                    membership_relation, member_count, membership_sha256, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'datetime', 'complete', ?, ?, ?, ?)
                """,
                (
                    publication.snapshot_id,
                    publication.universe_id,
                    scope_snapshot_id,
                    publication.capture_id,
                    publication.captured_at[:10],
                    publication.captured_at,
                    publication.membership_relation,
                    len(publication.members),
                    publication.membership_sha256,
                    active_run_id,
                ),
            )
            written += 1
            for source_row, plan in enumerate(publication.members, start=1):
                connection.execute(
                    """
                    INSERT INTO stage10_universe_snapshot_members (
                        universe_snapshot_id, instrument_id, source_row
                    ) VALUES (?, ?, ?)
                    """,
                    (publication.snapshot_id, plan.instrument_id, source_row),
                )
                written += 1
            artifact = ArtifactWrite(
                artifact_id=publication.artifact_id,
                dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                content_sha256=publication.raw_bytes_sha256,
                media_type="application/json",
                byte_count=len(publication.raw_bytes),
                source_reference=_source_reference(publication.endpoint_path),
                request_scope=dict(publication.request_scope),
                captured_at=publication.captured_at,
                captured_precision="datetime",
                normalization_version=STAGE10_UNIVERSE_NORMALIZATION_VERSION,
            )
            quality = (
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_universe_quality",
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                    rule_id=(
                        "response_digest_and_scope"
                        if publication.capture_id is not None
                        else "reviewed_scope_manifest_binding"
                    ),
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    artifact_id=publication.artifact_id,
                    snapshot_id=publication.snapshot_id,
                    observed={"rows": len(publication.members), "complete": True},
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_universe_quality",
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
                    observed={"members": len(publication.members)},
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_universe_quality",
                        STAGE10_UNIVERSES_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_UNIVERSES_DATASET_ID,
                    rule_id="complete_current_membership",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "universe_id": publication.universe_id,
                        "members": len(publication.members),
                        "complete": True,
                    },
                ),
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=STAGE10_UNIVERSES_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(publication.members),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(publication.artifact_id,),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=STAGE10_UNIVERSES_DATASET_ID,
            output_dataset_ids=_UNIVERSE_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=STAGE10_UNIVERSE_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.started_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.members),
            writer=writer,
            held_locks=held_locks,
        )

    def _publish_price(
        self,
        publication: _PricePublication,
        *,
        held_locks: HeldWriteLocks | None,
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            identity = connection.execute(
                """
                SELECT provider, provider_symbol, asset_type, display_name,
                       exchange_code, currency_segment, first_trade_date,
                       identity_seed_sha256
                FROM stage10_instruments WHERE instrument_id=?
                """,
                (publication.instrument.instrument_id,),
            ).fetchone()
            if identity is None or not _same_instrument_record(identity, publication.instrument):
                raise ConflictError("Stage 10 price candidate instrument binding is invalid")
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
                    publication.instrument.instrument_id,
                    capture.symbol,
                    FMP_STAGE10_PRICE_PATH,
                    self.scope.manifest_sha256,
                    dumps_strict(dict(publication.request_scope)),
                    publication.request_scope_sha256,
                    capture.raw_bytes_sha256,
                    capture.raw_bytes,
                    publication.semantic_identity,
                    publication.artifact_id,
                    publication.snapshot_id,
                    publication.captured_at,
                    capture.earliest_date,
                    capture.latest_date,
                    len(capture.rows),
                    STAGE10_PRICE_NORMALIZATION_VERSION,
                    active_run_id,
                ),
            )
            written = 1
            for row in capture.rows:
                current = _current_version(
                    connection,
                    instrument_id=publication.instrument.instrument_id,
                    trade_date=row.trade_date,
                )
                if current is not None and _same_price_values(current, row):
                    continue
                correction_sequence = 1 if current is None else int(current["correction_sequence"]) + 1
                supersedes = None if current is None else str(current["version_id"])
                version_id = stable_id(
                    "stage10_daily_price_version",
                    publication.instrument.instrument_id,
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
                        publication.instrument.instrument_id,
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
                        publication.artifact_id,
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
                            publication.instrument.instrument_id,
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
                            publication.instrument.instrument_id,
                            row.trade_date,
                            STAGE10_PRICE_VARIANT,
                            STAGE10_CURRENCY_SEGMENT,
                        ),
                    )
                written += 1
            artifact = ArtifactWrite(
                artifact_id=publication.artifact_id,
                dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                content_sha256=capture.raw_bytes_sha256,
                media_type="application/json",
                byte_count=len(capture.raw_bytes),
                source_reference="fmp/stable/historical-price-eod/full",
                request_scope=dict(publication.request_scope),
                captured_at=publication.captured_at,
                captured_precision="datetime",
                normalization_version=STAGE10_PRICE_NORMALIZATION_VERSION,
            )
            quality = (
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_price_quality",
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                    rule_id="response_digest_and_scope",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    artifact_id=publication.artifact_id,
                    snapshot_id=publication.snapshot_id,
                    observed={"rows": len(capture.rows), "complete": True},
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "stage10_price_quality",
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
                        "rows": len(capture.rows),
                        "earliest_trade_date": capture.earliest_date,
                        "latest_trade_date": capture.latest_date,
                    },
                ),
            )
            return WriteResult(
                written_count=written,
                artifacts=(artifact,),
                snapshot=SnapshotWrite(
                    snapshot_id=publication.snapshot_id,
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    semantic_identity=publication.semantic_identity,
                    scope=dict(publication.request_scope),
                    completeness="complete",
                    row_count=len(capture.rows),
                    captured_at=publication.captured_at,
                    captured_precision="datetime",
                    validation_state="validated",
                    artifact_ids=(publication.artifact_id,),
                ),
                quality_results=quality,
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
            output_dataset_ids=_PRICE_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=STAGE10_DAILY_HISTORY_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.started_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.capture.rows),
            writer=writer,
            held_locks=held_locks,
        )


__all__ = (
    "PreparedStage10Publication",
    "STAGE10_DAILY_HISTORY_COLLECTOR_ID",
    "STAGE10_DAILY_PRICES_DATASET_ID",
    "STAGE10_EVIDENCE_DATASET_ID",
    "STAGE10_INSTRUMENTS_DATASET_ID",
    "STAGE10_UNIVERSE_COLLECTOR_ID",
    "STAGE10_UNIVERSES_DATASET_ID",
    "Stage10HistoryImporter",
)
