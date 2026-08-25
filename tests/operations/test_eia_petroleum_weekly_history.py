from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.macro.stage11_eia import CapturedEiaResponse
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.migrations import initialize_all
from quant_data.operations import eia_petroleum_weekly_history as operation
from quant_data.registry import (
    CANONICAL_REGISTRY_PATH,
    load_registry,
    stage11_registry_profile,
)
from quant_data.stage1 import explicit_store_map


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
KEY = "fixture-eia-key"


def _body() -> bytes:
    return json.dumps(
        {
            "response": {
                "total": 1,
                "dateFormat": "YYYY-MM-DD",
                "frequency": "weekly",
                "data": [
                    {
                        "area-name": "United States",
                        "duoarea": "NUS",
                        "period": "2026-08-14",
                        "process": "SAE",
                        "process-name": "Stocks",
                        "product": "EPC0",
                        "product-name": "Crude Oil",
                        "series": "WCESTUS1",
                        "series-description": "Weekly ending stocks",
                        "units": "Thousand Barrels",
                        "value": 425000,
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")


class _Transport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, **kwargs: object) -> CapturedEiaResponse:
        self.calls += 1
        return CapturedEiaResponse(200, "application/json", _body())


class EiaPetroleumWeeklyHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.temporary.name)
        self.registry = load_registry(
            PROJECT_ROOT / CANONICAL_REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        self.stage11_registry = stage11_registry_profile(self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_live_runner_fetches_once_and_replays_unchanged(self) -> None:
        stores = explicit_store_map(self.root / "stores")
        initialize_all(
            stores,
            self.registry,
            applied_at="2026-08-23T12:00:00Z",
        )
        importer = Stage11MacroImporter(
            stores,
            self.stage11_registry,
            clock=lambda: NOW,
        )
        transport = _Transport()

        first = operation.run_eia_petroleum_weekly_stock_history(
            importer=importer,
            api_key=KEY,
            transport=transport,
        )
        second = operation.run_eia_petroleum_weekly_stock_history(
            importer=importer,
            api_key=KEY,
            transport=transport,
        )

        self.assertEqual(
            (first.outcome, second.outcome),
            ("succeeded", "unchanged"),
        )
        self.assertEqual(transport.calls, 2)


if __name__ == "__main__":
    unittest.main()
