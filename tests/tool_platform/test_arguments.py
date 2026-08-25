from __future__ import annotations

import dataclasses
import tempfile
import unittest
from collections.abc import Mapping
from decimal import Decimal

from quant_data.contracts import TimeSeries
from quant_data.errors import ValidationError
from quant_data.schema import validate_schema
from quant_data.tool_platform.arguments import (
    ArgumentParameter,
    MultiSeriesArguments,
    QueryArguments,
    Stage10AvailableTickerArgumentsV1,
    SearchArguments,
    SingleSeriesArguments,
    Stage10MarketRegressionArgumentsV21,
    Stage10MarketRegressionModelSuiteArgumentsV3,
    Stage10MarketRollingRegressionArgumentsV21,
    Stage10MarketStructuralBreakArgumentsV2,
    input_schema,
    parse_arguments,
    preflight_dimensions,
    public_arguments,
)


SERIES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["observations"],
    "properties": {
        "observations": {
            "type": "array",
            "maxItems": 10_000,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
        }
    },
}


def series(identifier: str = "fixture.series") -> TimeSeries:
    return TimeSeries(
        series_id=identifier,
        metadata={},
        observations=(),
        warnings=(),
        audit={},
        provenance={},
    )


def raw_series(observations: int = 0) -> dict[str, object]:
    return {"observations": [{} for _ in range(observations)]}


