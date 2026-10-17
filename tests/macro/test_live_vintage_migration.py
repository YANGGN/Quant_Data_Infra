from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0013_live_gdp_cpi_vintages.sql"


class LiveVintageMigrationTests(unittest.TestCase):
    def test_isolated_relations_are_strict_and_integrity_clean(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA recursive_triggers=ON")
            connection.executescript(RESOURCE.read_text(encoding="utf-8"))
            expected = {
                "macro_live_vintage_captures",
                "macro_live_vintage_series",
                "macro_live_vintage_releases",
                "macro_live_vintage_observation_versions",
                "macro_live_vintage_observations",
                "macro_live_vintage_capture_membership",
                "macro_live_vintage_gdp_provenance",
            }
            found = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'macro_live_vintage_%'"
                )
            }
            self.assertEqual(found, expected)
            table_rows = {
                str(row[1]): int(row[5])
                for row in connection.execute("PRAGMA table_list")
                if str(row[1]) in expected
            }
            self.assertEqual(table_rows, {name: 1 for name in expected})
            self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
        finally:
            connection.close()

    def test_missing_capture_foreign_key_is_rejected(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(RESOURCE.read_text(encoding="utf-8"))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_series (
                        series_id, provider, provider_series_code, title, frequency,
                        unit, value_representation, availability_basis, created_capture_id
                    ) VALUES (
                        'macro.gdp.real_qoq_saar_pct', 'bea', 'A191RL', 'x',
                        'quarterly', 'percent', 'rate', 'source_release', 'missing'
                    )
                    """
                )
        finally:
            connection.close()
