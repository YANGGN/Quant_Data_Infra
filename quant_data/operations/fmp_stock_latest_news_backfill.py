"""Restricted future live wrapper for the one approved FMP stock-news page.

The callable path is intentionally exact-target only.  It neither falls back
to registry defaults nor initializes unrelated stores, and it never prints a
credential or provider response body.
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

from ..errors import ConflictError, MigrationError, RegistryError, ValidationError
from ..json_codec import dumps_strict
from ..migrations import migrate_and_register_store
from ..news.fmp_stock_latest import (
    FMP_STOCK_LATEST_ARTICLES_DATASET_ID,
    FMP_STOCK_LATEST_COLLECTOR_ID,
    FMP_STOCK_LATEST_EVIDENCE_DATASET_ID,
    FMP_STOCK_LATEST_MIGRATION_ID,
    FmpStockLatestImporter,
    FmpStockLatestReceipt,
    FmpStockLatestTransport,
    StdlibFmpStockLatestTransport,
)
from ..registry import CANONICAL_REGISTRY_PATH, Registry, load_registry
from ..stores import StoreMap, StoreRole


APPROVED_PROJECT_ROOT = Path("/home/volatility/Python_Projects/Quant_Data_Infra")
APPROVED_NEWS_TARGET = APPROVED_PROJECT_ROOT / "data/news.sqlite"


class _ArgumentFailure(Exception):
    """Sanitized CLI-only rejection."""


class _ConfigurationFailure(Exception):
    """Sanitized preflight rejection that must create no request intent."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentFailure


@dataclass(frozen=True, slots=True)
class FmpStockLatestPopulationResult:
    outcome: str
    article_count: int
    written_count: int

    @classmethod
    def from_receipt(cls, receipt: FmpStockLatestReceipt) -> "FmpStockLatestPopulationResult":
        return cls(
            outcome=receipt.outcome,
            article_count=receipt.article_count,
            written_count=receipt.written_count,
        )


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="python3 scripts/populate_fmp_stock_latest_news_live.py",
        add_help=False,
        description="Run only the exact manual FMP stock-latest news page",
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--target", required=True)
    return parser


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


def _require_exact_project_root(value: object) -> Path:
    if not isinstance(value, str) or value != str(APPROVED_PROJECT_ROOT):
        raise _ArgumentFailure
    try:
        root = Path(value)
        if not root.is_absolute() or _has_symlink_component(root) or not root.is_dir():
            raise _ArgumentFailure
        if root.resolve(strict=True) != APPROVED_PROJECT_ROOT.resolve(strict=True):
            raise _ArgumentFailure
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ArgumentFailure from exc
    return root


def _require_exact_target(value: object) -> Path:
    if not isinstance(value, str) or value != str(APPROVED_NEWS_TARGET):
        raise _ArgumentFailure
    try:
        target = Path(value)
        if not target.is_absolute() or _has_symlink_component(target):
            raise _ArgumentFailure
        if target.exists() and not target.is_file():
            raise _ArgumentFailure
        if target.resolve(strict=False) != APPROVED_NEWS_TARGET.resolve(strict=False):
            raise _ArgumentFailure
    except (OSError, RuntimeError, ValueError) as exc:
        raise _ArgumentFailure from exc
    return target


def _read_api_key(environment: Mapping[str, str]) -> str:
    value = environment.get("FMP_API_KEY")
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _ConfigurationFailure
    return value


def _store_map(project_root: Path, target: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=data / "macro.sqlite",
        company=data / "company.sqlite",
        news=target,
    )


