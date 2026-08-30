"""Fixed read-only macro component gateway for v2 condition tools."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..errors import ResourceLimitError, ValidationError
from ..registry import Registry
from ..stores import StoreMap, StoreRole, quiet_immutable_read_connection
from ..temporal import DateOnlyPolicy, TemporalValue, availability_at_or_before, parse_date
from .canonical_access import (
    OFFICIAL_VINTAGE_SERIES_IDS,
    CanonicalMacroRepository,
    MacroSeriesRequest,
    MacroSeriesSelection,
    _reconcile_store_contract,
)
from .fmp_release_surprises import ALL_SURPRISE_KINDS, GDP_ADVANCE_KIND, MacroReleaseSurpriseRepository, ReleaseSurprise
from .fmp_treasury_curve import TENOR_MANIFEST
from .nyfed_overnight_rates import RATE_MANIFEST
from .nyfed_repo_facilities import FACILITY_MANIFEST
from .official_conditions import BIS_MANIFEST, CHICAGO_MANIFEST, CMDI_MANIFEST, FED_POLICY_RATE_MANIFEST, H41_MANIFEST, NBER_RECESSION_MANIFEST, TREASURY_TGA_MANIFEST


_MAX_HISTORY = 10_000
_SOMA_DATASET_ID = "fixture.macro.soma_summary"
_SOMA_DATASET_IDS = (
    _SOMA_DATASET_ID,
    "fixture.macro.soma_evidence",
)


@dataclass(frozen=True, slots=True)
class ConditionFact:
    component: str
    series_id: str
    period_start: str
    period_end: str
    value: Decimal | None
    missing_reason: str | None
    unit: str
    value_representation: str
    available_at: str
    available_precision: str
    captured_at: str
    captured_precision: str
    version_id: str
    dataset_id: str
    evidence_id: str | None
    snapshot_id: str | None
    source_vintage_identity: str | None = None


@dataclass(frozen=True, slots=True)
class RevisionFact:
    series_id: str
    period_start: str
    period_end: str
    unit: str
    first_value: Decimal | None
    latest_value: Decimal | None
    signed_revision: Decimal | None
    absolute_revision: Decimal | None
    first_version_id: str
    latest_version_id: str
    first_evidence_id: str | None
    latest_evidence_id: str | None
    first_snapshot_id: str | None
    latest_snapshot_id: str | None


@dataclass(frozen=True, slots=True)
class StandardizedSurprise:
    event_at: str
    event_id: str
    event_version_id: str
    kind: str
    release_stage: str | None
    reference_period: str | None
    unit: str
    surprise: Decimal
    z_score: Decimal
    sample_count: int
    sample_mean: Decimal
    population_standard_deviation: Decimal
    availability_assumption: str
    official_version_id: str | None
    event_evidence_id: str | None = None
    event_snapshot_id: str | None = None
    official_evidence_id: str | None = None
    coalesced_event_lineage: tuple[tuple[str, str | None, str | None], ...] = ()


@dataclass(frozen=True, slots=True)
class ConditionSelectionAudit:
    """One retained canonical selection contributing to a condition snapshot."""

    component: str
    selection: MacroSeriesSelection


@dataclass(frozen=True, slots=True)
class ConditionSnapshot:
    facts: tuple[ConditionFact, ...]
    selection_audit: tuple[ConditionSelectionAudit, ...]
    warnings: tuple[str, ...]


_rates = {item.code: item.series_id for item in RATE_MANIFEST}
_policy = {item.provider_code: item.series_id for item in FED_POLICY_RATE_MANIFEST}
FUNDING_COMPONENTS = (
    ("EFFR", _rates["EFFR"]), ("OBFR", _rates["OBFR"]),
    ("TGCR", _rates["TGCR"]), ("BGCR", _rates["BGCR"]),
    ("SOFR", _rates["SOFR"]), ("IORB", _policy["IORB"]),
    ("target_range_lower_bound", _policy["DFEDTARL"]),
    ("target_range_upper_bound", _policy["DFEDTARU"]),
)
REPO_COMPONENTS = tuple((item.code, item.series_id) for item in FACILITY_MANIFEST)
CURVE_COMPONENTS = tuple((item.tenor, item.series_id) for item in TENOR_MANIFEST)
LIQUIDITY_COMPONENTS = (
    ("federal_reserve_total_assets_less_eliminations", H41_MANIFEST[0].series_id),
    ("reserve_balances", H41_MANIFEST[1].series_id),
    ("treasury_general_account", TREASURY_TGA_MANIFEST[0].series_id),
)
CREDIT_COMPONENTS = (
    ("NFCI", CHICAGO_MANIFEST[0].series_id),
    ("ANFCI", CHICAGO_MANIFEST[1].series_id),
    ("BIS_credit_to_gdp", BIS_MANIFEST[0].series_id),
    ("BIS_credit_gap", BIS_MANIFEST[1].series_id),
    ("BIS_debt_service_ratio", BIS_MANIFEST[2].series_id),
    ("CMDI_market", CMDI_MANIFEST[0].series_id),
    ("CMDI_investment_grade", CMDI_MANIFEST[1].series_id),
    ("CMDI_high_yield", CMDI_MANIFEST[2].series_id),
)
NBER_RECESSION_SERIES_ID = NBER_RECESSION_MANIFEST[0].series_id


def _date(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} must be an ISO date or null")
    return parse_date(value, pointer=f"/{label}").isoformat()


def _number(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValidationError(f"Stored {label} is invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Stored {label} is invalid") from exc
    if not result.is_finite():
        raise ValidationError(f"Stored {label} is invalid")
    return result


def _temporal(mode: str, as_of: str | None, policy: str | DateOnlyPolicy) -> tuple[str, str | None, DateOnlyPolicy]:
    if mode not in {"latest", "as_of"}:
        raise ValidationError("Macro conditions support latest and as_of modes")
    try:
        parsed_policy = DateOnlyPolicy(policy)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Macro conditions date-only policy is unsupported") from exc
    if mode == "as_of":
        if not isinstance(as_of, str) or not as_of:
            raise ValidationError("Macro conditions as_of mode requires a cutoff")
        TemporalValue.parse(as_of, pointer="/as_of")
    elif as_of is not None:
        raise ValidationError("Macro conditions latest mode does not accept as_of")
    return mode, as_of, parsed_policy


def _fact(component: str, record: Mapping[str, object], dataset_id: str) -> ConditionFact:
    value = _number(record.get("value"), "macro value")
    missing = record.get("missing_reason")
    if missing is not None and not isinstance(missing, str):
        raise ValidationError("Stored macro missingness is invalid")
    if (value is None) == (missing is None):
        raise ValidationError("Stored macro value/missingness is inconsistent")
    keys = ("series_id", "period_start", "period_end", "unit", "value_representation", "available_at", "available_precision", "captured_at", "captured_precision", "version_id")
    if any(not isinstance(record.get(key), str) or not record[key] for key in keys):
        raise ValidationError("Stored macro condition record is invalid")
    evidence = record.get("artifact_id") or record.get("capture_id")
    snapshot = record.get("snapshot_id")
    vintage = record.get("source_vintage_identity")
    return ConditionFact(
        component, str(record["series_id"]), str(record["period_start"]),
        str(record["period_end"]), value, missing, str(record["unit"]),
        str(record["value_representation"]), str(record["available_at"]),
        str(record["available_precision"]), str(record["captured_at"]),
        str(record["captured_precision"]), str(record["version_id"]), dataset_id,
        str(evidence) if isinstance(evidence, str) and evidence else None,
        str(snapshot) if isinstance(snapshot, str) and snapshot else None,
        str(vintage) if isinstance(vintage, str) and vintage else None,
    )


def _latest(facts: Iterable[ConditionFact]) -> ConditionFact | None:
    values = tuple(facts)
    return max(values, key=lambda item: (item.period_start, item.period_end, item.available_at, item.captured_at, item.version_id)) if values else None


class MacroConditionsRepository:
    """Read only reviewed raw facts through host-controlled macro storage."""

    def __init__(self, store_map: StoreMap | None, registry: Registry | None, *, canonical_reader: Any = None, surprise_reader: Any = None) -> None:
        self._stores = store_map
        self._registry = registry
        self._canonical = canonical_reader or (
            CanonicalMacroRepository(store_map, registry)
            if store_map is not None and registry is not None else None
        )
        self._surprises = surprise_reader
        if self._canonical is None:
            raise ValidationError("Macro conditions require a canonical macro reader")

    @staticmethod
    def _facts_from_selection(
        component: str, selection: MacroSeriesSelection
    ) -> tuple[ConditionFact, ...]:
        if selection.truncated:
            raise ResourceLimitError(
                "Macro condition history exceeds the fixed read bound"
            )
        return tuple(
            _fact(component, row, selection.dataset_ids[0])
            for row in selection.records
        )

    def _shared_canonical_batch(
        self,
        connection: Any,
        requests: tuple[MacroSeriesRequest, ...],
    ) -> tuple[MacroSeriesSelection, ...]:
        reader = getattr(self._canonical, "_get_series_batch_in_connection", None)
        if not callable(reader):
            raise ValidationError(
                "Macro condition reader does not support a shared immutable snapshot"
            )
        selections = tuple(reader(connection, requests))
        if len(selections) != len(requests) or any(
            not isinstance(item, MacroSeriesSelection) for item in selections
        ):
            raise ValidationError("Shared macro condition selection is invalid")
        return selections

    def _soma_facts_in_connection(
        self, connection: Any
    ) -> tuple[ConditionFact, ...]:
        if self._registry is None:
            raise ValidationError("SOMA selection requires a macro registry")
        _reconcile_store_contract(connection, self._registry, _SOMA_DATASET_IDS)
        rows = list(
            connection.execute(
                """
                SELECT component.component_id, component.as_of_date,
                       component.value_text, component.missing_reason, component.unit,
                       component.available_at, component.available_precision,
                       snapshot.captured_at, snapshot.captured_precision,
                       snapshot.snapshot_id, artifact.artifact_id
                FROM soma_snapshots AS snapshot
                JOIN ingestion_runs AS run ON run.run_id=snapshot.run_id
                JOIN soma_summary_components AS component
                  ON component.snapshot_id=snapshot.snapshot_id
                LEFT JOIN soma_snapshot_artifacts AS membership
                  ON membership.snapshot_id=snapshot.snapshot_id
                 AND membership.artifact_ordinal=1
                LEFT JOIN soma_source_artifacts AS artifact
                  ON artifact.artifact_id=membership.artifact_id
                WHERE snapshot.completeness='complete' AND run.status='succeeded'
                  AND component.category='total' AND component.measure='amount'
                ORDER BY component.as_of_date, component.available_at,
                         snapshot.captured_at, snapshot.snapshot_id
                LIMIT 10001
                """
            )
        )
        if len(rows) > _MAX_HISTORY:
            raise ResourceLimitError("SOMA summary exceeds the fixed read bound")
        facts: list[ConditionFact] = []
        for row in rows:
            value = _number(row["value_text"], "SOMA value")
            missing = row["missing_reason"]
            if missing is not None and not isinstance(missing, str):
                raise ValidationError("Stored SOMA missingness is invalid")
            if (value is None) == (missing is None):
                raise ValidationError("Stored SOMA value/missingness is inconsistent")
            facts.append(
                ConditionFact(
                    "SOMA_total",
                    "macro.nyfed.soma.total",
                    str(row["as_of_date"]),
                    str(row["as_of_date"]),
                    value,
                    missing,
                    str(row["unit"]),
                    "amount",
                    str(row["available_at"]),
                    str(row["available_precision"]),
                    str(row["captured_at"]),
                    str(row["captured_precision"]),
                    str(row["component_id"]),
                    _SOMA_DATASET_ID,
                    (
                        str(row["artifact_id"])
                        if row["artifact_id"] is not None
                        else None
                    ),
                    str(row["snapshot_id"]),
                )
            )
        return tuple(facts)

    @staticmethod
    def _select_soma_snapshot(
        facts: tuple[ConditionFact, ...],
        *,
        observation_date: str | None,
        mode: str,
        as_of: str | None,
        policy: DateOnlyPolicy,
    ) -> tuple[ConditionFact | None, tuple[str, ...]]:
        cutoff = (
            TemporalValue.parse(as_of, pointer="/as_of")
            if mode == "as_of" and as_of is not None
            else None
        )
        selected: list[ConditionFact] = []
        warnings: set[str] = set()
        for fact in facts:
            if observation_date is not None and fact.period_start > observation_date:
                continue
            if cutoff is not None:
                available = TemporalValue.parse(
                    fact.available_at, pointer="/stored/soma/available_at"
                )
                if available.precision.value != fact.available_precision:
                    raise ValidationError("Stored SOMA availability precision is invalid")
                decision = availability_at_or_before(available, cutoff, policy)
                if not decision.included:
                    continue
                warnings.update(decision.warnings)
            selected.append(fact)
        return _latest(selected), tuple(sorted(warnings))

    def snapshots_with_audit(
        self,
        components: tuple[tuple[str, str], ...],
        *,
        observation_dates: tuple[str | None, ...],
        mode: str,
        as_of: str | None,
        date_only_policy: str | DateOnlyPolicy,
        include_soma: bool = False,
    ) -> tuple[ConditionSnapshot, ...]:
        """Select one or two raw condition snapshots from one transaction."""

        if not components:
            raise ValidationError("Macro condition components are required")
        if not observation_dates or len(observation_dates) > 2:
            raise ResourceLimitError(
                "Macro condition snapshots must contain one or two observation dates"
            )
        parsed_dates = tuple(
            _date(value, "observation_date") for value in observation_dates
        )
        mode, as_of, policy = _temporal(mode, as_of, date_only_policy)
        requests = tuple(
            MacroSeriesRequest(
                series_id=series_id,
                start_date=None,
                end_date=observation_date,
                mode=mode,
                as_of=as_of,
                date_only_policy=policy,
                limit=_MAX_HISTORY,
            )
            for observation_date in parsed_dates
            for _component, series_id in components
        )
        if len(requests) > 20:
            raise ResourceLimitError(
                "Macro condition snapshots exceed the fixed series read bound"
            )

        if self._stores is None or self._registry is None:
            if include_soma:
                raise ValidationError("SOMA selection requires a shared macro store")
            selections = tuple(
                self._canonical.get_series(request) for request in requests
            )
            soma_facts: tuple[ConditionFact, ...] = ()
        else:
            with quiet_immutable_read_connection(
                self._stores, StoreRole.MACRO
            ) as connection:
                selections = self._shared_canonical_batch(connection, requests)
                soma_facts = (
                    self._soma_facts_in_connection(connection)
                    if include_soma
                    else ()
                )

        offset = 0
        result: list[ConditionSnapshot] = []
        for observation_date in parsed_dates:
            facts: list[ConditionFact] = []
            audit: list[ConditionSelectionAudit] = []
            warnings: set[str] = set()
            for component, _series_id in components:
                selection = selections[offset]
                offset += 1
                audit.append(ConditionSelectionAudit(component, selection))
                warnings.update(getattr(selection, "warnings", ()))
                selected = _latest(self._facts_from_selection(component, selection))
                if selected is not None:
                    facts.append(selected)
            if include_soma:
                soma, soma_warnings = self._select_soma_snapshot(
                    soma_facts,
                    observation_date=observation_date,
                    mode=mode,
                    as_of=as_of,
                    policy=policy,
                )
                warnings.update(soma_warnings)
                if soma is not None:
                    facts.append(soma)
            if mode == "as_of":
                warnings.add("point_in_time_selection_applied")
            result.append(
                ConditionSnapshot(
                    tuple(facts), tuple(audit), tuple(sorted(warnings))
                )
            )
        return tuple(result)

    def snapshot_with_audit(
        self,
        components: tuple[tuple[str, str], ...],
        *,
        observation_date: str | None,
        mode: str,
        as_of: str | None,
        date_only_policy: str | DateOnlyPolicy,
        include_soma: bool = False,
    ) -> ConditionSnapshot:
        return self.snapshots_with_audit(
            components,
            observation_dates=(observation_date,),
            mode=mode,
            as_of=as_of,
            date_only_policy=date_only_policy,
            include_soma=include_soma,
        )[0]

    def snapshot(
        self,
        components: tuple[tuple[str, str], ...],
        *,
        observation_date: str | None,
        mode: str,
        as_of: str | None,
        date_only_policy: str | DateOnlyPolicy,
        include_soma: bool = False,
    ) -> tuple[ConditionFact, ...]:
        return self.snapshot_with_audit(
            components,
            observation_date=observation_date,
            mode=mode,
            as_of=as_of,
            date_only_policy=date_only_policy,
            include_soma=include_soma,
        ).facts

    def revision_analysis_with_audit(
        self,
        *,
        series_id: str,
        start_date: str | None,
        end_date: str | None,
        limit: int,
    ) -> tuple[tuple[RevisionFact, ...], tuple[ConditionSelectionAudit, ...]]:
        if series_id not in OFFICIAL_VINTAGE_SERIES_IDS:
            raise ValidationError("Revision analysis accepts only official-vintage series")
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        if start is not None and end is not None and end < start:
            raise ValidationError("Revision-analysis end_date precedes start_date")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_HISTORY
        ):
            raise ResourceLimitError("Revision-analysis limit is invalid")
        requests = (
            MacroSeriesRequest(
                series_id=series_id,
                start_date=start,
                end_date=end,
                mode="first_release",
                as_of=None,
                date_only_policy=DateOnlyPolicy.COMPLETED_DATE,
                limit=_MAX_HISTORY,
            ),
            MacroSeriesRequest(
                series_id=series_id,
                start_date=start,
                end_date=end,
                mode="latest",
                as_of=None,
                date_only_policy=DateOnlyPolicy.COMPLETED_DATE,
                limit=_MAX_HISTORY,
            ),
        )
        if self._stores is None or self._registry is None:
            first, latest = tuple(
                self._canonical.get_series(request) for request in requests
            )
        else:
            with quiet_immutable_read_connection(
                self._stores, StoreRole.MACRO
            ) as connection:
                first, latest = self._shared_canonical_batch(connection, requests)

        initial_by_period = {
            item.period_start: item
            for item in self._facts_from_selection("first_release", first)
        }
        current_by_period = {
            item.period_start: item
            for item in self._facts_from_selection("latest", latest)
        }
        records: list[RevisionFact] = []
        for period in sorted(initial_by_period.keys() & current_by_period.keys()):
            initial, current = initial_by_period[period], current_by_period[period]
            if initial.unit != current.unit:
                raise ValidationError("Official-vintage revision units are inconsistent")
            revision = (
                current.value - initial.value
                if initial.value is not None and current.value is not None
                else None
            )
            records.append(
                RevisionFact(
                    series_id,
                    period,
                    current.period_end,
                    current.unit,
                    initial.value,
                    current.value,
                    revision,
                    abs(revision) if revision is not None else None,
                    initial.version_id,
                    current.version_id,
                    initial.evidence_id,
                    current.evidence_id,
                    initial.snapshot_id,
                    current.snapshot_id,
                )
            )
        return (
            tuple(records[-limit:]),
            (
                ConditionSelectionAudit("first_release", first),
                ConditionSelectionAudit("latest", latest),
            ),
        )

    def revision_analysis(
        self,
        *,
        series_id: str,
        start_date: str | None,
        end_date: str | None,
        limit: int,
    ) -> tuple[RevisionFact, ...]:
        records, _audit = self.revision_analysis_with_audit(
            series_id=series_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        return records

    def standardize_surprises(self, *, kind: str, release_stage: str | None, start_date: str | None, end_date: str | None, limit: int) -> tuple[StandardizedSurprise, ...]:
        if kind not in ALL_SURPRISE_KINDS:
            raise ValidationError("Surprise kind is not reviewed")
        if release_stage is not None:
            if not isinstance(release_stage, str) or release_stage not in {"advance", "initial", "second", "third"}:
                raise ValidationError("Surprise release_stage is invalid")
            if kind != GDP_ADVANCE_KIND:
                raise ValidationError("release_stage is only valid for GDP surprises")
        start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
        if start is not None and end is not None and end < start:
            raise ValidationError("Surprise end_date precedes start_date")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= _MAX_HISTORY:
            raise ResourceLimitError("Surprise standardization limit is invalid")
        reader = self._surprises
        if reader is None:
            if self._stores is None or self._registry is None:
                raise ValidationError("Surprise standardization requires a macro store")
            reader = MacroReleaseSurpriseRepository(macro_store=self._stores.macro, registry=self._registry)
        selected = tuple(
            item for item in reader.query(start_date=start, end_date=end, kinds=(kind,))
            if item.status == "ok" and item.surprise is not None
            and (release_stage is None or item.release_stage == release_stage)
        )[-limit:]
        groups: dict[str, list[ReleaseSurprise]] = {}
        for item in selected:
            groups.setdefault(item.unit, []).append(item)
        output: list[StandardizedSurprise] = []
        for unit in sorted(groups):
            rows = groups[unit]
            if len(rows) < 2:
                raise ValidationError("Surprise standardization requires two retained observations per unit")
            values = tuple(item.surprise for item in rows)
            assert all(item is not None for item in values)
            population = tuple(item for item in values if item is not None)
            mean = sum(population, Decimal(0)) / Decimal(len(population))
            variance = sum((value - mean) ** 2 for value in population) / Decimal(len(population))
            if variance == 0:
                raise ValidationError("Surprise standardization is not established for zero variance")
            deviation = variance.sqrt()
            for item in rows:
                assert item.surprise is not None
                output.append(StandardizedSurprise(
                    item.event_at, item.event_id, item.event_version_id, item.kind,
                    item.release_stage, item.reference_period, item.unit,
                    item.surprise, (item.surprise - mean) / deviation, len(population),
                    mean, deviation, item.availability_assumption,
                    item.official_version_id, item.event_evidence_id,
                    item.event_snapshot_id, item.official_evidence_id,
                    item.coalesced_event_lineage,
                ))
        return tuple(sorted(output, key=lambda item: (item.event_at, item.kind, item.event_id)))


__all__ = (
    "CREDIT_COMPONENTS", "CURVE_COMPONENTS", "ConditionFact",
    "ConditionSelectionAudit", "ConditionSnapshot", "FUNDING_COMPONENTS",
    "LIQUIDITY_COMPONENTS", "MacroConditionsRepository",
    "NBER_RECESSION_SERIES_ID", "REPO_COMPONENTS", "RevisionFact",
    "StandardizedSurprise",
)
