from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES = tuple(
    PROJECT_ROOT / f"quant_data/migrations/macro/{name}"
    for name in (
        "0013_live_gdp_cpi_vintages.sql",
        "0014_live_employment_vintages.sql",
        "0015_live_macro_history_extension.sql",
    )
)


def _capture(connection: sqlite3.Connection, capture_id: str, provider: str) -> None:
    connection.execute(
        """
        INSERT INTO macro_live_vintage_captures (
            capture_id, provider, source_resource, media_type, response_sha256,
            response_bytes, semantic_identity, captured_at, captured_precision,
            source_published_at, source_published_precision, availability_basis,
            normalization_version, observation_count
        ) VALUES (?, ?, ?, 'application/json', ?, X'78', ?,
                  '2026-08-17T16:00:00.000000Z', 'datetime', NULL, NULL,
                  'local_capture', 'macro.live_vintage.v1', 1)
        """,
        (
            capture_id,
            provider,
            f"https://example.invalid/{capture_id}",
            (capture_id[0] * 64),
            (capture_id[-1] * 64),
        ),
    )


class LiveMacroHistoryExtensionMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_series_and_adds_narrow_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(Path(directory) / "macro.sqlite")
            try:
                connection.executescript(RESOURCES[0].read_text(encoding="utf-8"))
                connection.executescript(RESOURCES[1].read_text(encoding="utf-8"))
                _capture(connection, "a1", "bls")
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_series VALUES (
                        'macro.bls.cpi_u_all_items_sa', 'bls', 'CUSR0000SA0',
                        'Consumer Price Index for All Urban Consumers: All Items, seasonally adjusted',
                        'monthly', 'index', 'level', 'mixed', 'a1'
                    )
                    """
                )
                connection.commit()

                connection.executescript(RESOURCES[2].read_text(encoding="utf-8"))
                self.assertEqual(
                    connection.execute(
                        "SELECT provider_series_code FROM macro_live_vintage_series WHERE series_id=?",
                        ("macro.bls.cpi_u_all_items_sa",),
                    ).fetchone()[0],
                    "CUSR0000SA0",
                )
                _capture(connection, "b2", "philadelphia_fed")
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_series VALUES (
                        'macro.philadelphia_fed.nominal_output', 'philadelphia_fed',
                        'NOUTPUT', 'RTDSM Nominal GNP/GDP, seasonally adjusted annual rate',
                        'quarterly', 'billions_usd', 'level', 'local_capture', 'b2'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO macro_live_vintage_releases (
                        release_id, series_id, source_vintage_identity, vintage_at,
                        vintage_precision, source_release_order, available_at,
                        available_precision, availability_basis, is_first_release,
                        first_release_evidence, release_stage, source_published_at,
                        source_published_precision, capture_id
                    ) VALUES (
                        'r1', 'macro.bls.cpi_u_all_items_sa', 'PCPI98M11', NULL,
                        NULL, '000001:PCPI98M11', '2026-08-17T16:00:00.000000Z',
                        'datetime', 'local_capture', NULL, NULL, NULL, NULL, NULL, 'b2'
                    )
                    """
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO macro_live_vintage_releases (
                            release_id, series_id, source_vintage_identity, vintage_at,
                            vintage_precision, source_release_order, available_at,
                            available_precision, availability_basis, is_first_release,
                            first_release_evidence, release_stage, source_published_at,
                            source_published_precision, capture_id
                        ) VALUES (
                            'r2', 'macro.gdp.nominal_billions', 'bad', NULL, NULL,
                            'bad', '2026-08-17T16:00:00.000000Z', 'datetime',
                            'local_capture', NULL, NULL, NULL, NULL, NULL, 'b2'
                        )
                        """
                    )
                self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
