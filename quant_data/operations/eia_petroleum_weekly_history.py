"""Bounded EIA weekly petroleum-stock refresh."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from ..contracts import IngestionReceipt
from ..credentials import read_project_credential
from ..errors import StoreUnavailableError, ValidationError
from ..macro.stage11_eia import (
    EiaTransport,
    StdlibEiaTransport,
    capture_eia_weekly,
    prepare_eia_weekly_capture,
)
from ..macro.stage11_publication import Stage11MacroImporter
from ..registry import (
    CANONICAL_REGISTRY_PATH,
    Registry,
    load_registry,
    stage11_registry_profile,
)
from ..stores import StoreMap, StoreRole


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MACRO_STORE = PROJECT_ROOT / "data" / "macro.sqlite"
REQUEST_CAP = 1


def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _require_canonical_binding() -> Registry:
    try:
        root = PROJECT_ROOT.resolve(strict=True)
        target = MACRO_STORE.resolve(strict=True)
        target_info = MACRO_STORE.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise StoreUnavailableError(
            "EIA weekly petroleum canonical target is unavailable"
        ) from exc
    if (
        root != PROJECT_ROOT
        or PROJECT_ROOT.is_symlink()
        or target != MACRO_STORE
        or MACRO_STORE.is_symlink()
        or not stat.S_ISREG(target_info.st_mode)
        or target_info.st_nlink != 1
    ):
        raise ValidationError(
            "EIA weekly petroleum canonical target binding is invalid"
        )
    registry = load_registry(
        CANONICAL_REGISTRY_PATH,
        project_root=PROJECT_ROOT,
        environment={},
    )
    macro = registry.store(StoreRole.MACRO.value)
    if (
        registry.status != "validated"
        or macro.default_path != "data/macro.sqlite"
        or MACRO_STORE != PROJECT_ROOT / macro.default_path
    ):
        raise ValidationError(
            "EIA weekly petroleum canonical target binding is invalid"
        )
    return registry


def _api_key(project_root: Path) -> str:
    value = read_project_credential(
        project_root=project_root,
        name="EIA_API_KEY",
        environment=os.environ,
    )
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        )
    ):
        raise ValidationError("EIA credential is missing or invalid")
    return value


def run_eia_petroleum_weekly_stock_history(
    *,
    importer: Stage11MacroImporter,
    api_key: str,
    transport: EiaTransport,
) -> IngestionReceipt:
    """Fetch the one fixed complete weekly stock series without retries."""

    capture = capture_eia_weekly(
        prepare_eia_weekly_capture(),
        api_key=api_key,
        transport=transport,
    )
    return importer.publish_prepared(
        importer.prepare_eia_weekly_history(capture)
    )


def populate_eia_petroleum_weekly_stock_live() -> IngestionReceipt:
    """Run the fixed canonical EIA weekly stock refresh once."""

    registry = _require_canonical_binding()
    importer = Stage11MacroImporter(
        _store_map(PROJECT_ROOT, MACRO_STORE),
        stage11_registry_profile(registry),
    )
    return run_eia_petroleum_weekly_stock_history(
        importer=importer,
        api_key=_api_key(PROJECT_ROOT),
        transport=StdlibEiaTransport(),
    )


__all__ = (
    "REQUEST_CAP",
    "populate_eia_petroleum_weekly_stock_live",
    "run_eia_petroleum_weekly_stock_history",
)
