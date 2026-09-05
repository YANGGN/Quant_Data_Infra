from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from quant_data.errors import RegistryError, ValidationError
from quant_data.operations import alpaca_spy_options_refresh as operation


class AlpacaSpyOptionsRefreshTests(unittest.TestCase):
    def test_registry_dependency_load_is_guarded_by_shared_bundle_lock(self) -> None:
        registry = object()
        stores = Mock()
        stores.path.return_value = operation.MARKET_STORE.resolve(strict=False)
        events: list[str] = []

        @contextmanager
        def guarded(root: Path, *, exclusive: bool, timeout_seconds: float):
            self.assertEqual(root, operation.PROJECT_ROOT)
            self.assertFalse(exclusive)
            self.assertEqual(
                timeout_seconds,
                operation.REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS,
            )
            events.append("lock_enter")
            try:
                yield
            finally:
                events.append("lock_exit")

        def load(*args: object, **kwargs: object) -> object:
            self.assertEqual(events, ["lock_enter"])
            events.append("load")
            return registry

        def resolve(*args: object, **kwargs: object) -> object:
            self.assertEqual(events, ["lock_enter", "load"])
            events.append("resolve")
            return stores

        with (
            patch.object(
                operation,
                "registry_bundle_lock",
                side_effect=guarded,
            ) as bundle_lock,
            patch.object(operation, "load_registry", side_effect=load) as loader,
            patch.object(operation, "resolve_store_map", side_effect=resolve) as resolver,
        ):
            self.assertEqual(operation._canonical_dependencies(), (registry, stores))

        bundle_lock.assert_called_once()
        loader.assert_called_once()
        resolver.assert_called_once()
        self.assertEqual(events, ["lock_enter", "load", "resolve", "lock_exit"])

    def test_registry_failure_is_validated_once_without_retry(self) -> None:
        with (
            patch.object(operation, "registry_bundle_lock") as bundle_lock,
            patch.object(
                operation,
                "load_registry",
                side_effect=RegistryError("persistent catalog drift"),
            ) as load,
            patch.object(operation, "resolve_store_map") as resolve,
            self.assertRaises(RegistryError),
        ):
            operation._canonical_dependencies()

        bundle_lock.assert_called_once_with(
            operation.PROJECT_ROOT,
            exclusive=False,
            timeout_seconds=operation.REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS,
        )
        load.assert_called_once()
        resolve.assert_not_called()

    def test_main_emits_compact_credential_free_success_receipt(self) -> None:
        report = {
            "contracts": 300,
            "outcome": "succeeded",
            "requests_issued": 4,
            "selected_expiration": "2026-09-25",
            "session_date": "2026-08-25",
            "surface_rows": 300,
            "written_count": 610,
        }
        stdout, stderr = StringIO(), StringIO()

        with (
            patch.object(operation, "_run", return_value=report),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {
                "contract": "quant_data.alpaca_spy_option_surface_receipt",
                "receipt": report,
                "version": "1.0.0",
            },
        )

    def test_main_replaces_exception_text_with_generic_error_receipt(self) -> None:
        secret = "fixture-secret-must-not-appear"
        stdout, stderr = StringIO(), StringIO()

        with (
            patch.object(
                operation,
                "_run",
                side_effect=ValidationError(f"provider rejected {secret}"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = operation.main()

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, 64)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(secret, output)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "contract": "quant_data.alpaca_spy_option_surface_error",
                "error": "invalid_request",
                "exit_code": 64,
                "version": "1.0.0",
            },
        )


if __name__ == "__main__":
    unittest.main()
