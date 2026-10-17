"""Bounded, durable manual Stage 12C Market v1 gap population.

This module is deliberately a narrow operations adapter.  It owns the
credential-free durable journal, the one-at-a-time FMP transport seam, and
the recovery/replay discipline around the Stage 12C domain collector.  The
domain collector owns parsing and the short SQLite publication transaction;
this module never holds a database lock while it invokes a transport.

The public live entry point has no target-path override.  Its only write
target is the reviewed project-local ``data/market.sqlite``.  The injectable
runner exists solely so dependency-free tests can construct a temporary
project, target, retained-source equivalent, transport, and clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import errno
import fcntl
import hashlib
import http.client
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
from typing import Final, Protocol, runtime_checkable
from urllib.parse import quote, urlencode

from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..market.stage12c_incremental import (
    PreparedStage12CPublication,
    Stage12CIncrementalCollector,
    Stage12CLiveRequest,
    Stage12CLiveResponse,
    Stage12CPublicationReceipt,
)
from ..market.stage12_scope import Stage12MarketV1Scope, load_stage12_market_v1_scope
from ..market.stage12b_scope import (
    Stage12BIncrementalMarketScope,
    load_stage12b_incremental_market_v1_scope,
)
from ..market.stage12c_scope import (
    Stage12CMarketGapV1Scope,
    load_stage12c_market_gap_v1_scope,
    stage12c_ordered_symbols,
)
from ..stores import StoreWriteLock


APPROVED_PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_RETAINED_SOURCE: Final = Path(
    "/home/volatility/quant-data-nonprod/stage10-fmp-market-history-v1/stores/market.sqlite"
)
APPROVED_MARKET_RELATIVE_PATH: Final = Path("data/market.sqlite")
APPROVED_PRIVATE_RELATIVE_ROOT: Final = Path(
    "data/.stage12/market-v1/stage12c-20260813-20260814"
)
STAGE12C_BASELINE_SHA256: Final = (
    "b0ee0a02cc74e603320d0fa4f7a68a64339f8ed834229bdc480efa0351cc6f3c"
)
STAGE12C_ENDPOINT_PATH: Final = "/stable/historical-price-eod/full"
STAGE12C_FMP_HOST: Final = "financialmodelingprep.com"
STAGE12C_TIMEOUT_SECONDS: Final = 45
STAGE12C_MAX_RESPONSE_BYTES: Final = 65_536
STAGE12C_MINIMUM_REQUEST_INTERVAL_SECONDS: Final = 1.0
STAGE12C_EXPECTED_SYMBOL_COUNT: Final = 629
STAGE12C_EXPECTED_SESSIONS: Final = ("2026-08-13", "2026-08-14")

# The user authorized exactly this already-captured Stage 12C response for
# local closure.  These are deliberately private execution bindings, not a
# general HTTP 402 policy or a scope/plan revision.
_STAGE12C_AXJO_402_AUTHORIZATION_ID: Final = (
    "user_authorized_stage12c_axjo_402_entitlement_unavailable_v1"
)
_STAGE12C_AXJO_402_PLAN_SHA256: Final = (
    "5e5c07f03313c1a0d3d3980fa8a900973a301664fecf84e6d37ab3c2f38a92f1"
)
_STAGE12C_AXJO_402_ORDINAL: Final = 615
_STAGE12C_AXJO_402_SYMBOL: Final = "^AXJO"
_STAGE12C_AXJO_402_FROM_DATE: Final = "2026-08-13"
_STAGE12C_AXJO_402_TO_DATE: Final = "2026-08-14"
_STAGE12C_AXJO_402_UNIT_IDENTIFIER: Final = (
    "stage12c-97582963f362dc4a7c8f41bd579f0975"
)
_STAGE12C_AXJO_402_INTENT_SHA256: Final = (
    "d4638e71d1277279ab5e7d8d8208839fc7806e70400a9fd90f6a6713d5770137"
)
_STAGE12C_AXJO_402_HTTP_STATUS: Final = 402
_STAGE12C_AXJO_402_CONTENT_TYPE: Final = "application/json; charset=utf-8"
_STAGE12C_AXJO_402_RESPONSE_BYTE_COUNT: Final = 215
_STAGE12C_AXJO_402_RESPONSE_SHA256: Final = (
    "38e6a6ea2ed189c5d4cab610c93eefc962b31fffdae06dd65390b90d7c0cff7c"
)
_STAGE12C_AXJO_402_SPOOL_SHA256: Final = (
    "e65c1a9f7cb51ebfcd5702145836612d8d1e5d02de4c79e79ef2849d3e84e5de"
)

# This is a separate, exact user authorization for the sealed response on the
# remaining frozen units.  Per-unit spool digests remain verified by
# _read_response_spool rather than being pinned here.
_STAGE12C_SEALED_402_CONTINUATION_FIRST_ORDINAL: Final = 617
_STAGE12C_SEALED_402_CONTINUATION_LAST_ORDINAL: Final = 629

_ROOT_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_MAX_SIDECAR_JSON_BYTES: Final = 128 * 1024
_MAX_RECEIPT_JSON_BYTES: Final = 768 * 1024
_MAX_ERROR_TEXT: Final = 4_096
_CONTRACT: Final = "quant_data.stage12c_market_gap_v1"
_VERSION: Final = "1.0.0"
_PLAN_CONTRACT: Final = "quant_data.stage12c_market_gap_plan"
_BASELINE_CONTRACT: Final = "quant_data.stage12c_market_gap_baseline"
_INTENT_CONTRACT: Final = "quant_data.stage12c_market_gap_intent"
_SPOOL_CONTRACT: Final = "quant_data.stage12c_market_gap_spool"
_RESULT_CONTRACT: Final = "quant_data.stage12c_market_gap_result"
_RESUME_CONTRACT: Final = "quant_data.stage12c_market_gap_resume"
_COMPLETION_CONTRACT: Final = "quant_data.stage12c_market_gap_completion"
_RUN_LOCK_FILENAME: Final = "run.lock"
_REQUIRED_TABLES: Final = frozenset(
    {
        "dataset_registry",
        "ingestion_runs",
        "ingestion_artifacts",
        "ingestion_snapshots",
        "ingestion_snapshot_artifacts",
        "ingestion_run_outputs",
        "stage10_instruments",
        "stage10_daily_price_captures",
        "stage10_daily_price_versions",
        "stage10_daily_prices",
    }
)
_ROW_KEYS: Final = frozenset(
    {
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
    }
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValidationError(f"Stage 12C {label} is invalid")
    return value


def _utc_text(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"Stage 12C {label} is invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc_text(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 64:
        raise ConflictError(f"Stage 12C {label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConflictError(f"Stage 12C {label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConflictError(f"Stage 12C {label} is invalid")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    if canonical != value:
        raise ConflictError(f"Stage 12C {label} is invalid")
    return parsed.astimezone(timezone.utc)


def _content_type(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValidationError(f"Stage 12C {label} is invalid")
    normalized = value.split(";", 1)[0].strip().casefold()
    if normalized != "application/json":
        raise StoreUnavailableError("Stage 12C provider response has an unreviewed media type")
    return normalized


def _date_text(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ValidationError(f"Stage 12C {label} is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError(f"Stage 12C {label} is invalid") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"Stage 12C {label} is invalid")
    return value


def _number(value: object, label: str, *, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValidationError(f"Stage 12C {label} is invalid")
    parsed = Decimal(value)
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise ValidationError(f"Stage 12C {label} is invalid")
    return parsed


def _volume(value: object, label: str) -> int:
    parsed = _number(value, label, nonnegative=True)
    if parsed != parsed.to_integral_value():
        raise ValidationError(f"Stage 12C {label} is invalid")
    result = int(parsed)
    if result > 9_223_372_036_854_775_807:
        raise ValidationError(f"Stage 12C {label} is invalid")
    return result


def _physical_identity(path: Path) -> tuple[int, int, int]:
    try:
        info = path.stat()
    except OSError as exc:
        raise StoreUnavailableError("Stage 12C required path is unavailable") from exc
    return int(info.st_dev), int(info.st_ino), int(info.st_nlink)

def _identity_sha256(identity: tuple[int, int, int]) -> str:
    return _sha256_json(
        {"device": identity[0], "inode": identity[1], "nlink": identity[2]}
    )



def _require_direct_regular_file(path: Path, label: str) -> tuple[int, int, int]:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise ConflictError(f"Stage 12C {label} must name a direct regular file")
        resolved = path.resolve(strict=True)
    except ConflictError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError(f"Stage 12C {label} is unavailable") from exc
    if resolved != path or int(info.st_nlink) != 1:
        raise ConflictError(f"Stage 12C {label} physical identity is unsafe")
    return int(info.st_dev), int(info.st_ino), int(info.st_nlink)


def _require_direct_directory(path: Path, label: str, *, mode: int | None = None) -> None:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise ConflictError(f"Stage 12C {label} must name a direct directory")
        if path.resolve(strict=True) != path:
            raise ConflictError(f"Stage 12C {label} physical identity is unsafe")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise ConflictError(f"Stage 12C {label} has an unsafe mode")
    except ConflictError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError(f"Stage 12C {label} is unavailable") from exc


def _fsync_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(descriptor)
    except OSError as exc:
        raise StoreUnavailableError("Stage 12C private sidecar directory cannot be synchronized") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_exclusive_bytes(path: Path, value: bytes, *, maximum: int) -> None:
    if not isinstance(value, bytes) or len(value) > maximum:
        raise ResourceLimitError("Stage 12C sidecar exceeds its reviewed bound")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        os.fchmod(descriptor, _FILE_MODE)
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("short private sidecar write")
            offset += written
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise ConflictError("Stage 12C immutable sidecar already exists") from exc
    except OSError as exc:
        raise StoreUnavailableError("Stage 12C private sidecar cannot be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _fsync_directory(path.parent)


def _write_exclusive_json(path: Path, value: Mapping[str, object], *, maximum: int) -> None:
    _write_exclusive_bytes(path, dumps_strict(dict(value), max_bytes=maximum).encode("utf-8"), maximum=maximum)


def _require_private_file(path: Path, label: str, *, maximum: int) -> bytes:
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != _FILE_MODE:
            raise ConflictError(f"Stage 12C {label} is unsafe")
        if info.st_size > maximum:
            raise ResourceLimitError(f"Stage 12C {label} exceeds its reviewed bound")
        value = path.read_bytes()
    except (ConflictError, ResourceLimitError):
        raise
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12C {label} is unavailable") from exc
    if len(value) != info.st_size:
        raise ConflictError(f"Stage 12C {label} changed while being read")
    return value


def _read_private_json(path: Path, label: str, *, maximum: int) -> Mapping[str, object]:
    try:
        value = loads_strict(_require_private_file(path, label, maximum=maximum), max_bytes=maximum)
    except (ResourceLimitError, ValidationError) as exc:
        raise ConflictError(f"Stage 12C {label} is invalid") from exc
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ConflictError(f"Stage 12C {label} is invalid")
    return value


def _same_path_or_descendant(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


_FIXTURE_RUNNER_CAPABILITY: Final = object()
_CANONICAL_LIVE_CAPABILITY: Final = object()


def _resolved_system_temp_root() -> Path:
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12C system temporary root is unavailable") from exc
    _require_direct_directory(temporary_root, "system temporary root")
    return temporary_root


def _require_strict_temp_child(path: Path, label: str) -> None:
    temporary_root = _resolved_system_temp_root()
    try:
        relative = path.relative_to(temporary_root)
    except ValueError as exc:
        raise ValidationError(
            f"Stage 12C {label} must be a strict child of the system temporary root"
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise ValidationError(
            f"Stage 12C {label} must be a strict child of the system temporary root"
        )


def _require_fixture_project_root(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ValidationError("Stage 12C fixture project root is required")
    supplied = Path(value)
    if supplied == APPROVED_PROJECT_ROOT:
        raise ValidationError("Stage 12C public runner accepts fixture project roots only")
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValidationError("Stage 12C fixture project root must be an absolute physical directory")
    try:
        project = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12C fixture project root is unavailable") from exc
    if supplied != project:
        raise ValidationError("Stage 12C public runner accepts fixture project roots only")
    _require_direct_directory(project, "fixture project root")
    _require_strict_temp_child(project, "fixture project root")
    return project


def _require_fixture_retained_source(value: str | Path, project: Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ValidationError("Stage 12C fixture retained source is required")
    supplied = Path(value)
    if supplied == APPROVED_RETAINED_SOURCE:
        raise ValidationError("Stage 12C public runner accepts fixture retained sources only")
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValidationError("Stage 12C fixture retained source must be an absolute physical file")
    try:
        source = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12C fixture retained source is unavailable") from exc
    if supplied != source:
        raise ValidationError("Stage 12C public runner accepts fixture retained sources only")
    _require_direct_regular_file(source, "fixture retained source")
    _require_strict_temp_child(source, "fixture retained source")
    if _same_path_or_descendant(source, project) or _same_path_or_descendant(project, source):
        raise ValidationError("Stage 12C fixture source and project root must be disjoint")
    return source


def _require_fixture_target(project: Path) -> tuple[Path, tuple[int, int, int]]:
    data = project / "data"
    target = data / APPROVED_MARKET_RELATIVE_PATH.name
    if target == APPROVED_PROJECT_ROOT / APPROVED_MARKET_RELATIVE_PATH:
        raise ValidationError("Stage 12C public runner cannot select the canonical target")
    _require_direct_directory(data, "fixture project data directory")
    identity = _require_direct_regular_file(target, "fixture project market target")
    _require_strict_temp_child(target, "fixture project market target")
    return target, identity


def _require_canonical_live_roots() -> tuple[Path, Path, Path]:
    """Return the exact reviewed roots only for the private zero-argument path."""

    project = APPROVED_PROJECT_ROOT
    target = project / APPROVED_MARKET_RELATIVE_PATH
    source = APPROVED_RETAINED_SOURCE
    _require_direct_directory(project, "canonical project root")
    _require_direct_directory(target.parent, "canonical project data directory")
    target_identity = _require_direct_regular_file(target, "canonical project market target")
    source_identity = _require_direct_regular_file(source, "canonical retained source")
    if source_identity == target_identity:
        raise ConflictError("Stage 12C canonical retained source and target must be distinct")
    return project, target, source


@dataclass(frozen=True, slots=True)
class Stage12CTransportResponse:
    """Raw response returned by one injected Stage 12C provider attempt."""

    status: int
    media_type: str
    body: bytes = field(repr=False)
    elapsed_seconds: int | float | Decimal
    redirected: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int) or not 100 <= self.status <= 599:
            raise ValidationError("Stage 12C transport status is invalid")
        if not isinstance(self.media_type, str) or len(self.media_type) > 256:
            raise ValidationError("Stage 12C transport media type is invalid")
        if not isinstance(self.body, bytes):
            raise ValidationError("Stage 12C transport body is invalid")
        if isinstance(self.elapsed_seconds, bool) or not isinstance(self.elapsed_seconds, (int, float, Decimal)):
            raise ValidationError("Stage 12C transport elapsed time is invalid")
        parsed = Decimal(str(self.elapsed_seconds))
        if not parsed.is_finite() or parsed < 0:
            raise ValidationError("Stage 12C transport elapsed time is invalid")
        if not isinstance(self.redirected, bool):
            raise ValidationError("Stage 12C transport redirect marker is invalid")


@runtime_checkable
class Stage12CTransport(Protocol):
    """A one-request, injectable FMP HTTP seam."""

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse: ...


class _CanonicalStdlibStage12CTransport:
    """Private capability-gated TLS transport for the zero-argument live path."""

    __slots__ = ("_capability",)

    def __init__(self, *, capability: object) -> None:
        if capability is not _CANONICAL_LIVE_CAPABILITY:
            raise ValidationError("Stage 12C stdlib transport is canonical-live only")
        self._capability = capability

    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12CTransportResponse:
        if self._capability is not _CANONICAL_LIVE_CAPABILITY:
            raise ValidationError("Stage 12C stdlib transport capability is invalid")

        _validate_transport_request(
            path=path,
            query=query,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        target = f"{path}?{urlencode(dict(query))}"
        connection: http.client.HTTPSConnection | None = None
        started = time.monotonic()
        try:
            connection = http.client.HTTPSConnection(STAGE12C_FMP_HOST, timeout=timeout_seconds)
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            declared_size = response.getheader("Content-Length")
            if declared_size is not None:
                if not isinstance(declared_size, str) or not declared_size.isdecimal():
                    raise StoreUnavailableError("Stage 12C provider response is unavailable")
                if int(declared_size) > max_bytes:
                    raise ResourceLimitError("Stage 12C provider response exceeds the reviewed byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("Stage 12C provider response exceeds the reviewed byte bound")
            return Stage12CTransportResponse(
                status=response.status,
                media_type=response.getheader("Content-Type") or "",
                body=body,
                elapsed_seconds=time.monotonic() - started,
                redirected=300 <= response.status <= 399,
            )
        except (ResourceLimitError, ValidationError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Stage 12C provider request failed") from exc
        finally:
            if connection is not None:
                connection.close()


def _validated_fmp_api_key(value: object) -> str:
    """Return the one credential shape accepted by the reviewed FMP transport."""

    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ValidationError("Stage 12C provider credential is invalid")
    return value


def _validate_transport_request(
    *,
    path: object,
    query: object,
    headers: object,
    timeout_seconds: object,
    max_bytes: object,
) -> None:
    if path != STAGE12C_ENDPOINT_PATH or timeout_seconds != STAGE12C_TIMEOUT_SECONDS or max_bytes != STAGE12C_MAX_RESPONSE_BYTES:
        raise ValidationError("Stage 12C transport request is outside the reviewed scope")
    if not isinstance(query, Mapping) or dict(query).keys() != {"symbol", "from", "to"}:
        raise ValidationError("Stage 12C transport request is outside the reviewed scope")
    if not isinstance(headers, Mapping) or set(headers) != {"apikey"}:
        raise ValidationError("Stage 12C transport request is outside the reviewed scope")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in dict(query).items()):
        raise ValidationError("Stage 12C transport request is outside the reviewed scope")
    _validated_fmp_api_key(headers["apikey"])


@dataclass(frozen=True, slots=True)
class Stage12CBaselineExpectation:
    """Retained Stage 10 baseline facts that Stage 12C must prove before writes."""

    sha256: str
    current_price_rows: int
    immutable_price_versions: int
    daily_price_captures: int

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "baseline digest")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.current_price_rows,
                self.immutable_price_versions,
                self.daily_price_captures,
            )
        ):
            raise ValidationError("Stage 12C baseline counts are invalid")


RETAINED_STAGE10_BASELINE: Final = Stage12CBaselineExpectation(
    sha256=STAGE12C_BASELINE_SHA256,
    current_price_rows=4_235_893,
    immutable_price_versions=4_236_635,
    daily_price_captures=4_591,
)


@dataclass(frozen=True, slots=True)
class Stage12CMarketGapRunReport:
    """Credential- and path-free result of one bounded Stage 12C invocation."""

    plan_sha256: str
    scope_manifest_sha256: str
    closed_unit_count: int
    published_unit_count: int
    terminal_noncoverage_count: int
    requests_issued: int
    completion: str
    terminal_outcomes: tuple[Mapping[str, object], ...]
    receipt_sha256: str | None

    def to_primitive(self) -> dict[str, object]:
        return {
            "closed_unit_count": self.closed_unit_count,
            "completion": self.completion,
            "plan_sha256": self.plan_sha256,
            "published_unit_count": self.published_unit_count,
            "receipt_sha256": self.receipt_sha256,
            "requests_issued": self.requests_issued,
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "terminal_noncoverage_count": self.terminal_noncoverage_count,
            "terminal_outcomes": [dict(item) for item in self.terminal_outcomes],
        }


@dataclass(frozen=True, slots=True)
class _Unit:
    ordinal: int
    symbol: str
    from_date: str
    to_date: str
    session_dates: tuple[str, str]
    plan_sha256: str
    scope_manifest_sha256: str
    stage12a_roster_sha256: str
    stage12b_scope_sha256: str
    identifier: str

    @property
    def filename_stem(self) -> str:
        return f"{self.ordinal:04d}-{self.identifier}"

    def material(self) -> dict[str, object]:
        return {
            "endpoint_path": STAGE12C_ENDPOINT_PATH,
            "from": self.from_date,
            "ordinal": self.ordinal,
            "plan_sha256": self.plan_sha256,
            "query": {"from": self.from_date, "symbol": self.symbol, "to": self.to_date},
            "scope_manifest_sha256": self.scope_manifest_sha256,
            "session_dates": list(self.session_dates),
            "stage12a_roster_sha256": self.stage12a_roster_sha256,
            "stage12b_scope_sha256": self.stage12b_scope_sha256,
            "symbol": self.symbol,
            "to": self.to_date,
            "unit_id": self.identifier,
        }


@dataclass(frozen=True, slots=True)
class _Plan:
    units: tuple[_Unit, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class _Layout:
    root: Path
    intents: Path
    bodies: Path
    responses: Path
    results: Path
    resume: Path
    baseline: Path
    plan: Path
    completion: Path
    run_lock: Path


@dataclass(frozen=True, slots=True)
class _SourceFingerprint:
    sha256: str
    byte_count: int
    identity: tuple[int, int, int]
    mtime_ns: int

    def public_mapping(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "identity_sha256": _identity_sha256(self.identity),
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class _JournalState:
    unit: _Unit
    intent: Mapping[str, object] | None
    intent_sha256: str | None
    body: bytes | None
    response: Mapping[str, object] | None
    result: Mapping[str, object] | None
    resume: Mapping[str, object] | None



def _private_file(layout: _Layout, directory: str, unit: _Unit, suffix: str) -> Path:
    return getattr(layout, directory) / f"{unit.filename_stem}{suffix}"


def _require_project_root(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ValidationError("Stage 12C project root is required")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValidationError("Stage 12C project root must be an absolute physical directory")
    try:
        project = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12C project root is unavailable") from exc
    _require_direct_directory(project, "project root")
    return project


def _require_retained_source(value: str | Path) -> Path:
    if isinstance(value, bool) or not isinstance(value, (str, Path)):
        raise ValidationError("Stage 12C retained source is required")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValidationError("Stage 12C retained source must be an absolute physical file")
    try:
        source = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("Stage 12C retained source is unavailable") from exc
    _require_direct_regular_file(source, "retained source")
    return source


def _ensure_private_directory(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        _require_direct_directory(path, label, mode=_ROOT_MODE)
        return
    parent = path.parent
    _require_direct_directory(parent, f"{label} parent")
    try:
        os.mkdir(path, _ROOT_MODE)
        os.chmod(path, _ROOT_MODE)
    except FileExistsError:
        _require_direct_directory(path, label, mode=_ROOT_MODE)
        return
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12C {label} cannot be created") from exc
    _fsync_directory(parent)
    _require_direct_directory(path, label, mode=_ROOT_MODE)


def _open_or_create_layout(project: Path) -> _Layout:
    data = project / "data"
    _require_direct_directory(data, "project data directory")
    first = data / ".stage12"
    second = first / "market-v1"
    root = second / "stage12c-20260813-20260814"
    for path, label in (
        (first, "Stage 12C private parent"),
        (second, "Stage 12C private market parent"),
        (root, "Stage 12C private root"),
    ):
        _ensure_private_directory(path, label)
    directories: dict[str, Path] = {}
    for name in ("intents", "bodies", "responses", "results", "resume"):
        child = root / name
        _ensure_private_directory(child, f"Stage 12C private {name} directory")
        directories[name] = child
    return _Layout(
        root=root,
        intents=directories["intents"],
        bodies=directories["bodies"],
        responses=directories["responses"],
        results=directories["results"],
        resume=directories["resume"],
        baseline=root / "baseline.json",
        plan=root / "plan.json",
        completion=root / "completion.json",
        run_lock=root / _RUN_LOCK_FILENAME,
    )


@contextmanager
def _held_run_lock(layout: _Layout):
    descriptor: int | None = None
    created = False
    try:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(no_follow, int) or no_follow == 0:
            raise StoreUnavailableError("Stage 12C run lock cannot reject symlink aliases")
        try:
            before = layout.run_lock.lstat()
        except FileNotFoundError:
            before = None
        if before is not None and (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
        ):
            raise ConflictError("Stage 12C manual run lock has an unsafe physical identity")
        flags = os.O_RDWR | no_follow
        if before is None:
            try:
                descriptor = os.open(
                    layout.run_lock,
                    flags | os.O_CREAT | os.O_EXCL,
                    _FILE_MODE,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(layout.run_lock, flags)
        else:
            descriptor = os.open(layout.run_lock, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
            raise ConflictError("Stage 12C manual run lock has an unsafe physical identity")
        os.fchmod(descriptor, _FILE_MODE)
        if created:
            os.fsync(descriptor)
            _fsync_directory(layout.root)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise ConflictError("Stage 12C manual run is already active") from exc
    except (ConflictError, StoreUnavailableError):
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConflictError("Stage 12C manual run lock cannot be a symlink") from exc
        raise StoreUnavailableError("Stage 12C manual run lock is unavailable") from exc
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _file_sha256(path: Path, label: str) -> _SourceFingerprint:
    identity = _require_direct_regular_file(path, label)
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        after = path.stat()
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12C {label} cannot be fingerprinted") from exc
    after_identity = (int(after.st_dev), int(after.st_ino), int(after.st_nlink))
    if (
        identity != after_identity
        or int(before.st_size) != int(after.st_size)
        or int(before.st_mtime_ns) != int(after.st_mtime_ns)
        or after_identity[2] != 1
    ):
        raise ConflictError(f"Stage 12C {label} changed while being fingerprinted")
    return _SourceFingerprint(
        sha256=digest.hexdigest(),
        byte_count=int(after.st_size),
        identity=after_identity,
        mtime_ns=int(after.st_mtime_ns),
    )


def _readonly_connection(path: Path) -> sqlite3.Connection:
    try:
        uri = "file:" + quote(path.as_posix(), safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        return connection
    except sqlite3.Error as exc:
        raise StoreUnavailableError("Stage 12C market store is unavailable") from exc


def _scalar(connection: sqlite3.Connection, query: str, parameters: tuple[object, ...] = ()) -> int:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise ValidationError("Stage 12C store check returned no result")
    value = row[0]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("Stage 12C store check is invalid")
    return value


def _targeted_store_checks(
    path: Path,
    *,
    expected: Stage12CBaselineExpectation | None,
    session_dates: tuple[str, str],
) -> dict[str, object]:
    """Read only enough SQLite state to establish the reviewed 0010 invariants."""

    _require_direct_regular_file(path, "market store")
    connection = _readonly_connection(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not _REQUIRED_TABLES.issubset(tables):
            raise ValidationError("Stage 12C market store lacks the reviewed 0010 relations")
        datasets = {
            str(row[0])
            for row in connection.execute(
                "SELECT dataset_id FROM dataset_registry WHERE dataset_id IN (?, ?)",
                ("market.stage10.source_evidence", "market.stage10.daily_prices"),
            )
        }
        if datasets != {"market.stage10.source_evidence", "market.stage10.daily_prices"}:
            raise ValidationError("Stage 12C market store lacks required dataset registrations")
        integrity = tuple(str(row[0]) for row in connection.execute("PRAGMA integrity_check"))
        if integrity != ("ok",):
            raise ValidationError("Stage 12C market store integrity check failed")
        foreign_key_violations = _scalar(
            connection, "SELECT count(*) FROM pragma_foreign_key_check"
        )
        if foreign_key_violations:
            raise ValidationError("Stage 12C market store has foreign-key violations")
        current_rows = _scalar(connection, "SELECT count(*) FROM stage10_daily_prices")
        versions = _scalar(connection, "SELECT count(*) FROM stage10_daily_price_versions")
        captures = _scalar(connection, "SELECT count(*) FROM stage10_daily_price_captures")
        duplicate_current = _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT instrument_id, trade_date, provider, price_variant, currency_segment
                FROM stage10_daily_prices
                GROUP BY instrument_id, trade_date, provider, price_variant, currency_segment
                HAVING count(*) > 1
            )
            """,
        )
        duplicate_versions = _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT instrument_id, trade_date, provider, price_variant,
                       currency_segment, correction_sequence
                FROM stage10_daily_price_versions
                GROUP BY instrument_id, trade_date, provider, price_variant,
                         currency_segment, correction_sequence
                HAVING count(*) > 1
            )
            """,
        )
        duplicate_captures = _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT instrument_id, response_sha256
                FROM stage10_daily_price_captures
                GROUP BY instrument_id, response_sha256
                HAVING count(*) > 1
            )
            """,
        )
        pointer_anomalies = _scalar(
            connection,
            """
            SELECT count(*)
            FROM stage10_daily_prices AS current
            LEFT JOIN stage10_daily_price_versions AS version
              ON version.version_id=current.current_version_id
            WHERE version.version_id IS NULL
               OR version.instrument_id != current.instrument_id
               OR version.trade_date != current.trade_date
               OR version.provider != current.provider
               OR version.price_variant != current.price_variant
               OR version.currency_segment != current.currency_segment
               OR EXISTS (
                   SELECT 1
                   FROM stage10_daily_price_versions AS later
                   WHERE later.instrument_id=current.instrument_id
                     AND later.trade_date=current.trade_date
                     AND later.provider=current.provider
                     AND later.price_variant=current.price_variant
                     AND later.currency_segment=current.currency_segment
                     AND later.correction_sequence > version.correction_sequence
               )
            """,
        )
        scoped_rows = _scalar(
            connection,
            "SELECT count(*) FROM stage10_daily_prices WHERE trade_date IN (?, ?)",
            session_dates,
        )
        if duplicate_current or duplicate_versions or duplicate_captures or pointer_anomalies:
            raise ValidationError("Stage 12C market store has duplicate or current-pointer anomalies")
        if expected is not None and (
            current_rows != expected.current_price_rows
            or versions != expected.immutable_price_versions
            or captures != expected.daily_price_captures
            or scoped_rows != 0
        ):
            raise ValidationError("Stage 12C market store does not match the retained baseline counts")
        return {
            "current_price_rows": current_rows,
            "daily_price_captures": captures,
            "foreign_key_violation_count": foreign_key_violations,
            "immutable_price_versions": versions,
            "integrity_check": "ok",
            "scoped_current_row_count": scoped_rows,
            "stage10_capture_duplicate_count": duplicate_captures,
            "stage10_current_duplicate_count": duplicate_current,
            "stage10_current_pointer_anomaly_count": pointer_anomalies,
            "stage10_version_duplicate_count": duplicate_versions,
        }
    finally:
        connection.close()


