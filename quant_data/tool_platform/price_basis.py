"""Additive metadata contract for retained FMP full-EOD close values.

This module describes prices; it never adjusts them or establishes historical
provider publication times. Old public schemas continue to reject these fields.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from quant_data.errors import ValidationError

BASIS = "split_adjusted_excluding_distributions"
BINDING = "fmp_full_eod_close_split_only_v1"
DOCUMENTATION = "https://site.financialmodelingprep.com/faqs"
BASIS_FIELDS = (
    "source_price_field", "price_adjustment_applied_by_tool",
    "source_adjustment_binding", "source_adjustment_documentation",
    "source_capture_count", "adjustment_vintage_status", "source_publication_time",
)


def metadata_fields(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Copy explicit source evidence without deriving a new price basis."""
    if "source_price_field" not in metadata:
        return {}
    return {key: metadata[key] for key in BASIS_FIELDS}


def expected_price_metadata(metadata: Mapping[str, Any],
                            expected: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the new evidence convention at domain adapter seams only."""
    result = dict(expected)
    status = metadata.get("adjustment_status")
    supplied = any(key in metadata for key in BASIS_FIELDS)
    if not supplied and status == "not_established":
        return result
    if not all(key in metadata for key in BASIS_FIELDS):
        raise ValidationError("Price adjustment metadata is incomplete")
    count = metadata["source_capture_count"]
    if (isinstance(count, bool) or not isinstance(count, int) or
            not 0 <= count <= 10000 or
            metadata["price_adjustment_applied_by_tool"] is not False or
            metadata["source_publication_time"] is not None or
            metadata["source_price_field"] != metadata.get("observation_field")):
        raise ValidationError("Price adjustment metadata is inconsistent")
    if status == BASIS:
        if (metadata.get("provider") != "fmp" or
                metadata.get("price_variant") != "fmp_full_eod_v1" or
                metadata.get("currency_segment") != "provider_native" or
                metadata["source_price_field"] != "close" or count == 0 or
                metadata["source_adjustment_binding"] != BINDING or
                metadata["source_adjustment_documentation"] != DOCUMENTATION or
                metadata["adjustment_vintage_status"] !=
                ("single_provider_capture" if count == 1 else "mixed_provider_captures")):
            raise ValidationError("Split-only close requires its explicit provider binding")
    elif status == "not_established":
        if (metadata["source_adjustment_binding"] is not None or
                metadata["source_adjustment_documentation"] is not None or
                metadata["adjustment_vintage_status"] != "not_established"):
            raise ValidationError("Unestablished price basis cannot claim a provider binding")
    else:
        raise ValidationError("Unsupported price adjustment basis")
    result["adjustment_status"] = status
    return result



def validate_series_price_basis(series: Any) -> None:
    """Keep explicit source metadata, observation flags and warnings consistent."""
    metadata = series.metadata
    if not any(key in metadata for key in BASIS_FIELDS):
        return  # Preserve the predecessor's validation for predecessor-shaped inputs.
    expected_price_metadata(metadata, {"adjustment_status": "not_established"})
    status = metadata["adjustment_status"]
    declared_count = series.audit.get("source_selected_count", len(series.observations))
    if metadata["source_capture_count"] > declared_count:
        raise ValidationError("Capture count exceeds selected source observations")
    bound_flag = "adjustment_status:" + BASIS
    unknown_flags = {
        "adjustment_status:not_established", "adjustment_semantics:not_established",
    }
    for observation in series.observations:
        flags = set(observation.quality_flags)
        if status == BASIS and (bound_flag not in flags or flags & unknown_flags):
            raise ValidationError("Price basis and observation adjustment flags disagree")
        if status == "not_established" and bound_flag in flags:
            raise ValidationError("Unestablished price metadata contradicts observation flags")
    if status == BASIS and set(series.warnings) & {
        "adjustment_and_total_return_semantics_not_established",
        "raw_price_adjustment_semantics_not_established",
    }:
        raise ValidationError("Price basis and adjustment warnings disagree")

def extend_price_basis_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Extend only price-basis metadata nodes; leave every old schema untouched."""
    result = copy.deepcopy(dict(schema))

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            properties = node.get("properties", {})
            if "adjustment_status" in properties:
                properties["adjustment_status"] = {
                    "type": "string", "enum": ["not_established", BASIS],
                }
                properties.update({
                    "source_price_field": {"type": "string", "enum": ["open", "high", "low", "close"]},
                    "price_adjustment_applied_by_tool": {"type": "boolean", "const": False},
                    "source_adjustment_binding": {"type": ["string", "null"], "enum": [None, BINDING]},
                    "source_adjustment_documentation": {"type": ["string", "null"], "enum": [None, DOCUMENTATION]},
                    "source_capture_count": {"type": "integer", "minimum": 0, "maximum": 10000},
                    "adjustment_vintage_status": {"type": "string", "enum": [
                        "not_established", "single_provider_capture", "mixed_provider_captures",
                    ]},
                    "source_publication_time": {"type": "null"},
                })
                if "source_observation_fields" in properties:
                    properties["source_close_adjustment_status"] = {
                        "type": "string", "enum": ["not_established", BASIS],
                    }
            for value in node.values():
                visit(value)
    visit(result)
    return result
