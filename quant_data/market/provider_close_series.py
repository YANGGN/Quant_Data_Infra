"""Provider-evidenced successor for retained Stage 10 close series."""
from __future__ import annotations

from dataclasses import replace
import sqlite3
from typing import Any, Mapping, Sequence

from ..contracts import TimeSeries
from .stage10_series import (
    CURRENCY_SEGMENT, OHLC_FIELDS, PRICE_VARIANT, PROVIDER,
    Stage10DailyPriceQuery, Stage10DailyPriceRepository,
)

_BINDING_KEY = "_provider_close_binding_established"
_CAPTURE_QUERY_BATCH_SIZE = 500
_FMP_CLOSE_BASIS = "split_adjusted_excluding_distributions"
_FMP_CLOSE_BINDING = "fmp_full_eod_close_split_only_v1"
_FMP_CLOSE_DOCUMENTATION = "https://site.financialmodelingprep.com/faqs"
_FMP_FULL_EOD_ENDPOINT = "/stable/historical-price-eod/full"
_FMP_NORMALIZATION_VERSION = "stage10.fmp.daily_price.v1"
_ADJUSTMENT_STATUS_FLAG = "adjustment_status:" + _FMP_CLOSE_BASIS


class ProviderClosePriceRepository(Stage10DailyPriceRepository):
    """Expose documented FMP split-only close without transforming values."""

    @staticmethod
    def _with_capture_binding(
        connection: sqlite3.Connection, rows: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        """Attach fail-closed capture evidence with bounded batched queries."""
        if not rows:
            return []
        capture_ids = sorted({str(row["capture_id"]) for row in rows})
        captures: dict[str, Mapping[str, Any]] = {}
        for start in range(0, len(capture_ids), _CAPTURE_QUERY_BATCH_SIZE):
            batch = capture_ids[start:start + _CAPTURE_QUERY_BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            for capture in connection.execute(
                f"""SELECT capture_id, provider, instrument_id, endpoint_path,
                            normalization_version
                     FROM stage10_daily_price_captures
                     WHERE capture_id IN ({placeholders})""", batch,
            ):
                captures[str(capture["capture_id"])] = capture
        result: list[Mapping[str, Any]] = []
        for row in rows:
            capture = captures.get(str(row["capture_id"]))
            bound = (
                capture is not None
                and str(capture["provider"]) == PROVIDER
                and str(capture["instrument_id"]) == str(row["instrument_id"])
                and str(capture["endpoint_path"]) == _FMP_FULL_EOD_ENDPOINT
                and str(capture["normalization_version"]) == _FMP_NORMALIZATION_VERSION
                and str(row["provider"]) == PROVIDER
                and str(row["price_variant"]) == PRICE_VARIANT
                and str(row["currency_segment"]) == CURRENCY_SEGMENT
            )
            result.append({**dict(row), _BINDING_KEY: bound})
        return result

    @staticmethod
    def _unbound_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return [{**dict(row), _BINDING_KEY: False} for row in rows]

    @staticmethod
    def _latest_rows(
        connection: sqlite3.Connection, query: Stage10DailyPriceQuery,
        instrument_id: str,
    ) -> list[Mapping[str, Any]]:
        rows = Stage10DailyPriceRepository._latest_rows(connection, query, instrument_id)
        return (
            ProviderClosePriceRepository._with_capture_binding(connection, rows[:query.limit])
            + ProviderClosePriceRepository._unbound_rows(rows[query.limit:])
        )

    @staticmethod
    def _as_of_rows(
        connection: sqlite3.Connection, query: Stage10DailyPriceQuery,
        instrument_id: str,
    ) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
        rows, warnings = Stage10DailyPriceRepository._as_of_rows(connection, query, instrument_id)
        return (
            ProviderClosePriceRepository._with_capture_binding(connection, rows[:query.limit])
            + ProviderClosePriceRepository._unbound_rows(rows[query.limit:]),
            warnings,
        )

    def _series_from_rows(
        self, *, query: Stage10DailyPriceQuery, field: str,
        migrations: list[sqlite3.Row], instrument: sqlite3.Row,
        rows: list[Mapping[str, Any]], identity_warnings: tuple[str, ...],
        selection_warnings: tuple[str, ...], truncated: bool,
    ) -> TimeSeries:
        series = super()._series_from_rows(
            query=query, field=field, migrations=migrations, instrument=instrument,
            rows=rows, identity_warnings=identity_warnings,
            selection_warnings=selection_warnings, truncated=truncated,
        )
        if field not in OHLC_FIELDS:
            return series
        capture_count = len({str(row["capture_id"]) for row in rows})
        close_bound = (
            field == "close" and bool(rows)
            and all(bool(row.get(_BINDING_KEY)) for row in rows)
        )
        adjustment_status = _FMP_CLOSE_BASIS if close_bound else "not_established"
        vintage_status = (
            "single_provider_capture" if close_bound and capture_count == 1 else
            "mixed_provider_captures" if close_bound else "not_established"
        )
        metadata = {
            **dict(series.metadata),
            "adjustment_status": adjustment_status,
            "source_price_field": field,
            "price_adjustment_applied_by_tool": False,
            "source_adjustment_binding": _FMP_CLOSE_BINDING if close_bound else None,
            "source_adjustment_documentation": _FMP_CLOSE_DOCUMENTATION if close_bound else None,
            "source_capture_count": capture_count,
            "adjustment_vintage_status": vintage_status,
            "source_publication_time": None,
        }
        observations = series.observations
        warnings = set(series.warnings)
        if close_bound:
            warnings.discard("adjustment_and_total_return_semantics_not_established")
            warnings.update({
                "total_return_not_provided",
                "historical_adjustment_reconstruction_not_established",
            })
            if capture_count > 1:
                warnings.add("mixed_provider_adjustment_vintages_not_reconciled")
            observations = tuple(
                replace(
                    observation,
                    quality_flags=tuple(
                        _ADJUSTMENT_STATUS_FLAG
                        if flag == "adjustment_status:not_established" else flag
                        for flag in observation.quality_flags
                    ),
                ) for observation in observations
            )
        return replace(
            series, metadata=metadata, observations=observations,
            warnings=tuple(sorted(warnings)), lineage_digest="",
        )


__all__ = ("ProviderClosePriceRepository",)
