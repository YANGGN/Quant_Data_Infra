"""Offline coverage for explicit keyset pagination of company filings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from quant_data.boundary import ToolDispatcher
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.fingerprint import mutation_fingerprint
from quant_data.registry import load_registry
from quant_data.stage4 import run_clean_stage4_rebuild
from quant_data.stores import StoreMap


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
NORTHSTAR_CIK = "0001000001"
AURORA_CIK = "0001000002"


def _source_store_map(work_root: Path) -> StoreMap:
    source = work_root / "stage3" / "stage2" / "source"
    return StoreMap.four_explicit(
        market=source / "market.sqlite",
        macro=source / "macro.sqlite",
        company=source / "company.sqlite",
        news=source / "news.sqlite",
    )


def _fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        str(field["name"]): field["value"]
        for field in record["fields"]
    }


class CompanyFilingPaginationV2Tests(unittest.TestCase):
    """Public v2 pagination stays read-only over the Stage 4 fixture store."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(dir="/tmp")
        cls._root = Path(cls._temporary.name)
        cls._work_root = cls._root / "stage4"
        run_clean_stage4_rebuild(
            project_root=PROJECT_ROOT,
            work_root=cls._work_root,
        )
        cls.stores = _source_store_map(cls._work_root)
        cls.registry = load_registry(
            REGISTRY_PATH,
            project_root=PROJECT_ROOT,
            environment={},
        )
        cls.dispatcher = ToolDispatcher(cls.stores, cls.registry)
        cls._baseline = mutation_fingerprint(cls.stores)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def tearDown(self) -> None:
        self.assertEqual(
            self._baseline["sha256"],
            mutation_fingerprint(self.stores)["sha256"],
        )

    @staticmethod
    def _arguments(
        *,
        query: str = NORTHSTAR_CIK,
        as_of: str | None = None,
        cursor: str | None = None,
        limit: int = 2,
    ) -> dict[str, object]:
        return {
            "query": query,
            "as_of": as_of,
            "cursor": cursor,
            "limit": limit,
        }

    def _v2(self, **kwargs: object) -> dict[str, Any]:
        return self.dispatcher.call(
            "company.search_filings",
            self._arguments(**kwargs),
            tool_version="2.0.0",
        )

    def test_keyset_pages_are_ordered_disjoint_and_terminal(self) -> None:
        first = self._v2(limit=2)
        first_truncation = first["truncation"]
        self.assertEqual(first["tool"], "company.search_filings")
        self.assertTrue(first_truncation["applied"])
        self.assertTrue(first_truncation["has_more"])
        self.assertEqual(first_truncation["limit"], 2)
        self.assertEqual(first_truncation["returned_count"], 2)
        self.assertIsNone(first_truncation["total_known_count"])
        first_cursor = first_truncation["next_cursor"]
        self.assertIsInstance(first_cursor, str)

        second = self._v2(cursor=first_cursor, limit=1)
        second_truncation = second["truncation"]
        self.assertTrue(second_truncation["applied"])
        self.assertTrue(second_truncation["has_more"])
        self.assertEqual(second_truncation["limit"], 1)
        self.assertEqual(second_truncation["returned_count"], 1)
        second_cursor = second_truncation["next_cursor"]
        self.assertIsInstance(second_cursor, str)

        terminal = self._v2(cursor=second_cursor, limit=10)
        terminal_truncation = terminal["truncation"]
        self.assertFalse(terminal_truncation["applied"])
        self.assertFalse(terminal_truncation["has_more"])
        self.assertEqual(terminal_truncation["limit"], 10)
        self.assertIsNone(terminal_truncation["next_cursor"])

        records = [
            *first["records"],
            *second["records"],
            *terminal["records"],
        ]
        fields = [_fields(record) for record in records]
        keys = [
            (str(item["filing_date"]), str(item["accession_number"]))
            for item in fields
        ]
        accessions = [key[1] for key in keys]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(accessions), len(set(accessions)))
        self.assertGreater(len(accessions), 2)

    def test_cursor_is_bound_to_its_query_and_as_of(self) -> None:
        first = self._v2(limit=1)
        cursor = first["truncation"]["next_cursor"]
        self.assertIsInstance(cursor, str)

        with self.assertRaises(ValidationError):
            self._v2(cursor="*")
        with self.assertRaises(ValidationError):
            self._v2(query=AURORA_CIK, cursor=cursor)
        with self.assertRaises(ValidationError):
            self._v2(
                as_of="2026-12-31T00:00:00Z",
                cursor=cursor,
            )

    def test_v2_requires_one_exact_ten_digit_cik(self) -> None:
        for query in ("001000001", "00010000A1"):
            with self.subTest(query=query):
                with self.assertRaises(ValidationError):
                    self._v2(query=query)

    def test_frozen_v1_keeps_its_bounded_nonpaginated_behavior(self) -> None:
        legacy_arguments = {
            "query": NORTHSTAR_CIK,
            "as_of": None,
            "limit": 100,
        }
        legacy = self.dispatcher.call(
            "company.search_filings",
            legacy_arguments,
            tool_version="1.0.0",
        )
        self.assertFalse(legacy["truncation"]["applied"])
        self.assertFalse(legacy["truncation"]["has_more"])
        self.assertIsNone(legacy["truncation"]["next_cursor"])
        self.assertEqual(
            legacy["truncation"]["total_known_count"],
            len(legacy["records"]),
        )

        with self.assertRaises(ResourceLimitError):
            self.dispatcher.call(
                "company.search_filings",
                {
                    "query": NORTHSTAR_CIK,
                    "as_of": None,
                    "limit": 2,
                },
                tool_version="1.0.0",
            )


if __name__ == "__main__":
    unittest.main()
