#!/usr/bin/env python3
"""Audit every current public route through the public local CLI.

The runner uses only bin/quant-data-tools. It does not import the platform,
open SQLite, choose a store path, send a provider request, or use credentials.
It preserves ordinary JSON decode/re-encode composition on purpose.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOOL_PATH = PROJECT_ROOT / "bin" / "quant-data-tools"

# Frozen successor inventory from registry 2.56. Later additions stay outside
# this comparable audit even when it runs against a later registry revision.
TARGETS: tuple[tuple[str, str], ...] = (
    ("macro.search_series", "2.0.0"),
    ("macro.describe_series", "2.0.0"),
    ("macro.get_series", "2.0.0"),
    ("market.get_returns", "2.0.0"),
    ("market.get_forward_returns", "2.0.0"),
    ("market.technical_indicators", "2.0.0"),
    ("market.technical_indicators", "2.1.0"),
    ("market.technical_indicators", "2.2.0"),
    ("timeseries.describe", "2.0.0"),
    ("timeseries.align", "2.0.0"),
    ("timeseries.correlation", "2.0.0"),
    ("econometrics.regression", "2.0.0"),
    ("econometrics.regression", "2.1.0"),
    ("econometrics.regression", "3.0.0"),
    ("econometrics.rolling_regression", "2.0.0"),
    ("econometrics.rolling_regression", "2.1.0"),
    ("econometrics.stationarity", "2.0.0"),
    ("econometrics.stationarity", "2.1.0"),
    ("econometrics.structural_breaks", "2.0.0"),
    ("data.quality_audit", "2.0.0"),
    ("timeseries.transform", "2.0.0"),
    ("research.point_in_time_panel", "2.0.0"),
    ("research.event_study", "2.0.0"),
    ("alpha.signal_diagnostics", "2.0.0"),
    ("research.walk_forward_backtest", "2.0.0"),
    ("research.robustness_suite", "2.0.0"),
    ("stats.multiple_testing", "2.0.0"),
    ("forecast.evaluate", "2.0.0"),
    ("company.search_filings", "2.0.0"),
    ("company.get_share_count_history", "2.0.0"),
    ("market.search_instruments", "2.0.0"),
)
assert len(TARGETS) == 31
assert len(set(TARGETS)) == 31

# The requested 2.57 increment is reported separately, so the historical
# 31-variant result remains directly comparable with the prior audit.
NEW_BATCH_TARGETS: tuple[tuple[str, str], ...] = (
    ("macro.get_release_calendar", "2.0.0"),
    ("market.cross_sectional_performance", "2.0.0"),
    ("macro.revision_analysis", "2.0.0"),
    ("macro.standardize_surprises", "2.0.0"),
    ("rates.get_funding_conditions", "2.0.0"),
    ("rates.get_repo_facility_usage", "2.0.0"),
    ("rates.curve_analytics", "2.0.0"),
    ("macro.get_liquidity_snapshot", "2.0.0"),
    ("macro.get_liquidity_impulse", "2.0.0"),
    ("macro.get_credit_conditions", "2.0.0"),
    ("macro.regime_snapshot", "2.0.0"),
    ("research.liquidity_credit_state", "2.0.0"),
    ("energy.get_electricity_retail_sales", "2.0.0"),
    ("energy.get_weekly_fundamentals", "2.0.0"),
    ("company.get_fundamentals", "2.0.0"),
)
assert len(NEW_BATCH_TARGETS) == 15
assert len(set(NEW_BATCH_TARGETS)) == 15

# Eight successors added after 2.57 complete the current 2.61 inventory.
POST_257_TARGETS: tuple[tuple[str, str], ...] = (
    ("market.technical_indicators", "2.3.0"),
    ("market.technical_indicators", "2.4.0"),
    ("market.technical_indicators", "2.5.0"),
    ("market.technical_indicators", "2.6.0"),
    ("market.cross_sectional_performance", "2.1.0"),
    ("energy.get_electricity_retail_sales", "2.1.0"),
    ("energy.get_weekly_fundamentals", "2.1.0"),
    ("company.get_fundamentals", "2.1.0"),
)
assert len(POST_257_TARGETS) == 8
assert len(set(POST_257_TARGETS)) == 8

EXPECTED_DEFAULT_TOOL_COUNT = 65
EXPECTED_SUCCESSOR_COUNT = 54
EXPECTED_ROUTE_COUNT = 119

RETURN_REQUEST = {
    "identifier_kind": "provider_symbol",
    "start_date": "2026-07-01",
    "end_date": "2026-08-12",
    "mode": "latest",
    "as_of": None,
    "date_only_policy": "completed_date",
    "method": "simple",
    "horizon": 1,
    "limit": 100,
}
PRICE_REQUEST = {
    "ticker": "AAPL",
    "start_date": "2026-07-01",
    "end_date": "2026-08-12",
    "mode": "latest",
    "as_of": None,
    "date_only_policy": "completed_date",
    "limit": 100,
}


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Feeds:
    aapl: dict[str, Any]
    msft: dict[str, Any]
    ohlcv: list[dict[str, Any]]
    macro_series_id: str
    p_values: dict[str, Any]


@dataclass(frozen=True)
class Case:
    tool: str
    version: str
    basis: str
    note: str
    arguments: Callable[[Feeds], dict[str, Any]]


def _json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _cli(
    executable: Path,
    command: Sequence[str],
    body: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [str(executable), *command],
        input=None if body is None else _json(body),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f"{' '.join(command)} did not return strict JSON: {error}") from error
    if not isinstance(payload, dict):
        raise AuditError(f"{' '.join(command)} returned a non-object JSON value")
    return completed.returncode, payload


def _call(
    executable: Path,
    tool: str,
    version: str | None,
    arguments: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    envelope: dict[str, Any] = {
        "api_version": "1.0",
        "tool": tool,
        "arguments": dict(arguments),
    }
    if version is not None:
        envelope["tool_version"] = version
    return _cli(executable, ("call",), envelope)


def _result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    result = payload.get("result")
    if isinstance(result, Mapping):
        return result
    error = payload.get("error")
    if isinstance(error, Mapping):
        raise AuditError(
            f"feed failed: {error.get('code', 'tool_error')}: "
            f"{error.get('message', 'tool call failed')}"
        )
    raise AuditError("feed response has no result")


def _series(payload: Mapping[str, Any], expected: int) -> list[dict[str, Any]]:
    raw = _result(payload).get("series")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise AuditError("feed response has no typed series")
    if len(raw) != expected:
        raise AuditError(f"feed expected {expected} series, received {len(raw)}")
    return list(raw)


def _field(record: Mapping[str, Any], name: str) -> Any:
    fields = record.get("fields")
    if not isinstance(fields, list):
        return None
    for item in fields:
        if isinstance(item, Mapping) and item.get("name") == name:
            return item.get("value")
    return None


def _macro_id(payload: Mapping[str, Any]) -> str:
    records = _result(payload).get("records")
    if not isinstance(records, list):
        return "macro.gdp.real_qoq_saar_pct"
    values = [
        _field(record, "series_id")
        for record in records
        if isinstance(record, Mapping)
    ]
    if "macro.gdp.real_qoq_saar_pct" in values:
        return "macro.gdp.real_qoq_saar_pct"
    for value in values:
        if isinstance(value, str) and value:
            return value
    return "macro.gdp.real_qoq_saar_pct"


def _manifest(
    executable: Path,
) -> tuple[
    str,
    dict[tuple[str, str], Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    code, payload = _cli(executable, ("manifest",))
    if code != 0:
        raise AuditError(f"manifest failed with exit code {code}")
    revision = payload.get("registry_revision")
    tools = payload.get("tools")
    if not isinstance(revision, str) or not isinstance(tools, list):
        raise AuditError("manifest lacks registry_revision or tools")
    publics: dict[str, Mapping[str, Any]] = {}
    variants: dict[tuple[str, str], Mapping[str, Any]] = {}
    for public in tools:
        if not isinstance(public, Mapping):
            continue
        name = public.get("name")
        version = public.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        publics[name] = public
        variants[(name, version)] = public
        versions = public.get("versions")
        if versions is None:
            versions = []
        if not isinstance(versions, list):
            raise AuditError(f"manifest versions are invalid for {name}")
        for variant in versions:
            if isinstance(variant, Mapping) and isinstance(variant.get("version"), str):
                variants[(name, variant["version"])] = variant
    targets = (*TARGETS, *NEW_BATCH_TARGETS, *POST_257_TARGETS)
    missing = [target for target in targets if target not in variants]
    if missing:
        shown = ", ".join(f"{name}@{version}" for name, version in missing)
        raise AuditError(f"manifest is missing target successors: {shown}")
    if len(publics) != EXPECTED_DEFAULT_TOOL_COUNT:
        raise AuditError(f"expected 65 public defaults, observed {len(publics)}")
    successor_count = sum(version != "1.0.0" for _, version in variants)
    default_routes = {(name, public["version"]) for name, public in publics.items()}
    route_count = len(default_routes) + successor_count
    if successor_count != EXPECTED_SUCCESSOR_COUNT or route_count != EXPECTED_ROUTE_COUNT:
        raise AuditError(
            f"expected {EXPECTED_SUCCESSOR_COUNT} successors and "
            f"{EXPECTED_ROUTE_COUNT} routes, observed {successor_count} "
            f"and {route_count}"
        )
    return revision, variants, publics


def _p_values_from_records(payload: Mapping[str, Any]) -> list[float]:
    records = _result(payload).get("records")
    if not isinstance(records, list):
        raise AuditError("econometric p-value source returned no records")
    values: list[float] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        value = _field(record, "p_value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _genuine_p_value_series(
    executable: Path,
    aapl: dict[str, Any],
    msft: dict[str, Any],
) -> dict[str, Any]:
    calls = (
        ("2.1.0", {
            "series": [aapl, msft],
            "intercept": True,
            "covariance": "hc1",
            "hac_lag": 0,
            "diagnostic_lag": 2,
            "confidence_level": "0.95",
            "limit": 100,
        }),
        ("3.0.0", {
            "series": [aapl, msft],
            "analysis": "granger_causality",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": 1,
            "target_index": 0,
            "limit": 100,
        }),
        ("3.0.0", {
            "series": [aapl, msft],
            "analysis": "granger_causality",
            "deterministic": "constant",
            "lag_order": 1,
            "significance": "0.05",
            "source_index": 0,
            "target_index": 1,
            "limit": 100,
        }),
    )
    values: list[float] = []
    for version, arguments in calls:
        code, payload = _call(
            executable, "econometrics.regression", version, arguments
        )
        if code != 0:
            raise AuditError("econometric p-value feeder failed")
        values.extend(_p_values_from_records(payload))
    if len(values) != 4 or any(value < 0 or value > 1 for value in values):
        raise AuditError("expected four valid regression/Granger p-values")

    series = copy.deepcopy(aapl)
    observations = series.get("observations")
    if not isinstance(observations, list) or len(observations) < len(values):
        raise AuditError("return feeder is too short for the p-value audit")
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            raise AuditError("return feeder observation is invalid")
        if index < len(values):
            observation["value"] = values[index]
            observation["missing_reason"] = None
        else:
            observation["value"] = None
            observation["missing_reason"] = "not_selected_for_multiple_testing"
        flags = observation.get("quality_flags")
        if isinstance(flags, list):
            flags[:] = [
                flag for flag in flags
                if not flag.startswith("exact_decimal_value:")
            ]
            flags.append("derived_input:genuine_econometric_p_value")
    audit = series.get("audit")
    if isinstance(audit, dict):
        audit["missing_count"] = len(observations) - len(values)
    warnings = series.get("warnings")
    if isinstance(warnings, list):
        warnings.append("p_values_derived_from_actual_regression_and_granger_outputs")
    material = {key: value for key, value in series.items() if key != "lineage_digest"}
    rendered = json.dumps(
        material, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    series["lineage_digest"] = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return series


def _feeds(executable: Path) -> Feeds:
    calls = (
        ("aapl", "market.get_returns", "2.0.0", {**RETURN_REQUEST, "identifier": "AAPL"}),
        ("msft", "market.get_returns", "2.0.0", {**RETURN_REQUEST, "identifier": "MSFT"}),
        ("price", "market.get_price_series", None, PRICE_REQUEST),
        ("volume", "market.get_volume_series", None, PRICE_REQUEST),
        ("macro", "macro.search_series", "2.0.0", {"query": "gdp", "limit": 20}),
    )
    responses: dict[str, dict[str, Any]] = {}
    for label, tool, version, arguments in calls:
        code, response = _call(executable, tool, version, arguments)
        if code != 0:
            raise AuditError(f"{label} feeder failed with exit code {code}")
        responses[label] = response
    for label in ("aapl", "msft", "price", "volume"):
        truncation = _result(responses[label]).get("truncation")
        if isinstance(truncation, Mapping) and truncation.get("applied"):
            raise AuditError(
                f"{label} feeder is truncated; choose a smaller public date range"
            )
    aapl = _series(responses["aapl"], 1)[0]
    msft = _series(responses["msft"], 1)[0]
    return Feeds(
        aapl=aapl,
        msft=msft,
        ohlcv=[*_series(responses["price"], 4), *_series(responses["volume"], 1)],
        macro_series_id=_macro_id(responses["macro"]),
        p_values=_genuine_p_value_series(executable, aapl, msft),
    )


def _research(
    series: list[dict[str, Any]], parameters: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "series": series,
        "parameters": parameters,
        "limit": 100,
        "as_of": None,
        "unsafe_ok": True,
    }


def _cases() -> tuple[Case, ...]:
    real = "actual AAPL/MSFT trailing simple returns"
    contract_only = "actual returns plus contract-only research role"
    return (
        Case("macro.search_series", "2.0.0", "canonical macro store",
             "Retained macro identifier discovery.",
             lambda _: {"query": "gdp", "limit": 20}),
        Case("macro.describe_series", "2.0.0", "canonical macro store",
             "Describe the public-discovered GDP identifier.",
             lambda f: {"series_id": f.macro_series_id, "limit": 1}),
        Case("macro.get_series", "2.0.0", "canonical macro store",
             "Latest retained GDP series.",
             lambda f: {"series_id": f.macro_series_id, "mode": "latest",
                        "as_of": None, "date_only_policy": "completed_date",
                        "limit": 100, "start_date": None, "end_date": None}),
        Case("market.get_returns", "2.0.0", "canonical market store",
             "Actual AAPL rows in the populated window.",
             lambda _: {**RETURN_REQUEST, "identifier": "AAPL"}),
        Case("market.get_forward_returns", "2.0.0", "canonical market store",
             "Actual AAPL rows; forward values remain outcome labels.",
             lambda _: {**RETURN_REQUEST, "identifier": "AAPL"}),
        Case("market.technical_indicators", "2.0.0", "actual AAPL OHLCV",
             "Accumulation/distribution uses high, low, close, and volume.",
             lambda f: {"series": f.ohlcv, "indicator": "accumulation_distribution",
                        "window": None, "fast_window": None, "slow_window": None,
                        "signal_window": None, "standard_deviation_multiplier": None,
                        "limit": 100}),
        Case("market.technical_indicators", "2.1.0", "actual AAPL OHLCV",
             "Bounded SuperTrend AI over retained bars.",
             lambda f: {"series": f.ohlcv, "indicator": "supertrend_ai",
                        "window": 10, "fast_window": None, "slow_window": None,
                        "signal_window": None, "standard_deviation_multiplier": None,
                        "minimum_factor": 1, "maximum_factor": 2, "factor_step": 0.5,
                        "performance_memory": 10, "cluster": "best", "limit": 100}),
        Case("market.technical_indicators", "2.2.0", "actual AAPL OHLCV",
             "Causal swing-structure calculation over retained bars.",
             lambda f: {"series": f.ohlcv, "indicator": "swing_structure_forecast",
                        "window": 10, "fast_window": None, "slow_window": None,
                        "signal_window": None, "standard_deviation_multiplier": None,
                        "minimum_factor": None, "maximum_factor": None, "factor_step": None,
                        "performance_memory": None, "cluster": None, "sample_count": 3,
                        "aggregation_method": "average", "limit": 100}),
        Case("timeseries.describe", "2.0.0", real,
             "Ordinary JSON composition from the AAPL CLI result.",
             lambda f: {"series": f.aapl, "limit": 100}),
        Case("timeseries.align", "2.0.0", real,
             "Ordinary JSON composition from AAPL and MSFT CLI results.",
             lambda f: {"series": [f.aapl, f.msft], "join": "inner", "limit": 100}),
        Case("timeseries.correlation", "2.0.0", real,
             "Joint-complete AAPL/MSFT retained sample.",
             lambda f: {"series": [f.aapl, f.msft], "limit": 100}),
        Case("econometrics.regression", "2.0.0", real,
             "Classical OLS over actual retained returns.",
             lambda f: {"series": [f.aapl, f.msft], "intercept": True,
                        "covariance": "classical_homoskedastic",
                        "confidence_level": "0.95", "limit": 100}),
        Case("econometrics.regression", "2.1.0", real,
             "HC1 OLS with fixed residual diagnostics.",
             lambda f: {"series": [f.aapl, f.msft], "intercept": True,
                        "covariance": "hc1", "hac_lag": 0, "diagnostic_lag": 2,
                        "confidence_level": "0.95", "limit": 100}),
        Case("econometrics.regression", "3.0.0", real,
             "Fixed-lag VAR, not a forecasting claim.",
             lambda f: {"series": [f.aapl, f.msft], "analysis": "vector_autoregression",
                        "deterministic": "constant", "lag_order": 1,
                        "significance": "0.05", "source_index": -1,
                        "target_index": -1, "limit": 100}),
        Case("econometrics.rolling_regression", "2.0.0", real,
             "Fixed eight-observation rolling classical OLS.",
             lambda f: {"series": [f.aapl, f.msft], "window": 8, "intercept": True,
                        "covariance": "classical_homoskedastic",
                        "confidence_level": "0.95", "limit": 100}),
        Case("econometrics.rolling_regression", "2.1.0", real,
             "Fixed eight-observation rolling HC1 OLS.",
             lambda f: {"series": [f.aapl, f.msft], "window": 8, "intercept": True,
                        "covariance": "hc1", "hac_lag": 0, "diagnostic_lag": 2,
                        "confidence_level": "0.95", "limit": 100}),
        Case("econometrics.stationarity", "2.0.0", real,
             "Fixed-lag ADF over actual AAPL returns.",
             lambda f: {"series": f.aapl, "deterministic": "constant", "lag": 0,
                        "significance": "0.05", "limit": 100}),
        Case("econometrics.stationarity", "2.1.0", real,
             "Fixed-lag ADF and level-KPSS over actual AAPL returns.",
             lambda f: {"series": f.aapl, "deterministic": "constant",
                        "adf_lag": 0, "kpss_lag": 0, "significance": "0.05",
                        "limit": 100}),
        Case("econometrics.structural_breaks", "2.0.0", real,
             "One declared break at row 12; no automatic break search.",
             lambda f: {"series": [f.aapl, f.msft], "intercept": True,
                        "break_index": 12, "significance": "0.05", "limit": 100}),
        Case("data.quality_audit", "2.0.0", real,
             "Coverage and lineage diagnostics over real return feeds.",
             lambda f: {"series": [f.aapl, f.msft], "limit": 100}),
        Case("timeseries.transform", "2.0.0", real,
             "Five-observation rolling mean over actual AAPL returns.",
             lambda f: {"series": f.aapl, "operation": "rolling_statistic",
                        "rolling_statistic": "mean", "window": 5, "max_lag": None,
                        "ljung_box_lag": None, "limit": 100}),
        Case("research.point_in_time_panel", "2.0.0", real,
             "Actual-return alignment; no invented availability evidence.",
             lambda f: _research([f.aapl, f.msft], [{"name": "join", "value": "inner"}])),
        Case("research.event_study", "2.0.0", contract_only,
             "Contract-only: event_index=1 is not a retained real event.",
             lambda f: _research([f.aapl], [{"name": "event_index", "value": 1},
                                           {"name": "pre", "value": 0},
                                           {"name": "post", "value": 0}])),
        Case("alpha.signal_diagnostics", "2.0.0", contract_only,
             "Contract-only: two returns are not an alpha signal/outcome pair.",
             lambda f: _research([f.aapl, f.msft], [])),
        Case("research.walk_forward_backtest", "2.0.0", contract_only,
             "Contract-only: returns are not a prediction stream or strategy.",
             lambda f: _research([f.aapl, f.msft], [])),
        Case("research.robustness_suite", "2.0.0", contract_only,
             "Contract-only: no model variants or robustness design exist.",
             lambda f: _research([f.aapl], [])),
        Case("stats.multiple_testing", "2.0.0", "genuine econometric p-values",
             "Four p-values from actual HC1 OLS and bidirectional Granger tests.",
             lambda f: _research([f.p_values], [])),
        Case("forecast.evaluate", "2.0.0", contract_only,
             "Contract-only: AAPL/MSFT returns are not forecast pairs.",
             lambda f: _research([f.aapl, f.msft], [])),
        Case("company.search_filings", "2.0.0", "canonical company store",
             "First bounded AAPL filing page.",
             lambda _: {"query": "0000320193", "as_of": None, "cursor": None, "limit": 100}),
        Case("company.get_share_count_history", "2.0.0", "canonical company store",
             "Reviewed AAPL share-count history.",
             lambda _: {"cik": "0000320193", "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 1000}),
        Case("market.search_instruments", "2.0.0", "canonical market store",
             "Literal AAPL retained-identity search.",
             lambda _: {"query": "AAPL", "asset_type": "equity", "cursor": None,
                        "limit": 100}),
    )


def _new_cases() -> tuple[Case, ...]:
    """Bounded real-data calls for the 2.57 investment-analysis increment."""

    macro = "canonical macro store"
    return (
        Case("macro.get_release_calendar", "2.0.0", macro,
             "First bounded retained calendar page; cursor continuation is not required here.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 100,
                        "start_date": "2026-07-01", "end_date": "2026-08-28",
                        "event_name": None, "cursor": None}),
        Case("market.cross_sectional_performance", "2.0.0", "actual AAPL/MSFT prices",
             "Explicit two-symbol endpoint-return comparison; no portfolio semantics.",
             lambda _: {"tickers": ["AAPL", "MSFT"], "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 100,
                        "start_date": "2026-07-01", "end_date": "2026-08-12"}),
        Case("macro.revision_analysis", "2.0.0", macro,
             "Retained official GDP vintages only; no unreconciled generic fallback.",
             lambda _: {"series_id": "macro.gdp.real_qoq_saar_pct",
                        "start_date": None, "end_date": None, "limit": 500}),
        Case("macro.standardize_surprises", "2.0.0", macro,
             "Retained U.S. CPI headline month-over-month surprises.",
             lambda _: {"kind": "us_cpi_headline_mom", "release_stage": None,
                        "start_date": None, "end_date": None, "limit": 500}),
        Case("rates.get_funding_conditions", "2.0.0", macro,
             "Raw EFFR and SOFR comparison; no policy inference.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "spread_left": "EFFR", "spread_right": "SOFR", "limit": 20}),
        Case("rates.get_repo_facility_usage", "2.0.0", macro,
             "Observed ON RRP and SRF usage only.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "limit": 20}),
        Case("rates.curve_analytics", "2.0.0", macro,
             "Raw 10Y minus 2Y retained Treasury curve values.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "spread_left_tenor": "10Y", "spread_right_tenor": "2Y",
                        "limit": 20}),
        Case("macro.get_liquidity_snapshot", "2.0.0", macro,
             "Observed liquidity components, with no composite score.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "limit": 20}),
        Case("macro.get_liquidity_impulse", "2.0.0", macro,
             "Bounded 2026 retained-period liquidity deltas.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "start_date": "2026-01-01",
                        "end_date": "2026-08-01", "limit": 20}),
        Case("macro.get_credit_conditions", "2.0.0", macro,
             "Observed Chicago Fed, BIS, and CMDI components, no score.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "limit": 20}),
        Case("macro.regime_snapshot", "2.0.0", macro,
             "Direct NBER recession-state observation, no inferred regime.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "include_context": False, "limit": 20}),
        Case("research.liquidity_credit_state", "2.0.0", macro,
             "Descriptive liquidity/credit state; no score or classification.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "observation_date": None,
                        "include_context": False, "limit": 20}),
        Case("energy.get_electricity_retail_sales", "2.0.0", macro,
             "Retained monthly electricity sales facts; no demand forecast.",
             lambda _: {"metric": "sales", "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "start_date": None,
                        "end_date": None, "limit": 100}),
        Case("energy.get_weekly_fundamentals", "2.0.0", macro,
             "Retained weekly EIA U.S. petroleum-stock observations only.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "start_date": None,
                        "end_date": None, "limit": 100}),
        Case("company.get_fundamentals", "2.0.0", "canonical company store",
             "Normalized AAPL SEC fundamentals, preserving retained metric semantics.",
             lambda _: {"cik": "0000320193", "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "metric_codes": [],
                        "start_date": None, "end_date": None, "limit": 1000}),
    )




def _post_cases() -> tuple[Case, ...]:
    """Actual-data calls for the successors added after registry 2.57."""

    return (
        Case("market.technical_indicators", "2.3.0", "actual AAPL OHLCV",
             "KDJ over the retained daily bar grid.",
             lambda f: {"series": f.ohlcv, "indicator": "kdj",
                        "window": 9, "fast_window": None, "slow_window": None,
                        "signal_window": 3, "standard_deviation_multiplier": None,
                        "minimum_factor": None, "maximum_factor": None,
                        "factor_step": None, "performance_memory": None,
                        "cluster": None, "sample_count": None,
                        "aggregation_method": None, "limit": 100}),
        Case("market.technical_indicators", "2.4.0", "actual AAPL OHLCV",
             "Williams Vix Fix over retained closes.",
             lambda f: {"series": f.ohlcv, "indicator": "williams_vix_fix",
                        "window": 22, "fast_window": None, "slow_window": None,
                        "signal_window": 20, "standard_deviation_multiplier": 2,
                        "minimum_factor": None, "maximum_factor": None,
                        "factor_step": None, "performance_memory": None,
                        "cluster": None, "sample_count": None,
                        "aggregation_method": None, "percentile_window": 50,
                        "percentile_high_factor": 0.85,
                        "percentile_low_factor": 1.01, "limit": 100}),
        Case("market.technical_indicators", "2.5.0", "actual AAPL OHLCV",
             "WaveTrend with crosses over retained HLC bars.",
             lambda f: {"series": f.ohlcv, "indicator": "wavetrend_crosses",
                        "window": 10, "fast_window": None, "slow_window": None,
                        "signal_window": 21, "standard_deviation_multiplier": None,
                        "minimum_factor": None, "maximum_factor": None,
                        "factor_step": None, "performance_memory": None,
                        "cluster": None, "sample_count": None,
                        "aggregation_method": None, "percentile_window": None,
                        "percentile_high_factor": None,
                        "percentile_low_factor": None, "limit": 100}),
        Case("market.technical_indicators", "2.6.0", "actual AAPL OHLCV",
             "Parabolic SAR over retained HLC bars.",
             lambda f: {"series": f.ohlcv, "indicator": "parabolic_sar",
                        "window": None, "fast_window": None, "slow_window": None,
                        "signal_window": None, "standard_deviation_multiplier": None,
                        "minimum_factor": None, "maximum_factor": None,
                        "factor_step": None, "performance_memory": None,
                        "cluster": None, "sample_count": None,
                        "aggregation_method": None, "percentile_window": None,
                        "percentile_high_factor": None,
                        "percentile_low_factor": None, "start": 0.02,
                        "increment": 0.02, "maximum": 0.2, "limit": 100}),
        Case("market.cross_sectional_performance", "2.1.0",
             "actual AAPL/MSFT prices",
             "Breadth, realized volatility, relative return, and rolling beta.",
             lambda _: {"tickers": ["AAPL", "MSFT"],
                        "benchmark_ticker": "MSFT", "window": 20,
                        "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 100,
                        "start_date": "2026-07-01", "end_date": "2026-08-20"}),
        Case("energy.get_electricity_retail_sales", "2.1.0",
             "canonical macro store",
             "Exact 12-observation seasonal comparison.",
             lambda _: {"metric": "sales", "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 100,
                        "start_date": None, "end_date": None}),
        Case("energy.get_weekly_fundamentals", "2.1.0",
             "canonical macro store",
             "Exact 52-observation seasonal comparison.",
             lambda _: {"mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date", "limit": 100,
                        "start_date": None, "end_date": None}),
        Case("company.get_fundamentals", "2.1.0",
             "canonical company store",
             "Reviewed same-period AAPL net-margin and leverage ratios.",
             lambda _: {"cik": "0000320193", "mode": "latest", "as_of": None,
                        "date_only_policy": "completed_date",
                        "ratio_codes": ["net_margin", "liabilities_to_assets"],
                        "start_date": None, "end_date": None, "limit": 1000}),
    )
def _receipt(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only a small, public, path-free receipt subset in the audit."""

    raw = payload.get("receipt")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("registry_revision", "tool_version", "operation_graph_id"):
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = value
    stores = raw.get("logical_stores")
    if isinstance(stores, list) and all(isinstance(item, str) for item in stores):
        result["logical_stores"] = list(stores)
    return result


