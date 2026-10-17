from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data import stores as stores_module
from quant_data.errors import StoreUnavailableError
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry
from quant_data.stores import (
    StoreMap,
    StoreRole,
    quiet_immutable_read_connection,
)
from tests.runtime_data_guard import assert_project_data_unchanged, snapshot_project_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"


def _temporary_store_map(root: Path) -> StoreMap:
    return StoreMap.four_explicit(
        market=root / "market.sqlite",
        macro=root / "macro.sqlite",
        company=root / "company.sqlite",
        news=root / "news.sqlite",
    )


class QuietImmutableReadConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._project_data_before = snapshot_project_data(PROJECT_ROOT)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = _temporary_store_map(self.root)
        self.registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )
        initialize_all(self.store_map, self.registry)

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        finally:
            assert_project_data_unchanged(
                self._project_data_before,
                project_root=PROJECT_ROOT,
            )

    def test_pins_descriptor_uses_immutable_uri_and_is_query_only(self) -> None:
        real_connect = sqlite3.connect
        real_open = os.open
        connect_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        connections: list[sqlite3.Connection] = []
        descriptors: list[int] = []
        before = stores_module._immutable_store_snapshot(
            self.store_map.market, StoreRole.MARKET
        )

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connect_calls.append((args, kwargs))
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        def traced_open(*args: object, **kwargs: object) -> int:
            descriptor = real_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch.object(
            stores_module.sqlite3, "connect", side_effect=traced_connect
        ), patch.object(stores_module.os, "open", side_effect=traced_open):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA query_only").fetchone()[0], 1
                )
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertTrue(connection.in_transaction)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM store_metadata").fetchone()[0],
                    1,
                )

        self.assertEqual(len(connect_calls), 1)
        args, kwargs = connect_calls[0]
        self.assertTrue(str(args[0]).startswith("file:/proc/self/fd/"))
        self.assertTrue(str(args[0]).endswith("?mode=ro&immutable=1"))
        self.assertEqual(
            kwargs,
            {"uri": True, "timeout": 0.0, "isolation_level": None},
        )
        self.assertEqual(
            stores_module._immutable_store_snapshot(
                self.store_map.market, StoreRole.MARKET
            ),
            before,
        )
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_rejects_main_symlink_and_open_race_before_sqlite(self) -> None:
        real_connect = sqlite3.connect
        original = self.root / "market-original.sqlite"
        self.store_map.market.rename(original)
        self.store_map.market.symlink_to(original)
        with patch.object(
            stores_module.sqlite3, "connect", wraps=real_connect
        ) as connect_mock:
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    self.fail("symlinked main path must not yield")
            connect_mock.assert_not_called()

        self.store_map.market.unlink()
        original.rename(self.store_map.market)
        raced = self.root / "market-raced.sqlite"
        real_open = os.open

        def race_open(path: object, flags: int) -> int:
            self.store_map.market.rename(raced)
            self.store_map.market.symlink_to(raced)
            return real_open(path, flags)

        with patch.object(stores_module.os, "open", side_effect=race_open), patch.object(
            stores_module.sqlite3, "connect", wraps=real_connect
        ) as connect_mock:
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    self.fail("open race must not yield")
            connect_mock.assert_not_called()

    def test_rejects_sidecar_identity_changes_before_sqlite(self) -> None:
        real_connect = sqlite3.connect
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(sidecar=suffix):
                target = self.root / f"target{suffix}"
                target.write_bytes(b"")
                sidecar = Path(f"{self.store_map.market}{suffix}")
                sidecar.symlink_to(target)
                with patch.object(
                    stores_module.sqlite3, "connect", wraps=real_connect
                ) as connect_mock:
                    with self.assertRaises(StoreUnavailableError):
                        with quiet_immutable_read_connection(
                            self.store_map, StoreRole.MARKET
                        ):
                            self.fail("symlinked sidecar must not yield")
                    connect_mock.assert_not_called()
                sidecar.unlink()
                target.unlink()

        target = self.root / "hardlink-target"
        target.write_bytes(b"")
        hardlinked = Path(f"{self.store_map.market}-shm")
        os.link(target, hardlinked)
        with self.assertRaises(StoreUnavailableError):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ):
                self.fail("hardlinked sidecar must not yield")
        hardlinked.unlink()
        target.unlink()

        nonregular = Path(f"{self.store_map.market}-shm")
        nonregular.mkdir()
        with self.assertRaises(StoreUnavailableError):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ):
                self.fail("nonregular sidecar must not yield")

    def test_rejects_hardlinked_main_before_sqlite(self) -> None:
        alias = self.root / "market-hardlink.sqlite"
        os.link(self.store_map.market, alias)
        real_connect = sqlite3.connect
        with patch.object(
            stores_module.sqlite3, "connect", wraps=real_connect
        ) as connect_mock:
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    self.fail("hardlinked main file must not yield")
            connect_mock.assert_not_called()

    def test_rejects_nonero_wal_and_journal_but_allows_stable_shm(self) -> None:
        real_connect = sqlite3.connect
        for suffix in ("-wal", "-journal"):
            with self.subTest(sidecar=suffix):
                sidecar = Path(f"{self.store_map.market}{suffix}")
                sidecar.write_bytes(b"x")
                with patch.object(
                    stores_module.sqlite3, "connect", wraps=real_connect
                ) as connect_mock:
                    with self.assertRaises(StoreUnavailableError):
                        with quiet_immutable_read_connection(
                            self.store_map, StoreRole.MARKET
                        ):
                            self.fail("nonquiet store must not yield")
                    connect_mock.assert_not_called()
                sidecar.unlink()

        shm = Path(f"{self.store_map.market}-shm")
        shm.write_bytes(b"x")
        with quiet_immutable_read_connection(
            self.store_map, StoreRole.MARKET
        ) as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

    def test_validates_anchor_and_role_and_closes_failed_connections(self) -> None:
        real_connect = sqlite3.connect
        connections: list[sqlite3.Connection] = []

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch.object(
            stores_module.sqlite3, "connect", side_effect=traced_connect
        ):
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map,
                    StoreRole.MARKET,
                    expected_anchor="missing_anchor",
                ):
                    self.fail("missing anchor must not yield")

            swapped = StoreMap.four_explicit(
                market=self.store_map.macro,
                macro=self.store_map.market,
                company=self.store_map.company,
                news=self.store_map.news,
            )
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    swapped, StoreRole.MARKET
                ):
                    self.fail("wrong store role must not yield")

        self.assertEqual(len(connections), 2)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_detects_main_and_sidecar_drift_on_exit(self) -> None:
        before = os.stat(self.store_map.market, follow_symlinks=False)
        with self.assertRaises(StoreUnavailableError):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ):
                os.utime(
                    self.store_map.market,
                    ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
                    follow_symlinks=False,
                )

        sidecar = Path(f"{self.store_map.market}-shm")
        with self.assertRaises(StoreUnavailableError):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ):
                sidecar.write_bytes(b"x")

    def test_detects_path_replacement_before_yield_and_on_exit(self) -> None:
        real_connect = sqlite3.connect
        replacement = self.root / "replacement-before.sqlite"
        shutil.copy2(self.store_map.market, replacement)
        connections: list[sqlite3.Connection] = []
        yielded = False

        def replacing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            os.replace(replacement, self.store_map.market)
            return connection

        with patch.object(
            stores_module.sqlite3, "connect", side_effect=replacing_connect
        ):
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    yielded = True
        self.assertFalse(yielded)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")

        replacement = self.root / "replacement-exit.sqlite"
        shutil.copy2(self.store_map.market, replacement)
        with self.assertRaises(StoreUnavailableError):
            with quiet_immutable_read_connection(
                self.store_map, StoreRole.MARKET
            ) as connection:
                self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
                os.replace(replacement, self.store_map.market)


    def test_maps_sqlite_open_errors_and_closes_the_descriptor(self) -> None:
        real_open = os.open
        descriptors: list[int] = []

        def traced_open(*args: object, **kwargs: object) -> int:
            descriptor = real_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch.object(
            stores_module.os, "open", side_effect=traced_open
        ), patch.object(
            stores_module.sqlite3,
            "connect",
            side_effect=sqlite3.OperationalError("unsafe provider detail"),
        ):
            with self.assertRaises(StoreUnavailableError) as caught:
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    self.fail("SQLite open failure must not yield")
        self.assertNotIn("unsafe provider detail", str(caught.exception))
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_detects_pre_yield_sidecar_drift_and_closes_connection(self) -> None:
        real_connect = sqlite3.connect
        sidecar = Path(f"{self.store_map.market}-shm")
        connections: list[sqlite3.Connection] = []
        yielded = False

        def drifting_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            sidecar.write_bytes(b"x")
            return connection

        with patch.object(
            stores_module.sqlite3, "connect", side_effect=drifting_connect
        ):
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    yielded = True
        self.assertFalse(yielded)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")

    def test_body_exception_still_closes_connection_and_descriptor(self) -> None:
        class BodyFailure(Exception):
            pass

        real_connect = sqlite3.connect
        real_open = os.open
        connections: list[sqlite3.Connection] = []
        descriptors: list[int] = []

        def traced_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        def traced_open(*args: object, **kwargs: object) -> int:
            descriptor = real_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with patch.object(
            stores_module.sqlite3, "connect", side_effect=traced_connect
        ), patch.object(stores_module.os, "open", side_effect=traced_open):
            with self.assertRaises(BodyFailure):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    raise BodyFailure

        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_ordinary_reader_remains_wal_aware(self) -> None:
        writer = sqlite3.connect(self.store_map.market)
        try:
            self.assertEqual(
                writer.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                "wal",
            )
            writer.execute(
                "CREATE TABLE active_wal_probe(value INTEGER NOT NULL)"
            )
            writer.execute("INSERT INTO active_wal_probe(value) VALUES (7)")
            writer.commit()
            wal = Path(f"{self.store_map.market}-wal")
            self.assertTrue(wal.is_file())
            self.assertGreater(wal.stat().st_size, 0)

            with stores_module.read_connection(
                self.store_map, StoreRole.MARKET
            ) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM active_wal_probe"
                    ).fetchone()[0],
                    7,
                )
            with self.assertRaises(StoreUnavailableError):
                with quiet_immutable_read_connection(
                    self.store_map, StoreRole.MARKET
                ):
                    self.fail("quiet reader must reject an active WAL")
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