class ArgumentContractTests(unittest.TestCase):
    def test_schema_is_generated_from_typed_field_definitions(self) -> None:
        search = input_schema("search", SERIES_SCHEMA)
        declared = dataclasses.fields(SearchArguments)
        self.assertEqual(search["required"], [item.name for item in declared])
        query_declaration = next(item for item in declared if item.name == "query")
        self.assertEqual(
            search["properties"]["query"]["maxLength"],
            query_declaration.metadata["schema"].max_length,
        )
        self.assertFalse(search["additionalProperties"])

        multi = input_schema("multi_series", SERIES_SCHEMA)
        self.assertEqual(multi["properties"]["series"]["maxItems"], 20)
        self.assertEqual(
            multi["properties"]["series"]["items"]["properties"]["observations"][
                "maxItems"
            ],
            10_000,
        )
        multi["properties"]["series"]["items"]["properties"]["observations"][
            "maxItems"
        ] = 1
        self.assertEqual(
            input_schema("multi_series", SERIES_SCHEMA)["properties"]["series"][
                "items"
            ]["properties"]["observations"]["maxItems"],
            10_000,
        )

    def test_available_ticker_optional_limit_defaults_and_remains_strict(
        self,
    ) -> None:
        input_kind = "stage10_available_ticker_v1"
        schema = input_schema(input_kind, SERIES_SCHEMA)
        self.assertEqual(schema["required"], [])
        self.assertEqual(schema["properties"]["limit"]["maximum"], 10_000)
        validate_schema({}, schema)

        decoded = parse_arguments(input_kind, {}, lambda _: series())
        self.assertIsInstance(decoded, Stage10AvailableTickerArgumentsV1)
        self.assertEqual(decoded.limit, 10_000)
        self.assertEqual(
            preflight_dimensions({}, input_kind=input_kind),
            {"rows": 10_000, "series": 0, "operations": 10_000},
        )
        limited = parse_arguments(
            input_kind, {"limit": 25}, lambda _: series()
        )
        self.assertEqual(limited.limit, 25)
        with self.assertRaises(ValidationError):
            validate_schema({"limit": 10_001}, schema)
        with self.assertRaises(ValidationError):
            parse_arguments(
                input_kind,
                {"limit": 10_001},
                lambda _: series(),
            )
        with self.assertRaises(ValidationError):
            parse_arguments(
                input_kind,
                {"database_path": "/tmp/forbidden.sqlite"},
                lambda _: series(),
            )

    def test_typed_argument_values_are_frozen_mappings(self) -> None:
        source_parameters = [{"name": "window", "value": 5}]
        typed = parse_arguments(
            "query",
            {
                "identifiers": ["fixture"],
                "mode": "latest",
                "as_of": None,
                "start_date": None,
                "end_date": None,
                "parameters": source_parameters,
                "limit": 100,
            },
            lambda _: self.fail("query parsing must not decode a series"),
        )
        self.assertIsInstance(typed, QueryArguments)
        self.assertIsInstance(typed, Mapping)
        self.assertEqual(typed.get("mode"), "latest")
        self.assertEqual(tuple(typed), tuple(item.name for item in dataclasses.fields(QueryArguments)))
        self.assertIsInstance(typed["parameters"][0], ArgumentParameter)
        with self.assertRaises(TypeError):
            typed["limit"] = 1  # type: ignore[index]
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
            typed.limit = 1  # type: ignore[misc]
        with self.assertRaises(TypeError):
            typed["parameters"][0]["name"] = "changed"  # type: ignore[index]
        source_parameters[0]["value"] = 99
        self.assertEqual(typed["parameters"][0]["value"], 5)

    def test_21_series_is_rejected_by_schema_and_parser_before_decoder(self) -> None:
        public = {
            "series": [raw_series() for _ in range(21)],
            "parameters": [],
            "limit": 100,
        }
        with self.assertRaises(ValidationError):
            validate_schema(public, input_schema("multi_series", SERIES_SCHEMA))

        decoder_calls = 0

        def decode(_: object) -> TimeSeries:
            nonlocal decoder_calls
            decoder_calls += 1
            return series()

        with self.assertRaises(ValidationError):
            parse_arguments("multi_series", public, decode)
        self.assertEqual(decoder_calls, 0)

    def test_semantic_validation_rechecks_cross_fields_scalars_and_lineage(self) -> None:
        base = {
            "identifiers": ["fixture"],
            "mode": "as_of",
            "as_of": None,
            "start_date": "2024-02-02",
            "end_date": "2024-02-01",
            "parameters": [
                {"name": "window", "value": Decimal("NaN")},
                {"name": "window", "value": 5},
            ],
            "limit": 100,
        }
        with self.assertRaises(ValidationError):
            parse_arguments("query", base, lambda _: series())

        valid = {
            **base,
            "as_of": "2024-02-03T12:00:00Z",
            "end_date": "2024-02-02",
            "parameters": [{"name": "window", "value": 5}],
        }
        self.assertIsInstance(parse_arguments("query", valid, lambda _: series()), QueryArguments)
        with self.assertRaises(ValidationError):
            parse_arguments("query", {**valid, "mode": "latest"}, lambda _: series())

        with self.assertRaises(ValidationError):
            parse_arguments(
                "research",
                {
                    "series": [series()],
                    "parameters": [],
                    "limit": 1,
                    "as_of": "not-an-iso-cutoff",
                    "unsafe_ok": False,
                },
                lambda _: series(),
            )

        mutable_metadata: dict[str, object] = {
            "nested": {"state": "original"},
        }
        immutable = TimeSeries(
            series_id="immutable",
            metadata=mutable_metadata,
            observations=(),
            warnings=(),
            audit={},
            provenance={},
        )
        mutable_metadata["changed"] = True
        mutable_metadata["nested"]["state"] = "changed"  # type: ignore[index]
        parsed = parse_arguments(
            "single_series",
            {"series": immutable, "parameters": [], "limit": 1},
            lambda _: self.fail("direct TimeSeries values must not be decoded"),
        )
        self.assertIsInstance(parsed, SingleSeriesArguments)
        self.assertNotIn("changed", immutable.metadata)
        self.assertEqual(immutable.metadata["nested"]["state"], "original")
        with self.assertRaises(TypeError):
            immutable.metadata["changed"] = True  # type: ignore[index]
        with self.assertRaises(TypeError):
            immutable.metadata["nested"]["state"] = "changed"  # type: ignore[index]

    def test_v21_econometrics_arguments_have_bounded_preflight_costs(self) -> None:
        regression_public = {
            "series": [raw_series(12), raw_series(10)],
            "intercept": True,
            "covariance": "newey_west_hac_bartlett",
            "hac_lag": 2,
            "diagnostic_lag": 3,
            "confidence_level": "0.95",
            "limit": 10,
        }
        regression = parse_arguments(
            "stage10_market_regression_v2_1",
            regression_public,
            lambda _: series(),
        )
        self.assertIsInstance(
            regression, Stage10MarketRegressionArgumentsV21
        )
        self.assertEqual(
            preflight_dimensions(
                regression_public,
                input_kind="stage10_market_regression_v2_1",
            ),
            {"rows": 12, "series": 2, "operations": 192},
        )

        rolling_public = {
            **regression_public,
            "window": 8,
            "diagnostic_lag": 1,
        }
        rolling = parse_arguments(
            "stage10_market_rolling_regression_v2_1",
            rolling_public,
            lambda _: series(),
        )
        self.assertIsInstance(
            rolling, Stage10MarketRollingRegressionArgumentsV21
        )
        self.assertEqual(
            preflight_dimensions(
                rolling_public,
                input_kind="stage10_market_rolling_regression_v2_1",
            ),
            {"rows": 12, "series": 2, "operations": 1152},
        )

    def test_structural_break_arguments_are_strict_and_budget_three_fits(self) -> None:
        input_kind = "stage10_market_structural_breaks_v2"
        public = {
            "series": [raw_series(12), raw_series(10)],
            "intercept": True,
            "break_index": 6,
            "significance": "0.05",
            "limit": 12,
        }
        schema = input_schema(input_kind, SERIES_SCHEMA)
        self.assertEqual(
            schema["properties"]["break_index"],
            {"type": "integer", "minimum": 1, "maximum": 4999},
        )
        self.assertEqual(schema["properties"]["series"]["minItems"], 2)
        self.assertEqual(schema["properties"]["series"]["maxItems"], 20)
        validate_schema(public, schema)

        decoded = parse_arguments(
            input_kind,
            public,
            lambda _: series(),
        )
        self.assertIsInstance(
            decoded,
            Stage10MarketStructuralBreakArgumentsV2,
        )
        self.assertEqual(decoded.break_index, 6)
        self.assertEqual(
            preflight_dimensions(public, input_kind=input_kind),
            {"rows": 12, "series": 2, "operations": 144},
        )
        self.assertEqual(
            preflight_dimensions(public),
            {"rows": 12, "series": 2, "operations": 144},
        )

        with self.assertRaises(ValidationError):
            parse_arguments(
                input_kind,
                {**public, "break_index": 12},
                lambda _: series(),
            )
        with self.assertRaises(ValidationError):
            parse_arguments(
                input_kind,
                {**public, "significance": "0.025"},
                lambda _: series(),
            )
        with self.assertRaises(ValidationError):
            validate_schema(
                {**public, "break_index": True},
                schema,
            )


    def test_public_conversion_and_preflight_are_json_safe_and_decoder_free(self) -> None:
        first = series("first")
        second = series("second")
        typed = parse_arguments(
            "single_series",
            {
                "series": first,
                "parameters": [{"name": "threshold", "value": Decimal("1.5")}],
                "limit": 10,
            },
            lambda _: self.fail("direct TimeSeries values must not be decoded"),
        )
        self.assertIsInstance(typed, SingleSeriesArguments)
        public = public_arguments(typed)
        self.assertIsInstance(public["series"], dict)
        self.assertIsInstance(public["parameters"], list)
        self.assertEqual(public["parameters"][0], {"name": "threshold", "value": Decimal("1.5")})
        public["series"]["metadata"]["changed"] = True
        self.assertNotIn("changed", first.metadata)

        direct = public_arguments({"series": (first, second)})
        self.assertEqual([item["series_id"] for item in direct["series"]], ["first", "second"])

        dimensions = preflight_dimensions(
            {
                "series": [raw_series(3), raw_series(4)],
                "parameters": [],
                "limit": 2,
            }
        )
        self.assertEqual(dimensions, {"rows": 4, "series": 2, "operations": 16})

    def test_schema_hard_limits_cover_below_boundary_and_above(self) -> None:
        search_schema = input_schema("search", SERIES_SCHEMA)
        for query_length in (499, 500):
            validate_schema(
                {"query": "x" * query_length, "as_of": None, "limit": 500},
                search_schema,
            )
        with self.assertRaises(ValidationError):
            validate_schema(
                {"query": "x" * 501, "as_of": None, "limit": 500},
                search_schema,
            )
        for limit in (499, 500):
            validate_schema(
                {"query": "x", "as_of": None, "limit": limit},
                search_schema,
            )
        with self.assertRaises(ValidationError):
            validate_schema(
                {"query": "x", "as_of": None, "limit": 501},
                search_schema,
            )

        query_schema = input_schema("query", SERIES_SCHEMA)
        query = {
            "identifiers": [],
            "mode": "latest",
            "as_of": None,
            "start_date": None,
            "end_date": None,
            "parameters": [],
            "limit": 10_000,
        }
        validate_schema({**query, "limit": 9_999}, query_schema)
        validate_schema(query, query_schema)
        with self.assertRaises(ValidationError):
            validate_schema({**query, "limit": 10_001}, query_schema)

        multi_schema = input_schema("multi_series", SERIES_SCHEMA)
        multi = {"parameters": [], "limit": 10_000}
        for count in (19, 20):
            validate_schema(
                {**multi, "series": [raw_series() for _ in range(count)]},
                multi_schema,
            )
        with self.assertRaises(ValidationError):
            validate_schema(
                {**multi, "series": [raw_series() for _ in range(21)]},
                multi_schema,
            )
        for count in (49, 50):
            validate_schema(
                {
                    **multi,
                    "series": [raw_series()],
                    "parameters": [
                        {"name": f"p{index}", "value": index}
                        for index in range(count)
                    ],
                },
                multi_schema,
            )
        with self.assertRaises(ValidationError):
            validate_schema(
                {
                    **multi,
                    "series": [raw_series()],
                    "parameters": [
                        {"name": f"p{index}", "value": index}
                        for index in range(51)
                    ],
                },
                multi_schema,
            )

    def test_v3_model_suite_schema_cross_fields_and_preflight_are_bounded(
        self,
    ) -> None:
        input_kind = "stage10_market_regression_model_suite_v3"
        schema = input_schema(input_kind, SERIES_SCHEMA)
        base = {
            "series": [raw_series(30), raw_series(30)],
            "analysis": "engle_granger_cointegration",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": -1,
            "target_index": -1,
            "limit": 30,
        }

        self.assertEqual(schema["properties"]["series"]["minItems"], 2)
        self.assertEqual(schema["properties"]["series"]["maxItems"], 5)
        self.assertEqual(
            schema["properties"]["analysis"]["enum"],
            [
                "engle_granger_cointegration",
                "vector_autoregression",
                "granger_causality",
            ],
        )
        validate_schema(base, schema)
        decoded = parse_arguments(input_kind, base, lambda _: series())
        self.assertIsInstance(
            decoded,
            Stage10MarketRegressionModelSuiteArgumentsV3,
        )
        self.assertEqual(decoded.analysis, "engle_granger_cointegration")
        self.assertEqual(
            preflight_dimensions(base, input_kind=input_kind),
            {"rows": 30, "series": 2, "operations": 270},
        )

        var = {
            **base,
            "analysis": "vector_autoregression",
            "source_index": -1,
            "target_index": -1,
        }
        self.assertEqual(
            preflight_dimensions(var, input_kind=input_kind),
            {"rows": 30, "series": 2, "operations": 468},
        )
        granger = {
            **base,
            "analysis": "granger_causality",
            "source_index": 0,
            "target_index": 1,
        }
        self.assertEqual(
            preflight_dimensions(granger, input_kind=input_kind),
            {"rows": 30, "series": 2, "operations": 588},
        )

        worst_case = {
            **granger,
            "series": [raw_series(5_000) for _ in range(5)],
            "lag_order": 4,
            "source_index": 0,
            "target_index": 4,
            "limit": 5_000,
        }
        dimensions = preflight_dimensions(
            worst_case,
            input_kind=input_kind,
        )
        self.assertEqual(dimensions["operations"], 4_177_205)
        self.assertLess(dimensions["operations"], 5_000_000)

        invalid_cases = (
            {**base, "series": [raw_series(30) for _ in range(3)]},
            {**base, "limit": 20},
            {**var, "lag_order": 0},
            {**var, "source_index": 0},
            {**granger, "source_index": 1, "target_index": 1},
            {**granger, "target_index": 2},
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    parse_arguments(
                        input_kind,
                        invalid,
                        lambda _: self.fail(
                            "cross-field failures must precede series decoding"
                        ),
                    )



if __name__ == "__main__":
    unittest.main()
