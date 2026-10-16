"""One explicitly admitted retry; unavailable history units become durable gaps."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import gzip
import hashlib
import json
import os

from .cross_asset import MAX_BYTES, MAX_REQUESTS, METHODS, QUALITY_GAPS
from .cross_asset_parallel import ParallelCrossAssetJob
from .daily import sync_admission_directories
from .model import canonical_json
from .transport import Budget, ProviderFailure

RECOVERY_AMENDMENT = "unavailable-amendment.json"


def request_identity(method, params):
    return {"method": method, "params": {k: str(v) for k, v in params.items()}}


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class RecoveringCrossAssetJob(ParallelCrossAssetJob):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recovery = None
        self._unavailable = {}
        self._retry_consumed = False

    def _authorization(self, manifest):
        started = super()._authorization(manifest)
        raw = (self.work / "authorization.json").read_bytes()
        base = json.loads(raw)
        value = json.loads((self.work / RECOVERY_AMENDMENT).read_text())
        retry = value.get("retry", {})
        expected = {
            "contract": "theta_cross_asset_unavailable_amendment_v1",
            "authorization_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_sha256": base["manifest_sha256"],
            "unchanged_caps": base["caps"], "continue_codes": ["UNAVAILABLE"],
            "retry": retry, "authorized_at": value.get("authorized_at"),
        }
        if value != expected or set(retry) != {
                "method", "params", "unit", "previous_receipt_sha256", "additional_attempts"}:
            raise ValueError("cross_asset_unavailable_amendment")
        identity = request_identity(retry["method"], retry["params"])
        params = identity["params"]
        session = params.get("date", params.get("start_date"))
        allowed = {(s, d) for s, d, _ in manifest["units"]}
        if (retry["additional_attempts"] != 1 or retry["method"] not in METHODS or
                identity["params"] != retry["params"] or retry["unit"] != digest(identity) or
                (params.get("symbol"), session) not in allowed):
            raise ValueError("cross_asset_retry_scope")
        authorized = datetime.fromisoformat(value["authorized_at"])
        if authorized.tzinfo is None or not started <= authorized <= self.clock():
            raise ValueError("cross_asset_unavailable_amendment_clock")
        self.recovery = value
        return started

    def _budget_from_journal(self, started, completed):
        """Count physical attempts, including the one permitted duplicate identity."""
        attempts, receipts = defaultdict(list), defaultdict(list)
        for attempt in self.history:
            unit = digest(request_identity(attempt["method"], attempt["params"]))
            if attempt["unit"] != unit:
                raise RuntimeError("cross_asset_attempt_identity")
            attempts[unit].append(attempt)
        received = 0
        path = self.work / "request-receipts.jsonl"
        for line in path.read_text().splitlines():
            receipt = json.loads(line)
            count = receipt.get("response_bytes")
            if type(count) is not int or count < 0:
                raise RuntimeError("cross_asset_unbounded_receipt")
            received += count
            receipts[digest(request_identity(receipt["method"], receipt["params"]))].append(receipt)
        retry = self.recovery["retry"]
        target = retry["unit"]
        if (not receipts.get(target) or receipts[target][0].get("outcome") != "UNAVAILABLE" or
                digest(receipts[target][0]) != retry["previous_receipt_sha256"]):
            raise RuntimeError("cross_asset_retry_evidence")
        if set(attempts) != set(receipts):
            raise RuntimeError("cross_asset_unreconciled_attempts")
        for unit, events in attempts.items():
            outcomes = receipts[unit]
            limit = 2 if unit == target else 1
            if len(events) != len(outcomes) or not 1 <= len(events) <= limit:
                raise RuntimeError("cross_asset_unreconciled_attempts")
            if len(events) == 2 and events[1].get("retry_of") != retry["previous_receipt_sha256"]:
                raise RuntimeError("cross_asset_retry_evidence")
            if any(r.get("outcome") not in ("success", "empty", "no_data", "UNAVAILABLE") for r in outcomes):
                raise RuntimeError("cross_asset_unreconciled_attempts")
            latest = outcomes[-1]
            staged = self.stage / (unit + ".json.gz")
            if latest["outcome"] == "UNAVAILABLE":
                if staged.exists():
                    raise RuntimeError("cross_asset_failed_request_staged")
                self._unavailable[unit] = latest
            elif not staged.exists():
                params = latest["params"]
                session = params.get("date", params.get("start_date"))
                if (params.get("symbol"), session) not in completed:
                    raise RuntimeError("cross_asset_unreconciled_attempts")
        self._retry_consumed = len(attempts[target]) == 2
        self._budget = Budget(max_requests=MAX_REQUESTS, max_bytes=MAX_BYTES,
            max_seconds=max(1, int(self._remaining(started))),
            requests=len(self.history), received_bytes=received)
        return self._budget

    def _gap_state(self, allowed):
        gaps = {}
        for line in (self.work / "gap-events.jsonl").read_text().splitlines():
            event = json.loads(line)
            key = (event["symbol"], event["session"])
            if key not in allowed or event.get("reason") not in QUALITY_GAPS | {"provider_unavailable"}:
                raise ValueError("cross_asset_gap_event")
            gaps[key] = event
        return gaps

    def _unavailable_is_gap(self, exc):
        return self.recovery is not None and isinstance(exc, ProviderFailure) and exc.code == "UNAVAILABLE"

    def _get(self, started, method, *, wait, **params):
        unit = digest(request_identity(method, params))
        if unit in self._unavailable and (unit != self.recovery["retry"]["unit"] or self._retry_consumed):
            # A crash before the coordinator wrote its gap cannot admit a replay.
            raise ProviderFailure("UNAVAILABLE", self._unavailable[unit])
        return super()._get(started, method, wait=wait, **params)

    def cached_get(self, transport, method, **params):
        identity = request_identity(method, params)
        unit = digest(identity)
        target = self.stage / (unit + ".json.gz")
        retry = self.recovery["retry"] if self.recovery else None
        if target.exists() or not retry or unit != retry["unit"]:
            return super().cached_get(transport, method, **params)
        with self._journal_lock:
            self._check_stop()
            if self._retry_consumed or unit not in self._unavailable:
                raise RuntimeError("unit_previously_attempted_no_retry")
            self._append(self.attempt_path, {"unit": unit, **identity,
                "started_at": self.clock().isoformat(),
                "retry_of": retry["previous_receipt_sha256"]})
            self._retry_consumed = True
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

    def persist(self):
        if self.recovery is not None:
            self.status.update(unavailable_policy="record_gap_and_continue",
                explicit_retry_allowance=1, explicit_retry_consumed=self._retry_consumed)
        super().persist()
