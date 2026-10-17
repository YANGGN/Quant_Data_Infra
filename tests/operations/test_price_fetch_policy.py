import unittest
from quant_data.operations.price_fetch_policy import current_price_targets

class RetiredPricePolicyTests(unittest.TestCase):
    def test_retirement_dates_and_exact_symbols_preserve_original_rows(self):
        rows = tuple({"provider_symbol": s} for s in ("ATAI", "IRBO", "ARTY", "AAPL"))
        active, excluded = current_price_targets(rows, session="2026-09-22")
        self.assertEqual([r["provider_symbol"] for r in active], ["ARTY", "AAPL"])
        self.assertEqual([r["symbol"] for r in excluded], ["ATAI", "IRBO"])
        self.assertEqual(len(rows), 4)
        self.assertIs(active[0], rows[2])
        for day, expected in (("2024-08-09", ["ATAI", "IRBO", "ARTY", "AAPL"]),
                              ("2024-08-12", ["ATAI", "ARTY", "AAPL"]),
                              ("2026-09-10", ["ATAI", "ARTY", "AAPL"]),
                              ("2026-09-11", ["ARTY", "AAPL"])):
            with self.subTest(day=day):
                actual, _ = current_price_targets(rows, session=day)
                self.assertEqual([r["provider_symbol"] for r in actual], expected)