def _validate_row_shape(value: object, unit: _Unit, index: int) -> str:
    if not isinstance(value, Mapping) or set(value) != _ROW_KEYS:
        raise ValidationError("Stage 12C provider response has an invalid daily-price row")
    symbol = value["symbol"]
    if not isinstance(symbol, str) or symbol != unit.symbol:
        raise ValidationError("Stage 12C provider response symbol is invalid")
    trade_date = _date_text(value["date"], "provider response date")
    if trade_date not in unit.session_dates:
        raise ValidationError("Stage 12C provider response date is outside the reviewed sessions")
    open_value = _number(value["open"], "provider response open", nonnegative=True)
    high_value = _number(value["high"], "provider response high", nonnegative=True)
    low_value = _number(value["low"], "provider response low", nonnegative=True)
    close_value = _number(value["close"], "provider response close", nonnegative=True)
    if high_value < max(open_value, low_value, close_value) or low_value > min(open_value, high_value, close_value):
        raise ValidationError("Stage 12C provider response OHLC values are inconsistent")
    _volume(value["volume"], "provider response volume")
    _number(value["change"], "provider response change")
    _number(value["changePercent"], "provider response change percent")
    _number(value["vwap"], "provider response vwap", nonnegative=True)
    del index
    return trade_date


def _validate_terminal_error_envelope(body: bytes) -> None:
    try:
        value = loads_strict(body, max_bytes=STAGE12C_MAX_RESPONSE_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ValidationError("Stage 12C terminal provider error envelope is invalid") from exc
    if (
        not isinstance(value, Mapping)
        or set(value) != {"Error Message"}
        or not isinstance(value["Error Message"], str)
        or not value["Error Message"].strip()
        or len(value["Error Message"]) > _MAX_ERROR_TEXT
        or any(ord(character) < 32 or ord(character) == 127 for character in value["Error Message"])
    ):
        raise ValidationError("Stage 12C terminal provider error envelope is invalid")


def _build_plan(
    scope: Stage12CMarketGapV1Scope,
    stage12a_scope: Stage12MarketV1Scope,
    stage12b_scope: Stage12BIncrementalMarketScope,
) -> _Plan:
    """Bind every requested unit to the reviewed scope and frozen roster."""

    try:
        request_plan = scope.request_plan
        from_date = request_plan.from_date
        to_date = request_plan.to_date
        sessions = tuple(request_plan.session_dates)
        sentinel = request_plan.sentinel_symbol
        symbols = tuple(stage12c_ordered_symbols(scope, stage12a_scope, stage12b_scope))
        scope_sha = scope.manifest_sha256
        roster_sha = stage12a_scope.roster_sha256
        stage12b_sha = stage12b_scope.manifest_sha256
    except AttributeError as exc:
        raise ValidationError("Stage 12C scope binding is invalid") from exc
    if (
        from_date != STAGE12C_EXPECTED_SESSIONS[0]
        or to_date != STAGE12C_EXPECTED_SESSIONS[1]
        or sessions != STAGE12C_EXPECTED_SESSIONS
        or sentinel != "AAPL"
        or len(symbols) != STAGE12C_EXPECTED_SYMBOL_COUNT
        or symbols[0] != sentinel
        or symbols[1:] != tuple(sorted(symbols[1:]))
        or len(set(symbols)) != len(symbols)
        or any(
            not isinstance(symbol, str)
            or not symbol
            or symbol != symbol.upper()
            or len(symbol) > 32
            or any(character.isspace() for character in symbol)
            for symbol in symbols
        )
    ):
        raise ValidationError("Stage 12C scope does not match the reviewed two-session plan")
    for digest, label in (
        (scope_sha, "scope digest"),
        (roster_sha, "Stage 12A roster digest"),
        (stage12b_sha, "Stage 12B scope digest"),
    ):
        _require_sha256(digest, label)
    material = {
        "contract": _PLAN_CONTRACT,
        "endpoint_path": STAGE12C_ENDPOINT_PATH,
        "from": from_date,
        "scope_manifest_sha256": scope_sha,
        "session_dates": list(sessions),
        "stage12a_roster_sha256": roster_sha,
        "stage12b_scope_sha256": stage12b_sha,
        "symbols": list(symbols),
        "to": to_date,
        "version": _VERSION,
    }
    plan_sha = _sha256_json(material)
    units: list[_Unit] = []
    for ordinal, symbol in enumerate(symbols, start=1):
        identifier = "stage12c-" + _sha256_json(
            {
                "ordinal": ordinal,
                "plan_sha256": plan_sha,
                "symbol": symbol,
            }
        )[:32]
        units.append(
            _Unit(
                ordinal=ordinal,
                symbol=symbol,
                from_date=from_date,
                to_date=to_date,
                session_dates=(sessions[0], sessions[1]),
                plan_sha256=plan_sha,
                scope_manifest_sha256=scope_sha,
                stage12a_roster_sha256=roster_sha,
                stage12b_scope_sha256=stage12b_sha,
                identifier=identifier,
            )
        )
    return _Plan(units=tuple(units), sha256=plan_sha)


def _plan_material(plan: _Plan) -> dict[str, object]:
    return {
        "contract": _PLAN_CONTRACT,
        "plan_sha256": plan.sha256,
        "units": [unit.material() for unit in plan.units],
        "version": _VERSION,
    }


def _ensure_plan_receipt(layout: _Layout, plan: _Plan) -> None:
    material = _plan_material(plan)
    receipt = {**material, "sha256": _sha256_json(material)}
    if layout.plan.exists() or layout.plan.is_symlink():
        value = _read_private_json(layout.plan, "plan sidecar", maximum=_MAX_RECEIPT_JSON_BYTES)
        if value != receipt:
            raise ConflictError("Stage 12C existing plan sidecar differs from the reviewed plan")
        return
    _write_exclusive_json(layout.plan, receipt, maximum=_MAX_RECEIPT_JSON_BYTES)


def _intent_material(unit: _Unit, issued_at: str) -> dict[str, object]:
    return {
        **unit.material(),
        "issued_at": issued_at,
    }


def _write_intent(layout: _Layout, unit: _Unit, *, issued_at: str) -> tuple[Mapping[str, object], str]:
    material = _intent_material(unit, issued_at)
    digest = _sha256_json(material)
    receipt = {
        "contract": _INTENT_CONTRACT,
        "intent_sha256": digest,
        **material,
        "version": _VERSION,
    }
    _write_exclusive_json(
        _private_file(layout, "intents", unit, ".json"),
        receipt,
        maximum=_MAX_SIDECAR_JSON_BYTES,
    )
    return receipt, digest


def _read_intent(layout: _Layout, unit: _Unit) -> tuple[Mapping[str, object], str] | None:
    path = _private_file(layout, "intents", unit, ".json")
    if not path.exists() and not path.is_symlink():
        return None
    value = _read_private_json(path, "intent sidecar", maximum=_MAX_SIDECAR_JSON_BYTES)
    fields = set(unit.material()) | {"issued_at"}
    if (
        set(value) != fields | {"contract", "intent_sha256", "version"}
        or value.get("contract") != _INTENT_CONTRACT
        or value.get("version") != _VERSION
    ):
        raise ConflictError("Stage 12C intent sidecar is invalid")
    for key, expected in unit.material().items():
        if value.get(key) != expected:
            raise ConflictError("Stage 12C intent sidecar is outside the reviewed plan")
    _parse_utc_text(value.get("issued_at"), "intent issued timestamp")
    material = {key: value[key] for key in fields}
    digest = _require_sha256(value.get("intent_sha256"), "intent digest")
    if digest != _sha256_json(material):
        raise ConflictError("Stage 12C intent sidecar digest is invalid")
    return value, digest


def _response_metadata_material(
    unit: _Unit,
    *,
    intent_sha256: str,
    response: Stage12CTransportResponse,
    captured_at: str,
) -> dict[str, object]:
    elapsed = Decimal(str(response.elapsed_seconds))
    return {
        "body_byte_count": len(response.body),
        "captured_at": captured_at,
        "content_type": response.media_type,
        "elapsed_seconds": format(elapsed, "f"),
        "http_status": response.status,
        "intent_id": unit.identifier,
        "intent_sha256": intent_sha256,
        "redirected": response.redirected,
        "response_sha256": _sha256_bytes(response.body),
    }


def _write_response_spool(
    layout: _Layout,
    unit: _Unit,
    *,
    intent_sha256: str,
    response: Stage12CTransportResponse,
    captured_at: str,
) -> Mapping[str, object]:
    """Persist bytes first, then the descriptor that binds them to the intent."""

    _write_exclusive_bytes(
        _private_file(layout, "bodies", unit, ".body"),
        response.body,
        maximum=STAGE12C_MAX_RESPONSE_BYTES,
    )
    material = _response_metadata_material(
        unit,
        intent_sha256=intent_sha256,
        response=response,
        captured_at=captured_at,
    )
    receipt = {
        "contract": _SPOOL_CONTRACT,
        "spool_sha256": _sha256_json(material),
        **material,
        "version": _VERSION,
    }
    _write_exclusive_json(
        _private_file(layout, "responses", unit, ".json"),
        receipt,
        maximum=_MAX_SIDECAR_JSON_BYTES,
    )
    return receipt


def _read_response_spool(
    layout: _Layout,
    unit: _Unit,
    *,
    intent_sha256: str,
) -> tuple[Mapping[str, object], bytes] | None:
    body_path = _private_file(layout, "bodies", unit, ".body")
    response_path = _private_file(layout, "responses", unit, ".json")
    body_exists = body_path.exists() or body_path.is_symlink()
    response_exists = response_path.exists() or response_path.is_symlink()
    if not body_exists and not response_exists:
        return None
    if not body_exists or not response_exists:
        raise ConflictError("Stage 12C has an incomplete durable response spool")
    body = _require_private_file(body_path, "raw response spool", maximum=STAGE12C_MAX_RESPONSE_BYTES)
    value = _read_private_json(response_path, "response spool metadata", maximum=_MAX_SIDECAR_JSON_BYTES)
    fields = {
        "body_byte_count",
        "captured_at",
        "content_type",
        "elapsed_seconds",
        "http_status",
        "intent_id",
        "intent_sha256",
        "redirected",
        "response_sha256",
    }
    if (
        set(value) != fields | {"contract", "spool_sha256", "version"}
        or value.get("contract") != _SPOOL_CONTRACT
        or value.get("version") != _VERSION
        or value.get("intent_id") != unit.identifier
        or value.get("intent_sha256") != intent_sha256
        or isinstance(value.get("http_status"), bool)
        or not isinstance(value.get("http_status"), int)
        or not 100 <= value["http_status"] <= 599
        or not isinstance(value.get("content_type"), str)
        or not value["content_type"]
        or len(value["content_type"]) > 256
        or not isinstance(value.get("redirected"), bool)
        or not isinstance(value.get("body_byte_count"), int)
        or value["body_byte_count"] != len(body)
        or value.get("response_sha256") != _sha256_bytes(body)
    ):
        raise ConflictError("Stage 12C durable response spool is invalid")
    _parse_utc_text(value.get("captured_at"), "response captured timestamp")
    try:
        elapsed = Decimal(str(value.get("elapsed_seconds")))
    except Exception as exc:
        raise ConflictError("Stage 12C durable response spool elapsed time is invalid") from exc
    if not elapsed.is_finite() or elapsed < 0:
        raise ConflictError("Stage 12C durable response spool elapsed time is invalid")
    material = {key: value[key] for key in fields}
    if value.get("spool_sha256") != _sha256_json(material):
        raise ConflictError("Stage 12C durable response spool digest is invalid")
    return value, body


def _publication_mapping(receipt: Stage12CPublicationReceipt) -> dict[str, object]:
    if not isinstance(receipt, Stage12CPublicationReceipt):
        raise ValidationError("Stage 12C collector returned an invalid publication receipt")
    if receipt.outcome not in {"published", "unchanged"}:
        raise ValidationError("Stage 12C collector returned an invalid publication outcome")
    if receipt.semantic_identity is None:
        raise ValidationError("Stage 12C collector returned an incomplete publication receipt")
    if (
        not isinstance(receipt.semantic_identity, str)
        or (receipt.capture_id is not None and not isinstance(receipt.capture_id, str))
        or isinstance(receipt.written_versions, bool)
        or not isinstance(receipt.written_versions, int)
        or receipt.written_versions < 0
    ):
        raise ValidationError("Stage 12C collector returned an invalid publication receipt")
    if (receipt.outcome == "published") != (receipt.capture_id is not None):
        raise ValidationError("Stage 12C collector returned an incomplete publication receipt")
    return {
        "capture_id": receipt.capture_id,
        "outcome": receipt.outcome,
        "semantic_identity": receipt.semantic_identity,
        "written_versions": receipt.written_versions,
    }


def _result_material(
    unit: _Unit,
    *,
    intent_sha256: str,
    spool: Mapping[str, object],
    outcome: str,
    row_count: int,
    publication: Mapping[str, object] | None,
) -> dict[str, object]:
    if outcome not in {
        "published_complete",
        "noncoverage_empty",
        "noncoverage_partial",
        "noncoverage_http_404",
        "noncoverage_http_410",
        "noncoverage_http_422",
        "noncoverage_http_402_authorized",
    }:
        raise ValidationError("Stage 12C result outcome is invalid")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0 or row_count > 2:
        raise ValidationError("Stage 12C result row count is invalid")
    if (outcome == "published_complete") != (publication is not None):
        raise ValidationError("Stage 12C result publication binding is invalid")
    return {
        "captured_at": spool["captured_at"],
        "content_type": spool["content_type"],
        "http_status": spool["http_status"],
        "intent_id": unit.identifier,
        "intent_sha256": intent_sha256,
        "outcome": outcome,
        "publication": None if publication is None else dict(publication),
        "response_byte_count": spool["body_byte_count"],
        "response_sha256": spool["response_sha256"],
        "row_count": row_count,
        "spool_sha256": spool["spool_sha256"],
    }


def _write_result(
    layout: _Layout,
    unit: _Unit,
    material: Mapping[str, object],
) -> Mapping[str, object]:
    receipt = {
        "contract": _RESULT_CONTRACT,
        "result_sha256": _sha256_json(dict(material)),
        **dict(material),
        "version": _VERSION,
    }
    _write_exclusive_json(
        _private_file(layout, "results", unit, ".json"),
        receipt,
        maximum=_MAX_SIDECAR_JSON_BYTES,
    )
    return receipt


def _read_result(
    layout: _Layout,
    unit: _Unit,
    *,
    intent_sha256: str,
    spool: Mapping[str, object],
) -> Mapping[str, object] | None:
    path = _private_file(layout, "results", unit, ".json")
    if not path.exists() and not path.is_symlink():
        return None
    value = _read_private_json(path, "result sidecar", maximum=_MAX_SIDECAR_JSON_BYTES)
    fields = {
        "captured_at",
        "content_type",
        "http_status",
        "intent_id",
        "intent_sha256",
        "outcome",
        "publication",
        "response_byte_count",
        "response_sha256",
        "row_count",
        "spool_sha256",
    }
    if (
        set(value) != fields | {"contract", "result_sha256", "version"}
        or value.get("contract") != _RESULT_CONTRACT
        or value.get("version") != _VERSION
    ):
        raise ConflictError("Stage 12C result sidecar is invalid")
    material = {key: value[key] for key in fields}
    expected = _result_material(
        unit,
        intent_sha256=intent_sha256,
        spool=spool,
        outcome=material.get("outcome"),
        row_count=material.get("row_count"),
        publication=material.get("publication"),
    )
    if material != expected or value.get("result_sha256") != _sha256_json(expected):
        raise ConflictError("Stage 12C result sidecar binding is invalid")
    return value


def _resume_material(unit: _Unit, result: Mapping[str, object]) -> dict[str, object]:
    return {
        "intent_id": unit.identifier,
        "ordinal": unit.ordinal,
        "outcome": result["outcome"],
        "result_sha256": result["result_sha256"],
    }


def _write_resume(layout: _Layout, unit: _Unit, result: Mapping[str, object]) -> Mapping[str, object]:
    material = _resume_material(unit, result)
    receipt = {
        "contract": _RESUME_CONTRACT,
        "resume_sha256": _sha256_json(material),
        **material,
        "version": _VERSION,
    }
    path = _private_file(layout, "resume", unit, ".json")
    if path.exists() or path.is_symlink():
        existing = _read_private_json(path, "resume sidecar", maximum=_MAX_SIDECAR_JSON_BYTES)
        if existing != receipt:
            raise ConflictError("Stage 12C resume sidecar binding is invalid")
        return existing
    _write_exclusive_json(path, receipt, maximum=_MAX_SIDECAR_JSON_BYTES)
    return receipt


def _read_resume(
    layout: _Layout,
    unit: _Unit,
    result: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    path = _private_file(layout, "resume", unit, ".json")
    if not path.exists() and not path.is_symlink():
        return None
    if result is None:
        raise ConflictError("Stage 12C resume sidecar lacks a result sidecar")
    value = _read_private_json(path, "resume sidecar", maximum=_MAX_SIDECAR_JSON_BYTES)
    material = _resume_material(unit, result)
    if (
        set(value) != set(material) | {"contract", "resume_sha256", "version"}
        or value.get("contract") != _RESUME_CONTRACT
        or value.get("version") != _VERSION
        or any(value.get(key) != expected for key, expected in material.items())
        or value.get("resume_sha256") != _sha256_json(material)
    ):
        raise ConflictError("Stage 12C resume sidecar binding is invalid")
    return value


def _validate_journal_directory(
    directory: Path,
    *,
    expected_names: set[str],
    label: str,
    maximum: int,
) -> None:
    _require_direct_directory(directory, label, mode=_ROOT_MODE)
    try:
        entries = tuple(directory.iterdir())
    except OSError as exc:
        raise StoreUnavailableError(f"Stage 12C {label} is unavailable") from exc
    if len(entries) > STAGE12C_EXPECTED_SYMBOL_COUNT:
        raise ConflictError(f"Stage 12C {label} exceeds the reviewed unit bound")
    for entry in entries:
        if entry.name not in expected_names:
            raise ConflictError(f"Stage 12C {label} contains an unreviewed sidecar")
        _require_private_file(entry, label, maximum=maximum)


def _load_journal(layout: _Layout, plan: _Plan) -> dict[str, _JournalState]:
    json_names = {f"{unit.filename_stem}.json" for unit in plan.units}
    body_names = {f"{unit.filename_stem}.body" for unit in plan.units}
    _validate_journal_directory(layout.intents, expected_names=json_names, label="intent directory", maximum=_MAX_SIDECAR_JSON_BYTES)
    _validate_journal_directory(layout.bodies, expected_names=body_names, label="body directory", maximum=STAGE12C_MAX_RESPONSE_BYTES)
    _validate_journal_directory(layout.responses, expected_names=json_names, label="response directory", maximum=_MAX_SIDECAR_JSON_BYTES)
    _validate_journal_directory(layout.results, expected_names=json_names, label="result directory", maximum=_MAX_SIDECAR_JSON_BYTES)
    _validate_journal_directory(layout.resume, expected_names=json_names, label="resume directory", maximum=_MAX_SIDECAR_JSON_BYTES)
    states: dict[str, _JournalState] = {}
    for unit in plan.units:
        intent = _read_intent(layout, unit)
        intent_value: Mapping[str, object] | None
        intent_digest: str | None
        if intent is None:
            intent_value = None
            intent_digest = None
        else:
            intent_value, intent_digest = intent
        body_path = _private_file(layout, "bodies", unit, ".body")
        response_path = _private_file(layout, "responses", unit, ".json")
        result_path = _private_file(layout, "results", unit, ".json")
        any_after_intent = any(
            path.exists() or path.is_symlink()
            for path in (body_path, response_path, result_path, _private_file(layout, "resume", unit, ".json"))
        )
        if intent_value is None:
            if any_after_intent:
                raise ConflictError("Stage 12C sidecar exists without a durable intent")
            states[unit.identifier] = _JournalState(unit, None, None, None, None, None, None)
            continue
        spool = _read_response_spool(layout, unit, intent_sha256=intent_digest)
        if spool is None:
            raise ConflictError("Stage 12C has an ambiguous issued request; retry is forbidden")
        response, body = spool
        result = _read_result(layout, unit, intent_sha256=intent_digest, spool=response)
        resume = _read_resume(layout, unit, result)
        states[unit.identifier] = _JournalState(
            unit=unit,
            intent=intent_value,
            intent_sha256=intent_digest,
            body=body,
            response=response,
            result=result,
            resume=resume,
        )
    return states


@dataclass(frozen=True, slots=True)
class _Admission:
    outcome: str
    row_count: int


def _matches_stage12c_axjo_402_authorization(
    unit: _Unit,
    spool: Mapping[str, object],
    body: bytes,
) -> bool:
    """Match only the sealed user-authorized Stage 12C ``^AXJO`` response."""

    return (
        unit.plan_sha256 == _STAGE12C_AXJO_402_PLAN_SHA256
        and unit.ordinal == _STAGE12C_AXJO_402_ORDINAL
        and unit.symbol == _STAGE12C_AXJO_402_SYMBOL
        and unit.from_date == _STAGE12C_AXJO_402_FROM_DATE
        and unit.to_date == _STAGE12C_AXJO_402_TO_DATE
        and unit.identifier == _STAGE12C_AXJO_402_UNIT_IDENTIFIER
        and spool.get("intent_id") == _STAGE12C_AXJO_402_UNIT_IDENTIFIER
        and spool.get("intent_sha256") == _STAGE12C_AXJO_402_INTENT_SHA256
        and spool.get("http_status") == _STAGE12C_AXJO_402_HTTP_STATUS
        and spool.get("content_type") == _STAGE12C_AXJO_402_CONTENT_TYPE
        and spool.get("redirected") is False
        and spool.get("body_byte_count") == _STAGE12C_AXJO_402_RESPONSE_BYTE_COUNT
        and len(body) == _STAGE12C_AXJO_402_RESPONSE_BYTE_COUNT
        and spool.get("response_sha256") == _STAGE12C_AXJO_402_RESPONSE_SHA256
        and _sha256_bytes(body) == _STAGE12C_AXJO_402_RESPONSE_SHA256
        and spool.get("spool_sha256") == _STAGE12C_AXJO_402_SPOOL_SHA256
    )


def _matches_stage12c_sealed_402_continuation_authorization(
    unit: _Unit,
    spool: Mapping[str, object],
    body: bytes,
) -> bool:
    """Match only the separately authorized sealed response on ordinals 617–629."""

    expected_identifier = "stage12c-" + _sha256_json(
        {
            "ordinal": unit.ordinal,
            "plan_sha256": unit.plan_sha256,
            "symbol": unit.symbol,
        }
    )[:32]
    return (
        unit.plan_sha256 == _STAGE12C_AXJO_402_PLAN_SHA256
        and _STAGE12C_SEALED_402_CONTINUATION_FIRST_ORDINAL
        <= unit.ordinal
        <= _STAGE12C_SEALED_402_CONTINUATION_LAST_ORDINAL
        and unit.from_date == STAGE12C_EXPECTED_SESSIONS[0]
        and unit.to_date == STAGE12C_EXPECTED_SESSIONS[1]
        and unit.session_dates == STAGE12C_EXPECTED_SESSIONS
        and unit.identifier == expected_identifier
        and spool.get("intent_id") == unit.identifier
        and spool.get("http_status") == _STAGE12C_AXJO_402_HTTP_STATUS
        and spool.get("content_type") == _STAGE12C_AXJO_402_CONTENT_TYPE
        and spool.get("redirected") is False
        and spool.get("body_byte_count") == _STAGE12C_AXJO_402_RESPONSE_BYTE_COUNT
        and len(body) == _STAGE12C_AXJO_402_RESPONSE_BYTE_COUNT
        and spool.get("response_sha256") == _STAGE12C_AXJO_402_RESPONSE_SHA256
        and _sha256_bytes(body) == _STAGE12C_AXJO_402_RESPONSE_SHA256
    )


def _classify_spooled_response(
    unit: _Unit,
    spool: Mapping[str, object],
    body: bytes,
    *,
    max_seconds: int,
) -> _Admission:
    """Classify a durable response without opening SQLite or invoking transport."""

    try:
        elapsed = Decimal(str(spool["elapsed_seconds"]))
    except Exception as exc:
        raise ConflictError("Stage 12C durable response elapsed time is invalid") from exc
    if not elapsed.is_finite() or elapsed < 0 or elapsed > max_seconds:
        raise ResourceLimitError("Stage 12C provider response exceeds the reviewed elapsed-time bound")
    status = spool["http_status"]
    content_type = spool["content_type"]
    redirected = spool["redirected"]
    if not isinstance(status, int) or not isinstance(content_type, str) or not isinstance(redirected, bool):
        raise ConflictError("Stage 12C durable response descriptor is invalid")
    if redirected or 300 <= status <= 399:
        raise StoreUnavailableError("Stage 12C provider redirect is outside the reviewed scope")
    if (
        _matches_stage12c_axjo_402_authorization(unit, spool, body)
        or _matches_stage12c_sealed_402_continuation_authorization(unit, spool, body)
    ):
        return _Admission("noncoverage_http_402_authorized", 0)
    if status in {404, 410, 422}:
        _content_type(content_type, "terminal provider media type")
        _validate_terminal_error_envelope(body)
        return _Admission(f"noncoverage_http_{status}", 0)
    if status != 200:
        raise StoreUnavailableError("Stage 12C provider response is outside the reviewed status policy")
    _content_type(content_type, "provider media type")
    try:
        payload = loads_strict(body, max_bytes=STAGE12C_MAX_RESPONSE_BYTES)
    except (ResourceLimitError, ValidationError) as exc:
        raise ValidationError("Stage 12C provider response is malformed") from exc
    if not isinstance(payload, list):
        raise ValidationError("Stage 12C provider response must be a JSON list")
    if not payload:
        return _Admission("noncoverage_empty", 0)
    if len(payload) > 2:
        raise ValidationError("Stage 12C provider response exceeds the reviewed two-session row bound")
    dates = tuple(_validate_row_shape(row, unit, index) for index, row in enumerate(payload))
    if len(payload) == 1:
        return _Admission("noncoverage_partial", 1)
    if len(set(dates)) != len(dates) or set(dates) != set(unit.session_dates):
        raise ValidationError("Stage 12C provider response has duplicate or missing reviewed sessions")
    return _Admission("published_complete", 2)


def _replay_spooled_unit(
    *,
    layout: _Layout,
    state: _JournalState,
    collector: Stage12CIncrementalCollector,
    max_seconds: int,
    target_identity: tuple[int, int, int],
) -> _JournalState:
    """Complete a durable spool locally; this function never calls a provider."""

    if (
        state.intent is None
        or state.intent_sha256 is None
        or state.response is None
        or state.body is None
    ):
        raise ConflictError("Stage 12C durable spool replay is incomplete")
    if state.result is not None:
        resume = _write_resume(layout, state.unit, state.result)
        return _JournalState(
            unit=state.unit,
            intent=state.intent,
            intent_sha256=state.intent_sha256,
            body=state.body,
            response=state.response,
            result=state.result,
            resume=resume,
        )
    admission = _classify_spooled_response(
        state.unit,
        state.response,
        state.body,
        max_seconds=max_seconds,
    )
    if state.unit.ordinal == 1 and admission.outcome != "published_complete":
        raise StoreUnavailableError("Stage 12C AAPL sentinel did not return both reviewed sessions")
    publication: Mapping[str, object] | None = None
    if admission.outcome == "published_complete":
        if _require_direct_regular_file(collector.market_store, "collector market store") != target_identity:
            raise ConflictError("Stage 12C target store physical identity changed before publication")
        request = Stage12CLiveRequest(
            symbol=state.unit.symbol,
            from_date=state.unit.from_date,
            to_date=state.unit.to_date,
        )
        response = Stage12CLiveResponse(
            status=int(state.response["http_status"]),
            media_type=str(state.response["content_type"]),
            body=state.body,
            captured_at=str(state.response["captured_at"]),
            elapsed_seconds=Decimal(str(state.response["elapsed_seconds"])),
        )
        prepared: PreparedStage12CPublication = collector.prepare(request, response)
        publication = _publication_mapping(collector.publish(prepared))
    material = _result_material(
        state.unit,
        intent_sha256=state.intent_sha256,
        spool=state.response,
        outcome=admission.outcome,
        row_count=admission.row_count,
        publication=publication,
    )
    result = _write_result(layout, state.unit, material)
    resume = _write_resume(layout, state.unit, result)
    return _JournalState(
        unit=state.unit,
        intent=state.intent,
        intent_sha256=state.intent_sha256,
        body=state.body,
        response=state.response,
        result=result,
        resume=resume,
    )


def _baseline_material(
    *,
    plan: _Plan,
    expectation: Stage12CBaselineExpectation,
    source: _SourceFingerprint,
    target: _SourceFingerprint,
    source_checks: Mapping[str, object],
    target_checks: Mapping[str, object],
) -> dict[str, object]:
    return {
        "baseline_sha256": expectation.sha256,
        "plan_sha256": plan.sha256,
        "source": source.public_mapping(),
        "source_checks": dict(source_checks),
        "target": target.public_mapping(),
        "target_checks": dict(target_checks),
    }


def _read_baseline(
    layout: _Layout,
    plan: _Plan,
    expectation: Stage12CBaselineExpectation,
) -> Mapping[str, object] | None:
    if not layout.baseline.exists() and not layout.baseline.is_symlink():
        return None
    value = _read_private_json(layout.baseline, "baseline sidecar", maximum=_MAX_RECEIPT_JSON_BYTES)
    fields = {
        "baseline_sha256",
        "plan_sha256",
        "source",
        "source_checks",
        "target",
        "target_checks",
    }
    if (
        set(value) != fields | {"contract", "sha256", "version"}
        or value.get("contract") != _BASELINE_CONTRACT
        or value.get("version") != _VERSION
        or value.get("baseline_sha256") != expectation.sha256
        or value.get("plan_sha256") != plan.sha256
    ):
        raise ConflictError("Stage 12C baseline sidecar is invalid")
    material = {key: value[key] for key in fields}
    if value.get("sha256") != _sha256_json(material):
        raise ConflictError("Stage 12C baseline sidecar digest is invalid")
    return value


def _source_mapping_matches(value: object, fingerprint: _SourceFingerprint) -> bool:
    return isinstance(value, Mapping) and dict(value) == fingerprint.public_mapping()


def _ensure_baseline(
    *,
    layout: _Layout,
    plan: _Plan,
    target: Path,
    source: Path,
    expectation: Stage12CBaselineExpectation,
) -> tuple[Mapping[str, object], tuple[int, int, int]]:
    """Prove the retained baseline once, then require source neutrality on resume."""

    source_identity = _require_direct_regular_file(source, "retained source")
    target_identity = _require_direct_regular_file(target, "project market target")
    if source_identity == target_identity:
        raise ConflictError("Stage 12C retained source and project target must be distinct physical files")
    baseline = _read_baseline(layout, plan, expectation)
    if baseline is None:
        source_fingerprint = _file_sha256(source, "retained source")
        target_fingerprint = _file_sha256(target, "project market target")
        if (
            source_fingerprint.sha256 != expectation.sha256
            or target_fingerprint.sha256 != expectation.sha256
            or source_fingerprint.sha256 != target_fingerprint.sha256
        ):
            raise ConflictError("Stage 12C source and target do not match the approved retained baseline")
        source_checks = _targeted_store_checks(
            source,
            expected=expectation,
            session_dates=STAGE12C_EXPECTED_SESSIONS,
        )
        target_checks = _targeted_store_checks(
            target,
            expected=expectation,
            session_dates=STAGE12C_EXPECTED_SESSIONS,
        )
        material = _baseline_material(
            plan=plan,
            expectation=expectation,
            source=source_fingerprint,
            target=target_fingerprint,
            source_checks=source_checks,
            target_checks=target_checks,
        )
        baseline = {
            "contract": _BASELINE_CONTRACT,
            "sha256": _sha256_json(material),
            **material,
            "version": _VERSION,
        }
        _write_exclusive_json(layout.baseline, baseline, maximum=_MAX_RECEIPT_JSON_BYTES)
        return baseline, target_identity
    source_fingerprint = _file_sha256(source, "retained source")
    target_mapping = baseline.get("target")
    if not isinstance(target_mapping, Mapping) or target_mapping.get("identity_sha256") != _identity_sha256(
        target_identity
    ):
        raise ConflictError("Stage 12C project target changed after baseline preflight")
    if not _source_mapping_matches(baseline.get("source"), source_fingerprint):
        raise ConflictError("Stage 12C retained source changed after baseline preflight")
    source_checks = _targeted_store_checks(
        source,
        expected=expectation,
        session_dates=STAGE12C_EXPECTED_SESSIONS,
    )
    if baseline.get("source_checks") != source_checks:
        raise ConflictError("Stage 12C retained source checks drifted after baseline preflight")
    _targeted_store_checks(target, expected=None, session_dates=STAGE12C_EXPECTED_SESSIONS)
    return baseline, target_identity


class _RequestPacer:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_monotonic: float | None = None

    def arm_resume_delay(self) -> None:
        """Conservatively pace the first live attempt after durable recovery."""

        current = float(self._monotonic())
        if not math.isfinite(current):
            raise ValidationError("Stage 12C monotonic clock is invalid")
        self._last_monotonic = current

    def before_attempt(self) -> None:
        """Record the injected monotonic provider-start boundary after intent fsync."""

        current = float(self._monotonic())
        if not math.isfinite(current):
            raise ValidationError("Stage 12C monotonic clock is invalid")
        if self._last_monotonic is not None:
            remaining = STAGE12C_MINIMUM_REQUEST_INTERVAL_SECONDS - (
                current - self._last_monotonic
            )
            if remaining > 0:
                self._sleeper(remaining)
                current = float(self._monotonic())
                if not math.isfinite(current):
                    raise ValidationError("Stage 12C monotonic clock is invalid")
                if current - self._last_monotonic < STAGE12C_MINIMUM_REQUEST_INTERVAL_SECONDS:
                    raise ValidationError("Stage 12C monotonic pacing clock did not advance")
        self._last_monotonic = current


def _closed_result_counts(states: Mapping[str, _JournalState]) -> tuple[int, int, tuple[Mapping[str, object], ...]]:
    published = 0
    terminal: list[Mapping[str, object]] = []
    for state in sorted(states.values(), key=lambda item: item.unit.ordinal):
        result = state.result
        if result is None:
            continue
        outcome = result["outcome"]
        if outcome == "published_complete":
            published += 1
        else:
            terminal.append(
                {
                    "http_status": result["http_status"],
                    "ordinal": state.unit.ordinal,
                    "outcome": outcome,
                    "symbol": state.unit.symbol,
                }
            )
    return len([state for state in states.values() if state.result is not None]), published, tuple(terminal)


class _Stage12CMarketGapRunner:
    """Capability-bound Stage 12C runner shared by fixture and live adapters."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        retained_source: str | Path,
        scope: Stage12CMarketGapV1Scope,
        stage12a_scope: Stage12MarketV1Scope,
        stage12b_scope: Stage12BIncrementalMarketScope,
        collector: Stage12CIncrementalCollector,
        transport: Stage12CTransport,
        environment: Mapping[str, str],
        static_preflight: Callable[[str | Path], Mapping[str, object]],
        _construction_capability: object,
        baseline: Stage12CBaselineExpectation = RETAINED_STAGE10_BASELINE,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if _construction_capability is _FIXTURE_RUNNER_CAPABILITY:
            self._project = _require_fixture_project_root(project_root)
            self._target, target_identity = _require_fixture_target(self._project)
            self._source = _require_fixture_retained_source(retained_source, self._project)
            source_identity = _require_direct_regular_file(
                self._source,
                "fixture retained source",
            )
            if source_identity == target_identity:
                raise ConflictError(
                    "Stage 12C fixture retained source and project target must be distinct"
                )
        elif _construction_capability is _CANONICAL_LIVE_CAPABILITY:
            if project_root != APPROVED_PROJECT_ROOT or retained_source != APPROVED_RETAINED_SOURCE:
                raise ValidationError("Stage 12C canonical runner roots are fixed")
            self._project, self._target, self._source = _require_canonical_live_roots()
        else:
            raise ValidationError("Stage 12C runner construction capability is invalid")
        if not isinstance(scope, Stage12CMarketGapV1Scope):
            raise ValidationError("Stage 12C scope is invalid")
        if not isinstance(stage12a_scope, Stage12MarketV1Scope):
            raise ValidationError("Stage 12A scope binding is invalid")
        if not isinstance(stage12b_scope, Stage12BIncrementalMarketScope):
            raise ValidationError("Stage 12B scope binding is invalid")
        if not isinstance(collector, Stage12CIncrementalCollector):
            raise ValidationError("Stage 12C collector is invalid")
        if not isinstance(transport, Stage12CTransport):
            raise ValidationError("Stage 12C transport does not implement the reviewed interface")
        if not isinstance(environment, Mapping):
            raise ValidationError("Stage 12C process environment binding is invalid")
        if not callable(static_preflight) or not callable(monotonic) or not callable(sleeper) or not callable(utcnow):
            raise ValidationError("Stage 12C runner binding is invalid")
        if not isinstance(baseline, Stage12CBaselineExpectation):
            raise ValidationError("Stage 12C baseline expectation is invalid")
        if collector.market_store != self._target:
            raise ValidationError("Stage 12C collector is not bound to the fixed project-local target")
        self._scope = scope
        self._stage12a_scope = stage12a_scope
        self._stage12b_scope = stage12b_scope
        self._collector = collector
        self._transport = transport
        self._environment = environment
        self._static_preflight = static_preflight
        self._baseline = baseline
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._utcnow = utcnow

    @property
    def market_store(self) -> Path:
        return self._target

    def _now_text(self, label: str) -> str:
        return _utc_text(self._utcnow(), label)

    def _validate_static_preflight(self) -> Mapping[str, object]:
        value = self._static_preflight(self._project)
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            raise ValidationError("Stage 12C static preflight is invalid")
        try:
            dumps_strict(dict(value), max_bytes=_MAX_RECEIPT_JSON_BYTES)
        except (ResourceLimitError, ValidationError) as exc:
            raise ValidationError("Stage 12C static preflight is invalid") from exc
        return value

    def _read_credential(self) -> str:
        return _validated_fmp_api_key(
            read_project_credential(
                project_root=self._project,
                name="FMP_API_KEY",
                environment=self._environment,
            )
        )

    def _recover_spools(
        self,
        *,
        layout: _Layout,
        plan: _Plan,
        states: dict[str, _JournalState],
        target_identity: tuple[int, int, int],
    ) -> dict[str, _JournalState]:
        issued_ordinals = [state.unit.ordinal for state in states.values() if state.intent is not None]
        if issued_ordinals and sorted(issued_ordinals) != list(range(1, len(issued_ordinals) + 1)):
            raise ConflictError("Stage 12C issued intents are not a contiguous reviewed prefix")
        for unit in plan.units:
            state = states[unit.identifier]
            if state.intent is None:
                continue
            recovered = _replay_spooled_unit(
                layout=layout,
                state=state,
                collector=self._collector,
                max_seconds=self._scope.collector.max_seconds,
                target_identity=target_identity,
            )
            states[unit.identifier] = recovered
            if unit.ordinal == 1 and recovered.result is not None and recovered.result["outcome"] != "published_complete":
                raise StoreUnavailableError("Stage 12C AAPL sentinel did not publish both reviewed sessions")
        return states

    def _issue_one(
        self,
        *,
        layout: _Layout,
        unit: _Unit,
        target_identity: tuple[int, int, int],
        pacer: _RequestPacer,
        api_key: str,
    ) -> _JournalState:
        issued_at = self._now_text("intent issued timestamp")
        intent, intent_sha256 = _write_intent(layout, unit, issued_at=issued_at)
        pacer.before_attempt()
        response = self._transport.get(
            path=STAGE12C_ENDPOINT_PATH,
            query={"symbol": unit.symbol, "from": unit.from_date, "to": unit.to_date},
            headers={"apikey": api_key},
            timeout_seconds=STAGE12C_TIMEOUT_SECONDS,
            max_bytes=STAGE12C_MAX_RESPONSE_BYTES,
        )
        if not isinstance(response, Stage12CTransportResponse):
            raise ValidationError("Stage 12C transport returned an invalid response")
        if len(response.body) > STAGE12C_MAX_RESPONSE_BYTES:
            raise ResourceLimitError("Stage 12C provider response exceeds the reviewed byte bound")
        captured_at = self._now_text("response captured timestamp")
        spool = _write_response_spool(
            layout,
            unit,
            intent_sha256=intent_sha256,
            response=response,
            captured_at=captured_at,
        )
        state = _JournalState(
            unit=unit,
            intent=intent,
            intent_sha256=intent_sha256,
            body=response.body,
            response=spool,
            result=None,
            resume=None,
        )
        return _replay_spooled_unit(
            layout=layout,
            state=state,
            collector=self._collector,
            max_seconds=self._scope.collector.max_seconds,
            target_identity=target_identity,
        )

    def _complete_under_target_lock(
        self,
        *,
        layout: _Layout,
        plan: _Plan,
        states: Mapping[str, _JournalState],
        baseline: Mapping[str, object],
        static_preflight: Mapping[str, object],
        target_identity: tuple[int, int, int],
    ) -> Mapping[str, object]:
        """Recheck the physical target while its approved store lock is held."""

        with StoreWriteLock(self._target):
            current_identity = _require_direct_regular_file(
                self._target,
                "project market target",
            )
            if current_identity != target_identity:
                raise ConflictError(
                    "Stage 12C project target changed before completion receipt"
                )
            receipt = self._completion_receipt(
                layout=layout,
                plan=plan,
                states=states,
                baseline=baseline,
                static_preflight=static_preflight,
                expected_target_identity=target_identity,
            )
            current_identity = _require_direct_regular_file(
                self._target,
                "project market target",
            )
            if current_identity != target_identity:
                raise ConflictError(
                    "Stage 12C project target changed before completion receipt"
                )
            if layout.completion.exists() or layout.completion.is_symlink():
                existing = _read_private_json(
                    layout.completion,
                    "completion receipt",
                    maximum=_MAX_RECEIPT_JSON_BYTES,
                )
                if existing != receipt:
                    raise ConflictError("Stage 12C completion receipt differs from durable run evidence")
                post_load_receipt = self._completion_receipt(
                    layout=layout,
                    plan=plan,
                    states=states,
                    baseline=baseline,
                    static_preflight=static_preflight,
                    expected_target_identity=target_identity,
                )
                if post_load_receipt != existing:
                    raise ConflictError("Stage 12C completion receipt no longer binds the final target")
                current_identity = _require_direct_regular_file(
                    self._target,
                    "project market target",
                )
                if current_identity != target_identity:
                    raise ConflictError(
                        "Stage 12C project target changed while loading completion receipt"
                    )
                return existing
            receipt_bytes = dumps_strict(dict(receipt), max_bytes=_MAX_RECEIPT_JSON_BYTES).encode("utf-8")
            _write_exclusive_json(layout.completion, receipt, maximum=_MAX_RECEIPT_JSON_BYTES)
            receipt_identity = _require_direct_regular_file(
                layout.completion,
                "completion receipt",
            )
            try:
                if _require_private_file(
                    layout.completion,
                    "completion receipt",
                    maximum=_MAX_RECEIPT_JSON_BYTES,
                ) != receipt_bytes:
                    raise ConflictError("Stage 12C newly written completion receipt differs from durable evidence")
                post_write_receipt = self._completion_receipt(
                    layout=layout,
                    plan=plan,
                    states=states,
                    baseline=baseline,
                    static_preflight=static_preflight,
                    expected_target_identity=target_identity,
                )
                if post_write_receipt != receipt:
                    raise ConflictError("Stage 12C completion receipt no longer binds the final target")
                current_identity = _require_direct_regular_file(
                    self._target,
                    "project market target",
                )
                if current_identity != target_identity:
                    raise ConflictError(
                        "Stage 12C project target changed after completion receipt write"
                    )
            except Exception:
                try:
                    if _require_direct_regular_file(
                        layout.completion,
                        "completion receipt",
                    ) != receipt_identity:
                        raise ConflictError("Stage 12C completion receipt changed before rollback")
                    if _require_private_file(
                        layout.completion,
                        "completion receipt",
                        maximum=_MAX_RECEIPT_JSON_BYTES,
                    ) != receipt_bytes:
                        raise ConflictError("Stage 12C completion receipt changed before rollback")
                    os.unlink(layout.completion)
                    _fsync_directory(layout.root)
                    if layout.completion.exists() or layout.completion.is_symlink():
                        raise ConflictError("Stage 12C completion receipt remains after rollback")
                except Exception as cleanup_error:
                    raise ConflictError("Stage 12C completion receipt could not be safely rolled back") from cleanup_error
                raise
            return receipt

    def _completion_receipt(
        self,
        *,
        layout: _Layout,
        plan: _Plan,
        states: Mapping[str, _JournalState],
        baseline: Mapping[str, object],
        static_preflight: Mapping[str, object],
        expected_target_identity: tuple[int, int, int],
    ) -> Mapping[str, object]:
        current_identity = _require_direct_regular_file(
            self._target,
            "project market target",
        )
        if current_identity != expected_target_identity:
            raise ConflictError(
                "Stage 12C project target changed before completion receipt"
            )
        closed, published, terminal = _closed_result_counts(states)
        if closed != len(plan.units):
            raise ConflictError("Stage 12C cannot complete before all reviewed units close")
        sentinel = states[plan.units[0].identifier].result
        if sentinel is None or sentinel.get("outcome") != "published_complete":
            raise ConflictError("Stage 12C cannot complete without the AAPL sentinel publication")
        final_checks = _targeted_store_checks(
            self._target,
            expected=None,
            session_dates=STAGE12C_EXPECTED_SESSIONS,
        )
        expected_rows = self._baseline.current_price_rows + 2 * published
        expected_versions = self._baseline.immutable_price_versions + 2 * published
        expected_captures = self._baseline.daily_price_captures + published
        if (
            final_checks["current_price_rows"] != expected_rows
            or final_checks["immutable_price_versions"] != expected_versions
            or final_checks["daily_price_captures"] != expected_captures
            or final_checks["scoped_current_row_count"] != 2 * published
        ):
            raise ConflictError("Stage 12C final market counts do not reconcile to durable unit outcomes")
        source_final = _file_sha256(self._source, "retained source")
        if not _source_mapping_matches(baseline.get("source"), source_final):
            raise ConflictError("Stage 12C retained source changed during the manual run")
        target_final = _file_sha256(self._target, "project market target")
        current_identity = _require_direct_regular_file(
            self._target,
            "project market target",
        )
        if current_identity != expected_target_identity:
            raise ConflictError(
                "Stage 12C project target changed before completion receipt"
            )
        result_digests = [
            str(states[unit.identifier].result["result_sha256"])
            for unit in plan.units
        ]
        material = {
            "baseline_sha256": self._baseline.sha256,
            "final_target": target_final.public_mapping(),
            "final_target_checks": final_checks,
            "plan_sha256": plan.sha256,
            "published_unit_count": published,
            "result_sha256s": result_digests,
            "scope_manifest_sha256": self._scope.manifest_sha256,
            "source_final": source_final.public_mapping(),
            "static_preflight_sha256": _sha256_json(dict(static_preflight)),
            "terminal_outcomes": [dict(item) for item in terminal],
            "terminal_noncoverage_count": len(terminal),
            "total_attempt_count": len(plan.units),
            "version": _VERSION,
        }
        receipt = {
            "contract": _COMPLETION_CONTRACT,
            "sha256": _sha256_json(material),
            **material,
        }
        return receipt

    def _completed_report(self, plan: _Plan, states: Mapping[str, _JournalState], receipt: Mapping[str, object], *, requests_issued: int) -> Stage12CMarketGapRunReport:
        closed, published, terminal = _closed_result_counts(states)
        return Stage12CMarketGapRunReport(
            plan_sha256=plan.sha256,
            scope_manifest_sha256=self._scope.manifest_sha256,
            closed_unit_count=closed,
            published_unit_count=published,
            terminal_noncoverage_count=len(terminal),
            requests_issued=requests_issued,
            completion="complete",
            terminal_outcomes=terminal,
            receipt_sha256=str(receipt["sha256"]),
        )

    def run(self) -> Stage12CMarketGapRunReport:
        """Run or locally resume the exact Stage 12C plan.

        The process environment is intentionally not inspected until after
        every local path, scope, journal, baseline, SQLite, and static-registry
        preflight has completed.
        """

        plan = _build_plan(self._scope, self._stage12a_scope, self._stage12b_scope)
        layout = _open_or_create_layout(self._project)
        with _held_run_lock(layout):
            _ensure_plan_receipt(layout, plan)
            baseline, target_identity = _ensure_baseline(
                layout=layout,
                plan=plan,
                target=self._target,
                source=self._source,
                expectation=self._baseline,
            )
            states = _load_journal(layout, plan)
            recovered_durable_request = any(
                state.intent is not None for state in states.values()
            )
            static_preflight = self._validate_static_preflight()
            states = self._recover_spools(
                layout=layout,
                plan=plan,
                states=states,
                target_identity=target_identity,
            )
            if all(state.result is not None for state in states.values()):
                receipt = self._complete_under_target_lock(
                    layout=layout,
                    plan=plan,
                    states=states,
                    baseline=baseline,
                    static_preflight=static_preflight,
                    target_identity=target_identity,
                )
                return self._completed_report(plan, states, receipt, requests_issued=0)

            sentinel = states[plan.units[0].identifier].result
            if sentinel is not None and sentinel.get("outcome") != "published_complete":
                raise StoreUnavailableError("Stage 12C cannot continue after a failed AAPL sentinel")
            api_key = self._read_credential()
            pacer = _RequestPacer(
                monotonic=self._monotonic,
                sleeper=self._sleeper,
            )
            if recovered_durable_request:
                pacer.arm_resume_delay()
            issued = 0
            for unit in plan.units:
                state = states[unit.identifier]
                if state.result is not None:
                    continue
                if unit.ordinal > 1:
                    sentinel = states[plan.units[0].identifier].result
                    if sentinel is None or sentinel.get("outcome") != "published_complete":
                        raise StoreUnavailableError("Stage 12C cannot issue later requests before AAPL succeeds")
                state = self._issue_one(
                    layout=layout,
                    unit=unit,
                    target_identity=target_identity,
                    pacer=pacer,
                    api_key=api_key,
                )
                issued += 1
                states[unit.identifier] = state
            receipt = self._complete_under_target_lock(
                layout=layout,
                plan=plan,
                states=states,
                baseline=baseline,
                static_preflight=static_preflight,
                target_identity=target_identity,
            )
            return self._completed_report(plan, states, receipt, requests_issued=issued)


    @classmethod
    def _for_canonical_live(cls) -> _Stage12CMarketGapRunner:
        """Build the sole non-injectable canonical live runner."""

        if cls is not _Stage12CMarketGapRunner:
            raise ValidationError("Stage 12C canonical runner cannot be subclassed")
        project, target, source = _require_canonical_live_roots()
        scope = load_stage12c_market_gap_v1_scope(
            project / "config" / "stage12c_market_gap_v1_scope.json"
        )
        stage12a_scope = load_stage12_market_v1_scope(
            project / "config" / "stage12_market_v1_scope.json"
        )
        stage12b_scope = load_stage12b_incremental_market_v1_scope(
            project / "config" / "stage12b_incremental_market_v1_scope.json"
        )
        collector = Stage12CIncrementalCollector._for_canonical_live(
            scope=scope,
            stage12a_scope=stage12a_scope,
            stage12b_scope=stage12b_scope,
        )
        if collector.market_store != target:
            raise ValidationError("Stage 12C canonical collector target is invalid")

        def static_preflight(project_root: str | Path) -> Mapping[str, object]:
            from ..stage12c import validate_stage12c_live_static_preflight

            return validate_stage12c_live_static_preflight(project_root)

        return cls(
            project_root=project,
            retained_source=source,
            scope=scope,
            stage12a_scope=stage12a_scope,
            stage12b_scope=stage12b_scope,
            collector=collector,
            transport=_CanonicalStdlibStage12CTransport(
                capability=_CANONICAL_LIVE_CAPABILITY
            ),
            environment=os.environ,
            static_preflight=static_preflight,
            _construction_capability=_CANONICAL_LIVE_CAPABILITY,
            baseline=RETAINED_STAGE10_BASELINE,
        )


class Stage12CMarketGapRunner(_Stage12CMarketGapRunner):
    """Fixture-only injectable Stage 12C runner.

    The public constructor rejects canonical roots and requires every supplied
    filesystem object to be a physical descendant of the system temporary root.
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        retained_source: str | Path,
        scope: Stage12CMarketGapV1Scope,
        stage12a_scope: Stage12MarketV1Scope,
        stage12b_scope: Stage12BIncrementalMarketScope,
        collector: Stage12CIncrementalCollector,
        transport: Stage12CTransport,
        environment: Mapping[str, str],
        static_preflight: Callable[[str | Path], Mapping[str, object]],
        baseline: Stage12CBaselineExpectation = RETAINED_STAGE10_BASELINE,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        super().__init__(
            project_root=project_root,
            retained_source=retained_source,
            scope=scope,
            stage12a_scope=stage12a_scope,
            stage12b_scope=stage12b_scope,
            collector=collector,
            transport=transport,
            environment=environment,
            static_preflight=static_preflight,
            _construction_capability=_FIXTURE_RUNNER_CAPABILITY,
            baseline=baseline,
            monotonic=monotonic,
            sleeper=sleeper,
            utcnow=utcnow,
        )


def run_stage12c_market_gap_live() -> Stage12CMarketGapRunReport:
    """Run the one reviewed canonical Stage 12C slice with no injected inputs."""

    return _Stage12CMarketGapRunner._for_canonical_live().run()


__all__ = (
    "APPROVED_MARKET_RELATIVE_PATH",
    "APPROVED_PRIVATE_RELATIVE_ROOT",
    "APPROVED_PROJECT_ROOT",
    "APPROVED_RETAINED_SOURCE",
    "RETAINED_STAGE10_BASELINE",
    "STAGE12C_BASELINE_SHA256",
    "STAGE12C_ENDPOINT_PATH",
    "STAGE12C_EXPECTED_SESSIONS",
    "STAGE12C_EXPECTED_SYMBOL_COUNT",
    "STAGE12C_MAX_RESPONSE_BYTES",
    "STAGE12C_MINIMUM_REQUEST_INTERVAL_SECONDS",
    "STAGE12C_TIMEOUT_SECONDS",
    "Stage12CBaselineExpectation",
    "Stage12CMarketGapRunReport",
    "Stage12CMarketGapRunner",
    "Stage12CTransport",
    "Stage12CTransportResponse",
    "run_stage12c_market_gap_live",
)
