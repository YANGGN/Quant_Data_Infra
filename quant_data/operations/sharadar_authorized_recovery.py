"""One-use, explicitly authorized reconciliation of an unknown Sharadar attempt.

No CLI or scheduler calls this entry point. An operator must supply a reviewed
approval pinned to the exact ledger/checkpoint preimages. A durable claim makes
all second invocations fail closed, including after a crash; it is not a general
transport retry policy. The regular refresh keeps its existing stop behavior.
"""
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
import hashlib
from pathlib import Path
import re
import time

from ..company.sharadar_direct import parse_metadata
from ..errors import ConflictError, ResourceLimitError, ValidationError
from ..market.collection_universe import _utc
from .collection_plan import AcquisitionUnit, digest, validate_unit, validate_budget
from .collection_queue import (_clock, _read, _response, _save, _state, _utcnow,
                               atomic, STATE_BOUND)
from .collection_sharadar_direct import metadata_unit, sf1_units
from .equibles_transcript_backfill import job_lock, private_directory, read_file
from .sharadar_recovery import SharadarRecovery, instant
from .sharadar_selected_refresh import BUDGET, _state as refresh_state, _run_refresh_locked


@dataclass(frozen=True)
class RecoveryApproval:
    approval_id: str
    authorized_at: str
    decision: str
    ledger_sha256: str
    checkpoint_sha256: str
    pending_unit_id: str
    window: str
    from_date: str
    to_date: str
    completed_partitions: int
    total_partitions: int


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _preflight(root, stores, selection, approval, now):
    raw = read_file(root / "ledger.json", STATE_BOUND)
    checkpoint = read_file(root / "refresh-state.json", STATE_BOUND)
    if (_sha(raw) != approval.ledger_sha256
            or _sha(checkpoint) != approval.checkpoint_sha256):
        raise ConflictError("Sharadar recovery preimages changed")
    state = _state(root, "sharadar")
    saved = refresh_state(root, stores, selection, now)
    pending = saved["pending"]
    if (not isinstance(pending, dict) or pending.get("window") != approval.window
            or pending.get("from_date") != approval.from_date
            or pending.get("to_date") != approval.to_date
            or pending.get("definitions_complete") is not True
            or len(pending.get("completed_units", [])) != approval.completed_partitions):
        raise ConflictError("Sharadar recovery fixed window differs")
    reservation = state["pending"]
    if not isinstance(reservation, dict) or reservation.get("unit_id") != approval.pending_unit_id:
        raise ConflictError("Sharadar recovery pending attempt differs")
    unit_raw = reservation["unit"]
    unit = validate_unit(AcquisitionUnit(**{**unit_raw,
        "parameters": tuple(tuple(x) for x in unit_raw["parameters"]),
        "selected_symbols": tuple(unit_raw["selected_symbols"])}))
    if (unit.unit_id != approval.pending_unit_id or unit.provider != "sharadar"
            or unit.endpoint != "SHARADAR_DIRECT/fundamentals"
            or unit.observation_window != approval.window
            or unit.request_id != reservation["request_id"]
            or digest(asdict(unit)) != digest(unit_raw)):
        raise ConflictError("Sharadar recovery exact request differs")
    bucket = state["usage"].get(reservation["day"])
    if (not isinstance(bucket, dict) or type(reservation["attempt"]) is not int
            or reservation["attempt"] < 1
            or bucket.get("charged_attempts") != reservation["attempt"]
            or _utc(reservation["charged_at"])[:10] != reservation["day"]
            or state["last_request_at"] != reservation["charged_at"]
            or state["units"].get(unit.unit_id) != {"status": "failed", "request_id": unit.request_id}
            or state["provider_backoff"] is not None):
        raise ConflictError("Sharadar recovery original accounting differs")
    attempt = root / "attempts" / (reservation["day"] + "-" + str(reservation["attempt"]) + ".json")
    if _read(attempt) != reservation:
        raise ConflictError("Sharadar recovery original attempt evidence differs")
    if (_response(root, unit.unit_id, unit) is not None
            or (root / "failures" / (unit.unit_id + ".json")).exists()):
        raise ConflictError("Sharadar recovery outcome is already recorded")
    meta = metadata_unit(selection, mode="incremental", observation_window=approval.window)
    evidence = _response(root, meta.unit_id, meta)
    if evidence is None or evidence[0]["status"] != 200:
        raise ConflictError("Sharadar recovery requires retained successful metadata")
    receipt, body = evidence
    schema = parse_metadata(body, captured_at=receipt["captured_at"],
        source_reference="collection/sharadar/blobs/" + receipt["content_sha256"] + ".json")
    units = sf1_units(stores, selection, schema=schema, mode="incremental",
        observation_window=approval.window, cutoff=now, date_to=approval.to_date,
        batch_size=100, lastupdated=(approval.from_date, approval.to_date))
    completed = set(pending["completed_units"])
    remaining = [u for u in units if u.unit_id not in completed]
    if (len(units) != approval.total_partitions or len(completed) != approval.completed_partitions
            or not completed <= {u.unit_id for u in units} or not remaining
            or asdict(remaining[0]) != asdict(unit)):
        raise ConflictError("Sharadar recovery partition manifest differs")
    return state, raw, checkpoint, unit, reservation


