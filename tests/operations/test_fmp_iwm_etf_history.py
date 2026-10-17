from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from quant_data.errors import ConflictError, ResourceLimitError
from quant_data.operations import fmp_iwm_etf_history as operation


def _digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _receipt(*, outcome: str = "succeeded", written_count: int = 100) -> dict[str, object]:
    return {
        "outcome": outcome,
        "base_scope_manifest_sha256": _digest("base"),
        "scope_manifest_sha256": _digest("scope"),
        "successor_membership_sha256": _digest("membership"),
        "normalized_complete_history_sha256": _digest("history"),
        "response_sha256": _digest("response"),
        "price_row_count": 2,
        "successor_member_count": 96,
        "written_count": written_count,
    }


class FmpIwmEtfHistoryOperationTests(unittest.TestCase):
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

    def test_completion_receipt_requires_reservation_is_immutable_private_and_blocks_second_run(
        self,
    ) -> None:
        receipt = _receipt()
        with self._fixed_root():
            with self.assertRaises(ConflictError):
                operation._write_completion(receipt)
            operation._reserve_attempt()
            operation._write_completion(receipt)
            state = self.root / "data" / ".operations" / "fmp-iwm-etf-history-v1"
            completion = state / "completion.json"
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(completion.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(completion.read_text(encoding="utf-8")),
                {
                    "base_scope_manifest_sha256": receipt["base_scope_manifest_sha256"],
                    "contract": "quant_data.fmp_iwm_etf_history_completion",
                    "normalized_complete_history_sha256": receipt[
                        "normalized_complete_history_sha256"
                    ],
                    "outcome": "succeeded",
                    "price_row_count": 2,
                    "response_sha256": receipt["response_sha256"],
                    "scope_manifest_sha256": receipt["scope_manifest_sha256"],
                    "successor_member_count": 96,
                    "successor_membership_sha256": receipt["successor_membership_sha256"],
                    "version": "1.0.0",
                    "written_count": 100,
                },
            )
            with self.assertRaises(ConflictError):
                operation._completion_preflight()

    def test_existing_completion_rejects_before_dependencies_credential_or_network(self) -> None:
        with self._fixed_root():
            operation._reserve_attempt()
            operation._write_completion(_receipt())
            with (
                mock.patch.object(operation, "_canonical_dependencies") as dependencies,
                mock.patch.object(operation, "read_project_credential") as credential,
                mock.patch.object(operation, "run_iwm_etf_history") as runner,
                self.assertRaises(ConflictError),
            ):
                operation._run()
        dependencies.assert_not_called()
        credential.assert_not_called()
        runner.assert_not_called()

    def test_reserved_or_preexisting_empty_state_fails_closed_before_work(self) -> None:
        with self._fixed_root():
            operation._reserve_attempt()
            with self.assertRaises(ConflictError):
                operation._reserve_attempt()
            with (
                mock.patch.object(operation, "_canonical_dependencies") as dependencies,
                mock.patch.object(operation, "read_project_credential") as credential,
                mock.patch.object(operation, "run_iwm_etf_history") as runner,
                self.assertRaises(ConflictError),
            ):
                operation._run()
        dependencies.assert_not_called()
        credential.assert_not_called()
        runner.assert_not_called()


    def test_store_preflight_precedes_credential_reservation_and_fixed_runner(self) -> None:
        events: list[str] = []
        fake_registry = object()
        fake_stores = object()
        fake_scope = object()

        class _Publisher:
            def __init__(self, stores: object, registry: object, scope: object) -> None:
                self.assertIs(stores, fake_stores)
                self.assertIs(registry, fake_registry)
                self.assertIs(scope, fake_scope)
                events.append("publisher")

            def preflight_store(self) -> None:
                events.append("store_preflight")

            def assertIs(self, left: object, right: object) -> None:
                if left is not right:
                    raise AssertionError("operation supplied a wrong fixed dependency")

        def credential_reader(**kwargs: object) -> str:
            self.assertEqual(kwargs["name"], "FMP_API_KEY")
            events.append("credential")
            return "offline-fmp-key"

        def reserve() -> Path:
            events.append("reservation")
            return self.root / "reserved"

        def runner(**kwargs: object) -> dict[str, object]:
            self.assertIs(kwargs["store_map"], fake_stores)
            self.assertIs(kwargs["registry"], fake_registry)
            self.assertIs(kwargs["scope"], fake_scope)
            self.assertEqual(kwargs["api_key"], "offline-fmp-key")
            events.append("runner")
            return _receipt()

        with self._fixed_root(), mock.patch.object(
            operation, "_canonical_dependencies", return_value=(fake_registry, fake_stores)
        ), mock.patch.object(operation, "_scope", return_value=fake_scope), mock.patch.object(
            operation, "IwmEtfHistoryPublisher", _Publisher
        ), mock.patch.object(
            operation, "read_project_credential", side_effect=credential_reader
        ), mock.patch.object(
            operation, "_reserve_attempt", side_effect=reserve
        ), mock.patch.object(
            operation, "run_iwm_etf_history", side_effect=runner
        ), mock.patch.object(
            operation, "_write_completion", side_effect=lambda value: events.append("completion")
        ):
            result = operation._run()

        self.assertEqual(result, _receipt())
        self.assertEqual(
            events,
            [
                "publisher",
                "store_preflight",
                "credential",
                "reservation",
                "runner",
                "completion",
            ],
        )


    def test_completion_write_failure_after_runner_blocks_any_second_provider_attempt(self) -> None:
        fake_registry = object()
        fake_stores = object()
        fake_scope = object()

        class _Publisher:
            def __init__(self, stores: object, registry: object, scope: object) -> None:
                if stores is not fake_stores or registry is not fake_registry or scope is not fake_scope:
                    raise AssertionError("operation supplied a wrong fixed dependency")

            def preflight_store(self) -> None:
                return None

        with self._fixed_root(), mock.patch.object(
            operation, "_canonical_dependencies", return_value=(fake_registry, fake_stores)
        ), mock.patch.object(operation, "_scope", return_value=fake_scope), mock.patch.object(
            operation, "IwmEtfHistoryPublisher", _Publisher
        ), mock.patch.object(
            operation, "read_project_credential", return_value="offline-fmp-key"
        ), mock.patch.object(
            operation, "run_iwm_etf_history", return_value=_receipt()
        ) as runner, mock.patch.object(
            operation,
            "_write_completion",
            side_effect=ResourceLimitError("fixture completion failure"),
        ):
            with self.assertRaises(ResourceLimitError):
                operation._run()

        state = self.root / "data" / ".operations" / "fmp-iwm-etf-history-v1"
        self.assertTrue(state.is_dir())
        self.assertFalse((state / "completion.json").exists())
        runner.assert_called_once()

        with self._fixed_root(), mock.patch.object(
            operation, "_canonical_dependencies"
        ) as dependencies, mock.patch.object(
            operation, "read_project_credential"
        ) as credential, mock.patch.object(operation, "run_iwm_etf_history") as second_runner:
            with self.assertRaises(ConflictError):
                operation._run()
        dependencies.assert_not_called()
        credential.assert_not_called()
        second_runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
