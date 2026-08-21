"""Fixed-target Stage 12E weekday market-close collection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
import fcntl
import hashlib
import http.client
import os
from pathlib import Path
import stat
import sys
import time
from typing import Final, Protocol
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from ..credentials import read_project_credential
from ..errors import ConflictError, ResourceLimitError, StoreUnavailableError, ValidationError
from ..json_codec import dumps_strict, loads_strict
from ..market.stage12_incremental import (
    Stage12BFixtureRequest,
    Stage12BFixtureResponse,
    Stage12BIncrementalCollector,
    Stage12BPublicationReceipt,
)
from ..market.stage12_scope import load_stage12_market_v1_scope
from ..market.stage12b_scope import load_stage12b_incremental_market_v1_scope


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
STATE_ROOT: Final = PROJECT_ROOT / "data" / ".stage12" / "market-v1" / "stage12e-market-close"
STAGE12C_COMPLETION: Final = (
    PROJECT_ROOT / "data" / ".stage12" / "market-v1"
    / "stage12c-20260813-20260814" / "completion.json"
)
STAGE12C_COMPLETION_SHA256: Final = (
    "0b598c7f5df93cb21104bcf6a45ae3e798a56eda7682474d59b2a81def683f88"
)
FMP_HOST: Final = "financialmodelingprep.com"
FMP_PATH: Final = "/stable/historical-price-eod/full"
TIMEZONE: Final = "America/New_York"
MARKET_CLOSE_TIME: Final = "18:00"
MINIMUM_REQUEST_INTERVAL_SECONDS: Final = 1.0
TIMEOUT_SECONDS: Final = 45
MAX_RESPONSE_BYTES: Final = 65_536
EXPECTED_SYMBOL_COUNT: Final = 619
_MODE_DIR: Final = 0o700
_MODE_FILE: Final = 0o600
_MAX_JSON_BYTES: Final = 256 * 1024
_MAX_COMPLETION_BYTES: Final = 1024 * 1024
_CONTRACT_PREFIX: Final = "quant_data.stage12e_market_close"
_VERSION: Final = "1.0.0"
_FIXTURE_CAPABILITY = object()
_CANONICAL_CAPABILITY = object()
_TERMINAL_BINDINGS: Final = (
    ("EA", "noncoverage_empty", 200),
    ("EUV", "noncoverage_empty", 200),
    ("IRBO", "noncoverage_empty", 200),
    ("^AXJO", "noncoverage_http_402_authorized", 402),
    ("^FCHI", "noncoverage_http_402_authorized", 402),
    ("^GDAXI", "noncoverage_http_402_authorized", 402),
    ("^GSPTSE", "noncoverage_http_402_authorized", 402),
    ("^KS11", "noncoverage_http_402_authorized", 402),
    ("^NDX", "noncoverage_http_402_authorized", 402),
    ("^TWII", "noncoverage_http_402_authorized", 402),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(dumps_strict(value).encode("utf-8"))


_AUTHORITY: Final = {
    "completion_receipt_sha256": STAGE12C_COMPLETION_SHA256,
    "credential": "FMP_API_KEY",
    "endpoint_path": FMP_PATH,
    "max_attempts": 1,
    "minimum_request_interval_seconds": MINIMUM_REQUEST_INTERVAL_SECONDS,
    "schedule": "Mon..Fri 18:00",
    "symbol_count": EXPECTED_SYMBOL_COUNT,
    "symbol_policy": "stage12c_published_complete_only",
    "timezone": TIMEZONE,
    "version": _VERSION,
}
STAGE12E_AUTHORITY_SHA256: Final = _sha256_json(_AUTHORITY)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Stage 12E time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _date_text(value: object) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ValidationError("Stage 12E session date is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Stage 12E session date is invalid") from exc
    if parsed.isoformat() != value:
        raise ValidationError("Stage 12E session date is invalid")
    return value


def scheduled_session_date(now: datetime) -> str:
    """Return the current completed New York weekday session."""

    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationError("Stage 12E schedule time is invalid")
    local = now.astimezone(ZoneInfo(TIMEZONE))
    if local.weekday() > 4:
        raise ValidationError("Stage 12E market-close job runs only on weekdays")
    if local.timetz().replace(tzinfo=None) < datetime_time(18, 0):
        raise ValidationError("Stage 12E market-close job cannot run before 18:00 New York time")
    return local.date().isoformat()


def _envelope(contract: str, material: Mapping[str, object]) -> dict[str, object]:
    business = dict(material)
    return {"contract": contract, **business, "sha256": _sha256_json(business)}


def _validate_envelope(value: object, *, contract: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or value.get("contract") != contract:
        raise ConflictError("Stage 12E durable sidecar contract is invalid")
    digest = value.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ConflictError("Stage 12E durable sidecar digest is invalid")
    material = {key: child for key, child in value.items() if key not in {"contract", "sha256"}}
    if _sha256_json(material) != digest:
        raise ConflictError("Stage 12E durable sidecar digest binding is invalid")
    return value


def _direct_identity(path: Path) -> tuple[int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise StoreUnavailableError("Stage 12E required path is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
        raise ConflictError("Stage 12E required file is not direct and single-linked")
    return int(info.st_dev), int(info.st_ino), int(info.st_nlink)


def _read_bytes(path: Path, *, maximum: int, private: bool = True) -> bytes:
    identity = _direct_identity(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if (
                (int(info.st_dev), int(info.st_ino), int(info.st_nlink)) != identity
                or (private and stat.S_IMODE(info.st_mode) != _MODE_FILE)
                or int(info.st_size) > maximum
            ):
                raise ConflictError("Stage 12E durable file identity or mode is invalid")
            chunks: list[bytes] = []
            remaining = int(info.st_size)
            while remaining:
                block = os.read(descriptor, min(remaining, 64 * 1024))
                if not block:
                    raise ConflictError("Stage 12E durable file is truncated")
                chunks.append(block)
                remaining -= len(block)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except ConflictError:
        raise
    except OSError as exc:
        raise StoreUnavailableError("Stage 12E durable file cannot be read") from exc


def _read_json(path: Path, *, maximum: int = _MAX_JSON_BYTES) -> Mapping[str, object]:
    value = loads_strict(_read_bytes(path, maximum=maximum), max_bytes=maximum)
    if not isinstance(value, Mapping):
        raise ConflictError("Stage 12E durable JSON must be an object")
    return value


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=_MODE_DIR)
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise ConflictError("Stage 12E private directory cannot be an alias")
        info = path.stat()
    except ConflictError:
        raise
    except (OSError, RuntimeError) as exc:
        raise StoreUnavailableError("Stage 12E private directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != _MODE_DIR:
        raise ConflictError("Stage 12E private directory mode is invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, _MODE_FILE)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short Stage 12E sidecar write")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode) != _MODE_FILE or info.st_nlink != 1:
            raise ConflictError("Stage 12E sidecar mode or identity is invalid")
    except (ConflictError, FileExistsError):
        raise
    except OSError as exc:
        raise StoreUnavailableError("Stage 12E sidecar cannot be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _fsync_directory(path.parent)


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _write_exclusive(path, dumps_strict(dict(value)).encode("utf-8"))


@contextmanager
def _held_run_lock(root: Path):
    path = root / "run.lock"
    flags = (
        os.O_RDWR | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, _MODE_FILE)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != _MODE_FILE
        ):
            raise ConflictError("Stage 12E run lock is invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ConflictError("Stage 12E market-close run is already active") from exc
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    try:
        yield
    finally:
        assert descriptor is not None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class Stage12ETransportResponse:
    status: int
    media_type: str
    body: bytes
    redirected: bool = False


class Stage12ETransport(Protocol):
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12ETransportResponse: ...


class _StdlibTransport:
    def get(
        self,
        *,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_bytes: int,
    ) -> Stage12ETransportResponse:
        if path != FMP_PATH or set(query) != {"symbol", "from", "to"} or set(headers) != {"apikey"}:
            raise ValidationError("Stage 12E provider request is invalid")
        target = f"{quote(path, safe='/')}?{urlencode(dict(query))}"
        connection = http.client.HTTPSConnection(FMP_HOST, timeout=timeout_seconds)
        try:
            connection.request("GET", target, headers=dict(headers))
            response = connection.getresponse()
            declared_text = response.getheader("Content-Length")
            if declared_text is not None:
                try:
                    declared = int(declared_text)
                except ValueError as exc:
                    raise StoreUnavailableError("Stage 12E provider length is invalid") from exc
                if declared < 0 or declared > max_bytes:
                    raise ResourceLimitError("Stage 12E provider response exceeds its byte bound")
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResourceLimitError("Stage 12E provider response exceeds its byte bound")
            return Stage12ETransportResponse(
                status=int(response.status),
                media_type=response.getheader("Content-Type") or "",
                body=body,
                redirected=(
                    300 <= int(response.status) < 400
                    or response.getheader("Location") is not None
                ),
            )
        except (ResourceLimitError, StoreUnavailableError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise StoreUnavailableError("Stage 12E provider transport failed") from exc
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class _Unit:
    ordinal: int
    symbol: str
    identifier: str


@dataclass(frozen=True, slots=True)
class Stage12EMarketCloseReport:
    session_date: str
    plan_sha256: str
    authority_sha256: str
    total_units: int
    published: int
    unchanged: int
    terminal_noncoverage: int
    requests_issued: int
    outcome: str
    receipt_sha256: str

    def mapping(self) -> dict[str, object]:
        return {
            "authority_sha256": self.authority_sha256,
            "outcome": self.outcome,
            "plan_sha256": self.plan_sha256,
            "published": self.published,
            "receipt_sha256": self.receipt_sha256,
            "requests_issued": self.requests_issued,
            "session_date": self.session_date,
            "terminal_noncoverage": self.terminal_noncoverage,
            "total_units": self.total_units,
            "unchanged": self.unchanged,
        }


class _Pacer:
    def __init__(self, monotonic: Callable[[], float], sleeper: Callable[[float], None]) -> None:
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_start: float | None = None

    def arm_after_recovery(self) -> None:
        self._last_start = self._monotonic()

    def wait(self) -> None:
        now = self._monotonic()
        if self._last_start is not None:
            remaining = MINIMUM_REQUEST_INTERVAL_SECONDS - (now - self._last_start)
            if remaining > 0:
                self._sleeper(remaining)
        self._last_start = self._monotonic()


def _unit_paths(root: Path, unit: _Unit) -> dict[str, Path]:
    stem = f"{unit.ordinal:03d}-{unit.identifier}"
    journal = root / "journal"
    return {
        "intent": journal / f"{stem}.intent.json",
        "body": journal / f"{stem}.response.bin",
        "response": journal / f"{stem}.response.json",
        "result": journal / f"{stem}.result.json",
    }


def _canonical_symbols() -> tuple[str, ...]:
    receipt = _validate_envelope(
        _read_json(STAGE12C_COMPLETION, maximum=_MAX_COMPLETION_BYTES),
        contract="quant_data.stage12c_market_gap_completion",
    )
    if (
        receipt.get("sha256") != STAGE12C_COMPLETION_SHA256
        or receipt.get("published_unit_count") != 619
        or receipt.get("terminal_noncoverage_count") != 10
    ):
        raise ConflictError("Stage 12E Stage 12C completion binding is invalid")
    terminal = receipt.get("terminal_outcomes")
    if not isinstance(terminal, list):
        raise ConflictError("Stage 12E Stage 12C terminal ledger is invalid")
    observed = tuple(
        (item.get("symbol"), item.get("outcome"), item.get("http_status"))
        for item in terminal
        if isinstance(item, Mapping)
    )
    if observed != _TERMINAL_BINDINGS:
        raise ConflictError("Stage 12E Stage 12C terminal ledger changed")
    stage12a = load_stage12_market_v1_scope(
        PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
    )
    excluded = {symbol for symbol, _, _ in _TERMINAL_BINDINGS}
    roster = tuple(item.symbol for item in stage12a.roster if item.symbol not in excluded)
    symbols = ("AAPL", *tuple(sorted(symbol for symbol in roster if symbol != "AAPL")))
    if len(symbols) != EXPECTED_SYMBOL_COUNT or len(set(symbols)) != len(symbols):
        raise ConflictError("Stage 12E covered roster is invalid")
    return symbols


class _Runner:
    def __init__(
        self,
        *,
        project_root: Path,
        market_store: Path,
        state_root: Path,
        session_date: str,
        symbols: tuple[str, ...],
        collector: Stage12BIncrementalCollector,
        transport: Stage12ETransport,
        environment: Mapping[str, str],
        authority_sha256: str,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], None],
        utcnow: Callable[[], datetime],
        capability: object,
    ) -> None:
        if capability not in {_FIXTURE_CAPABILITY, _CANONICAL_CAPABILITY}:
            raise ValidationError("Stage 12E runner capability is invalid")
        self._project = project_root
        self._target = market_store
        self._state_root = state_root
        self._session = _date_text(session_date)
        self._symbols = symbols
        self._collector = collector
        self._transport = transport
        self._environment = environment
        self._authority = authority_sha256
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._utcnow = utcnow
        if (
            not symbols
            or symbols[0] != "AAPL"
            or len(set(symbols)) != len(symbols)
            or any(
                not isinstance(symbol, str)
                or not symbol
                or symbol != symbol.upper()
                or any(character.isspace() for character in symbol)
                for symbol in symbols
            )
        ):
            raise ValidationError("Stage 12E symbol plan is invalid")
        if collector.market_store != market_store:
            raise ValidationError("Stage 12E collector target binding is invalid")
        try:
            if project_root.is_symlink() or market_store.is_symlink():
                raise ValidationError("Stage 12E paths cannot be aliases")
            if (
                project_root.resolve(strict=True) != project_root
                or market_store.resolve(strict=True) != market_store
            ):
                raise ValidationError("Stage 12E paths cannot be aliases")
            if market_store != project_root / "data" / "market.sqlite":
                raise ValidationError("Stage 12E target must be project_root/data/market.sqlite")
            target_info = market_store.stat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise StoreUnavailableError("Stage 12E market target is unavailable") from exc
        if not stat.S_ISREG(target_info.st_mode) or target_info.st_nlink != 1:
            raise ConflictError("Stage 12E market target identity is invalid")
        self._target_identity = (
            int(target_info.st_dev), int(target_info.st_ino), int(target_info.st_nlink)
        )
        if capability is _FIXTURE_CAPABILITY:
            try:
                project_root.relative_to(Path("/tmp").resolve(strict=True))
                state_root.relative_to(project_root)
            except ValueError as exc:
                raise ValidationError("Stage 12E fixtures must remain below /tmp") from exc
        elif (
            project_root != PROJECT_ROOT
            or market_store != MARKET_STORE
            or state_root != STATE_ROOT
            or len(symbols) != EXPECTED_SYMBOL_COUNT
            or authority_sha256 != STAGE12E_AUTHORITY_SHA256
        ):
            raise ValidationError("Stage 12E canonical runner binding is invalid")

    def _plan(self) -> tuple[tuple[_Unit, ...], Mapping[str, object]]:
        units = tuple(
            _Unit(
                ordinal=index,
                symbol=symbol,
                identifier="stage12e-" + hashlib.sha256(
                    f"{self._authority}\\0{self._session}\\0{index}\\0{symbol}".encode()
                ).hexdigest()[:32],
            )
            for index, symbol in enumerate(self._symbols, start=1)
        )
        material = {
            "authority_sha256": self._authority,
            "session_date": self._session,
            "units": [
                {"identifier": unit.identifier, "ordinal": unit.ordinal, "symbol": unit.symbol}
                for unit in units
            ],
            "version": _VERSION,
        }
        return units, _envelope(f"{_CONTRACT_PREFIX}_plan", material)

    def _validate_target(self) -> None:
        if _direct_identity(self._target) != self._target_identity:
            raise ConflictError("Stage 12E market target changed during the run")

    def _load_state(
        self, root: Path, unit: _Unit, plan_sha256: str
    ) -> tuple[Mapping[str, object] | None, bytes | None, Mapping[str, object] | None]:
        paths = _unit_paths(root, unit)
        present = {name: path.exists() or path.is_symlink() for name, path in paths.items()}
        if present["result"] and not all(present.values()):
            raise ConflictError("Stage 12E result lacks its durable request chain")
        if (present["body"] or present["response"]) and not present["intent"]:
            raise ConflictError("Stage 12E response lacks its durable intent")
        if present["body"] != present["response"]:
            raise ConflictError("Stage 12E response spool is incomplete")
        if present["intent"] and not present["response"]:
            raise ConflictError("Stage 12E has an ambiguous issued request; retry is forbidden")
        if not present["intent"]:
            return None, None, None
        intent = _validate_envelope(
            _read_json(paths["intent"]), contract=f"{_CONTRACT_PREFIX}_intent"
        )
        for key, expected in (
            ("identifier", unit.identifier),
            ("ordinal", unit.ordinal),
            ("plan_sha256", plan_sha256),
            ("session_date", self._session),
            ("symbol", unit.symbol),
        ):
            if intent.get(key) != expected:
                raise ConflictError("Stage 12E intent binding is invalid")
        body = _read_bytes(paths["body"], maximum=MAX_RESPONSE_BYTES)
        response = _validate_envelope(
            _read_json(paths["response"]), contract=f"{_CONTRACT_PREFIX}_response"
        )
        if (
            response.get("identifier") != unit.identifier
            or response.get("intent_sha256") != intent.get("sha256")
            or response.get("body_sha256") != _sha256_bytes(body)
            or response.get("body_bytes") != len(body)
        ):
            raise ConflictError("Stage 12E response spool binding is invalid")
        if not present["result"]:
            return intent, body, response
        result = _validate_envelope(
            _read_json(paths["result"]), contract=f"{_CONTRACT_PREFIX}_result"
        )
        if (
            result.get("identifier") != unit.identifier
            or result.get("response_sha256") != response.get("sha256")
            or result.get("symbol") != unit.symbol
        ):
            raise ConflictError("Stage 12E result binding is invalid")
        return result, body, response

    def _write_intent(self, root: Path, unit: _Unit, plan_sha256: str) -> Mapping[str, object]:
        intent = _envelope(
            f"{_CONTRACT_PREFIX}_intent",
            {
                "identifier": unit.identifier,
                "issued_at": _utc_text(self._utcnow()),
                "ordinal": unit.ordinal,
                "plan_sha256": plan_sha256,
                "session_date": self._session,
                "symbol": unit.symbol,
                "version": _VERSION,
            },
        )
        _write_json(_unit_paths(root, unit)["intent"], intent)
        return intent

    def _issue(
        self,
        root: Path,
        unit: _Unit,
        plan_sha256: str,
        api_key: str,
        pacer: _Pacer,
    ) -> tuple[bytes, Mapping[str, object]]:
        intent = self._write_intent(root, unit, plan_sha256)
        pacer.wait()
        started = self._monotonic()
        response = self._transport.get(
            path=FMP_PATH,
            query={"symbol": unit.symbol, "from": self._session, "to": self._session},
            headers={"apikey": api_key},
            timeout_seconds=TIMEOUT_SECONDS,
            max_bytes=MAX_RESPONSE_BYTES,
        )
        elapsed = self._monotonic() - started
        if elapsed < 0:
            raise ConflictError("Stage 12E monotonic clock moved backwards")
        paths = _unit_paths(root, unit)
        _write_exclusive(paths["body"], response.body)
        spool = _envelope(
            f"{_CONTRACT_PREFIX}_response",
            {
                "body_bytes": len(response.body),
                "body_sha256": _sha256_bytes(response.body),
                "captured_at": _utc_text(self._utcnow()),
                "elapsed_seconds": format(elapsed, ".6f"),
                "identifier": unit.identifier,
                "intent_sha256": intent["sha256"],
                "media_type": response.media_type,
                "redirected": response.redirected,
                "status": response.status,
                "version": _VERSION,
            },
        )
        _write_json(paths["response"], spool)
        return response.body, spool

    @staticmethod
    def _error_envelope(body: bytes) -> None:
        value = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
        if (
            not isinstance(value, Mapping)
            or set(value) != {"Error Message"}
            or not isinstance(value["Error Message"], str)
            or not value["Error Message"].strip()
            or len(value["Error Message"]) > 4096
        ):
            raise ValidationError("Stage 12E provider error envelope is invalid")

    def _publish_result(
        self, root: Path, unit: _Unit, body: bytes, spool: Mapping[str, object]
    ) -> Mapping[str, object]:
        status = spool.get("status")
        media_type = spool.get("media_type")
        if spool.get("redirected") is not False:
            raise StoreUnavailableError("Stage 12E provider redirect is not allowed")
        if (
            not isinstance(media_type, str)
            or media_type.split(";", 1)[0].strip().casefold() != "application/json"
        ):
            raise StoreUnavailableError("Stage 12E provider media type is invalid")
        if isinstance(status, bool) or not isinstance(status, int):
            raise ConflictError("Stage 12E provider status is invalid")
        publication: dict[str, object] | None = None
        if status == 200:
            parsed = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
            if parsed == []:
                outcome = "no_market_session" if unit.ordinal == 1 else "terminal_empty"
            else:
                prepared = self._collector.prepare(
                    Stage12BFixtureRequest(
                        symbol=unit.symbol,
                        from_date=self._session,
                        to_date=self._session,
                        session_dates=(self._session,),
                        captured_at=str(spool["captured_at"]),
                    ),
                    Stage12BFixtureResponse(
                        status=status,
                        media_type=media_type,
                        body=body,
                        elapsed_seconds=float(str(spool["elapsed_seconds"])),
                    ),
                )
                receipt: Stage12BPublicationReceipt = self._collector.publish(prepared)
                if receipt.outcome not in {"published", "unchanged"}:
                    raise ConflictError("Stage 12E publication outcome is invalid")
                outcome = receipt.outcome
                publication = {
                    "capture_id": receipt.capture_id,
                    "semantic_identity": receipt.semantic_identity,
                    "written_versions": receipt.written_versions,
                }
        elif status in {404, 410, 422} and unit.ordinal != 1:
            self._error_envelope(body)
            outcome = "terminal_noncoverage"
        else:
            raise StoreUnavailableError("Stage 12E provider response is outside policy")
        result = _envelope(
            f"{_CONTRACT_PREFIX}_result",
            {
                "http_status": status,
                "identifier": unit.identifier,
                "outcome": outcome,
                "publication": publication,
                "response_sha256": spool["sha256"],
                "session_date": self._session,
                "symbol": unit.symbol,
                "version": _VERSION,
            },
        )
        _write_json(_unit_paths(root, unit)["result"], result)
        return result

    def _complete(
        self,
        root: Path,
        plan: Mapping[str, object],
        units: tuple[_Unit, ...],
        results: Mapping[str, Mapping[str, object]],
    ) -> Mapping[str, object]:
        self._validate_target()
        sentinel = results.get(units[0].identifier)
        if sentinel is None:
            raise ConflictError("Stage 12E cannot complete without AAPL")
        no_session = sentinel.get("outcome") == "no_market_session"
        if not no_session and len(results) != len(units):
            raise ConflictError("Stage 12E cannot complete before all units close")
        outcomes = [str(result["outcome"]) for result in results.values()]
        receipt = _envelope(
            f"{_CONTRACT_PREFIX}_completion",
            {
                "authority_sha256": self._authority,
                "outcome": "no_market_session" if no_session else "complete",
                "plan_sha256": plan["sha256"],
                "published": outcomes.count("published"),
                "result_sha256s": [
                    str(results[unit.identifier]["sha256"])
                    for unit in units if unit.identifier in results
                ],
                "session_date": self._session,
                "stage12c_completion_sha256": STAGE12C_COMPLETION_SHA256,
                "target_identity": {
                    "device": self._target_identity[0],
                    "inode": self._target_identity[1],
                    "nlink": self._target_identity[2],
                },
                "terminal_noncoverage": (
                    outcomes.count("terminal_empty")
                    + outcomes.count("terminal_noncoverage")
                ),
                "total_units": len(units),
                "unchanged": outcomes.count("unchanged"),
                "version": _VERSION,
            },
        )
        completion_path = root / "completion.json"
        if completion_path.exists() or completion_path.is_symlink():
            existing = _validate_envelope(
                _read_json(completion_path, maximum=_MAX_COMPLETION_BYTES),
                contract=f"{_CONTRACT_PREFIX}_completion",
            )
            if existing != receipt:
                raise ConflictError("Stage 12E completion receipt changed")
            return existing
        _write_json(completion_path, receipt)
        self._validate_target()
        return receipt

    @staticmethod
    def _report(
        receipt: Mapping[str, object], requests_issued: int
    ) -> Stage12EMarketCloseReport:
        return Stage12EMarketCloseReport(
            session_date=str(receipt["session_date"]),
            plan_sha256=str(receipt["plan_sha256"]),
            authority_sha256=str(receipt["authority_sha256"]),
            total_units=int(receipt["total_units"]),
            published=int(receipt["published"]),
            unchanged=int(receipt["unchanged"]),
            terminal_noncoverage=int(receipt["terminal_noncoverage"]),
            requests_issued=requests_issued,
            outcome=str(receipt["outcome"]),
            receipt_sha256=str(receipt["sha256"]),
        )

    def run(self) -> Stage12EMarketCloseReport:
        units, plan = self._plan()
        session_root = self._state_root / self._session
        _ensure_private_directory(session_root)
        _ensure_private_directory(session_root / "journal")
        with _held_run_lock(session_root):
            plan_path = session_root / "plan.json"
            if plan_path.exists() or plan_path.is_symlink():
                existing_plan = _validate_envelope(
                    _read_json(plan_path), contract=f"{_CONTRACT_PREFIX}_plan"
                )
                if existing_plan != plan:
                    raise ConflictError("Stage 12E durable plan changed")
            else:
                _write_json(plan_path, plan)
            completion_path = session_root / "completion.json"
            if completion_path.exists() or completion_path.is_symlink():
                self._validate_target()
                receipt = _validate_envelope(
                    _read_json(completion_path, maximum=_MAX_COMPLETION_BYTES),
                    contract=f"{_CONTRACT_PREFIX}_completion",
                )
                return self._report(receipt, 0)

            results: dict[str, Mapping[str, object]] = {}
            recovered_spool = False
            for unit in units:
                state, body, spool = self._load_state(
                    session_root, unit, str(plan["sha256"])
                )
                if state is not None and state.get("contract") == f"{_CONTRACT_PREFIX}_result":
                    results[unit.identifier] = state
                elif state is not None and body is not None and spool is not None:
                    result = self._publish_result(session_root, unit, body, spool)
                    results[unit.identifier] = result
                    recovered_spool = True
                if (
                    unit.ordinal == 1
                    and unit.identifier in results
                    and results[unit.identifier].get("outcome") == "no_market_session"
                ):
                    receipt = self._complete(session_root, plan, units, results)
                    return self._report(receipt, 0)

            missing = tuple(unit for unit in units if unit.identifier not in results)
            if not missing:
                return self._report(
                    self._complete(session_root, plan, units, results), 0
                )
            api_key = read_project_credential(
                project_root=self._project,
                name="FMP_API_KEY",
                environment=self._environment,
            )
            if not api_key or any(character.isspace() for character in api_key):
                raise ValidationError("Credential is missing or invalid")
            pacer = _Pacer(self._monotonic, self._sleeper)
            if recovered_spool:
                pacer.arm_after_recovery()
            issued = 0
            for unit in missing:
                body, spool = self._issue(
                    session_root, unit, str(plan["sha256"]), api_key, pacer
                )
                issued += 1
                result = self._publish_result(session_root, unit, body, spool)
                results[unit.identifier] = result
                if unit.ordinal == 1:
                    if result.get("outcome") == "no_market_session":
                        return self._report(
                            self._complete(session_root, plan, units, results), issued
                        )
                    if result.get("outcome") not in {"published", "unchanged"}:
                        raise StoreUnavailableError("Stage 12E AAPL sentinel did not publish")
            return self._report(
                self._complete(session_root, plan, units, results), issued
            )

    @classmethod
    def _for_canonical_live(cls) -> "_Runner":
        if cls is not _Runner:
            raise ValidationError("Stage 12E canonical runner cannot be subclassed")
        stage12a = load_stage12_market_v1_scope(
            PROJECT_ROOT / "config" / "stage12_market_v1_scope.json"
        )
        stage12b = load_stage12b_incremental_market_v1_scope(
            PROJECT_ROOT / "config" / "stage12b_incremental_market_v1_scope.json"
        )
        collector = Stage12BIncrementalCollector._for_canonical_market_close(
            scope=stage12b,
            stage12a_scope=stage12a,
            schedule_authority_sha256=STAGE12E_AUTHORITY_SHA256,
        )
        return cls(
            project_root=PROJECT_ROOT,
            market_store=MARKET_STORE,
            state_root=STATE_ROOT,
            session_date=scheduled_session_date(datetime.now(timezone.utc)),
            symbols=_canonical_symbols(),
            collector=collector,
            transport=_StdlibTransport(),
            environment=os.environ,
            authority_sha256=STAGE12E_AUTHORITY_SHA256,
            monotonic=time.monotonic,
            sleeper=time.sleep,
            utcnow=lambda: datetime.now(timezone.utc),
            capability=_CANONICAL_CAPABILITY,
        )


class Stage12EMarketCloseRunner(_Runner):
    """Fixture-only injectable runner for focused offline tests."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        market_store: str | Path,
        state_root: str | Path,
        session_date: str,
        symbols: tuple[str, ...],
        collector: Stage12BIncrementalCollector,
        transport: Stage12ETransport,
        environment: Mapping[str, str],
        authority_sha256: str = "f" * 64,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        super().__init__(
            project_root=Path(project_root),
            market_store=Path(market_store),
            state_root=Path(state_root),
            session_date=session_date,
            symbols=symbols,
            collector=collector,
            transport=transport,
            environment=environment,
            authority_sha256=authority_sha256,
            monotonic=monotonic,
            sleeper=sleeper,
            utcnow=utcnow,
            capability=_FIXTURE_CAPABILITY,
        )


