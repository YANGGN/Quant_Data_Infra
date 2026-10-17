"""NY Fed aggregate Primary Dealer Statistics normalization.

The official catalog supplies stable seriesbreak/keyid identities. This module
maps the four reviewed aggregate families onto the existing generic macro
observation model and contains no provider or store access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Final, Mapping

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..temporal import TemporalPrecision, TemporalValue, parse_date
from .official_conditions import (
    OfficialConditionsCapture,
    OfficialObservation,
    OfficialResponsePart,
    OfficialSeriesSpec,
)


SOURCE_KEY: Final = "nyfed_primary_dealer_statistics"
PROVIDER: Final = "nyfed_primary_dealer_statistics"
COLLECTOR_ID: Final = "nyfed.macro.primary_dealer_statistics_history"
HANDLER: Final = "macro.nyfed_primary_dealer_statistics_history"
NORMALIZATION_VERSION: Final = "nyfed_primary_dealer_statistics_v1"
SOURCE_REFERENCE: Final = (
    "nyfed/markets-data-api/pd/list/timeseries+get/{seriesbreak}/timeseries"
)
ARTIFACT_MEDIA_TYPE: Final = "application/vnd.quant-data.json-bundle"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024
MAX_CATALOG_ROWS: Final = 5_000
MAX_RESPONSE_ROWS: Final = 100_000
MAX_RESPONSE_PARTS: Final = 33
MAX_SERIES_KEYS: Final = 1_600
MAX_WINDOW_DAYS: Final = 5_000

_SERIES_BREAK = re.compile(r"SBN[0-9]{4}")
_KEY_ID = re.compile(r"[A-Z0-9][A-Z0-9-]{0,95}")


PRIMARY_DEALER_MANIFEST: Final = (
    OfficialSeriesSpec(
        "macro.nyfed.primary_dealer.positions",
        "positions",
        "Primary dealer aggregate positions",
        "weekly",
        "usd_millions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.nyfed.primary_dealer.transactions",
        "transactions",
        "Primary dealer aggregate transactions",
        "weekly",
        "usd_millions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.nyfed.primary_dealer.financing",
        "financing",
        "Primary dealer aggregate financing",
        "weekly",
        "usd_millions",
        "amount",
    ),
    OfficialSeriesSpec(
        "macro.nyfed.primary_dealer.settlement_fails",
        "settlement_fails",
        "Primary dealer aggregate settlement fails",
        "weekly",
        "usd_millions",
        "amount",
    ),
)
_SPEC_BY_FAMILY: Final = {
    item.provider_code: item for item in PRIMARY_DEALER_MANIFEST
}
_FAMILY_ORDINAL: Final = {
    item.provider_code: ordinal
    for ordinal, item in enumerate(PRIMARY_DEALER_MANIFEST)
}


@dataclass(frozen=True, slots=True)
class NyFedPrimaryDealerSourceMetadata:
    """The exact metadata needed by the shared generic-source hook."""

    key: str
    provider: str
    collector_id: str
    handler: str
    normalization_version: str
    source_reference: str
    artifact_media_type: str
    manifest: tuple[OfficialSeriesSpec, ...]
    max_requests: int
    configuration_env: tuple[str, ...]


PRIMARY_DEALER_SOURCE_METADATA: Final = NyFedPrimaryDealerSourceMetadata(
    key=SOURCE_KEY,
    provider=PROVIDER,
    collector_id=COLLECTOR_ID,
    handler=HANDLER,
    normalization_version=NORMALIZATION_VERSION,
    source_reference=SOURCE_REFERENCE,
    artifact_media_type=ARTIFACT_MEDIA_TYPE,
    manifest=PRIMARY_DEALER_MANIFEST,
    max_requests=MAX_RESPONSE_PARTS,
    configuration_env=(),
)


@dataclass(frozen=True, slots=True)
class NyFedPrimaryDealerCatalogSeries:
    series_break: str
    key_id: str
    description: str | None
    family: str
    category: str | None
    maturity: str | None


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("NY Fed primary-dealer capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("NY Fed primary-dealer request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def _json(body: bytes, *, label: str) -> Mapping[str, object]:
    if not isinstance(body, bytes) or not body:
        raise _fail(f"NY Fed primary-dealer {label} must contain JSON bytes")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResourceLimitError(
            f"NY Fed primary-dealer {label} exceeds its byte bound"
        )
    raw = loads_strict(body, max_bytes=MAX_RESPONSE_BYTES)
    if not isinstance(raw, dict):
        raise _fail(f"NY Fed primary-dealer {label} must be a JSON object")
    return raw


def _text(value: object, *, field: str, maximum: int = 4_096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise _fail(f"NY Fed primary-dealer {field} must be non-empty text")
    return value


def _optional_value(value: object, *, field: str, maximum: int = 4_096) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise _fail(f"NY Fed primary-dealer {field} must be non-empty text")
    if not value.strip():
        return None
    if value != value.strip():
        raise _fail(f"NY Fed primary-dealer {field} must be non-empty text")
    return value


def _optional_text(
    row: Mapping[str, object], primary: str, alias: str
) -> str | None:
    values = [
        _optional_value(row[name], field=primary)
        for name in (primary, alias)
        if name in row
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    if len(set(values)) != 1:
        raise _fail(f"NY Fed primary-dealer {primary} aliases conflict")
    return values[0]


def _description(row: Mapping[str, object]) -> str | None:
    if "description" not in row:
        return None
    return _optional_value(row["description"], field="description")


def _known_family(key_id: str) -> str | None:
    if key_id.startswith("PDFT"):
        return "settlement_fails"
    if key_id.startswith("PDTR"):
        return "transactions"
    if key_id.startswith("PDPOS"):
        return "positions"
    if key_id.startswith("PDSORA"):
        return "financing"
    return None


def _fallback_family(
    category: str | None, description: str | None
) -> str | None:
    context = " ".join(
        item.casefold() for item in (category, description) if item is not None
    )
    if "fail" in context:
        return "settlement_fails"
    if "transaction" in context:
        return "transactions"
    if "position" in context:
        return "positions"
    if (
        "financing" in context
        or "reverse repo" in context
        or "repurchase" in context
        or "securities borrowed" in context
        or "securities lent" in context
    ):
        return "financing"
    return None


def _known_prefix_metadata(
    row: Mapping[str, object],
) -> tuple[str | None, str | None, str | None]:
    try:
        description = _description(row)
    except ValidationError:
        description = None
    try:
        category = _optional_text(row, "category", "seriescategory")
    except ValidationError:
        category = None
    try:
        maturity = _optional_text(row, "maturity", "maturitybucket")
    except ValidationError:
        maturity = None
    return description, category, maturity


def parse_nyfed_primary_dealer_catalog(
    body: bytes, *, series_break: str
) -> tuple[NyFedPrimaryDealerCatalogSeries, ...]:
    """Select the four reviewed aggregate families from the official catalog."""

    if (
        not isinstance(series_break, str)
        or _SERIES_BREAK.fullmatch(series_break) is None
    ):
        raise _fail("NY Fed primary-dealer series break is invalid")
    raw = _json(body, label="catalog")
    pd = raw.get("pd")
    if not isinstance(pd, dict) or not isinstance(pd.get("timeseries"), list):
        raise _fail("NY Fed primary-dealer catalog must contain pd.timeseries")
    rows = pd["timeseries"]
    if len(rows) > MAX_CATALOG_ROWS:
        raise ResourceLimitError(
            "NY Fed primary-dealer catalog exceeds its row bound"
        )
    selected: list[NyFedPrimaryDealerCatalogSeries] = []
    seen: set[tuple[str, str]] = set()
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise _fail("Each NY Fed primary-dealer catalog row must be an object")
        row_break = _text(
            raw_row.get("seriesbreak"), field="seriesbreak", maximum=16
        )
        key_id = _text(raw_row.get("keyid"), field="keyid", maximum=96)
        if (
            _SERIES_BREAK.fullmatch(row_break) is None
            or _KEY_ID.fullmatch(key_id) is None
        ):
            raise _fail("NY Fed primary-dealer catalog identity is invalid")
        identity = (row_break, key_id)
        if identity in seen:
            raise _fail("NY Fed primary-dealer catalog identities must be unique")
        seen.add(identity)
        if row_break != series_break:
            continue
        family = _known_family(key_id)
        if family is None:
            try:
                description = _description(raw_row)
                category = _optional_text(
                    raw_row, "category", "seriescategory"
                )
                maturity = _optional_text(
                    raw_row, "maturity", "maturitybucket"
                )
            except ValidationError:
                continue
            family = _fallback_family(category, description)
        else:
            description, category, maturity = _known_prefix_metadata(
                raw_row
            )
        if family is not None:
            selected.append(
                NyFedPrimaryDealerCatalogSeries(
                    row_break,
                    key_id,
                    description,
                    family,
                    category,
                    maturity,
                )
            )
    if not selected:
        raise _fail("NY Fed primary-dealer selected catalog is empty")
    if len(selected) > MAX_SERIES_KEYS:
        raise ResourceLimitError(
            "NY Fed primary-dealer selected catalog exceeds its key bound"
        )
    selected.sort(key=lambda item: (_FAMILY_ORDINAL[item.family], item.key_id))
    if {item.family for item in selected} != set(_SPEC_BY_FAMILY):
        raise _fail("NY Fed primary-dealer family coverage is incomplete")
    return tuple(selected)


def _date_field(row: Mapping[str, object]) -> str:
    values = [row[name] for name in ("asofdate", "asOfDate") if name in row]
    if len(values) != 1 or not isinstance(values[0], str):
        raise _fail("NY Fed primary-dealer asofdate is invalid")
    return parse_date(values[0], pointer="/pd/timeseries/asofdate").isoformat()


def _value(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_missing"
    if isinstance(value, bool):
        raise _fail("NY Fed primary-dealer value must be decimal or missing")
    if isinstance(value, str):
        raw = value.strip()
        if raw in {"", "*"}:
            return None, "source_missing"
        raw = raw.replace(",", "")
    elif isinstance(value, (int, Decimal, float)):
        raw = str(value)
    else:
        raise _fail("NY Fed primary-dealer value must be decimal or missing")
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(
            "NY Fed primary-dealer value must be decimal or missing"
        ) from exc
    if not parsed.is_finite():
        raise _fail("NY Fed primary-dealer value must be decimal or missing")
    ensure_number_within_limits(parsed)
    normalized = parsed.normalize()
    return ("0" if normalized.is_zero() else format(normalized, "f")), None


def _dimensions(
    metadata: NyFedPrimaryDealerCatalogSeries,
) -> tuple[tuple[str, str], ...]:
    values = {
        "family": metadata.family,
        "official_series_key": metadata.key_id,
        "series_break": metadata.series_break,
    }
    if metadata.description is not None:
        values["official_description"] = metadata.description
    if metadata.category is not None:
        values["category"] = metadata.category
    if metadata.maturity is not None:
        values["maturity"] = metadata.maturity
    return tuple(sorted(values.items()))


def _semantic_material(
    scope: Mapping[str, object],
    observations: tuple[OfficialObservation, ...],
) -> dict[str, object]:
    return {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": PROVIDER,
        "request_scope": dict(scope),
        "series_manifest": [
            {"provider_code": item.provider_code, "series_id": item.series_id}
            for item in PRIMARY_DEALER_MANIFEST
        ],
        "normalized_observations": [
            {
                "series_id": item.series_id,
                "source_period": item.source_period,
                "period_start": item.period_start,
                "period_end": item.period_end,
                "value": item.value_text,
                "missing_reason": item.missing_reason,
                "dimensions": dict(item.dimensions),
            }
            for item in observations
        ],
    }


def parse_nyfed_primary_dealer_statistics(
    catalog_body: bytes,
    history_bodies: tuple[bytes, ...],
    *,
    captured_at: str,
    series_break: str,
    start_date: str,
    end_date: str,
) -> OfficialConditionsCapture:
    """Normalize bounded aggregate weekly history without opening a store."""

    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)
    catalog = parse_nyfed_primary_dealer_catalog(
        catalog_body, series_break=series_break
    )
    if (
        not isinstance(history_bodies, tuple)
        or not history_bodies
        or len(history_bodies) + 1 > MAX_RESPONSE_PARTS
    ):
        raise _fail("NY Fed primary-dealer response part count is invalid")
    if (
        len(catalog_body)
        + sum(
            len(item) if isinstance(item, bytes) else MAX_TOTAL_BYTES + 1
            for item in history_bodies
        )
        > MAX_TOTAL_BYTES
    ):
        raise ResourceLimitError(
            "NY Fed primary-dealer responses exceed their total byte bound"
        )
    catalog_by_key = {item.key_id: item for item in catalog}
    values: list[
        tuple[NyFedPrimaryDealerCatalogSeries, str, str | None, str | None]
    ] = []
    seen: set[tuple[str, str]] = set()
    for page_number, body in enumerate(history_bodies, start=1):
        raw = _json(body, label=f"history page {page_number}")
        pd = raw.get("pd")
        if not isinstance(pd, dict) or not isinstance(pd.get("timeseries"), list):
            raise _fail(
                "NY Fed primary-dealer history must contain pd.timeseries"
            )
        rows = pd["timeseries"]
        if len(rows) > MAX_RESPONSE_ROWS:
            raise ResourceLimitError(
                "NY Fed primary-dealer history exceeds its row bound"
            )
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                raise _fail(
                    "Each NY Fed primary-dealer history row must be an object"
                )
            key_id = _text(raw_row.get("keyid"), field="keyid", maximum=96)
            metadata = catalog_by_key.get(key_id)
            if metadata is None:
                raise _fail(
                    "NY Fed primary-dealer history key is outside catalog scope"
                )
            if (
                "seriesbreak" in raw_row
                and raw_row["seriesbreak"] != series_break
            ):
                raise _fail(
                    "NY Fed primary-dealer history series break conflicts"
                )
            as_of = _date_field(raw_row)
            observed = date.fromisoformat(as_of)
            if observed < start or observed > end:
                continue
            if len(values) >= MAX_RESPONSE_ROWS:
                raise ResourceLimitError(
                    "NY Fed primary-dealer history exceeds its row bound"
                )
            identity = (key_id, as_of)
            if identity in seen:
                raise _fail(
                    "NY Fed primary-dealer observations must be unique"
                )
            seen.add(identity)
            value_text, missing_reason = _value(raw_row.get("value"))
            values.append((metadata, as_of, value_text, missing_reason))
    if not values:
        raise _fail("NY Fed primary-dealer selected history is empty")
    values.sort(
        key=lambda item: (
            item[1],
            _FAMILY_ORDINAL[item[0].family],
            item[0].key_id,
        )
    )
    observations = tuple(
        OfficialObservation(
            series_id=_SPEC_BY_FAMILY[metadata.family].series_id,
            provider_code=metadata.family,
            source_period=as_of,
            period_start=as_of,
            period_end=as_of,
            value_text=value_text,
            missing_reason=missing_reason,
            source_row=source_row,
            dimensions=_dimensions(metadata),
        )
        for source_row, (
            metadata,
            as_of,
            value_text,
            missing_reason,
        ) in enumerate(values, start=1)
    )
    scope = {
        "availability_basis": "local_capture",
        "completeness": "complete",
        "families": [
            item.provider_code for item in PRIMARY_DEALER_MANIFEST
        ],
        "from": start_text,
        "series_break": series_break,
        "series_keys": [item.key_id for item in catalog],
        "to": end_text,
        "tombstone_authoritative": False,
    }
    parts = (
        OfficialResponsePart(
            "catalog", catalog_body, _sha256_bytes(catalog_body)
        ),
        *(
            OfficialResponsePart(
                f"history_{ordinal:04d}", body, _sha256_bytes(body)
            )
            for ordinal, body in enumerate(history_bodies, start=1)
        ),
    )
    artifact_sha256 = _sha256_text(
        dumps_strict(
            [
                {
                    "name": item.name,
                    "sha256": item.sha256,
                    "byte_count": len(item.body),
                }
                for item in parts
            ]
        )
    )
    return OfficialConditionsCapture(
        source_key=SOURCE_KEY,
        parts=parts,
        artifact_sha256=artifact_sha256,
        semantic_identity=_sha256_text(
            dumps_strict(_semantic_material(scope, observations))
        ),
        captured_at=captured,
        request_scope_json=dumps_strict(scope),
        observations=observations,
    )


__all__ = (
    "ARTIFACT_MEDIA_TYPE",
    "COLLECTOR_ID",
    "HANDLER",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_PARTS",
    "MAX_RESPONSE_ROWS",
    "MAX_SERIES_KEYS",
    "MAX_TOTAL_BYTES",
    "MAX_WINDOW_DAYS",
    "NORMALIZATION_VERSION",
    "PRIMARY_DEALER_MANIFEST",
    "PRIMARY_DEALER_SOURCE_METADATA",
    "PROVIDER",
    "SOURCE_KEY",
    "SOURCE_REFERENCE",
    "NyFedPrimaryDealerCatalogSeries",
    "NyFedPrimaryDealerSourceMetadata",
    "parse_nyfed_primary_dealer_catalog",
    "parse_nyfed_primary_dealer_statistics",
)
