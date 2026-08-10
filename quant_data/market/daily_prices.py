"""Offline, revision-aware daily-price ingestion and read services.

This module owns only market-domain behavior.  Store routing, transactions,
semantic replay coordination, temporal comparison, and strict JSON encoding
remain in the shared Stage 1 spine.
"""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Mapping

from ..contracts import IngestionReceipt
from ..errors import ConflictError, Issue, ResourceLimitError, ValidationError
from ..fixtures import Fixture, FixtureManifest
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection, stable_id
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


_CSV_COLUMNS = (
    "provider",
    "provider_symbol",
    "asset_type",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "currency",
    "price_variant",
    "available_at",
    "available_precision",
)
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807
_MAX_AS_OF_CANDIDATES = 200_000

# The fixture adapter deliberately maps provider labels to opaque, reviewed
# local identity seeds.  It does not derive permanent instrument IDs from a
# provider symbol.  A later catalog adapter can replace this small controlled
# binding without changing the importer or version model.
_FIXTURE_IDENTITY_BINDINGS = {
    ("fixture_fmp", "SPY", "etf"): "fixture.market.instrument.spy.v1",
    ("fixture_fmp", "^GSPC", "index"): "fixture.market.instrument.gspc.v1",
}


@dataclass(frozen=True, slots=True)
class DailyPriceQuery:
    """Bounded, read-only daily-price selection.

    The field order is a public Stage 1 contract.  Callers must give one
    stable instrument ID or one unambiguous provider symbol, while provider,
    variant, and currency remain explicit dimensions in either form.
    """

    start_date: str
    end_date: str
    provider: str
    price_variant: str
    currency_segment: str
    mode: str
    as_of: str | None
    date_only_policy: DateOnlyPolicy | str
    limit: int
    instrument_id: str | None = None
    provider_symbol: str | None = None

    def __post_init__(self) -> None:
        start = parse_date(self.start_date, pointer="/start_date")
        end = parse_date(self.end_date, pointer="/end_date")
        if end < start:
            raise ValidationError(
                "End date cannot be before start date",
                issues=(Issue("/end_date", "range", "end_date must not precede start_date"),),
            )
        for field_name, value in (
            ("provider", self.provider),
            ("price_variant", self.price_variant),
            ("currency_segment", self.currency_segment),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    f"{field_name} is required",
                    issues=(Issue(f"/{field_name}", "required", "Expected a nonempty string"),),
                )
        if self.mode not in {"latest", "as_of"}:
            raise ValidationError(
                "Unsupported daily-price mode",
                issues=(Issue("/mode", "enum", "Expected latest or as_of"),),
            )
        if self.mode == "as_of":
            if not isinstance(self.as_of, str) or not self.as_of:
                raise ValidationError(
                    "as_of is required for as_of mode",
                    issues=(Issue("/as_of", "required", "Expected an ISO date or aware datetime"),),
                )
            TemporalValue.parse(self.as_of, pointer="/as_of")
        elif self.as_of is not None:
            raise ValidationError(
                "as_of is not allowed for latest mode",
                issues=(Issue("/as_of", "forbidden", "latest mode has no availability cutoff"),),
            )
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Unsupported date-only policy",
                issues=(
                    Issue(
                        "/date_only_policy",
                        "enum",
                        "Expected completed_date or calendar_date_inclusive",
                    ),
                ),
            ) from exc
        object.__setattr__(self, "date_only_policy", policy)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 10_000:
            raise ValidationError(
                "Daily-price limit is outside the supported range",
                issues=(Issue("/limit", "range", "Expected an integer from 1 through 10000"),),
            )
        for field_name, value in (
            ("instrument_id", self.instrument_id),
            ("provider_symbol", self.provider_symbol),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValidationError(
                    f"{field_name} must be a nonempty string when supplied",
                    issues=(Issue(f"/{field_name}", "type", "Expected null or a nonempty string"),),
                )
        has_instrument = self.instrument_id is not None
        has_symbol = self.provider_symbol is not None
        if has_instrument == has_symbol:
            raise ValidationError(
                "Exactly one price selector is required",
                issues=(
                    Issue(
                        "/instrument_id",
                        "exclusive_selector",
                        "Provide exactly one of instrument_id or provider_symbol",
                    ),
                ),
            )


@dataclass(frozen=True, slots=True)
class _DailyPriceRow:
    provider: str
    provider_symbol: str
    asset_type: str
    trade_date: str
    open_value: Decimal
    high_value: Decimal
    low_value: Decimal
    close_value: Decimal
    volume: int
    currency_segment: str
    price_variant: str
    available_at: TemporalValue
    source_row: int

    @property
    def source_natural_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.provider,
            self.provider_symbol,
            self.trade_date,
            self.price_variant,
            self.currency_segment,
        )

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "asset_type": self.asset_type,
            "trade_date": self.trade_date,
            "open": _decimal_text(self.open_value),
            "high": _decimal_text(self.high_value),
            "low": _decimal_text(self.low_value),
            "close": _decimal_text(self.close_value),
            "volume": self.volume,
            "currency_segment": self.currency_segment,
            "price_variant": self.price_variant,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
        }


