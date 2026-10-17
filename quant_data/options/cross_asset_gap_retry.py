"""One authorized pass over the completed cross-asset run's recorded gaps."""
from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
import json
import os

from .cross_asset import MAX_BYTES, MAX_REQUESTS, QUALITY_GAPS, WORK, reviewed_manifest
from .cross_asset_durable import DurableCrossAssetJob, DurableResponseBudget, RESERVATIONS, WINDOW
from .cross_asset_parallel import ParallelCrossAssetJob
from .cross_asset_recovery import digest, request_identity
from .daily import sync_admission_directories
from .transport import ProviderFailure

RETRY_WORK = WORK / "gap-retry-20261014"
AUTHORIZATION = "retry-authorization.json"
PARENT_FILES = ("authorization.json", "manifest.json", "status.json", "attempts.jsonl",
    "request-receipts.jsonl", "authentication-attempts.jsonl", "authentication-outcomes.jsonl",
    "gap-events.jsonl", RESERVATIONS, "parallel-amendment.json", "unavailable-amendment.json",
    "interruption-amendment.json")
GAPS = QUALITY_GAPS | {"provider_unavailable", "interrupted_no_receipt", "response_not_staged"}


def retry_authorization(root, authorized_at, *, clock):
    """Build reviewable scope; calling this does not itself authorize execution."""
    def forbidden(*args, **kwargs):
        raise AssertionError("retry_preflight_provider_forbidden")
    parent = DurableCrossAssetJob(root, transport_factory=forbidden, clock=clock)
    manifest = reviewed_manifest(root)
    started = parent._authorization(manifest)
    status = json.loads(parent.status_path.read_text())
    if status["state"] != "completed_with_gaps" or not status["gaps"]:
        raise ValueError("retry_parent_not_completed_with_gaps")
    allowed = {(s, d) for s, d, _ in manifest["units"]}
    gaps = parent._gap_state(allowed)
    if gaps != {(g["symbol"], g["session"]): g for g in status["gaps"]}:
        raise ValueError("retry_parent_gap_drift")
    parent._authentication_state()
    completed = parent._coverage(manifest)
    budget = parent._budget_from_journal(started, completed).snapshot()
    units = [u for u in manifest["units"] if tuple(u[:2]) in gaps]
    authorized = datetime.fromisoformat(authorized_at)
    if authorized.tzinfo is None or not started <= authorized <= clock():
        raise ValueError("retry_authorization_clock")
    return {"contract": "theta_cross_asset_gap_retry_v1", "authorized_at": authorized_at,
        "parent_sha256": {name: hashlib.sha256((parent.work / name).read_bytes()).hexdigest()
                          for name in PARENT_FILES},
        "units": units, "unchanged_caps": manifest["caps"], "workers": 4,
        "after_hours_only": True, "additional_attempts_per_request": 1,
        "max_new_requests": 3 * len(units), "started_at": started.isoformat(),
        "base_budget": budget, "base_authentications": parent.authentications,
        "base_completed_sessions": status["completed_sessions"]}


