"""Filing-triggered SEC facts refresh with finite, retained, no-retry acquisitions."""
from datetime import datetime
from pathlib import Path
import re
import time
from quant_data.errors import ConflictError, ResourceLimitError, ValidationError
from quant_data.ingestion import PublicationDeferred
from quant_data.json_codec import loads_strict
from quant_data.stores import acquire_write_session, quiet_immutable_read_connection
from quant_data.company.sec_submissions import parse_submissions, SecSubmissionsPublisher, FINANCIAL_FORMS
from quant_data.stores import stable_id
from .collection_sec import sec_units, requires_active_binding, publish_sec_pairs, SecSourceRejected
from .collection_targets import validate_selection
from .collection_plan import ProviderBudget, build_plan
from .collection_queue import atomic, _read, _response, _clock, _utcnow, run_queue
from .equibles_transcript_backfill import private_directory, job_lock

CONTRACT = "quant_data.selected_sec_refresh.v2"
MAX_REQUESTS = 4500
MAX_BYTES = 12 * 1024 * 1024 * 1024
MAX_SECONDS = 6 * 3600

def _new_financial_filings(stores, parsed):
    with acquire_write_session(stores, ("company",), timeout_seconds=1):
        with quiet_immutable_read_connection(stores, "company") as connection:
            known = {r[0] for r in connection.execute(
                """SELECT DISTINCT m.accession_number FROM company_sec_filing_snapshot_membership m
                JOIN company_sec_snapshots s ON s.snapshot_id=m.snapshot_id
                JOIN company_issuers i ON i.issuer_id=s.issuer_id WHERE i.cik=?""", (parsed.cik,))}
    return sorted(f.accession_number for f in parsed.filings
        if f.form_type in FINANCIAL_FORMS and f.accession_number not in known)

