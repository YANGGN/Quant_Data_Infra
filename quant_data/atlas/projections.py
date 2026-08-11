"""Fixed, bounded Atlas projections over read-only SQLite copy cohorts."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import math
import sqlite3
import time
from typing import Any, Callable, Mapping, Sequence

from ..errors import ResourceLimitError, ValidationError
from ..json_codec import dumps_strict
from ..stores import StoreMap, StoreRole, read_connection
from ..temporal import (
    DateOnlyPolicy,
    TemporalPrecision,
    TemporalValue,
    availability_at_or_before,
)
from .contracts import AtlasProjection


_EXPECTED_CUTOFF = {
    "source": "export_start",
    "precision": "datetime",
    "timezone": "aware_utc",
    "availability": "at_or_before",
    "date_only_policy": "completed_date",
    "vintage_mode": "as_of",
}
_EXPECTED_QUERY_BOUNDS = {
    "max_total_rows": 12_000,
    "max_bytes": 8 * 1024 * 1024,
    "max_runtime_seconds": 30,
}
_EXPECTED_PROJECTIONS: tuple[dict[str, object], ...] = (
    {
        "id": "market-prices",
        "operation_id": "market.daily_prices",
        "store": "market",
        "dataset": "fixture.market.daily_prices",
        "fields": (
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "available_at",
            "available_precision",
            "captured_at",
            "captured_precision",
            "correction_sequence",
        ),
        "schema_id": "atlas.fixture_snapshot.market_prices.v1",
        "relations": ("prices_daily", "prices_daily_versions"),
        "row_identity": (
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
        ),
        "order_by": (
            "instrument_id",
            "trade_date",
            "provider",
            "price_variant",
            "currency_segment",
            "correction_sequence",
        ),
        "max_rows": 5_000,
    },
    {
        "id": "gdp-vintages",
        "operation_id": "macro.gdp_vintages",
        "store": "macro",
        "dataset": "fixture.macro.gdp_vintages",
        "fields": (
            "source",
            "source_vintage_identity",
            "release_stage",
            "vintage_at",
            "vintage_precision",
            "source_published_at",
            "source_published_precision",
            "available_at",
            "available_precision",
        ),
        "schema_id": "atlas.fixture_snapshot.gdp_vintages.v1",
        "relations": ("gdp_vintages",),
        "row_identity": ("source", "source_vintage_identity"),
        "order_by": (
            "source",
            "vintage_at",
            "source_vintage_identity",
        ),
        "max_rows": 1_000,
    },
    {
        "id": "company-issuers",
        "operation_id": "company.issuers",
        "store": "company",
        "dataset": "fixture.company.issuers",
        "fields": (
            "issuer_id",
            "cik",
            "legal_name",
            "entity_type",
            "name_state",
            "missing_reason",
            "available_at",
            "available_precision",
            "version_sequence",
        ),
        "schema_id": "atlas.fixture_snapshot.company_issuers.v1",
        "relations": ("company_issuers", "company_issuer_versions"),
        "row_identity": ("issuer_id",),
        "order_by": (
            "cik",
            "issuer_id",
            "version_sequence",
        ),
        "max_rows": 1_000,
    },
    {
        "id": "news-items",
        "operation_id": "news.items",
        "store": "news",
        "dataset": "fixture.news.items",
        "fields": (
            "item_id",
            "source_name",
            "source_item_id",
            "source_kind",
            "headline",
            "published_at",
            "published_precision",
            "content_state",
            "content_missing_reason",
            "item_state",
            "retraction_reason",
            "available_at",
            "available_precision",
            "captured_at",
            "captured_precision",
            "version_sequence",
        ),
        "schema_id": "atlas.fixture_snapshot.news_items.v1",
        "relations": ("news_items", "news_item_versions"),
        "row_identity": ("item_id",),
        "order_by": (
            "source_name",
            "source_item_id",
            "version_sequence",
        ),
        "max_rows": 5_000,
    },
)


def _attribute_mapping(value: object, name: str) -> Mapping[str, Any]:
    candidate = getattr(value, name, None)
    if not isinstance(candidate, Mapping):
        raise ValidationError("Atlas export declaration is missing a validated mapping")
    return candidate


def _positive_bound(
    value: object,
    *,
    message: str,
    maximum: int = 10_000,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > maximum
    ):
        raise ValidationError(message)
    return value


_ALLOWED_PUBLIC_TYPES = frozenset(
    {
        "string",
        "date",
        "datetime",
        "temporal_string",
        "temporal_precision",
        "source_temporal_precision",
        "decimal_string",
        "integer",
    }
)
_PRECISION_COMPANIONS = {
    "available_precision": "available_at",
    "captured_precision": "captured_at",
    "vintage_precision": "vintage_at",
    "published_precision": "published_at",
    "source_published_precision": "source_published_at",
}


def _order_contract(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValidationError("Atlas projection order contract is invalid")
    result: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"field", "direction"}
            or not isinstance(item.get("field"), str)
            or item.get("direction") != "asc"
        ):
            raise ValidationError("Atlas projection order contract is invalid")
        result.append((item["field"], item["direction"]))
    return tuple(result)


def _schema_field_contracts(
    export_declaration: object,
    declared: Mapping[str, Any],
) -> tuple[tuple[str, str, bool], ...]:
    schema_contract = _attribute_mapping(export_declaration, "schema_contract")
    if schema_contract.get("serialization") != {
        "encoding": "utf-8",
        "format": "strict_json",
        "non_finite": "reject",
    }:
        raise ValidationError("Atlas schema serialization contract drifted")
    schemas = schema_contract.get("schemas")
    if not isinstance(schemas, (list, tuple)):
        raise ValidationError("Atlas schema contract is invalid")
    matches = [
        schema
        for schema in schemas
        if isinstance(schema, Mapping) and schema.get("id") == declared.get("schema_id")
    ]
    if len(matches) != 1:
        raise ValidationError("Atlas projection schema is not registered exactly once")
    schema = matches[0]
    if set(schema) != {
        "constraints", "fields", "id", "order_by", "row_identity"
    }:
        raise ValidationError("Atlas projection schema contract is invalid")
    raw_fields = schema.get("fields")
    declared_fields = tuple(declared.get("fields", ()))
    if not isinstance(raw_fields, (list, tuple)) or len(raw_fields) != len(declared_fields):
        raise ValidationError("Atlas projection schema field contract is invalid")
    contracts: list[tuple[str, str, bool]] = []
    for item, name in zip(raw_fields, declared_fields, strict=True):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"name", "type", "nullable"}
            or item.get("name") != name
            or not isinstance(item.get("type"), str)
            or item["type"] not in _ALLOWED_PUBLIC_TYPES
            or not isinstance(item.get("nullable"), bool)
        ):
            raise ValidationError("Atlas projection schema type contract drifted")
        contracts.append((str(name), item["type"], item["nullable"]))
    if (
        tuple(schema.get("row_identity", ())) != tuple(declared.get("row_identity", ()))
        or _order_contract(schema.get("order_by")) != _order_contract(declared.get("order_by"))
        or schema.get("constraints") != {
            "additional_properties": False,
            "finite_numbers": "reject",
            "row_identity_unique": True,
            "total_order": True,
        }
    ):
        raise ValidationError("Atlas projection schema constraints drifted")
    return tuple(contracts)



def _projection_specs(export_declaration: object) -> tuple[Mapping[str, Any], ...]:
    query = _attribute_mapping(export_declaration, "query_contract")
    if query.get("cutoff") != _EXPECTED_CUTOFF:
        raise ValidationError("Atlas export cutoff contract is not the reviewed completed-date policy")
    if not isinstance(query.get("id"), str) or not query["id"]:
        raise ValidationError("Atlas export query contract identity is invalid")
    if not isinstance(query.get("version"), str) or not query["version"]:
        raise ValidationError("Atlas export query contract version is invalid")
    query_bounds = query.get("bounds")
    if (
        not isinstance(query_bounds, Mapping)
        or dict(query_bounds) != _EXPECTED_QUERY_BOUNDS
    ):
        raise ValidationError("Atlas export query bounds drifted")
    raw = query.get("projections")
    if not isinstance(raw, (list, tuple)) or len(raw) != len(_EXPECTED_PROJECTIONS):
        raise ValidationError("Atlas export must declare the complete fixed projection set")
    result: list[Mapping[str, Any]] = []
    for declared, expected in zip(raw, _EXPECTED_PROJECTIONS, strict=True):
        if not isinstance(declared, Mapping) or set(declared) != {
            "bounds", "dataset", "fields", "id", "operation_id", "order_by",
            "relations", "row_identity", "schema_id", "store",
        }:
            raise ValidationError("Atlas projection declaration is invalid")
        for key in ("id", "operation_id", "store", "dataset", "schema_id"):
            if declared.get(key) != expected[key]:
                raise ValidationError("Atlas projection identity drifted")
        fields = declared.get("fields")
        if not isinstance(fields, (list, tuple)) or tuple(fields) != expected["fields"]:
            raise ValidationError("Atlas projection public field contract drifted")
        relations = declared.get("relations")
        if (
            not isinstance(relations, (list, tuple))
            or tuple(relations) != expected["relations"]
        ):
            raise ValidationError("Atlas projection schema contract is invalid")
        order_by = declared.get("order_by")
        if (
            not isinstance(order_by, (list, tuple))
            or _order_contract(order_by)
            != tuple((field, "asc") for field in expected["order_by"])
        ):
            raise ValidationError("Atlas projection order is invalid")
        row_identity = declared.get("row_identity")
        if (
            not isinstance(row_identity, (list, tuple))
            or tuple(row_identity) != expected["row_identity"]
        ):
            raise ValidationError("Atlas projection row identity is invalid")
        bounds = declared.get("bounds")
        if (
            not isinstance(bounds, Mapping)
            or dict(bounds) != {"max_rows": expected["max_rows"]}
        ):
            raise ValidationError("Atlas projection bounds are invalid")
        material = dict(declared)
        material["_schema_fields"] = _schema_field_contracts(
            export_declaration,
            declared,
        )
        result.append(material)
    return tuple(result)


def _availability(row: Mapping[str, Any], cutoff: TemporalValue) -> tuple[bool, tuple[str, ...]]:
    availability = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
    if availability.precision.value != str(row["available_precision"]):
        raise ValidationError("Atlas source availability precision is inconsistent")
    decision = availability_at_or_before(
        availability,
        cutoff,
        DateOnlyPolicy.COMPLETED_DATE,
    )
    return decision.included, decision.warnings


def _candidate_limit(max_rows: int) -> int:
    return min(200_000, max(128, max_rows * 32))


def _execution_deadline(
    deadline: float | None,
    monotonic: Callable[[], float] | None,
    phase: str,
) -> None:
    """Turn an in-flight projection deadline expiry into a safe public error."""

    if deadline is None:
        return
    try:
        _check_deadline(
            deadline,
            time.monotonic if monotonic is None else monotonic,
            phase,
        )
    except ResourceLimitError:
        raise ValidationError(
            "Atlas projection deadline exceeded during SQLite query"
        ) from None


def _query_rows(
    connection: Any,
    sql: str,
    *,
    max_rows: int,
    message: str,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> list[Mapping[str, Any]]:
    """Read bounded candidates while actively enforcing an optional deadline."""

    limit = _candidate_limit(max_rows)
    if deadline is None:
        rows = list(connection.execute(sql, (limit + 1,)))
    else:
        handler_error: ValidationError | None = None

        def progress_handler() -> int:
            nonlocal handler_error
            try:
                _execution_deadline(
                    deadline,
                    monotonic,
                    "projection_query_progress",
                )
            except ValidationError as exc:
                handler_error = exc
                return 1
            return 0

        try:
            connection.set_progress_handler(progress_handler, 1_000)
            _execution_deadline(deadline, monotonic, "projection_query_before")
            cursor = connection.execute(sql, (limit + 1,))
            rows = []
            for row in cursor:
                _execution_deadline(deadline, monotonic, "projection_query_rows")
                rows.append(row)
            _execution_deadline(deadline, monotonic, "projection_query_after")
        except sqlite3.Error:
            if handler_error is not None:
                raise handler_error from None
            raise
        finally:
            connection.set_progress_handler(None, 0)
    if len(rows) > limit:
        raise ResourceLimitError(message)
    return rows


def _sort_value(value: object) -> tuple[int, object]:
    if value is None:
        return (0, "")
    if isinstance(value, int) and not isinstance(value, bool):
        return (1, value)
    return (2, str(value))


def _order_rows(
    rows: Sequence[Mapping[str, object]],
    order_by: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    fields = tuple(str(item["field"]) for item in order_by)
    return sorted(
        rows,
        key=lambda row: tuple(_sort_value(row[field]) for field in fields),
    )


def _validate_public_value(
    value: object,
    *,
    field: str,
    type_name: str,
    nullable: bool,
) -> None:
    if value is None:
        if not nullable:
            raise ValidationError("Atlas public row violated a non-null schema field")
        return
    if isinstance(value, bool):
        raise ValidationError("Atlas source type drifted into a boolean public field")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("Atlas source contains a non-finite number")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValidationError("Atlas source contains a non-finite decimal")
    if type_name == "integer":
        if type(value) is not int:
            raise ValidationError("Atlas source integer type drifted")
        return
    if type_name == "decimal_string":
        if not isinstance(value, str):
            raise ValidationError("Atlas source decimal string type drifted")
        try:
            parsed_decimal = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("Atlas source decimal string is invalid") from exc
        if not parsed_decimal.is_finite():
            raise ValidationError("Atlas source decimal string is non-finite")
        return
    if not isinstance(value, str):
        raise ValidationError("Atlas source string type drifted")
    if type_name == "string":
        return
    if type_name == "date":
        parsed = TemporalValue.parse(value, pointer="/" + field)
        if parsed.precision is not TemporalPrecision.DATE:
            raise ValidationError("Atlas source date field has the wrong precision")
        return
    if type_name == "datetime":
        parsed = TemporalValue.parse(value, pointer="/" + field)
        if parsed.precision is not TemporalPrecision.DATETIME:
            raise ValidationError("Atlas source datetime field has the wrong precision")
        return
    if type_name == "temporal_string":
        TemporalValue.parse(value, pointer="/" + field)
        return
    if type_name == "temporal_precision":
        if value not in {"date", "datetime"}:
            raise ValidationError("Atlas temporal precision field is invalid")
        return
    if type_name == "source_temporal_precision":
        if value not in {"date", "datetime", "unknown"}:
            raise ValidationError("Atlas source temporal precision field is invalid")
        return
    raise ValidationError("Atlas schema declared an unsupported public type")



def _public_rows(
    rows: Sequence[Mapping[str, Any]],
    field_contracts: Sequence[tuple[str, str, bool]],
    *,
    deadline: float | None,
    monotonic: Callable[[], float] | None,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        _execution_deadline(deadline, monotonic, "projection_public_rows")
        result.append(_public_row(row, field_contracts))
    return result


def _public_row(
    row: Mapping[str, Any],
    field_contracts: Sequence[tuple[str, str, bool]],
) -> dict[str, object]:
    keys = getattr(row, "keys", None)
    if not callable(keys):
        raise ValidationError("Atlas source row does not match its public schema")
    source_fields = tuple(keys())
    if (
        len(source_fields) != len(set(source_fields))
        or not all(isinstance(field, str) for field in source_fields)
    ):
        raise ValidationError("Atlas source row does not match its public schema")
    result: dict[str, object] = {}
    for field, type_name, nullable in field_contracts:
        if field not in source_fields:
            raise ValidationError("Atlas source row does not match its public schema")
        value = row[field]
        _validate_public_value(
            value,
            field=field,
            type_name=type_name,
            nullable=nullable,
        )
        result[field] = value
    for precision_field, temporal_field in _PRECISION_COMPANIONS.items():
        if precision_field not in result:
            continue
        precision = result[precision_field]
        temporal = result.get(temporal_field)
        if precision == "unknown":
            if temporal is not None:
                raise ValidationError("Atlas unknown source precision has a temporal value")
            continue
        if not isinstance(precision, str) or not isinstance(temporal, str):
            raise ValidationError("Atlas temporal companion fields type drifted")
        parsed = TemporalValue.parse(temporal, pointer="/" + temporal_field)
        if parsed.precision.value != precision:
            raise ValidationError("Atlas temporal companion precision is inconsistent")
    return result


def _finish_projection(
    *,
    declaration: Mapping[str, Any],
    rows: Sequence[Mapping[str, object]],
    warnings: set[str],
) -> AtlasProjection:
    fields = tuple(str(item) for item in declaration["fields"])
    max_rows = _positive_bound(
        declaration["bounds"]["max_rows"],
        message="Atlas projection row bound is invalid",
    )
    if len(rows) > max_rows:
        raise ResourceLimitError("Atlas projection exceeded its registered row bound")
    if any(not isinstance(row, Mapping) or tuple(row) != fields for row in rows):
        raise ValidationError("Atlas public row fields do not match the registered schema")
    identities = [
        tuple(row[field] for field in declaration["row_identity"])
        for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise ValidationError("Atlas projection row identity is not unique")
    ordered = tuple(_order_rows(rows, tuple(declaration["order_by"])))
    order_fields = tuple(str(item["field"]) for item in declaration["order_by"])
    order_keys = [
        tuple(_sort_value(row[field]) for field in order_fields)
        for row in ordered
    ]
    if len(order_keys) != len(set(order_keys)):
        raise ValidationError("Atlas projection declared total order is not total")
    return AtlasProjection(
        projection_id=str(declaration["id"]),
        operation_id=str(declaration["operation_id"]),
        dataset_id=str(declaration["dataset"]),
        store=str(declaration["store"]),
        schema_id=str(declaration["schema_id"]),
        fields=fields,
        rows=ordered,
        row_sha256=hashlib.sha256(dumps_strict(list(ordered)).encode("utf-8")).hexdigest(),
        warnings=tuple(sorted(warnings)),
    )


def _market_projection(
    store_map: StoreMap,
    declaration: Mapping[str, Any],
    cutoff: TemporalValue,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> AtlasProjection:
    max_rows = _positive_bound(
        declaration["bounds"]["max_rows"],
        message="Atlas projection row bound is invalid",
    )
    with read_connection(store_map, StoreRole.MARKET) as connection:
        candidates = _query_rows(
            connection,
            """
            SELECT version_id, instrument_id, trade_date, provider, price_variant,
                   currency_segment, open_value AS open, high_value AS high,
                   low_value AS low, close_value AS close, volume, available_at,
                   available_precision, captured_at, captured_precision,
                   correction_sequence
            FROM prices_daily_versions
            ORDER BY instrument_id, trade_date, provider, price_variant,
                     currency_segment, correction_sequence, version_id
            LIMIT ?
            """,
            max_rows=max_rows,
            message="Atlas market version history exceeds its registered bound",
            deadline=deadline,
            monotonic=monotonic,
        )
    grouped: dict[tuple[str, str, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in candidates:
        _execution_deadline(deadline, monotonic, "projection_candidate_rows")
        included, row_warnings = _availability(row, cutoff)
        warnings.update(row_warnings)
        if included:
            grouped[
                (
                    str(row["instrument_id"]),
                    str(row["trade_date"]),
                    str(row["provider"]),
                    str(row["price_variant"]),
                    str(row["currency_segment"]),
                )
            ].append(row)
    selected: list[Mapping[str, Any]] = []
    for group in grouped.values():
        _execution_deadline(deadline, monotonic, "projection_selected_rows")
        selected.append(
            max(
                group,
                key=lambda row: (
                    int(row["correction_sequence"]),
                    str(row["available_at"]),
                    str(row["version_id"]),
                ),
            )
        )
    return _finish_projection(
        declaration=declaration,
        rows=_public_rows(
            selected,
            declaration["_schema_fields"],
            deadline=deadline,
            monotonic=monotonic,
        ),
        warnings=warnings,
    )

def _gdp_projection(
    store_map: StoreMap,
    declaration: Mapping[str, Any],
    cutoff: TemporalValue,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> AtlasProjection:
    max_rows = _positive_bound(
        declaration["bounds"]["max_rows"],
        message="Atlas projection row bound is invalid",
    )
    with read_connection(store_map, StoreRole.MACRO) as connection:
        candidates = _query_rows(
            connection,
            """
            SELECT source, source_vintage_identity, release_stage, vintage_at,
                   vintage_precision, source_published_at,
                   source_published_precision, available_at, available_precision
            FROM gdp_vintages
            ORDER BY source, vintage_at, source_vintage_identity
            LIMIT ?
            """,
            max_rows=max_rows,
            message="Atlas GDP vintage history exceeds its registered bound",
            deadline=deadline,
            monotonic=monotonic,
        )
    warnings: set[str] = set()
    selected: list[Mapping[str, Any]] = []
    for row in candidates:
        _execution_deadline(deadline, monotonic, "projection_candidate_rows")
        included, row_warnings = _availability(row, cutoff)
        warnings.update(row_warnings)
        if included:
            selected.append(row)
    return _finish_projection(
        declaration=declaration,
        rows=_public_rows(
            selected,
            declaration["_schema_fields"],
            deadline=deadline,
            monotonic=monotonic,
        ),
        warnings=warnings,
    )

def _company_projection(
    store_map: StoreMap,
    declaration: Mapping[str, Any],
    cutoff: TemporalValue,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> AtlasProjection:
    max_rows = _positive_bound(
        declaration["bounds"]["max_rows"],
        message="Atlas projection row bound is invalid",
    )
    with read_connection(store_map, StoreRole.COMPANY) as connection:
        candidates = _query_rows(
            connection,
            """
            SELECT version.issuer_version_id, issuer.issuer_id, issuer.cik,
                   version.legal_name, version.entity_type, version.name_state,
                   version.missing_reason, version.available_at,
                   version.available_precision, version.version_sequence
            FROM company_issuers AS issuer
            JOIN company_issuer_versions AS version
              ON version.issuer_id = issuer.issuer_id
            ORDER BY issuer.cik, version.version_sequence, version.issuer_version_id
            LIMIT ?
            """,
            max_rows=max_rows,
            message="Atlas company issuer history exceeds its registered bound",
            deadline=deadline,
            monotonic=monotonic,
        )
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in candidates:
        _execution_deadline(deadline, monotonic, "projection_candidate_rows")
        included, row_warnings = _availability(row, cutoff)
        warnings.update(row_warnings)
        if included:
            grouped[str(row["issuer_id"])].append(row)
    selected: list[Mapping[str, Any]] = []
    for group in grouped.values():
        _execution_deadline(deadline, monotonic, "projection_selected_rows")
        selected.append(
            max(
                group,
                key=lambda row: (
                    int(row["version_sequence"]),
                    str(row["available_at"]),
                    str(row["issuer_version_id"]),
                ),
            )
        )
    return _finish_projection(
        declaration=declaration,
        rows=_public_rows(
            selected,
            declaration["_schema_fields"],
            deadline=deadline,
            monotonic=monotonic,
        ),
        warnings=warnings,
    )

def _news_projection(
    store_map: StoreMap,
    declaration: Mapping[str, Any],
    cutoff: TemporalValue,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> AtlasProjection:
    max_rows = _positive_bound(
        declaration["bounds"]["max_rows"],
        message="Atlas projection row bound is invalid",
    )
    with read_connection(store_map, StoreRole.NEWS) as connection:
        candidates = _query_rows(
            connection,
            """
            SELECT version.news_item_version_id, item.item_id, item.source_name,
                   item.source_item_id, item.source_kind, version.headline,
                   version.published_at, version.published_precision,
                   version.content_state, version.content_missing_reason,
                   version.item_state, version.retraction_reason,
                   version.available_at, version.available_precision,
                   version.captured_at, version.captured_precision,
                   version.version_sequence
            FROM news_items AS item
            JOIN news_item_versions AS version ON version.item_id = item.item_id
            ORDER BY item.source_name, item.source_item_id, version.version_sequence,
                     version.news_item_version_id
            LIMIT ?
            """,
            max_rows=max_rows,
            message="Atlas news version history exceeds its registered bound",
            deadline=deadline,
            monotonic=monotonic,
        )
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in candidates:
        _execution_deadline(deadline, monotonic, "projection_candidate_rows")
        included, row_warnings = _availability(row, cutoff)
        warnings.update(row_warnings)
        if included:
            grouped[str(row["item_id"])].append(row)
    selected: list[Mapping[str, Any]] = []
    for group in grouped.values():
        _execution_deadline(deadline, monotonic, "projection_selected_rows")
        selected.append(
            max(
                group,
                key=lambda row: (
                    int(row["version_sequence"]),
                    str(row["available_at"]),
                    str(row["news_item_version_id"]),
                ),
            )
        )
    return _finish_projection(
        declaration=declaration,
        rows=_public_rows(
            selected,
            declaration["_schema_fields"],
            deadline=deadline,
            monotonic=monotonic,
        ),
        warnings=warnings,
    )

def _check_deadline(
    deadline: float | None,
    monotonic: Callable[[], float],
    phase: str,
) -> None:
    if deadline is None:
        return
    value = monotonic()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValidationError("Atlas monotonic clock returned an invalid value")
    if value > deadline:
        raise ResourceLimitError(
            "Atlas export exceeded its registered runtime bound during " + phase
        )



def collect_atlas_projections(
    registry: object,
    copy_stores: StoreMap,
    export_declaration: object,
    cutoff: TemporalValue,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] | None = None,
) -> tuple[AtlasProjection, ...]:
    """Select the four fixed public datasets from read-only copied stores."""

    del registry
    if cutoff.precision.value != "datetime":
        raise ValidationError("Atlas cutoff must be an aware datetime")
    declarations = _projection_specs(export_declaration)
    monotonic_clock = time.monotonic if monotonic is None else monotonic
    selectors = {
        "market-prices": _market_projection,
        "gdp-vintages": _gdp_projection,
        "company-issuers": _company_projection,
        "news-items": _news_projection,
    }
    result: list[AtlasProjection] = []
    for declaration in declarations:
        _check_deadline(deadline, monotonic_clock, "projection_before")
        selector = selectors[str(declaration["id"])]
        if deadline is None:
            result.append(selector(copy_stores, declaration, cutoff))
        else:
            result.append(
                selector(
                    copy_stores,
                    declaration,
                    cutoff,
                    deadline=deadline,
                    monotonic=monotonic_clock,
                )
            )
        _check_deadline(deadline, monotonic_clock, "projection_after")
    query = _attribute_mapping(export_declaration, "query_contract")
    total_bound = _positive_bound(
        query["bounds"]["max_total_rows"],
        message="Atlas export total row bound is invalid",
        maximum=100_000,
    )
    if sum(len(item.rows) for item in result) > total_bound:
        raise ResourceLimitError("Atlas export exceeded its registered total row bound")
    total_bytes = sum(
        len(dumps_strict(list(item.rows)).encode("utf-8"))
        for item in result
    )
    byte_bound = _positive_bound(
        query["bounds"]["max_bytes"],
        message="Atlas export byte bound is invalid",
        maximum=8 * 1024 * 1024,
    )
    if total_bytes > byte_bound:
        raise ResourceLimitError("Atlas export exceeded its registered byte bound")
    _check_deadline(deadline, monotonic_clock, "projection_bounds")
    return tuple(result)


__all__ = ("collect_atlas_projections",)
