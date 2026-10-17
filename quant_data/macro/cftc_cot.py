"""Bounded CFTC Commitments of Traders parsing and publication.

The provider boundary is intentionally separate. This module receives complete
CFTC PRE JSON pages and publishes only source-level futures-only positions via
the existing macro evidence and immutable-version tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Final, Sequence

from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import (
    ArtifactWrite,
    IngestionCoordinator,
    QualityWrite,
    SnapshotWrite,
    WriteResult,
)
from ..json_codec import dumps_strict, ensure_number_within_limits, loads_strict
from ..registry import Registry
from ..stores import StoreMap, StoreRole, stable_id
from ..temporal import TemporalPrecision, TemporalValue, parse_date


PROVIDER: Final = "cftc"
TFF_FUTURES_ONLY: Final = "tff_futures_only"
DISAGGREGATED_FUTURES_ONLY: Final = "disaggregated_futures_only"
COLLECTOR_ID_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "cftc.macro.tff_futures_only_history",
    DISAGGREGATED_FUTURES_ONLY: "cftc.macro.disaggregated_futures_only_history",
}
HANDLER_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "macro.cftc_tff_futures_only_history",
    DISAGGREGATED_FUTURES_ONLY: "macro.cftc_disaggregated_futures_only_history",
}
OUTPUT_DATASET_IDS: Final = (
    "fixture.macro.rtdsm_employ_evidence",
    "fixture.macro.rtdsm_employ",
    "fixture.macro.stage3_catalog",
)
EVIDENCE_DATASET_ID: Final = OUTPUT_DATASET_IDS[0]
CANONICAL_DATASET_ID: Final = OUTPUT_DATASET_IDS[1]
NORMALIZATION_VERSION: Final = "cftc_cot_v1"
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_RESPONSE_ROWS: Final = 4_000
MAX_RESPONSE_PAGES: Final = 8
MAX_OBSERVATIONS: Final = 20_000
MAX_WINDOW_DAYS: Final = 21
SOURCE_REFERENCE_BY_FAMILY: Final = {
    TFF_FUTURES_ONLY: "publicreporting.cftc.gov/resource/gpe5-46if.json",
    DISAGGREGATED_FUTURES_ONLY: "publicreporting.cftc.gov/resource/72hh-3qpy.json",
}
_REPORT_DATE_TIMESTAMP: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T00:00:00(?:\.0{1,6})?$"
)
_CONTRACT_CODE: Final = re.compile(
    r"^(?:[A-Za-z0-9_-]{1,64}|[A-Za-z0-9_-]{1,63}\+)$"
)


@dataclass(frozen=True, slots=True)
class CftcPositionGroup:
    key: str
    long_field: str
    short_field: str
    spread_field: str | None = None


@dataclass(frozen=True, slots=True)
class CftcReportFamily:
    key: str
    endpoint: str
    source_reference: str
    groups: tuple[CftcPositionGroup, ...]


_TFF_GROUPS: Final = (
    CftcPositionGroup(
        "dealer",
        "dealer_positions_long_all",
        "dealer_positions_short_all",
        "dealer_positions_spread_all",
    ),
    CftcPositionGroup(
        "asset_manager",
        "asset_mgr_positions_long",
        "asset_mgr_positions_short",
        "asset_mgr_positions_spread",
    ),
    CftcPositionGroup(
        "leveraged_money",
        "lev_money_positions_long",
        "lev_money_positions_short",
        "lev_money_positions_spread",
    ),
    CftcPositionGroup(
        "other_reportables",
        "other_rept_positions_long",
        "other_rept_positions_short",
        "other_rept_positions_spread",
    ),
)
_DISAGGREGATED_GROUPS: Final = (
    CftcPositionGroup(
        "producer_merchant",
        "prod_merc_positions_long",
        "prod_merc_positions_short",
    ),
    CftcPositionGroup(
        "swap_dealers",
        "swap_positions_long_all",
        "swap__positions_short_all",
        "swap__positions_spread_all",
    ),
    CftcPositionGroup(
        "managed_money",
        "m_money_positions_long_all",
        "m_money_positions_short_all",
        "m_money_positions_spread",
    ),
    CftcPositionGroup(
        "other_reportables",
        "other_rept_positions_long",
        "other_rept_positions_short",
        "other_rept_positions_spread",
    ),
)
_FAMILY_BY_KEY: Final = {
    TFF_FUTURES_ONLY: CftcReportFamily(
        TFF_FUTURES_ONLY,
        "https://publicreporting.cftc.gov/resource/gpe5-46if.json",
        SOURCE_REFERENCE_BY_FAMILY[TFF_FUTURES_ONLY],
        _TFF_GROUPS,
    ),
    DISAGGREGATED_FUTURES_ONLY: CftcReportFamily(
        DISAGGREGATED_FUTURES_ONLY,
        "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",
        SOURCE_REFERENCE_BY_FAMILY[DISAGGREGATED_FUTURES_ONLY],
        _DISAGGREGATED_GROUPS,
    ),
}
_OPTIONAL_TOTAL_FIELDS: Final = (
    ("total_reportable", "long", "tot_rept_positions_long_all"),
    ("total_reportable", "short", "tot_rept_positions_short"),
    ("nonreportable", "long", "nonrept_positions_long_all"),
    ("nonreportable", "short", "nonrept_positions_short_all"),
)
_METADATA_FIELDS: Final = (
    "cftc_market_code",
    "cftc_region_code",
    "cftc_commodity_code",
    "market_and_exchange_names",
    "contract_market_name",
    "commodity_name",
    "commodity_group_name",
    "commodity_subgroup_name",
)


@dataclass(frozen=True, slots=True)
class CftcCotObservation:
    report_family: str
    report_date: str
    contract_code: str
    series_id: str
    value_text: str | None
    missing_reason: str | None
    dimensions: tuple[tuple[str, str], ...]
    source_row: int


@dataclass(frozen=True, slots=True)
class CftcCotCapture:
    response_parts: tuple[bytes, ...]
    response_sha256: str
    response_byte_count: int
    semantic_identity: str
    captured_at: str
    captured_precision: str
    request_start_date: str
    request_end_date: str
    report_family: str
    source_row_count: int
    observations: tuple[CftcCotObservation, ...]


@dataclass(frozen=True, slots=True)
class CftcCotPublishReport:
    outcome: str
    report_family: str
    semantic_identity: str
    run_id: str | None
    artifact_id: str | None
    snapshot_id: str | None
    written_series: int
    written_observation_versions: int



def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _family(value: object) -> CftcReportFamily:
    if not isinstance(value, str) or value not in _FAMILY_BY_KEY:
        raise _fail("CFTC COT report family is invalid")
    return _FAMILY_BY_KEY[value]


def _utc_capture(raw: str) -> str:
    parsed = TemporalValue.parse(raw, pointer="/captured_at")
    if (
        parsed.precision is not TemporalPrecision.DATETIME
        or not isinstance(parsed.value, datetime)
    ):
        raise _fail("CFTC COT capture time must be an aware datetime")
    return (
        parsed.value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _window(start_date: str, end_date: str) -> tuple[str, str, date, date]:
    start = parse_date(start_date, pointer="/start_date")
    end = parse_date(end_date, pointer="/end_date")
    if start > end or (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise _fail("CFTC COT request window is invalid")
    return start.isoformat(), end.isoformat(), start, end


def _report_date(value: object) -> date:
    if not isinstance(value, str):
        raise _fail("CFTC COT report date is invalid")
    date_text = value if len(value) == 10 else value[:10]
    if len(value) != 10 and _REPORT_DATE_TIMESTAMP.fullmatch(value) is None:
        raise _fail("CFTC COT report date is invalid")
    try:
        parsed = date.fromisoformat(date_text)
    except ValueError as exc:
        raise _fail("CFTC COT report date is invalid") from exc
    if parsed.weekday() != 1:
        raise _fail("CFTC COT report date must be a Tuesday")
    return parsed


def _text(value: object, *, label: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise _fail(f"CFTC COT {label} is required")
        return None
    if not isinstance(value, str):
        raise _fail(f"CFTC COT {label} must be text")
    result = value.strip()
    if not result:
        if required:
            raise _fail(f"CFTC COT {label} is required")
        return None
    if len(result) > 512:
        raise _fail(f"CFTC COT {label} is too long")
    return result


def _contract_code(value: object) -> str:
    result = _text(value, label="contract code", required=True)
    if (
        result is None
        or value != result
        or _CONTRACT_CODE.fullmatch(result) is None
    ):
        raise _fail("CFTC COT contract code is invalid")
    return result


def _futures_only(row: dict[str, object]) -> None:
    value = _text(
        row.get("futonly_or_combined"),
        label="futures-only scope",
        required=True,
    )
    if value is None:
        raise _fail("CFTC COT futures-only scope is invalid")
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    if normalized not in {"futonly", "futuresonly"}:
        raise _fail("CFTC COT source is not futures-only")


def _position_value(value: object, *, field: str) -> tuple[str | None, str | None]:
    if value is None:
        return None, "source_null"
    if isinstance(value, bool) or isinstance(value, float):
        raise _fail(f"CFTC COT {field} must be a nonnegative integer or null")
    if isinstance(value, str):
        if not value or value != value.strip():
            raise _fail(f"CFTC COT {field} must be a nonnegative integer or null")
        candidate: object = value
    elif isinstance(value, (int, Decimal)):
        candidate = value
    else:
        raise _fail(f"CFTC COT {field} must be a nonnegative integer or null")
    try:
        parsed = Decimal(candidate)
    except (InvalidOperation, ValueError) as exc:
        raise _fail(f"CFTC COT {field} must be a nonnegative integer or null") from exc
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        raise _fail(f"CFTC COT {field} must be a nonnegative integer or null")
    ensure_number_within_limits(parsed)
    return str(int(parsed)), None


def _contract_dimensions(
    row: dict[str, object],
    *,
    family: CftcReportFamily,
    contract_code: str,
) -> dict[str, str]:
    contract_units = _text(row.get("contract_units"), label="contract units", required=True)
    if contract_units is None:
        raise _fail("CFTC COT contract units are required")
    result = {
        "cftc_contract_market_code": contract_code,
        "contract_units": contract_units,
        "report_family": family.key,
        "source_futures_scope": "futures_only",
    }
    for field in _METADATA_FIELDS:
        value = _text(row.get(field), label=field)
        if value is not None:
            result[field] = value
    return result


def _dimensions(
    base: dict[str, str],
    *,
    measure: str,
    participant_group: str,
    position_side: str,
) -> tuple[tuple[str, str], ...]:
    result = dict(base)
    result.update(
        {
            "measure": measure,
            "participant_group": participant_group,
            "position_side": position_side,
        }
    )
    return tuple(sorted(result.items()))


def _series_id(family: str, contract_code: str) -> str:
    return f"macro.cftc.cot.{family}.{contract_code.casefold()}"


def _page_digest(parts: tuple[bytes, ...]) -> str:
    material = [
        {"byte_count": len(item), "sha256": _sha256_bytes(item)}
        for item in parts
    ]
    return _sha256_text(dumps_strict(material))



def parse_cftc_cot(
    response_parts: Sequence[bytes],
    *,
    report_family: str,
    captured_at: str,
    start_date: str,
    end_date: str,
) -> CftcCotCapture:
    """Normalize complete bounded CFTC PRE pages without opening a store."""

    family = _family(report_family)
    if isinstance(response_parts, (bytes, bytearray, str)):
        raise _fail("CFTC COT response pages are invalid")
    parts = tuple(response_parts)
    if not parts or len(parts) > MAX_RESPONSE_PAGES:
        raise _fail("CFTC COT response page count is invalid")
    if any(not isinstance(item, bytes) or not item for item in parts):
        raise _fail("CFTC COT response pages must contain JSON bytes")
    byte_count = sum(len(item) for item in parts)
    if byte_count > MAX_RESPONSE_BYTES:
        raise ResourceLimitError("CFTC COT response exceeds its byte bound")
    start_text, end_text, start, end = _window(start_date, end_date)
    captured = _utc_capture(captured_at)

    rows: list[dict[str, object]] = []
    for part in parts:
        page = loads_strict(part, max_bytes=MAX_RESPONSE_BYTES)
        if not isinstance(page, list):
            raise _fail("CFTC COT response page must be a JSON array")
        if len(rows) + len(page) > MAX_RESPONSE_ROWS:
            raise ResourceLimitError("CFTC COT response exceeds its row bound")
        for row in page:
            if not isinstance(row, dict):
                raise _fail("CFTC COT source row must be an object")
            rows.append(dict(row))
    if not rows:
        raise _fail("CFTC COT response is empty")

    normalized: list[
        tuple[
            str,
            str,
            str | None,
            str | None,
            tuple[tuple[str, str], ...],
        ]
    ] = []
    seen_contract_reports: set[tuple[str, str]] = set()
    for row in rows:
        report_date = _report_date(row.get("report_date_as_yyyy_mm_dd"))
        if report_date < start or report_date > end:
            raise _fail("CFTC COT source row is outside the requested window")
        _futures_only(row)
        contract_code = _contract_code(row.get("cftc_contract_market_code"))
        report_date_text = report_date.isoformat()
        contract_identity = (report_date_text, contract_code)
        if contract_identity in seen_contract_reports:
            raise _fail("CFTC COT report date and contract code must be unique")
        seen_contract_reports.add(contract_identity)
        base = _contract_dimensions(row, family=family, contract_code=contract_code)

        def add(
            raw_value: object,
            *,
            field: str,
            measure: str,
            group: str,
            side: str,
        ) -> None:
            value_text, missing_reason = _position_value(raw_value, field=field)
            normalized.append(
                (
                    report_date_text,
                    contract_code,
                    value_text,
                    missing_reason,
                    _dimensions(
                        base,
                        measure=measure,
                        participant_group=group,
                        position_side=side,
                    ),
                )
            )

        if "open_interest_all" not in row:
            raise _fail("CFTC COT open_interest_all is required")
        add(
            row["open_interest_all"],
            field="open_interest_all",
            measure="open_interest",
            group="all",
            side="all",
        )
        for group in family.groups:
            for side, field in (("long", group.long_field), ("short", group.short_field)):
                if field not in row:
                    raise _fail(f"CFTC COT {field} is required")
                add(
                    row[field],
                    field=field,
                    measure="position",
                    group=group.key,
                    side=side,
                )
            if group.spread_field is not None and group.spread_field in row:
                add(
                    row[group.spread_field],
                    field=group.spread_field,
                    measure="position",
                    group=group.key,
                    side="spreading",
                )
        for group, side, field in _OPTIONAL_TOTAL_FIELDS:
            if field in row:
                add(
                    row[field],
                    field=field,
                    measure="position",
                    group=group,
                    side=side,
                )

    if len(normalized) > MAX_OBSERVATIONS:
        raise ResourceLimitError("CFTC COT normalized result exceeds its row bound")
    normalized.sort(
        key=lambda item: (
            item[0],
            item[1],
            dict(item[4])["measure"],
            dict(item[4])["participant_group"],
            dict(item[4])["position_side"],
        )
    )
    observations = tuple(
        CftcCotObservation(
            report_family=family.key,
            report_date=report_date,
            contract_code=contract_code,
            series_id=_series_id(family.key, contract_code),
            value_text=value_text,
            missing_reason=missing_reason,
            dimensions=dimensions,
            source_row=ordinal,
        )
        for ordinal, (
            report_date,
            contract_code,
            value_text,
            missing_reason,
            dimensions,
        ) in enumerate(normalized, start=1)
    )


    semantic_material = {
        "normalization_version": NORMALIZATION_VERSION,
        "provider": PROVIDER,
        "resource": family.source_reference,
        "report_family": family.key,
        "request_scope": {"from": start_text, "to": end_text},
        "selected_observations": [
            {
                "report_date": item.report_date,
                "contract_code": item.contract_code,
                "value": item.value_text,
                "missing_reason": item.missing_reason,
                "dimensions": dict(item.dimensions),
            }
            for item in observations
        ],
    }
    return CftcCotCapture(
        response_parts=parts,
        response_sha256=_page_digest(parts),
        response_byte_count=byte_count,
        semantic_identity=_sha256_text(dumps_strict(semantic_material)),
        captured_at=captured,
        captured_precision="datetime",
        request_start_date=start_text,
        request_end_date=end_text,
        report_family=family.key,
        source_row_count=len(rows),
        observations=observations,
    )


def _store_map(project_root: Path, macro_store: Path) -> StoreMap:
    data = project_root / "data"
    return StoreMap.four_explicit(
        market=data / "market.sqlite",
        macro=macro_store,
        company=data / "company.sqlite",
        news=data / "news.sqlite",
    )


def _registry_binding(registry: object, family: CftcReportFamily) -> Registry:
    if not isinstance(registry, Registry):
        raise _fail("CFTC COT publisher requires the reviewed registry")
    collector_id = COLLECTOR_ID_BY_FAMILY[family.key]
    collectors = [
        item for item in registry.collectors if str(item.get("id")) == collector_id
    ]
    datasets = {item.id: item for item in registry.datasets}
    if len(collectors) != 1:
        raise _fail("CFTC COT registry collector binding is invalid")
    collector = collectors[0]
    if (
        collector.get("handler") != HANDLER_BY_FAMILY[family.key]
        or collector.get("network") is not True
        or collector.get("version") != "1.0.0"
        or collector.get("output_datasets") != list(OUTPUT_DATASET_IDS)
        or collector.get("configuration_env") != []
        or collector.get("schedule_eligibility") != {"mode": "manual_only"}
    ):
        raise _fail("CFTC COT registry collector binding is invalid")
    for dataset_id in OUTPUT_DATASET_IDS:
        dataset = datasets.get(dataset_id)
        if (
            dataset is None
            or dataset.store != "macro"
            or not dataset.active
            or collector_id not in dataset.collector_ids
        ):
            raise _fail("CFTC COT registry dataset binding is invalid")
    return registry


def _capture_binding(capture: object) -> CftcCotCapture:
    if not isinstance(capture, CftcCotCapture):
        raise _fail("CFTC COT capture binding is invalid")
    reparsed = parse_cftc_cot(
        capture.response_parts,
        report_family=capture.report_family,
        captured_at=capture.captured_at,
        start_date=capture.request_start_date,
        end_date=capture.request_end_date,
    )
    if reparsed != capture:
        raise _fail("CFTC COT capture binding is invalid")
    return capture


class CftcCotPublisher:
    """Publish one CFTC COT family batch to the bound macro store."""

    def __init__(
        self,
        *,
        macro_store: Path,
        project_root: Path,
        registry: object,
        _canonical: bool = False,
    ) -> None:
        if not isinstance(project_root, Path) or not isinstance(macro_store, Path):
            raise _fail("CFTC COT publisher paths are invalid")
        try:
            root = project_root.resolve(strict=True)
            store = macro_store.resolve(strict=True)
            root_info = project_root.lstat()
            store_info = macro_store.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _fail("CFTC COT publisher target is unavailable") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or not stat.S_ISREG(store_info.st_mode)
            or project_root.is_symlink()
            or macro_store.is_symlink()
            or store_info.st_nlink != 1
            or store != root / "data" / "macro.sqlite"
        ):
            raise _fail("CFTC COT publisher target binding is invalid")
        if not _canonical:
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            try:
                root.relative_to(temporary)
            except ValueError as exc:
                raise _fail("CFTC COT fixture root must be temporary") from exc
            if root == temporary:
                raise _fail("CFTC COT fixture root is too broad")
        self._registry = registry
        self._root = root
        self._store = store
        self._stores = _store_map(root, store)
        self._coordinator = IngestionCoordinator(
            self._stores,
            code_version=NORMALIZATION_VERSION,
        )

    def publish(self, capture: CftcCotCapture) -> CftcCotPublishReport:
        prepared = _capture_binding(capture)
        family = _family(prepared.report_family)
        _registry_binding(self._registry, family)
        collector_id = COLLECTOR_ID_BY_FAMILY[family.key]
        run_id = stable_id("cftc_cot_run", prepared.semantic_identity)
        scope = {
            "provider": PROVIDER,
            "resource": family.source_reference,
            "report_family": family.key,
            "from": prepared.request_start_date,
            "to": prepared.request_end_date,
            "completeness": "complete",
            "tombstone_authoritative": False,
        }
        counts = {"series": 0, "observation_versions": 0}

        def writer(connection: sqlite3.Connection, active_run_id: str) -> WriteResult:
            return self._write_candidate(
                connection,
                prepared,
                family,
                active_run_id,
                scope,
                counts,
            )

        receipt = self._coordinator.execute(
            role=StoreRole.MACRO,
            dataset_id=CANONICAL_DATASET_ID,
            output_dataset_ids=OUTPUT_DATASET_IDS,
            semantic_identity=prepared.semantic_identity,
            run_id=run_id,
            command=collector_id,
            scope=scope,
            started_at=prepared.captured_at,
            completed_at=prepared.captured_at,
            fetched_count=prepared.source_row_count,
            writer=writer,
        )
        if receipt.outcome == "unchanged":
            outcome = "unchanged"
        elif receipt.outcome == "succeeded":
            outcome = "published"
        else:
            raise ConflictError("CFTC COT publisher returned an invalid outcome")
        return CftcCotPublishReport(
            outcome=outcome,
            report_family=family.key,
            semantic_identity=prepared.semantic_identity,
            run_id=receipt.run_id,
            artifact_id=receipt.artifact_id,
            snapshot_id=receipt.snapshot_id,
            written_series=counts["series"],
            written_observation_versions=counts["observation_versions"],
        )



    @staticmethod
    def _write_candidate(
        connection: sqlite3.Connection,
        capture: CftcCotCapture,
        family: CftcReportFamily,
        run_id: str,
        scope: dict[str, object],
        counts: dict[str, int],
    ) -> WriteResult:
        artifact_id = stable_id(
            "cftc_cot_artifact",
            capture.semantic_identity,
            capture.response_sha256,
        )
        snapshot_id = stable_id(
            "cftc_cot_snapshot",
            CANONICAL_DATASET_ID,
            capture.semantic_identity,
        )
        for contract_code in sorted({item.contract_code for item in capture.observations}):
            counts["series"] += int(
                _ensure_series(connection, family, contract_code, run_id)
            )
        connection.execute(
            """
            INSERT INTO macro_source_artifacts (
                artifact_id, dataset_id, sha256, media_type, byte_count,
                request_scope_json, captured_at, captured_precision,
                source_resource, normalization_version, run_id
            ) VALUES (?, ?, ?, 'application/vnd.cftc.pre-pages+json', ?, ?, ?,
                      'datetime', ?, ?, ?)
            """,
            (
                artifact_id,
                EVIDENCE_DATASET_ID,
                capture.response_sha256,
                capture.response_byte_count,
                dumps_strict(scope),
                capture.captured_at,
                family.source_reference,
                NORMALIZATION_VERSION,
                run_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO macro_source_snapshots (
                snapshot_id, series_id, release_id, artifact_id,
                semantic_identity, completeness, row_count, validation_state,
                warnings_json, run_id, quality_flags_json
            ) VALUES (?, NULL, NULL, ?, ?, 'complete', ?, 'validated', '[]',
                      ?, '[]')
            """,
            (
                snapshot_id,
                artifact_id,
                capture.semantic_identity,
                len(capture.observations),
                run_id,
            ),
        )
        scope_json = dumps_strict(scope)
        scope_digest = _sha256_text(scope_json)
        connection.execute(
            """
            INSERT INTO macro_snapshot_scopes (
                scope_id, snapshot_id, scope_json, scope_digest, completeness,
                tombstone_authoritative
            ) VALUES (?, ?, ?, ?, 'complete', 0)
            """,
            (
                stable_id("cftc_cot_scope", snapshot_id, scope_digest),
                snapshot_id,
                scope_json,
                scope_digest,
            ),
        )

        release_ids: dict[tuple[str, str], str] = {}
        for observation in capture.observations:
            release_key = (observation.series_id, observation.report_date)
            if release_key not in release_ids:
                release_ids[release_key] = _ensure_release(
                    connection,
                    observation,
                    capture.captured_at,
                    run_id,
                )
            _ensure_dimensions(
                connection,
                observation,
                capture.captured_at,
                snapshot_id,
                run_id,
            )
            version_id, appended = _append_or_reuse_observation(
                connection,
                observation,
                release_ids[release_key],
                artifact_id,
                snapshot_id,
                capture.captured_at,
                run_id,
            )
            counts["observation_versions"] += int(appended)
            connection.execute(
                """
                INSERT INTO macro_snapshot_observation_membership (
                    snapshot_id, version_id, source_row
                ) VALUES (?, ?, ?)
                """,
                (snapshot_id, version_id, observation.source_row),
            )

        artifact = ArtifactWrite(
            artifact_id=artifact_id,
            dataset_id=EVIDENCE_DATASET_ID,
            content_sha256=capture.response_sha256,
            media_type="application/vnd.cftc.pre-pages+json",
            byte_count=capture.response_byte_count,
            source_reference=family.source_reference,
            request_scope=scope,
            captured_at=capture.captured_at,
            captured_precision="datetime",
            normalization_version=NORMALIZATION_VERSION,
        )
        return WriteResult(
            written_count=counts["series"] + counts["observation_versions"],
            artifacts=(artifact,),
            snapshot=SnapshotWrite(
                snapshot_id=snapshot_id,
                dataset_id=CANONICAL_DATASET_ID,
                semantic_identity=capture.semantic_identity,
                scope=scope,
                completeness="complete",
                row_count=len(capture.observations),
                captured_at=capture.captured_at,
                captured_precision="datetime",
                validation_state="validated",
                artifact_ids=(artifact_id,),
            ),
            quality_results=(
                QualityWrite(
                    quality_result_id=stable_id(
                        "cftc_cot_quality",
                        snapshot_id,
                        "selected_positions",
                    ),
                    dataset_id=CANONICAL_DATASET_ID,
                    rule_id="cftc_cot.selected_positions",
                    rule_version="1.0.0",
                    severity="informational",
                    outcome="passed",
                    subject_kind="snapshot",
                    subject_id=snapshot_id,
                    artifact_id=artifact_id,
                    snapshot_id=snapshot_id,
                    observed={
                        "report_family": family.key,
                        "source_row_count": capture.source_row_count,
                        "observation_count": len(capture.observations),
                    },
                ),
            ),
        )