def _publish_metadata(stores, registry, parsed, root, deadline, monotonic):
    """Retain the narrowly classified immutable metadata conflict without replaying it."""
    run_id = stable_id("sec_submissions_run", parsed.semantic_identity)
    folder = root / "submissions-metadata-holds"
    private_directory(folder)
    path = folder / (run_id + ".json")
    identity = {"contract": "quant_data.sec_submissions_metadata_hold.v1", "run_id": run_id,
        "semantic_identity": parsed.semantic_identity, "cik": parsed.cik}
    def failure():
        with acquire_write_session(stores, ("company",), timeout_seconds=1):
            with quiet_immutable_read_connection(stores, "company") as connection:
                row = connection.execute("SELECT * FROM ingestion_run_failures WHERE run_id=?", (run_id,)).fetchone()
                if (row is None or row["dataset_id"] != "fixture.company.filings"
                    or row["semantic_identity"] != parsed.semantic_identity
                    or row["error_code"] != "conflict" or row["status"] != "failed"):
                    raise ConflictError("SEC submissions hold lacks its canonical failure audit")
                return dict(row)
    old = _read(path)
    if old is not None:
        evidence = old.get("original_source", {}) if isinstance(old, dict) else {}
        if (not isinstance(evidence, dict)
            or set(evidence) != {"submissions_sha256", "captured_at"}
            or not isinstance(evidence.get("submissions_sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", evidence["submissions_sha256"])
            or old != {**identity, "failure_audit": failure(), "original_source": evidence}):
            raise ConflictError("SEC submissions metadata hold differs from its evidence")
        from quant_data.market.collection_universe import _utc
        _utc(evidence["captured_at"])
        return False
    try:
        SecSubmissionsPublisher(stores, registry).publish(parsed, deadline=deadline, monotonic=monotonic)
    except ConflictError as error:
        if type(error) is not ConflictError or str(error) != "SEC filing accession conflicts with immutable prior metadata":
            raise
        atomic(path, {**identity, "failure_audit": failure(), "original_source": {
            "submissions_sha256": parsed.submissions_sha256, "captured_at": parsed.captured_at}})
        return False
    return True

def run_conditional_refresh(*, root, stores, registry, selection, fetch, secret_values=(),
        request_limit=MAX_REQUESTS, byte_limit=MAX_BYTES, second_limit=MAX_SECONDS,
        utcnow=_utcnow, monotonic=time.monotonic, sleeper=time.sleep,
        check_ciks=(), check_id=None, check_reason=None):
    """Daily metadata checks; facts only for unseen financial accessions or a finite explicit check.

    A date continuation consumes only untouched work in that date's ledger.
    Retained failures/uncertain requests are never retried. A later date does
    not retry failed facts merely because those values remain missing.
    """
    started = monotonic()
    if any(type(v) is not int or not 1 <= v <= maximum for v, maximum in (
        (request_limit, MAX_REQUESTS), (byte_limit, MAX_BYTES), (second_limit, MAX_SECONDS))):
        raise ResourceLimitError("SEC conditional refresh exceeds its activated bounds")
    if check_ciks:
        if (not isinstance(check_ciks, tuple) or len(check_ciks) > 10 or len(set(check_ciks)) != len(check_ciks)
            or any(not isinstance(c, str) or not re.fullmatch("[0-9]{10}", c) for c in check_ciks)
            or not isinstance(check_id, str) or not re.fullmatch("[a-z0-9][a-z0-9-]{0,63}", check_id)
            or not isinstance(check_reason, str) or not 1 <= len(check_reason.strip()) <= 300):
            raise ValidationError("A targeted SEC check requires up to ten exact CIKs, a stable ID and a reason")
        request_limit = min(request_limit, 2 * len(check_ciks))
        byte_limit = min(byte_limit, 128 * 1024 * 1024 * len(check_ciks))
        second_limit = min(second_limit, 7200, 600 * len(check_ciks))
    elif check_id is not None or check_reason is not None:
        raise ValidationError("Targeted check metadata requires explicit CIKs")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValidationError("SEC refresh needs its resolved private root")
    at = _clock(utcnow)
    validate_selection(stores, selection, cutoff=at, collection="sec_filings_companyfacts",
        require_active=requires_active_binding(stores))
    private_directory(root)
    with job_lock(root / "current-controller"):
        queue = root / "filing-triggered" / ("checks/" + check_id if check_ciks else "current/" + at[:10])
        private_directory(queue)
        private_directory(queue / "facts-decisions")
        old_manifest = _read(queue / "scheduled-manifest.json")
        window = old_manifest["observation_window"] if old_manifest else at[:10] + "T00:00:00.000000Z"
        units = sec_units(stores, selection, mode="incremental", observation_window=window,
            cutoff=at, require_active=requires_active_binding(stores))
        all_ciks = {u.subject for u in units}
        if check_ciks:
            if not set(check_ciks) <= all_ciks:
                raise ValidationError("Targeted SEC CIK is outside the selected binding")
            units = tuple(u for u in units if u.subject in check_ciks)
            all_ciks = set(check_ciks)
        if len(units) > MAX_REQUESTS:
            raise ResourceLimitError("SEC selected issuer count exceeds its approved scope")
        manifest = {"contract": CONTRACT, "selection_sha256": selection.scope_sha256,
            "observation_window": window, "unit_ids": [u.unit_id for u in units],
            "max_requests": request_limit, "max_total_bytes": byte_limit, "max_run_seconds": second_limit,
            "check_ciks": list(check_ciks), "check_reason": check_reason, "retries": 0}
        if old_manifest is not None and old_manifest != manifest:
            raise ConflictError("SEC conditional scope changed during its observation")
        atomic(queue / "scheduled-manifest.json", manifest)
        previous = _read(queue / "scheduled-result.json")
        if previous is not None:
            if previous.get("contract") != CONTRACT or previous.get("selection_sha256") != selection.scope_sha256:
                raise ConflictError("SEC completed observation differs")
            return {**previous, "outcome": "already_recorded", "requests_this_run": 0}
        requests = received = facts_requests = 0
        outcomes, decisions = {}, {}
        stop = None
        issuer_deadline = started + second_limit
        def acquire(unit):
            nonlocal requests, received, facts_requests
            # Retained evidence remains publishable even after the GET budget was spent.
            if _response(queue, unit.unit_id, unit) is not None:
                return True
            seconds = int(min(started + second_limit, issuer_deadline) - monotonic())
            if requests >= request_limit or seconds <= 120 or byte_limit - received < unit.max_response_bytes:
                return False
            budget = ProviderBudget("sec", 1, min(1024 * 1024 * 1024, byte_limit - received),
                min(7200, seconds), 1000)
            plan = build_plan(selections=(selection,), units=(unit,), budgets=(budget,), created_at=_clock(utcnow))
            report = run_queue(root=queue, plan=plan, provider="sec", fetch=fetch, publish=None,
                acquire_only=True, secret_values=secret_values, utcnow=utcnow, monotonic=monotonic, sleeper=sleeper)
            requests += report.get("requests", 0)
            received += report.get("received_bytes", 0)
            if unit.endpoint == "companyfacts":
                facts_requests += report.get("requests", 0)
            return report["outcome"] in ("acquired", "acquired_with_gaps")
        for cik in sorted(all_ciks):
            issuer_deadline = min(started + second_limit, monotonic() + 600) if check_ciks else started + second_limit
            pair = tuple(u for u in units if u.subject == cik)
            submissions = next(u for u in pair if u.endpoint == "submissions")
            facts = next(u for u in pair if u.endpoint == "companyfacts")
            if monotonic() - started >= second_limit:
                stop = "targeted_check_deadline" if check_ciks else "publication_deadline"
                break
            if not acquire(submissions):
                if check_ciks and issuer_deadline - monotonic() <= 120:
                    outcomes[cik] = "targeted_check_deadline"
                    continue
                stop = "invocation_budget"
                break
            retained = _response(queue, submissions.unit_id, submissions)
            if retained is None or retained[0]["status"] != 200:
                outcomes[cik] = "submissions_unavailable"
                continue
            try:
                parsed = parse_submissions(cik=cik, body=retained[1],
                    captured_at=datetime.fromisoformat(retained[0]["captured_at"].replace("Z", "+00:00")))
                source = loads_strict(retained[1], max_bytes=submissions.max_response_bytes)
                tickers = source.get("tickers", [])
                symbols = {s.provider_symbol for s in selection.eligible if s.cik == cik}
                if not isinstance(tickers, list) or any(not isinstance(t, str) for t in tickers) or (tickers and not symbols.intersection(tickers)):
                    raise ValidationError("SEC submissions differ from selected issuer symbols")
            except (ConflictError, ValidationError, ResourceLimitError) as error:
                outcomes[cik] = "source_rejected:" + type(error).__name__
                continue
            if parsed.captured_at > _clock(utcnow):
                raise ConflictError("SEC submissions capture is beyond the publication cutoff")
            decision_path = queue / "facts-decisions" / (cik + ".json")
            decision = _read(decision_path)
            identity = {"cik": cik, "submissions_sha256": parsed.submissions_sha256,
                "selection_sha256": selection.scope_sha256}
            if decision is None:
                decision = {**identity, "new_financial_accessions": _new_financial_filings(stores, parsed),
                    "targeted_check": bool(check_ciks)}
                atomic(decision_path, decision)
            elif any(decision.get(k) != v for k, v in identity.items()):
                raise ConflictError("SEC facts decision differs from retained evidence")
            decisions[cik] = decision
            try:
                if not _publish_metadata(stores, registry, parsed, root, issuer_deadline, monotonic):
                    outcomes[cik] = "blocked_canonical_conflict"
                    continue
            except PublicationDeferred:
                if check_ciks:
                    outcomes[cik] = "targeted_check_deadline"
                    continue
                stop = "publication_deadline"
                break
            needs_facts = decision["targeted_check"] or bool(decision["new_financial_accessions"])
            if not needs_facts:
                outcomes[cik] = "metadata_current_facts_unchanged"
                continue
            outcomes[cik] = "facts_pending"
            if not acquire(facts):
                if check_ciks and issuer_deadline - monotonic() <= 120:
                    outcomes[cik] = "targeted_check_deadline"
                    continue
                stop = "invocation_budget"
                break
            try:
                receipts = publish_sec_pairs(root=queue, stores=stores, registry=registry,
                    selection=selection, units=pair, cutoff=_clock(utcnow),
                    deadline=issuer_deadline, monotonic=monotonic,
                    hold_root=root / "sec-metadata-holds")
                outcomes[cik] = receipts[0]["outcome"]
            except SecSourceRejected as error:
                outcomes[cik] = "source_rejected:" + error.reason_code
            if outcomes[cik] == "publication_deadline":
                if check_ciks:
                    outcomes[cik] = "targeted_check_deadline"
                    continue
                stop = "publication_deadline"
                break
        good = {"succeeded", "unchanged", "reused", "metadata_current_facts_unchanged"}
        partial = {"incomplete_pair", "facts_pending", "publication_deadline", "targeted_check_deadline"}
        failed = {c for c, outcome in outcomes.items() if outcome not in good | partial}
        incomplete = {c for c, outcome in outcomes.items() if outcome in partial}
        successful = {c for c, outcome in outcomes.items() if outcome in good}
        unattempted = all_ciks - set(outcomes)
        result = {"contract": CONTRACT, "selection_sha256": selection.scope_sha256,
            "observation_window": window, "completed_at": _clock(utcnow),
            "outcome": "succeeded" if not (failed or incomplete or unattempted or stop) else "partial",
            "stop_reason": stop, "selected_cik_count": len(all_ciks),
            "succeeded_cik_count": len(successful), "failed_cik_count": len(failed),
            "partial_cik_count": len(incomplete), "unattempted_cik_count": len(unattempted),
            "failed_issuers": [{"cik": c, "outcome": outcomes[c]} for c in sorted(failed)],
            "skipped_existing_cik_count": sum(v == "metadata_current_facts_unchanged" for v in outcomes.values()),
            "facts_triggered_cik_count": sum(bool(d["new_financial_accessions"]) or d["targeted_check"] for d in decisions.values()),
            "facts_requests": facts_requests, "identity_gap_count": len(selection.gaps),
            "requests": requests, "response_bytes": received, "retries": 0}
        # Targeted checks are one finite invocation, including partial budget stops.
        # Only normal scheduled dates may continue untouched work under a new invocation budget.
        if check_ciks or stop not in ("invocation_budget", "publication_deadline"):
            atomic(queue / "scheduled-result.json", result)
        atomic(queue / "latest-invocation.json", result, replace=True)
        return result
