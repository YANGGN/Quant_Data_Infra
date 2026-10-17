from __future__ import annotations

import http.client
import json
import unittest
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.news.fmp_stock_latest import (
    FMP_STOCK_LATEST_MAX_BYTES,
    FMP_STOCK_LATEST_PATH,
    StdlibFmpStockLatestTransport,
    _parse_rows,
)


class _Response:
    def __init__(
        self,
        *,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        body: bytes = b"[]",
        content_length: str | None = None,
    ) -> None:
        self.status = status
        self._content_type = content_type
        self._body = body
        self._content_length = content_length
        self.read_sizes: list[int] = []

    def getheader(self, name: str) -> str | None:
        if name == "Content-Type":
            return self._content_type
        if name == "Content-Length":
            return self._content_length
        return None

    def read(self, amount: int) -> bytes:
        self.read_sizes.append(amount)
        return self._body


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


class FmpStockLatestTransportTests(unittest.TestCase):
    def _call(self, response: _Response) -> tuple[_Connection, object]:
        connection = _Connection(response)
        with mock.patch.object(http.client, "HTTPSConnection", return_value=connection) as factory:
            captured = StdlibFmpStockLatestTransport().get(
                path=FMP_STOCK_LATEST_PATH,
                query={"page": "0", "limit": "1000"},
                headers={"apikey": "offline-key", "Accept": "application/json"},
                timeout_seconds=60,
                max_bytes=FMP_STOCK_LATEST_MAX_BYTES,
            )
        factory.assert_called_once_with("financialmodelingprep.com", timeout=60)
        return connection, captured

    def test_transport_uses_one_exact_get_and_keeps_raw_bytes(self) -> None:
        response = _Response(body=b"[]")
        connection, captured = self._call(response)
        self.assertEqual(
            connection.requests,
            [
                (
                    "GET",
                    "/stable/news/stock-latest?page=0&limit=1000",
                    {"apikey": "offline-key", "Accept": "application/json"},
                )
            ],
        )
        self.assertEqual(captured.body, b"[]")
        self.assertEqual(response.read_sizes, [FMP_STOCK_LATEST_MAX_BYTES + 1])
        self.assertTrue(connection.closed)

    def test_transport_rejects_redirect_status_wrong_mime_and_oversize(self) -> None:
        cases = (
            (_Response(status=302), StoreUnavailableError),
            (_Response(status=503), StoreUnavailableError),
            (_Response(content_type="text/plain"), StoreUnavailableError),
            (
                _Response(content_length=str(FMP_STOCK_LATEST_MAX_BYTES + 1)),
                ResourceLimitError,
            ),
        )
        for response, error in cases:
            with self.subTest(status=response.status, content_type=response._content_type):
                with self.assertRaises(error):
                    self._call(response)

    def test_parser_rejects_error_envelopes_and_any_malformed_row(self) -> None:
        with self.assertRaises(ValidationError):
            _parse_rows(b'{"Error Message":"not an array"}')
        with self.assertRaises(ValidationError):
            _parse_rows(
                b'[{"symbol":"AAPL","publishedDate":"2026-08-14",'
                b'"title":"valid","text":"","url":"https://example.test/a",'
                b'"site":"example"},{"symbol":"MSFT","publishedDate":"",'
                b'"title":"bad","text":"","url":"https://example.test/b",'
                b'"site":"example"}]'
            )

    def test_parser_rejects_non_utf8_and_more_than_one_page(self) -> None:
        valid_row = {
            "symbol": "AAPL",
            "publishedDate": "2026-08-14",
            "title": "valid",
            "text": "",
            "url": "https://example.test/a",
            "site": "example",
        }
        with self.assertRaises(ValidationError):
            _parse_rows(b"\xff")
        with self.assertRaises(ValidationError):
            _parse_rows(json.dumps([valid_row] * 1001).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
