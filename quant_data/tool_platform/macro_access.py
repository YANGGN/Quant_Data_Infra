"""Typed public adapters for canonical macro access and release calendars."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any

from quant_data.contracts import (
    LineageRef,
    MacroAvailabilityV2,
    MacroCaptureRefV2,
    MacroDimensionV2,
    MacroEvidenceRefV2,
    MacroObservationV2,
    MacroReleaseRefV2,
    MacroTimeSeriesV2,
    TruncationV1,
    WarningV1,
)
from quant_data.errors import ValidationError
from quant_data.json_codec import dumps_strict, loads_strict
from quant_data.macro.canonical_access import (
    CALENDAR_DATASET_IDS,
    MACRO_ACCESS_DATASET_IDS,
    CanonicalMacroRepository,
    MacroCatalogQuery,
    MacroReleaseCalendarQuery,
    MacroSeriesRequest,
)
from quant_data.registry import Registry

from .arguments import (
    CanonicalMacroDescribeArgumentsV2,
    CanonicalMacroSearchArgumentsV2,
    CanonicalMacroSeriesArgumentsV2,
    MacroReleaseCalendarArgumentsV1,
)
from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    RecordV1,
    fields_from_mapping,
    query_result_schema,
)


CANONICAL_MACRO_TOOL_NAMES = (
    "macro.search_series",
    "macro.describe_series",
    "macro.get_series",
)
CALENDAR_TOOL_NAME = "macro.get_release_calendar"
CALENDAR_OPERATION_VERSION = "2.0.0"
_CALENDAR_CURSOR_VERSION = 1
_CALENDAR_CURSOR_MAX_LENGTH = 2_048


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _text() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _nullable_text() -> dict[str, Any]:
    return {"type": ["string", "null"]}


def _integer(maximum: int = 10_000) -> dict[str, Any]:
    return {"type": "integer", "minimum": 0, "maximum": maximum}


def macro_time_series_v2_schema() -> dict[str, Any]:
    """Return the strict source-native macro series schema used by v2."""

    release = _strict(
        {
            "release_id": _text(),
            "source_vintage_identity": _text(),
            "source_release_order": _text(),
            "vintage_at": _nullable_text(),
            "vintage_precision": {
                "type": ["string", "null"],
            },
            "availability_basis": _text(),
            "is_first_release": {"type": ["boolean", "null"]},
            "first_release_evidence": _nullable_text(),
            "release_stage": _nullable_text(),
        }
    )
    availability = _strict(
        {
            "at": _text(),
            "precision": {
                "type": "string",
                "enum": ["date", "datetime"],
            },
        }
    )
    capture = _strict(
        {
            "captured_at": _text(),
            "captured_precision": {"type": "string", "const": "datetime"},
        }
    )
    evidence = _strict(
        {
            "kind": {"type": "string", "enum": ["artifact", "capture"]},
            "id": _text(),
            "snapshot_id": _nullable_text(),
            "run_id": _nullable_text(),
        }
    )
    observation = _strict(
        {
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "source_period": _text(),
            "value": {"type": ["number", "null"]},
            "missing_reason": _nullable_text(),
            "unit": _text(),
            "value_representation": _text(),
            "scale": _nullable_text(),
            "dimensions": {
                "type": "array",
                "maxItems": 100,
                "items": _strict(
                    {
                        "name": _text(),
                        "value": _text(),
                    }
                ),
            },
            "version_id": _text(),
            "correction_sequence": {
                "type": "integer",
                "minimum": 1,
            },
            "source_row": {"type": "integer", "minimum": 1},
            "release": release,
            "availability": availability,
            "capture": capture,
            "evidence": evidence,
        }
    )
    descriptor = {
        "active": {"type": "boolean"},
        "availability_basis": _text(),
        "category": _nullable_text(),
        "coverage_end": _nullable_text(),
        "coverage_start": _nullable_text(),
        "description": _nullable_text(),
        "first_release_count": _integer(200_000),
        "frequency": _text(),
        "observation_count": _integer(200_000),
        "provider": _text(),
        "provider_series_code": _text(),
        "release_count": _integer(200_000),
        "scale": _text(),
        "series_id": _text(),
        "storage_model": {
            "type": "string",
            "enum": ["generic_version_core", "official_vintage"],
        },
        "supported_modes_json": _text(),
        "title": _text(),
        "unit": _text(),
        "value_representation": _text(),
        "version_count": _integer(200_000),
    }
    metadata = _strict(descriptor)
    audit = _strict(
        {
            "mode": {
                "type": "string",
                "enum": ["latest", "as_of", "first_release"],
            },
            "requested_mode": {
                "type": "string",
                "enum": ["latest", "as_of", "first_release"],
            },
            "actual_mode": {
                "type": "string",
                "enum": ["latest", "as_of", "first_release"],
            },
            "cutoff": _nullable_text(),
            "cutoff_precision": _nullable_text(),
            "date_only_policy": {
                "type": "string",
                "enum": ["completed_date", "calendar_date_inclusive"],
            },
            "availability_basis": _text(),
            "period_range_rule": {"type": "string", "const": "period_start"},
            "requested_start_date": _nullable_text(),
            "requested_end_date": _nullable_text(),
            "limit": {"type": "integer", "minimum": 1, "maximum": 10_000},
            "selected_count": _integer(10_000),
            "total_selected_count": _integer(200_000),
            "storage_model": {
                "type": "string",
                "enum": ["generic_version_core", "official_vintage"],
            },
            "point_in_time_status": {
                "type": "string",
                "enum": ["safe", "unsafe", "not_applicable"],
            },
            "unsafe_reasons": {
                "type": "array",
                "maxItems": 100,
                "items": _text(),
            },
            "first_release_policy": {
                "type": "string",
                "const": "explicit_flag_and_evidence_required",
            },
        }
    )
    provenance = _strict(
        {
            "dataset_ids": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": _text(),
            },
            "store_role": {"type": "string", "const": "macro"},
            "registry_revision": _text(),
            "migration_ids": {
                "type": "array",
                "maxItems": 100,
                "items": _text(),
            },
            "receipt_sha256": _text(),
        }
    )
    return _strict(
        {
            "contract": {
                "type": "string",
                "const": "quant_data.macro_timeseries",
            },
            "contract_version": {"type": "string", "const": "2.0.0"},
            "series_id": _text(),
            "metadata": metadata,
            "observations": {
                "type": "array",
                "maxItems": 10_000,
                "items": observation,
            },
            "warnings": {
                "type": "array",
                "maxItems": 100,
                "items": _text(),
            },
            "audit": audit,
            "provenance": provenance,
            "truncated": {"type": "boolean"},
            "lineage_digest": _text(),
        }
    )


def canonical_macro_query_result_schema(
    name: str, *, carries_series: bool
) -> dict[str, Any]:
    schema = query_result_schema(name, macro_time_series_v2_schema())
    schema["properties"]["series"]["maxItems"] = 1 if carries_series else 0
    if carries_series:
        schema["properties"]["series"]["minItems"] = 1
    return schema


def _descriptor_record(item: Any) -> RecordV1:
    return RecordV1(
        record_type="canonical_macro_series",
        fields=fields_from_mapping(item.to_record()),
    )


def _lineage(
    dataset_ids: tuple[str, ...], semantic_id: str
) -> tuple[LineageRef, ...]:
    return tuple(
        LineageRef(
            dataset_id=dataset_id,
            store_role="macro",
            semantic_id=semantic_id,
        )
        for dataset_id in dataset_ids
    )


def _catalog_result(
    *,
    name: str,
    arguments: CanonicalMacroSearchArgumentsV2 | CanonicalMacroDescribeArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    repository = CanonicalMacroRepository(context.store_map, registry)
    if isinstance(arguments, CanonicalMacroSearchArgumentsV2):
        selection = repository.search_series(
            MacroCatalogQuery(
                query=arguments.query,
                provider=arguments.provider,
                frequency=arguments.frequency,
                limit=arguments.limit,
            )
        )
        descriptors = selection.descriptors
        total_count = selection.total_count
        truncated = selection.truncated
        diagnostic_code = "canonical_macro_catalog_search"
    else:
        description = repository.describe_series(arguments.series_id)
        descriptors = (description.descriptor,)
        selection = description
        total_count = 1
        truncated = False
        diagnostic_code = "canonical_macro_series_description"
    records = tuple(_descriptor_record(item) for item in descriptors)
    warning = WarningV1(
        code="current_retained_catalog_not_historical_as_of",
        message=(
            "Catalog membership describes the current retained store; use "
            "macro.get_series version 2.0.0 for observation-level as-of selection."
        ),
    )
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code=diagnostic_code,
                message="Canonical macro catalog metadata was read without provider access.",
                metrics=fields_from_mapping(
                    {
                        "returned_count": len(records),
                        "total_count": total_count,
                        "truncated": truncated,
                    }
                ),
            ),
        ),
        warnings=(warning,),
        lineage=_lineage(MACRO_ACCESS_DATASET_IDS, selection.receipt_sha256),
        truncation=TruncationV1(
            applied=truncated,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=total_count,
            has_more=truncated,
        ),
    )


def _macro_observation(record: Mapping[str, Any]) -> MacroObservationV2:
    dimensions = loads_strict(str(record["dimensions_json"]), max_bytes=4096)
    if not isinstance(dimensions, dict):
        raise ValidationError("Canonical macro dimensions are invalid")
    if record["artifact_id"] is not None:
        evidence = MacroEvidenceRefV2(
            kind="artifact",
            id=str(record["artifact_id"]),
            snapshot_id=str(record["snapshot_id"]),
            run_id=str(record["run_id"]),
        )
    else:
        evidence = MacroEvidenceRefV2(
            kind="capture",
            id=str(record["capture_id"]),
            snapshot_id=None,
            run_id=None,
        )
    return MacroObservationV2(
        period_start=str(record["period_start"]),
        period_end=str(record["period_end"]),
        source_period=str(record["source_period"]),
        value=record["value"],
        missing_reason=(
            None
            if record["missing_reason"] is None
            else str(record["missing_reason"])
        ),
        unit=str(record["unit"]),
        value_representation=str(record["value_representation"]),
        scale=None if record["scale"] is None else str(record["scale"]),
        dimensions=tuple(
            MacroDimensionV2(name=str(key), value=str(value))
            for key, value in sorted(dimensions.items())
        ),
        version_id=str(record["version_id"]),
        correction_sequence=int(record["correction_sequence"]),
        source_row=int(record["source_row"]),
        release=MacroReleaseRefV2(
            release_id=str(record["release_id"]),
            source_vintage_identity=str(record["source_vintage_identity"]),
            source_release_order=str(record["source_release_order"]),
            vintage_at=(
                None if record["vintage_at"] is None else str(record["vintage_at"])
            ),
            vintage_precision=(
                None
                if record["vintage_precision"] is None
                else str(record["vintage_precision"])
            ),
            availability_basis=str(record["availability_basis"]),
            is_first_release=record["is_first_release"],
            first_release_evidence=(
                None
                if record["first_release_evidence"] is None
                else str(record["first_release_evidence"])
            ),
            release_stage=(
                None
                if record["release_stage"] is None
                else str(record["release_stage"])
            ),
        ),
        availability=MacroAvailabilityV2(
            at=str(record["available_at"]),
            precision=str(record["available_precision"]),
        ),
        capture=MacroCaptureRefV2(
            captured_at=str(record["captured_at"]),
            captured_precision=str(record["captured_precision"]),
        ),
        evidence=evidence,
    )


def _series_result(
    *,
    name: str,
    arguments: CanonicalMacroSeriesArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    context.budget.require(
        rows=arguments.limit,
        series=1,
        operations=arguments.limit,
    )
    request = MacroSeriesRequest(
        series_id=arguments.series_id,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        mode=arguments.mode,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        limit=arguments.limit,
    )
    selection = CanonicalMacroRepository(context.store_map, registry).get_series(
        request
    )
    cutoff = request.cutoff
    unsafe_reasons = tuple(
        code
        for code in selection.warnings
        if code == "date_only_same_day_intraday_safety_not_established"
    )
    point_in_time_status = (
        "unsafe"
        if request.mode == "as_of" and unsafe_reasons
        else "safe"
        if request.mode == "as_of"
        else "not_applicable"
    )
    series = MacroTimeSeriesV2(
        series_id=selection.descriptor.series_id,
        metadata=selection.descriptor.to_record(),
        observations=tuple(_macro_observation(item) for item in selection.records),
        warnings=selection.warnings,
        audit={
            "mode": request.mode,
            "requested_mode": request.mode,
            "actual_mode": request.mode,
            "cutoff": request.as_of,
            "cutoff_precision": (
                None if cutoff is None else cutoff.precision.value
            ),
            "date_only_policy": request.date_only_policy.value,
            "availability_basis": selection.descriptor.availability_basis,
            "period_range_rule": "period_start",
            "requested_start_date": request.start_date,
            "requested_end_date": request.end_date,
            "limit": request.limit,
            "selected_count": len(selection.records),
            "total_selected_count": selection.total_selected_count,
            "storage_model": selection.descriptor.storage_model,
            "point_in_time_status": point_in_time_status,
            "unsafe_reasons": list(unsafe_reasons),
            "first_release_policy": "explicit_flag_and_evidence_required",
        },
        provenance={
            "dataset_ids": list(selection.dataset_ids),
            "store_role": "macro",
            "registry_revision": registry.revision,
            "migration_ids": list(selection.migration_ids),
            "receipt_sha256": selection.receipt_sha256,
        },
        truncated=selection.truncated,
    )
    return QueryResult(
        tool=name,
        status="ok",
        series=(series,),
        diagnostics=(
            DiagnosticV1(
                code="canonical_macro_series_selection",
                message=(
                    "One canonical macro series was selected with source-native "
                    "availability and vintage precision."
                ),
                metrics=fields_from_mapping(
                    {
                        "mode": request.mode,
                        "observation_count": len(series.observations),
                        "first_release_candidate_group_count": (
                            selection.first_release_candidate_group_count
                        ),
                        "first_release_evidenced_group_count": (
                            selection.first_release_evidenced_group_count
                        ),
                        "first_release_missing_evidence_group_count": (
                            selection.first_release_missing_evidence_group_count
                        ),
                        "point_in_time_status": series.audit[
                            "point_in_time_status"
                        ],
                        "storage_model": selection.descriptor.storage_model,
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Canonical macro series warning: {code}.",
            )
            for code in selection.warnings
        ),
        lineage=_lineage(selection.dataset_ids, series.lineage_digest),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=arguments.limit,
            returned_count=len(series.observations),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
        ),
    )


def invoke_canonical_macro(
    name: str,
    arguments: CanonicalMacroSearchArgumentsV2
    | CanonicalMacroDescribeArgumentsV2
    | CanonicalMacroSeriesArgumentsV2,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    if name not in CANONICAL_MACRO_TOOL_NAMES:
        raise LookupError("Canonical macro operation is not registered")
    expected_type = {
        "macro.search_series": CanonicalMacroSearchArgumentsV2,
        "macro.describe_series": CanonicalMacroDescribeArgumentsV2,
        "macro.get_series": CanonicalMacroSeriesArgumentsV2,
    }[name]
    if not isinstance(arguments, expected_type):
        raise ValidationError("Canonical macro operation requires typed arguments")
    context.checkpoint()
    if isinstance(arguments, CanonicalMacroSeriesArgumentsV2):
        result = _series_result(
            name=name,
            arguments=arguments,
            context=context,
            registry=registry,
        )
    else:
        result = _catalog_result(
            name=name,
            arguments=arguments,
            context=context,
            registry=registry,
        )
    context.checkpoint()
    return result


def _calendar_v2_argument_value(
    arguments: object,
    field: str,
    *,
    optional: bool = False,
) -> object:
    """Read one v2 field from a typed object or temporary mapping."""

    if isinstance(arguments, Mapping):
        if field in arguments:
            return arguments[field]
    elif hasattr(arguments, field):
        return getattr(arguments, field)
    if optional:
        return None
    raise ValidationError(f"Release calendar v2 requires {field}")


def _calendar_v2_query(arguments: object) -> MacroReleaseCalendarQuery:
    """Build the validated selection query before decoding its cursor."""

    initial = MacroReleaseCalendarQuery(
        start_date=_calendar_v2_argument_value(arguments, "start_date"),
        end_date=_calendar_v2_argument_value(arguments, "end_date"),
        mode=_calendar_v2_argument_value(arguments, "mode"),
        as_of=_calendar_v2_argument_value(arguments, "as_of"),
        date_only_policy=_calendar_v2_argument_value(
            arguments,
            "date_only_policy",
        ),
        event_name=_calendar_v2_argument_value(arguments, "event_name"),
        limit=_calendar_v2_argument_value(arguments, "limit"),
    )
    after = _decode_release_calendar_cursor(
        _calendar_v2_argument_value(arguments, "cursor", optional=True),
        query=initial,
    )
    return MacroReleaseCalendarQuery(
        start_date=initial.start_date,
        end_date=initial.end_date,
        mode=initial.mode,
        as_of=initial.as_of,
        date_only_policy=initial.date_only_policy,
        event_name=initial.event_name,
        limit=initial.limit,
        after=after,
    )


def _encode_release_calendar_cursor(
    *,
    query: MacroReleaseCalendarQuery,
    after: tuple[str, str, str],
) -> str:
    event_at, event_name, event_id = after
    material = {
        "version": _CALENDAR_CURSOR_VERSION,
        "mode": query.mode,
        "as_of": query.as_of,
        "date_only_policy": query.date_only_policy.value,
        "start_date": query.start_date,
        "end_date": query.end_date,
        "event_name": query.event_name,
        "last_event_at": event_at,
        "last_event_name": event_name,
        "last_event_id": event_id,
    }
    return (
        base64.urlsafe_b64encode(dumps_strict(material).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )


def _decode_release_calendar_cursor(
    cursor: object,
    *,
    query: MacroReleaseCalendarQuery,
) -> tuple[str, str, str] | None:
    if cursor is None:
        return None
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > _CALENDAR_CURSOR_MAX_LENGTH
    ):
        raise ValidationError(
            "Release calendar cursor must be null or a bounded string"
        )
    try:
        encoded = cursor.encode("ascii")
        padded = encoded + b"=" * ((4 - len(encoded) % 4) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise ValidationError("Release calendar cursor is malformed") from exc
    if base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii") != cursor:
        raise ValidationError("Release calendar cursor is not canonical")
    try:
        material = loads_strict(payload, max_bytes=4_096)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValidationError("Release calendar cursor is malformed") from exc
    if (
        not isinstance(material, dict)
        or set(material)
        != {
            "version",
            "mode",
            "as_of",
            "date_only_policy",
            "start_date",
            "end_date",
            "event_name",
            "last_event_at",
            "last_event_name",
            "last_event_id",
        }
        or isinstance(material["version"], bool)
        or material["version"] != _CALENDAR_CURSOR_VERSION
        or material["mode"] != query.mode
        or material["as_of"] != query.as_of
        or material["date_only_policy"] != query.date_only_policy.value
        or material["start_date"] != query.start_date
        or material["end_date"] != query.end_date
        or material["event_name"] != query.event_name
    ):
        raise ValidationError(
            "Release calendar cursor does not match the selected query"
        )
    after = (
        material["last_event_at"],
        material["last_event_name"],
        material["last_event_id"],
    )
    try:
        validated = MacroReleaseCalendarQuery(
            start_date=query.start_date,
            end_date=query.end_date,
            mode=query.mode,
            as_of=query.as_of,
            date_only_policy=query.date_only_policy,
            event_name=query.event_name,
            limit=query.limit,
            after=after,
        )
    except ValidationError as exc:
        raise ValidationError("Release calendar cursor key is invalid") from exc
    assert validated.after is not None
    return validated.after


def invoke_release_calendar_v2(
    name: str,
    arguments: object,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Page the reconciled release calendar with a query-bound cursor."""

    if name != CALENDAR_TOOL_NAME:
        raise LookupError("Release-calendar operation is not registered")
    query = _calendar_v2_query(arguments)
    context.checkpoint()
    context.budget.require(
        rows=query.limit,
        series=0,
        operations=query.limit,
    )
    selection = CanonicalMacroRepository(
        context.store_map, registry
    ).get_release_calendar(query)
    cutoff = query.cutoff
    records = tuple(
        RecordV1(
            record_type="macro_release_calendar_event",
            fields=fields_from_mapping(record),
        )
        for record in selection.records
    )
    next_cursor = (
        _encode_release_calendar_cursor(
            query=query,
            after=(
                str(selection.records[-1]["event_at"]),
                str(selection.records[-1]["event_name"]),
                str(selection.records[-1]["event_id"]),
            ),
        )
        if selection.truncated and selection.records
        else None
    )
    unsafe_reasons = tuple(
        code
        for code in selection.warnings
        if code == "date_only_same_day_intraday_safety_not_established"
    )
    point_in_time_status = (
        "unsafe"
        if query.mode == "as_of" and unsafe_reasons
        else "safe"
        if query.mode == "as_of"
        else "not_applicable"
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="macro_release_calendar_selection",
                message=(
                    "The 0016 wholesale predecessor and 0018 incremental "
                    "successor were reconciled by event identity."
                ),
                metrics=fields_from_mapping(
                    {
                        "mode": query.mode,
                        "requested_mode": query.mode,
                        "actual_mode": query.mode,
                        "cutoff": query.as_of,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": query.date_only_policy.value,
                        "availability_basis": "local_capture",
                        "point_in_time_status": point_in_time_status,
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                        "period_range_rule": "event_at_calendar_date",
                        "requested_start_date": query.start_date,
                        "requested_end_date": query.end_date,
                        "cursor_applied": query.after is not None,
                        "returned_count": len(records),
                        "total_count": selection.total_selected_count,
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Macro release-calendar warning: {code}.",
            )
            for code in selection.warnings
        ),
        lineage=_lineage(CALENDAR_DATASET_IDS, selection.receipt_sha256),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=query.limit,
            returned_count=len(records),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
            next_cursor=next_cursor,
        ),
    )


