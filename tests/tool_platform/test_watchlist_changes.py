from decimal import Decimal
from types import SimpleNamespace
import unittest
from quant_data.tool_platform.watchlist_changes import invoke,change
from quant_data.tool_platform.retained_research_common import unpack
from quant_data.tool_platform.context import FixedClock,ExecutionBudget
from tests.company import test_fmp_analyst_history as fixtures

class WatchlistChangesTests(unittest.TestCase):
    def test_capture_window_does_not_backdate_old_earnings_events(self):
        f=fixtures.AnalystHistoryTests();f.setUp();self.addCleanup(f.tearDown)
        row=dict(symbol="NST",date="2020-01-01",epsActual=1)
        f.pub([row],endpoint="earnings")
        f.pub([dict(row,epsActual=2)],endpoint="earnings",at="2026-09-08T00:00:00Z")
        context=SimpleNamespace(store_map=f.stores,clock=FixedClock(Decimal(1),"2026-09-28T22:00:00Z"),budget=ExecutionBudget(),checkpoint=lambda:None)
        args=dict(symbols=["NST"],since="2026-09-07T23:00:00Z",domains=["earnings"])
        out=invoke(args,context,f.registry)
        rows=[unpack(r) for r in out.records if r.record_type=="watchlist_change"]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["change_kind"],"revised_value")
        same=invoke(dict(args,since="2026-09-07T22:00:00Z"),context,f.registry)
        self.assertEqual(rows[0]["change_id"],next(unpack(r)["change_id"] for r in same.records if r.record_type=="watchlist_change"))
        empty=invoke(dict(args,since="2026-09-08T00:00:00Z"),context,f.registry)
        self.assertFalse(any(r.record_type=="watchlist_change" for r in empty.records))
    def test_change_ids_bind_domain_identity_not_review_interval(self):
        a=change("A","earnings","id","2026-01-01",{}, "new")[1]
        b=change("A","estimates","id","2026-01-01",{}, "new")[1]
        self.assertNotEqual(a["change_id"],b["change_id"])
