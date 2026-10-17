from __future__ import annotations

import sqlite3
import unittest
from types import SimpleNamespace

from quant_data.market.provider_close_series import ProviderClosePriceRepository
from quant_data.market.stage10_series import Stage10DailyPriceQuery, Stage10DailyPriceRepository


class ProviderClosePriceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.query = Stage10DailyPriceQuery(
            identifier="AAPL", identifier_kind="provider_symbol",
            start_date="2026-08-10", end_date="2026-08-12", mode="latest",
            as_of=None, date_only_policy="completed_date", limit=100,
        )
        self.instrument = {
            "instrument_id": "fixture-aapl", "provider_symbol": "AAPL",
            "asset_type": "equity", "display_name": "AAPL fixture",
            "exchange_code": "XNAS", "currency_segment": "provider_native",
            "identity_seed_sha256": "a" * 64,
            "captured_at": "2026-08-15T01:00:00Z", "captured_precision": "datetime",
            "run_id": "identity-run",
        }
        self.row = {
            "version_id": "version-1", "instrument_id": "fixture-aapl",
            "trade_date": "2026-08-10", "provider": "fmp",
            "price_variant": "fmp_full_eod_v1", "currency_segment": "provider_native",
            "open_value": "100", "high_value": "103", "low_value": "99",
            "close_value": "102", "volume": 1,
            "available_at": "2026-08-15T01:00:00Z", "available_precision": "datetime",
            "captured_at": "2026-08-15T01:00:00Z", "captured_precision": "datetime",
            "correction_sequence": 1, "supersedes_version_id": None,
            "capture_id": "capture-1", "artifact_id": "artifact-1",
            "snapshot_id": "snapshot-1", "run_id": "capture-run", "source_row": 1,
            "response_sha256": "b" * 64, "request_scope_sha256": "c" * 64,
            "semantic_identity": "d" * 64,
            "normalization_version": "stage10.fmp.daily_price.v1",
        }
        self.old = object.__new__(Stage10DailyPriceRepository)
        self.new = object.__new__(ProviderClosePriceRepository)
        self.old._registry = SimpleNamespace(revision="fixture")
        self.new._registry = SimpleNamespace(revision="fixture")

    def _series(self, repository, field, rows):
        return repository._series_from_rows(
            query=self.query, field=field, migrations=[], instrument=self.instrument,
            rows=rows, identity_warnings=(), selection_warnings=(), truncated=False,
        )

    def test_bound_close_is_unchanged_and_old_reader_stays_unestablished(self) -> None:
        old = self._series(self.old, "close", [self.row])
        bound = {**self.row, "_provider_close_binding_established": True}
        series = self._series(self.new, "close", [bound])
        self.assertEqual(series.observations[0].value, old.observations[0].value)
        self.assertEqual(old.metadata["adjustment_status"], "not_established")
        self.assertIn("adjustment_and_total_return_semantics_not_established", old.warnings)
        self.assertEqual(series.metadata["adjustment_status"], "split_adjusted_excluding_distributions")
        self.assertEqual(series.metadata["source_price_field"], "close")
        self.assertFalse(series.metadata["price_adjustment_applied_by_tool"])
        self.assertEqual(series.metadata["source_capture_count"], 1)
        self.assertEqual(series.metadata["adjustment_vintage_status"], "single_provider_capture")
        self.assertNotIn("adjustment_and_total_return_semantics_not_established", series.warnings)
        self.assertIn("total_return_not_provided", series.warnings)
        self.assertIn("historical_adjustment_reconstruction_not_established", series.warnings)
        self.assertIn("adjustment_status:split_adjusted_excluding_distributions", series.observations[0].quality_flags)
        series.validate_lineage()

    def test_open_and_unbound_close_are_not_overclaimed(self) -> None:
        bound = {**self.row, "_provider_close_binding_established": True}
        for field, rows in (("open", [bound]), ("close", [{**self.row, "_provider_close_binding_established": False}])):
            with self.subTest(field=field):
                series = self._series(self.new, field, rows)
                self.assertEqual(series.metadata["adjustment_status"], "not_established")
                self.assertIsNone(series.metadata["source_adjustment_binding"])
                self.assertIn("adjustment_status:not_established", series.observations[0].quality_flags)

    def test_empty_or_wrong_capture_evidence_fails_closed(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("""CREATE TABLE stage10_daily_price_captures (
            capture_id TEXT, provider TEXT, instrument_id TEXT, endpoint_path TEXT,
            normalization_version TEXT)""")
        selected = {key: self.row[key] for key in (
            "capture_id", "instrument_id", "provider", "price_variant", "currency_segment",
        )}
        self.assertFalse(ProviderClosePriceRepository._with_capture_binding(connection, [selected])[0]["_provider_close_binding_established"])
        connection.execute("INSERT INTO stage10_daily_price_captures VALUES (?, ?, ?, ?, ?)", (
            "capture-1", "fmp", "fixture-aapl", "/wrong", "stage10.fmp.daily_price.v1",
        ))
        self.assertFalse(ProviderClosePriceRepository._with_capture_binding(connection, [selected])[0]["_provider_close_binding_established"])
        connection.execute("UPDATE stage10_daily_price_captures SET endpoint_path=?", ("/stable/historical-price-eod/full",))
        self.assertTrue(ProviderClosePriceRepository._with_capture_binding(connection, [selected])[0]["_provider_close_binding_established"])
        for column, value in (("provider", "other"), ("instrument_id", "other"),
                              ("normalization_version", "other")):
            with self.subTest(column=column):
                original = connection.execute(f"SELECT {column} FROM stage10_daily_price_captures").fetchone()[0]
                connection.execute(f"UPDATE stage10_daily_price_captures SET {column}=?", (value,))
                self.assertFalse(ProviderClosePriceRepository._with_capture_binding(connection, [selected])[0]["_provider_close_binding_established"])
                connection.execute(f"UPDATE stage10_daily_price_captures SET {column}=?", (original,))
        connection.close()

    def test_mixed_capture_vintages_are_disclosed(self) -> None:
        rows = [
            {**self.row, "_provider_close_binding_established": True},
            {**self.row, "version_id": "version-2", "capture_id": "capture-2",
             "trade_date": "2026-08-11", "_provider_close_binding_established": True},
        ]
        series = self._series(self.new, "close", rows)
        self.assertEqual(series.metadata["source_capture_count"], 2)
        self.assertEqual(series.metadata["adjustment_vintage_status"], "mixed_provider_captures")
        self.assertIn("mixed_provider_adjustment_vintages_not_reconciled", series.warnings)


if __name__ == "__main__":
    unittest.main()
