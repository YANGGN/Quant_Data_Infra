from __future__ import annotations

import json
import unittest
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.macro.stage11_bea import (
    BEA_MAX_REQUESTS,
    BEA_MAX_RESPONSE_BYTES,
    BEA_MAX_ROWS_PER_SERIES,
    BEA_NIPA_MAX_RESPONSE_BYTES,
    BEA_NIPA_MAX_ROWS_PER_SERIES,
    BEA_NIPA_MAX_TABLE_ROWS,
    BEA_NIPA_PATH,
    BEA_NIPA_T10101,
    BEA_NIPA_T10105,
    BEA_NIPA_TIMEOUT_SECONDS,
    CapturedBeaResponse,
    StdlibBeaTransport,
    assemble_bea_nipa_history,
    capture_bea_nipa,
    parse_bea_nipa_response,
    prepare_bea_nipa_capture,
    prepare_bea_nipa_captures,
)


KEY = "stage11-bea-test-secret"
_FORBIDDEN_RAW_TOKENS = (
    KEY.encode("utf-8"),
    b"api_key",
    b"apikey",
    b"userid",
    b"authorization",
    b"header",
    b"url",
    b"uri",
    b"query",
    b"request",
    b"x-trace",
    b"content-length",
    b"trace-id",
)
_FORBIDDEN_REPR_TOKENS = tuple(
    token for token in _FORBIDDEN_RAW_TOKENS if token != b"request"
)


def _row(
    table_name: str,
    series_code: str,
    period: str,
    value: str,
    *,
    unit: str | None = None,
    unit_mult: str = "0",
) -> dict[str, object]:
    return {
        "TableName": table_name,
        "SeriesCode": series_code,
        "TimePeriod": period,
        "DataValue": value,
        "CL_UNIT": unit or ("Percent change" if series_code == "A191RL" else "Billions of dollars"),
        "UNIT_MULT": unit_mult,
        "LineNumber": "1",
    }


