"""Strict deterministic RFC-JSON parsing and rendering."""

from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from .errors import Issue, ResourceLimitError, ValidationError


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_DECIMAL_DIGITS = 4096
MAX_DECIMAL_EXPONENT = 4096
MAX_INTEGER_BITS = 13_610


def _reject_constant(value: str) -> None:
    raise ValidationError(
        "Non-finite JSON number is not allowed",
        issues=(Issue("/", "finite", f"Unsupported token {value}"),),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(
                "Duplicate JSON object key",
                issues=(Issue(f"/{key}", "unique_key", "Object keys must be unique"),),
            )
        result[key] = value
    return result


def ensure_number_within_limits(value: int | float | Decimal) -> None:
    """Reject compact numbers that would require unbounded materialization.

    The byte limit alone is insufficient for exponent notation: a tiny token
    such as ``1e8500000`` can expand to megabytes when rendered in canonical
    fixed-point form.  These limits are deliberately well below the public
    response bound and are applied both while parsing and before rendering or
    schema-driven arithmetic.
    """

    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if value.bit_length() > MAX_INTEGER_BITS:
            raise ResourceLimitError("JSON integer exceeds the supported magnitude")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite JSON number is not allowed")
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Non-finite JSON number is not allowed")
        representation = value.as_tuple()
        exponent = int(representation.exponent)
        if (
            len(representation.digits) > MAX_DECIMAL_DIGITS
            or abs(exponent) > MAX_DECIMAL_EXPONENT
            or abs(value.adjusted()) > MAX_DECIMAL_EXPONENT
        ):
            raise ResourceLimitError("JSON decimal exceeds the supported magnitude")


def _parse_integer(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_DECIMAL_DIGITS:
        raise ResourceLimitError("JSON integer exceeds the supported magnitude")
    value = int(token)
    ensure_number_within_limits(value)
    return value


def _parse_decimal(token: str) -> Decimal:
    value = Decimal(token)
    ensure_number_within_limits(value)
    return value


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ResourceLimitError("JSON nesting exceeds the supported limit")
    if isinstance(value, dict):
        for child in value.values():
            _check_depth(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_depth(child, depth + 1)


def loads_strict(payload: bytes | str, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
    if isinstance(payload, bytes):
        if len(payload) > max_bytes:
            raise ResourceLimitError("JSON request exceeds the supported byte limit")
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "Request body is not valid UTF-8",
                issues=(Issue("/", "utf8", "Strict UTF-8 is required"),),
                code="invalid_utf8",
            ) from exc
    elif isinstance(payload, str):
        text = payload
        if len(text.encode("utf-8")) > max_bytes:
            raise ResourceLimitError("JSON request exceeds the supported byte limit")
    else:
        raise ValidationError("JSON payload must be bytes or text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=_parse_decimal,
            parse_int=_parse_integer,
            parse_constant=_reject_constant,
        )
    except ValueError as exc:
        raise ValidationError(
            "Malformed JSON",
            issues=(Issue("/", "json", "Request body must contain one valid JSON value"),),
            code="invalid_json",
        ) from exc
    _check_depth(value)
    return value


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("Non-finite output is not allowed", code="invalid_output")
    ensure_number_within_limits(value)
    text = format(value, "f")
    if text in ("-0", "-0.0"):
        return "0"
    return text


def _encode(value: Any, depth: int = 0) -> str:
    if depth > MAX_JSON_DEPTH:
        raise ResourceLimitError("JSON output nesting exceeds the supported limit")
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        ensure_number_within_limits(value)
        return str(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite output is not allowed", code="invalid_output")
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (date, datetime)):
        raise ValidationError(
            "Implementation date and datetime objects are not public JSON values",
            code="invalid_output",
        )
    if isinstance(value, Enum):
        return _encode(value.value, depth)
    if hasattr(value, "to_primitive") and callable(value.to_primitive):
        value = value.to_primitive()
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("JSON object keys must be strings", code="invalid_output")
        parts = []
        for key in sorted(value):
            parts.append(json.dumps(key, ensure_ascii=False) + ":" + _encode(value[key], depth + 1))
        return "{" + ",".join(parts) + "}"
    raise ValidationError(
        f"Unsupported JSON output type: {type(value).__name__}",
        code="invalid_output",
    )


def dumps_strict(value: Any, *, max_bytes: int = MAX_JSON_BYTES) -> str:
    rendered = _encode(value)
    if len(rendered.encode("utf-8")) > max_bytes:
        raise ResourceLimitError("JSON output exceeds the supported byte limit")
    return rendered
