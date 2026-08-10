"""Closed, read-only adapters over the established fixture repositories.

The adapter never accepts a database path, connection, SQL fragment, provider
endpoint, or import path.  Exact repository facts are projected into scalar
Stage 5 results; absent composites remain explicitly not established.
"""

from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

from quant_data.company.stage4_repository import (
    CompanyStage4Query,
    CompanyStage4Repository,
)
from quant_data.contracts import LineageRef, TimeSeries, TruncationV1, WarningV1
from quant_data.errors import ValidationError
from quant_data.macro.stage3_repository import (
    MacroStage3RecessionQuery,
    MacroStage3Repository,
)
from quant_data.market.stage4_options import (
    OptionsStage4Query,
    OptionsStage4Repository,
)
from quant_data.news.stage4_repository import NewsStage4Query, NewsStage4Repository
from quant_data.registry import Registry

from .context import ToolExecutionContext
from .results import (
    DiagnosticV1,
    QueryResult,
    ResearchEnvelopeV1,
    Scalar,
    fields_from_mapping,
    records_from_mappings,
    research_envelope,
)


# This excludes legacy routes, the declared unavailable intraday capability,
# and direct-series analytical graphs owned by the analytical adapter.
DOMAIN_OPERATION_NAMES: frozenset[str] = frozenset(
    {
        "macro.search_series",
        "macro.describe_series",
        "macro.release_surprises",
        "macro.revision_analysis",
        "macro.align_us_recessions",
        "macro.standardize_surprises",
        "macro.get_liquidity_snapshot",
        "macro.get_liquidity_impulse",
        "macro.get_credit_conditions",
        "macro.regime_snapshot",
        "company.search_issuers",
        "company.search_filings",
        "company.get_fundamentals",
        "company.get_corporate_actions",
        "company.get_share_count_history",
        "company.get_earnings_calendar",
        "company.get_consensus_history",
        "company.get_guidance_history",
        "company.get_estimate_revisions",
        "company.get_earnings_setup",
        "energy.get_electricity_retail_sales",
        "energy.get_weekly_fundamentals",
        "market.search_instruments",
        "market.get_returns",
        "market.get_forward_returns",
        "market.technical_indicators",
        "market.cross_sectional_performance",
        "rates.get_funding_conditions",
        "rates.get_repo_facility_usage",
        "rates.curve_analytics",
        "options.search_captures",
        "options.search_contracts",
        "options.get_surface_snapshot",
        "options.surface_diagnostics",
        "options.screen_contracts",
        "options.strategy_scenario",
        "news.search",
        "research.liquidity_credit_state",
    }
)

_RESEARCH_NAMES = frozenset({"research.liquidity_credit_state"})
_COMPANY_EARNINGS_NAMES = frozenset(
    {
        "company.get_earnings_calendar",
        "company.get_consensus_history",
        "company.get_guidance_history",
    }
)


def _limit(arguments: Mapping[str, Any], *, maximum: int = 10_000) -> int:
    value = arguments.get("limit", maximum)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValidationError("Tool operation limit is invalid")
    return value


def _as_of(arguments: Mapping[str, Any]) -> str | None:
    value = arguments.get("as_of")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError("Tool operation as_of must be a nonempty temporal string")
    return value


def _temporal_mode(
    arguments: Mapping[str, Any],
    *,
    supported: frozenset[str],
) -> tuple[str, str | None] | None:
    mode = arguments.get("mode", "latest")
    if not isinstance(mode, str):
        raise ValidationError("Tool operation mode is invalid")
    cutoff = _as_of(arguments)
    if mode not in supported:
        return None
    if mode == "as_of" and cutoff is None:
        raise ValidationError("as_of mode requires an explicit cutoff")
    if mode != "as_of" and cutoff is not None:
        raise ValidationError("Only as_of mode may provide a cutoff")
    return mode, cutoff


