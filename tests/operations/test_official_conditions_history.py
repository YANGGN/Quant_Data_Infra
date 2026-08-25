"""Focused offline route tests for fixed official-condition history sources."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

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
_TREASURY_DEBT_BODY = (
    b'{"data":[{"record_date":"2026-08-20",'
    b'"tot_pub_debt_out_amt":"37000123456789.01",'
    b'"debt_held_public_amt":"29500123456789.01",'
    b'"intragov_hold_amt":"7500000000000.00"}],'
    b'"meta":{"total-pages":"1","total-count":"1"}}'
)
_TREASURY_FISCAL_BALANCE_BODY = (
    b'{"data":['
    b'{"record_date":"2026-07-31","amt_category":"Receipts",'
    b'"mil_amt":"334010"},'
    b'{"record_date":"2026-07-31","amt_category":"Outlays",'
    b'"mil_amt":"766318"},'
    b'{"record_date":"2026-07-31",'
    b'"amt_category":"Deficit/Surplus (-)","mil_amt":"432308"},'
    b'{"record_date":"2026-07-31",'
    b'"amt_category":"Borrowing from the Public","mil_amt":"100"}],'
    b'"meta":{"total-pages":"1","total-count":"4"}}'
)
_EIA_BODY = (
    b'{"response":{"frequency":"weekly","total":"1","api_key":"'
    + SECRET.encode("utf-8")
    + b'","data":[{"period":"2026-08-21","value":"3300"}]}}'
)
_EIA_GASOLINE_STOCKS_BODY = (
    b'{"response":{"frequency":"weekly","total":"1","api_key":"'
    + SECRET.encode("utf-8")
    + b'","data":[{"period":"2026-08-14","series":"WGTSTUS1",'
    + b'"units":"Thousand Barrels","value":"235000"}]}}'
)
_EIA_DISTILLATE_STOCKS_BODY = (
    b'{"response":{"frequency":"weekly","total":"1","api_key":"'
    + SECRET.encode("utf-8")
    + b'","data":[{"period":"2026-08-14","series":"WDISTUS1",'
    + b'"units":"Thousand Barrels","value":"120000"}]}}'
)
_EIA_GASOLINE_SUPPLIED_BODY = (
    b'{"response":{"frequency":"weekly","total":"1","api_key":"'
    + SECRET.encode("utf-8")
    + b'","data":[{"period":"2026-08-14","series":"WGFUPUS2",'
    + b'"units":"Thousand Barrels per Day","value":"9100"}]}}'
)
_NBER_BODY = b'[{"peak":"2020-02","trough":"2020-04"}]'
_BLS_BODY = (
    b'{"status":"REQUEST_SUCCEEDED","message":[],"Results":{"series":['
    b'{"seriesID":"WPSFD4","data":['
    b'{"year":"2026","period":"M07","value":"150.1"}]},'
    b'{"seriesID":"CES0500000003","data":['
    b'{"year":"2026","period":"M07","value":"38.10"}]},'
    b'{"seriesID":"PRS85006092","data":['
    b'{"year":"2026","period":"Q02","value":"2.7"}]},'
    b'{"seriesID":"CIS1010000000000I","data":['
    b'{"year":"2026","period":"Q02","value":"168.1"}]}]}}'
)
_BEA_BODY = dumps_strict(
    {
        "BEAAPI": {
            "Request": {
                "RequestParam": [
                    {
                        "ParameterName": "UserID",
                        "ParameterValue": SECRET,
                    }
                ]
            },
            "Results": {
                "Data": [
                    {
                        "TableName": "T20600",
                        "SeriesCode": "A065RC",
                        "LineNumber": "1",
                        "LineDescription": "Personal income",
                        "TimePeriod": "2026M06",
                        "METRIC_NAME": "Current Dollars",
                        "CL_UNIT": "Level",
                        "UNIT_MULT": "6",
                        "DataValue": "26500.1",
                    },
                    {
                        "TableName": "T20600",
                        "SeriesCode": "A067RC",
                        "LineNumber": "27",
                        "LineDescription": (
                            "Equals: Disposable personal income"
                        ),
                        "TimePeriod": "2026M06",
                        "METRIC_NAME": "Current Dollars",
                        "CL_UNIT": "Level",
                        "UNIT_MULT": "6",
                        "DataValue": "22300.2",
                    },
                    {
                        "TableName": "T20600",
                        "SeriesCode": "DPCERC",
                        "LineNumber": "29",
                        "LineDescription": (
                            "Personal consumption expenditures"
                        ),
                        "TimePeriod": "2026M06",
                        "METRIC_NAME": "Current Dollars",
                        "CL_UNIT": "Level",
                        "UNIT_MULT": "6",
                        "DataValue": "20900.3",
                    },
                ]
            },
        }
    }
).encode("utf-8")

_POLICY_RATES_BODY = (
    b"observation_date,IORB,DFEDTARL,DFEDTARU\n"
    b"2026-08-20,3.65,3.50,3.75\n"
)
_INDUSTRIAL_PRODUCTION_BODY = (
    b"observation_date,INDPRO\n"
    b"2026-07-01,102.9\n"
)

_CHICAGO_BODY = (
    b"Friday_of_Week,NFCI,ANFCI,Risk,Credit,Leverage,"
    b"Nonfinancial_Leverage\n"
    b"08/21/2026,-0.28,-0.14,0.02,-0.03,0.01,0\n"
)


def _cfnai_body() -> bytes:
    headers = (
        "Date",
        "P_I",
        "EU_H",
        "C_H",
        "SO_I",
        "CFNAI",
        "CFNAI_MA3",
        "DIFFUSION",
    )
    shared_values = headers + ("2026:07",)
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        + "".join(f"<si><t>{item}</t></si>" for item in shared_values)
        + "</sst>"
    )
    header_cells = "".join(
        f'<c r="{chr(ord("A") + index)}1" t="s"><v>{index}</v></c>'
        for index in range(len(headers))
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        f'<row r="1">{header_cells}</row>'
        '<row r="2"><c r="A2" t="s"><v>8</v></c>'
        '<c r="F2"><v>0.14</v></c></row>'
        "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


_CFNAI_BODY = _cfnai_body()


class _Transport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> operation.CsvResponse:
        self.calls.append(dict(kwargs))
        url = str(kwargs["url"])
        if url.startswith(operation.TREASURY_FISCAL_BALANCE_URL):
            body = _TREASURY_FISCAL_BALANCE_BODY
        elif url.startswith(operation.TREASURY_DEBT_URL):
            body = _TREASURY_DEBT_BODY
        elif url.startswith(operation.TREASURY_TGA_URL):
            body = _TREASURY_BODY
        elif url.startswith(operation.EIA_NATURAL_GAS_STORAGE_URL):
            body = _EIA_BODY
        elif url.startswith(operation.EIA_TOTAL_MOTOR_GASOLINE_STOCKS_URL):
            body = _EIA_GASOLINE_STOCKS_BODY
        elif url.startswith(operation.EIA_DISTILLATE_FUEL_OIL_STOCKS_URL):
            body = _EIA_DISTILLATE_STOCKS_BODY
        elif url.startswith(
            operation.EIA_FINISHED_MOTOR_GASOLINE_PRODUCT_SUPPLIED_URL
        ):
            body = _EIA_GASOLINE_SUPPLIED_BODY
        elif url.startswith(operation.BEA_PERSONAL_INCOME_URL):
            body = _BEA_BODY
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
        elif url.startswith(operation.FRED_POLICY_RATES_URL):
            query = parse_qs(urlsplit(url).query)
            if query.get("id") == ["INDPRO"]:
                body = _INDUSTRIAL_PRODUCTION_BODY
            else:
                body = _POLICY_RATES_BODY
            return operation.CsvResponse(200, "text/csv", body, False)
        elif url == operation.CHICAGO_NFCI_URL:
            body = _CHICAGO_BODY
            return operation.CsvResponse(200, "text/csv", body, False)
        elif url == operation.CHICAGO_CFNAI_URL:
            body = _CFNAI_BODY
            return operation.CsvResponse(
                200,
                (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                body,
                False,
            )
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

    def test_three_petroleum_routes_issue_one_request_each(self) -> None:
        runner = self._runner()
        with patch.object(
            operation, "read_project_credential", return_value=SECRET
        ) as credential:
            runner.run_eia_total_motor_gasoline_stocks()
            runner.run_eia_distillate_fuel_oil_stocks()
            runner.run_eia_finished_motor_gasoline_product_supplied()

        self.assertEqual(credential.call_count, 3)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(
            [
                urlsplit(str(call["url"])).path
                for call in self.transport.calls
            ],
            [
                "/v2/seriesid/PET.WGTSTUS1.W",
                "/v2/seriesid/PET.WDISTUS1.W",
                "/v2/seriesid/PET.WGFUPUS2.W",
            ],
        )
        self.assertEqual(
            [capture.source_key for capture in self.publisher.captures],
            [
                "eia_total_motor_gasoline_stocks",
                "eia_distillate_fuel_oil_stocks",
                "eia_finished_motor_gasoline_product_supplied",
            ],
        )
        self.assertEqual(
            [
                capture.observations[0].provider_code
                for capture in self.publisher.captures
            ],
            ["WGTSTUS1", "WDISTUS1", "WGFUPUS2"],
        )
        for capture in self.publisher.captures:
            self.assertNotIn(
                SECRET.encode("utf-8"), capture.parts[0].body
            )

    def test_treasury_debt_route_uses_one_complete_fixed_page(self) -> None:
        runner = self._runner()
        runner.run_treasury_debt(
            start_date="2026-08-20", end_date="2026-08-20"
        )

        self.assertEqual(len(self.transport.calls), 1)
        query = parse_qs(
            urlsplit(str(self.transport.calls[0]["url"])).query
        )
        self.assertEqual(
            query["fields"],
            [
                "record_date,tot_pub_debt_out_amt,"
                "debt_held_public_amt,intragov_hold_amt"
            ],
        )
        self.assertEqual(
            query["filter"],
            ["record_date:gte:2026-08-20,record_date:lte:2026-08-20"],
        )
        self.assertEqual(query["page[size]"], ["10000"])
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "treasury_debt")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            [
                "tot_pub_debt_out_amt",
                "debt_held_public_amt",
                "intragov_hold_amt",
            ],
        )

    def test_treasury_fiscal_balance_uses_one_complete_fixed_page(self) -> None:
        runner = self._runner()
        runner.run_treasury_fiscal_balance(
            start_date="2026-07-01", end_date="2026-07-31"
        )

        self.assertEqual(len(self.transport.calls), 1)
        query = parse_qs(
            urlsplit(str(self.transport.calls[0]["url"])).query
        )
        self.assertEqual(
            query["fields"],
            ["record_date,amt_category,mil_amt"],
        )
        self.assertEqual(
            query["filter"],
            ["record_date:gte:2026-07-01,record_date:lte:2026-07-31"],
        )
        self.assertEqual(query["page[size]"], ["10000"])
        self.assertEqual(query["sort"], ["record_date"])
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "treasury_fiscal_balance")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["Receipts", "Outlays", "Deficit/Surplus (-)"],
        )

    def test_policy_rate_route_uses_one_fixed_fred_csv_request(self) -> None:
        runner = self._runner()
        runner.run_policy_rates(
            start_date="2026-08-20", end_date="2026-08-20"
        )

        self.assertEqual(len(self.transport.calls), 1)
        query = parse_qs(
            urlsplit(str(self.transport.calls[0]["url"])).query
        )
        self.assertEqual(
            query,
            {
                "id": ["IORB,DFEDTARL,DFEDTARU"],
                "cosd": ["2026-08-20"],
                "coed": ["2026-08-20"],
            },
        )
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "policy_rates")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["IORB", "DFEDTARL", "DFEDTARU"],
        )

    def test_industrial_production_uses_one_fixed_fred_request(self) -> None:
        runner = self._runner()
        runner.run_industrial_production(
            start_date="1919-01-01", end_date="2026-09-30"
        )

        self.assertEqual(len(self.transport.calls), 1)
        query = parse_qs(
            urlsplit(str(self.transport.calls[0]["url"])).query
        )
        self.assertEqual(
            query,
            {
                "id": ["INDPRO"],
                "cosd": ["1919-01-01"],
                "coed": ["2026-09-30"],
            },
        )
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "industrial_production")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["INDPRO"],
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

    def test_cfnai_route_uses_one_fixed_official_workbook(self) -> None:
        runner = self._runner()
        runner.run_cfnai(
            start_date="1967-03-01", end_date="2026-09-30"
        )

        self.assertEqual(len(self.transport.calls), 1)
        call = self.transport.calls[0]
        self.assertEqual(call["url"], operation.CHICAGO_CFNAI_URL)
        self.assertEqual(
            call["headers"]["Accept"],
            (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "cfnai")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["CFNAI"],
        )

    def test_bea_route_uses_one_fixed_full_history_request(self) -> None:
        runner = self._runner()
        with patch.object(
            operation, "read_project_credential", return_value=SECRET
        ) as credential:
            runner.run_bea_personal_income()

        credential.assert_called_once()
        self.assertEqual(len(self.transport.calls), 1)
        call = self.transport.calls[0]
        self.assertEqual(call["method"], "GET")
        query = parse_qs(urlsplit(str(call["url"])).query)
        self.assertEqual(
            query,
            {
                "method": ["GetData"],
                "datasetname": ["NIPA"],
                "TableName": ["T20600"],
                "Frequency": ["M"],
                "Year": ["ALL"],
                "ResultFormat": ["JSON"],
                "UserID": [SECRET],
            },
        )
        capture = self.publisher.captures[0]
        self.assertEqual(capture.source_key, "bea_personal_income")
        self.assertEqual(
            [item.provider_code for item in capture.observations],
            ["A065RC", "A067RC", "DPCERC"],
        )
        self.assertNotIn(SECRET.encode("utf-8"), capture.parts[0].body)

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
                    "CIS1010000000000I",
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