def invoke_release_calendar(
    name: str,
    arguments: MacroReleaseCalendarArgumentsV1,
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    if name != CALENDAR_TOOL_NAME:
        raise LookupError("Release-calendar operation is not registered")
    if not isinstance(arguments, MacroReleaseCalendarArgumentsV1):
        raise ValidationError("Release calendar requires typed arguments")
    context.checkpoint()
    query = MacroReleaseCalendarQuery(
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        mode=arguments.mode,
        as_of=arguments.as_of,
        date_only_policy=arguments.date_only_policy,
        event_name=arguments.event_name,
        limit=arguments.limit,
    )
    selection = CanonicalMacroRepository(
        context.store_map, registry
    ).get_release_calendar(query)
    cutoff = query.cutoff
    records = tuple(
        RecordV1(
            record_type="macro_release_calendar_event",
            fields=fields_from_mapping(record),
        )
        for record in selection.records
    )
    unsafe_reasons = tuple(
        code
        for code in selection.warnings
        if code == "date_only_same_day_intraday_safety_not_established"
    )
    point_in_time_status = (
        "unsafe"
        if arguments.mode == "as_of" and unsafe_reasons
        else "safe"
        if arguments.mode == "as_of"
        else "not_applicable"
    )
    context.checkpoint()
    return QueryResult(
        tool=name,
        status="ok",
        records=records,
        diagnostics=(
            DiagnosticV1(
                code="macro_release_calendar_selection",
                message=(
                    "The 0016 wholesale predecessor and 0018 incremental "
                    "successor were reconciled by event identity."
                ),
                metrics=fields_from_mapping(
                    {
                        "mode": arguments.mode,
                        "requested_mode": arguments.mode,
                        "actual_mode": arguments.mode,
                        "cutoff": arguments.as_of,
                        "cutoff_precision": (
                            None if cutoff is None else cutoff.precision.value
                        ),
                        "date_only_policy": arguments.date_only_policy,
                        "availability_basis": "local_capture",
                        "point_in_time_status": point_in_time_status,
                        "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                        "period_range_rule": "event_at_calendar_date",
                        "requested_start_date": arguments.start_date,
                        "requested_end_date": arguments.end_date,
                        "returned_count": len(records),
                        "total_count": selection.total_selected_count,
                        "truncated": selection.truncated,
                    }
                ),
            ),
        ),
        warnings=tuple(
            WarningV1(
                code=code,
                message=f"Macro release-calendar warning: {code}.",
            )
            for code in selection.warnings
        ),
        lineage=_lineage(CALENDAR_DATASET_IDS, selection.receipt_sha256),
        truncation=TruncationV1(
            applied=selection.truncated,
            limit=arguments.limit,
            returned_count=len(records),
            total_known_count=selection.total_selected_count,
            has_more=selection.truncated,
        ),
    )


__all__ = (
    "CALENDAR_TOOL_NAME",
    "CALENDAR_OPERATION_VERSION",
    "CANONICAL_MACRO_TOOL_NAMES",
    "canonical_macro_query_result_schema",
    "invoke_canonical_macro",
    "invoke_release_calendar",
    "invoke_release_calendar_v2",
    "macro_time_series_v2_schema",
)