def _validation_error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _nonempty(value: object, *, pointer: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation_error(pointer, "required", "Expected a nonempty field")
    return value.strip()


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValidationError("Daily-price values must be finite")
    if value.is_zero():
        return "0"
    return format(value.normalize(), "f")


def _decimal(value: object, *, pointer: str) -> Decimal:
    raw = _nonempty(value, pointer=pointer)
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise _validation_error(pointer, "number", "Expected a finite decimal value") from exc
    if not result.is_finite():
        raise _validation_error(pointer, "finite", "Expected a finite decimal value")
    return result


def _volume(value: object, *, pointer: str) -> int:
    parsed = _decimal(value, pointer=pointer)
    if parsed != parsed.to_integral_value():
        raise _validation_error(pointer, "integer", "Volume must be an integer")
    result = int(parsed)
    if result < 0:
        raise _validation_error(pointer, "minimum", "Volume must be nonnegative")
    if result > _MAX_SQLITE_INTEGER:
        raise _validation_error(pointer, "maximum", "Volume exceeds SQLite integer capacity")
    return result


def _fixture_scope(fixture: Fixture) -> dict[str, object]:
    scope = fixture.request_scope
    if set(scope) != {"symbols", "start_date", "end_date", "price_variant", "completeness"}:
        raise ValidationError("Market fixture scope has an invalid shape")
    symbols_raw = scope["symbols"]
    if not isinstance(symbols_raw, list) or not symbols_raw:
        raise ValidationError("Market fixture scope must declare symbols")
    symbols = tuple(_nonempty(symbol, pointer="/request_scope/symbols") for symbol in symbols_raw)
    if len(set(symbols)) != len(symbols):
        raise ValidationError("Market fixture scope contains duplicate symbols")
    start = parse_date(_nonempty(scope["start_date"], pointer="/request_scope/start_date"), pointer="/request_scope/start_date")
    end = parse_date(_nonempty(scope["end_date"], pointer="/request_scope/end_date"), pointer="/request_scope/end_date")
    if end < start:
        raise ValidationError("Market fixture scope has an invalid date range")
    price_variant = _nonempty(scope["price_variant"], pointer="/request_scope/price_variant")
    completeness = _nonempty(scope["completeness"], pointer="/request_scope/completeness")
    if completeness not in {"complete", "partial"}:
        raise ValidationError("Market fixture completeness must be complete or partial")
    return {
        "symbols": sorted(symbols),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "price_variant": price_variant,
        "completeness": completeness,
    }


def _normalization_version(fixture: Fixture) -> str:
    value = fixture.metadata.get("normalization_version")
    return _nonempty(value, pointer="/metadata/normalization_version")


def _parse_fixture_rows(
    fixture: Fixture,
) -> tuple[tuple[_DailyPriceRow, ...], dict[str, object], TemporalValue, str]:
    if fixture.store != StoreRole.MARKET.value:
        raise ValidationError("Daily-price importer accepts only market fixtures")
    if not fixture.identity_dataset_id:
        raise ValidationError("Market fixture must declare an identity dataset")
    scope = _fixture_scope(fixture)
    captured = TemporalValue.parse(fixture.captured_at, pointer="/captured_at")
    if captured.precision is not TemporalPrecision.DATETIME:
        raise ValidationError("Market fixture capture must be an aware datetime")
    normalization_version = _normalization_version(fixture)
    try:
        text = fixture.bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _validation_error("/fixture", "utf8", "Market fixture must be strict UTF-8") from exc
    reader = csv.DictReader(StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != _CSV_COLUMNS:
        raise ValidationError("Market fixture CSV header is invalid")

    rows: list[_DailyPriceRow] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    scope_symbols = set(scope["symbols"])
    for source_row, raw_row in enumerate(reader, start=2):
        if raw_row is None or None in raw_row or set(raw_row) != set(_CSV_COLUMNS):
            raise _validation_error(f"/rows/{source_row}", "shape", "Market CSV row has an invalid shape")
        provider = _nonempty(raw_row["provider"], pointer=f"/rows/{source_row}/provider")
        if provider != fixture.provider:
            raise _validation_error(
                f"/rows/{source_row}/provider",
                "provider",
                "CSV provider must match the fixture manifest",
            )
        symbol = _nonempty(raw_row["provider_symbol"], pointer=f"/rows/{source_row}/provider_symbol")
        if symbol not in scope_symbols:
            raise _validation_error(
                f"/rows/{source_row}/provider_symbol",
                "scope",
                "Provider symbol is outside the declared request scope",
            )
        asset_type = _nonempty(raw_row["asset_type"], pointer=f"/rows/{source_row}/asset_type")
        if asset_type not in {"etf", "index"}:
            raise _validation_error(
                f"/rows/{source_row}/asset_type",
                "enum",
                "Stage 1 supports fixture ETF and index instruments",
            )
        trade_date = parse_date(
            _nonempty(raw_row["trade_date"], pointer=f"/rows/{source_row}/trade_date"),
            pointer=f"/rows/{source_row}/trade_date",
        )
        if not scope["start_date"] <= trade_date.isoformat() <= scope["end_date"]:
            raise _validation_error(
                f"/rows/{source_row}/trade_date",
                "scope",
                "Trade date is outside the declared request scope",
            )
        open_value = _decimal(raw_row["open"], pointer=f"/rows/{source_row}/open")
        high_value = _decimal(raw_row["high"], pointer=f"/rows/{source_row}/high")
        low_value = _decimal(raw_row["low"], pointer=f"/rows/{source_row}/low")
        close_value = _decimal(raw_row["close"], pointer=f"/rows/{source_row}/close")
        if low_value > min(open_value, close_value) or high_value < max(open_value, close_value) or low_value > high_value:
            raise _validation_error(
                f"/rows/{source_row}",
                "ohlc",
                "Daily-price OHLC values are inconsistent",
            )
        volume = _volume(raw_row["volume"], pointer=f"/rows/{source_row}/volume")
        currency_segment = _nonempty(raw_row["currency"], pointer=f"/rows/{source_row}/currency")
        price_variant = _nonempty(raw_row["price_variant"], pointer=f"/rows/{source_row}/price_variant")
        if price_variant != scope["price_variant"]:
            raise _validation_error(
                f"/rows/{source_row}/price_variant",
                "scope",
                "Price variant must match the fixture request scope",
            )
        available = TemporalValue.parse(
            _nonempty(raw_row["available_at"], pointer=f"/rows/{source_row}/available_at"),
            pointer=f"/rows/{source_row}/available_at",
        )
        declared_precision = _nonempty(
            raw_row["available_precision"], pointer=f"/rows/{source_row}/available_precision"
        )
        if declared_precision not in {"date", "datetime"} or declared_precision != available.precision.value:
            raise _validation_error(
                f"/rows/{source_row}/available_precision",
                "precision",
                "Availability precision must exactly match its source representation",
            )
        row = _DailyPriceRow(
            provider=provider,
            provider_symbol=symbol,
            asset_type=asset_type,
            trade_date=trade_date.isoformat(),
            open_value=open_value,
            high_value=high_value,
            low_value=low_value,
            close_value=close_value,
            volume=volume,
            currency_segment=currency_segment,
            price_variant=price_variant,
            available_at=available,
            source_row=source_row,
        )
        # Verify that a durable opaque identity exists before the shared
        # coordinator opens an ingestion run or writer transaction.
        _binding_for(row)
        if row.source_natural_key in seen:
            raise _validation_error(
                f"/rows/{source_row}",
                "duplicate_natural_key",
                "Duplicate daily-price natural key in one batch",
            )
        seen.add(row.source_natural_key)
        rows.append(row)
    if not rows:
        raise ValidationError("Market fixture contains no daily-price rows")
    if {row.provider_symbol for row in rows} != scope_symbols:
        raise ValidationError("Market fixture rows do not cover the declared symbol scope")
    expected_rows = fixture.metadata.get("expected_rows")
    expected_missing = fixture.metadata.get("expected_missing")
    if not isinstance(expected_rows, int) or expected_rows != len(rows):
        raise ValidationError("Market fixture row count does not match reviewed metadata")
    if expected_missing != 0:
        raise ValidationError("Market daily-price fixtures cannot declare missing values")
    rows.sort(
        key=lambda row: (
            row.trade_date,
            row.provider,
            row.provider_symbol,
            row.price_variant,
            row.currency_segment,
        )
    )
    return tuple(rows), scope, captured, normalization_version


def _scope_digest(scope: Mapping[str, object]) -> str:
    return hashlib.sha256(dumps_strict(dict(scope)).encode("utf-8")).hexdigest()


def _lineage_digest(public_payload: Mapping[str, object]) -> str:
    """Bind every public daily-price result field except its own digest."""

    material = {
        field_name: value
        for field_name, value in public_payload.items()
        if field_name != "lineage_digest"
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _semantic_identity(
    fixture: Fixture,
    scope: Mapping[str, object],
    normalization_version: str,
    rows: tuple[_DailyPriceRow, ...],
) -> str:
    material = {
        "ingestion_family_id": fixture.ingestion_family_id,
        "canonical_dataset_id": fixture.canonical_dataset_id,
        "provider": fixture.provider,
        "scope": dict(scope),
        "completeness": scope["completeness"],
        "normalization_version": normalization_version,
        # Rows are already sorted canonically.  Source-row ordering and CSV
        # physical order are intentionally excluded from semantic equality.
        "rows": [row.semantic_mapping() for row in rows],
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _binding_for(row: _DailyPriceRow) -> str:
    try:
        return _FIXTURE_IDENTITY_BINDINGS[(row.provider, row.provider_symbol, row.asset_type)]
    except KeyError as exc:
        raise ValidationError(
            "Fixture provider identifier has no reviewed stable identity binding",
            issues=(
                Issue(
                    f"/rows/{row.source_row}/provider_symbol",
                    "identity_binding",
                    "A fixture instrument must resolve through an explicit opaque binding",
                ),
            ),
        ) from exc


def _ensure_instrument(
    connection: sqlite3.Connection,
    *,
    row: _DailyPriceRow,
    valid_from: str,
    identity_dataset_id: str,
    artifact_id: str,
    run_id: str,
) -> str:
    matching = list(
        connection.execute(
            """
            SELECT identifier_id, ii.instrument_id, i.asset_type
            FROM instrument_identifiers AS ii
            JOIN instruments AS i ON i.instrument_id=ii.instrument_id
            WHERE ii.provider=?
              AND ii.provider_symbol=?
              AND ii.valid_from<=?
              AND (ii.valid_through IS NULL OR ii.valid_through>=?)
            ORDER BY ii.valid_from, ii.identifier_id
            """,
            (row.provider, row.provider_symbol, row.trade_date, row.trade_date),
        )
    )
    if len(matching) > 1:
        raise ConflictError("Provider identifier is ambiguous for the trade date")
    if matching:
        result = matching[0]
        if result["asset_type"] != row.asset_type:
            raise ConflictError("Provider identifier conflicts with an existing asset type")
        return str(result["instrument_id"])

    identity_seed = _binding_for(row)
    instrument_id = stable_id("instrument", identity_dataset_id, identity_seed)
    existing_instrument = connection.execute(
        "SELECT asset_type FROM instruments WHERE instrument_id=?", (instrument_id,)
    ).fetchone()
    if existing_instrument is None:
        connection.execute(
            "INSERT INTO instruments (instrument_id, asset_type, created_run_id) VALUES (?, ?, ?)",
            (instrument_id, row.asset_type, run_id),
        )
    elif existing_instrument["asset_type"] != row.asset_type:
        raise ConflictError("Stable instrument identity conflicts with an existing asset type")
    identifier_id = stable_id(
        "instrument_identifier", instrument_id, row.provider, row.provider_symbol, valid_from
    )
    connection.execute(
        """
        INSERT INTO instrument_identifiers (
            identifier_id, instrument_id, provider, provider_symbol, valid_from,
            valid_through, available_at, available_precision, evidence_id, run_id
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            identifier_id,
            instrument_id,
            row.provider,
            row.provider_symbol,
            valid_from,
            row.available_at.raw,
            row.available_at.precision.value,
            artifact_id,
            run_id,
        ),
    )
    return instrument_id


def _current_version(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    row: _DailyPriceRow,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT version.version_id, version.correction_sequence,
               version.open_value, version.high_value, version.low_value,
               version.close_value, version.volume, version.available_at,
               version.available_precision
        FROM prices_daily AS current
        JOIN prices_daily_versions AS version ON version.version_id=current.current_version_id
        WHERE current.instrument_id=? AND current.trade_date=? AND current.provider=?
          AND current.price_variant=? AND current.currency_segment=?
        """,
        (
            instrument_id,
            row.trade_date,
            row.provider,
            row.price_variant,
            row.currency_segment,
        ),
    ).fetchone()


def _matches_current(current: sqlite3.Row, row: _DailyPriceRow) -> bool:
    return (
        current["open_value"] == _decimal_text(row.open_value)
        and current["high_value"] == _decimal_text(row.high_value)
        and current["low_value"] == _decimal_text(row.low_value)
        and current["close_value"] == _decimal_text(row.close_value)
        and current["volume"] == row.volume
        and current["available_at"] == row.available_at.raw
        and current["available_precision"] == row.available_at.precision.value
    )


class DailyPriceImporter:
    """Import reviewed market CSV fixtures through the shared no-write gate."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self.store_map = store_map
        self.fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map)

    def import_fixture(self, fixture_id: str) -> IngestionReceipt:
        fixture = self.fixture_manifest.get(fixture_id)
        rows, scope, captured, normalization_version = _parse_fixture_rows(fixture)
        semantic_identity = _semantic_identity(fixture, scope, normalization_version, rows)
        scope_digest = _scope_digest(scope)
        if not fixture.identity_dataset_id:
            raise ValidationError("Market fixture must declare an identity dataset")
        run_id = stable_id("ingestion_run", fixture.canonical_dataset_id, semantic_identity)
        request_id = stable_id("market_price_request", fixture.evidence_dataset_id, semantic_identity)
        artifact_id = stable_id(
            "market_artifact", fixture.evidence_dataset_id, fixture.sha256, scope_digest
        )
        snapshot_id = stable_id("market_snapshot", fixture.evidence_dataset_id, semantic_identity)
        valid_from_by_identifier: dict[tuple[str, str, str], str] = {}
        for row in rows:
            key = (row.provider, row.provider_symbol, row.asset_type)
            prior = valid_from_by_identifier.get(key)
            valid_from_by_identifier[key] = min(prior, row.trade_date) if prior else row.trade_date

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            connection.execute(
                """
                INSERT INTO market_price_ingestion_requests (
                    request_id, dataset_id, provider, scope_json, scope_digest,
                    semantic_identity, completeness, artifact_id, artifact_sha256,
                    snapshot_id, captured_at, captured_precision, source_resource,
                    row_count, normalization_version, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    fixture.evidence_dataset_id,
                    fixture.provider,
                    dumps_strict(scope),
                    scope_digest,
                    semantic_identity,
                    scope["completeness"],
                    artifact_id,
                    fixture.sha256,
                    snapshot_id,
                    captured.raw,
                    captured.precision.value,
                    fixture.resource_name,
                    len(rows),
                    normalization_version,
                    active_run_id,
                ),
            )
            appended_versions = 0
            for row in rows:
                identity_key = (row.provider, row.provider_symbol, row.asset_type)
                instrument_id = _ensure_instrument(
                    connection,
                    row=row,
                    valid_from=valid_from_by_identifier[identity_key],
                    identity_dataset_id=fixture.identity_dataset_id,
                    artifact_id=artifact_id,
                    run_id=active_run_id,
                )
                current = _current_version(connection, instrument_id=instrument_id, row=row)
                if current is not None and _matches_current(current, row):
                    continue
                correction_sequence = 1 if current is None else int(current["correction_sequence"]) + 1
                version_id = stable_id(
                    "daily_price_version",
                    semantic_identity,
                    instrument_id,
                    row.trade_date,
                    row.provider,
                    row.price_variant,
                    row.currency_segment,
                )
                supersedes = None if current is None else str(current["version_id"])
                connection.execute(
                    """
                    INSERT INTO prices_daily_versions (
                        version_id, instrument_id, trade_date, provider, price_variant,
                        currency_segment, open_value, high_value, low_value, close_value,
                        volume, available_at, available_precision, captured_at,
                        captured_precision, correction_sequence, supersedes_version_id,
                        request_id, artifact_id, snapshot_id, run_id, source_row
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        instrument_id,
                        row.trade_date,
                        row.provider,
                        row.price_variant,
                        row.currency_segment,
                        _decimal_text(row.open_value),
                        _decimal_text(row.high_value),
                        _decimal_text(row.low_value),
                        _decimal_text(row.close_value),
                        row.volume,
                        row.available_at.raw,
                        row.available_at.precision.value,
                        captured.raw,
                        captured.precision.value,
                        correction_sequence,
                        supersedes,
                        request_id,
                        artifact_id,
                        snapshot_id,
                        active_run_id,
                        row.source_row,
                    ),
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO prices_daily (
                            instrument_id, trade_date, provider, price_variant,
                            currency_segment, current_version_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            instrument_id,
                            row.trade_date,
                            row.provider,
                            row.price_variant,
                            row.currency_segment,
                            version_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE prices_daily SET current_version_id=?
                        WHERE instrument_id=? AND trade_date=? AND provider=?
                          AND price_variant=? AND currency_segment=?
                        """,
                        (
                            version_id,
                            instrument_id,
                            row.trade_date,
                            row.provider,
                            row.price_variant,
                            row.currency_segment,
                        ),
                    )
                appended_versions += 1
            return WriteResult(
                written_count=appended_versions,
                artifacts=(
                    ArtifactWrite(
                        artifact_id=artifact_id,
                        dataset_id=fixture.evidence_dataset_id,
                        content_sha256=fixture.sha256,
                        media_type="text/csv",
                        byte_count=fixture.byte_count,
                        source_reference=fixture.resource_name,
                        request_scope=dict(scope),
                        captured_at=captured.raw,
                        captured_precision=captured.precision.value,
                        normalization_version=normalization_version,
                    ),
                ),
                snapshot=SnapshotWrite(
                    snapshot_id=snapshot_id,
                    dataset_id=fixture.canonical_dataset_id,
                    semantic_identity=semantic_identity,
                    scope=dict(scope),
                    completeness=str(scope["completeness"]),
                    row_count=len(rows),
                    captured_at=captured.raw,
                    captured_precision=captured.precision.value,
                    validation_state="validated",
                    artifact_ids=(artifact_id,),
                ),
                quality_results=(
                    QualityWrite(
                        quality_result_id=stable_id(
                            "quality_result",
                            active_run_id,
                            fixture.canonical_dataset_id,
                            "fixture.batch_contract",
                            "1.0.0",
                        ),
                        dataset_id=fixture.canonical_dataset_id,
                        rule_id="fixture.batch_contract",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=snapshot_id,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        observed={
                            "fetched_count": len(rows),
                            "written_count": appended_versions,
                            "completeness": scope["completeness"],
                        },
                    ),
                ),
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=fixture.canonical_dataset_id,
            output_dataset_ids=(
                fixture.evidence_dataset_id,
                fixture.identity_dataset_id,
                fixture.canonical_dataset_id,
            ),
            semantic_identity=semantic_identity,
            run_id=run_id,
            command=fixture.ingestion_family_id,
            scope={
                "fixture_id": fixture.id,
                "request_scope": scope,
                "source_resource": fixture.resource_name,
            },
            started_at=captured.raw or fixture.captured_at,
            completed_at=captured.raw or fixture.captured_at,
            fetched_count=len(rows),
            writer=writer,
        )


def _stored_temporal(row: sqlite3.Row, *, value_name: str, precision_name: str) -> TemporalValue:
    value = TemporalValue.parse(str(row[value_name]), pointer=f"/stored/{value_name}")
    if value.precision.value != row[precision_name]:
        raise ValidationError("Stored market temporal precision is inconsistent")
    return value


def _stored_decimal(row: sqlite3.Row, name: str) -> Decimal:
    try:
        result = Decimal(str(row[name]))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Stored daily-price value is invalid") from exc
    if not result.is_finite():
        raise ValidationError("Stored daily-price value is non-finite")
    return result


class DailyPriceRepository:
    """Host-routed, read-only daily-price repository."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self.store_map = store_map
        self.registry = registry

    @staticmethod
    def _query_instrument(
        connection: sqlite3.Connection, query: DailyPriceQuery
    ) -> tuple[str, str, str | None]:
        if query.instrument_id is not None:
            row = connection.execute(
                "SELECT instrument_id, asset_type FROM instruments WHERE instrument_id=?",
                (query.instrument_id,),
            ).fetchone()
            if row is None:
                raise ValidationError("Unknown stable market instrument")
            identifier = connection.execute(
                """
                SELECT provider_symbol FROM instrument_identifiers
                WHERE instrument_id=? AND provider=?
                  AND valid_from<=? AND (valid_through IS NULL OR valid_through>=?)
                ORDER BY valid_from DESC, identifier_id
                LIMIT 1
                """,
                (query.instrument_id, query.provider, query.end_date, query.start_date),
            ).fetchone()
            return str(row["instrument_id"]), str(row["asset_type"]), (
                None if identifier is None else str(identifier["provider_symbol"])
            )
        assert query.provider_symbol is not None
        matches = list(
            connection.execute(
                """
                SELECT DISTINCT ii.instrument_id, i.asset_type
                FROM instrument_identifiers AS ii
                JOIN instruments AS i ON i.instrument_id=ii.instrument_id
                WHERE ii.provider=? AND ii.provider_symbol=?
                  AND ii.valid_from<=? AND (ii.valid_through IS NULL OR ii.valid_through>=?)
                ORDER BY ii.instrument_id
                """,
                (query.provider, query.provider_symbol, query.end_date, query.start_date),
            )
        )
        if not matches:
            raise ValidationError("Unknown provider market identifier")
        if len(matches) != 1:
            raise ConflictError("Provider market identifier is ambiguous for the requested range")
        return str(matches[0]["instrument_id"]), str(matches[0]["asset_type"]), query.provider_symbol

    @staticmethod
    def _latest_rows(
        connection: sqlite3.Connection, query: DailyPriceQuery, instrument_id: str
    ) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT version.*,
                       request.artifact_sha256 AS evidence_artifact_sha256,
                       request.scope_digest AS evidence_scope_digest,
                       request.semantic_identity AS evidence_semantic_identity,
                       request.normalization_version AS evidence_normalization_version
                FROM prices_daily AS current
                JOIN prices_daily_versions AS version ON version.version_id=current.current_version_id
                JOIN market_price_ingestion_requests AS request ON request.request_id=version.request_id
                WHERE current.instrument_id=? AND current.trade_date>=? AND current.trade_date<=?
                  AND current.provider=? AND current.price_variant=?
                  AND current.currency_segment=?
                ORDER BY current.trade_date, current.instrument_id, version.version_id
                LIMIT ?
                """,
                (
                    instrument_id,
                    query.start_date,
                    query.end_date,
                    query.provider,
                    query.price_variant,
                    query.currency_segment,
                    query.limit + 1,
                ),
            )
        )

    @staticmethod
    def _as_of_rows(
        connection: sqlite3.Connection,
        query: DailyPriceQuery,
        instrument_id: str,
        cutoff: TemporalValue,
    ) -> tuple[list[sqlite3.Row], tuple[str, ...]]:
        candidates = list(
            connection.execute(
                """
                SELECT version.*,
                       request.artifact_sha256 AS evidence_artifact_sha256,
                       request.scope_digest AS evidence_scope_digest,
                       request.semantic_identity AS evidence_semantic_identity,
                       request.normalization_version AS evidence_normalization_version
                FROM prices_daily_versions AS version
                JOIN market_price_ingestion_requests AS request ON request.request_id=version.request_id
                WHERE version.instrument_id=? AND version.trade_date>=? AND version.trade_date<=?
                  AND version.provider=? AND version.price_variant=? AND version.currency_segment=?
                ORDER BY version.trade_date, version.correction_sequence DESC, version.version_id
                LIMIT ?
                """,
                (
                    instrument_id,
                    query.start_date,
                    query.end_date,
                    query.provider,
                    query.price_variant,
                    query.currency_segment,
                    _MAX_AS_OF_CANDIDATES + 1,
                ),
            )
        )
        if len(candidates) > _MAX_AS_OF_CANDIDATES:
            raise ResourceLimitError("Daily-price revision history exceeds the bounded query contract")
        selected: dict[tuple[str, str, str, str, str], sqlite3.Row] = {}
        warnings: set[str] = set()
        for candidate in candidates:
            key = (
                str(candidate["instrument_id"]),
                str(candidate["trade_date"]),
                str(candidate["provider"]),
                str(candidate["price_variant"]),
                str(candidate["currency_segment"]),
            )
            if key in selected:
                continue
            decision = availability_at_or_before(
                _stored_temporal(candidate, value_name="available_at", precision_name="available_precision"),
                cutoff,
                query.date_only_policy,
            )
            if decision.included:
                selected[key] = candidate
                warnings.update(decision.warnings)
        rows = sorted(
            selected.values(),
            key=lambda row: (str(row["trade_date"]), str(row["instrument_id"]), str(row["version_id"])),
        )
        return rows, tuple(sorted(warnings))

    @staticmethod
    def _observation(row: sqlite3.Row) -> dict[str, object]:
        return {
            "instrument_id": str(row["instrument_id"]),
            "trade_date": str(row["trade_date"]),
            "provider": str(row["provider"]),
            "price_variant": str(row["price_variant"]),
            "currency_segment": str(row["currency_segment"]),
            "open": _stored_decimal(row, "open_value"),
            "high": _stored_decimal(row, "high_value"),
            "low": _stored_decimal(row, "low_value"),
            "close": _stored_decimal(row, "close_value"),
            "volume": int(row["volume"]),
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "version_id": str(row["version_id"]),
            "supersedes_version_id": (
                None if row["supersedes_version_id"] is None else str(row["supersedes_version_id"])
            ),
            "request_id": str(row["request_id"]),
            "evidence_id": str(row["artifact_id"]),
            "snapshot_id": str(row["snapshot_id"]),
            "run_id": str(row["run_id"]),
        }

    @staticmethod
    def _receipt_version(row: sqlite3.Row) -> dict[str, object]:
        """Return every immutable price/evidence identity selected by a query.

        This payload deliberately follows the macro receipt pattern: a receipt
        is tied to the reviewed migrations, the fixed series contract, and the
        exact immutable source versions rather than to a mutable current table
        or a file-level timestamp.
        """

        return {
            "version_id": str(row["version_id"]),
            "instrument_id": str(row["instrument_id"]),
            "trade_date": str(row["trade_date"]),
            "provider": str(row["provider"]),
            "price_variant": str(row["price_variant"]),
            "currency_segment": str(row["currency_segment"]),
            "open_value": str(row["open_value"]),
            "high_value": str(row["high_value"]),
            "low_value": str(row["low_value"]),
            "close_value": str(row["close_value"]),
            "volume": int(row["volume"]),
            "available_at": str(row["available_at"]),
            "available_precision": str(row["available_precision"]),
            "captured_at": str(row["captured_at"]),
            "captured_precision": str(row["captured_precision"]),
            "correction_sequence": int(row["correction_sequence"]),
            "supersedes_version_id": (
                None if row["supersedes_version_id"] is None else str(row["supersedes_version_id"])
            ),
            "request_id": str(row["request_id"]),
            "artifact_id": str(row["artifact_id"]),
            "artifact_sha256": str(row["evidence_artifact_sha256"]),
            "snapshot_id": str(row["snapshot_id"]),
            "run_id": str(row["run_id"]),
            "source_row": int(row["source_row"]),
            "evidence_scope_digest": str(row["evidence_scope_digest"]),
            "evidence_semantic_identity": str(row["evidence_semantic_identity"]),
            "evidence_normalization_version": str(
                row["evidence_normalization_version"]
            ),
        }

    def get_prices(self, query: DailyPriceQuery) -> dict[str, object]:
        if not isinstance(query, DailyPriceQuery):
            raise ValidationError("Daily-price repository requires a DailyPriceQuery")
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            instrument_id, asset_type, resolved_symbol = self._query_instrument(connection, query)
            if query.mode == "latest":
                selected_rows = self._latest_rows(connection, query, instrument_id)
                selection_warnings: tuple[str, ...] = ()
                cutoff = None
            else:
                assert query.as_of is not None
                cutoff = TemporalValue.parse(query.as_of, pointer="/as_of")
                selected_rows, selection_warnings = self._as_of_rows(
                    connection, query, instrument_id, cutoff
                )
            truncated = len(selected_rows) > query.limit
            selected_rows = selected_rows[: query.limit]
            observations = [self._observation(row) for row in selected_rows]
            migration_rows = list(
                connection.execute(
                    "SELECT migration_id, sha256 FROM schema_migrations ORDER BY ordinal"
                )
            )

        series_id = stable_id(
            "daily_price_series",
            instrument_id,
            query.provider,
            query.price_variant,
            query.currency_segment,
        )
        audit = {
            "mode": query.mode,
            "cutoff": None if cutoff is None else cutoff.raw,
            "cutoff_precision": None if cutoff is None else cutoff.precision.value,
            "date_only_policy": query.date_only_policy.value,
            "availability_basis": "source_release",
            "period_range_rule": "trade_date",
            "requested_start_date": query.start_date,
            "requested_end_date": query.end_date,
            "provider": query.provider,
            "price_variant": query.price_variant,
            "currency_segment": query.currency_segment,
            "limit": query.limit,
            "selected_count": len(observations),
        }
        migration_ids = [str(row["migration_id"]) for row in migration_rows]
        receipt_material = {
            "migrations": [
                {"migration_id": str(row["migration_id"]), "sha256": str(row["sha256"])}
                for row in migration_rows
            ],
            "series_contract": {
                "contract": "quant_data.daily_price_series",
                "contract_version": "1.0.0",
                "series_id": series_id,
                "instrument_id": instrument_id,
                "asset_type": asset_type,
                "provider": query.provider,
                "provider_symbol": resolved_symbol,
                "price_variant": query.price_variant,
                "currency_segment": query.currency_segment,
                "observation_field": "trade_date",
                "availability_basis": "source_release",
            },
            "selected_immutable_versions": [
                self._receipt_version(row) for row in selected_rows
            ],
        }
        store_receipt = {
            "migration_ids": migration_ids,
            "sha256": hashlib.sha256(dumps_strict(receipt_material).encode("utf-8")).hexdigest(),
        }
        provenance = {
            "dataset_id": "fixture.market.daily_prices",
            "evidence_dataset_id": "fixture.market.daily_price_evidence",
            "identity_dataset_id": "fixture.market.instruments",
            "store_role": StoreRole.MARKET.value,
            "registry_revision": self.registry.revision,
            "store_receipt": store_receipt,
        }
        result: dict[str, object] = {
            "contract": "quant_data.daily_price_series",
            "contract_version": "1.0.0",
            "series_id": series_id,
            "instrument": {
                "instrument_id": instrument_id,
                "asset_type": asset_type,
                "provider": query.provider,
                "provider_symbol": resolved_symbol,
            },
            "observations": observations,
            "warnings": list(selection_warnings),
            "audit": audit,
            "provenance": provenance,
            "truncated": truncated,
        }
        result["lineage_digest"] = _lineage_digest(result)
        # Assert strict finite JSON compatibility at the domain boundary before
        # handing the primitive result to the HTTP/dashboard adapter.
        dumps_strict(result)
        return result
