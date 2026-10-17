"""Coherent retained-only ETF input audit; no ingestion or investment authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from types import MappingProxyType
from typing import Callable, Mapping

from quant_data.contracts import LineageRef
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.registry import Registry
from quant_data.stores import StoreMap
from quant_data.temporal import TemporalValue
from .etf_calculations import (
    FEATURE_NAMES, calculate_features, completed_month_end, month_end, sessions,
)
from .stage10_series import (
    DATASET_ID, Stage10DailyPriceQuery, Stage10DailyPriceRepository,
    _quiet_market_connection, _reconcile_store_contract,
)

_UNAVAILABLE = {
    "Requested Stage 10 market instrument is unavailable": "instrument_not_retained",
    "Requested Stage 10 market instrument is unavailable at the cutoff":
        "instrument_not_available_at_decision",
}
_HISTORY_LIMIT = 400
_FMP_CLOSE_BASIS = "split_adjusted_excluding_distributions"
_FMP_CLOSE_BINDING = "fmp_full_eod_close_split_only_v1"
_FMP_CLOSE_DOCUMENTATION = "https://site.financialmodelingprep.com/faqs"


def _fmp_close_is_bound(connection, selected) -> bool:
    """Bind retained close_value to the documented FMP close, never a factor.

    Both importers preserve close under the same normalization version.
    Reject other endpoints and unknown parsers; do not infer semantics from
    a field name alone or from corporate-action rows.
    """
    if not selected:
        return False
    capture_ids = sorted({str(row["capture_id"]) for row in selected})
    placeholders = ",".join("?" for _ in capture_ids)
    captures = {str(row["capture_id"]): row for row in connection.execute(
        f"""SELECT capture_id, provider, instrument_id, endpoint_path,
                   normalization_version
            FROM stage10_daily_price_captures
            WHERE capture_id IN ({placeholders})""", capture_ids)}
    for row in selected:
        capture = captures.get(str(row["capture_id"]))
        if (capture is None or capture["provider"] != "fmp"
                or capture["instrument_id"] != row["instrument_id"]
                or capture["endpoint_path"] != "/stable/historical-price-eod/full"
                or capture["normalization_version"] != "stage10.fmp.daily_price.v1"
                or row["provider"] != "fmp"
                or row["price_variant"] != "fmp_full_eod_v1"
                or row["currency_segment"] != "provider_native"):
            return False
    return True


@dataclass(frozen=True)
class EtfSnapshot:
    rows: tuple[Mapping[str, object], ...]
    summary: Mapping[str, object]
    lineage: tuple[LineageRef, ...]


def _cutoff(value: str) -> datetime:
    parsed = TemporalValue.parse(value)
    if not isinstance(parsed.value, datetime):
        raise ValidationError("ETF decision_as_of requires a timezone-aware datetime")
    return parsed.value


def retained_etf_snapshot(
    store_map: StoreMap, registry: Registry, symbols: tuple[str, ...],
    decision_as_of: str, *, now: str, checkpoint: Callable[[], None],
    tool_version: str = "1.0.0",
) -> EtfSnapshot:
    """Select one store transaction, using existing identity/version selectors.

    Version 1 preserves its original unbound evidence gate. Version 2 binds
    the known FMP full-EOD parser's close to the provider's split-only basis.
    Neither version transforms close or applies corporate-action factors.
    Local capture availability does not establish historical publication.
    """
    if tool_version not in {"1.0.0", "2.0.0"}:
        raise ValidationError("Unsupported ETF snapshot version")
    provider_close = tool_version == "2.0.0"
    cutoff = _cutoff(decision_as_of)
    if cutoff > _cutoff(now):
        raise ValidationError("ETF decision_as_of cannot be in the future")
    endpoint = completed_month_end(cutoff)
    rows: list[dict[str, object]] = []
    lineage: list[LineageRef] = []

    def empty(symbol: str, reason: str) -> dict[str, object]:
        result = {
            "symbol": symbol, "instrument_id": None, "exchange": None,
            "display_name": None, "asset_type": None,
            "exposure": None, "legal_structure": None,
            "is_leveraged": None, "is_inverse": None,
            "classification_status": "not_retained",
            "data_quality_status": "blocked",
            "eligible_month_end": None if endpoint is None else endpoint.isoformat(),
            "feature_observation_date": None,
            "source_first_observation_date": None, "source_last_observation_date": None,
            "source_endpoint_date": None, "source_observation_count": 0,
            "source_available_from": None, "source_available_through": None,
            "source_availability_basis": "local_capture",
            "identity_available_at": None, "source_lineage_sha256": None,
            "source_adjustment_status": "not_established",
            "point_in_time_status": "not_established",
            "missing_inputs": dumps_strict([reason]),
            "missing_month_end_dates": "[]", "missing_session_dates": "[]",
            **{name: None for name in FEATURE_NAMES},
            **{name + "_missing_reason": reason for name in FEATURE_NAMES},
        }
        if provider_close:
            result.update({
                "source_price_field": "close",
                "price_adjustment_applied_by_tool": False,
                "source_adjustment_binding": None,
                "source_adjustment_documentation": None,
                "source_capture_count": 0,
                "adjustment_vintage_status": "not_established",
                "source_publication_time": None,
                "available_feature_count": 0,
            })
        return result

    if endpoint is None:
        rows = [empty(symbol, "calendar_coverage_not_established") for symbol in symbols]
    else:
        start = max(date(endpoint.year - 1, endpoint.month, 1), date(2024, 1, 1))
        expected_sessions = sessions(start, endpoint)
        monthly = []
        for lag in range(13):
            ordinal = endpoint.year * 12 + endpoint.month - 1 - lag
            monthly.append(month_end(ordinal // 12, ordinal % 12 + 1))
        repository = Stage10DailyPriceRepository(store_map, registry)
        # One descriptor-pinned immutable transaction brackets every symbol. The
        # existing gateway rejects WAL activity, replacement and changed stamps.
        with _quiet_market_connection(store_map) as connection:
            migrations = _reconcile_store_contract(connection, registry)
            for symbol in symbols:
                checkpoint()
                row = empty(symbol, "price_history_not_available_at_decision")
                query = Stage10DailyPriceQuery(
                    identifier=symbol, identifier_kind="provider_symbol",
                    start_date=start.isoformat(), end_date=endpoint.isoformat(),
                    mode="as_of", as_of=decision_as_of,
                    date_only_policy="completed_date", limit=_HISTORY_LIMIT,
                )
                try:
                    instrument, identity_warnings = repository._instrument(connection, query)
                except ValidationError as exc:
                    if str(exc) not in _UNAVAILABLE:
                        raise
                    rows.append(empty(symbol, _UNAVAILABLE[str(exc)]))
                    continue
                identity = {
                    "instrument_id": instrument["instrument_id"],
                    "exchange": instrument["exchange_code"],
                    "display_name": instrument["display_name"],
                    "asset_type": instrument["asset_type"],
                    "identity_available_at": instrument["captured_at"],
                    "classification_status": "retained_identity_only",
                }
                if instrument["asset_type"] != "etf":
                    row = empty(symbol, "instrument_type_mismatch")
                    row.update(identity)
                    rows.append(row)
                    continue
                row.update(identity)
                selected, selection_warnings = repository._as_of_rows(
                    connection, query, str(instrument["instrument_id"])
                )
                if len(selected) > _HISTORY_LIMIT:
                    raise ResourceLimitError("ETF bounded history cannot be truncated")
                for selected_row in selected:
                    repository._validate_ohlc_row(selected_row)
                series = repository._series_from_rows(
                    query=query, field="close", migrations=migrations,
                    instrument=instrument, rows=selected,
                    identity_warnings=identity_warnings,
                    selection_warnings=selection_warnings, truncated=False,
                )
                prices = {date.fromisoformat(item.period_end): item.value
                          for item in series.observations}
                # The repository checks available_at == captured_at, and selected
                # versions are cutoff filtered. Never use a later capture for an
                # earlier decision, even when its trade date is old.
                for item in series.observations:
                    if _cutoff(item.available_at) > cutoff:
                        raise ValidationError("ETF source availability exceeds decision")
                adjustment = str(series.metadata["adjustment_status"])
                if provider_close and _fmp_close_is_bound(connection, selected):
                    adjustment = _FMP_CLOSE_BASIS
                # Prices are exactly the retained provider close values. In
                # particular, do not apply splits or dividend adjustments here.
                values, missing = calculate_features(
                    prices, endpoint, adjustment_status=adjustment
                )
                missing_sessions = [day.isoformat() for day in expected_sessions
                                    if day not in prices]
                missing_months = [day.isoformat() for day in monthly
                                  if day is not None and day not in prices]
                gaps = ["split_adjustment_provenance_not_retained",
                        "historical_split_and_distribution_reconstruction_not_established"]
                if provider_close:
                    gaps = ([] if adjustment == _FMP_CLOSE_BASIS else
                            ["fmp_close_source_binding_not_established"])
                if not prices:
                    gaps.append("price_history_not_available_at_decision")
                if missing_months:
                    gaps.append("missing_month_end_observations")
                if missing_sessions:
                    gaps.append("missing_trading_session_observations")
                if any(day is None for day in monthly):
                    gaps.append("required_history_outside_calendar_coverage")
                available = [item.available_at for item in series.observations]
                row.update({
                    "source_first_observation_date": min(prices).isoformat() if prices else None,
                    "source_last_observation_date": max(prices).isoformat() if prices else None,
                    "source_endpoint_date": endpoint.isoformat() if endpoint in prices else None,
                    "source_observation_count": len(prices),
                    "source_available_from": min(available, key=_cutoff) if available else None,
                    "source_available_through": max(available, key=_cutoff) if available else None,
                    "source_lineage_sha256": series.lineage_digest,
                    "source_adjustment_status": adjustment,
                    "missing_inputs": dumps_strict(gaps),
                    "missing_month_end_dates": dumps_strict(missing_months),
                    "missing_session_dates": dumps_strict(missing_sessions),
                    **values,
                    **{name + "_missing_reason": missing.get(name) for name in FEATURE_NAMES},
                })
                if provider_close:
                    available_features = sum(value is not None for value in values.values())
                    bound = adjustment == _FMP_CLOSE_BASIS
                    capture_count = len({item["capture_id"] for item in selected})
                    row.update({
                        "source_adjustment_binding": _FMP_CLOSE_BINDING if bound else None,
                        "source_adjustment_documentation": _FMP_CLOSE_DOCUMENTATION if bound else None,
                        "source_capture_count": capture_count,
                        "adjustment_vintage_status": (
                            "single_provider_capture" if capture_count == 1 else
                            "mixed_provider_captures" if capture_count > 1 else "not_established"),
                        "available_feature_count": available_features,
                        **{name + "_missing_reason": None for name in FEATURE_NAMES
                           if values[name] is not None},
                        "data_quality_status": (
                            "ready" if available_features == len(FEATURE_NAMES) else
                            "partial" if available_features else "blocked"),
                        "feature_observation_date": endpoint.isoformat() if available_features else None,
                    })
                rows.append(row)
                lineage.append(LineageRef(dataset_id=DATASET_ID, store_role="market",
                                          semantic_id=series.lineage_digest))

    summary: dict[str, object] = {
        "registry_revision": registry.revision, "decision_as_of": decision_as_of,
        "selected_month_end": None if endpoint is None else endpoint.isoformat(),
        "feature_observation_date": None,
        "final_eligible_month_end": endpoint is not None,
        "complete": False, "truncated": False, "data_quality_status": "blocked",
        "requested_symbol_count": len(symbols), "returned_symbol_count": len(rows),
        "ready_symbol_count": 0, "point_in_time_status": "not_established",
        "point_in_time_scope": "retained_local_captures_only",
        "calendar": "US_cash_equity_2024_2026", "calendar_timezone": "America/New_York",
        "calendar_coverage": "2024-01-01/2026-12-31",
        "price_basis_required": "split_adjusted_excluding_distributions",
        "source_availability_basis": "local_capture",
        "source_publication_time": None,
        "weighting_volatility_recommendation": "realized_volatility_63",
        "weighting_volatility_decision": "consumer_selection_required",
        "lineage_sha256": dumps_strict([item.semantic_id for item in lineage]),
        "limitations": dumps_strict([
            "Retained FMP full EOD adjustment basis is not established.",
            "No split-only adjustment or historical corporate-action reconstruction is certified.",
            "Local capture time does not establish original provider publication time.",
            "Exposure, legal structure and leveraged/inverse classifications are not retained.",
            "Calendar support is bounded to 2024-2026; no observation is forward-filled.",
            "Investment approvals, clusters, caps, freshness and risk scaling belong to the consumer.",
        ]),
    }
    if provider_close:
        ready = sum(row["data_quality_status"] == "ready" for row in rows)
        feature_count = sum(int(row["available_feature_count"]) for row in rows)
        bound_count = sum(row["source_adjustment_binding"] == _FMP_CLOSE_BINDING for row in rows)
        summary.update({
            "complete": ready == len(symbols),
            "ready_symbol_count": ready,
            "available_feature_count": feature_count,
            "data_quality_status": (
                "ready" if ready == len(symbols) else "partial" if feature_count else "blocked"),
            "feature_observation_date": endpoint.isoformat() if feature_count else None,
            "price_adjustment_applied_by_tool": False,
            "source_price_field": "close",
            "source_adjustment_binding_required": _FMP_CLOSE_BINDING,
            "bound_source_symbol_count": bound_count,
            "source_adjustment_binding": _FMP_CLOSE_BINDING if bound_count == len(symbols) else None,
            "source_adjustment_documentation": _FMP_CLOSE_DOCUMENTATION if bound_count else None,
            "research_convention": "retained_provider_adjusted_close_at_each_capture",
            "limitations": dumps_strict([
                "FMP full-EOD close is already split-adjusted and excludes dividend adjustments; it is used unchanged.",
                "Each row retains the provider adjustment vintage at its own capture; mixed captures do not certify a common adjustment vintage.",
                "Historical adjustment reconstruction and original provider publication times are not established.",
                "Local capture availability and observation dates remain distinct; later captures cannot serve earlier decisions.",
                "Feature readiness describes price inputs only; exposure, legal structure and leveraged/inverse classifications remain incomplete.",
                "Calendar support is bounded to 2024-2026; no observation is forward-filled.",
                "Investment approvals, volatility selection and portfolio risk limits belong to the consumer.",
            ]),
        })
        # The successor removes the legacy app-side recommendation: selecting
        # an allocation volatility is the consuming application's responsibility.
        summary.pop("weighting_volatility_recommendation")
        summary.pop("weighting_volatility_decision")
    material = {"tool_version": tool_version, "registry_source_sha256": registry.source_sha256,
                "summary": summary, "rows": rows}
    summary["snapshot_id"] = hashlib.sha256(dumps_strict(material).encode()).hexdigest()
    return EtfSnapshot(tuple(MappingProxyType(row) for row in rows),
                       MappingProxyType(summary), tuple(lineage))
