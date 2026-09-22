"""Announcement-anchored forward EPS/P/E research proxy; no I/O or provider work."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

VERSION = "announcement_forward_pe.v1"
RATIO_POLICY = "signed_nonzero_forward_eps.v1"
PERIOD_MATCHING_POLICY = "corroborated_fiscal_periods.v2"
ESTIMATE_PERIOD_POLICY = "latest_capture_unique_fiscal_period.v1"
LABEL = "Daily forward P/E - reconstructed estimates"
UNKNOWN = {None, "", "SOURCE_UNSPECIFIED", "provider_native", "not_established"}
SPLIT_ONLY = "split_adjusted_excluding_distributions"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and math.isfinite(float(result)) else None
    except InvalidOperation:
        return None


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def latest(rows, key, cutoff):
    """Freeze each natural identity at the build cutoff, not the reference date."""
    selected = {}
    for row in rows:
        at = instant(row["captured_at"])
        if at > instant(cutoff):
            continue
        ident = row[key]
        if ident not in selected or at > instant(selected[ident]["captured_at"]):
            selected[ident] = row
        elif at == instant(selected[ident]["captured_at"]) and row != selected[ident]:
            raise ValueError("Conflicting source versions at the same capture time")
    return list(selected.values())


def effective_session(announcement, sessions, keys=None):
    """Sessions are ordered (ISO date, aware UTC close) pairs."""
    dates, closes = keys or ([s[0] for s in sessions], [instant(s[1]) for s in sessions])
    if len(announcement) == 10:
        index = bisect_right(dates, announcement)
        flags = ["announcement_time_unknown_next_session"]
    else:
        when = instant(announcement)
        index = bisect_right(closes, when)
        flags = []
    return (sessions[index][0] if index < len(sessions) else None), flags


def fiscal_labels(statement):
    """Retain provider fiscal-year conventions as aliases of an actual period end."""
    labels = set()
    year, quarter = statement.get("fiscal_year"), statement.get("fiscal_period")
    if year is not None and quarter in ("Q1", "Q2", "Q3", "Q4"):
        labels.add((str(year), quarter))
    if statement.get("source") == "sharadar_ARQ":
        raw = json.loads(statement["payload_json"])
        match = re.fullmatch(r"(\d{4})-(Q[1-4])", str(raw.get("fiscalperiod", "")))
        if match:
            labels.add(match.groups())
    return labels


def period_mapping(earnings, statements, transcripts, *, reported_events=None, evidence=None):
    """Link periods with retained evidence; the earnings date remains the anchor.

    Exact revenue retains precedence. Repeated revenue needs a unique nearby
    filing to disambiguate. Fiscal aliases and uniquely associated calls within
    14 days may resolve source date disagreements. A filing date only supports
    a period link; it is never used as the earnings announcement timestamp.
    """
    raw = json.loads(earnings["payload_json"])
    announced = date.fromisoformat(earnings["source_event_date"])
    candidates = [s for s in statements if
                  0 <= (announced - date.fromisoformat(s["period_end"])).days <= 120]
    explicit = raw.get("fiscalDateEnding") or raw.get("referencePeriodEnd")
    if explicit:
        matches = {s["period_end"] for s in candidates if s["period_end"] == explicit}
        return (explicit, "explicit_period") if len(matches) == 1 else (None, "unmapped_announcement")
    actual = number(raw.get("revenueActual"))
    revenue_matches = {s["period_end"] for s in candidates if actual is not None and actual > 0
                       and number(json.loads(s["payload_json"]).get("revenue")) == actual}
    filings = []
    for statement in candidates:
        payload = json.loads(statement["payload_json"])
        filed = (statement.get("source_datekey") if statement.get("source") == "sharadar_ARQ"
                 else payload.get("filingDate"))
        try:
            offset = (date.fromisoformat(str(filed)[:10]) - announced).days
        except ValueError:
            continue
        if 0 <= offset <= 7:
            filings.append(dict(period_end=statement["period_end"], filing_date=str(filed)[:10],
                                version_id=statement.get("research_row_id")))
    filing_matches = {item["period_end"] for item in filings}
    primary, nearby = [], []
    direct_matches = set()
    for call in transcripts:
        event = json.loads(call["event_json"])
        call_date = event.get("callDate", "")[:10]
        try:
            offset = (date.fromisoformat(call_date) - announced).days
        except ValueError:
            continue
        if abs(offset) > 14:
            continue
        label = (str(call["fiscal_year"]), "Q" + str(call["fiscal_quarter"]))
        matches = {s["period_end"] for s in candidates if label in fiscal_labels(s)}
        detail = dict(call_date=call_date, fiscal_year=call["fiscal_year"],
                      fiscal_quarter=call["fiscal_quarter"], matching_periods=sorted(matches),
                      capture_id=call.get("capture_id"))
        if offset in (0, 1):
            primary.append(detail)
            direct_matches.update(s["period_end"] for s in candidates
                                  if str(s.get("fiscal_year")) == label[0]
                                  and s.get("fiscal_period") == label[1])
        elif reported_events is not None:
            # A nearby call must belong to only this reported event, not a
            # competing release/amendment. Missing context never relaxes dates.
            associated = {e["natural_identity"] for e in reported_events
                          if abs((date.fromisoformat(e["source_event_date"]) -
                                  date.fromisoformat(call_date)).days) <= 14}
            if associated == {earnings["natural_identity"]}:
                nearby.append(detail)
    call_matches = {p for call in primary for p in call["matching_periods"]}

    def supported(period, mapping, calls):
        if evidence is not None:
            evidence.update(transcripts=calls, filing_periods=filings,
                            revenue_match_periods=sorted(revenue_matches))
        return period, mapping

    if len(revenue_matches) == 1:
        mapping = ("reported_revenue_match_transcript_conflict"
                   if call_matches and revenue_matches != call_matches else "reported_revenue_match")
        return next(iter(revenue_matches)), mapping
    if len(revenue_matches) > 1:
        corroborated = revenue_matches & filing_matches
        if len(corroborated) == 1 and len(filing_matches) == 1:
            return supported(next(iter(corroborated)), "reported_revenue_match_filing_corroborated", primary)
        return None, ("conflicting_announcement_period" if call_matches and revenue_matches != call_matches
                      else "unmapped_announcement")
    if len(call_matches) == 1:
        period = next(iter(call_matches))
        if period not in direct_matches:
            return supported(period, "transcript_fiscal_alias_match", primary)
        return period, "transcript_fiscal_match"
    if call_matches:
        return None, "unmapped_announcement"
    if primary and len(filing_matches) == 1:
        return supported(next(iter(filing_matches)), "filing_period_with_transcript_date", primary)
    nearby_matches = {p for call in nearby for p in call["matching_periods"]}
    if len(nearby_matches) == 1:
        return supported(next(iter(nearby_matches)), "transcript_fiscal_match_date_disagreement", nearby)
    return None, "unmapped_announcement"


def estimate_periods(estimates, actual_ends):
    """Normalize date aliases before selecting the newest captured quarter row.

    A cluster must fit wholly within seven days and identify at most one actual
    statement end. Same-capture collisions remain ambiguous; no averaging or
    preference for an exact date breaks a tie.
    """
    targets = sorted({e["target_period_end"] for e in estimates})
    clusters = []
    for target in targets:
        if clusters and (date.fromisoformat(target) - date.fromisoformat(clusters[-1][-1])).days <= 7:
            clusters[-1].append(target)
        else:
            clusters.append([target])
    by_target = {}
    for estimate in estimates:
        by_target.setdefault(estimate["target_period_end"], []).append(estimate)
    by_period = {}
    for cluster in clusters:
        matches = {p for p in actual_ends if any(
            abs((date.fromisoformat(p) - date.fromisoformat(target)).days) <= 7 for target in cluster)}
        width = (date.fromisoformat(cluster[-1]) - date.fromisoformat(cluster[0])).days
        rows = [e for target in cluster for e in by_target[target]]
        if width > 7 or len(matches) > 1:
            # Preserve the conflict rather than joining through a chain of dates.
            for target in cluster:
                by_period.setdefault(target, []).extend(by_target[target])
            continue
        newest = max(instant(e["captured_at"]) for e in rows)
        current = [e for e in rows if instant(e["captured_at"]) == newest]
        period = next(iter(matches)) if matches else (current[0]["target_period_end"]
                                                     if len(current) == 1 else cluster[0])
        if len(rows) > 1 and len(current) == 1 and len(cluster) > 1:
            selected = dict(current[0], period_date_aliases=[dict(
                target_period_end=e["target_period_end"], version_id=e["observation_version_id"],
                captured_at=e["captured_at"]) for e in sorted(rows, key=lambda e: (e["target_period_end"], e["observation_version_id"]))])
            by_period.setdefault(period, []).append(selected)
        else:
            by_period.setdefault(period, []).extend(rows)
    return by_period


def has_long_fiscal_quarter(statements):
    """Recognize retained 16/17-week quarters without relaxing missing-quarter gaps."""
    labelled = {}
    for statement in statements:
        source = statement.get("source") or "fmp"
        for year, quarter in fiscal_labels(statement):
            labelled.setdefault((source, int(year), int(quarter[1])), set()).add(statement["period_end"])
    for (source, year, quarter), ends in labelled.items():
        prior_key = (source, year, quarter-1) if quarter > 1 else (source, year-1, 4)
        previous = labelled.get(prior_key, set())
        if len(ends) == len(previous) == 1:
            gap = (date.fromisoformat(next(iter(ends))) - date.fromisoformat(next(iter(previous)))).days
            if 111 <= gap <= 119:
                return True
    return False


def make_windows(inputs, sessions, *, allow_unverified_basis=False):
    """Return one interval boundary for each actual reported earnings event."""
    cutoff = inputs["cutoff"]
    estimates = latest(inputs["estimates"], "natural_identity", cutoff)
    statements = latest(inputs["statements"], "natural_identity", cutoff)
    statements = [r for r in statements if r.get("fiscal_period") in ("Q1", "Q2", "Q3", "Q4", "quarter")]
    earnings = latest(inputs["earnings"], "natural_identity", cutoff)
    transcripts = [r for r in inputs.get("transcripts", [])
                   if instant(r["captured_at"]) <= instant(cutoff)]
    actual_ends = sorted({r["period_end"] for r in statements})
    by_period = estimate_periods(estimates, actual_ends)
    periods = sorted(set(actual_ends) | set(by_period))
    long_quarters = has_long_fiscal_quarter(statements)
    reported_events = [e for e in earnings if e["source_event_date"] <= instant(cutoff).date().isoformat()
                       and any(number(json.loads(e["payload_json"]).get(k)) is not None
                               for k in ("epsActual", "revenueActual"))]
    windows = []
    highest_period = None
    session_keys = ([s[0] for s in sessions], [instant(s[1]) for s in sessions])
    for event in sorted(earnings, key=lambda r: (r["source_event_date"], r["natural_identity"])):
        raw = json.loads(event["payload_json"])
        if (number(raw.get("epsActual")) is None and number(raw.get("revenueActual")) is None):
            continue  # Scheduled events never advance the window.
        if event["source_event_date"] > instant(cutoff).date().isoformat():
            continue
        announced = (event["source_time_raw"] if event.get("event_precision") == "datetime"
                     else event["source_event_date"])
        effective, flags = effective_session(announced, sessions, session_keys)
        if effective is None:
            continue
        mapping_evidence = {}
        period, mapping = period_mapping(event, statements, transcripts,
                                         reported_events=reported_events, evidence=mapping_evidence)
        if mapping == "reported_revenue_match_transcript_conflict":
            flags.append("transcript_fiscal_label_conflict")
        if mapping in ("transcript_fiscal_alias_match", "transcript_fiscal_match_date_disagreement",
                       "filing_period_with_transcript_date", "reported_revenue_match_filing_corroborated"):
            flags.append(mapping)
        # An amendment or duplicate for an older quarter cannot roll backwards.
        if period is not None and highest_period is not None and period <= highest_period:
            continue
        if period is not None:
            highest_period = period
        flags += ["reconstructed_not_point_in_time"]
        reasons = []
        components = []
        if period is None:
            reasons.append(mapping)
        else:
            index = bisect_right(periods, period)
            selected = periods[index:index + 4]
            preceding = period
            if len(selected) != 4:
                reasons.append("missing_quarters")
            for p in selected:
                gap = (date.fromisoformat(p) - date.fromisoformat(preceding)).days
                if not 70 <= gap <= 110:
                    if 111 <= gap <= 119 and long_quarters:
                        flags.append("observed_long_fiscal_quarter")
                    else:
                        reasons.append("nonconsecutive_quarters")
                preceding = p
                matches = by_period.get(p, [])
                estimate = matches[0] if len(matches) == 1 else None
                payload = json.loads(estimate["payload_json"]) if estimate else {}
                eps = number(payload.get("epsAvg"))
                if estimate is None:
                    reasons.append("missing_estimate" if not matches else "ambiguous_estimate_period")
                elif eps is None:
                    reasons.append("missing_eps")
                if estimate and estimate.get("period_date_aliases"):
                    flags.append("estimate_period_date_revision")
                components.append({
                    "period_end": p, "source_period_end": estimate["target_period_end"] if estimate else None,
                    "eps": str(eps) if eps is not None else None,
                    "version_id": estimate["observation_version_id"] if estimate else None,
                    "captured_at": estimate["captured_at"] if estimate else None,
                    "currency": estimate.get("currency") if estimate else None,
                    "estimate_basis": estimate.get("estimate_basis") if estimate else None,
                    "share_basis": payload.get("shareBasis"),
                    "split_basis_date": payload.get("splitBasisDate"),
                    "num_analysts": payload.get("numAnalystsEps"),
                    **({"period_date_aliases": estimate["period_date_aliases"]}
                       if estimate and estimate.get("period_date_aliases") else {}),
                })
        forward = None if reasons else sum((Decimal(c["eps"]) for c in components), Decimal(0))
        if forward is not None and not math.isfinite(float(forward)):
            reasons.append("nonfinite_forward_eps")
            forward = None
        currencies = {c["currency"] for c in components if c["currency"] not in UNKNOWN}
        bases = {c["estimate_basis"] for c in components if c["estimate_basis"] not in UNKNOWN}
        if len(currencies) > 1 or len(bases) > 1:
            reasons.append("incompatible_estimate_basis")
        verified = (
            len(components) == 4
            and all(c["currency"] not in UNKNOWN and c["estimate_basis"] not in UNKNOWN
                    and c["share_basis"] == inputs.get("share_basis")
                    and c["split_basis_date"] is not None
                    and c["split_basis_date"] == inputs.get("split_basis_date") for c in components)
            and inputs.get("price_currency") not in UNKNOWN
            and currencies == {inputs.get("price_currency")}
        )
        if currencies and inputs.get("price_currency") not in UNKNOWN and currencies != {inputs["price_currency"]}:
            reasons.append("currency_mismatch")
        known_share_bases = {c["share_basis"] for c in components if c["share_basis"] not in UNKNOWN}
        known_split_dates = {c["split_basis_date"] for c in components if c["split_basis_date"] not in UNKNOWN}
        if (len(known_share_bases) > 1 or len(known_split_dates) > 1
            or (known_share_bases and inputs.get("share_basis") not in UNKNOWN
                and known_share_bases != {inputs["share_basis"]})
            or (known_split_dates and inputs.get("split_basis_date") not in UNKNOWN
                and known_split_dates != {inputs["split_basis_date"]})):
            reasons.append("share_basis_mismatch")
        if not verified:
            flags.append("currency_or_eps_share_basis_unverified")
        if inputs.get("price_basis") != SPLIT_ONLY:
            reasons.append("unsupported_price_basis")
        if forward == 0:
            reasons.append("zero_forward_eps")
        elif forward is not None and forward < 0:
            flags.append("negative_forward_eps")
        if not verified and not allow_unverified_basis:
            reasons.append("unverified_basis")
        flags.extend(inputs.get("flags", []))
        window = dict(
            announcement=announced, effective_date=effective, reported_period_end=period,
            announcement_version_id=event["observation_version_id"], mapping=mapping,
            components=components, forward_eps=str(forward) if forward is not None else None,
            status=(sorted(set(reasons))[0] if reasons else "expected_loss" if forward < 0
                    else "ok" if verified else "unverified_basis"),
            ratio_allowed=not reasons, flags=sorted(set(flags + reasons)),
        )
        if mapping_evidence:
            window["mapping_evidence"] = mapping_evidence
        window["window_id"] = digest({"instrument": inputs["instrument_id"], "window": window})
        windows.append(window)
    # Conflicting events effective on the same session must not choose an arbitrary winner.
    by_effective = {}
    for window in windows:
        prior = by_effective.get(window["effective_date"])
        if prior and prior["reported_period_end"] != window["reported_period_end"]:
            window = dict(window, ratio_allowed=False, status="conflicting_announcements",
                          flags=sorted(set(window["flags"] + ["conflicting_announcements"])))
            window["window_id"] = digest(window)
        by_effective[window["effective_date"]] = window
    return sorted(by_effective.values(), key=lambda w: w["effective_date"])


def daily_series(inputs, sessions, windows):
    """Left join to exchange sessions; never fill a missing market close."""
    prices = {p["trade_date"]: p for p in latest(inputs["prices"], "trade_date", inputs["cutoff"])}
    effective = [w["effective_date"] for w in windows]
    for day, _ in sessions:
        if not inputs["start"] <= day <= inputs["end"]:
            continue
        i = bisect_right(effective, day) - 1
        window = windows[i] if i >= 0 else None
        price = prices.get(day)
        close = number(price["close_value"]) if price else None
        ratio = None
        status = window["status"] if window else "no_announcement_anchor"
        if close is None or close <= 0:
            status = "missing_price" if close is None else "invalid_price"
        elif window and window["ratio_allowed"]:
            ratio = close / Decimal(window["forward_eps"])
            if not math.isfinite(float(ratio)):
                ratio = None
                status = "nonfinite_pe"
        yield dict(trade_date=day, close=str(close) if close is not None else None,
                   forward_eps=window["forward_eps"] if window else None,
                   forward_pe_proxy=str(ratio) if ratio is not None else None, status=status,
                   window_id=window["window_id"] if window else None,
                   price_version_id=price["version_id"] if price else None)
