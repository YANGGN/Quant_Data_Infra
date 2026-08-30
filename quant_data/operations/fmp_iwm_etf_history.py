"""Fixed, manual FMP IWM ETF full-history operation.

This zero-argument boundary fixes the project root, canonical market store, one
reviewed IWM extension scope, and one FMP credential. It rejects any existing
attempt state before dependency, credential, or network activity; after store
preflight it atomically reserves one durable private attempt before the bounded
provider runner and final immutable completion receipt.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Final

from ..credentials import read_project_credential
from ..errors import (
    ConflictError,
    RegistryError,
    ResourceLimitError,
    StoreUnavailableError,
    ValidationError,
)
from ..json_codec import dumps_strict
from ..market.fmp_bulk_daily_prices import FmpPriceHistoryUnavailable
from ..market.fmp_iwm_etf_history import (
    IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
    IwmEtfHistoryPublisher,
    StdlibFmpIwmEtfHistoryTransport,
    load_iwm_etf_history_scope,
    run_iwm_etf_history,
)
from ..market.stage10_scope import load_stage10_market_scope
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stores import StoreMap, StoreRole, resolve_store_map


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
_VERSION: Final = "1.0.0"
_RECEIPT_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_receipt"
_ERROR_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_error"
_COMPLETION_CONTRACT: Final = "quant_data.fmp_iwm_etf_history_completion"
_COMPLETION_DIRECTORY_NAME: Final = "fmp-iwm-etf-history-v1"
_COMPLETION_FILE_NAME: Final = "completion.json"
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_PRICE_ROWS: Final = 30_000
_MAX_WRITTEN_COUNT: Final = 30_500
_REPORT_FIELDS: Final = (
    "outcome",
    "base_scope_manifest_sha256",
    "scope_manifest_sha256",
    "successor_membership_sha256",
    "normalized_complete_history_sha256",
    "response_sha256",
    "price_row_count",
    "successor_member_count",
    "written_count",
)


def _completion_directory() -> Path:
    return PROJECT_ROOT / "data" / ".operations" / _COMPLETION_DIRECTORY_NAME


def _completion_path() -> Path:
    return _completion_directory() / _COMPLETION_FILE_NAME


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ResourceLimitError("IWM ETF history completion state is unavailable") from exc


def _require_directory(path: Path, *, private: bool) -> os.stat_result:
    details = _lstat(path)
    if details is None or stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ConflictError("IWM ETF history completion state conflicts")
    if private and stat.S_IMODE(details.st_mode) != 0o700:
        raise ConflictError("IWM ETF history completion state conflicts")
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
        raise ConflictError("IWM ETF history completion receipt conflicts")
    return True


def _completion_preflight() -> None:
    """Fail closed on a completed, reserved, or malformed private state."""

    state = _completion_directory()
    parent = state.parent
    if _lstat(parent) is not None:
        _require_directory(parent, private=False)
    if _lstat(state) is None:
        return
    _require_directory(state, private=True)
    _completion_file_exists(state / _COMPLETION_FILE_NAME)
    raise ConflictError("IWM ETF history attempt is already reserved or completed")


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
            raise ResourceLimitError("IWM ETF history completion state is unavailable") from exc
    _require_directory(parent, private=False)
    if created:
        _fsync_directory(parent.parent)
    return parent


def _reserve_attempt() -> Path:
    """Atomically reserve this one manual attempt before provider activity."""

    _completion_preflight()
    parent = _ensure_operations_directory()
    state = _completion_directory()
    try:
        os.mkdir(os.fspath(state), 0o700)
        os.chmod(os.fspath(state), 0o700)
    except FileExistsError as exc:
        raise ConflictError("IWM ETF history attempt is already reserved or completed") from exc
    except OSError as exc:
        raise ResourceLimitError("IWM ETF history completion state is unavailable") from exc
    _require_directory(state, private=True)
    _fsync_directory(state)
    _fsync_directory(parent)
    return state


def _require_reserved_state() -> Path:
    state = _completion_directory()
    _require_directory(state.parent, private=False)
    _require_directory(state, private=True)
    if _completion_file_exists(state / _COMPLETION_FILE_NAME):
        raise ConflictError("IWM ETF history is already completed")
    return state


def _fsync_directory(path: Path) -> None:
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        descriptor = os.open(os.fspath(path), flags)
    except (AttributeError, OSError) as exc:
        raise ResourceLimitError("IWM ETF history completion state is unavailable") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ResourceLimitError("IWM ETF history completion state is unavailable") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _safe_digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError("IWM ETF history receipt is invalid")
    return value


def _safe_count(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValidationError("IWM ETF history receipt is invalid")
    return value


def _receipt_mapping(value: object) -> dict[str, object]:
    """Return a compact, credential- and path-free report mapping."""

    mapping_method = getattr(value, "mapping", None)
    candidate = mapping_method() if callable(mapping_method) else value
    if not isinstance(candidate, Mapping) or set(candidate) != set(_REPORT_FIELDS):
        raise ValidationError("IWM ETF history receipt is invalid")
    outcome = candidate.get("outcome")
    if outcome not in {"succeeded", "unchanged"}:
        raise ValidationError("IWM ETF history receipt is invalid")
    price_row_count = _safe_count(candidate.get("price_row_count"), maximum=_MAX_PRICE_ROWS)
    if price_row_count < 1:
        raise ValidationError("IWM ETF history receipt is invalid")
    successor_member_count = _safe_count(
        candidate.get("successor_member_count"),
        maximum=IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT,
    )
    if successor_member_count != IWM_ETF_HISTORY_SUCCESSOR_MEMBER_COUNT:
        raise ValidationError("IWM ETF history receipt is invalid")
    written_count = _safe_count(candidate.get("written_count"), maximum=_MAX_WRITTEN_COUNT)
    if outcome == "unchanged" and written_count != 0:
        raise ValidationError("IWM ETF history receipt is invalid")
    return {
        "outcome": outcome,
        "base_scope_manifest_sha256": _safe_digest(candidate.get("base_scope_manifest_sha256")),
        "scope_manifest_sha256": _safe_digest(candidate.get("scope_manifest_sha256")),
        "successor_membership_sha256": _safe_digest(candidate.get("successor_membership_sha256")),
        "normalized_complete_history_sha256": _safe_digest(
            candidate.get("normalized_complete_history_sha256")
        ),
        "response_sha256": _safe_digest(candidate.get("response_sha256")),
        "price_row_count": price_row_count,
        "successor_member_count": successor_member_count,
        "written_count": written_count,
    }


def _completion_payload(receipt: Mapping[str, object]) -> bytes:
    payload = {
        "base_scope_manifest_sha256": receipt["base_scope_manifest_sha256"],
        "contract": _COMPLETION_CONTRACT,
        "normalized_complete_history_sha256": receipt["normalized_complete_history_sha256"],
        "outcome": receipt["outcome"],
        "price_row_count": receipt["price_row_count"],
        "response_sha256": receipt["response_sha256"],
        "scope_manifest_sha256": receipt["scope_manifest_sha256"],
        "successor_member_count": receipt["successor_member_count"],
        "successor_membership_sha256": receipt["successor_membership_sha256"],
        "version": _VERSION,
        "written_count": receipt["written_count"],
    }
    encoded = (dumps_strict(payload) + "\n").encode("utf-8")
    if not encoded or len(encoded) > 4096:
        raise ValidationError("IWM ETF history completion receipt is invalid")
    return encoded


def _write_all(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        try:
            written = os.write(descriptor, remaining)
        except OSError as exc:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable") from exc
        if written <= 0:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable")
        remaining = remaining[written:]


def _write_completion(receipt: Mapping[str, object]) -> None:
    """Create one immutable receipt only after a successful atomic publish."""

    state = _require_reserved_state()
    encoded = _completion_payload(receipt)
    path = state / _COMPLETION_FILE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(os.fspath(path), flags, 0o600)
    except FileExistsError as exc:
        raise ConflictError("IWM ETF history is already completed") from exc
    except OSError as exc:
        raise ResourceLimitError("IWM ETF history completion receipt is unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable")
        try:
            os.fchmod(descriptor, 0o600)
        except OSError as exc:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable") from exc
        _write_all(descriptor, encoded)
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable") from exc
        final_details = os.fstat(descriptor)
        if stat.S_IMODE(final_details.st_mode) != 0o600:
            raise ResourceLimitError("IWM ETF history completion receipt is unavailable")
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
        raise ValidationError("IWM ETF history canonical market target is invalid")
    return registry, stores


def _scope() -> object:
    base_scope = load_stage10_market_scope(PROJECT_ROOT / "config" / "stage10_market_scope.json")
    return load_iwm_etf_history_scope(
        PROJECT_ROOT / "config" / "iwm_etf_history_v1_scope.json",
        base_scope=base_scope,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run() -> dict[str, object]:
    """Run exactly one fixed IWM request after store-only preflight."""

    _completion_preflight()
    registry, stores = _canonical_dependencies()
    scope = _scope()
    IwmEtfHistoryPublisher(stores, registry, scope).preflight_store()
    api_key = read_project_credential(
        project_root=PROJECT_ROOT,
        name="FMP_API_KEY",
        environment=os.environ,
    )
    _reserve_attempt()
    report = run_iwm_etf_history(
        store_map=stores,
        registry=registry,
        scope=scope,
        transport=StdlibFmpIwmEtfHistoryTransport(),
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
    """Run the exact manual IWM population with no caller-selected inputs."""

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
