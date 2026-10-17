"""Offline key-selection and heap bounds over explicit temporary SQLite data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import tracemalloc
import unittest

from quant_data.operations.forward_pe import _sharadar_observation_ids


class ForwardPeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.db = sqlite3.connect(Path(self.root.name) / "keys.sqlite")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE company_sharadar_sf1_observations "
                        "(observation_id TEXT, source_ticker TEXT, dimension TEXT)")

    def insert(self, rows):
        self.db.executemany("INSERT INTO company_sharadar_sf1_observations VALUES (?,?,?)", rows)
        self.db.commit()

    def test_selected_keys_equal_prior_lookup_including_order_and_duplicates(self):
        self.insert([("a1", "A", "ARQ"), ("b1", "B", "ARQ"),
                     ("a2", "A", "ARQ"), ("annual", "A", "ARY"),
                     ("a1", "A", "ARQ"), ("c1", "C", "ARQ")])
        self.assertEqual(_sharadar_observation_ids(self.db, ["A", "C", "MISSING"]),
                         {"A": ["a1", "a2", "a1"], "C": ["c1"]})

    def test_exact_scan_bound_is_accepted(self):
        self.insert([("a1", "A", "ARQ"), ("b1", "B", "ARQ")])
        self.assertEqual(_sharadar_observation_ids(self.db, ["A"], limit=2),
                         {"A": ["a1"]})

    def test_unselected_rows_still_count_toward_original_scan_bound(self):
        self.insert([("a1", "A", "ARQ"), ("b1", "B", "ARQ"), ("b2", "B", "ARQ")])
        for wanted in (["A"], []):
            with self.subTest(wanted=wanted), self.assertRaisesRegex(ValueError, "Source row limit exceeded"):
                _sharadar_observation_ids(self.db, wanted, limit=2)

    def test_unselected_keys_do_not_accumulate_in_python_heap(self):
        self.insert((f"observation-{i:08d}", "A" if i == 0 else "OTHER", "ARQ")
                    for i in range(40000))
        tracemalloc.start()
        try:
            result = _sharadar_observation_ids(self.db, ["A"])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result, {"A": ["observation-00000000"]})
        self.assertLess(peak, 1024 * 1024, "Unselected source keys must be streamed, not materialized")


if __name__ == "__main__":
    unittest.main()
