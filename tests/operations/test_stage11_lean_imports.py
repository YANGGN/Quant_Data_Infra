"""Import-boundary regressions for the lean Stage 11 runner."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Stage11LeanImportTests(unittest.TestCase):
    def test_runner_does_not_activate_retired_heavy_stack(self) -> None:
        environment = dict(os.environ)
        for name in ("BEA_API_KEY", "EIA_API_KEY", "FMP_API_KEY"):
            environment.pop(name, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.run(
            (
                sys.executable,
                "-B",
                "-c",
                "import sys; "
                "import quant_data.operations.stage11_backfill; "
                "names=('quant_data.operations.stage11_repopulation',"
                "'quant_data.operations.stage10_repopulation',"
                "'quant_data.fingerprint','quant_data.operations.health',"
                "'quant_data.operations.backup'); "
                "print(' '.join(str(name in sys.modules) for name in names))",
            ),
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(
            process.stdout.strip(),
            "False False False False False",
        )


if __name__ == "__main__":
    unittest.main()
