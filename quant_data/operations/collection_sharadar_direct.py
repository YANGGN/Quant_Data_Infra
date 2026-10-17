"""Bounded direct Sharadar acquisition using retained JSON evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from datetime import date
import time

from ..company.sharadar_direct import (DIMENSIONS, MAX_BYTES, _parameters,
    parse_definition_metadata, parse_definition_page, parse_metadata, parse_page,
    prepare_definition_snapshot, prepare_partition)
from ..company.sharadar_definition_repository import SharadarDefinitionPublisher
from ..company.sharadar_repository import SharadarSf1Publisher
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..ingestion import PublicationDeferred
from ..market.collection_universe import _utc
from .collection_plan import AcquisitionUnit, ProviderBudget, build_plan, digest, validate_budget, validate_unit
from .collection_queue import _clock, _read, _response, _utcnow, atomic, run_queue
from .collection_sec import requires_active_binding
from .collection_targets import validate_selection
from .equibles_transcript_backfill import job_lock, private_directory

MAX_PAGES = 100
MAX_ROWS = 100000
MAX_PARTITION_BYTES = 256 * 1024 * 1024
DESCRIPTION_PARAMETERS = {"tablename": "fundamentals", "format": "json", "limit": "1000", "sort": "indicator.asc"}


def metadata_unit(selection, *, mode, observation_window):
    """The single descriptions response used for both metadata and definitions."""
    return validate_unit(AcquisitionUnit("sharadar_fundamentals", "sharadar", "SHARADAR_DIRECT/descriptions", "fundamentals",
        tuple(sorted(DESCRIPTION_PARAMETERS.items())), mode, observation_window, (), selection.scope_sha256,
        max_response_bytes=MAX_BYTES, max_rows=1000, timeout_seconds=20))


def sf1_units(stores, selection, *, schema, mode, observation_window, cutoff, batch_size=25, lastupdated=None, date_to=None):
    validate_selection(stores, selection, cutoff=cutoff, collection="sharadar_fundamentals")
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise ResourceLimitError("direct SF1 ticker batch size must be between one and 100")
    if lastupdated is not None and (not isinstance(lastupdated, tuple) or len(lastupdated) != 2):
        raise ValidationError("direct SF1 incremental acquisition needs a bounded lastupdated date window")
    date_filters = {} if lastupdated is None else {"lastupdated.gte": lastupdated[0], "lastupdated.lte": lastupdated[1]}
    groups = {}
    for item in selection.eligible:
        groups.setdefault((item.provider_symbol, item.provider_subject), set()).add(item.source_symbol)
    by_ticker = {}
    for ticker, subject in groups:
        if ticker in by_ticker and by_ticker[ticker] != subject:
            raise ConflictError("direct SF1 selected ticker has conflicting permanent identities")
        by_ticker[ticker] = subject
    result = []
    items = sorted(groups)
    cutoff_date = _utc(cutoff)[:10]
    if date_to is not None:
        if not isinstance(date_to,str):raise ValidationError("direct SF1 end date must remain date-only")
        try:parsed=date.fromisoformat(date_to)
        except ValueError as error:raise ValidationError("direct SF1 end date is invalid") from error
        if parsed.isoformat()!=date_to or date_to>cutoff_date or date_to<"1900-01-01":
            raise ValidationError("direct SF1 end date is outside its capture window")
        cutoff_date=date_to
    batches, batch, ticker_length = [], [], 0
    for item in items:
        added_length = len(item[0]) + bool(batch)
        if batch and (len(batch) >= min(batch_size, 30) or ticker_length + added_length > 200):
            batches.append(batch)
            batch, ticker_length = [], 0
            added_length = len(item[0])
        batch.append(item)
        ticker_length += added_length
    if batch:
        batches.append(batch)
    for batch in batches:
        ticker = ",".join(sorted(key[0] for key in batch))
        subject = ",".join(sorted(key[1] for key in batch))
        symbols = tuple(sorted({symbol for key in batch for symbol in groups[key]}))
        for dimension in sorted(DIMENSIONS):
            params = _parameters({"ticker": ticker, "dimension": dimension, "format": "json", "sort": "date.asc",
                "from": "1900-01-01", "to": cutoff_date, "limit": "10000", "offset": "0", **date_filters}, schema)
            result.append(validate_unit(AcquisitionUnit("sharadar_fundamentals", "sharadar", "SHARADAR_DIRECT/fundamentals", subject,
                params, mode, observation_window, symbols, selection.scope_sha256,
                max_response_bytes=MAX_BYTES, max_rows=10000, timeout_seconds=30)))
    return tuple(result)


@dataclass(frozen=True)
class Sf1Progress:
    outcome: str
    metadata_unit: AcquisitionUnit
    next_unit: AcquisitionUnit | None
    partition: object | None
    page_units: tuple[AcquisitionUnit, ...]
    captured_bytes: int


def _validate_initial(stores, selection, unit, cutoff):
    validate_selection(stores, selection, cutoff=cutoff, collection="sharadar_fundamentals", require_active=requires_active_binding(stores))
    validate_unit(unit)
    params = dict(unit.parameters)
    if unit.endpoint != "SHARADAR_DIRECT/fundamentals" or params.get("offset") != "0":
        raise ValidationError("direct SF1 offset walk starts with one explicit first-page request")
    if "qopts.cursor_id" in params:
        raise ValidationError("direct SF1 does not accept legacy cursor parameters")
    build_plan(selections=(selection,), units=(unit,), budgets=(ProviderBudget("sharadar", 1, MAX_PARTITION_BYTES, 7200, 1000),), created_at=cutoff)


def _metadata(root, selection, unit, cutoff, recovery=None):
    meta = metadata_unit(selection, mode=unit.mode, observation_window=unit.observation_window)
    evidence = recovery.response(meta) if recovery else _response(root, meta.unit_id, meta)
    if evidence is None:
        return meta, None, None
    receipt, body = evidence
    if receipt["status"] != 200:
        return meta, receipt, None
    if _utc(receipt["captured_at"]) > _utc(cutoff):
        raise ConflictError("direct SF1 metadata is beyond cutoff")
    return recovery.unit(meta) if recovery else meta, receipt, body


def inspect_partition(*, root, stores, selection, initial_unit, cutoff, recovery=None):
    _validate_initial(stores, selection, initial_unit, cutoff)
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("direct SF1 private root must be explicit and resolved")
    meta, receipt, body = _metadata(root, selection, initial_unit, cutoff, recovery)
    if receipt is None:
        return Sf1Progress("metadata_required", meta, meta, None, (), 0)
    if body is None:
        return Sf1Progress("metadata_http_failure", meta, None, None, (), 0)
    schema = parse_metadata(body, captured_at=receipt["captured_at"], source_reference="collection/sharadar/blobs/" + receipt["content_sha256"] + ".json")
    _parameters(dict(initial_unit.parameters), schema)
    pages, units, received, unit = [], [], len(body), initial_unit
    for _ in range(MAX_PAGES):
        evidence = recovery.response(unit) if recovery else _response(root, unit.unit_id, unit)
        if evidence is None:
            return Sf1Progress("page_required", meta, unit, None, tuple(units), received)
        page_receipt, page_body = evidence
        if page_receipt["status"] != 200:
            return Sf1Progress("page_http_failure", meta, None, None, tuple(units), received + len(page_body))
        if _utc(page_receipt["captured_at"]) > _utc(cutoff):
            raise ConflictError("direct SF1 page is beyond cutoff")
        received += len(page_body)
        if received > MAX_PARTITION_BYTES:
            raise ResourceLimitError("direct SF1 metadata and pages exceed partition byte cap")
        page = parse_page(page_body, schema=schema, parameters=dict(unit.parameters), captured_at=page_receipt["captured_at"],
            source_reference="collection/sharadar/blobs/" + page_receipt["content_sha256"] + ".json")
        pages.append(page); units.append(recovery.unit(unit) if recovery else unit)
        partition = prepare_partition(pages, max_pages=MAX_PAGES, max_rows=MAX_ROWS, max_bytes=MAX_PARTITION_BYTES)
        if partition.transport_complete:
            return Sf1Progress("ready", meta, None, partition, tuple(units), received)
        if page.next_cursor is None:
            raise ConflictError("direct SF1 incomplete page lacks a next offset")
        unit = replace(initial_unit, parameters=tuple(sorted({**dict(unit.parameters), "offset": page.next_cursor}.items())))
    return Sf1Progress("page_cap_reached_incomplete", meta, None, None, tuple(units), received)


def publish_definitions(*, root, stores, registry, selection, mode, observation_window, cutoff, deadline=None, monotonic=time.monotonic, recovery=None):
    """Publish definitions from the exact retained descriptions response."""
    root = Path(root)
    meta = metadata_unit(selection, mode=mode, observation_window=observation_window)
    with job_lock(root):
        if deadline is not None and monotonic() >= deadline:
            return {"outcome": "invocation_budget", "written_count": 0}
        evidence = recovery.response(meta) if recovery else _response(root, meta.unit_id, meta)
        if evidence is None:
            return {"outcome": "metadata_required", "written_count": 0}
        receipt, body = evidence
        if receipt["status"] != 200:
            return {"outcome": "metadata_http_failure", "written_count": 0}
        if _utc(receipt["captured_at"]) > _utc(cutoff):
            raise ConflictError("direct definition metadata is beyond cutoff")
        reference = "collection/sharadar/blobs/" + receipt["content_sha256"] + ".json"
        schema = parse_definition_metadata(body, captured_at=receipt["captured_at"], source_reference=reference)
        page = parse_definition_page(body, schema=schema, parameters=dict(meta.parameters), captured_at=receipt["captured_at"], source_reference=reference)
        if page.next_cursor is not None or len(page.rows) >= 1000:
            raise ResourceLimitError("direct definitions response is not one bounded complete page")
        snapshot = prepare_definition_snapshot((page,))
        if deadline is not None and monotonic() >= deadline:
            return {"outcome": "invocation_budget", "written_count": 0}
        try:
            result = SharadarDefinitionPublisher(stores, registry).publish(snapshot, ingested_at=cutoff, deadline=deadline, monotonic=monotonic)
        except PublicationDeferred:
            if deadline is None or monotonic() < deadline:
                raise
            return {"outcome": "invocation_budget", "written_count": 0}
        return {"outcome": result.outcome, "written_count": result.written_count, "snapshot_id": result.snapshot_id,
            "pages": 1, "transport_complete": True, "metadata_unit_id": (recovery.unit(meta) if recovery else meta).unit_id}


def publish_partition(*, root, stores, registry, selection, initial_unit, cutoff, deadline=None, monotonic=time.monotonic, recovery=None):
    root = Path(root)
    with job_lock(root):
        if deadline is not None and monotonic() >= deadline:
            return {"outcome": "invocation_budget", "written_count": 0}
        progress = inspect_partition(root=root, stores=stores, selection=selection, initial_unit=initial_unit, cutoff=cutoff, recovery=recovery)
        if deadline is not None and monotonic() >= deadline:
            return {"outcome": "invocation_budget", "written_count": 0}
        if progress.outcome != "ready":
            return {"outcome": progress.outcome, "pages": len(progress.page_units), "written_count": 0,
                "next_unit": None if progress.next_unit is None else asdict(progress.next_unit)}
        try:
            result = SharadarSf1Publisher(stores, registry).publish(progress.partition, selection=selection,
                ingested_at=cutoff, deadline=deadline, monotonic=monotonic)
        except PublicationDeferred:
            if deadline is None or monotonic() < deadline:
                raise
            return {"outcome": "invocation_budget", "written_count": 0}
        receipt = {"outcome": result.outcome, "capture_id": result.snapshot_id, "written_count": result.written_count,
            "pages": len(progress.page_units), "transport_complete": True, "remote_coherence": "unproven",
            "selection_sha256": selection.scope_sha256, "acquisition_id": progress.partition.acquisition_id,
            "unit_ids": [unit.unit_id for unit in progress.page_units], "metadata_unit_id": progress.metadata_unit.unit_id}
        private_directory(root / "direct-sf1-publications")
        path = root / "direct-sf1-publications" / (digest({"acquisition": progress.partition.acquisition_id,
            "selection": selection.scope_sha256}) + ".json")
        if _read(path) is None:
            atomic(path, receipt)
        return receipt


def run_partition(*, root, stores, registry, selection, initial_unit, budget, fetch, retained=(), resolve_retained=None,
        secret_values=(), utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep,
        deadline=None, before_request=None, recovery=None):
    started = monotonic(); validate_budget(budget)
    hard_deadline = min(started + budget.max_run_seconds, deadline) if deadline is not None else started + budget.max_run_seconds
    if budget.provider != "sharadar" or budget.max_requests > MAX_PAGES or budget.max_total_bytes > MAX_PARTITION_BYTES:
        raise ResourceLimitError("direct SF1 partition requires at most 100 requests and 256 MiB")
    _validate_initial(stores, selection, initial_unit, _clock(utcnow))
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("direct SF1 private root must be explicit and resolved")
    private_directory(root); requests = received = 0
    with job_lock(root / "direct-sf1-controller"):
        for _ in range(MAX_PAGES + 1):
            progress = inspect_partition(root=root, stores=stores, selection=selection, initial_unit=initial_unit, cutoff=_clock(utcnow), recovery=recovery)
            if monotonic() >= hard_deadline:
                return {"outcome": "invocation_budget", "requests": requests, "received_bytes": received, "written_count": 0}
            if progress.outcome == "ready":
                result = publish_partition(root=root, stores=stores, registry=registry, selection=selection, initial_unit=initial_unit,
                    cutoff=_clock(utcnow), deadline=hard_deadline, monotonic=monotonic, recovery=recovery)
                return {**result, "requests": requests, "received_bytes": received}
            unit = progress.next_unit
            if unit is None:
                return {"outcome": progress.outcome, "requests": requests, "received_bytes": received}
            seconds = int(hard_deadline - monotonic())
            if (requests >= budget.max_requests or received + unit.max_response_bytes > budget.max_total_bytes
                    or progress.captured_bytes + unit.max_response_bytes > MAX_PARTITION_BYTES or seconds <= unit.timeout_seconds):
                return {"outcome": "invocation_budget", "requests": requests, "received_bytes": received,
                    "next_unit": asdict(unit), "pages": len(progress.page_units)}
            remaining = replace(budget, max_requests=budget.max_requests - requests, max_total_bytes=budget.max_total_bytes - received,
                max_run_seconds=seconds)
            plan = build_plan(selections=(selection,), units=(recovery.unit(unit) if recovery else unit,), budgets=(remaining,), retained=retained, created_at=_clock(utcnow))
            if before_request is not None and not before_request(unit=unit, deadline=hard_deadline):
                return {"outcome": "invocation_budget", "requests": requests, "received_bytes": received}
            report = run_queue(root=root, plan=plan, provider="sharadar", fetch=fetch, publish=None, acquire_only=True,
                resolve_retained=resolve_retained, secret_values=secret_values, utcnow=utcnow, monotonic=monotonic, sleeper=sleeper, deadline=hard_deadline)
            requests += report.get("requests", 0); received += report.get("received_bytes", 0)
            if recovery:
                recovery.response(unit)
            if report["outcome"] not in ("acquired", "acquired_with_gaps", "complete"):
                return {**report, "requests": requests, "received_bytes": received}
    raise ResourceLimitError("direct SF1 bounded controller did not terminate")

def run_definitions(*, root, stores, registry, selection, mode, observation_window, budget, fetch,
        retained=(), resolve_retained=None, secret_values=(), utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep,
        deadline=None, before_request=None, recovery=None):
    """Acquire and publish the one direct descriptions response exactly once."""
    started = monotonic()
    validate_budget(budget)
    hard_deadline = min(started + budget.max_run_seconds, deadline) if deadline is not None else started + budget.max_run_seconds
    if (budget.provider != "sharadar" or budget.max_requests > 1 or budget.max_total_bytes > 16 * 1024 * 1024 or budget.max_run_seconds > 30):
        raise ResourceLimitError("direct definitions require at most 1 request, 16 MiB and 30 seconds")
    validate_selection(stores, selection, cutoff=_clock(utcnow), collection="sharadar_fundamentals", require_active=requires_active_binding(stores))
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("direct definitions need an explicit resolved private root")
    private_directory(root)
    meta = metadata_unit(selection, mode=mode, observation_window=observation_window)
    evidence = recovery.response(meta) if recovery else _response(root, meta.unit_id, meta)
    requests = received = 0
    if evidence is None:
        plan = build_plan(selections=(selection,), units=(recovery.unit(meta) if recovery else meta,), budgets=(budget,), retained=retained, created_at=_clock(utcnow))
        if before_request is not None and not before_request(unit=meta, deadline=hard_deadline):
            return {"outcome": "invocation_budget", "requests": 0, "received_bytes": 0}
        result = run_queue(root=root, plan=plan, provider="sharadar", fetch=fetch, publish=None, acquire_only=True, resolve_retained=resolve_retained, secret_values=secret_values, utcnow=utcnow, monotonic=monotonic, sleeper=sleeper, deadline=hard_deadline)
        if recovery:
            recovery.response(meta)
        requests = result.get("requests", 0)
        received = result.get("received_bytes", 0)
        if result["outcome"] not in ("acquired", "acquired_with_gaps", "complete"):
            return {**result, "requests": requests, "received_bytes": received, "written_count": 0}
    result = publish_definitions(root=root, stores=stores, registry=registry, selection=selection, mode=mode, observation_window=observation_window, cutoff=_clock(utcnow), deadline=hard_deadline, monotonic=monotonic, recovery=recovery)
    return {**result, "requests": requests, "received_bytes": received}
