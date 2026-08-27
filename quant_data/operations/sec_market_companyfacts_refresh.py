"""Fixed, manual SEC CompanyFacts refresh for Stage 10 market equities.

This operation owns the provider-facing bulk boundary only.  It first reads
the retained Stage 10 equity roster through a descriptor-pinned immutable
SQLite connection, resolves that fixed roster through SEC's official ticker
map, and then handles one issuer at a time.  Each issuer's submissions and
CompanyFacts bodies stay in memory only until the existing company-domain
publisher receives them.  The publisher owns the company-store lock, so no
network work is performed while that lock is held.

The operation intentionally has no caller-selected ticker, CIK, path, or
scheduler argument.  It is a bounded bulk population primitive; a host job
may invoke only this zero-argument entry point after its own authorization.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
from time import monotonic, sleep
from typing import Any, Final, Iterator, Protocol
from urllib.parse import urlsplit

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreMap, StoreRole, resolve_store_map


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
COMPANY_STORE: Final = PROJECT_ROOT / "data" / "company.sqlite"

SEC_TICKERS_URL: Final = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TEMPLATE: Final = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL_TEMPLATE: Final = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)

MAX_STAGE10_EQUITIES: Final = 700
REQUEST_CAP: Final = 1 + 2 * MAX_STAGE10_EQUITIES
MAX_DISCOVERY_BYTES: Final = 8 * 1024 * 1024
MAX_DISCOVERY_ROWS: Final = 20_000
MAX_PER_CIK_RESPONSE_BYTES: Final = 64 * 1024 * 1024
MAX_PER_CIK_TOTAL_RESPONSE_BYTES: Final = 64 * 1024 * 1024
MAX_RUN_SECONDS: Final = 6 * 60 * 60
MAX_PER_CIK_SECONDS: Final = 120
TIMEOUT_SECONDS: Final = 60
MIN_REQUEST_INTERVAL_SECONDS: Final = 0.2
_JSON_MEDIA_TYPE: Final = "application/json"
_VERSION: Final = "1.1.0"
_AAPL_CIK: Final = "0000320193"
_MAX_COMPANY_ISSUERS: Final = 10_000
_SOURCE_SIDECARS: Final = ("-wal", "-shm", "-journal")
_SENSITIVE_KEY_PARTS: Final = frozenset(
    {
        "agent",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "email",
        "header",
        "password",
        "secret",
        "token",
        "user_agent",
        "username",
    }
)
_MAX_SAFE_STRING: Final = 512
_MAX_SAFE_ITEMS: Final = 64
_MAX_SAFE_DEPTH: Final = 4


@dataclass(frozen=True, slots=True)
class SecMarketCompanyFactsTransportResponse:
    """One bounded direct SEC response held only until it is consumed."""

    status: int
    media_type: str
    body: bytes
    redirected: bool = False


class SecMarketCompanyFactsTransport(Protocol):
    """Injectable one-request SEC transport; callers never ask it to retry."""

    def request(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> SecMarketCompanyFactsTransportResponse: ...


class StdlibSecMarketCompanyFactsTransport:
    """One bounded HTTPS GET with redirects deliberately disabled."""

    def request(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> SecMarketCompanyFactsTransportResponse:
        if (
            not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or max_bytes > MAX_PER_CIK_RESPONSE_BYTES
            or not isinstance(headers, Mapping)
            or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in headers.items()
            )
        ):
            raise ValidationError("SEC market provider request is invalid")
        _validate_sec_url(url)
        parsed = urlsplit(url)
        try:
            connection = http.client.HTTPSConnection(
                parsed.netloc, timeout=timeout_seconds
            )
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("SEC market provider transport failed") from exc
        try:
            connection.request("GET", parsed.path, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError(
                        "SEC market provider length is invalid"
                    ) from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError(
                        "SEC market provider response exceeds its byte bound"
                    )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError(
                    "SEC market provider response exceeds its byte bound"
                )
            status = int(response.status)
            return SecMarketCompanyFactsTransportResponse(
                status=status,
                media_type=response.getheader("Content-Type") or "",
                body=body,
                redirected=(
                    300 <= status < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("SEC market provider transport failed") from exc
        finally:
            connection.close()


DomainCall = Callable[..., object]
CredentialReader = Callable[..., str]


@dataclass(frozen=True, slots=True)
class _FileStamp:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True, slots=True)
class _IssuerPlan:
    cik: str
    symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SecMarketCompanyFactsReport:
    """Compact, credential-free aggregate receipt for one bounded run."""

    requested: int
    response_bytes: int
    planned_symbol_count: int
    matched_symbol_count: int
    unique_cik_count: int
    selected_cik_count: int
    skipped_existing_cik_count: int
    succeeded_cik_count: int
    failed_cik_count: int
    unmatched_symbol_count: int
    ambiguous_symbol_count: int
    ticker_mismatch_cik_count: int
    rejected_discovery_record_count: int
    aggregate_deadline_exceeded: bool
    matched_cik_digest: str
    succeeded_cik_digest: str
    failed_cik_digest: str
    unmatched_symbol_digest: str
    ambiguous_symbol_digest: str
    failure_stage_counts: tuple[tuple[str, int], ...]
    failure_kind_counts: tuple[tuple[str, int], ...]

    @property
    def complete(self) -> bool:
        return (
            self.failed_cik_count == 0
            and self.unmatched_symbol_count == 0
            and self.ambiguous_symbol_count == 0
            and not self.aggregate_deadline_exceeded
        )

    def mapping(self) -> dict[str, object]:
        return {
            "aggregate_deadline_exceeded": self.aggregate_deadline_exceeded,
            "ambiguous_symbol_count": self.ambiguous_symbol_count,
            "ambiguous_symbol_digest": self.ambiguous_symbol_digest,
            "complete": self.complete,
            "failed_cik_count": self.failed_cik_count,
            "failed_cik_digest": self.failed_cik_digest,
            "failure_kind_counts": dict(self.failure_kind_counts),
            "failure_stage_counts": dict(self.failure_stage_counts),
            "matched_cik_digest": self.matched_cik_digest,
            "matched_symbol_count": self.matched_symbol_count,
            "planned_symbol_count": self.planned_symbol_count,
            "rejected_discovery_record_count": self.rejected_discovery_record_count,
            "requested": self.requested,
            "response_bytes": self.response_bytes,
            "selected_cik_count": self.selected_cik_count,
            "skipped_existing_cik_count": self.skipped_existing_cik_count,
            "succeeded_cik_count": self.succeeded_cik_count,
            "succeeded_cik_digest": self.succeeded_cik_digest,
            "ticker_mismatch_cik_count": self.ticker_mismatch_cik_count,
            "unique_cik_count": self.unique_cik_count,
            "unmatched_symbol_count": self.unmatched_symbol_count,
            "unmatched_symbol_digest": self.unmatched_symbol_digest,
        }


def _submissions_url(cik: str) -> str:
    return SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)


def _companyfacts_url(cik: str) -> str:
    return SEC_COMPANYFACTS_URL_TEMPLATE.format(cik=cik)


def _cik(value: object, *, label: str) -> str | None:
    """Return one valid, zero-padded SEC CIK, or reject this discovery row."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str) and value.isdecimal():
        text = value
    else:
        return None
    if not text or len(text) > 10:
        return None
    try:
        number = int(text)
    except ValueError:  # pragma: no cover - isdecimal above covers it
        return None
    if number <= 0 or number > 9_999_999_999:
        return None
    result = str(number).zfill(10)
    if len(result) != 10:  # pragma: no cover - defensive narrowing
        raise ValidationError(f"SEC market {label} CIK is invalid")
    return result


