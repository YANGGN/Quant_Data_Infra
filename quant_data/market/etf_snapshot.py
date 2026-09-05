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
) -> EtfSnapshot:
    """Select one store transaction, using existing identity/version selectors.

    Retained FMP full EOD rows have no established adjustment basis. Accordingly
    they can establish coverage and lineage, but cannot yield split-only features.
    A future reviewed source binding must supply that evidence before this guard
    can be changed. There is no public switch for asserting adjustment provenance.
    """
    cutoff = _cutoff(decision_as_of)
    if cutoff > _cutoff(now):
        raise ValidationError("ETF decision_as_of cannot be in the future")
    endpoint = completed_month_end(cutoff)
    rows: list[dict[str, object]] = []
    lineage: list[LineageRef] = []

    def empty(symbol: str, reason: str) -> dict[str, object]:
        return {
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
                values, missing = calculate_features(
                    prices, endpoint, adjustment_status=adjustment
                )
                missing_sessions = [day.isoformat() for day in expected_sessions
                                    if day not in prices]
                missing_months = [day.isoformat() for day in monthly
                                  if day is not None and day not in prices]
                gaps = ["split_adjustment_provenance_not_retained",
                        "historical_split_and_distribution_reconstruction_not_established"]
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
    material = {"tool_version": "1.0.0", "registry_source_sha256": registry.source_sha256,
                "summary": summary, "rows": rows}
    summary["snapshot_id"] = hashlib.sha256(dumps_strict(material).encode()).hexdigest()
    return EtfSnapshot(tuple(MappingProxyType(row) for row in rows),
                       MappingProxyType(summary), tuple(lineage))
