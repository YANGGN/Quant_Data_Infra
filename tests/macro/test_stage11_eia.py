from __future__ import annotations

import json
import unittest
from unittest import mock

from quant_data.errors import ResourceLimitError, StoreUnavailableError, ValidationError
from quant_data.macro.stage11_eia import (
    EIA_MAX_RESPONSE_BYTES,
    EIA_RETAIL_MAX_RESPONSE_BYTES,
    EIA_RETAIL_MAX_PAGES,
    EIA_RETAIL_PAGE_LENGTH,
    EIA_RETAIL_PATH,
    EIA_TIMEOUT_SECONDS,
    EIA_WEEKLY_MAX_RESPONSE_BYTES,
    EIA_WEEKLY_MAX_ROWS,
    EIA_WEEKLY_PATH,
    CapturedEiaResponse,
    StdlibEiaTransport,
    assemble_eia_retail_capture,
    capture_eia_retail_page,
    capture_eia_weekly,
    parse_eia_retail_page_response,
    parse_eia_weekly_response,
    prepare_eia_retail_page,
    prepare_eia_weekly_capture,
)


KEY = "stage11-eia-test-secret"
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


def _retail_row(period: str, *, sales: str = "100.0") -> dict[str, object]:
    return {
        "period": period,
        "stateid": "US",
        "sectorid": "ALL",
        "sales": sales,
        "revenue": "200.0",
        "price": "2.0",
        "customers": "3.0",
        "sales-units": "million kilowatthours",
        "revenue-units": "million dollars",
        "price-units": "cents per kilowatthour",
        "customers-units": "thousand customers",
        "stateDescription": "U.S.",
        "sectorName": "all sectors",
    }


