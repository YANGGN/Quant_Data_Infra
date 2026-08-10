"""Bounded, read-only Stage 4 company selection over the company store.

This internal repository deliberately accepts no database path or SQL.  It
uses the explicit host-owned company store and applies the shared mixed-
precision availability predicate before it selects the latest known version.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Hashable, Iterable, Mapping, Sequence

from ..errors import ResourceLimitError, ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, read_connection
from ..temporal import (
    DateOnlyPolicy,
    TemporalValue,
    availability_at_or_before,
)


_MAX_CANDIDATE_ROWS = 200_000
_MAX_QUERY_LIMIT = 10_000
_REQUIRED_DATASETS = frozenset(
    {
        "fixture.company.sec_evidence",
        "fixture.company.issuers",
        "fixture.company.filings",
        "fixture.company.fundamentals",
        "fixture.company.action_evidence",
        "fixture.company.corporate_actions",
        "fixture.company.expectation_evidence",
        "fixture.company.expectations",
        "fixture.company.filing_issuer_membership",
    }
)


@dataclass(frozen=True, slots=True)
class CompanyStage4Query:
    """One bounded CIK-backed latest or as-of company read request."""

    cik: str
    as_of: str | TemporalValue | None = None
    date_only_policy: str | DateOnlyPolicy = DateOnlyPolicy.COMPLETED_DATE
    limit: int = 1_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.cik, str)
            or len(self.cik) != 10
            or not self.cik.isdigit()
        ):
            raise ValidationError("Company query CIK must be a ten-digit SEC identity")
        try:
            policy = DateOnlyPolicy(self.date_only_policy)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Unsupported date-only policy") from exc
        object.__setattr__(self, "date_only_policy", policy)
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or not 1 <= self.limit <= _MAX_QUERY_LIMIT
        ):
            raise ResourceLimitError("Company query limit must be between 1 and 10000")
        if self.as_of is not None:
            cutoff = (
                self.as_of
                if isinstance(self.as_of, TemporalValue)
                else TemporalValue.parse(self.as_of, pointer="/as_of")
            )
            object.__setattr__(self, "as_of", cutoff)

    @property
    def cutoff(self) -> TemporalValue | None:
        return self.as_of if isinstance(self.as_of, TemporalValue) else None


def _availability(row: Mapping[str, Any]) -> TemporalValue:
    value = TemporalValue.parse(str(row["available_at"]), pointer="/available_at")
    if value.precision.value != str(row["available_precision"]):
        raise ValidationError("Stored company availability precision is inconsistent")
    return value


def _bounded(rows: Sequence[Mapping[str, Any]], *, limit: int, message: str) -> None:
    if len(rows) > limit:
        raise ResourceLimitError(message)


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("Stored company numeric value is invalid") from exc
    if not result.is_finite():
        raise ValidationError("Stored company numeric value is invalid")
    return result


def _select_versions(
    rows: Iterable[Mapping[str, Any]],
    *,
    query: CompanyStage4Query,
    identity: Callable[[Mapping[str, Any]], Hashable],
    sequence_field: str,
) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    grouped: dict[Hashable, list[Mapping[str, Any]]] = defaultdict(list)
    warnings: set[str] = set()
    for row in rows:
        if query.cutoff is not None:
            decision = availability_at_or_before(
                _availability(row),
                query.cutoff,
                query.date_only_policy,
            )
            warnings.update(decision.warnings)
            if not decision.included:
                continue
        grouped[identity(row)].append(row)
    selected = [
        max(
            candidates,
            key=lambda item: (
                int(item[sequence_field]),
                str(item["available_at"]),
            ),
        )
        for candidates in grouped.values()
    ]
    return selected, tuple(sorted(warnings))


def _query_rows(connection: Any, sql: str, parameters: tuple[object, ...]) -> list[Mapping[str, Any]]:
    rows = list(connection.execute(sql, parameters))
    if len(rows) > _MAX_CANDIDATE_ROWS:
        raise ResourceLimitError("Company version history exceeds the bounded query contract")
    return rows


class CompanyStage4Repository:
    """Read canonical Stage 4 company facts through the registered company store."""

    def __init__(self, store_map: StoreMap, registry: Registry) -> None:
        self._store_map = store_map
        self._registry = registry
        datasets = {item.id for item in registry.datasets_for(StoreRole.COMPANY.value)}
        if not _REQUIRED_DATASETS.issubset(datasets):
            raise ValidationError("Stage 4 company repository is not bound to the frozen catalog datasets")

    @staticmethod
    def _issuer_id(connection: Any, cik: str) -> str:
        row = connection.execute(
            "SELECT issuer_id FROM company_issuers WHERE cik=?",
            (cik,),
        ).fetchone()
        if row is None:
            raise ValidationError("Requested CIK-backed issuer is unavailable")
        return str(row["issuer_id"])

    def get_issuer(self, query: CompanyStage4Query) -> dict[str, Any]:
        with read_connection(self._store_map, StoreRole.COMPANY) as connection:
            issuer_id = self._issuer_id(connection, query.cik)
            versions = _query_rows(
                connection,
                """
                SELECT issuer_version_id, legal_name, entity_type, name_state,
                       missing_reason, available_at, available_precision,
                       version_sequence
                FROM company_issuer_versions
                WHERE issuer_id=?
                ORDER BY version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            selected, warnings = _select_versions(
                versions,
                query=query,
                identity=lambda _row: "issuer",
                sequence_field="version_sequence",
            )
            if not selected:
                raise ValidationError("Issuer metadata is unavailable at the requested cutoff")
            version = selected[0]
            links = self._issuer_links(connection, issuer_id, query)
        return {
            "issuer_id": issuer_id,
            "cik": query.cik,
            "legal_name": str(version["legal_name"]) if version["legal_name"] is not None else None,
            "entity_type": str(version["entity_type"]) if version["entity_type"] is not None else None,
            "name_state": str(version["name_state"]),
            "missing_reason": str(version["missing_reason"]) if version["missing_reason"] is not None else None,
            "available_at": str(version["available_at"]),
            "available_precision": str(version["available_precision"]),
            "links": links[0],
            "warnings": tuple(sorted(set(warnings).union(links[1]))),
        }

    @staticmethod
    def _issuer_links(
        connection: Any,
        issuer_id: str,
        query: CompanyStage4Query,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
        rows = _query_rows(
            connection,
            """
            SELECT provider, provider_symbol, security_identifier, valid_from,
                   valid_through, assertion_state, confidence, available_at,
                   available_precision, assertion_sequence, link_assertion_id
            FROM company_issuer_security_link_assertions
            WHERE issuer_id=?
            ORDER BY provider, provider_symbol, security_identifier, valid_from,
                     COALESCE(valid_through, ''), assertion_sequence
            LIMIT ?
            """,
            (issuer_id, _MAX_CANDIDATE_ROWS + 1),
        )
        selected, warnings = _select_versions(
            rows,
            query=query,
            identity=lambda row: (
                str(row["provider"]),
                str(row["provider_symbol"]),
                str(row["security_identifier"]),
                str(row["valid_from"]),
                "" if row["valid_through"] is None else str(row["valid_through"]),
            ),
            sequence_field="assertion_sequence",
        )
        active = [row for row in selected if str(row["assertion_state"]) == "active"]
        _bounded(active, limit=query.limit, message="Company issuer links exceed the query limit")
        return (
            tuple(
                {
                    "provider": str(row["provider"]),
                    "provider_symbol": str(row["provider_symbol"]),
                    "security_identifier": str(row["security_identifier"]),
                    "valid_from": str(row["valid_from"]),
                    "valid_through": str(row["valid_through"]) if row["valid_through"] is not None else None,
                    "confidence": str(row["confidence"]),
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "link_assertion_id": str(row["link_assertion_id"]),
                }
                for row in sorted(
                    active,
                    key=lambda item: (
                        str(item["provider"]),
                        str(item["provider_symbol"]),
                        str(item["valid_from"]),
                        str(item["security_identifier"]),
                    ),
                )
            ),
            warnings,
        )

    def get_filings(self, query: CompanyStage4Query) -> tuple[dict[str, Any], ...]:
        with read_connection(self._store_map, StoreRole.COMPANY) as connection:
            issuer_id = self._issuer_id(connection, query.cik)
            rows = _query_rows(
                connection,
                """
                SELECT filing.accession_number, filing.first_observed_issuer_id,
                       filing.form_type, filing.filing_date,
                       filing.filing_date_precision, filing.accepted_at,
                       filing.accepted_precision, filing.report_period_start,
                       filing.report_period_end, filing.primary_document,
                       filing.source_url, filing.available_at,
                       filing.available_precision
                FROM company_sec_filing_issuer_membership AS membership
                JOIN company_sec_filings AS filing
                  ON filing.accession_number=membership.accession_number
                WHERE membership.issuer_id=?
                ORDER BY filing.filing_date, filing.accession_number
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            eligible: list[Mapping[str, Any]] = []
            warnings: set[str] = set()
            for row in rows:
                if query.cutoff is not None:
                    decision = availability_at_or_before(
                        _availability(row), query.cutoff, query.date_only_policy
                    )
                    warnings.update(decision.warnings)
                    if not decision.included:
                        continue
                eligible.append(row)
            _bounded(eligible, limit=query.limit, message="Company filings exceed the query limit")
        return tuple(
            {
                "accession_number": str(row["accession_number"]),
                "first_observed_issuer_id": str(row["first_observed_issuer_id"]),
                "form_type": str(row["form_type"]),
                "filing_date": str(row["filing_date"]),
                "filing_date_precision": str(row["filing_date_precision"]),
                "accepted_at": str(row["accepted_at"]),
                "accepted_precision": str(row["accepted_precision"]),
                "report_period_start": str(row["report_period_start"]) if row["report_period_start"] is not None else None,
                "report_period_end": str(row["report_period_end"]) if row["report_period_end"] is not None else None,
                "primary_document": str(row["primary_document"]) if row["primary_document"] is not None else None,
                "source_url": str(row["source_url"]) if row["source_url"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "warnings": tuple(sorted(warnings)),
            }
            for row in eligible
        )

    def get_fundamentals(
        self,
        query: CompanyStage4Query,
        *,
        metric_codes: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        requested = None
        if metric_codes is not None:
            if isinstance(metric_codes, (str, bytes)) or not all(
                isinstance(item, str) and item for item in metric_codes
            ):
                raise ValidationError("Fundamental metric codes must be nonempty strings")
            requested = set(metric_codes)
        with read_connection(self._store_map, StoreRole.COMPANY) as connection:
            issuer_id = self._issuer_id(connection, query.cik)
            rows = _query_rows(
                connection,
                """
                SELECT fundamental.fundamental_version_id, fundamental.metric_id,
                       metric.display_name, metric.base_unit,
                       fundamental.mapping_id, fundamental.share_semantics,
                       fundamental.fiscal_year, fundamental.fiscal_period,
                       fundamental.reference_period_start,
                       fundamental.reference_period_end, fundamental.accession_number,
                       fundamental.source_fact_version_id, fundamental.value_text,
                       fundamental.value_state, fundamental.missing_reason,
                       fundamental.available_at, fundamental.available_precision,
                       fundamental.version_sequence, fundamental.source_snapshot_id,
                       mapping.mapping_version
                FROM company_fundamental_observation_versions AS fundamental
                JOIN company_metric_definitions AS metric
                  ON metric.metric_id=fundamental.metric_id
                JOIN company_metric_mappings AS mapping
                  ON mapping.mapping_id=fundamental.mapping_id
                WHERE fundamental.issuer_id=?
                ORDER BY fundamental.metric_id, fundamental.reference_period_end,
                         fundamental.version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            if requested is not None:
                rows = [
                    row for row in rows
                    if str(row["metric_id"]) in requested
                ]
            selected, warnings = _select_versions(
                rows,
                query=query,
                identity=lambda row: (
                    str(row["metric_id"]),
                    str(row["reference_period_end"]),
                ),
                sequence_field="version_sequence",
            )
            selected = sorted(
                selected,
                key=lambda row: (
                    str(row["reference_period_end"]),
                    str(row["metric_id"]),
                    int(row["version_sequence"]),
                ),
            )
            _bounded(
                selected,
                limit=query.limit,
                message="Company fundamentals exceed the query limit",
            )
        return tuple(
            {
                "fundamental_version_id": str(row["fundamental_version_id"]),
                "metric_id": str(row["metric_id"]),
                "metric_label": str(row["display_name"]),
                "base_unit": str(row["base_unit"]),
                "mapping_id": str(row["mapping_id"]),
                "mapping_version": str(row["mapping_version"]),
                "share_semantics": str(row["share_semantics"]),
                "fiscal_year": int(row["fiscal_year"]) if row["fiscal_year"] is not None else None,
                "fiscal_period": str(row["fiscal_period"]) if row["fiscal_period"] is not None else None,
                "reference_period_start": str(row["reference_period_start"]) if row["reference_period_start"] is not None else None,
                "reference_period_end": str(row["reference_period_end"]),
                "accession_number": str(row["accession_number"]),
                "source_fact_version_id": str(row["source_fact_version_id"]),
                "value": _as_decimal(row["value_text"]),
                "value_state": str(row["value_state"]),
                "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "warnings": warnings,
            }
            for row in selected
        )

    def get_corporate_actions(
        self,
        query: CompanyStage4Query,
    ) -> tuple[dict[str, Any], ...]:
        with read_connection(self._store_map, StoreRole.COMPANY) as connection:
            issuer_id = self._issuer_id(connection, query.cik)
            rows = _query_rows(
                connection,
                """
                SELECT action_version_id, provider, provider_symbol,
                       provider_event_id, action_kind, event_date, record_date,
                       pay_date, declared_date, cash_amount, currency,
                       split_from_quantity, split_to_quantity, action_state,
                       missing_reason, available_at, available_precision,
                       version_sequence, source_snapshot_id
                FROM company_corporate_action_versions
                WHERE issuer_id=?
                ORDER BY event_date, provider, provider_event_id, version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            selected, warnings = _select_versions(
                rows,
                query=query,
                identity=lambda row: (
                    str(row["provider"]),
                    str(row["provider_event_id"]),
                ),
                sequence_field="version_sequence",
            )
            selected = [
                row for row in selected if str(row["action_state"]) == "active"
            ]
            selected.sort(
                key=lambda row: (
                    str(row["event_date"]),
                    str(row["provider"]),
                    str(row["provider_event_id"]),
                )
            )
            _bounded(
                selected,
                limit=query.limit,
                message="Company corporate actions exceed the query limit",
            )
        return tuple(
            {
                "action_version_id": str(row["action_version_id"]),
                "provider": str(row["provider"]),
                "provider_symbol": str(row["provider_symbol"]),
                "provider_event_id": str(row["provider_event_id"]),
                "action_kind": str(row["action_kind"]),
                "event_date": str(row["event_date"]),
                "record_date": str(row["record_date"]) if row["record_date"] is not None else None,
                "pay_date": str(row["pay_date"]) if row["pay_date"] is not None else None,
                "declared_date": str(row["declared_date"]) if row["declared_date"] is not None else None,
                "cash_amount": _as_decimal(row["cash_amount"]),
                "currency": str(row["currency"]) if row["currency"] is not None else None,
                "split_from_quantity": _as_decimal(row["split_from_quantity"]),
                "split_to_quantity": _as_decimal(row["split_to_quantity"]),
                "available_at": str(row["available_at"]),
                "available_precision": str(row["available_precision"]),
                "source_snapshot_id": str(row["source_snapshot_id"]),
                "warnings": warnings,
            }
            for row in selected
        )

    def get_earnings(self, query: CompanyStage4Query) -> dict[str, Any]:
        with read_connection(self._store_map, StoreRole.COMPANY) as connection:
            issuer_id = self._issuer_id(connection, query.cik)
            consensus_rows = _query_rows(
                connection,
                """
                SELECT consensus.consensus_version_id, consensus.expectation_metric_id,
                       metric.display_name, metric.base_unit,
                       consensus.fiscal_year, consensus.fiscal_period,
                       consensus.reference_period_start,
                       consensus.reference_period_end, consensus.statistic_kind,
                       consensus.value, consensus.value_state, consensus.missing_reason,
                       consensus.available_at, consensus.available_precision,
                       consensus.version_sequence, consensus.source_snapshot_id
                FROM company_consensus_observation_versions AS consensus
                JOIN company_expectation_metric_definitions AS metric
                  ON metric.expectation_metric_id=consensus.expectation_metric_id
                WHERE consensus.issuer_id=?
                ORDER BY consensus.reference_period_end, consensus.expectation_metric_id,
                         consensus.statistic_kind, consensus.version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            event_rows = _query_rows(
                connection,
                """
                SELECT event.event_version_id, event.provider, event.provider_event_key,
                       event.event_state, event.event_at, event.event_precision,
                       event.fiscal_year, event.fiscal_period,
                       event.reference_period_end, event.missing_reason,
                       event.available_at, event.available_precision,
                       event.version_sequence, event.source_snapshot_id
                FROM company_earnings_event_versions AS event
                WHERE event.issuer_id=?
                ORDER BY event.event_at, event.provider, event.provider_event_key,
                         event.version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            guidance_rows = _query_rows(
                connection,
                """
                SELECT guidance.guidance_version_id, guidance.expectation_metric_id,
                       metric.display_name, metric.base_unit, guidance.accession_number,
                       guidance.source_url, guidance.target_period_start,
                       guidance.target_period_end, guidance.guidance_shape,
                       guidance.point_value, guidance.low_value, guidance.high_value,
                       guidance.narrative, guidance.missing_reason,
                       guidance.review_state, guidance.available_at,
                       guidance.available_precision, guidance.version_sequence,
                       guidance.source_snapshot_id
                FROM company_guidance_versions AS guidance
                JOIN company_expectation_metric_definitions AS metric
                  ON metric.expectation_metric_id=guidance.expectation_metric_id
                WHERE guidance.issuer_id=?
                ORDER BY guidance.target_period_end, guidance.expectation_metric_id,
                         guidance.version_sequence
                LIMIT ?
                """,
                (issuer_id, _MAX_CANDIDATE_ROWS + 1),
            )
            consensus, consensus_warnings = _select_versions(
                consensus_rows,
                query=query,
                identity=lambda row: (
                    str(row["expectation_metric_id"]),
                    str(row["reference_period_end"]),
                    str(row["statistic_kind"]),
                ),
                sequence_field="version_sequence",
            )
            events, event_warnings = _select_versions(
                event_rows,
                query=query,
                identity=lambda row: (
                    str(row["provider"]),
                    str(row["provider_event_key"]),
                ),
                sequence_field="version_sequence",
            )
            guidance, guidance_warnings = _select_versions(
                guidance_rows,
                query=query,
                identity=lambda row: (
                    str(row["expectation_metric_id"]),
                    str(row["target_period_end"]),
                ),
                sequence_field="version_sequence",
            )
            total = len(consensus) + len(events) + len(guidance)
            if total > query.limit:
                raise ResourceLimitError("Company earnings records exceed the query limit")
        warnings = tuple(
            sorted(set(consensus_warnings).union(event_warnings, guidance_warnings))
        )
        return {
            "cik": query.cik,
            "consensus": tuple(
                {
                    "consensus_version_id": str(row["consensus_version_id"]),
                    "expectation_metric_id": str(row["expectation_metric_id"]),
                    "metric_label": str(row["display_name"]),
                    "base_unit": str(row["base_unit"]),
                    "fiscal_year": int(row["fiscal_year"]) if row["fiscal_year"] is not None else None,
                    "fiscal_period": str(row["fiscal_period"]) if row["fiscal_period"] is not None else None,
                    "reference_period_start": str(row["reference_period_start"]) if row["reference_period_start"] is not None else None,
                    "reference_period_end": str(row["reference_period_end"]),
                    "statistic_kind": str(row["statistic_kind"]),
                    "value": _as_decimal(row["value"]),
                    "value_state": str(row["value_state"]),
                    "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "source_snapshot_id": str(row["source_snapshot_id"]),
                }
                for row in sorted(
                    consensus,
                    key=lambda row: (
                        str(row["reference_period_end"]),
                        str(row["expectation_metric_id"]),
                        str(row["statistic_kind"]),
                    ),
                )
            ),
            "events": tuple(
                {
                    "event_version_id": str(row["event_version_id"]),
                    "provider": str(row["provider"]),
                    "provider_event_key": str(row["provider_event_key"]),
                    "event_state": str(row["event_state"]),
                    "event_at": str(row["event_at"]) if row["event_at"] is not None else None,
                    "event_precision": str(row["event_precision"]) if row["event_precision"] is not None else None,
                    "fiscal_year": int(row["fiscal_year"]) if row["fiscal_year"] is not None else None,
                    "fiscal_period": str(row["fiscal_period"]) if row["fiscal_period"] is not None else None,
                    "reference_period_end": str(row["reference_period_end"]) if row["reference_period_end"] is not None else None,
                    "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "source_snapshot_id": str(row["source_snapshot_id"]),
                }
                for row in sorted(
                    events,
                    key=lambda row: (
                        str(row["event_at"]) if row["event_at"] is not None else "",
                        str(row["provider"]),
                        str(row["provider_event_key"]),
                    ),
                )
            ),
            "guidance": tuple(
                {
                    "guidance_version_id": str(row["guidance_version_id"]),
                    "expectation_metric_id": str(row["expectation_metric_id"]),
                    "metric_label": str(row["display_name"]),
                    "base_unit": str(row["base_unit"]),
                    "accession_number": str(row["accession_number"]) if row["accession_number"] is not None else None,
                    "source_url": str(row["source_url"]),
                    "target_period_start": str(row["target_period_start"]) if row["target_period_start"] is not None else None,
                    "target_period_end": str(row["target_period_end"]),
                    "guidance_shape": str(row["guidance_shape"]),
                    "point_value": _as_decimal(row["point_value"]),
                    "low_value": _as_decimal(row["low_value"]),
                    "high_value": _as_decimal(row["high_value"]),
                    "narrative": str(row["narrative"]) if row["narrative"] is not None else None,
                    "missing_reason": str(row["missing_reason"]) if row["missing_reason"] is not None else None,
                    "review_state": str(row["review_state"]),
                    "available_at": str(row["available_at"]),
                    "available_precision": str(row["available_precision"]),
                    "source_snapshot_id": str(row["source_snapshot_id"]),
                }
                for row in sorted(
                    guidance,
                    key=lambda row: (
                        str(row["target_period_end"]),
                        str(row["expectation_metric_id"]),
                    ),
                )
            ),
            "warnings": warnings,
        }


__all__ = ("CompanyStage4Query", "CompanyStage4Repository")
