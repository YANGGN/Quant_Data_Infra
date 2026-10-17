"""Bounded FMP backfill for the missing pre-2021 IWM daily history.

The completed additive IWM capture remains immutable. This module binds one
separate request to the interval from the fund inception date through the day
before the retained five-year response begins, validates the existing 96-ETF
successor and accepted 1,254-row IWM state before network access, and appends
only previously absent Stage 10 price dates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
    FmpPriceHistoryUnavailable,
    FmpStage10PriceCapture,
    FmpStage10Transport,
    parse_fmp_stage10_price_response,
)
from .fmp_iwm_etf_history import (
    IWM_ETF_HISTORY_SYMBOL,
    IwmEtfHistoryPublisher,
    IwmEtfHistoryScope,
)
from .stage10_history_importer import (
    STAGE10_CURRENCY_SEGMENT,
    STAGE10_DAILY_PRICES_DATASET_ID,
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_PRICE_NORMALIZATION_VERSION,
    STAGE10_PRICE_VARIANT,
)


IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID: Final = (
    "fmp.market.iwm_etf_daily_history_backfill"
)
IWM_ETF_HISTORY_BACKFILL_HANDLER: Final = (
    "market.fmp_iwm_etf_daily_history_backfill"
)
IWM_ETF_HISTORY_BACKFILL_SCOPE_CONTRACT: Final = (
    "quant_data.iwm_etf_history_backfill_scope"
)
IWM_ETF_HISTORY_BACKFILL_SCOPE_VERSION: Final = "1.0.0"
IWM_ETF_HISTORY_BACKFILL_NORMALIZATION_VERSION: Final = (
    "stage10.iwm_etf_history_backfill.v1"
)
IWM_ETF_HISTORY_BACKFILL_START_DATE: Final = "2000-05-22"
IWM_ETF_HISTORY_BACKFILL_END_DATE: Final = "2021-08-29"
IWM_ETF_HISTORY_FIRST_RETAINED_DATE: Final = "2021-08-30"
IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT: Final = 1_254
IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256: Final = (
    "6721ad6b8607a8aa6aca91f467b792ee76f91e2957ed06ae5402b05a99091e93"
)
IWM_ETF_HISTORY_BACKFILL_INPUT_DATASETS: Final = (
    "market.stage10.instruments",
    "market.stage10.universes",
)
IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS: Final = (
    STAGE10_EVIDENCE_DATASET_ID,
    STAGE10_DAILY_PRICES_DATASET_ID,
)
IWM_ETF_HISTORY_PRIOR_COMPLETION: Final[dict[str, str]] = {
    "base_scope_manifest_sha256": (
        "0782e0fdf39c3113f3c9537238200c9c73495f2fc2462637792d723cf33e68fd"
    ),
    "normalized_complete_history_sha256": (
        "6338a0330718a22e17f094c956205986cc4a715dd8e7b2ca3abf92d11efc82c6"
    ),
    "response_sha256": (
        "dfc44adb7cadbf650eedf6a615442cbe045be2b714a41540aa1b629432a65e69"
    ),
    "successor_membership_sha256": (
        "c0d84f5ff2c35dc13d44da136b04a84687dad7242527b86abcb555f99c7f67b1"
    ),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_API_KEY = re.compile(r"^[^\s\x00-\x1f\x7f]{1,4096}$")
_SCOPE_MAX_BYTES: Final = 64 * 1024


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _utc_text(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"IWM backfill {label} must be an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError("IWM backfill price value must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _store_map_identity(store_map: StoreMap) -> tuple[tuple[str, str], ...]:
    if not isinstance(store_map, StoreMap):
        raise ValidationError("IWM backfill requires explicit stores")
    return tuple((role.value, canonical_path_uri(path)) for role, path in store_map.items())


@dataclass(frozen=True, slots=True)
class IwmEtfHistoryBackfillBounds:
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
class IwmEtfHistoryBackfillScope:
    """One immutable missing-date slice bound to the completed IWM successor."""

    iwm_scope: IwmEtfHistoryScope = field(repr=False)
    bounds: IwmEtfHistoryBackfillBounds
    manifest_sha256: str

    def manifest_mapping(self) -> dict[str, object]:
        return {
            "bounds": self.bounds.manifest_mapping(),
            "contract": IWM_ETF_HISTORY_BACKFILL_SCOPE_CONTRACT,
            "existing_history": {
                "first_retained_trade_date": IWM_ETF_HISTORY_FIRST_RETAINED_DATE,
                "prior_price_row_count": IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT,
            },
            "iwm_scope_manifest_sha256": IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256,
            "price_history": {
                "currency_policy": "provider_declared_no_conversion",
                "endpoint_path": FMP_STAGE10_PRICE_PATH,
                "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
                "history_policy": "fixed_missing_interval_before_retained_history",
                "interval": "daily",
                "price_variant": STAGE10_PRICE_VARIANT,
                "query_fields": ["symbol", "from", "to"],
                "symbol": IWM_ETF_HISTORY_SYMBOL,
                "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
                "volume_policy": "provider_value_nonnegative_zero_allowed",
            },
            "prior_completion": dict(IWM_ETF_HISTORY_PRIOR_COMPLETION),
            "provider": "fmp",
            "retry_policy": {"max_attempts": 1, "mode": "none"},
            "version": IWM_ETF_HISTORY_BACKFILL_SCOPE_VERSION,
        }


def load_iwm_etf_history_backfill_scope(
    path: str | Path,
    *,
    iwm_scope: IwmEtfHistoryScope,
) -> IwmEtfHistoryBackfillScope:
    """Load the closed pre-2021 request and bind it to the accepted successor."""

    if (
        not isinstance(iwm_scope, IwmEtfHistoryScope)
        or iwm_scope.manifest_sha256 != IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256
    ):
        raise ValidationError("IWM backfill requires the accepted IWM successor scope")
    if isinstance(path, bool) or not isinstance(path, (str, Path)):
        raise ValidationError("IWM backfill scope path is invalid")
    try:
        payload = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise ValidationError("IWM backfill scope is unavailable") from exc
    if not payload or len(payload) > _SCOPE_MAX_BYTES:
        raise ValidationError("IWM backfill scope is outside the byte bound")
    raw = loads_strict(payload, max_bytes=_SCOPE_MAX_BYTES)
    bounds = IwmEtfHistoryBackfillBounds(
        max_price_response_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
        max_price_rows=FMP_STAGE10_MAX_PRICE_ROWS,
        max_requests=1,
        max_seconds_per_request=FMP_STAGE10_TIMEOUT_SECONDS,
    )
    candidate = IwmEtfHistoryBackfillScope(
        iwm_scope=iwm_scope,
        bounds=bounds,
        manifest_sha256="0" * 64,
    )
    expected = candidate.manifest_mapping()
    if not isinstance(raw, Mapping) or dumps_strict(raw) != dumps_strict(expected):
        raise ValidationError("IWM backfill scope differs from the reviewed interval")
    return IwmEtfHistoryBackfillScope(
        iwm_scope=iwm_scope,
        bounds=bounds,
        manifest_sha256=_sha256_json(expected),
    )


class StdlibFmpIwmEtfHistoryBackfillTransport:
    """TLS-verified transport fixed to one dated IWM request."""

    @staticmethod
    def _validate_request(
        *,
        path: object,
        query: object,
        headers: object,
        timeout_seconds: object,
        max_bytes: object,
    ) -> tuple[dict[str, str], dict[str, str]]:
        if path != FMP_STAGE10_PRICE_PATH or not isinstance(query, Mapping):
            raise ValidationError("IWM backfill transport request is outside fixed scope")
        if not isinstance(headers, Mapping):
            raise ValidationError("IWM backfill transport headers are invalid")
        query_values = dict(query)
        header_values = dict(headers)
        if (
            query_values
            != {
                "symbol": IWM_ETF_HISTORY_SYMBOL,
                "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
                "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
            }
            or set(header_values) != {"apikey"}
            or not isinstance(header_values["apikey"], str)
            or _API_KEY.fullmatch(header_values["apikey"]) is None
            or timeout_seconds != FMP_STAGE10_TIMEOUT_SECONDS
            or max_bytes != FMP_STAGE10_MAX_PRICE_BYTES
        ):
            raise ValidationError("IWM backfill transport request is outside fixed scope")
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
                    raise ResourceLimitError("FMP response exceeds the IWM backfill byte bound")
            body = response.read(FMP_STAGE10_MAX_PRICE_BYTES + 1)
            if len(body) > FMP_STAGE10_MAX_PRICE_BYTES:
                raise ResourceLimitError("FMP response exceeds the IWM backfill byte bound")
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


def capture_iwm_etf_history_backfill(
    *,
    api_key: str,
    transport: FmpStage10Transport,
) -> FmpStage10PriceCapture:
    """Issue and strictly parse exactly one fixed missing-interval request."""

    response = transport.get(
        path=FMP_STAGE10_PRICE_PATH,
        query={
            "symbol": IWM_ETF_HISTORY_SYMBOL,
            "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
            "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
        },
        headers={"apikey": api_key},
        timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
        max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
    )
    if not isinstance(response, CapturedFmpStage10Response):
        raise ValidationError("IWM backfill transport returned an invalid response")
    media_type = response.content_type.split(";", 1)[0].strip().lower()
    if response.status != 200 or media_type != "application/json":
        raise StoreUnavailableError("FMP provider response was unavailable")
    if not response.body or len(response.body) > FMP_STAGE10_MAX_PRICE_BYTES:
        raise ResourceLimitError("FMP response is outside the IWM backfill byte bound")
    value = loads_strict(response.body, max_bytes=FMP_STAGE10_MAX_PRICE_BYTES)
    if value == []:
        raise FmpPriceHistoryUnavailable(
            symbol=IWM_ETF_HISTORY_SYMBOL,
            status=200,
            content_type="application/json",
            body_sha256=_sha256_bytes(response.body),
            body_byte_count=len(response.body),
        )
    capture = parse_fmp_stage10_price_response(
        symbol=IWM_ETF_HISTORY_SYMBOL,
        body=response.body,
    )
    if (
        capture.earliest_date < IWM_ETF_HISTORY_BACKFILL_START_DATE
        or capture.latest_date > IWM_ETF_HISTORY_BACKFILL_END_DATE
        or any(
            not IWM_ETF_HISTORY_BACKFILL_START_DATE
            <= row.trade_date
            <= IWM_ETF_HISTORY_BACKFILL_END_DATE
            for row in capture.rows
        )
    ):
        raise ValidationError("FMP response contains dates outside the IWM backfill interval")
    return capture


def _validate_backfill_registry(registry: Registry) -> Registry:
    if not isinstance(registry, Registry) or registry.status != "validated":
        raise ValidationError("IWM backfill requires the validated current registry")
    declarations = [
        item
        for item in registry.collectors
        if item.get("id") == IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID
    ]
    if len(declarations) != 1:
        raise ValidationError("IWM backfill collector is not registered exactly once")
    collector = declarations[0]
    if (
        collector.get("version") != "1.0.0"
        or collector.get("handler") != IWM_ETF_HISTORY_BACKFILL_HANDLER
        or collector.get("network") is not True
        or tuple(collector.get("input_datasets", ()))
        != IWM_ETF_HISTORY_BACKFILL_INPUT_DATASETS
        or tuple(collector.get("output_datasets", ()))
        != IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS
        or tuple(collector.get("configuration_env", ())) != ("FMP_API_KEY",)
        or collector.get("physical_locks") != "derived_from_output_store_paths"
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
        or collector.get("workload_bounds")
        != {
            "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
            "max_requests": 1,
            "max_rows": FMP_STAGE10_MAX_PRICE_ROWS,
            "max_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
        }
        or collector.get("retry_policy")
        != {
            "backoff": "none_single_attempt",
            "honor_retry_after": False,
            "max_attempts": 1,
            "transient_classes": [],
        }
        or collector.get("mutation_policy")
        != {
            "mode": "append_missing_price_versions",
            "unchanged": "zero_persistent_writes",
        }
        or collector.get("semantic_identity")
        != {
            "excludes": [
                "api_key",
                "captured_at",
                "http_headers",
                "source_row_order",
            ],
            "includes": [
                "iwm_scope_manifest_sha256",
                "backfill_scope_manifest_sha256",
                "request_scope",
                "normalization_version",
                "normalized_complete_backfill_sha256",
            ],
        }
    ):
        raise ValidationError("IWM backfill collector declaration is invalid")
    datasets = {item.id: item for item in registry.datasets}
    if any(
        dataset_id not in datasets
        or datasets[dataset_id].store != StoreRole.MARKET.value
        or not datasets[dataset_id].active
        for dataset_id in (
            *IWM_ETF_HISTORY_BACKFILL_INPUT_DATASETS,
            *IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS,
        )
    ):
        raise ValidationError("IWM backfill dataset binding is invalid")
    if any(
        datasets[dataset_id].collector_ids.count(
            IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID
        )
        != 1
        for dataset_id in IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS
    ):
        raise ValidationError("IWM backfill output binding is invalid")
    if any(
        step.collector_id == IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID
        for job in registry.jobs
        for step in job.steps
    ):
        raise ValidationError("IWM backfill must remain unbound from registry jobs")
    return registry


@dataclass(frozen=True, slots=True)
class _BackfillPublication:
    capture: FmpStage10PriceCapture
    request_scope: Mapping[str, object]
    request_scope_sha256: str
    semantic_identity: str
    captured_at: str
    instrument_id: str
    run_id: str
    artifact_id: str
    snapshot_id: str
    capture_id: str


@dataclass(frozen=True, slots=True)
class _PreparedBackfillState:
    owner_token: object
    store_map: StoreMap
    store_map_identity: tuple[tuple[str, str], ...]
    publication: _BackfillPublication


class PreparedIwmEtfHistoryBackfillPublication:
    """Opaque collector-bound backfill candidate."""

    __slots__ = ("__weakref__",)


_PREPARED: "weakref.WeakKeyDictionary[PreparedIwmEtfHistoryBackfillPublication, _PreparedBackfillState]" = (
    weakref.WeakKeyDictionary()
)


@dataclass(frozen=True, slots=True)
class IwmEtfHistoryBackfillRunReport:
    outcome: str
    backfill_scope_manifest_sha256: str
    iwm_scope_manifest_sha256: str
    normalized_complete_backfill_sha256: str
    response_sha256: str
    earliest_trade_date: str
    latest_trade_date: str
    price_row_count: int
    written_count: int

    def mapping(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "backfill_scope_manifest_sha256": self.backfill_scope_manifest_sha256,
            "iwm_scope_manifest_sha256": self.iwm_scope_manifest_sha256,
            "normalized_complete_backfill_sha256": (
                self.normalized_complete_backfill_sha256
            ),
            "response_sha256": self.response_sha256,
            "earliest_trade_date": self.earliest_trade_date,
            "latest_trade_date": self.latest_trade_date,
            "price_row_count": self.price_row_count,
            "written_count": self.written_count,
        }


class IwmEtfHistoryBackfillPublisher(IwmEtfHistoryPublisher):
    """Append one pre-retained IWM slice through the existing Stage 10 model."""

    def __init__(
        self,
        store_map: StoreMap,
        registry: Registry,
        scope: IwmEtfHistoryBackfillScope,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(scope, IwmEtfHistoryBackfillScope):
            raise ValidationError("IWM backfill publisher scope is invalid")
        super().__init__(store_map, registry, scope.iwm_scope, clock=clock)
        self.backfill_scope = scope
        self.registry = _validate_backfill_registry(registry)
        self._coordinator = IngestionCoordinator(
            store_map,
            code_version="iwm.etf.history.backfill.1.0.0",
        )
        self._backfill_owner_token = object()

    def _validate_initial_gap(self, connection: sqlite3.Connection) -> str:
        state = self._store_preflight_state()
        self._validate_store_state(connection, state)
        stats = connection.execute(
            """
            SELECT count(*) AS row_count, min(trade_date) AS first_trade_date
            FROM stage10_daily_prices
            WHERE instrument_id=? AND provider='fmp' AND price_variant=?
              AND currency_segment=?
            """,
            (state.iwm.instrument_id, STAGE10_PRICE_VARIANT, STAGE10_CURRENCY_SEGMENT),
        ).fetchone()
        gap_count = connection.execute(
            """
            SELECT count(*)
            FROM stage10_daily_prices
            WHERE instrument_id=? AND provider='fmp' AND price_variant=?
              AND currency_segment=? AND trade_date BETWEEN ? AND ?
            """,
            (
                state.iwm.instrument_id,
                STAGE10_PRICE_VARIANT,
                STAGE10_CURRENCY_SEGMENT,
                IWM_ETF_HISTORY_BACKFILL_START_DATE,
                IWM_ETF_HISTORY_BACKFILL_END_DATE,
            ),
        ).fetchone()[0]
        if (
            stats is None
            or int(stats["row_count"]) != IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT
            or str(stats["first_trade_date"]) != IWM_ETF_HISTORY_FIRST_RETAINED_DATE
            or int(gap_count) != 0
        ):
            raise ConflictError("IWM backfill requires the accepted missing-date boundary")
        return state.iwm.instrument_id

    def preflight_store(self) -> None:
        """Validate the accepted successor and exact missing interval before credentials."""

        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            self._validate_initial_gap(connection)

    def prepare(
        self,
        capture: FmpStage10PriceCapture,
    ) -> PreparedIwmEtfHistoryBackfillPublication:
        if (
            not isinstance(capture, FmpStage10PriceCapture)
            or capture.symbol != IWM_ETF_HISTORY_SYMBOL
            or capture.earliest_date < IWM_ETF_HISTORY_BACKFILL_START_DATE
            or capture.latest_date > IWM_ETF_HISTORY_BACKFILL_END_DATE
            or not capture.rows
        ):
            raise ValidationError("IWM backfill capture is invalid")
        captured_at = _utc_text(self._clock(), "captured_at")
        if capture.latest_date > captured_at[:10]:
            raise ValidationError("IWM backfill cannot contain future trade dates")
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            instrument_id = self._validate_initial_gap(connection)
        request_scope: dict[str, object] = {
            "provider": "fmp",
            "endpoint_path": FMP_STAGE10_PRICE_PATH,
            "symbol": IWM_ETF_HISTORY_SYMBOL,
            "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
            "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
            "interval": "daily",
            "history_policy": "fixed_missing_interval_before_retained_history",
            "price_variant": STAGE10_PRICE_VARIANT,
            "currency_policy": "provider_declared_no_conversion",
            "volume_policy": "provider_value_nonnegative_zero_allowed",
            "iwm_scope_manifest_sha256": self.scope.manifest_sha256,
            "backfill_scope_manifest_sha256": self.backfill_scope.manifest_sha256,
            "target_profile_id": self.scope.target_profile_id,
        }
        request_scope_sha256 = _sha256_json(request_scope)
        semantic_identity = _sha256_json(
            {
                "collector_id": IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID,
                "canonical_dataset_id": STAGE10_DAILY_PRICES_DATASET_ID,
                "iwm_scope_manifest_sha256": self.scope.manifest_sha256,
                "backfill_scope_manifest_sha256": self.backfill_scope.manifest_sha256,
                "request_scope": request_scope,
                "normalization_version": IWM_ETF_HISTORY_BACKFILL_NORMALIZATION_VERSION,
                "normalized_complete_backfill_sha256": capture.semantic_sha256,
            }
        )
        publication = _BackfillPublication(
            capture=capture,
            request_scope=request_scope,
            request_scope_sha256=request_scope_sha256,
            semantic_identity=semantic_identity,
            captured_at=captured_at,
            instrument_id=instrument_id,
            run_id=stable_id(
                "iwm_etf_history_backfill_run",
                STAGE10_DAILY_PRICES_DATASET_ID,
                semantic_identity,
            ),
            artifact_id=stable_id(
                "iwm_etf_history_backfill_artifact",
                STAGE10_EVIDENCE_DATASET_ID,
                capture.raw_bytes_sha256,
                request_scope_sha256,
            ),
            snapshot_id=stable_id(
                "iwm_etf_history_backfill_snapshot",
                STAGE10_DAILY_PRICES_DATASET_ID,
                semantic_identity,
            ),
            capture_id=stable_id(
                "iwm_etf_history_backfill_capture",
                STAGE10_EVIDENCE_DATASET_ID,
                semantic_identity,
            ),
        )
        candidate = PreparedIwmEtfHistoryBackfillPublication()
        _PREPARED[candidate] = _PreparedBackfillState(
            owner_token=self._backfill_owner_token,
            store_map=self.store_map,
            store_map_identity=_store_map_identity(self.store_map),
            publication=publication,
        )
        return candidate

    def _prepared_state(self, prepared: object) -> _PreparedBackfillState:
        if not isinstance(prepared, PreparedIwmEtfHistoryBackfillPublication):
            raise ValidationError("IWM backfill requires a prepared publication")
        state = _PREPARED.get(prepared)
        if (
            state is None
            or state.owner_token is not self._backfill_owner_token
            or state.store_map is not self.store_map
            or state.store_map_identity != _store_map_identity(self.store_map)
        ):
            raise ValidationError("IWM backfill prepared publication is foreign or expired")
        return state

    def publish(
        self,
        prepared: PreparedIwmEtfHistoryBackfillPublication,
        *,
        held_locks: HeldWriteLocks | None = None,
    ) -> IngestionReceipt:
        state = self._prepared_state(prepared)
        if held_locks is not None:
            if not isinstance(held_locks, HeldWriteLocks):
                raise ValidationError("IWM backfill held locks are invalid")
            held_locks._require_target(self.store_map, StoreRole.MARKET)
        return self._publish_backfill(state.publication, held_locks=held_locks)

    def _publish_backfill(
        self,
        publication: _BackfillPublication,
        *,
        held_locks: HeldWriteLocks | None,
    ) -> IngestionReceipt:
        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != publication.run_id:
                raise ValidationError("IWM backfill coordinator run identity is invalid")
            instrument_id = self._validate_initial_gap(connection)
            if instrument_id != publication.instrument_id:
                raise ConflictError("IWM backfill instrument identity changed")
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
                ) VALUES (?, ?, 'fmp', ?, 'IWM', ?, ?, ?, ?, ?, ?, 200,
                          'application/json', ?, 'complete', ?, ?, ?, 'datetime',
                          ?, ?, ?, ?, ?)
                """,
                (
                    publication.capture_id,
                    STAGE10_EVIDENCE_DATASET_ID,
                    publication.instrument_id,
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
                current = connection.execute(
                    """
                    SELECT 1 FROM stage10_daily_prices
                    WHERE instrument_id=? AND trade_date=? AND provider='fmp'
                      AND price_variant=? AND currency_segment=?
                    """,
                    (
                        publication.instrument_id,
                        row.trade_date,
                        STAGE10_PRICE_VARIANT,
                        STAGE10_CURRENCY_SEGMENT,
                    ),
                ).fetchone()
                if current is not None:
                    raise ConflictError("IWM backfill overlaps an existing canonical price")
                version_id = stable_id(
                    "stage10_daily_price_version",
                    publication.instrument_id,
                    row.trade_date,
                    STAGE10_PRICE_VARIANT,
                    STAGE10_CURRENCY_SEGMENT,
                    "1",
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
                              'datetime', 1, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        publication.instrument_id,
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
                        publication.capture_id,
                        publication.artifact_id,
                        publication.snapshot_id,
                        active_run_id,
                        row.source_row,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO stage10_daily_prices (
                        instrument_id, trade_date, provider, price_variant,
                        currency_segment, current_version_id
                    ) VALUES (?, ?, 'fmp', ?, ?, ?)
                    """,
                    (
                        publication.instrument_id,
                        row.trade_date,
                        STAGE10_PRICE_VARIANT,
                        STAGE10_CURRENCY_SEGMENT,
                        version_id,
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
                normalization_version=IWM_ETF_HISTORY_BACKFILL_NORMALIZATION_VERSION,
            )
            quality = (
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_backfill_quality",
                        STAGE10_EVIDENCE_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_EVIDENCE_DATASET_ID,
                    rule_id="fixed_missing_interval_response",
                    rule_version="1.0.0",
                    severity="critical",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=publication.snapshot_id,
                    artifact_id=publication.artifact_id,
                    snapshot_id=publication.snapshot_id,
                    observed={
                        "rows": len(capture.rows),
                        "from": IWM_ETF_HISTORY_BACKFILL_START_DATE,
                        "to": IWM_ETF_HISTORY_BACKFILL_END_DATE,
                    },
                ),
                QualityWrite(
                    quality_result_id=stable_id(
                        "iwm_etf_history_backfill_quality",
                        STAGE10_DAILY_PRICES_DATASET_ID,
                        publication.semantic_identity,
                    ),
                    dataset_id=STAGE10_DAILY_PRICES_DATASET_ID,
                    rule_id="missing_interval_ohlcv_append",
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
            output_dataset_ids=IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS,
            semantic_identity=publication.semantic_identity,
            run_id=publication.run_id,
            command=IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID,
            scope=dict(publication.request_scope),
            started_at=publication.captured_at,
            completed_at=publication.captured_at,
            fetched_count=len(publication.capture.rows),
            writer=writer,
            held_locks=held_locks,
        )


def run_iwm_etf_history_backfill(
    *,
    store_map: StoreMap,
    registry: Registry,
    scope: IwmEtfHistoryBackfillScope,
    transport: FmpStage10Transport,
    api_key: str,
    captured_at: datetime,
) -> IwmEtfHistoryBackfillRunReport:
    """Preflight, fetch, and publish the one fixed pre-retained IWM slice."""

    publisher = IwmEtfHistoryBackfillPublisher(
        store_map,
        registry,
        scope,
        clock=lambda: captured_at,
    )
    publisher.preflight_store()
    capture = capture_iwm_etf_history_backfill(api_key=api_key, transport=transport)
    prepared = publisher.prepare(capture)
    receipt = publisher.publish(prepared)
    return IwmEtfHistoryBackfillRunReport(
        outcome=receipt.outcome,
        backfill_scope_manifest_sha256=scope.manifest_sha256,
        iwm_scope_manifest_sha256=scope.iwm_scope.manifest_sha256,
        normalized_complete_backfill_sha256=capture.semantic_sha256,
        response_sha256=capture.raw_bytes_sha256,
        earliest_trade_date=capture.earliest_date,
        latest_trade_date=capture.latest_date,
        price_row_count=len(capture.rows),
        written_count=receipt.written_count,
    )


__all__ = (
    "IWM_ETF_HISTORY_BACKFILL_COLLECTOR_ID",
    "IWM_ETF_HISTORY_BACKFILL_END_DATE",
    "IWM_ETF_HISTORY_BACKFILL_HANDLER",
    "IWM_ETF_HISTORY_BACKFILL_INPUT_DATASETS",
    "IWM_ETF_HISTORY_BACKFILL_OUTPUT_DATASETS",
    "IWM_ETF_HISTORY_BACKFILL_START_DATE",
    "IwmEtfHistoryBackfillPublisher",
    "IwmEtfHistoryBackfillRunReport",
    "IwmEtfHistoryBackfillScope",
    "PreparedIwmEtfHistoryBackfillPublication",
    "StdlibFmpIwmEtfHistoryBackfillTransport",
    "capture_iwm_etf_history_backfill",
    "load_iwm_etf_history_backfill_scope",
    "run_iwm_etf_history_backfill",
)