def _summary(code: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        error = payload.get("error")
        error = error if isinstance(error, Mapping) else {}
        return {
            "transport": "error",
            "exit_code": code,
            "error_code": error.get("code", "unknown_error"),
            "error_message": error.get("message", "No structured error"),
            "receipt": _receipt(payload),
        }
    records = result.get("records")
    series = result.get("series")
    observations = []
    if isinstance(series, list):
        for item in series:
            if isinstance(item, Mapping) and isinstance(item.get("observations"), list):
                observations.append(len(item["observations"]))
    truncation = result.get("truncation")
    truncation = truncation if isinstance(truncation, Mapping) else {}
    return {
        "transport": "success" if code == 0 else "nonzero_with_result",
        "exit_code": code,
        "result_status": result.get("status", "unknown"),
        "record_count": len(records) if isinstance(records, list) else None,
        "series_count": len(series) if isinstance(series, list) else None,
        "observation_counts": observations,
        "truncated": truncation.get("applied"),
        "has_more": truncation.get("has_more"),
        "receipt": _receipt(payload),
    }


def _outcome(summary: Mapping[str, Any]) -> str:
    if summary.get("transport") != "success":
        return str(summary.get("error_code", "transport_error"))
    status = summary.get("result_status")
    if status == "not_established":
        return "not_established"
    if status != "ok":
        return str(status)
    if summary.get("record_count") == 0 and summary.get("series_count") == 0:
        return "successful_empty"
    return "useful_or_derived_output"


def _native_statistics(feeds: Feeds) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("stats.distribution_diagnostics", {"series": feeds.aapl, "limit": 100}),
        ("stats.bootstrap_confidence_interval", {
            "series": feeds.aapl, "statistic": "mean", "seed": 20260828,
            "replicates": 200, "confidence_level": "0.95", "limit": 100,
        }),
        ("stats.covariance_matrix", {"series": [feeds.aapl, feeds.msft], "limit": 100}),
        ("stats.principal_components", {
            "series": [feeds.aapl, feeds.msft], "basis": "correlation",
            "components": 2, "include_scores": True, "limit": 100,
        }),
    )