def _body(
    table_name: str,
    series_code: str,
    *,
    production_time: str = "2026-08-12T12:00:00Z",
    value: str = "1.1",
    echo: bool = False,
) -> bytes:
    payload: dict[str, object] = {
        "BEAAPI": {
            "Request": {
                "Parameter": {
                    "UserID": KEY,
                    "nested": {"api_key": KEY},
                    "echo_url": f"https://example.invalid/?UserID={KEY}",
                }
            }
            if echo
            else {},
            "Results": {
                "UTCProductionTime": production_time,
                "Data": [
                    _row(table_name, series_code, "1947Q2", value),
                    _row(table_name, series_code, "1947Q1", "1.0"),
                    _row(table_name, "OTHER", "1947Q1", "99.0"),
                ],
            },
        }
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _hostile_body(table_name: str, series_code: str) -> bytes:
    payload = json.loads(_body(table_name, series_code, echo=True))
    results = payload["BEAAPI"]["Results"]
    results.update(
        {
            "echo_url": f"https://example.invalid/?api_key={KEY}",
            "request_headers": {
                "Authorization": f"Bearer {KEY}",
                "x-api-key": KEY,
            },
            "Authorization": f"Bearer {KEY}",
            "x-api-key": KEY,
            "url_with_key": f"https://example.invalid/?UserID={KEY}",
            "query_string": f"UserID={KEY}",
            "arbitrary_secret": KEY,
            "X-Trace": "trace-id",
            "Content-Length": "123",
            "ordinary": f"api_key={KEY}",
            "plain_metadata": "X-Trace: trace-id",
            "notes": [
                f"https://example.invalid/?api_key={KEY}",
                f"Authorization: Bearer {KEY}",
                KEY,
            ],
        }
    )
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _assert_safe_evidence(testcase: unittest.TestCase, value: object, raw: bytes) -> None:
    material = raw.lower()
    for token in _FORBIDDEN_RAW_TOKENS:
        testcase.assertNotIn(token.lower(), material)
    for token in _FORBIDDEN_REPR_TOKENS:
        testcase.assertNotIn(token.decode("utf-8").lower(), repr(value).lower())


class _RecordingTransport:
    def __init__(self, response: CapturedBeaResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> CapturedBeaResponse:
        self.calls.append(dict(kwargs))
        return self.response


class Stage11BeaCaptureTests(unittest.TestCase):
    def test_public_bounds_match_the_frozen_scope(self) -> None:
        self.assertEqual(BEA_MAX_RESPONSE_BYTES, 8_388_608)
        self.assertEqual(BEA_MAX_REQUESTS, 2)
        self.assertEqual(BEA_MAX_ROWS_PER_SERIES, 1_000)

    def test_two_exact_prepared_requests_and_redacted_capture(self) -> None:
        prepared = prepare_bea_nipa_captures()
        self.assertEqual(
            tuple((item.table_name, item.series_code) for item in prepared),
            ((BEA_NIPA_T10101, "A191RL"), (BEA_NIPA_T10105, "A191RC")),
        )
        request = prepared[0]
        transport = _RecordingTransport(
            CapturedBeaResponse(200, "application/json", _body(BEA_NIPA_T10101, "A191RL", echo=True))
        )
        capture = capture_bea_nipa(request, api_key=KEY, transport=transport)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0],
            {
                "path": BEA_NIPA_PATH,
                "query": {
                    "method": "GetData",
                    "datasetname": "NIPA",
                    "TableName": BEA_NIPA_T10101,
                    "Frequency": "Q",
                    "Year": "ALL",
                    "ResultFormat": "JSON",
                    "UserID": KEY,
                },
                "headers": {"Accept": "application/json"},
                "timeout_seconds": BEA_NIPA_TIMEOUT_SECONDS,
                "max_bytes": BEA_NIPA_MAX_RESPONSE_BYTES,
            },
        )
        self.assertEqual(tuple(row.period for row in capture.rows), ("1947Q1", "1947Q2"))
        self.assertNotIn(KEY.encode("utf-8"), capture.raw_bytes)
        for value in (transport.response, request, capture):
            self.assertNotIn(KEY, repr(value))

    def test_utc_only_change_is_semantically_unchanged_but_data_change_is_not(self) -> None:
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)
        first = parse_bea_nipa_response(
            body=_body(BEA_NIPA_T10101, "A191RL", production_time="2026-08-12T01:00:00Z"),
            prepared=request,
        )
        utc_only = parse_bea_nipa_response(
            body=_body(BEA_NIPA_T10101, "A191RL", production_time="2026-08-12T02:00:00Z"),
            prepared=request,
        )
        changed = parse_bea_nipa_response(
            body=_body(BEA_NIPA_T10101, "A191RL", value="1.2"),
            prepared=request,
        )
        self.assertNotEqual(first.raw_bytes_sha256, utc_only.raw_bytes_sha256)
        self.assertEqual(first.semantic_sha256, utc_only.semantic_sha256)
        self.assertNotEqual(first.semantic_sha256, changed.semantic_sha256)

    def test_hostile_response_echoes_are_dropped_before_raw_evidence_or_errors(self) -> None:
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)
        transport = _RecordingTransport(
            CapturedBeaResponse(200, "application/json", _hostile_body(BEA_NIPA_T10101, "A191RL"))
        )
        capture = capture_bea_nipa(request, api_key=KEY, transport=transport)
        _assert_safe_evidence(self, transport.response, capture.raw_bytes)
        _assert_safe_evidence(self, capture, capture.raw_bytes)

        bad_payload = json.loads(_hostile_body(BEA_NIPA_T10101, "A191RL"))
        bad_payload["BEAAPI"]["Results"]["Data"][0]["DataValue"] = KEY
        with self.assertRaises(ValidationError) as raised:
            capture_bea_nipa(
                request,
                api_key=KEY,
                transport=_RecordingTransport(
                    CapturedBeaResponse(200, "application/json", json.dumps(bad_payload).encode())
                ),
            )
        error_text = str(raised.exception).lower()
        for token in _FORBIDDEN_REPR_TOKENS:
            self.assertNotIn(token.decode("utf-8").lower(), error_text)

    def test_cohort_requires_both_reviewed_table_series_pairs(self) -> None:
        one = parse_bea_nipa_response(
            body=_body(BEA_NIPA_T10101, "A191RL"),
            prepared=prepare_bea_nipa_capture(BEA_NIPA_T10101),
        )
        two = parse_bea_nipa_response(
            body=_body(BEA_NIPA_T10105, "A191RC"),
            prepared=prepare_bea_nipa_capture(BEA_NIPA_T10105),
        )
        cohort = assemble_bea_nipa_history([two, one])
        self.assertEqual(
            tuple((row.table_name, row.series_code) for row in cohort.rows),
            ((BEA_NIPA_T10101, "A191RL"), (BEA_NIPA_T10101, "A191RL"), (BEA_NIPA_T10105, "A191RC"), (BEA_NIPA_T10105, "A191RC")),
        )
        with self.assertRaises(ValidationError):
            assemble_bea_nipa_history([one])

    def test_parser_rejects_malformed_errors_oversize_wrong_table_or_series_and_units(self) -> None:
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)
        malformed = b"\xff"
        error = json.dumps({"BEAAPI": {"Results": {"Error": {"APIErrorCode": "1"}}}}).encode()
        wrong_table = json.loads(_body(BEA_NIPA_T10101, "A191RL"))
        wrong_table["BEAAPI"]["Results"]["Data"][0]["TableName"] = BEA_NIPA_T10105
        wrong_series = json.loads(_body(BEA_NIPA_T10101, "A191RL"))
        wrong_series["BEAAPI"]["Results"]["Data"][0]["SeriesCode"] = "A191RC"
        wrong_series["BEAAPI"]["Results"]["Data"][1]["SeriesCode"] = "A191RC"
        inconsistent_unit = json.loads(_body(BEA_NIPA_T10101, "A191RL"))
        inconsistent_unit["BEAAPI"]["Results"]["Data"][1]["CL_UNIT"] = "different"
        for name, body, error_type in (
            ("malformed", malformed, ValidationError),
            ("error", error, StoreUnavailableError),
            ("wrong_table", json.dumps(wrong_table).encode(), ValidationError),
            ("wrong_series", json.dumps(wrong_series).encode(), ValidationError),
            ("unit", json.dumps(inconsistent_unit).encode(), ValidationError),
        ):
            with self.subTest(name=name):
                with self.assertRaises(error_type):
                    parse_bea_nipa_response(body=body, prepared=request)
        with self.assertRaises(ResourceLimitError):
            parse_bea_nipa_response(
                body=b"x" * (BEA_NIPA_MAX_RESPONSE_BYTES + 1),
                prepared=request,
            )

    def test_per_series_row_bound_accepts_exact_limit_and_rejects_one_more(self) -> None:
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)

        def rows(count: int) -> list[dict[str, object]]:
            return [
                _row(
                    BEA_NIPA_T10101,
                    "A191RL",
                    f"{1800 + index // 4:04d}Q{index % 4 + 1}",
                    str(index),
                )
                for index in range(count)
            ]

        exact_payload = {"BEAAPI": {"Results": {"Data": rows(BEA_NIPA_MAX_ROWS_PER_SERIES)}}}
        exact = parse_bea_nipa_response(
            body=json.dumps(exact_payload, separators=(",", ":")).encode(),
            prepared=request,
        )
        self.assertEqual(len(exact.rows), BEA_NIPA_MAX_ROWS_PER_SERIES)
        too_many_payload = {"BEAAPI": {"Results": {"Data": rows(BEA_NIPA_MAX_ROWS_PER_SERIES + 1)}}}
        with self.assertRaises(ValidationError):
            parse_bea_nipa_response(
                body=json.dumps(too_many_payload, separators=(",", ":")).encode(),
                prepared=request,
            )

    def test_selected_series_bound_is_independent_from_the_provider_table_bound(self) -> None:
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)
        unrelated = [
            _row(
                BEA_NIPA_T10101,
                f"OTHER{index}",
                "1947Q1",
                "99.0",
            )
            for index in range(BEA_NIPA_MAX_ROWS_PER_SERIES)
        ]
        payload = {
            "BEAAPI": {
                "Results": {
                "Data": [
                        *[
                            _row(
                                BEA_NIPA_T10101,
                                "A191RL",
                                f"{1800 + index // 4:04d}Q{index % 4 + 1}",
                                str(index),
                            )
                            for index in range(BEA_NIPA_MAX_ROWS_PER_SERIES)
                        ],
                        *unrelated,
                    ]
                }
            }
        }
        capture = parse_bea_nipa_response(
            body=json.dumps(payload, separators=(",", ":")).encode(),
            prepared=request,
        )
        self.assertEqual(len(capture.rows), BEA_NIPA_MAX_ROWS_PER_SERIES)
        self.assertGreater(BEA_NIPA_MAX_TABLE_ROWS, BEA_NIPA_MAX_ROWS_PER_SERIES)

        over_limit = {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        *[
                            _row(
                                BEA_NIPA_T10101,
                                "A191RL",
                                f"{1800 + index // 4:04d}Q{index % 4 + 1}",
                                str(index),
                            )
                            for index in range(BEA_NIPA_MAX_ROWS_PER_SERIES + 1)
                        ],
                        *unrelated,
                    ]
                }
            }
        }
        with self.assertRaises(ValidationError):
            parse_bea_nipa_response(
                body=json.dumps(over_limit, separators=(",", ":")).encode(),
                prepared=request,
            )

    def test_stdlib_transport_rejects_scope_broadeners_and_does_not_follow_redirects(self) -> None:
        transport = StdlibBeaTransport()
        request = prepare_bea_nipa_capture(BEA_NIPA_T10101)
        with mock.patch("quant_data.macro.stage11_bea.http.client.HTTPSConnection") as https:
            with self.assertRaises(ValidationError):
                transport.get(
                    path=BEA_NIPA_PATH,
                    query={**request.query, "UserID": KEY, "Year": "2025"},
                    headers={"Accept": "application/json"},
                    timeout_seconds=BEA_NIPA_TIMEOUT_SECONDS,
                    max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES,
                )
            https.assert_not_called()

        response = mock.Mock()
        response.status = 302
        response.getheader.side_effect = lambda name: {
            "Content-Length": "2",
            "Content-Type": "application/json",
        }.get(name)
        response.read.return_value = b"{}"
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch(
            "quant_data.macro.stage11_bea.http.client.HTTPSConnection",
            return_value=connection,
        ):
            captured = transport.get(
                path=BEA_NIPA_PATH,
                query={**request.query, "UserID": KEY},
                headers={"Accept": "application/json"},
                timeout_seconds=BEA_NIPA_TIMEOUT_SECONDS,
                max_bytes=BEA_NIPA_MAX_RESPONSE_BYTES,
            )
        self.assertEqual(captured.status, 302)
        self.assertEqual(connection.request.call_count, 1)
        self.assertNotIn(KEY, connection.request.call_args.args[1].split("?")[0])


if __name__ == "__main__":
    unittest.main()
