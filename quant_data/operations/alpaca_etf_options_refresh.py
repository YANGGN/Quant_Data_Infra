"""Bounded capture of the fixed Alpaca major-ETF option-surface grid.

The market module owns the provider plan and publication. This zero-argument
boundary supports the fixed weekday service as well as direct manual use,
fixes the canonical project root and store, emits only compact,
credential-free receipts, and deliberately creates or changes no systemd unit.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
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
    run_alpaca_etf_option_surface_grid,
)
from .alpaca_spy_options_refresh import (
    PROJECT_ROOT,
    _canonical_dependencies,
    _receipt_mapping,
    _utc_text,
)


_VERSION: Final = "1.0.0"


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
    receipt = run_alpaca_etf_option_surface_grid(
        project_root=PROJECT_ROOT,
        stores=stores,
        registry=registry,
        transport=StdlibAlpacaHttpTransport(),
        captured_at=_utc_text(datetime.now(timezone.utc)),
        api_key=api_key,
        api_secret=api_secret,
    )
    return _receipt_mapping(receipt)


def _error(
    name: str,
    code: int,
    *,
    receipt: dict[str, Any] | None = None,
) -> int:
    payload: dict[str, Any] = {
        "contract": "quant_data.alpaca_etf_option_surface_grid_error",
        "error": name,
        "exit_code": code,
        "version": _VERSION,
    }
    if receipt is not None:
        payload["receipt"] = _receipt_mapping(receipt)
    sys.stderr.write(
        dumps_strict(payload)
        + "\n"
    )
    sys.stderr.flush()
    return code


def main() -> int:
    """Run the fixed major-ETF grid without caller-selected paths or universe."""

    try:
        receipt = _run()
    except ValidationError:
        return _error("invalid_request", 64)
    except StoreUnavailableError:
        return _error("store_unavailable", 69)
    except ResourceLimitError:
        return _error("local_io", 74)
    except RegistryError:
        return _error("registry_not_ready", 75)
    except ConflictError:
        return _error("temporary_conflict", 75)
    except Exception:
        return _error("internal_failure", 70)
    if receipt.get("outcome") == "partial":
        return _error("incomplete_universe", 75, receipt=receipt)
    sys.stdout.write(
        dumps_strict(
            {
                "contract": "quant_data.alpaca_etf_option_surface_grid_receipt",
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
