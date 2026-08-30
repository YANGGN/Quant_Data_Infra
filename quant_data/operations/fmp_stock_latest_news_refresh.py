"""Fixed-target wrapper for one repeatable FMP current-news poll.

This wrapper intentionally accepts no caller-selected path, provider scope, or
poll slot. It is suitable for a future independently authorized timer but does
not install, enable, or invoke one itself.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..credentials import read_project_credential
from ..errors import ConflictError, MigrationError, RegistryError, ValidationError
from ..json_codec import dumps_strict
from ..migrations import migrate_and_register_store
from ..news.fmp_stock_latest_current import (
    FMP_STOCK_LATEST_CURRENT_ARTICLES_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_EVIDENCE_DATASET_ID,
    FMP_STOCK_LATEST_CURRENT_MIGRATION_ID,
    FmpStockLatestCurrentImporter,
    FmpStockLatestCurrentReceipt,
    FmpStockLatestCurrentTransport,
    StdlibFmpStockLatestCurrentTransport,
    _validate_registry,
)
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stores import StoreMap, StoreRole


APPROVED_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_NEWS_TARGET = APPROVED_PROJECT_ROOT / "data/news.sqlite"


class _ArgumentFailure(Exception):
    """Sanitized CLI-only rejection."""


class _ConfigurationFailure(Exception):
    """Sanitized preflight rejection that makes no request intent."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class FmpStockLatestCurrentRefreshResult:
    outcome: str
    poll_slot: str
    provider_row_count: int
    accepted_row_count: int
    rejected_row_count: int
    written_count: int

    @classmethod
    def from_receipt(
        cls,
        receipt: FmpStockLatestCurrentReceipt,
    ) -> "FmpStockLatestCurrentRefreshResult":
        return cls(
            outcome=receipt.outcome,
            poll_slot=receipt.poll_slot,
            provider_row_count=receipt.provider_row_count,
            accepted_row_count=receipt.accepted_row_count,
            rejected_row_count=receipt.rejected_row_count,
            written_count=receipt.written_count,
        )


def _parser() -> argparse.ArgumentParser:
    return _SafeArgumentParser(
        prog="python3 scripts/refresh_fmp_stock_latest_news.py",
        add_help=False,
        description="Run one fixed FMP current stock-latest news poll",
    )


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode):
            return True
    return False


def _require_canonical_target() -> tuple[Path, Path]:
    root = APPROVED_PROJECT_ROOT
    target = APPROVED_NEWS_TARGET
    try:
        if (
            not root.is_absolute()
            or not target.is_absolute()
            or target != root / "data" / "news.sqlite"
            or _has_symlink_component(root)
            or _has_symlink_component(target)
            or not root.is_dir()
            or not target.is_file()
            or root.resolve(strict=True) != APPROVED_PROJECT_ROOT.resolve(strict=True)
            or target.resolve(strict=True) != APPROVED_NEWS_TARGET.resolve(strict=True)
        ):
            raise _ConfigurationFailure
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ConfigurationFailure from exc
    return root, target


def _store_map(root: Path, target: Path) -> StoreMap:
    data = root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=target,
    )


def _expected_news_ledger(registry: Registry) -> tuple[tuple[object, ...], ...]:
    declarations = tuple(
        sorted(registry.migrations_for(StoreRole.NEWS.value), key=lambda item: item.ordinal)
    )
    expected = tuple(
        (
            item.id,
            item.store,
            item.ordinal,
            item.resource,
            item.sha256,
            item.reconstruction_state,
        )
        for item in declarations
    )
    if (
        len(expected) != 6
        or tuple(item[0] for item in expected)[-1] != FMP_STOCK_LATEST_CURRENT_MIGRATION_ID
        or tuple(item[2] for item in expected) != (1, 2, 3, 4, 5, 6)
    ):
        raise _ConfigurationFailure
    return expected


def _preflight_existing_news_target(root: Path, target: Path, registry: Registry) -> None:
    """Accept only the exact reviewed 0001--0005 prefix or current 0001--0006 ledger."""

    connection: sqlite3.Connection | None = None
    try:
        if target.parent != root / "data":
            raise _ConfigurationFailure
        root_stat = root.lstat()
        parent_stat = target.parent.lstat()
        target_stat = target.lstat()
        if (
            _has_symlink_component(root)
            or _has_symlink_component(target)
            or not stat.S_ISDIR(root_stat.st_mode)
            or not stat.S_ISDIR(parent_stat.st_mode)
            or not stat.S_ISREG(target_stat.st_mode)
        ):
            raise _ConfigurationFailure
        connection = sqlite3.connect(
            f"{target.as_uri()}?mode=ro&immutable=1",
            uri=True,
            isolation_level=None,
        )
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise _ConfigurationFailure
        metadata = tuple(
            connection.execute("SELECT singleton, store_role FROM store_metadata ORDER BY singleton")
        )
        if metadata != ((1, StoreRole.NEWS.value),):
            raise _ConfigurationFailure
        actual = tuple(
            connection.execute(
                """
                SELECT migration_id, store_role, ordinal, resource, sha256,
                       reconstruction_state
                FROM schema_migrations
                ORDER BY ordinal
                """
            )
        )
        expected = _expected_news_ledger(registry)
        if actual not in (expected[:-1], expected):
            raise _ConfigurationFailure
    except _ConfigurationFailure:
        raise
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise _ConfigurationFailure from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def refresh_fmp_stock_latest_news(
    *,
    environment: Mapping[str, str],
    transport: FmpStockLatestCurrentTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FmpStockLatestCurrentRefreshResult:
    """Apply only 0006 when needed, then issue one fixed current-news request."""

    root, target = _require_canonical_target()
    try:
        api_key = read_project_credential(
            project_root=root,
            name="FMP_API_KEY",
            environment=environment,
        )
    except ValidationError as exc:
        raise _ConfigurationFailure from exc
    registry = load_registry(
        root / CANONICAL_REGISTRY_PATH,
        project_root=root,
        environment={},
    )
    try:
        _validate_registry(registry)
    except ValidationError as exc:
        raise _ConfigurationFailure from exc
    _preflight_existing_news_target(root, target, registry)
    stores = _store_map(root, target)
    applied = migrate_and_register_store(
        stores,
        registry,
        StoreRole.NEWS,
        applied_at=_utc_now_text(clock),
    )
    if tuple(applied) != tuple(item[0] for item in _expected_news_ledger(registry)):
        raise MigrationError("FMP current stock-latest target migration state is invalid")
    active_transport = StdlibFmpStockLatestCurrentTransport() if transport is None else transport
    receipt = FmpStockLatestCurrentImporter(stores, registry, clock=clock).run_once(
        api_key=api_key,
        transport=active_transport,
    )
    return FmpStockLatestCurrentRefreshResult.from_receipt(receipt)


def _utc_now_text(clock: Callable[[], datetime]) -> str:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _ConfigurationFailure
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    try:
        _parser().parse_args(argv)
        result = refresh_fmp_stock_latest_news(environment=os.environ)
    except (_ArgumentFailure, _ConfigurationFailure, ConflictError, MigrationError, RegistryError, ValidationError):
        return 2
    except Exception:
        return 1
    sys.stdout.write(
        dumps_strict(
            {
                "accepted_row_count": result.accepted_row_count,
                "outcome": result.outcome,
                "poll_slot": result.poll_slot,
                "provider_row_count": result.provider_row_count,
                "rejected_row_count": result.rejected_row_count,
                "written_count": result.written_count,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