def _default_arguments(
    name: str,
    public: Mapping[str, Any],
    feeds: Feeds,
) -> tuple[dict[str, Any], str, str]:
    examples = public.get("examples")
    if not isinstance(examples, list) or not examples or not isinstance(examples[0], Mapping):
        raise AuditError(f"default route {name} has no public example")
    arguments = copy.deepcopy(dict(examples[0]))
    native = dict(_native_statistics(feeds))
    if name in native:
        return native[name], "actual AAPL/MSFT returns", "Native current-data statistic."

    if name == "market.get_available_ticker":
        return {}, "canonical market store", "Current retained ticker discovery."
    if name in {"market.get_price_series", "market.get_volume_series"}:
        return dict(PRICE_REQUEST), "canonical market store", "Bounded actual AAPL rows."
    if name == "macro.get_release_calendar":
        return {"mode": "latest", "as_of": None,
                "date_only_policy": "completed_date", "limit": 1000}, (
                    "canonical macro store"
                ), "First bounded retained release-calendar selection."
    if name == "company.search_issuers":
        return {"query": "0000320193", "as_of": None, "limit": 20}, (
            "canonical company store"
        ), "Actual AAPL issuer lookup."
    if name == "company.search_filings":
        return {"query": "0000320193", "as_of": None, "limit": 500}, (
            "canonical company store"
        ), "Maximum v1 AAPL filing selection."
    if name.startswith("company.get_"):
        arguments["identifiers"] = ["0000320193"]
        if name == "company.get_fundamentals":
            arguments["limit"] = 1000
        return arguments, "canonical company store", "Actual AAPL company selection."
    if name == "news.search":
        arguments["query"] = "Apple"
        return arguments, "canonical news store", "Actual Apple news selection."
    if name.startswith("options.search_"):
        arguments["query"] = "AAPL"
        return arguments, "canonical options store", "Actual AAPL options selection."
    if name.startswith("options."):
        arguments["identifiers"] = ["AAPL"]
        return arguments, "canonical options store", "Actual AAPL options selection."

    current_single = {
        "timeseries.transform",
        "timeseries.describe",
        "econometrics.stationarity",
        "data.quality_audit",
        "research.event_study",
        "research.robustness_suite",
        "stats.multiple_testing",
    }
    current_pair = {
        "timeseries.align",
        "timeseries.correlation",
        "econometrics.regression",
        "econometrics.rolling_regression",
        "research.point_in_time_panel",
        "alpha.signal_diagnostics",
        "research.walk_forward_backtest",
        "forecast.evaluate",
    }
    if name in current_single:
        arguments["series"] = feeds.p_values if name == "stats.multiple_testing" else feeds.aapl
        return arguments, "actual Stage 10 returns", "Frozen v1 composability check."
    if name in current_pair:
        arguments["series"] = [feeds.aapl, feeds.msft]
        return arguments, "actual AAPL/MSFT returns", "Frozen v1 composability check."

    if name == "macro.release_surprises":
        return arguments, "canonical macro store", "Retained GDP release-surprise selection."
    if name.startswith(("macro.", "rates.", "energy.")):
        return arguments, "canonical macro store", "Frozen default behavior on its public input."
    if name.startswith("market."):
        return arguments, "canonical market store", "Frozen default behavior on its public input."
    return arguments, "public compatibility input", "Frozen default behavior."