class GapRetryJob(ParallelCrossAssetJob):
    WORK_DIRECTORY = str(RETRY_WORK)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = None
        self.request_units = {}
        self.blocked_sessions = {}
        self.status["contract"] = "theta_cross_asset_gap_retry_status"

    def _authorization(self, manifest):
        value = json.loads((self.work / AUTHORIZATION).read_text())
        expected = retry_authorization(self.root, value["authorized_at"], clock=self.clock)
        # JSON-normalize tuple-valued calendar units before comparing.
        if value != json.loads(json.dumps(expected)):
            raise ValueError("retry_authorization_drift")
        self.policy = value
        self.allowed_sessions = {tuple(u[:2]) for u in value["units"]}
        for symbol, day, _ in value["units"]:
            for method in ("option_history_greeks_eod", "option_history_eod", "option_history_open_interest"):
                params = {"symbol": symbol, "expiration": "*"}
                if method == "option_history_open_interest":
                    params["date"] = day
                else:
                    params.update(start_date=day, end_date=day)
                    if method == "option_history_greeks_eod":
                        params.update(version="1", underlyer_use_nbbo=True)
                identity = request_identity(method, params)
                self.request_units[digest(identity)] = identity
        self.status.update(original_gap_count=len(value["units"]),
            retry_request_cap=value["max_new_requests"], additional_attempts_per_request=1)
        return datetime.fromisoformat(value["started_at"])

    def _authentication_state(self):
        attempts = [json.loads(x) for x in (self.work / "authentication-attempts.jsonl").read_text().splitlines()]
        outcomes = [json.loads(x) for x in (self.work / "authentication-outcomes.jsonl").read_text().splitlines()]
        base = self.policy["base_authentications"]
        if len(attempts) != len(outcomes) or base + len(attempts) > self.policy["unchanged_caps"]["max_authentications"]:
            raise RuntimeError("retry_authentication_failed_or_unknown")
        for ordinal, (attempt, outcome) in enumerate(zip(attempts, outcomes), base + 1):
            if attempt.get("attempt") != ordinal or outcome != {
                    "attempt": ordinal, "epoch": attempt.get("epoch"), "outcome": "success"}:
                raise RuntimeError("retry_authentication_failed_or_unknown")
        self.authentications = base + len(attempts)

    def _budget_from_journal(self, started, completed):
        attempts = {}
        for event in self.history:
            unit = event["unit"]
            if (unit in attempts or self.request_units.get(unit) != request_identity(event["method"], event["params"])
                    or event.get("gap_retry_authorization_sha256") != digest(self.policy)):
                raise RuntimeError("retry_attempt_scope")
            attempts[unit] = event
        path = self.work / RESERVATIONS
        with path.open("a") as stream:
            os.fsync(stream.fileno())
        sync_admission_directories(path.parent, self.root)
        reserved = {}
        for line in path.read_text().splitlines():
            event = json.loads(line)
            unit, size = event.get("unit"), event.get("reserved_bytes")
            if (set(event) != {"unit", "reserved_bytes"} or unit not in attempts or type(size) is not int
                    or size != reserved.get(unit, 0) + WINDOW or size > MAX_BYTES):
                raise RuntimeError("retry_reservation_evidence")
            reserved[unit] = size
        receipts = {}
        for line in (self.work / "request-receipts.jsonl").read_text().splitlines():
            receipt = json.loads(line)
            unit = digest(request_identity(receipt["method"], receipt["params"]))
            count = receipt.get("response_bytes")
            if (unit in receipts or unit not in attempts or type(count) is not int or count < 0
                    or count > reserved.get(unit, 0)
                    or receipt.get("outcome") not in ("success", "empty", "no_data", "UNAVAILABLE")):
                raise RuntimeError("retry_receipt_evidence")
            receipts[unit] = receipt
        uncertain = 0
        for unit, event in attempts.items():
            params = event["params"]
            key = (params["symbol"], params.get("date", params.get("start_date")))
            target = self.stage / (unit + ".json.gz")
            if unit not in receipts:
                if target.exists():
                    raise RuntimeError("retry_unreceipted_stage")
                uncertain += reserved.get(unit, 0)
                self.blocked_sessions[key] = "interrupted_no_receipt"
            elif receipts[unit]["outcome"] == "UNAVAILABLE":
                if target.exists():
                    raise RuntimeError("retry_failed_stage")
                self.blocked_sessions[key] = "provider_unavailable"
            elif not target.exists() and key not in completed:
                self.blocked_sessions[key] = "response_not_staged"
        base = self.policy["base_budget"]
        self._budget = DurableResponseBudget(
            journal=lambda event: self._append(path, event),
            known_bytes=base["received_bytes"] + sum(x["response_bytes"] for x in receipts.values()),
            uncertain_bytes=base["response_bytes_unmeasured_allowance"] + uncertain,
            requests=base["data_requests"] + len(attempts),
            max_requests=min(MAX_REQUESTS, base["data_requests"] + self.policy["max_new_requests"]),
            max_bytes=MAX_BYTES, max_seconds=max(1, int(self._remaining(started))))
        if (self._budget.requests > self._budget.max_requests
                or self._budget.snapshot()["response_bytes_accounted"] > MAX_BYTES):
            raise RuntimeError("retry_budget_exhausted")
        return self._budget

    def _adopt(self, method, params):
        # New evidence is isolated from the original completed population.
        return self.stage / (digest(request_identity(method, params)) + ".json.gz")

    def _get(self, started, method, *, wait, **params):
        identity = request_identity(method, params)
        unit = digest(identity)
        if self.request_units.get(unit) != identity:
            raise RuntimeError("retry_request_outside_scope")
        with self._budget.request_context(unit):
            return super()._get(started, method, wait=wait, **params)

    def cached_get(self, transport, method, **params):
        identity = request_identity(method, params)
        unit = digest(identity)
        if self.request_units.get(unit) != identity:
            raise RuntimeError("retry_request_outside_scope")
        target = self._adopt(method, params)
        if target.exists():
            with gzip.open(target, "rt") as stream:
                batch = json.load(stream)
            if request_identity(batch["receipt"]["method"], batch["receipt"]["params"]) != identity:
                raise RuntimeError("retry_staged_identity")
            return batch, target
        with self._journal_lock:
            self._check_stop()
            if unit in self.attempted:
                raise RuntimeError("unit_previously_attempted_no_retry")
            self._append(self.attempt_path, {"unit": unit, **identity,
                "started_at": self.clock().isoformat(), "gap_retry_authorization_sha256": digest(self.policy)})
            self.attempted.add(unit)
        rows, receipt = transport.request(method, **params)
        batch = {"rows": rows, "receipt": receipt}
        tmp = target.with_suffix(".tmp")
        with gzip.open(tmp, "wt", compresslevel=1) as stream:
            json.dump(batch, stream, sort_keys=True, allow_nan=False)
        with tmp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        sync_admission_directories(self.stage, self.root)
        return batch, target

    def receipt(self, receipt, budget):
        super().receipt(receipt, budget)
        budget.settle(receipt)

    def _unavailable_is_gap(self, exc):
        return isinstance(exc, ProviderFailure) and exc.code == "UNAVAILABLE"

    def _download(self, started, unit, references, wait):
        key = tuple(unit[:2])
        if key in self.blocked_sessions:
            return None, self.blocked_sessions[key], set()
        return super()._download(started, unit, references, wait)

    def _gap_state(self, allowed):
        gaps = {}
        for line in (self.work / "gap-events.jsonl").read_text().splitlines():
            event = json.loads(line)
            key = (event["symbol"], event["session"])
            if key not in self.allowed_sessions or event.get("reason") not in GAPS:
                raise ValueError("retry_gap_scope")
            gaps[key] = event
        return gaps

    def _sessions(self, started, units, references, *, wait):
        if any(tuple(unit[:2]) not in self.allowed_sessions for unit in units):
            raise RuntimeError("retry_missing_outside_scope")
        yield from super()._sessions(started, units, references, wait=wait)

    def persist(self):
        if self.policy is not None:
            self.status["recovered_gap_sessions"] = max(0, self.status.get("completed_sessions", 0)
                - self.policy["base_completed_sessions"])
        super().persist()
