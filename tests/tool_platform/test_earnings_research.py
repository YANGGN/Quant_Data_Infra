"""Offline source-membership and cutoff checks for production earnings."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from tests.company import test_fmp_analyst_history as analyst_fixture
from quant_data.tool_platform.context import FixedClock, ExecutionBudget
from quant_data.tool_platform.earnings_research import calendar, setup
from quant_data.tool_platform.retained_research_common import unpack
from quant_data.company.retained_analyst_reader import read_analyst
from quant_data.fingerprint import mutation_fingerprint

class EarningsResearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=analyst_fixture.AnalystHistoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.context=SimpleNamespace(store_map=self.fixture.stores, budget=ExecutionBudget(),
            clock=FixedClock(Decimal(1),"2026-09-28T22:00:00Z"), checkpoint=lambda:None)
        self.args=dict(symbols=["NST"],start_date="2026-09-01",end_date="2026-10-31")
    def test_calendar_latest_capture_membership_and_zero_actual(self):
        old=dict(symbol="NST",date="2026-09-10",epsActual=1)
        current=dict(symbol="NST",date="2026-10-01",epsActual=None,epsEstimated=2)
        self.fixture.pub([old,current],endpoint="earnings")
        self.fixture.pub([{**current,"epsActual":0}],endpoint="earnings",at="2026-09-08T00:00:00Z")
        before=mutation_fingerprint(self.fixture.stores)
        value=calendar(self.args,self.context,self.fixture.registry)
        events=[unpack(r) for r in value.records if r.record_type=="earnings_event"]
        self.assertEqual([r["event_date"] for r in events],["2026-10-01"])
        self.assertEqual(events[0]["date_status"],"reported_actual_present")
        self.assertEqual(events[0]["eps_actual"],0)
        self.assertEqual(before,mutation_fingerprint(self.fixture.stores))
    def test_cutoff_excludes_later_capture_and_preserves_unconfirmed(self):
        self.fixture.pub([dict(symbol="NST",date="2026-10-01",epsEstimated=2)],endpoint="earnings")
        self.fixture.pub([dict(symbol="NST",date="2026-10-02",epsEstimated=3)],endpoint="earnings",at="2026-09-08T00:00:00Z")
        value=calendar(dict(self.args,as_of="2026-09-07T21:00:00-02:00"),self.context,self.fixture.registry)
        event=unpack(value.records[0])
        self.assertEqual(event["event_date"],"2026-10-01")
        self.assertEqual(event["date_status"],"scheduled_unconfirmed")
        empty=calendar(dict(self.args,as_of="2026-01-01T00:00:00Z"),self.context,self.fixture.registry)
        self.assertEqual(empty.status,"not_established")
    def test_history_records_membership_time_separately_from_value_time(self):
        a=dict(symbol="NST",date="2026-12-31",epsAvg=2)
        b=dict(symbol="NST",date="2027-12-31",epsAvg=3)
        self.fixture.pub([a,b],period="quarter")
        self.fixture.pub([a,dict(b,epsAvg=4)],period="quarter",at="2026-09-08T00:00:00Z")
        rows=read_analyst(self.context,"NST","analyst-estimates","2026-09-28T00:00:00.000000Z",period="quarter",history=True)
        repeat=[r for r in rows if r["target_period_end"]=="2026-12-31"]
        self.assertEqual(len(repeat),2)
        self.assertEqual(repeat[0]["observation_version_id"],repeat[1]["observation_version_id"])
        self.assertNotEqual(repeat[0]["capture_observed_at"],repeat[1]["capture_observed_at"])
    def test_setup_composes_independent_context_and_preserves_target(self):
        self.fixture.pub([dict(symbol="NST",date="2026-12-31",epsAvg=2)],period="quarter")
        with patch("quant_data.tool_platform.retained_research_sources.transcript_history",return_value=[]), \
             patch("quant_data.tool_platform.retained_research_sources.price_context",return_value=[]), \
             patch("quant_data.tool_platform.retained_research_sources.news_context",return_value=[]), \
             patch("quant_data.tool_platform.retained_research_sources.valuation_context",return_value=[]):
            value=setup(dict(symbol="NST",start_date="2026-09-01",end_date="2026-10-31"),self.context,self.fixture.registry)
        self.assertEqual(unpack(value.records[0])["target_period_end"],"2026-12-31")
        self.assertEqual(unpack(value.records[0])["event_period_link"],"not_inferred")
