"""Fixed one-shot operation for the missing pre-2021 IWM daily history."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
import stat
import sys
from typing import Final

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict, loads_strict
from ..market.fmp_bulk_daily_prices import FmpPriceHistoryUnavailable
from ..market.fmp_iwm_etf_history import load_iwm_etf_history_scope
from ..market.fmp_iwm_etf_history_backfill import (
    IWM_ETF_HISTORY_BACKFILL_END_DATE,
    IWM_ETF_HISTORY_BACKFILL_START_DATE,
    IWM_ETF_HISTORY_PRIOR_COMPLETION,
    IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT,
    IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256,
    IwmEtfHistoryBackfillPublisher,
    StdlibFmpIwmEtfHistoryBackfillTransport,
    load_iwm_etf_history_backfill_scope,
    run_iwm_etf_history_backfill,
)
from ..market.stage10_scope import load_stage10_market_scope
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stores import StoreMap, StoreRole, resolve_store_map


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
_VERSION: Final = "1.0.0"
_RECEIPT_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_backfill_receipt"
_ERROR_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_backfill_error"
_COMPLETION_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_backfill_completion"
_COMPLETION_DIRECTORY_NAME: Final = "fmp-iwm-etf-history-backfill-v1"
_COMPLETION_FILE_NAME: Final = "completion.json"
_PRIOR_COMPLETION_DIRECTORY_NAME: Final = "fmp-iwm-etf-history-v1"
_PRIOR_COMPLETION_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_completion"
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_PRICE_ROWS: Final = 30_000
_MAX_WRITTEN_COUNT: Final = 30_500
_MAX_COMPLETION_BYTES: Final = 4096
_REPORT_FIELDS: Final = (
    "outcome",
    "backfill_scope_manifest_sha256",
    "iwm_scope_manifest_sha256",
    "normalized_complete_backfill_sha256",
    "response_sha256",
    "earliest_trade_date",
    "latest_trade_date",
    "price_row_count",
    "written_count",
)


def _completion_directory() -> Path:
    return PROJECT_ROOT / "data" / ".operations" / _COMPLETION_DIRECTORY_NAME


def _completion_path() -> Path:
    return _completion_directory() / _COMPLETION_FILE_NAME


def _prior_completion_path() -> Path:
    return (
        PROJECT_ROOT
        / "data"
        / ".operations"
        / _PRIOR_COMPLETION_DIRECTORY_NAME
        / _COMPLETION_FILE_NAME
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ResourceLimitError("IWM backfill completion state is unavailable") from exc


def _require_directory(path: Path, *, private: bool) -> os.stat_result:
    details = _lstat(path)
    if details is None or stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ConflictError("IWM backfill completion state conflicts")
    if private and stat.S_IMODE(details.st_mode) != 0o700:
        raise ConflictError("IWM backfill completion state conflicts")
    return details


def _completion_file_exists(path: Path) -> bool:
    details = _lstat(path)
    if details is None:
        return False
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ConflictError("IWM backfill completion receipt conflicts")
    return True


def _read_private_file(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ConflictError("IWM prior completion receipt is unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size < 1
            or details.st_size > _MAX_COMPLETION_BYTES
        ):
            raise ConflictError("IWM prior completion receipt conflicts")
        chunks: list[bytes] = []
        remaining = _MAX_COMPLETION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != details.st_size or len(payload) > _MAX_COMPLETION_BYTES:
            raise ConflictError("IWM prior completion receipt conflicts")
        return payload
    except OSError as exc:
        raise ConflictError("IWM prior completion receipt is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _prior_completion_preflight() -> None:
    path = _prior_completion_path()
    _require_directory(path.parent.parent, private=False)
    _require_directory(path.parent, private=True)
    _completion_file_exists(path)
    raw = loads_strict(_read_private_file(path), max_bytes=_MAX_COMPLETION_BYTES)
    expected = {
        "base_scope_manifest_sha256": IWM_ETF_HISTORY_PRIOR_COMPLETION[
            "base_scope_manifest_sha256"
        ],
        "contract": _PRIOR_COMPLETION_CONTRACT,
        "normalized_complete_history_sha256": IWM_ETF_HISTORY_PRIOR_COMPLETION[
            "normalized_complete_history_sha256"
        ],
        "outcome": "succeeded",
        "price_row_count": IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT,
        "response_sha256": IWM_ETF_HISTORY_PRIOR_COMPLETION["response_sha256"],
        "scope_manifest_sha256": IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256,
        "successor_member_count": 96,
        "successor_membership_sha256": IWM_ETF_HISTORY_PRIOR_COMPLETION[
            "successor_membership_sha256"
        ],
        "version": "1.0.0",
        "written_count": 1_354,
    }
    if not isinstance(raw, Mapping) or dumps_strict(raw) != dumps_strict(expected):
        raise ConflictError("IWM prior completion receipt conflicts")


def _completion_preflight() -> None:
    state = _completion_directory()
    parent = state.parent
    if _lstat(parent) is not None:
        _require_directory(parent, private=False)
    if _lstat(state) is None:
        return
    _require_directory(state, private=True)
    _completion_file_exists(state / _COMPLETION_FILE_NAME)
    raise ConflictError("IWM backfill attempt is already reserved or completed")


def _fsync_directory(path: Path) -> None:
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        descriptor = os.open(os.fspath(path), flags)
    except (AttributeError, OSError) as exc:
        raise ResourceLimitError("IWM backfill completion state is unavailable") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ResourceLimitError("IWM backfill completion state is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _ensure_operations_directory() -> Path:
    parent = _completion_directory().parent
    created = False
    if _lstat(parent) is None:
        try:
            os.mkdir(os.fspath(parent), 0o700)
            created = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise ResourceLimitError("IWM backfill completion state is unavailable") from exc
    _require_directory(parent, private=False)
    if created:
        _fsync_directory(parent.parent)
    return parent


def _reserve_attempt() -> Path:
    _completion_preflight()
    parent = _ensure_operations_directory()
    state = _completion_directory()
    try:
        os.mkdir(os.fspath(state), 0o700)
        os.chmod(os.fspath(state), 0o700)
    except FileExistsError as exc:
        raise ConflictError("IWM backfill attempt is already reserved or completed") from exc
    except OSError as exc:
        raise ResourceLimitError("IWM backfill completion state is unavailable") from exc
    _require_directory(state, private=True)
    _fsync_directory(state)
    _fsync_directory(parent)
    return state


def _require_reserved_state() -> Path:
    state = _completion_directory()
    _require_directory(state.parent, private=False)
    _require_directory(state, private=True)
    if _completion_file_exists(state / _COMPLETION_FILE_NAME):
        raise ConflictError("IWM backfill is already completed")
    return state


def _safe_digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError("IWM backfill receipt is invalid")
    return value


def _safe_count(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValidationError("IWM backfill receipt is invalid")
    return value


def _safe_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("IWM backfill receipt is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("IWM backfill receipt is invalid") from exc
    if parsed.isoformat() != value:
        raise ValidationError("IWM backfill receipt is invalid")
    return value


def _receipt_mapping(value: object) -> dict[str, object]:
    mapping_method = getattr(value, "mapping", None)
    candidate = mapping_method() if callable(mapping_method) else value
    if not isinstance(candidate, Mapping) or set(candidate) != set(_REPORT_FIELDS):
        raise ValidationError("IWM backfill receipt is invalid")
    outcome = candidate.get("outcome")
    if outcome not in {"succeeded", "unchanged"}:
        raise ValidationError("IWM backfill receipt is invalid")
    earliest = _safe_date(candidate.get("earliest_trade_date"))
    latest = _safe_date(candidate.get("latest_trade_date"))
    if (
        not IWM_ETF_HISTORY_BACKFILL_START_DATE
        <= earliest
        <= latest
        <= IWM_ETF_HISTORY_BACKFILL_END_DATE
    ):
        raise ValidationError("IWM backfill receipt is invalid")
    row_count = _safe_count(candidate.get("price_row_count"), maximum=_MAX_PRICE_ROWS)
    if row_count < 1:
        raise ValidationError("IWM backfill receipt is invalid")
    written_count = _safe_count(candidate.get("written_count"), maximum=_MAX_WRITTEN_COUNT)
    if outcome == "unchanged" and written_count != 0:
        raise ValidationError("IWM backfill receipt is invalid")
    return {
        "outcome": outcome,
        "backfill_scope_manifest_sha256": _safe_digest(
            candidate.get("backfill_scope_manifest_sha256")
        ),
        "iwm_scope_manifest_sha256": _safe_digest(
            candidate.get("iwm_scope_manifest_sha256")
        ),
        "normalized_complete_backfill_sha256": _safe_digest(
            candidate.get("normalized_complete_backfill_sha256")
        ),
        "response_sha256": _safe_digest(candidate.get("response_sha256")),
        "earliest_trade_date": earliest,
        "latest_trade_date": latest,
        "price_row_count": row_count,
        "written_count": written_count,
    }


def _completion_payload(receipt: Mapping[str, object]) -> bytes:
    payload = {
        "backfill_scope_manifest_sha256": receipt[
            "backfill_scope_manifest_sha256"
        ],
        "contract": _COMPLETION_CONTRACT,
        "earliest_trade_date": receipt["earliest_trade_date"],
        "iwm_scope_manifest_sha256": receipt["iwm_scope_manifest_sha256"],
        "latest_trade_date": receipt["latest_trade_date"],
        "normalized_complete_backfill_sha256": receipt[
            "normalized_complete_backfill_sha256"
        ],
        "outcome": receipt["outcome"],
        "price_row_count": receipt["price_row_count"],
        "response_sha256": receipt["response_sha256"],
        "version": _VERSION,
        "written_count": receipt["written_count"],
    }
    encoded = (dumps_strict(payload) + "\n").encode("utf-8")
    if not encoded or len(encoded) > _MAX_COMPLETION_BYTES:
        raise ValidationError("IWM backfill completion receipt is invalid")
    return encoded


def _write_all(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        try:
            written = os.write(descriptor, remaining)
        except OSError as exc:
            raise ResourceLimitError("IWM backfill completion receipt is unavailable") from exc
        if written <= 0:
            raise ResourceLimitError("IWM backfill completion receipt is unavailable")
        remaining = remaining[written:]


def _write_completion(receipt: Mapping[str, object]) -> None:
    state = _require_reserved_state()
    encoded = _completion_payload(receipt)
    path = state / _COMPLETION_FILE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(os.fspath(path), flags, 0o600)
    except FileExistsError as exc:
        raise ConflictError("IWM backfill is already completed") from exc
    except OSError as exc:
        raise ResourceLimitError("IWM backfill completion receipt is unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ResourceLimitError("IWM backfill completion receipt is unavailable")
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise ResourceLimitError("IWM backfill completion receipt is unavailable")
    except OSError as exc:
        raise ResourceLimitError("IWM backfill completion receipt is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    _fsync_directory(state)


def _canonical_dependencies() -> tuple[Registry, StoreMap]:
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
    if stores.path(StoreRole.MARKET) != MARKET_STORE.resolve(strict=False):
        raise ValidationError("IWM backfill canonical market target is invalid")
    return registry, stores


def _scope() -> object:
    base_scope = load_stage10_market_scope(PROJECT_ROOT / "config" / "stage10_market_scope.json")
    iwm_scope = load_iwm_etf_history_scope(
        PROJECT_ROOT / "config" / "iwm_etf_history_v1_scope.json",
        base_scope=base_scope,
    )
    return load_iwm_etf_history_backfill_scope(
        PROJECT_ROOT / "config" / "iwm_etf_history_backfill_v1_scope.json",
        iwm_scope=iwm_scope,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run() -> dict[str, object]:
    """Run the exact one-request backfill after prior-receipt and store preflight."""

    _completion_preflight()
    _prior_completion_preflight()
    registry, stores = _canonical_dependencies()
    scope = _scope()
    IwmEtfHistoryBackfillPublisher(stores, registry, scope).preflight_store()
    api_key = read_project_credential(
        project_root=PROJECT_ROOT,
        name="FMP_API_KEY",
        environment=os.environ,
    )
    _reserve_attempt()
    report = run_iwm_etf_history_backfill(
        store_map=stores,
        registry=registry,
        scope=scope,
        transport=StdlibFmpIwmEtfHistoryBackfillTransport(),
        api_key=api_key,
        captured_at=_utc_now(),
    )
    receipt = _receipt_mapping(report)
    _write_completion(receipt)
    return receipt


def _error(name: str, code: int) -> int:
    sys.stderr.write(
        dumps_strict(
            {
                "contract": _ERROR_CONTRACT,
                "error": name,
                "exit_code": code,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stderr.flush()
    return code


def main() -> int:
    try:
        receipt = _run()
    except ValidationError:
        return _error("invalid_request", 64)
    except StoreUnavailableError:
        return _error("store_unavailable", 69)
    except FmpPriceHistoryUnavailable:
        return _error("provider_unavailable", 69)
    except ResourceLimitError:
        return _error("local_io", 74)
    except (RegistryError, ConflictError):
        return _error("temporary_conflict", 75)
    except Exception:
        return _error("internal_failure", 70)
    sys.stdout.write(
        dumps_strict(
            {
                "contract": _RECEIPT_CONTRACT,
                "receipt": receipt,
                "version": _VERSION,
            }
        )
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
