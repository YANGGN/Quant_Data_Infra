from __future__ import annotations

from html.parser import HTMLParser
import unittest

from quant_data.canonical_inspector import _VIEWS, _render_current_news_page
from quant_data.dashboard.inspector_shell import (
    render_inspector_shell,
    render_inspector_table,
)


class Markup(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.cells: dict[str, tuple[dict[str, str | None], str]] = {}
        self._cell: tuple[dict[str, str | None], list[str]] | None = None
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        self.elements.append((tag, attributes))
        if tag in ("td", "th") and "data-field" in attributes:
            self._cell = (attributes, [])

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[1].append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            attributes, text = self._cell
            self.cells[attributes["data-field"]] = (attributes, "".join(text))
            self._cell = None


class InspectorPresentationTests(unittest.TestCase):
    def shell(self, active: str = "market-prices", **overrides) -> str:
        arguments = {
            "title": "Market prices", "active": active, "revision": "fixture",
            "body": "<section>Fixture body</section>",
            "description": "Fixture description",
        }
        arguments.update(overrides)
        return render_inspector_shell(**arguments)

    def test_navigation_keeps_every_existing_destination_on_each_section(self):
        expected = {"/?view=" + view for view in _VIEWS}
        expected.update(("/news", "/status", "/data-status", "/agent-tools"))
        for active, expected_active in (
            ("market-prices", "/?view=market-prices"),
            ("/news", "/news"),
            ("/status", "/status"),
            ("/data-status", "/data-status"),
            ("/agent-tools", "/agent-tools"),
        ):
            with self.subTest(active=active):
                markup = Markup(self.shell(active))
                links = [attrs for tag, attrs in markup.elements if tag == "a"]
                self.assertTrue(expected.issubset({a.get("href") for a in links}))
                current = [a["href"] for a in links if a.get("aria-current") == "page"]
                self.assertEqual(current, [expected_active])

    def test_shell_escapes_metadata_and_loads_only_local_assets(self):
        hostile = '<img src=x onerror="window.__injected=true">'
        markup = Markup(self.shell(title=hostile, revision=hostile,
                                  description=hostile, eyebrow=hostile, footer=hostile))
        self.assertFalse(any(tag == "img" for tag, _ in markup.elements))
        scripts = [attrs for tag, attrs in markup.elements if tag == "script"]
        self.assertTrue(scripts)
        self.assertTrue(all(str(a.get("src", "")).startswith("/assets/") for a in scripts))
        assets = [attrs for tag, attrs in markup.elements
                  if tag == "link" and attrs.get("rel") in ("stylesheet", "preload")]
        self.assertTrue(all(str(a.get("href", "")).startswith("/assets/") for a in assets))

    def test_all_fields_and_exact_values_survive_without_javascript(self):
        row = {
            "symbol": "QA",
            "trade_date": "2026-09-04",
            "close": "12345678901234567890.1234500",
            "available_at": "2026-09-05T00:00:00.000001Z",
            "captured_at": "2026-09-05T00:01:00Z",
            "volume": 0,
            "missing_reason": None,
            "empty": "",
            "flag": False,
            "hostile": '</td><script>window.__injected=true</script>',
        }
        markup = Markup(render_inspector_table(
            columns=tuple(row), rows=[row], caption="Fixture rows",
            empty_message="No fixture rows",
        ))
        self.assertEqual(set(markup.cells), set(row))
        for field in ("symbol", "trade_date", "close", "available_at",
                      "captured_at", "hostile"):
            self.assertEqual(markup.cells[field][1], row[field])
        self.assertEqual(markup.cells["volume"][1], "0")
        kinds = [markup.cells[field][0].get("data-value-kind")
                 for field in ("volume", "missing_reason", "empty", "flag")]
        self.assertEqual(len(set(kinds)), 4)
        self.assertFalse(any(tag == "script" for tag, _ in markup.elements))

    def test_news_source_failure_is_visible_without_opening_a_disclosure(self):
        for error in (None, "Retained source status unavailable"):
            document = _render_current_news_page({}, {}, "fixture", None, {}, error)
            markup = Markup(document)
            disclosures = [attrs for tag, attrs in markup.elements
                           if tag == "details" and attrs.get("class") == "inspector-disclosure"]
            self.assertEqual(len(disclosures), 1)
            self.assertEqual("open" in disclosures[0], error is not None)
            if error:
                self.assertIn(error, document)
                self.assertTrue(any(attrs.get("role") == "alert"
                                    for _, attrs in markup.elements))

    def test_empty_result_keeps_table_semantics_and_escapes_message(self):
        message = '<img src=x onerror="bad"> No retained rows'
        document = render_inspector_table(
            columns=("symbol", "close"), rows=[], caption="Empty fixture",
            empty_message=message,
        )
        markup = Markup(document)
        self.assertFalse(any(tag == "img" for tag, _ in markup.elements))
        self.assertTrue(any(tag == "table" for tag, _ in markup.elements))
        self.assertIn("&lt;img", document)
        self.assertIn("No retained rows", document)


if __name__ == "__main__":
    unittest.main()
