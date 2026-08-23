"""Focused offline route tests for fixed official-condition history sources."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.operations import official_conditions_history as operation
from quant_data.registry import load_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "config" / "system_registry.json"
SECRET = "fixture-eia-key-not-retained"
FIXED_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)

_TREASURY_BODY = (
    b'{"data":[{"record_date":"2026-08-20","account_type":"Treasury General '
    b'Account (TGA) Closing Balance","open_today_bal":"700000"}],'
    b'"meta":{"total-pages":"1","total-count":"1"}}'
)
_EIA_BODY = (
    b'{"response":{"frequency":"weekly","total":"1","api_key":"'
    + SECRET.encode("utf-8")
    + b'","data":[{"period":"2026-08-21","value":"3300"}]}}'
)
_NBER_BODY = b'[{"peak":"2020-02","trough":"2020-04"}]'
_BLS_BODY = (
    b'{"status":"REQUEST_SUCCEEDED","message":[],"Results":{"series":['
    b'{"seriesID":"WPSFD4","data":['
    b'{"year":"2026","period":"M07","value":"150.1"}]},'
    b'{"seriesID":"CES0500000003","data":['
    b'{"year":"2026","period":"M07","value":"38.10"}]},'
    b'{"seriesID":"PRS85006092","data":['
    b'{"year":"2026","period":"Q02","value":"2.7"}]}]}}'
)
_CHICAGO_BODY = (
    b"Friday_of_Week,NFCI,ANFCI,Risk,Credit,Leverage,"
    b"Nonfinancial_Leverage\n"
    b"08/21/2026,-0.28,-0.14,0.02,-0.03,0.01,0\n"
)


class _Transport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.CsvResponse:
        self.calls.append(dict(kwargs))
        url = str(kwargs["url"])
        if url.startswith(operation.TREASURY_TGA_URL):
            body = _TREASURY_BODY
        elif url.startswith(operation.EIA_NATURAL_GAS_STORAGE_URL):
            body = _EIA_BODY
        elif url == operation.NBER_BUSINESS_CYCLE_DATES_URL:
            body = _NBER_BODY
        elif url == operation.BLS_PRICE_WAGE_PRODUCTIVITY_URL:
            request_payload = loads_strict(kwargs["body"])
            payload = loads_strict(_BLS_BODY)
            by_code = {
                item["seriesID"]: item
                for item in payload["Results"]["series"]
            }
            payload["Results"]["series"] = [
                by_code[code]
                for code in request_payload["seriesid"]
            ]
            for item in payload["Results"]["series"]:
                item["data"][0]["year"] = request_payload["startyear"]
            body = dumps_strict(payload).encode("utf-8")
        elif url == operation.CHICAGO_NFCI_URL:
            body = _CHICAGO_BODY
            return operation.CsvResponse(200, "text/csv", body, False)
        else:
            raise AssertionError("unexpected fixed source URL")
        return operation.CsvResponse(200, "application/json", body, False)


class _Publisher:
    def __init__(self) -> None:
        self.captures: list[object] = []

    def publish(self, capture: object) -> operation.OfficialConditionsPublishReport:
        self.captures.append(capture)
        periods = [item.source_period for item in capture.observations]
        return operation.OfficialConditionsPublishReport(
            capture.source_key,
            "published",
            capture.semantic_identity,
            None,
            None,
            None,
            0,
            0,
            min(periods),
            max(periods),
        )


class OfficialConditionsHistoryOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.project = Path(self.temporary.name) / "project"
        self.target = self.project / "data" / "macro.sqlite"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"fixture macro store")
        self.transport = _Transport()
        self.publisher = _Publisher()
        self.registry = load_registry(
            REGISTRY_PATH, project_root=PROJECT_ROOT, environment={}
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runner(self) -> operation.OfficialConditionsHistoryRunner:
        return operation.OfficialConditionsHistoryRunner(
            project_root=self.project,
            macro_store=self.target,
            registry=self.registry,
            transport=self.transport,
            publisher_factory=lambda _source: self.publisher,
            utcnow=lambda: FIXED_NOW,
        )

    def test_three_fixed_routes_issue_one_request_each_and_redact_eia(self) -> None:
        runner = self._runner()
        runner.run_treasury_tga(start_date="2026-08-20", end_date="2026-08-20")
        runner.run_nber_us_recession()
        with patch.object(operation, "read_project_credential", return_value=SECRET):
            runner.run_eia_natural_gas_storage()

        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(
            [capture.source_key for capture in self.publisher.captures],
            ["treasury_tga", "nber_recession", "eia_gas"],
        )
        treasury_query = parse_qs(urlsplit(str(self.transport.calls[0]["url"])).query)
        self.assertEqual(treasury_query["page[size]"], ["10000"])
        self.assertIn("Treasury General Account", treasury_query["filter"][0])
        self.assertEqual(urlsplit(str(self.transport.calls[1]["url"])).query, "")
        self.assertNotIn(
            SECRET.encode("utf-8"), self.publisher.captures[2].parts[0].body
        )

    def test_chicago_route_uses_one_existing_csv_request_for_components(self) -> None:
        runner = self._runner()
        runner.run_chicago(
            start_date="2026-08-21", end_date="2026-08-21"
        )

        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(
            self.transport.calls[0]["url"], operation.CHICAGO_NFCI_URL
        )
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "chicago")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["NFCI", "ANFCI", "Risk", "Credit", "Leverage"],
        )

    def test_bls_route_uses_one_credential_free_post_for_ten_years(self) -> None:
        runner = self._runner()
        runner.run_bls_price_wage_productivity()

        self.assertEqual(len(self.transport.calls), 1)
        call = self.transport.calls[0]
        self.assertEqual(call["url"], operation.BLS_PRICE_WAGE_PRODUCTIVITY_URL)
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(
            loads_strict(call["body"]),
            {
                "seriesid": [
                    "WPSFD4",
                    "CES0500000003",
                    "PRS85006092",
                ],
                "startyear": "2017",
                "endyear": "2026",
            },
        )
        self.assertEqual(
            self.publisher.captures[0].source_key,
            "bls_price_wage_productivity",
        )

    def test_bls_route_accepts_one_fixed_historical_subset(self) -> None:
        runner = self._runner()
        runner.run_bls_price_wage_productivity(
            start_year=1947,
            end_year=1956,
            series_codes=("PRS85006092",),
        )

        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(
            loads_strict(self.transport.calls[0]["body"]),
            {
                "seriesid": ["PRS85006092"],
                "startyear": "1947",
                "endyear": "1956",
            },
        )
        self.assertEqual(
            [
                item.provider_code
                for item in self.publisher.captures[0].observations
            ],
            ["PRS85006092"],
        )

    def test_cli_dispatches_bls_historical_subset(self) -> None:
        report = operation.OfficialConditionsPublishReport(
            "bls_price_wage_productivity",
            "published",
            "fixture",
            None,
            None,
            None,
            0,
            0,
            "1947-Q2",
            "1956-Q4",
        )
        output = StringIO()
        with patch.object(
            operation,
            "populate_bls_price_wage_productivity_live",
            return_value=report,
        ) as populate:
            with redirect_stdout(output):
                self.assertEqual(
                    operation.main(
                        [
                            "bls_price_wage_productivity",
                            "--from",
                            "1947",
                            "--to",
                            "1956",
                            "--series",
                            "PRS85006092",
                        ]
                    ),
                    0,
                )
        populate.assert_called_once_with(
            start_year=1947,
            end_year=1956,
            series_codes=("PRS85006092",),
        )
        self.assertIn('"source_key":"bls_price_wage_productivity"', output.getvalue())

    def test_cli_dispatches_credentialed_source_without_window(self) -> None:
        report = operation.OfficialConditionsPublishReport(
            "eia_gas", "published", "fixture", None, None, None, 0, 0,
            "2026-08-21", "2026-08-21",
        )
        output = StringIO()
        with patch.object(
            operation, "populate_eia_natural_gas_storage_live", return_value=report
        ) as populate:
            with redirect_stdout(output):
                self.assertEqual(operation.main(["eia_gas"]), 0)
        populate.assert_called_once_with()
        self.assertIn('"source_key":"eia_gas"', output.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
