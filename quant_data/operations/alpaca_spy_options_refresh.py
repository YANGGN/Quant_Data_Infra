"""Fixed, bounded scheduled capture of the Alpaca indicative SPY option surface.

This is deliberately only an operation boundary.  The market module owns the
four-request, no-retry provider plan, trading-day gate, normalization, and
replay-safe publication.  The wrapper fixes the canonical project root and
store map, reads the two named credentials without exporting ``.env`` values,
and emits only compact, credential-free JSON receipts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import math
import os
from pathlib import Path
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
from ..market.alpaca_options import (
    StdlibAlpacaHttpTransport,
    run_alpaca_spy_option_surface,
)
from ..registry import CANONICAL_REGISTRY_PATH, load_registry
from ..stores import StoreRole, resolve_store_map


PROJECT_ROOT: Final = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
MARKET_STORE: Final = PROJECT_ROOT / "data" / "market.sqlite"
_VERSION: Final = "1.0.0"
_SENSITIVE_KEY_PARTS: Final = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "header",
        "password",
        "secret",
        "token",
    }
)
_MAX_SAFE_STRING: Final = 512
_MAX_SAFE_ITEMS: Final = 64
_MAX_SAFE_DEPTH: Final = 4


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Alpaca option-surface capture time must include an offset")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _safe_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise ValidationError("Alpaca option-surface receipt is invalid")
    if not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValidationError("Alpaca option-surface receipt is invalid")
    if value.casefold() in _SENSITIVE_KEY_PARTS:
        raise ValidationError("Alpaca option-surface receipt is invalid")
    return value


def _safe_value(value: object, *, depth: int = 0) -> Any:
    """Bound output to small JSON primitives and reject credential-shaped keys."""

    if depth > _MAX_SAFE_DEPTH:
        raise ValidationError("Alpaca option-surface receipt is invalid")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Alpaca option-surface receipt is invalid")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_SAFE_STRING or "\x00" in value:
            raise ValidationError("Alpaca option-surface receipt is invalid")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("Alpaca option-surface receipt is invalid")
        return {
            _safe_key(key): _safe_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_SAFE_ITEMS:
            raise ValidationError("Alpaca option-surface receipt is invalid")
        return [_safe_value(item, depth=depth + 1) for item in value]
    raise ValidationError("Alpaca option-surface receipt is invalid")


def _receipt_mapping(value: object) -> dict[str, Any]:
    mapping_method = getattr(value, "mapping", None)
    candidate = mapping_method() if callable(mapping_method) else value
    if not isinstance(candidate, Mapping):
        raise ValidationError("Alpaca option-surface receipt is invalid")
    safe = _safe_value(candidate)
    if not isinstance(safe, dict):  # pragma: no cover - defensive narrowing
        raise ValidationError("Alpaca option-surface receipt is invalid")
    return safe


def _canonical_dependencies() -> tuple[object, object]:
    """Load only the reviewed registry and its fixed canonical store map."""

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
        raise ValidationError("Alpaca option-surface canonical market target is invalid")
    return registry, stores


def _run() -> dict[str, Any]:
    registry, stores = _canonical_dependencies()
    api_key = read_project_credential(
        project_root=PROJECT_ROOT,
        name="ALPACA_API_KEY",
        environment=os.environ,
    )
    api_secret = read_project_credential(
        project_root=PROJECT_ROOT,
        name="ALPACA_API_SECRET",
        environment=os.environ,
    )
    receipt = run_alpaca_spy_option_surface(
        project_root=PROJECT_ROOT,
        stores=stores,
        registry=registry,
        transport=StdlibAlpacaHttpTransport(),
        captured_at=_utc_text(datetime.now(timezone.utc)),
        api_key=api_key,
        api_secret=api_secret,
    )
    return _receipt_mapping(receipt)


def main() -> int:
    """Run the fixed canonical capture without accepting caller-selected scope."""

    try:
        receipt = _run()
    except ValidationError:
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
                    "contract": "quant_data.alpaca_spy_option_surface_receipt",
                    "receipt": receipt,
                    "version": _VERSION,
                }
            )
            + "\n"
        )
        sys.stdout.flush()
        return 0
    sys.stderr.write(
        dumps_strict(
            {
                "contract": "quant_data.alpaca_spy_option_surface_error",
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
    raise SystemExit(main())
