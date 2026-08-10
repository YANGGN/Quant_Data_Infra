"""Offline Stage 3 market catalog, classification, and universe services.

The module deliberately accepts only reviewed fixture bytes.  It has no
provider client, ambient database path, or default-store behavior: callers
give a :class:`~quant_data.stores.StoreMap` and a verified fixture manifest.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

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
from ..json_codec import dumps_strict, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection, stable_id
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
    parse_date,
)


# This recovered manifest is intentionally narrow.  It is a catalog contract,
# not a request for a broader list of indexes or an ETF-proxy substitution.
RECOVERED_FMP_INDEXES: Mapping[str, str] = MappingProxyType(
    {
        "^GSPC": "S&P 500",
        "^N225": "Nikkei 225",
        "^IXIC": "Nasdaq Composite",
        "^FTSE": "FTSE 100",
        "^DJI": "Dow Jones Industrial Average",
        "^STOXX50E": "Euro Stoxx 50",
        "^HSI": "Hang Seng",
        "^RUT": "Russell 2000",
        "^VIX": "Cboe Volatility Index",
    }
)


_CATALOG_COLLECTOR_ID = "fixture.market.catalog_import"
_CATALOG_EVIDENCE_DATASET_ID = "fixture.market.catalog_evidence"
_CLASSIFICATION_DATASET_ID = "fixture.market.instrument_classifications"
_UNIVERSE_DATASET_ID = "fixture.market.controlled_universes"
_IDENTITY_DATASET_ID = "fixture.market.instruments"
_MAX_FIXTURE_BYTES = 1_048_576
_MAX_INSTRUMENTS = 1_000
_MAX_IDENTIFIERS = 10_000
_MAX_CLASSIFICATIONS = 10_000
_MAX_UNIVERSE_MEMBERS = 10_000
_MAX_READ_LIMIT = 1_000
_MAX_AS_OF_CANDIDATES = 100_000

# Stage 2 supplied two opaque identity bindings.  Stage 3 enriches those
# identities rather than using a ticker as a permanent identifier or inventing
# an earlier identifier interval.
_REVIEWED_IDENTITY_BINDINGS = MappingProxyType(
    {
        "fixture.market.instrument.spy": "fixture.market.instrument.spy.v1",
        "fixture.market.index.gspc": "fixture.market.instrument.gspc.v1",
    }
)


def _error(pointer: str, rule: str, message: str) -> ValidationError:
    return ValidationError(message, issues=(Issue(pointer, rule, message),))


def _nonempty(value: object, *, pointer: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(pointer, "required", "Expected a nonempty string")
    return value.strip()


def _nullable_text(value: object, *, pointer: str) -> str | None:
    if value is None:
        return None
    return _nonempty(value, pointer=pointer)


def _mapping(value: object, *, pointer: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(pointer, "type", "Expected an object")
    if not all(isinstance(key, str) for key in value):
        raise _error(pointer, "type", "Object keys must be strings")
    return value


def _array(value: object, *, pointer: str, maximum: int) -> Sequence[Any]:
    if not isinstance(value, list):
        raise _error(pointer, "type", "Expected an array")
    if len(value) > maximum:
        raise ResourceLimitError(f"{pointer} exceeds the supported fixture bound")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    pointer: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    fields = set(value)
    if not required.issubset(fields) or not fields.issubset(required | optional):
        raise _error(pointer, "shape", "Object fields do not match the reviewed fixture shape")


def _date(value: object, *, pointer: str) -> str:
    return parse_date(_nonempty(value, pointer=pointer), pointer=pointer).isoformat()


def _temporal(value: object, *, precision: object, pointer: str) -> TemporalValue:
    parsed = TemporalValue.parse(_nonempty(value, pointer=pointer), pointer=pointer)
    precision_pointer = pointer.rsplit("/", 1)[0] + "/available_precision"
    declared = _nonempty(precision, pointer=precision_pointer)
    if declared not in {"date", "datetime"} or declared != parsed.precision.value:
        raise _error(
            precision_pointer,
            "precision",
            "Availability precision must exactly match its source representation",
        )
    return parsed


def _interval_overlaps(
    left_from: str,
    left_through: str | None,
    right_from: str,
    right_through: str | None,
) -> bool:
    return (left_through is None or right_from <= left_through) and (
        right_through is None or left_from <= right_through
    )


def _opaque_binding(instrument_key: str) -> str:
    """Return a reviewed opaque identity seed, never a provider ticker."""

    return _REVIEWED_IDENTITY_BINDINGS.get(
        instrument_key,
        f"fixture.market.catalog.binding.v1:{instrument_key}",
    )


@dataclass(frozen=True, slots=True)
class _CatalogIdentifier:
    provider: str
    provider_symbol: str
    valid_from: str
    valid_through: str | None
    available_at: TemporalValue
    confirmation_state: str

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "provider_symbol": self.provider_symbol,
            "valid_from": self.valid_from,
            "valid_through": self.valid_through,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "confirmation_state": self.confirmation_state,
        }


@dataclass(frozen=True, slots=True)
class _CatalogInstrument:
    instrument_key: str
    asset_type: str
    canonical_symbol: str
    display_name: str
    exchange: str | None
    currency: str | None
    country: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    active: bool
    identifiers: tuple[_CatalogIdentifier, ...]

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "instrument_key": self.instrument_key,
            "asset_type": self.asset_type,
            "canonical_symbol": self.canonical_symbol,
            "display_name": self.display_name,
            "exchange": self.exchange,
            "currency": self.currency,
            "country": self.country,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "active": self.active,
            "identifiers": [item.semantic_mapping() for item in self.identifiers],
        }


@dataclass(frozen=True, slots=True)
class _Classification:
    instrument_key: str
    classification_provider: str
    sector: str | None
    industry: str | None
    effective_from: str
    effective_through: str | None
    available_at: TemporalValue
    source_row: int

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "instrument_key": self.instrument_key,
            "classification_provider": self.classification_provider,
            "sector": self.sector,
            "industry": self.industry,
            "effective_from": self.effective_from,
            "effective_through": self.effective_through,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
        }


@dataclass(frozen=True, slots=True)
class _UniverseMembership:
    instrument_key: str
    effective_from: str
    effective_through: str | None
    source_row: int

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "instrument_key": self.instrument_key,
            "effective_from": self.effective_from,
            "effective_through": self.effective_through,
        }


@dataclass(frozen=True, slots=True)
class _Universe:
    universe_key: str
    display_name: str
    completeness: str
    available_at: TemporalValue
    members: tuple[_UniverseMembership, ...]

    def semantic_mapping(self) -> dict[str, object]:
        return {
            "universe_key": self.universe_key,
            "display_name": self.display_name,
            "completeness": self.completeness,
            "available_at": self.available_at.raw,
            "available_precision": self.available_at.precision.value,
            "members": [member.semantic_mapping() for member in self.members],
        }


@dataclass(frozen=True, slots=True)
class _IndexManifestEntry:
    instrument_key: str
    fmp_symbol: str
    canonical_name: str

    def semantic_mapping(self) -> dict[str, str]:
        return {
            "instrument_key": self.instrument_key,
            "fmp_symbol": self.fmp_symbol,
            "canonical_name": self.canonical_name,
        }


@dataclass(frozen=True, slots=True)
class _CatalogPayload:
    instruments: tuple[_CatalogInstrument, ...]
    classifications: tuple[_Classification, ...]
    index_manifest: tuple[_IndexManifestEntry, ...]
    universe: _Universe
    tombstone_authoritative: bool


def _parse_identifier(raw: object, *, pointer: str) -> _CatalogIdentifier:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "provider",
            "provider_symbol",
            "valid_from",
            "valid_through",
            "available_at",
            "available_precision",
        },
        optional={"confirmation_state"},
    )
    valid_from = _date(value["valid_from"], pointer=f"{pointer}/valid_from")
    valid_through = (
        None
        if value["valid_through"] is None
        else _date(value["valid_through"], pointer=f"{pointer}/valid_through")
    )
    if valid_through is not None and valid_through < valid_from:
        raise _error(f"{pointer}/valid_through", "range", "Identifier interval is inverted")
    confirmation_state = value.get("confirmation_state", "confirmed")
    if confirmation_state not in {"confirmed", "unconfirmed"}:
        raise _error(
            f"{pointer}/confirmation_state",
            "enum",
            "Expected confirmed or unconfirmed",
        )
    return _CatalogIdentifier(
        provider=_nonempty(value["provider"], pointer=f"{pointer}/provider"),
        provider_symbol=_nonempty(value["provider_symbol"], pointer=f"{pointer}/provider_symbol"),
        valid_from=valid_from,
        valid_through=valid_through,
        available_at=_temporal(
            value["available_at"],
            precision=value["available_precision"],
            pointer=f"{pointer}/available_at",
        ),
        confirmation_state=str(confirmation_state),
    )


def _parse_instrument(raw: object, *, pointer: str) -> _CatalogInstrument:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "instrument_key",
            "asset_type",
            "canonical_symbol",
            "display_name",
            "active",
            "identifiers",
        },
        optional={"exchange", "currency", "country", "first_seen_at", "last_seen_at"},
    )
    asset_type = _nonempty(value["asset_type"], pointer=f"{pointer}/asset_type")
    if asset_type not in {"equity", "etf", "index"}:
        raise _error(f"{pointer}/asset_type", "enum", "Expected equity, etf, or index")
    if not isinstance(value["active"], bool):
        raise _error(f"{pointer}/active", "type", "Expected a boolean")
    identifiers_raw = _array(
        value["identifiers"], pointer=f"{pointer}/identifiers", maximum=_MAX_IDENTIFIERS
    )
    if not identifiers_raw:
        raise _error(f"{pointer}/identifiers", "minimum", "An instrument requires an identifier")
    identifiers = tuple(
        _parse_identifier(identifier, pointer=f"{pointer}/identifiers/{ordinal}")
        for ordinal, identifier in enumerate(identifiers_raw)
    )
    return _CatalogInstrument(
        instrument_key=_nonempty(value["instrument_key"], pointer=f"{pointer}/instrument_key"),
        asset_type=asset_type,
        canonical_symbol=_nonempty(value["canonical_symbol"], pointer=f"{pointer}/canonical_symbol"),
        display_name=_nonempty(value["display_name"], pointer=f"{pointer}/display_name"),
        exchange=_nullable_text(value.get("exchange"), pointer=f"{pointer}/exchange"),
        currency=_nullable_text(value.get("currency"), pointer=f"{pointer}/currency"),
        country=_nullable_text(value.get("country"), pointer=f"{pointer}/country"),
        first_seen_at=_nullable_text(value.get("first_seen_at"), pointer=f"{pointer}/first_seen_at"),
        last_seen_at=_nullable_text(value.get("last_seen_at"), pointer=f"{pointer}/last_seen_at"),
        active=value["active"],
        identifiers=tuple(
            sorted(
                identifiers,
                key=lambda item: (item.provider, item.provider_symbol, item.valid_from),
            )
        ),
    )


def _parse_classification(raw: object, *, pointer: str, source_row: int) -> _Classification:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={
            "instrument_key",
            "classification_provider",
            "sector",
            "industry",
            "effective_from",
            "effective_through",
            "available_at",
            "available_precision",
        },
    )
    effective_from = _date(value["effective_from"], pointer=f"{pointer}/effective_from")
    effective_through = (
        None
        if value["effective_through"] is None
        else _date(value["effective_through"], pointer=f"{pointer}/effective_through")
    )
    if effective_through is not None and effective_through < effective_from:
        raise _error(f"{pointer}/effective_through", "range", "Classification interval is inverted")
    sector = _nullable_text(value["sector"], pointer=f"{pointer}/sector")
    industry = _nullable_text(value["industry"], pointer=f"{pointer}/industry")
    if sector is None and industry is None:
        raise _error(pointer, "minimum", "Classification requires a sector or industry")
    return _Classification(
        instrument_key=_nonempty(value["instrument_key"], pointer=f"{pointer}/instrument_key"),
        classification_provider=_nonempty(
            value["classification_provider"], pointer=f"{pointer}/classification_provider"
        ),
        sector=sector,
        industry=industry,
        effective_from=effective_from,
        effective_through=effective_through,
        available_at=_temporal(
            value["available_at"],
            precision=value["available_precision"],
            pointer=f"{pointer}/available_at",
        ),
        source_row=source_row,
    )


def _parse_membership(raw: object, *, pointer: str, source_row: int) -> _UniverseMembership:
    value = _mapping(raw, pointer=pointer)
    _exact_keys(
        value,
        pointer=pointer,
        required={"instrument_key", "effective_from", "effective_through"},
    )
    effective_from = _date(value["effective_from"], pointer=f"{pointer}/effective_from")
    effective_through = (
        None
        if value["effective_through"] is None
        else _date(value["effective_through"], pointer=f"{pointer}/effective_through")
    )
    if effective_through is not None and effective_through < effective_from:
        raise _error(f"{pointer}/effective_through", "range", "Membership interval is inverted")
    return _UniverseMembership(
        instrument_key=_nonempty(value["instrument_key"], pointer=f"{pointer}/instrument_key"),
        effective_from=effective_from,
        effective_through=effective_through,
        source_row=source_row,
    )


def _parse_universe(raw: object) -> _Universe:
    value = _mapping(raw, pointer="/universe")
    _exact_keys(
        value,
        pointer="/universe",
        required={
            "universe_key",
            "display_name",
            "completeness",
            "available_at",
            "available_precision",
            "members",
        },
    )
    completeness = _nonempty(value["completeness"], pointer="/universe/completeness")
    if completeness not in {"complete", "partial"}:
        raise _error("/universe/completeness", "enum", "Expected complete or partial")
    members_raw = _array(
        value["members"], pointer="/universe/members", maximum=_MAX_UNIVERSE_MEMBERS
    )
    members = tuple(
        _parse_membership(member, pointer=f"/universe/members/{ordinal}", source_row=ordinal + 1)
        for ordinal, member in enumerate(members_raw)
    )
    member_keys = [member.instrument_key for member in members]
    if len(member_keys) != len(set(member_keys)):
        raise _error("/universe/members", "unique", "Universe membership keys must be unique")
    return _Universe(
        universe_key=_nonempty(value["universe_key"], pointer="/universe/universe_key"),
        display_name=_nonempty(value["display_name"], pointer="/universe/display_name"),
        completeness=completeness,
        available_at=_temporal(
            value["available_at"],
            precision=value["available_precision"],
            pointer="/universe/available_at",
        ),
        members=tuple(sorted(members, key=lambda item: item.instrument_key)),
    )


def _parse_index_manifest(
    raw: object,
    instruments: Mapping[str, _CatalogInstrument],
    *,
    provider: str,
) -> tuple[_IndexManifestEntry, ...]:
    entries_raw = _array(raw, pointer="/index_manifest", maximum=len(RECOVERED_FMP_INDEXES))
    entries: list[_IndexManifestEntry] = []
    for ordinal, raw_entry in enumerate(entries_raw):
        pointer = f"/index_manifest/{ordinal}"
        value = _mapping(raw_entry, pointer=pointer)
        _exact_keys(
            value,
            pointer=pointer,
            required={"instrument_key", "fmp_symbol", "canonical_name"},
        )
        entries.append(
            _IndexManifestEntry(
                instrument_key=_nonempty(value["instrument_key"], pointer=f"{pointer}/instrument_key"),
                fmp_symbol=_nonempty(value["fmp_symbol"], pointer=f"{pointer}/fmp_symbol"),
                canonical_name=_nonempty(value["canonical_name"], pointer=f"{pointer}/canonical_name"),
            )
        )
    if not entries:
        return ()
    by_symbol = {entry.fmp_symbol: entry for entry in entries}
    if len(by_symbol) != len(entries) or set(by_symbol) != set(RECOVERED_FMP_INDEXES):
        raise _error(
            "/index_manifest",
            "exact_manifest",
            "Index manifest must contain exactly the nine recovered FMP indexes",
        )
    for symbol, canonical_name in RECOVERED_FMP_INDEXES.items():
        entry = by_symbol[symbol]
        if entry.canonical_name != canonical_name:
            raise _error(
                "/index_manifest",
                "canonical_name",
                "Index manifest names must match the recovered FMP contract",
            )
        instrument = instruments.get(entry.instrument_key)
        if instrument is None or instrument.asset_type != "index":
            raise _error(
                "/index_manifest",
                "asset_type",
                "Every recovered FMP index must resolve to an index instrument",
            )
        if instrument.display_name != canonical_name:
            raise _error(
                "/index_manifest",
                "display_name",
                "Index display name must match its recovered canonical name",
            )
        if not any(
            identifier.provider == provider and identifier.provider_symbol == symbol
            for identifier in instrument.identifiers
        ):
            raise _error(
                "/index_manifest",
                "identifier",
                "Index manifest entries require their exact FMP identifier",
            )
    return tuple(sorted(entries, key=lambda item: item.fmp_symbol))


def _fixture_scope(fixture: Fixture, universe: _Universe, tombstone_authoritative: bool) -> dict[str, object]:
    scope = _mapping(fixture.request_scope, pointer="/request_scope")
    _exact_keys(
        scope,
        pointer="/request_scope",
        required={"scope_kind", "provider", "universe_key", "completeness"},
    )
    result = {
        "scope_kind": _nonempty(scope["scope_kind"], pointer="/request_scope/scope_kind"),
        "provider": _nonempty(scope["provider"], pointer="/request_scope/provider"),
        "universe_key": _nonempty(scope["universe_key"], pointer="/request_scope/universe_key"),
        "completeness": _nonempty(scope["completeness"], pointer="/request_scope/completeness"),
        "tombstone_authoritative": tombstone_authoritative,
    }
    if result["scope_kind"] not in {"catalog_and_universe", "universe_membership"}:
        raise _error("/request_scope/scope_kind", "enum", "Unsupported catalog fixture scope")
    if result["provider"] != fixture.provider:
        raise _error("/request_scope/provider", "provider", "Scope provider must match fixture provider")
    if result["universe_key"] != universe.universe_key:
        raise _error("/request_scope/universe_key", "scope", "Scope universe must match fixture universe")
    if result["completeness"] != universe.completeness:
        raise _error("/request_scope/completeness", "scope", "Scope completeness must match fixture universe")
    return result


def _normalization_version(fixture: Fixture) -> str:
    metadata = _mapping(fixture.metadata, pointer="/metadata")
    return _nonempty(metadata.get("normalization_version"), pointer="/metadata/normalization_version")


def _tombstone_authoritative(fixture: Fixture, universe: _Universe) -> bool:
    metadata = _mapping(fixture.metadata, pointer="/metadata")
    value = metadata.get("tombstone_authoritative")
    if not isinstance(value, bool):
        raise _error(
            "/metadata/tombstone_authoritative",
            "type",
            "Expected a reviewed boolean tombstone authority flag",
        )
    if value and universe.completeness != "complete":
        raise _error(
            "/metadata/tombstone_authoritative",
            "scope",
            "Only complete catalog fixtures can be tombstone-authoritative",
        )
    return value


def _validate_identifier_intervals(instruments: Sequence[_CatalogInstrument]) -> None:
    candidates: dict[tuple[str, str], list[tuple[str, _CatalogIdentifier]]] = {}
    for instrument in instruments:
        for identifier in instrument.identifiers:
            candidates.setdefault((identifier.provider, identifier.provider_symbol), []).append(
                (instrument.instrument_key, identifier)
            )
    for (provider, symbol), values in candidates.items():
        ordered = sorted(values, key=lambda item: (item[1].valid_from, item[0]))
        for index, (left_key, left) in enumerate(ordered):
            for right_key, right in ordered[index + 1 :]:
                if _interval_overlaps(
                    left.valid_from,
                    left.valid_through,
                    right.valid_from,
                    right.valid_through,
                ):
                    raise _error(
                        "/instruments",
                        "identifier_overlap",
                        f"Provider identifier {provider}:{symbol} overlaps {left_key} and {right_key}",
                    )


def _parse_fixture(
    fixture: Fixture,
) -> tuple[_CatalogPayload, dict[str, object], TemporalValue, str]:
    if fixture.store != StoreRole.MARKET.value:
        raise ValidationError("Market catalog importer accepts only market fixtures")
    if fixture.ingestion_family_id != _CATALOG_COLLECTOR_ID:
        raise ValidationError("Market catalog fixture has an unexpected collector")
    if fixture.evidence_dataset_id != _CATALOG_EVIDENCE_DATASET_ID:
        raise ValidationError("Market catalog fixture has an unexpected evidence dataset")
    if fixture.canonical_dataset_id != _UNIVERSE_DATASET_ID:
        raise ValidationError("Market catalog fixture has an unexpected canonical dataset")
    if fixture.identity_dataset_id != _IDENTITY_DATASET_ID:
        raise ValidationError("Market catalog fixture has an unexpected identity dataset")
    captured = TemporalValue.parse(fixture.captured_at, pointer="/captured_at")
    if captured.precision is not TemporalPrecision.DATETIME:
        raise _error("/captured_at", "precision", "Catalog captures must be aware datetimes")
    try:
        raw = loads_strict(fixture.bytes, max_bytes=_MAX_FIXTURE_BYTES)
    except UnicodeDecodeError as exc:  # pragma: no cover - loads_strict converts this error
        raise _error("/fixture", "utf8", "Catalog fixture must be strict UTF-8") from exc
    root = _mapping(raw, pointer="/")
    _exact_keys(
        root,
        pointer="/",
        required={
            "schema_version",
            "fixture_kind",
            "instruments",
            "classifications",
            "index_manifest",
            "universe",
        },
    )
    if root["schema_version"] != "1.0.0" or root["fixture_kind"] != "market_catalog_snapshot":
        raise ValidationError("Unsupported market catalog fixture")
    raw_instruments = _array(root["instruments"], pointer="/instruments", maximum=_MAX_INSTRUMENTS)
    instruments = tuple(
        _parse_instrument(raw_instrument, pointer=f"/instruments/{ordinal}")
        for ordinal, raw_instrument in enumerate(raw_instruments)
    )
    instrument_keys = [instrument.instrument_key for instrument in instruments]
    if len(instrument_keys) != len(set(instrument_keys)):
        raise _error("/instruments", "unique", "Instrument keys must be unique")
    instrument_map = {instrument.instrument_key: instrument for instrument in instruments}
    _validate_identifier_intervals(instruments)
    raw_classifications = _array(
        root["classifications"], pointer="/classifications", maximum=_MAX_CLASSIFICATIONS
    )
    classifications = tuple(
        _parse_classification(
            raw_classification,
            pointer=f"/classifications/{ordinal}",
            source_row=ordinal + 1,
        )
        for ordinal, raw_classification in enumerate(raw_classifications)
    )
    classification_keys = [
        (
            item.instrument_key,
            item.classification_provider,
            item.effective_from,
            item.effective_through,
        )
        for item in classifications
    ]
    if len(classification_keys) != len(set(classification_keys)):
        raise _error("/classifications", "unique", "Classification natural keys must be unique")
    universe = _parse_universe(root["universe"])
    tombstone_authoritative = _tombstone_authoritative(fixture, universe)
    scope = _fixture_scope(fixture, universe, tombstone_authoritative)
    index_manifest = _parse_index_manifest(
        root["index_manifest"], instrument_map, provider=fixture.provider
    )
    if scope["scope_kind"] == "catalog_and_universe" and not index_manifest:
        raise _error(
            "/index_manifest",
            "required",
            "Catalog-and-universe fixtures require the exact recovered index manifest",
        )
    payload = _CatalogPayload(
        instruments=tuple(sorted(instruments, key=lambda item: item.instrument_key)),
        classifications=tuple(
            sorted(
                classifications,
                key=lambda item: (
                    item.instrument_key,
                    item.classification_provider,
                    item.effective_from,
                    item.effective_through or "",
                ),
            )
        ),
        index_manifest=index_manifest,
        universe=universe,
        tombstone_authoritative=tombstone_authoritative,
    )
    return payload, scope, captured, _normalization_version(fixture)


def _scope_digest(scope: Mapping[str, object]) -> str:
    return hashlib.sha256(dumps_strict(dict(scope)).encode("utf-8")).hexdigest()


def _semantic_identity(
    fixture: Fixture,
    *,
    payload: _CatalogPayload,
    scope: Mapping[str, object],
    normalization_version: str,
) -> str:
    """Canonical identity excludes only reviewed physical-order/capture fields."""

    material = {
        "ingestion_family_id": fixture.ingestion_family_id,
        "canonical_dataset_id": fixture.canonical_dataset_id,
        "provider": fixture.provider,
        "scope": dict(scope),
        "normalization_version": normalization_version,
        "instruments": [item.semantic_mapping() for item in payload.instruments],
        "classifications": [item.semantic_mapping() for item in payload.classifications],
        "index_manifest": [item.semantic_mapping() for item in payload.index_manifest],
        "universe": payload.universe.semantic_mapping(),
    }
    return hashlib.sha256(dumps_strict(material).encode("utf-8")).hexdigest()


def _instrument_id(identity_dataset_id: str, instrument_key: str) -> str:
    return stable_id("instrument", identity_dataset_id, _opaque_binding(instrument_key))


def _same_nullable(left: object, right: object) -> bool:
    return left == right


def _assert_existing_identifier_compatibility(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    identifier: _CatalogIdentifier,
) -> None:
    """Fail closed before an identifier can point a ticker at another identity."""

    matches = list(
        connection.execute(
            """
            SELECT identifier_id, instrument_id, valid_from, valid_through,
                   available_at, available_precision
            FROM instrument_identifiers
            WHERE provider=? AND provider_symbol=?
              AND (valid_through IS NULL OR valid_through>=?)
              AND (? IS NULL OR valid_from<=?)
            ORDER BY valid_from, identifier_id
            """,
            (
                identifier.provider,
                identifier.provider_symbol,
                identifier.valid_from,
                identifier.valid_through,
                identifier.valid_through,
            ),
        )
    )
    for row in matches:
        if str(row["instrument_id"]) != instrument_id:
            raise ConflictError("Provider identifier overlaps a different stable instrument")
        if str(row["valid_from"]) != identifier.valid_from:
            raise ConflictError("Provider identifier overlaps an existing identifier interval")
        if (
            not _same_nullable(row["valid_through"], identifier.valid_through)
            or str(row["available_at"]) != identifier.available_at.raw
            or str(row["available_precision"]) != identifier.available_at.precision.value
        ):
            raise ConflictError("Provider identifier conflicts with immutable prior evidence")


def _ensure_instrument(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    instrument: _CatalogInstrument,
    run_id: str,
) -> int:
    existing = connection.execute(
        """
        SELECT instrument_id, asset_type, canonical_symbol, display_name,
               exchange, currency, country, first_seen_at, last_seen_at, active
        FROM instruments WHERE instrument_id=?
        """,
        (instrument_id,),
    ).fetchone()
    desired = {
        "canonical_symbol": instrument.canonical_symbol,
        "display_name": instrument.display_name,
        "exchange": instrument.exchange,
        "currency": instrument.currency,
        "country": instrument.country,
        "first_seen_at": instrument.first_seen_at,
        "last_seen_at": instrument.last_seen_at,
        "active": int(instrument.active),
    }
    if existing is None:
        connection.execute(
            """
            INSERT INTO instruments (
                instrument_id, asset_type, created_run_id, canonical_symbol,
                display_name, exchange, currency, country, first_seen_at,
                last_seen_at, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument_id,
                instrument.asset_type,
                run_id,
                desired["canonical_symbol"],
                desired["display_name"],
                desired["exchange"],
                desired["currency"],
                desired["country"],
                desired["first_seen_at"],
                desired["last_seen_at"],
                desired["active"],
            ),
        )
        return 1
    if str(existing["asset_type"]) != instrument.asset_type:
        raise ConflictError("Stable instrument identity conflicts with an existing asset type")
    # Optional catalog attributes are additive: omission in a later bounded
    # snapshot cannot erase already reviewed catalog information.
    changed: dict[str, object] = {
        field: value
        for field, value in desired.items()
        if (
            field in {"canonical_symbol", "display_name", "active"}
            or value is not None
        )
        and existing[field] != value
    }
    if not changed:
        return 0
    assignments = ", ".join(f"{field}=?" for field in changed)
    connection.execute(
        f"UPDATE instruments SET {assignments} WHERE instrument_id=?",
        (*changed.values(), instrument_id),
    )
    return 1