def _registry_preflight(registry: Registry) -> None:
    try:
        collector = [
            item for item in registry.collectors if item.get("id") == FMP_STOCK_LATEST_COLLECTOR_ID
        ]
        migration = [
            item for item in registry.migrations if item.id == FMP_STOCK_LATEST_MIGRATION_ID
        ]
        datasets = {item.id: item for item in registry.datasets}
    except (AttributeError, TypeError) as exc:
        raise _ConfigurationFailure from exc
    if (
        (registry.schema_version, registry.registry_version)
        not in {
            ("1.8.0", "2.21.0"),
            ("1.8.0", "2.23.0"),
            ("1.8.0", "2.24.0"),
            ("1.8.0", "2.25.0"),
            ("1.9.0", "2.43.0"),
            ("1.9.0", "2.44.0"),
            ("1.9.0", "2.45.0"),
            ("1.9.0", "2.46.0"),
            ("1.9.0", "2.47.0"),
            ("1.9.0", "2.48.0"),
            ("1.9.0", "2.49.0"),
        }
        or len(collector) != 1
        or len(migration) != 1
        or migration[0].store != StoreRole.NEWS.value
        or migration[0].ordinal != 5
        or migration[0].reconstruction_state != "fixture_validated"
        or collector[0].get("handler") != "news.fmp_stock_latest"
        or collector[0].get("network") is not True
        or tuple(collector[0].get("configuration_env", ())) != ("FMP_API_KEY",)
        or tuple(collector[0].get("output_datasets", ()))
        != (FMP_STOCK_LATEST_EVIDENCE_DATASET_ID, FMP_STOCK_LATEST_ARTICLES_DATASET_ID)
        or not {
            FMP_STOCK_LATEST_EVIDENCE_DATASET_ID,
            FMP_STOCK_LATEST_ARTICLES_DATASET_ID,
        }.issubset(datasets)
    ):
        raise _ConfigurationFailure
    if any(
        datasets[dataset_id].store != StoreRole.NEWS.value
        or datasets[dataset_id].tool_ids
        or datasets[dataset_id].dashboard_ids
        or datasets[dataset_id].export_ids
        for dataset_id in (
            FMP_STOCK_LATEST_EVIDENCE_DATASET_ID,
            FMP_STOCK_LATEST_ARTICLES_DATASET_ID,
        )
    ):
        raise _ConfigurationFailure


def _expected_pre_0005_news_migration_states(registry: Registry) -> tuple[tuple[object, ...], ...]:
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
    prefix = tuple(
        state
        for declaration, state in zip(declarations, expected)
        if declaration.id != FMP_STOCK_LATEST_MIGRATION_ID
    )
    if (
        len(expected) != 5
        or len(prefix) != 4
        or expected[-1][0] != FMP_STOCK_LATEST_MIGRATION_ID
        or tuple(item[2] for item in prefix) != (1, 2, 3, 4)
    ):
        raise _ConfigurationFailure
    return prefix


def _preflight_existing_news_target(root: Path, target: Path, registry: Registry) -> None:
    """Reject an existing non-news or unanchored target before migration.

    This deliberately checks only the immutable store anchor and migration
    ledger prefix.  Dataset relations are reconciled later by the reviewed
    target-only migration/register path, after migration 0005 is available.
    """

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
            connection.execute(
                "SELECT singleton, store_role FROM store_metadata ORDER BY singleton"
            )
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
        if actual != _expected_pre_0005_news_migration_states(registry):
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


def populate_fmp_stock_latest_news(
    *,
    project_root: object,
    target: object,
    environment: Mapping[str, str],
    transport: FmpStockLatestTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FmpStockLatestPopulationResult:
    """Migrate/register only the exact news target, then make one request."""

    api_key = _read_api_key(environment)
    root = _require_exact_project_root(project_root)
    target_path = _require_exact_target(target)
    registry = load_registry(
        root / CANONICAL_REGISTRY_PATH,
        project_root=root,
        environment={},
    )
    _registry_preflight(registry)
    _preflight_existing_news_target(root, target_path, registry)
    stores = _store_map(root, target_path)
    applied = migrate_and_register_store(
        stores,
        registry,
        StoreRole.NEWS,
        applied_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
    )
    if not applied or applied[-1] != FMP_STOCK_LATEST_MIGRATION_ID:
        raise MigrationError("FMP stock-latest target migration state is invalid")
    active_transport = StdlibFmpStockLatestTransport() if transport is None else transport
    receipt = FmpStockLatestImporter(stores, registry, clock=clock).run_once(
        api_key=api_key,
        transport=active_transport,
    )
    return FmpStockLatestPopulationResult.from_receipt(receipt)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = populate_fmp_stock_latest_news(
            project_root=arguments.project_root,
            target=arguments.target,
            environment=os.environ,
        )
    except (_ArgumentFailure, _ConfigurationFailure, ConflictError, MigrationError, RegistryError, ValidationError):
        return 2
    except Exception:
        return 1
    sys.stdout.write(
        dumps_strict(
            {
                "article_count": result.article_count,
                "outcome": result.outcome,
                "written_count": result.written_count,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the script wrapper
    raise SystemExit(main())
