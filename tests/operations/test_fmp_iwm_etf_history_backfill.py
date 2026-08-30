from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from quant_data.errors import ConflictError, ResourceLimitError
from quant_data.operations import fmp_iwm_etf_history_backfill as operation


def _receipt(*, outcome: str = "succeeded", written_count: int = 104) -> dict[str, object]:
    return {
        "outcome": outcome,
        "backfill_scope_manifest_sha256": "a" * 64,
        "iwm_scope_manifest_sha256": operation.IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256,
        "normalized_complete_backfill_sha256": "b" * 64,
        "response_sha256": "c" * 64,
        "earliest_trade_date": "2000-05-26",
        "latest_trade_date": "2021-08-27",
        "price_row_count": 3,
        "written_count": written_count,
    }


def _prior_completion() -> dict[str, object]:
    pinned = operation.IWM_ETF_HISTORY_PRIOR_COMPLETION
    return {
        "base_scope_manifest_sha256": pinned["base_scope_manifest_sha256"],
        "contract": "quant_data.fmp_iwm_etf_history_completion",
        "normalized_complete_history_sha256": pinned[
            "normalized_complete_history_sha256"
        ],
        "outcome": "succeeded",
        "price_row_count": operation.IWM_ETF_HISTORY_PRIOR_PRICE_ROW_COUNT,
        "response_sha256": pinned["response_sha256"],
        "scope_manifest_sha256": operation.IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256,
        "successor_member_count": 96,
        "successor_membership_sha256": pinned["successor_membership_sha256"],
        "version": "1.0.0",
        "written_count": 1_354,
    }


class FmpIwmEtfHistoryBackfillOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name) / "project"
        (self.root / "data").mkdir(parents=True)
        self.market_store = self.root / "data" / "market.sqlite"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _fixed_root(self) -> object:
        return mock.patch.multiple(
            operation,
            PROJECT_ROOT=self.root,
            MARKET_STORE=self.market_store,
        )

    def _write_prior_completion(self, payload: dict[str, object] | None = None) -> Path:
        operations = self.root / "data" / ".operations"
        operations.mkdir(mode=0o700, exist_ok=True)
        state = operations / "fmp-iwm-etf-history-v1"
        state.mkdir(mode=0o700, exist_ok=True)
        state.chmod(0o700)
        completion = state / "completion.json"
        completion.write_text(
            json.dumps(payload or _prior_completion(), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        completion.chmod(0o600)
        return completion

    def test_prior_completion_is_exact_private_and_pinned(self) -> None:
        with self._fixed_root():
            completion = self._write_prior_completion()
            operation._prior_completion_preflight()

            completion.chmod(0o644)
            with self.assertRaises(ConflictError):
                operation._prior_completion_preflight()

            completion.chmod(0o600)
            drifted = _prior_completion()
            drifted["price_row_count"] = 1_253
            completion.write_text(
                json.dumps(drifted, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            completion.chmod(0o600)
            with self.assertRaises(ConflictError):
                operation._prior_completion_preflight()

    def test_completion_receipt_requires_reservation_and_blocks_second_run(self) -> None:
        receipt = _receipt()
        with self._fixed_root():
            with self.assertRaises(ConflictError):
                operation._write_completion(receipt)
            operation._reserve_attempt()
            operation._write_completion(receipt)
            state = (
                self.root
                / "data"
                / ".operations"
                / "fmp-iwm-etf-history-backfill-v1"
            )
            completion = state / "completion.json"
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(completion.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(completion.read_text(encoding="utf-8")),
                {
                    "backfill_scope_manifest_sha256": "a" * 64,
                    "contract": "quant_data.fmp_iwm_etf_history_backfill_completion",
                    "earliest_trade_date": "2000-05-26",
                    "iwm_scope_manifest_sha256": (
                        operation.IWM_ETF_HISTORY_SCOPE_MANIFEST_SHA256
                    ),
                    "latest_trade_date": "2021-08-27",
                    "normalized_complete_backfill_sha256": "b" * 64,
                    "outcome": "succeeded",
                    "price_row_count": 3,
                    "response_sha256": "c" * 64,
                    "version": "1.0.0",
                    "written_count": 104,
                },
            )
            with self.assertRaises(ConflictError):
                operation._completion_preflight()

    def test_existing_completion_rejects_before_prior_or_provider_work(self) -> None:
        with self._fixed_root():
            operation._reserve_attempt()
            operation._write_completion(_receipt())
            with (
                mock.patch.object(operation, "_prior_completion_preflight") as prior,
                mock.patch.object(operation, "_canonical_dependencies") as dependencies,
                mock.patch.object(operation, "read_project_credential") as credential,
                mock.patch.object(operation, "run_iwm_etf_history_backfill") as runner,
                self.assertRaises(ConflictError),
            ):
                operation._run()
        prior.assert_not_called()
        dependencies.assert_not_called()
        credential.assert_not_called()
        runner.assert_not_called()

    def test_order_is_prior_store_credential_reservation_runner_completion(self) -> None:
        events: list[str] = []
        fake_registry = object()
        fake_stores = object()
        fake_scope = object()

        class _Publisher:
            def __init__(self, stores: object, registry: object, scope: object) -> None:
                if (
                    stores is not fake_stores
                    or registry is not fake_registry
                    or scope is not fake_scope
                ):
                    raise AssertionError("operation supplied a wrong fixed dependency")
                events.append("publisher")

            def preflight_store(self) -> None:
                events.append("store_preflight")

        def dependencies() -> tuple[object, object]:
            events.append("dependencies")
            return fake_registry, fake_stores

        def credential_reader(**kwargs: object) -> str:
            self.assertEqual(kwargs["name"], "FMP_API_KEY")
            events.append("credential")
            return "offline-fmp-key"

        def runner(**kwargs: object) -> dict[str, object]:
            self.assertIs(kwargs["store_map"], fake_stores)
            self.assertIs(kwargs["registry"], fake_registry)
            self.assertIs(kwargs["scope"], fake_scope)
            self.assertEqual(kwargs["api_key"], "offline-fmp-key")
            events.append("runner")
            return _receipt()

        with self._fixed_root(), mock.patch.object(
            operation, "_completion_preflight", side_effect=lambda: events.append("completion_preflight")
        ), mock.patch.object(
            operation, "_prior_completion_preflight", side_effect=lambda: events.append("prior_preflight")
        ), mock.patch.object(
            operation, "_canonical_dependencies", side_effect=dependencies
        ), mock.patch.object(
            operation, "_scope", side_effect=lambda: events.append("scope") or fake_scope
        ), mock.patch.object(
            operation, "IwmEtfHistoryBackfillPublisher", _Publisher
        ), mock.patch.object(
            operation, "read_project_credential", side_effect=credential_reader
        ), mock.patch.object(
            operation, "_reserve_attempt", side_effect=lambda: events.append("reservation")
        ), mock.patch.object(
            operation, "run_iwm_etf_history_backfill", side_effect=runner
        ), mock.patch.object(
            operation, "_write_completion", side_effect=lambda value: events.append("completion")
        ):
            result = operation._run()

        self.assertEqual(result, _receipt())
        self.assertEqual(
            events,
            [
                "completion_preflight",
                "prior_preflight",
                "dependencies",
                "scope",
                "publisher",
                "store_preflight",
                "credential",
                "reservation",
                "runner",
                "completion",
            ],
        )

    def test_prior_conflict_rejects_before_store_credential_or_network(self) -> None:
        with self._fixed_root(), mock.patch.object(
            operation,
            "_prior_completion_preflight",
            side_effect=ConflictError("fixture prior conflict"),
        ), mock.patch.object(
            operation, "_canonical_dependencies"
        ) as dependencies, mock.patch.object(
            operation, "read_project_credential"
        ) as credential, mock.patch.object(
            operation, "run_iwm_etf_history_backfill"
        ) as runner:
            with self.assertRaises(ConflictError):
                operation._run()
        dependencies.assert_not_called()
        credential.assert_not_called()
        runner.assert_not_called()

    def test_completion_failure_leaves_reservation_and_blocks_retry(self) -> None:
        fake_registry = object()
        fake_stores = object()
        fake_scope = object()

        class _Publisher:
            def __init__(self, stores: object, registry: object, scope: object) -> None:
                if (
                    stores is not fake_stores
                    or registry is not fake_registry
                    or scope is not fake_scope
                ):
                    raise AssertionError("operation supplied a wrong fixed dependency")

            def preflight_store(self) -> None:
                return None

        with self._fixed_root(), mock.patch.object(
            operation, "_prior_completion_preflight"
        ), mock.patch.object(
            operation, "_canonical_dependencies", return_value=(fake_registry, fake_stores)
        ), mock.patch.object(
            operation, "_scope", return_value=fake_scope
        ), mock.patch.object(
            operation, "IwmEtfHistoryBackfillPublisher", _Publisher
        ), mock.patch.object(
            operation, "read_project_credential", return_value="offline-fmp-key"
        ), mock.patch.object(
            operation, "run_iwm_etf_history_backfill", return_value=_receipt()
        ) as runner, mock.patch.object(
            operation,
            "_write_completion",
            side_effect=ResourceLimitError("fixture completion failure"),
        ):
            with self.assertRaises(ResourceLimitError):
                operation._run()

        state = (
            self.root
            / "data"
            / ".operations"
            / "fmp-iwm-etf-history-backfill-v1"
        )
        self.assertTrue(state.is_dir())
        self.assertFalse((state / "completion.json").exists())
        runner.assert_called_once()

        with self._fixed_root(), mock.patch.object(
            operation, "_prior_completion_preflight"
        ) as prior, mock.patch.object(
            operation, "_canonical_dependencies"
        ) as dependencies, mock.patch.object(
            operation, "read_project_credential"
        ) as credential, mock.patch.object(
            operation, "run_iwm_etf_history_backfill"
        ) as second_runner:
            with self.assertRaises(ConflictError):
                operation._run()
        prior.assert_not_called()
        dependencies.assert_not_called()
        credential.assert_not_called()
        second_runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