def _ensure_identifier(
    connection: sqlite3.Connection,
    *,
    instrument_id: str,
    identifier: _CatalogIdentifier,
    artifact_id: str,
    snapshot_id: str,
    run_id: str,
) -> int:
    _assert_existing_identifier_compatibility(
        connection,
        instrument_id=instrument_id,
        identifier=identifier,
    )
    existing = connection.execute(
        """
        SELECT identifier_id, instrument_id, valid_through, available_at,
               available_precision
        FROM instrument_identifiers
        WHERE provider=? AND provider_symbol=? AND valid_from=?
        """,
        (identifier.provider, identifier.provider_symbol, identifier.valid_from),
    ).fetchone()
    if existing is not None:
        if str(existing["instrument_id"]) != instrument_id:
            raise ConflictError("Provider identifier is already bound to another instrument")
        if (
            not _same_nullable(existing["valid_through"], identifier.valid_through)
            or str(existing["available_at"]) != identifier.available_at.raw
            or str(existing["available_precision"]) != identifier.available_at.precision.value
        ):
            raise ConflictError("Provider identifier conflicts with immutable prior evidence")
        # Stage 2 identifiers intentionally have no catalog snapshot lineage;
        # do not mutate them merely to attach a later catalog fixture.
        return 0
    identifier_id = stable_id(
        "instrument_identifier",
        instrument_id,
        identifier.provider,
        identifier.provider_symbol,
        identifier.valid_from,
    )
    connection.execute(
        """
        INSERT INTO instrument_identifiers (
            identifier_id, instrument_id, provider, provider_symbol, valid_from,
            valid_through, available_at, available_precision, evidence_id, run_id,
            confirmation_state, source_snapshot_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identifier_id,
            instrument_id,
            identifier.provider,
            identifier.provider_symbol,
            identifier.valid_from,
            identifier.valid_through,
            identifier.available_at.raw,
            identifier.available_at.precision.value,
            artifact_id,
            run_id,
            identifier.confirmation_state,
            snapshot_id,
        ),
    )
    return 1


def _instrument_exists(connection: sqlite3.Connection, instrument_id: str) -> None:
    if connection.execute(
        "SELECT 1 FROM instruments WHERE instrument_id=?", (instrument_id,)
    ).fetchone() is None:
        raise ValidationError("Catalog fact references an unknown stable instrument")


def _apply_classification(
    connection: sqlite3.Connection,
    *,
    classification: _Classification,
    instrument_id: str,
    snapshot_id: str,
    semantic_identity: str,
    run_id: str,
) -> int:
    _instrument_exists(connection, instrument_id)
    current = connection.execute(
        """
        SELECT classification_version_id, correction_sequence, sector, industry,
               available_at, available_precision
        FROM instrument_classifications
        WHERE instrument_id=? AND classification_provider=? AND effective_from=?
          AND COALESCE(effective_through, '')=COALESCE(?, '')
        ORDER BY correction_sequence DESC, classification_version_id DESC
        LIMIT 1
        """,
        (
            instrument_id,
            classification.classification_provider,
            classification.effective_from,
            classification.effective_through,
        ),
    ).fetchone()
    if current is not None and (
        _same_nullable(current["sector"], classification.sector)
        and _same_nullable(current["industry"], classification.industry)
        and str(current["available_at"]) == classification.available_at.raw
        and str(current["available_precision"]) == classification.available_at.precision.value
    ):
        return 0
    correction_sequence = 1 if current is None else int(current["correction_sequence"]) + 1
    supersedes = None if current is None else str(current["classification_version_id"])
    version_id = stable_id(
        "instrument_classification",
        semantic_identity,
        instrument_id,
        classification.classification_provider,
        classification.effective_from,
        classification.effective_through or "",
        str(correction_sequence),
    )
    connection.execute(
        """
        INSERT INTO instrument_classifications (
            classification_version_id, instrument_id, sector, industry,
            classification_provider, effective_from, effective_through,
            available_at, available_precision, correction_sequence,
            supersedes_version_id, source_snapshot_id, run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            instrument_id,
            classification.sector,
            classification.industry,
            classification.classification_provider,
            classification.effective_from,
            classification.effective_through,
            classification.available_at.raw,
            classification.available_at.precision.value,
            correction_sequence,
            supersedes,
            snapshot_id,
            run_id,
            classification.source_row,
        ),
    )
    return 1