def _series_metadata(
    family: CftcReportFamily,
    contract_code: str,
) -> tuple[object, ...]:
    return (
        PROVIDER,
        f"{family.key}:{contract_code}",
        f"CFTC COT {family.key} contract {contract_code}",
        "weekly",
        "contracts",
        "count",
        "1",
        dumps_strict(
            {
                "cftc_contract_market_code": contract_code,
                "report_family": family.key,
            }
        ),
        dumps_strict(["latest", "as_of"]),
        "local_capture",
    )


def _ensure_series(
    connection: sqlite3.Connection,
    family: CftcReportFamily,
    contract_code: str,
    run_id: str,
) -> bool:
    series_id = _series_id(family.key, contract_code)
    expected = _series_metadata(family, contract_code)
    existing = connection.execute(
        """
        SELECT provider, provider_series_code, title, frequency, unit,
               value_representation, scale, dimensions_json,
               supported_modes_json, availability_basis
        FROM macro_series
        WHERE series_id=?
        """,
        (series_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO macro_series (
                series_id, provider, provider_series_code, title, frequency,
                unit, value_representation, scale, dimensions_json,
                supported_modes_json, availability_basis, created_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (series_id, *expected, run_id),
        )
        return True
    if tuple(existing) != expected:
        raise _fail("CFTC COT series conflicts with immutable catalog metadata")
    return False


def _source_vintage_identity(observation: CftcCotObservation) -> str:
    return (
        f"cftc:{observation.report_family}:{observation.contract_code}:"
        f"{observation.report_date}:current_state"
    )


def _ensure_release(
    connection: sqlite3.Connection,
    observation: CftcCotObservation,
    captured_at: str,
    run_id: str,
) -> str:
    source_vintage_identity = _source_vintage_identity(observation)
    expected = (
        observation.report_date,
        "date",
        observation.report_date,
        0,
        None,
        "current_state",
        None,
        "unknown",
    )
    existing = connection.execute(
        """
        SELECT release_id, vintage_at, vintage_precision, source_release_order,
               is_first_release, first_release_evidence, release_stage,
               source_published_at, source_published_precision
        FROM macro_releases
        WHERE series_id=? AND source_vintage_identity=?
        """,
        (observation.series_id, source_vintage_identity),
    ).fetchone()
    if existing is not None:
        if tuple(existing)[1:] != expected:
            raise _fail("CFTC COT release conflicts with immutable metadata")
        return str(existing["release_id"])
    release_id = stable_id(
        "cftc_cot_release",
        observation.series_id,
        source_vintage_identity,
    )
    connection.execute(
        """
        INSERT INTO macro_releases (
            release_id, series_id, source_vintage_identity, vintage_at,
            vintage_precision, source_release_order, available_at,
            available_precision, is_first_release, first_release_evidence,
            first_seen_run_id, release_stage, source_published_at,
            source_published_precision
        ) VALUES (?, ?, ?, ?, 'date', ?, ?, 'datetime', 0, NULL, ?,
                  'current_state', NULL, 'unknown')
        """,
        (
            release_id,
            observation.series_id,
            source_vintage_identity,
            observation.report_date,
            observation.report_date,
            captured_at,
            run_id,
        ),
    )
    return release_id


def _ensure_dimensions(
    connection: sqlite3.Connection,
    observation: CftcCotObservation,
    captured_at: str,
    snapshot_id: str,
    run_id: str,
) -> None:
    for ordinal, (key, value) in enumerate(observation.dimensions):
        existing = connection.execute(
            """
            SELECT dimension_id
            FROM macro_series_dimensions
            WHERE series_id=? AND dimension_key=? AND dimension_value=?
            """,
            (observation.series_id, key, value),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO macro_series_dimensions (
                dimension_id, series_id, dimension_key, dimension_value, ordinal,
                metadata_json, available_at, available_precision,
                source_snapshot_id, run_id
            ) VALUES (?, ?, ?, ?, ?, '{}', ?, 'datetime', ?, ?)
            """,
            (
                stable_id("cftc_cot_dimension", observation.series_id, key, value),
                observation.series_id,
                key,
                value,
                ordinal,
                captured_at,
                snapshot_id,
                run_id,
            ),
        )


def _stored_value_matches(stored: object, value_text: str | None) -> bool:
    if stored is None:
        return value_text is None
    if value_text is None:
        return False
    try:
        parsed = Decimal(str(stored))
    except (InvalidOperation, ValueError) as exc:
        raise ConflictError("Stored CFTC COT value is invalid") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ConflictError("Stored CFTC COT value is invalid")
    return str(int(parsed)) == value_text



def _append_or_reuse_observation(
    connection: sqlite3.Connection,
    observation: CftcCotObservation,
    release_id: str,
    artifact_id: str,
    snapshot_id: str,
    captured_at: str,
    run_id: str,
) -> tuple[str, bool]:
    dimensions_json = dumps_strict(dict(observation.dimensions))
    dimensions_digest = _sha256_text(dimensions_json)
    source_vintage_identity = _source_vintage_identity(observation)
    existing = connection.execute(
        """
        SELECT version_id, correction_sequence, value_text, missing_reason,
               available_at, state
        FROM macro_observation_versions
        WHERE series_id=? AND period_start=? AND period_end=?
          AND dimensions_digest=? AND source_vintage_identity=?
        ORDER BY correction_sequence DESC
        LIMIT 1
        """,
        (
            observation.series_id,
            observation.report_date,
            observation.report_date,
            dimensions_digest,
            source_vintage_identity,
        ),
    ).fetchone()
    if existing is not None and (
        _stored_value_matches(existing["value_text"], observation.value_text)
        and existing["missing_reason"] == observation.missing_reason
        and existing["state"] == "active"
    ):
        return str(existing["version_id"]), False
    if existing is not None and str(existing["available_at"]) > captured_at:
        raise _fail("CFTC COT correction capture precedes stored evidence")
    correction_sequence = 1 if existing is None else int(existing["correction_sequence"]) + 1
    supersedes = None if existing is None else str(existing["version_id"])
    available_at, available_precision = captured_at, "datetime"
    version_material = {
        "series_id": observation.series_id,
        "report_date": observation.report_date,
        "source_vintage_identity": source_vintage_identity,
        "correction_sequence": correction_sequence,
        "value": observation.value_text,
        "missing_reason": observation.missing_reason,
        "dimensions": dict(observation.dimensions),
        "dimensions_digest": dimensions_digest,
        "available_at": available_at,
        "available_precision": available_precision,
    }
    version_id = stable_id(
        "cftc_cot_observation_version",
        observation.series_id,
        observation.report_date,
        str(correction_sequence),
        _sha256_text(dumps_strict(version_material)),
    )
    connection.execute(
        """
        INSERT INTO macro_observation_versions (
            version_id, series_id, period_start, period_end, dimensions_json,
            dimensions_digest, release_id, source_vintage_identity,
            correction_sequence, value_text, missing_reason, unit,
            value_representation, scale, available_at, available_precision,
            captured_at, captured_precision, supersedes_version_id, artifact_id,
            snapshot_id, run_id, source_row, state, is_preliminary,
            quality_flags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'contracts', 'count', '1',
                  ?, ?, ?, 'datetime', ?, ?, ?, ?, ?, 'active', 0, '[]')
        """,
        (
            version_id,
            observation.series_id,
            observation.report_date,
            observation.report_date,
            dimensions_json,
            dimensions_digest,
            release_id,
            source_vintage_identity,
            correction_sequence,
            observation.value_text,
            observation.missing_reason,
            available_at,
            available_precision,
            captured_at,
            supersedes,
            artifact_id,
            snapshot_id,
            run_id,
            observation.source_row,
        ),
    )
    current = connection.execute(
        """
        SELECT current.current_version_id, version.available_at,
               version.correction_sequence
        FROM macro_observations AS current
        JOIN macro_observation_versions AS version
          ON version.version_id=current.current_version_id
        WHERE current.series_id=? AND current.period_start=?
          AND current.period_end=? AND current.dimensions_digest=?
        """,
        (
            observation.series_id,
            observation.report_date,
            observation.report_date,
            dimensions_digest,
        ),
    ).fetchone()
    if current is None:
        connection.execute(
            """
            INSERT INTO macro_observations (
                series_id, period_start, period_end, dimensions_digest,
                current_version_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                observation.series_id,
                observation.report_date,
                observation.report_date,
                dimensions_digest,
                version_id,
            ),
        )
    elif (available_at, correction_sequence) >= (
        str(current["available_at"]),
        int(current["correction_sequence"]),
    ):
        connection.execute(
            """
            UPDATE macro_observations
            SET current_version_id=?
            WHERE series_id=? AND period_start=? AND period_end=?
              AND dimensions_digest=?
            """,
            (
                version_id,
                observation.series_id,
                observation.report_date,
                observation.report_date,
                dimensions_digest,
            ),
        )
    return version_id, True


__all__ = (
    "CANONICAL_DATASET_ID",
    "COLLECTOR_ID_BY_FAMILY",
    "CftcCotCapture",
    "CftcCotObservation",
    "CftcCotPublishReport",
    "CftcCotPublisher",
    "CftcPositionGroup",
    "CftcReportFamily",
    "DISAGGREGATED_FUTURES_ONLY",
    "EVIDENCE_DATASET_ID",
    "HANDLER_BY_FAMILY",
    "MAX_OBSERVATIONS",
    "MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_PAGES",
    "MAX_RESPONSE_ROWS",
    "MAX_WINDOW_DAYS",
    "NORMALIZATION_VERSION",
    "OUTPUT_DATASET_IDS",
    "PROVIDER",
    "SOURCE_REFERENCE_BY_FAMILY",
    "TFF_FUTURES_ONLY",
    "parse_cftc_cot",
)
