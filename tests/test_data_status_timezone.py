from __future__ import annotations

from html.parser import HTMLParser
import unittest

from quant_data.dashboard.data_status_page import render_data_status_page


class CaptureCell(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.column = -1
        self.in_capture = False
        self.text = []
        self.attributes = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if tag == 'tbody':
            self.in_body = True
        elif self.in_body and tag == 'tr':
            self.column = -1
        elif self.in_body and tag in ('td', 'th'):
            self.column += 1
            self.in_capture = dict(attrs).get("data-field") == "last_successful_capture"
        if self.in_capture:
            self.attributes.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == 'tbody':
            self.in_body = False
        elif tag in ('td', 'th'):
            self.in_capture = False

    def handle_data(self, text):
        if self.in_capture:
            self.text.append(text)


def cell_for(value):
    result = {'records': [{'fields': [
        {'name': 'id', 'value': 'fixture.capture'},
        {'name': 'latest_successful_capture', 'value': value},
    ]}]}
    document = render_data_status_page(result, registry_revision='fixture')
    return CaptureCell(document)


class DataStatusTimezoneTests(unittest.TestCase):
    def test_eastern_conversion_preserves_instant_fraction_and_dst(self):
        cases = (
            ('2026-01-15T13:05:00Z', '2026-01-15 08:05:00 EST'),
            ('2026-09-04T12:45:20Z', '2026-09-04 08:45:20 EDT'),
            ('2026-01-15T02:30:00Z', '2026-01-14 21:30:00 EST'),
            ('2026-09-03T22:30:20.930288Z', '2026-09-03 18:30:20.930288 EDT'),
            ('2026-09-03T22:30:20.1200Z', '2026-09-03 18:30:20.1200 EDT'),
            ('2026-09-04T14:45:20+02:00', '2026-09-04 08:45:20 EDT'),
            ('2026-03-08T06:59:59Z', '2026-03-08 01:59:59 EST'),
            ('2026-03-08T07:00:00Z', '2026-03-08 03:00:00 EDT'),
            ('2026-11-01T05:30:00Z', '2026-11-01 01:30:00 EDT'),
            ('2026-11-01T06:30:00Z', '2026-11-01 01:30:00 EST'),
        )
        for original, expected in cases:
            with self.subTest(original=original):
                parsed = cell_for({'captured_at': {'value': original, 'precision': 'datetime'}})
                self.assertEqual(''.join(parsed.text).strip(), expected)
                self.assertTrue(any(attrs.get('datetime') == original or attrs.get('title') == original
                                    for _, attrs in parsed.attributes))

    def test_missing_and_unzoned_values_do_not_gain_invented_precision(self):
        cases = (
            (None, '—'),
            ({'captured_at': None}, '—'),
            ({'captured_at': '2026-09-04'}, '2026-09-04'),
            ({'captured_at': '2026-09-04T12:45:20'}, '2026-09-04T12:45:20'),
            ({'captured_at': 'invalid'}, 'invalid'),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(''.join(cell_for(value).text).strip(), expected)

    def test_string_json_fallback_and_hostile_timestamp_are_safe(self):
        parsed = cell_for('{"recorded_at":{"value":"2026-09-04T12:45:20Z"}}')
        self.assertEqual(''.join(parsed.text).strip(), '2026-09-04 08:45:20 EDT')
        hostile = '<img src=x onerror="bad">'
        parsed = cell_for({'captured_at': hostile})
        self.assertEqual(''.join(parsed.text).strip(), hostile)
        self.assertFalse(any(tag == 'img' for tag, _ in parsed.attributes))


if __name__ == '__main__':
    unittest.main()
