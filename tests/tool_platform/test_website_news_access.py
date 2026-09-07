from __future__ import annotations

from dataclasses import replace

from quant_data.tool_platform.arguments import CurrentNewsSearchArgumentsV22, CurrentNewsSearchArgumentsV23
from quant_data.tool_platform.operations import invoke_operation
from tests.news.test_website_source_integration import _Response
from tests.news.test_website_listings import CLOCK, finviz, rss
from tests.tool_platform import test_news_access_v21 as prior
from quant_data.news.current_multi_source import CurrentMultiSourceImporter, CurrentMultiSourceRequest, CurrentMultiSourceCredentials


class WebsiteNewsAccessTests(prior.NewsAccessV21Tests):
    def _seed_current_news(self):
        super()._seed_current_news()
        for source, body, media in (("finviz", finviz(), "text/html"),
                                    ("financialjuice", rss(), "text/xml")):
            CurrentMultiSourceImporter(self.store_map, self.registry, clock=lambda: CLOCK).run_once(
                request=CurrentMultiSourceRequest(feed_id=source, poll_slot=CLOCK),
                credentials=CurrentMultiSourceCredentials(),
                transport=_Response(self.store_map, body, media))

    def page(self, version, **changes):
        cls = CurrentNewsSearchArgumentsV23 if version == "2.3.0" else CurrentNewsSearchArgumentsV22
        values = dict(query="", symbols=(), mode="latest", as_of=None,
            date_only_policy="completed_date", start_date=None, end_date=None,
            limit=100, source_ids=(), cursor=None)
        values.update(changes)
        context = replace(self._context(), tool_version=version, operation_version=version,
                          operation_graph_id="tool_platform.news.search.v" + version[:-2].replace(".", "_"))
        return invoke_operation(prior.TOOL_NAME, cls(**values), context, self.registry)

    def test_v22_retains_original_source_set_and_v23_includes_websites(self):
        old = self.page("2.2.0")
        new = self.page("2.3.0")
        self.assertEqual({prior._fields(r)["feed_id"] for r in old.records}, {"fed_press", "fmp_stock_latest"})
        self.assertEqual({prior._fields(r)["feed_id"] for r in new.records},
                         {"fed_press", "fmp_stock_latest", "finviz", "financialjuice"})
        for source in ("finviz", "financialjuice"):
            selected = self.page("2.3.0", source_ids=(source,))
            self.assertEqual([prior._fields(r)["feed_id"] for r in selected.records], [source])

    def test_public_dispatch_accepts_both_website_filters(self):
        from quant_data.boundary.dispatcher import ToolDispatcher
        from quant_data.canonical_inspector import _current_news_arguments
        dispatcher = ToolDispatcher(self.store_map, self.registry)
        for source in ("finviz", "financialjuice"):
            result = dispatcher.call("news.search", _current_news_arguments({"source_id": source}),
                                     tool_version="2.3.0")
            fields = {f["name"]: f["value"] for f in result["records"][0]["fields"]}
            self.assertEqual(fields["feed_id"], source)
        self.assertEqual(len(dispatcher.call("news.search", _current_news_arguments({}),
                                            tool_version="2.3.0")["records"]), 4)

    def test_v23_pages_cover_each_article_once(self):
        page = self.page("2.3.0", limit=1)
        seen = []
        while True:
            seen.extend(prior._fields(row)["article_id"] for row in page.records)
            cursor = page.truncation.next_cursor
            if not cursor:
                break
            page = self.page("2.3.0", limit=1, cursor=cursor)
        self.assertEqual(len(seen), 4)
        self.assertEqual(len(set(seen)), 4)