def _validate_sec_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("SEC market provider URL is invalid")
    allowed = value == SEC_TICKERS_URL
    if not allowed:
        for template in (SEC_SUBMISSIONS_URL_TEMPLATE, SEC_COMPANYFACTS_URL_TEMPLATE):
            prefix, suffix = template.split("{cik}", 1)
            if value.startswith(prefix) and value.endswith(suffix):
                candidate = value[len(prefix) : len(value) - len(suffix)]
                allowed = _cik(candidate, label="provider") == candidate
                break
    parsed = urlsplit(value)
    if (
        not allowed
        or parsed.scheme != "https"
        or parsed.netloc not in {"www.sec.gov", "data.sec.gov"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError("SEC market provider URL is invalid")
    return value


def _media_type(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise StoreUnavailableError("SEC market source media type is invalid")
    result = value.split(";", 1)[0].strip().casefold()
    if not result:
        raise StoreUnavailableError("SEC market source media type is invalid")
    return result


def _validate_response(
    response: object, *, max_bytes: int
) -> SecMarketCompanyFactsTransportResponse:
    if (
        not isinstance(max_bytes, int)
        or max_bytes <= 0
        or max_bytes > MAX_PER_CIK_RESPONSE_BYTES
    ):
        raise ValidationError("SEC market response bound is invalid")
    if not isinstance(response, SecMarketCompanyFactsTransportResponse):
        raise StoreUnavailableError("SEC market transport returned an invalid response")
    if response.redirected:
        raise StoreUnavailableError("SEC market provider redirect is not allowed")
    if isinstance(response.status, bool) or response.status != 200:
        raise StoreUnavailableError("SEC market provider response is outside policy")
    if _media_type(response.media_type) != _JSON_MEDIA_TYPE:
        raise StoreUnavailableError("SEC market provider media type is invalid")
    if not isinstance(response.body, bytes) or not response.body:
        raise StoreUnavailableError("SEC market provider response body is invalid")
    if len(response.body) > max_bytes:
        raise ResourceLimitError("SEC market provider response exceeds its byte bound")
    try:
        json.loads(response.body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreUnavailableError("SEC market provider JSON is invalid") from exc
    return response


def _credential_component(value: object, *, kind: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 254
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ValidationError("SEC User-Agent credential is missing or invalid")
    if kind == "email":
        local, separator, domain = value.partition("@")
        if (
            separator != "@"
            or not local
            or not domain
            or "@" in domain
            or "." not in domain
        ):
            raise ValidationError("SEC User-Agent credential is missing or invalid")
    elif kind != "name":  # pragma: no cover - internal invariant
        raise ValidationError("SEC User-Agent credential is missing or invalid")
    return value


def _utcnow(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("SEC market capture time must include an offset")
    return value.astimezone(timezone.utc)


def _safe_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise ValidationError("SEC market receipt is invalid")
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValidationError("SEC market receipt is invalid")
    if value.casefold() in _SENSITIVE_KEY_PARTS:
        raise ValidationError("SEC market receipt is invalid")
    return value


def _safe_value(
    value: object, *, forbidden_text: tuple[str, ...], depth: int = 0
) -> Any:
    if depth > _MAX_SAFE_DEPTH:
        raise ValidationError("SEC market receipt is invalid")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("SEC market receipt is invalid")
        return value
    if isinstance(value, str):
        if (
            len(value) > _MAX_SAFE_STRING
            or "\x00" in value
            or any(secret and secret in value for secret in forbidden_text)
        ):
            raise ValidationError("SEC market receipt is invalid")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("SEC market receipt is invalid")
        return {
            _safe_key(key): _safe_value(
                item, forbidden_text=forbidden_text, depth=depth + 1
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("SEC market receipt is invalid")
        return [
            _safe_value(item, forbidden_text=forbidden_text, depth=depth + 1)
            for item in value
        ]
    raise ValidationError("SEC market receipt is invalid")


def _stamp(path: Path, *, label: str) -> _FileStamp:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StoreUnavailableError(f"SEC market {label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValidationError(f"SEC market {label} binding is invalid")
    return _FileStamp(
        device=info.st_dev,
        inode=info.st_ino,
        mode=stat.S_IMODE(info.st_mode),
        links=info.st_nlink,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
    )


def _sidecar_stamps(path: Path, *, label: str) -> dict[str, _FileStamp | None]:
    """Capture sidecar identity without treating a quiet SHM file as a hazard.

    SQLite can retain a stable shared-memory sidecar after a quiet read.  A
    zero-byte WAL is also harmless here: immutable mode never consults or
    mutates it.  A nonempty WAL or rollback journal can contain source facts
    absent from the main file, so that remains fail-closed.
    """

    result: dict[str, _FileStamp | None] = {}
    for suffix in _SOURCE_SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            result[suffix] = None
            continue
        except OSError as exc:
            raise StoreUnavailableError(f"SEC market {label} is unavailable") from exc
        if sidecar.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValidationError(f"SEC market {label} sidecar binding is invalid")
        result[suffix] = _FileStamp(
            device=info.st_dev,
            inode=info.st_ino,
            mode=stat.S_IMODE(info.st_mode),
            links=info.st_nlink,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            ctime_ns=info.st_ctime_ns,
        )
    for suffix in ("-wal", "-journal"):
        stamp = result[suffix]
        if stamp is not None and stamp.size != 0:
            raise ConflictError(f"SEC market {label} has unsealed SQLite state")
    return result


def _immutable_descriptor_uri(descriptor: int) -> str:
    if not isinstance(descriptor, int) or descriptor < 0:
        raise ValidationError("SEC market descriptor is invalid")
    return f"file:/proc/self/fd/{descriptor}?mode=ro&immutable=1"


def _require_bound_targets(
    *,
    project_root: Path,
    market_store: Path,
    company_store: Path,
    stores: StoreMap,
    canonical: bool,
) -> None:
    """Bind both stores to the one project-local path pair before any work."""

    try:
        root = project_root.resolve(strict=True)
        market = market_store.resolve(strict=True)
        company = company_store.resolve(strict=True)
        root_info = project_root.lstat()
        market_info = market_store.lstat()
        company_info = company_store.lstat()
        mapped_market = stores.path(StoreRole.MARKET).resolve(strict=True)
        mapped_company = stores.path(StoreRole.COMPANY).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError("SEC market target is unavailable") from exc
    if (
        root != project_root
        or market != market_store
        or company != company_store
        or mapped_market != market
        or mapped_company != company
        or project_root.is_symlink()
        or market_store.is_symlink()
        or company_store.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or not stat.S_ISREG(market_info.st_mode)
        or not stat.S_ISREG(company_info.st_mode)
        or market_info.st_nlink != 1
        or company_info.st_nlink != 1
        or market_store != project_root / "data" / "market.sqlite"
        or company_store != project_root / "data" / "company.sqlite"
    ):
        raise ValidationError("SEC market target binding is invalid")
    _sidecar_stamps(market_store, label="market target")
    # The company writer owns its own physical lock.  This preflight only
    # rejects unsealed content, while allowing ordinary stable SHM/zero-WAL
    # state just as the Inspector's immutable reader does.
    _sidecar_stamps(company_store, label="company target")
    if canonical:
        if (
            project_root != PROJECT_ROOT
            or market_store != MARKET_STORE
            or company_store != COMPANY_STORE
        ):
            raise ValidationError("SEC market canonical target binding is invalid")
        return
    try:
        project_root.relative_to(Path("/tmp").resolve(strict=True))
    except ValueError as exc:
        raise ValidationError("SEC market fixtures must use a temporary root") from exc


def _valid_symbol(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 32
        or value != value.strip()
        or not value.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ValidationError("SEC market Stage 10 equity symbol is invalid")
    return value


@contextmanager
def _immutable_store_connection(
    store_path: Path, *, label: str
) -> Iterator[sqlite3.Connection]:
    """Descriptor-pin one source store and prove the immutable read was inert."""

    before = _stamp(store_path, label=label)
    before_sidecars = _sidecar_stamps(store_path, label=label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(store_path, flags)
    except OSError as exc:
        raise StoreUnavailableError("SEC market target is unavailable") from exc
    connection: sqlite3.Connection | None = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != before.device
            or opened.st_ino != before.inode
        ):
            raise ConflictError("SEC market target changed before immutable roster read")
        try:
            connection = sqlite3.connect(
                _immutable_descriptor_uri(descriptor),
                uri=True,
                isolation_level=None,
                timeout=0.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            query_only = connection.execute("PRAGMA query_only").fetchone()
            if query_only is None or query_only[0] != 1:
                raise ConflictError("SEC market immutable roster read is not query-only")
            yield connection
        except (ConflictError, ValidationError, StoreUnavailableError):
            raise
        except sqlite3.Error as exc:
            raise StoreUnavailableError("SEC market roster is unavailable") from exc
    finally:
        if connection is not None:
            connection.close()
        os.close(descriptor)
        if _stamp(store_path, label=label) != before:
            raise ConflictError("SEC market target changed during immutable roster read")
        if _sidecar_stamps(store_path, label=label) != before_sidecars:
            raise ConflictError("SEC market target sidecar changed during immutable roster read")


def load_stage10_equity_symbols(market_store: str | Path) -> tuple[str, ...]:
    """Load the exact current FMP Stage 10 equity strings, not a normalized copy."""

    target = Path(market_store)
    with _immutable_store_connection(target, label="market target") as connection:
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage10_instruments'"
            ).fetchone()
            if table is None:
                raise ValidationError("SEC market target lacks the Stage 10 roster")
            rows = connection.execute(
                """
                SELECT provider_symbol
                FROM stage10_instruments
                WHERE provider='fmp' AND asset_type='equity'
                ORDER BY provider_symbol
                """
            ).fetchall()
        except ValidationError:
            raise
        except sqlite3.Error as exc:
            raise ValidationError("SEC market target lacks the Stage 10 roster") from exc
    symbols = tuple(_valid_symbol(row["provider_symbol"]) for row in rows)
    if not symbols:
        raise ValidationError("SEC market Stage 10 equity roster is empty")
    if len(symbols) > MAX_STAGE10_EQUITIES:
        raise ResourceLimitError("SEC market Stage 10 equity roster exceeds its bound")
    if len(set(symbols)) != len(symbols):
        raise ConflictError("SEC market Stage 10 equity roster is duplicated")
    return symbols


def load_company_issuer_ciks(company_store: str | Path) -> frozenset[str]:
    """Load already published issuer CIKs without mutating the company store."""

    target = Path(company_store)
    with _immutable_store_connection(target, label="company target") as connection:
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_issuers'"
            ).fetchone()
            if table is None:
                raise ValidationError("SEC company target lacks the issuer roster")
            rows = connection.execute(
                "SELECT cik FROM company_issuers ORDER BY cik"
            ).fetchall()
        except ValidationError:
            raise
        except sqlite3.Error as exc:
            raise ValidationError("SEC company target lacks the issuer roster") from exc
    ciks: list[str] = []
    for row in rows:
        value = row["cik"]
        if _cik(value, label="company") != value:
            raise ValidationError("SEC company issuer CIK is invalid")
        ciks.append(value)
    if len(ciks) > _MAX_COMPANY_ISSUERS:
        raise ResourceLimitError("SEC company issuer roster exceeds its bound")
    if len(set(ciks)) != len(ciks):
        raise ConflictError("SEC company issuer roster is duplicated")
    return frozenset(ciks)


def _parse_discovery(
    body: bytes,
) -> tuple[dict[str, tuple[str, ...]], int]:
    """Return exact SEC ticker->CIK candidates and rejected discovery-row count."""

    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # validated earlier
        raise StoreUnavailableError("SEC market ticker discovery JSON is invalid") from exc
    if not isinstance(payload, Mapping) or len(payload) > MAX_DISCOVERY_ROWS:
        raise ResourceLimitError("SEC market ticker discovery exceeds its row bound")
    candidates: dict[str, set[str]] = {}
    rejected = 0
    for row in payload.values():
        if not isinstance(row, Mapping):
            rejected += 1
            continue
        ticker = row.get("ticker")
        cik = _cik(row.get("cik_str"), label="discovery")
        if (
            not isinstance(ticker, str)
            or not ticker
            or len(ticker) > 32
            or ticker != ticker.strip()
            or not ticker.isascii()
            or any(ord(character) < 33 or ord(character) > 126 for character in ticker)
            or cik is None
        ):
            rejected += 1
            continue
        candidates.setdefault(ticker, set()).add(cik)
    return {
        ticker: tuple(sorted(ciks))
        for ticker, ciks in candidates.items()
    }, rejected


def plan_sec_market_issuers(
    *, symbols: Sequence[str], discovery_body: bytes
) -> tuple[tuple[_IssuerPlan, ...], tuple[str, ...], tuple[str, ...], int]:
    """Exact-match the fixed market symbol strings, retaining ambiguity explicitly."""

    if not isinstance(symbols, Sequence) or isinstance(symbols, (str, bytes, bytearray)):
        raise ValidationError("SEC market Stage 10 equity roster is invalid")
    roster = tuple(_valid_symbol(symbol) for symbol in symbols)
    if not roster or len(roster) > MAX_STAGE10_EQUITIES or len(set(roster)) != len(roster):
        raise ValidationError("SEC market Stage 10 equity roster is invalid")
    ticker_candidates, rejected = _parse_discovery(discovery_body)
    by_cik: dict[str, list[str]] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    for symbol in roster:
        candidates = ticker_candidates.get(symbol, ())
        if not candidates:
            unmatched.append(symbol)
        elif len(candidates) != 1:
            ambiguous.append(symbol)
        else:
            by_cik.setdefault(candidates[0], []).append(symbol)
    plans = tuple(
        _IssuerPlan(cik=cik, symbols=tuple(sorted(mapped_symbols)))
        for cik, mapped_symbols in sorted(by_cik.items())
    )
    return plans, tuple(sorted(unmatched)), tuple(sorted(ambiguous)), rejected


def _digest(values: Sequence[str]) -> str:
    return hashlib.sha256(
        dumps_strict({"items": list(sorted(values))}).encode("utf-8")
    ).hexdigest()


def _failure_kind(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, ResourceLimitError):
        return "resource_limit"
    if isinstance(exc, StoreUnavailableError):
        return "store_unavailable"
    if isinstance(exc, RegistryError):
        return "registry"
    if isinstance(exc, ConflictError):
        return "conflict"
    return "internal"


def _counts(values: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((name, count) for name, count in values.items() if count))


def _submissions_tickers(body: bytes) -> frozenset[str]:
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # validated earlier
        raise StoreUnavailableError("SEC market submissions JSON is invalid") from exc
    if not isinstance(payload, Mapping):
        raise StoreUnavailableError("SEC market submissions JSON is invalid")
    tickers = payload.get("tickers")
    if not isinstance(tickers, list) or len(tickers) > 128:
        raise ValidationError("SEC market submissions tickers are invalid")
    result: set[str] = set()
    for ticker in tickers:
        if (
            not isinstance(ticker, str)
            or not ticker
            or len(ticker) > 32
            or ticker != ticker.strip()
            or not ticker.isascii()
            or any(ord(character) < 33 or ord(character) > 126 for character in ticker)
        ):
            raise ValidationError("SEC market submissions tickers are invalid")
        result.add(ticker)
    return frozenset(result)


def _receipt_token(cik: str, receipt: object) -> str:
    """Extract just enough non-sensitive domain receipt identity for an aggregate digest."""

    converter = getattr(receipt, "to_primitive", None)
    candidate = converter() if callable(converter) else receipt
    if not isinstance(candidate, Mapping):
        raise ValidationError("SEC market company publication receipt is invalid")
    identity = candidate.get("semantic_identity")
    if (
        not isinstance(identity, str)
        or not identity
        or len(identity) > 128
        or not identity.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in identity)
    ):
        raise ValidationError("SEC market company publication receipt is invalid")
    return f"{cik}:{identity}"


class SecMarketCompanyFactsRunner:
    """Resolve and refresh the fixed Stage 10 equity universe sequentially."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        market_store: str | Path,
        company_store: str | Path,
        stores: StoreMap,
        registry: object,
        domain_call: DomainCall,
        transport: SecMarketCompanyFactsTransport,
        credential_environment: Mapping[str, str],
        skip_ciks: frozenset[str] = frozenset(),
        credential_reader: CredentialReader = read_project_credential,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic_clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        _canonical: bool = False,
    ) -> None:
        self._project_root = Path(project_root)
        self._market_store = Path(market_store)
        self._company_store = Path(company_store)
        self._stores = stores
        self._registry = registry
        self._domain_call = domain_call
        self._transport = transport
        self._credential_environment = credential_environment
        self._skip_ciks = skip_ciks
        self._credential_reader = credential_reader
        self._utcnow = utcnow
        self._monotonic = monotonic_clock
        self._sleeper = sleeper
        self._last_request_started: float | None = None
        self._requested = 0
        self._response_bytes = 0
        if (
            not isinstance(self._stores, StoreMap)
            or not isinstance(self._skip_ciks, frozenset)
            or len(self._skip_ciks) > _MAX_COMPANY_ISSUERS
            or any(
                _cik(cik, label="skip") != cik for cik in self._skip_ciks
            )
        ):
            raise ValidationError("SEC market runner dependencies are invalid")
        _require_bound_targets(
            project_root=self._project_root,
            market_store=self._market_store,
            company_store=self._company_store,
            stores=self._stores,
            canonical=_canonical,
        )
        if (
            self._registry is None
            or not callable(self._domain_call)
            or not callable(getattr(self._transport, "request", None))
            or not isinstance(self._credential_environment, Mapping)
            or not callable(self._credential_reader)
            or not callable(self._utcnow)
            or not callable(self._monotonic)
            or not callable(self._sleeper)
        ):
            raise ValidationError("SEC market runner dependencies are invalid")

    def _read_user_agent(self) -> tuple[str, tuple[str, ...]]:
        try:
            name = self._credential_reader(
                project_root=self._project_root,
                name="SEC_USER_AGENT_NAME",
                environment=self._credential_environment,
            )
            email = self._credential_reader(
                project_root=self._project_root,
                name="SEC_USER_AGENT_EMAIL",
                environment=self._credential_environment,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("SEC User-Agent credential is missing or invalid") from exc
        safe_name = _credential_component(name, kind="name")
        safe_email = _credential_component(email, kind="email")
        user_agent = f"{safe_name} {safe_email}"
        if len(user_agent) > 512:
            raise ValidationError("SEC User-Agent credential is missing or invalid")
        return user_agent, (safe_name, safe_email, user_agent)

    def _remaining_timeout(self, *, deadline: float, label: str) -> int:
        now = self._monotonic()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValidationError("SEC market monotonic clock is invalid")
        remaining = deadline - float(now)
        if remaining <= 0:
            raise ResourceLimitError(f"SEC market {label} deadline exceeded")
        return min(TIMEOUT_SECONDS, max(1, math.ceil(remaining)))

    def _pace(self, *, deadline: float, label: str) -> None:
        if self._last_request_started is None:
            return
        now = self._monotonic()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
            raise ValidationError("SEC market monotonic clock is invalid")
        wait_seconds = MIN_REQUEST_INTERVAL_SECONDS - (float(now) - self._last_request_started)
        if wait_seconds <= 0:
            return
        if float(now) + wait_seconds >= deadline:
            raise ResourceLimitError(f"SEC market {label} deadline exceeded")
        self._sleeper(wait_seconds)

    def _request(
        self,
        *,
        url: str,
        user_agent: str,
        max_bytes: int,
        deadline: float,
        label: str,
    ) -> SecMarketCompanyFactsTransportResponse:
        self._pace(deadline=deadline, label=label)
        timeout_seconds = self._remaining_timeout(deadline=deadline, label=label)
        request_started = self._monotonic()
        if (
            not isinstance(request_started, (int, float))
            or isinstance(request_started, bool)
            or not math.isfinite(request_started)
            or request_started >= deadline
        ):
            raise ResourceLimitError(f"SEC market {label} deadline exceeded")
        if self._requested >= REQUEST_CAP:
            raise ResourceLimitError("SEC market request cap exceeded")
        self._last_request_started = float(request_started)
        self._requested += 1
        response = self._transport.request(
            url=url,
            headers={"Accept": _JSON_MEDIA_TYPE, "User-Agent": user_agent},
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
        if isinstance(response, SecMarketCompanyFactsTransportResponse) and isinstance(
            response.body, bytes
        ):
            self._response_bytes += len(response.body)
        validated = _validate_response(response, max_bytes=max_bytes)
        self._remaining_timeout(deadline=deadline, label=label)
        return validated

    def run(self) -> SecMarketCompanyFactsReport:
        """Execute one bounded discovery plus sequential, isolated issuer pairs."""

        started = self._monotonic()
        if (
            not isinstance(started, (int, float))
            or isinstance(started, bool)
            or not math.isfinite(started)
        ):
            raise ValidationError("SEC market monotonic clock is invalid")
        aggregate_deadline = float(started) + MAX_RUN_SECONDS
        self._last_request_started = None
        self._requested = 0
        self._response_bytes = 0
        # The descriptor-pinned roster read completes before credentials or network.
        symbols = load_stage10_equity_symbols(self._market_store)
        user_agent, forbidden_text = self._read_user_agent()
        discovery_deadline = min(aggregate_deadline, self._monotonic() + TIMEOUT_SECONDS)
        discovery = self._request(
            url=SEC_TICKERS_URL,
            user_agent=user_agent,
            max_bytes=MAX_DISCOVERY_BYTES,
            deadline=discovery_deadline,
            label="discovery",
        )
        plans, unmatched, ambiguous, rejected_discovery = plan_sec_market_issuers(
            symbols=symbols,
            discovery_body=discovery.body,
        )
        matched_symbols = sum(len(item.symbols) for item in plans)
        selected_plans = tuple(
            plan for plan in plans if plan.cik not in self._skip_ciks
        )
        skipped_existing = len(plans) - len(selected_plans)
        succeeded: list[str] = []
        failed: list[str] = []
        ticker_mismatches: list[str] = []
        aggregate_deadline_exceeded = False
        failure_stages: dict[str, int] = {}
        failure_kinds: dict[str, int] = {}

        for position, plan in enumerate(selected_plans):
            now = self._monotonic()
            if not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise ValidationError("SEC market monotonic clock is invalid")
            if float(now) >= aggregate_deadline:
                aggregate_deadline_exceeded = True
                remaining_plans = selected_plans[position:]
                failed.extend(remaining.cik for remaining in remaining_plans)
                failure_stages["aggregate_deadline"] = len(remaining_plans)
                failure_kinds["resource_limit"] = len(remaining_plans)
                break
            per_cik_deadline = min(float(now) + MAX_PER_CIK_SECONDS, aggregate_deadline)
            submissions_body = b""
            companyfacts_body = b""
            stage = "submissions_request"
            try:
                submissions = self._request(
                    url=_submissions_url(plan.cik),
                    user_agent=user_agent,
                    max_bytes=MAX_PER_CIK_TOTAL_RESPONSE_BYTES,
                    deadline=per_cik_deadline,
                    label="issuer",
                )
                remaining = MAX_PER_CIK_TOTAL_RESPONSE_BYTES - len(submissions.body)
                stage = "companyfacts_request"
                if remaining <= 0:
                    raise ResourceLimitError("SEC market issuer responses exceed their byte bound")
                companyfacts = self._request(
                    url=_companyfacts_url(plan.cik),
                    user_agent=user_agent,
                    max_bytes=remaining,
                    deadline=per_cik_deadline,
                    label="issuer",
                )
                submissions_body = submissions.body
                stage = "ticker_confirmation"
                companyfacts_body = companyfacts.body
                submissions_tickers = _submissions_tickers(submissions_body)
                if submissions_tickers and not submissions_tickers.intersection(plan.symbols):
                    ticker_mismatches.append(plan.cik)
                    raise ValidationError("SEC market submissions ticker does not match its plan")
                # This is intentionally the first point a company write may occur.
                stage = "prewrite_deadline"
                self._remaining_timeout(deadline=per_cik_deadline, label="issuer")
                stage = "domain_call"
                receipt = self._domain_call(
                    cik=plan.cik,
                    stores=self._stores,
                    registry=self._registry,
                    submissions_body=submissions_body,
                    companyfacts_body=companyfacts_body,
                    captured_at=_utcnow(self._utcnow()),
                    deadline=per_cik_deadline,
                )
                stage = "receipt"
                succeeded.append(_receipt_token(plan.cik, receipt))
            except Exception as exc:
                # Per-issuer failures are deliberately isolated.  The aggregate
                # receipt contains only a digest, never provider bodies, CIK lists,
                # exception text, or User-Agent components.
                failed.append(plan.cik)
                kind = _failure_kind(exc)
                failure_stages[stage] = failure_stages.get(stage, 0) + 1
                failure_kinds[kind] = failure_kinds.get(kind, 0) + 1
            finally:
                submissions_body = b""
                companyfacts_body = b""

        report = SecMarketCompanyFactsReport(
            requested=self._requested,
            response_bytes=self._response_bytes,
            planned_symbol_count=len(symbols),
            matched_symbol_count=matched_symbols,
            unique_cik_count=len(plans),
            selected_cik_count=len(selected_plans),
            skipped_existing_cik_count=skipped_existing,
            succeeded_cik_count=len(succeeded),
            failed_cik_count=len(failed),
            unmatched_symbol_count=len(unmatched),
            ambiguous_symbol_count=len(ambiguous),
            ticker_mismatch_cik_count=len(ticker_mismatches),
            rejected_discovery_record_count=rejected_discovery,
            aggregate_deadline_exceeded=aggregate_deadline_exceeded,
            matched_cik_digest=_digest([plan.cik for plan in plans]),
            succeeded_cik_digest=_digest(succeeded),
            failed_cik_digest=_digest(failed),
            unmatched_symbol_digest=_digest(list(unmatched)),
            ambiguous_symbol_digest=_digest(list(ambiguous)),
            failure_stage_counts=_counts(failure_stages),
            failure_kind_counts=_counts(failure_kinds),
        )
        # Keep sanitization adjacent to the user-agent scope so accidental future
        # receipt expansion cannot leak its configured components.
        _safe_value(report.mapping(), forbidden_text=forbidden_text)
        return report


def _canonical_dependencies() -> tuple[object, StoreMap]:
    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    stores = resolve_store_map(
        registry,
        project_root=PROJECT_ROOT,
        environment={},
    )
    if (
        stores.path(StoreRole.MARKET) != MARKET_STORE.resolve(strict=False)
        or stores.path(StoreRole.COMPANY) != COMPANY_STORE.resolve(strict=False)
    ):
        raise ValidationError("SEC market canonical target is invalid")
    return registry, stores


def _domain_api() -> DomainCall:
    """Bind generic SEC publication; retain only the existing AAPL compatibility path."""

    from ..company import sec_companyfacts

    generic = getattr(sec_companyfacts, "run_sec_companyfacts", None)
    if callable(generic):
        return generic
    legacy = getattr(sec_companyfacts, "run_sec_aapl_companyfacts", None)
    if not callable(legacy):
        raise ValidationError("SEC market generic company publisher is unavailable")

    def aapl_only(**kwargs: object) -> object:
        if kwargs.get("cik") != _AAPL_CIK:
            raise ValidationError("SEC market generic company publisher is unavailable")
        return legacy(
            stores=kwargs["stores"],
            registry=kwargs["registry"],
            submissions_body=kwargs["submissions_body"],
            companyfacts_body=kwargs["companyfacts_body"],
            captured_at=kwargs["captured_at"],
            deadline=kwargs["deadline"],
        )

    return aapl_only


def _populate_sec_market_companyfacts_live(
    *, skip_ciks: frozenset[str]
) -> SecMarketCompanyFactsReport:
    """Run one fixed Stage 10 equity selection, with no retry loop."""

    registry, stores = _canonical_dependencies()
    return SecMarketCompanyFactsRunner(
        project_root=PROJECT_ROOT,
        market_store=MARKET_STORE,
        company_store=COMPANY_STORE,
        stores=stores,
        registry=registry,
        domain_call=_domain_api(),
        transport=StdlibSecMarketCompanyFactsTransport(),
        credential_environment=os.environ,
        skip_ciks=skip_ciks,
        _canonical=True,
    ).run()


def populate_sec_market_companyfacts_live() -> SecMarketCompanyFactsReport:
    """Run the full fixed Stage 10 equity population once, with no retry loop."""

    return _populate_sec_market_companyfacts_live(skip_ciks=frozenset())


def populate_sec_market_companyfacts_residual_live() -> SecMarketCompanyFactsReport:
    """Run only mapped issuers absent from the canonical company store."""

    existing_ciks = load_company_issuer_ciks(COMPANY_STORE)
    return _populate_sec_market_companyfacts_live(skip_ciks=existing_ciks)


class _ArgumentFailure(Exception):
    """Sanitized rejection for caller-selected scope."""


def _run() -> SecMarketCompanyFactsReport:
    return populate_sec_market_companyfacts_live()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixed bulk operation and emit its compact aggregate receipt."""

    try:
        if argv is not None and tuple(argv):
            raise _ArgumentFailure
        report = _run()
        safe_report = _safe_value(report.mapping(), forbidden_text=())
        if not isinstance(safe_report, dict):  # pragma: no cover - narrowing
            raise ValidationError("SEC market receipt is invalid")
    except (_ArgumentFailure, ValidationError):
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except ResourceLimitError:
        code, name = 74, "local_io"
    except (RegistryError, ConflictError):
        code, name = 75, "temporary_conflict"
    except Exception:
        code, name = 70, "internal_failure"
    else:
        sys.stdout.write(
            dumps_strict(
                {
                    "contract": "quant_data.sec_market_companyfacts_receipt",
                    "receipt": safe_report,
                    "version": _VERSION,
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        return 0 if report.complete else 1
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.sec_market_companyfacts_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))


__all__ = (
    "COMPANY_STORE",
    "MARKET_STORE",
    "MAX_PER_CIK_TOTAL_RESPONSE_BYTES",
    "MAX_RUN_SECONDS",
    "MAX_STAGE10_EQUITIES",
    "PROJECT_ROOT",
    "REQUEST_CAP",
    "SEC_COMPANYFACTS_URL_TEMPLATE",
    "SEC_SUBMISSIONS_URL_TEMPLATE",
    "SEC_TICKERS_URL",
    "SecMarketCompanyFactsReport",
    "SecMarketCompanyFactsRunner",
    "SecMarketCompanyFactsTransport",
    "SecMarketCompanyFactsTransportResponse",
    "StdlibSecMarketCompanyFactsTransport",
    "load_company_issuer_ciks",
    "load_stage10_equity_symbols",
    "plan_sec_market_issuers",
    "populate_sec_market_companyfacts_live",
    "populate_sec_market_companyfacts_residual_live",
)
