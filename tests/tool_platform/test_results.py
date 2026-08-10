"""Focused tests for canonical Stage 5 result and research contracts."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
import unittest

from quant_data.contracts import (
    ExclusionV1,
    LineageRef,
    ResearchContractV1,
    TemporalQuery,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ValidationError
from quant_data.schema import validate_schema
from quant_data.tool_platform.results import (
    DiagnosticV1,
    FieldV1,
    MatrixV1,
    QueryResult,
    RecordV1,
    ResearchEnvelopeV1,
    _declared_result_fields,
    _static_result_fields,
    query_result_schema,
    research_envelope,
)


class ResultContractTests(unittest.TestCase):
    def test_matrix_is_rectangular_finite_and_bounded(self) -> None:
        matrix = MatrixV1(
            "correlation",
            ("left", "right"),
            ("left", "right"),
            (
                (Decimal("1"), Decimal("0.5")),
                (Decimal("0.5"), Decimal("1")),
            ),
        )
        self.assertEqual(matrix.to_primitive()["values"][0][1], Decimal("0.5"))
        with self.assertRaises(ValidationError):
            MatrixV1("bad", ("row",), ("a", "b"), ((Decimal("1"),),))
        with self.assertRaises(ValidationError):
            MatrixV1("bad", ("row", "row"), ("a",), ((1,), (2,)))
        with self.assertRaises(ValidationError):
            MatrixV1("bad", ("row",), ("a",), ((1.25,),))
        with self.assertRaises(ValidationError):
            MatrixV1(
                "too-large",
                tuple(str(index) for index in range(1001)),
                tuple(str(index) for index in range(100)),
                (),
            )

    def test_research_contract_is_complete_deterministic_and_path_free(self) -> None:
        lineage = (
            LineageRef(
                "fixture.macro.rtdsm_employ",
                "macro",
                "fixture.series",
                evidence_id="evidence-1",
                snapshot_id="snapshot-1",
                canonical_version_id="version-1",
            ),
        )
        arguments = {
            "series": [],
            "parameters": [{"name": "window", "value": 12}],
            "limit": 100,
            "as_of": "2026-08-01T00:00:00Z",
            "unsafe_ok": True,
        }
        first = research_envelope(
            "research.event_study", arguments, (), lineage,
        )
        second = research_envelope(
            "research.event_study", arguments, (), lineage,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.status, "declared")
        contract = first.contract
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(len(contract.analysis_id), 64)
        self.assertEqual(contract.temporal.mode, "as_of")
        self.assertEqual(contract.temporal.cutoff, arguments["as_of"])
        self.assertEqual(contract.point_in_time_status, "not_established")
        self.assertEqual(
            contract.unsafe_reasons,
            ("point_in_time_semantics_not_established",),
        )
        primitive = first.to_primitive()
        serialized = str(primitive).lower()
        self.assertNotIn("sqlite", serialized)
        self.assertNotIn("/home/", serialized)
        with self.assertRaises(TypeError):
            contract.parameters["window"] = 13  # type: ignore[index]

    def test_unsafe_research_requires_explicit_authorization(self) -> None:
        arguments = {
            "parameters": [],
            "as_of": None,
            "unsafe_ok": False,
        }
        with self.assertRaises(ValidationError):
            research_envelope(
                "research.event_study",
                arguments,
                (),
                (),
                point_in_time_status="unsafe",
                unsafe_reasons=("ex_post_fixture",),
            )

    def test_schema_is_generated_from_immutable_typed_declarations(self) -> None:
        schema = query_result_schema(
            "research.event_study",
            {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
        )

        def assert_contract(
            contract_type: type[object],
            object_schema: dict[str, object],
        ) -> None:
            declarations = [
                (item.name, item.declaration)
                for item in _static_result_fields(contract_type)
            ]
            typed_fields = _declared_result_fields(contract_type)
            self.assertEqual(
                [field.name for field, _ in typed_fields],
                [field.name for field in dataclasses.fields(contract_type)],
            )
            declarations.extend(
                (field.name, declaration)
                for field, declaration in typed_fields
            )
            expected = [
                (name, declaration)
                for _, (name, declaration) in sorted(
                    enumerate(declarations),
                    key=lambda item: (item[1][1].order, item[0]),
                )
            ]
            self.assertEqual(
                object_schema["required"],
                [name for name, declaration in expected if declaration.required],
            )
            self.assertEqual(
                list(object_schema["properties"]),
                [name for name, _ in expected],
            )
            self.assertFalse(object_schema["additionalProperties"])
            properties = object_schema["properties"]
            assert isinstance(properties, dict)
            for name, declaration in expected:
                field_schema = properties[name]
                assert isinstance(field_schema, dict)
                if declaration.form == "tool":
                    self.assertEqual(field_schema["const"], "research.event_study")
                elif declaration.form == "scalar":
                    expected_type: str | list[str] = (
                        declaration.types[0]
                        if len(declaration.types) == 1
                        else list(declaration.types)
                    )
                    self.assertEqual(field_schema["type"], expected_type)
                if declaration.enum:
                    self.assertEqual(field_schema["enum"], list(declaration.enum))
                if declaration.minimum is not None:
                    self.assertEqual(field_schema["minimum"], declaration.minimum)
                if declaration.maximum is not None:
                    self.assertEqual(field_schema["maximum"], declaration.maximum)
                if declaration.min_length is not None:
                    self.assertEqual(field_schema["minLength"], declaration.min_length)
                if declaration.max_length is not None:
                    self.assertEqual(field_schema["maxLength"], declaration.max_length)
                if declaration.min_items is not None:
                    self.assertEqual(field_schema["minItems"], declaration.min_items)
                if declaration.max_items is not None:
                    self.assertEqual(field_schema["maxItems"], declaration.max_items)

        assert_contract(QueryResult, schema)
        assert_contract(
            RecordV1,
            schema["properties"]["records"]["items"],
        )
        assert_contract(
            FieldV1,
            schema["properties"]["records"]["items"]["properties"]["fields"]["items"],
        )
        assert_contract(
            MatrixV1,
            schema["properties"]["matrices"]["items"],
        )
        assert_contract(
            DiagnosticV1,
            schema["properties"]["diagnostics"]["items"],
        )
        assert_contract(
            WarningV1,
            schema["properties"]["warnings"]["items"],
        )
        assert_contract(
            ExclusionV1,
            schema["properties"]["exclusions"]["items"],
        )
        assert_contract(
            LineageRef,
            schema["properties"]["lineage"]["items"],
        )
        assert_contract(
            TruncationV1,
            schema["properties"]["truncation"],
        )
        assert_contract(
            ResearchEnvelopeV1,
            schema["properties"]["research_contract"],
        )
        research_schema = schema["properties"]["research_contract"]["properties"][
            "contract"
        ]
        assert_contract(ResearchContractV1, research_schema)
        assert_contract(
            TemporalQuery,
            research_schema["properties"]["temporal"],
        )

    def test_query_result_and_generated_schema_share_the_same_shape(self) -> None:
        matrix = MatrixV1("one", ("r",), ("c",), ((Decimal("1"),),))
        result = QueryResult(
            tool="research.event_study",
            matrices=(matrix,),
            truncation=TruncationV1(False, 100, 0, 0, False),
            research_contract=research_envelope(
                "research.event_study",
                {"parameters": [], "as_of": None, "unsafe_ok": True},
                (),
                (),
                exclusions=(ExclusionV1("none", "fixture boundary"),),
            ),
        )
        primitive = result.to_primitive()
        schema = query_result_schema(
            "research.event_study",
            {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
            },
        )
        validate_schema(primitive, schema, code="invalid_output")
        self.assertIn("matrices", schema["required"])
        self.assertEqual(primitive["matrices"][0]["name"], "one")


if __name__ == "__main__":
    unittest.main()