def _execute_defaults(
    executable: Path,
    publics: Mapping[str, Mapping[str, Any]],
    feeds: Feeds,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, public in publics.items():
        version = public.get("version")
        if version != "1.0.0":
            raise AuditError(f"default route {name} unexpectedly selected {version}")
        arguments, basis, note = _default_arguments(name, public, feeds)
        code, payload = _call(executable, name, version, arguments)
        summary = _summary(code, payload)
        results.append({
            "tool": name,
            "tool_version": version,
            "input_basis": basis,
            "note": note,
            "outcome": _outcome(summary),
            "summary": summary,
        })
    return results
def _execute_cases(
    executable: Path,
    discovered: Mapping[tuple[str, str], Mapping[str, Any]],
    feeds: Feeds,
    cases: Sequence[Case],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        if discovered[(case.tool, case.version)].get("version") != case.version:
            raise AuditError(f"manifest selected the wrong version for {case.tool}")
        code, payload = _call(executable, case.tool, case.version, case.arguments(feeds))
        summary = _summary(code, payload)
        results.append({
            "tool": case.tool,
            "tool_version": case.version,
            "input_basis": case.basis,
            "note": case.note,
            "outcome": _outcome(summary),
            "summary": summary,
        })
    return results


def run_audit(executable: Path, expected_registry: str | None) -> dict[str, Any]:
    if not executable.is_file():
        raise AuditError(f"tool executable is unavailable: {executable}")
    revision, discovered, publics = _manifest(executable)
    if expected_registry is not None and revision != expected_registry:
        raise AuditError(
            f"expected registry {expected_registry}, observed {revision}; rerun after integration"
        )
    feeds = _feeds(executable)
    legacy_cases = _cases()
    new_cases = _new_cases()
    post_cases = _post_cases()
    if tuple((case.tool, case.version) for case in legacy_cases) != TARGETS:
        raise AssertionError("legacy audit cases do not match the frozen target inventory")
    if tuple((case.tool, case.version) for case in new_cases) != NEW_BATCH_TARGETS:
        raise AssertionError("new audit cases do not match the 2.57 target inventory")
    if tuple((case.tool, case.version) for case in post_cases) != POST_257_TARGETS:
        raise AssertionError("post-2.57 cases do not match the current target inventory")

    legacy_results = _execute_cases(executable, discovered, feeds, legacy_cases)
    default_results = _execute_defaults(executable, publics, feeds)
    new_results = _execute_cases(executable, discovered, feeds, new_cases)

    post_results = _execute_cases(executable, discovered, feeds, post_cases)
    final_revision, _, _ = _manifest(executable)
    if final_revision != revision:
        raise AuditError(f"registry changed during audit: {revision} to {final_revision}")

    all_results = [*default_results, *legacy_results, *new_results, *post_results]
    if len(all_results) != EXPECTED_ROUTE_COUNT:
        raise AuditError(f"expected {EXPECTED_ROUTE_COUNT} audited routes, observed {len(all_results)}")
    return {
        "audit": "current_public_routes_actual_data",
        "registry_revision": revision,
        "route_count": len(all_results),
        "default_count": len(default_results),
        "default_routes": default_results,
        "target_variant_count": len(legacy_results),
        "target_variants": legacy_results,
        "new_batch_count": len(new_results),
        "new_batch_variants": new_results,
        "post_2_57_count": len(post_results),
        "post_2_57_variants": post_results,
        "method": {
            "boundary": "bin/quant-data-tools call only",
            "direct_database_access": False,
            "immutable_read_procedure": "host-owned quiet immutable canonical readers",
            "return_feed": {
                "tickers": ["AAPL", "MSFT"],
                "start_date": RETURN_REQUEST["start_date"],
                "end_date": RETURN_REQUEST["end_date"],
                "method": "simple",
                "horizon": 1,
            },
            "p_value_feed": "four genuine HC1 OLS and bidirectional Granger p-values",
            "technical_feed": "actual AAPL OHLCV from public price and volume readers",
            "composition": "ordinary stdlib JSON parse and reserialization",
        },
    }

def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _shape(summary: Mapping[str, Any]) -> str:
    if summary.get("transport") != "success":
        return f"error:{summary.get('error_code', 'unknown_error')}"
    parts = [f"status:{summary.get('result_status', 'unknown')}"]
    records = summary.get("record_count")
    series = summary.get("series_count")
    if records is not None:
        parts.append(f"records:{records}")
    if series is not None:
        parts.append(f"series:{series}")
    observations = summary.get("observation_counts")
    if isinstance(observations, list) and observations:
        parts.append("observations:" + ",".join(str(item) for item in observations))
    if summary.get("truncated") is True:
        parts.append("truncated")
    return "; ".join(parts)


def _append_matrix(
    lines: list[str],
    heading: str,
    values: Sequence[Mapping[str, Any]],
) -> None:
    counts = Counter(str(item.get("outcome", "unknown")) for item in values)
    lines.extend([
        "",
        heading,
        "",
        "| Observed outcome | Tools |",
        "| --- | ---: |",
    ])
    for outcome, count in sorted(counts.items()):
        lines.append(f"| {_cell(outcome)} | {count} |")
    lines.extend([
        "",
        "| Tool | Version | Input basis | Outcome | Concise result | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for item in values:
        summary = item.get("summary")
        summary = summary if isinstance(summary, Mapping) else {}
        lines.append(
            "| " + " | ".join((
                _cell(item.get("tool", "")),
                _cell(item.get("tool_version", "")),
                _cell(item.get("input_basis", "")),
                _cell(item.get("outcome", "")),
                _cell(_shape(summary)),
                _cell(item.get("note", "")),
            )) + " |"
        )


def render_markdown(report: Mapping[str, Any]) -> str:
    defaults = report.get("default_routes")
    legacy = report.get("target_variants")
    new_batch = report.get("new_batch_variants")
    post = report.get("post_2_57_variants")
    groups = (defaults, legacy, new_batch, post)
    if not all(isinstance(group, list) for group in groups):
        raise AuditError("report has an invalid result shape")
    default_rows = [item for item in defaults if isinstance(item, Mapping)]
    legacy_rows = [item for item in legacy if isinstance(item, Mapping)]
    new_rows = [item for item in new_batch if isinstance(item, Mapping)]
    post_rows = [item for item in post if isinstance(item, Mapping)]
    all_rows = [*default_rows, *legacy_rows, *new_rows, *post_rows]
    counts = Counter(str(item.get("outcome", "unknown")) for item in all_rows)
    lines = [
        f"# Current public-tool actual-data audit — {date.today().isoformat()}",
        "",
        "## Status and scope",
        "",
        (
            f"This checkpoint called all {len(all_rows)} public routes advertised by registry "
            f"{report['registry_revision']}: {len(default_rows)} logical defaults and "
            f"{len(all_rows) - len(default_rows)} explicit successors. Every route was invoked "
            "separately through bin/quant-data-tools call."
        ),
        "",
        (
            "The runner never selects a database path, opens SQLite, sends a provider request, "
            "or supplies a credential. Store-backed calls use the host-owned quiet immutable "
            "reader. AAPL/MSFT statistical inputs are actual non-truncated Stage 10 returns, and "
            "technical inputs are actual AAPL OHLCV."
        ),
        "",
        (
            "stats.multiple_testing@2.0.0 receives four genuine p-values produced first by actual "
            "HC1 OLS and bidirectional Granger calls. Its Benjamini-Hochberg output is therefore "
            "statistically exercised, although its current public input envelope is still shaped "
            "like a return series."
        ),
        "",
        "## Overall outcomes",
        "",
        "| Observed outcome | Routes |",
        "| --- | ---: |",
    ]
    for outcome, count in sorted(counts.items()):
        lines.append(f"| {_cell(outcome)} | {count} |")
    _append_matrix(lines, "## Logical defaults (v1 behavior)", default_rows)
    _append_matrix(lines, "## Pre-2.57 successors", legacy_rows)
    _append_matrix(lines, "## Registry 2.57 investment-analysis successors", new_rows)
    _append_matrix(lines, "## Post-2.57 successors", post_rows)
    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- A successful call establishes observed public-contract behavior over retained local data only.",
        "- Empty results describe current coverage, not completeness of the underlying source.",
        "- Contract-only event, signal, forecast, and backtest calls do not establish economic validity.",
        "- Frozen v1 incompatibilities are retained for compatibility; their working successors are audited separately.",
        "- A returned error is retained as a route-specific finding; this audit does not hide or retry it.",
        "",
    ])
    return "\n".join(lines)

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool-path", type=Path, default=DEFAULT_TOOL_PATH)
    parser.add_argument("--expected-registry")
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_audit(args.tool_path.resolve(), args.expected_registry)
        if args.markdown is not None:
            args.markdown.write_text(render_markdown(report), encoding="utf-8")
        sys.stdout.write(json.dumps(report, allow_nan=False, indent=2, sort_keys=True) + "\n")
        return 0
    except AuditError as error:
        sys.stderr.write(f"audit failed: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
