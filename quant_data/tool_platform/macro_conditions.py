"""Typed projections for the raw macro, rates, and liquidity v2 successors."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from quant_data.contracts import LineageRef, TruncationV1, WarningV1
from quant_data.errors import ResourceLimitError, ValidationError
from quant_data.json_codec import dumps_strict
from quant_data.registry import Registry
from quant_data.tool_platform.context import ToolExecutionContext
from quant_data.tool_platform.results import (
    DiagnosticV1,
    QueryResult,
    fields_from_mapping,
    records_from_mappings,
    research_envelope,
)

from quant_data.macro.canonical_access import (
    OFFICIAL_CANONICAL_DATASET_ID,
    OFFICIAL_EVIDENCE_DATASET_ID,
)
from quant_data.macro.fmp_release_surprises import CALENDAR_DATASET_ID
from quant_data.macro.conditions import (
    CREDIT_COMPONENTS,
    CURVE_COMPONENTS,
    FUNDING_COMPONENTS,
    LIQUIDITY_COMPONENTS,
    NBER_RECESSION_SERIES_ID,
    REPO_COMPONENTS,
    ConditionFact,
    ConditionSelectionAudit,
    ConditionSnapshot,
    MacroConditionsRepository,
    RevisionFact,
    StandardizedSurprise,
)


TOOL_NAMES = frozenset(
    {
        "macro.revision_analysis",
        "macro.standardize_surprises",
        "rates.get_funding_conditions",
        "rates.get_repo_facility_usage",
        "rates.curve_analytics",
        "macro.get_liquidity_snapshot",
        "macro.get_liquidity_impulse",
        "macro.get_credit_conditions",
        "macro.regime_snapshot",
        "research.liquidity_credit_state",
    }
)


def _limit(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("limit", 20)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
        raise ResourceLimitError("Macro conditions limit is invalid")
    return value


def _text(arguments: Mapping[str, Any], name: str, *, required: bool = False) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} is invalid")
    return value


def _temporal(arguments: Mapping[str, Any]) -> tuple[str, str | None, str, str | None]:
    mode = _text(arguments, "mode", required=True)
    assert mode is not None
    as_of = _text(arguments, "as_of")
    policy = _text(arguments, "date_only_policy", required=True)
    assert policy is not None
    observation_date = _text(arguments, "observation_date")
    return mode, as_of, policy, observation_date


def _lineage(facts: Sequence[ConditionFact]) -> tuple[LineageRef, ...]:
    result: list[LineageRef] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for fact in facts:
        key = (fact.dataset_id, fact.version_id, fact.evidence_id, fact.snapshot_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            LineageRef(
                dataset_id=fact.dataset_id,
                store_role="macro",
                semantic_id=fact.version_id,
                evidence_id=fact.evidence_id,
                snapshot_id=fact.snapshot_id,
                canonical_version_id=fact.version_id,
            )
        )
    return tuple(result)



def _dedupe_lineage(refs: Sequence[LineageRef]) -> tuple[LineageRef, ...]:
    unique: list[LineageRef] = []
    seen: set[tuple[str, str, str, str | None, str | None, str | None]] = set()
    for item in refs:
        key = (
            item.dataset_id,
            item.store_role,
            item.semantic_id,
            item.evidence_id,
            item.snapshot_id,
            item.canonical_version_id,
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _revision_lineage(items: Sequence[RevisionFact]) -> tuple[LineageRef, ...]:
    refs: list[LineageRef] = []
    for item in items:
        for version_id, evidence_id, snapshot_id in (
            (
                item.first_version_id,
                item.first_evidence_id,
                item.first_snapshot_id,
            ),
            (
                item.latest_version_id,
                item.latest_evidence_id,
                item.latest_snapshot_id,
            ),
        ):
            refs.append(
                LineageRef(
                    dataset_id=OFFICIAL_CANONICAL_DATASET_ID,
                    store_role="macro",
                    semantic_id=version_id,
                    evidence_id=evidence_id,
                    snapshot_id=snapshot_id,
                    canonical_version_id=version_id,
                )
            )
            if evidence_id is not None:
                refs.append(
                    LineageRef(
                        dataset_id=OFFICIAL_EVIDENCE_DATASET_ID,
                        store_role="macro",
                        semantic_id=evidence_id,
                        evidence_id=evidence_id,
                    )
                )
    return _dedupe_lineage(refs)


def _surprise_lineage(
    items: Sequence[StandardizedSurprise],
) -> tuple[LineageRef, ...]:
    refs: list[LineageRef] = []
    for item in items:
        event_lineage = (
            (
                item.event_version_id,
                item.event_evidence_id,
                item.event_snapshot_id,
            ),
            *item.coalesced_event_lineage,
        )
        for event_version_id, evidence_id, snapshot_id in event_lineage:
            refs.append(
                LineageRef(
                    dataset_id=CALENDAR_DATASET_ID,
                    store_role="macro",
                    semantic_id=event_version_id,
                    evidence_id=evidence_id,
                    snapshot_id=snapshot_id,
                    canonical_version_id=event_version_id,
                )
            )
        if item.official_version_id is not None:
            refs.append(
                LineageRef(
                    dataset_id=OFFICIAL_CANONICAL_DATASET_ID,
                    store_role="macro",
                    semantic_id=item.official_version_id,
                    evidence_id=item.official_evidence_id,
                    canonical_version_id=item.official_version_id,
                )
            )
        if item.official_evidence_id is not None:
            refs.append(
                LineageRef(
                    dataset_id=OFFICIAL_EVIDENCE_DATASET_ID,
                    store_role="macro",
                    semantic_id=item.official_evidence_id,
                    evidence_id=item.official_evidence_id,
                )
            )
    return _dedupe_lineage(refs)



def _point_in_time_status(
    mode: str, warnings: Sequence[str]
) -> tuple[str, tuple[str, ...]]:
    unsafe_reasons = tuple(
        sorted(
            {
                warning
                for warning in warnings
                if warning == "date_only_same_day_intraday_safety_not_established"
            }
        )
    )
    if mode != "as_of":
        return "not_applicable", ()
    if unsafe_reasons:
        return "not_established", unsafe_reasons
    return "safe", ()


def _canonical_warning_codes(
    audits: Sequence[ConditionSelectionAudit],
    warnings: Sequence[str],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(warnings).union(
                warning for audit in audits for warning in audit.selection.warnings
            )
        )
    )


def _selection_reporting(
    audits: Sequence[ConditionSelectionAudit],
    warnings: Sequence[str],
    *,
    mode: str,
    as_of: str | None,
    date_only_policy: str,
    observation_date: str | None,
    fact_count: int,
) -> tuple[
    tuple[DiagnosticV1, ...],
    tuple[WarningV1, ...],
    str,
    tuple[str, ...],
]:
    warning_codes = _canonical_warning_codes(audits, warnings)
    point_in_time_status, unsafe_reasons = _point_in_time_status(
        mode, warning_codes
    )
    availability_bases = tuple(
        sorted({audit.selection.descriptor.availability_basis for audit in audits})
    )
    if not availability_bases and mode == "latest_ex_post":
        availability_bases = ("event_date_and_latest_retained_ex_post",)
    if not availability_bases and mode == "first_release_and_latest":
        availability_bases = (
            "official_vintage_first_release_and_latest",
        )

    cutoff_precision = (
        None if as_of is None else ("date" if len(as_of) == 10 else "datetime")
    )
    diagnostics: list[DiagnosticV1] = [
        DiagnosticV1(
            code="canonical_macro_condition_snapshot",
            message=(
                "Raw macro components were selected from one retained immutable "
                "macro snapshot."
            ),
            metrics=fields_from_mapping(
                {
                    "actual_mode": mode,
                    "availability_basis": dumps_strict(list(availability_bases)),
                    "component_selection_count": len(audits),
                    "cutoff": as_of,
                    "cutoff_precision": cutoff_precision,
                    "date_only_policy": date_only_policy,
                    "observation_date_request": observation_date,
                    "point_in_time_status": point_in_time_status,
                    "requested_mode": mode,
                    "returned_fact_count": fact_count,
                    "unsafe_reasons": dumps_strict(list(unsafe_reasons)),
                }
            ),
        )
    ]
    for audit in audits:
        selection = audit.selection
        diagnostics.append(
            DiagnosticV1(
                code="canonical_macro_condition_component_selection",
                message=(
                    "One raw macro component retained its canonical selection "
                    "audit metadata."
                ),
                metrics=fields_from_mapping(
                    {
                        "availability_basis": selection.descriptor.availability_basis,
                        "component": audit.component,
                        "dataset_ids": dumps_strict(list(selection.dataset_ids)),
                        "first_release_candidate_group_count": (
                            selection.first_release_candidate_group_count
                        ),
                        "first_release_evidenced_group_count": (
                            selection.first_release_evidenced_group_count
                        ),
                        "first_release_missing_evidence_group_count": (
                            selection.first_release_missing_evidence_group_count
                        ),
                        "migration_ids": dumps_strict(list(selection.migration_ids)),
                        "receipt_sha256": selection.receipt_sha256,
                        "selected_count": len(selection.records),
                        "series_id": selection.descriptor.series_id,
                        "total_selected_count": selection.total_selected_count,
                        "truncated": selection.truncated,
                        "warnings": dumps_strict(list(selection.warnings)),
                    }
                ),
            )
        )
    return (
        tuple(diagnostics),
        tuple(
            WarningV1(
                code=code,
                message=f"Canonical macro condition warning: {code}.",
            )
            for code in warning_codes
        ),
        point_in_time_status,
        unsafe_reasons,
    )


def _fact_record(
    fact: ConditionFact,
    *,
    mode: str,
    as_of: str | None,
    date_only_policy: str,
    observation_date: str | None,
) -> dict[str, object]:
    return {
        "available_at": fact.available_at,
        "available_precision": fact.available_precision,
        "captured_at": fact.captured_at,
        "captured_precision": fact.captured_precision,
        "component": fact.component,
        "missing_reason": fact.missing_reason,
        "observation_date_request": observation_date,
        "period_end": fact.period_end,
        "period_start": fact.period_start,
        "selection_as_of": as_of,
        "selection_date_only_policy": date_only_policy,
        "selection_mode": mode,
        "series_id": fact.series_id,
        "source_vintage_identity": fact.source_vintage_identity,
        "unit": fact.unit,
        "value": fact.value,
        "value_representation": fact.value_representation,
        "version_id": fact.version_id,
    }


def _combine_warnings(*groups: Sequence[WarningV1]) -> tuple[WarningV1, ...]:
    result: list[WarningV1] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item.code not in seen:
                seen.add(item.code)
                result.append(item)
    return tuple(result)


def _result(
    name: str,
    *,
    limit: int,
    record_type: str,
    records: Sequence[Mapping[str, object]],
    facts: Sequence[ConditionFact] = (),
    lineage_refs: Sequence[LineageRef] = (),
    diagnostics: Sequence[DiagnosticV1] = (),
    warnings: Sequence[WarningV1] = (),
    selection_audits: Sequence[ConditionSelectionAudit] = (),
    selection_warnings: Sequence[str] = (),
    temporal: tuple[str, str | None, str, str | None] | None = None,
    selection_mode: str | None = None,
    research: bool = False,
    arguments: Mapping[str, Any] | None = None,
) -> QueryResult:
    all_values = tuple(records)
    values = all_values[:limit]
    truncated = len(all_values) > limit
    lineage = _dedupe_lineage((*_lineage(tuple(facts)), *lineage_refs))
    reporting_diagnostics: tuple[DiagnosticV1, ...] = ()
    reporting_warnings: tuple[WarningV1, ...] = ()
    point_in_time_status = "not_applicable"
    unsafe_reasons: tuple[str, ...] = ()
    reporting_temporal = temporal
    if reporting_temporal is None and selection_mode is not None:
        reporting_temporal = (
            selection_mode, None, "completed_date", None
        )
    if reporting_temporal is not None:
        mode, as_of, policy, observation_date = reporting_temporal
        (
            reporting_diagnostics,
            reporting_warnings,
            point_in_time_status,
            unsafe_reasons,
        ) = _selection_reporting(
            selection_audits,
            selection_warnings,
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            observation_date=observation_date,
            fact_count=len(facts),
        )
    research_arguments = arguments or {}
    research_reasons = (
        unsafe_reasons if point_in_time_status == "not_established" else ()
    )
    return QueryResult(
        tool=name,
        records=records_from_mappings(record_type, values),
        diagnostics=tuple(diagnostics) + reporting_diagnostics,
        warnings=_combine_warnings(tuple(warnings), reporting_warnings),
        lineage=lineage,
        truncation=TruncationV1(
            applied=truncated,
            limit=limit,
            returned_count=len(values),
            total_known_count=len(all_values),
            has_more=truncated,
        ),
        research_contract=(
            research_envelope(
                name,
                research_arguments,
                (),
                lineage,
                point_in_time_status=point_in_time_status,
                unsafe_reasons=research_reasons,
            )
            if research
            else QueryResult.__dataclass_fields__["research_contract"].default_factory()
        ),
    )


def _not_established(
    name: str,
    *,
    limit: int,
    code: str,
    message: str,
    arguments: Mapping[str, Any],
    selection_audits: Sequence[ConditionSelectionAudit] = (),
    selection_warnings: Sequence[str] = (),
    temporal: tuple[str, str | None, str, str | None] | None = None,
    selection_mode: str | None = None,
) -> QueryResult:
    reporting_diagnostics: tuple[DiagnosticV1, ...] = ()
    reporting_warnings: tuple[WarningV1, ...] = ()
    point_in_time_status = "not_applicable"
    unsafe_reasons: tuple[str, ...] = ()
    reporting_temporal = temporal
    if reporting_temporal is None and selection_mode is not None:
        reporting_temporal = (
            selection_mode, None, "completed_date", None
        )
    if reporting_temporal is not None:
        mode, as_of, policy, observation_date = reporting_temporal
        (
            reporting_diagnostics,
            reporting_warnings,
            point_in_time_status,
            unsafe_reasons,
        ) = _selection_reporting(
            selection_audits,
            selection_warnings,
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            observation_date=observation_date,
            fact_count=0,
        )
    research_reasons = (
        unsafe_reasons if point_in_time_status == "not_established" else ()
    )
    return QueryResult(
        tool=name,
        status="not_established",
        diagnostics=(DiagnosticV1(code, message),) + reporting_diagnostics,
        warnings=_combine_warnings(
            (
                WarningV1(
                    "not_established",
                    "No value, spread, score, or classification was fabricated.",
                ),
            ),
            reporting_warnings,
        ),
        truncation=TruncationV1(False, limit, 0, 0, False),
        research_contract=(
            research_envelope(
                name,
                arguments,
                (),
                (),
                point_in_time_status=point_in_time_status,
                unsafe_reasons=research_reasons,
            )
            if name.startswith("research.")
            else QueryResult.__dataclass_fields__["research_contract"].default_factory()
        ),
    )


def _spread(
    facts: Sequence[ConditionFact],
    *,
    left: str | None,
    right: str | None,
    kind: str,
) -> Mapping[str, object] | None:
    if left is None and right is None:
        return None
    if left is None or right is None:
        raise ValidationError("Both spread components are required")
    by_component = {item.component: item for item in facts}
    first, second = by_component.get(left), by_component.get(right)
    if first is None or second is None:
        raise ValidationError("Requested spread component is unavailable")
    if (
        first.value is None
        or second.value is None
        or first.unit != second.unit
        or first.period_start != second.period_start
    ):
        raise ValidationError(
            "Requested spread requires two numeric same-date same-unit observations"
        )
    return {
        "calculation": "left_minus_right",
        "left_component": left,
        "left_period_start": first.period_start,
        "left_value": first.value,
        "right_component": right,
        "right_period_start": second.period_start,
        "right_value": second.value,
        "spread": first.value - second.value,
        "unit": "percentage_points" if first.unit == "percent" else first.unit,
        "vector_kind": kind,
    }


def _revision_records(items: Sequence[RevisionFact]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "absolute_revision": item.absolute_revision,
            "comparison_basis": "current_retained_first_vs_latest",
            "first_value": item.first_value,
            "first_version_id": item.first_version_id,
            "latest_value": item.latest_value,
            "latest_version_id": item.latest_version_id,
            "period_end": item.period_end,
            "period_start": item.period_start,
            "series_id": item.series_id,
            "signed_revision": item.signed_revision,
            "unit": item.unit,
        }
        for item in items
    )


def _surprise_records(
    items: Sequence[StandardizedSurprise],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "availability_assumption": item.availability_assumption,
            "event_at": item.event_at,
            "event_id": item.event_id,
            "event_version_id": item.event_version_id,
            "kind": item.kind,
            "official_version_id": item.official_version_id,
            "population_basis": "latest_retained_ex_post",
            "reference_period": item.reference_period,
            "release_stage": item.release_stage,
            "sample_count": item.sample_count,
            "sample_mean": item.sample_mean,
            "population_standard_deviation": item.population_standard_deviation,
            "surprise": item.surprise,
            "unit": item.unit,
            "z_score": item.z_score,
        }
        for item in items
    )


def _snapshot(
    repository: MacroConditionsRepository,
    components: tuple[tuple[str, str], ...],
    arguments: Mapping[str, Any],
    *,
    include_soma: bool = False,
) -> tuple[
    tuple[ConditionFact, ...],
    tuple[str, str | None, str, str | None],
    tuple[ConditionSelectionAudit, ...],
    tuple[str, ...],
]:
    request = _temporal(arguments)
    mode, as_of, policy, observation_date = request
    detailed = getattr(repository, "snapshot_with_audit", None)
    if callable(detailed):
        snapshot = detailed(
            components,
            observation_date=observation_date,
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            include_soma=include_soma,
        )
        if not isinstance(snapshot, ConditionSnapshot):
            raise ValidationError("Macro condition snapshot audit is invalid")
        return (
            snapshot.facts,
            request,
            snapshot.selection_audit,
            snapshot.warnings,
        )
    return (
        repository.snapshot(
            components,
            observation_date=observation_date,
            mode=mode,
            as_of=as_of,
            date_only_policy=policy,
            include_soma=include_soma,
        ),
        request,
        (),
        (),
    )


def _snapshots(
    repository: MacroConditionsRepository,
    components: tuple[tuple[str, str], ...],
    arguments: Mapping[str, Any],
    *,
    observation_dates: tuple[str | None, ...],
    include_soma: bool = False,
) -> tuple[
    tuple[tuple[ConditionFact, ...], ...],
    tuple[str, str | None, str, str | None],
    tuple[ConditionSelectionAudit, ...],
    tuple[str, ...],
]:
    request = _temporal(arguments)
    mode, as_of, policy, _observation_date = request
    detailed = getattr(repository, "snapshots_with_audit", None)
    if callable(detailed):
        snapshots = tuple(
            detailed(
                components,
                observation_dates=observation_dates,
                mode=mode,
                as_of=as_of,
                date_only_policy=policy,
                include_soma=include_soma,
            )
        )
        if len(snapshots) != len(observation_dates) or any(
            not isinstance(snapshot, ConditionSnapshot) for snapshot in snapshots
        ):
            raise ValidationError("Macro condition snapshots audit is invalid")
        return (
            tuple(snapshot.facts for snapshot in snapshots),
            request,
            tuple(
                audit
                for snapshot in snapshots
                for audit in snapshot.selection_audit
            ),
            tuple(
                sorted(
                    {
                        warning
                        for snapshot in snapshots
                        for warning in snapshot.warnings
                    }
                )
            ),
        )
    return (
        tuple(
            repository.snapshot(
                components,
                observation_date=observation_date,
                mode=mode,
                as_of=as_of,
                date_only_policy=policy,
                include_soma=include_soma,
            )
            for observation_date in observation_dates
        ),
        request,
        (),
        (),
    )


def invoke_macro_conditions(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
    *,
    repository: MacroConditionsRepository | None = None,
) -> QueryResult:
    """Invoke exactly one raw macro/rates v2 projection."""

    if name not in TOOL_NAMES:
        raise LookupError("Macro condition operation is not registered")
    if not isinstance(arguments, Mapping):
        raise ValidationError("Macro condition arguments must be a mapping")
    limit = _limit(arguments)
    context.checkpoint()
    context.budget.require(rows=limit, series=20, operations=20_000)
    source = repository or MacroConditionsRepository(context.store_map, registry)

    if name == "macro.revision_analysis":
        revision_arguments = {
            "series_id": _text(arguments, "series_id", required=True) or "",
            "start_date": _text(arguments, "start_date"),
            "end_date": _text(arguments, "end_date"),
            "limit": limit,
        }
        detailed = getattr(source, "revision_analysis_with_audit", None)
        if callable(detailed):
            items, selection_audits = detailed(**revision_arguments)
            if not isinstance(items, tuple) or not isinstance(
                selection_audits, tuple
            ):
                raise ValidationError("Macro revision selection audit is invalid")
        else:
            items = source.revision_analysis(**revision_arguments)
            selection_audits = ()
        if not items:
            return _not_established(
                name, limit=limit, code="no_retained_revision_pairs",
                message="No first/latest official-vintage pairs were retained.",
                arguments=arguments,
                selection_audits=selection_audits,
                selection_mode="first_release_and_latest",
            )
        return _result(
            name, limit=limit, record_type="macro_revision",
            records=_revision_records(items),
            lineage_refs=_revision_lineage(items),
            selection_audits=selection_audits,
            selection_mode="first_release_and_latest",
        )

    if name == "macro.standardize_surprises":
        try:
            items = source.standardize_surprises(
                kind=_text(arguments, "kind", required=True) or "",
                release_stage=_text(arguments, "release_stage"),
                start_date=_text(arguments, "start_date"),
                end_date=_text(arguments, "end_date"),
                limit=limit,
            )
        except ValidationError as exc:
            if "zero variance" in str(exc) or "two retained" in str(exc):
                return _not_established(
                    name, limit=limit, code="surprise_population_not_established",
                    message=str(exc), arguments=arguments,
                    selection_mode="latest_ex_post",
                )
            raise
        if not items:
            return _not_established(
                name, limit=limit, code="no_retained_surprises",
                message="No status-ok retained surprises matched the request.",
                arguments=arguments,
                selection_mode="latest_ex_post",
            )
        return _result(
            name, limit=limit, record_type="standardized_macro_surprise",
            records=_surprise_records(items),
            lineage_refs=_surprise_lineage(items),
            selection_mode="latest_ex_post",
        )

    if name == "rates.get_funding_conditions":
        facts, request, selection_audits, selection_warnings = _snapshot(
            source, FUNDING_COMPONENTS, arguments
        )
        mode, as_of, policy, observation_date = request
        records: list[Mapping[str, object]] = [
            _fact_record(item, mode=mode, as_of=as_of, date_only_policy=policy,
                         observation_date=observation_date)
            for item in facts
        ]
        spread = _spread(
            facts,
            left=_text(arguments, "spread_left"),
            right=_text(arguments, "spread_right"),
            kind="funding",
        )
        if spread is not None:
            records.append(spread)
        return _result(
            name, limit=limit, record_type="funding_condition",
            records=records, facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "rates.get_repo_facility_usage":
        facts, request, selection_audits, selection_warnings = _snapshot(
            source, REPO_COMPONENTS, arguments
        )
        mode, as_of, policy, observation_date = request
        return _result(
            name, limit=limit, record_type="repo_facility_usage",
            records=tuple(
                _fact_record(item, mode=mode, as_of=as_of,
                             date_only_policy=policy,
                             observation_date=observation_date)
                for item in facts
            ),
            facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "rates.curve_analytics":
        facts, request, selection_audits, selection_warnings = _snapshot(
            source, CURVE_COMPONENTS, arguments
        )
        mode, as_of, policy, observation_date = request
        records: list[Mapping[str, object]] = [
            _fact_record(item, mode=mode, as_of=as_of, date_only_policy=policy,
                         observation_date=observation_date)
            for item in facts
        ]
        spread = _spread(
            facts,
            left=_text(arguments, "spread_left_tenor"),
            right=_text(arguments, "spread_right_tenor"),
            kind="observed_treasury_curve",
        )
        if spread is not None:
            records.append(spread)
        return _result(
            name, limit=limit, record_type="curve_observation",
            records=records, facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "macro.get_liquidity_snapshot":
        facts, request, selection_audits, selection_warnings = _snapshot(
            source, LIQUIDITY_COMPONENTS, arguments, include_soma=True
        )
        mode, as_of, policy, observation_date = request
        return _result(
            name, limit=limit, record_type="liquidity_component",
            records=tuple(
                _fact_record(item, mode=mode, as_of=as_of,
                             date_only_policy=policy,
                             observation_date=observation_date)
                for item in facts
            ),
            facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "macro.get_liquidity_impulse":
        start_date = _text(arguments, "start_date", required=True)
        end_date = _text(arguments, "end_date", required=True)
        if start_date is None or end_date is None:
            raise ValidationError("Liquidity impulse bounds are required")
        (
            snapshots,
            request,
            selection_audits,
            selection_warnings,
        ) = _snapshots(
            source,
            LIQUIDITY_COMPONENTS,
            arguments,
            observation_dates=(start_date, end_date),
            include_soma=True,
        )
        start_facts, end_facts = snapshots
        start_by_component = {item.component: item for item in start_facts}
        end_by_component = {item.component: item for item in end_facts}
        records: list[Mapping[str, object]] = []
        used: list[ConditionFact] = []
        for component in sorted(start_by_component.keys() & end_by_component.keys()):
            first, second = start_by_component[component], end_by_component[component]
            if first.unit != second.unit:
                raise ValidationError("Liquidity component unit changed across bounds")
            if first.value is None or second.value is None:
                continue
            used.extend((first, second))
            records.append(
                {
                    "component": component,
                    "delta": second.value - first.value,
                    "end_period_start": second.period_start,
                    "end_value": second.value,
                    "methodology": "component_end_minus_start_no_aggregate",
                    "start_period_start": first.period_start,
                    "start_value": first.value,
                    "unit": first.unit,
                }
            )
        if not records:
            return _not_established(
                name, limit=limit, code="no_numeric_component_pairs",
                message="No numeric raw liquidity component had both bounds.",
                arguments=arguments,
                selection_audits=selection_audits,
                selection_warnings=selection_warnings,
                temporal=request,
            )
        return _result(
            name, limit=limit, record_type="liquidity_component_delta",
            records=records, facts=used,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "macro.get_credit_conditions":
        facts, request, selection_audits, selection_warnings = _snapshot(
            source, CREDIT_COMPONENTS, arguments
        )
        mode, as_of, policy, observation_date = request
        return _result(
            name, limit=limit, record_type="credit_condition",
            records=tuple(
                _fact_record(item, mode=mode, as_of=as_of,
                             date_only_policy=policy,
                             observation_date=observation_date)
                for item in facts
            ),
            facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    if name == "macro.regime_snapshot":
        include_context = bool(arguments.get("include_context", False))
        regime_components = ((
            "NBER_recession_indicator", NBER_RECESSION_SERIES_ID
        ),)
        components = (
            regime_components + LIQUIDITY_COMPONENTS + CREDIT_COMPONENTS
            if include_context
            else regime_components
        )
        facts, request, selection_audits, selection_warnings = _snapshot(
            source,
            components,
            arguments,
            include_soma=include_context,
        )
        fact = next(
            (
                item
                for item in facts
                if item.component == "NBER_recession_indicator"
            ),
            None,
        )
        if fact is None or fact.value not in {Decimal(0), Decimal(1)}:
            return _not_established(
                name, limit=limit, code="nber_indicator_not_available",
                message="No direct retained NBER expansion/recession indicator is available.",
                arguments=arguments,
                selection_audits=selection_audits,
                selection_warnings=selection_warnings,
                temporal=request,
            )
        mode, as_of, policy, observation_date = request
        records: list[Mapping[str, object]] = [
            {
                "methodology": "direct_stored_nber_indicator",
                "period_start": fact.period_start,
                "regime": "recession" if fact.value == Decimal(1) else "expansion",
                "selection_as_of": as_of,
                "selection_date_only_policy": policy,
                "selection_mode": mode,
                "unit": fact.unit,
                "version_id": fact.version_id,
            }
        ]
        if include_context:
            context_facts = tuple(
                item
                for item in facts
                if item.component != "NBER_recession_indicator"
            )
            records.extend(
                _fact_record(item, mode=mode, as_of=as_of,
                             date_only_policy=policy,
                             observation_date=observation_date)
                for item in context_facts
            )
        return _result(
            name, limit=limit, record_type="regime_snapshot",
            records=records, facts=facts,
            selection_audits=selection_audits,
            selection_warnings=selection_warnings,
            temporal=request,
        )

    facts, request, selection_audits, selection_warnings = _snapshot(
        source, LIQUIDITY_COMPONENTS + CREDIT_COMPONENTS, arguments,
        include_soma=True,
    )
    mode, as_of, policy, observation_date = request
    return _result(
        name, limit=limit, record_type="liquidity_credit_component",
        records=tuple(
            {
                **_fact_record(item, mode=mode, as_of=as_of,
                               date_only_policy=policy,
                               observation_date=observation_date),
                "methodology": "no_score_no_classification",
            }
            for item in facts
        ),
        facts=facts,
        selection_audits=selection_audits,
        selection_warnings=selection_warnings,
        temporal=request,
        research=True,
        arguments=arguments,
    )


__all__ = ("TOOL_NAMES", "invoke_macro_conditions")
