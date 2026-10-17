"""Four after-hours downloads sharing one Theta login and the original budget."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import threading

from .cross_asset import (
    CROSS_ASSET_ETFS, MAX_AUTHENTICATIONS, BackgroundBusy, CrossAssetJob, Paused,
    _request_window,
)

WORKERS = 4
AMENDMENT = "parallel-amendment.json"


class ParallelCrossAssetJob(CrossAssetJob):
    """Keep the original manifest/authorization immutable; opt in by amendment."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session_guard = threading.RLock()
        self._inflight = 0
        self._peak = 0
        self._renew = False
        self._download_failure = None

    def _authorization(self, manifest):
        original = (self.work / "authorization.json").read_bytes()
        base = json.loads(original)
        amendment = json.loads((self.work / AMENDMENT).read_text())
        expected = {
            "contract": "theta_cross_asset_parallel_amendment_v1",
            "authorization_sha256": hashlib.sha256(original).hexdigest(),
            "manifest_sha256": base["manifest_sha256"],
            "unchanged_caps": base["caps"], "workers": WORKERS,
            "after_hours_only": True, "authorized_at": amendment.get("authorized_at"),
        }
        if amendment != expected:
            raise ValueError("cross_asset_parallel_amendment")
        authorized = datetime.fromisoformat(amendment["authorized_at"])
        if authorized.tzinfo is None or authorized > self.clock():
            raise ValueError("cross_asset_parallel_amendment_clock")
        return super()._authorization(manifest)

    def persist(self):
        # Only the coordinator writes status; network workers update counters
        # under the same guard used for session renewal and admission.
        with self._session_guard:
            self.status.update(download_workers=WORKERS, after_hours_only=True,
                inflight_requests=self._inflight, max_inflight_requests=self._peak,
                authentication_attempts=self.authentications)
            if self.transport is not None:
                self.status["subscription_code"] = self.transport.subscription_code
            super().persist()

    def _check_stop(self):
        if self._stop.is_set():
            raise Paused("parallel_workers_drained")

    def _unavailable_is_gap(self, exc):
        return False

    def _stop_after_failure(self, exc):
        # Publish the causal error before siblings can observe shutdown.
        with self._session_guard:
            if not isinstance(exc, Paused) and self._download_failure is None:
                self._download_failure = exc
            self._stop.set()

    def _close_idle(self):
        if self._inflight:
            raise RuntimeError("parallel_session_still_inflight")
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.epoch = None

    def _shared_transport(self, epoch):
        """Caller holds the session guard and the cross-process request lease."""
        self._check_stop()
        if self.transport is None or self.epoch != epoch or self._renew:
            if self._inflight:
                raise BackgroundBusy("draining_previous_session")
            self._close_idle()
            if self.authentications >= MAX_AUTHENTICATIONS:
                raise RuntimeError("cross_asset_authentication_cap")
            ordinal = self.authentications + 1
            self._append(self.work / "authentication-attempts.jsonl",
                         {"attempt": ordinal, "at": self.clock().isoformat(), "epoch": epoch})
            try:
                transport = self.transport_factory(self.root, self._budget,
                    receipt_callback=self.receipt, symbols=CROSS_ASSET_ETFS)
            except BaseException as exc:
                self._stop_after_failure(exc)
                self._append(self.work / "authentication-outcomes.jsonl",
                    {"attempt": ordinal, "epoch": epoch, "outcome": "failed",
                     "error": type(exc).__name__})
                raise
            self._append(self.work / "authentication-outcomes.jsonl",
                         {"attempt": ordinal, "epoch": epoch, "outcome": "success"})
            self.transport, self.epoch = transport, epoch
            self.authentications, self._renew = ordinal, False
        return self.transport

    def _get(self, started, method, *, wait, **params):
        self._check_stop()
        target = self._adopt(method, params)
        if target.exists():
            return self.cached_get(None, method, **params)
        while True:
            self._check_stop()
            self._remaining(started)
            if not _request_window(self.clock()):
                with self._session_guard:
                    self._renew = True
                    if not self._inflight:
                        self._close_idle()
                if not wait:
                    raise Paused("scheduled_or_market_window")
            else:
                try:
                    with self.admission.background(self.clock()) as epoch:
                        with self._session_guard:
                            self._check_stop()
                            if not _request_window(self.clock()):
                                self._renew = True
                                raise BackgroundBusy("scheduled_or_market_window")
                            self._storage_check()
                            self._budget.check()
                            transport = self._shared_transport(epoch)
                            if not _request_window(self.clock()):
                                self._renew = True
                                raise BackgroundBusy("scheduled_or_market_window")
                            self._inflight += 1
                            self._peak = max(self._peak, self._inflight)
                        try:
                            return self.cached_get(transport, method, **params)
                        except BaseException as exc:
                            if type(exc) is RuntimeError and str(exc) == "parallel_download_stopped":
                                raise Paused("parallel_workers_drained") from None
                            if not self._unavailable_is_gap(exc):
                                self._stop_after_failure(exc)
                            raise
                        finally:
                            with self._session_guard:
                                self._inflight -= 1
                except BackgroundBusy:
                    if not wait:
                        raise Paused("live_or_background_capacity") from None
            # Event.wait lets a graceful stop wake idle workers immediately.
            self._stop.wait(min(1, max(.01, self._remaining(started))))

    def _download(self, started, unit, references, wait):
        try:
            return self._session(started, *unit, references, wait=wait)
        except BaseException as exc:
            if self._unavailable_is_gap(exc):
                return None, "provider_unavailable", set()
            self._stop_after_failure(exc)
            raise

    def _sessions(self, started, units, references, *, wait):
        pending = deque()
        iterator = iter(units)
        pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="cross-asset")
        try:
            while True:
                while len(pending) < WORKERS and not self._stop.is_set():
                    unit = next(iterator, None)
                    if unit is None:
                        break
                    pending.append((unit, pool.submit(self._download, started, unit, references, wait)))
                if not pending:
                    self._check_stop()
                    return
                unit, future = pending.popleft()
                self.status.update(active_symbol=unit[0], active_session=unit[1],
                                   active_sessions=[list(unit[:2])] + [list(u[:2]) for u, _ in pending])
                self.persist()
                capture, reason, paths = future.result()
                yield unit[0], unit[1], capture, reason, paths
                # Refill only after the coordinator has published and cleaned
                # the yielded capture; at most four sessions retain buffers.
        except BaseException:
            self._stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            if self._download_failure is not None:
                raise self._download_failure
            raise
        finally:
            self._stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            self.status["active_sessions"] = []
