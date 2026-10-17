from __future__ import annotations

import hashlib
import sqlite3
import unittest
from pathlib import Path

from quant_data.migrations import _sql_statements


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0013_live_gdp_cpi_vintages.sql"
RESOURCE = PROJECT_ROOT / "quant_data/migrations/macro/0014_live_employment_vintages.sql"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_SERIES = {
    "macro.gdp.real_qoq_saar_pct": (
        "bea",
        "A191RL",
        "Real GDP growth",
        "quarterly",
        "percent",
        "rate",
        "source_release",
    ),
    "macro.bls.cpi_u_all_items_sa": (
        "bls",
        "CUSR0000SA0",
        "CPI-U all items, seasonally adjusted",
        "monthly",
        "index",
        "level",
        "mixed",
    ),
    "macro.bls.total_nonfarm_payrolls_sa": (
        "bls",
        "CES0000000001",
        "Total nonfarm payrolls, seasonally adjusted",
        "monthly",
        "thousands_persons",
        "level",
        "mixed",
    ),
    "macro.bls.unemployment_rate_sa": (
        "bls",
        "LNS14000000",
        "Unemployment rate, seasonally adjusted",
        "monthly",
        "percent",
        "rate",
        "mixed",
    ),
}


class LiveEmploymentVintageMigrationTests(unittest.TestCase):
    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(BASE_RESOURCE.read_text(encoding="utf-8"))
        return connection

    @staticmethod
    def _upgrade(connection: sqlite3.Connection) -> None:
        connection.commit()
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _sql_statements(RESOURCE.read_text(encoding="utf-8")):
                connection.execute(statement)
            if tuple(connection.execute("PRAGMA foreign_key_check")):
                raise AssertionError("0014 leaves foreign-key violations")
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _capture(
        connection: sqlite3.Connection,
        *,
        suffix: str,
        provider: str,
        captured_at: str = "2026-08-17T12:00:00Z",
    ) -> str:
        capture_id = f"capture-{suffix}"
        connection.execute(
            """
            INSERT INTO macro_live_vintage_captures (
                capture_id, provider, source_resource, media_type,
                response_sha256, response_bytes, semantic_identity, captured_at,
                captured_precision, source_published_at, source_published_precision,
                availability_basis, normalization_version, observation_count
            ) VALUES (?, ?, ?, 'application/json', ?, ?, ?, ?, 'datetime',
                      '2026-08-15', 'date', 'source_release',
                      'macro.live_vintage.v1', 1)
            """,
            (
                capture_id,
                provider,
                f"{provider}/historical/{suffix}",
                _digest(f"response:{suffix}"),
                b"{}",
                _digest(f"semantic:{suffix}"),
                captured_at,
            ),
        )
        return capture_id

    @staticmethod
    def _series(
        connection: sqlite3.Connection,
        *,
        series_id: str,
        capture_id: str,
    ) -> None:
        (
            provider,
            provider_series_code,
            title,
            frequency,
            unit,
            value_representation,
            availability_basis,
        ) = _SERIES[series_id]
        connection.execute(
            """
            INSERT INTO macro_live_vintage_series (
                series_id, provider, provider_series_code, title, frequency, unit,
                value_representation, availability_basis, created_capture_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series_id,
                provider,
                provider_series_code,
                title,
                frequency,
                unit,
                value_representation,
                availability_basis,
                capture_id,
            ),
        )

    @staticmethod
    def _release(
        connection: sqlite3.Connection,
        *,
        suffix: str,
        series_id: str,
        capture_id: str,
        order: str,
    ) -> str:
        release_id = f"release-{suffix}"
        source_vintage_identity = f"vintage-{suffix}"
        connection.execute(
            """
            INSERT INTO macro_live_vintage_releases (
                release_id, series_id, source_vintage_identity, vintage_at,
                vintage_precision, source_release_order, available_at,
                available_precision, availability_basis, is_first_release,
                first_release_evidence, release_stage, source_published_at,
                source_published_precision, capture_id
            ) VALUES (?, ?, ?, '2026-08-15', 'date', ?, '2026-08-15', 'date',
                      'source_release', 1, ?, 'historical', '2026-08-15',
                      'date', ?)
            """,
            (
                release_id,
                series_id,
                source_vintage_identity,
                order,
                f"evidence:{suffix}",
                capture_id,
            ),
        )
        return release_id

    @staticmethod
    def _version(
        connection: sqlite3.Connection,
        *,
        suffix: str,
        series_id: str,
        release_id: str,
        capture_id: str,
        period: str,
        captured_at: str = "2026-08-17T12:00:00Z",
    ) -> str:
        version_id = f"version-{suffix}"
        connection.execute(
            """
            INSERT INTO macro_live_vintage_observation_versions (
                version_id, series_id, period, release_id, source_vintage_identity,
                correction_sequence, value_text, value_sha256, unit, available_at,
                available_precision, captured_at, captured_precision,
                supersedes_version_id, capture_id, source_row
            ) VALUES (?, ?, ?, ?, ?, 1, '1', ?, ?, '2026-08-15', 'date', ?,
                      'datetime', NULL, ?, 1)
            """,
            (
                version_id,
                series_id,
                period,
                release_id,
                f"vintage-{suffix}",
                _digest(f"value:{suffix}"),
                _SERIES[series_id][4],
                captured_at,
                capture_id,
            ),
        )
        return version_id

    def _assert_clean(self, connection: sqlite3.Connection) -> None:
        self.assertEqual(list(connection.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_upgrade_preserves_stage13_gdp_and_cpi_rows_and_triggers(self) -> None:
        connection = self._connection()
        try:
            bea_capture = self._capture(connection, suffix="base-bea", provider="bea")
            self._series(
                connection,
                series_id="macro.gdp.real_qoq_saar_pct",
                capture_id=bea_capture,
            )
            gdp_release = self._release(
                connection,
                suffix="base-gdp",
                series_id="macro.gdp.real_qoq_saar_pct",
                capture_id=bea_capture,
                order="00000000000000000001",
            )
            gdp_version = self._version(
                connection,
                suffix="base-gdp",
                series_id="macro.gdp.real_qoq_saar_pct",
                release_id=gdp_release,
                capture_id=bea_capture,
                period="2026Q2",
            )
            connection.execute(
                "INSERT INTO macro_live_vintage_observations VALUES (?, ?, ?)",
                ("macro.gdp.real_qoq_saar_pct", "2026Q2", gdp_version),
            )
            connection.execute(
                "INSERT INTO macro_live_vintage_capture_membership VALUES (?, ?, ?, 1)",
                (bea_capture, gdp_version, "macro.gdp.real_qoq_saar_pct"),
            )
            connection.execute(
                """
                INSERT INTO macro_live_vintage_gdp_provenance (
                    provenance_id, release_id, capture_id, reference_period,
                    release_stage, source_vintage_identity, source_row
                ) VALUES ('gdp-provenance-base', ?, ?, '2026Q2', 'historical',
                          'vintage-base-gdp', 1)
                """,
                (gdp_release, bea_capture),
            )

            bls_capture = self._capture(connection, suffix="base-bls", provider="bls")
            self._series(
                connection,
                series_id="macro.bls.cpi_u_all_items_sa",
                capture_id=bls_capture,
            )
            cpi_release = self._release(
                connection,
                suffix="base-cpi",
                series_id="macro.bls.cpi_u_all_items_sa",
                capture_id=bls_capture,
                order="00000000000000000002",
            )
            cpi_version = self._version(
                connection,
                suffix="base-cpi",
                series_id="macro.bls.cpi_u_all_items_sa",
                release_id=cpi_release,
                capture_id=bls_capture,
                period="2026-07",
            )
            connection.execute(
                "INSERT INTO macro_live_vintage_observations VALUES (?, ?, ?)",
                ("macro.bls.cpi_u_all_items_sa", "2026-07", cpi_version),
            )
            connection.execute(
                "INSERT INTO macro_live_vintage_capture_membership VALUES (?, ?, ?, 1)",
                (bls_capture, cpi_version, "macro.bls.cpi_u_all_items_sa"),
            )
            before = list(
                connection.execute(
                    """
                    SELECT series_id, provider, provider_series_code, created_capture_id
                    FROM macro_live_vintage_series ORDER BY series_id
                    """
                )
            )

            self._upgrade(connection)

            after = list(
                connection.execute(
                    """
                    SELECT series_id, provider, provider_series_code, created_capture_id
                    FROM macro_live_vintage_series
                    WHERE series_id IN ('macro.gdp.real_qoq_saar_pct',
                                        'macro.bls.cpi_u_all_items_sa')
                    ORDER BY series_id
                    """
                )
            )
            self.assertEqual(after, before)
            self.assertEqual(
                connection.execute(
                    "SELECT response_bytes FROM macro_live_vintage_captures WHERE capture_id=?",
                    (bea_capture,),
                ).fetchone()[0],
                b"{}",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT current_version_id FROM macro_live_vintage_observations WHERE series_id=?",
                    ("macro.gdp.real_qoq_saar_pct",),
                ).fetchone()[0],
                gdp_version,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT provenance_id FROM macro_live_vintage_gdp_provenance WHERE release_id=?",
                    (gdp_release,),
                ).fetchone()[0],
                "gdp-provenance-base",
            )
            self.assertEqual(
                {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='trigger' AND name LIKE 'macro_live_vintage_%'
                        """
                    )
                },
                {
                    "macro_live_vintage_capture_immutable_update",
                    "macro_live_vintage_capture_immutable_delete",
                    "macro_live_vintage_series_immutable_update",
                    "macro_live_vintage_series_immutable_delete",
                    "macro_live_vintage_release_capture_lineage",
                    "macro_live_vintage_release_immutable_update",
                    "macro_live_vintage_release_immutable_delete",
                    "macro_live_vintage_version_lineage",
                    "macro_live_vintage_version_immutable_update",
                    "macro_live_vintage_version_immutable_delete",
                    "macro_live_vintage_membership_lineage",
                    "macro_live_vintage_membership_immutable_update",
                    "macro_live_vintage_membership_immutable_delete",
                    "macro_live_vintage_gdp_provenance_lineage",
                    "macro_live_vintage_gdp_provenance_immutable_update",
                    "macro_live_vintage_gdp_provenance_immutable_delete",
                    "macro_live_vintage_current_insert",
                    "macro_live_vintage_current_update",
                    "macro_live_vintage_current_key_immutable",
                },
            )
            for statement, parameters in (
                (
                    "UPDATE macro_live_vintage_captures SET source_resource='changed' WHERE capture_id=?",
                    (bea_capture,),
                ),
                (
                    "UPDATE macro_live_vintage_series SET title='changed' WHERE series_id=?",
                    ("macro.gdp.real_qoq_saar_pct",),
                ),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement, parameters)
            self._assert_clean(connection)
        finally:
            connection.close()

    def test_exact_philadelphia_fed_employment_alias_is_allowed_and_others_fail(self) -> None:
        connection = self._connection()
        try:
            self._upgrade(connection)
            bls_capture = self._capture(connection, suffix="bls", provider="bls")
            bls_replay_capture = self._capture(
                connection,
                suffix="bls-replay",
                provider="bls",
            )
            historical_capture = self._capture(
                connection,
                suffix="philadelphia-fed",
                provider="philadelphia_fed",
            )
            historical_replay_capture = self._capture(
                connection,
                suffix="philadelphia-fed-replay",
                provider="philadelphia_fed",
            )
            for ordinal, series_id in enumerate(
                (
                    "macro.bls.total_nonfarm_payrolls_sa",
                    "macro.bls.unemployment_rate_sa",
                ),
                start=1,
            ):
                with self.subTest(series_id=series_id):
                    self._series(connection, series_id=series_id, capture_id=bls_capture)
                    release = self._release(
                        connection,
                        suffix=f"employment-{ordinal}",
                        series_id=series_id,
                        capture_id=historical_capture,
                        order=f"{ordinal:020d}",
                    )
                    version = self._version(
                        connection,
                        suffix=f"employment-{ordinal}",
                        series_id=series_id,
                        release_id=release,
                        capture_id=historical_capture,
                        period="2026-07",
                    )
                    connection.execute(
                        "INSERT INTO macro_live_vintage_observations VALUES (?, '2026-07', ?)",
                        (series_id, version),
                    )
                    connection.execute(
                        "INSERT INTO macro_live_vintage_capture_membership VALUES (?, ?, ?, 1)",
                        (historical_replay_capture, version, series_id),
                    )

            self._series(
                connection,
                series_id="macro.bls.cpi_u_all_items_sa",
                capture_id=bls_capture,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._release(
                    connection,
                    suffix="cpi-wrong-provider",
                    series_id="macro.bls.cpi_u_all_items_sa",
                    capture_id=historical_capture,
                    order="00000000000000000003",
                )
            cpi_release = self._release(
                connection,
                suffix="cpi-valid",
                series_id="macro.bls.cpi_u_all_items_sa",
                capture_id=bls_capture,
                order="00000000000000000003",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._version(
                    connection,
                    suffix="cpi-valid",
                    series_id="macro.bls.cpi_u_all_items_sa",
                    release_id=cpi_release,
                    capture_id=historical_capture,
                    period="2026-07",
                )
            cpi_version = self._version(
                connection,
                suffix="cpi-valid",
                series_id="macro.bls.cpi_u_all_items_sa",
                release_id=cpi_release,
                capture_id=bls_capture,
                period="2026-07",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO macro_live_vintage_capture_membership VALUES (?, ?, ?, 1)",
                    (historical_capture, cpi_version, "macro.bls.cpi_u_all_items_sa"),
                )
            connection.execute(
                "INSERT INTO macro_live_vintage_capture_membership VALUES (?, ?, ?, 1)",
                (bls_replay_capture, cpi_version, "macro.bls.cpi_u_all_items_sa"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT provider FROM macro_live_vintage_captures WHERE capture_id=?",
                    (historical_capture,),
                ).fetchone()[0],
                "philadelphia_fed",
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT provider_series_code, unit, value_representation, availability_basis
                    FROM macro_live_vintage_series
                    WHERE series_id='macro.bls.total_nonfarm_payrolls_sa'
                    """
                ).fetchone(),
                ("CES0000000001", "thousands_persons", "level", "mixed"),
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT provider_series_code, unit, value_representation, availability_basis
                    FROM macro_live_vintage_series
                    WHERE series_id='macro.bls.unemployment_rate_sa'
                    """
                ).fetchone(),
                ("LNS14000000", "percent", "rate", "mixed"),
            )
            self._assert_clean(connection)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
