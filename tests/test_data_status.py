from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from quant_data.boundary import ToolDispatcher
from quant_data.tool_platform.arguments import DatasetStatusArgumentsV1
from quant_data.tool_platform.context import (
    CapabilitySet,
    CancellationToken,
    Deadline,
    ExecutionBudget,
    FixedClock,
    ToolExecutionContext,
)
from quant_data.tool_platform.data_status_access import invoke_dataset_status
from quant_data.data_status import (
    DataStatusSourceKind,
    DataStatusTarget,
    FreshnessPolicy,
    ReferencePeriodKind,
    data_status_records,
    data_status_snapshot,
    registry_data_status_targets,
)
from quant_data.registry import CANONICAL_REGISTRY_PATH, load_registry
from quant_data.stores import StoreMap, StoreRole


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DataStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        root = Path(self.temporary.name)
        self.stores = StoreMap.four_explicit(
            market=root / "market.sqlite",
            macro=root / "macro.sqlite",
            company=root / "company.sqlite",
            news=root / "news.sqlite",
        )
        for role, path in self.stores.items():
            self._create_store(path, role)
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _create_store(path: Path, role: StoreRole) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE store_metadata (
                singleton INTEGER PRIMARY KEY,
                store_role TEXT NOT NULL
            );
            CREATE TABLE ingestion_runs (
                run_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                status TEXT NOT NULL,
                completed_at TEXT,
                fetched_count INTEGER NOT NULL,
                written_count INTEGER NOT NULL
            );
            CREATE TABLE ingestion_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                captured_precision TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                completeness TEXT NOT NULL
            );
            CREATE TABLE ingestion_run_failures (
                run_id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                status TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                fetched_count INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO store_metadata (singleton, store_role) VALUES (1, ?)",
            (role.value,),
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _policy(*, stale_after: str | None = "P1D") -> FreshnessPolicy | None:
        if stale_after is None:
            return None
        return FreshnessPolicy(
            cadence="daily",
            expected_lag="P0D",
            stale_after=stale_after,
            measured_from="successful_capture",
        )

    def _target(
        self,
        role: StoreRole,
        dataset_id: str,
        *,
        stale_after: str | None = "P1D",
    ) -> DataStatusTarget:
        return DataStatusTarget(
            id=f"{role.value}.{dataset_id}",
            store=role,
            dataset_id=dataset_id,
            freshness=self._policy(stale_after=stale_after),
        )

    def _seed_success(
        self,
        role: StoreRole,
        dataset_id: str,
        captured_at: str,
    ) -> None:
        connection = sqlite3.connect(self.stores.path(role))
        connection.execute(
            """
            INSERT INTO ingestion_runs (
                run_id, dataset_id, status, completed_at, fetched_count, written_count
            ) VALUES (?, ?, 'succeeded', ?, 9, 7)
            """,
            (f"run-{dataset_id}", dataset_id, captured_at),
        )
        connection.execute(
            """
            INSERT INTO ingestion_snapshots (
                snapshot_id, run_id, dataset_id, captured_at, captured_precision,
                row_count, completeness
            ) VALUES (?, ?, ?, ?, 'datetime', 7, 'complete')
            """,
            (f"snapshot-{dataset_id}", f"run-{dataset_id}", dataset_id, captured_at),
        )
        connection.commit()
        connection.close()

    def test_reports_current_record_from_a_successful_capture(self) -> None:
        target = self._target(StoreRole.MARKET, "market.current")
        self._seed_success(
            StoreRole.MARKET,
            "market.current",
            "2026-09-01T11:00:00Z",
        )

        records = data_status_records(self.stores, targets=(target,), now=self.now)

        self.assertEqual(records[0]["status"], "current")
        self.assertEqual(
            records[0]["status_reason"],
            "within_successful_capture_stale_threshold",
        )
        self.assertEqual(records[0]["store"], "market")
        self.assertEqual(records[0]["dataset_id"], "market.current")
        self.assertEqual(records[0]["latest_reference_period"], None)
        self.assertEqual(
            records[0]["latest_successful_capture"],
            {
                "snapshot_id": "snapshot-market.current",
                "captured_at": {"value": "2026-09-01T11:00:00Z", "precision": "datetime"},
                "row_count": 7,
                "completeness": "complete",
            },
        )
        self.assertEqual(records[0]["scope"], "retained_data_only")

    def test_reports_stale_record_after_the_declared_threshold(self) -> None:
        target = self._target(StoreRole.MACRO, "macro.stale")
        self._seed_success(
            StoreRole.MACRO,
            "macro.stale",
            "2026-08-30T12:00:00Z",
        )

        records = data_status_records(self.stores, targets=(target,), now=self.now)

        self.assertEqual(records[0]["status"], "stale")
        self.assertEqual(
            records[0]["status_reason"],
            "successful_capture_exceeds_stale_threshold",
        )
        self.assertEqual(records[0]["stale_at"], "2026-08-31T12:00:00.000000Z")

    def test_reports_no_data_when_there_is_no_retained_outcome(self) -> None:
        target = self._target(StoreRole.COMPANY, "company.empty")

        records = data_status_records(self.stores, targets=(target,), now=self.now)

        self.assertEqual(records[0]["status"], "no_data")
        self.assertEqual(records[0]["status_reason"], "no_retained_outcome")
        self.assertIsNone(records[0]["latest_successful_capture"])
        self.assertIsNone(records[0]["latest_retained_outcome"])

    def test_reports_unknown_without_a_declared_stale_threshold(self) -> None:
        target = self._target(
            StoreRole.NEWS,
            "news.unknown",
            stale_after=None,
        )
        self._seed_success(
            StoreRole.NEWS,
            "news.unknown",
            "2026-09-01T11:00:00Z",
        )

        snapshot = data_status_snapshot(self.stores, targets=(target,), now=self.now)
        record = snapshot["records"][0]

        self.assertEqual(snapshot["evaluated_at"], "2026-09-01T12:00:00.000000Z")
        self.assertEqual(record["status"], "unknown")
        self.assertEqual(record["status_reason"], "stale_threshold_not_declared")
        self.assertIsNone(record["freshness"])

    def test_current_news_source_uses_the_established_timestamp_shape(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        target = DataStatusTarget(
            id="news.source.fmp_stock_latest",
            store=StoreRole.NEWS,
            dataset_id="news.fmp.stock_latest_current_evidence",
            freshness=self._policy(),
            source_kind=DataStatusSourceKind.CURRENT_NEWS,
            source_id="fmp_stock_latest",
        )
        retained_status = (
            {
                "source_id": "fmp_stock_latest",
                "latest_successful_capture": {
                    "capture_id": "capture-news-current",
                    "captured_at": "2026-09-01T11:00:00Z",
                    "provider_row_count": 3,
                    "accepted_row_count": 2,
                    "rejected_row_count": 1,
                },
                "latest_outcome": {
                    "outcome_id": "outcome-news-current",
                    "outcome_kind": "succeeded",
                    "recorded_at": "2026-09-01T11:00:00Z",
                    "http_status": 200,
                    "provider_row_count": 3,
                    "accepted_row_count": 2,
                    "rejected_row_count": 1,
                },
            },
        )

        with mock.patch(
            "quant_data.news.tool_repository.CurrentNewsToolRepository.source_status_evidence",
            return_value=retained_status,
        ):
            records = data_status_records(
                self.stores,
                targets=(target,),
                registry=registry,
                now=self.now,
            )

        self.assertEqual(records[0]["status"], "current")
        self.assertEqual(
            records[0]["status_reason"],
            "within_successful_capture_stale_threshold",
        )
        self.assertEqual(
            records[0]["latest_successful_capture"]["captured_at"],
            "2026-09-01T11:00:00Z",
        )

    def test_reports_a_fixed_macro_series_reference_period(self) -> None:
        dataset_id = "macro.series"
        self._seed_success(
            StoreRole.MACRO,
            dataset_id,
            "2026-09-01T11:00:00Z",
        )
        connection = sqlite3.connect(self.stores.path(StoreRole.MACRO))
        connection.executescript(
            """
            CREATE TABLE macro_observation_versions (
                version_id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                captured_precision TEXT NOT NULL
            );
            CREATE TABLE macro_observations (
                series_id TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                current_version_id TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO macro_observation_versions VALUES
                ('version-1', '2026-09-01T11:00:00Z', 'datetime')
            """
        )
        connection.execute(
            """
            INSERT INTO macro_observations VALUES
                ('macro.example.series', '2026-08-01', '2026-08-31', 'version-1')
            """
        )
        connection.commit()
        connection.close()
        target = DataStatusTarget(
            id="macro.series.reference",
            store=StoreRole.MACRO,
            dataset_id=dataset_id,
            freshness=self._policy(),
            reference_kind=ReferencePeriodKind.MACRO_CURRENT_SERIES,
            series_id="macro.example.series",
        )

        records = data_status_records(self.stores, targets=(target,), now=self.now)

        self.assertEqual(
            records[0]["latest_reference_period"],
            {
                "period_start": "2026-08-01",
                "period_end": "2026-08-31",
                "precision": "date",
            },
        )
        self.assertEqual(records[0]["status"], "current")

    def test_read_is_mutation_neutral_across_the_fixed_four_store_map(self) -> None:
        targets = tuple(
            self._target(role, f"{role.value}.dataset") for role in StoreRole
        )
        for role in StoreRole:
            self._seed_success(
                role,
                f"{role.value}.dataset",
                "2026-09-01T11:00:00Z",
            )
        before = {
            role: (
                hashlib.sha256(self.stores.path(role).read_bytes()).hexdigest(),
                self.stores.path(role).stat().st_mtime_ns,
                Path(f"{self.stores.path(role)}-wal").exists(),
                Path(f"{self.stores.path(role)}-shm").exists(),
            )
            for role in StoreRole
        }

        records = data_status_records(self.stores, targets=targets, now=self.now)

        after = {
            role: (
                hashlib.sha256(self.stores.path(role).read_bytes()).hexdigest(),
                self.stores.path(role).stat().st_mtime_ns,
                Path(f"{self.stores.path(role)}-wal").exists(),
                Path(f"{self.stores.path(role)}-shm").exists(),
            )
            for role in StoreRole
        }
        self.assertEqual(len(records), 4)
        self.assertTrue(all(record["status"] == "current" for record in records))
        self.assertEqual(before, after)

    def test_public_tool_dispatches_the_bounded_retained_status_contract(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        before = {
            role: hashlib.sha256(self.stores.path(role).read_bytes()).hexdigest()
            for role in StoreRole
        }

        result = ToolDispatcher(self.stores, registry).call(
            "data.get_dataset_status",
            {
                "stores": [],
                "dataset_ids": [],
                "statuses": [],
                "limit": 128,
            },
            tool_version="1.0.0",
        )

        self.assertEqual(result["tool"], "data.get_dataset_status")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            len(result["records"]),
            len(registry_data_status_targets(registry)),
        )
        self.assertEqual(
            [warning["code"] for warning in result["warnings"]],
            ["not_live_operational_health"],
        )
        self.assertEqual(
            before,
            {
                role: hashlib.sha256(self.stores.path(role).read_bytes()).hexdigest()
                for role in StoreRole
            },
        )

    def test_public_adapter_uses_the_host_execution_clock(self) -> None:
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        context = ToolExecutionContext(
            store_map=self.stores,
            registry_revision=registry.revision,
            registry_sha256="a" * 64,
            request_id="data-status-fixed-clock",
            api_version="1.0",
            tool_name="data.get_dataset_status",
            tool_version="1.0.0",
            operation_graph_id="tool_platform.data.get_dataset_status.v1",
            operation_version="1.0.0",
            budget=ExecutionBudget(max_rows=128, max_operations=128),
            capabilities=CapabilitySet(),
            deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(
                monotonic_value=Decimal("0"),
                instant_value="2026-09-01T12:00:00Z",
            ),
            execution="read_only",
        )
        result = invoke_dataset_status(
            "data.get_dataset_status",
            DatasetStatusArgumentsV1(
                stores=(), dataset_ids=(), statuses=(), limit=128
            ),
            context,
            registry,
        )
        metrics = {
            field.name: field.value for field in result.diagnostics[0].metrics
        }
        self.assertEqual(metrics["evaluated_at"], "2026-09-01T12:00:00.000000Z")



    def _bounded_status_call(self, targets, *, stores=(), dataset_ids=(), statuses=(),
                             limit=128, max_operations=128):
        registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT, environment={},
        )
        context = ToolExecutionContext(
            store_map=self.stores, registry_revision=registry.revision,
            registry_sha256="a" * 64, request_id="bounded-data-status",
            api_version="1.0", tool_name="data.get_dataset_status",
            tool_version="1.0.0", operation_graph_id="tool_platform.data.get_dataset_status.v1",
            operation_version="1.0.0",
            budget=ExecutionBudget(max_rows=128, max_operations=max_operations),
            capabilities=CapabilitySet(), deadline=Deadline(),
            cancellation=CancellationToken(),
            clock=FixedClock(monotonic_value=Decimal("0"), instant_value="2026-09-01T12:00:00Z"),
            execution="read_only",
        )
        with mock.patch(
            "quant_data.tool_platform.data_status_access.registry_data_status_targets",
            return_value=tuple(sorted(targets, key=lambda target: target.id)),
        ), mock.patch(
            "quant_data.tool_platform.data_status_access.data_status_snapshot",
            wraps=data_status_snapshot,
        ) as snapshot:
            result = invoke_dataset_status(
                "data.get_dataset_status",
                DatasetStatusArgumentsV1(stores=stores, dataset_ids=dataset_ids,
                                         statuses=statuses, limit=limit),
                context, registry,
            )
        self.assertEqual(snapshot.call_count, 1)
        return result, snapshot.call_args.kwargs["targets"]

    def test_public_limit_reads_only_returned_targets_with_exact_total(self):
        targets = tuple(self._target(role, "dataset") for role in StoreRole)
        for role in StoreRole:
            self._seed_success(role, "dataset", "2026-09-01T11:00:00Z")
        before = {role: hashlib.sha256(path.read_bytes()).hexdigest()
                  for role, path in self.stores.items()}
        full, _ = self._bounded_status_call(targets)
        limited, read_targets = self._bounded_status_call(targets, limit=1, max_operations=1)
        self.assertEqual(limited.records, full.records[:1])
        self.assertEqual(len(read_targets), 1)
        self.assertEqual(read_targets[0].id, min(target.id for target in targets))
        metrics = {field.name: field.value for field in limited.diagnostics[0].metrics}
        self.assertEqual(metrics["matched_count"], len(targets))
        self.assertEqual(metrics["returned_count"], 1)
        self.assertTrue(limited.truncation.applied)
        self.assertTrue(limited.truncation.has_more)
        self.assertEqual(limited.truncation.total_known_count, len(targets))
        self.assertEqual(before, {role: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for role, path in self.stores.items()})

    def test_public_filters_bound_store_reads_and_preserve_dataset_aliases(self):
        from dataclasses import replace
        shared = self._target(StoreRole.MARKET, "shared.dataset")
        alias = replace(shared, id="market.second-alias")
        excluded = self._target(StoreRole.MACRO, "shared.dataset")
        other = self._target(StoreRole.MARKET, "other.dataset")
        targets = (shared, alias, excluded, other)
        self._seed_success(StoreRole.MARKET, "shared.dataset", "2026-09-01T11:00:00Z")
        result, selected = self._bounded_status_call(
            targets, stores=("market",), dataset_ids=("shared.dataset",), limit=1,
        )
        self.assertEqual(selected, (alias,))
        self.assertEqual(result.truncation.total_known_count, 2)
        exact, exact_targets = self._bounded_status_call(
            targets, stores=("market",), dataset_ids=(shared.id,), limit=1,
        )
        self.assertEqual(exact_targets, (shared,))
        self.assertEqual(exact.truncation.total_known_count, 1)
        self.assertFalse(exact.truncation.has_more)
        self.assertEqual({f.name: f.value for f in exact.records[0].fields}["id"], shared.id)

    def test_public_status_filter_scans_candidates_before_limit(self):
        targets = tuple(self._target(StoreRole.MARKET, name) for name in ("a", "b", "c"))
        self._seed_success(StoreRole.MARKET, "a", "2026-08-29T11:00:00Z")
        for name in ("b", "c"):
            self._seed_success(StoreRole.MARKET, name, "2026-09-01T11:00:00Z")
        full, _ = self._bounded_status_call(targets)
        result, selected = self._bounded_status_call(targets, statuses=("current",), limit=1)
        self.assertEqual(selected, targets)
        self.assertEqual(result.records, full.records[1:2])
        self.assertEqual(result.truncation.total_known_count, 2)
        self.assertTrue(result.truncation.has_more)
        no_match, no_match_targets = self._bounded_status_call(targets, statuses=("unknown",), limit=1)
        self.assertEqual(no_match_targets, targets)
        self.assertEqual(no_match.records, ())
        self.assertEqual(no_match.truncation.total_known_count, 0)
        self.assertFalse(no_match.truncation.applied)

    def test_public_empty_metadata_match_opens_no_store(self):
        targets = (self._target(StoreRole.MARKET, "a"),)
        with mock.patch("quant_data.data_status.quiet_immutable_read_connection",
                        side_effect=AssertionError("unselected store was accessed")):
            result, selected = self._bounded_status_call(
                targets, stores=("news",), dataset_ids=("absent",), limit=1,
            )
        self.assertEqual(selected, ())
        self.assertEqual(result.records, ())
        self.assertEqual(result.truncation.total_known_count, 0)
        self.assertFalse(result.truncation.has_more)

    def test_public_status_filter_keeps_operation_budget_closed(self):
        from quant_data.errors import ResourceLimitError
        targets = tuple(self._target(StoreRole.MARKET, name) for name in ("a", "b"))
        with mock.patch("quant_data.data_status.quiet_immutable_read_connection",
                        side_effect=AssertionError("over-budget store was accessed")):
            with self.assertRaises(ResourceLimitError):
                self._bounded_status_call(targets, statuses=("current",),
                                          limit=1, max_operations=1)

    def test_public_selected_store_filter_still_preserves_status_counts(self):
        targets = (self._target(StoreRole.MACRO, "a"),
                   self._target(StoreRole.MARKET, "b"),
                   self._target(StoreRole.MARKET, "c"))
        for target in targets:
            self._seed_success(target.store, target.dataset_id, "2026-09-01T11:00:00Z")
        result, selected = self._bounded_status_call(
            targets, stores=("market",), statuses=("current",), limit=1,
        )
        self.assertEqual(selected, targets[1:])
        self.assertEqual(result.truncation.total_known_count, 2)
        self.assertEqual(len(result.records), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
