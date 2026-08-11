from __future__ import annotations

import hashlib
import math
import multiprocessing
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from quant_data.composition import (
    BoundedCrossStoreComposer,
    ComponentBatch,
    ComponentRow,
    CompositionContext,
    read_ingestion_run_component,
)
from quant_data.errors import ConflictError, MigrationError, RegistryError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.fixtures import FixtureManifest
from quant_data.ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    SnapshotWrite,
    WriteResult,
)
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro import MacroFixtureImporter
from quant_data.market import DailyPriceImporter
from quant_data.migrations import initialize_all
from quant_data.registry import load_registry, stage2_registry_profile
from quant_data.stage1 import explicit_store_map
from quant_data.stores import (
    STORE_ROLES,
    HeldWriteLocks,
    StoreMap,
    StoreRole,
    StoreWriteLock,
    acquire_write_locks,
    acquire_write_session,
    dataset_read_connection,
    physical_lock_key,
    resolve_store_map,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
FIXTURE_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "manifest.json"
CONTROL_TABLES = (
    "schema_migrations",
    "dataset_registry",
    "dataset_identity_contracts",
    "ingestion_runs",
    "ingestion_run_outputs",
    "ingestion_run_failures",
    "ingestion_artifacts",
    "ingestion_snapshots",
    "ingestion_snapshot_artifacts",
    "data_quality_results",
)


def _registry():
    return stage2_registry_profile(
        load_registry(REGISTRY_PATH, project_root=PROJECT_ROOT, environment={})
    )


def _opposite_order_lock_worker(paths, roles, queue) -> None:
    stores = StoreMap.from_mapping(paths)
    with acquire_write_locks(stores, roles, timeout_seconds=2.0):
        queue.put((os.getpid(), "entered", time.monotonic()))
        time.sleep(0.08)
        queue.put((os.getpid(), "exited", time.monotonic()))


class Stage2RegistryTests(unittest.TestCase):
    def test_registry_is_the_single_stage2_ownership_source(self) -> None:
        registry = _registry()
        self.assertEqual(registry.schema_id, "quant_data.system_registry")
        self.assertEqual(registry.registry_version, "2.0.0")
        self.assertEqual(len(registry.stores), 4)
        self.assertEqual(len(registry.migrations), 10)
        self.assertEqual(
            {store.id: store.control_tables for store in registry.stores},
            {role.value: CONTROL_TABLES for role in STORE_ROLES},
        )
        self.assertEqual(
            {dataset.id for dataset in registry.datasets},
            {
                "fixture.market.daily_price_evidence",
                "fixture.market.instruments",
                "fixture.market.daily_prices",
                "fixture.macro.rtdsm_employ_evidence",
                "fixture.macro.rtdsm_employ",
            },
        )
        for dataset in registry.datasets:
            self.assertEqual(len(dataset.identity_sha256), 64)
            self.assertTrue(dataset.temporal)
            self.assertTrue(dataset.freshness)
            self.assertTrue(dataset.quality_contract)

    def _load_mutation(self, mutate) -> None:
        raw = loads_strict(REGISTRY_PATH.read_bytes())
        mutate(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(dumps_strict(raw), encoding="utf-8")
            load_registry(path, project_root=PROJECT_ROOT, environment={})

    def test_registry_rejects_collisions_mutable_identity_and_unsafe_routing(self) -> None:
        with self.assertRaises(RegistryError):
            self._load_mutation(
                lambda raw: raw["datasets"][0]["identity"]["stable_fields"].append(
                    "last_successful_run_id"
                )
            )
        with self.assertRaises(RegistryError):
            self._load_mutation(
                lambda raw: raw["migrations"][1].__setitem__(
                    "resource", raw["migrations"][0]["resource"]
                )
            )
        with self.assertRaises(RegistryError):
            self._load_mutation(
                lambda raw: raw["stores"][0].__setitem__(
                    "default_path", "data/quant_data.sqlite"
                )
            )
        with self.assertRaises(RegistryError):
            self._load_mutation(
                lambda raw: raw["datasets"][4]["tool_ids"].append(
                    "market.get_returns"
                )
            )
        with self.assertRaises(RegistryError):
            self._load_mutation(
                lambda raw: raw["datasets"][0].__setitem__("layer", "research")
            )

    def test_physical_registry_uses_the_three_accepted_contract_layers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_map = explicit_store_map(Path(directory))
            registry = _registry()
            initialize_all(store_map, registry)
            connection = sqlite3.connect(store_map.company)
            for ordinal, layer in enumerate(("evidence", "canonical", "derived"), start=1):
                connection.execute(
                    """
                    INSERT INTO dataset_registry (
                        dataset_id, store_role, layer, schema_version,
                        relations_json, active, registered_at
                    ) VALUES (?, 'company', ?, '1.0.0', '[]', 0, ?)
                    """,
                    (f"test.layer.{ordinal}", layer, "2026-08-09T12:00:00-04:00"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO dataset_registry (
                        dataset_id, store_role, layer, schema_version,
                        relations_json, active, registered_at
                    ) VALUES ('test.layer.research', 'company', 'research',
                              '1.0.0', '[]', 0, '2026-08-09T12:00:00-04:00')
                    """
                )
            connection.rollback()
            connection.close()


class Stage2RoutingAndLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = _registry()

    def test_resolver_precedence_is_explicit_and_never_uses_ambient_state(self) -> None:
        defaults = resolve_store_map(
            self.registry,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.assertEqual(defaults.market, PROJECT_ROOT / "data" / "market_data.sqlite")
        self.assertFalse((PROJECT_ROOT / "data").exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                f"QUANT_{role.value.upper()}_DB_PATH": str(root / f"env-{role.value}.sqlite")
                for role in STORE_ROLES
            }
            environment = resolve_store_map(
                self.registry,
                project_root=PROJECT_ROOT,
                environment=env,
            )
            self.assertTrue(all("env-" in path.name for _, path in environment.items()))
            explicit_paths = {
                role.value: root / f"explicit-{role.value}.sqlite" for role in STORE_ROLES
            }
            explicit = resolve_store_map(
                self.registry,
                project_root=PROJECT_ROOT,
                environment=env,
                explicit_paths=explicit_paths,
            )
            self.assertTrue(all("explicit-" in path.name for _, path in explicit.items()))
            with self.assertRaises(ValidationError):
                resolve_store_map(
                    self.registry,
                    project_root=PROJECT_ROOT,
                    environment={"QUANT_DB_PATH": str(root / "unified.sqlite")},
                )
            with self.assertRaises(ValidationError):
                resolve_store_map(
                    self.registry,
                    project_root=PROJECT_ROOT,
                    environment={"QUANT_MARKET_DB_PATH": ""},
                )

    def test_aliases_and_broad_targets_fail_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValidationError):
                StoreMap.four_explicit(
                    market=root,
                    macro=root / "macro.sqlite",
                    company=root / "company.sqlite",
                    news=root / "news.sqlite",
                )
            original = root / "original.sqlite"
            original.write_bytes(b"not a database")
            hardlink = root / "hardlink.sqlite"
            os.link(original, hardlink)
            with self.assertRaises(ConflictError):
                StoreMap.four_explicit(
                    market=original,
                    macro=hardlink,
                    company=root / "company.sqlite",
                    news=root / "news.sqlite",
                )
            symlink = root / "symlink.sqlite"
            symlink.symlink_to(original)
            with self.assertRaises(ConflictError):
                StoreMap.four_explicit(
                    market=original,
                    macro=symlink,
                    company=root / "company.sqlite",
                    news=root / "news.sqlite",
                )

    def test_multi_lock_order_and_key_contract_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            expected = hashlib.sha256(
                b"quant-data-sqlite-lock-v1\x00" + stores.market.as_uri().encode("utf-8")
            ).hexdigest()
            self.assertEqual(physical_lock_key(stores.market), expected)
            with acquire_write_locks(
                stores,
                (StoreRole.NEWS, StoreRole.MARKET, StoreRole.MACRO),
            ) as identities:
                self.assertEqual(
                    [identity.canonical_uri for identity in identities],
                    sorted(identity.canonical_uri for identity in identities),
                )

    def test_write_session_exposes_sanitized_active_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            requested = (StoreRole.NEWS, StoreRole.MARKET, StoreRole.MACRO)
            expected = tuple(
                identity
                for identity in sorted(
                    stores.identities(), key=lambda identity: identity.canonical_uri
                )
                if identity.role in requested
            )
            with acquire_write_session(stores, requested, timeout_seconds=0.05) as held:
                self.assertIsInstance(held, HeldWriteLocks)
                self.assertTrue(held.active)
                self.assertEqual(held.roles, tuple(identity.role for identity in expected))
                self.assertEqual(
                    tuple(identity.role for identity in held.identities),
                    tuple(identity.role for identity in expected),
                )
                self.assertEqual(
                    tuple(identity.lock_key for identity in held.identities),
                    tuple(identity.lock_key for identity in expected),
                )
                self.assertEqual(
                    len(held.acquisition_wait_seconds), len(held.identities)
                )
                self.assertTrue(
                    all(
                        isinstance(wait_seconds, float)
                        and math.isfinite(wait_seconds)
                        and wait_seconds >= 0.0
                        for wait_seconds in held.acquisition_wait_seconds
                    )
                )
                self.assertTrue(held.holds(StoreRole.MARKET))
                self.assertFalse(held.holds(StoreRole.COMPANY))
                self.assertNotIn(str(stores.market), repr(held.identities))
                with self.assertRaises(AttributeError):
                    held.identities += ()
                with self.assertRaises(AttributeError):
                    held.acquisition_wait_seconds += ()
            self.assertFalse(held.active)
            self.assertFalse(held.holds(StoreRole.MARKET))
            self.assertEqual(held.roles, tuple(identity.role for identity in expected))

        with self.assertRaises(TypeError):
            HeldWriteLocks()
        forged = object.__new__(HeldWriteLocks)
        self.assertFalse(forged.active)
        self.assertEqual(forged.identities, ())

    def test_write_session_uses_one_deadline_and_reverse_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            requested = (StoreRole.NEWS, StoreRole.MARKET, StoreRole.MACRO)
            expected_paths = [
                identity.path
                for identity in sorted(
                    (
                        identity
                        for identity in stores.identities()
                        if identity.role in requested
                    ),
                    key=lambda identity: identity.canonical_uri,
                )
            ]
            events: list[tuple[object, ...]] = []

            class RecordingLock:
                def __init__(self, path: Path, *, timeout_seconds: float) -> None:
                    self.path = path
                    events.append(("constructed", path, timeout_seconds))

                def _acquire_until(self, deadline: float) -> "RecordingLock":
                    events.append(("acquired", self.path, deadline))
                    return self

                def __exit__(self, exc_type, exc, traceback) -> None:
                    events.append(("released", self.path))

            with patch("quant_data.stores.StoreWriteLock", RecordingLock):
                with acquire_write_session(
                    stores, requested, timeout_seconds=0.25
                ) as held:
                    self.assertTrue(held.active)
                    self.assertEqual(
                        held.roles,
                        tuple(
                            identity.role
                            for identity in sorted(
                                (
                                    identity
                                    for identity in stores.identities()
                                    if identity.role in requested
                                ),
                                key=lambda identity: identity.canonical_uri,
                            )
                        ),
                    )
                    self.assertTrue(
                        all(wait_seconds >= 0.0 for wait_seconds in held.acquisition_wait_seconds)
                    )

            acquired = [event for event in events if event[0] == "acquired"]
            released = [event for event in events if event[0] == "released"]
            self.assertEqual([event[1] for event in acquired], expected_paths)
            self.assertEqual(len({event[2] for event in acquired}), 1)
            self.assertEqual(
                [event[1] for event in released],
                list(reversed(expected_paths)),
            )

    def test_multi_lock_uses_one_deadline_and_releases_partial_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            with StoreWriteLock(stores.market):
                with self.assertRaises(ConflictError):
                    with acquire_write_locks(
                        stores,
                        (StoreRole.MACRO, StoreRole.MARKET),
                        timeout_seconds=0.05,
                    ):
                        pass
                # Macro sorts before market and was acquired before the
                # timeout. The failed set must have released it.
                with acquire_write_locks(
                    stores, (StoreRole.MACRO,), timeout_seconds=0.05
                ) as identities:
                    self.assertEqual(identities[0].role, StoreRole.MACRO)

    def test_lock_timeouts_reject_boolean_nonfinite_and_negative_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            for value in (True, math.nan, math.inf, -1.0):
                with self.subTest(value=value):
                    with self.assertRaises(ValidationError):
                        StoreWriteLock(stores.market, timeout_seconds=value)
                    with self.assertRaises(ValidationError):
                        with acquire_write_locks(
                            stores,
                            (StoreRole.MARKET,),
                            timeout_seconds=value,
                        ):
                            pass

    def test_opposite_order_processes_serialize_without_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stores = explicit_store_map(Path(directory))
            paths = {role.value: str(path) for role, path in stores.items()}
            context = multiprocessing.get_context("fork")
            queue = context.Queue()
            first = context.Process(
                target=_opposite_order_lock_worker,
                args=(paths, ("market", "macro"), queue),
            )
            second = context.Process(
                target=_opposite_order_lock_worker,
                args=(paths, ("macro", "market"), queue),
            )
            first.start()
            second.start()
            first.join(5)
            second.join(5)
            self.assertEqual(first.exitcode, 0)
            self.assertEqual(second.exitcode, 0)
            events = [queue.get(timeout=1) for _ in range(4)]
            intervals = {}
            for process_id, event, moment in events:
                intervals.setdefault(process_id, {})[event] = moment
            self.assertEqual(len(intervals), 2)
            ordered = sorted(
                (values["entered"], values["exited"]) for values in intervals.values()
            )
            self.assertLessEqual(ordered[0][1], ordered[1][0])


    def test_lock_open_failure_leaves_no_handle_to_release(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            lock = StoreWriteLock(Path(directory) / "market.sqlite")
            with patch("builtins.open", side_effect=OSError("simulated open failure")):
                with self.assertRaises(OSError):
                    lock.__enter__()
            self.assertIsNone(lock._handle)
            lock.__exit__(None, None, None)


class Stage2ControlPlaneAndCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store_map = explicit_store_map(self.root / "stores")
        self.registry = _registry()
        initialize_all(self.store_map, self.registry)
        manifest = FixtureManifest.load(FIXTURE_MANIFEST, project_root=PROJECT_ROOT)
        self.market = DailyPriceImporter(self.store_map, manifest)
        self.macro = MacroFixtureImporter(self.store_map, manifest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_shared_control_plane_records_lineage_and_exact_replay_writes_nothing(self) -> None:
        self.market.import_fixture("market.base")
        self.macro.import_fixture("macro.first_vintage")
        before = mutation_fingerprint(self.store_map)
        self.assertEqual(self.market.import_fixture("market.base").outcome, "unchanged")
        self.assertEqual(self.macro.import_fixture("macro.first_vintage").outcome, "unchanged")
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])
        for role, output_count in ((StoreRole.MARKET, 3), (StoreRole.MACRO, 2)):
            path = self.store_map.path(role)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            expected_counts = {
                "ingestion_artifacts": 1,
                "ingestion_run_outputs": output_count,
                "ingestion_run_failures": 0,
                "ingestion_snapshots": 1,
                "ingestion_snapshot_artifacts": 1,
                "data_quality_results": 1,
            }
            for relation, expected in expected_counts.items():
                self.assertEqual(
                    connection.execute(f'SELECT count(*) FROM "{relation}"').fetchone()[0],
                    expected,
                )
            artifact_id = connection.execute(
                "SELECT artifact_id FROM ingestion_artifacts"
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE ingestion_artifacts SET media_type='application/json' WHERE artifact_id=?",
                    (artifact_id,),
                )
            connection.rollback()
            connection.execute("PRAGMA recursive_triggers=OFF")
            before_replace = mutation_fingerprint(self.store_map)
            for relation in (
                "store_metadata",
                "schema_migrations",
                "dataset_registry",
                "dataset_identity_contracts",
                "ingestion_runs",
                "ingestion_run_outputs",
                "ingestion_artifacts",
                "ingestion_snapshots",
                "ingestion_snapshot_artifacts",
                "data_quality_results",
            ):
                with self.subTest(role=role.value, relation=relation):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            f'INSERT OR REPLACE INTO "{relation}" '
                            f'SELECT * FROM "{relation}" LIMIT 1'
                        )
                    connection.rollback()
            self.assertEqual(
                before_replace["sha256"],
                mutation_fingerprint(self.store_map)["sha256"],
            )
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            connection.close()

    def test_invalid_rejected_and_partial_publications_never_succeed(self) -> None:
        coordinator = IngestionCoordinator(self.store_map)

        def candidate(
            semantic_identity: str,
            *,
            content_sha256: str,
            validation_state: str = "validated",
            completeness: str = "complete",
            run_outcome: str = "succeeded",
        ) -> WriteResult:
            artifact_id = f"artifact-{semantic_identity[:8]}"
            snapshot_id = f"snapshot-{semantic_identity[:8]}"
            return WriteResult(
                written_count=0,
                artifacts=(
                    ArtifactWrite(
                        artifact_id=artifact_id,
                        dataset_id="fixture.market.daily_prices",
                        content_sha256=content_sha256,
                        media_type="text/csv",
                        byte_count=1,
                        source_reference="fixtures/stage2-negative.csv",
                        request_scope={},
                        captured_at="2026-08-09T12:00:00-04:00",
                        captured_precision="datetime",
                        normalization_version="1.0.0",
                    ),
                ),
                snapshot=SnapshotWrite(
                    snapshot_id=snapshot_id,
                    dataset_id="fixture.market.daily_prices",
                    semantic_identity=semantic_identity,
                    scope={},
                    completeness=completeness,
                    row_count=0,
                    captured_at="2026-08-09T12:00:00-04:00",
                    captured_precision="datetime",
                    validation_state=validation_state,
                    artifact_ids=(artifact_id,),
                ),
                quality_results=(),
                run_outcome=run_outcome,
            )

        def execute(run_id: str, semantic_identity: str, result: WriteResult) -> None:
            coordinator.execute(
                role=StoreRole.MARKET,
                dataset_id="fixture.market.daily_prices",
                output_dataset_ids=("fixture.market.daily_prices",),
                semantic_identity=semantic_identity,
                run_id=run_id,
                command="test.stage2_rejection",
                scope={},
                started_at="2026-08-09T12:00:00-04:00",
                completed_at="2026-08-09T12:00:01-04:00",
                fetched_count=0,
                writer=lambda connection, active_run_id: result,
            )

        before_invalid_identity = mutation_fingerprint(self.store_map)
        with self.assertRaises(ValidationError):
            execute("bad-identity", "g" * 64, candidate("a" * 64, content_sha256="a" * 64))
        self.assertEqual(
            before_invalid_identity["sha256"],
            mutation_fingerprint(self.store_map)["sha256"],
        )

        cases = (
            ("bad-content", "a" * 64, candidate("a" * 64, content_sha256="g" * 64)),
            (
                "rejected-snapshot",
                "b" * 64,
                candidate("b" * 64, content_sha256="b" * 64, validation_state="rejected"),
            ),
            (
                "partial-snapshot",
                "c" * 64,
                candidate("c" * 64, content_sha256="c" * 64, run_outcome="partial"),
            ),
        )
        for run_id, semantic_identity, result in cases:
            with self.subTest(run_id=run_id), self.assertRaises(ValidationError):
                execute(run_id, semantic_identity, result)

        before_reused_failure = mutation_fingerprint(self.store_map)
        with self.assertRaises(ConflictError):
            execute(
                "bad-content",
                "a" * 64,
                candidate("a" * 64, content_sha256="a" * 64),
            )
        self.assertEqual(
            before_reused_failure["sha256"],
            mutation_fingerprint(self.store_map)["sha256"],
        )

        connection = sqlite3.connect(self.store_map.market)
        self.assertEqual(connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM ingestion_artifacts").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM ingestion_snapshots").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM ingestion_run_outputs").fetchone()[0], 0)
        self.assertEqual(
            list(
                connection.execute(
                    "SELECT status FROM ingestion_run_failures ORDER BY run_id"
                )
            ),
            [("rejected",), ("partial",), ("rejected",)],
        )
        connection.execute("PRAGMA recursive_triggers=OFF")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT OR REPLACE INTO ingestion_run_failures
                SELECT * FROM ingestion_run_failures LIMIT 1
                """
            )
        connection.rollback()
        connection.close()

    def test_success_lineage_and_checkpoints_reject_orphans(self) -> None:
        connection = sqlite3.connect(self.store_map.market)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, semantic_identity, command, scope_json,
                status, started_at, fetched_count, code_version
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, 0, ?)
            """,
            (
                "orphan-probe",
                "fixture.market.daily_prices",
                "d" * 64,
                "test.orphan",
                "{}",
                "2026-08-09T12:00:00-04:00",
                "test",
            ),
        )
        connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ingestion_runs SET semantic_identity=? WHERE run_id=?",
                ("e" * 64, "orphan-probe"),
            )
        connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status='succeeded', completed_at=?, artifact_id=?, snapshot_id=?
                WHERE run_id=?
                """,
                (
                    "2026-08-09T12:00:01-04:00",
                    "orphan-artifact",
                    "orphan-snapshot",
                    "orphan-probe",
                ),
            )
        connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE dataset_registry
                SET last_successful_run_id=?, last_semantic_identity=?
                WHERE dataset_id=?
                """,
                ("orphan-probe", "d" * 64, "fixture.market.daily_prices"),
            )
        connection.rollback()
        connection.close()

    def test_dataset_identity_drift_and_undeclared_relations_fail_closed(self) -> None:
        before = mutation_fingerprint(self.store_map)
        raw = loads_strict(REGISTRY_PATH.read_bytes())
        raw["datasets"][0]["identity"]["stable_fields"].append("provider")
        path = self.root / "mutated-registry.json"
        path.write_text(dumps_strict(raw), encoding="utf-8")
        mutated = stage2_registry_profile(
            load_registry(path, project_root=PROJECT_ROOT, environment={})
        )
        with self.assertRaises(MigrationError):
            initialize_all(self.store_map, mutated)
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

        connection = sqlite3.connect(self.store_map.company)
        connection.execute("CREATE TABLE undeclared_stage2_relation(value TEXT) STRICT")
        connection.commit()
        connection.close()
        with self.assertRaises(MigrationError):
            initialize_all(self.store_map, self.registry)

    def test_dataset_gateway_and_actual_cross_store_composition_are_read_only(self) -> None:
        self.market.import_fixture("market.base")
        self.macro.import_fixture("macro.first_vintage")
        with dataset_read_connection(
            self.store_map, self.registry, "fixture.market.daily_prices"
        ) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM ingestion_runs")
        context = CompositionContext(
            cutoff="2026-07-20T00:00:00Z",
            date_only_policy="completed_date",
            component_limit=100,
            joined_limit=100,
        )
        before = mutation_fingerprint(self.store_map)
        components = (
            read_ingestion_run_component(
                self.store_map,
                self.registry,
                dataset_id="fixture.market.daily_prices",
                context=context,
            ),
            read_ingestion_run_component(
                self.store_map,
                self.registry,
                dataset_id="fixture.macro.rtdsm_employ",
                context=context,
            ),
        )
        result = BoundedCrossStoreComposer(self.registry).compose(
            context=context, components=components
        )
        self.assertEqual(result["consistency"], "best_effort_multi_store")
        self.assertEqual(result["cutoff"], context.cutoff)
        self.assertEqual({item["store_role"] for item in result["components"]}, {"market", "macro"})
        for component in result["components"]:
            receipt = component["store_receipt"]
            self.assertEqual(receipt["availability_field"], "captured_at")
            self.assertEqual(receipt["read_snapshot"], "sqlite_read_transaction")
            self.assertEqual(receipt["cutoff"], context.cutoff)
            self.assertEqual(
                receipt["read_start_state_sha256"],
                receipt["read_completion_state_sha256"],
            )
        self.assertEqual(before["sha256"], mutation_fingerprint(self.store_map)["sha256"])

    def test_positive_join_is_bounded_and_uses_one_cutoff_without_invented_domain_mapping(self) -> None:
        context = CompositionContext(
            cutoff="2026-07-20T00:00:00Z",
            date_only_policy="completed_date",
            component_limit=2,
            joined_limit=1,
        )
        market = ComponentBatch(
            dataset_id="fixture.market.daily_prices",
            store_role="market",
            rows=(
                ComponentRow("fixture.join.shared", "2026-07-19", {"close": "640"}),
                ComponentRow("fixture.join.market_only", "2026-07-21", {"close": "641"}),
            ),
            store_receipt={"sha256": "1" * 64},
        )
        macro = ComponentBatch(
            dataset_id="fixture.macro.rtdsm_employ",
            store_role="macro",
            rows=(
                ComponentRow("fixture.join.shared", "2026-07-18", {"value": "101"}),
            ),
            store_receipt={"sha256": "2" * 64},
        )
        result = BoundedCrossStoreComposer(self.registry).compose(
            context=context,
            components=(market, macro),
        )
        reversed_result = BoundedCrossStoreComposer(self.registry).compose(
            context=context,
            components=(macro, market),
        )
        self.assertEqual(result, reversed_result)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["join_identity"], "fixture.join.shared")
        self.assertEqual(result["rows"][0]["missing_datasets"], [])
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()
