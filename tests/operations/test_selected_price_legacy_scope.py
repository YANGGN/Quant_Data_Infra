import unittest
from unittest.mock import patch
from quant_data.operations.company_market_refresh import load_inputs
from quant_data.operations import stage12e_market_close as market_close
from quant_data.news.current_market_coverage import read_current_market_news_coverage
from tests.market import test_selected_instrument_creation as fixtures

class LegacyPriceEnrollmentTests(unittest.TestCase):
    def test_selected_instrument_insertion_does_not_expand_other_legacy_collectors(self):
        f=fixtures.SelectedInstrumentCreationTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        before_news=read_current_market_news_coverage(f.f.stores)
        before_company=load_inputs(f.f.stores)[0]
        f.f.publisher.publish(f.batch(),create_missing_instruments=True)
        self.assertEqual(read_current_market_news_coverage(f.f.stores),before_news)
        self.assertEqual(load_inputs(f.f.stores)[0],before_company)
        with patch.object(market_close,'_quiet_market_connection',lambda _:__import__('quant_data.stores',fromlist=['quiet_immutable_read_connection']).quiet_immutable_read_connection(f.f.stores,'market')),patch.object(market_close,'_reconcile_store_contract'):
            scope=market_close._canonical_scheduled_universe()
        self.assertNotIn('NVDA',{r.symbol for r in scope.instruments})
        self.assertIn('AAPL',{r.symbol for r in scope.instruments})
