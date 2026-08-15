"""Offline tests for the frozen Stage 10 historical-window capture contract."""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from decimal import Decimal

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.market.fmp_bulk_daily_prices import (
    CapturedFmpStage10Response,
    FMP_STAGE10_MAX_PRICE_BYTES,
    FMP_STAGE10_MAX_PRICE_ROWS,
    FMP_STAGE10_TIMEOUT_SECONDS,
)
from quant_data.market.stage10_history_windows import (
    STAGE10_HISTORY_WINDOWS,
    Stage10HistoryWindow,
    capture_fmp_stage10_window,
    parse_fmp_stage10_window_response,
    prepare_fmp_stage10_window_capture,
)


def _row(symbol: str, trade_date: str, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "symbol": symbol,
        "date": trade_date,
        "open": 10,
        "high": 12,
        "low": 9,
        "close": 11,
        "volume": 1_000,
        "change": 1,
        "changePercent": 10,
        "vwap": 10,
    }
    result.update(overrides)
    return result


def _body(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(rows, separators=(",", ":")).encode("utf-8")


class _Transport:
    def __init__(self, response: CapturedFmpStage10Response) -> None:
        self.response = response
        self.calls = 0
        self.kwargs: dict[str, object] | None = None

    def get(self, **kwargs: object) -> CapturedFmpStage10Response:
        self.calls += 1
        self.kwargs = kwargs
        return self.response


class Stage10HistoryWindowsTests(unittest.TestCase):
    def test_frozen_windows_are_exact_contiguous_non_overlapping_plan(self) -> None:
        self.assertEqual(len(STAGE10_HISTORY_WINDOWS), 8)
        self.assertEqual(STAGE10_HISTORY_WINDOWS[0].start_date, "1990-01-01")
        self.assertEqual(STAGE10_HISTORY_WINDOWS[-1].end_date, "2026-08-12")
        for previous, current in zip(
            STAGE10_HISTORY_WINDOWS, STAGE10_HISTORY_WINDOWS[1:]
        ):
            previous_end = previous.end_date
            current_start = current.start_date
            self.assertLess(previous_end, current_start)
            self.assertEqual(
                date.fromisoformat(current_start),
                date.fromisoformat(previous_end) + timedelta(days=1),
            )

    def test_unknown_window_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Stage10HistoryWindow("wrong", "1990-01-01", "1994-12-31")

    def test_capture_uses_exact_query_and_one_transport_call(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        transport = _Transport(
            CapturedFmpStage10Response(
                200, "application/json; charset=utf-8", _body([_row("AAPL", "1990-01-02")])
            )
        )
        capture = capture_fmp_stage10_window(prepared, "offline-key", transport)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(
            transport.kwargs,
            {
                "path": "/stable/historical-price-eod/full",
                "query": {"symbol": "AAPL", "from": "1990-01-01", "to": "1994-12-31"},
                "headers": {"apikey": "offline-key"},
                "timeout_seconds": FMP_STAGE10_TIMEOUT_SECONDS,
                "max_bytes": FMP_STAGE10_MAX_PRICE_BYTES,
            },
        )
        self.assertEqual(capture.disposition, "complete")
        self.assertEqual(capture.row_count, 1)
        self.assertEqual(capture.rows[0].trade_date, "1990-01-02")
        self.assertIsInstance(capture.rows[0].close_value, Decimal)

    def test_empty_list_is_complete_empty_evidence(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        transport = _Transport(CapturedFmpStage10Response(200, "application/json", b"[]"))
        capture = capture_fmp_stage10_window(prepared, "offline-key", transport)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(capture.disposition, "complete_empty")
        self.assertEqual(capture.rows, ())

    def test_parser_rejects_out_of_window_rows_without_filtering(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        with self.assertRaisesRegex(ValidationError, "outside the requested interval"):
            parse_fmp_stage10_window_response(
                prepared, _body([_row("AAPL", "1995-01-01")])
            )

    def test_parser_rejects_duplicate_dates_and_symbol_mismatch(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        with self.assertRaisesRegex(ValidationError, "duplicate"):
            parse_fmp_stage10_window_response(
                prepared,
                _body([_row("AAPL", "1990-01-02"), _row("AAPL", "1990-01-02")]),
            )
        with self.assertRaisesRegex(ValidationError, "unexpected symbol"):
            parse_fmp_stage10_window_response(
                prepared, _body([_row("MSFT", "1990-01-02")])
            )

    def test_parser_accepts_decimal_and_rejects_boolean_and_bad_ohlcv(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        rows = parse_fmp_stage10_window_response(
            prepared, _body([_row("AAPL", "1990-01-02", open=10.5)])
        )
        self.assertEqual(rows[0].open_value, Decimal("10.5"))
        with self.assertRaisesRegex(ValidationError, "JSON number"):
            parse_fmp_stage10_window_response(
                prepared, _body([_row("AAPL", "1990-01-02", open=True)])
            )
        with self.assertRaisesRegex(ValidationError, "inconsistent OHLC"):
            parse_fmp_stage10_window_response(
                prepared, _body([_row("AAPL", "1990-01-02", high=8)])
            )

    def test_parser_sorts_but_retains_provider_source_row(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        rows = parse_fmp_stage10_window_response(
            prepared,
            _body([_row("AAPL", "1990-01-03"), _row("AAPL", "1990-01-02")]),
        )
        self.assertEqual([row.trade_date for row in rows], ["1990-01-02", "1990-01-03"])
        self.assertEqual([row.source_row for row in rows], [2, 1])

    def test_parser_enforces_resource_bounds(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        with self.assertRaises(ResourceLimitError):
            parse_fmp_stage10_window_response(
                prepared, b" " * (FMP_STAGE10_MAX_PRICE_BYTES + 1)
            )
        with self.assertRaises(ResourceLimitError):
            parse_fmp_stage10_window_response(
                prepared,
                _body([_row("AAPL", "1990-01-02")] * (FMP_STAGE10_MAX_PRICE_ROWS + 1)),
            )

    def test_non_200_is_not_parsed_or_retried(self) -> None:
        prepared = prepare_fmp_stage10_window_capture(
            "AAPL", STAGE10_HISTORY_WINDOWS[0]
        )
        transport = _Transport(CapturedFmpStage10Response(402, "application/json", b"{}"))
        with self.assertRaises(StoreUnavailableError):
            capture_fmp_stage10_window(prepared, "offline-key", transport)
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
