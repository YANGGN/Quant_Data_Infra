from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_APPROVED_TARGET_ROOT,
    FMP_STAGE10_MAX_CONSTITUENT_BYTES,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_PRICE_PATH,
    FMP_STAGE10_SP500_CONSTITUENT_PATH,
    FMP_STAGE10_TIMEOUT_SECONDS,
    FmpPriceHistoryUnavailable,
    FmpStage10PriceCapture,
    StdlibFmpStage10Transport,
    capture_fmp_stage10_price,
    capture_fmp_stage10_universe,
    parse_fmp_stage10_price_response,
    parse_fmp_stage10_universe_response,
    prepare_fmp_stage10_price_capture,
    prepare_fmp_stage10_universe_capture,
)
from quant_data.market.stage10_scope import load_stage10_market_scope
from quant_data.stores import StoreMap


KEY = "stage10-test-secret"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = PROJECT_ROOT / "config" / "stage10_market_scope.json"


def _price_row(
    symbol: str,
    trade_date: str,
    *,
    volume: int = 1_000,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": trade_date,
        "open": 100,
        "high": 103,
        "low": 99,
        "close": 102,
        "volume": volume,
        "change": 2,
        "changePercent": 2,
        "vwap": 101,
    }


def _price_body(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


def _constituent_body(*, secret_name: bool = False) -> bytes:
    rows = [
        {
            "symbol": "AAPL",
            "name": KEY if secret_name else "Apple Inc.",
            "sector": "Information Technology",
            "subSector": "Technology Hardware, Storage & Peripherals",
            "headQuarter": "Cupertino, California",
            "dateFirstAdded": "1982-11-30",
            "cik": "0000320193",
            "founded": "1976",
        },
        {"symbol": "MSFT", "name": "Microsoft Corporation"},
    ]
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


class _RecordingTransport:
    def __init__(self, response: CapturedFmpStage10Response) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> CapturedFmpStage10Response:
        self.calls.append(dict(kwargs))
        return self._response


class Stage10FmpCaptureTests(unittest.TestCase):
    def test_capture_request_shapes_are_exact_and_sensitive_values_stay_out_of_repr(
        self,
    ) -> None:
        universe_response = CapturedFmpStage10Response(
            status=200,
            content_type="application/json",
            body=_constituent_body(secret_name=True),
        )
        universe_transport = _RecordingTransport(universe_response)
        universe_prepared = prepare_fmp_stage10_universe_capture(
            FMP_STAGE10_SP500_CONSTITUENT_PATH
        )
        universe_capture = capture_fmp_stage10_universe(
            universe_prepared,
            api_key=KEY,
            transport=universe_transport,
        )

        self.assertEqual(len(universe_transport.calls), 1)
        self.assertEqual(
            universe_transport.calls[0],
            {
                "path": FMP_STAGE10_SP500_CONSTITUENT_PATH,
                "query": {},
                "headers": {"apikey": KEY},
                "timeout_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
                "max_bytes": FMP_STAGE10_MAX_CONSTITUENT_BYTES,
            },
        )

        price_response = CapturedFmpStage10Response(
            status=200,
            content_type="application/json; charset=utf-8",
            body=_price_body([_price_row("SPY", "1993-01-29")]),
        )
        price_transport = _RecordingTransport(price_response)
        price_prepared = prepare_fmp_stage10_price_capture("SPY")
        price_capture = capture_fmp_stage10_price(
            price_prepared,
            api_key=KEY,
            transport=price_transport,
        )

        self.assertEqual(len(price_transport.calls), 1)
        self.assertEqual(
            price_transport.calls[0],
            {
                "path": FMP_STAGE10_PRICE_PATH,
                "query": {"symbol": "SPY"},
                "headers": {"apikey": KEY},
                "timeout_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
                "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
            },
        )
        for value in (
            universe_response,
            universe_prepared,
            universe_capture,
            price_prepared,
            price_capture,
        ):
            with self.subTest(value=type(value).__name__):
                self.assertNotIn(KEY, repr(value))

    def test_price_histories_allow_variable_earliest_dates_sort_and_zero_volume_indexes(
        self,
    ) -> None:
        newest_first = _price_body(
            [
                _price_row("SPY", "2026-08-10"),
                _price_row("SPY", "1993-01-29"),
            ]
        )
        oldest_first = _price_body(
            [
                _price_row("SPY", "1993-01-29"),
                _price_row("SPY", "2026-08-10"),
            ]
        )
        first = parse_fmp_stage10_price_response(symbol="SPY", body=newest_first)
        second = parse_fmp_stage10_price_response(symbol="SPY", body=oldest_first)

        self.assertEqual(first.earliest_date, "1993-01-29")
        self.assertEqual(first.latest_date, "2026-08-10")
        self.assertEqual(
            tuple(row.trade_date for row in first.rows),
            ("1993-01-29", "2026-08-10"),
        )
        self.assertNotEqual(first.raw_bytes_sha256, second.raw_bytes_sha256)
        self.assertEqual(first.semantic_sha256, second.semantic_sha256)

        index_capture = parse_fmp_stage10_price_response(
            symbol="^GSPC",
            body=_price_body(
                [
                    _price_row("^GSPC", "1957-03-04", volume=0),
                    _price_row("^GSPC", "2026-08-10", volume=0),
                ]
            ),
        )
        self.assertEqual(index_capture.earliest_date, "1957-03-04")
        self.assertEqual(tuple(row.volume for row in index_capture.rows), (0, 0))

    def test_universe_parser_retains_raw_digest_and_rejects_nonclosed_shapes(self) -> None:
        body = _constituent_body()
        capture = parse_fmp_stage10_universe_response(
            endpoint_path=FMP_STAGE10_SP500_CONSTITUENT_PATH,
            body=body,
        )
        self.assertEqual(capture.raw_bytes_sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(capture.raw_bytes, body)
        self.assertEqual(
            tuple(row.symbol for row in capture.constituents),
            ("AAPL", "MSFT"),
        )

        extra = json.loads(body)
        extra[0]["unexpected"] = "rejected"
        missing_name = json.loads(body)
        missing_name[0].pop("name")
        duplicate = json.loads(body)
        duplicate[1]["symbol"] = "AAPL"
        for name, rows in (
            ("extra", extra),
            ("missing_name", missing_name),
            ("duplicate", duplicate),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    parse_fmp_stage10_universe_response(
                        endpoint_path=FMP_STAGE10_SP500_CONSTITUENT_PATH,
                        body=_price_body(rows),
                    )

    def test_price_parser_rejects_duplicate_symbols_nonfinite_ohlc_volume_and_malformed_data(
        self,
    ) -> None:
        base = [_price_row("SPY", "1993-01-29"), _price_row("SPY", "1993-02-01")]
        duplicate = [dict(row) for row in base]
        duplicate[1]["date"] = "1993-01-29"
        wrong_symbol = [dict(row) for row in base]
        wrong_symbol[1]["symbol"] = "QQQ"
        bad_ohlc = [dict(row) for row in base]
        bad_ohlc[0]["low"] = 104
        negative_volume = [dict(row) for row in base]
        negative_volume[0]["volume"] = -1
        fractional_volume = [dict(row) for row in base]
        fractional_volume[0]["volume"] = 1.5
        nonfinite = [dict(row) for row in base]
        nonfinite[0]["close"] = float("nan")
        for name, body in (
            ("duplicate", _price_body(duplicate)),
            ("wrong_symbol", _price_body(wrong_symbol)),
            ("ohlc", _price_body(bad_ohlc)),
            ("negative_volume", _price_body(negative_volume)),
            ("fractional_volume", _price_body(fractional_volume)),
            ("nonfinite", _price_body(nonfinite)),
            ("malformed", b"\xff"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    parse_fmp_stage10_price_response(symbol="SPY", body=body)

        with self.assertRaises(ResourceLimitError):
            parse_fmp_stage10_price_response(
                symbol="SPY",
                body=b"x" * (FMP_STAGE10_MAX_PRICE_BYTES + 1),
            )
        with self.assertRaises(ResourceLimitError):
            parse_fmp_stage10_universe_response(
                endpoint_path=FMP_STAGE10_SP500_CONSTITUENT_PATH,
                body=b"x" * (FMP_STAGE10_MAX_CONSTITUENT_BYTES + 1),
            )

    def test_capture_marks_only_http_json_list_history_contract_failures_skippable(self) -> None:
        prepared = prepare_fmp_stage10_price_capture("EUV")
        skippable = (
            b"[]",
            _price_body([{"symbol": "EUV"}]),
        )
        for body in skippable:
            with self.subTest(body=body):
                with self.assertRaises(FmpPriceHistoryUnavailable) as raised:
                    capture_fmp_stage10_price(
                        prepared,
                        api_key=KEY,
                        transport=_RecordingTransport(
                            CapturedFmpStage10Response(
                                status=200,
                                content_type="application/json; charset=utf-8",
                                body=body,
                            )
                        ),
                    )
                failure = raised.exception
                self.assertEqual(failure.symbol, "EUV")
                self.assertEqual(failure.status, 200)
                self.assertEqual(failure.content_type, "application/json")
                self.assertEqual(failure.body_sha256, hashlib.sha256(body).hexdigest())
                self.assertNotIn(KEY, repr(failure))

        for status, content_type, body in (
            (200, "application/json", b"{"),
            (200, "application/json", b"{}"),
            (401, "application/json", b"[]"),
            (200, "text/plain", b"[]"),
        ):
            with self.subTest(status=status, content_type=content_type):
                with self.assertRaises((ValidationError, StoreUnavailableError)):
                    capture_fmp_stage10_price(
                        prepared,
                        api_key=KEY,
                        transport=_RecordingTransport(
                            CapturedFmpStage10Response(status, content_type, body)
                        ),
                    )

    def test_stdlib_transport_rejects_scope_broadeners_before_connection_and_does_not_redirect(
        self,
    ) -> None:
        scope = load_stage10_market_scope(SCOPE_PATH)
        stores = FMP_STAGE10_APPROVED_TARGET_ROOT / "stores"
        approved_map = StoreMap.four_explicit(
            market=stores / "market.sqlite",
            macro=stores / "macro.sqlite",
            company=stores / "company.sqlite",
            news=stores / "news.sqlite",
        )
        with self.assertRaises(ValidationError):
            StdlibFmpStage10Transport(
                StoreMap.four_explicit(
                    market=Path("/tmp/stage10-wrong/market.sqlite"),
                    macro=Path("/tmp/stage10-wrong/macro.sqlite"),
                    company=Path("/tmp/stage10-wrong/company.sqlite"),
                    news=Path("/tmp/stage10-wrong/news.sqlite"),
                ),
                scope,
            )
        transport = StdlibFmpStage10Transport(approved_map, scope)
        with mock.patch(
            "quant_data.market.fmp_bulk_daily_prices.http.client.HTTPSConnection"
        ) as https:
            for kwargs in (
                {
                    "path": FMP_STAGE10_PRICE_PATH,
                    "query": {"symbol": "SPY", "from": "1993-01-29"},
                    "headers": {"apikey": KEY},
                    "timeout_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
                    "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
                },
                {
                    "path": FMP_STAGE10_PRICE_PATH,
                    "query": {"symbol": "SPY"},
                    "headers": {"apikey": KEY, "Accept": "application/json"},
                    "timeout_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
                    "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
                },
                {
                    "path": FMP_STAGE10_PRICE_PATH,
                    "query": {"symbol": "SPY"},
                    "headers": {"apikey": KEY},
                    "timeout_seconds": 44,
                    "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
                },
            ):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValidationError):
                        transport.get(**kwargs)
            https.assert_not_called()
        with mock.patch(
            "quant_data.market.fmp_bulk_daily_prices.http.client.HTTPSConnection"
        ) as https:
            with self.assertRaises(ValidationError):
                transport.get(
                    path=FMP_STAGE10_PRICE_PATH,
                    query={"symbol": "IWM"},
                    headers={"apikey": KEY},
                    timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
                    max_bytes=FMP_STAGE10_MAX_PRICE_BYTES,
                )
            https.assert_not_called()


        response = mock.Mock()
        response.status = 302
        response.getheader.side_effect = lambda name: {
            "Content-Length": "2",
            "Content-Type": "application/json",
        }.get(name)
        response.read.return_value = b"[]"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch(
            "quant_data.market.fmp_bulk_daily_prices.http.client.HTTPSConnection",
            return_value=connection,
        ):
            captured = transport.get(
                path=FMP_STAGE10_SP500_CONSTITUENT_PATH,
                query={},
                headers={"apikey": KEY},
                timeout_seconds=FMP_STAGE10_TIMEOUT_SECONDS,
                max_bytes=FMP_STAGE10_MAX_CONSTITUENT_BYTES,
            )
        self.assertEqual(captured.status, 302)
        self.assertEqual(connection.request.call_count, 1)
        target = connection.request.call_args.args[1]
        self.assertEqual(target, FMP_STAGE10_SP500_CONSTITUENT_PATH)
        self.assertNotIn(KEY, target)


if __name__ == "__main__":
    unittest.main()
