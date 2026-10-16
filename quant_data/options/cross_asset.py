"""Finite, restart-safe cross-asset options history through established Theta paths."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from quant_data.stores import StoreWriteLock
from .daily import sync_admission_directories
from .job import SpyHistoryJob, atomic_json, sessions_from_calendars
from .model import build_capture, canonical_json
from .monitor.admission import BackgroundBusy, ThetaAdmission
from .monitor.backfill import backfill_window
from .store import OptionsStore
from .transport import Budget, ProviderFailure, ThetaTransport
from .universe import CROSS_ASSET_ETFS


WORK = Path(".local/cross-asset-options-20261012")
CEILING = date(2026, 10, 12)
EARLIEST = date(2016, 1, 4)  # retained calendar evidence starts in 2015
MAX_REQUESTS = 100000
MAX_BYTES = 128 * 1024**3
MAX_SECONDS = 72 * 3600
MAX_AUTHENTICATIONS = 256
STORE_LIMIT = 25 * 1024**3
FREE_RESERVE = 20 * 1024**3
QUALITY_GAPS = {
    "eod_no_data", "open_interest_no_data", "missing_contemporaneous_spot",
    "fallback_reference_not_crosschecked", "fallback_reference_basis_mismatch",
    "inconsistent_underlying_reference", "no_usable_eod_or_oi",
}
METHODS = {
    "option_history_greeks_eod", "option_history_eod", "option_history_open_interest",
}


class Paused(RuntimeError):
    """A new provider request is deferred without consuming its admission."""


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _durable_json(path, value, root):
    atomic_json(path, value)
    sync_admission_directories(path.parent, root)


def _request_window(now):
    # Direct Theta RPCs can use 180 seconds; avoid starting one close enough
    # to carry it into an existing scheduled or live-protected window.
    return backfill_window(now) and backfill_window(now + timedelta(minutes=3))


def _reviewed_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("cross_asset_evidence_path")
    path = root / relative
    if ".." in Path(relative).parts or path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("cross_asset_evidence_path")
    return path


def _receipt_header(path):
    # Discovery writes sorted JSON with receipt before rows. The compressed
    # bytes are hashed separately; planning needs only the request identity.
    with gzip.open(path, "rt") as stream:
        prefix = stream.read(16384)
    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    if cursor >= len(prefix) or prefix[cursor] != "{":
        raise ValueError("cross_asset_response_evidence")
    cursor += 1
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    key, cursor = decoder.raw_decode(prefix, cursor)
    if key != "receipt":
        raise ValueError("cross_asset_response_evidence")
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    if cursor >= len(prefix) or prefix[cursor] != ":":
        raise ValueError("cross_asset_response_evidence")
    cursor += 1
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    receipt, cursor = decoder.raw_decode(prefix, cursor)
    while cursor < len(prefix) and (prefix[cursor].isspace() or prefix[cursor] == ","):
        cursor += 1
    next_key, cursor = decoder.raw_decode(prefix, cursor)
    if next_key != "rows":
        raise ValueError("cross_asset_response_evidence")
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    if cursor >= len(prefix) or prefix[cursor] != ":":
        raise ValueError("cross_asset_response_evidence")
    cursor += 1
    while cursor < len(prefix) and prefix[cursor].isspace():
        cursor += 1
    if cursor >= len(prefix) or prefix[cursor] != "[":
        raise ValueError("cross_asset_response_evidence")
    if not isinstance(receipt, dict):
        raise ValueError("cross_asset_response_evidence")
    return receipt


def _evidence_index(root, hashes, floors):
    index = {}
    indexed_hashes = {}
    for relative in hashes:
        if not relative.endswith(".json.gz"):
            raise ValueError("cross_asset_response_evidence")
        path = _reviewed_path(root, relative)
        receipt = _receipt_header(path)
        method, params = receipt.get("method"), receipt.get("params")
        if method not in METHODS or not isinstance(params, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
            raise ValueError("cross_asset_response_identity")
        symbol = params.get("symbol")
        if symbol not in CROSS_ASSET_ETFS:
            raise ValueError("cross_asset_evidence_symbol")
        if method == "option_history_open_interest":
            expected = {"symbol", "expiration", "date"}
            day = params.get("date")
        else:
            expected = {"symbol", "expiration", "start_date", "end_date"}
            if method == "option_history_greeks_eod":
                expected |= {"version", "underlyer_use_nbbo"}
                if params.get("version") != "1" or params.get("underlyer_use_nbbo") != "True":
                    raise ValueError("cross_asset_response_identity")
            day = params.get("start_date")
            if params.get("end_date") != day:
                raise ValueError("cross_asset_response_identity")
        if set(params) != expected or params.get("expiration") != "*" or not day:
            raise ValueError("cross_asset_response_identity")
        if date.fromisoformat(day).isoformat() != day or not floors[symbol] <= day <= CEILING.isoformat():
            raise ValueError("cross_asset_response_date")
        if receipt.get("outcome") not in ("success", "empty", "no_data"):
            raise ValueError("cross_asset_response_outcome")
        identity = {"method": method, "params": params}
        unit = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        if unit in index and indexed_hashes[unit] != hashes[relative]:
            raise ValueError("cross_asset_evidence_conflict")
        index[unit] = path
        indexed_hashes[unit] = hashes[relative]
    for symbol in CROSS_ASSET_ETFS:
        for method, params in (
            ("option_history_greeks_eod", {"symbol": symbol, "expiration": "*",
                "start_date": "2026-10-09", "end_date": "2026-10-09",
                "version": "1", "underlyer_use_nbbo": "True"}),
            ("option_history_open_interest", {"symbol": symbol, "expiration": "*",
                "date": "2026-10-09"}),
        ):
            unit = hashlib.sha256(canonical_json({"method": method, "params": params}).encode()).hexdigest()
            if unit not in index:
                raise ValueError("cross_asset_discovery_identity")
    return index


def reviewed_manifest(root):
    """Validate reviewed local scope; this function does not admit a run."""
    root = Path(root).resolve(strict=True)
    work = root / WORK
    plan_path = work / "plan.json"
    plan = json.loads(plan_path.read_text())
    if not isinstance(plan, dict) or not {"symbols", "start_dates", "end_date", "evidence"} <= plan.keys():
        raise ValueError("cross_asset_plan_shape")
    if set(plan) - {"symbols", "start_dates", "end_date", "evidence", "caps"}:
        raise ValueError("cross_asset_plan_shape")
    if plan["symbols"] != list(CROSS_ASSET_ETFS) or plan["end_date"] != CEILING.isoformat():
        raise ValueError("cross_asset_plan_scope")
    floors = plan["start_dates"]
    if not isinstance(floors, dict) or set(floors) != set(CROSS_ASSET_ETFS):
        raise ValueError("cross_asset_plan_floors")
    for value in floors.values():
        if not isinstance(value, str):
            raise ValueError("cross_asset_plan_floors")
        day = date.fromisoformat(value)
        if day.isoformat() != value or not EARLIEST <= day <= CEILING:
            raise ValueError("cross_asset_plan_floors")
    caps = {"max_requests": MAX_REQUESTS, "max_bytes": MAX_BYTES,
            "max_seconds": MAX_SECONDS, "max_authentications": MAX_AUTHENTICATIONS,
            "max_store_bytes": STORE_LIMIT, "min_free_bytes": FREE_RESERVE,
            "workers": 1, "retries": 0}
    if "caps" in plan and plan["caps"] != caps:
        raise ValueError("cross_asset_plan_caps")
    evidence = plan["evidence"]
    if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)):
        raise ValueError("cross_asset_plan_evidence")
    hashes = {}
    for relative in evidence:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("cross_asset_plan_evidence")
        # The reviewed plan names response files relative to its work directory.
        normalized = relative if relative.startswith(str(WORK) + "/") else str(WORK / relative)
        path = _reviewed_path(root, normalized)
        if normalized in hashes:
            raise ValueError("cross_asset_plan_evidence")
        hashes[normalized] = _sha(path)
    # Discovery already admitted these 24 data requests. Never refetch them.
    for symbol in CROSS_ASSET_ETFS:
        for suffix in ("recent-greeks", "recent-oi"):
            relative = str(WORK / f"{symbol}-{suffix}.json.gz")
            if relative not in hashes:
                raise ValueError("cross_asset_discovery_evidence_missing")
    _evidence_index(root, hashes, floors)
    reference_hashes = {}
    for symbol in CROSS_ASSET_ETFS:
        relative = str(WORK / "references" / symbol / "spot-references.json")
        path = root / relative
        if path.exists():
            path = _reviewed_path(root, relative)
            if not isinstance(json.loads(path.read_text()), dict):
                raise ValueError("cross_asset_reference_shape")
            reference_hashes[relative] = _sha(path)
    calendars = {}
    calendar_hashes = {}
    for year in range((min(date.fromisoformat(s) for s in floors.values()) -
                       timedelta(days=14)).year, CEILING.year + 1):
        relative = f".local/theta-spy-20260924/calendar-{year}.json"
        path = _reviewed_path(root, relative)
        batch = json.loads(path.read_text())
        if not isinstance(batch, dict) or not isinstance(batch.get("rows"), list) or not batch["rows"]:
            raise ValueError("cross_asset_calendar_evidence")
        calendars[year] = batch["rows"]
        calendar_hashes[relative] = _sha(path)
    sessions = sessions_from_calendars(calendars,
        min(date.fromisoformat(s) for s in floors.values()), CEILING)
    units = [(symbol, session, previous) for session, previous in reversed(sessions)
             for symbol in CROSS_ASSET_ETFS if session >= floors[symbol]]
    if not units or len({(symbol, session) for symbol, session, _ in units}) != len(units):
        raise ValueError("cross_asset_manifest_units")
    return {
        "contract": "theta_cross_asset_history_v1", "symbols": list(CROSS_ASSET_ETFS),
        "start_dates": floors, "end_date": CEILING.isoformat(),
        "ordering": "date_desc_then_declared_symbol_order", "units": units,
        "caps": caps, "plan_sha256": _sha(plan_path), "evidence_sha256": hashes,
        "calendar_sha256": calendar_hashes, "reference_sha256": reference_hashes,
    }


def plan_summary(manifest):
    return {"contract": manifest["contract"], "symbols": manifest["symbols"],
            "start_dates": manifest["start_dates"], "end_date": manifest["end_date"],
            "planned_sessions": len(manifest["units"]), "caps": manifest["caps"],
            "manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest()}


class CrossAssetJob(SpyHistoryJob):
    WORK_DIRECTORY = str(WORK)
    STORE_LIMIT_BYTES = STORE_LIMIT

    def __init__(self, root, *, transport_factory=ThetaTransport,
                 clock=lambda: datetime.now(timezone.utc), sleep=time.sleep):
        super().__init__(root, transport_factory=transport_factory)
        self.clock, self.sleep = clock, sleep
        self.admission = ThetaAdmission(self.root)
        self.transport = None
        self.epoch = None
        self.authentications = 0
        self.evidence_batches = {}
        self.evidence_hashes = {}
        self.status.pop("symbol", None)
        self.status.update(contract="theta_cross_asset_history_status",
            symbols=list(CROSS_ASSET_ETFS), scope_end=CEILING.isoformat(),
            requests_cap=MAX_REQUESTS, bytes_cap=MAX_BYTES, seconds_cap=MAX_SECONDS,
            authentication_cap=MAX_AUTHENTICATIONS, automatic_retries=0,
            download_workers=1)

    def reconcile_cleanup(self):
        with StoreWriteLock(self.store.path, timeout_seconds=10):
            return super().reconcile_cleanup()

    def _append(self, path, value):
        with path.open("a") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _freeze(self, name, value):
        path = self.work / name
        if path.exists():
            if json.loads(path.read_text()) != json.loads(canonical_json(value)):
                raise ValueError("cross_asset_" + name.replace(".json", "") + "_drift")
        else:
            _durable_json(path, value, self.root)

    def _authorization(self, manifest):
        path = self.work / "authorization.json"
        digest = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
        if path.exists():
            value = json.loads(path.read_text())
            if value.get("manifest_sha256") != digest or value.get("caps") != manifest["caps"]:
                raise ValueError("cross_asset_authorization_drift")
        else:
            if self.status_path.exists() or (self.attempt_path.exists() and self.attempt_path.stat().st_size):
                raise RuntimeError("cross_asset_authorization_missing")
            value = {"contract": manifest["contract"], "manifest_sha256": digest,
                     "started_at": self.clock().isoformat(), "caps": manifest["caps"]}
            _durable_json(path, value, self.root)
        started = datetime.fromisoformat(value["started_at"])
        if started.tzinfo is None or self.clock() < started:
            raise ValueError("cross_asset_authorization_clock")
        return started

    def _remaining(self, started):
        remaining = MAX_SECONDS - (self.clock() - started).total_seconds()
        if remaining <= 0:
            raise RuntimeError("authorization_duration_exhausted")
        return remaining

    def _budget_from_journal(self, started, completed):
        receipt_path = self.work / "request-receipts.jsonl"
        receipts = [json.loads(s) for s in receipt_path.read_text().splitlines() if s]
        if len(receipts) != len(self.attempted):
            raise RuntimeError("cross_asset_unreconciled_attempts")
        received = 0
        for receipt in receipts:
            count = receipt.get("response_bytes")
            if type(count) is not int or count < 0:
                raise RuntimeError("cross_asset_unbounded_receipt")
            received += count
        for attempt in self.history:
            unit = attempt["unit"]
            if (self.stage / (unit + ".json.gz")).exists():
                continue
            params = attempt["params"]
            session = params.get("date", params.get("start_date"))
            if (params.get("symbol"), session) not in completed:
                raise RuntimeError("cross_asset_unreconciled_attempts")
        budget = Budget(max_requests=MAX_REQUESTS, max_bytes=MAX_BYTES,
                        max_seconds=max(1, int(self._remaining(started))),
                        requests=len(self.attempted), received_bytes=received)
        self._budget = budget
        return budget

    def _authentication_state(self):
        attempts = [json.loads(line) for line in
                    (self.work / "authentication-attempts.jsonl").read_text().splitlines()]
        outcomes = [json.loads(line) for line in
                    (self.work / "authentication-outcomes.jsonl").read_text().splitlines()]
        if len(attempts) != len(outcomes) or len(attempts) > MAX_AUTHENTICATIONS:
            raise RuntimeError("cross_asset_authentication_failed_or_unknown")
        for ordinal, (attempt, outcome) in enumerate(zip(attempts, outcomes), 1):
            if (attempt.get("attempt") != ordinal or
                    outcome != {"attempt": ordinal, "epoch": attempt.get("epoch"),
                                "outcome": "success"}):
                raise RuntimeError("cross_asset_authentication_failed_or_unknown")
        self.authentications = len(attempts)

    def _references(self, manifest):
        references = {}
        reviewed = set(manifest["reference_sha256"])
        for symbol in CROSS_ASSET_ETFS:
            relative = str(WORK / "references" / symbol / "spot-references.json")
            path = self.root / relative
            if path.exists() and relative not in reviewed:
                raise ValueError("cross_asset_unreviewed_reference")
            if path.exists() and _sha(path) != manifest["reference_sha256"][relative]:
                raise ValueError("cross_asset_reference_changed")
            value = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(value, dict):
                raise ValueError("cross_asset_reference_shape")
            references[symbol] = value
        return references

    def _index_evidence(self, manifest):
        self.evidence_batches = _evidence_index(
            self.root, manifest["evidence_sha256"], manifest["start_dates"])
        self.evidence_hashes = {
            str(self.root / relative): digest
            for relative, digest in manifest["evidence_sha256"].items()
        }

    def _adopt(self, method, params):
        identity = {"method": method, "params": {k: str(v) for k, v in params.items()}}
        unit = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        target = self.stage / (unit + ".json.gz")
        if target.exists() or unit not in self.evidence_batches:
            return target
        source = self.evidence_batches[unit]
        if _sha(source) != self.evidence_hashes[str(source)]:
            raise ValueError("cross_asset_evidence_changed")
        temporary = target.with_suffix(".tmp")
        shutil.copyfile(source, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_admission_directories(self.stage, self.root)
        return target

    def _storage_check(self):
        if self.store.path.stat().st_size >= STORE_LIMIT:
            raise RuntimeError("options_database_cap")
        if shutil.disk_usage(self.work).free < FREE_RESERVE:
            raise RuntimeError("disk_reserve")

    def cached_get(self, transport, method, **params):
        batch, target = super().cached_get(transport, method, **params)
        # Persist the rename before publication, including after a previous
        # directory-sync failure left a staged response for restart to reuse.
        sync_admission_directories(target.parent, self.root)
        return batch, target

    def _get(self, started, method, *, wait, **params):
        target = self._adopt(method, params)
        if target.exists():
            return self.cached_get(None, method, **params)
        while True:
            self._remaining(started)
            now = self.clock()
            if not _request_window(now):
                # Scheduled daily/weekly owners may log in without changing
                # the monitor epoch. Do not retain their predecessor's token.
                if self.transport is not None:
                    self.transport.close()
                    self.transport = None
                    self.epoch = None
                if not wait:
                    raise Paused("scheduled_or_market_window")
            else:
                try:
                    with self.admission.background(now) as epoch:
                        if not _request_window(self.clock()):
                            raise BackgroundBusy("scheduled_or_market_window")
                        self._storage_check()
                        self._budget.check()
                        if self.transport is None or epoch != self.epoch:
                            if self.transport is not None:
                                self.transport.close()
                                self.transport = None
                            if self.authentications >= MAX_AUTHENTICATIONS:
                                raise RuntimeError("cross_asset_authentication_cap")
                            ordinal = self.authentications + 1
                            self._append(self.work / "authentication-attempts.jsonl",
                                         {"attempt": ordinal, "at": self.clock().isoformat(),
                                          "epoch": epoch})
                            try:
                                transport = self.transport_factory(
                                    self.root, self._budget, receipt_callback=self.receipt,
                                    symbols=CROSS_ASSET_ETFS)
                            except BaseException as exc:
                                self._append(self.work / "authentication-outcomes.jsonl",
                                             {"attempt": ordinal, "epoch": epoch,
                                              "outcome": "failed", "error": type(exc).__name__})
                                raise
                            self._append(self.work / "authentication-outcomes.jsonl",
                                         {"attempt": ordinal, "epoch": epoch,
                                          "outcome": "success"})
                            self.transport = transport
                            self.epoch = epoch
                            self.authentications = ordinal
                            self.status["authentication_attempts"] = ordinal
                            self.status["subscription_code"] = self.transport.subscription_code
                            self.persist()
                        if not _request_window(self.clock()):
                            raise BackgroundBusy("scheduled_or_market_window")
                        return self.cached_get(self.transport, method, **params)
                except BackgroundBusy:
                    if not wait:
                        raise Paused("live_or_background_capacity") from None
            self.sleep(min(30, max(1, self._remaining(started))))

    def _coverage(self, manifest):
        with StoreWriteLock(self.store.path, timeout_seconds=10):
            with self.store.read() as connection:
                self.store._check(connection, require_latest=True)
                return {(row[0], row[1]) for row in connection.execute(
                    "SELECT symbol,session_date FROM option_current "
                    "WHERE session_date BETWEEN ? AND ?",
                    (min(manifest["start_dates"].values()), CEILING.isoformat()),
                ) if row[0] in CROSS_ASSET_ETFS}

    def _gap_state(self, allowed):
        path = self.work / "gap-events.jsonl"
        gaps = {}
        for line in path.read_text().splitlines():
            event = json.loads(line)
            key = (event["symbol"], event["session"])
            if key not in allowed or event.get("reason") not in QUALITY_GAPS:
                raise ValueError("cross_asset_gap_event")
            gaps[key] = event
        return gaps

    def _record_gap(self, symbol, session, reason):
        event = {"symbol": symbol, "session": session, "reason": reason,
                 "at": self.clock().isoformat()}
        self._append(self.work / "gap-events.jsonl", event)
        return event

    def _session(self, started, symbol, session, previous, references, *, wait):
        day = date.fromisoformat(session)
        greek, greek_path = self._get(started, "option_history_greeks_eod", wait=wait,
            symbol=symbol, expiration="*", start_date=day, end_date=day,
            version="1", underlyer_use_nbbo=True)
        paths = {greek_path}
        receipts = [greek["receipt"]]
        rows = greek["rows"]
        if not rows:
            prices, price_path = self._get(started, "option_history_eod", wait=wait,
                symbol=symbol, expiration="*", start_date=day, end_date=day)
            paths.add(price_path)
            receipts.append(prices["receipt"])
            rows = prices["rows"]
        if not rows:
            return None, "eod_no_data", paths
        oi, oi_path = self._get(started, "option_history_open_interest", wait=wait,
            symbol=symbol, expiration="*", date=day)
        paths.add(oi_path)
        receipts.append(oi["receipt"])
        try:
            capture = build_capture(session, rows, oi["rows"], receipts,
                previous_session=previous, spot_reference=references[symbol].get(session),
                symbol=symbol)
        except ValueError as exc:
            if str(exc) not in QUALITY_GAPS:
                raise
            return None, str(exc), paths
        if not capture["coverage"]["eod_contracts"]:
            return None, "no_usable_eod_or_oi", paths
        return capture, None, paths

    def _sessions(self, started, units, references, *, wait):
        """Download iterator; canonical publication remains in the coordinator."""
        for symbol, session, previous in units:
            self._remaining(started)
            self._storage_check()
            self.status.update(active_symbol=symbol, active_session=session)
            self.persist()
            capture, reason, paths = self._session(
                started, symbol, session, previous, references, wait=wait)
            yield symbol, session, capture, reason, paths

    def run(self, *, wait=False):
        os.umask(0o077)
        lock = (self.work / "run.lock").open("a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("cross_asset_job_already_running") from None
        try:
            self.reload_durable_state()
            manifest = reviewed_manifest(self.root)
            self._freeze("manifest.json", manifest)
            started = self._authorization(manifest)
            sync_admission_directories(self.work, self.root)
            for name in ("attempts.jsonl", "request-receipts.jsonl",
                         "authentication-attempts.jsonl", "authentication-outcomes.jsonl",
                         "gap-events.jsonl"):
                path = self.work / name
                path.touch(exist_ok=True)
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            sync_admission_directories(self.work, self.root)
            self._authentication_state()
            self._index_evidence(manifest)
            references = self._references(manifest)
            completed = self._coverage(manifest)
            allowed = {(s, d) for s, d, _ in manifest["units"]}
            gaps = {key: event for key, event in self._gap_state(allowed).items()
                    if key not in completed}
            if (self.work / "pending-cleanup.json").exists():
                if not self.reconcile_cleanup():
                    raise RuntimeError("cross_asset_cleanup_unreconciled")
            if self.status.get("state") in ("completed", "completed_with_gaps"):
                if len((completed & allowed) | set(gaps)) != len(allowed):
                    raise RuntimeError("cross_asset_completed_state_drift")
                return self.status
            self._remaining(started)
            self._budget_from_journal(started, completed)
            self.status.pop("error", None)
            self.status.pop("pause_reason", None)
            self.status.update(state="running", planned_sessions=len(allowed),
                manifest_sha256=hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
                authorization_started_at=started.isoformat(),
                completed_sessions=len(completed & allowed), gaps=list(gaps.values()))
            self.persist()
            units = [unit for unit in manifest["units"]
                     if (unit[0], unit[1]) not in completed and (unit[0], unit[1]) not in gaps]
            with closing(self._sessions(started, units, references, wait=wait)) as sessions:
                for symbol, session, capture, reason, paths in sessions:
                    self._remaining(started)
                    self._storage_check()
                    key = (symbol, session)
                    if reason is not None:
                        gaps[key] = self._record_gap(symbol, session, reason)
                    else:
                        self.prepare_cleanup(capture, paths)
                        result = self.store.publish(capture, missing_only=True)
                        if result["outcome"] == "existing":
                            (self.work / "pending-cleanup.json").unlink()
                            sync_admission_directories(self.work, self.root)
                            self.status["preserved"] = self.status.get("preserved", 0) + 1
                        elif not self.reconcile_cleanup():
                            raise RuntimeError("cross_asset_cleanup_unverified")
                        else:
                            self.status["published"] = self.status.get("published", 0) + result["canonical_changes"]
                        completed.add(key)
                        self.status["last_capture_id"] = result["capture_id"]
                    self.status.update(completed_sessions=len(completed & allowed), gaps=list(gaps.values()))
                    self.persist()
            self.status.update(state="completed_with_gaps" if gaps else "completed",
                               active_symbol=None, active_session=None)
            self.persist()
            return self.status
        except Paused as exc:
            self.status.update(state="paused", pause_reason=str(exc),
                               active_symbol=None, active_session=None)
            self.persist()
            return self.status
        except BaseException as exc:
            self.status.update(state="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "stopped",
                error=(exc.code if isinstance(exc, ProviderFailure)
                else str(exc) if type(exc) in (RuntimeError, ValueError) else type(exc).__name__))
            self.persist()
            raise
        finally:
            if self.transport is not None:
                self.transport.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reviewed finite cross-asset Theta backfill")
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--wait", action="store_true", help="Wait outside provider gates, at most 30 seconds per check")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if args.command == "plan":
        if args.wait:
            parser.error("--wait is only valid with run")
        print(canonical_json(plan_summary(reviewed_manifest(root))))
    else:
        print(canonical_json(CrossAssetJob(root).run(wait=args.wait)))


if __name__ == "__main__":
    main()
