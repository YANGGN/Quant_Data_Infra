from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from quant_data.errors import RegistryError
from quant_data.registry_bundle_lock import registry_bundle_lock


class RegistryBundleLockTests(unittest.TestCase):
    def test_exclusive_publisher_blocks_shared_reader(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            publisher_ready = threading.Event()
            release_publisher = threading.Event()
            errors: list[BaseException] = []

            def publish() -> None:
                try:
                    with registry_bundle_lock(
                        root,
                        exclusive=True,
                        timeout_seconds=1.0,
                    ):
                        publisher_ready.set()
                        release_publisher.wait(1.0)
                except BaseException as exc:  # pragma: no cover - test handoff
                    errors.append(exc)

            publisher = threading.Thread(target=publish)
            publisher.start()
            self.assertTrue(publisher_ready.wait(1.0))
            try:
                with self.assertRaises(RegistryError):
                    with registry_bundle_lock(
                        root,
                        exclusive=False,
                        timeout_seconds=0.02,
                    ):
                        self.fail("shared reader entered an active publication")
            finally:
                release_publisher.set()
                publisher.join(1.0)

            self.assertFalse(publisher.is_alive())
            self.assertEqual(errors, [])
            with registry_bundle_lock(
                root,
                exclusive=False,
                timeout_seconds=0.1,
            ):
                pass


if __name__ == "__main__":
    unittest.main()