def _identifiers(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    value = arguments.get("identifiers", ())
    if not isinstance(value, (tuple, list)):
        return ()
    result = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(result) != len(value) or len(result) > 50:
        raise ValidationError("Tool operation identifiers are invalid")
    return result


def _company_cik(arguments: Mapping[str, Any], *, search: bool) -> str | None:
    if search:
        query = arguments.get("query")
        if isinstance(query, str):
            candidate = query.strip()
            if len(candidate) == 10 and candidate.isdigit():
                return candidate
        return None
    identifiers = _identifiers(arguments)
    if len(identifiers) != 1:
        return None
    candidate = identifiers[0]
    return candidate if len(candidate) == 10 and candidate.isdigit() else None


def _scalar(value: object) -> Scalar:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Repository returned a non-finite decimal")
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValidationError("Repository returned a non-finite float")
        return Decimal(str(value))
    if isinstance(value, (str, int, bool, type(None))):
        return value
    raise ValidationError("Repository returned a non-scalar result field")


def _project(source: Mapping[str, object], fields: Sequence[str]) -> dict[str, Scalar]:
    return {field: _scalar(source.get(field)) for field in fields}


def _warning_values(values: object) -> tuple[WarningV1, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    messages = sorted({item for item in values if isinstance(item, str) and item})
    return tuple(WarningV1("source_selection_warning", message) for message in messages)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _lineage(
    records: Iterable[Mapping[str, object]],
    *,
    dataset_id: str,
    store_role: str,
    semantic_field: str,
    evidence_field: str | None = None,
    snapshot_field: str | None = None,
    canonical_field: str | None = None,
) -> tuple[LineageRef, ...]:
    result: list[LineageRef] = []
    seen: set[tuple[str, str | None, str | None, str | None]] = set()
    for record in records:
        semantic_id = _text(record.get(semantic_field))
        if semantic_id is None:
            continue
        evidence_id = _text(record.get(evidence_field)) if evidence_field else None
        snapshot_id = _text(record.get(snapshot_field)) if snapshot_field else None
        canonical_version_id = (
            _text(record.get(canonical_field)) if canonical_field else semantic_id
        )
        key = (semantic_id, evidence_id, snapshot_id, canonical_version_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            LineageRef(
                dataset_id=dataset_id,
                store_role=store_role,
                semantic_id=semantic_id,
                evidence_id=evidence_id,
                snapshot_id=snapshot_id,
                canonical_version_id=canonical_version_id,
            )
        )
    return tuple(result)


def _result(
    name: str,
    *,
    limit: int,
    record_type: str,
    values: Sequence[Mapping[str, Scalar]],
    lineage: tuple[LineageRef, ...],
    warnings: tuple[WarningV1, ...] = (),
) -> QueryResult:
    records = records_from_mappings(record_type, values)
    return QueryResult(
        tool=name,
        records=records,
        warnings=warnings,
        lineage=lineage,
        truncation=TruncationV1(False, limit, len(records), len(records), False),
    )


def _research_inputs(
    arguments: Mapping[str, Any],
) -> tuple[tuple[TimeSeries, ...], tuple[LineageRef, ...]]:
    raw = arguments.get("series", ())
    if not isinstance(raw, (list, tuple)):
        raise ValidationError("Research operation series must be a bounded sequence")
    inputs = tuple(raw)
    if not inputs or not all(isinstance(item, TimeSeries) for item in inputs):
        raise ValidationError("Research operation requires typed TimeSeries inputs")
    lineage: list[LineageRef] = []
    for item in inputs:
        item.validate_lineage()
        dataset_id = item.provenance.get("dataset_id")
        store_role = item.provenance.get("store_role")
        if (
            isinstance(dataset_id, str)
            and dataset_id
            and store_role in {"market", "macro", "company", "news"}
        ):
            reference = LineageRef(
                dataset_id=dataset_id,
                store_role=store_role,
                semantic_id=item.series_id,
            )
            if reference not in lineage:
                lineage.append(reference)
    return inputs, tuple(lineage)


def _not_established(
    name: str,
    arguments: Mapping[str, Any],
    *,
    reason: str,
) -> QueryResult:
    limit = _limit(arguments)
    research = name in _RESEARCH_NAMES
    research_inputs, research_lineage = (
        _research_inputs(arguments) if research else ((), ())
    )
    return QueryResult(
        tool=name,
        status="not_established",
        diagnostics=(
            DiagnosticV1(
                "fixture_semantics_not_established",
                "The offline fixtures do not establish this requested result.",
                fields_from_mapping(
                    {
                        "forward_reconstructed_contract": True,
                        "reason": reason,
                    }
                ),
            ),
        ),
        warnings=(
            WarningV1(
                "not_established",
                "No historical, live, or composite value was fabricated.",
            ),
        ),
        truncation=TruncationV1(False, limit, 0, 0, False),
        research_contract=research_envelope(
            name, arguments, research_inputs, research_lineage,
        ) if research else ResearchEnvelopeV1(),
        lineage=research_lineage,
    )


def _is_fixture_unavailable(error: ValidationError) -> bool:
    message = str(error).lower()
    return "unavailable" in message or "no option capture" in message


def _macro_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    if name != "macro.align_us_recessions":
        return _not_established(
            name,
            arguments,
            reason="no_fixed_read_only_gateway_for_requested_macro_semantics",
        )
    temporal = _temporal_mode(arguments, supported=frozenset({"latest"}))
    if temporal is None:
        return _not_established(
            name,
            arguments,
            reason="recession_chronology_supports_latest_only",
        )
    limit = min(_limit(arguments), 1_000)
    rows = MacroStage3Repository(context.store_map, registry).get_recession_periods(
        MacroStage3RecessionQuery(limit=limit)
    )
    values = tuple(
        _project(
            row,
            (
                "recession_period_id",
                "provider",
                "peak_month",
                "trough_month",
                "status",
                "available_at",
                "available_precision",
                "evidence_id",
                "snapshot_id",
                "run_id",
            ),
        )
        for row in rows
    )
    return _result(
        name,
        limit=limit,
        record_type="macro_recession_period",
        values=values,
        lineage=_lineage(
            rows,
            dataset_id="fixture.macro.recession_periods",
            store_role="macro",
            semantic_field="recession_period_id",
            evidence_field="evidence_id",
            snapshot_field="snapshot_id",
        ),
    )


def _company_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    search = name in {"company.search_issuers", "company.search_filings"}
    cik = _company_cik(arguments, search=search)
    if cik is None:
        return _not_established(
            name,
            arguments,
            reason="fixture_company_gateway_requires_one_exact_cik",
        )
    if name in {
        "company.get_share_count_history",
        "company.get_estimate_revisions",
        "company.get_earnings_setup",
    }:
        return _not_established(
            name,
            arguments,
            reason="requested_company_composite_is_not_established_by_fixture_gateway",
        )

    if search:
        cutoff = _as_of(arguments)
    else:
        temporal = _temporal_mode(
            arguments,
            supported=frozenset({"latest", "as_of"}),
        )
        if temporal is None:
            return _not_established(
                name,
                arguments,
                reason="requested_company_vintage_mode_is_not_available",
            )
        _, cutoff = temporal
    limit = _limit(arguments)
    query = CompanyStage4Query(cik=cik, as_of=cutoff, limit=limit)
    repository = CompanyStage4Repository(context.store_map, registry)

    try:
        if name == "company.search_issuers":
            row = repository.get_issuer(query)
            values = (
                _project(
                    row,
                    (
                        "issuer_id",
                        "cik",
                        "legal_name",
                        "entity_type",
                        "name_state",
                        "missing_reason",
                        "available_at",
                        "available_precision",
                    ),
                ),
            )
            return _result(
                name,
                limit=limit,
                record_type="company_issuer",
                values=values,
                lineage=_lineage(
                    (row,),
                    dataset_id="fixture.company.issuers",
                    store_role="company",
                    semantic_field="issuer_id",
                ),
                warnings=_warning_values(row.get("warnings")),
            )
        if name == "company.search_filings":
            rows = repository.get_filings(query)
            values = tuple(
                _project(
                    row,
                    (
                        "accession_number",
                        "first_observed_issuer_id",
                        "form_type",
                        "filing_date",
                        "filing_date_precision",
                        "accepted_at",
                        "accepted_precision",
                        "report_period_start",
                        "report_period_end",
                        "primary_document",
                        "available_at",
                        "available_precision",
                    ),
                )
                for row in rows
            )
            warnings = _warning_values(
                tuple(
                    warning
                    for row in rows
                    for warning in row.get("warnings", ())
                    if isinstance(warning, str)
                )
            )
            return _result(
                name,
                limit=limit,
                record_type="company_filing",
                values=values,
                lineage=_lineage(
                    rows,
                    dataset_id="fixture.company.filings",
                    store_role="company",
                    semantic_field="accession_number",
                ),
                warnings=warnings,
            )
        if name == "company.get_fundamentals":
            rows = repository.get_fundamentals(query)
            values = tuple(
                _project(
                    row,
                    (
                        "fundamental_version_id",
                        "metric_id",
                        "metric_label",
                        "base_unit",
                        "mapping_id",
                        "mapping_version",
                        "share_semantics",
                        "fiscal_year",
                        "fiscal_period",
                        "reference_period_start",
                        "reference_period_end",
                        "accession_number",
                        "source_fact_version_id",
                        "value",
                        "value_state",
                        "missing_reason",
                        "available_at",
                        "available_precision",
                        "source_snapshot_id",
                    ),
                )
                for row in rows
            )
            return _result(
                name,
                limit=limit,
                record_type="company_fundamental",
                values=values,
                lineage=_lineage(
                    rows,
                    dataset_id="fixture.company.fundamentals",
                    store_role="company",
                    semantic_field="fundamental_version_id",
                    snapshot_field="source_snapshot_id",
                ),
                warnings=_warning_values(
                    tuple(
                        warning
                        for row in rows
                        for warning in row.get("warnings", ())
                        if isinstance(warning, str)
                    )
                ),
            )
        if name == "company.get_corporate_actions":
            rows = repository.get_corporate_actions(query)
            values = tuple(
                _project(
                    row,
                    (
                        "action_version_id",
                        "provider",
                        "provider_symbol",
                        "provider_event_id",
                        "action_kind",
                        "event_date",
                        "record_date",
                        "pay_date",
                        "declared_date",
                        "cash_amount",
                        "currency",
                        "split_from_quantity",
                        "split_to_quantity",
                        "available_at",
                        "available_precision",
                        "source_snapshot_id",
                    ),
                )
                for row in rows
            )
            return _result(
                name,
                limit=limit,
                record_type="company_corporate_action",
                values=values,
                lineage=_lineage(
                    rows,
                    dataset_id="fixture.company.corporate_actions",
                    store_role="company",
                    semantic_field="action_version_id",
                    snapshot_field="source_snapshot_id",
                ),
                warnings=_warning_values(
                    tuple(
                        warning
                        for row in rows
                        for warning in row.get("warnings", ())
                        if isinstance(warning, str)
                    )
                ),
            )
        if name in _COMPANY_EARNINGS_NAMES:
            earnings = repository.get_earnings(query)
            key = {
                "company.get_earnings_calendar": "events",
                "company.get_consensus_history": "consensus",
                "company.get_guidance_history": "guidance",
            }[name]
            rows = earnings[key]
            assert isinstance(rows, tuple)
            fields = {
                "events": (
                    "event_version_id",
                    "provider",
                    "provider_event_key",
                    "event_state",
                    "event_at",
                    "event_precision",
                    "fiscal_year",
                    "fiscal_period",
                    "reference_period_end",
                    "missing_reason",
                    "available_at",
                    "available_precision",
                    "source_snapshot_id",
                ),
                "consensus": (
                    "consensus_version_id",
                    "expectation_metric_id",
                    "metric_label",
                    "base_unit",
                    "fiscal_year",
                    "fiscal_period",
                    "reference_period_start",
                    "reference_period_end",
                    "statistic_kind",
                    "value",
                    "value_state",
                    "missing_reason",
                    "available_at",
                    "available_precision",
                    "source_snapshot_id",
                ),
                "guidance": (
                    "guidance_version_id",
                    "expectation_metric_id",
                    "metric_label",
                    "base_unit",
                    "accession_number",
                    "target_period_start",
                    "target_period_end",
                    "guidance_shape",
                    "point_value",
                    "low_value",
                    "high_value",
                    "narrative",
                    "missing_reason",
                    "review_state",
                    "available_at",
                    "available_precision",
                    "source_snapshot_id",
                ),
            }[key]
            semantic_field = {
                "events": "event_version_id",
                "consensus": "consensus_version_id",
                "guidance": "guidance_version_id",
            }[key]
            typed_rows = tuple(row for row in rows if isinstance(row, Mapping))
            values = tuple(_project(row, fields) for row in typed_rows)
            return _result(
                name,
                limit=limit,
                record_type={
                    "events": "company_earnings_event",
                    "consensus": "company_earnings_consensus",
                    "guidance": "company_earnings_guidance",
                }[key],
                values=values,
                lineage=_lineage(
                    typed_rows,
                    dataset_id="fixture.company.expectations",
                    store_role="company",
                    semantic_field=semantic_field,
                    snapshot_field="source_snapshot_id",
                ),
                warnings=_warning_values(earnings.get("warnings")),
            )
    except ValidationError as error:
        if _is_fixture_unavailable(error):
            return _not_established(
                name,
                arguments,
                reason="requested_company_fixture_record_is_unavailable",
            )
        raise

    return _not_established(
        name,
        arguments,
        reason="no_fixed_read_only_gateway_for_requested_company_semantics",
    )


def _options_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    if name != "options.get_surface_snapshot":
        return _not_established(
            name,
            arguments,
            reason="no_fixed_read_only_gateway_for_requested_options_semantics",
        )
    identifiers = _identifiers(arguments)
    if len(identifiers) != 1:
        return _not_established(
            name,
            arguments,
            reason="fixture_options_gateway_requires_one_stable_underlying_identifier",
        )
    temporal = _temporal_mode(
        arguments,
        supported=frozenset({"latest", "as_of"}),
    )
    if temporal is None:
        return _not_established(
            name,
            arguments,
            reason="requested_options_vintage_mode_is_not_available",
        )
    mode, cutoff = temporal
    limit = min(_limit(arguments), 1_000)
    query = OptionsStage4Query(
        underlying_instrument_id=identifiers[0],
        # The feed is a reviewed fixture binding, not a caller control.
        resolved_feed="fixture_options",
        mode=mode,
        as_of=cutoff,
        limit=limit,
    )
    try:
        raw = OptionsStage4Repository(context.store_map, registry).get_surface(query)
    except ValidationError as error:
        if _is_fixture_unavailable(error):
            return _not_established(
                name,
                arguments,
                reason="requested_options_fixture_capture_is_unavailable",
            )
        raise
    capture = raw["capture"]
    surface = raw["surface"]
    if not isinstance(capture, Mapping) or not isinstance(surface, list):
        raise ValidationError("Options repository returned an invalid snapshot")
    capture_id = _text(capture.get("capture_id"))
    rows: list[dict[str, object]] = []
    for item in surface:
        if not isinstance(item, Mapping):
            raise ValidationError("Options repository returned an invalid surface row")
        contract = item.get("contract")
        quote = item.get("quote")
        if not isinstance(contract, Mapping) or not isinstance(quote, Mapping):
            raise ValidationError("Options repository returned an invalid surface shape")
        rows.append(
            {
                "capture_id": capture_id,
                "semantic_identity": capture.get("semantic_identity"),
                "underlying_instrument_id": capture.get("underlying_instrument_id"),
                "resolved_feed": capture.get("resolved_feed"),
                "completed_at": capture.get("completed_at"),
                "capture_available_at": capture.get("available_at"),
                "capture_available_precision": capture.get("available_precision"),
                "capture_artifact_id": capture.get("artifact_id"),
                "capture_snapshot_id": capture.get("source_snapshot_id"),
                "surface_snapshot_id": item.get("surface_snapshot_id"),
                "contract_id": contract.get("contract_id"),
                "provider_contract_id": contract.get("provider_contract_id"),
                "contract_symbol": contract.get("contract_symbol"),
                "expiration_date": contract.get("expiration_date"),
                "strike_price": contract.get("strike_price"),
                "option_type": contract.get("option_type"),
                "deliverable_kind": contract.get("deliverable_kind"),
                "contract_multiplier": contract.get("contract_multiplier"),
                "contract_status": contract.get("contract_status"),
                "surface_state": item.get("state"),
                "missing_reason": item.get("missing_reason"),
                "exclusion_reason": item.get("exclusion_reason"),
                "bid_price": quote.get("bid_price"),
                "ask_price": quote.get("ask_price"),
                "last_price": quote.get("last_price"),
                "implied_volatility": quote.get("implied_volatility"),
                "delta": quote.get("delta"),
                "gamma": quote.get("gamma"),
                "theta": quote.get("theta"),
                "vega": quote.get("vega"),
                "rho": quote.get("rho"),
                "available_at": item.get("available_at"),
                "available_precision": item.get("available_precision"),
            }
        )
    if not rows:
        rows.append(
            {
                "capture_id": capture_id,
                "semantic_identity": capture.get("semantic_identity"),
                "underlying_instrument_id": capture.get("underlying_instrument_id"),
                "resolved_feed": capture.get("resolved_feed"),
                "completed_at": capture.get("completed_at"),
                "capture_available_at": capture.get("available_at"),
                "capture_available_precision": capture.get("available_precision"),
                "capture_artifact_id": capture.get("artifact_id"),
                "capture_snapshot_id": capture.get("source_snapshot_id"),
                "surface_snapshot_id": None,
                "contract_id": None,
                "provider_contract_id": None,
                "contract_symbol": None,
                "expiration_date": None,
                "strike_price": None,
                "option_type": None,
                "deliverable_kind": None,
                "contract_multiplier": None,
                "contract_status": None,
                "surface_state": "not_available",
                "missing_reason": "no_selected_surface_rows",
                "exclusion_reason": None,
                "bid_price": None,
                "ask_price": None,
                "last_price": None,
                "implied_volatility": None,
                "delta": None,
                "gamma": None,
                "theta": None,
                "vega": None,
                "rho": None,
                "available_at": capture.get("available_at"),
                "available_precision": capture.get("available_precision"),
            }
        )
    fields = tuple(rows[0])
    values = tuple(_project(row, fields) for row in rows)
    capture_lineage = _lineage(
        (capture,),
        dataset_id="fixture.market.option_capture_evidence",
        store_role="market",
        semantic_field="capture_id",
        evidence_field="artifact_id",
        snapshot_field="source_snapshot_id",
    )
    surface_lineage = _lineage(
        rows,
        dataset_id="fixture.market.options",
        store_role="market",
        semantic_field="surface_snapshot_id",
        evidence_field="capture_artifact_id",
        snapshot_field="capture_snapshot_id",
    )
    return _result(
        name,
        limit=max(limit, len(values)),
        record_type="option_surface_snapshot",
        values=values,
        lineage=capture_lineage + surface_lineage,
        warnings=_warning_values(raw.get("warnings")),
    )


def _news_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    query_text = arguments.get("query")
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValidationError("News search query is required")
    limit = min(_limit(arguments), 1_000)
    rows = NewsStage4Repository(context.store_map, registry).search(
        NewsStage4Query(as_of=_as_of(arguments), limit=limit),
        query_text.strip(),
    )
    fields = (
        "news_item_version_id",
        "item_id",
        "source_name",
        "source_item_id",
        "source_kind",
        "content_identity",
        "headline",
        "summary",
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
        "supersedes_news_item_version_id",
        "source_snapshot_id",
        "run_id",
        "source_row",
    )
    values = tuple(_project(row, fields) for row in rows)
    warnings = _warning_values(
        tuple(
            warning
            for row in rows
            for warning in row.get("warnings", ())
            if isinstance(warning, str)
        )
    )
    return _result(
        name,
        limit=limit,
        record_type="news_item",
        values=values,
        lineage=_lineage(
            rows,
            dataset_id="fixture.news.items",
            store_role="news",
            semantic_field="news_item_version_id",
            snapshot_field="source_snapshot_id",
        ),
        warnings=warnings,
    )


def invoke_domain_operation(
    name: str,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
    registry: Registry,
) -> QueryResult:
    """Run one closed, host-routed fixture-domain operation."""

    if name not in DOMAIN_OPERATION_NAMES:
        raise LookupError("Domain operation is not registered")
    if not isinstance(arguments, Mapping):
        raise ValidationError("Domain operation arguments must be a mapping")
    context.checkpoint()
    limit = _limit(arguments)
    context.budget.require(rows=limit, operations=limit)

    if name.startswith("macro."):
        result = _macro_operation(name, arguments, context, registry)
    elif name.startswith("company."):
        result = _company_operation(name, arguments, context, registry)
    elif name.startswith("options."):
        result = _options_operation(name, arguments, context, registry)
    elif name == "news.search":
        result = _news_operation(name, arguments, context, registry)
    else:
        result = _not_established(
            name,
            arguments,
            reason="no_fixed_read_only_gateway_for_requested_semantics",
        )
    context.checkpoint()
    return result


__all__ = ("DOMAIN_OPERATION_NAMES", "invoke_domain_operation")
