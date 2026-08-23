from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from quant_data.macro.stage11_eia import CapturedEiaResponse
from quant_data.macro.stage11_publication import Stage11MacroImporter
from quant_data.migrations import initialize_all
from quant_data.operations import eia_electricity_retail_history as operation
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
                "dateFormat": "YYYY-MM",
                "frequency": "monthly",
                "data": [
                    {
                        "period": "2026-06",
                        "stateid": "US",
                        "sectorid": "ALL",
                        "sales": "350.1",
                        "revenue": "48000.2",
                        "price": "13.71",
                        "customers": "162000",
                        "sales-units": "million kilowatthours",
                        "revenue-units": "million dollars",
                        "price-units": "cents per kilowatthour",
                        "customers-units": "thousand customers",
                        "stateDescription": "U.S.",
                        "sectorName": "all sectors",
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


class EiaElectricityRetailHistoryTests(unittest.TestCase):
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

    def _stores(self, name: str):
        store_map = explicit_store_map(self.root / name)
        initialize_all(
            store_map,
            self.registry,
            applied_at="2026-08-23T12:00:00Z",
        )
        return store_map

    def test_live_runner_fetches_one_complete_page_and_replays_unchanged(self) -> None:
        store_map = self._stores("target")
        importer = Stage11MacroImporter(
            store_map,
            self.stage11_registry,
            clock=lambda: NOW,
        )
        transport = _Transport()

        first = operation.run_eia_electricity_retail_history(
            importer=importer,
            api_key=KEY,
            transport=transport,
        )
        second = operation.run_eia_electricity_retail_history(
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
