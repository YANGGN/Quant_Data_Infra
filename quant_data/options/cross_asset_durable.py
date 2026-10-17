"""Reconcile interrupted history without replay, with durable byte reservations."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import gzip
import hashlib
import json
import os
import threading

from .cross_asset import MAX_BYTES, MAX_REQUESTS, QUALITY_GAPS
from .cross_asset_recovery import RecoveringCrossAssetJob, digest, request_identity
from .daily import sync_admission_directories
from .transport import Budget

INTERRUPTION_AMENDMENT = "interruption-amendment.json"
RESERVATIONS = "response-reservations.jsonl"
MESSAGE_LIMIT = 64 * 1024**2  # Existing gRPC max_receive_message_length.
WINDOW = 2 * MESSAGE_LIMIT
LEGACY_ALLOWANCE = 1024**3


class DurableResponseBudget(Budget):
    """Reserve before RPC/next message; settle only after a durable receipt."""
    def __init__(self, *, journal, known_bytes, uncertain_bytes, **kwargs):
        super().__init__(received_bytes=known_bytes, **kwargs)
        self.journal = journal
        self.uncertain_bytes = uncertain_bytes
        self.settled_bytes = known_bytes
        self.active = {}
        self.context = threading.local()

    @contextmanager
    def request_context(self, unit):
        self.context.unit = unit
        try:
            yield
        finally:
            del self.context.unit

    def _accounted(self):
        return self.settled_bytes + self.uncertain_bytes + sum(v[0] for v in self.active.values())

    def _check_locked(self):
        super()._check_locked()
        if self._accounted() >= self.max_bytes:
            raise RuntimeError("response_byte_cap")

    def reserve_request(self):
        with self._lock:
            self._check_locked()
            unit = self.context.unit
            if unit in self.active:
                raise RuntimeError("cross_asset_duplicate_active_request")
            if self._accounted() + WINDOW > self.max_bytes:
                raise RuntimeError("response_byte_cap")
            self.journal({"unit": unit, "reserved_bytes": WINDOW})
            self.active[unit] = [WINDOW, 0]
            self.requests += 1

    def add_bytes(self, count):
        with self._lock:
            unit = self.context.unit
            reservation = self.active[unit]
            if type(count) is not int or not 0 <= count <= MESSAGE_LIMIT:
                raise RuntimeError("cross_asset_message_limit")
            if reservation[1] + count > reservation[0]:
                raise RuntimeError("cross_asset_unreserved_response")
            reservation[1] += count
            self.received_bytes += count
            # The current message is covered by the prior reservation. A new
            # durable window must cover the largest possible next message.
            if reservation[0] - reservation[1] < MESSAGE_LIMIT:
                if self._accounted() + WINDOW > self.max_bytes:
                    raise RuntimeError("response_byte_cap")
                enlarged = reservation[0] + WINDOW
                self.journal({"unit": unit, "reserved_bytes": enlarged})
                reservation[0] = enlarged
            if self.remaining_seconds() <= 0:
                raise RuntimeError("duration_cap")

    def remaining_seconds(self):
        import time
        return self.max_seconds - (time.monotonic() - self.started)

    def settle(self, receipt):
        with self._lock:
            unit = self.context.unit
            if unit not in self.active:
                if receipt["response_bytes"] == 0:
                    return  # Reservation/admission failed before the RPC.
                raise RuntimeError("cross_asset_unreserved_response")
            reservation = self.active[unit]
            if receipt["response_bytes"] != reservation[1]:
                raise RuntimeError("cross_asset_receipt_byte_mismatch")
            self.settled_bytes += reservation[1]
            del self.active[unit]

    def snapshot(self):
        with self._lock:
            return {"data_requests": self.requests, "received_bytes": self.received_bytes,
                "response_bytes_accounted": self._accounted(),
                "response_bytes_unmeasured_allowance": self.uncertain_bytes,
                "response_bytes_inflight_reservation": sum(v[0] for v in self.active.values())}


class DurableCrossAssetJob(RecoveringCrossAssetJob):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interruption = None
        self.interrupted_sessions = set()

    def _authorization(self, manifest):
        started = super()._authorization(manifest)
        value = json.loads((self.work / INTERRUPTION_AMENDMENT).read_text())
        base = json.loads((self.work / "authorization.json").read_text())
        expected = {"contract": "theta_cross_asset_interruption_amendment_v1",
            "authorization_sha256": hashlib.sha256((self.work / "authorization.json").read_bytes()).hexdigest(),
            "manifest_sha256": base["manifest_sha256"], "unchanged_caps": base["caps"],
            "legacy_allowance_bytes_per_request": LEGACY_ALLOWANCE,
            "additional_retries": 0, "interrupted_policy": "record_gap_and_continue",
            "prefixes": value.get("prefixes"), "requests": value.get("requests"),
            "authorized_at": value.get("authorized_at")}
        if value != expected:
            raise ValueError("cross_asset_interruption_amendment")
        authorized = datetime.fromisoformat(value["authorized_at"])
        if authorized.tzinfo is None or not started <= authorized <= self.clock():
            raise ValueError("cross_asset_interruption_clock")
        if set(value["prefixes"]) != {"attempts.jsonl", "request-receipts.jsonl"}:
            raise ValueError("cross_asset_interruption_prefix")
        for name, prefix in value["prefixes"].items():
            if set(prefix) != {"bytes", "sha256", "lines"} or type(prefix["bytes"]) is not int or prefix["bytes"] < 0:
                raise ValueError("cross_asset_interruption_prefix")
            with (self.work / name).open("rb") as stream:
                raw = stream.read(prefix["bytes"])
            if (len(raw) != prefix["bytes"] or hashlib.sha256(raw).hexdigest() != prefix["sha256"] or
                    len(raw.splitlines()) != prefix["lines"] or (raw and not raw.endswith(b"\n"))):
                raise ValueError("cross_asset_interruption_prefix")
        allowed = {(s, d) for s, d, _ in manifest["units"]}
        requests = value["requests"]
        if not isinstance(requests, list) or len(requests) != 4:
            raise ValueError("cross_asset_interruption_scope")
        units = set()
        for item in requests:
            if set(item) != {"unit", "method", "params", "attempt_sha256"}:
                raise ValueError("cross_asset_interruption_scope")
            params = item["params"]
            key = (params.get("symbol"), params.get("date", params.get("start_date")))
            if item["unit"] != digest(request_identity(item["method"], params)) or key not in allowed:
                raise ValueError("cross_asset_interruption_scope")
            units.add(item["unit"])
        if len(units) != 4:
            raise ValueError("cross_asset_interruption_scope")
        self.interruption = value
        return started

    def _budget_from_journal(self, started, completed):
        all_attempts = self.history
        counts = Counter(x["unit"] for x in all_attempts)
        receipts = [json.loads(x) for x in (self.work / "request-receipts.jsonl").read_text().splitlines()]
        receipt_counts = Counter(digest(request_identity(x["method"], x["params"])) for x in receipts)
        legacy = {x["unit"]: x for x in self.interruption["requests"]}
        boundary = self.interruption["prefixes"]["attempts.jsonl"]["lines"]
        prior = {x["unit"]: x for x in all_attempts[:boundary]}
        for unit, item in legacy.items():
            if unit not in prior or digest(prior[unit]) != item["attempt_sha256"] or counts[unit] != 1 or receipt_counts[unit]:
                raise RuntimeError("cross_asset_interruption_evidence")
        reservations = {}
        path = self.work / RESERVATIONS
        # The directory entry must survive a crash before absence of a
        # reservation can prove that a marked attempt never reached the RPC.
        # Repeat on every startup, including after an earlier sync failure.
        with path.open("a") as stream:
            os.fsync(stream.fileno())
        sync_admission_directories(path.parent, self.root)
        fresh_units = {x["unit"] for x in all_attempts[boundary:]}
        if path.exists():
            for line in path.read_text().splitlines():
                event = json.loads(line)
                unit, size = event.get("unit"), event.get("reserved_bytes")
                if (set(event) != {"unit", "reserved_bytes"} or unit not in fresh_units or
                        type(size) is not int or size != reservations.get(unit, 0) + WINDOW or size > MAX_BYTES):
                    raise RuntimeError("cross_asset_reservation_evidence")
                reservations[unit] = size
        excluded, uncertain = set(), 0
        for index, attempt in enumerate(all_attempts):
            unit = attempt["unit"]
            if counts[unit] == receipt_counts[unit]:
                continue
            if counts[unit] != 1 or receipt_counts[unit] != 0:
                raise RuntimeError("cross_asset_unreconciled_attempts")
            if unit in legacy:
                uncertain += LEGACY_ALLOWANCE
            elif (index >= boundary and attempt.get("response_reservation_contract") == "v1" and
                  attempt.get("interruption_amendment_sha256") == digest(self.interruption)):
                # No reservation proves no RPC was started by this version.
                uncertain += reservations.get(unit, 0)
            else:
                raise RuntimeError("cross_asset_unreconciled_attempts")
            if (self.stage / (unit + ".json.gz")).exists():
                raise RuntimeError("cross_asset_interrupted_request_staged")
            excluded.add(unit)
            params = attempt["params"]
            self.interrupted_sessions.add((params["symbol"], params.get("date", params.get("start_date"))))
        # The established recovery validator handles all receipted units,
        # including the already-consumed one-time TLT exception.
        try:
            self.history = [x for x in all_attempts if x["unit"] not in excluded]
            budget = super()._budget_from_journal(started, completed)
        finally:
            self.history = all_attempts
        self._budget = DurableResponseBudget(
            journal=lambda event: self._append(path, event),
            known_bytes=budget.received_bytes, uncertain_bytes=uncertain,
            requests=len(all_attempts), max_requests=MAX_REQUESTS,
            max_bytes=MAX_BYTES, max_seconds=max(1, int(self._remaining(started))))
        self._budget.check()
        return self._budget

    def _get(self, started, method, *, wait, **params):
        unit = digest(request_identity(method, params))
        with self._budget.request_context(unit):
            return super()._get(started, method, wait=wait, **params)

    def cached_get(self, transport, method, **params):
        identity = request_identity(method, params)
        unit = digest(identity)
        target = self.stage / (unit + ".json.gz")
        if target.exists() or unit == self.recovery["retry"]["unit"]:
            return super().cached_get(transport, method, **params)
        day = params.get("date", params.get("start_date"))
        legacy = self.work / (method + "-" + str(day) + ".json.gz")
        if legacy.exists():
            with gzip.open(legacy, "rt") as stream:
                batch = json.load(stream)
            if batch["receipt"]["params"] == identity["params"]:
                return batch, legacy
        with self._journal_lock:
            self._check_stop()
            if unit in self.attempted:
                raise RuntimeError("unit_previously_attempted_no_retry")
            self._append(self.attempt_path, {"unit": unit, **identity,
                "started_at": self.clock().isoformat(),
                "response_reservation_contract": "v1",
                "interruption_amendment_sha256": digest(self.interruption)})
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

    def _download(self, started, unit, references, wait):
        if (unit[0], unit[1]) in self.interrupted_sessions:
            return None, "interrupted_no_receipt", set()
        return super()._download(started, unit, references, wait)

    def _gap_state(self, allowed):
        gaps = {}
        for line in (self.work / "gap-events.jsonl").read_text().splitlines():
            event = json.loads(line)
            key = (event["symbol"], event["session"])
            if key not in allowed or event.get("reason") not in QUALITY_GAPS | {"provider_unavailable", "interrupted_no_receipt"}:
                raise ValueError("cross_asset_gap_event")
            gaps[key] = event
        return gaps

    def persist(self):
        if self.interruption is not None:
            self.status.update(interrupted_policy="record_gap_and_continue",
                               interrupted_sessions=len(self.interrupted_sessions))
        super().persist()
