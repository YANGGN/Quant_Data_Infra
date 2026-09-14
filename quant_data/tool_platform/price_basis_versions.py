"""Explicit successor declarations for the close-price metadata correction."""
from __future__ import annotations

import copy
from .price_basis import extend_price_basis_schema

# (frozen calculation contract, additive metadata contract)
PRICE_BASIS_VERSIONS = {
    "market.get_price_series": ("1.0.0", "2.0.0"),
    "market.get_returns": ("2.0.0", "2.1.0"),
    "market.get_forward_returns": ("2.0.0", "2.1.0"),
    "market.technical_indicators": ("2.7.0", "2.8.0"),
    "market.cross_sectional_performance": ("2.1.0", "2.2.0"),
    "research.news_event_impact": ("1.0.0", "2.0.0"),
    "timeseries.describe": ("2.0.0", "2.1.0"),
    "timeseries.align": ("2.0.0", "2.1.0"),
    "timeseries.correlation": ("2.0.0", "2.1.0"),
    "timeseries.transform": ("2.0.0", "2.1.0"),
    "data.quality_audit": ("2.0.0", "2.1.0"),
    "stats.distribution_diagnostics": ("1.0.0", "2.0.0"),
    "stats.covariance_matrix": ("1.0.0", "2.0.0"),
    "stats.bootstrap_confidence_interval": ("1.0.0", "2.0.0"),
    "stats.principal_components": ("1.0.0", "2.0.0"),
    "econometrics.regression": ("3.0.0", "3.1.0"),
    "econometrics.rolling_regression": ("2.1.0", "2.2.0"),
    "econometrics.stationarity": ("2.1.0", "2.2.0"),
    "econometrics.structural_breaks": ("2.0.0", "2.1.0"),
    "research.point_in_time_panel": ("2.0.0", "2.1.0"),
    "research.event_study": ("2.0.0", "2.1.0"),
    "alpha.signal_diagnostics": ("2.0.0", "2.1.0"),
    "research.walk_forward_backtest": ("2.0.0", "2.1.0"),
    "research.robustness_suite": ("2.0.0", "2.1.0"),
    "stats.multiple_testing": ("2.0.0", "2.1.0"),
    "forecast.evaluate": ("2.0.0", "2.1.0"),
}
NEW_PRICE_BASIS_POLICIES = tuple(
    name for name, (base, _) in PRICE_BASIS_VERSIONS.items() if base == "1.0.0"
)


def add_price_basis_policies(policies, additive_entries):
    result = copy.deepcopy(list(policies))
    by_name = {item["tool"]: item for item in result}
    additive = {item["id"]: item for item in additive_entries}
    for name, (base_version, version) in PRICE_BASIS_VERSIONS.items():
        if base_version == "1.0.0":
            base = additive[name]
            policy = {
                "tool": name, "default_version": "1.0.0",
                "selector_field": "tool_version", "variants": [],
                "deprecations": [{
                    "version": "1.0.0", "code": "tool_version_deprecated",
                    "message": f"{name} version 1.0.0 remains available; select {version} for explicit FMP close metadata.",
                    "replacement": {"tool": name, "version": version},
                    "removal": {"status": "not_scheduled", "milestone": None},
                }],
            }
            result.append(policy)
        else:
            policy = by_name[name]
            base = next(v for v in policy["variants"] if v["version"] == base_version)
        variant = copy.deepcopy(base)
        suffix = version.split(".")[0] if version.endswith(".0.0") else version[:-2].replace(".", "_")
        graph = f"tool_platform.{name}.v{suffix}"
        variant.update({
            "version": version, "operation_version": version,
            "handler": graph, "operation_graph_id": graph,
            "compatibility": {"status": "successor_breaking_v2", "predecessor": base_version},
            "input_schema_id": f"urn:quant-data:tool:{name}:input:{version}",
            "output_schema_id": f"urn:quant-data:tool:{name}:output:{version}",
            "input_schema": extend_price_basis_schema(base["input_schema"]),
            "output_schema": extend_price_basis_schema(base["output_schema"]),
            "description": base["description"] + (
                " Carries explicit retained FMP split-only close metadata; values "
                "are unchanged, distributions are excluded, and original publication "
                "times and historical adjustment reconstruction remain unestablished."
            ),
            "assumptions": [
                item for item in base["assumptions"]
                if item not in {"adjustment_semantics_not_established", "raw_provider_native_ohlc",
                                "raw_price_adjustment_semantics_not_established",
                                "caller_supplied_stage10_raw_ohlcv_series"}
            ] + ["fmp_full_eod_close_already_split_adjusted",
                 "no_second_adjustment", "other_price_fields_not_established",
                 "retained_provider_adjustment_vintage_at_each_capture"],
        })
        if name in {"market.get_price_series", "market.technical_indicators"}:
            variant["contracts"]["returns"] = "not_applicable_provider_native_fields_basis_in_metadata"
        if name == "market.technical_indicators":
            variant["assumptions"].append("caller_supplied_stage10_provider_native_ohlcv_series")
        policy["variants"].append(variant)
    return tuple(result)


def remove_price_basis_policies(policies):
    result = []
    for original in policies:
        if original["tool"] in NEW_PRICE_BASIS_POLICIES:
            continue
        policy = copy.deepcopy(dict(original))
        pair = PRICE_BASIS_VERSIONS.get(policy["tool"])
        if pair:
            policy["variants"] = [v for v in policy["variants"] if v["version"] != pair[1]]
        result.append(policy)
    return tuple(result)
