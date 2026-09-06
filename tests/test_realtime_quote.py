"""Offline safety and freshness checks for the one-call live quote boundary."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from quant_data.errors import CapabilityUnavailableError, ValidationError
from quant_data.tool_platform.context import CapabilitySet
from quant_data.tool_platform.realtime_quote import (
    CAPABILITY, QuoteCapture, invoke_quote, local_quote_fetcher,
    parse_quote_arguments, quote_result,
)

def capture(**overrides):
    row = {"symbol": "MSFT", "price": 100, "timestamp": 1788544800,
           "volume": 123, "previousClose": 99}
    row.update(overrides)
    return QuoteCapture(json.dumps([row]).encode(), "2026-09-06T12:00:00Z")

class RealtimeQuoteTests(unittest.TestCase):
    def test_fresh_fetch_discloses_old_provider_trade_and_unknown_currency(self):
        result = quote_result("MSFT", capture())
        fields = {f.name: f.value for f in result.records[0].fields}
        self.assertEqual(fields["price"], 100)
        self.assertEqual(fields["currency"], "SOURCE_UNSPECIFIED")
        self.assertGreater(fields["quote_age_seconds"], 86400)
        self.assertIsNone(fields["bid"])
        self.assertIn("quote_older_than_15_minutes", [w.code for w in result.warnings])

    def test_symbol_and_numeric_timestamp_guards(self):
        for changes in [{"symbol":"AAPL"}, {"price":-1}, {"price":True}, {"timestamp":True},
                        {"timestamp":999999999999}, {"timestamp":0}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                quote_result("MSFT", capture(**changes))
        self.assertEqual(quote_result("MSFT", QuoteCapture(b"[]", "2026-09-06T12:00:00Z")).status, "not_established")
        self.assertEqual(quote_result("MSFT", capture(price=None)).status, "not_established")

    def test_invalid_public_arguments_cannot_reach_transport(self):
        fetch = Mock(return_value=capture())
        context = SimpleNamespace(checkpoint=lambda:None, capabilities=CapabilitySet(frozenset({CAPABILITY})), quote_fetcher=fetch)
        for args in [{"ticker":"MSFT,AAPL"}, {"ticker":"MSFT&apikey=x"}, {"ticker":" MSFT"},
                     {"ticker":"MSFT", "url":"https://example.com"}, {"ticker":None}, {"ticker":"../MSFT"}]:
            with self.subTest(args=args), self.assertRaises(ValidationError):
                invoke_quote(args, context)
        fetch.assert_not_called()

    def test_disabled_host_has_no_fetch_and_enabled_host_exactly_one(self):
        fetch = Mock(return_value=capture())
        context = SimpleNamespace(checkpoint=lambda:None, capabilities=CapabilitySet(), quote_fetcher=fetch)
        with self.assertRaises(CapabilityUnavailableError):
            invoke_quote({"ticker":"MSFT"},context)
        fetch.assert_not_called()
        context.capabilities = CapabilitySet(frozenset({CAPABILITY}))
        result = invoke_quote({"ticker":"msft"},context)
        self.assertEqual(result.status,"ok")
        fetch.assert_called_once_with("MSFT")

    def test_construction_has_no_credential_probe_and_failure_has_no_retry_or_secret(self):
        with tempfile.TemporaryDirectory(prefix="quote-offline-") as temp:
            with patch("quant_data.credentials.read_project_credential") as secret:
                fetch = local_quote_fetcher(Path(temp))
                secret.assert_not_called()
            with patch("quant_data.credentials.read_project_credential",return_value="test-secret") as secret, \
                 patch("quant_data.operations.fmp_macro_calendar_history._StdlibTransport") as transport:
                transport.return_value.request.side_effect = RuntimeError("https://bad/?apikey=test-secret")
                with self.assertRaises(CapabilityUnavailableError) as caught:
                    fetch("MSFT")
                self.assertNotIn("test-secret", str(caught.exception))
                transport.return_value.request.assert_called_once()
                secret.assert_called_once()
