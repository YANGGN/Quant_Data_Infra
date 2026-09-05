from __future__ import annotations

from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quant_data.registry_bundle_lock import REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS
from quant_data.tool_platform.generate import (
    CATALOG_RESOURCE,
    REGISTRY_RESOURCE,
    VERSIONED_CATALOG_RESOURCE,
    generate,
)


class RegistryBundlePublicationTests(unittest.TestCase):
    def test_generator_holds_exclusive_lock_through_all_replacements(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            payloads = (b"registry\n", b"catalog-v1\n", b"catalog-v2\n")
            expected = dict(
                zip(
                    (REGISTRY_RESOURCE, CATALOG_RESOURCE, VERSIONED_CATALOG_RESOURCE),
                    payloads,
                    strict=True,
                )
            )
            state = {"locked": False}

            @contextmanager
            def guarded(
                project_root: Path,
                *,
                exclusive: bool,
                timeout_seconds: float,
            ):
                self.assertEqual(project_root, root.resolve(strict=True))
                self.assertTrue(exclusive)
                self.assertEqual(
                    timeout_seconds,
                    REGISTRY_BUNDLE_LOCK_TIMEOUT_SECONDS,
                )
                state["locked"] = True
                try:
                    yield
                finally:
                    for resource, payload in expected.items():
                        self.assertEqual((root / resource).read_bytes(), payload)
                    state["locked"] = False

            def generated(project_root: Path) -> tuple[bytes, bytes, bytes]:
                self.assertEqual(project_root, root.resolve(strict=True))
                self.assertTrue(state["locked"])
                return payloads

            with (
                patch(
                    "quant_data.tool_platform.generate.registry_bundle_lock",
                    side_effect=guarded,
                ) as bundle_lock,
                patch(
                    "quant_data.tool_platform.generate.generated_bytes",
                    side_effect=generated,
                ) as generated_bytes,
            ):
                generate(root)

            bundle_lock.assert_called_once()
            generated_bytes.assert_called_once()
            self.assertFalse(state["locked"])


if __name__ == "__main__":
    unittest.main()