def run_authorized_recovery(*, root, stores, registry, selection, fetch_factory,
        approval, budget=BUDGET, utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep):
    """Consume one approval; never renew its limits or retry an unknown outcome."""
    started = monotonic()
    deadline = started + budget.max_run_seconds
    validate_budget(budget)
    if (budget.provider != "sharadar" or budget.max_requests > BUDGET.max_requests
            or budget.max_total_bytes > BUDGET.max_total_bytes
            or budget.max_run_seconds > BUDGET.max_run_seconds
            or budget.min_interval_milliseconds < 1000 or budget.daily_ceiling != 1000):
        raise ResourceLimitError("Sharadar recovery exceeds approved maximum bounds")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("Sharadar recovery requires a resolved explicit root")
    if (not isinstance(approval, RecoveryApproval)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", approval.approval_id)
            or not approval.decision or len(approval.decision) > 4096):
        raise ValidationError("Sharadar recovery requires an explicit bounded approval")
    now = _clock(utcnow)
    if _utc(approval.authorized_at) > now:
        raise ConflictError("Sharadar recovery approval is from the future")
    private_directory(root)
    with job_lock(root / "refresh-supervisor"):
        journal = root / "operator-recoveries" / approval.approval_id
        if (journal / "started.json").exists():
            raise ConflictError("Sharadar recovery approval has already been consumed")
        with job_lock(root / "refresh-controller"), job_lock(root):
            state, raw, checkpoint, unit, reservation = _preflight(root, stores, selection, approval, now)
            recovery = SharadarRecovery(root, utcnow=utcnow, monotonic=monotonic,
                sleeper=sleeper, deadline=deadline)
            if unit.unit_id in recovery.state["selections"]:
                raise ConflictError("Sharadar recovery request already has a replacement")
            at = _clock(utcnow)
            selected = replace(unit, observation_window=at)
            if selected.unit_id == unit.unit_id or at <= reservation["charged_at"]:
                raise ConflictError("Sharadar recovery requires a fresh later attempt")
            if (selected.unit_id in state["units"]
                    or (root / "responses" / (selected.unit_id + ".json")).exists()
                    or (root / "retry-links" / (selected.unit_id + ".json")).exists()):
                raise ConflictError("Sharadar recovery replacement identity already exists")
            if monotonic() + unit.timeout_seconds >= deadline:
                raise ResourceLimitError("Sharadar recovery original deadline exhausted")
            allowance = replace(budget, max_total_bytes=budget.max_total_bytes - unit.max_response_bytes)
            validate_budget(allowance)
            private_directory(journal.parent)
            private_directory(journal)
            atomic(journal / "approval.json", asdict(approval))
            atomic(journal / "before-ledger.json", raw)
            atomic(journal / "before-refresh-state.json", checkpoint)
            atomic(journal / "before-retry-state.json", _read(root / "refresh-retry-state.json"))
            # A crash after this fsynced claim cannot renew the one-use allowance.
            atomic(journal / "started.json", {"contract": "quant_data.sharadar_operator_recovery.v1",
                "approval_sha256": digest(asdict(approval)), "started_at": at,
                "deadline_at": (instant(at) + timedelta(seconds=max(0, deadline-monotonic()))).isoformat(),
                "budget": asdict(budget), "prior_uncertain_bytes": unit.max_response_bytes,
                "replacement_unit_id": selected.unit_id, "original_reservation": reservation})
            private_directory(root / "failures")
            atomic(root / "failures" / (unit.unit_id + ".json"), {
                "contract": "quant_data.collection_failed_request.v1", **reservation,
                "error": "sharadar_outcome_unknown_operator_reconciled", "http_status": None,
                "automatic_retry": False, "reserved_bytes": unit.max_response_bytes,
                "operator_approval": str((journal / "approval.json").relative_to(root))})
            private_directory(root / "retry-links")
            atomic(root / "retry-links" / (selected.unit_id + ".json"), {
                "contract": "quant_data.sharadar_authorized_retry.v1",
                "original_unit_id": unit.unit_id, "retry_unit_id": selected.unit_id,
                "request_id": unit.request_id, "previous_unit_id": unit.unit_id,
                "previous_outcome": "unknown", "retry_at": at,
                "operator_approval": str((journal / "approval.json").relative_to(root))})
            recovery.state["selections"][unit.unit_id] = at
            recovery.save()
            # Retire the reservation only after replacement evidence is durable.
            # Original attempt, charge and measured received bytes stay intact.
            bucket = state["usage"][reservation["day"]]
            bucket["uncertain_bytes"] = bucket.get("uncertain_bytes", 0) + unit.max_response_bytes
            state["units"][unit.unit_id] = {**state["units"][unit.unit_id],
                "error": "sharadar_outcome_unknown_operator_reconciled", "http_status": None}
            state["pending"] = None
            _save(root, state)
        try:
            fetch = fetch_factory()
            result = _run_refresh_locked(root=root, stores=stores, registry=registry,
                selection=selection, fetch=fetch, budget=allowance,
                secret_values=getattr(fetch, "secret_values", ()), utcnow=utcnow,
                monotonic=monotonic, sleeper=sleeper, deadline=deadline)
        except Exception as error:
            atomic(journal / "finished.json", {"outcome": "blocked", "error": type(error).__name__,
                "finished_at": _clock(utcnow), "elapsed_seconds": monotonic()-started})
            raise
        atomic(journal / "finished.json", {"result": result, "finished_at": _clock(utcnow),
            "elapsed_seconds": monotonic()-started, "prior_uncertain_bytes": unit.max_response_bytes})
        return result