def run_stage12e_market_close_live() -> Stage12EMarketCloseReport:
    """Run the sole fixed-target scheduled market-close cycle."""

    return _Runner._for_canonical_live().run()


def main() -> int:
    try:
        report = run_stage12e_market_close_live()
        sys.stdout.write(dumps_strict(report.mapping()) + "\\n")
        sys.stdout.flush()
        return 0
    except ValidationError:
        code, name = 64, "invalid_request"
    except StoreUnavailableError:
        code, name = 69, "store_unavailable"
    except ConflictError:
        code, name = 75, "temporary_conflict"
    except (OSError, ResourceLimitError):
        code, name = 74, "local_io"
    except Exception:
        code, name = 70, "internal_failure"
    sys.stderr.write(
        dumps_strict(
            {
                "contract": f"{_CONTRACT_PREFIX}_error",
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\\n"
    )
    sys.stderr.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "EXPECTED_SYMBOL_COUNT",
    "MARKET_CLOSE_TIME",
    "MINIMUM_REQUEST_INTERVAL_SECONDS",
    "STAGE12E_AUTHORITY_SHA256",
    "Stage12EMarketCloseReport",
    "Stage12EMarketCloseRunner",
    "Stage12ETransport",
    "Stage12ETransportResponse",
    "TIMEZONE",
    "run_stage12e_market_close_live",
    "scheduled_session_date",
)