def _ensure_universe(
    connection: sqlite3.Connection,
    *,
    universe_id: str,
    universe: _Universe,
    source_name: str,
    run_id: str,
) -> int:
    existing = connection.execute(
        "SELECT name, source_name FROM market_universes WHERE universe_id=?", (universe_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO market_universes (universe_id, name, source_name, created_run_id)
            VALUES (?, ?, ?, ?)
            """,
            (universe_id, universe.display_name, source_name, run_id),
        )
        return 1
    if str(existing["name"]) != universe.display_name or str(existing["source_name"]) != source_name:
        raise ConflictError("Controlled universe identity conflicts with prior catalog evidence")
    return 0


def _current_membership(
    connection: sqlite3.Connection,
    *,
    universe_id: str,
    instrument_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT version.membership_version_id, version.effective_from,
               version.effective_through, version.available_at,
               version.available_precision, version.state, version.version_sequence
        FROM market_universe_memberships AS current
        JOIN market_universe_membership_versions AS version
          ON version.membership_version_id=current.current_version_id
        WHERE current.universe_id=? AND current.instrument_id=?
        """,
        (universe_id, instrument_id),
    ).fetchone()


def _insert_snapshot_membership(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    membership_version_id: str,
    source_row: int,
) -> None:
    connection.execute(
        """
        INSERT INTO market_universe_snapshot_memberships (
            snapshot_id, membership_version_id, source_row
        ) VALUES (?, ?, ?)
        """,
        (snapshot_id, membership_version_id, source_row),
    )


def _append_membership_version(
    connection: sqlite3.Connection,
    *,
    universe_id: str,
    instrument_id: str,
    effective_from: str,
    effective_through: str | None,
    available_at: TemporalValue,
    state: str,
    previous: sqlite3.Row | None,
    snapshot_id: str,
    semantic_identity: str,
    run_id: str,
    source_row: int,
) -> int:
    if previous is None:
        sequence = 1
        supersedes = None
        if state != "active":
            raise ValidationError("Initial universe membership must be active")
    else:
        sequence = int(previous["version_sequence"]) + 1
        supersedes = str(previous["membership_version_id"])
        if str(previous["state"]) == state:
            raise ConflictError("Universe membership transitions must alternate active and tombstone")
    version_id = stable_id(
        "market_universe_membership",
        semantic_identity,
        universe_id,
        instrument_id,
        state,
        str(sequence),
    )
    connection.execute(
        """
        INSERT INTO market_universe_membership_versions (
            membership_version_id, universe_id, instrument_id, effective_from,
            effective_through, available_at, available_precision, state,
            version_sequence, supersedes_membership_version_id, source_snapshot_id,
            run_id, source_row
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            universe_id,
            instrument_id,
            effective_from,
            effective_through,
            available_at.raw,
            available_at.precision.value,
            state,
            sequence,
            supersedes,
            snapshot_id,
            run_id,
            source_row,
        ),
    )
    if previous is None:
        connection.execute(
            """
            INSERT INTO market_universe_memberships (
                universe_id, instrument_id, current_version_id
            ) VALUES (?, ?, ?)
            """,
            (universe_id, instrument_id, version_id),
        )
    else:
        connection.execute(
            """
            UPDATE market_universe_memberships SET current_version_id=?
            WHERE universe_id=? AND instrument_id=?
            """,
            (version_id, universe_id, instrument_id),
        )
    _insert_snapshot_membership(
        connection,
        snapshot_id=snapshot_id,
        membership_version_id=version_id,
        source_row=source_row,
    )
    return 1


def _apply_universe(
    connection: sqlite3.Connection,
    *,
    fixture: Fixture,
    payload: _CatalogPayload,
    snapshot_id: str,
    semantic_identity: str,
    run_id: str,
) -> int:
    universe = payload.universe
    universe_id = stable_id("market_universe", fixture.identity_dataset_id or "", universe.universe_key)
    changes = _ensure_universe(
        connection,
        universe_id=universe_id,
        universe=universe,
        source_name=fixture.provider,
        run_id=run_id,
    )
    present_instrument_ids: set[str] = set()
    for membership in universe.members:
        instrument_id = _instrument_id(fixture.identity_dataset_id or "", membership.instrument_key)
        _instrument_exists(connection, instrument_id)
        present_instrument_ids.add(instrument_id)
        current = _current_membership(
            connection,
            universe_id=universe_id,
            instrument_id=instrument_id,
        )
        if current is None:
            changes += _append_membership_version(
                connection,
                universe_id=universe_id,
                instrument_id=instrument_id,
                effective_from=membership.effective_from,
                effective_through=membership.effective_through,
                available_at=universe.available_at,
                state="active",
                previous=None,
                snapshot_id=snapshot_id,
                semantic_identity=semantic_identity,
                run_id=run_id,
                source_row=membership.source_row,
            )
            continue
        if str(current["state"]) == "tombstone":
            changes += _append_membership_version(
                connection,
                universe_id=universe_id,
                instrument_id=instrument_id,
                effective_from=membership.effective_from,
                effective_through=membership.effective_through,
                available_at=universe.available_at,
                state="active",
                previous=current,
                snapshot_id=snapshot_id,
                semantic_identity=semantic_identity,
                run_id=run_id,
                source_row=membership.source_row,
            )
            continue
        if (
            str(current["effective_from"]) != membership.effective_from
            or not _same_nullable(current["effective_through"], membership.effective_through)
        ):
            raise ConflictError(
                "An active universe membership cannot change effective bounds without a tombstone transition"
            )
        _insert_snapshot_membership(
            connection,
            snapshot_id=snapshot_id,
            membership_version_id=str(current["membership_version_id"]),
            source_row=membership.source_row,
        )
        changes += 1

    if not payload.tombstone_authoritative:
        return changes
    current_active = list(
        connection.execute(
            """
            SELECT current.instrument_id, version.membership_version_id,
                   version.effective_from, version.effective_through,
                   version.available_at, version.available_precision,
                   version.state, version.version_sequence
            FROM market_universe_memberships AS current
            JOIN market_universe_membership_versions AS version
              ON version.membership_version_id=current.current_version_id
            WHERE current.universe_id=? AND version.state='active'
            ORDER BY current.instrument_id
            """,
            (universe_id,),
        )
    )
    next_source_row = len(universe.members) + 1
    for ordinal, current in enumerate(current_active):
        instrument_id = str(current["instrument_id"])
        if instrument_id in present_instrument_ids:
            continue
        # A complete authoritative omission tells us this version is no
        # longer current.  Its source-effective interval is preserved rather
        # than invented from the snapshot availability timestamp.
        changes += _append_membership_version(
            connection,
            universe_id=universe_id,
            instrument_id=instrument_id,
            effective_from=str(current["effective_from"]),
            effective_through=(
                None if current["effective_through"] is None else str(current["effective_through"])
            ),
            available_at=universe.available_at,
            state="tombstone",
            previous=current,
            snapshot_id=snapshot_id,
            semantic_identity=semantic_identity,
            run_id=run_id,
            source_row=next_source_row + ordinal,
        )
    return changes


class MarketCatalogFixtureImporter:
    """Import a reviewed, offline market catalog fixture through Stage 2 coordination."""

    def __init__(self, store_map: StoreMap, fixture_manifest: FixtureManifest) -> None:
        self.store_map = store_map
        self.fixture_manifest = fixture_manifest
        self._coordinator = IngestionCoordinator(store_map)

    def import_fixture(self, fixture_id: str) -> IngestionReceipt:
        fixture = self.fixture_manifest.get(fixture_id)
        payload, scope, captured, normalization_version = _parse_fixture(fixture)
        semantic_identity = _semantic_identity(
            fixture,
            payload=payload,
            scope=scope,
            normalization_version=normalization_version,
        )
        if semantic_identity != fixture.expected_semantic_identity:
            raise ValidationError("Catalog fixture semantic identity does not match its reviewed manifest pin")
        scope_digest = _scope_digest(scope)
        run_id = stable_id("ingestion_run", fixture.canonical_dataset_id, semantic_identity)
        artifact_id = stable_id(
            "market_catalog_artifact",
            fixture.evidence_dataset_id,
            fixture.sha256,
            scope_digest,
        )
        snapshot_id = stable_id(
            "market_catalog_snapshot", fixture.evidence_dataset_id, semantic_identity
        )
        fetched_count = (
            len(payload.instruments) + len(payload.classifications) + len(payload.universe.members)
        )

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            if active_run_id != run_id:
                raise ValidationError("Ingestion coordinator supplied an unexpected run identity")
            # The artifact FK is DEFERRABLE: the coordinator publishes the
            # immutable generic artifact later in this same transaction.
            connection.execute(
                """
                INSERT INTO market_instrument_catalog_snapshots (
                    snapshot_id, semantic_identity, artifact_id, scope_json,
                    scope_digest, completeness, tombstone_authoritative,
                    captured_at, captured_precision, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    semantic_identity,
                    artifact_id,
                    dumps_strict(scope),
                    scope_digest,
                    payload.universe.completeness,
                    int(payload.tombstone_authoritative),
                    captured.raw,
                    captured.precision.value,
                    active_run_id,
                ),
            )
            changes = 1
            instrument_ids = {
                instrument.instrument_key: _instrument_id(
                    fixture.identity_dataset_id or "", instrument.instrument_key
                )
                for instrument in payload.instruments
            }
            # Resolve pre-existing provider identifiers before making any
            # catalog mutation.  A ticker change cannot silently become a new
            # stable instrument or overlap a different one.
            for instrument in payload.instruments:
                target_id = instrument_ids[instrument.instrument_key]
                for identifier in instrument.identifiers:
                    _assert_existing_identifier_compatibility(
                        connection,
                        instrument_id=target_id,
                        identifier=identifier,
                    )
            for instrument in payload.instruments:
                changes += _ensure_instrument(
                    connection,
                    instrument_id=instrument_ids[instrument.instrument_key],
                    instrument=instrument,
                    run_id=active_run_id,
                )
            for instrument in payload.instruments:
                target_id = instrument_ids[instrument.instrument_key]
                for identifier in instrument.identifiers:
                    changes += _ensure_identifier(
                        connection,
                        instrument_id=target_id,
                        identifier=identifier,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        run_id=active_run_id,
                    )
            for classification in payload.classifications:
                changes += _apply_classification(
                    connection,
                    classification=classification,
                    instrument_id=_instrument_id(
                        fixture.identity_dataset_id or "", classification.instrument_key
                    ),
                    snapshot_id=snapshot_id,
                    semantic_identity=semantic_identity,
                    run_id=active_run_id,
                )
            changes += _apply_universe(
                connection,
                fixture=fixture,
                payload=payload,
                snapshot_id=snapshot_id,
                semantic_identity=semantic_identity,
                run_id=active_run_id,
            )
            return WriteResult(
                written_count=changes,
                artifacts=(
                    ArtifactWrite(
                        artifact_id=artifact_id,
                        dataset_id=fixture.evidence_dataset_id,
                        content_sha256=fixture.sha256,
                        media_type="application/json",
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
                    completeness=payload.universe.completeness,
                    row_count=fetched_count,
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
                            "fixture.catalog_batch_contract",
                            "1.0.0",
                        ),
                        dataset_id=fixture.canonical_dataset_id,
                        rule_id="fixture.catalog_batch_contract",
                        rule_version="1.0.0",
                        severity="informational",
                        outcome="passed",
                        subject_kind="snapshot",
                        subject_id=snapshot_id,
                        artifact_id=artifact_id,
                        snapshot_id=snapshot_id,
                        observed={
                            "fetched_count": fetched_count,
                            "written_count": changes,
                            "completeness": payload.universe.completeness,
                            "tombstone_authoritative": payload.tombstone_authoritative,
                        },
                    ),
                ),
            )

        return self._coordinator.execute(
            role=StoreRole.MARKET,
            dataset_id=fixture.canonical_dataset_id,
            output_dataset_ids=(
                fixture.evidence_dataset_id,
                fixture.identity_dataset_id or "",
                _CLASSIFICATION_DATASET_ID,
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
            fetched_count=fetched_count,
            writer=writer,
        )


def _policy(value: DateOnlyPolicy | str) -> DateOnlyPolicy:
    try:
        return DateOnlyPolicy(value)
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


def _cutoff(value: str | None) -> TemporalValue | None:
    if value is None:
        return None
    return TemporalValue.parse(value, pointer="/as_of")


def _bounded_limit(value: object, *, maximum: int = _MAX_READ_LIMIT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValidationError(
            "Catalog read limit is outside the supported range",
            issues=(Issue("/limit", "range", f"Expected an integer from 1 through {maximum}"),),
        )
    return value


def _stored_temporal(row: sqlite3.Row, *, value_name: str, precision_name: str) -> TemporalValue:
    value = TemporalValue.parse(str(row[value_name]), pointer=f"/stored/{value_name}")
    if value.precision.value != row[precision_name]:
        raise ValidationError("Stored market temporal precision is inconsistent")
    return value


def _included_at_cutoff(
    row: sqlite3.Row,
    *,
    cutoff: TemporalValue | None,
    policy: DateOnlyPolicy,
) -> tuple[bool, tuple[str, ...]]:
    if cutoff is None:
        return True, ()
    decision = availability_at_or_before(
        _stored_temporal(row, value_name="available_at", precision_name="available_precision"),
        cutoff,
        policy,
    )
    return decision.included, decision.warnings


def _instrument_projection(row: sqlite3.Row) -> dict[str, object]:
    return {
        "instrument_id": str(row["instrument_id"]),
        "asset_type": str(row["asset_type"]),
        "canonical_symbol": None
        if row["canonical_symbol"] is None
        else str(row["canonical_symbol"]),
        "display_name": None if row["display_name"] is None else str(row["display_name"]),
        "active": bool(row["active"]),
    }


class MarketCatalogRepository:
    """Host-routed, bounded read-only catalog and controlled-universe access."""

    def __init__(self, store_map: StoreMap, registry: Registry | None = None) -> None:
        self.store_map = store_map
        # Registry routing is centralized in StoreMap/StoreRole.  Retaining an
        # optional registry mirror keeps the constructor ergonomic alongside
        # the Stage 2 repository without giving callers a path or SQL handle.
        self.registry = registry

    def list_recovered_fmp_indexes(
        self,
        *,
        effective_date: str,
        provider: str = "fixture_fmp",
        as_of: str | None = None,
        date_only_policy: DateOnlyPolicy | str = DateOnlyPolicy.COMPLETED_DATE,
        limit: int = len(RECOVERED_FMP_INDEXES),
    ) -> dict[str, object]:
        date_text = _date(effective_date, pointer="/effective_date")
        provider_text = _nonempty(provider, pointer="/provider")
        if limit > len(RECOVERED_FMP_INDEXES):
            raise ValidationError(
                "Recovered FMP index limit cannot exceed the exact manifest",
                issues=(Issue("/limit", "range", "Expected an integer from 1 through 9"),),
            )
        limit = _bounded_limit(limit, maximum=len(RECOVERED_FMP_INDEXES))
        policy = _policy(date_only_policy)
        cutoff = _cutoff(as_of)
        selected: list[dict[str, object]] = []
        warnings: set[str] = set()
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            for provider_symbol, canonical_name in RECOVERED_FMP_INDEXES.items():
                candidates = list(
                    connection.execute(
                        """
                        SELECT ii.provider, ii.provider_symbol, ii.valid_from,
                               ii.valid_through, ii.available_at, ii.available_precision,
                               i.instrument_id, i.asset_type, i.canonical_symbol,
                               i.display_name, i.active
                        FROM instrument_identifiers AS ii
                        JOIN instruments AS i ON i.instrument_id=ii.instrument_id
                        WHERE ii.provider=? AND ii.provider_symbol=?
                          AND ii.valid_from<=?
                          AND (ii.valid_through IS NULL OR ii.valid_through>=?)
                        ORDER BY ii.valid_from DESC, ii.identifier_id
                        LIMIT 3
                        """,
                        (provider_text, provider_symbol, date_text, date_text),
                    )
                )
                eligible: list[sqlite3.Row] = []
                for candidate in candidates:
                    included, candidate_warnings = _included_at_cutoff(
                        candidate, cutoff=cutoff, policy=policy
                    )
                    if included:
                        eligible.append(candidate)
                        warnings.update(candidate_warnings)
                if not eligible:
                    continue
                if len(eligible) != 1:
                    raise ConflictError("Recovered FMP index identifier is ambiguous")
                row = eligible[0]
                if str(row["asset_type"]) != "index" or row["display_name"] != canonical_name:
                    raise ConflictError("Recovered FMP index manifest conflicts with catalog identity")
                selected.append(
                    {
                        **_instrument_projection(row),
                        "provider": str(row["provider"]),
                        "provider_symbol": str(row["provider_symbol"]),
                        "valid_from": str(row["valid_from"]),
                        "valid_through": (
                            None if row["valid_through"] is None else str(row["valid_through"])
                        ),
                        "availability": _stored_temporal(
                            row,
                            value_name="available_at",
                            precision_name="available_precision",
                        ).to_dict(),
                    }
                )
        if cutoff is None and len(selected) != len(RECOVERED_FMP_INDEXES):
            raise ValidationError("Recovered FMP index manifest is incomplete in the market catalog")
        return {
            "provider": provider_text,
            "effective_date": date_text,
            "as_of": None if cutoff is None else cutoff.raw,
            "date_only_policy": policy.value,
            "warnings": sorted(warnings),
            "indexes": selected[:limit],
        }

    # Two intentionally thin aliases keep callers focused on the frozen
    # exact-manifest behavior rather than a generic index-search surface.
    def list_indexes(self, **kwargs: object) -> dict[str, object]:
        return self.list_recovered_fmp_indexes(**kwargs)  # type: ignore[arg-type]

    def list_exact_fmp_indexes(self, **kwargs: object) -> dict[str, object]:
        return self.list_recovered_fmp_indexes(**kwargs)  # type: ignore[arg-type]

    def resolve_provider_identifier(
        self,
        *,
        provider: str,
        provider_symbol: str,
        effective_date: str,
        as_of: str | None = None,
        date_only_policy: DateOnlyPolicy | str = DateOnlyPolicy.COMPLETED_DATE,
    ) -> dict[str, object] | None:
        provider_text = _nonempty(provider, pointer="/provider")
        provider_symbol_text = _nonempty(provider_symbol, pointer="/provider_symbol")
        date_text = _date(effective_date, pointer="/effective_date")
        policy = _policy(date_only_policy)
        cutoff = _cutoff(as_of)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            candidates = list(
                connection.execute(
                    """
                    SELECT ii.identifier_id, ii.provider, ii.provider_symbol,
                           ii.valid_from, ii.valid_through, ii.available_at,
                           ii.available_precision, ii.confirmation_state,
                           i.instrument_id, i.asset_type, i.canonical_symbol,
                           i.display_name, i.active
                    FROM instrument_identifiers AS ii
                    JOIN instruments AS i ON i.instrument_id=ii.instrument_id
                    WHERE ii.provider=? AND ii.provider_symbol=?
                      AND ii.valid_from<=?
                      AND (ii.valid_through IS NULL OR ii.valid_through>=?)
                    ORDER BY ii.valid_from DESC, ii.identifier_id
                    LIMIT 3
                    """,
                    (provider_text, provider_symbol_text, date_text, date_text),
                )
            )
        eligible: list[sqlite3.Row] = []
        warnings: set[str] = set()
        for candidate in candidates:
            included, candidate_warnings = _included_at_cutoff(
                candidate, cutoff=cutoff, policy=policy
            )
            if included:
                eligible.append(candidate)
                warnings.update(candidate_warnings)
        if not eligible:
            return None
        if len(eligible) != 1:
            raise ConflictError("Provider identifier is ambiguous for the effective date")
        row = eligible[0]
        return {
            **_instrument_projection(row),
            "identifier_id": str(row["identifier_id"]),
            "provider": str(row["provider"]),
            "provider_symbol": str(row["provider_symbol"]),
            "valid_from": str(row["valid_from"]),
            "valid_through": None if row["valid_through"] is None else str(row["valid_through"]),
            "confirmation_state": (
                None if row["confirmation_state"] is None else str(row["confirmation_state"])
            ),
            "availability": _stored_temporal(
                row,
                value_name="available_at",
                precision_name="available_precision",
            ).to_dict(),
            "as_of": None if cutoff is None else cutoff.raw,
            "date_only_policy": policy.value,
            "warnings": sorted(warnings),
        }

    def resolve_identifier(self, **kwargs: object) -> dict[str, object] | None:
        return self.resolve_provider_identifier(**kwargs)  # type: ignore[arg-type]

    def get_classification(
        self,
        *,
        instrument_id: str,
        classification_provider: str,
        effective_date: str,
        as_of: str | None = None,
        date_only_policy: DateOnlyPolicy | str = DateOnlyPolicy.COMPLETED_DATE,
    ) -> dict[str, object] | None:
        instrument_text = _nonempty(instrument_id, pointer="/instrument_id")
        provider_text = _nonempty(classification_provider, pointer="/classification_provider")
        date_text = _date(effective_date, pointer="/effective_date")
        policy = _policy(date_only_policy)
        cutoff = _cutoff(as_of)
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            if connection.execute(
                "SELECT 1 FROM instruments WHERE instrument_id=?", (instrument_text,)
            ).fetchone() is None:
                raise ValidationError("Unknown stable market instrument")
            candidates = list(
                connection.execute(
                    """
                    SELECT classification_version_id, instrument_id, sector, industry,
                           classification_provider, effective_from, effective_through,
                           available_at, available_precision, correction_sequence
                    FROM instrument_classifications
                    WHERE instrument_id=? AND classification_provider=?
                      AND effective_from<=?
                      AND (effective_through IS NULL OR effective_through>=?)
                    ORDER BY effective_from, COALESCE(effective_through, ''),
                             correction_sequence DESC, classification_version_id DESC
                    LIMIT ?
                    """,
                    (instrument_text, provider_text, date_text, date_text, _MAX_AS_OF_CANDIDATES + 1),
                )
            )
        if len(candidates) > _MAX_AS_OF_CANDIDATES:
            raise ResourceLimitError("Classification candidate set exceeds the supported bound")
        by_interval: dict[tuple[str, str | None], sqlite3.Row] = {}
        warnings: set[str] = set()
        for candidate in candidates:
            interval = (
                str(candidate["effective_from"]),
                None
                if candidate["effective_through"] is None
                else str(candidate["effective_through"]),
            )
            if interval in by_interval:
                continue
            included, candidate_warnings = _included_at_cutoff(
                candidate, cutoff=cutoff, policy=policy
            )
            if included:
                by_interval[interval] = candidate
                warnings.update(candidate_warnings)
        if not by_interval:
            return None
        if len(by_interval) != 1:
            raise ConflictError("Classification ranges overlap for the effective date")
        row = next(iter(by_interval.values()))
        return {
            "classification_version_id": str(row["classification_version_id"]),
            "instrument_id": str(row["instrument_id"]),
            "classification_provider": str(row["classification_provider"]),
            "sector": None if row["sector"] is None else str(row["sector"]),
            "industry": None if row["industry"] is None else str(row["industry"]),
            "effective_from": str(row["effective_from"]),
            "effective_through": (
                None if row["effective_through"] is None else str(row["effective_through"])
            ),
            "availability": _stored_temporal(
                row,
                value_name="available_at",
                precision_name="available_precision",
            ).to_dict(),
            "as_of": None if cutoff is None else cutoff.raw,
            "date_only_policy": policy.value,
            "warnings": sorted(warnings),
        }

    def get_universe_memberships(
        self,
        *,
        universe_id: str,
        effective_date: str,
        as_of: str | None = None,
        date_only_policy: DateOnlyPolicy | str = DateOnlyPolicy.COMPLETED_DATE,
        limit: int = 100,
    ) -> dict[str, object]:
        universe_text = _nonempty(universe_id, pointer="/universe_id")
        date_text = _date(effective_date, pointer="/effective_date")
        policy = _policy(date_only_policy)
        cutoff = _cutoff(as_of)
        limit = _bounded_limit(limit)
        warnings: set[str] = set()
        selected: list[sqlite3.Row] = []
        with read_connection(self.store_map, StoreRole.MARKET) as connection:
            if connection.execute(
                "SELECT 1 FROM market_universes WHERE universe_id=?", (universe_text,)
            ).fetchone() is None:
                raise ValidationError("Unknown controlled market universe")
            if cutoff is None:
                candidates = list(
                    connection.execute(
                        """
                        SELECT version.membership_version_id, version.universe_id,
                               version.instrument_id, version.effective_from,
                               version.effective_through, version.available_at,
                               version.available_precision, version.state,
                               version.version_sequence, i.asset_type,
                               i.canonical_symbol, i.display_name, i.active
                        FROM market_universe_memberships AS current
                        JOIN market_universe_membership_versions AS version
                          ON version.membership_version_id=current.current_version_id
                        JOIN instruments AS i ON i.instrument_id=version.instrument_id
                        WHERE current.universe_id=? AND version.state='active'
                          AND version.effective_from<=?
                          AND (version.effective_through IS NULL OR version.effective_through>=?)
                        ORDER BY version.instrument_id, version.membership_version_id
                        LIMIT ?
                        """,
                        (universe_text, date_text, date_text, limit + 1),
                    )
                )
                if len(candidates) > limit:
                    raise ResourceLimitError("Universe membership result exceeds the requested limit")
                selected = candidates
            else:
                candidates = list(
                    connection.execute(
                        """
                        SELECT version.membership_version_id, version.universe_id,
                               version.instrument_id, version.effective_from,
                               version.effective_through, version.available_at,
                               version.available_precision, version.state,
                               version.version_sequence, i.asset_type,
                               i.canonical_symbol, i.display_name, i.active
                        FROM market_universe_membership_versions AS version
                        JOIN instruments AS i ON i.instrument_id=version.instrument_id
                        WHERE version.universe_id=?
                          AND version.effective_from<=?
                          AND (version.effective_through IS NULL OR version.effective_through>=?)
                        ORDER BY version.instrument_id, version.version_sequence DESC,
                                 version.membership_version_id DESC
                        LIMIT ?
                        """,
                        (universe_text, date_text, date_text, _MAX_AS_OF_CANDIDATES + 1),
                    )
                )
                if len(candidates) > _MAX_AS_OF_CANDIDATES:
                    raise ResourceLimitError("Universe membership candidate set exceeds the supported bound")
                chosen: dict[str, sqlite3.Row] = {}
                for candidate in candidates:
                    instrument = str(candidate["instrument_id"])
                    if instrument in chosen:
                        continue
                    included, candidate_warnings = _included_at_cutoff(
                        candidate, cutoff=cutoff, policy=policy
                    )
                    if included:
                        chosen[instrument] = candidate
                        warnings.update(candidate_warnings)
                selected = [
                    candidate
                    for _, candidate in sorted(chosen.items())
                    if str(candidate["state"]) == "active"
                ]
                if len(selected) > limit:
                    raise ResourceLimitError("Universe membership result exceeds the requested limit")
        memberships: list[dict[str, object]] = []
        for row in selected:
            memberships.append(
                {
                    **_instrument_projection(row),
                    "membership_version_id": str(row["membership_version_id"]),
                    "effective_from": str(row["effective_from"]),
                    "effective_through": (
                        None if row["effective_through"] is None else str(row["effective_through"])
                    ),
                    "availability": _stored_temporal(
                        row,
                        value_name="available_at",
                        precision_name="available_precision",
                    ).to_dict(),
                }
            )
        return {
            "universe_id": universe_text,
            "effective_date": date_text,
            "as_of": None if cutoff is None else cutoff.raw,
            "date_only_policy": policy.value,
            "warnings": sorted(warnings),
            "memberships": memberships,
        }

    def list_universe_memberships(self, **kwargs: object) -> dict[str, object]:
        return self.get_universe_memberships(**kwargs)  # type: ignore[arg-type]

    def universe_memberships(self, **kwargs: object) -> dict[str, object]:
        return self.get_universe_memberships(**kwargs)  # type: ignore[arg-type]
