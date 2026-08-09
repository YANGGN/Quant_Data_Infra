"""Small deterministic JSON-Schema subset for registry-owned contracts."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from .errors import Issue, ValidationError
from .json_codec import ensure_number_within_limits
from .temporal import TemporalValue


_SUPPORTED_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float, Decimal))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
            and (not isinstance(value, Decimal) or value.is_finite())
        )
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    code: str = "invalid_request",
    max_issues: int = 32,
) -> None:
    issues: list[Issue] = []

    def add(pointer: str, rule: str, message: str) -> None:
        if len(issues) < max_issues:
            issues.append(Issue(pointer or "/", rule, message))

    def visit(item: Any, declaration: dict[str, Any], pointer: str) -> None:
        raw_types = declaration.get("type")
        expected = [raw_types] if isinstance(raw_types, str) else raw_types
        if not isinstance(expected, list) or not expected or not set(expected).issubset(_SUPPORTED_TYPES):
            add(pointer, "schema", "Contract contains an unsupported type declaration")
            return
        if not any(_matches_type(item, candidate) for candidate in expected):
            add(pointer, "type", f"Expected {' or '.join(expected)}")
            return
        if "const" in declaration and item != declaration["const"]:
            add(pointer, "const", "Value does not match the fixed contract value")
        if "enum" in declaration and item not in declaration["enum"]:
            add(pointer, "enum", "Value is not one of the supported choices")

        if isinstance(item, dict) and "object" in expected:
            properties = declaration.get("properties", {})
            required = declaration.get("required", [])
            for name in required:
                if name not in item:
                    add(f"{pointer}/{name}", "required", "Required field is missing")
            if declaration.get("additionalProperties") is False:
                for name in item:
                    if name not in properties:
                        add(f"{pointer}/{name}", "additional_properties", "Unknown field")
            for name, child in item.items():
                if name in properties:
                    visit(child, properties[name], f"{pointer}/{name}")
        elif isinstance(item, (list, tuple)) and "array" in expected:
            if "maxItems" in declaration and len(item) > declaration["maxItems"]:
                add(pointer, "max_items", "Array exceeds the supported item limit")
            if "minItems" in declaration and len(item) < declaration["minItems"]:
                add(pointer, "min_items", "Array has too few items")
            child_schema = declaration.get("items")
            if isinstance(child_schema, dict):
                for index, child in enumerate(item):
                    visit(child, child_schema, f"{pointer}/{index}")
        elif isinstance(item, str) and "string" in expected:
            if "minLength" in declaration and len(item) < declaration["minLength"]:
                add(pointer, "min_length", "String is too short")
            if "maxLength" in declaration and len(item) > declaration["maxLength"]:
                add(pointer, "max_length", "String is too long")
            if declaration.get("format") == "date":
                try:
                    temporal = TemporalValue.parse(item, pointer=pointer or "/")
                    if temporal.precision.value != "date":
                        add(pointer, "format", "Expected a calendar date")
                except ValidationError:
                    add(pointer, "format", "Expected a valid calendar date")
        elif (
            isinstance(item, (int, float, Decimal))
            and not isinstance(item, bool)
            and any(candidate in expected for candidate in ("integer", "number"))
        ):
            ensure_number_within_limits(item)
            if "minimum" in declaration and item < declaration["minimum"]:
                add(pointer, "minimum", "Number is below the supported minimum")
            if "maximum" in declaration and item > declaration["maximum"]:
                add(pointer, "maximum", "Number exceeds the supported maximum")

    visit(value, schema, "")
    if issues:
        message = "Output failed its contract" if code == "invalid_output" else "Request failed its contract"
        raise ValidationError(message, issues=issues, code=code)