def _retail_rows(count: int, *, start_offset: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(start_offset, start_offset + count):
        year = 1800 + index // 12
        month = index % 12 + 1
        rows.append(_retail_row(f"{year:04d}-{month:02d}", sales=str(100 + index)))
    return rows


def _retail_body(
    rows: list[dict[str, object]],
    *,
    total: int | str,
    echo: bool = False,
) -> bytes:
    payload: dict[str, object] = {
        "warnings": [],
        "response": {
            "total": total,
            "dateFormat": "YYYY-MM",
            "frequency": "monthly",
            "data": rows,
        },
    }
    if echo:
        payload["request"] = {
            "api_key": KEY,
            "nested": {"UserID": KEY},
            "echo_url": f"https://example.invalid/?api_key={KEY}",
        }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _weekly_body(
    rows: list[dict[str, object]],
    *,
    total: int | str | None = None,
    echo: bool = False,
) -> bytes:
    payload: dict[str, object] = {
        "response": {
            "total": total if total is not None else len(rows),
            "dateFormat": "YYYY-MM-DD",
            "frequency": "weekly",
            "data": rows,
        }
    }
    if echo:
        payload["request"] = {
            "api_key": KEY,
            "nested": {"UserID": KEY},
            "echo_url": f"https://example.invalid/?api_key={KEY}",
        }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _weekly_row(period: str, value: str) -> dict[str, object]:
    return {
        "period": period,
        "value": value,
        "series": "PET.WCESTUS1.W",
        "units": "Dollars per Gallon",
    }


def _weekly_path_bound_row(
    period: str,
    value: str,
    *,
    description: str = "West Coast Conventional Retail Gasoline Prices",
) -> dict[str, object]:
    """A documented ``/v2/seriesid/<id>`` provider-shaped row.

    EIA binds the identity in the URL path and returns a human-readable
    ``series-description`` rather than a ``series`` field in each row.
    """

    return {
        "period": period,
        "series-description": description,
        "value": value,
        "units": "Dollars per Gallon",
    }


def _weekly_provider_row(period: str, value: object) -> dict[str, object]:
    """The exact closed row shape observed from the reviewed live endpoint."""

    return {
        "area-name": "United States",
        "duoarea": "NUS",
        "period": period,
        "process": "SAE",
        "process-name": "Stocks",
        "product": "EPC0",
        "product-name": "Crude Oil",
        "series": "WCESTUS1",
        "series-description": "Weekly ending stocks",
        "units": "Thousand Barrels",
        "value": value,
    }


def _hostile_response_body(body: bytes) -> bytes:
    payload = json.loads(body)
    response = payload["response"]
    response.update(
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
    def __init__(self, response: CapturedEiaResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> CapturedEiaResponse:
        self.calls.append(dict(kwargs))
        return self.response


class Stage11EiaCaptureTests(unittest.TestCase):
    def test_retail_capture_is_exact_order_invariant_and_redacts_echoes(self) -> None:
        request = prepare_eia_retail_page()
        newest_first = _retail_body(
            [_retail_row("2020-02", sales="101"), _retail_row("2020-01", sales="100")],
            total="2",
            echo=True,
        )
        oldest_first = _retail_body(
            [_retail_row("2020-01", sales="100"), _retail_row("2020-02", sales="101")],
            total=2,
        )
        transport = _RecordingTransport(CapturedEiaResponse(200, "application/json", newest_first))
        first = capture_eia_retail_page(request, api_key=KEY, transport=transport)
        second = parse_eia_retail_page_response(body=oldest_first, prepared=request)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0],
            {
                "path": EIA_RETAIL_PATH,
                "query": {**request.query, "api_key": KEY},
                "headers": {"Accept": "application/json"},
                "timeout_seconds": EIA_TIMEOUT_SECONDS,
                "max_bytes": EIA_RETAIL_MAX_RESPONSE_BYTES,
            },
        )
        self.assertEqual(first.semantic_sha256, second.semantic_sha256)
        self.assertNotEqual(first.raw_bytes_sha256, second.raw_bytes_sha256)
        self.assertEqual(
            tuple((row.period, row.metric) for row in first.rows[:4]),
            (("2020-01", "customers"), ("2020-01", "price"), ("2020-01", "revenue"), ("2020-01", "sales")),
        )
        self.assertNotIn(KEY.encode(), first.raw_bytes)
        for value in (transport.response, request, first):
            self.assertNotIn(KEY, repr(value))

    def test_retail_null_metric_is_retained_as_missing_not_zero(self) -> None:
        request = prepare_eia_retail_page()
        row = _retail_row("1990-01")
        row["customers"] = None
        capture = parse_eia_retail_page_response(
            body=_retail_body([row], total=1),
            prepared=request,
        )
        self.assertEqual(capture.raw_row_count, 1)
        self.assertEqual(
            tuple((item.metric, str(item.value)) for item in capture.rows),
            (("price", "2.0"), ("revenue", "200.0"), ("sales", "100.0")),
        )
        self.assertFalse(any(item.metric == "customers" for item in capture.rows))
        cohort = assemble_eia_retail_capture((capture,))
        self.assertEqual(len(cohort.rows), 3)
        self.assertEqual(cohort.request_scope["pagination_total"], 1)
        retained = json.loads(capture.raw_bytes)
        self.assertIsNone(retained["response"]["data"][0]["customers"])

        for metric in ("sales", "revenue", "price"):
            with self.subTest(metric=metric):
                invalid = _retail_row("1990-01")
                invalid[metric] = None
                with self.assertRaises(ValidationError):
                    parse_eia_retail_page_response(
                        body=_retail_body([invalid], total=1),
                        prepared=request,
                    )

    def test_hostile_eia_echoes_are_dropped_before_evidence_or_errors(self) -> None:
        retail_request = prepare_eia_retail_page()
        retail_transport = _RecordingTransport(
            CapturedEiaResponse(
                200,
                "application/json",
                _hostile_response_body(
                    _retail_body([_retail_row("2020-01")], total=1, echo=True)
                ),
            )
        )
        retail = capture_eia_retail_page(
            retail_request,
            api_key=KEY,
            transport=retail_transport,
        )
        _assert_safe_evidence(self, retail_transport.response, retail.raw_bytes)
        _assert_safe_evidence(self, retail, retail.raw_bytes)

        weekly_request = prepare_eia_weekly_capture()
        weekly_transport = _RecordingTransport(
            CapturedEiaResponse(
                200,
                "application/json",
                _hostile_response_body(
                    _weekly_body([_weekly_row("2020-01-03", "2.4")], echo=True)
                ),
            )
        )
        weekly = capture_eia_weekly(
            weekly_request,
            api_key=KEY,
            transport=weekly_transport,
        )
        _assert_safe_evidence(self, weekly_transport.response, weekly.raw_bytes)
        _assert_safe_evidence(self, weekly, weekly.raw_bytes)

        bad_payload = json.loads(
            _hostile_response_body(_retail_body([_retail_row("2020-01")], total=1))
        )
        bad_payload["response"]["data"][0]["sales"] = KEY
        with self.assertRaises(ValidationError) as raised:
            capture_eia_retail_page(
                retail_request,
                api_key=KEY,
                transport=_RecordingTransport(
                    CapturedEiaResponse(200, "application/json", json.dumps(bad_payload).encode())
                ),
            )
        error_text = str(raised.exception).lower()
        for token in _FORBIDDEN_REPR_TOKENS:
            self.assertNotIn(token.decode("utf-8").lower(), error_text)

    def test_retail_assembler_requires_gap_free_same_total_pages_and_never_authorizes_tombstones(self) -> None:
        page_after_gap = parse_eia_retail_page_response(
            body=_retail_body(_retail_rows(1, start_offset=EIA_RETAIL_PAGE_LENGTH), total=EIA_RETAIL_PAGE_LENGTH + 1),
            prepared=prepare_eia_retail_page(EIA_RETAIL_PAGE_LENGTH),
        )
        with self.assertRaises(ValidationError):
            assemble_eia_retail_capture([page_after_gap])

        first_page = parse_eia_retail_page_response(
            body=_retail_body(_retail_rows(EIA_RETAIL_PAGE_LENGTH), total=EIA_RETAIL_PAGE_LENGTH + 1),
            prepared=prepare_eia_retail_page(0),
        )
        different_total = parse_eia_retail_page_response(
            body=_retail_body(_retail_rows(2, start_offset=EIA_RETAIL_PAGE_LENGTH), total=EIA_RETAIL_PAGE_LENGTH + 2),
            prepared=prepare_eia_retail_page(EIA_RETAIL_PAGE_LENGTH),
        )
        with self.assertRaises(ValidationError):
            assemble_eia_retail_capture([first_page, different_total])

        final_page = parse_eia_retail_page_response(
            body=_retail_body(_retail_rows(1, start_offset=EIA_RETAIL_PAGE_LENGTH), total=EIA_RETAIL_PAGE_LENGTH + 1),
            prepared=prepare_eia_retail_page(EIA_RETAIL_PAGE_LENGTH),
        )
        complete = assemble_eia_retail_capture([final_page, first_page])
        self.assertEqual(complete.completeness, "complete")
        self.assertFalse(complete.tombstone_authoritative)
        self.assertEqual(complete.request_scope["pagination_total"], EIA_RETAIL_PAGE_LENGTH + 1)

    def test_retail_rejects_duplicate_periods_malformed_errors_and_oversize(self) -> None:
        request = prepare_eia_retail_page()
        duplicate = _retail_body([_retail_row("2020-01"), _retail_row("2020-01")], total=2)
        wrong_scope = _retail_body([_retail_row("2020-01")], total=1)
        wrong_scope_value = json.loads(wrong_scope)
        wrong_scope_value["response"]["data"][0]["stateid"] = "CA"
        for name, body, error_type in (
            ("duplicate", duplicate, ValidationError),
            ("wrong_scope", json.dumps(wrong_scope_value).encode(), ValidationError),
            ("malformed", b"\xff", ValidationError),
            ("error", b'{"error":{"message":"no"}}', StoreUnavailableError),
        ):
            with self.subTest(name=name):
                with self.assertRaises(error_type):
                    parse_eia_retail_page_response(body=body, prepared=request)
        with self.assertRaises(ResourceLimitError):
            parse_eia_retail_page_response(
                body=b"x" * (EIA_RETAIL_MAX_RESPONSE_BYTES + 1),
                prepared=request,
            )

    def test_scope_page_and_weekly_row_bounds_match_the_frozen_limits(self) -> None:
        self.assertEqual(EIA_MAX_RESPONSE_BYTES, 16_777_216)
        self.assertEqual(EIA_RETAIL_MAX_PAGES, 8)
        self.assertEqual(EIA_RETAIL_PAGE_LENGTH, 5_000)
        self.assertEqual(EIA_WEEKLY_MAX_ROWS, 5_000)
        last_allowed = prepare_eia_retail_page(
            (EIA_RETAIL_MAX_PAGES - 1) * EIA_RETAIL_PAGE_LENGTH
        )
        self.assertEqual(last_allowed.page_number, EIA_RETAIL_MAX_PAGES)
        with self.assertRaises(ValidationError):
            prepare_eia_retail_page(EIA_RETAIL_MAX_PAGES * EIA_RETAIL_PAGE_LENGTH)
        with self.assertRaises(ValidationError):
            parse_eia_weekly_response(
                body=_weekly_body([], total=EIA_WEEKLY_MAX_ROWS + 1),
                prepared=prepare_eia_weekly_capture(),
            )

    def test_weekly_capture_has_per_row_content_identity_and_order_invariance(self) -> None:
        request = prepare_eia_weekly_capture()
        body = _weekly_body(
            [_weekly_row("2020-01-10", "2.50"), _weekly_row("2020-01-03", "2.4")],
            echo=True,
        )
        ordered = _weekly_body(
            [_weekly_row("2020-01-03", "2.4"), _weekly_row("2020-01-10", "2.50")]
        )
        transport = _RecordingTransport(CapturedEiaResponse(200, "application/json", body))
        first = capture_eia_weekly(request, api_key=KEY, transport=transport)
        second = parse_eia_weekly_response(body=ordered, prepared=request)
        self.assertEqual(first.semantic_sha256, second.semantic_sha256)
        self.assertNotEqual(first.raw_bytes_sha256, second.raw_bytes_sha256)
        self.assertEqual(first.earliest_period, "2020-01-03")
        self.assertEqual(first.latest_period, "2020-01-10")
        self.assertEqual(len({row.content_sha256 for row in first.rows}), 2)
        self.assertNotIn(KEY.encode(), first.raw_bytes)
        self.assertEqual(
            transport.calls[0],
            {
                "path": EIA_WEEKLY_PATH,
                "query": {"api_key": KEY},
                "headers": {"Accept": "application/json"},
                "timeout_seconds": EIA_TIMEOUT_SECONDS,
                "max_bytes": EIA_WEEKLY_MAX_RESPONSE_BYTES,
            },
        )

    def test_weekly_accepts_documented_path_bound_rows_and_uses_prepared_identity(self) -> None:
        request = prepare_eia_weekly_capture()
        provider_shaped = _weekly_body(
            [
                _weekly_path_bound_row("2020-01-10", "2.50"),
                _weekly_path_bound_row("2020-01-03", "2.4"),
            ],
            echo=True,
        )
        display_metadata_changed = _weekly_body(
            [
                _weekly_path_bound_row(
                    "2020-01-03",
                    "2.4",
                    description="Human-readable display metadata only",
                ),
                _weekly_path_bound_row(
                    "2020-01-10",
                    "2.50",
                    description="Human-readable display metadata only",
                ),
            ]
        )
        transport = _RecordingTransport(
            CapturedEiaResponse(200, "application/json", provider_shaped)
        )

        captured = capture_eia_weekly(request, api_key=KEY, transport=transport)
        changed = parse_eia_weekly_response(
            body=display_metadata_changed,
            prepared=request,
        )

        self.assertEqual(captured.semantic_sha256, changed.semantic_sha256)
        self.assertNotEqual(captured.raw_bytes_sha256, changed.raw_bytes_sha256)
        self.assertEqual(
            tuple(row.semantic_mapping()["series_id"] for row in captured.rows),
            ("PET.WCESTUS1.W", "PET.WCESTUS1.W"),
        )
        _assert_safe_evidence(self, captured, captured.raw_bytes)
        self.assertEqual(
            transport.calls,
            [
                {
                    "path": EIA_WEEKLY_PATH,
                    "query": {"api_key": KEY},
                    "headers": {"Accept": "application/json"},
                    "timeout_seconds": EIA_TIMEOUT_SECONDS,
                    "max_bytes": EIA_WEEKLY_MAX_RESPONSE_BYTES,
                }
            ],
        )

    def test_weekly_accepts_exact_live_provider_row_shape(self) -> None:
        request = prepare_eia_weekly_capture()
        captured = parse_eia_weekly_response(
            body=_weekly_body(
                [
                    _weekly_provider_row("2020-01-10", 250.0),
                    _weekly_provider_row("2020-01-03", 240.0),
                ]
            ),
            prepared=request,
        )

        self.assertEqual(
            tuple(row.period for row in captured.rows),
            ("2020-01-03", "2020-01-10"),
        )
        self.assertEqual(
            tuple(row.canonical_series_id for row in captured.rows),
            ("macro.eia.weekly.petroleum_stock",) * 2,
        )

    def test_weekly_live_provider_shape_rejects_wrong_series_extra_and_invalid_metadata(self) -> None:
        request = prepare_eia_weekly_capture()
        wrong_series = _weekly_provider_row("2020-01-03", 240.0)
        wrong_series["series"] = "PET.OTHER.W"
        path_series = _weekly_provider_row("2020-01-03", 240.0)
        path_series["series"] = "PET.WCESTUS1.W"
        extra_field = _weekly_provider_row("2020-01-03", 240.0)
        extra_field["unreviewed"] = "value"
        string_value = _weekly_provider_row("2020-01-03", "240.0")
        cases = [
            ("wrong_series", wrong_series),
            ("path_series", path_series),
            ("extra_field", extra_field),
            ("string_value", string_value),
        ]
        hostile_values = (
            ("url", f"https://example.invalid/?api_key={KEY}"),
            ("header", f"Authorization: Bearer {KEY}"),
            ("credential", KEY),
        )
        for field_name in (
            "area-name",
            "duoarea",
            "process",
            "process-name",
            "product",
            "product-name",
            "series-description",
        ):
            invalid = _weekly_provider_row("2020-01-03", 240.0)
            invalid[field_name] = None
            cases.append((f"invalid_{field_name}", invalid))
            for hostile_name, hostile_value in hostile_values:
                hostile = _weekly_provider_row("2020-01-03", 240.0)
                hostile[field_name] = hostile_value
                cases.append((f"{hostile_name}_{field_name}", hostile))

        for name, row in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as raised:
                    parse_eia_weekly_response(
                        body=_weekly_body([row]),
                        prepared=request,
                        credential_value=KEY,
                    )
                self.assertNotIn(KEY, str(raised.exception))

    def test_weekly_path_bound_shape_rejects_injected_identity_unknown_and_hostile_metadata(self) -> None:
        request = prepare_eia_weekly_capture()

        injected_series = _weekly_path_bound_row("2020-01-03", "2.4")
        injected_series["series"] = "PET.WCESTUS1.W"
        unexpected_series = _weekly_row("2020-01-03", "2.4")
        unexpected_series["series"] = "PET.OTHER.W"
        unknown_key = _weekly_path_bound_row("2020-01-03", "2.4")
        unknown_key["unreviewed"] = "value"

        for name, row in (
            ("injected_series", injected_series),
            ("unexpected_legacy_series", unexpected_series),
            ("unknown_key", unknown_key),
            (
                "url_description",
                _weekly_path_bound_row(
                    "2020-01-03",
                    "2.4",
                    description=f"https://example.invalid/?api_key={KEY}",
                ),
            ),
            (
                "header_description",
                _weekly_path_bound_row(
                    "2020-01-03",
                    "2.4",
                    description=f"Authorization: Bearer {KEY}",
                ),
            ),
            (
                "credential_description",
                _weekly_path_bound_row("2020-01-03", "2.4", description=KEY),
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as raised:
                    parse_eia_weekly_response(
                        body=_weekly_body([row]),
                        prepared=request,
                        credential_value=KEY,
                    )
                self.assertNotIn(KEY, str(raised.exception))

    def test_weekly_requires_exact_prepared_path_scope_before_transport(self) -> None:
        request = prepare_eia_weekly_capture()
        object.__setattr__(request, "endpoint_path", "/v2/seriesid/PET.OTHER.W")
        transport = _RecordingTransport(
            CapturedEiaResponse(
                200,
                "application/json",
                _weekly_body([_weekly_path_bound_row("2020-01-03", "2.4")]),
            )
        )

        with self.assertRaises(ValidationError):
            capture_eia_weekly(request, api_key=KEY, transport=transport)
        self.assertEqual(transport.calls, [])

    def test_weekly_rejects_duplicate_wrong_series_total_mismatch_nonfinite_null_and_oversize(self) -> None:
        request = prepare_eia_weekly_capture()
        duplicate = _weekly_body(
            [
                _weekly_path_bound_row("2020-01-03", "2.4"),
                _weekly_path_bound_row("2020-01-03", "2.5"),
            ]
        )
        wrong_series = _weekly_body([_weekly_row("2020-01-03", "2.4")])
        wrong_series_value = json.loads(wrong_series)
        wrong_series_value["response"]["data"][0]["series"] = "PET.OTHER.W"
        total_mismatch = _weekly_body([_weekly_path_bound_row("2020-01-03", "2.4")], total=2)
        nonfinite = _weekly_path_bound_row("2020-01-03", "NaN")
        null_description = _weekly_path_bound_row("2020-01-03", "2.4")
        null_description["series-description"] = None
        null_value = _weekly_path_bound_row("2020-01-03", "2.4")
        null_value["value"] = None
        null_unit = _weekly_path_bound_row("2020-01-03", "2.4")
        null_unit["units"] = None
        for name, body in (
            ("duplicate", duplicate),
            ("wrong_series", json.dumps(wrong_series_value).encode()),
            ("total", total_mismatch),
            ("nonfinite", _weekly_body([nonfinite])),
            ("null_description", _weekly_body([null_description])),
            ("null_value", _weekly_body([null_value])),
            ("null_unit", _weekly_body([null_unit])),
            ("malformed", b"\xff"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    parse_eia_weekly_response(body=body, prepared=request)
        with self.assertRaises(ResourceLimitError):
            parse_eia_weekly_response(
                body=b"x" * (EIA_WEEKLY_MAX_RESPONSE_BYTES + 1),
                prepared=request,
            )

    def test_stdlib_transport_rejects_scope_broadeners_and_does_not_follow_redirects(self) -> None:
        transport = StdlibEiaTransport()
        request = prepare_eia_retail_page()
        with mock.patch("quant_data.macro.stage11_eia.http.client.HTTPSConnection") as https:
            with self.assertRaises(ValidationError):
                transport.get(
                    path=EIA_RETAIL_PATH,
                    query={**request.query, "api_key": KEY, "frequency": "annual"},
                    headers={"Accept": "application/json"},
                    timeout_seconds=EIA_TIMEOUT_SECONDS,
                    max_bytes=EIA_RETAIL_MAX_RESPONSE_BYTES,
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
            "quant_data.macro.stage11_eia.http.client.HTTPSConnection",
            return_value=connection,
        ):
            captured = transport.get(
                path=EIA_RETAIL_PATH,
                query={**request.query, "api_key": KEY},
                headers={"Accept": "application/json"},
                timeout_seconds=EIA_TIMEOUT_SECONDS,
                max_bytes=EIA_RETAIL_MAX_RESPONSE_BYTES,
            )
        self.assertEqual(captured.status, 302)
        self.assertEqual(connection.request.call_count, 1)
        self.assertTrue(connection.request.call_args.args[1].startswith(EIA_RETAIL_PATH + "?"))


if __name__ == "__main__":
    unittest.main()
